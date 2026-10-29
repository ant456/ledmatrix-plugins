"""The one network call NBA Live Court makes: ESPN's public NBA scoreboard.

ESPN rejects ``dates=YYYYMMDD-YYYYMMDD`` ranges (HTTP 400 since 2026-09-15), but
answers a single ``dates=YYYYMMDD`` fine, where the day means the US Eastern
calendar day. So the plugin asks for up to three single days -- yesterday (for
recent finals), today, tomorrow (for upcoming) -- each cached under its own
key with its own freshness rule:

* today: as fast as the live poll;
* yesterday: as fast as the live poll while any of its games is still going
  (a late game that tips off at 10 pm Eastern is still "yesterday" after
  midnight), otherwise rarely;
* tomorrow: rarely.

Each payload is cached as ``{"fetched_at": epoch, "data": ...}`` and read back
with no expiry so this module judges freshness itself and can fall back to the
last good copy when a fetch fails. Keys are namespaced with the plugin id.
"""

import datetime
import logging
import time
from typing import Any, Dict, List, Optional

import requests

import nbalive_model as model

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
USER_AGENT = "LEDMatrix-NBALiveCourt/1.0 (+https://github.com/ChuckBuilds/ledmatrix-plugins)"

#: How long a quiet day's scoreboard is reused before it is asked for again.
QUIET_MAX_AGE = 900.0


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
    """The ``n``-th ``weekday`` (Mon=0) of a month."""
    first = datetime.date(year, month, 1)
    return first + datetime.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _utc(epoch: float) -> datetime.datetime:
    """A naive UTC datetime for an epoch (timezone-aware under the hood, so no deprecation warnings)."""
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).replace(tzinfo=None)


def eastern_offset_hours(epoch: float) -> int:
    """-4 during US daylight time, else -5 (rule-based, so no tz database is needed)."""
    utc = _utc(epoch)
    start = datetime.datetime.combine(_nth_weekday(utc.year, 3, 6, 2), datetime.time(7, 0))   # 2:00 EST
    end = datetime.datetime.combine(_nth_weekday(utc.year, 11, 6, 1), datetime.time(6, 0))    # 2:00 EDT
    return -4 if start <= utc < end else -5


def espn_day(now: float, offset_days: int = 0) -> str:
    """``YYYYMMDD`` of the US Eastern day ``offset_days`` from ``now``'s."""
    eastern = _utc(now + eastern_offset_hours(now) * 3600)
    return (eastern.date() + datetime.timedelta(days=offset_days)).strftime("%Y%m%d")


class NbaLiveData:
    """Fetches, caches and parses the scoreboard days."""

    def __init__(self, cache_manager, logger: Optional[logging.Logger], plugin_id: str,
                 timeout: float = 10.0, session: Optional[requests.Session] = None):
        self.cache = cache_manager
        self.logger = logger or logging.getLogger(__name__)
        self.plugin_id = plugin_id
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.last_error: Optional[str] = None

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

    # one day ---------------------------------------------------------------

    @staticmethod
    def _has_live(payload: Any) -> bool:
        return any(g["live"] for g in model.parse_scoreboard(payload))

    def day(self, day: str, max_age: float, now: float, quiet_age: float = QUIET_MAX_AGE) -> Optional[Dict[str, Any]]:
        """One day's raw payload: the cached copy while it is fresh, else a new fetch.

        ``max_age`` applies while the cached copy has a game in progress (or
        there is none yet); a copy with nothing live is reused for
        ``quiet_age``. A failed fetch falls back to the last good copy, however
        old, and ``last_error`` says what went wrong.
        """
        key = self.key("scoreboard", day)
        cached = self.read(key)
        if cached:
            age = now - float(cached.get("fetched_at", 0))
            limit = max_age if self._has_live(cached["data"]) else max(max_age, quiet_age)
            if 0 <= age < limit:
                return cached["data"]
        try:
            response = self.session.get(ESPN_SCOREBOARD_URL, params={"dates": day}, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("scoreboard was not a JSON object")
            self.write(key, payload, now)
            return payload
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.logger.warning("NBA scoreboard fetch for %s failed (%s); using last good copy", day, self.last_error)
            return cached["data"] if cached else None

    # the slate -------------------------------------------------------------

    def games(self, max_age: float, now: Optional[float] = None, yesterday: bool = True,
              tomorrow: bool = True) -> List[Dict[str, Any]]:
        """Parsed games for yesterday/today/tomorrow (Eastern), de-duplicated by game id."""
        now = time.time() if now is None else now
        days = []
        if yesterday:
            days.append(espn_day(now, -1))
        days.append(espn_day(now, 0))
        if tomorrow:
            days.append(espn_day(now, 1))
        today = espn_day(now, 0)
        self.last_error = None
        seen: Dict[str, Dict[str, Any]] = {}
        failed = 0
        for day in days:
            payload = self.day(day, max_age, now, quiet_age=max_age if day == today else QUIET_MAX_AGE)
            if payload is None:
                failed += 1
                continue
            for game in model.parse_scoreboard(payload):
                seen[game["id"]] = game
        if failed == len(days):
            return []
        # a day that failed but has no cached copy is not an error worth shouting about
        # as long as today answered; last_error stays set for get_info().
        return sorted(seen.values(), key=lambda g: (g.get("start") or 0))
