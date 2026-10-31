"""The plugin as the display controller sees it, against a minimal stand-in for the core.

Uses the real core's BasePlugin when it is importable; otherwise a tiny stub, so
these tests run from a bare checkout of this repo.
"""

import os
import sys
import types

import pytest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:  # pragma: no cover - depends on the environment
    import src.plugin_system.base_plugin  # noqa: F401
except ImportError:
    import logging

    class _Base:
        def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
            self.plugin_id, self.config = plugin_id, config
            self.display_manager, self.cache_manager, self.plugin_manager = display_manager, cache_manager, plugin_manager
            self.logger = logging.getLogger(plugin_id)
            self.enabled = config.get("enabled", True)

        def on_config_change(self, new_config):
            self.config = new_config

        def get_info(self):
            return {"id": self.plugin_id}

        def cleanup(self):
            pass

    for name in ("src", "src.plugin_system", "src.plugin_system.base_plugin"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["src.plugin_system.base_plugin"].BasePlugin = _Base

import manager as manager_mod  # noqa: E402
import nbalive_model as model  # noqa: E402
import nbalive_demo as demo  # noqa: E402


class FakeDisplay:
    def __init__(self, width=128, height=64):
        self.width, self.height = width, height
        self.image = Image.new("RGB", (width, height))
        self.pushes = 0

    def update_display(self):
        self.pushes += 1


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key, max_age=None):
        return self.store.get(key)

    def set(self, key, value, ttl=None):
        self.store[key] = value


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeImageResponse:
    def __init__(self, body):
        self.body, self.headers = body, {"Content-Length": str(len(body))}

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]

    def close(self):
        pass


class FakeSession:
    def __init__(self, payload):
        self.payload, self.calls, self.headers, self.urls = payload, 0, {}, []

    def get(self, url, params=None, timeout=None, stream=False):
        self.calls += 1
        self.urls.append(url)
        self.params = getattr(self, "params", [])
        self.params.append(params)
        if isinstance(self.payload, Exception):
            raise self.payload
        if "headshots" in url:
            return FakeImageResponse(demo.headshot_png(len(self.urls)))
        return FakeResponse(self.payload)

    def close(self):
        pass


SLATE = demo.scoreboard(0)
NOW = 1_000_000.0


def make(config=None, payload=SLATE, size=(128, 64)):
    cfg = {"enabled": True, "favorite_teams": [], "lock_to_favorites": False,
           "advanced": {"animations": False, "headshot_downloads": False}}
    for key, value in (config or {}).items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    plugin = manager_mod.NbaLiveCourtPlugin("nba-live-court", cfg, FakeDisplay(*size), FakeCache(), types.SimpleNamespace())
    plugin.data.session = FakeSession(payload)
    return plugin


def live_ids(plugin):
    return [g["id"] for g in plugin.live]


def kinds(plugin, mode=None):
    return [i["kind"] for i in plugin.items[mode or manager_mod.LIVE_MODE]]


def clear_scoreboards(plugin):
    for key in [k for k in plugin.data.cache.store if ":scoreboard:" in k]:
        del plugin.data.cache.store[key]


def score_now(plugin, step=3, now=None):
    """Feed the plugin the slate in which the Lakers have just gone on a 9-0 run and the lead."""
    clear_scoreboards(plugin)
    plugin.data.session = FakeSession(demo.scoreboard(step))
    plugin._refresh(manager_mod.time.time() if now is None else now)


def n2(plugin):
    return next(g for g in plugin.all_games if g["id"] == "n2")


# --- the favorites lock ------------------------------------------------------

def test_no_favorites_shows_every_live_game():
    p = make()
    p.update()
    assert sorted(live_ids(p)) == ["n1", "n2", "n3"]
    assert p.has_live_content()


def test_lock_shows_only_favorites():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True})
    p.update()
    assert live_ids(p) == ["n2"]
    assert p.get_info()["live_matchups"] == ["DEN@LAL"]


