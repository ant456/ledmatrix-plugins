"""
HA Notifications Plugin for LEDMatrix

Receives notifications from Home Assistant via:
  - HTTP webhook (Flask, port 5006 by default)
  - MQTT (paho-mqtt, subscribes to a configurable topic)

Arcade style (same family as NBA / NFL / NHL Live): a splash on arrival, then a
card with the icon, title and a big message that crawls once so it is read in full.
Drawing lives in hanotif_render.py; display() is called by the controller and
draws from the clock, not from a per-call counter.
"""

from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageSequence

from src.plugin_system.base_plugin import BasePlugin

import hanotif_render as render

# ── Optional Flask ────────────────────────────────────────────────────────────
try:
    from flask import Flask, Response, request, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# ── Optional paho-mqtt ────────────────────────────────────────────────────────
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
# Icons
# ═══════════════════════════════════════════════════════════════════════════════

ICONS: Dict[str, List[str]] = {
    "door": [
        "...########.....",
        "..#........####.",
        "..#.........#.#.",
        "..#.........#.#.",
        "..#..........#..",
        "..#.....##...#..",
        "..#.....##...#..",
        "..#..........#..",
        "..#..........#..",
        "..#..........#..",
        "..#..........#..",
        "..#..........#..",
        "..#..........#..",
        "..#..........#..",
        "..############..",
        "................",
    ],
    "motion": [
        "....####........",
        "...#....#.......",
        "..#......#...##.",
        "..#..##..#..#...",
        "..#..##..#.#....",
        "...#....#..#....",
        "....####...#....",
        "...........#....",
        "..........#.....",
        ".........#......",
        ".......##.......",
        "......#.........",
        ".....#..........",
        "....#...........",
        "................",
        "................",
    ],
    "lock": [
        "....######......",
        "...#......#.....",
        "..#........#....",
        "..#........#....",
        "..########..##..",
        ".#..........#.#.",
        ".#..........#.#.",
        ".#....##....###.",
        ".#....##........",
        ".#....##........",
        ".#..........#...",
        ".#..........#...",
        ".############...",
        "................",
        "................",
        "................",
    ],
    "fire": [
        ".....#..........",
        "....###.........",
        "...#####........",
        "..##.####.......",
        ".###..####......",
        ".##....###......",
        ".#......##......",
        ".#......##......",
        ".##....###......",
        ".######.##......",
        ".########.#.....",
        "..#######.#.....",
        "...######.#.....",
        "....####.#......",
        ".....####.......",
        "................",
    ],
    "water": [
        ".......#........",
        "......###.......",
        ".....#####......",
        "....#######.....",
        "...#########....",
        "..###########...",
        "..###########...",
        "..###########...",
        "..###########...",
        "...#########....",
        "....#######.....",
        ".....#####......",
        "......###.......",
        ".......#........",
        "................",
        "................",
    ],
    "thermometer": [
        "......###.......",
        ".....#...#......",
        ".....#...#......",
        ".....#...#......",
        ".....####.......",
        ".....#..........",
        ".....#..........",
        ".....#..........",
        ".....#..........",
        "....###.........",
        "...#####........",
        "...#####........",
        "....###.........",
        "................",
        "................",
        "................",
    ],
    "person": [
        ".....####.......",
        "....######......",
        "....######......",
        ".....####.......",
        "...##########...",
        "..############..",
        "..####..####.#..",
        "..###....####...",
        "..###....###....",
        "..####..####....",
        "...##########...",
        "....########....",
        "....##....##....",
        "....##....##....",
        "...####..####...",
        "................",
    ],
    "bell": [
        "......##........",
        ".....#..#.......",
        "....######......",
        "...########.....",
        "...########.....",
        "...########.....",
        "...########.....",
        "...########.....",
        "..##########....",
        "................",
        ".....####.......",
        ".....####.......",
        "......##........",
        "................",
        "................",
        "................",
    ],
    "check": [
        "................",
        "................",
        "..............#.",
        ".............##.",
        "............###.",
        "...........###..",
        "..........###...",
        ".#........###...",
        ".##......###....",
        ".###....###.....",
        "..###..###......",
        "...######.......",
        "....####........",
        ".....##.........",
        "................",
        "................",
    ],
    "warning": [
        ".......##.......",
        "......####......",
        ".....######.....",
        ".....##..##.....",
        "....##....##....",
        "....##....##....",
        "...##......##...",
        "...##..##..##...",
        "..##...##...##..",
        "..##...##...##..",
        ".##....##....##.",
        ".##...####...##.",
        "##############..",
        "................",
        "................",
        "................",
    ],
    "home": [
        ".......##.......",
        "......####......",
        ".....######.....",
        "....########....",
        "...##########...",
        "..####..####....",
        ".#####..#####...",
        "######..######..",
        "###.######.###..",
        "###.######.###..",
        "###........###..",
        "###........###..",
        "###........###..",
        "####......####..",
        "................",
        "................",
    ],
    "camera": [
        "................",
        "................",
        "....#...#.......",
        "...###.###......",
        "..############..",
        ".##.########.##.",
        ".##.########.##.",
        ".##..######..##.",
        ".##...####...##.",
        ".##...####...##.",
        ".##..######..##.",
        ".##.########.##.",
        "..############..",
        "................",
        "................",
        "................",
    ],
    "car": [
        "................",
        "................",
        "...##########...",
        ".##..######.##..",
        "##############..",
        "##############..",
        "##############..",
        "##############..",
        ".##..####..##...",
        ".##########.#...",
        ".##########.#...",
        "..####..####....",
        "................",
        "................",
        "................",
        "................",
    ],
    "power": [
        ".......##.......",
        "......####......",
        ".....##..##.....",
        "....##....##....",
        "....##....##....",
        "...##......##...",
        "...##..##..##...",
        "...##.####.##...",
        "....#######.....",
        ".....#####......",
        "......###.......",
        ".......#........",
        "................",
        "................",
        "................",
        "................",
    ],
    "music": [
        ".....#########..",
        ".....#########..",
        ".....##......##.",
        ".....##......##.",
        ".....##......##.",
        ".....##......##.",
        ".....##......##.",
        ".....##......##.",
        "..####.......##.",
        ".#####.......##.",
        "######.......##.",
        ".#####.......##.",
        "..####.....####.",
        "...........####.",
        "...........####.",
        "................",
    ],
    "light": [
        "................",
        ".......##.......",
        "......####......",
        ".....######.....",
        "....########....",
        "....########....",
        "....########....",
        ".....######.....",
        "......####......",
        "......####......",
        ".....######.....",
        "......####......",
        ".....######.....",
        "......####......",
        ".......##.......",
        "................",
    ],
}

