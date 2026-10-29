"""Sky Weather data: what is asked of Home Assistant and how the answers are read.

Everything comes from Home Assistant, so there are no extra keys to manage:

* a ``weather`` entity            current conditions (state + attributes)
* ``weather.get_forecasts``       hourly and daily forecast (a service call, refreshed every few minutes)
* ``sun.sun``                     live sun elevation, to colour the sky
* an NWS alerts sensor            ``Alerts`` attribute: the active alerts (``nws_alerts`` integration)
* ``/api/config``                 latitude, longitude and time zone, so sunrise and the Moon are for *this* place

Parsing is split from fetching so it can be tested without a network.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

UTC = timezone.utc
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")

DEFAULTS = {
    "weather_entity": "weather.openweathermap",
    "sun_entity": "sun.sun",
    "alerts_entity": "sensor.nws_alerts_alerts",
}

# HA condition -> how the scene paints it. cloud: minimum cloud cover 0..1; rain 0/1/2; snow 0/1; ...
CONDITIONS: Dict[str, Dict[str, Any]] = {
    "sunny":            {"label": "SUNNY",     "cloud": 0.0, "rain": 0, "snow": 0, "storm": 0, "fog": 0, "hail": 0},
    "clear-night":      {"label": "CLEAR",     "cloud": 0.0, "rain": 0, "snow": 0, "storm": 0, "fog": 0, "hail": 0},
    "partlycloudy":     {"label": "PT CLOUDY",  "cloud": 0.4, "rain": 0, "snow": 0, "storm": 0, "fog": 0, "hail": 0},
    "cloudy":           {"label": "CLOUDY",    "cloud": 0.9, "rain": 0, "snow": 0, "storm": 0, "fog": 0, "hail": 0},
    "rainy":            {"label": "RAIN",      "cloud": 0.9, "rain": 1, "snow": 0, "storm": 0, "fog": 0, "hail": 0},
    "pouring":          {"label": "HVY RAIN",  "cloud": 1.0, "rain": 2, "snow": 0, "storm": 0, "fog": 0, "hail": 0},
    "lightning":        {"label": "STORMY",    "cloud": 1.0, "rain": 0, "snow": 0, "storm": 1, "fog": 0, "hail": 0},
    "lightning-rainy":  {"label": "T-STORM",   "cloud": 1.0, "rain": 2, "snow": 0, "storm": 1, "fog": 0, "hail": 0},
    "snowy":            {"label": "SNOW",      "cloud": 0.9, "rain": 0, "snow": 1, "storm": 0, "fog": 0, "hail": 0},
    "snowy-rainy":      {"label": "SLEET",     "cloud": 0.9, "rain": 1, "snow": 1, "storm": 0, "fog": 0, "hail": 0},
    "hail":             {"label": "HAIL",      "cloud": 1.0, "rain": 1, "snow": 0, "storm": 0, "fog": 0, "hail": 1},
    "fog":              {"label": "FOG",       "cloud": 0.1, "rain": 0, "snow": 0, "storm": 0, "fog": 1, "hail": 0},
    "windy":            {"label": "WINDY",     "cloud": 0.1, "rain": 0, "snow": 0, "storm": 0, "fog": 0, "hail": 0},
    "windy-variant":    {"label": "WINDY",     "cloud": 0.5, "rain": 0, "snow": 0, "storm": 0, "fog": 0, "hail": 0},
    "exceptional":      {"label": "EXTREME",   "cloud": 0.8, "rain": 0, "snow": 0, "storm": 0, "fog": 0, "hail": 0},
}
UNKNOWN_COND = {"label": "--", "cloud": 0.0, "rain": 0, "snow": 0, "storm": 0, "fog": 0, "hail": 0}

SEVERITY_RANK = {"extreme": 4, "severe": 3, "moderate": 2, "minor": 1, "unknown": 0}
COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def valid_entity(entity: Any, fallback: str) -> str:
    """Entity ids go into URLs, so only well-formed ones are used."""
    e = str(entity or "").strip().lower()
    return e if _ENTITY.match(e) else fallback


def cond(name: Any) -> Dict[str, Any]:
    return CONDITIONS.get(str(name or "").lower(), UNKNOWN_COND)


def compass(deg: Any) -> str:
    try:
        return COMPASS[int((float(deg) % 360.0) / 22.5 + 0.5) % 16]
    except (TypeError, ValueError):
        return ""


def num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_now(state: Dict[str, Any]) -> Dict[str, Any]:
    """Current conditions from a ``weather.*`` state object."""
    a = state.get("attributes") or {}
    return {
        "cond": str(state.get("state") or "").lower(),
        "temp": num(a.get("temperature")), "feels": num(a.get("apparent_temperature")),
        "unit": str(a.get("temperature_unit") or "°F").replace("°", ""),
        "humidity": num(a.get("humidity")), "cloud": num(a.get("cloud_coverage")),
        "wind": num(a.get("wind_speed")), "gust": num(a.get("wind_gust_speed")),
        "wind_unit": str(a.get("wind_speed_unit") or "mph"), "bearing": num(a.get("wind_bearing")),
        "uv": num(a.get("uv_index")), "pressure": num(a.get("pressure")),
        "pressure_unit": str(a.get("pressure_unit") or ""), "dew": num(a.get("dew_point")),
        "visibility": num(a.get("visibility")), "visibility_unit": str(a.get("visibility_unit") or ""),
    }


def parse_forecast(items: Any, daily: bool) -> List[Dict[str, Any]]:
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        t = parse_dt(it.get("datetime"))
        if t is None:
            continue
        out.append({
            "t": t, "cond": str(it.get("condition") or "").lower(),
            "temp": num(it.get("temperature")), "low": num(it.get("templow")),
            "pop": num(it.get("precipitation_probability")), "wind": num(it.get("wind_speed")),
            "precip": num(it.get("precipitation")), "uv": num(it.get("uv_index")),
        })
    return out


def _key(d: Dict[str, Any], *names: str) -> Any:
    low = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        if n in low and low[n] not in (None, ""):
            return low[n]
    return None


def parse_alerts(attrs: Any) -> List[Dict[str, Any]]:
    """Active alerts from the NWS Alerts sensor's ``Alerts`` attribute, most severe first."""
    raw = None
    if isinstance(attrs, dict):
        raw = _key(attrs, "alerts")
    out = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        event = str(_key(a, "event") or "").strip()
        if not event:
            continue
        sev = str(_key(a, "severity") or "unknown").strip().lower()
        out.append({
            "event": event,
            "severity": sev if sev in SEVERITY_RANK else "unknown",
            "headline": str(_key(a, "headline") or "").strip(),
            "expires": parse_dt(_key(a, "ends", "expires")),
            "urgency": str(_key(a, "urgency") or "").strip().lower(),
        })
    out.sort(key=lambda x: -SEVERITY_RANK.get(x["severity"], 0))
    return out


