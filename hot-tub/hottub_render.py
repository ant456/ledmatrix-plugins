"""Hot Tub screens, drawn in the NBA/NFL Live arcade style.

Two screens, both 128x32:

* ``tub``   -- header band (status light, status word, set point), a gradient
               temperature panel with arcade numerals, heater / filter / jets
               indicators, and a cross-section of the tub whose water rises
               toward the set point, with a live wave, rising bubbles and steam.
* ``burst`` -- a spinning sunburst and confetti when the water reaches
               temperature (the way the NBA plugin celebrates a run).

``s`` is the state dict built by ``HotTubPlugin._read_state``.
"""

import math
from typing import Any, Dict, Tuple

from PIL import Image

import bdf_face
import hottub_draw as d
import hottub_font as font

RGB = Tuple[int, int, int]

W, H = 128, 32

# Big text uses BDF fonts shipped with LEDMatrix: a smooth 13px-cap face for the
# temperature, a bold one for FAULT. Missing files fall back to the 3x5 font zoom.
HERO = 3        # temperature digits
UNIT = 2        # F / C
LABEL = 2       # FAULT


def load_faces(dirs=()) -> None:
    global HERO, UNIT, LABEL
    HERO = bdf_face.load("10x20", dirs) or 3
    UNIT = bdf_face.load("6x10", dirs) or 2
    LABEL = bdf_face.load("7x13B", dirs) or 2

STATUS_COLORS: Dict[str, RGB] = {
    "READY": d.GREEN,
    "HEATING": d.ORANGE,
    "ERROR": d.RED,
    "NO DATA": d.YELLOW,
    "IDLE": d.WHITE,
}

WATER: RGB = (36, 128, 200)
CABINET_HI: RGB = (96, 60, 34)
CABINET_LO: RGB = (34, 20, 12)

LEFT_W = 56
SCENE_X = 58

FILTER7 = ("11111", "1.1.1", "1.1.1", "1.1.1", "1.1.1", "1.1.1", "11111")
JETS7 = ("..1..", ".1.1.", "..1.1", "...1.", ".11..", "1..1.", ".11..")


def status_color(s: Dict[str, Any]) -> RGB:
    return STATUS_COLORS.get(s["status"], d.GRAY)


# ----------------------------------------------------------------------
# pieces
# ----------------------------------------------------------------------

