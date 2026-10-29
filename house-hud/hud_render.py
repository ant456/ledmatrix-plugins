"""House HUD screens, drawn in the NBA / NFL Live arcade style (128x32).

* ``screen`` -- four 32px tiles side by side, each a status-coloured gradient with a pixel icon,
                a label and one value (a word such as LOCKED or a number such as 67 with a degree ring).
                Alarms and jams blink a red border. No header, no footer.
* ``burst``  -- an event: sunburst and a big word (UNLOCKED!, OPENED!, WELCOME HOME!).
* ``notice`` -- hazard stripes when Home Assistant cannot be reached.
"""

from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image

import bdf_face
import hud_draw as d
import hud_font as font
from bdf_face import BdfFace

RGB = Tuple[int, int, int]

W, H = 128, 32
TILE_W = 32

WORD: Union[int, BdfFace] = 2      # LOCKED, OPEN, SAFE ...
NUM: Union[int, BdfFace] = 3       # temperatures and counts
HERO: Union[int, BdfFace] = 2      # burst headline

SPRITES: Dict[str, Tuple[str, ...]] = {
    "lock": ("...11111...", "..11...11..", "..11...11..", "..11...11..", ".111111111.", ".111111111.",
             ".1111.1111.", ".1111.1111.", ".111111111.", ".111111111.", "..........."),
    "lock_open": ("...11111...", "..11...11..", "..11........", "..11........", ".111111111.", ".111111111.",
                  ".1111.1111.", ".1111.1111.", ".111111111.", ".111111111.", "..........."),
    "door": (".111111111.", ".1.......1.", ".1.......1.", ".1.......1.", ".1.....1.1.", ".1.....1.1.",
             ".1.......1.", ".1.......1.", ".1.......1.", ".111111111.", "..........."),
    "door_open": (".111111111.", ".1.......1.", ".11......1.", ".111.....1.", ".1111....1.", ".1111....1.",
                  ".111.....1.", ".11......1.", ".1.......1.", ".111111111.", "..........."),
    "smoke": ("...11111...", ".11.....11.", ".1.......1.", "1....1....1", "1...111...1", "1....1....1",
              ".1.......1.", ".11.....11.", "...11111...", "...........", "..........."),
    "house": ("....1......", "...111.....", "..11111....", ".1111111...", "111111111..", ".111111111.",
              ".111...111.", ".111...111.", ".111...111.", ".111...111.", "..........."),
    "thermo": ("....111....", "....1.1....", "....1.1....", "....1.1....", "....1.1....", "...11.11...",
               "..111.111..", "..1111111..", "..1111111..", "...11111...", "..........."),
    "sun": (".....1.....", ".1...1...1.", "..1.....1..", "....111....", "...11111...", "11.11111.11",
            "...11111...", "....111....", "..1.....1..", ".1...1...1.", ".....1....."),
    "bulb": ("...11111...", "..1111111..", ".111111111.", ".111111111.", ".111111111.", "..1111111..",
             "...11111...", "...11111...", "....111....", "....111....", "..........."),
    "tub": ("..1...1....", "...1...1...", "..1...1....", "...........", "...........", "11111111111",
            "11111111111", ".111111111.", ".111111111.", "..1111111..", "..........."),
}
SPRITES["lock_open"] = tuple(r[:11].ljust(11, ".") for r in SPRITES["lock_open"])


def load_faces(dirs=()) -> None:
    global WORD, NUM, HERO
    WORD = bdf_face.load("5x8", dirs) or 2
    NUM = bdf_face.load("7x13B", dirs) or 3
    HERO = bdf_face.load("9x15B", dirs) or 2


def sprite(img: Image.Image, x: int, y: int, name: str, color: RGB) -> None:
    rows = SPRITES.get(name) or SPRITES["house"]
    d._sprite(img, x, y, rows, {"1": color})


def tile(img: Image.Image, x0: int, v: Dict[str, Any], now: float, first: bool) -> None:
    color: RGB = v["color"]
    blink_on = v["blink"] and int(now * 2.5) % 2 == 0
    d.vgrad(img, x0, 0, TILE_W, H, d.scale(color, 0.34 if not blink_on else 0.55), d.scale(color, 0.05))
    if not first:
        d.rect(img, x0, 0, 1, H, d.BLACK)
    if blink_on:
        d.frame(img, x0, 0, TILE_W, H, color)
    sprite(img, x0 + (TILE_W - 11) // 2, 2, v["icon"], color if color != d.DIM else d.GRAY)
    d.text(img, font.fit(v["label"], TILE_W - 2), x0 + TILE_W // 2, 15, d.GRAY, 1, "center")
    cx = x0 + TILE_W // 2
    if v["number"] is not None:
        txt = str(int(round(v["number"])))
        tw = font.text_width(txt, NUM)
        face = NUM
        extra = 4 if v.get("ring") else 0
        if tw + extra > TILE_W - 2:
            face, tw = WORD, font.text_width(txt, WORD)
        left = cx - (tw + extra) // 2
        th = font.text_height(face)
        d.big_number(img, txt, left, 21 + (9 - th) // 2, d.WHITE, face, "left")
        if v.get("ring"):                                   # degree ring
            rx = left + tw + 1
            for dx, dy in ((1, 0), (2, 0), (0, 1), (3, 1), (1, 2), (2, 2)):
                d.rect(img, rx + dx - 1, 21 + dy, 1, 1, d.WHITE)
    else:
        d.text(img, v["word"] or "", cx, 22, d.WHITE if color != d.DIM else d.GRAY, WORD, "center", d.BLACK)


def screen(views: List[Dict[str, Any]], now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    for i, v in enumerate(views[:4]):
        tile(img, i * TILE_W, v, now, i == 0)
    return img


def burst(ev: Dict[str, Any], t: float, clock: str) -> Image.Image:
    color: RGB = ev["color"]
    img = Image.new("RGB", (W, H), d.BLACK)
    d.rays(img, 0, 0, W, H, W / 2, H / 2, t * 1.8, color, d.scale(color, 0.5))
    if color == d.GREEN or ev["text"].startswith("WELCOME"):
        d.confetti(img, 0, 0, W, H, t, [color, d.GOLD, d.WHITE], count=16)
    if ev.get("top"):
        d.text(img, font.fit(ev["top"], W - 8), W // 2, 4, d.GOLD if int(t * 3) % 2 == 0 else d.WHITE, 1, "center", d.BLACK)
    face = HERO
    if font.text_width(ev["text"], face) > W - 4:
        face = NUM
    d.text(img, ev["text"], W // 2, 16 - font.text_height(face) // 2, d.mix(color, d.WHITE, 0.55), face, "center", d.BLACK)
    d.text(img, clock, W // 2, 26, d.WHITE, 1, "center", d.BLACK)
    return img


def notice(now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    off = int(now * 10)
    d.hazard(img, 0, 0, W, 3, off)
    d.hazard(img, 0, H - 3, W, 3, -off)
    d.big_number(img, "NO HOME DATA", W // 2, 13 - font.text_height(HERO) // 2, d.RED, HERO, "center")
    d.text(img, "CHECK HOME ASSISTANT", W // 2, 22, d.GRAY, 1, "center")
    return img
