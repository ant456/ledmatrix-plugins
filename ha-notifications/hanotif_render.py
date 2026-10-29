"""HA Notifications screens, drawn in the NBA / NFL Live arcade style (128x32).

* ``splash`` -- the arrival: a flash, a spinning sunburst and the severity word
                (ALERT!, WARNING!, ...) for about a second.
* ``card``   -- the notification: icon tile and the message in big letters on a
                full-height gradient row that crawls once when it is too long.
                Critical ones blink a border. Nothing else on the panel.
* ``icon``   -- the icon_only layout: a sunburst behind a big pulsing icon.
* ``idle``   -- nothing pending: the HA logo and ALL QUIET.
"""

from typing import Optional, Tuple, Union

from PIL import Image

import bdf_face
import hanotif_draw as d
import hanotif_font as font
from bdf_face import BdfFace

RGB = Tuple[int, int, int]

W, H = 128, 32
TILE_W = 32
HOLD = 1.2              # seconds a long message rests at each end of its crawl
SPLASH_SECONDS = 1.3

MSG: Union[int, BdfFace] = 2       # the message
HERO: Union[int, BdfFace] = 2      # splash word, idle headline

LABELS = {"default": "NOTICE", "info": "INFO", "warning": "WARNING", "critical": "CRITICAL", "success": "SUCCESS"}
SPLASH_WORDS = {"default": "NEW", "info": "INFO", "warning": "WARNING!", "critical": "ALERT!", "success": "SUCCESS!"}


def load_faces(message: str, hero: str, dirs=()) -> None:
    global MSG, HERO
    MSG = bdf_face.load(message, dirs) or 2
    HERO = bdf_face.load(hero, dirs) or 2


# ----------------------------------------------------------------------
# text
# ----------------------------------------------------------------------

def crawl_seconds(text: str, width: int, speed: float) -> float:
    """Seconds the message needs: rest, one pass, rest (0 when it fits)."""
    over = font.text_width(text, MSG) - width
    if over <= 0:
        return 0.0
    return HOLD + over / max(1.0, speed) + HOLD


def crawl(img: Image.Image, text: str, x: int, y: int, w: int, color: RGB, t: float, speed: float,
          outline: Optional[RGB] = None) -> None:
    tw = font.text_width(text, MSG)
    if tw <= w:
        d.text(img, text, x, y, color, MSG, "left", outline)
        return
    off = int(min(max(0.0, t - HOLD) * speed, tw - w))
    pad = 1 if outline else 0
    th = font.text_mask(text, MSG).height + 2 * pad
    row = img.crop((x, y - pad, x + w, y - pad + th))        # keeps the gradient behind the text
    d.text(row, text, -off, pad, color, MSG, "left", outline)
    img.paste(row, (x, y - pad))


# ----------------------------------------------------------------------
# pieces
# ----------------------------------------------------------------------

def tile_base(color: RGB) -> Image.Image:
    """The icon tile's background: a gradient in the severity colour."""
    img = Image.new("RGB", (TILE_W, H), d.BLACK)
    d.vgrad(img, 0, 0, TILE_W, H, d.scale(color, 0.45), d.scale(color, 0.08))
    return img


# ----------------------------------------------------------------------
# screens
# ----------------------------------------------------------------------

def splash(severity: str, color: RGB, t: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    d.rays(img, 0, 0, W, H, W / 2, H / 2, t * 2.2, color, d.scale(color, 0.5))
    if severity == "success":
        d.confetti(img, 0, 0, W, H, t, [color, d.GOLD, d.WHITE], count=18)
    word = SPLASH_WORDS.get(severity, "NEW")
    grow = min(1.0, t / 0.25)                                 # snaps in over a quarter second
    face = HERO
    y = int(H / 2 - font.text_height(face) / 2 + (1 - grow) * 6)
    d.text(img, word, W // 2, y, d.mix(color, d.WHITE, 0.55), face, "center", d.BLACK)
    flash = max(0.0, 1.0 - t / 0.18)                          # white flash on arrival
    if flash > 0:
        d.rect(img, 0, 0, W, H, d.WHITE, flash * 0.8)
    return img


def card(severity: str, color: RGB, title: str, message: str, tile: Optional[Image.Image],
         t: float, now: float, speed: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    x0 = 0
    if tile is not None:
        img.paste(tile, (0, 0))
        d.rect(img, tile.width, 0, 2, H, color)
        x0 = tile.width + 2
    pw = W - x0
    big = message or title                                    # no message: the title is the line

    d.hgrad(img, x0, 0, pw, H, d.scale(color, 0.42), d.scale(color, 0.14))
    d.rect(img, x0, 0, 2, H, color)
    crawl(img, big, x0 + 5, (H - font.text_height(MSG)) // 2, pw - 7, d.WHITE, t, speed, d.BLACK)

    if severity == "critical" and int(now * 2.5) % 2 == 0:
        d.frame(img, 0, 0, W, H, color)
    return img


def icon_only(severity: str, color: RGB, icon: Image.Image, now: float) -> Image.Image:
    img = Image.new("RGB", (W, H), d.BLACK)
    d.rays(img, 0, 0, W, H, W / 2, H / 2, now * 1.2, color, d.scale(color, 0.5))
    img.paste(icon, ((W - icon.width) // 2, (H - icon.height) // 2), icon if icon.mode == "RGBA" else None)
    if severity == "critical" and int(now * 2.5) % 2 == 0:
        d.frame(img, 0, 0, W, H, color)
    return img


def idle(logo: Optional[Image.Image], now: float) -> Image.Image:
    """Nothing pending: the HA logo and ALL QUIET."""
    blue = (60, 160, 255)
    img = Image.new("RGB", (W, H), d.BLACK)
    d.vgrad(img, 0, 0, TILE_W, H, d.scale(blue, 0.40), d.scale(blue, 0.06))
    if logo is not None:
        img.paste(logo, ((TILE_W - logo.width) // 2, (H - logo.height) // 2), logo)
    d.rect(img, TILE_W, 0, 2, H, blue)
    x0 = TILE_W + 2
    pw = W - x0
    d.hgrad(img, x0, 0, pw, H, d.scale(blue, 0.30), d.scale(blue, 0.08))
    d.big_number(img, "ALL QUIET", x0 + pw // 2, (H - font.text_height(HERO)) // 2, d.WHITE, HERO, "center")
    return img
