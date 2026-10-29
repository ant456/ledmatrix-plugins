"""Lumen Clock scenes (128x32). Each scene paints one frame from a ``ctx`` dict:

    now      animation clock in seconds (real time, always moving)
    dt       the (local) time being shown        text / prev / age   "12:45", the previous text, seconds since it changed
    sec      seconds into the minute, with the fraction (0..60)
    dow, md, ampm, temp   caption pieces ("THU", "OCT 29", "PM", "65")
    face, face_s, face_t  big / smaller / temperature typefaces
    lat, lon, info        place and sky facts, for the Daylight scene (None when unknown)
"""

from __future__ import annotations

import math
from datetime import timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

import lumen_draw as d
import lumen_sky as sky
import lumen_type as ty

RGB = Tuple[int, int, int]
W, H = 128, 32
INFO_RIGHT = 125
SHADOW: RGB = (2, 4, 14)


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


# ── shared pieces ────────────────────────────────────────────────────────────

def tiny(img: Image.Image, text: str, x: int, y: int, color: RGB, align: str = "left") -> None:
    d.text(img, text, x, y, color, 1, align)


_POOL: Optional[tuple] = None
_TEMP_MASKS: Dict[Tuple[int, str], tuple] = {}


def pool(img: Image.Image, x0: int, alpha: float = 0.42) -> None:
    """A soft dark pool behind the caption column, so small text reads on bright skies and water."""
    global _POOL
    if _POOL is None or _POOL[0] != (x0, alpha):
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).rectangle([x0 - 1, 1, W + 8, H + 8], fill=255)
        _POOL = ((x0, alpha), ty.scale_mask(m.filter(ImageFilter.GaussianBlur(4)), alpha))
    img.paste((2, 4, 16), (0, 0), _POOL[1])


def caption(img: Image.Image, ctx: Dict[str, Any], x_left: int, label: RGB, value: RGB, accent: RGB) -> None:
    """Weekday + AM/PM, the date, and the outdoor temperature, in a column from ``x_left`` to the right edge."""
    pool(img, x_left)
    tiny(img, ctx["dow"], x_left, 4, value)
    if ctx["ampm"]:
        tiny(img, ctx["ampm"], INFO_RIGHT, 4, accent, "right")
    tiny(img, ctx["md"], x_left, 11, label)
    if ctx.get("temp") is not None:
        key = (id(ctx["face_t"]), str(ctx["temp"]))
        if key not in _TEMP_MASKS:
            if len(_TEMP_MASKS) > 16:
                _TEMP_MASKS.clear()
            _TEMP_MASKS[key] = ctx["face_t"].mask_of(str(ctx["temp"]))
        m, base = _TEMP_MASKS[key]
        x = INFO_RIGHT - m.width + 2 - 4
        y = 19 + (10 - base) + 2
        img.paste(Image.new("RGB", m.size, value), (x, y), m)
        for dx, dy in ((1, 0), (2, 0), (0, 1), (3, 1), (1, 2), (2, 2)):           # degree ring
            d.rect(img, INFO_RIGHT - 3 + dx - 2, 19 + dy, 1, 1, value)


def digits(ctx: Dict[str, Any], cx: float, cy: float, face: Optional[ty.Face] = None):
    face = face or ctx["face"]
    return ty.time_mask(face, ctx["text"], ctx["prev"], ctx["age"], cx, cy)


def lerp_color(a: RGB, b: RGB, t: float) -> RGB:
    return d.mix(a, b, clamp(t))


# ── 1. Aurora ────────────────────────────────────────────────────────────────

AURORA: List[RGB] = [(40, 255, 150), (70, 205, 255), (170, 100, 255)]


