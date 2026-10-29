"""The arena-scoreboard panel: LED-dot digits on a blue board, like the ones
that hang over a gym (the clock in red, the quarter in amber, each team's record
where the penalty clocks would be, and a logo box in the middle).

Everything on it is real data: the game clock and quarter straight from ESPN,
the teams' records, and a blinking gold lamp while a team is on a scoring run.
The logo box shows the NBA crest when the assets have it, else the letters NBA.

Three layouts, picked from the room available:

* **full**    (about 40+ rows): big clock and quarter on top, each team's
  record either side of the logo underneath;
* **two-row** (about 22-39 rows): clock, quarter and a small logo on top, a
  records line underneath;
* **compact** (under 22 rows): clock and quarter on one line.
"""

import re
from typing import Any, Dict, Optional, Tuple

from PIL import Image

import nbalive_draw as d
import nbalive_font as font
import nbalive_model as model

RGB = Tuple[int, int, int]

BOARD_BLUE: RGB = (28, 58, 88)
BOARD_EDGE: RGB = (70, 120, 160)
BLACK_BOX: RGB = (3, 4, 7)
BOX_EDGE: RGB = (18, 26, 36)
GHOST: RGB = (40, 41, 46)
RED_LED: RGB = (255, 56, 44)
AMBER_LED: RGB = (255, 152, 32)
RUN_LAMP_POINTS = 6


# ----------------------------------------------------------------------
# LED digits
# ----------------------------------------------------------------------

def _pattern(ch: str):
    return font._GLYPHS.get(ch) or font._GLYPHS["?"]


def _cell(img: Image.Image, x: int, y: int, s: int, color: RGB) -> None:
    """One LED: a dot with a gap on its right and bottom (s >= 3), a block with a
    darker corner (s == 2), or a pixel."""
    if s >= 3:
        d.rect(img, x, y, s - 1, s - 1, color)
    elif s == 2:
        d.rect(img, x, y, 2, 2, color)
        d.rect(img, x + 1, y + 1, 1, 1, d.scale(color, 0.62))
    else:
        d.rect(img, x, y, 1, 1, color)


def _advance(ch: str, s: int) -> int:
    """Pixels from one character's left edge to the next's."""
    if ch in ":.":
        return 2 * s
    if ch == " ":
        return 4 * s              # a blank digit slot is as wide as a digit
    return (len(_pattern(ch)[0]) + 1) * s


def led_width(text: str, s: int) -> int:
    """Width of ``text`` as LED characters (no trailing gap)."""
    if not text:
        return 0
    return sum(_advance(ch, s) for ch in text) - s


def led_text(img: Image.Image, text: str, x: int, y: int, color: RGB, s: int, ghost: bool = True) -> int:
    """Draw ``text`` as LED dots; digits (and blank slots, ' ') show their unlit
    '8' segments in dark grey underneath when ``ghost``. Returns the width."""
    cx = x
    for ch in text:
        if ch == ":":
            for row in (1, 3):
                _cell(img, cx, y + row * s, s, color)
        elif ch == ".":
            _cell(img, cx, y + 4 * s, s, color)
        else:
            lit = _pattern(ch) if ch != " " else None
            if ghost and (ch.isdigit() or ch == " "):
                for r, line in enumerate(_pattern("8")):
                    for c, bit in enumerate(line):
                        if bit == "1":
                            _cell(img, cx + c * s, y + r * s, s, GHOST)
            if lit:
                for r, line in enumerate(lit):
                    for c, bit in enumerate(line):
                        if bit == "1":
                            _cell(img, cx + c * s, y + r * s, s, color)
        cx += _advance(ch, s)
    return led_width(text, s)


# ----------------------------------------------------------------------
# what the board says
# ----------------------------------------------------------------------

def clock_face(game: Dict[str, Any]) -> str:
    """The clock as the board shows it: ' 7:05', '12:00', '45.3' (tenths under a
    minute, as ESPN sends them), or HALF."""
    ptxt = game.get("period_text", "")
    if ptxt == "HALF":
        return "HALF"
    raw = str(game.get("clock") or "").strip()
    if "." in raw and ":" not in raw:
        return raw[:4]
    secs = model.clock_seconds(raw)
    if secs is None:
        return " 0:00"
    return f"{int(secs // 60)}:{int(secs % 60):02d}".rjust(5)


