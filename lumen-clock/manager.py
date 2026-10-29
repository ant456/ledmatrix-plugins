"""
Lumen Clock for LEDMatrix.

A clock that is meant to be looked at: large soft digits that roll over when they change, over five scenes that
take turns, one per appearance on the panel:

    aurora    northern lights flowing behind the time, a thread of light along the foot counting the minute
    tide      water that rises with the seconds and drains at the top of the minute
    neon      glowing tubes on a brick wall, a comet running once round the edge every minute
    daylight  the real sky for your location (sun, Moon with its true phase, stars) behind the time
    orbit     a smooth analog dial with a comet-tailed second hand beside the digital time

The weekday, date and (optionally) the outdoor temperature from Home Assistant sit in a quiet column. The clock
runs on the Pi's own time and never waits on the network; Home Assistant is only asked for the temperature and, for
the Daylight scene, where you are.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from src.plugin_system.base_plugin import BasePlugin

import lumen_astro as astro
import lumen_data as data
import lumen_scenes as scenes
import lumen_type as ty

MODE = "lumen_clock"
UTC = timezone.utc
DEMO_SCENE_SECONDS = 9.0
FONT_DIRS_EXTRA = ("/home/pi/LEDMatrix/assets/fonts",)


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - no tz database or an unknown name: the machine's own zone
        return datetime.now().astimezone().tzinfo or UTC


class LumenClockPlugin(BasePlugin):
    """The clock."""

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.W: int = display_manager.width
        self.H: int = display_manager.height
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._place: Dict[str, Any] = {}
        self._temp: Optional[int] = None
        self._temp_ts = 0.0
        self._info: Optional[Dict[str, Any]] = None
        self._info_ts = 0.0
        self._info_key: Any = None
        self._demo_info: Optional[Dict[str, Any]] = None
        self._demo_key: Any = None
        self._text: Optional[str] = None
        self._prev = ""
        self._change_t = -1e9
        self._turn = -1
        self._turn_t0 = 0.0
        self._last_display = 0.0
        self._demo_t0 = time.time()
        self.enable_scrolling = True                         # high-FPS path: the scenes are always moving
        self._load_config(config)

        here = Path(__file__).resolve().parent
        dirs = [str(here / "fonts"), str(here.parent.parent / "assets" / "fonts")] + list(FONT_DIRS_EXTRA)
        path = ty.find_font(dirs, ty.FONT_NAMES) or ty.find_font(dirs, ty.FALLBACKS)
        self.faces = {"big": ty.Face(path, 28, 500, 0.70), "small": ty.Face(path, 22, 500, 0.70), "temp": ty.Face(path, 10, 600)}
        if path is None:
            self.logger.warning("No TrueType font found; using Pillow's built-in face")

        if self.test_mode:
            self._request_on_demand(self.get_display_duration() + 2.0)
            self.logger.info("Lumen Clock TEST MODE: time runs %dx faster, scenes every %ds", self.demo_speed, DEMO_SCENE_SECONDS)
        elif self.ha_token or self._cfg_lat is not None:
            threading.Thread(target=self._loop, daemon=True, name="lumen-clock-poll").start()
        self.logger.info("Lumen Clock ready  scenes=%s  W=%d H=%d", self.scene_names, self.W, self.H)

    # ── config ───────────────────────────────────────────────────────────────

    def _load_config(self, c: Dict[str, Any]) -> None:
        self.ha_url = str(c.get("ha_url", "")).rstrip("/")
        self.ha_token = str(c.get("ha_token", ""))
        self.weather_entity = data.valid_entity(c.get("weather_entity"), "weather.openweathermap")
        self.h12 = str(c.get("time_format", "12h")).lower() != "24h"
        self.show_seconds = bool(c.get("show_seconds", True))
        self.show_temperature = bool(c.get("show_temperature", True))
        self.fade = float(max(0.0, min(3.0, data.num(c.get("fade_seconds")) if data.num(c.get("fade_seconds")) is not None else 0.6)))
        style = str(c.get("style", "cycle")).lower()
        wanted = [s for s in (c.get("scenes") or list(scenes.SCENES)) if s in scenes.SCENES]
        self.style = style if style in scenes.SCENES else "cycle"
        self.scene_names: List[str] = [self.style] if self.style != "cycle" else (wanted or list(scenes.SCENES))
        self.display_duration = float(max(8, int(data.num(c.get("display_duration")) or 20)))
        self.demo_speed = int(max(1, min(600, data.num(c.get("demo_speed")) or 90)))
        self.test_mode = bool(c.get("test_mode", False))
        self._cfg_lat, self._cfg_lon = data.num(c.get("latitude")), data.num(c.get("longitude"))
        self._cfg_tz = str(c.get("timezone") or "")

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        super().on_config_change(new_config)
        self.config = new_config
        self._load_config(new_config)
        self._info = None

    @property
    def tz(self):
        return _zone(self._cfg_tz or self._place.get("tz") or "")

    @property
    def lat(self) -> Optional[float]:
        return self._cfg_lat if self._cfg_lat is not None else self._place.get("lat")

    @property
    def lon(self) -> Optional[float]:
        return self._cfg_lon if self._cfg_lon is not None else self._place.get("lon")

    # ── background work (optional: temperature and place) ────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            if self.ha_url and self.ha_token:
                try:
                    if not self._place:
                        self._place = data.fetch_place(self.ha_url, self.ha_token)
                    if self.show_temperature:
                        t = data.fetch_temperature(self.ha_url, self.ha_token, self.weather_entity)
                        with self._lock:
                            self._temp, self._temp_ts = t, now
                except Exception as exc:  # noqa: BLE001 - the clock carries on without it
                    self.logger.warning("Home Assistant poll failed: %s", exc)
            try:
                if self.lat is not None and self.lon is not None and (self._info is None or now - self._info_ts > 240):
                    self._refresh_info(now)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("sky facts failed: %s", exc)
            self._stop.wait(60.0)

    def _refresh_info(self, now: float) -> None:
        self._info = astro.day_info(datetime.fromtimestamp(now, UTC), self.lat, self.lon, self.tz)
        self._info_ts = now

    def _request_on_demand(self, seconds: float) -> None:
        """Ask the display controller to show us right now (the way the other plugins do)."""
        try:
            self.cache_manager.set("display_on_demand_request", {
                "request_id": str(uuid.uuid4()), "action": "start", "plugin_id": self.plugin_id,
                "mode": MODE, "duration": seconds}, ttl=3600)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Failed to request on-demand switch: %s", exc)

    # ── what to show ─────────────────────────────────────────────────────────

    def _virtual_time(self, now: float) -> datetime:
        if self.test_mode:
            base = datetime.fromtimestamp(self._demo_t0, self.tz).replace(hour=5, minute=40, second=0, microsecond=0)
            return base + timedelta(seconds=(now - self._demo_t0) * self.demo_speed)
        return datetime.fromtimestamp(now, self.tz)

    def _info_for(self, dt: datetime, now: float) -> Optional[Dict[str, Any]]:
        if self.lat is None or self.lon is None:
            return None
        if not self.test_mode:
            if self._info is None:
                self._refresh_info(now)                       # first frame before the worker got there
            return self._info
        key = (dt.date(), dt.hour * 2 + dt.minute // 30)
        if key != self._demo_key or self._demo_info is None:
            self._demo_info = astro.day_info(dt.astimezone(UTC), self.lat, self.lon, self.tz)
            self._demo_key = key
        return self._demo_info

    def _available(self, info: Optional[Dict[str, Any]]) -> List[str]:
        return [s for s in self.scene_names if s not in scenes.NEEDS_PLACE or info is not None] or ["aurora"]

    def _temp_text(self, now: float) -> Optional[str]:
        if not self.show_temperature:
            return None
        if self.test_mode:
            return "65"
        with self._lock:
            ok = self._temp is not None and now - self._temp_ts < 1800
            return str(self._temp) if ok else None

    def compose(self, now: Optional[float] = None) -> Image.Image:
        """The next frame (separate from display() for tests)."""
        now = time.time() if now is None else now
        dt = self._virtual_time(now)
        info = self._info_for(dt, now)
        avail = self._available(info)
        text = ("%d:%02d" % (dt.hour % 12 or 12, dt.minute)) if self.h12 else "%02d:%02d" % (dt.hour, dt.minute)
        if self._text is None:
            self._text = self._prev = text                    # a fresh turn starts steady: nothing rolls on the first frame
        elif text != self._text:
            self._prev, self._text, self._change_t = self._text, text, now
        if self.test_mode:
            name = avail[int((now - self._demo_t0) // DEMO_SCENE_SECONDS) % len(avail)]
        else:
            name = avail[self._turn % len(avail)] if self._turn >= 0 else avail[0]
        ctx = {
            "now": now, "dt": dt, "sec": dt.second + dt.microsecond / 1e6, "text": text, "prev": self._prev,
            "age": now - self._change_t, "dow": dt.strftime("%a").upper(), "md": "%s %d" % (dt.strftime("%b").upper(), dt.day),
            "ampm": ("PM" if dt.hour >= 12 else "AM") if self.h12 else "", "temp": self._temp_text(now),
            "face": self.faces["big"], "face_s": self.faces["small"], "face_t": self.faces["temp"],
            "lat": self.lat, "lon": self.lon, "info": info, "show_seconds": self.show_seconds,
        }
        frame = scenes.SCENES[name](ctx)
        since = now - (self._demo_t0 + ((now - self._demo_t0) // DEMO_SCENE_SECONDS) * DEMO_SCENE_SECONDS) if self.test_mode \
            else now - self._turn_t0
        if self.fade and 0.0 <= since < self.fade:                         # each appearance fades up from black
            frame = Image.blend(Image.new("RGB", frame.size, (0, 0, 0)), frame, ty.ease(since / self.fade))
        return self._fit(frame)

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
            now = time.time()
            if force_clear or now - self._last_display > 1.5:  # a fresh turn: next scene, fade up, no digit roll
                self._turn += 1
                self._turn_t0 = now
                self._change_t = -1e9
                self._text = None
            self._last_display = now
            frame = self.compose(now)
            dm = self.display_manager
            dm.image.paste(frame, (0, 0))
            dm.update_display()
            return True
        except Exception as exc:  # noqa: BLE001
            import traceback
            self.logger.error("Error rendering lumen clock: %s\n%s", exc, traceback.format_exc())
            return False

    def get_display_duration(self) -> float:
        if self.test_mode:
            return DEMO_SCENE_SECONDS * len(self.scene_names)
        return self.display_duration

    def validate_config(self) -> bool:
        return True

    def cleanup(self) -> None:
        self._stop.set()

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update({"scenes": self.scene_names, "turn": self._turn, "temp": self._temp})
        return info
