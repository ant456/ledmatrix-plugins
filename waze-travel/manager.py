"""
Waze Travel Time for LEDMatrix  (arcade style, same family as NBA / NFL / NHL Live).

Drive times from Home Assistant's Waze Travel Time sensors, one row per
destination: a status-coloured band (CLEAR / BUSY / HEAVY / JAM), the delay
against free flow, a congestion meter, a trend arrow and the minutes in big
digits. A route that turns heavy interrupts with a burst. When Home Assistant's
sensors are unavailable the panel says so instead of waiting on "Loading".

Free flow comes from, in order: `typical_minutes` on the destination, the sensor
named by `free_flow_entity` (e.g. HERE's "duration" next to "duration in traffic"),
or the shortest time seen for the route (kept in the core cache).

update() starts the HA requests on a worker thread and returns; display() only
draws from memory.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from PIL import Image

from src.plugin_system.base_plugin import BasePlugin

import wazearc_render as render

BURST_SECONDS = 5.0
PAGE_SECONDS = 8.0          # destinations beyond two take turns on screen
TREND_WINDOW = 900.0        # compare with the reading from about 15 minutes ago
KEEP_STALE = 600.0          # a failed refresh keeps the last good time this long
RANK = {"CLEAR": 0, "BUSY": 1, "HEAVY": 2, "JAM": 3}


def classify(minutes: float, typical: Optional[float]) -> Tuple[str, int, float]:
    """(status, minutes over free flow, ratio to free flow)."""
    if not typical or typical < 1:
        return "CLEAR", 0, 1.0
    delta = int(round(minutes - typical))
    ratio = minutes / typical
    if delta < 3 or ratio < 1.15:
        return "CLEAR", max(0, delta), ratio
    if ratio < 1.40:
        return "BUSY", delta, ratio
    if ratio < 1.80:
        return "HEAVY", delta, ratio
    return "JAM", delta, ratio


class Destination:
    def __init__(self, label: str, entity: str, color: Tuple[int, int, int], typical: Optional[float]):
        self.label, self.entity, self.color, self.typical = label, entity, color, typical
        self.free_entity = ""
        self.free_flow: Optional[float] = None
        self.minutes: Optional[float] = None
        self.good_at = 0.0
        self.error: Optional[str] = None
        self.fetched = False
        self.best: Optional[float] = None
        self.samples: Deque[Tuple[float, float]] = deque(maxlen=40)
        self.last_status: Optional[str] = None


def _color(value: Any, default: Tuple[int, int, int]) -> Tuple[int, int, int]:
    try:
        r, g, b = (max(0, min(255, int(v))) for v in value)
        return (r, g, b)
    except (TypeError, ValueError):
        return default


class WazeTravelPlugin(BasePlugin):
    """Arcade drive times."""

    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.W: int = display_manager.width
        self.H: int = display_manager.height
        self._load_config(config)

        here = Path(__file__).resolve().parent
        render.load_faces(str(config.get("number_font", "7x13B")), "9x15B",
                          (str(here.parent.parent / "assets" / "fonts"), "/home/pi/LEDMatrix/assets/fonts"))

        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._last_update = 0.0
        self._burst: Optional[Dict[str, Any]] = None
        self.enable_scrolling = True                       # high-FPS path for the blink and burst
        self.logger.info("Waze Travel ready - %d destinations", len(self.dests))

    # ── config ───────────────────────────────────────────────────────────────

    def _load_config(self, c: Dict[str, Any]) -> None:
        self.ha_url = str(c.get("ha_url", "http://homeassistant.local:8123")).rstrip("/")
        self.ha_token = str(c.get("ha_token", ""))
        self.update_interval = max(30, int(c.get("update_interval", 120) or 120))
        self.alerts = bool(c.get("traffic_alerts", True))
        self.display_duration = float(c.get("display_duration", 20))
        keep = {d.entity: d for d in getattr(self, "dests", [])}
        dests = []
        for i, item in enumerate(c.get("destinations") or []):
            if not isinstance(item, dict) or not item.get("entity"):
                continue
            typical = item.get("typical_minutes")
            try:
                typical = float(typical) if typical else None
            except (TypeError, ValueError):
                typical = None
            dest = keep.get(item["entity"]) or Destination("", item["entity"], (255, 200, 50), None)
            dest.label = str(item.get("label") or "?").upper()[:8]
            dest.color = _color(item.get("color"), (255, 200, 50))
            dest.typical = typical
            dest.free_entity = str(item.get("free_flow_entity") or "").strip()
            dests.append(dest)
        self.dests: List[Destination] = dests

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        super().on_config_change(new_config)
        self.config = new_config
        self._load_config(new_config)
        self._last_update = 0.0

    # ── data (worker thread) ─────────────────────────────────────────────────

    def _cache_key(self, dest: Destination) -> str:
        return "waze-travel:best:%s" % dest.entity

    def _load_best(self, dest: Destination) -> None:
        try:
            entry = self.cache_manager.get(self._cache_key(dest))
            if isinstance(entry, dict) and float(entry.get("best", 0)) > 0:
                dest.best = float(entry["best"])
        except Exception:  # noqa: BLE001 - a broken cache only costs the free-flow memory
            pass

    def _save_best(self, dest: Destination) -> None:
        try:
            self.cache_manager.set(self._cache_key(dest), {"best": dest.best})
        except Exception:  # noqa: BLE001
            pass

    def _fetch(self, dest: Destination, entity: Optional[str] = None) -> float:
        req = urllib.request.Request(
            "%s/api/states/%s" % (self.ha_url, entity or dest.entity),
            headers={"Authorization": "Bearer %s" % self.ha_token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310 - the user's own HA URL
            data = json.loads(resp.read())
        state = str(data.get("state", "")).strip().lower()
        if state in ("", "unavailable", "unknown", "none"):
            raise ValueError("sensor is %s" % (state or "empty"))
        try:
            return float(state)
        except ValueError:
            return float((data.get("attributes") or {}).get("duration"))   # older HA: the minutes live here

    def _refresh_one(self, dest: Destination) -> None:
        now = time.time()
        if dest.best is None and not dest.fetched:
            self._load_best(dest)
        try:
            minutes = self._fetch(dest)
        except Exception as exc:  # noqa: BLE001 - one dead sensor must not stop the other
            with self._lock:
                dest.error = "%s: %s" % (type(exc).__name__, str(exc)[:80])
                dest.fetched = True
            self.logger.warning("Waze sensor %s failed (%s)", dest.entity, dest.error)
            return
        free = None
        if dest.free_entity:
            try:
                free = self._fetch(dest, dest.free_entity)
            except Exception as exc:  # noqa: BLE001 - fall back to the learned free flow
                self.logger.debug("Free-flow sensor %s unavailable: %s", dest.free_entity, exc)
        with self._lock:
            dest.minutes, dest.good_at, dest.error, dest.fetched = minutes, now, None, True
            if free:
                dest.free_flow = free
            dest.samples.append((now, minutes))
            improved = dest.best is None or minutes < dest.best
            if improved:
                dest.best = minutes
            typical = dest.typical or dest.free_flow or dest.best
            status, _, _ = classify(minutes, typical)
            if (self.alerts and dest.last_status is not None and RANK[status] >= 2
                    and RANK[status] > RANK[dest.last_status] and self._burst is None):
                self._burst = {"dest": dest, "t0": 0.0, "pending": True}
            dest.last_status = status
        if improved:
            self._save_best(dest)

    def _refresh_all(self) -> None:
        threads = [threading.Thread(target=self._refresh_one, args=(d,), daemon=True) for d in list(self.dests)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=12)
        self._last_update = time.time()

    def update(self) -> None:
        """Start a refresh and return at once."""
        if self._worker is not None and self._worker.is_alive():
            return
        if time.time() - self._last_update < self.update_interval and self._last_update:
            return
        self._worker = threading.Thread(target=self._refresh_all, daemon=True, name="waze-fetch")
        self._worker.start()

    def get_update_interval(self) -> Optional[float]:
        return float(self.update_interval)

    # ── drawing ──────────────────────────────────────────────────────────────

    def _row(self, dest: Destination, now: float) -> Dict[str, Any]:
        with self._lock:
            minutes, good_at, error, fetched = dest.minutes, dest.good_at, dest.error, dest.fetched
            typical = dest.typical or dest.free_flow or dest.best
            samples = list(dest.samples)
        have = minutes is not None and (error is None or now - good_at < KEEP_STALE)
        if not have:
            return {"label": dest.label, "color": dest.color, "state": "DOWN" if fetched else "NONE",
                    "minutes": 0.0, "status": None, "delta": 0, "ratio": None, "trend": 0}
        status, delta, ratio = classify(minutes, typical)
        past = [m for ts, m in samples if now - ts >= TREND_WINDOW - 300]
        diff = minutes - past[-1] if past else 0.0
        return {"label": dest.label, "color": dest.color, "state": "OK", "minutes": minutes,
                "status": status, "delta": delta, "ratio": ratio,
                "trend": 1 if diff >= 2 else -1 if diff <= -2 else 0}

    def compose(self, now: Optional[float] = None) -> Image.Image:
        now = time.time() if now is None else now
        if not self.dests:
            return self._fit(render.notice("OFFLINE", now))

        burst = self._burst
        if burst is not None:
            if burst["pending"]:
                burst["pending"], burst["t0"] = False, now
            if now - burst["t0"] < BURST_SECONDS:
                return self._fit(render.alert(self._row(burst["dest"], now), now - burst["t0"]))
            with self._lock:
                self._burst = None

        rs = [self._row(d, now) for d in self.dests]
        if all(r["state"] == "NONE" for r in rs):
            return self._fit(render.notice("LOADING", now))
        if all(r["state"] != "OK" for r in rs):
            return self._fit(render.notice("OFFLINE", now))
        pages = [rs[i:i + 2] for i in range(0, len(rs), 2)]
        page = pages[int(now // PAGE_SECONDS) % len(pages)]
        return self._fit(render.rows(page, now))

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
            self.logger.error("Error rendering waze travel: %s\n%s", e, traceback.format_exc())

    def get_display_duration(self) -> float:
        return self.display_duration

    def validate_config(self) -> bool:
        if not self.ha_token:
            self.logger.error("ha_token is required")
            return False
        return bool(self.dests)

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update({"destinations": [
            {"label": d.label, "entity": d.entity, "minutes": d.minutes, "free_flow": d.free_flow,
             "best": d.best, "error": d.error}
            for d in self.dests], "last_update": self._last_update})
        return info

    def cleanup(self) -> None:
        super().cleanup()
