"""Typography for Lumen Clock: smooth (antialiased) digits, rolling digit changes, glow and gradient fills.

The big time is drawn from a TrueType face (Orbitron by default) rather than a bitmap font, so the digits are
round and soft at 20+ px. Every glyph is rendered once into a mask and remembered; a frame only pastes masks.

* ``Face``       a font at one size / weight with a per-glyph mask cache and fixed digit slots
* ``time_mask``  the whole time as one frame-sized mask, with digits that roll over when they change
* ``fill`` / ``glow`` / ``shadow``   turn a mask into light on the frame
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

RGB = Tuple[int, int, int]
W, H = 128, 32
ROLL_SECONDS = 0.45                 # how long a changing digit takes to roll over

FONT_NAMES = ("orbitron-variablefont_wght.ttf", "Orbitron-VariableFont_wght.ttf")
FALLBACKS = ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf")


def find_font(dirs: Iterable[str], names: Sequence[str]) -> Optional[str]:
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return None


class Face:
    """A TrueType face at one pixel size and weight, with cached glyph masks on a shared baseline."""

    def __init__(self, path: Optional[str], size: int, weight: Optional[int] = None, xscale: float = 1.0):
        self.size = size
        self.xs = xscale                                            # <1 squeezes the digits sideways (tall and slim)
        try:
            self.font = ImageFont.truetype(path, size) if path else ImageFont.load_default()
            if weight is not None and hasattr(self.font, "set_variation_by_axes"):
                try:
                    self.font.set_variation_by_axes([weight])
                except Exception:  # noqa: BLE001 - a static font has no weight axis
                    pass
        except Exception:  # noqa: BLE001 - unreadable file: Pillow's built-in face keeps the clock alive
            self.font = ImageFont.load_default()
        self._glyphs: Dict[str, Image.Image] = {}
        digit_box = [self.font.getbbox(d, anchor="ls") for d in "0123456789"]
        self.digit_top = min(b[1] for b in digit_box)               # negative: above the baseline
        self.digit_h = -self.digit_top
        self.base_slot = int(max(self.font.getlength(d) for d in "0123456789") + 1.5)
        self.base_colon = max(3, int(self.font.getlength(":")))
        self.slot = max(4, int(round(self.base_slot * xscale)))
        self.colon_w = max(3, int(round(self.base_colon * xscale)))
        self.pad = 3
        self.cell = (self.slot + 2 * self.pad, self.digit_h + 2 * self.pad + 3)

    def glyph(self, ch: str) -> Image.Image:
        """Mask of ``ch`` centred in a digit-slot cell; the baseline sits ``pad + digit_h`` from the top."""
        g = self._glyphs.get(ch)
        if g is None:
            w = (self.base_colon if ch == ":" else self.base_slot) + 2 * self.pad
            g = Image.new("L", (w, self.cell[1]), 0)
            shape = "O" if ch == "0" else ch                      # Orbitron's zero is slashed (reads as a crossed O): use its round O
            ImageDraw.Draw(g).text((w / 2.0, self.pad + self.digit_h), shape, fill=255, font=self.font, anchor="ms")
            if self.xs != 1.0:
                g = g.resize((max(1, int(round(w * self.xs))), g.height), Image.LANCZOS)
            self._glyphs[ch] = g
        return g

    def advance(self, ch: str) -> int:
        return self.colon_w if ch == ":" else self.slot

    def width(self, text: str) -> int:
        return sum(self.advance(c) for c in text)

    def mask_of(self, text: str) -> Tuple[Image.Image, int]:
        """A tight mask of arbitrary text (letters too) and the baseline y inside it; for small captions."""
        box = self.font.getbbox(text, anchor="ls")
        w, h = max(1, box[2] - box[0] + 2 * self.pad), max(1, box[3] - box[1] + 2 * self.pad)
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).text((self.pad - box[0], self.pad - box[1]), text, fill=255, font=self.font, anchor="ls")
        return m, self.pad - box[1]


def ease(p: float) -> float:
    p = 0.0 if p < 0 else 1.0 if p > 1 else p
    return p * p * (3.0 - 2.0 * p)


_STEADY: Dict[tuple, Tuple[Image.Image, Tuple[int, int, int, int]]] = {}
_BLURS: Dict[Tuple[int, float], Tuple[Image.Image, Image.Image]] = {}


def time_mask(face: Face, text: str, prev: str, age: float, cx: float, cy: float,
              roll: Optional[int] = None) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """The time as a frame-sized mask, centred on ``cx`` with the digits' vertical middle at ``cy``.

    A slot whose character differs from ``prev`` rolls: the old glyph slides up and fades while the new one rises
    from below, over ``ROLL_SECONDS`` (``age`` is seconds since the change). Returns the mask and the box
    (x0, y0, x1, y1) the digits occupy, for placing the extras around them.
    """
    rolling = bool(prev) and len(prev) == len(text) and 0.0 <= age < ROLL_SECONDS and prev != text
    key = (id(face), text, int(round(cx)), int(round(cy * 2)))
    if not rolling and key in _STEADY:                       # a steady time is the same picture every frame: build it once
        return _STEADY[key]
    mask = Image.new("L", (W, H), 0)
    x = int(round(cx - face.width(text) / 2.0))
    top = int(round(cy - face.digit_h / 2.0)) - face.pad                      # y of the cell's top edge
    roll = roll if roll is not None else max(6, int(face.digit_h * 0.55))
    x_start = x
    for i, ch in enumerate(text):
        adv = face.advance(ch)
        g = face.glyph(ch)
        gx = x + (adv - g.width) // 2
        if rolling and prev[i] != ch and ch != ":":
            p = ease(age / ROLL_SECONDS)
            old = face.glyph(prev[i])
            layer = Image.new("L", (W, H), 0)
            layer.paste(scale_mask(old, 1.0 - p), (gx, top - int(p * roll)))
            mask = ImageChops.lighter(mask, layer)
            layer = Image.new("L", (W, H), 0)
            layer.paste(scale_mask(g, p), (gx, top + int((1.0 - p) * roll)))
            mask = ImageChops.lighter(mask, layer)
        else:
            layer = Image.new("L", (W, H), 0)
            layer.paste(g, (gx, top))
            mask = ImageChops.lighter(mask, layer)
        x += adv
    result = (mask, (x_start, top + face.pad, x, top + face.pad + face.digit_h))
    if not rolling:
        if len(_STEADY) > 24:
            _STEADY.clear()
        _STEADY[key] = result
    return result


# ── colouring ────────────────────────────────────────────────────────────────

def vgradient(top: RGB, bottom: RGB, y0: int = 0, y1: int = H, size: Tuple[int, int] = (W, H)) -> Image.Image:
    """A frame-sized image that fades from ``top`` at ``y0`` to ``bottom`` at ``y1``."""
    strip = Image.new("RGB", (1, size[1]))
    span = max(1, y1 - y0)
    strip.putdata([tuple(int(top[k] + (bottom[k] - top[k]) * min(1.0, max(0.0, (j - y0) / span))) for k in range(3))
                   for j in range(size[1])])
    return strip.resize(size, Image.NEAREST)


def scale_mask(mask: Image.Image, a: float) -> Image.Image:
    """``mask`` times ``a`` (0..1), at C speed (Image.point with a lambda costs a Python call per level)."""
    if a >= 0.999:
        return mask
    return ImageChops.multiply(mask, Image.new("L", mask.size, max(0, min(255, int(a * 255)))))


def blurred(mask: Image.Image, radius: float) -> Image.Image:
    """A Gaussian blur of ``mask``, remembered while the same (steady) mask is in use."""
    cached = _BLURS.get((id(mask), radius))
    if cached is not None and cached[0] is mask:
        return cached[1]
    out = mask.filter(ImageFilter.GaussianBlur(radius))
    if len(_BLURS) > 24:
        _BLURS.clear()
    _BLURS[(id(mask), radius)] = (mask, out)
    return out


def fill(img: Image.Image, mask: Image.Image, color: Union[RGB, Image.Image], alpha: float = 1.0) -> None:
    """Paint ``color`` (an RGB tuple or a frame-sized image) through ``mask``."""
    img.paste(color, (0, 0), scale_mask(mask, alpha))


def glow(img: Image.Image, mask: Image.Image, color: RGB, radius: float, strength: float = 1.0) -> None:
    """Add a soft halo of ``color`` around ``mask`` (light adds, so it never darkens what is behind)."""
    halo = scale_mask(blurred(mask, radius), min(1.0, strength))
    if strength > 1.0:
        halo = ImageChops.add(halo, scale_mask(blurred(mask, radius), strength - 1.0))
    light = Image.composite(Image.new("RGB", img.size, color), Image.new("RGB", img.size, (0, 0, 0)), halo)
    img.paste(ImageChops.add(img, light))


def shadow(img: Image.Image, mask: Image.Image, offset: Tuple[int, int] = (1, 1), color: RGB = (0, 0, 8),
           alpha: float = 0.6, blur: float = 0.8) -> None:
    """A soft drop shadow under ``mask``."""
    moved = blurred(mask, blur) if blur else mask
    if offset != (0, 0):
        moved = ImageChops.offset(moved, offset[0], offset[1])
    img.paste(color, (0, 0), scale_mask(moved, alpha))
