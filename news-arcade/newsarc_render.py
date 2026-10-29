"""News Arcade screens, drawn in the NBA / NFL Live arcade style (128x32).

* ``headline`` -- tile (feed logo or initials) and a full-height gradient headline
                  row that crawls once when the line is too long. Nothing else.
* ``breaking`` -- sunburst and confetti when a new top story lands.
* ``empty``    -- hazard-stripe NO NEWS screen.
"""

from typing import Optional, Tuple, Union

from PIL import Image

import bdf_face
import newsarc_draw as d
import newsarc_font as font
from bdf_face import BdfFace

RGB = Tuple[int, int, int]

W, H = 128, 32
TILE_W = 26
HOLD = 1.5              # seconds a long headline rests at each end of its crawl

TITLE: Union[int, BdfFace] = 2
HERO: Union[int, BdfFace] = 2


def load_faces(title: str, hero: str, dirs=()) -> None:
    global TITLE, HERO
    TITLE = bdf_face.load(title, dirs) or 2
    HERO = bdf_face.load(hero, dirs) or 2


# ----------------------------------------------------------------------
# timing
# ----------------------------------------------------------------------

def crawl_seconds(title: str, width: int, speed: float) -> float:
    """Seconds the headline needs on screen: rest, one pass, rest."""
    over = font.text_width(title, TITLE) - width
    if over <= 0:
        return 0.0
    return HOLD + over / max(1.0, speed) + HOLD


def crawl(img: Image.Image, text: str, x: int, y: int, w: int, color: RGB, t: float, speed: float,
          outline: Optional[RGB] = None) -> None:
    """Left-aligned headline; a long one rests, crawls to its end once, then rests."""
    tw = font.text_width(text, TITLE)
    if tw <= w:
        d.text(img, text, x, y, color, TITLE, "left", outline)
        return
    off = int(min(max(0.0, t - HOLD) * speed, tw - w))
    pad = 1 if outline else 0
    th = font.text_mask(text, TITLE).height + 2 * pad
    row = img.crop((x, y - pad, x + w, y - pad + th))        # keeps the gradient behind the text
    d.text(row, text, -off, pad, color, TITLE, "left", outline)
    img.paste(row, (x, y - pad))


# ----------------------------------------------------------------------
# pieces
# ----------------------------------------------------------------------

def tile(color: RGB, name: str, logo: Optional[Image.Image] = None) -> Image.Image:
    img = Image.new("RGB", (TILE_W, H), color)
    img.paste(Image.new("RGB", (TILE_W - 2, H - 2), d.BLACK), (1, 1))
    d.vgrad(img, 1, 1, TILE_W - 2, H - 2, d.scale(color, 0.40), d.BLACK)
    if logo is not None:
        box = TILE_W - 4
        scaled = logo.copy()
        scaled.thumbnail((box, box))
        img.paste(scaled, ((TILE_W - scaled.width) // 2, (H - scaled.height) // 2), scaled)
    else:
        words = name.replace("-", " ").split()
        letters = "".join(w[0] for w in words[:2]) if len(words) > 1 else (words[0][:2] if words else "?")
        d.big_number(img, letters.upper()[:2], TILE_W // 2, (H - font.text_height(3)) // 2, color, 3, "center")
    return img


# ----------------------------------------------------------------------
# screens
# ----------------------------------------------------------------------

def headline(item: dict, color: RGB, logo_tile: Optional[Image.Image], t: float, speed: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    x0 = 0
    if logo_tile is not None:
        img.paste(logo_tile, (0, 0))
        x0 = logo_tile.width + 2
    pw = W - x0

    d.hgrad(img, x0, 0, pw, H, d.scale(color, 0.42), d.scale(color, 0.14))
    d.rect(img, x0, 0, 2, H, color)
    crawl(img, item["title"], x0 + 5, (H - font.text_height(TITLE)) // 2, pw - 7, d.WHITE, t, speed, d.BLACK)
    return img


def breaking(item: dict, color: RGB, logo_tile: Optional[Image.Image], t: float, speed: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    x0 = logo_tile.width + 2 if logo_tile is not None else 0
    pw = W - x0
    d.rays(img, 0, 0, W, H, W * 0.62, H / 2, t * 1.4, d.RED, d.scale(d.RED, 0.5))
    d.confetti(img, x0, 0, pw, H, t, [d.RED, d.GOLD, d.WHITE], count=14)
    if logo_tile is not None:
        img.paste(logo_tile, (0, 0))
    d.text(img, "BREAKING", x0 + pw // 2, 4, d.GOLD if int(t * 3) % 2 == 0 else d.WHITE, 1, "center", d.BLACK)
    crawl(img, item["title"], x0 + 2, 17 - font.text_height(TITLE) // 2, pw - 4, d.WHITE, t, speed, d.BLACK)
    d.text(img, font.fit(item["feed"].upper(), pw - 4), x0 + pw // 2, 26, color, 1, "center", d.BLACK)
    return img


def empty(now: float, note: str = "CHECK FEEDS") -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    off = int(now * 10)
    d.hazard(img, 0, 0, W, 3, off, (120, 90, 10), (26, 20, 4))
    d.hazard(img, 0, H - 3, W, 3, -off, (120, 90, 10), (26, 20, 4))
    if int(now * 1.5) % 2 == 0:
        d.big_number(img, "NO NEWS", W // 2, 13 - font.text_height(HERO) // 2, d.YELLOW, HERO, "center")
    d.text(img, note, W // 2, 21, d.GRAY, 1, "center")
    return img