# ── HA calls ─────────────────────────────────────────────────────────────────────────────────

def _call(base: str, token: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 12.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:                    # noqa: S310 - the user's own HA
        return json.load(r)


def fetch_place(base: str, token: str) -> Dict[str, Any]:
    cfg = _call(base, token, "/api/config")
    return {"lat": num(cfg.get("latitude")), "lon": num(cfg.get("longitude")),
            "tz": str(cfg.get("time_zone") or "")}


def fetch_state(base: str, token: str, entities: Dict[str, str]) -> Dict[str, Any]:
    """Current conditions, sun and alerts. A missing alerts sensor never costs the weather."""
    out: Dict[str, Any] = {}
    out["now"] = parse_now(_call(base, token, "/api/states/" + entities["weather_entity"]))
    try:
        sun = _call(base, token, "/api/states/" + entities["sun_entity"])
        a = sun.get("attributes") or {}
        out["sun_elev"], out["sun_az"] = num(a.get("elevation")), num(a.get("azimuth"))
    except Exception:  # noqa: BLE001
        out["sun_elev"] = out["sun_az"] = None
    try:
        out["alerts"] = parse_alerts((_call(base, token, "/api/states/" + entities["alerts_entity"])).get("attributes"))
        out["alerts_ok"] = True
    except Exception:  # noqa: BLE001
        out["alerts"], out["alerts_ok"] = [], False
    return out


def fetch_forecast(base: str, token: str, entity: str, kind: str) -> List[Dict[str, Any]]:
    r = _call(base, token, "/api/services/weather/get_forecasts?return_response",
              {"entity_id": entity, "type": kind})
    block = (r.get("service_response") or {}).get(entity) or {}
    return parse_forecast(block.get("forecast"), kind == "daily")
