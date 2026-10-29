"""The ESPN fetch layer: Eastern-day maths, one request per day, per-day freshness, fallbacks."""

import calendar
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nbalive_data as data  # noqa: E402
import nbalive_demo as demo  # noqa: E402


def utc(y, mo, d, h=0, mi=0):
    return float(calendar.timegm((y, mo, d, h, mi, 0)))


class Cache:
    def __init__(self):
        self.store = {}

    def get(self, key, max_age=None):
        return self.store.get(key)

    def set(self, key, value, ttl=None):
        self.store[key] = value


class Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.payload


class Session:
    """Answers each ``dates`` value from a dict, else the default payload."""

    def __init__(self, by_day=None, default=None, fail=False):
        self.by_day, self.default, self.fail = by_day or {}, default if default is not None else {"events": []}, fail
        self.requests, self.headers = [], {}

    def get(self, url, params=None, timeout=None, stream=False):
        self.requests.append((url, dict(params or {})))
        if self.fail:
            raise ConnectionError("offline")
        return Resp(self.by_day.get((params or {}).get("dates"), self.default))


def make(session, cache=None):
    return data.NbaLiveData(cache or Cache(), None, "nba-live-court", 5.0, session=session)


# --- Eastern days ---------------------------------------------------------------

@pytest.mark.parametrize("when,expected", [
    (utc(2026, 1, 15, 3, 0), "20260114"),     # 10 pm EST the evening before
    (utc(2026, 1, 15, 5, 0), "20260115"),     # midnight EST
    (utc(2026, 7, 1, 3, 30), "20260630"),     # 11:30 pm EDT
    (utc(2026, 7, 1, 4, 0), "20260701"),
    (utc(2026, 12, 31, 20, 0), "20261231"),
    (utc(2027, 1, 1, 4, 59), "20261231"),
])
def test_espn_day_is_the_us_eastern_calendar_day(when, expected):
    assert data.espn_day(when) == expected


def test_espn_day_offsets_cross_month_and_year_ends():
    assert data.espn_day(utc(2026, 12, 31, 20), 1) == "20270101"
    assert data.espn_day(utc(2026, 3, 1, 20), -1) == "20260228"
    assert data.espn_day(utc(2028, 3, 1, 20), -1) == "20280229"


def test_daylight_saving_switches_on_the_right_instants():
    assert data.eastern_offset_hours(utc(2026, 3, 8, 6, 59)) == -5   # 1:59 am EST, the second Sunday of March
    assert data.eastern_offset_hours(utc(2026, 3, 8, 7, 0)) == -4    # 3:00 am EDT
    assert data.eastern_offset_hours(utc(2026, 11, 1, 5, 59)) == -4  # 1:59 am EDT, the first Sunday of November
    assert data.eastern_offset_hours(utc(2026, 11, 1, 6, 0)) == -5
    assert data.eastern_offset_hours(utc(2026, 7, 4, 12)) == -4 and data.eastern_offset_hours(utc(2026, 1, 4, 12)) == -5


# --- fetching ---------------------------------------------------------------------

NOW = utc(2026, 1, 15, 18, 0)  # 1 pm Eastern


def test_games_asks_for_three_single_days_never_a_range():
    s = Session()
    make(s).games(25.0, now=NOW)
    days = [p["dates"] for _, p in s.requests]
    assert days == ["20260114", "20260115", "20260116"]
    assert all("-" not in d for d in days)
    assert all(url == data.ESPN_SCOREBOARD_URL for url, _ in s.requests)
    assert data.ESPN_SCOREBOARD_URL.endswith("/basketball/nba/scoreboard")


def test_modes_that_are_off_skip_their_day():
    s = Session()
    make(s).games(25.0, now=NOW, yesterday=False, tomorrow=False)
    assert [p["dates"] for _, p in s.requests] == ["20260115"]


def test_games_from_all_days_merge_sorted_and_deduplicated():
    live = demo.scoreboard(0, now=NOW)
    final = {"events": [live["events"][3]]}            # n4
    s = Session({"20260114": final, "20260115": {"events": live["events"][:4]}, "20260116": {"events": live["events"][4:]}})
    games = make(s).games(25.0, now=NOW)
    assert sorted(g["id"] for g in games) == ["n1", "n2", "n3", "n4", "n5", "n6"]   # n4 came twice
    starts = [g["start"] or 0 for g in games]
    assert starts == sorted(starts)


def test_fresh_days_are_not_refetched():
    s = Session()
    d = make(s)
    d.games(25.0, now=NOW)
    d.games(25.0, now=NOW + 10)
    assert len(s.requests) == 3


def test_today_refreshes_at_the_live_rate_but_a_quiet_yesterday_and_tomorrow_do_not():
    s = Session()
    d = make(s)
    d.games(25.0, now=NOW)
    d.games(25.0, now=NOW + 60)
    assert [p["dates"] for _, p in s.requests[3:]] == ["20260115"]     # only today again
    d.games(25.0, now=NOW + 1000)
    assert {p["dates"] for _, p in s.requests[4:]} == {"20260114", "20260115", "20260116"}  # past the 15 minute quiet age


def test_a_yesterday_with_a_live_game_is_polled_at_the_live_rate():
    """A 10 pm Eastern tip-off is still 'yesterday' at 1 am."""
    live = {"events": [demo.scoreboard(0, now=NOW)["events"][0]]}
    s = Session({"20260114": live})
    d = make(s)
    d.games(25.0, now=NOW)
    d.games(25.0, now=NOW + 60)
    assert [p["dates"] for _, p in s.requests[3:]] == ["20260114", "20260115"]


def test_failed_fetch_falls_back_to_the_last_good_copy_and_reports():
    cache = Cache()
    good = make(Session(default=demo.scoreboard(0, now=NOW)), cache)
    assert len(good.games(25.0, now=NOW)) == 6
    bad = make(Session(fail=True), cache)
    assert len(bad.games(25.0, now=NOW + 5000)) == 6
    assert "ConnectionError" in bad.last_error


def test_everything_failing_with_no_cache_is_an_empty_slate_not_an_error():
    d = make(Session(fail=True))
    assert d.games(25.0, now=NOW) == [] and "ConnectionError" in d.last_error


def test_a_non_object_payload_counts_as_a_failure():
    d = make(Session(default=["nope"]))
    assert d.games(25.0, now=NOW) == [] and "not a JSON object" in d.last_error


def test_a_success_clears_the_last_error():
    cache = Cache()
    make(Session(fail=True), cache).games(25.0, now=NOW)
    ok = make(Session(default=demo.scoreboard(0, now=NOW)), cache)
    ok.last_error = "old"
    ok.games(25.0, now=NOW)
    assert ok.last_error is None


def test_keys_are_namespaced_with_the_plugin_id_and_the_day():
    cache = Cache()
    make(Session(), cache).games(25.0, now=NOW)
    assert sorted(cache.store) == ["nba-live-court:scoreboard:20260114", "nba-live-court:scoreboard:20260115",
                                   "nba-live-court:scoreboard:20260116"]


def test_a_broken_cache_never_breaks_a_poll():
    class Broken(Cache):
        def get(self, key, max_age=None):
            raise OSError("disk")

        def set(self, key, value, ttl=None):
            raise OSError("disk")

    d = make(Session(default=demo.scoreboard(0, now=NOW)), Broken())
    assert len(d.games(25.0, now=NOW)) == 6