def aurora_sky(img: Image.Image, t: float) -> None:
    d.vgrad(img, 0, 0, W, H, (2, 4, 14), (6, 14, 34))
    sky.stars(img, 0.9, t)
    for k, col in enumerate(AURORA):
        low_pts: List[Tuple[float, float]] = []
        top_pts: List[Tuple[float, float]] = []
        for xi in range(0, W + 6, 5):
            u = xi / float(W)
            low = 21 + 3.2 * math.sin(u * 5.0 + t * 0.32 + k * 2.1) + 1.7 * math.sin(u * 11.0 + t * 0.7 + k * 1.3)
            hgt = 9 + 5 * math.sin(u * 3.2 + t * 0.41 + k * 1.9) + 2.2 * math.sin(u * 9.0 - t * 0.5 + k)
            low_pts.append((xi, low))
            top_pts.append((xi, low - max(2.5, hgt)))
        mask = Image.new("L", (W, H), 0)
        dm = ImageDraw.Draw(mask)
        steps = 5
        for j in range(steps, 0, -1):                                    # tall faint veil first, bright foot last
            frac = j / float(steps)
            poly = [(x, y_low - (y_low - y_top) * frac) for (x, y_low), (_, y_top) in zip(low_pts, top_pts)]
            poly += [(x, y_low) for x, y_low in reversed(low_pts)]
            dm.polygon(poly, fill=int(40 + 190 * (1.0 - frac) ** 1.4))
        mask = mask.filter(ImageFilter.GaussianBlur(1.3))
        amp = 0.50 + 0.35 * math.sin(t * 0.21 + k * 1.7)
        mask = ty.scale_mask(mask, amp)
        light = Image.composite(Image.new("RGB", (W, H), col), Image.new("RGB", (W, H), (0, 0, 0)), mask)
        img.paste(ImageChops.add(img, light))


def scene_aurora(ctx: Dict[str, Any]) -> Image.Image:
    t = ctx["now"]
    img = Image.new("RGB", (W, H))
    aurora_sky(img, t)
    mask, box = digits(ctx, 50, 15.5)
    tint = lerp_color(AURORA[0], AURORA[1], 0.5 + 0.5 * math.sin(t * 0.3))
    ty.shadow(img, mask, (0, 0), (0, 6, 20), 0.55, 2.4)
    ty.glow(img, mask, tint, 3.2, 0.95)
    ty.fill(img, mask, ty.vgradient((255, 255, 255), d.mix((200, 255, 230), tint, 0.35), box[1], box[3]))
    caption(img, ctx, 99, (120, 170, 190), (225, 245, 250), tint)
    if ctx["show_seconds"]:                                                # a bright thread along the foot, one minute long
        x = int(W * ctx["sec"] / 60.0)
        d.rect(img, 0, H - 1, x, 1, d.scale(tint, 0.55))
        d.rect(img, max(0, x - 1), H - 2, 3, 2, d.mix(tint, (255, 255, 255), 0.7))
    return img


# ── 2. Tide ──────────────────────────────────────────────────────────────────

def tide_level(sec: float) -> float:
    """Water height in px: climbs through the minute, then drains in the first second and a bit."""
    if sec < 1.2:
        return 17.0 + (3.0 - 17.0) * ty.ease(sec / 1.2)
    return 3.0 + 14.0 * (sec - 1.2) / (60.0 - 1.2)


