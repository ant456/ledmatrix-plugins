"""
News Arcade for LEDMatrix  (arcade style, same family as NBA / NFL / NHL Live).

Your RSS headlines as a continuous ticker: every story runs right to left on one
long strip, round-robin across feeds, so nothing is cut off and several stories
are always on the way. The logo tile on the left follows the story at the edge.
(layout "cards" keeps the old one-story-at-a-time screens.) A new top story from a
feed interrupts with a BREAKING burst that stays up long enough to read it.
A feed that fails keeps showing its last headlines.

update() does every network call; display() only draws from memory.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image

from src.plugin_system.base_plugin import BasePlugin

import newsarc_feeds as feeds
import newsarc_render as render

BURST_SECONDS = 5.0
MAX_BURST_SECONDS = 14.0
PALETTE = [(246, 145, 4), (60, 160, 255), (70, 220, 120), (235, 70, 90), (180, 100, 240), (255, 214, 60)]


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _color(value: Any, default: Tuple[int, int, int]) -> Tuple[int, int, int]:
    try:
        r, g, b = (max(0, min(255, int(v))) for v in value)
        return (r, g, b)
    except (TypeError, ValueError):
        return default


class NewsArcadePlugin(BasePlugin):
    """Arcade RSS headlines."""

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.W: int = display_manager.width
        self.H: int = display_manager.height
        self._load_config(config)

        here = Path(__file__).resolve().parent
        self.root = here.parent.parent                         # the LEDMatrix folder
        render.load_faces(str(config.get("title_font", "7x13B")), "9x15B",
                          (str(self.root / "assets" / "fonts"), "/home/pi/LEDMatrix/assets/fonts"))

        self.session = requests.Session()
        self._lock = threading.Lock()
        self._by_feed: Dict[str, List[Dict[str, Any]]] = {}    # last good headlines per feed
        self._seen: Dict[str, set] = {}                        # keys already seen per feed
        self._errors: Dict[str, str] = {}
        self.queue: List[Dict[str, Any]] = []
        self._logos: Dict[str, Optional[Image.Image]] = {}
        self._tiles: Dict[Tuple[str, bool], Image.Image] = {}

        self._pos = 0
        self._item_t0 = 0.0
        self._strip: Optional[render.Strip] = None
        self._strip_key: Any = None
        self._scroll = 0.0                                     # ticker position in pixels, kept across turns
        self._scroll_t: Optional[float] = None
        self._alert: Optional[Dict[str, Any]] = None
        self._last_update = 0.0
        self._worker: Optional[threading.Thread] = None
        self.enable_scrolling = True                           # high-FPS path for the crawl and burst
        self.logger.info("News Arcade ready  feeds=%d  W=%d H=%d", len(self.feeds), self.W, self.H)

    # ── config ───────────────────────────────────────────────────────────────

    def _load_config(self, c: Dict[str, Any]) -> None:
        raw = c.get("feeds")
        out = []
        for i, f in enumerate(raw if isinstance(raw, list) and raw else feeds.DEFAULT_FEEDS):
            if not isinstance(f, dict) or not f.get("url") or f.get("enabled", True) is False:
                continue
            out.append({"name": str(f.get("name") or "NEWS").strip(), "url": str(f["url"]).strip(),
                        "color": _color(f.get("color"), PALETTE[i % len(PALETTE)]),
                        "logo": str(f.get("logo") or "").strip()})
        self.feeds = out
        self.per_feed = _int(c.get("headlines_per_feed"), 5, 1, 20)
        self.min_seconds = _int(c.get("min_headline_seconds"), 6, 3, 60)
        self.max_seconds = max(self.min_seconds, _int(c.get("max_headline_seconds"), 20, 5, 120))
        self.speed = float(_int(c.get("crawl_speed"), 45, 10, 120))
        self.max_age = _int(c.get("max_age_hours"), 48, 1, 24 * 14) * 3600
        self.show_logos = bool(c.get("show_logos", True))
        self.breaking = bool(c.get("breaking_alerts", True))
        self.timeout = float(_int(c.get("request_timeout"), 8, 3, 30))
        self.display_duration = float(_int(c.get("display_duration"), 45, 10, 300))
        self.view_mode = "cards" if str(c.get("layout", "ticker")).lower() == "cards" else "ticker"

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        super().on_config_change(new_config)
        self.config = new_config
        self._load_config(new_config)
        self._tiles.clear()
        self._logos.clear()
        self._last_update = 0.0
        self._rebuild(time.time())

    # ── update: the only network code ────────────────────────────────────────

    def update(self) -> None:
        """Start a refresh and return at once: feeds can take seconds and the panel must not wait."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._refresh, daemon=True, name="news-arcade-fetch")
        self._worker.start()

    def _refresh(self) -> None:
        try:
            self._fetch_all()
        except Exception as exc:  # noqa: BLE001 - never lose the thread silently
            self.logger.error("News refresh failed: %s", exc, exc_info=True)

    def _fetch_all(self) -> None:
        now = time.time()
        for f in list(self.feeds):
            name = f["name"]
            try:
                items = feeds.fetch(self.session, f["url"], name, self.per_feed, self.timeout)
            except Exception as exc:  # noqa: BLE001 - a feed failure keeps its last headlines
                self._errors[name] = "%s: %s" % (type(exc).__name__, exc)
                self.logger.warning("Feed %s failed (%s); keeping last headlines", name, self._errors[name])
                continue
            self._errors.pop(name, None)
            keys = [i["link"] or i["title"] for i in items]
            with self._lock:
                known = self._seen.get(name)
                if known is not None and self.breaking:
                    fresh = [i for i, k in zip(items, keys) if k not in known]
                    if fresh and self._alert is None:
                        # only the newest story interrupts; the rest just join the rotation
                        self._alert = {"item": fresh[0], "color": f["color"], "logo": f["logo"],
                                       "t0": 0.0, "pending": True}
                self._seen[name] = set(keys) | (known or set())
                if items:
                    self._by_feed[name] = items
        self._last_update = now
        self._rebuild(now)

    def get_update_interval(self) -> Optional[float]:
        return float(_int(self.config.get("update_interval"), 300, 60, 3600))

    def _rebuild(self, now: float) -> None:
        """Round-robin across feeds, dropping stories older than max_age."""
        with self._lock:
            lanes = []
            for f in self.feeds:
                lane = [dict(i, color=f["color"], logo=f["logo"]) for i in self._by_feed.get(f["name"], [])
                        if not i["ts"] or now - i["ts"] <= self.max_age]
                if lane:
                    lanes.append(lane)
            queue = []
            for rank in range(self.per_feed):
                for lane in lanes:
                    if rank < len(lane):
                        queue.append(lane[rank])
            self.queue = queue
            if self._pos >= len(queue):
                self._pos = 0
        self._build_strip()

    def _view_w(self) -> int:
        return self.W - (render.TILE_W + 2 if self.show_logos else 0)

    def _build_strip(self) -> None:
        """Lay the queue out as one strip (only when it changed; runs on the update thread)."""
        with self._lock:
            queue = list(self.queue)
        key = (tuple((q["title"], q["feed"]) for q in queue), self._view_w(), id(render.TITLE))
        if key == self._strip_key:
            return
        strip = render.Strip(queue, self._view_w())
        with self._lock:
            old = self._strip
            if old is not None and old.total and strip.total and self._scroll_t is not None:
                # keep the story now at the left edge there, not the same pixel offset
                title = old.items[old.index_at(self._scroll)]["title"]
                at = next((i for i, it in enumerate(strip.items) if it["title"] == title), None)
                if at is not None:
                    self._scroll = float(strip.spans[at][0])
            self._strip, self._strip_key = strip, key
            if not strip.total:
                self._scroll = 0.0

    # ── helpers ──────────────────────────────────────────────────────────────

    def _logo(self, path: str) -> Optional[Image.Image]:
        if not path:
            return None
        if path not in self._logos:
            logo = None
            for cand in (Path(path), self.root / path.lstrip("/\\")):
                if cand.is_file():
                    try:
                        with Image.open(cand) as src:
                            logo = src.convert("RGBA")
                        break
                    except Exception as exc:  # noqa: BLE001
                        self.logger.debug("Logo %s unreadable: %s", cand, exc)
            self._logos[path] = logo
        return self._logos[path]

    def _tile(self, item: Dict[str, Any]) -> Optional[Image.Image]:
        if not self.show_logos:
            return None
        key = (item["feed"], bool(item["logo"]))
        if key not in self._tiles:
            self._tiles[key] = render.tile(item["color"], item["feed"], self._logo(item["logo"]))
        return self._tiles[key]

    def _seconds(self, item: Dict[str, Any]) -> float:
        pw = self.W - (render.TILE_W + 2 if self.show_logos else 0) - 7
        need = render.crawl_seconds(item["title"], pw, self.speed)
        return max(float(self.min_seconds), min(float(self.max_seconds), need + 1.0))

    # ── drawing ──────────────────────────────────────────────────────────────

    def _ticker(self, now: float) -> Image.Image:
        with self._lock:
            strip = self._strip
            if strip is None or not strip.total:
                return self._fit(render.empty(now, "NO FEEDS" if not self.feeds else "CHECK FEEDS"))
            last, self._scroll_t = self._scroll_t, now
            if last is not None and 0 < now - last <= 0.5:     # a long gap (other plugins had the panel) just resumes
                self._scroll = (self._scroll + (now - last) * self.speed) % strip.total
            x = self._scroll
        frame = Image.new("RGB", (self.W, self.H), (0, 0, 0))
        x0 = 0
        if self.show_logos:
            tile = self._tile(strip.items[strip.index_at(x)])
            if tile is not None:
                frame.paste(tile, (0, 0))
                x0 = tile.width + 2
        frame.paste(strip.window(x), (x0, 0))
        return self._fit(frame)

    def _jump_to(self, title: str) -> None:
        """Bring the story with this title to the left edge of the ticker."""
        with self._lock:
            strip = self._strip
            if strip is not None:
                for i, it in enumerate(strip.items):
                    if it["title"] == title:
                        self._scroll = float(strip.spans[i][0])
                        break

    def compose(self, now: Optional[float] = None) -> Image.Image:
        now = time.time() if now is None else now
        with self._lock:
            queue = list(self.queue)
            alert = self._alert

        if alert is not None:
            if alert["pending"]:
                alert["pending"] = False
                alert["t0"] = now
                x0 = render.TILE_W + 2 if self.show_logos else 0
                need = render.crawl_seconds(alert["item"]["title"], self.W - x0 - 4, self.speed)
                alert["dur"] = max(BURST_SECONDS, min(MAX_BURST_SECONDS, need + 1.0))
            if now - alert["t0"] < alert.get("dur", BURST_SECONDS):
                item = dict(alert["item"], logo=alert["logo"], color=alert["color"])
                frame = render.breaking(item, alert["color"], self._tile(item), now - alert["t0"], self.speed)
                return self._fit(frame)
            with self._lock:                                   # burst over: show that story in the rotation
                self._alert = None
            for i, q in enumerate(queue):
                if q["title"] == alert["item"]["title"]:
                    self._pos, self._item_t0 = i, now
                    break
            self._jump_to(alert["item"]["title"])
            self._scroll_t = None

        if self.view_mode == "ticker":
            return self._ticker(now)

        if not queue:
            return self._fit(render.empty(now, "NO FEEDS" if not self.feeds else "CHECK FEEDS"))

        if not self._item_t0:
            self._item_t0 = now
        self._pos %= len(queue)
        item = queue[self._pos]
        dur = self._seconds(item)
        t = now - self._item_t0
        if t >= dur:                                           # next story
            self._pos = (self._pos + 1) % len(queue)
            self._item_t0 = now
            item = queue[self._pos]
            dur, t = self._seconds(item), 0.0
        frame = render.headline(item, item["color"], self._tile(item), t, self.speed)
        return self._fit(frame)

    def _fit(self, frame: Image.Image) -> Image.Image:
        if (self.W, self.H) != frame.size:
            canvas = Image.new("RGB", (self.W, self.H), (0, 0, 0))
            canvas.paste(frame, ((self.W - frame.width) // 2, (self.H - frame.height) // 2))
            frame = canvas
        return frame

    # ── BasePlugin ───────────────────────────────────────────────────────────

    def display(self, force_clear: bool = False) -> None:
        try:
            frame = self.compose()
            dm = self.display_manager
            dm.image.paste(frame, (0, 0))
            dm.update_display()
        except Exception as e:
            import traceback
            self.logger.error("Error rendering news: %s\n%s", e, traceback.format_exc())

    def get_display_duration(self) -> float:
        return self.display_duration

    def validate_config(self) -> bool:
        return bool(self.feeds)

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update({"feeds": [f["name"] for f in self.feeds], "headlines": len(self.queue),
                     "errors": dict(self._errors), "last_update": self._last_update})
        return info

    def cleanup(self) -> None:
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass
        super().cleanup()
