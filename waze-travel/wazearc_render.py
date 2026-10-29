"""Waze Travel screens, drawn in the NBA / NFL Live arcade style (128x32).

* ``rows``    -- one row per destination: status-coloured gradient, car, label, a
                 traffic word with the delay against free flow, a segmented
                 congestion meter, a trend arrow and the drive time in big digits.
* ``alert``   -- sunburst when a route turns heavy.
* ``notice``  -- hazard-stripe LOADING / OFFLINE screens, so a dead sensor says so.

``r`` is a dict built by ``WazeTravelPlugin._row``: label, color, state
("OK" | "DOWN" | "NONE"), minutes, status, delta, ratio, trend.
"""

from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image

import bdf_face
import wazearc_draw as d
import wazearc_font as font
from bdf_face import BdfFace

RGB = Tuple[int, int, int]

W, H = 128, 32

NUM: Union[int, BdfFace] = 2       # drive-time digits
HERO: Union[int, BdfFace] = 2      # alert / offline headline
T_NUM: Union[int, BdfFace] = 3     # full-height layout: drive-time digits
T_LABEL: Union[int, BdfFace] = 2   # full-height layout: destination name
T_SUB: Union[int, BdfFace] = 2     # full-height layout: status word and MIN

STATUS_COLORS: Dict[str, RGB] = {
    "CLEAR": d.GREEN,
    "BUSY": d.YELLOW,
    "HEAVY": d.ORANGE,
    "JAM": d.RED,
}
METER_COLORS: List[RGB] = [d.GREEN, (170, 235, 70), d.YELLOW, d.ORANGE, (255, 95, 50), d.RED]

CAR = ("........",
       ".1111...",
       "111111..",
       "1.11.11.",
       "11111111",
       "11111111",
       ".1.11.1.",
       "........")
UP = ("..1..", ".111.", "11111")
DOWN = ("11111", ".111.", "..1..")


def load_faces(num: str, hero: str, dirs=()) -> None:
    global NUM, HERO, T_NUM, T_LABEL, T_SUB
    NUM = bdf_face.load(num, dirs) or 3
    HERO = bdf_face.load(hero, dirs) or 2
    T_NUM = bdf_face.load("10x20", dirs) or 3
    T_LABEL = bdf_face.load("7x13B", dirs) or 2
    T_SUB = bdf_face.load("6x10", dirs) or 2


def status_color(r: Dict[str, Any]) -> RGB:
    return STATUS_COLORS.get(r.get("status") or "", d.GRAY) if r["state"] == "OK" else d.DIM


def meter(img: Image.Image, x: int, y: int, h: int, ratio: float) -> int:
    """Six segments from free flow (green) to a jam (red). Returns the meter's width."""
    seg, gap, n = 4, 1, len(METER_COLORS)
    lit = max(1, int(round(max(0.0, min(1.0, (ratio - 1.0) / 0.9)) * n)))
    for i in range(n):
        d.rect(img, x + i * (seg + gap), y, seg, h, METER_COLORS[i] if i < lit else d.scale(METER_COLORS[i], 0.14))
    return n * (seg + gap) - gap


def car2(img: Image.Image, x: int, y: int, color: RGB) -> None:
    """The car sprite at 2x (16x16)."""
    for ry, line in enumerate(CAR):
        for rx, ch in enumerate(line):
            if ch == "1":
                d.rect(img, x + rx * 2, y + ry * 2, 2, 2, color)