def _header(img: Image.Image, s: Dict[str, Any], t: float) -> None:
    d.hgrad(img, 0, 0, W, 7, (30, 34, 46), (12, 14, 20))
    color = status_color(s)
    status = s["status"]
    # blinking light while heating, a fast blink on a fault, steady otherwise
    if status == "HEATING":
        on = int(t * 1.4) % 2 == 0
    elif status == "ERROR":
        on = int(t * 3) % 2 == 0
    else:
        on = True
    d.dot(img, 3, 3, color if on else d.scale(color, 0.35))
    used = 7 + d.text(img, status, 7, 1, color)
    d.text(img, "HOT TUB", (W + used) // 2 - 12, 1, d.GOLD)
    target = "SET --" if s["target"] is None else "SET %d" % int(round(s["target"]))
    d.text(img, target, W - 2, 1, d.WHITE, 1, "right")


def _temp_panel(img: Image.Image, s: Dict[str, Any], t: float) -> None:
    """The score-row look: gradient in the status color, a bright edge, big numerals."""
    color = status_color(s)
    x, y, w, h = 0, 8, LEFT_W, 17
    pulse = 0.0
    if s["status"] == "READY":
        pulse = 0.18 * (0.5 + 0.5 * math.sin(t * 3))
    d.hgrad(img, x, y, w, h, d.mix(d.scale(color, 0.42), d.WHITE, pulse), d.scale(color, 0.16))
    d.rect(img, x, y, 2, h, color)
    cur = "--" if s["current"] is None else str(int(round(s["current"])))
    nx = x + 6
    ny = y + (h - font.text_height(HERO)) // 2
    nw = d.big_number(img, cur, nx, ny, d.WHITE, HERO)
    d.text(img, s["unit"], nx + nw + 3, ny, color, UNIT, "left", d.BLACK)


def _indicators(img: Image.Image, s: Dict[str, Any], t: float, show: Dict[str, bool]) -> None:
    """Heater flame, filter cartridge and jets, lit when active and dim when off."""
    d.hgrad(img, 0, 25, LEFT_W, 7, (20, 22, 30), (8, 9, 14))
    dim = d.scale(d.GRAY, 0.55)
    items = []
    if show["heater"]:
        items.append(("heater", 5))
    if show["filter"]:
        items.append(("filter", 5))
    if show["bubbles"]:
        items.append(("bubbles", 10))
    gap = 10
    total = sum(w for _, w in items) + gap * max(0, len(items) - 1)
    x = (LEFT_W - total) // 2 + 1
    for name, w in items:
        if name == "heater":
            if s["heater_on"]:
                d.flame(img, x, 25, flicker=int(t * 6) % 2 == 0)
            else:
                d._sprite(img, x, 25, d.FLAME, {"1": dim, "2": dim, "3": dim})
        elif name == "filter":
            d._sprite(img, x, 25, FILTER7, {"1": d.GREEN if s["filter_on"] else dim})
        else:
            level = s["bubbles_level"]
            c = d.ICE if level else dim
            d._sprite(img, x, 25, JETS7, {"1": c})
            for i in range(2):
                d.rect(img, x + 7, 30 - i * 3, 3, 2, c if level >= i + 1 else (30, 32, 42))
        x += w + gap


def _scene(img: Image.Image, s: Dict[str, Any], t: float) -> None:
    """Tub cross-section. Water height is the approach to the set point."""
    x, y, w, h = SCENE_X, 8, W - SCENE_X, 24
    color = status_color(s)
    d.vgrad(img, x, y, w, h, (8, 12, 24), (2, 3, 8))

    rim_y = y + 8
    d.vgrad(img, x + 1, rim_y, w - 2, h - 8, CABINET_HI, CABINET_LO)
    d.rect(img, x + 1, rim_y, w - 2, 1, d.mix(CABINET_HI, d.WHITE, 0.4))

    ix, iy = x + 4, rim_y + 2
    iw, ih = w - 8, h - 8 - 4
    d.rect(img, ix, iy, iw, ih, (6, 16, 22))

    frac = s["frac"] if s["frac"] is not None else 0.5
    depth = 3 + int(round(frac * (ih - 3)))
    surf = iy + ih - depth
    water = d.mix(WATER, color, 0.22)
    d.vgrad(img, ix, surf, iw, iy + ih - surf, d.mix(water, d.WHITE, 0.12), d.scale(water, 0.32))

    px = img.load()
    crest = d.mix(water, d.WHITE, 0.55)
    for cx in range(iw):
        wy = surf + int(round(math.sin(cx * 0.45 + t * 3.0) * 0.8))
        if iy <= wy < iy + ih:
            px[ix + cx, wy] = crest

    level = s["bubbles_level"]
    if level and depth > 4:
        count = 10 if level >= 2 else 5
        speed = 9.0 if level >= 2 else 5.5
        bc = d.mix(water, d.WHITE, 0.7)
        for i in range(count):
            bx = ix + 3 + (i * 37 + 11) % max(1, iw - 6)
            by = iy + ih - 1 - int((t * speed + i * 5) % (depth + 2))
            if surf + 1 < by < iy + ih:
                px[bx, by] = bc

    if s["status"] == "HEATING":
        for i in range(3):
            sx = ix + 8 + i * (iw // 3)
            for k in range(4):
                sy = rim_y - 2 - k * 2
                if sy < y:
                    break
                drift = math.sin(t * 1.3 + i * 2.1 + k * 0.8) * 2.0
                shade = 95 - k * 20
                xx = int(round(sx + drift))
                if x <= xx < x + w:
                    px[xx, sy] = (shade, shade, int(shade * 1.1))

    d.rect(img, x, y, 1, h, d.DIM)


def _fault_scene(img: Image.Image, t: float) -> None:
    x, y, w, h = SCENE_X, 8, W - SCENE_X, 24
    d.hazard(img, x, y, w, h, offset=int(t * 10))
    label = "FAULT"
    tw, th = font.text_width(label, LABEL), font.text_height(LABEL)
    d.rect(img, x + (w - tw) // 2 - 3, y + 12 - th // 2 - 2, tw + 6, th + 4, d.BLACK)
    if int(t * 2.4) % 2 == 0:
        d.text(img, label, x + w // 2, y + 12 - th // 2, d.RED, LABEL, "center")
    d.rect(img, x, y, 1, h, d.DIM)


# ----------------------------------------------------------------------
# screens
# ----------------------------------------------------------------------

def tub(s: Dict[str, Any], t: float, show: Dict[str, bool]) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    if not s["has_data"]:
        _header(img, s, t)
        d.rect(img, 0, 8, LEFT_W, 17, (36, 32, 8))
        d.rect(img, 0, 8, 2, 17, d.YELLOW)
        d.big_number(img, "--", 6, 8 + (17 - font.text_height(HERO)) // 2, d.scale(d.YELLOW, 0.8), HERO)
        _indicators(img, s, t, show)
        _scene(img, dict(s, frac=0.0, bubbles_level=0), t)
        if int(t * 1.5) % 2 == 0:
            d.text(img, "NO DATA", SCENE_X + (W - SCENE_X) // 2, 11, d.YELLOW, 1, "center", d.BLACK)
        return img
    _header(img, s, t)
    _temp_panel(img, s, t)
    _indicators(img, s, t, show)
    if s["status"] == "ERROR":
        _fault_scene(img, t)
    else:
        _scene(img, s, t)
    return img


def burst(s: Dict[str, Any], t: float) -> Image.Image:
    """Water is at temperature: sunburst, confetti, the number."""
    img = Image.new("RGB", (W, H), d.BLACK)
    color = d.GREEN
    d.rays(img, 0, 0, W, H, W / 2, H / 2, t * 1.4, color, d.scale(color, 0.5))
    d.confetti(img, 0, 0, W, H, t, [d.GREEN, d.GOLD, d.WHITE, d.ICE], count=20)
    d.big_number(img, "READY!", W // 2, 10 - font.text_height(HERO) // 2, color, HERO, "center")
    cur = "--" if s["current"] is None else str(int(round(s["current"])))
    d.text(img, "%s%s  GET IN" % (cur, s["unit"]), W // 2, 21, d.WHITE, 1, "center", d.BLACK)
    return img