def period_face(game: Dict[str, Any]) -> str:
    """'1'..'4', 'OT' for any overtime, '2' at the half, '-' before the game."""
    if game.get("period_text") == "HALF":
        return "2"
    period = int(game.get("period") or 0)
    if period <= 0:
        return "-"
    return str(period) if period <= 4 else "OT"


def run_of(game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The scoring run worth a lamp (6+ unanswered points), else None."""
    run = game.get("run") if game.get("live") else None
    return run if run and run.get("points", 0) >= RUN_LAMP_POINTS else None


def _record(game: Dict[str, Any], side: str) -> str:
    """A team's season record, e.g. '41-12'; '--' when ESPN has none."""
    return str(game[side].get("record") or "--")


# ----------------------------------------------------------------------
# boxes
# ----------------------------------------------------------------------

def _box(img: Image.Image, x: int, y: int, w: int, h: int) -> None:
    d.rect(img, x, y, w, h, BLACK_BOX)
    d.frame(img, x, y, w, h, BOX_EDGE)


def _clock_box(img: Image.Image, x: int, y: int, h: int, game: Dict[str, Any], s: int, t: float,
               animate: bool) -> int:
    """The red clock in a black box; returns the box width."""
    face = clock_face(game)
    template = "88.8" if ("." in face and ":" not in face) else "88:88"
    slots = led_width(template, s)
    w = slots + 4
    _box(img, x, y, w, h)
    ty = y + max(1, (h - 5 * s) // 2)
    if face == "HALF":
        blink = (not animate) or int(t * 1.4) % 2 == 0
        text_w = led_width(face, s)
        led_text(img, face, x + (w - text_w) // 2, ty, RED_LED if blink else d.scale(RED_LED, 0.4), s, ghost=False)
    else:
        led_text(img, face.rjust(len(template)) if template == "88.8" else face, x + 2, ty, RED_LED, s)
    return w


def _period_box(img: Image.Image, x: int, y: int, w: int, h: int, game: Dict[str, Any]) -> None:
    face = period_face(game)
    s = 2 if len(face) == 1 and h >= 12 else 1
    _box(img, x, y, w, h)
    tw = led_width(face, s)
    led_text(img, face, x + max(1, (w - tw) // 2), y + max(1, (h - 5 * s) // 2), AMBER_LED, s, ghost=len(face) == 1 and s >= 2)


def period_box_width(game: Dict[str, Any], h: int) -> int:
    face = period_face(game)
    s = 2 if len(face) == 1 and h >= 12 else 1
    return led_width(face, s) + 4


def _logo_box(ctx, img: Image.Image, x: int, y: int, w: int, h: int, game: Dict[str, Any], t: float,
              animate: bool, lamp: bool = True) -> None:
    """The logo in a black box. During a scoring run the box's border blinks gold."""
    _box(img, x, y, w, h)
    logo = ctx.board_logo(max(2, w - 4), max(2, h - 4))
    if logo is not None:
        img.paste(logo, (x + (w - logo.width) // 2, y + (h - logo.height) // 2), logo)
    elif w >= 14 and h >= 8:
        d.text(img, "NBA", x + w // 2, y + max(1, (h - 5) // 2), d.scale(d.WHITE, 0.8), 1, "center")
    if lamp and run_of(game):
        on = (not animate) or int(t * 2.4) % 2 == 0
        d.frame(img, x, y, w, h, d.GOLD if on else d.scale(d.GOLD, 0.35))


# ----------------------------------------------------------------------
# the three layouts
# ----------------------------------------------------------------------

def _full(ctx, img, x, y, w, h, game, t, animate):
    inner_x, inner_w = x + 2, w - 4
    s = 3 if led_width("88:88", 3) + 4 + 1 + 10 <= inner_w else 2
    row_a = 5 * s + 4
    cw = _clock_box(img, inner_x, y + 2, row_a, game, s, t, animate)
    pw = max(10, min(inner_w - cw - 1, period_box_width(game, row_a)))
    _period_box(img, inner_x + cw + 1, y + 2, pw, row_a, game)
    # row C: each team's record (captioned with the team) either side of the logo
    cy = y + 2 + row_a + 1
    ch = y + h - 2 - cy
    if ch < 12:
        return
    box_w = 26 if inner_w >= 80 else max(14, (inner_w - 22) // 2)
    logo_w = inner_w - 2 * box_w - 2
    box_y, box_h = cy + 6, ch - 6
    for i, side in enumerate(("home", "away")):
        bx = inner_x if i == 0 else inner_x + inner_w - box_w
        d.text(img, game[side]["abbr"], bx + box_w // 2, cy, d.scale(d.WHITE, 0.9), 1, "center")
        _box(img, bx, box_y, box_w, box_h)
        rec = _record(game, side)
        d.text(img, rec, bx + box_w // 2, box_y + max(1, (box_h - 5) // 2), RED_LED, 1, "center")
    if logo_w >= 10:
        _logo_box(ctx, img, inner_x + box_w + 1, cy, logo_w, ch, game, t, animate)


def _two_row(ctx, img, x, y, w, h, game, t, animate):
    inner_x, inner_w = x + 2, w - 4
    s = 2 if led_width("88:88", 2) + 4 + 1 + 8 <= inner_w else 1
    row_a = 5 * s + 2
    cw = _clock_box(img, inner_x, y + 1, row_a, game, s, t, animate)
    pw = max(8, min(inner_w - cw - 1, period_box_width(game, row_a)))
    _period_box(img, inner_x + cw + 1, y + 1, pw, row_a, game)
    used = cw + 1 + pw + 1
    if inner_w - used >= 14:
        _logo_box(ctx, img, inner_x + used, y + 1, inner_w - used, row_a, game, t, animate, lamp=False)
    by = y + 1 + row_a + 1
    bh = y + h - 1 - by
    if bh < 7:
        return
    away, home = game["away"], game["home"]
    run = run_of(game)
    run_text = f"{run['points']}-0" if run else ""
    mid_pp = bool(run) and inner_w >= 56
    options = [
        (f"{away['abbr']} {_record(game, 'away')}", f"{_record(game, 'home')} {home['abbr']}"),
        (_record(game, "away"), _record(game, "home")),
    ]
    for left, right in options:
        need = font.text_width(left) + font.text_width(right) + (14 if mid_pp else 4)
        if need <= inner_w:
            ty = by + max(0, (bh - 5) // 2)
            d.text(img, left, inner_x + 1, ty, d.scale(d.WHITE, 0.9))
            d.text(img, right, inner_x + inner_w - 1, ty, d.scale(d.WHITE, 0.9), 1, "right")
            break
    if run and inner_w >= 40:
        on = (not animate) or int(t * 2.4) % 2 == 0
        ty = by + max(0, (bh - 7) // 2)
        if on:
            d.tag(img, inner_x + (inner_w - (font.text_width(run_text) + 4)) // 2, ty, run_text, d.GOLD)


def _compact(ctx, img, x, y, w, h, game, t, animate):
    inner_x, inner_w = x + 1, w - 2
    s = 2 if (h >= 12 and led_width("88:88", 2) + 4 + 1 + 8 <= inner_w) else 1
    row_h = min(h - 2, 5 * s + 2)
    cy = y + max(1, (h - row_h) // 2)
    cw = _clock_box(img, inner_x, cy, row_h, game, s, t, animate)
    pw = max(8, min(inner_w - cw - 1, period_box_width(game, row_h)))
    if cw + 1 + pw <= inner_w:
        _period_box(img, inner_x + cw + 1, cy, pw, row_h, game)
    used = cw + 1 + pw + 1
    run = run_of(game)
    if run and inner_w - used >= 16:
        on = (not animate) or int(t * 2.4) % 2 == 0
        if on:
            d.tag(img, inner_x + used + 1, cy + max(0, (row_h - 7) // 2), f"{run['points']}-0", d.GOLD)


def board(ctx, img: Image.Image, x: int, y: int, w: int, h: int, game: Dict[str, Any], t: float,
          animate: bool) -> None:
    """Draw the scoreboard into the ``w`` x ``h`` box at ``x, y``."""
    if w < 20 or h < 8:
        return
    d.vgrad(img, x, y, w, h, d.scale(BOARD_BLUE, 1.12), d.scale(BOARD_BLUE, 0.85))
    d.frame(img, x, y, w, h, BOARD_EDGE)
    if h >= 38 and w >= 56:
        _full(ctx, img, x, y, w, h, game, t, animate)
    elif h >= 22 and w >= 34:
        _two_row(ctx, img, x, y, w, h, game, t, animate)
    else:
        _compact(ctx, img, x, y, w, h, game, t, animate)
