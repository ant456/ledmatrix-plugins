"""
Media Marquee Plugin for LEDMatrix  (plex-marquee + now-playing, arcade style)

One plugin for everything that plays in the house.

  Plex video with a TMDB match   -> Fanart.tv banner, alternating with an info card
  Anything with player art       -> info card with the art (music, native Plex entity)
  Everything else (YouTube TV,   -> info card with the LaMetric service icon,
  Netflix, Disney+ ...)             title, time remaining and a segmented progress bar
  Nothing playing                -> rotating banners of recently added Plex titles

Data comes from Home Assistant media_player entities (REST polling, no MQTT).
Only Plex playback is sent to TMDB, so a YouTube TV show can never pull in the
banner of a show you don't own.

Config (see config_schema.json):
  ha_url / ha_token        Home Assistant
  entities                 media_player entity ids, in priority order
  entity_services          optional {entity_id: service_key} for players that
                           report no app_name (e.g. a music player -> "spotify")
  tmdb_token               TMDB API Read Access Token (starts with eyJ)
  fanart_api_key           Fanart.tv API key
  plex_url / plex_token    Plex server, for the idle recently-added rotation
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from src.plugin_system.base_plugin import BasePlugin

import arcade_draw as ad
import arcade_screens as screens

try:
    from flask import Flask, Response, jsonify, request
    FLASK_AVAILABLE = True
except ImportError:                                   # portal is optional
    FLASK_AVAILABLE = False

Color = Tuple[int, int, int]

# Hosts the portal may fetch for previews / saved banners (blocks SSRF via user input)
IMAGE_HOSTS = ("assets.fanart.tv", "fanart.tv", "image.tmdb.org")

# ═══════════════════════════════════════════════════════════════════════════════
# Services  (names, colors and LaMetric ids carried over from now-playing)
# ═══════════════════════════════════════════════════════════════════════════════

# HA app_name (lower-case) -> (service key, color)
SERVICE_MAP: Dict[str, Tuple[str, Color]] = {
    "netflix":            ("netflix",       (229, 9, 20)),
    "hulu":               ("hulu",          (28, 231, 131)),
    "youtube":            ("youtube",       (255, 0, 0)),
    "youtube tv":         ("youtube_tv",    (255, 0, 0)),
    "youtube music":      ("youtube_music", (255, 0, 0)),
    "disney+":            ("disney",        (17, 60, 207)),
    "disney plus":        ("disney",        (17, 60, 207)),
    "spotify":            ("spotify",       (30, 215, 96)),
    "prime video":        ("prime",         (0, 168, 225)),
    "amazon prime video": ("prime",         (0, 168, 225)),
    "max":                ("max",           (120, 30, 230)),
    "hbo max":            ("max",           (120, 30, 230)),
    "plex":               ("plex",          (229, 169, 59)),
}

# Looser matches for app names like "Plex for Android TV"
SERVICE_CONTAINS: List[Tuple[str, str]] = [
    ("youtube tv", "youtube_tv"), ("youtube music", "youtube_music"),
    ("youtube", "youtube"), ("netflix", "netflix"), ("disney", "disney"),
    ("plex", "plex"), ("spotify", "spotify"), ("hulu", "hulu"),
    ("prime", "prime"), ("hbo", "max"),
]

SERVICE_COLORS: Dict[str, Color] = {v[0]: v[1] for v in SERVICE_MAP.values()}
DEFAULT_COLOR: Color = (255, 220, 60)

SERVICE_LABELS: Dict[str, str] = {
    "netflix": "Netflix", "hulu": "Hulu", "youtube": "YouTube",
    "youtube_tv": "YouTube TV", "youtube_music": "YT Music", "disney": "Disney+",
    "spotify": "Spotify", "prime": "Prime Video", "max": "Max", "plex": "Plex",
}

LAMETRIC_ICON_IDS: Dict[str, str] = {
    "netflix": "1442", "hulu": "36126", "youtube": "26963", "youtube_tv": "26963",
    "youtube_music": "26963", "disney": "69251", "plex": "15185",
    "spotify": "647", "prime": "69250", "max": "72187",
}
# Shared with now-playing and ha-notifications, so icons fetched by any of the
# three are instantly available to the others.
LAMETRIC_CACHE_DIR = os.path.expanduser("~/.cache/ledmatrix/lametric_icons")

BG: Color = (0, 0, 0)
WHITE: Color = (255, 255, 255)
DIM: Color = (150, 150, 160)
EMPTY_SEG: Color = (36, 36, 44)
COIN: Color = (255, 214, 10)


# ═══════════════════════════════════════════════════════════════════════════════
# Small helpers
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_service(app_name: str, entity_id: str = "",
                    overrides: Optional[Dict[str, str]] = None) -> Tuple[str, Color, str]:
    """Return (service_key, color, label) for a player."""
    overrides = overrides or {}
    key = overrides.get(entity_id)
    if not key:
        name = (app_name or "").strip().lower()
        if name in SERVICE_MAP:
            key = SERVICE_MAP[name][0]
        else:
            key = next((k for needle, k in SERVICE_CONTAINS if needle in name), None)
        if not key and entity_id.startswith("media_player.plex"):
            key = "plex"
    if key:
        label = SERVICE_LABELS.get(key, key.replace("_", " ").title())
        return key, SERVICE_COLORS.get(key, DEFAULT_COLOR), label
    label = (app_name or "").strip() or "Media"
    return "generic", DEFAULT_COLOR, label


def ascii_clean(text: str) -> str:
    """PressStart2P only has basic Latin: fold accents, fix curly quotes."""
    if not text:
        return ""
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace("–", "-").replace("—", "-").replace("…", "..."))
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii").strip()


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _parse_ha_time(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Data
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Media:
    entity_id:   str
    state:       str                    # "playing" | "paused"
    app_name:    str
    service:     str
    color:       Color
    label:       str
    title:       str                    # series name if TV, else movie/track title
    subtitle:    str                    # episode title / artist
    year:        str
    media_type:  str                    # "movie" | "tv"
    art_path:    str                    # HA entity_picture path or ""
    pos_base:    float                  # media_position at pos_base_t
    pos_base_t:  float                  # epoch seconds
    duration:    float
    use_tmdb:    bool
    has_pos:     bool = False           # player actually reported media_position
    key:         str = ""               # identity, changes when the content changes
    first_seen:  float = field(default_factory=time.time)

    @property
    def is_playing(self) -> bool:
        return self.state == "playing"

    def position_now(self, now: float) -> float:
        pos = self.pos_base
        if self.is_playing and self.pos_base_t:
            pos += max(0.0, now - self.pos_base_t)
        if self.duration > 0:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    def has_timing(self) -> bool:
        return self.duration > 0


@dataclass
class Resolved:
    """Everything fetched from the network for one piece of media."""
    key:     str
    done:    bool = False
    banner:  Optional[Image.Image] = None     # W x H
    art:     Optional[Image.Image] = None     # player art, else TMDB poster (RGB)
    art_src: str = ""                         # "player" | "poster" | ""


# ═══════════════════════════════════════════════════════════════════════════════
# Plugin
# ═══════════════════════════════════════════════════════════════════════════════

class MediaMarqueePlugin(BasePlugin):

    BURST_SECONDS = 4.0
    TMDB_BASE   = "https://api.themoviedb.org/3"
    TMDB_IMG    = "https://image.tmdb.org/t/p/w185"
    FANART_BASE = "https://webservice.fanart.tv/v3"

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        c = config
        self.ha_url:   str = str(c.get("ha_url", "")).rstrip("/")
        self.ha_token: str = str(c.get("ha_token", ""))
        self.entities: List[str] = list(c.get("entities", []))
        self.entity_services: Dict[str, str] = dict(c.get("entity_services", {}))
        self.tmdb_services = {s.lower() for s in c.get("tmdb_services", ["plex"])}

        self.tmdb_token:     str = str(c.get("tmdb_token", ""))
        self.fanart_api_key: str = str(c.get("fanart_api_key", ""))
        self.plex_url:   str = str(c.get("plex_url", "")).rstrip("/")
        self.plex_token: str = str(c.get("plex_token", ""))

        self.poll_seconds:      float = float(c.get("poll_seconds", 4))
        self.banner_seconds:    float = float(c.get("banner_seconds", 12))
        self.info_seconds:      float = float(c.get("info_seconds", 8))
        self.rotation_duration: float = float(c.get("rotation_duration", 30))
        self.auto_recent_count: int   = int(c.get("auto_recent_count", 20))
        self.paused_timeout:    float = float(c.get("paused_timeout", 600))
        self.scroll_speed:      float = float(c.get("scroll_px_per_sec", 22))
        self.uppercase:         bool  = bool(c.get("uppercase", True))
        self.service_badge:     bool  = bool(c.get("show_service_badge", True))
        self.banner_progress:   bool  = bool(c.get("banner_progress", True))
        self.now_burst:         bool  = bool(c.get("now_playing_burst", True))

        self.W: int = display_manager.width
        self.H: int = display_manager.height

        self.plugin_dir = Path(__file__).parent
        self.cache_dir  = self.plugin_dir / "cache"
        self.icons_dir  = self.plugin_dir / "icons"       # optional <service>.png overrides
        self.library_path = self.plugin_dir / "library.json"   # optional curated banners
        self.cache_dir.mkdir(exist_ok=True)

        self.gifs_dir   = self.plugin_dir / "gifs"        # optional <service>.gif full-screen

        # Big text uses the BDF fonts shipped with LEDMatrix; missing files fall back to the 3x5 font.
        font_dirs = (str(self.plugin_dir.parent.parent / "assets" / "fonts"),
                     "/home/pi/LEDMatrix/assets/fonts")
        screens.load_faces(str(c.get("title_font", "7x13B")), "9x15B", font_dirs)
        self.portal_port:    int  = int(c.get("portal_port", 5010))
        self.portal_enabled: bool = bool(c.get("portal_enabled", True))
        self.max_upload_mb:  int  = int(c.get("max_upload_mb", 8))

        self._font_path = str(c.get("font_path", "")) or ""
        self._fonts: Dict[int, Any] = {}

        # Playback
        self._lock = threading.Lock()
        self._media: Optional[Media] = None
        self._resolved: Optional[Resolved] = None
        self._poll_t = 0.0
        self._polling = False
        self._phase_t0 = time.time()
        self._visit_start = time.time()
        self._cycle_done = False
        self._burst_init = False                    # first compose() adopts whatever is already playing
        self._burst_key: Optional[str] = None
        self._burst_t0 = 0.0

        # Caches
        self._art_cache: Dict[str, Optional[Image.Image]] = {}
        self._tvdb_cache: Dict[int, Optional[int]] = {}
        self._icon_frames: Dict[str, Optional[List[Image.Image]]] = {}
        self._tile_cache: Dict[Tuple, Image.Image] = {}
        self._gif_cache: Dict[str, Optional[Tuple[List[Image.Image], List[int]]]] = {}
        self._icon_loading: set = set()
        self._entity_status: Dict[str, Dict[str, str]] = {}

        # Curated banner library (edited from the portal)
        self._lib_lock = threading.Lock()
        self._library: List[Dict[str, Any]] = self._load_library()
        self._curated_imgs: Dict[str, Image.Image] = {}
        self._curated_busy = False

        # Idle rotation
        self._auto: List[Dict[str, Any]] = []        # {id, title, img}
        self._auto_fetched = 0.0
        self._auto_busy = False
        self._rot_idx = -1
        self._rot_start = 0.0
        self._rot_img: Optional[Image.Image] = None

        # Ask the display controller for a high frame rate (scroll + animation)
        self.enable_scrolling = True

        self._ensure_curated_imgs()

        if self.portal_enabled:
            if FLASK_AVAILABLE:
                self._start_portal()
            else:
                self.logger.warning("Flask not installed -- portal disabled (pip install Flask)")

        self.logger.info("Media Marquee ready  W=%d H=%d  entities=%d  library=%d  portal=:%d",
                         self.W, self.H, len(self.entities), len(self._library), self.portal_port)

    # ── Fonts ────────────────────────────────────────────────────────────────

    def _font(self, size: int):
        if size in self._fonts:
            return self._fonts[size]
        candidates = [self._font_path,
                      str(self.plugin_dir.parent.parent / "assets" / "fonts" / "PressStart2P-Regular.ttf"),
                      "/home/pi/LEDMatrix/assets/fonts/PressStart2P-Regular.ttf"]
        font = None
        for p in candidates:
            if p and os.path.exists(p):
                try:
                    font = ImageFont.truetype(p, size)
                    break
                except Exception:
                    continue
        if font is None:
            try:
                font = ImageFont.load_default(size)
            except TypeError:
                font = ImageFont.load_default()
        self._fonts[size] = font
        return font

    @staticmethod
    def _text_w(text: str, font) -> int:
        try:
            return int(font.getlength(text))
        except Exception:
            return len(text) * 8

    def _txt(self, s: str) -> str:
        s = ascii_clean(s)
        return s.upper() if self.uppercase else s

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _get_bytes(self, url: str, headers: Optional[Dict[str, str]] = None,
                   timeout: float = 8) -> Optional[bytes]:
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "LEDMatrix-MediaMarquee/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            # never log the url: HA proxy urls carry short-lived tokens
            self.logger.debug("GET failed (%s): %s", type(e).__name__, e)
            return None

    def _get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Optional[Dict]:
        raw = self._get_bytes(url, headers)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    # ═════════════════════════════════════════════════════════════════════════
    # Home Assistant
    # ═════════════════════════════════════════════════════════════════════════

    def _ha_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.ha_token}", "Content-Type": "application/json"}

    def parse_state(self, data: Dict[str, Any], poll_time: float) -> Optional[Media]:
        """Turn one HA /api/states/<entity> payload into a Media, or None."""
        if not data or data.get("state") not in ("playing", "paused"):
            return None
        eid = str(data.get("entity_id", ""))
        a = data.get("attributes", {}) or {}
        app = str(a.get("app_name") or "")
        service, color, label = resolve_service(app, eid, self.entity_services)

        series = str(a.get("media_series_title") or "")
        mtitle = str(a.get("media_title") or "")
        ctype  = str(a.get("media_content_type") or "").lower()
        if series:
            title, sub, mtype = series, mtitle, "tv"
        else:
            title, sub = mtitle, str(a.get("media_artist") or "")
            mtype = "tv" if ctype in ("tvshow", "episode") else "movie"

        year = str(a.get("media_year", "") or "")
        m = re.search(r"\((\d{4})\)\s*$", title)
        if m:
            year = year or m.group(1)
            title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()

        pos = _num(a.get("media_position"))
        pos_t = _parse_ha_time(a.get("media_position_updated_at")) or poll_time
        art = str(a.get("entity_picture_local") or a.get("entity_picture") or "")

        key = f"{eid}|{series}|{mtitle}|{app}"
        return Media(
            entity_id=eid, state=data["state"], app_name=app, service=service,
            color=color, label=label, title=title, subtitle=sub, year=year,
            media_type=mtype, art_path=art, pos_base=pos, pos_base_t=pos_t,
            duration=_num(a.get("media_duration")),
            use_tmdb=(service in self.tmdb_services and bool(title)),
            has_pos=a.get("media_position") is not None, key=key,
        )

    def _poll_ha(self) -> None:
        try:
            now = time.time()
            found: List[Media] = []
            for eid in self.entities:
                data = self._get_json(f"{self.ha_url}/api/states/{eid}", self._ha_headers())
                self._entity_status[eid] = {
                    "state": str(data.get("state")) if data else "unreachable",
                    "app": str((data or {}).get("attributes", {}).get("app_name") or ""),
                }
                m = self.parse_state(data, now) if data else None
                if m:
                    found.append(m)

            with self._lock:
                cur = self._media
            chosen: Optional[Media] = None
            playing = [m for m in found if m.is_playing]
            if cur is not None:
                # stay on the current player while it is still active (no flapping)
                chosen = next((m for m in playing if m.entity_id == cur.entity_id), None)
            if chosen is None and playing:
                chosen = playing[0]
            if chosen is None and found:
                same = next((m for m in found if cur and m.entity_id == cur.entity_id), None)
                chosen = same or found[0]

            self._set_media(chosen)
        except Exception as e:
            self.logger.error("HA poll failed: %s", e)
        finally:
            self._polling = False

    def _set_media(self, new: Optional[Media]) -> None:
        with self._lock:
            old = self._media
            if new is None:
                self._media = None
                return
            if old is not None and old.key == new.key:
                new.first_seen = old.first_seen           # same content: keep clock
                self._media = new
                return
            self._media = new
            self._resolved = Resolved(key=new.key)
            self._phase_t0 = time.time()
        self.logger.info("Now playing: %s (%s)", new.title or new.label, new.label)
        threading.Thread(target=self._resolve, args=(new,), daemon=True).start()

    def _maybe_poll(self) -> None:
        now = time.time()
        if self._polling or not (self.ha_url and self.ha_token and self.entities):
            return
        if now - self._poll_t < self.poll_seconds:
            return
        self._poll_t = now
        self._polling = True
        threading.Thread(target=self._poll_ha, daemon=True).start()

    def _active(self) -> Optional[Media]:
        with self._lock:
            m = self._media
        if m is None:
            return None
        if not m.is_playing and (time.time() - m.pos_base_t) > self.paused_timeout \
                and (time.time() - m.first_seen) > self.paused_timeout:
            return None
        return m

    # ═════════════════════════════════════════════════════════════════════════
    # Resolve art / banner for the current media (background thread)
    # ═════════════════════════════════════════════════════════════════════════

    def _resolve(self, m: Media) -> None:
        res = Resolved(key=m.key)
        try:
            # 1) player art (music players, native Plex entity)
            if m.art_path:
                res.art = self._fetch_player_art(m.art_path)
                res.art_src = "player" if res.art else ""

            # 2) Plex video -> TMDB -> Fanart banner (+ poster as art fallback)
            if m.use_tmdb and self.tmdb_token and self.fanart_api_key:
                hit = self._tmdb_search(m.title, m.year, m.media_type)
                if hit:
                    banner_url = self._pick_preferred(hit["id"]) or \
                                 self._fanart_best_banner(hit["id"], m.media_type)
                    if banner_url:
                        res.banner = self._load_banner(banner_url, f"b_{m.media_type}_{hit['id']}")
                    if res.art is None and hit.get("poster"):
                        res.art = self._fetch_image(self.TMDB_IMG + hit["poster"])
                        res.art_src = "poster" if res.art else ""
                else:
                    self.logger.info("No TMDB match for '%s'", m.title)

            # 3) service icon (warm the cache)
            self._icon(m.service)
        except Exception as e:
            self.logger.error("resolve failed: %s", e)
        res.done = True
        with self._lock:
            if self._media is not None and self._media.key == m.key:
                self._resolved = res

    def _fetch_image(self, url: str, headers: Optional[Dict[str, str]] = None) -> Optional[Image.Image]:
        raw = self._get_bytes(url, headers, timeout=10)
        if not raw:
            return None
        try:
            return Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            return None

    def _fetch_player_art(self, path: str) -> Optional[Image.Image]:
        url = f"{self.ha_url}{path}" if path.startswith("/") else path
        if url in self._art_cache:
            return self._art_cache[url]
        img = self._fetch_image(url, {"Authorization": f"Bearer {self.ha_token}"}) \
            if url.startswith(self.ha_url) else self._fetch_image(url)
        if len(self._art_cache) > 8:
            self._art_cache.pop(next(iter(self._art_cache)))
        self._art_cache[url] = img
        return img

    # ── TMDB ─────────────────────────────────────────────────────────────────

    def _tmdb_search(self, title: str, year: str, media_type: str) -> Optional[Dict[str, Any]]:
        """Best TMDB match, or None. Rejects results whose name doesn't resemble the title."""
        if not self.tmdb_token or not title:
            return None
        hdrs = {"Authorization": f"Bearer {self.tmdb_token}", "Accept": "application/json"}
        movie = media_type == "movie"
        for use_year in ((True, False) if year else (False,)):
            params = {"query": title, "language": "en-US", "page": 1}
            if use_year:
                params["year" if movie else "first_air_date_year"] = year
            data = self._get_json(
                f"{self.TMDB_BASE}/search/{'movie' if movie else 'tv'}?{urllib.parse.urlencode(params)}", hdrs)
            best, best_score = None, 0.0
            for rank, r in enumerate(((data or {}).get("results") or [])[:6]):
                names = [r.get("title"), r.get("original_title")] if movie \
                    else [r.get("name"), r.get("original_name")]
                score = max(SequenceMatcher(None, _norm(title), _norm(n or "")).ratio() for n in names)
                ry = (r.get("release_date") or r.get("first_air_date") or "")[:4]
                if year and ry == year:
                    score += 0.05
                score -= rank * 0.01
                if score > best_score:
                    best, best_score = r, score
            if best and best_score >= 0.6:
                return {"id": best.get("id"), "poster": best.get("poster_path")}
        return None

    def _tvdb_id(self, tmdb_id: int) -> Optional[int]:
        """Fanart.tv's /tv/ endpoint needs the TVDB id."""
        if tmdb_id in self._tvdb_cache:
            return self._tvdb_cache[tmdb_id]
        hdrs = {"Authorization": f"Bearer {self.tmdb_token}", "Accept": "application/json"}
        data = self._get_json(f"{self.TMDB_BASE}/tv/{tmdb_id}/external_ids", hdrs)
        tvdb = int(data["tvdb_id"]) if data and data.get("tvdb_id") else None
        self._tvdb_cache[tmdb_id] = tvdb
        return tvdb

    # ── Fanart.tv ────────────────────────────────────────────────────────────

    def _fanart_all(self, tmdb_id: int, media_type: str) -> List[Dict[str, Any]]:
        """All Fanart.tv images for a title: [{url, type, lang, likes}], banners first."""
        if media_type == "movie":
            fid, endpoint = tmdb_id, "movies"
            keys = ["moviebanner", "moviethumb", "moviebackground"]
        else:
            fid, endpoint = self._tvdb_id(tmdb_id), "tv"
            keys = ["tvbanner", "tvthumb", "showbackground"]
            if not fid:
                return []
        data = self._get_json(f"{self.FANART_BASE}/{endpoint}/{fid}?api_key={self.fanart_api_key}")
        if not data:
            return []
        out: List[Dict[str, Any]] = []
        for key in keys:                       # wide banners first, then the rest
            items = sorted(data.get(key, []),
                           key=lambda x: (x.get("lang") not in ("en", "", "00"), -int(x.get("likes", 0) or 0)))
            out += [{"url": i["url"], "type": key, "lang": i.get("lang", ""),
                     "likes": int(i.get("likes", 0) or 0)} for i in items if i.get("url")]
        return out

    def _fanart_best_banner(self, tmdb_id: int, media_type: str) -> Optional[str]:
        items = self._fanart_all(tmdb_id, media_type)
        return items[0]["url"] if items else None

    def _fit_banner(self, img: Image.Image) -> Image.Image:
        """Stretch near-banner ratios, centre-crop anything much taller."""
        w, h = img.size
        ratio = (w / h) / (self.W / self.H)
        if 0.7 <= ratio <= 1.45:
            return img.resize((self.W, self.H), Image.LANCZOS)
        return self._cover(img)

    def _cover(self, img: Image.Image) -> Image.Image:
        """Scale to fill W x H, centre-crop the overflow."""
        w, h = img.size
        scale = max(self.W / w, self.H / h)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        img = img.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - self.W) // 2, (nh - self.H) // 2
        return img.crop((left, top, left + self.W, top + self.H))

    def _load_banner(self, url: str, cache_key: str) -> Optional[Image.Image]:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", cache_key)[:60]
        cpath = self.cache_dir / f"{safe}_{self.W}x{self.H}.jpg"
        if cpath.exists():
            try:
                return Image.open(str(cpath)).convert("RGB")
            except Exception:
                pass
        img = self._fetch_image(url)
        if img is None:
            return None
        img = self._fit_banner(img)
        try:
            img.save(str(cpath), "JPEG", quality=88)
        except Exception:
            pass
        return img

    # ── Curated library (optional, same format as plex-marquee) ──────────────

    def _load_library(self) -> List[Dict[str, Any]]:
        if not self.library_path.exists():
            return []
        try:
            return [e for e in json.loads(self.library_path.read_text())
                    if isinstance(e, dict) and e.get("url") and e.get("id")]
        except Exception:
            return []

    def _save_library(self) -> None:
        """Caller holds _lib_lock."""
        try:
            tmp = self.library_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._library, indent=2))
            os.replace(tmp, self.library_path)
        except Exception as e:
            self.logger.error("Save library: %s", e)

    def _enabled_library(self) -> List[Dict[str, Any]]:
        with self._lib_lock:
            return [dict(e) for e in self._library if e.get("enabled", True)]

    def _pick_preferred(self, tmdb_id: int) -> Optional[str]:
        """A banner you picked in the portal wins over the automatic one."""
        for e in self._enabled_library():
            if int(e.get("tmdb_id", 0) or 0) == tmdb_id:
                return e["url"]
        return None

    def _ensure_curated_imgs(self) -> None:
        """Download/scale curated banners in the background; rotation uses what's ready."""
        if self._curated_busy:
            return                      # the running loader re-checks for new entries when it finishes
        if not [e for e in self._enabled_library() if e["id"] not in self._curated_imgs]:
            return
        self._curated_busy = True

        def run():
            tried: set = set()
            try:
                while True:             # keep going until nothing new is waiting (entries added mid-run)
                    want = [e for e in self._enabled_library()
                            if e["id"] not in self._curated_imgs and e["id"] not in tried]
                    if not want:
                        break
                    for e in want:
                        tried.add(e["id"])          # a failed download is retried by a later update()
                        img = self._load_banner(e["url"], f"lib_{e['id']}")
                        if img:
                            self._curated_imgs[e["id"]] = img
            finally:
                self._curated_busy = False
        threading.Thread(target=run, daemon=True).start()

    # ═════════════════════════════════════════════════════════════════════════
    # Idle rotation: recently added on Plex
    # ═════════════════════════════════════════════════════════════════════════

    def _plex_xml(self, path: str):
        import xml.etree.ElementTree as ET
        if not self.plex_url or not self.plex_token:
            return None
        sep = "&" if "?" in path else "?"
        raw = self._get_bytes(f"{self.plex_url}{path}{sep}X-Plex-Token={self.plex_token}")
        try:
            return ET.fromstring(raw) if raw else None
        except Exception:
            return None

    def _plex_recent(self) -> List[Dict[str, str]]:
        """Recently added titles. Episodes/seasons are reduced to their SHOW name
        (searching TMDB for an episode title like 'Pilot' returns the wrong show)."""
        results: List[Dict[str, str]] = []
        seen = set()
        for section_type, plex_type in (("movie", "1"), ("show", "2")):
            root = self._plex_xml(f"/library/recentlyAdded?type={plex_type}")
            if root is None:
                continue
            for item in root:
                t = item.get("type", "")
                if t == "episode":
                    title, year = item.get("grandparentTitle", ""), ""
                elif t == "season":
                    title, year = item.get("parentTitle", ""), ""
                elif t in ("movie", "show"):
                    title, year = item.get("title", ""), str(item.get("year", "") or "")
                else:
                    continue
                if title and (title, section_type) not in seen:
                    seen.add((title, section_type))
                    results.append({"title": title, "year": year, "type": section_type})
        return results[: self.auto_recent_count]

    def _refresh_auto(self) -> None:
        try:
            banners: List[Dict[str, Any]] = []
            for item in self._plex_recent():
                mtype = "movie" if item["type"] == "movie" else "tv"
                hit = self._tmdb_search(item["title"], item["year"], mtype)
                if not hit:
                    continue
                url = self._pick_preferred(hit["id"]) or self._fanart_best_banner(hit["id"], mtype)
                img = self._load_banner(url, f"b_{mtype}_{hit['id']}") if url else None
                if img:
                    banners.append({"id": f"{mtype}{hit['id']}", "title": item["title"], "img": img})
            self._auto = banners
            self._auto_fetched = time.time()
            self.logger.info("Auto banners: %d titles", len(banners))
        except Exception as e:
            self.logger.error("auto refresh failed: %s", e)
            self._auto_fetched = time.time() - 21600 + 600       # retry in 10 min
        finally:
            self._auto_busy = False

    def _maybe_refresh_auto(self) -> None:
        if self._auto_busy or not (self.plex_url and self.plex_token and self.tmdb_token and self.fanart_api_key):
            return
        if time.time() - self._auto_fetched < 21600:
            return
        self._auto_busy = True
        threading.Thread(target=self._refresh_auto, daemon=True).start()

    def _rotation_pool(self) -> List[Dict[str, Any]]:
        """Banners you curated in the portal, else recently added from Plex."""
        curated = [{"id": e["id"], "title": e.get("title", ""), "img": self._curated_imgs[e["id"]]}
                   for e in self._enabled_library() if e["id"] in self._curated_imgs]
        return curated or self._auto

    def _rotation_image(self) -> Optional[Image.Image]:
        pool = self._rotation_pool()
        if not pool:
            return None
        now = time.time()
        if self._rot_img is None or now - self._rot_start >= self.rotation_duration:
            self._rot_idx = (self._rot_idx + 1) % len(pool)
            self._rot_start = now
            self._rot_img = pool[self._rot_idx]["img"]
        return self._rot_img

    # ═════════════════════════════════════════════════════════════════════════
    # Service icons (LaMetric, shared cache)
    # ═════════════════════════════════════════════════════════════════════════

    def _icon(self, service: str, network: bool = True) -> Optional[List[Image.Image]]:
        """RGBA frames at 32x32 (first frame used for static drawing).

        The render path passes network=False: it only uses what is already on disk
        or in memory, and a missing icon is fetched in the background instead of
        stalling the display loop."""
        if service in self._icon_frames:
            return self._icon_frames[service]
        frames = self._load_icon_override(service) or self._load_lametric(service, network)
        if frames or network:
            self._icon_frames[service] = frames
        elif service not in self._icon_loading:
            self._icon_loading.add(service)

            def warm():
                try:
                    self._icon(service, network=True)
                finally:
                    self._icon_loading.discard(service)
            threading.Thread(target=warm, daemon=True).start()
        return frames

    def _invalidate_service(self, service: str) -> None:
        self._icon_frames.pop(service, None)
        self._gif_cache.pop(service, None)
        for k in [k for k in self._tile_cache if k[0] == "badge" and k[1] == service]:
            del self._tile_cache[k]

    def _load_icon_override(self, service: str) -> Optional[List[Image.Image]]:
        for ext in ("png", "gif", "jpg"):
            p = self.icons_dir / f"{service}.{ext}"
            if p.exists():
                fr = self._frames_from_file(str(p))
                if fr:
                    return fr
        return None

    @staticmethod
    def _frames_from_file(path: str) -> Optional[List[Image.Image]]:
        try:
            im = Image.open(path)
            frames = [f.convert("RGBA").resize((32, 32), Image.NEAREST)
                      for f in ImageSequence.Iterator(im)]
            return frames or None
        except Exception:
            return None

    def _load_lametric(self, service: str, network: bool = True) -> Optional[List[Image.Image]]:
        icon_id = LAMETRIC_ICON_IDS.get(service)
        if not icon_id:
            return None
        try:
            os.makedirs(LAMETRIC_CACHE_DIR, exist_ok=True)
        except OSError:
            pass
        for ext in (".gif", ".png"):
            p = os.path.join(LAMETRIC_CACHE_DIR, f"{icon_id}{ext}")
            if os.path.exists(p):
                fr = self._frames_from_file(p)
                if fr:
                    return fr
        if not network:
            return None
        for ext in (".gif", ".png"):
            raw = self._get_bytes(f"https://developer.lametric.com/content/apps/icon_thumbs/{icon_id}{ext}",
                                  {"User-Agent": "ledmatrix-media-marquee"}, timeout=6)
            if not raw:
                continue
            p = os.path.join(LAMETRIC_CACHE_DIR, f"{icon_id}{ext}")
            try:
                with open(p, "wb") as f:
                    f.write(raw)
            except OSError:
                continue
            fr = self._frames_from_file(p)
            if fr:
                return fr
        return None

    # ═════════════════════════════════════════════════════════════════════════
    # Rendering  (arcade style)
    # ═════════════════════════════════════════════════════════════════════════

    def _art_tile(self, m: Media, res: Optional[Resolved], t: float) -> Tuple[Image.Image, bool]:
        """Left tile. Returns (tile image, is_service_icon)."""
        H = self.H
        if res and res.art is not None:
            ck = ("art", res.key, H)
            tile = self._tile_cache.get(ck)
            if tile is None:
                inner = H - 2
                art = res.art.copy()
                art.thumbnail((inner, inner), Image.LANCZOS)         # fit, keep ratio
                tile = Image.new("RGB", (art.width + 2, H), m.color)  # 1px bezel in service color
                tile.paste(Image.new("RGB", (art.width, inner), BG), (1, 1))
                tile.paste(art, (1, 1 + (inner - art.height) // 2))
                for stale in [k for k in self._tile_cache if k[0] == "art"]:
                    del self._tile_cache[stale]
                self._tile_cache[ck] = tile
            return tile, False

        frames = self._icon(m.service, network=False)
        if frames:
            frame = frames[0] if len(frames) == 1 else frames[int(t * 8) % len(frames)]
            frame = frame.resize((H, H), Image.NEAREST)
            tile = Image.new("RGB", (H, H), BG)
            ad.vgrad(tile, 0, 0, H, H, ad.scale(m.color, 0.38), BG)
            tile.paste(frame, (0, 0), frame)
            return tile, True

        # no art, no icon: bezel + service initials
        tile = Image.new("RGB", (H, H), m.color)
        tile.paste(Image.new("RGB", (H - 2, H - 2), BG), (1, 1))
        ad.vgrad(tile, 1, 1, H - 2, H - 2, ad.scale(m.color, 0.38), BG)
        ad.big_number(tile, self._txt(m.label)[:2], H // 2, (H - 20) // 2, m.color, 4, "center")
        return tile, True

    def _badge(self, service: str, size: int) -> Optional[Image.Image]:
        ck = ("badge", service, size)
        if ck in self._tile_cache:
            return self._tile_cache[ck]
        frames = self._icon(service, network=False)
        if not frames:
            return None
        base = Image.new("RGB", (32, 32), BG)
        base.paste(frames[0], (0, 0), frames[0])
        badge = base.resize((size, size), Image.LANCZOS)
        self._tile_cache[ck] = badge
        return badge

    def _scroll_row(self, frame: Image.Image, text: str, font, color: Color,
                    x: int, y: int, w: int, h: int, t: float) -> None:
        """Left-aligned text, marquee-scrolls with a pause when wider than the box."""
        tw = self._text_w(text, font)
        if tw <= w:
            ImageDraw.Draw(frame).text((x, y), text, font=font, fill=color)
            return
        gap = 24
        loop = tw + gap
        hold = 1.5
        off = 0 if t < hold else int((t - hold) * self.scroll_speed) % loop
        row = Image.new("RGB", (w, h), BG)
        rd = ImageDraw.Draw(row)
        rd.text((-off, 0), text, font=font, fill=color)
        rd.text((-off + loop, 0), text, font=font, fill=color)
        frame.paste(row, (x, y))

    def _draw_bar(self, d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int,
                  frac: Optional[float], color: Color, t: float, playing: bool) -> None:
        """Chunky arcade segments. frac None -> chase animation (no duration known)."""
        seg, gap = 3, 1
        n = max(1, (w + gap) // (seg + gap))
        if frac is None:
            head = int(t * 14) % (n + 6)
            for i in range(n):
                dist = head - i
                if 0 <= dist < 4:
                    k = 1.0 - dist * 0.25
                    c = tuple(int(v * k) for v in color)
                else:
                    c = EMPTY_SEG
                d.rectangle([x + i * (seg + gap), y, x + i * (seg + gap) + seg - 1, y + h - 1], fill=c)
            return
        filled = int(frac * n)
        for i in range(n):
            sx = x + i * (seg + gap)
            if i < filled:
                c = color
            elif i == filled and frac < 1.0 and (not playing or int(t * 2) % 2 == 0):
                c = color                              # leading segment blinks
            else:
                c = EMPTY_SEG
            d.rectangle([sx, y, sx + seg - 1, y + h - 1], fill=c)

    def _draw_state_glyph(self, d: ImageDraw.ImageDraw, x: int, y: int, color: Color,
                          playing: bool, t: float) -> None:
        if playing:
            d.polygon([(x, y), (x, y + 6), (x + 5, y + 3)], fill=color)
        elif int(t * 2) % 2 == 0:
            d.rectangle([x, y, x + 1, y + 6], fill=color)
            d.rectangle([x + 4, y, x + 5, y + 6], fill=color)

    def _info_parts(self, m: Media, now: float) -> Tuple[str, str, str]:
        """(title, subtitle, time text) for the info card and the burst."""
        title, sub, label = self._txt(m.title), self._txt(m.subtitle), self._txt(m.label)
        if not title:                                     # e.g. Netflix reports no title
            title, sub = label, "STREAMING"
        elif not sub:
            sub = label
        pos = m.position_now(now)
        if m.has_timing():
            time_txt = "-" + fmt_time(m.duration - pos)
        elif m.has_pos:
            time_txt = fmt_time(pos)
        else:
            time_txt = "PLAYING" if m.is_playing else "PAUSED"
        return title, sub, time_txt

    def render_info(self, m: Media, res: Optional[Resolved], now: float, t: float) -> Image.Image:
        tile, _ = self._art_tile(m, res, now)
        title, sub, time_txt = self._info_parts(m, now)
        pos = m.position_now(now)
        frac = (pos / m.duration) if m.has_timing() else None

        def bar(img: Image.Image, x: int, y: int, w: int, h: int) -> None:
            self._draw_bar(ImageDraw.Draw(img), x, y, w, h, frac, m.color, now, m.is_playing)

        return screens.info(self.W, self.H, tile, m.color, self._txt(m.label), title, sub,
                            time_txt, m.is_playing, now, t, self.scroll_speed, bar)

    def render_burst(self, m: Media, res: Optional[Resolved], now: float, t: float) -> Image.Image:
        tile, _ = self._art_tile(m, res, now)
        title, _, _ = self._info_parts(m, now)
        return screens.burst(self.W, self.H, tile, m.color, self._txt(m.label), title, t, self.scroll_speed)

    def render_banner(self, m: Media, banner: Image.Image, now: float) -> Image.Image:
        frame = banner.copy()
        if self.banner_progress and m.has_timing():
            d = ImageDraw.Draw(frame)
            d.rectangle([0, self.H - 2, self.W - 1, self.H - 1], fill=BG)
            filled = int(self.W * m.position_now(now) / m.duration)
            if filled > 0:
                d.rectangle([0, self.H - 2, filled - 1, self.H - 1], fill=m.color)
        return frame

    def _gif(self, service: str) -> Optional[Tuple[List[Image.Image], List[int]]]:
        """Full-screen GIF for a service (gifs/<service>.gif), scaled to W x H."""
        if service in self._gif_cache:
            return self._gif_cache[service]
        result = None
        path = self.gifs_dir / f"{service}.gif"
        if path.exists():
            try:
                frames, durs = [], []
                with Image.open(str(path)) as im:
                    for fr in ImageSequence.Iterator(im):
                        frames.append(self._cover(fr.convert("RGB")))
                        durs.append(max(20, int(fr.info.get("duration", 100))))
                        if len(frames) >= 200:                       # memory guard
                            break
                result = (frames, durs) if frames else None
            except Exception as e:
                self.logger.error("GIF %s unreadable: %s", service, e)
        self._gif_cache[service] = result
        return result

    def render_gif(self, m: Media, gif: Tuple[List[Image.Image], List[int]],
                   now: float, t: float) -> Image.Image:
        """GIF full screen, darkened strip along the bottom with title + progress."""
        frames, durs = gif
        total = sum(durs) or 1
        pos_ms, acc, idx = int((now * 1000) % total), 0, len(frames) - 1
        for i, dur in enumerate(durs):
            acc += dur
            if pos_ms < acc:
                idx = i
                break
        frame = frames[idx].copy()
        W, H = self.W, self.H

        strip_y = H - 13
        strip = frame.crop((0, strip_y, W, H)).point(lambda v: v // 3)
        frame.paste(strip, (0, strip_y))
        title = self._txt(m.title or m.label)
        self._scroll_row(frame, title, self._font(8), WHITE, 1, H - 12, W - 2, 8, t)

        d = ImageDraw.Draw(frame)
        frac = (m.position_now(now) / m.duration) if m.has_timing() else None
        self._draw_bar(d, 0, H - 2, W, 2, frac, m.color, now, m.is_playing)
        return frame

    def render_idle(self, now: float) -> Image.Image:
        return screens.idle(self.W, self.H, now)

    # ═════════════════════════════════════════════════════════════════════════
    # BasePlugin
    # ═════════════════════════════════════════════════════════════════════════

    def update(self) -> None:
        self._maybe_poll()
        self._maybe_refresh_auto()
        self._ensure_curated_imgs()

    def has_live_content(self) -> bool:
        m = self._active()
        return bool(m and m.is_playing)

    def compose(self, now: Optional[float] = None) -> Image.Image:
        """Build the next frame (separated from display() so it can be tested)."""
        now = time.time() if now is None else now
        m = self._active()
        if not self._burst_init:                      # whatever is playing at startup is not "new"
            self._burst_init = True
            self._burst_key = m.key if m else None
        elif m is not None and m.key != self._burst_key:
            self._burst_key = m.key
            if self.now_burst:
                self._burst_t0 = now
        if m is None:
            img = self._rotation_image()
            return img if img is not None else self.render_idle(now)

        with self._lock:
            res = self._resolved if (self._resolved and self._resolved.key == m.key) else None
        banner = res.banner if res else None
        t = now - self._phase_t0

        if self._burst_t0 and now - self._burst_t0 < self.BURST_SECONDS:
            return self.render_burst(m, res, now, now - self._burst_t0)

        show_banner = False
        if banner is not None:
            if self.info_seconds <= 0:
                show_banner = True
            else:
                show_banner = (t % (self.banner_seconds + self.info_seconds)) < self.banner_seconds
        if show_banner:
            return self.render_banner(m, banner, now)
        # info text scrolls from the start of each info phase
        cycle = self.banner_seconds + self.info_seconds
        info_t = (t % cycle) - self.banner_seconds if banner is not None and self.info_seconds > 0 else t
        info_t = max(0.0, info_t)
        # an uploaded GIF replaces the info card when there is no real art to show
        if res is None or res.art is None:
            gif = self._gif(m.service)
            if gif:
                return self.render_gif(m, gif, now, info_t)
        return self.render_info(m, res, now, info_t)

    def display(self, force_clear: bool = False) -> None:
        try:
            self._maybe_poll()
            frame = self.compose()
            dm = self.display_manager
            dm.image.paste(frame, (0, 0))
            dm.update_display()
            m = self._active()
            if m is None or (time.time() - self._visit_start) >= self.get_display_duration():
                self._cycle_done = True
        except Exception as e:
            import traceback
            self.logger.error("display() error: %s\n%s", e, traceback.format_exc())

    def is_cycle_complete(self) -> bool:
        return self._cycle_done

    def reset_cycle_state(self) -> None:
        self._cycle_done = False
        self._visit_start = time.time()
        self._phase_t0 = self._visit_start

    def get_display_duration(self) -> float:
        m = self._active()
        if m is not None:
            with self._lock:
                res = self._resolved
            has_banner = bool(res and res.key == m.key and res.banner is not None)
            return self.banner_seconds + self.info_seconds if has_banner and self.info_seconds > 0 \
                else float(self.config.get("display_duration", 12))
        return self.rotation_duration

    def validate_config(self) -> bool:
        if not super().validate_config():
            return False
        if not self.entities:
            self.logger.warning("no media_player entities configured -- only idle banners will show")
        if self.entities and not (self.ha_url and self.ha_token):
            self.logger.error("ha_url and ha_token are required when entities are set")
            return False
        return True

    # ═════════════════════════════════════════════════════════════════════════
    # Web portal  (live view / banner picker / icons + GIFs)
    # ═════════════════════════════════════════════════════════════════════════

    def _service_keys(self) -> List[str]:
        extra = sorted(set(self.entity_services.values()) - set(SERVICE_LABELS))
        return list(SERVICE_LABELS) + extra + ["generic"]

    @staticmethod
    def _service_label(key: str) -> str:
        if key == "generic":
            return "Generic (fallback)"
        return SERVICE_LABELS.get(key, key.replace("_", " ").title())

    @staticmethod
    def _allowed_image_url(url: str) -> bool:
        try:
            u = urllib.parse.urlparse(url)
        except Exception:
            return False
        return u.scheme == "https" and (u.hostname or "") in IMAGE_HOSTS

    def _plex_search(self, query: str) -> List[Dict[str, str]]:
        root = self._plex_xml(f"/search?query={urllib.parse.quote(query)}")
        out: List[Dict[str, str]] = []
        for item in (root if root is not None else []):
            t = item.get("type", "")
            if t in ("movie", "show"):
                out.append({"title": item.get("title", ""), "year": str(item.get("year", "") or ""),
                            "type": "movie" if t == "movie" else "tv"})
        return out[:20]

    def _status(self) -> Dict[str, Any]:
        """Everything the live view shows. Never includes tokens or art URLs."""
        now = time.time()
        m = self._active()
        media = None
        if m:
            with self._lock:
                res = self._resolved if (self._resolved and self._resolved.key == m.key) else None
            media = {
                "entity": m.entity_id, "state": m.state, "title": m.title, "subtitle": m.subtitle,
                "service": m.service, "label": m.label, "app_name": m.app_name,
                "position": int(m.position_now(now)) if (m.has_pos or m.has_timing()) else None,
                "duration": int(m.duration) if m.duration else None,
                "uses_tmdb": m.use_tmdb, "art": res.art_src if res else "",
                "banner": bool(res and res.banner is not None), "resolved": bool(res and res.done),
                "gif": bool(self._gif(m.service)),
            }
        return {
            "media": media,
            "entities": [{"entity_id": e, **self._entity_status.get(e, {"state": "not polled yet", "app": ""})}
                         for e in self.entities],
            "idle": {"curated": len(self._enabled_library()), "auto": len(self._auto),
                     "showing": len(self._rotation_pool())},
            "display": {"width": self.W, "height": self.H,
                        "banner_seconds": self.banner_seconds, "info_seconds": self.info_seconds},
        }

    def _build_app(self):
        app = Flask(f"media_marquee_{self.plugin_id}")
        app.config["MAX_CONTENT_LENGTH"] = self.max_upload_mb * 1024 * 1024
        keys = self._service_keys()

        def err(msg: str, code: int = 400):
            return jsonify({"error": msg}), code

        def png_response(img: Image.Image):
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return Response(buf.getvalue(), mimetype="image/png",
                            headers={"Cache-Control": "no-store"})

        # ── page + font ──────────────────────────────────────────────────────
        @app.get("/")
        def index():
            page = self.plugin_dir / "static" / "portal.html"
            return Response(page.read_text() if page.exists() else "<h1>Media Marquee</h1><p>static/portal.html missing</p>",
                            mimetype="text/html")

        @app.get("/font.ttf")
        def font_file():
            for p in (self._font_path,
                      str(self.plugin_dir.parent.parent / "assets" / "fonts" / "PressStart2P-Regular.ttf"),
                      "/home/pi/LEDMatrix/assets/fonts/PressStart2P-Regular.ttf"):
                if p and os.path.exists(p):
                    return Response(open(p, "rb").read(), mimetype="font/ttf",
                                    headers={"Cache-Control": "max-age=86400"})
            return err("font not found", 404)

        # ── live view ────────────────────────────────────────────────────────
        @app.get("/api/status")
        def status():
            return jsonify(self._status())

        @app.get("/api/frame.png")
        def frame():
            try:
                scale = max(1, min(12, int(request.args.get("scale", 6))))
                img = self.compose().resize((self.W * scale, self.H * scale), Image.NEAREST)
            except Exception as e:
                return err(f"render failed: {e}", 500)
            return png_response(img)

        # ── banner picker ────────────────────────────────────────────────────
        @app.get("/api/search")
        def search():
            q = request.args.get("q", "").strip()
            return jsonify(self._plex_search(q) if q else [])

        @app.get("/api/banners")
        def banners():
            title = request.args.get("title", "").strip()
            year = request.args.get("year", "").strip()
            mtype = "movie" if request.args.get("type", "movie") == "movie" else "tv"
            if not title:
                return err("title required")
            if not self.tmdb_token or not self.fanart_api_key:
                return err("tmdb_token and fanart_api_key must both be set in the plugin config "
                           "(tmdb_token is the long API Read Access Token starting with eyJ)", 503)
            hit = self._tmdb_search(title, year, mtype)
            if not hit:
                return err(f"No TMDB match for '{title}'. If every title fails, check tmdb_token "
                           "(it must be the API Read Access Token starting with eyJ, not the v3 key).", 404)
            return jsonify({"tmdb_id": hit["id"], "title": title, "media_type": mtype,
                            "banners": self._fanart_all(hit["id"], mtype)})

        @app.get("/api/preview")
        def preview():
            url = request.args.get("url", "")
            if not self._allowed_image_url(url):
                return err("url not allowed", 400)
            data = self._get_bytes(url, timeout=10)
            if not data:
                return err("fetch failed", 502)
            try:
                mime = Image.MIME.get(Image.open(io.BytesIO(data)).format or "", "image/jpeg")
            except Exception:
                return err("not an image", 502)
            return Response(data, mimetype=mime, headers={"Cache-Control": "max-age=3600"})

        # ── curated library ──────────────────────────────────────────────────
        @app.get("/api/library")
        def lib_get():
            with self._lib_lock:
                return jsonify(self._library)

        @app.post("/api/library")
        def lib_add():
            d = request.get_json(silent=True) or {}
            url, title = str(d.get("url", "")), str(d.get("title", "")).strip()[:120]
            if not title or not self._allowed_image_url(url):
                return err("title and an allowed image url are required")
            try:
                tmdb_id = int(d.get("tmdb_id", 0) or 0)
            except (TypeError, ValueError):
                return err("bad tmdb_id")
            entry = {"id": f"{tmdb_id}_{int(time.time() * 1000)}", "title": title, "url": url,
                     "tmdb_id": tmdb_id, "media_type": "movie" if d.get("media_type") == "movie" else "tv",
                     "enabled": True, "added": time.time()}
            with self._lib_lock:
                self._library.append(entry)
                self._save_library()
            self._ensure_curated_imgs()
            return jsonify(entry)

        @app.post("/api/library/<entry_id>/toggle")
        def lib_toggle(entry_id):
            with self._lib_lock:
                for e in self._library:
                    if e["id"] == entry_id:
                        e["enabled"] = not e.get("enabled", True)
                        self._save_library()
                        out = dict(e)
                        break
                else:
                    return err("not found", 404)
            self._ensure_curated_imgs()
            return jsonify(out)

        @app.delete("/api/library/<entry_id>")
        def lib_delete(entry_id):
            with self._lib_lock:
                self._library = [e for e in self._library if e["id"] != entry_id]
                self._save_library()
            self._curated_imgs.pop(entry_id, None)
            return jsonify({"status": "deleted"})

        @app.post("/api/reorder")
        def lib_reorder():
            order = (request.get_json(silent=True) or {}).get("order", [])
            if not isinstance(order, list):
                return err("order must be a list")
            with self._lib_lock:
                by_id = {e["id"]: e for e in self._library}
                self._library = [by_id[i] for i in order if i in by_id] + \
                                [e for e in self._library if e["id"] not in order]
                self._save_library()
            return jsonify({"status": "ok"})

        # ── service icons + GIFs ─────────────────────────────────────────────
        def override_icon(svc: str) -> Optional[Path]:
            for ext in ("png", "gif", "jpg"):
                p = self.icons_dir / f"{svc}.{ext}"
                if p.exists():
                    return p
            return None

        @app.get("/api/services")
        def services():
            return jsonify([{
                "key": k, "label": self._service_label(k),
                "color": "#%02x%02x%02x" % SERVICE_COLORS.get(k, DEFAULT_COLOR),
                "lametric_id": LAMETRIC_ICON_IDS.get(k),
                "icon_override": override_icon(k) is not None,
                "gif": (self.gifs_dir / f"{k}.gif").exists(),
            } for k in keys])

        @app.get("/api/icon_preview/<svc>")
        def icon_preview(svc):
            if svc not in keys:
                return err("unknown service", 404)
            frames = self._icon(svc)
            if not frames:
                return err("no icon", 404)
            tile = Image.new("RGB", (32, 32), BG)
            tile.paste(frames[0], (0, 0), frames[0])
            return png_response(tile.resize((128, 128), Image.NEAREST))

        def read_upload(allowed: Dict[str, str]):
            f = request.files.get("file")
            if f is None:
                return None, err("no file")
            data = f.read()
            try:
                im = Image.open(io.BytesIO(data))
                fmt = (im.format or "").upper()
                im.verify()
            except Exception:
                return None, err("not a valid image")
            if fmt not in allowed:
                return None, err("allowed formats: " + ", ".join(sorted(allowed)))
            return (data, allowed[fmt]), None

        @app.post("/api/upload_icon/<svc>")
        def upload_icon(svc):
            if svc not in keys:
                return err("unknown service", 404)
            got, bad = read_upload({"PNG": "png", "GIF": "gif", "JPEG": "jpg"})
            if bad:
                return bad
            data, ext = got
            self.icons_dir.mkdir(exist_ok=True)
            for e in ("png", "gif", "jpg"):
                (self.icons_dir / f"{svc}.{e}").unlink(missing_ok=True)
            (self.icons_dir / f"{svc}.{ext}").write_bytes(data)       # fixed name: client filename unused
            self._invalidate_service(svc)
            return jsonify({"status": "ok"})

        @app.delete("/api/icon/<svc>")
        def delete_icon(svc):
            if svc not in keys:
                return err("unknown service", 404)
            for e in ("png", "gif", "jpg"):
                (self.icons_dir / f"{svc}.{e}").unlink(missing_ok=True)
            self._invalidate_service(svc)
            return jsonify({"status": "deleted"})

        @app.get("/api/gif/<svc>")
        def gif_get(svc):
            p = self.gifs_dir / f"{svc}.gif"
            if svc not in keys or not p.exists():
                return err("no gif", 404)
            return Response(p.read_bytes(), mimetype="image/gif", headers={"Cache-Control": "no-store"})

        @app.post("/api/upload_gif/<svc>")
        def upload_gif(svc):
            if svc not in keys:
                return err("unknown service", 404)
            got, bad = read_upload({"GIF": "gif"})
            if bad:
                return bad
            self.gifs_dir.mkdir(exist_ok=True)
            (self.gifs_dir / f"{svc}.gif").write_bytes(got[0])
            self._invalidate_service(svc)
            return jsonify({"status": "ok"})

        @app.delete("/api/gif/<svc>")
        def delete_gif(svc):
            if svc not in keys:
                return err("unknown service", 404)
            (self.gifs_dir / f"{svc}.gif").unlink(missing_ok=True)
            self._invalidate_service(svc)
            return jsonify({"status": "deleted"})

        @app.errorhandler(413)
        def too_big(_):
            return err(f"file too large (max {self.max_upload_mb} MB)", 413)

        return app

    def _start_portal(self) -> None:
        app = self._build_app()

        def run():
            import logging as _l
            _l.getLogger("werkzeug").setLevel(_l.ERROR)
            try:
                app.run(host="0.0.0.0", port=self.portal_port, debug=False, use_reloader=False)
            except OSError as e:
                self.logger.error("Portal could not bind port %d (%s). Is another plugin (plex-marquee "
                                  "uses 5009, now-playing 5008) already on it? Set portal_port.",
                                  self.portal_port, e)

        threading.Thread(target=run, daemon=True, name="media-marquee-portal").start()
        self.logger.info("Media Marquee portal on port %d", self.portal_port)
