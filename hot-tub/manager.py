"""
Hot Tub status display for LEDMatrix  (arcade style, same family as NBA / NFL Live).

A native plugin: holds a real MQTT subscription and redraws from live state.

Expects a single JSON payload on one topic, published by a Home Assistant
automation (see readme.md). Every field is optional; missing data degrades to
a dash rather than an error.

    {
      "current_temp": 102,
      "target_temp":  104,
      "unit":         "F",
      "power":        "on",
      "heater":       "heating",   // heating | idle | off
      "filter":       "on",
      "bubbles":      "off",       // off | medium | high
      "connected":    true,
      "errors":       false
    }

Screens (hottub_render.py): the tub scene, and a READY burst when the water
reaches temperature after heating.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image

from src.plugin_system.base_plugin import BasePlugin

import hottub_render as render

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

# Within this many degrees of target counts as ready.
READY_TOLERANCE = 1.0
# The water level spans the last N degrees before target (0..target would
# barely move across a whole heat-up).
APPROACH_SPAN = 10.0
# No MQTT message for this long and the reading is suspect.
STALE_AFTER = 900.0
# How long the READY burst takes over the panel.
BURST_SECONDS = 6.0


class HotTubPlugin(BasePlugin):
    """Live hot tub status from Home Assistant over MQTT."""

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        self.mqtt_host: str = config.get("mqtt_host", "localhost")
        self.mqtt_port: int = int(config.get("mqtt_port", 1883))
        self.mqtt_topic: str = config.get("mqtt_topic", "ledmatrix/hottub")
        self.mqtt_username: str = config.get("mqtt_username", "")
        self.mqtt_password: str = config.get("mqtt_password", "")
        self.mqtt_client_id: str = config.get("mqtt_client_id", f"ledmatrix-{plugin_id}")

        self.show = {
            "heater": bool(config.get("show_heater", True)),
            "filter": bool(config.get("show_filter", True)),
            "bubbles": bool(config.get("show_bubbles", True)),
        }
        self.show_burst: bool = bool(config.get("show_ready_burst", True))

        self.W: int = display_manager.width
        self.H: int = display_manager.height

        # MQTT arrives on its own thread; the display loop reads this.
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {}
        self._last_msg: float = 0.0

        render.load_faces((str(Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"),
                           "/home/pi/LEDMatrix/assets/fonts"))

        self._prev_status: Optional[str] = None
        self._burst_t0: float = 0.0

        # High-FPS cadence for the water, bubbles and burst.
        self.enable_scrolling = True

        if MQTT_AVAILABLE:
            self._start_mqtt_client()
        else:
            self.logger.warning("paho-mqtt not installed -- hot tub data unavailable")

        self.logger.info("Hot Tub ready  W=%d H=%d  mqtt=%s:%d topic=%s",
                         self.W, self.H, self.mqtt_host, self.mqtt_port, self.mqtt_topic)

    # ── MQTT ─────────────────────────────────────────────────────────────────

    def _start_mqtt_client(self) -> None:
        client = mqtt.Client(client_id=self.mqtt_client_id)
        if self.mqtt_username:
            client.username_pw_set(self.mqtt_username, self.mqtt_password or None)

        def on_connect(c, userdata, flags, rc):
            if rc == 0:
                c.subscribe(self.mqtt_topic)
                self.logger.info("MQTT connected, subscribed to '%s'", self.mqtt_topic)
            else:
                self.logger.error("MQTT connect failed rc=%d", rc)

        def on_message(c, userdata, msg):
            try:
                data = json.loads(msg.payload.decode("utf-8"))
                if not isinstance(data, dict):
                    self.logger.warning("Ignoring non-object MQTT payload")
                    return
                with self._lock:
                    self._state = data
                    self._last_msg = time.time()
            except Exception as e:
                self.logger.error("MQTT parse error: %s", e)

        client.on_connect = on_connect
        client.on_message = on_message

        def _run():
            while True:
                try:
                    client.connect(self.mqtt_host, self.mqtt_port, keepalive=60)
                    client.loop_forever()
                except Exception as e:
                    self.logger.error("MQTT error: %s -- retrying in 10s", e)
                    time.sleep(10)

        threading.Thread(target=_run, daemon=True, name="hot-tub-mqtt").start()
        self._mqtt_client = client

    # ── State interpretation ─────────────────────────────────────────────────

    @staticmethod
    def _num(value) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _is_on(value) -> bool:
        return str(value).strip().lower() in ("on", "true", "1", "yes", "heat", "heating")

    @staticmethod
    def _level(value) -> int:
        """Bubbles setting as 0=off, 1=medium, 2=high.

        The entity is a `select` with named options, so collapsing it to on/off
        would throw away the level. Unrecognised-but-truthy values count as
        high, so a different option set still shows as running.
        """
        text = str(value).strip().lower()
        if text in ("", "off", "none", "false", "0", "unavailable", "unknown"):
            return 0
        if text in ("medium", "med", "low", "1", "half"):
            return 1
        return 2

    def _read_state(self, now: float) -> Dict[str, Any]:
        with self._lock:
            state = dict(self._state)
            last = self._last_msg

        current = self._num(state.get("current_temp"))
        target = self._num(state.get("target_temp"))
        connected = state.get("connected", True)
        errors = state.get("errors", False)
        power_on = self._is_on(state.get("power", "on"))
        heater = str(state.get("heater", "")).strip().lower()
        have_temps = current is not None and target is not None

        if last == 0.0:
            status = "NO DATA"
        elif errors:
            status = "ERROR"
        elif not connected:
            status = "OFFLINE"
        elif now - last > STALE_AFTER:
            status = "STALE"
        elif not power_on:
            status = "OFF"
        elif have_temps and current >= target - READY_TOLERANCE:
            status = "READY"
        elif heater in ("heating", "heat") or (have_temps and current < target):
            status = "HEATING"
        else:
            status = "IDLE"

        frac: Optional[float] = None
        if status in ("READY", "HEATING", "IDLE") and have_temps:
            frac = max(0.0, min(1.0, (current - (target - APPROACH_SPAN)) / APPROACH_SPAN))

        return {
            "current": current,
            "target": target,
            "unit": str(state.get("unit", "F")).upper()[:1] or "F",
            "status": status,
            "frac": frac,
            "heater_on": heater in ("heating", "heat") or status == "HEATING",
            "filter_on": self._is_on(state.get("filter")),
            "bubbles_level": self._level(state.get("bubbles")),
            "has_data": last > 0.0,
        }

    # ── Rendering ────────────────────────────────────────────────────────────

    def compose(self, now: Optional[float] = None) -> Image.Image:
        """Build the next frame (separate from display() so it can be tested)."""
        now = time.time() if now is None else now
        s = self._read_state(now)

        # Celebrate only a real heat-up finishing, never the first reading after a restart.
        if self.show_burst and s["status"] == "READY" and self._prev_status == "HEATING":
            self._burst_t0 = now
        self._prev_status = s["status"]

        if self._burst_t0 and now - self._burst_t0 < BURST_SECONDS:
            frame = render.burst(s, now - self._burst_t0)
        else:
            frame = render.tub(s, now, self.show)

        if (self.W, self.H) != frame.size:
            canvas = Image.new("RGB", (self.W, self.H), (0, 0, 0))
            canvas.paste(frame, ((self.W - frame.width) // 2, (self.H - frame.height) // 2))
            frame = canvas
        return frame

    # ── BasePlugin ───────────────────────────────────────────────────────────

    def update(self) -> None:
        # Nothing to poll: MQTT pushes state as it changes.
        return

    def display(self, force_clear: bool = False) -> None:
        try:
            frame = self.compose()
            dm = self.display_manager
            dm.image.paste(frame, (0, 0))
            dm.update_display()
        except Exception as e:
            import traceback
            self.logger.error("Error rendering hot tub display: %s\n%s", e, traceback.format_exc())

    def get_display_duration(self) -> float:
        return float(self.config.get("display_duration", 15))

    def validate_config(self) -> bool:
        return bool(self.mqtt_host and self.mqtt_topic)