def test_lock_with_idle_favorite_returns_false_so_the_core_moves_on():
    p = make({"favorite_teams": ["GS"], "lock_to_favorites": True})  # GS's game is final, not live
    p.update()
    assert p.live == [] and p.items[manager_mod.LIVE_MODE] == [] and not p.has_live_content()
    assert p.display(display_mode=manager_mod.LIVE_MODE) is False
    assert p.display_manager.pushes == 0
    # GS's *recent* game still shows under the recent mode -- the lock is per
    # favorite team, not "only ever show live content".
    assert p.display(display_mode=manager_mod.RECENT_MODE, force_clear=True) is True


def test_lock_fallback_shows_others_until_favorite_plays():
    p = make({"favorite_teams": ["GS"], "lock_to_favorites": True, "lock_fallback_to_all": True})
    p.update()
    assert sorted(live_ids(p)) == ["n1", "n2", "n3"]


def test_lock_can_be_switched_off_live_via_config_change():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True})
    p.update()
    assert live_ids(p) == ["n2"]
    p.on_config_change({"enabled": True, "favorite_teams": ["LAL"], "lock_to_favorites": False,
                        "advanced": {"animations": False}})
    assert live_ids(p) == ["n2", "n2", "n1", "n3"]  # LAL first with the default 2x boost


def test_exclude_hides_a_game_even_from_the_lock():
    p = make({"favorite_teams": ["LAL", "PHI"], "lock_to_favorites": True, "exclude_teams": ["PHI"]})
    p.update()
    assert live_ids(p) == ["n2"]


def test_favorites_accept_alternate_spellings():
    p = make({"favorite_teams": ["lakers", "LAL"]})
    assert p.favorites == ["LAKERS", "LAL"]
    q = make({"favorite_teams": ["GSW"], "lock_to_favorites": True})
    assert q.favorites == ["GS"]


# --- the arcade screens ------------------------------------------------------

def test_each_game_gets_a_court_screen_then_its_player_cards():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True})
    p.update()
    assert kinds(p) == ["scoreboard", "card", "card"]
    cards = [i["player"]["last"] for i in p.items[manager_mod.LIVE_MODE] if i["kind"] == "card"]
    assert cards == ["Hale", "Okafor"]  # LAL is the favorite: its players lead


def test_cards_per_game_zero_leaves_only_the_court():
    p = make({"cards_per_game": 0})
    p.update()
    assert set(kinds(p)) == {"scoreboard"}


def test_favorites_players_lead_the_cards():
    p = make({"favorite_teams": ["DEN"], "lock_to_favorites": True, "cards_per_game": 1})
    p.update()
    assert p.items[manager_mod.LIVE_MODE][1]["player"]["team"] == "DEN"


def test_screens_rotate_by_dwell_time():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True, "live_game_duration": 10, "card_seconds": 4})
    p.update()
    assert p._locate(manager_mod.LIVE_MODE, 0.0)[0] == 0 and p._locate(manager_mod.LIVE_MODE, 9.9)[0] == 0
    assert p._locate(manager_mod.LIVE_MODE, 10.0)[0] == 1 and p._locate(manager_mod.LIVE_MODE, 14.0)[0] == 2
    assert p._locate(manager_mod.LIVE_MODE, 18.0)[0] == 0  # wraps
    assert p.get_display_duration() == 18.0


def test_display_draws_every_screen_kind():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True, "live_game_duration": 10, "card_seconds": 4})
    p.update()
    demo.seed_headshots(p.headshots, p.live)
    seen = set()
    for elapsed in (1, 11, 15):
        p._mode_started = manager_mod.time.time() - elapsed
        assert p.display() is True
        seen.add(p.display_manager.image.tobytes())
    assert p.display_manager.pushes == 3
    assert len(seen) == 3


def test_display_returns_false_when_disabled():
    p = make({"enabled": False})
    p.update()
    assert p.display() is False


# --- bursts and runs -----------------------------------------------------------

