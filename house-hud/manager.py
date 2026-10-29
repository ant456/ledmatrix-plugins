"""
House HUD for LEDMatrix  (arcade style, same family as NBA / NFL / NHL Live).

A game-style status screen for the house, read from Home Assistant: four 32px tiles per
screen (lock, door, smoke, who is home / temperatures, lights, hot tub ...), screens taking
turns. Alarms blink; when something happens (the front door opens or unlocks, you arrive
home) a burst takes over the panel for a few seconds.

Tiles are set in config (`screens`); the defaults match this house. A background thread
polls HA with one template call; display() only draws from memory.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from src.plugin_system.base_plugin import BasePlugin

import hud_data as hd
import hud_render as render

MODE = "house_hud"
CYCLE = 64.0                    # length of the test_mode demo


def clock(ts: float) -> str:
    t = time.localtime(ts)
    return "%d:%02d%s" % (t.tm_hour % 12 or 12, t.tm_min, "A" if t.tm_hour < 12 else "P")


class HouseHudPlugin(BasePlugin):
    """Arcade house status tiles."""

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.W: int = display_manager.width
        self.H: int = display_manager.height
        self._load_config(config)

        here = Path(__file__).resolve().parent
        render.load_faces((str(here.parent.parent / "assets" / "fonts"), "/home/pi/LEDMatrix/assets/fonts"))

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._raw: Optional[Dict[str, Any]] = None
        self._event: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None
        self._last_ok = 0.0
        self._demo_t0 = time.time()
        self.enable_scrolling = True                         # high-FPS path for the blink and burst

        if self.test_mode:
            self._request_on_demand(CYCLE + 2.0)
            self.logger.info("House HUD TEST MODE: made-up events, no polling")
        else:
            threading.Thread(target=self._loop, daemon=True, name="house-hud-poll").start()
        self.logger.info("House HUD ready  screens=%d  W=%d H=%d", len(self.screens), self.W, self.H)

    # ── config ───────────────────────────────────────────────────────────────

    def _load_config(self, c: Dict[str, Any]) -> None:
        self.ha_url = str(c.get("ha_url", "http://homeassistant.local:8123")).rstrip("/")
        self.ha_token = str(c.get("ha_token", ""))
        self.screens: List[Dict[str, Any]] = hd.clean_screens(c.get("screens"))
        self.tiles: Dict[str, Dict[str, Any]] = {
            "s%dt%d" % (si, ti): t for si, s in enumerate(self.screens) for ti, t in enumerate(s["tiles"])}
        self.template = hd.build_template(self.screens)
        self.poll = float(max(2, int(c.get("poll_seconds", 5) or 5)))
        self.page_seconds = float(max(3, int(c.get("page_seconds", 7) or 7)))
        self.display_duration = float(max(5, int(c.get("display_duration", 14) or 14)))
        self.event_seconds = float(max(2, int(c.get("event_seconds", 5) or 5)))
        self.events = bool(c.get("events", True))
        self.interrupt = bool(c.get("interrupt", True))
        self.test_mode = bool(c.get("test_mode", False))

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        super().on_config_change(new_config)
        self.config = new_config
        self._load_config(new_config)
        self._raw = None

    # ── polling (worker thread) ──────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._apply(hd.fetch(self.ha_url, self.ha_token, self.template), time.time())
                self._error = None
            except Exception as exc:  # noqa: BLE001 - keep polling; the last values stay on screen
                self._error = "%s: %s" % (type(exc).__name__, str(exc)[:80])
                self.logger.warning("HA poll failed (%s)", self._error)
            self._stop.wait(self.poll)

    def _request_on_demand(self, seconds: float) -> None:
        """Ask the display controller to show us right now (the way ha-notifications does)."""
        try:
            self.cache_manager.set("display_on_demand_request", {
                "request_id": str(uuid.uuid4()), "action": "start", "plugin_id": self.plugin_id,
                "mode": MODE, "duration": seconds}, ttl=3600)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Failed to request on-demand switch: %s", exc)

    def _apply(self, raw: Dict[str, Any], now: float) -> None:
        notify = False
        with self._lock:
            prev, self._raw, self._last_ok = self._raw, raw, now
            if prev is not None and self.events:
                found = hd.events(self.tiles, prev, raw)
                if found:
                    self._event = dict(found[0], t0=now)
                    notify = True
                    self.logger.info("HUD event: %s %s", found[0]["top"], found[0]["text"])
        if notify and self.interrupt and not self.test_mode:
            self._request_on_demand(self.event_seconds + 4.0)

    # ── test mode ────────────────────────────────────────────────────────────

    def _demo_raw(self, tt: float) -> Dict[str, Any]:
        """A made-up day: the door opens, the front door unlocks and locks, smoke blips, you come home."""
        raw: Dict[str, Any] = {}
        for key, t in self.tiles.items():
            kind = t["type"]
            if kind == "lock":
                raw[key] = "unlocked" if 22 <= tt < 30 else "locked"
            elif kind == "opening":
                raw[key] = "on" if 10 <= tt < 18 else "off"
            elif kind == "safe":
                raw[key] = {"on": 1 if 40 <= tt < 45 else 0, "na": 0, "n": len(t["entities"])}
            elif kind == "presence":
                raw[key] = "not_home" if 34 <= tt < 52 else "home"
            elif kind == "temp":
                raw[key] = 102 if t.get("icon") == "tub" else (58 if t.get("icon") == "sun" else 67 + (tt > 32))
            else:
                raw[key] = 43 if tt < 32 else 38
        return raw

    # ── drawing ──────────────────────────────────────────────────────────────

    def compose(self, now: Optional[float] = None) -> Optional[Image.Image]:
        """The next frame, or None when there is no data yet (separate from display() for tests)."""
        now = time.time() if now is None else now
        if self.test_mode:
            self._apply(self._demo_raw((now - self._demo_t0) % CYCLE), now)
        with self._lock:
            raw, ev = self._raw, self._event
        if raw is None:
            return None
        if not self.test_mode and self._error and now - self._last_ok > 120:
            return self._fit(render.notice(now))
        if ev and now - ev["t0"] < self.event_seconds:
            return self._fit(render.burst(ev, now - ev["t0"], clock(ev["t0"])))
        si = int(now // self.page_seconds) % len(self.screens)
        views = [hd.view(t, raw.get("s%dt%d" % (si, ti))) for ti, t in enumerate(self.screens[si]["tiles"])]
        return self._fit(render.screen(views, now))

    def _fit(self, frame: Image.Image) -> Image.Image:
        if (self.W, self.H) != frame.size:
            canvas = Image.new("RGB", (self.W, self.H), (0, 0, 0))
            canvas.paste(frame, ((self.W - frame.width) // 2, (self.H - frame.height) // 2))
            frame = canvas
        return frame

    # ── BasePlugin ───────────────────────────────────────────────────────────

    def update(self) -> None:
        return                                                # the worker thread polls

    def display(self, force_clear: bool = False, display_mode: Optional[str] = None, **_: Any) -> bool:
        try:
            frame = self.compose()
            if frame is None:
                return False                                  # no data yet: the rotation moves on
            dm = self.display_manager
            dm.image.paste(frame, (0, 0))
            dm.update_display()
            return True
        except Exception as exc:  # noqa: BLE001
            import traceback
            self.logger.error("Error rendering house hud: %s\n%s", exc, traceback.format_exc())
            return False

    def get_display_duration(self) -> float:
        return CYCLE + 2.0 if self.test_mode else self.display_duration

    def validate_config(self) -> bool:
        if not self.ha_token:
            self.logger.error("ha_token is required")
            return False
        return bool(self.screens)

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update({"tiles": len(self.tiles), "last_ok": self._last_ok, "error": self._error})
        return info

    def cleanup(self) -> None:
        self._stop.set()
        super().cleanup()
