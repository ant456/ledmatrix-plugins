"""Native bitmap faces from the BDF fonts shipped in LEDMatrix's assets/fonts.

The 3x5 arcade font scaled 2x or 3x has chunky stair-stepped edges. A BDF face
is drawn at its design size, so a 10px title or a 15px temperature has real
curves. A face can be passed anywhere the arcade font takes a ``scale``.

Only printable ASCII is read. ``height`` is the cap height (what layout cares
about); the mask may reach a few rows lower for descenders such as the comma.
"""

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image

_FACES: Dict[Tuple[str, Tuple[str, ...]], Optional["BdfFace"]] = {}


class BdfFace:
    def __init__(self, path: str):
        ascent = descent = 0
        raw: Dict[str, Tuple[int, int, int, int, int, int, List[int]]] = {}
        cur: Dict[str, object] = {}
        bitmap: Optional[List[int]] = None
        with open(path, "r", encoding="latin-1") as fh:
            for line in fh:
                parts = line.split()
                if not parts:
                    continue
                key = parts[0]
                if bitmap is not None:
                    if key == "ENDCHAR":
                        enc = cur.get("enc", -1)
                        if 32 <= enc <= 126:                          # type: ignore[operator]
                            w, h, xo, yo = cur["bbx"]                  # type: ignore[misc]
                            raw[chr(enc)] = (cur["adv"], w, h, xo, yo,         # type: ignore[arg-type]
                                             cur.get("hexw", 2) * 4, bitmap)  # type: ignore[operator]
                        bitmap = None
                    else:
                        bitmap.append(int(key, 16))
                        cur.setdefault("hexw", len(key))
                elif key == "FONT_ASCENT":
                    ascent = int(parts[1])
                elif key == "FONT_DESCENT":
                    descent = int(parts[1])
                elif key == "STARTCHAR":
                    cur = {}
                elif key == "ENCODING":
                    cur["enc"] = int(parts[1])
                elif key == "DWIDTH":
                    cur["adv"] = int(parts[1])
                elif key == "BBX":
                    cur["bbx"] = tuple(int(p) for p in parts[1:5])
                elif key == "BITMAP":
                    bitmap = []
        if "H" not in raw:
            raise ValueError("%s has no ASCII capitals" % path)

        box_h = ascent + descent
        cells: Dict[str, Image.Image] = {}
        self._adv: Dict[str, int] = {}
        for ch, (adv, w, h, xo, yo, hex_bits, rows) in raw.items():
            cell = Image.new("L", (max(adv, xo + w), box_h), 0)
            px = cell.load()
            for j, row in enumerate(rows[:h]):
                y = ascent - (yo + h) + j
                if not 0 <= y < box_h:
                    continue
                for i in range(w):
                    if row >> (hex_bits - 1 - i) & 1 and 0 <= xo + i < cell.width:
                        px[xo + i, y] = 255
            cells[ch] = cell
            self._adv[ch] = adv

        # cap rows come from the letter H, so layout matches what is actually lit
        hrows = [y for y in range(box_h) for x in range(cells["H"].width) if cells["H"].getpixel((x, y))]
        self._top = min(hrows)
        self.height = max(hrows) - self._top + 1
        self._cells = {ch: c.crop((0, self._top, c.width, box_h)) for ch, c in cells.items()}
        self._mask_h = box_h - self._top
        self.name = os.path.splitext(os.path.basename(path))[0]

    def _cell(self, ch: str) -> Tuple[Image.Image, int]:
        ch = ch if ch in self._cells else "?"
        return self._cells.get(ch) or self._cells[" "], self._adv.get(ch, self._adv.get(" ", 4))

    def width(self, text: str) -> int:
        if not text:
            return 0
        total = sum(self._cell(ch)[1] for ch in text)
        last, adv = self._cell(text[-1])
        bbox = last.getbbox()
        trailing = max(0, adv - bbox[2]) if bbox else adv
        return max(0, total - trailing)

    def mask(self, text: str) -> Image.Image:
        width = max(1, sum(self._cell(ch)[1] for ch in text))
        mask = Image.new("L", (width, self._mask_h), 0)
        x = 0
        for ch in text:
            cell, adv = self._cell(ch)
            mask.paste(cell, (x, 0))
            x += adv
        return mask.crop((0, 0, max(1, self.width(text)), self._mask_h))


def load(name: str, extra_dirs: Iterable[str] = ()) -> Optional[BdfFace]:
    """The face ``name`` (e.g. ``"6x10"``), or None if its .bdf file is not found."""
    dirs = tuple(str(d) for d in extra_dirs)
    key = (name, dirs)
    if key in _FACES:
        return _FACES[key]
    candidates = [Path(d) / (name + ".bdf") for d in dirs]
    candidates.append(Path(os.environ.get("ARCADE_FONT_DIR", "")) / (name + ".bdf"))
    face = None
    for path in candidates:
        if path.is_file():
            try:
                face = BdfFace(str(path))
                break
            except Exception:
                continue
    _FACES[key] = face
    return face
