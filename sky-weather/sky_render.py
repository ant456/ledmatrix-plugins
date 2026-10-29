"""Sky Weather screens (128x32).

A sky that follows the real sun: the gradient is chosen from the sun's elevation (night, dawn glow, blue day,
golden dusk), the sun rides an arc between today's sunrise and sunset, the Moon rides its own rise-to-set arc
with the correct phase and tilt for this place, and clouds / rain / snow / fog / lightning come from the
current conditions.

Pages (``PAGES``): now, sun, moon, hourly, daily, details, plus alert pages when NWS alerts are active.
Everything draws onto a plain PIL RGB image and reads only the ``world`` dict it is handed, so it is easy to test.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageChops, ImageDraw

import bdf_face
import sky_astro as astro
import sky_data as data
import sky_draw as d
import sky_font as font
from bdf_face import BdfFace

RGB = Tuple[int, int, int]
W, H = 128, 32
UTC = timezone.utc

WORD: Union[int, BdfFace] = 2       # 5x8: short words
NUM: Union[int, BdfFace] = 3        # 7x13B: numbers
BIG: Union[int, BdfFace] = 3        # 9x15B: the temperature
HORIZON = 26                        # y of the horizon on the "now" page

SHADOW: RGB = (4, 6, 18)
TINY_GRAY: RGB = (150, 160, 185)
MOON_LIT: RGB = (240, 236, 212)
MOON_DARK: RGB = (30, 34, 54)


def load_faces(dirs=()) -> None:
    global WORD, NUM, BIG
    WORD = bdf_face.load("5x8", dirs) or 2
    NUM = bdf_face.load("7x13B", dirs) or 3
    BIG = bdf_face.load("9x15B", dirs) or 3


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


# ── colour model ─────────────────────────────────────────────────────────────────────────────

#   elevation, top of sky, horizon (morning), horizon (evening)
SKY_KEYS: List[Tuple[float, RGB, RGB, RGB]] = [
    (-18.0, (3, 4, 16),     (7, 9, 28),      (7, 9, 28)),
    (-12.0, (7, 9, 34),     (24, 24, 68),    (24, 22, 62)),
    (-6.0,  (16, 22, 70),   (100, 56, 118),  (120, 52, 92)),
    (-1.5,  (34, 52, 124),  (238, 112, 124), (244, 104, 62)),
    (2.5,   (54, 96, 172),  (255, 176, 116), (255, 156, 70)),
    (9.0,   (58, 120, 198), (206, 206, 228), (252, 208, 138)),
    (26.0,  (40, 112, 212), (116, 182, 240), (116, 182, 240)),
]


def sky_colors(elev: float, morning: bool) -> Tuple[RGB, RGB]:
    """(top, horizon) colours of the clear sky for a sun elevation in degrees."""
    keys = SKY_KEYS
    if elev <= keys[0][0]:
        return keys[0][1], keys[0][2]
    if elev >= keys[-1][0]:
        return keys[-1][1], keys[-1][2]
    for (e0, t0, m0, v0), (e1, t1, m1, v1) in zip(keys, keys[1:]):
        if e0 <= elev <= e1:
            f = (elev - e0) / (e1 - e0)
            h0, h1 = (m0, m1) if morning else (v0, v1)
            return d.mix(t0, t1, f), d.mix(h0, h1, f)
    return keys[-1][1], keys[-1][2]


def grayed(c: RGB, amount: float, dark: float = 0.0) -> RGB:
    """Pull a colour toward the grey of the same brightness (overcast), then darken it."""
    lum = int(0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2])
    return d.scale(d.mix(c, (lum, lum + 2, lum + 6), amount), 1.0 - dark)


def sky_state(world: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the painters need about the sky right now (sun, moon, colours, cloud, positions)."""
    ts = world["ts"]
    dt = datetime.fromtimestamp(ts, UTC)
    info = world["info"]
    cur = world.get("cur") or {}
    cdef = data.cond(cur.get("cond"))
    cover = max(clamp((cur.get("cloud") or 0.0) / 100.0), cdef["cloud"] if not cur.get("cloud") else 0.0)
    if cur.get("cloud") is not None and cdef["cloud"] >= 0.9:
        cover = max(cover, cdef["cloud"])
    elev, az = astro.sun_altaz(dt, world["lat"], world["lon"])
    morning = az < 180.0
    top, hor = sky_colors(elev, morning)
    gray = clamp(cover * 0.7 - 0.1) + (0.15 if cdef["rain"] or cdef["storm"] else 0.0)
    dark = 0.12 * cdef["rain"] + 0.2 * cdef["storm"] + (0.08 if cover > 0.85 else 0.0)
    if gray > 0.0 and elev > -12:
        top, hor = grayed(top, gray, dark), grayed(hor, gray * 0.9, dark * 0.8)

    sr, ss = info.get("sunrise"), info.get("sunset")
    sun_frac = None
    now_l = dt.astimezone(world["tz"])
    if sr and ss and sr <= now_l <= ss:
        sun_frac = (now_l - sr).total_seconds() / max(1.0, (ss - sr).total_seconds())
    moon = info["moon"]
    moon_frac = None
    if moon.get("pass"):
        r, s = moon["pass"]
        r = r or (now_l - timedelta(hours=6))
        s = s or (now_l + timedelta(hours=6))
        moon_frac = clamp((now_l - r).total_seconds() / max(1.0, (s - r).total_seconds()))
    if cur.get("cond") in ("sunny", "clear-night"):                       # HA's state can lag the sun by a few minutes
        cdef = dict(cdef, label="CLEAR" if elev < -1.0 else "SUNNY")
    return {
        "dt": now_l, "elev": elev, "az": az, "morning": morning, "top": top, "hor": hor, "cover": cover,
        "cdef": cdef, "night_f": clamp((-elev - 3.0) / 12.0), "day_f": clamp((elev + 4.0) / 16.0),
        "sun_frac": sun_frac, "moon_frac": moon_frac, "moon": moon, "info": info,
        "max_alt": info.get("max_alt") or 45.0, "t": world.get("anim", ts),     # animation clock (real time)
    }


