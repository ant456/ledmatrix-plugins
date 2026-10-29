"""Player headshots and club crests for the NBA Live Court cards.

ESPN's scoreboard already names each team's points, rebounds and assists
leader and links their headshot (a transparent PNG, which is what an LED panel
wants), so no search step is needed. Photos are downloaded in ``update()``
only, stored in the plugin's cache as a downscaled PNG (no directory of our
own to prune on the SD card), and read back on the render path. Only ESPN hosts
are fetched and a download is capped at 5 MB.

Crests use the core's ``assets/sports/nba_logos/<ABBR>.png``.
"""

import base64
import io
import os
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

from PIL import Image

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9.1
    RESAMPLE = Image.LANCZOS

ALLOWED_HOSTS = (".espncdn.com", ".espn.com")
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
STORED_WIDTH = 200
IMAGE_TTL = 30 * 86400
RETRY_AFTER = 1800
NBA_LOGO_DIR = os.path.join("assets", "sports", "nba_logos")
#: The core's NBA crests are filed under ESPN's own codes (GS, NY, NO, SA, UTAH, WSH), so no renaming.
LOGO_NAMES: Dict[str, str] = {}


class HeadshotStore:
    """Downloads, stores and crops player photos; draws from the cache only."""

    def __init__(self, data, logger):
        self.data = data
        self.logger = logger
        self._images: "OrderedDict[str, Image.Image]" = OrderedDict()
        self._portraits: "OrderedDict[Tuple[Any, ...], Image.Image]" = OrderedDict()
        self._logos: "OrderedDict[Tuple[str, int, int], Optional[Image.Image]]" = OrderedDict()
        self._retry_at: Dict[str, float] = {}

    # network (update() only) ------------------------------------------------

    def has_image(self, player_id: str, now: float) -> bool:
        entry = self.data.read(self.data.key("headshot", player_id))
        return entry is not None and now - float(entry.get("fetched_at") or 0) < IMAGE_TTL

    def _download(self, player: Dict[str, Any], now: float) -> bool:
        url = player.get("headshot_url") or ""
        if not is_allowed_url(url):
            self.logger.debug("Refusing a non-ESPN headshot URL: %s", url)
            return False
        resp = self.data.session.get(url, timeout=self.data.timeout, stream=True)
        try:
            resp.raise_for_status()
            declared = resp.headers.get("Content-Length")
            if declared and str(declared).isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
                return False
            buf = bytearray()
            for chunk in resp.iter_content(16384):
                buf.extend(chunk)
                if len(buf) > MAX_DOWNLOAD_BYTES:
                    return False
        finally:
            resp.close()
        with Image.open(io.BytesIO(bytes(buf))) as src:
            img = src.convert("RGBA")
        if img.width > STORED_WIDTH:
            img = img.resize((STORED_WIDTH, max(1, round(img.height * STORED_WIDTH / img.width))), RESAMPLE)
        out = io.BytesIO()
        img.save(out, "PNG", optimize=True)
        self.data.write(self.data.key("headshot", player["id"]),
                        {"png_b64": base64.b64encode(out.getvalue()).decode("ascii")}, now)
        self._images.pop(str(player["id"]), None)
        for key in [k for k in self._portraits if k[0] == str(player["id"])]:
            self._portraits.pop(key, None)
        return True

    def prefetch(self, players: Iterable[Dict[str, Any]], max_downloads: int = 4,
                 budget_seconds: float = 8.0) -> int:
        """Fetch missing photos, bounded: update() holds the plugin and the panel waits.

        At most ``max_downloads`` photos and ``budget_seconds`` in all; a
        failing player is not retried for half an hour and one failure never
        stops the others. Returns how many photos arrived.
        """
        started, now, done = time.monotonic(), time.time(), 0
        seen = set()
        for player in players:
            pid = str(player.get("id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            if done >= max_downloads or time.monotonic() - started > budget_seconds:
                break
            if self._retry_at.get(pid, 0.0) > now or self.has_image(pid, now):
                continue
            try:
                if self._download(player, now):
                    done += 1
                else:
                    self._retry_at[pid] = now + RETRY_AFTER
            except Exception as exc:  # noqa: BLE001 - one bad photo never stops the rest
                self._retry_at[pid] = now + RETRY_AFTER
                self.logger.warning("Headshot for %s (%s) failed: %s", player.get("name"), pid, exc)
        return done

    # images (cache only; safe on the render path) -------------------------

    def _image(self, player_id: str) -> Optional[Image.Image]:
        player_id = str(player_id)
        if player_id in self._images:
            self._images.move_to_end(player_id)
            return self._images[player_id]
        entry = self.data.read(self.data.key("headshot", player_id))
        if entry is None:
            return None
        try:
            raw = base64.b64decode(((entry.get("data") or {}).get("png_b64") or "").encode("ascii"))
            with Image.open(io.BytesIO(raw)) as src:
                img = src.convert("RGBA")
        except Exception as exc:  # noqa: BLE001 - a corrupt entry is just a miss
            self.logger.debug("Stored headshot %s unreadable: %s", player_id, exc)
            return None
        self._images[player_id] = img
        while len(self._images) > 40:
            self._images.popitem(last=False)
        return img

    def portrait(self, player: Dict[str, Any], width: int, height: int) -> Optional[Image.Image]:
        """The player's headshot cropped to fill ``width`` x ``height`` (RGBA)."""
        if width < 4 or height < 4 or not player.get("id"):
            return None
        key = (str(player["id"]), width, height)
        if key in self._portraits:
            self._portraits.move_to_end(key)
            return self._portraits[key]
        src = self._image(player["id"])
        if src is None:
            return None
        out = crop_portrait(src, width, height)
        self._portraits[key] = out
        while len(self._portraits) > 60:
            self._portraits.popitem(last=False)
        return out

    def logo(self, abbr: str, width: int, height: int) -> Optional[Image.Image]:
        """The club crest scaled to fit ``width`` x ``height``, or None."""
        key = (abbr, width, height)
        if key in self._logos:
            return self._logos[key]
        logo = None
        path = logo_path(abbr)
        if path and width > 2 and height > 2:
            try:
                with Image.open(path) as src:
                    img = src.convert("RGBA")
                bbox = img.getbbox()
                if bbox:
                    img = img.crop(bbox)
                ratio = min(width / img.width, height / img.height)
                logo = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))), RESAMPLE)
            except Exception as exc:  # noqa: BLE001 - a bad crest is just absent
                self.logger.debug("Crest %s unreadable: %s", abbr, exc)
        self._logos[key] = logo
        while len(self._logos) > 48:
            self._logos.popitem(last=False)
        return logo