# ═══════════════════════════════════════════════════════════════════════════════
# LaMetric icon support — full color + animated GIF, fetched + cached on first use
# ═══════════════════════════════════════════════════════════════════════════════

LAMETRIC_ICON_SIZE = 16
LAMETRIC_CACHE_DIR = os.path.expanduser("~/.cache/ledmatrix-ha-notifications/lametric_icons")


@dataclass
class IconAsset:
    frames: List[Image.Image]        # RGBA frames, pre-sized to LAMETRIC_ICON_SIZE
    frame_durations_ms: List[int]
    total_duration_ms: int = field(init=False)

    def __post_init__(self):
        self.total_duration_ms = sum(self.frame_durations_ms) or 1

    def frame_at(self, t: float) -> Image.Image:
        if len(self.frames) == 1:
            return self.frames[0]
        elapsed_ms = int((t * 1000) % self.total_duration_ms)
        acc = 0
        for frame, dur in zip(self.frames, self.frame_durations_ms):
            acc += dur
            if elapsed_ms < acc:
                return frame
        return self.frames[-1]


_LAMETRIC_ASSET_CACHE: Dict[str, IconAsset] = {}


def _lametric_icon_id(name: str) -> Optional[str]:
    """Accept 'lametric:1234', 'lm:1234', or a bare numeric icon_name."""
    n = name.strip().lower()
    if n.startswith("lametric:"):
        n = n[len("lametric:"):]
    elif n.startswith("lm:"):
        n = n[len("lm:"):]
    return n if n.isdigit() else None