def tall(img: Image.Image, y0: int, h: int, r: Dict[str, Any], t: float) -> None:
    """A single destination using the whole panel: big car, name, status, meter and 20px digits."""
    ok = r["state"] == "OK"
    sc = status_color(r)
    d.hgrad(img, 0, y0, W, h, d.scale(sc, 0.30), d.scale(sc, 0.05))
    d.rect(img, 0, y0, 3, h, sc)
    car2(img, 8, y0 + 3, r["color"])

    x = 30
    d.text(img, font.fit(r["label"], 52, T_LABEL), x, y0 + 3, r["color"], T_LABEL, "left", d.BLACK)
    right = W - 14
    if ok:
        word = r["status"] + ((" +%d" % r["delta"]) if r["delta"] and r["delta"] > 0 else "")
        d.text(img, font.fit(word, 56, T_SUB), x, y0 + 17, sc, T_SUB, "left", d.BLACK)
        meter_w = meter(img, x, y0 + h - 5, 3, r["ratio"] or 1.0)
        mins = str(max(0, int(round(r["minutes"]))))
        face = T_NUM
        if font.text_width(mins, face) > 26:                     # three digits: step down a size
            face = T_LABEL
        th, sh = font.text_height(face), font.text_height(T_SUB)
        top = y0 + (h - (th + 3 + sh)) // 2                       # digits with MIN centred under them
        nw = d.big_number(img, mins, right, top, d.WHITE, face, "right")
        d.text(img, "MIN", right - nw // 2, top + th + 3, d.GRAY, T_SUB, "center", d.BLACK)
        if r["trend"]:
            up = r["trend"] > 0
            d._sprite(img, right + 5, top + (th - 3) // 2, UP if up else DOWN, {"1": d.RED if up else d.GREEN})
    else:
        if r["state"] == "DOWN":
            if int(t * 1.5) % 2 == 0:
                d.text(img, "NO DATA", x, y0 + 17, d.YELLOW, T_SUB, "left", d.BLACK)
        else:
            d.text(img, "...", x, y0 + 17, d.GRAY, T_SUB)
        d.big_number(img, "--", right, y0 + (h - font.text_height(T_NUM)) // 2 - 1, d.DIM, T_NUM, "right")


def row(img: Image.Image, y0: int, h: int, r: Dict[str, Any], t: float) -> None:
    if h >= 24:
        return tall(img, y0, h, r, t)
    ok = r["state"] == "OK"
    sc = status_color(r)
    d.hgrad(img, 0, y0, W, h, d.scale(sc, 0.26), d.scale(sc, 0.06))
    d.rect(img, 0, y0, 2, h, sc)

    d._sprite(img, 5, y0 + (h - 8) // 2, CAR, {"1": r["color"]})

    top = y0 + (h - 12) // 2                               # label line + status line, centred
    d.text(img, font.fit(r["label"], 30), 16, top, r["color"])
    if ok:
        word = r["status"]
        if r["delta"] and r["delta"] > 0:
            word += " +%d" % r["delta"]
        d.text(img, font.fit(word, 34), 16, top + 7, sc)
    elif r["state"] == "DOWN":
        if int(t * 1.5) % 2 == 0:
            d.text(img, "NO DATA", 16, top + 7, d.YELLOW)
    else:
        d.text(img, "...", 16, top + 7, d.GRAY)

    if not ok:
        d.big_number(img, "--", W - 18, y0 + (h - font.text_height(NUM)) // 2, d.DIM, NUM, "right")
        return

    mins = str(max(0, int(round(r["minutes"]))))
    num_h = font.text_height(NUM)
    right = W - 17
    nw = d.big_number(img, mins, right, y0 + (h - num_h) // 2, d.WHITE, NUM, "right")
    d.text(img, "MIN", W - 2, y0 + (h - 5) // 2, d.GRAY, 1, "right")

    mh = 6 if h < 24 else 8
    mx = 56
    mw = meter(img, mx, y0 + (h - mh) // 2, mh, r["ratio"] or 1.0)
    arrow_x = right - nw - 8
    if r["trend"] and arrow_x > mx + mw + 2:
        up = r["trend"] > 0
        d._sprite(img, arrow_x, y0 + (h - 3) // 2, UP if up else DOWN, {"1": d.RED if up else d.GREEN})


def rows(rs: List[Dict[str, Any]], t: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    n = max(1, len(rs))
    h = H // n
    for i, r in enumerate(rs):
        row(img, i * h, h, r, t)
        if i:
            d.rect(img, 0, i * h, W, 1, d.BLACK)
    return img


def alert(r: Dict[str, Any], t: float) -> Image.Image:
    sc = status_color(r)
    img = Image.new("RGB", (W, H), d.BLACK)
    d.rays(img, 0, 0, W, H, W / 2, H / 2, t * 1.4, sc, d.scale(sc, 0.5))
    d.confetti(img, 0, 0, W, H, t, [sc, d.GOLD, d.WHITE], count=12)
    d.text(img, "%s TRAFFIC" % r["status"], W // 2, 4, d.GOLD if int(t * 3) % 2 == 0 else d.WHITE, 1, "center", d.BLACK)
    delta = ("+%d" % r["delta"]) if r["delta"] else ""
    d.big_number(img, ("%s %s" % (r["label"], delta)).strip(), W // 2, 17 - font.text_height(HERO) // 2, sc, HERO, "center")
    d.text(img, "NOW %d MIN" % int(round(r["minutes"])), W // 2, 26, d.WHITE, 1, "center", d.BLACK)
    return img


def notice(kind: str, t: float) -> Image.Image:
    """kind: "LOADING" or "OFFLINE"."""
    img = Image.new("RGB", (W, H), d.BLACK)
    off = int(t * 10)
    color = (120, 90, 10) if kind == "LOADING" else (150, 18, 30)
    dark = (26, 20, 4) if kind == "LOADING" else (36, 6, 10)
    d.hazard(img, 0, 0, W, 3, off, color, dark)
    d.hazard(img, 0, H - 3, W, 3, -off, color, dark)
    if kind == "LOADING":
        if int(t * 1.5) % 2 == 0:
            d.big_number(img, "TRAFFIC", W // 2, 13 - font.text_height(HERO) // 2, d.YELLOW, HERO, "center")
        d.text(img, "FETCHING TIMES", W // 2, 21, d.GRAY, 1, "center")
    else:
        d.big_number(img, "NO WAZE", W // 2, 13 - font.text_height(HERO) // 2, d.RED, HERO, "center")
        d.text(img, "CHECK HOME ASSISTANT", W // 2, 21, d.GRAY, 1, "center")
    return img
