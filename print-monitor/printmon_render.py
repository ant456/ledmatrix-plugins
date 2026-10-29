"""Print Monitor screens, drawn in the NBA / NFL Live arcade style (128x32).

* ``printing``  -- model thumbnail tile, big percent, time left and ETA, nozzle and
                   bed temperatures, layer count and a segmented progress gauge in
                   the filament colour.
* ``preparing`` -- heating / levelling / loading: the stage in big letters and a
                   heat gauge.
* ``paused``    -- amber, blinking, with the reason.
* ``failed``    -- hazard stripes and a blinking PRINT FAILED.
* ``burst``     -- PRINT DONE! sunburst and confetti.
* ``done_card`` / ``idle`` -- the quiet states.
"""

from typing import Any, Dict, Optional, Tuple, Union

from PIL import Image

import bdf_face
import printmon_data as pd
import printmon_draw as d
import printmon_font as font
from bdf_face import BdfFace

RGB = Tuple[int, int, int]

W, H = 128, 32
TILE_W = 32
HOLD = 1.2

CYAN: RGB = (70, 200, 255)
NOZZLE: RGB = (255, 150, 40)
BED: RGB = (255, 90, 70)

BIG: Union[int, BdfFace] = 3      # percent digits
MID: Union[int, BdfFace] = 2      # time left, small labels
LABEL: Union[int, BdfFace] = 2    # stage and state words
HERO: Union[int, BdfFace] = 2     # PRINT DONE!


def load_faces(dirs=()) -> None:
    global BIG, MID, LABEL, HERO
    BIG = bdf_face.load("10x20", dirs) or 3
    MID = bdf_face.load("6x10", dirs) or 2
    LABEL = bdf_face.load("7x13B", dirs) or 2
    HERO = bdf_face.load("9x15B", dirs) or 2


def accent_for(color: Optional[RGB]) -> RGB:
    """The filament colour, lifted so dark filaments (black, brown) still glow on the panel."""
    return d.lift(color, 150) if color else CYAN


# ----------------------------------------------------------------------
# pieces
# ----------------------------------------------------------------------