def is_allowed_url(url: str) -> bool:
    try:
        parts = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if parts.scheme not in ("https", "http"):
        return False
    host = (parts.hostname or "").lower()
    return any(host == h.lstrip(".") or host.endswith(h) for h in ALLOWED_HOSTS)


def crop_portrait(src: Image.Image, width: int, height: int) -> Image.Image:
    """Crop ESPN's head-and-shoulders frame to fill a box, tuned for LEDs.

    ESPN frames the face top-centre. A tall box keeps the shoulders; a small
    one (under 32 px) crops in on the face. Colour and contrast get a small
    lift because a panel shows photos darker and flatter than a monitor.
    """
    w0, h0 = src.size
    aspect = width / float(height)
    crop_h = h0 if height >= 32 else int(h0 * 0.78)
    crop_w = int(round(crop_h * aspect))
    if crop_w > w0:
        crop_w = w0
        crop_h = int(round(crop_w / aspect))
    left = max(0, (w0 - crop_w) // 2)
    img = src.crop((left, 0, left + crop_w, crop_h)).resize((width, height), RESAMPLE)
    rgb = led_boost(img.convert("RGB"))
    rgb.putalpha(img.getchannel("A"))
    return rgb


def led_boost(rgb: Image.Image) -> Image.Image:
    """+25% saturation and +12% contrast in integer arithmetic (same bytes on every host)."""
    data = bytearray(rgb.tobytes())
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        grey = (r * 299 + g * 587 + b * 114) // 1000
        out = []
        for c in (r, g, b):
            c = grey + (c - grey) * 5 // 4
            c = 128 + (c - 128) * 112 // 100
            out.append(0 if c < 0 else 255 if c > 255 else c)
        data[i], data[i + 1], data[i + 2] = out
    return Image.frombytes("RGB", rgb.size, bytes(data))


def asset_roots() -> Tuple[Path, ...]:
    """Where the core's ``assets/`` tree may be, best candidate first."""
    here = Path(__file__).resolve()
    roots = [Path("."), here.parent.parent.parent, here.parent]
    env_root = os.environ.get("LEDMATRIX_CORE")
    if env_root:
        roots.insert(0, Path(env_root))
    core_file = getattr(sys.modules.get("src"), "__file__", None)
    if core_file:
        roots.insert(0, Path(core_file).resolve().parent.parent)
    return tuple(roots)


def logo_path(abbr: str) -> Optional[str]:
    if not abbr:
        return None
    for name in (abbr, LOGO_NAMES.get(abbr, abbr)):
        for root in asset_roots():
            candidate = Path(root, NBA_LOGO_DIR, f"{name}.png")
            if candidate.exists():
                return str(candidate)
    return None