def scene_tide(ctx: Dict[str, Any]) -> Image.Image:
    t, sec = ctx["now"], ctx["sec"]
    img = Image.new("RGB", (W, H))
    d.vgrad(img, 0, 0, W, H, (14, 10, 38), (52, 44, 96))                  # dusk above the water, so the water stands out
    sky.stars(img, 0.55, t)
    mask, box = digits(ctx, 50, 14.5)
    ty.shadow(img, mask, (0, 0), (0, 4, 16), 0.5, 2.0)
    ty.glow(img, mask, (130, 190, 255), 2.4, 0.6)
    ty.fill(img, mask, ty.vgradient((255, 255, 255), (205, 225, 255), box[1], box[3]))

    lvl = tide_level(sec)
    yw = H - lvl
    amp = 1.0 + 0.9 * max(0.0, 1.0 - sec / 2.5)                         # ripples from the drain
    def surface(x: float, phase: float, a: float) -> float:
        return yw + a * (1.5 * math.sin(x * 0.16 + t * 2.0 + phase) + 0.9 * math.sin(x * 0.31 - t * 1.4 + phase * 2))
    body = ty.vgradient((50, 190, 235), (4, 40, 110), int(yw), H)
    for phase, a, strength in ((1.7, 0.9, 120), (0.0, 1.0, 175)):         # a darker wave behind, the front wave over it
        pts = [(x, surface(x, phase, amp * a) - (1.3 if phase else 0)) for x in range(0, W + 3, 2)]
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).polygon(pts + [(W + 3, H), (0, H)], fill=strength)
        img.paste(body, (0, 0), m)
    ImageDraw.Draw(img).line([(x, surface(x, 0.0, amp)) for x in range(0, W + 2, 2)], fill=(190, 236, 255), width=1)
    dr = ImageDraw.Draw(img)
    for i in range(9):                                                    # bubbles climbing to the surface
        span = max(3.0, lvl)
        by = H - ((t * (5 + i % 3 * 2) + i * 7.3) % span)
        bx = (i * 29 + 11) % W + 1.5 * math.sin(t * 1.3 + i)
        if by > surface(bx, 0.0, amp) + 1:
            dr.point((int(bx), int(by)), fill=(150, 205, 255))
    caption(img, ctx, 99, (150, 190, 230), (235, 246, 255), (140, 220, 255))
    return img


# ── 3. Neon ──────────────────────────────────────────────────────────────────

_BRICK: Optional[Image.Image] = None
_VIGNETTE: Optional[Image.Image] = None


