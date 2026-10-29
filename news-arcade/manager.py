"""
News Arcade for LEDMatrix  (arcade style, same family as NBA / NFL / NHL Live).

One headline at a time from your RSS feeds, round-robin so every feed gets turns.
A long headline rests, crawls to its end once, and rests again, so it is always
read in full. A new top story from a feed interrupts the rotation with a
BREAKING burst. A feed that fails keeps showing its last headlines.

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
        self.speed = float(_int(c.get("crawl_speed"), 36, 10, 120))
        self.max_age = _int(c.get("max_age_hours"), 48, 1, 24 * 14) * 3600
        self.show_logos = bool(c.get("show_logos", True))
        self.breaking = bool(c.get("breaking_alerts", True))
        self.timeout = float(_int(c.get("request_timeout"), 8, 3, 30))
        self.display_duration = float(_int(c.get("display_duration"), 45, 10, 300))

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

    def compose(self, now: Optional[float] = None) -> Image.Image:
        now = time.time() if now is None else now
        with self._lock:
            queue = list(self.queue)
            alert = self._alert

        if alert is not None:
            if alert["pending"]:
                alert["pending"] = False
                alert["t0"] = now
            if now - alert["t0"] < BURST_SECONDS:
                item = dict(alert["item"], logo=alert["logo"])
                frame = render.breaking(item, alert["color"], self._tile(item), now - alert["t0"], self.speed)
                return self._fit(frame)
            with self._lock:                                   # burst over: show that story in the rotation
                self._alert = None
            for i, q in enumerate(queue):
                if q["title"] == alert["item"]["title"]:
                    self._pos, self._item_t0 = i, now
                    break

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
