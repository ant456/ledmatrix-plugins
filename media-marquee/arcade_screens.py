"""Media Marquee screens in the NBA / NFL Live arcade style.

Pure drawing: every function takes the pieces it needs and returns a finished
frame, so the plugin's compose() stays the only place that knows about Home
Assistant, TMDB or the caches.

* ``info``    -- header band (status light, service, time), a gradient title row
                 in the service color, scrolling subtitle, segmented progress bar.
* ``burst``   -- spinning sunburst when something new starts playing.
* ``idle``    -- INSERT COIN attract screen with moving hazard stripes.
"""

from typing import Callable, Optional, Tuple, Union

from PIL import Image

import arcade_draw as d
import arcade_font as font
import bdf_face
from bdf_face import BdfFace

RGB = Tuple[int, int, int]

# Big text: a BdfFace drawn at its design size, or (when the .bdf is missing) an
# integer zoom of the 3x5 font. Set by load_faces().
TITLE = 2
HERO = 2

HOLD = 1.5          # seconds a long line rests before it scrolls
GAP = 24            # pixels between the end of a scrolling line and its repeat


def load_faces(title: str, hero: str, dirs=()) -> None:
    """Pick the big faces by BDF name (e.g. "6x10", "9x15B"); fall back to the 3x5 zoom."""
    global TITLE, HERO
    TITLE = bdf_face.load(title, dirs) or 2
    HERO = bdf_face.load(hero, dirs) or 2


def scroll_text(img: Image.Image, text: str, x: int, y: int, w: int, color: RGB,
                scale: Union[int, BdfFace], t: float, speed: float, outline: Optional[RGB] = None) -> None:
    """Left-aligned text; marquee-scrolls with a pause when wider than ``w``.

    The row is cropped from ``img`` and pasted back, so whatever gradient sits
    behind the text survives the scroll.
    """
    tw = font.text_width(text, scale)
    if tw <= w:
        d.text(img, text, x, y, color, scale, "left", outline)
        return
    pad = 1 if outline else 0                      # room for the outline ring
    th = font.text_mask(text, scale).height + 2 * pad
    row = img.crop((x, y - pad, x + w, y - pad + th))
    loop = tw + GAP
    off = 0 if t < HOLD else int((t - HOLD) * speed) % loop
    d.text(row, text, -off, pad, color, scale, "left", outline)
    d.text(row, text, -off + loop, pad, color, scale, "left", outline)
    img.paste(row, (x, y - pad))


def info(W: int, H: int, tile: Image.Image, color: RGB, label: str, title: str, sub: str,
         time_txt: str, playing: bool, now: float, t: float, speed: float,
         draw_bar: Callable[[Image.Image, int, int, int, int], None]) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    img.paste(tile, (0, 0))
    x0 = tile.width + 2
    pw = W - x0

    # header band: light, service, time
    d.hgrad(img, x0, 0, pw, 7, (30, 34, 46), (12, 14, 20))
    on = playing or int(now * 1.4) % 2 == 0
    d.dot(img, x0 + 3, 3, color if on else d.scale(color, 0.35))
    right_w = font.text_width(time_txt) + 2
    d.text(img, font.fit(label, pw - 10 - right_w), x0 + 7, 1, color)
    d.text(img, time_txt, W - 2, 1, d.WHITE, 1, "right")

    # title row: the score-row look
    d.hgrad(img, x0, 8, pw, 12, d.scale(color, 0.42), d.scale(color, 0.14))
    d.rect(img, x0, 8, 2, 12, color)
    scroll_text(img, title, x0 + 5, 8 + (12 - font.text_height(TITLE)) // 2, pw - 7, d.WHITE, TITLE, t, speed, d.BLACK)

    # subtitle
    scroll_text(img, sub, x0 + 1, 21, pw - 2, d.GRAY, 1, t, speed)

    # segmented progress, full panel width
    draw_bar(img, x0, H - 5, pw, 4)
    return img


def burst(W: int, H: int, tile: Image.Image, color: RGB, label: str, title: str,
          t: float, speed: float) -> Image.Image:
    """Something new started: sunburst behind the tile, the title in big letters."""
    img = Image.new("RGB", (W, H), d.BLACK)
    d.rays(img, 0, 0, W, H, W * 0.62, H / 2, t * 1.4, color, d.scale(color, 0.5))
    d.confetti(img, tile.width, 0, W - tile.width, H, t, [color, d.GOLD, d.WHITE], count=14)
    img.paste(tile, (0, 0))
    x0 = tile.width + 2
    pw = W - x0
    d.text(img, "NOW PLAYING", x0 + pw // 2, 4, d.GOLD, 1, "center", d.BLACK)
    scroll_text(img, title, x0 + 2, 17 - font.text_height(TITLE) // 2, pw - 4, d.WHITE, TITLE, t, speed, d.BLACK)
    d.text(img, font.fit(label, pw - 4), x0 + pw // 2, 26, color, 1, "center", d.BLACK)
    return img


def idle(W: int, H: int, now: float) -> Image.Image:
    """Attract mode."""
    img = Image.new("RGB", (W, H), d.BLACK)
    off = int(now * 10)
    d.hazard(img, 0, 0, W, 3, off, (120, 90, 10), (26, 20, 4))
    d.hazard(img, 0, H - 3, W, 3, -off, (120, 90, 10), (26, 20, 4))
    if int(now * 1.5) % 2 == 0:
        d.big_number(img, "INSERT COIN", W // 2, 13 - font.text_height(HERO) // 2, d.YELLOW, HERO, "center")
    d.text(img, "PRESS PLAY", W // 2, 21, d.GRAY, 1, "center")
    return img