def brick_wall() -> Image.Image:
    global _BRICK, _VIGNETTE
    if _BRICK is None:
        img = Image.new("RGB", (W, H), (19, 13, 27))
        dr = ImageDraw.Draw(img)
        for row in range(0, H, 4):
            dr.line([(0, row), (W, row)], fill=(6, 4, 10))
            for x in range((row // 4 % 2) * 6, W, 12):
                dr.line([(x, row), (x, row + 3)], fill=(6, 4, 10))
        _BRICK = img
        v = Image.new("L", (W, H), 0)
        dv = ImageDraw.Draw(v)
        for i in range(14):
            dv.rectangle([i, i // 2, W - 1 - i, H - 1 - i // 2], outline=int(150 * (1 - i / 14.0) ** 1.6))
        _VIGNETTE = v.filter(ImageFilter.GaussianBlur(2))
    return _BRICK.copy()


def comet(img: Image.Image, pos: float, color: RGB) -> None:
    """A bright dot with a fading tail running once round the edge of the panel per minute."""
    per = 2 * (W - 1) + 2 * (H - 1)
    def xy(s: float) -> Tuple[int, int]:
        s %= per
        if s < W - 1:
            return int(s), 0
        s -= W - 1
        if s < H - 1:
            return W - 1, int(s)
        s -= H - 1
        if s < W - 1:
            return W - 1 - int(s), H - 1
        s -= W - 1
        return 0, H - 1 - int(s)
    head = pos * per
    for j in range(18):
        x, y = xy(head - j)
        a = (1.0 - j / 18.0) ** 1.6
        base = img.getpixel((x, y))
        c = d.mix((0, 0, 0), d.mix(color, (255, 255, 255), 0.6 if j < 2 else 0.0), a)
        img.putpixel((x, y), tuple(min(255, b + cc) for b, cc in zip(base, c)))


_NEON_PARTS: Dict[int, tuple] = {}


def scene_neon(ctx: Dict[str, Any]) -> Image.Image:
    t = ctx["now"]
    img = brick_wall()
    mask, box = digits(ctx, 50, 15.5)
    colon_x = box[0] + ctx["face"].width(ctx["text"].split(":")[0])
    parts = _NEON_PARTS.get(id(mask))
    if parts is None or parts[0] is not mask:                        # hours / minutes / cores, rebuilt only when the mask is
        left = mask.copy()
        left.paste(0, (colon_x + ctx["face"].colon_w // 2, 0, W, H))
        right = ImageChops.subtract(mask, left)
        cores = [p.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.4)) for p in (left, right)]
        if len(_NEON_PARTS) > 8:
            _NEON_PARTS.clear()
        _NEON_PARTS[id(mask)] = parts = (mask, left, right, cores)
    _, left, right, cores = parts
    flick = 1.0
    if int(t * 9) * 2654435761 % 211 < 5:                                  # now and then a tube stutters
        flick = 0.35
    pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.cos(2 * math.pi * t))           # the colon breathes once a second
    for part, core, col, k in ((left, cores[0], (255, 55, 170), 1.0), (right, cores[1], (50, 215, 255), flick)):
        if part.getbbox() is None:
            continue
        strength = k
        ty.glow(img, part, col, 7.0, 0.55 * strength)
        ty.glow(img, part, col, 3.2, 0.85 * strength)
        ty.fill(img, part, d.scale(col, 0.92 * strength + 0.08))
        ty.fill(img, core, d.mix(col, (255, 255, 255), 0.72), 0.95 * strength)
    # the colon belongs to neither half: breathe it separately
    colon_box = (colon_x - 1, 0, colon_x + ctx["face"].colon_w + 2, H)
    cm = Image.new("L", (W, H), 0)
    cm.paste(mask.crop(colon_box), colon_box[:2])
    if cm.getbbox() is not None:
        ty.glow(img, cm, (255, 150, 220), 4.0, 0.5 * pulse)
    img.paste(ImageChops.multiply(img, Image.merge("RGB", [ImageChops.invert(_VIGNETTE)] * 3)))
    comet(img, ctx["sec"] / 60.0, (120, 235, 255))
    caption(img, ctx, 99, (150, 70, 130), (255, 130, 205), (90, 225, 255))
    return img


# ── 4. Daylight ──────────────────────────────────────────────────────────────

def scene_daylight(ctx: Dict[str, Any]) -> Image.Image:
    t = ctx["now"]
    img = Image.new("RGB", (W, H))
    S = sky.state(ctx["dt"].astimezone(timezone.utc), ctx["lat"], ctx["lon"], ctx["info"])
    sky.skygrad(img, S["top"], S["hor"])
    sky.stars(img, S["night_f"], t)
    sky.celestials(img, S, t)
    sky.hills(img, S)
    mask, box = digits(ctx, 49, 13.5, ctx["face_s"])
    ty.shadow(img, mask, (0, 0), (2, 6, 24), 0.62, 2.2)                    # a soft dark pool so the digits read on any sky
    ty.shadow(img, mask, (1, 1), (2, 4, 16), 0.6, 0.6)
    warm = d.mix((255, 255, 255), S["hor"], 0.18 * (1.0 - S["night_f"]))
    ty.fill(img, mask, ty.vgradient((255, 255, 255), warm, box[1], box[3]))
    caption(img, ctx, 99, d.mix(S["hor"], (255, 255, 255), 0.6), (250, 250, 255), d.mix(S["hor"], (255, 255, 255), 0.8))
    return img


# ── 5. Orbit ─────────────────────────────────────────────────────────────────

_DIAL_FACE: Optional[Image.Image] = None


def dial_face() -> Image.Image:
    """The part of the dial that never moves (bezel and ticks), at 4x; drawn once."""
    global _DIAL_FACE
    if _DIAL_FACE is not None:
        return _DIAL_FACE
    S, size, R = 4, 34, 15.0
    big = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
    dr = ImageDraw.Draw(big)
    c = size * S / 2.0
    dr.ellipse([c - R * S, c - R * S, c + R * S, c + R * S], fill=(9, 12, 26, 255), outline=(110, 130, 185, 255), width=int(S * 0.9))
    for i in range(60):
        a = math.radians(i * 6)
        major = i % 5 == 0
        r0, r1 = (R - 4.4, R - 1.5) if major else (R - 2.6, R - 1.5)
        dr.line([(c + math.sin(a) * r0 * S, c - math.cos(a) * r0 * S), (c + math.sin(a) * r1 * S, c - math.cos(a) * r1 * S)],
                fill=(240, 244, 255, 255) if major else (130, 145, 195, 255), width=int(S * (1.5 if major else 0.8)))
    _DIAL_FACE = big
    return big


def dial(ctx: Dict[str, Any], accent: RGB) -> Image.Image:
    """An analog face, drawn at 4x and shrunk so the hands are smooth; the second hand sweeps with a comet tail."""
    S, size, R = 4, 34, 15.0
    big = dial_face().copy()
    dr = ImageDraw.Draw(big)
    c = size * S / 2.0
    sec, minute, hour = ctx["sec"], ctx["dt"].minute + ctx["sec"] / 60.0, ctx["dt"].hour % 12 + ctx["dt"].minute / 60.0
    for j in range(18, 0, -1):                                           # the comet tail behind the second hand
        a0 = (sec - j * 2.2) * 6 - 90
        shade = (1.0 - j / 18.0) ** 1.8
        dr.arc([c - (R - 2.2) * S, c - (R - 2.2) * S, c + (R - 2.2) * S, c + (R - 2.2) * S], a0, a0 + 2.4 * 6,
               fill=tuple(int(v * shade) for v in accent) + (int(255 * shade),), width=int(S * 1.6))

    def hand(angle_deg: float, length: float, width: float, color, tail: float = 0.0) -> None:
        a = math.radians(angle_deg)
        dr.line([(c - math.sin(a) * tail * S, c + math.cos(a) * tail * S),
                 (c + math.sin(a) * length * S, c - math.cos(a) * length * S)], fill=color, width=int(width * S))
    hand(hour * 30, R * 0.50, 2.4, (245, 248, 255, 255))
    hand(minute * 6, R * 0.78, 1.7, (245, 248, 255, 255))
    hand(sec * 6, R * 0.86, 0.9, accent + (255,), tail=2.5)
    dr.ellipse([c - 1.6 * S, c - 1.6 * S, c + 1.6 * S, c + 1.6 * S], fill=accent + (255,))
    return big.resize((size, size), Image.LANCZOS)


def scene_orbit(ctx: Dict[str, Any]) -> Image.Image:
    t = ctx["now"]
    img = Image.new("RGB", (W, H))
    d.vgrad(img, 0, 0, W, H, (12, 16, 34), (5, 7, 18))
    d.ellipse(img, 17, 16, 30, 24, (40, 60, 130), 0.20)                    # a soft glow behind the dial
    accent: RGB = (255, 120, 60)
    face = dial(ctx, accent)
    img.paste(face, (0, -1), face)
    mask, box = ty.time_mask(ctx["face_s"], ctx["text"], ctx["prev"], ctx["age"], 82, 12.5)
    ty.shadow(img, mask, (1, 1), (0, 0, 8), 0.7, 0.8)
    ty.glow(img, mask, (110, 150, 255), 2.4, 0.55)
    ty.fill(img, mask, ty.vgradient((255, 255, 255), (200, 215, 255), box[1], box[3]))
    d.text(img, "%s %s" % (ctx["dow"], ctx["md"]), 40, 25, (150, 165, 205), 1)
    if ctx["ampm"]:
        d.text(img, ctx["ampm"], box[2] + 3, box[1] + 1, accent, 1)
    if ctx.get("temp") is not None:
        d.text(img, str(ctx["temp"]), INFO_RIGHT - 6, 25, (225, 235, 255), 1, "right")
        degree_x = INFO_RIGHT - 4
        for dx, dy in ((1, 0), (2, 0), (0, 1), (3, 1), (1, 2), (2, 2)):
            d.rect(img, degree_x + dx - 1, 24 + dy, 1, 1, (225, 235, 255))
    return img


SCENES: Dict[str, Callable[[Dict[str, Any]], Image.Image]] = {
    "aurora": scene_aurora, "tide": scene_tide, "neon": scene_neon, "daylight": scene_daylight, "orbit": scene_orbit}
NEEDS_PLACE = {"daylight"}
