"""The only network use of Lumen Clock, and it is optional: the outdoor temperature and the place (for the
Daylight scene) from Home Assistant. The clock itself never waits on any of it."""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Dict, Optional

_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def valid_entity(entity: Any, fallback: str) -> str:
    e = str(entity or "").strip().lower()
    return e if _ENTITY.match(e) else fallback


def num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _get(base: str, token: str, path: str, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=timeout) as r:                    # noqa: S310 - the user's own HA
        return json.load(r)


def fetch_place(base: str, token: str) -> Dict[str, Any]:
    cfg = _get(base, token, "/api/config")
    return {"lat": num(cfg.get("latitude")), "lon": num(cfg.get("longitude")), "tz": str(cfg.get("time_zone") or "")}


def fetch_temperature(base: str, token: str, entity: str) -> Optional[int]:
    """The weather entity's temperature, rounded, in whatever unit Home Assistant uses; None if it has none."""
    state = _get(base, token, "/api/states/" + entity)
    t = num((state.get("attributes") or {}).get("temperature"))
    return None if t is None else int(round(t))
