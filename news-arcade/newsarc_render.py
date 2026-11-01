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


# ----------------------------------------------------------------------
# continuous ticker
# ----------------------------------------------------------------------

BAR = 2                 # coloured bar that opens every headline
PAD = 4                 # gap between that bar and the text
GAP = 14                # gap after the text


class Strip:
    """Every headline laid end to end on one long image, drawn once.

    ``spans[i]`` is the (start, end) x of headline ``i``; ``items[i]`` is the story.
    The image is ``total`` pixels of one lap plus a copy of the first ``view_w``
    pixels, so any window starting before ``total`` is complete and the lap
    joins up with no seam.
    """

    def __init__(self, stories: list, view_w: int):
        self.view_w = max(1, int(view_w))
        stories = list(stories)
        width_of = [BAR + PAD + font.text_width(s["title"], TITLE) + GAP for s in stories]
        lap = sum(width_of)
        if stories and lap < self.view_w:                      # short list: repeat it so a window is never empty
            reps = -(-self.view_w // lap)
            stories, width_of = stories * reps, width_of * reps
        self.items = stories
        self.spans = []
        x = 0
        for w in width_of:
            self.spans.append((x, x + w))
            x += w
        self.total = x
        self.image = Image.new("RGB", (max(1, self.total + self.view_w), H), d.BLACK)
        if not stories:
            return
        ty = (H - font.text_height(TITLE)) // 2
        for s, (a, b) in zip(stories, self.spans):
            color = s["color"]
            d.hgrad(self.image, a, 0, b - a, H, d.scale(color, 0.42), d.scale(color, 0.14))
            d.rect(self.image, a, 0, BAR, H, color)
            d.text(self.image, s["title"], a + BAR + PAD, ty, d.WHITE, TITLE, "left", d.BLACK)
        self.image.paste(self.image.crop((0, 0, self.view_w, H)), (self.total, 0))

    def index_at(self, x: float) -> int:
        """Which headline owns column ``x`` of the lap."""
        x = x % self.total if self.total else 0
        for i, (a, b) in enumerate(self.spans):
            if a <= x < b:
                return i
        return 0

    def window(self, x: float) -> Image.Image:
        x = int(x % self.total) if self.total else 0
        return self.image.crop((x, 0, x + self.view_w, H))
