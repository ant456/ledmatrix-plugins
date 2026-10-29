"""The one network call NFL Live Field makes: ESPN's public NFL scoreboard.

The payload is cached as ``{"fetched_at": epoch, "data": ...}`` and read back
with no expiry so this module judges freshness itself and can fall back to the
last good copy when a fetch fails. Keys are namespaced with the plugin id.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import requests

import nfllive_model as model

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
USER_AGENT = "LEDMatrix-NFLLiveField/1.0 (+https://github.com/ChuckBuilds/ledmatrix-plugins)"


class NflLiveData:
    """Fetches, caches and parses the scoreboard."""

    def __init__(self, cache_manager, logger: Optional[logging.Logger], plugin_id: str,
                 timeout: float = 10.0, session: Optional[requests.Session] = None):
        self.cache = cache_manager
        self.logger = logger or logging.getLogger(__name__)
        self.plugin_id = plugin_id
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.last_error: Optional[str] = None

    @property
    def _key(self) -> str:
        return f"{self.plugin_id}:scoreboard"

    # generic cache helpers, shared with the headshot store ---------------
    def key(self, *parts: Any) -> str:
        return ":".join([self.plugin_id] + [str(p) for p in parts])

    def read(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            entry = self.cache.get(key)
        except Exception:  # a broken cache must never break a frame
            return None
        return entry if isinstance(entry, dict) and "data" in entry else None

    def write(self, key: str, data: Any, now: Optional[float] = None) -> None:
        try:
            self.cache.set(key, {"fetched_at": time.time() if now is None else now, "data": data})
        except Exception as exc:  # noqa: BLE001 - caching is best effort
            self.logger.debug("Cache write failed for %s: %s", key, exc)

    def _read(self) -> Optional[Dict[str, Any]]:
        try:
            entry = self.cache.get(self._key)
        except Exception:  # a broken cache must never break a poll
            return None
        return entry if isinstance(entry, dict) and "data" in entry else None

    def games(self, max_age: float, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Parsed games, re-fetched when the cached copy is older than ``max_age``.

        On a failed fetch the last good copy is returned (however old); with
        none, an empty list. ``last_error`` says what went wrong.
        """
        now = time.time() if now is None else now
        cached = self._read()
        if cached and now - float(cached.get("fetched_at", 0)) < max_age:
            return model.parse_scoreboard(cached["data"])
        try:
            response = self.session.get(ESPN_SCOREBOARD_URL, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("scoreboard was not a JSON object")
            self.cache.set(self._key, {"fetched_at": now, "data": payload})
            self.last_error = None
            return model.parse_scoreboard(payload)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.logger.warning("NFL scoreboard fetch failed (%s); using last good copy", self.last_error)
            return model.parse_scoreboard(cached["data"]) if cached else []