def _load_icon_asset_from_file(path: str) -> Optional[IconAsset]:
    try:
        im = Image.open(path)
    except (OSError, ValueError):
        return None
    frames: List[Image.Image] = []
    durations: List[int] = []
    try:
        for frame in ImageSequence.Iterator(im):
            rgba = frame.convert("RGBA").resize(
                (LAMETRIC_ICON_SIZE, LAMETRIC_ICON_SIZE), Image.NEAREST
            )
            frames.append(rgba)
            durations.append(max(20, frame.info.get("duration", 100)))
    except Exception:
        return None
    return IconAsset(frames=frames, frame_durations_ms=durations) if frames else None


def _fetch_lametric_asset(icon_id: str) -> Optional[IconAsset]:
    os.makedirs(LAMETRIC_CACHE_DIR, exist_ok=True)

    # Already cached on disk from a previous run?
    for ext in (".gif", ".png"):
        cache_path = os.path.join(LAMETRIC_CACHE_DIR, f"{icon_id}{ext}")
        if os.path.exists(cache_path):
            asset = _load_icon_asset_from_file(cache_path)
            if asset:
                return asset

    # Fetch the native icon (LaMetric's real asset is 8x8 — NEAREST-scaling
    # it up to 16x16 keeps it crisp/pixel-art instead of blurry)
    for ext in (".gif", ".png"):
        url = f"https://developer.lametric.com/content/apps/icon_thumbs/{icon_id}{ext}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ledmatrix-ha-notifications"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                raw = resp.read()
        except (urllib.error.URLError, OSError):
            continue
        cache_path = os.path.join(LAMETRIC_CACHE_DIR, f"{icon_id}{ext}")
        try:
            with open(cache_path, "wb") as f:
                f.write(raw)
        except OSError:
            pass
        asset = _load_icon_asset_from_file(cache_path)
        if asset:
            return asset
    return None


def _lametric_asset(name: str) -> Optional[IconAsset]:
    lm_id = _lametric_icon_id(name)
    if lm_id is None:
        return None
    if lm_id in _LAMETRIC_ASSET_CACHE:
        return _LAMETRIC_ASSET_CACHE[lm_id]
    asset = _fetch_lametric_asset(lm_id)
    if asset:
        _LAMETRIC_ASSET_CACHE[lm_id] = asset
    return asset


def _paste_rgba_icon(img: Image.Image, frame: Image.Image, ox: int, oy: int, scale: int) -> None:
    if scale != 1:
        w, h = frame.size
        frame = frame.resize((w * scale, h * scale), Image.NEAREST)
    img.paste(frame, (ox, oy), frame)   # frame's own alpha channel used as mask


HA_LOGO_ICON_ID = "69580"   # Home Assistant logo, shown centered on the idle screen


SEV_COLORS: Dict[str, Tuple[int, int, int]] = {
    "default":  (0,   180, 255),
    "info":     (0,   200, 100),
    "warning":  (255, 160, 0),
    "critical": (255, 30,  30),
    "success":  (50,  255, 100),
}

VALID_SEVERITIES = set(SEV_COLORS.keys())
VALID_LAYOUTS    = {"auto", "text_only", "icon_left", "icon_only"}


