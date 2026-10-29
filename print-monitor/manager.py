"""
Print Monitor for LEDMatrix  (arcade style, same family as NBA / NFL / NHL Live).

Watches a Bambu Lab printer through Home Assistant's Bambu Lab integration:
model thumbnail, big percent, time left and ETA, nozzle and bed temperatures,
layer count and a segmented gauge in the filament colour. Preparing, paused and
failed prints get their own screens, and a finished print bursts onto the panel.

While the printer is idle the plugin has nothing to show and returns False from
display(), so the rotation moves on (set show_idle to keep a PRINTER IDLE card).

A background thread polls HA (one template call per poll); display() only draws
from memory.
"""

from __future__ import annotations

import io
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from src.plugin_system.base_plugin import BasePlugin

import printmon_data as pd
import printmon_demo as demo
import printmon_render as render

BURST_SECONDS = 6.0
ACTIVE = (pd.PRINTING, pd.PREPARING, pd.PAUSED)
MODE = "print_monitor"


class PrintMonitorPlugin(BasePlugin):
    """Arcade 3D-print status."""

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.W: int = display_manager.width
        self.H: int = display_manager.height
        self._load_config(config)

        here = Path(__file__).resolve().parent
        render.load_faces((str(here.parent.parent / "assets" / "fonts"), "/home/pi/LEDMatrix/assets/fonts"))

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state: Optional[Dict[str, Any]] = None
        self._phase: Optional[str] = None
        self._burst_t0 = 0.0
        self._done_at = 0.0
        self._failed_at = 0.0
        self._cover: Optional[Image.Image] = None
        self._cover_key: Optional[str] = None
        self._cover_retry_at = 0.0
        self._tiles: Dict[Tuple, Image.Image] = {}
        self._error: Optional[str] = None
        self._last_poll = 0.0
        self.enable_scrolling = True                      # high-FPS path for the gauge and burst

        self._demo_t0 = time.time()
        if self.test_mode:
            # a made-up job plays through every screen; only the model thumbnail comes from the printer
            threading.Thread(target=self._refresh_cover, args=(demo.snapshot(20.0)[0], pd.PRINTING, time.time()),
                             daemon=True, name="print-monitor-cover").start()
            self._request_on_demand(demo.CYCLE + 2.0)
            self.logger.info("Print Monitor TEST MODE: demo job, no printer polling")
        else:
            self._worker = threading.Thread(target=self._loop, daemon=True, name="print-monitor-poll")
            self._worker.start()
        self.logger.info("Print Monitor ready  prefix=%s  W=%d H=%d", self.prefix, self.W, self.H)

    # ── config ───────────────────────────────────────────────────────────────

    def _load_config(self, c: Dict[str, Any]) -> None:
        self.ha_url = str(c.get("ha_url", "http://homeassistant.local:8123")).rstrip("/")
        self.ha_token = str(c.get("ha_token", ""))
        self.prefix = str(c.get("printer_prefix", "p1s_YOURSERIAL")).strip().strip("_")
        self.poll = float(max(2, int(c.get("poll_seconds", 5) or 5)))
        self.idle_poll = float(max(10, int(c.get("idle_poll_seconds", 30) or 30)))
        self.display_duration = float(max(5, int(c.get("display_duration", 15) or 15)))
        self.interrupt = bool(c.get("interrupt", True))
        self.hold = float(max(0, int(c.get("finished_hold_minutes", 30) or 0))) * 60.0
        self.show_idle = bool(c.get("show_idle", False))
        self.live_priority = bool(c.get("live_priority", False))
        self.show_cover = bool(c.get("show_cover", True))
        self.test_mode = bool(c.get("test_mode", False))

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        super().on_config_change(new_config)
        self.config = new_config
        self._load_config(new_config)
        self._tiles.clear()
        self._cover_key = None

    # ── polling (worker thread) ──────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                state = pd.fetch(self.ha_url, self.ha_token, self.prefix)
                self._error = None
                self._apply(state, time.time())
            except Exception as exc:  # noqa: BLE001 - keep polling; the last state stays on screen
                self._error = "%s: %s" % (type(exc).__name__, str(exc)[:80])
                self.logger.warning("Printer poll failed (%s)", self._error)
            self._last_poll = time.time()
            active = self._phase in ACTIVE
            self._stop.wait(self.poll if active else self.idle_poll)

    def _request_on_demand(self, seconds: float = 20.0) -> None:
        """Ask the display controller to show us right now (the way ha-notifications does)."""
        try:
            self.cache_manager.set("display_on_demand_request", {
                "request_id": str(uuid.uuid4()), "action": "start", "plugin_id": self.plugin_id,
                "mode": MODE, "duration": seconds}, ttl=3600)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Failed to request on-demand switch: %s", exc)

    def _apply(self, state: Dict[str, Any], now: float) -> None:
        ph = pd.phase(state)
        with self._lock:
            prev = self._phase
            self._state, self._phase = state, ph
            notify = False
            if prev is not None:
                if prev in ACTIVE and ph == pd.DONE:
                    self._burst_t0, self._done_at, notify = now, now, True
                    self.logger.info("Print finished: %s", state["task"])
                elif ph == pd.FAILED and prev != pd.FAILED:
                    self._failed_at, notify = now, True
                    self.logger.info("Print failed: %s", state["task"])
                elif ph == pd.PAUSED and prev != pd.PAUSED:
                    notify = True
                    self.logger.info("Print paused: %s", pd.pause_label(state))
        if notify and self.interrupt:
            self._request_on_demand()
        self._refresh_cover(state, ph, now)

    def _refresh_cover(self, s: Dict[str, Any], ph: str, now: float) -> None:
        if not self.show_cover or ph not in (pd.PRINTING, pd.PREPARING, pd.PAUSED, pd.DONE, pd.FAILED):
            return
        if not s["cover"] or s["cover"] == self._cover_key or now < self._cover_retry_at:
            return
        try:
            with Image.open(io.BytesIO(pd.fetch_cover(self.ha_url, self.ha_token, self.prefix))) as src:
                art = src.convert("RGBA")
            box = art.getbbox()
            if box:
                art = art.crop(box)
            self._cover, self._cover_key = art, s["cover"]
            self._tiles.clear()
        except Exception as exc:  # noqa: BLE001 - the "3D" tile stands in
            self._cover_retry_at = now + 300.0
            self.logger.warning("Cover image unavailable (%s: %s)", type(exc).__name__, str(exc)[:60])

    # ── drawing ──────────────────────────────────────────────────────────────

    def _tile(self, s: Dict[str, Any]) -> Image.Image:
        accent = render.accent_for(s["color"])
        key = (accent, id(self._cover))
        if key not in self._tiles:
            self._tiles[key] = render.tile(accent, self._cover)
        return self._tiles[key]

    def compose(self, now: Optional[float] = None) -> Optional[Image.Image]:
        """The next frame, or None when there is nothing worth showing (separate from display() for tests)."""
        now = time.time() if now is None else now
        if self.test_mode:
            s, b, dn, fl = demo.snapshot(now - self._demo_t0)
            ph = pd.phase(s)
            burst_t0 = now - b if b >= 0 else 0.0
            done_at = now - dn if dn >= 0 else 0.0
            failed_at = now - fl if fl >= 0 else 0.0
            if s["cover"] and self._cover is not None:
                s["cover"] = self._cover_key or s["cover"]
        else:
            with self._lock:
                s, ph = self._state, self._phase
                burst_t0, done_at, failed_at = self._burst_t0, self._done_at, self._failed_at
        if s is None:
            return None
        accent = render.accent_for(s["color"])
        frame: Optional[Image.Image] = None
        if burst_t0 and now - burst_t0 < BURST_SECONDS:
            frame = render.burst(s, self._tile(s), now - burst_t0)
        elif ph == pd.PRINTING:
            frame = render.printing(s, self._tile(s), accent, now, now)
        elif ph == pd.PREPARING:
            frame = render.preparing(s, self._tile(s), accent, now, now)
        elif ph == pd.PAUSED:
            frame = render.paused(s, self._tile(s), now)
        elif ph == pd.FAILED and failed_at and now - failed_at < max(self.hold, 60.0):
            frame = render.failed(s, now)
        elif ph == pd.DONE and done_at and now - done_at < self.hold:
            frame = render.done_card(s, self._tile(s), now)
        elif self.show_idle:
            frame = render.idle(s, now)
        if frame is None:
            return None
        if (self.W, self.H) != frame.size:
            canvas = Image.new("RGB", (self.W, self.H), (0, 0, 0))
            canvas.paste(frame, ((self.W - frame.width) // 2, (self.H - frame.height) // 2))
            frame = canvas
        return frame

    # ── BasePlugin ───────────────────────────────────────────────────────────

    def update(self) -> None:
        return                                            # the worker thread polls

    def display(self, force_clear: bool = False, display_mode: Optional[str] = None, **_: Any) -> bool:
        try:
            frame = self.compose()
            if frame is None:
                return False                              # nothing to show: the rotation moves on
            dm = self.display_manager
            dm.image.paste(frame, (0, 0))
            dm.update_display()
            return True
        except Exception as exc:  # noqa: BLE001
            import traceback
            self.logger.error("Error rendering print monitor: %s\n%s", exc, traceback.format_exc())
            return False

    def get_display_duration(self) -> float:
        return demo.CYCLE + 2.0 if self.test_mode else self.display_duration

    def has_live_content(self) -> bool:
        return bool(self.live_priority and self._phase in ACTIVE)

    def validate_config(self) -> bool:
        if not self.ha_token:
            self.logger.error("ha_token is required")
            return False
        return bool(self.prefix)

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        s = self._state or {}
        info.update({"phase": self._phase, "progress": s.get("progress"), "task": s.get("task"),
                     "last_poll": self._last_poll, "error": self._error})
        return info

    def cleanup(self) -> None:
        self._stop.set()
        super().cleanup()
