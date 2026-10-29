"""The sky behind the Daylight scene: palette from the real sun, stars, Moon with its true phase, sun, hills.

Same model as the Sky Weather plugin (each plugin carries its own copy of the kit), minus the weather.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from PIL import Image, ImageChops, ImageDraw

import lumen_astro as astro
import lumen_draw as d

RGB = Tuple[int, int, int]
W, H = 128, 32
UTC = timezone.utc
HORIZON = 27

#   elevation, top of sky, horizon (morning), horizon (evening)
KEYS: List[Tuple[float, RGB, RGB, RGB]] = [
    (-18.0, (3, 4, 16),     (7, 9, 28),      (7, 9, 28)),
    (-12.0, (7, 9, 34),     (24, 24, 68),    (24, 22, 62)),
    (-6.0,  (16, 22, 70),   (100, 56, 118),  (120, 52, 92)),
    (-1.5,  (34, 52, 124),  (238, 112, 124), (244, 104, 62)),
    (2.5,   (54, 96, 172),  (255, 176, 116), (255, 156, 70)),
    (9.0,   (58, 120, 198), (206, 206, 228), (252, 208, 138)),
    (26.0,  (40, 112, 212), (116, 182, 240), (116, 182, 240)),
]


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def sky_colors(elev: float, morning: bool) -> Tuple[RGB, RGB]:
    if elev <= KEYS[0][0]:
        return KEYS[0][1], KEYS[0][2]
    if elev >= KEYS[-1][0]:
        return KEYS[-1][1], KEYS[-1][2]
    for (e0, t0, m0, v0), (e1, t1, m1, v1) in zip(KEYS, KEYS[1:]):
        if e0 <= elev <= e1:
            f = (elev - e0) / (e1 - e0)
            h0, h1 = (m0, m1) if morning else (v0, v1)
            return d.mix(t0, t1, f), d.mix(h0, h1, f)
    return KEYS[-1][1], KEYS[-1][2]


def skygrad(img: Image.Image, top: RGB, hor: RGB, gamma: float = 1.7) -> None:
    strip = Image.new("RGB", (1, H))
    strip.putdata([d.mix(top, hor, (j / (H - 1)) ** gamma) for j in range(H)])
    img.paste(strip.resize((W, H), Image.NEAREST), (0, 0))


def state(dt_utc: datetime, lat: float, lon: float, info: Dict) -> Dict:
    elev, az = astro.sun_altaz(dt_utc, lat, lon)
    top, hor = sky_colors(elev, az < 180.0)
    local = dt_utc.astimezone(info["sunrise"].tzinfo) if info.get("sunrise") else dt_utc
    sr, ss = info.get("sunrise"), info.get("sunset")
    sun_frac = None
    if sr and ss and sr <= local <= ss:
        sun_frac = (local - sr).total_seconds() / max(1.0, (ss - sr).total_seconds())
    moon = info["moon"]
    moon_frac = None
    if moon.get("pass"):
        r, s = moon["pass"]
        from datetime import timedelta
        r = r or (local - timedelta(hours=6))
        s = s or (local + timedelta(hours=6))
        moon_frac = clamp((local - r).total_seconds() / max(1.0, (s - r).total_seconds()))
    return {"elev": elev, "az": az, "morning": az < 180.0, "top": top, "hor": hor, "night_f": clamp((-elev - 3.0) / 12.0),
            "sun_frac": sun_frac, "moon_frac": moon_frac, "moon": moon, "max_alt": info.get("max_alt") or 45.0}


_STARS = [(random.Random(11 + i).uniform(0, W - 1), random.Random(91 + i).uniform(0, 22), i) for i in range(34)]


def stars(img: Image.Image, amount: float, t: float) -> None:
    """Twinkling stars, drawn on a black layer and laid over with 'lighter' so they never darken a bright sky."""
    if amount <= 0.03:
        return
    layer = Image.new("RGB", (W, H), (0, 0, 0))
    dr = ImageDraw.Draw(layer)
    for x, y, i in _STARS:
        tw = 0.55 + 0.45 * math.sin(t * (0.9 + (i % 4) * 0.45) + i * 1.7)
        a = amount * tw * (0.5 if i % 3 else 1.0)
        dr.point((int(x), int(y)), fill=d.mix((0, 0, 0), (220, 228, 255), a))
    img.paste(ImageChops.lighter(img, layer))


_MOONS: Dict[Tuple[int, int, int], Image.Image] = {}
MARE = ((-0.30, -0.22, 0.30), (0.26, 0.14, 0.24), (-0.08, 0.52, 0.20), (0.36, -0.40, 0.16), (-0.46, 0.30, 0.15))
MOON_LIT: RGB = (240, 236, 212)
MOON_DARK: RGB = (30, 34, 54)


def moon_sprite(r: int, illum: float, limb: float) -> Image.Image:
    key = (r, int(round(illum * 48)), int(round(limb / 5.0)))
    if key in _MOONS:
        return _MOONS[key]
    iq, lq = key[1] / 48.0, key[2] * 5.0
    sl, cl = math.sin(math.radians(lq)), math.cos(math.radians(lq))
    size = 2 * r + 3
    c = size / 2.0
    n = 3
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for py in range(size):
        for px_ in range(size):
            lit = inside = shade = 0.0
            for sy in range(n):
                for sx in range(n):
                    u = (px_ + (sx + 0.5) / n - c) / r
                    v = (py + (sy + 0.5) / n - c) / r
                    if u * u + v * v > 1.0:
                        continue
                    inside += 1
                    xr, yr = u * sl - v * cl, u * cl + v * sl
                    if xr > (1.0 - 2.0 * iq) * math.sqrt(max(0.0, 1.0 - yr * yr)) + 1e-6:
                        lit += 1
                        if any((u - mx) ** 2 + (v - my) ** 2 < mr * mr for mx, my, mr in MARE):
                            shade += 1
            if inside == 0:
                continue
            col = d.mix(MOON_DARK, MOON_LIT, lit / inside)
            if lit:
                col = d.mix(col, d.scale(col, 0.68), shade / lit)
            img.putpixel((px_, py), col + (int(255 * inside / (n * n)),))
    _MOONS[key] = img
    return img


def halo(img: Image.Image, cx: float, cy: float, r: float, color: RGB, strength: float) -> None:
    for k, a in ((2.2, 0.05), (1.6, 0.08), (1.2, 0.12)):
        d.ellipse(img, cx, cy, r * k, r * k * 0.9, color, a * strength)


def arc_xy(frac: float, x0: int, x1: int, horizon: float, height: float) -> Tuple[float, float]:
    return x0 + (x1 - x0) * frac, horizon - math.sin(math.pi * frac) * height


def celestials(img: Image.Image, S: Dict, t: float, x0: int = 8, x1: int = 120) -> None:
    height = 8.0 + 11.0 * clamp(S["max_alt"] / 65.0)
    if S["sun_frac"] is None and S["elev"] > -9.0:                        # glow where the sun is about to rise or has set
        gx = x0 - 8 if S["morning"] else x1 + 8
        d.ellipse(img, gx, HORIZON + 3, 34, 11, S["hor"], clamp(1.0 + S["elev"] / 9.0) * 0.55)
    if S["moon_frac"] is not None:
        mx, my = arc_xy(S["moon_frac"], x0, x1, HORIZON, min(height, 17.0) + 2)
        spr = moon_sprite(6, S["moon"]["illum"], S["moon"]["limb"])
        alpha = 0.30 + 0.70 * S["night_f"]
        if alpha < 1.0:
            spr = spr.copy()
            a_ch = spr.getchannel("A")
            spr.putalpha(ImageChops.multiply(a_ch, Image.new("L", a_ch.size, max(0, min(255, int(alpha * 255))))))
        halo(img, mx, my, 5.0, (200, 215, 255), S["night_f"] * (0.25 + 0.4 * S["moon"]["illum"]))
        img.paste(spr, (int(mx - spr.width / 2), int(my - spr.height / 2)), spr)
    if S["sun_frac"] is not None:
        sx, sy = arc_xy(S["sun_frac"], x0, x1, HORIZON, height)
        low = clamp(1.0 - S["elev"] / 18.0)
        core = d.mix((255, 244, 176), (255, 138, 56), low)
        halo(img, sx, sy, 4.0, d.mix((255, 220, 120), (255, 110, 50), low), 1.0 + low)
        d.ellipse(img, sx, sy, 3.6, 3.6, core, 1.0)
        d.ellipse(img, sx - 0.6, sy - 0.7, 1.8, 1.8, d.mix(core, (255, 255, 230), 0.6), 0.8)
        for i in range(8):
            a = t * 0.35 + i * math.pi / 4
            rr = 6.2 + 0.6 * math.sin(t * 2 + i)
            d.rect(img, int(round(sx + math.cos(a) * rr)), int(round(sy + math.sin(a) * rr)), 1, 1,
                   d.mix(core, (255, 255, 255), 0.2), 0.7)


def hills(img: Image.Image, S: Dict) -> None:
    ground = d.mix(d.scale(S["hor"], 0.16), (2, 3, 8), 0.45)
    pts = [(0, H)]
    for x in range(0, W + 1, 2):
        pts.append((x, HORIZON + 2.6 + 1.5 * math.sin(x * 0.075 + 1.0) + 1.0 * math.sin(x * 0.19 + 0.5)))
    pts.append((W, H))
    ImageDraw.Draw(img).polygon(pts, fill=ground)