# ═══════════════════════════════════════════════════════════════════════════════
# Data class
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class HANotification:
    title:      str
    message:    str
    severity:   str = "default"
    icon_name:  str = ""
    icon_color: Optional[Tuple[int, int, int]] = None
    layout:     str = "auto"
    expires_at: float = field(default_factory=lambda: time.time() + 3600)

    def effective_color(self) -> Tuple[int, int, int]:
        if self.icon_color:
            return self.icon_color
        return SEV_COLORS.get(self.severity, SEV_COLORS["default"])


def _parse_payload(data: Dict[str, Any]) -> Optional[HANotification]:
    title   = str(data.get("title",   "")).strip()[:40]
    message = str(data.get("message", "")).strip()[:200]
    if not title:
        return None
    severity = str(data.get("severity", "default")).lower()
    if severity not in VALID_SEVERITIES:
        severity = "default"
    layout = str(data.get("layout", "auto")).lower()
    if layout not in VALID_LAYOUTS:
        layout = "auto"
    icon_name  = str(data.get("icon_name", "")).strip()
    if icon_name:
        _lametric_asset(icon_name)   # pre-fetch/cache off the render path
    raw_color  = data.get("icon_color")
    icon_color = None
    if isinstance(raw_color, list) and len(raw_color) == 3:
        icon_color = tuple(max(0, min(255, int(v))) for v in raw_color)
    ttl = max(5, min(86400, int(data.get("ttl", 3600))))
    return HANotification(
        title=title, message=message, severity=severity,
        icon_name=icon_name, icon_color=icon_color, layout=layout,
        expires_at=time.time() + ttl,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Icon renderer
# ═══════════════════════════════════════════════════════════════════════════════

def draw_icon(img: Image.Image, name: str, ox: int, oy: int,
              color: Tuple[int, int, int], scale: int = 1, brightness: float = 1.0) -> None:
    asset = _lametric_asset(name)
    if asset is not None:
        frame = asset.frame_at(time.time())
        if brightness != 1.0:
            frame = ImageEnhance.Brightness(frame).enhance(brightness)
        _paste_rgba_icon(img, frame, ox, oy, scale)
        return

    rows = ICONS.get(name)          # fall back to hand-drawn mono icons
    if not rows:
        return
    draw = ImageDraw.Draw(img)
    r, g, b = color
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            if ch == "#":
                x0 = ox + rx * scale
                y0 = oy + ry * scale
                if scale == 1:
                    draw.point((x0, y0), fill=(r, g, b))
                else:
                    draw.rectangle([x0, y0, x0+scale-1, y0+scale-1], fill=(r, g, b))


# ═══════════════════════════════════════════════════════════════════════════════
# Plugin
# ═══════════════════════════════════════════════════════════════════════════════

class HANotificationsPlugin(BasePlugin):
    """
    Home Assistant Notifications plugin for LEDMatrix (arcade style).
    Supports HTTP webhooks and MQTT. A new notification interrupts whatever is
    on screen, arrives with a splash, then shows as a card until its message
    has been read.
    """

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)

        # Config
        self.display_duration: float = float(config.get("display_duration", 15))
        self.max_queue:        int   = int(config.get("max_queue", 10))
        self.speed:            float = float(max(10, min(200, int(config.get("crawl_speed", 45) or 45))))
        self.min_seconds:      float = float(max(3, int(config.get("min_seconds", 6) or 6)))
        self.max_seconds:      float = float(max(self.min_seconds, int(config.get("max_seconds", 25) or 25)))
        self.splash:           bool  = bool(config.get("splash", True))
        self.webhook_enabled:  bool  = bool(config.get("webhook_enabled", True))
        self.webhook_port:     int   = int(config.get("webhook_port", 5006))
        self.webhook_secret:   str   = config.get("webhook_secret", "")
        self.mqtt_enabled:     bool  = bool(config.get("mqtt_enabled", False))
        self.mqtt_host:        str   = config.get("mqtt_host", "localhost")
        self.mqtt_port:        int   = int(config.get("mqtt_port", 1883))
        self.mqtt_topic:       str   = config.get("mqtt_topic", "ledmatrix/notify")
        self.mqtt_username:    str   = config.get("mqtt_username", "")
        self.mqtt_password:    str   = config.get("mqtt_password", "")
        self.mqtt_client_id:   str   = config.get("mqtt_client_id", f"ledmatrix-{plugin_id}")

        self.W: int = display_manager.width
        self.H: int = display_manager.height

        here = Path(__file__).resolve().parent
        render.load_faces(str(config.get("message_font", "9x15B")), "9x15B",
                          (str(here.parent.parent / "assets" / "fonts"), "/home/pi/LEDMatrix/assets/fonts"))

        # State
        self._notif_queue: queue.Queue[HANotification] = queue.Queue(maxsize=self.max_queue)
        self._current:     Optional[HANotification]    = None
        self._t0:          float = 0.0          # when the current notification first reached the panel
        self._dur:         float = 0.0          # how long it stays (splash + message)
        self._cycle_done:  bool  = False
        self._mqtt_client: Optional[Any] = None

        # Tells the display controller to render us at 60fps (smooth crawl and splash)
        self.enable_scrolling = True

        if self.webhook_enabled:
            self._start_webhook_server()
        if self.mqtt_enabled:
            self._start_mqtt_client()

        threading.Thread(target=_lametric_asset, args=(f"lametric:{HA_LOGO_ICON_ID}",), daemon=True).start()

        self.logger.info(
            "HA Notifications ready  W=%d H=%d  webhook=%s  mqtt=%s",
            self.W, self.H,
            f":{self.webhook_port}" if self.webhook_enabled else "off",
            f"{self.mqtt_host}:{self.mqtt_port}" if self.mqtt_enabled else "off",
        )

    # ── Queue helper ──────────────────────────────────────────────────────────

    def _request_on_demand(self) -> None:
        """
        Ask display_controller to switch to us RIGHT NOW, overriding whatever
        is currently on screen — including another on-demand session, which
        the passive has_live_priority()/has_live_content() checks can't do
        (the controller skips those entirely while something else is pinned
        on-demand). This writes straight into the same shared cache the
        controller polls every single frame, so it's effectively instant.
        """
        try:
            self.cache_manager.set(
                "display_on_demand_request",
                {
                    "request_id": str(uuid.uuid4()),
                    "action": "start",
                    "plugin_id": self.plugin_id,
                    "mode": "ha_notifications",
                    "duration": self.display_duration,
                },
                ttl=3600,
            )
        except Exception as e:
            self.logger.warning("Failed to request on-demand switch: %s", e)

    def _enqueue(self, notif: HANotification) -> None:
        # Drop anything still waiting — for a status feed (like a light's
        # on/off state), only the newest notification matters; stale queued
        # ones would just delay it further.
        while not self._notif_queue.empty():
            try:
                self._notif_queue.get_nowait()
            except queue.Empty:
                break
        try:
            self._notif_queue.put_nowait(notif)
        except queue.Full:
            pass
        # Interrupt whatever's currently on screen — don't wait for its
        # crawl/duration to finish before showing the new one.
        self._current    = None
        self._cycle_done = True
        self._request_on_demand()
        self.logger.info("Queued [%s] %s (interrupting current)", notif.severity, notif.title)

    # ── Webhook ───────────────────────────────────────────────────────────────

    def _start_webhook_server(self) -> None:
        if not FLASK_AVAILABLE:
            self.logger.warning("Flask not installed — webhook disabled")
            return
        app    = Flask(f"ha_notifications_{self.plugin_id}")
        secret = self.webhook_secret

        def _auth_ok():
            return (not secret) or request.headers.get("X-Matrix-Token", "") == secret

        @app.route("/notify", methods=["POST"])
        def notify():
            if not _auth_ok():
                return jsonify({"error": "Unauthorized"}), 401
            try:
                data = request.get_json(force=True) or {}
            except Exception:
                return jsonify({"error": "Invalid JSON"}), 400
            notif = _parse_payload(data)
            if not notif:
                return jsonify({"error": "title required"}), 400
            self._enqueue(notif)
            return jsonify({"status": "queued"}), 200

        @app.route("/status", methods=["GET"])
        def status():
            return jsonify({"status": "ok", "queue": self._notif_queue.qsize()}), 200

        @app.route("/icons", methods=["GET"])
        def icons():
            return jsonify({"icons": sorted(ICONS.keys())}), 200

        @app.route("/frame.png", methods=["GET"])
        def frame_png():
            """The panel's current notification frame, upscaled (for checking the look)."""
            frame = self.compose(tick=False)
            buf = BytesIO()
            frame.resize((frame.width * 6, frame.height * 6), Image.NEAREST).save(buf, format="PNG")
            return Response(buf.getvalue(), mimetype="image/png")

        @app.route("/lametric/preview", methods=["GET"])
        def lametric_preview():
            """
            Fetch, cache, and render a LaMetric icon so you can eyeball it
            before wiring the ID into an HA automation:
              http://<PI_IP>:5006/lametric/preview?id=8149
            """
            icon_id = request.args.get("id", "").strip()
            if not icon_id.isdigit():
                return jsonify({"error": "id must be numeric"}), 400
            asset = _lametric_asset(f"lametric:{icon_id}")
            if asset is None:
                return jsonify({"error": "icon not found or fetch failed"}), 404
            frame = asset.frame_at(time.time())
            preview = frame.resize((frame.width * 8, frame.height * 8), Image.NEAREST)
            buf = BytesIO()
            preview.save(buf, format="PNG")
            return Response(buf.getvalue(), mimetype="image/png")

        def _run():
            import logging as _log
            _log.getLogger("werkzeug").setLevel(_log.ERROR)
            app.run(host="0.0.0.0", port=self.webhook_port, debug=False, use_reloader=False)

        threading.Thread(target=_run, daemon=True, name="ha-notify-webhook").start()
        self.logger.info("Webhook server started on port %d", self.webhook_port)

    # ── MQTT ──────────────────────────────────────────────────────────────────

    def _start_mqtt_client(self) -> None:
        if not MQTT_AVAILABLE:
            self.logger.warning("paho-mqtt not installed — MQTT disabled")
            return
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
                data  = json.loads(msg.payload.decode("utf-8"))
                notif = _parse_payload(data)
                if notif:
                    self._enqueue(notif)
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
                    self.logger.error("MQTT error: %s — retrying in 10s", e)
                    time.sleep(10)

        threading.Thread(target=_run, daemon=True, name="ha-notify-mqtt").start()
        self._mqtt_client = client

    # ── State ─────────────────────────────────────────────────────────────────

    def _layout(self, notif: HANotification) -> str:
        if notif.layout != "auto":
            return notif.layout
        if notif.icon_name and notif.message:
            return "icon_left"
        return "icon_only" if notif.icon_name else "text_only"

    def _duration(self, notif: HANotification) -> float:
        """Splash, then long enough for the message to crawl through once."""
        tiled = self._layout(notif) == "icon_left"
        pw = self.W - ((render.TILE_W + 2) if tiled else 0) - 7
        text = notif.message or notif.title
        if self._layout(notif) == "icon_only":
            need = 0.0
        else:
            need = render.crawl_seconds(text, pw, self.speed)
        show = max(self.min_seconds, min(self.max_seconds, need + 1.0))
        return (render.SPLASH_SECONDS if self.splash else 0.0) + show

    def _advance(self, now: float) -> None:
        """Take the next live notification off the queue, if there is one."""
        while True:
            try:
                nxt = self._notif_queue.get_nowait()
            except queue.Empty:
                return
            if nxt.expires_at < now:
                continue
            self._current, self._t0, self._cycle_done = nxt, now, False
            self._dur = self._duration(nxt)
            self.logger.info("Displaying: %s", nxt.title)
            return

    def _tick(self, now: float) -> None:
        if self._current is not None and (self._current.expires_at < now or now - self._t0 >= self._dur):
            self._current, self._cycle_done = None, True
        if self._current is None:
            self._advance(now)

    # ── BasePlugin ────────────────────────────────────────────────────────────

    def update(self) -> None:
        """Expire stale notifications and load the next one if the current is done."""
        self._tick(time.time())

    def display(self, force_clear: bool = False) -> None:
        try:
            frame = self.compose()
            dm = self.display_manager
            dm.image.paste(frame, (0, 0))
            dm.update_display()
        except Exception as e:
            import traceback
            self.logger.error("Error rendering notification: %s\n%s", e, traceback.format_exc())

    def has_live_content(self) -> bool:
        return not self._notif_queue.empty() or (
            self._current is not None and not self._cycle_done
        )

    def is_cycle_complete(self) -> bool:
        return self._cycle_done

    def reset_cycle_state(self) -> None:
        self._cycle_done = False

    def get_display_duration(self) -> float:
        return self.display_duration

    def cleanup(self) -> None:
        if self._mqtt_client:
            try:
                self._mqtt_client.disconnect()
            except Exception:
                pass
        super().cleanup()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _icon_tile(self, notif: HANotification, color: Tuple[int, int, int], now: float) -> Image.Image:
        tile = render.tile_base(color)
        pulse = 0.75 + 0.25 * math.sin(now * 5) if notif.severity == "critical" else 1.0
        draw_icon(tile, notif.icon_name, ox=0, oy=0, color=color, scale=2, brightness=pulse)
        return tile

    def compose(self, now: Optional[float] = None, tick: bool = True) -> Image.Image:
        """Build the next frame (separate from display() so it can be tested).

        ``tick=False`` only looks: it never takes a notification off the queue or ends one,
        so the /frame.png preview on a web thread cannot disturb what the panel shows."""
        now = time.time() if now is None else now
        if tick:
            self._tick(now)
        notif = self._current
        if notif is None:
            return self._fit(self._idle(now))

        color = SEV_COLORS.get(notif.severity, SEV_COLORS["default"])
        t = now - self._t0
        if self.splash and t < render.SPLASH_SECONDS:
            return self._fit(render.splash(notif.severity, color, t))
        t -= render.SPLASH_SECONDS if self.splash else 0.0

        layout = self._layout(notif)
        if layout == "icon_only":
            icon = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            draw_icon(icon, notif.icon_name, ox=0, oy=0, color=notif.effective_color(), scale=2,
                      brightness=0.7 + 0.3 * math.sin(now * 3))
            return self._fit(render.icon_only(notif.severity, color, icon, now))
        tile = self._icon_tile(notif, notif.effective_color(), now) if layout == "icon_left" else None
        return self._fit(render.card(notif.severity, color, notif.title, notif.message, tile, t, now, self.speed))

    def _idle(self, now: float) -> Image.Image:
        asset = _lametric_asset(f"lametric:{HA_LOGO_ICON_ID}")
        logo = None
        if asset:
            frame = asset.frame_at(now)
            logo = frame.resize((frame.width * 2, frame.height * 2), Image.NEAREST)
        return render.idle(logo, now)

    def _fit(self, frame: Image.Image) -> Image.Image:
        if (self.W, self.H) != frame.size:
            canvas = Image.new("RGB", (self.W, self.H), (0, 0, 0))
            canvas.paste(frame, ((self.W - frame.width) // 2, (self.H - frame.height) // 2))
            frame = canvas
        return frame

    def validate_config(self) -> bool:
        if not super().validate_config():
            return False
        port = self.config.get("webhook_port", 5006)
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            self.logger.error("webhook_port must be 1024-65535")
            return False
        return True