def test_favorite_run_and_lead_change_queues_a_burst_naming_the_scorer():
    p = make({"favorite_teams": ["LAL"]})
    p.update()
    score_now(p)
    assert len(p._alerts) == 1
    a = p._alerts[0]
    assert (a["kind"], a["team"], a["player"]["last"]) == ("LEAD CHANGE", "LAL", "Hale")  # "M.Hale makes driving layup"


def test_the_run_is_tracked_and_flames_on_the_court():
    p = make({"favorite_teams": ["LAL"]})
    p.update()
    assert n2(p)["run"] is None
    score_now(p)
    assert n2(p)["run"] == {"side": "home", "points": 9}
    assert model.run_on_fire(n2(p)) == "home"
    assert next(g for g in p.all_games if g["id"] == "n1")["run"] is None


def test_burst_interrupts_the_rotation_then_hands_back_and_glows():
    p = make({"favorite_teams": ["LAL"], "advanced": {"alert_seconds": 3}})
    p.update()
    score_now(p)
    now = manager_mod.time.time()
    assert p._next_alert(now)["game"] == "n2"
    assert p.display() is True and p._alert is not None
    assert p._next_alert(now + 2)["game"] == "n2"
    assert p._next_alert(now + 4) is None and p._alert is None
    assert p._glow["game"] == "n2" and p._glow["side"] == "home"


def test_non_favorite_run_is_quiet_unless_asked():
    payload = demo.scoreboard(0)
    payload["events"][0]["competitions"][0]["competitors"][0]["score"] = "80"  # PHI 71 -> 80, a 9-0 run
    p = make({"favorite_teams": ["LAL"]})
    p.update()
    clear_scoreboards(p)
    p.data.session = FakeSession(payload)
    p._refresh(manager_mod.time.time() + 1)
    assert p._alerts == []
    q = make({"favorite_teams": ["LAL"], "celebrate_all_teams": True})
    q.update()
    clear_scoreboards(q)
    q.data.session = FakeSession(payload)
    q._refresh(manager_mod.time.time() + 1)
    assert [(a["team"], a["kind"]) for a in q._alerts] == [("PHI", "BIG RUN")]


def test_run_size_is_configurable():
    payload = demo.scoreboard(0)
    payload["events"][0]["competitions"][0]["competitors"][0]["score"] = "76"  # PHI +5
    p = make({"celebrate_all_teams": True, "alert_min_points": 5})
    p.update()
    clear_scoreboards(p)
    p.data.session = FakeSession(payload)
    p._refresh(manager_mod.time.time() + 1)
    assert [a["kind"] for a in p._alerts] == ["BIG RUN"]
    q = make({"celebrate_all_teams": True})  # default 8: five points is nothing
    q.update()
    clear_scoreboards(q)
    q.data.session = FakeSession(payload)
    q._refresh(manager_mod.time.time() + 1)
    assert q._alerts == []


def test_alerts_can_be_turned_off():
    p = make({"favorite_teams": ["LAL"], "score_alerts": False})
    p.update()
    score_now(p)
    assert p._alerts == []
    assert n2(p)["run"] is not None  # the flame does not depend on the bursts


def test_no_favorites_means_every_burst_is_celebrated():
    p = make()
    p.update()
    score_now(p)
    assert [a["team"] for a in p._alerts] == ["LAL"]


def test_locked_out_games_never_burst():
    p = make({"favorite_teams": ["PHI"], "lock_to_favorites": True, "celebrate_all_teams": True})
    p.update()
    score_now(p)  # LAL runs, but the lock hides LAL-DEN
    assert p._alerts == []


def test_a_long_gap_between_polls_is_not_a_run():
    t0 = manager_mod.time.time()
    p = make({"favorite_teams": ["LAL"]})
    p._refresh(t0)
    clear_scoreboards(p)
    p.data.session = FakeSession(demo.scoreboard(3))
    p._refresh(t0 + 900)   # a restart or an outage: those points were not "just scored"
    assert p._alerts == [] and n2(p)["run"] is None