# ── tiny helpers ─────────────────────────────────────────────────────────────────────────────

def shadow_text(img: Image.Image, s: object, x: int, y: int, color: RGB,
                face: Union[int, BdfFace] = 1, align: str = "left") -> int:
    d.text(img, s, x + 1, y + 1, SHADOW, face, align)
    return d.text(img, s, x, y, color, face, align)


def fmt_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return "--:--"
    h = dt.hour % 12 or 12
    return "%d:%02d%s" % (h, dt.minute, "A" if dt.hour < 12 else "P")


def fmt_hour(dt: datetime) -> str:
    return "%d%s" % (dt.hour % 12 or 12, "A" if dt.hour < 12 else "P")


def fmt_span(seconds: float) -> str:
    m = int(max(0, seconds) // 60)
    return "%dH%02dM" % (m // 60, m % 60) if m >= 60 else "%dM" % m


def degree(img: Image.Image, x: int, y: int, color: RGB) -> None:
    for dx, dy in ((1, 0), (2, 0), (0, 1), (3, 1), (1, 2), (2, 2)):
        d.rect(img, x + dx, y + dy, 1, 1, color)


def temp_color(t: Optional[float], unit: str = "F") -> RGB:
    if t is None:
        return d.GRAY
    f = t if unit.upper().startswith("F") else t * 9 / 5 + 32
    pts = [(10, (150, 200, 255)), (32, (120, 190, 255)), (50, (140, 235, 220)), (65, (170, 245, 150)),
           (78, (255, 224, 110)), (90, (255, 160, 70)), (102, (255, 90, 70))]
    if f <= pts[0][0]:
        return pts[0][1]
    for (a, ca), (b, cb) in zip(pts, pts[1:]):
        if a <= f <= b:
            return d.mix(ca, cb, (f - a) / (b - a))
    return pts[-1][1]


def sev_color(sev: str) -> RGB:
    return {"extreme": (255, 40, 60), "severe": (255, 70, 60), "moderate": (255, 150, 40)}.get(sev, (255, 214, 60))


# ── sprites: weather icons (13x11, drawn per pixel so rain falls and rays twinkle) ──────────

SUN_ROWS = (".....o.....", "..o.....o..", "....YYY....", "...YYYYY...", "o..YYYYY..o",
            "...YYYYY...", "....YYY....", "..o.....o..", ".....o.....")
SUN_S = ("...o...", ".o...o.", "..YYY..", "o.YYY.o", "..YYY..", ".o...o.", "...o...")
CLOUD_ROWS = ("...WWW.WW..", "..WWWWWWWW.", ".WWWWWWWWWW", "WWWWWWWWWWW", "WWWWWWWWWWW", ".GGGGGGGGG.")
BOLT = ("..LL", ".LL.", "LLLL", ".LL.", "LL..")
CLOUD_PAL = {
    "light": {"W": (238, 242, 250), "G": (150, 162, 188)},
    "rain": {"W": (176, 186, 206), "G": (112, 124, 150)},
    "storm": {"W": (126, 134, 156), "G": (80, 88, 114)},
}
_ICONS: Dict[Tuple[str, int], Image.Image] = {}


def _paint(px: Dict[Tuple[int, int], RGB], rows: Sequence[str], ox: int, oy: int, pal: Dict[str, RGB]) -> None:
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in pal:
                px[(ox + x, oy + y)] = pal[ch]


def _moon_px(px: Dict[Tuple[int, int], RGB], ox: int, oy: int, size: int) -> None:
    c = (size - 1) / 2.0
    for y in range(size):
        for x in range(size):
            if math.hypot(x - c, y - c) <= size / 2.0 and math.hypot(x - c - size * 0.28, y - c + size * 0.12) > size * 0.40:
                px[(ox + x, oy + y)] = MOON_LIT


def icon_name(condition: str, night: bool) -> str:
    c = (condition or "").lower()
    if c in ("sunny", "clear-night", "windy"):
        return "moon" if night else "sun"
    if c in ("partlycloudy", "windy-variant"):
        return "pmoon" if night else "partly"
    if c in ("rainy", "pouring"):
        return "rain"
    if c in ("lightning", "lightning-rainy"):
        return "storm"
    if c in ("snowy", "snowy-rainy", "hail"):
        return "snow"
    if c == "fog":
        return "fog"
    return "cloud"


def icon(name: str, frame: int = 0) -> Image.Image:
    key = (name, frame)
    if key in _ICONS:
        return _ICONS[key]
    px: Dict[Tuple[int, int], RGB] = {}
    ray = (255, 150, 40) if frame == 0 else (255, 196, 80)
    sun_pal = {"Y": (255, 222, 70), "o": ray}
    if name == "sun":
        _paint(px, SUN_ROWS, 1, 1, sun_pal)
    elif name == "moon":
        _moon_px(px, 2, 1, 9)
        for sx, sy in ((10, 1), (11, 6), (1, 9)):
            px[(sx, sy)] = (200, 210, 255) if frame == 0 else (120, 130, 180)
    elif name == "cloud":
        _paint(px, CLOUD_ROWS, 1, 2, CLOUD_PAL["light"])
    elif name == "partly":
        _paint(px, SUN_S, 0, 0, sun_pal)
        _paint(px, CLOUD_ROWS, 2, 4, CLOUD_PAL["light"])
    elif name == "pmoon":
        _moon_px(px, 0, 0, 7)
        _paint(px, CLOUD_ROWS, 2, 4, CLOUD_PAL["light"])
    elif name == "rain":
        _paint(px, CLOUD_ROWS, 1, 0, CLOUD_PAL["rain"])
        for dx, dy in ((2, 7), (6, 6), (9, 8)):
            px[(dx + 1, dy + frame)] = (96, 164, 255)
            px[(dx + 1, dy + frame + 2)] = (60, 120, 220)
    elif name == "storm":
        _paint(px, CLOUD_ROWS, 1, 0, CLOUD_PAL["storm"])
        if frame == 0:
            _paint(px, BOLT, 5, 6, {"L": (255, 230, 60)})
        else:
            _paint(px, BOLT, 5, 6, {"L": (255, 255, 200)})
    elif name == "snow":
        _paint(px, CLOUD_ROWS, 1, 0, CLOUD_PAL["light"])
        for sx, sy in ((3, 7), (6, 8), (9, 7), (4, 10), (8, 10)):
            px[(sx, min(10, sy + frame))] = (255, 255, 255)
    elif name == "fog":
        for i, y in enumerate((2, 4, 6, 8)):
            off = (frame + i) % 2
            for x in range(1 + off, 11 + off - (i % 2) * 2):
                px[(x, y)] = (170, 184, 204) if i % 2 == 0 else (130, 144, 166)
    img = Image.new("RGBA", (13, 11), (0, 0, 0, 0))
    for (x, y), c in px.items():
        if 0 <= x < 13 and 0 <= y < 11:
            img.putpixel((x, y), c + (255,))
    _ICONS[key] = img
    return img


def put_icon(img: Image.Image, x: int, y: int, name: str, frame: int = 0) -> None:
    ic = icon(name, frame)
    img.paste(ic, (x, y), ic)


# ── the Moon ─────────────────────────────────────────────────────────────────────────────────

_MOONS: Dict[Tuple[int, int, int], Image.Image] = {}
MARE = ((-0.30, -0.22, 0.30), (0.26, 0.14, 0.24), (-0.08, 0.52, 0.20), (0.36, -0.40, 0.16), (-0.46, 0.30, 0.15))


def moon_sprite(r: int, illum: float, limb: float) -> Image.Image:
    """RGBA Moon of radius ``r``: lit by ``illum`` (0..1) on the side facing the Sun, tilted by ``limb`` degrees."""
    key = (r, int(round(illum * 48)), int(round(limb / 5.0)))
    if key in _MOONS:
        return _MOONS[key]
    illum_q, limb_q = key[1] / 48.0, key[2] * 5.0
    sl, cl = math.sin(math.radians(limb_q)), math.cos(math.radians(limb_q))
    size = 2 * r + 3
    c = size / 2.0
    n = 3
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for py in range(size):
        for px_ in range(size):
            lit = inside = 0.0
            shade = 0.0
            for sy in range(n):
                for sx in range(n):
                    u = (px_ + (sx + 0.5) / n - c) / r
                    v = (py + (sy + 0.5) / n - c) / r
                    if u * u + v * v > 1.0:
                        continue
                    inside += 1
                    xr, yr = u * sl - v * cl, u * cl + v * sl
                    if xr > (1.0 - 2.0 * illum_q) * math.sqrt(max(0.0, 1.0 - yr * yr)) + 1e-6:
                        lit += 1
                        if any((u - mx) ** 2 + (v - my) ** 2 < mr * mr for mx, my, mr in MARE):
                            shade += 1
            if inside == 0:
                continue
            f = lit / inside
            col = d.mix(MOON_DARK, MOON_LIT, f)
            if lit:
                col = d.mix(col, d.scale(col, 0.68), shade / lit)
            img.putpixel((px_, py), col + (int(255 * inside / (n * n)),))
    _MOONS[key] = img
    return img


def halo(img: Image.Image, cx: float, cy: float, r: float, color: RGB, strength: float) -> None:
    for k, a in ((2.2, 0.05), (1.6, 0.08), (1.2, 0.12)):
        d.ellipse(img, cx, cy, r * k, r * k * 0.9, color, a * strength)


def sun_disc(img: Image.Image, cx: float, cy: float, S: Dict[str, Any], t: float, rays: bool = True) -> None:
    low = clamp(1.0 - S["elev"] / 18.0)
    core = d.mix((255, 244, 176), (255, 138, 56), low)
    core = d.mix(core, (255, 255, 255), clamp(S["cover"] * 1.2))                 # thin cloud turns the disc white, not green
    halo(img, cx, cy, 4.0, d.mix((255, 220, 120), (255, 110, 50), low), 1.0 + low)
    d.ellipse(img, cx, cy, 3.6, 3.6, core, 1.0)
    d.ellipse(img, cx - 0.6, cy - 0.7, 1.8, 1.8, d.mix(core, (255, 255, 230), 0.6), 0.8)
    if rays and S["cover"] < 0.75:
        for i in range(8):
            a = t * 0.35 + i * math.pi / 4
            rr = 6.2 + 0.6 * math.sin(t * 2 + i)
            d.rect(img, int(round(cx + math.cos(a) * rr)), int(round(cy + math.sin(a) * rr)), 1, 1,
                   d.mix(core, (255, 255, 255), 0.2), 0.7)


def dimmed(img: Image.Image, alpha: float, paint) -> None:
    """Run ``paint(img)`` but let only ``alpha`` of it show (the Sun and Moon through cloud)."""
    if alpha >= 0.98:
        paint(img)
        return
    tmp = img.copy()
    paint(tmp)
    img.paste(Image.blend(img, tmp, max(0.0, alpha)))


def arc_xy(frac: float, x0: int, x1: int, horizon: float, height: float) -> Tuple[float, float]:
    return x0 + (x1 - x0) * frac, horizon - math.sin(math.pi * frac) * height


# ── the scene (sky, stars, clouds, weather, hills) ───────────────────────────────────────────

_STARS = [(random.Random(11 + i).uniform(0, W - 1), random.Random(91 + i).uniform(0, 22), i) for i in range(34)]
_SCRIM: Optional[Image.Image] = None


def stars(img: Image.Image, S: Dict[str, Any], t: float, ymax: int = 22, strength: Optional[float] = None) -> None:
    amt = S["night_f"] * (1.0 - 0.85 * S["cover"]) if strength is None else strength
    if amt <= 0.03:
        return
    for x, y, i in _STARS:
        if y > ymax:
            continue
        tw = 0.55 + 0.45 * math.sin(t * (0.9 + (i % 4) * 0.45) + i * 1.7)
        a = amt * tw * (0.5 if i % 3 else 1.0)
        c = d.mix((0, 0, 0), (220, 228, 255), a)
        px = (int(x), int(y))
        base = img.getpixel(px)
        img.putpixel(px, tuple(max(b, cc) for b, cc in zip(base, c)))
        if i % 11 == 0 and a > 0.5:                                       # a few bright ones get a cross
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p2 = (px[0] + ox, px[1] + oy)
                if 0 <= p2[0] < W and 0 <= p2[1] < H:
                    b2 = img.getpixel(p2)
                    img.putpixel(p2, tuple(max(b, int(cc * 0.4)) for b, cc in zip(b2, c)))


def cloud_color(S: Dict[str, Any]) -> RGB:
    base = d.mix((64, 70, 96), (246, 246, 252), S["day_f"])
    glow = clamp(1.0 - abs(S["elev"] - 1.0) / 9.0)
    c = d.mix(base, S["hor"], 0.42 * glow)
    heavy = clamp((S["cover"] - 0.6) / 0.4) * 0.6 + (0.25 if S["cdef"]["rain"] or S["cdef"]["storm"] else 0.0)
    return grayed(c, heavy * 0.7, heavy * 0.35)


def clouds(img: Image.Image, S: Dict[str, Any], cur: Dict[str, Any], t: float, ymin: int = 2, ymax: int = 19) -> None:
    cover = S["cover"]
    n = int(round(cover * 9)) + (1 if cover > 0.15 else 0)
    if n <= 0:
        return
    col = cloud_color(S)
    wind = (cur.get("wind") or 4.0)
    mask = Image.new("L", (W, H), 0)
    dm = ImageDraw.Draw(mask)
    for i in range(n):
        rng = random.Random(500 + i)
        scale = rng.uniform(0.7, 1.25) + cover * 0.2
        speed = rng.uniform(1.6, 3.4) * (0.6 + wind / 14.0)
        span = W + 40
        x = (rng.uniform(0, span) + speed * t) % span - 20
        y = rng.uniform(ymin + 3, ymax - 2)
        for ox, oy, rx, ry in ((0, 0, 7, 2.6), (-3.5, -1.7, 3.4, 2.6), (1.5, -2.6, 4.2, 3.2), (4.8, -0.9, 3.0, 2.2)):
            dm.ellipse([x + (ox - rx) * scale, y + (oy - ry) * scale, x + (ox + rx) * scale, y + (oy + ry) * scale], fill=255)
    solid = mask.point(lambda v: int(v * 0.88))
    under = Image.new("L", (W, H), 0)
    under.paste(solid, (0, 1))
    img.paste(d.scale(col, 0.62), (0, 0), under)
    img.paste(col, (0, 0), solid)
    lower = Image.new("L", (W, H), 0)
    lower.paste(solid, (0, 1))
    img.paste(d.mix(col, (255, 255, 255), 0.35), (0, 0), ImageChops.subtract(solid, lower))      # lit top edge
    upper = Image.new("L", (W, H), 0)
    upper.paste(solid, (0, -1))
    img.paste(d.scale(col, 0.78), (0, 0), ImageChops.subtract(solid, upper))                    # shaded belly
    if cover > 0.7:                                                       # overcast: a soft grey lid across the top
        lid = d.scale(col, 0.7)
        for y in range(0, 6):
            d.rect(img, 0, y, W, 1, lid, (cover - 0.6) * (1.0 - y / 6.0) * 1.1)


def precipitation(img: Image.Image, S: Dict[str, Any], t: float, bottom: int = 28) -> None:
    c = S["cdef"]
    dr = ImageDraw.Draw(img)
    if c["rain"]:
        n = 20 if c["rain"] == 1 else 40
        col = d.mix((100, 140, 210), (170, 205, 255), S["day_f"] * 0.6 + 0.2)
        for i in range(n):
            speed = 46 + (i * 7) % 24
            x = (i * 53 + 7) % (W + 8)
            y = (i * 29 + speed * t) % (bottom + 8) - 6
            dr.line([(x - (y * 0.18) % 3, y), (x - (y * 0.18) % 3 - 1, y + 2)], fill=col)
    if c["snow"]:
        for i in range(26):
            speed = 7 + (i * 5) % 7
            x = (i * 41 + 9) % W + math.sin(t * 1.3 + i) * 2
            y = (i * 17 + speed * t) % (bottom + 4)
            d.rect(img, int(x), int(y), 1, 1, (246, 248, 255) if i % 3 else (190, 205, 230))
    if c["hail"]:
        for i in range(18):
            x = (i * 47 + 5) % W
            y = (i * 23 + 70 * t) % (bottom + 4)
            d.rect(img, int(x), int(y), 2, 2, (235, 242, 255))
    if c["fog"]:
        haze = d.mix((120, 130, 146), (206, 214, 226), S["day_f"])
        d.rect(img, 0, 0, W, H, haze, 0.28)
        for k, y in enumerate((6, 12, 18, 23)):
            drift = math.sin(t * 0.25 + k * 2) * 7
            d.rect(img, int(drift) - 6, y, W + 12, 4, haze, 0.30 + 0.08 * (k % 2))
    if c["storm"]:
        slot = int(t * 4)
        if (slot * 2654435761 % 1000) < 75:                               # a flash roughly every 3 s, never rapid
            d.rect(img, 0, 0, W, H, (255, 255, 255), 0.30 if (t * 4) % 1 < 0.5 else 0.14)


def hills(img: Image.Image, S: Dict[str, Any], t: float, trees_from: int = 66) -> None:
    ground = d.mix(d.scale(S["hor"], 0.16), (2, 3, 8), 0.45)
    pts = [(0, H)]
    for x in range(0, W + 1, 2):
        pts.append((x, HORIZON + 2.4 + 1.5 * math.sin(x * 0.075 + 1.0) + 1.0 * math.sin(x * 0.19 + 0.5)))
    pts.append((W, H))
    ImageDraw.Draw(img).polygon(pts, fill=ground)
    dr = ImageDraw.Draw(img)
    for tx, th in ((trees_from + 6, 5), (trees_from + 24, 4), (W - 14, 6), (W - 6, 4)):                  # little pines
        base = HORIZON + 3.5
        dr.polygon([(tx, base - th), (tx - 2, base), (tx + 2, base)], fill=d.scale(ground, 1.2))
    hx = trees_from + 14                                                                               # a house
    dr.rectangle([hx, HORIZON, hx + 4, HORIZON + 3], fill=d.scale(ground, 1.25))
    dr.polygon([(hx - 1, HORIZON), (hx + 2, HORIZON - 3), (hx + 5, HORIZON)], fill=d.scale(ground, 1.1))
    if S["night_f"] > 0.35:
        d.rect(img, hx + 1, HORIZON + 1, 1, 1, (255, 196, 90))
        d.rect(img, hx + 3, HORIZON + 1, 1, 1, (255, 170, 70), 0.8)


def skygrad(img: Image.Image, x: int, y: int, w: int, h: int, top: RGB, hor: RGB, gamma: float = 1.7) -> None:
    """Vertical gradient that keeps the top colour longer, so the warm horizon stays a band instead of muddying the sky."""
    if w <= 0 or h <= 0:
        return
    strip = Image.new("RGB", (1, h))
    strip.putdata([d.mix(top, hor, (j / max(1, h - 1)) ** gamma) for j in range(h)])
    img.paste(strip.resize((w, h), Image.NEAREST), (x, y))


def paint_sky(img: Image.Image, S: Dict[str, Any], y0: int = 0, y1: int = H) -> None:
    skygrad(img, 0, y0, W, y1 - y0, S["top"], S["hor"])


def celestials(img: Image.Image, S: Dict[str, Any], cur: Dict[str, Any], t: float,
               x0: int = 70, x1: int = 122, horizon: float = HORIZON, max_h: float = 22.0) -> None:
    """Sun on its sunrise-to-sunset arc, Moon on its rise-to-set arc, and the glow of a sun just below the edge."""
    height = 8.0 + (max_h - 8.0) * clamp(S["max_alt"] / 65.0)
    if S["sun_frac"] is None and S["elev"] > -9.0:                       # twilight: glow where the sun will rise / set
        gx = x0 - 6 if S["morning"] else x1 + 6
        d.ellipse(img, gx, horizon + 3, 30, 11, S["hor"], clamp(1.0 + S["elev"] / 9.0) * 0.55)
    mf = S["moon_frac"]
    if mf is not None:
        mx, my = arc_xy(mf, x0, x1, horizon, min(height, 17.0) + 2)
        sprite = moon_sprite(6, S["moon"]["illum"], S["moon"]["limb"])
        alpha = 0.30 + 0.70 * S["night_f"]
        if alpha < 1.0:
            faded = sprite.copy()
            faded.putalpha(faded.getchannel("A").point(lambda v: int(v * alpha)))
            sprite = faded

        def paint_moon(im: Image.Image) -> None:
            halo(im, mx, my, 5.0, (200, 215, 255), S["night_f"] * (0.25 + 0.4 * S["moon"]["illum"]))
            im.paste(sprite, (int(mx - sprite.width / 2), int(my - sprite.height / 2)), sprite)
        dimmed(img, clamp(1.0 - S["cover"] * 0.9, 0.12, 1.0), paint_moon)
    if S["sun_frac"] is not None:
        sx, sy = arc_xy(S["sun_frac"], x0, x1, horizon, height)
        dimmed(img, clamp(1.0 - S["cover"] * 0.95, 0.10, 1.0), lambda im: sun_disc(im, sx, sy, S, t))


def scrim(img: Image.Image, width: int = 70, peak: int = 150) -> None:
    global _SCRIM
    if _SCRIM is None or _SCRIM.size != (W, H):
        m = Image.new("L", (W, H), 0)
        dm = ImageDraw.Draw(m)
        for x in range(width):
            dm.line([(x, 0), (x, H)], fill=int(peak * (1.0 - x / width) ** 1.4))
        _SCRIM = m
    img.paste((0, 0, 8), (0, 0), _SCRIM)


def paint_scene(img: Image.Image, S: Dict[str, Any], cur: Dict[str, Any], t: float, x0: int = 70, x1: int = 122) -> None:
    paint_sky(img, S)
    stars(img, S, t)
    celestials(img, S, cur, t, x0, x1)
    clouds(img, S, cur, t)
    precipitation(img, S, t)
    hills(img, S, t, trees_from=x0 - 4)


# ── pages ────────────────────────────────────────────────────────────────────────────────────

def alert_badge(img: Image.Image, world: Dict[str, Any], x: int, y: int) -> None:
    alerts = world.get("alerts") or []
    if not alerts:
        return
    col = sev_color(alerts[0]["severity"])
    pulse = 0.55 + 0.45 * math.sin(world.get("anim", world["ts"]) * 5)
    c = d.scale(col, pulse)
    for row, (a, b) in enumerate(((3, 4), (2, 5), (2, 5), (1, 6), (1, 6), (0, 7), (0, 7))):
        d.rect(img, x + a, y + row, b - a + 1, 1, c)
    d.rect(img, x + 3, y + 2, 2, 3, (10, 6, 6))
    d.rect(img, x + 3, y + 5, 2, 1, (10, 6, 6))


def page_now(world: Dict[str, Any], S: Dict[str, Any]) -> Image.Image:
    img = Image.new("RGB", (W, H), (0, 0, 0))
    cur, t = world["cur"], S["t"]
    paint_scene(img, S, cur, t)
    scrim(img)
    temp = cur.get("temp")
    tx = 4
    if temp is not None:
        txt = str(int(round(temp)))
        col = d.mix(temp_color(temp, cur.get("unit", "F")), (255, 255, 255), 0.55)
        w = shadow_text(img, txt, tx, 2, col, BIG)
        degree(img, tx + w + 1, 3, col)
        hx = tx + w + 9
    else:
        shadow_text(img, "--", tx, 2, d.GRAY, BIG)
        hx = 30
    day = (world.get("daily") or [None])[0]
    if day and day.get("temp") is not None:
        d.text(img, "H%d" % round(day["temp"]), hx, 2, (255, 184, 110), 1)
        if day.get("low") is not None:
            d.text(img, "L%d" % round(day["low"]), hx, 8, (140, 200, 255), 1)
    label = S["cdef"]["label"]
    shadow_text(img, label, 4, 19, d.mix(S["hor"], (255, 255, 255), 0.82), WORD)
    feels = cur.get("feels")
    if feels is not None and temp is not None and abs(feels - temp) >= 2 and day and day.get("temp") is not None:
        d.text(img, "F%d" % round(feels), hx, 14, (214, 220, 236), 1)               # what it feels like, under H / L
    alert_badge(img, world, 118, 1)
    return img


def page_sun(world: Dict[str, Any], S: Dict[str, Any]) -> Image.Image:
    img = Image.new("RGB", (W, H), (0, 0, 0))
    t, info = S["t"], S["info"]
    hy = 14
    paint_sky(img, S, 0, hy + 1)
    stars(img, S, t, ymax=11)
    d.vgrad(img, 0, hy + 1, W, H - hy - 1, d.scale(S["hor"], 0.22), (3, 4, 10))
    x0, x1, height = 10, 117, 5 + 5 * clamp(S["max_alt"] / 65.0)
    frac = S["sun_frac"]
    for i in range(0, 91):
        f = i / 90.0
        x, y = arc_xy(f, x0, x1, hy, height)
        if i % 2:
            continue
        passed = frac is not None and f <= frac
        d.rect(img, int(round(x)), int(round(y)), 1, 1, d.scale((255, 214, 120), 0.35 if passed else 0.9))
    d.rect(img, 3, hy, W - 6, 1, d.mix(S["hor"], (255, 255, 255), 0.35))
    if frac is not None:
        sx, sy = arc_xy(frac, x0, x1, hy, height)
        sun_disc(img, sx, sy, S, t, rays=False)
    else:
        sx, sy = (x0 - 3, hy) if S["morning"] else (x1 + 3, hy)
        d.ellipse(img, sx, hy + 1, 9, 4, d.mix(S["hor"], (255, 190, 100), 0.5), 0.25 * (1 - S["night_f"]))
    d.text(img, "SUNRISE", 5, 18, TINY_GRAY, 1)
    d.text(img, "SUNSET", W - 5, 18, TINY_GRAY, 1, "right")
    d.text(img, fmt_time(info.get("sunrise")), 5, 24, (255, 196, 110), WORD)
    d.text(img, fmt_time(info.get("sunset")), W - 5, 24, (255, 150, 90), WORD, "right")
    now_l, sr, ss = S["dt"], info.get("sunrise"), info.get("sunset")
    if frac is not None and ss:
        l1 = "SET IN " + fmt_span((ss - now_l).total_seconds())
    else:
        nxt = sr if (sr and now_l < sr) else info.get("next_sunrise")
        l1 = "RISE IN " + fmt_span((nxt - now_l).total_seconds()) if nxt else ""
    l2 = "DAY " + fmt_span(info["daylight"]) if info.get("daylight") else ""
    if frac is not None and 0 < S["elev"] < 7 and not S["morning"]:
        l2 = "GOLDEN HOUR"
    elif frac is not None and 0 < S["elev"] < 7:
        l2 = "GOLDEN HOUR"
    d.text(img, l1, W // 2, 19, (235, 238, 248), 1, "center")
    d.text(img, l2, W // 2, 26, TINY_GRAY, 1, "center")
    return img


MOON_EVENT = {"NEW MOON": "NEW", "FIRST QUARTER": "1ST", "FULL MOON": "FULL", "LAST QUARTER": "LAST"}


def page_moon(world: Dict[str, Any], S: Dict[str, Any]) -> Image.Image:
    img = Image.new("RGB", (W, H), (0, 0, 0))
    t, moon = S["t"], S["moon"]
    d.vgrad(img, 0, 0, W, H, (4, 5, 20), (14, 16, 44))
    stars(img, S, t, ymax=30, strength=0.85)
    cx, cy = 17, 16
    halo(img, cx, cy, 11.0, (190, 205, 255), 0.25 + moon["illum"] * 0.45)
    spr = moon_sprite(11, moon["illum"], moon["limb"])
    img.paste(spr, (cx - spr.width // 2, cy - spr.height // 2), spr)
    words = moon["name"].split()
    if len(words) == 2:
        first, second = words
    else:
        first, second = "", words[0]
    x = 38
    if first:
        d.text(img, first, x, 2, (150, 170, 220), 1)
    shadow_text(img, second, x, 8, (244, 240, 222), WORD)
    age_days = moon["age"] * astro.SYNODIC
    d.text(img, "%d%% LIT" % round(moon["illum"] * 100), x, 19, (200, 210, 240), 1)
    d.text(img, "DAY %d" % (int(age_days) + 1), W - 4, 19, TINY_GRAY, 1, "right")
    nr, ns = moon.get("next_rise"), moon.get("next_set")
    if moon["up"] and ns:
        ev = "SET " + fmt_time(ns)
    elif nr:
        ev = "RISE " + fmt_time(nr)
    else:
        ev = ""
    ev_w = d.text(img, ev, x, 26, (160, 220, 255) if moon["up"] else TINY_GRAY, 1)
    if moon.get("events"):
        name, when = moon["events"][0]
        nxt = "%s %s %d" % (MOON_EVENT.get(name, name), when.strftime("%b").upper(), when.day)
        if x + ev_w + 6 < W - 4 - font.text_width(nxt, 1):
            d.text(img, nxt, W - 4, 26, (255, 224, 140), 1, "right")
    return img


def sky_at(world: Dict[str, Any], dt: datetime) -> Tuple[RGB, RGB]:
    elev, az = astro.sun_altaz(dt, world["lat"], world["lon"])
    return sky_colors(elev, az < 180.0)


def page_hourly(world: Dict[str, Any], S: Dict[str, Any]) -> Image.Image:
    img = Image.new("RGB", (W, H), (0, 0, 0))
    step = max(1, int(world.get("hourly_step", 2)))
    hours = [h for h in (world.get("hourly") or []) if h["t"] >= datetime.fromtimestamp(world["ts"] - 1800, UTC)]
    cols = 6
    cw = 21
    frame = int(S["t"] * 2) % 2
    x0 = (W - cols * cw) // 2
    night_now = S["elev"] < -1
    for i in range(cols):
        x = x0 + i * cw
        if i == 0:
            cur = world["cur"]
            item = {"t": datetime.fromtimestamp(world["ts"], UTC), "cond": cur.get("cond"),
                    "temp": cur.get("temp"), "pop": (hours[0]["pop"] if hours else None)}
        else:
            idx = i * step
            if idx >= len(hours):
                continue
            item = hours[idx]
        loc = item["t"].astimezone(world["tz"])
        top, hor = sky_at(world, item["t"])
        d.vgrad(img, x, 0, cw, H, d.scale(top, 0.62), d.scale(hor, 0.62))
        if i:
            d.rect(img, x, 0, 1, H, (0, 0, 0))
        night = astro.sun_altaz(item["t"], world["lat"], world["lon"])[0] < -1
        d.text(img, "NOW" if i == 0 else fmt_hour(loc), x + cw // 2, 1, (235, 238, 248), 1, "center", SHADOW)
        put_icon(img, x + (cw - 13) // 2, 6, icon_name(item["cond"], night), frame)
        if item.get("temp") is not None:
            d.text(img, str(int(round(item["temp"]))), x + cw // 2, 17, d.mix(temp_color(item["temp"], world["cur"].get("unit", "F")), (255, 255, 255), 0.5), WORD, "center", SHADOW)
        pop = item.get("pop")
        if pop:
            bw = max(1, int(round((cw - 5) * clamp(pop / 100.0))))
            d.rect(img, x + 2, 31, cw - 5, 1, (20, 30, 60))
            d.rect(img, x + 2, 31, bw, 1, (96, 168, 255))
            if pop >= 30:
                d.text(img, "%d%%" % round(pop), x + cw // 2, 26, (150, 205, 255), 1, "center")
    return img


def day_tint(cond: str) -> Tuple[RGB, RGB]:
    c = data.cond(cond)
    if c["storm"]:
        return (44, 40, 84), (88, 78, 130)
    if c["snow"]:
        return (96, 112, 150), (170, 186, 216)
    if c["rain"] or c["fog"]:
        return (52, 72, 112), (104, 128, 164)
    if c["cloud"] >= 0.85:
        return (72, 88, 118), (136, 150, 174)
    if c["cloud"] >= 0.3:
        return (52, 104, 176), (144, 190, 232)
    return (40, 110, 210), (126, 190, 244)


def page_daily(world: Dict[str, Any], S: Dict[str, Any]) -> Image.Image:
    img = Image.new("RGB", (W, H), (0, 0, 0))
    days = [x for x in (world.get("daily") or []) if x["t"].astimezone(world["tz"]).date() >= S["dt"].date()]
    cols = 5
    cw = 25
    x0 = (W - cols * cw) // 2
    frame = int(S["t"] * 2) % 2
    for i in range(cols):
        if i >= len(days):
            break
        it = days[i]
        x = x0 + i * cw
        top, hor = day_tint(it["cond"])
        d.vgrad(img, x, 0, cw, H, d.scale(top, 0.6), d.scale(hor, 0.6))
        if i:
            d.rect(img, x, 0, 1, H, (0, 0, 0))
        loc = it["t"].astimezone(world["tz"])
        name = "TODAY" if loc.date() == S["dt"].date() else loc.strftime("%a").upper()
        d.text(img, name, x + cw // 2, 1, (240, 242, 250), 1, "center", SHADOW)
        put_icon(img, x + (cw - 13) // 2, 6, icon_name(it["cond"], False), frame)
        if it.get("temp") is not None:
            d.text(img, str(int(round(it["temp"]))), x + cw // 2, 17, (255, 226, 160), WORD, "center", SHADOW)
        if it.get("low") is not None:
            d.text(img, str(int(round(it["low"]))), x + 3, 26, (140, 196, 255), 1)
        if it.get("pop") and it["pop"] >= 30:
            d.text(img, "%d%%" % round(it["pop"]), x + cw - 2, 26, (150, 205, 255), 1, "right")
    return img


def uv_color(uv: float) -> RGB:
    if uv < 3:
        return (80, 220, 120)
    if uv < 6:
        return (255, 214, 60)
    if uv < 8:
        return (255, 146, 40)
    if uv < 11:
        return (255, 60, 70)
    return (181, 102, 255)


def tile_values(world: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    cur = world["cur"]
    hours = world.get("hourly") or []
    nxt = [h["pop"] for h in hours[:6] if h.get("pop") is not None]
    out: Dict[str, Dict[str, Any]] = {}
    if cur.get("wind") is not None:
        out["wind"] = {"label": "WIND", "value": "%d" % round(cur["wind"]), "cap": ("%s %s" % (data.compass(cur.get("bearing")), cur.get("wind_unit", "").upper())).strip(),
                       "color": (120, 205, 255), "arrow": cur.get("bearing")}
    if cur.get("humidity") is not None:
        out["humidity"] = {"label": "HUMID", "value": "%d" % round(cur["humidity"]), "cap": "PERCENT", "color": (96, 160, 255)}
    uv = cur.get("uv")
    if uv is None:
        uv = next((h["uv"] for h in hours if h.get("uv") is not None), None)
    if uv is not None:
        out["uv"] = {"label": "UV", "value": "%d" % round(uv), "cap": ("LOW", "MODERATE", "HIGH", "V.HIGH", "EXTREME")[min(4, 0 if uv < 3 else 1 if uv < 6 else 2 if uv < 8 else 3 if uv < 11 else 4)],
                     "color": uv_color(uv)}
    if nxt:
        out["rain"] = {"label": "RAIN", "value": "%d" % round(max(nxt)), "cap": "NEXT 6H %", "color": (110, 170, 255)}
    if cur.get("pressure") is not None:
        out["pressure"] = {"label": "PRESS", "value": ("%.2f" % cur["pressure"]) if cur["pressure"] < 100 else "%d" % round(cur["pressure"]),
                           "cap": (cur.get("pressure_unit") or "").upper(), "color": (190, 170, 255)}
    if cur.get("feels") is not None:
        out["feels"] = {"label": "FEELS", "value": "%d" % round(cur["feels"]), "cap": "LIKE", "color": temp_color(cur["feels"], cur.get("unit", "F"))}
    if cur.get("dew") is not None:
        out["dew"] = {"label": "DEW PT", "value": "%d" % round(cur["dew"]), "cap": "POINT", "color": (110, 220, 200)}
    if cur.get("visibility") is not None:
        out["visibility"] = {"label": "VISIB", "value": "%d" % round(cur["visibility"]), "cap": (cur.get("visibility_unit") or "").upper(), "color": (190, 200, 215)}
    return out


def arrow(img: Image.Image, cx: float, cy: float, deg: float, color: RGB, size: float = 4.0) -> None:
    """An arrow pointing the way the wind blows (``deg`` is where it comes FROM)."""
    a = math.radians(deg + 180.0)
    dx, dy = math.sin(a), -math.cos(a)
    px, py = -dy, dx
    tip = (cx + dx * size, cy + dy * size)
    tail = (cx - dx * size, cy - dy * size)
    l = (cx - dx * size * 0.1 + px * size * 0.7, cy - dy * size * 0.1 + py * size * 0.7)
    r = (cx - dx * size * 0.1 - px * size * 0.7, cy - dy * size * 0.1 - py * size * 0.7)
    dr = ImageDraw.Draw(img)
    dr.line([tail, tip], fill=color)
    dr.polygon([tip, l, r], fill=color)


def page_details(world: Dict[str, Any], S: Dict[str, Any]) -> Image.Image:
    img = Image.new("RGB", (W, H), (0, 0, 0))
    vals = tile_values(world)
    prefer = list(world.get("detail_tiles") or ["wind", "humidity", "uv", "rain"])
    want = [k for k in prefer + ["feels", "pressure", "dew", "visibility"] if k in vals]
    want = [k for i, k in enumerate(want) if k not in want[:i]][:4]
    for i, key in enumerate(want):
        v = vals[key]
        x = i * 32
        d.vgrad(img, x, 0, 32, H, d.scale(v["color"], 0.30), d.scale(v["color"], 0.05))
        if i:
            d.rect(img, x, 0, 1, H, (0, 0, 0))
        d.text(img, v["label"], x + 3, 3, d.mix(v["color"], (255, 255, 255), 0.35), 1)
        if v.get("arrow") is not None:
            arrow(img, x + 25, 5, v["arrow"], v["color"], 3.6)
        face = NUM
        if font.text_width(v["value"], face) > 28:
            face = WORD
        th = font.text_height(face)
        shadow_text(img, v["value"], x + 16, 10 + (14 - th) // 2, (250, 250, 255), face, "center")
        d.text(img, font.fit(v["cap"], 29), x + 16, 26, d.mix(v["color"], (255, 255, 255), 0.2), 1, "center")
    return img


def page_alert(world: Dict[str, Any], S: Dict[str, Any], index: int) -> Image.Image:
    alerts = world.get("alerts") or []
    al = alerts[index % len(alerts)]
    t = S["t"]
    col = sev_color(al["severity"])
    pulse = 0.5 + 0.5 * math.sin(t * 3.0)
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d.vgrad(img, 0, 0, W, H, d.scale(col, 0.10 + 0.10 * pulse), d.scale(col, 0.03))
    off = int(t * 12)
    d.hazard(img, 0, 0, W, 2, off, col, (12, 6, 4))
    d.hazard(img, 0, H - 2, W, 2, -off, col, (12, 6, 4))
    d.text(img, "WEATHER ALERT", 4, 4, d.mix(col, (255, 255, 255), 0.4), 1)
    tag = al["severity"].upper() if al["severity"] != "unknown" else ""
    right = ("%d/%d " % (index % len(alerts) + 1, len(alerts)) if len(alerts) > 1 else "") + tag
    d.text(img, right, W - 4, 4, col, 1, "right")
    ev = al["event"].upper()
    face = NUM
    if font.text_width(ev, face) > W - 8:
        face = WORD
    wid = font.text_width(ev, face)
    th = font.text_height(face)
    y = 11 + (14 - th) // 2
    if wid <= W - 8:
        shadow_text(img, ev, W // 2, y, (255, 246, 240), face, "center")
    else:                                                                   # long name: a marquee
        span = wid + 40
        x = 4 - int((t * 26) % span)
        shadow_text(img, ev, x, y, (255, 246, 240), face)
        shadow_text(img, ev, x + span, y, (255, 246, 240), face)
    exp = al.get("expires")
    if exp:
        loc = exp.astimezone(world["tz"])
        day = "" if loc.date() == S["dt"].date() else loc.strftime("%a ").upper()
        d.text(img, "UNTIL " + day + fmt_time(loc), W // 2, 25, (255, 220, 200), 1, "center")
    elif al.get("headline"):
        d.text(img, font.fit(al["headline"].upper(), W - 8), W // 2, 25, TINY_GRAY, 1, "center")
    return img


def notice(now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d.vgrad(img, 0, 0, W, H, (10, 14, 34), (4, 6, 14))
    d.text(img, "NO WEATHER DATA", W // 2, 10, d.GRAY, WORD, "center")
    d.text(img, "CHECK HOME ASSISTANT", W // 2, 22, TINY_GRAY, 1, "center")
    return img


PAGES = {"now": page_now, "sun": page_sun, "moon": page_moon, "hourly": page_hourly,
         "daily": page_daily, "details": page_details}
DEFAULT_PAGES = ["now", "sun", "moon", "hourly", "daily", "details"]


def render(world: Dict[str, Any], page: str, S: Optional[Dict[str, Any]] = None, alert_index: int = 0) -> Image.Image:
    S = S or sky_state(world)
    if page == "alert":
        return page_alert(world, S, alert_index)
    return PAGES.get(page, page_now)(world, S)
