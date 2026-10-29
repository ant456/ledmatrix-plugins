"""
Sky Weather for LEDMatrix.

A weather display that follows the sky: the backdrop is coloured by the real sun (night, dawn, day, golden hour,
dusk), the sun and Moon ride their arcs, the Moon shows its true phase and tilt for this place, and clouds / rain /
snow / fog / lightning come from the current conditions. Pages: now, sun, moon, hourly, daily, details, and an alert
page for every active National Weather Service alert.

Everything comes from Home Assistant (weather entity + forecast service + NWS alerts sensor + HA's own location),
so there are no extra API keys. A background thread polls; display() only draws from memory.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from src.plugin_system.base_plugin import BasePlugin

import sky_astro as astro
import sky_data as data
import sky_render as render

MODE = "sky_weather"
UTC = timezone.utc
TIMELAPSE = 30.0                     # test_mode: seconds of fast-forwarded day
DEMO_PAGE = 6.0                      # test_mode: seconds per page after the time-lapse


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - no tz database or an unknown name: use the machine's own zone
        return datetime.now().astimezone().tzinfo or UTC


def _num(v: Any, default: float) -> float:
    f = data.num(v)
    return default if f is None else f


def _rgb(v: Any, default: tuple) -> tuple:
    try:
        r, g, b = (int(min(255, max(0, x))) for x in v)
        return (r, g, b)
    except (TypeError, ValueError):
        return default


class SkyWeatherPlugin(BasePlugin):
    """The sky as a weather display."""

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.W: int = display_manager.width
        self.H: int = display_manager.height
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state: Dict[str, Any] = {}
        self._hourly: List[Dict[str, Any]] = []
        self._daily: List[Dict[str, Any]] = []
        self._place: Dict[str, Any] = {}
        self._info_key: Optional[int] = None
        self._info: Optional[Dict[str, Any]] = None
        self._info_ts = 0.0
        self._demo_key: Optional[int] = None
        self._demo_info: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None
        self._last_ok = 0.0
        self._seen_alerts: set = set()
        self._alerts_primed = False
        self._t0 = 0.0
        self._last_display = 0.0
        self._demo_t0 = time.time()
        self._radar = None                                   # sky_radar.RadarFetcher once the location is known
        self._radar_sig: Optional[tuple] = None
        self._prev_page = ""
        self._radar_announced = False
        self.enable_scrolling = True                         # high-FPS path: clouds drift, rain falls, stars twinkle
        self._load_config(config)

        here = Path(__file__).resolve().parent
        render.load_faces((str(here.parent.parent / "assets" / "fonts"), "/home/pi/LEDMatrix/assets/fonts"))

        if self.test_mode:
            self._request_on_demand(self.get_display_duration() + 2.0)
            self.logger.info("Sky Weather TEST MODE: a made-up day, no polling")
        else:
            threading.Thread(target=self._loop, daemon=True, name="sky-weather-poll").start()
            threading.Thread(target=self._radar_loop, daemon=True, name="sky-weather-radar").start()
        self.logger.info("Sky Weather ready  pages=%s  W=%d H=%d", self.pages, self.W, self.H)

    # ── config ───────────────────────────────────────────────────────────────

    def _load_config(self, c: Dict[str, Any]) -> None:
        self.ha_url = str(c.get("ha_url", "http://homeassistant.local:8123")).rstrip("/")
        self.ha_token = str(c.get("ha_token", ""))
        self.entities = {k: data.valid_entity(c.get(k), v) for k, v in data.DEFAULTS.items()}
        self.radar_on = bool(c.get("radar", True))
        pages = [p for p in (c.get("pages") or render.DEFAULT_PAGES) if p in render.ALL_PAGES and (self.radar_on or p != "radar")]
        self.pages: List[str] = pages or [p for p in render.DEFAULT_PAGES if p != "radar" or self.radar_on]
        self.radar_cfg = {
            "range_miles": max(5.0, _num(c.get("radar_range_miles"), 75.0)),
            "update_interval": int(max(60, _num(c.get("radar_update_interval"), 180))),
            "map_style": str(c.get("radar_map_style") or "carto_dark"),
            "tile_server": str(c.get("radar_tile_server") or ""),
            "map_brightness": min(1.0, max(0.1, _num(c.get("radar_map_brightness"), 0.5))),
            "show_nowcast": bool(c.get("radar_show_nowcast", False)),
            "past_frames": int(min(12, max(2, _num(c.get("radar_past_frames"), 6)))),
            "frame_seconds": min(2.0, max(0.1, _num(c.get("radar_frame_seconds"), 0.5))),
            "loop_pause_seconds": min(5.0, max(0.0, _num(c.get("radar_loop_pause_seconds"), 1.5))),
            "smooth": bool(c.get("radar_smooth", True)),
            "line_color": _rgb(c.get("radar_line_color"), (0, 130, 70)),
            "fill_color": _rgb(c.get("radar_fill_color"), (15, 25, 15)),
        }
        self.radar_seconds = float(max(0, _num(c.get("radar_seconds"), 0)))      # 0 = two loops, between page_seconds and 30
        self.page_seconds = float(max(3, int(c.get("page_seconds", 7) or 7)))
        self.poll = float(max(15, int(c.get("poll_seconds", 60) or 60)))
        self.forecast_poll = float(max(120, int(c.get("forecast_seconds", 600) or 600)))
        self.hourly_step = int(min(6, max(1, int(c.get("hourly_step", 2) or 2))))
        self.detail_tiles = [t for t in (c.get("detail_tiles") or []) if isinstance(t, str)]
        self.show_alerts = bool(c.get("alerts", True))
        self.interrupt_alerts = bool(c.get("interrupt_alerts", False))
        self.alert_seconds = float(max(4, int(c.get("alert_seconds", 8) or 8)))
        self.max_alerts = int(min(5, max(1, int(c.get("max_alerts", 3) or 3))))
        self.test_mode = bool(c.get("test_mode", False))
        self._cfg_lat, self._cfg_lon = data.num(c.get("latitude")), data.num(c.get("longitude"))
        self._cfg_tz = str(c.get("timezone") or "")

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        super().on_config_change(new_config)
        self.config = new_config
        self._load_config(new_config)
        self._info = None

    # ── place and time zone ──────────────────────────────────────────────────

    @property
    def tz(self):
        return _zone(self._cfg_tz or self._place.get("tz") or "")

    @property
    def lat(self) -> Optional[float]:
        return self._cfg_lat if self._cfg_lat is not None else self._place.get("lat")

    @property
    def lon(self) -> Optional[float]:
        return self._cfg_lon if self._cfg_lon is not None else self._place.get("lon")

    # ── polling (worker thread) ──────────────────────────────────────────────

    def _loop(self) -> None:
        last_forecast = 0.0
        while not self._stop.is_set():
            now = time.time()
            try:
                if not self._place:
                    self._place = data.fetch_place(self.ha_url, self.ha_token)
                state = data.fetch_state(self.ha_url, self.ha_token, self.entities)
                self._apply_state(state, now)
                self._error = None
                if self.lat is not None and self.lon is not None and now - self._info_ts > 240:
                    self._day_info(now, self.lat, self.lon, force=True)
                if now - last_forecast >= self.forecast_poll or not self._hourly or not self._daily:
                    ok = True
                    for kind, attr in (("hourly", "_hourly"), ("daily", "_daily")):
                        try:
                            items = data.fetch_forecast(self.ha_url, self.ha_token, self.entities["weather_entity"], kind)
                            if items:
                                setattr(self, attr, items)
                        except Exception as exc:  # noqa: BLE001 - one forecast failing keeps the other and the last values
                            ok = False
                            self.logger.warning("%s forecast failed: %s", kind, exc)
                    last_forecast = now if ok else now - self.forecast_poll + 60.0
            except Exception as exc:  # noqa: BLE001 - keep polling; the last values stay on screen
                self._error = "%s: %s" % (type(exc).__name__, str(exc)[:80])
                self.logger.warning("HA poll failed (%s)", self._error)
            self._stop.wait(self.poll)

    # ── radar (own thread: tile downloads can take a while and must never stall the weather poll) ──

    def _ensure_radar(self) -> None:
        if not self.radar_on or self.lat is None or self.lon is None:
            return
        cfg = self.radar_cfg
        sig = (self.lat, self.lon) + tuple(sorted((k, str(v)) for k, v in cfg.items()))
        if sig == self._radar_sig and self._radar is not None:
            return
        from sky_radar import RadarFetcher                    # lazy: pulls in requests and the tile code
        self._radar = RadarFetcher(
            self.lat, self.lon, cfg["range_miles"], self.cache_manager, map_style=cfg["map_style"],
            custom_tile_server=cfg["tile_server"] or None, line_color=cfg["line_color"], fill_color=cfg["fill_color"],
            map_brightness=cfg["map_brightness"], show_nowcast=cfg["show_nowcast"], past_frames=cfg["past_frames"],
            frame_seconds=cfg["frame_seconds"], loop_pause_seconds=cfg["loop_pause_seconds"], plugin_id=self.plugin_id)
        self._radar_sig = sig

    def _update_radar(self) -> None:
        self._ensure_radar()
        fetcher = self._radar
        if fetcher is not None and fetcher.needs_refresh(self.radar_cfg["update_interval"]):
            fetcher.refresh_data(self.W, self.H)

    def _radar_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._update_radar()
                if not self._radar_announced and self._radar_ready():
                    self._radar_announced = True
                    self.logger.info("Radar ready: %d frames, %.1fs loop", len(self._radar._playback_frames()),
                                     self._radar.get_loop_duration())
            except Exception as exc:  # noqa: BLE001 - radar trouble never touches the other pages
                self.logger.warning("radar refresh failed: %s", exc)
            self._stop.wait(10.0)

    def _radar_ready(self) -> bool:
        """True once the radar has at least one finished frame to play."""
        f = self._radar
        return bool(self.radar_on and f is not None and f._playback_frames())

    def _radar_page_seconds(self) -> float:
        if self.radar_seconds:
            return self.radar_seconds
        f = self._radar
        loop = f.get_loop_duration() if f is not None else 0.0
        return min(30.0, max(self.page_seconds, 2.0 * loop))

    def _apply_state(self, state: Dict[str, Any], now: float) -> None:
        fresh = False
        with self._lock:
            self._state, self._last_ok = state, now
            keys = {(a["event"], a["severity"]) for a in state.get("alerts", [])}
            if self._alerts_primed and not keys <= self._seen_alerts:
                fresh = True
            self._seen_alerts = keys
            self._alerts_primed = True
        if fresh and self.show_alerts:
            self.logger.info("New weather alert: %s", [a["event"] for a in state.get("alerts", [])][:3])
            if self.interrupt_alerts and not self.test_mode:
                self._request_on_demand(self.alert_seconds * min(len(state["alerts"]), self.max_alerts) + 2.0)

    def _request_on_demand(self, seconds: float) -> None:
        """Ask the display controller to show us right now (the way ha-notifications does)."""
        try:
            self.cache_manager.set("display_on_demand_request", {
                "request_id": str(uuid.uuid4()), "action": "start", "plugin_id": self.plugin_id,
                "mode": MODE, "duration": seconds}, ttl=3600)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Failed to request on-demand switch: %s", exc)

    # ── world ────────────────────────────────────────────────────────────────

    def _day_info(self, now: float, lat: float, lon: float, force: bool = False) -> Dict[str, Any]:
        """Sunrise, sunset, Moon times (about 150 ms on a Pi). The poll thread keeps this fresh so drawing never waits on it."""
        if force or self._info is None or now - self._info_ts > 600 or self._info_key != (lat, lon):
            self._info = astro.day_info(datetime.fromtimestamp(now, UTC), lat, lon, self.tz)
            self._info_ts, self._info_key = now, (lat, lon)
        return self._info

    def _world(self, now: float) -> Optional[Dict[str, Any]]:
        if self.lat is None or self.lon is None:
            return None
        with self._lock:
            st, hourly, daily = dict(self._state), list(self._hourly), list(self._daily)
        if not st.get("now"):
            return None
        alerts = []
        if self.show_alerts:
            cutoff = datetime.fromtimestamp(now, UTC)
            alerts = [a for a in st.get("alerts", []) if not (a["expires"] and a["expires"] < cutoff)][:self.max_alerts]
        return {"ts": now, "tz": self.tz, "lat": self.lat, "lon": self.lon, "cur": st["now"],
                "hourly": hourly, "daily": daily, "alerts": alerts, "info": self._day_info(now, self.lat, self.lon),
                "hourly_step": self.hourly_step, "detail_tiles": self.detail_tiles}

    # ── test mode ────────────────────────────────────────────────────────────

    def _demo_world(self, tt: float, now: float) -> Dict[str, Any]:
        """A made-up day: 30 s of time-lapse (clear dawn, clouds, a storm, a golden sunset, moonrise), then every page."""
        tz = self.tz
        lat = self.lat if self.lat is not None else 40.03
        lon = self.lon if self.lon is not None else -75.32
        midnight = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
        timelapse = tt < TIMELAPSE
        hour = 5.0 + (tt / TIMELAPSE) * 17.0 if timelapse else 15.0
        ts = (midnight + timedelta(hours=hour)).timestamp() if timelapse else (midnight + timedelta(hours=15)).timestamp()
        sched = [(0, "clear-night", 0), (6.5, "sunny", 0), (10, "partlycloudy", 45), (13, "cloudy", 85),
                 (14.5, "lightning-rainy", 100), (16.5, "rainy", 95), (17.5, "partlycloudy", 40), (19, "clear-night", 5)]
        cond, cloud = next((c, k) for h, c, k in reversed(sched) if hour >= h)
        if not timelapse:
            cond, cloud = "partlycloudy", 35
        temp = 52 + 14 * math.sin((hour - 9.0) / 24.0 * 2 * math.pi)
        cur = {"cond": cond, "temp": temp, "feels": temp - 2, "unit": "F", "humidity": 64, "cloud": cloud, "wind": 9.0,
               "gust": 16.0, "wind_unit": "mph", "bearing": 230, "uv": 3.0, "pressure": 30.1, "pressure_unit": "inHg",
               "dew": 48, "visibility": 9, "visibility_unit": "mi"}
        base = datetime.fromtimestamp(ts if timelapse else now, UTC)
        conds = ["sunny", "sunny", "partlycloudy", "cloudy", "rainy", "rainy", "partlycloudy", "clear-night", "clear-night"]
        hourly = [{"t": base + timedelta(hours=i), "cond": conds[(i // 2) % len(conds)], "temp": temp + 6 * math.sin(i / 4.0) - i * 0.6,
                   "low": None, "pop": [0, 5, 20, 45, 70, 80, 35, 10, 0][(i // 2) % 9], "wind": 8.0, "precip": 0.0, "uv": 3.0}
                  for i in range(30)]
        days = ["sunny", "partlycloudy", "rainy", "cloudy", "snowy", "sunny", "sunny", "partlycloudy"]
        daily = [{"t": base + timedelta(days=i), "cond": days[i % 8], "temp": 66 + 3 * math.sin(i), "low": 48 + 2 * math.cos(i),
                  "pop": [0, 10, 70, 40, 60, 0, 0, 20][i % 8], "wind": 8.0, "precip": 0.0, "uv": 3.0} for i in range(8)]
        alerts = [{"event": "Flood Watch", "severity": "moderate", "headline": "", "expires": base + timedelta(hours=9), "urgency": ""},
                  {"event": "Severe Thunderstorm Warning", "severity": "severe", "headline": "", "expires": base + timedelta(hours=1), "urgency": ""}]
        world_ts = ts if timelapse else now
        key = int(world_ts // 300)
        if key != self._demo_key:
            self._demo_info, self._demo_key = astro.day_info(datetime.fromtimestamp(world_ts, UTC), lat, lon, tz), key
        return {"ts": world_ts, "anim": now, "tz": tz, "lat": lat, "lon": lon, "cur": cur, "hourly": hourly, "daily": daily,
                "alerts": alerts if (not timelapse and tt >= TIMELAPSE + 5 * DEMO_PAGE) else [], "info": self._demo_info,
                "hourly_step": self.hourly_step, "detail_tiles": self.detail_tiles}

    # ── drawing ──────────────────────────────────────────────────────────────

    def page_sequence(self, world: Dict[str, Any]) -> List[str]:
        alerts = [("alert", i) for i in range(len(world.get("alerts") or []))]
        ready = self._radar_ready()
        return alerts + [(p, 0) for p in self.pages if p != "radar" or ready]

    def compose(self, now: Optional[float] = None) -> Optional[Image.Image]:
        """The next frame, or None when there is no data yet (separate from display() for tests)."""
        now = time.time() if now is None else now
        if self.test_mode:
            tt = (now - self._demo_t0) % (TIMELAPSE + DEMO_PAGE * 6)
            world = self._demo_world(tt, now)
            if tt < TIMELAPSE:
                page = ("now", 0)
            else:
                order = ["sun", "moon", "hourly", "daily", "details", "alert"]
                page = (order[min(5, int((tt - TIMELAPSE) // DEMO_PAGE))], 0)
            frame = render.render(world, page[0], None, page[1])
            return self._fit(frame)
        world = self._world(now)
        if world is None:
            return None
        if self._error and now - self._last_ok > 600:
            return self._fit(render.notice(now))
        seq = self.page_sequence(world)
        name, arg = seq[self._index(seq, now)]
        if name == "radar":
            if self._prev_page != "radar":
                self._radar.reset_loop()                      # always start the loop on its first frame
            self._prev_page = name
            get = self._radar.get_smooth_image if self.radar_cfg["smooth"] else self._radar.get_radar_image
            frame = get(self.W, self.H)
            if frame is not None:
                return self._fit(frame.convert("RGB"))
            name = "now"
        self._prev_page = name
        return self._fit(render.render(world, name, None, arg))

    def _seconds_for(self, seq, i: int) -> float:
        if seq[i][0] == "alert":
            return self.alert_seconds
        return self._radar_page_seconds() if seq[i][0] == "radar" else self.page_seconds

    def _index(self, seq, now: float) -> int:
        """Which page is up: pages take turns from the moment this plugin's turn on the panel began."""
        elapsed = max(0.0, now - self._t0)
        total = sum(self._seconds_for(seq, i) for i in range(len(seq)))
        elapsed %= total
        for i in range(len(seq)):
            elapsed -= self._seconds_for(seq, i)
            if elapsed < 0:
                return i
        return 0

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
            if force_clear or now - self._last_display > 1.5:
                self._t0 = now                                # a fresh turn: start at the first page
            self._last_display = now
            frame = self.compose(now)
            if frame is None:
                return False                                  # no data yet: the rotation moves on
            dm = self.display_manager
            dm.image.paste(frame, (0, 0))
            dm.update_display()
            return True
        except Exception as exc:  # noqa: BLE001
            import traceback
            self.logger.error("Error rendering sky weather: %s\n%s", exc, traceback.format_exc())
            return False

    def get_display_duration(self) -> float:
        if self.test_mode:
            return TIMELAPSE + DEMO_PAGE * 6
        with self._lock:
            n_alerts = min(len(self._state.get("alerts", [])), self.max_alerts) if self.show_alerts else 0
        others = [p for p in self.pages if p != "radar"]
        radar = self._radar_page_seconds() if ("radar" in self.pages and self._radar_ready()) else 0.0
        return self.page_seconds * len(others) + radar + self.alert_seconds * n_alerts

    def validate_config(self) -> bool:
        if not self.ha_token:
            self.logger.error("ha_token is required")
            return False
        return True

    def cleanup(self) -> None:
        self._stop.set()

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update({"pages": self.pages, "last_ok": self._last_ok, "error": self._error,
                     "alerts": len(self._state.get("alerts", []))})
        return info