def test_spoiler_delay_holds_back_scores_and_bursts():
    t0 = manager_mod.time.time()
    p = make({"favorite_teams": ["LAL"], "spoiler_delay_seconds": 60})
    p._refresh(t0)
    assert p.live == [] and not p.has_live_content()  # nothing is old enough yet
    p._sync(t0 + 61)
    assert sorted(set(live_ids(p))) == ["n1", "n2", "n3"]  # n2 appears twice: LAL's 2x boost
    clear_scoreboards(p)
    p.data.session = FakeSession(demo.scoreboard(3))
    p._refresh(t0 + 62)
    p._sync(t0 + 62)
    assert n2(p)["home"]["score"] == 95  # still the old score
    assert p._alerts == []
    p._sync(t0 + 130)
    assert n2(p)["home"]["score"] == 104
    assert len(p._alerts) == 1


# --- photos ---------------------------------------------------------------------

def test_update_downloads_photos_for_the_cards_on_screen():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True, "advanced": {"headshot_downloads": True}})
    p.update()
    urls = [u for u in p.data.session.urls if "headshots" in u]
    assert urls and all(u.startswith("https://a.espncdn.com/") for u in urls)
    star = p.items[manager_mod.LIVE_MODE][1]["player"]
    assert p.headshots.has_image(star["id"], manager_mod.time.time())
    assert p.headshots.portrait(star, 24, 30).size == (24, 30)


def test_photo_downloads_are_bounded_and_only_from_espn():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True, "cards_per_game": 4, "advanced": {"headshot_downloads": True}})
    p.update()
    assert len([u for u in p.data.session.urls if "headshots" in u]) <= 4
    evil = dict(p.items[manager_mod.LIVE_MODE][1]["player"], id="999", headshot_url="https://evil.example.com/x.png")
    p.data.session.urls.clear()
    assert p.headshots.prefetch([evil]) == 0 and p.data.session.urls == []


def test_photos_off_never_fetches():
    p = make({"favorite_teams": ["LAL"], "show_headshots": False, "advanced": {"headshot_downloads": True}})
    p.update()
    assert not [u for u in p.data.session.urls if "headshots" in u]


# --- robustness -------------------------------------------------------------------

def test_fetch_failure_keeps_last_good_data_and_does_not_raise():
    p = make()
    p.update()
    p.data.session = FakeSession(ConnectionError("offline"))
    for key, entry in p.data.cache.store.items():
        if ":scoreboard:" in key:
            entry["fetched_at"] = 0
    p.update()
    assert sorted(live_ids(p)) == ["n1", "n2", "n3"]
    assert "ConnectionError" in p.get_info()["last_error"]


def test_fetch_failure_with_empty_cache_is_quiet():
    p = make(payload=ConnectionError("offline"))
    p.update()
    assert p.live == [] and p.display() is False


def test_test_mode_needs_no_network_and_fires_a_burst_every_sixth_update():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True, "advanced": {"test_mode": True}})
    p.data.session = FakeSession(ConnectionError("must not be called"))
    for _ in range(3):
        p.update()
    assert p.data.session.calls == 0
    assert live_ids(p) == ["n2"] and len(p._alerts) == 1 and p._alerts[0]["kind"] == "LEAD CHANGE"
    assert p.headshots.portrait(p.items[manager_mod.LIVE_MODE][1]["player"], 24, 30) is not None


def test_test_mode_never_asks_espn_for_the_made_up_players_photos():
    p = make({"favorite_teams": ["LAL"], "advanced": {"test_mode": True, "headshot_downloads": True}})
    p.data.session = FakeSession(ConnectionError("must not be called"))
    p.update()
    assert p.data.session.calls == 0


def test_update_interval_is_fast_when_live_and_slow_when_idle():
    p = make()
    p.update()
    assert p.get_update_interval() == 30.0
    idle = make(payload={"events": [SLATE["events"][3]]})
    idle.update()
    assert idle.get_update_interval() == 600.0