def tile(accent: RGB, cover: Optional[Image.Image], blink: bool = False) -> Image.Image:
    """The left tile: the model's render on a gradient, or "3D" when there is no cover."""
    img = Image.new("RGB", (TILE_W, H), d.BLACK)
    d.vgrad(img, 0, 0, TILE_W, H, d.scale(accent, 0.42), d.scale(accent, 0.06))
    if cover is not None:
        art = cover.copy()
        art.thumbnail((TILE_W - 4, H - 4))
        img.paste(art, ((TILE_W - art.width) // 2, (H - art.height) // 2), art)
    else:
        d.big_number(img, "3D", TILE_W // 2, (H - font.text_height(3)) // 2, accent, 3, "center")
    return img


def gauge(img: Image.Image, x: int, y: int, w: int, h: int, frac: float, color: RGB, t: float = 0.0) -> None:
    """Segmented progress: the segment at the edge of the fill breathes."""
    seg, gap = 4, 1
    n = max(1, (w + gap) // (seg + gap))
    pos = max(0.0, min(1.0, frac)) * n
    full = int(pos)
    for i in range(n):
        sx = x + i * (seg + gap)
        if i < full:
            d.rect(img, sx, y, seg, h, color)
        elif i == full and pos - full > 0.05:
            pulse = 0.55 + 0.45 * abs(((t * 1.2) % 2) - 1)
            d.rect(img, sx, y, seg, h, d.scale(color, 0.35 + 0.65 * pulse * (pos - full)))
        else:
            d.rect(img, sx, y, seg, h, d.scale(color, 0.14))


def temps(img: Image.Image, x: int, y: int, s: Dict[str, Any]) -> None:
    def pair(label: str, cur: Optional[float], tgt: Optional[float]) -> str:
        if cur is None:
            return "%s--" % label
        if tgt and tgt > 0:
            return "%s%d/%d" % (label, round(cur), round(tgt))
        return "%s%d" % (label, round(cur))
    w = d.text(img, pair("N", s["nozzle"], s["nozzle_t"]), x, y, NOZZLE)
    d.text(img, pair("B", s["bed"], s["bed_t"]), x + w + 4, y, BED)


# ----------------------------------------------------------------------
# screens
# ----------------------------------------------------------------------

def printing(s: Dict[str, Any], tile_img: Image.Image, accent: RGB, t: float, now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    img.paste(tile_img, (0, 0))
    d.rect(img, TILE_W, 0, 2, H, accent)
    x0 = TILE_W + 2
    pw = W - x0
    d.hgrad(img, x0, 0, pw, H, d.scale(accent, 0.16), d.scale(accent, 0.03))

    pct = s["progress"] or 0.0
    big_h = font.text_height(BIG)
    nw = d.big_number(img, str(int(pct)), x0 + 3, 2, d.WHITE, BIG, "left")
    d.text(img, "%", x0 + 3 + nw + 2, 2 + big_h - font.text_height(MID), accent, MID, "left", d.BLACK)

    d.text(img, pd.fmt_remaining(s["remaining_s"]), W - 3, 2, d.WHITE, MID, "right", d.BLACK)
    end = pd.eta(s, now)
    if end:
        d.text(img, "ETA " + pd.fmt_clock(end), W - 3, 14, d.GRAY, 1, "right")

    temps(img, x0 + 3, 20, s)
    if s["layer"] is not None and s["layers"]:
        d.text(img, "%d/%d" % (s["layer"], s["layers"]), W - 3, 20, d.GRAY, 1, "right")
    gauge(img, x0 + 2, 26, pw - 4, 5, pct / 100.0, accent, t)
    return img


def preparing(s: Dict[str, Any], tile_img: Image.Image, accent: RGB, t: float, now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    img.paste(tile_img, (0, 0))
    d.rect(img, TILE_W, 0, 2, H, d.ORANGE)
    x0 = TILE_W + 2
    pw = W - x0
    d.hgrad(img, x0, 0, pw, H, d.scale(d.ORANGE, 0.30), d.scale(d.ORANGE, 0.06))
    label = pd.prep_label(s)
    tw = font.text_width(label, LABEL)
    y = 3
    if tw <= pw - 8:
        d.text(img, label, x0 + 5, y, d.WHITE, LABEL, "left", d.BLACK)
    else:                                                    # rest, crawl once, rest
        off = int(min(max(0.0, t - HOLD) * 40, tw - (pw - 8)))
        row = img.crop((x0 + 5, y - 1, x0 + pw - 3, y + font.text_height(LABEL) + 1))
        d.text(row, label, -off, 1, d.WHITE, LABEL, "left", d.BLACK)
        img.paste(row, (x0 + 5, y - 1))
    temps(img, x0 + 5, 19, s)
    target = s["nozzle_t"] if (s["nozzle_t"] or 0) > 0 else None
    frac = (s["nozzle"] / target) if (target and s["nozzle"] is not None) else 0.0
    gauge(img, x0 + 2, 26, pw - 4, 5, frac, d.ORANGE, t)
    return img


def paused(s: Dict[str, Any], tile_img: Image.Image, now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    img.paste(tile_img, (0, 0))
    amber = d.YELLOW
    on = int(now * 1.6) % 2 == 0
    d.rect(img, TILE_W, 0, 2, H, amber)
    x0 = TILE_W + 2
    pw = W - x0
    d.hgrad(img, x0, 0, pw, H, d.scale(amber, 0.30 if on else 0.16), d.scale(amber, 0.06))
    d.text(img, "PAUSED", x0 + 5, 3, d.WHITE if on else d.scale(d.WHITE, 0.55), LABEL, "left", d.BLACK)
    d.text(img, font.fit(pd.pause_label(s), pw - 8), x0 + 5, 19, amber)
    pct = s["progress"]
    if pct is not None:
        gauge(img, x0 + 2, 26, pw - 4, 5, pct / 100.0, amber)
    return img


def failed(s: Dict[str, Any], now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    off = int(now * 10)
    d.hazard(img, 0, 0, W, 3, off)
    d.hazard(img, 0, H - 3, W, 3, -off)
    if int(now * 2.4) % 2 == 0:
        d.big_number(img, "PRINT FAILED", W // 2, 13 - font.text_height(HERO) // 2, d.RED, HERO, "center")
    d.text(img, font.fit(s["task"].upper() or "CHECK THE PRINTER", W - 8), W // 2, 22, d.GRAY, 1, "center")
    return img


def burst(s: Dict[str, Any], tile_img: Image.Image, t: float) -> Image.Image:
    """The print finished: sunburst, confetti, the news."""
    img = Image.new("RGB", (W, H), d.BLACK)
    green = d.GREEN
    d.rays(img, 0, 0, W, H, W * 0.62, H / 2, t * 1.4, green, d.scale(green, 0.5))
    d.confetti(img, TILE_W, 0, W - TILE_W, H, t, [green, d.GOLD, d.WHITE, d.ICE], count=18)
    img.paste(tile_img, (0, 0))
    x0 = TILE_W + 2
    pw = W - x0
    d.text(img, "PRINT DONE!", x0 + pw // 2, 4, d.GOLD if int(t * 3) % 2 == 0 else d.WHITE, 1, "center", d.BLACK)
    d.big_number(img, "DONE!", x0 + pw // 2, 10, d.GREEN, HERO, "center")
    d.text(img, font.fit(s["task"].upper(), pw - 4), x0 + pw // 2, 26, d.WHITE, 1, "center", d.BLACK)
    return img


def done_card(s: Dict[str, Any], tile_img: Image.Image, now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    img.paste(tile_img, (0, 0))
    d.rect(img, TILE_W, 0, 2, H, d.GREEN)
    x0 = TILE_W + 2
    pw = W - x0
    d.hgrad(img, x0, 0, pw, H, d.scale(d.GREEN, 0.30), d.scale(d.GREEN, 0.06))
    d.text(img, "PRINT DONE", x0 + 5, 3, d.WHITE, LABEL, "left", d.BLACK)
    d.text(img, font.fit(s["task"].upper() or "-", pw - 8), x0 + 5, 19, d.GRAY)
    stats = []
    if s["weight"]:
        stats.append("%.1fG" % s["weight"])
    if s["layers"]:
        stats.append("%d LAYERS" % s["layers"])
    d.text(img, "  ".join(stats), x0 + 5, 25, d.GREEN)
    return img


def idle(s: Dict[str, Any], now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    d.hgrad(img, 0, 0, W, H, d.scale(CYAN, 0.14), d.scale(CYAN, 0.03))
    d.big_number(img, "PRINTER IDLE", W // 2, 8, d.scale(CYAN, 0.9), HERO, "center")
    if s.get("task"):
        d.text(img, font.fit("LAST " + s["task"].upper(), W - 8), W // 2, 24, d.GRAY, 1, "center")
    return img