def test_update_interval_speeds_up_just_before_tipoff():
    soon = demo.scoreboard(0, now=manager_mod.time.time())
    soon["events"] = [soon["events"][4]]
    soon["events"][0]["date"] = demo._iso(manager_mod.time.time() + 300)
    p = make(payload=soon)
    p.update()
    assert p.get_update_interval() == 30.0


def test_bad_config_values_are_clamped_not_fatal():
    p = make({"live_game_duration": "abc", "favorite_live_boost": 99, "favorite_teams": "PHI", "cards_per_game": -3,
              "alert_min_points": "x"})
    assert p.field_seconds == 12 and p.boost == 5 and p.cards_per_game == 0 and p.alert_min_points == 8


def test_needs_high_fps_only_with_animation_and_content():
    p = make({"advanced": {"animations": True}})
    assert p.needs_high_fps is False
    p.update()
    assert p.needs_high_fps is True
    p.on_config_change({"enabled": True, "advanced": {"animations": False}})
    assert p.needs_high_fps is False


# --- recent and upcoming games -----------------------------------------------

def test_recent_and_upcoming_modes_are_declared_and_toggleable():
    p = make()
    assert set(p.modes) == {manager_mod.LIVE_MODE, manager_mod.RECENT_MODE, manager_mod.UPCOMING_MODE}
    q = make({"show_recent": False, "show_upcoming": False})
    assert q.modes == [manager_mod.LIVE_MODE]


def test_recent_mode_shows_the_final_game_with_cards():
    p = make({"favorite_teams": ["GS"], "recent_cards_per_game": 1})
    p.update()
    assert [g["id"] for g in p.recent] == ["n4"]
    items = p.items[manager_mod.RECENT_MODE]
    assert items[0]["kind"] == "scoreboard" and items[0]["game"]["final"] is True
    assert items[1]["kind"] == "card" and items[1]["player"]["team"] == "GS"


def test_upcoming_mode_shows_preview_items_only():
    p = make({"favorite_teams": ["NY"], "game_limits": {"favorite_rotation_boost": 1, "other_upcoming_games_to_show": 0}})
    p.update()
    assert [g["id"] for g in p.upcoming] == ["n5"]
    items = p.items[manager_mod.UPCOMING_MODE]
    assert items and all(i["kind"] == "preview" for i in items)


def test_favorite_rotation_boost_repeats_the_favorite_in_recent_and_upcoming():
    p = make({"favorite_teams": ["NY"], "game_limits": {"favorite_rotation_boost": 3}})
    p.update()
    assert [g["id"] for g in p.upcoming].count("n5") == 3


def test_lock_applies_to_recent_and_upcoming_too():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True})
    p.update()
    assert p.items[manager_mod.RECENT_MODE] == []   # LAL has no recent (final) game in the demo slate
    assert p.items[manager_mod.UPCOMING_MODE] == []  # nor an upcoming one


def test_other_games_show_when_lock_is_off():
    p = make({"favorite_teams": ["LAL"], "game_limits": {"other_upcoming_games_to_show": 2}})
    p.update()
    assert [g["id"] for g in p.recent] == ["n4"]
    assert set(g["id"] for g in p.upcoming) == {"n5", "n6"}


def test_other_upcoming_games_default_to_a_single_rotating_slot():
    p = make({"favorite_teams": ["LAL"]})  # default other_upcoming_games_to_show is 1
    p.update()
    assert len(set(g["id"] for g in p.upcoming)) == 1
    assert set(g["id"] for g in p.upcoming) <= {"n5", "n6"}


def test_recent_game_screen_shows_final_not_live():
    p = make({"favorite_teams": ["GS"]})
    p.update()
    assert p.display(display_mode=manager_mod.RECENT_MODE, force_clear=True) is True
    assert p.display_manager.pushes == 1


def test_upcoming_screen_draws():
    p = make({"favorite_teams": ["NY"]})
    p.update()
    assert p.display(display_mode=manager_mod.UPCOMING_MODE, force_clear=True) is True


def test_display_mode_gating():
    p = make({"favorite_teams": ["LAL"], "lock_to_favorites": True})  # only live has content
    p.update()
    assert p.display(display_mode=manager_mod.LIVE_MODE, force_clear=True) is True
    assert p.display(display_mode=manager_mod.RECENT_MODE, force_clear=True) is False
    assert p.display(display_mode=manager_mod.UPCOMING_MODE, force_clear=True) is False
    assert p.display(display_mode="nba_bogus") is False


def test_get_display_duration_reflects_the_mode_last_shown():
    p = make({"favorite_teams": ["NY"], "recent_game_duration": 10, "upcoming_game_duration": 8})
    p.update()
    p.display(display_mode=manager_mod.UPCOMING_MODE, force_clear=True)
    assert p.get_display_duration() == 8.0 * len(p.items[manager_mod.UPCOMING_MODE])


def test_exclude_hides_a_team_from_recent_and_upcoming():
    p = make({"exclude_teams": ["GS"]})
    p.update()
    assert p.recent == []


def test_all_three_modes_cost_three_scoreboard_days_and_no_more():
    p = make({"favorite_teams": ["GS", "NY"]})
    p.update()
    assert p.data.session.calls == 3  # yesterday, today, tomorrow
    assert p.recent and p.upcoming
    days = sorted(prm["dates"] for prm in p.data.session.params)
    assert len(set(days)) == 3 and all(len(day) == 8 and day.isdigit() for day in days)


def test_live_only_asks_for_today():
    p = make({"show_recent": False, "show_upcoming": False})
    p.update()
    assert p.data.session.calls == 1


def test_info_reports_the_slate():
    p = make({"favorite_teams": ["LAL"]})
    p.update()
    info = p.get_info()
    assert info["games_fetched"] == 6 and info["games_live"] == 3 and info["favorite_teams"] == ["LAL"]
    assert info["recent_matchups"] == ["OKC@GS"] and p.get_live_modes() == [manager_mod.LIVE_MODE]


# ---- Govee lights follow the score ---------------------------------------------------------

class GoveeRecorder:
    def __init__(self):
        self.calls = []

    def trigger(self, event, primary="ffffff", accent="ffffff", label=""):
        self.calls.append((event, primary, accent))
        return True

    def configure(self, cfg):
        pass

    def close(self):
        pass


def govee_scene(favorites, **extra):
    probe = make()
    probe.update()
    score_now(probe)
    alert = probe._alerts[0]
    p = make({"favorite_teams": [t for t in favorites(alert)], **extra})
    p.govee = GoveeRecorder()
    p.update()
    score_now(p)
    return p, alert


def test_govee_fires_in_the_scoring_teams_colours():
    p, a = govee_scene(lambda a: [a["team"]])
    assert len(p.govee.calls) == 1
    event, primary, accent = p.govee.calls[0]
    assert event in ("score", "big_score")
    assert (primary, accent) == (a["primary"], a["accent"])


def test_govee_against_when_the_other_side_scores_on_a_favorite():
    p, a = govee_scene(lambda a: [a["opp"]])
    assert [c[0] for c in p.govee.calls] == ["against"]
    assert p._alerts == []              # still no burst on the panel


def test_govee_quiet_for_games_without_a_favorite():
    p, a = govee_scene(lambda a: ["ZZZ"])
    assert p.govee.calls == []


def test_govee_treats_every_team_as_a_score_when_no_favorites_are_set():
    p, a = govee_scene(lambda a: [])
    assert [c[0] for c in p.govee.calls] in (["score"], ["big_score"])


def test_govee_follows_the_panel_alert_setting():
    p, a = govee_scene(lambda a: [a["team"]], score_alerts=False, govee_score_points=0, govee_run_points=0)
    assert p.govee.calls == []


def test_govee_is_off_by_default_and_a_broken_light_cannot_break_the_plugin():
    p = make({"favorite_teams": ["KC"]})
    assert p.govee.enabled is False
    class Boom(GoveeRecorder):
        def trigger(self, *a, **k):
            raise RuntimeError("light on fire")
    q, a = govee_scene(lambda a: [a["team"]])
    q.govee = Boom()
    score_now(q)                        # must not raise


# ---- NBA lights: baskets and runs --------------------------------------------------------------

def basket_scene(config=None, gain=3, side="home", old_gain=0, favorites="side"):
    """A live game where `side` gains `gain` points between two updates (after `old_gain` earlier unanswered points)."""
    import copy
    probe = make()
    probe.update()
    live = [g for g in probe.all_games if g["live"]][0]
    fav = {"side": [live[side]["abbr"]], "other": [live["away" if side == "home" else "home"]["abbr"]], "none": []}[favorites]
    p = make({"favorite_teams": fav, **(config or {})})
    p.govee = GoveeRecorder()
    p.update()
    live = [g for g in p.all_games if g["live"]][0]
    prev = copy.deepcopy(live)
    cur = copy.deepcopy(live)
    cur[side]["score"] += gain
    p._previous = {prev["id"]: prev}
    p._previous_ts = 100.0
    p._runs = {prev["id"]: {"side": side, "points": old_gain}} if old_gain else {}
    p._detect_scores([cur], 101.0, 130.0)
    return p, cur


def test_nba_a_three_flashes_the_score_effect_in_team_colours():
    p, game = basket_scene(gain=3)
    assert [c[0] for c in p.govee.calls] == ["score"]
    _, primary, accent = p.govee.calls[0]
    assert (primary, accent) == (game["home"]["color"], game["home"]["alt_color"])


def test_nba_a_two_pointer_is_quiet_by_default_but_can_be_asked_for():
    p, _ = basket_scene(gain=2)
    assert p.govee.calls == []
    q, _ = basket_scene({"govee_score_points": 2}, gain=2)
    assert [c[0] for c in q.govee.calls] == ["score"]


def test_nba_score_flash_can_be_switched_off():
    p, _ = basket_scene({"govee_score_points": 0, "govee_run_points": 0}, gain=3)
    assert p.govee.calls == []


def test_nba_an_eight_point_run_is_one_big_moment_not_two():
    p, _ = basket_scene(gain=8)             # the run check and the on-panel big-run alert both see it
    assert [c[0] for c in p.govee.calls] == ["big_score"]


def test_nba_a_run_only_counts_when_it_first_reaches_the_size():
    p, _ = basket_scene({"alert_min_points": 0}, gain=2, old_gain=8)    # already 8, now 10: not a new big moment
    assert p.govee.calls == []
    q, _ = basket_scene({"alert_min_points": 0}, gain=2, old_gain=6)    # 6 then +2 reaches 8
    assert [c[0] for c in q.govee.calls] == ["big_score"]


def test_nba_the_other_team_scoring_never_flashes_for_baskets():
    p, _ = basket_scene(gain=3, favorites="other")
    assert p.govee.calls == []


def test_nba_with_no_favorites_every_team_counts():
    p, _ = basket_scene(gain=3, favorites="none")
    assert [c[0] for c in p.govee.calls] == ["score"]


def test_nba_basket_flash_does_not_need_the_panel_alerts():
    p, _ = basket_scene({"score_alerts": False}, gain=3)
    assert [c[0] for c in p.govee.calls] == ["score"]


def test_nba_labels_say_what_happened():
    p, game = basket_scene(gain=3)
    assert p.govee.calls[0][0] == "score"
    seen = []
    p2, _ = basket_scene(gain=3)
    p2.govee.trigger = lambda event, primary="", accent="", label="": seen.append(label)
    p2._previous = {game["id"]: __import__("copy").deepcopy(game)}
    g2 = __import__("copy").deepcopy(game)
    g2["home"]["score"] += 3
    p2._detect_scores([g2], 102.0, 131.0)
    assert seen and "+3" in seen[0]
