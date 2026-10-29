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
import nhllive_model as model  # noqa: E402
import nhllive_demo as demo  # noqa: E402


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
    plugin = manager_mod.NhlLiveIcePlugin("nhl-live-ice", cfg, FakeDisplay(*size), FakeCache(), types.SimpleNamespace())
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
    """Feed the plugin the slate in which the Maple Leafs have just scored three goals and taken the lead."""
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
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True})
    p.update()
    assert live_ids(p) == ["n2"]
    assert p.get_info()["live_matchups"] == ["BOS@TOR"]


def test_lock_with_idle_favorite_returns_false_so_the_core_moves_on():
    p = make({"favorite_teams": ["COL"], "lock_to_favorites": True})  # COL's game is final, not live
    p.update()
    assert p.live == [] and p.items[manager_mod.LIVE_MODE] == [] and not p.has_live_content()
    assert p.display(display_mode=manager_mod.LIVE_MODE) is False
    assert p.display_manager.pushes == 0
    # COL's *recent* game still shows under the recent mode -- the lock is per
    # favorite team, not "only ever show live content".
    assert p.display(display_mode=manager_mod.RECENT_MODE, force_clear=True) is True


def test_lock_fallback_shows_others_until_favorite_plays():
    p = make({"favorite_teams": ["COL"], "lock_to_favorites": True, "lock_fallback_to_all": True})
    p.update()
    assert sorted(live_ids(p)) == ["n1", "n2", "n3"]


def test_lock_can_be_switched_off_live_via_config_change():
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True})
    p.update()
    assert live_ids(p) == ["n2"]
    p.on_config_change({"enabled": True, "favorite_teams": ["TOR"], "lock_to_favorites": False,
                        "advanced": {"animations": False}})
    assert live_ids(p) == ["n2", "n2", "n1", "n3"]  # TOR first with the default 2x boost


def test_exclude_hides_a_game_even_from_the_lock():
    p = make({"favorite_teams": ["TOR", "MTL"], "lock_to_favorites": True, "exclude_teams": ["MTL"]})
    p.update()
    assert live_ids(p) == ["n2"]


def test_favorites_accept_alternate_spellings():
    p = make({"favorite_teams": ["leafs", "TOR"]})
    assert p.favorites == ["LEAFS", "TOR"]
    q = make({"favorite_teams": ["LAK"], "lock_to_favorites": True})
    assert q.favorites == ["LA"]


# --- the arcade screens ------------------------------------------------------

def test_each_game_gets_a_rink_screen_then_its_player_cards():
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True})
    p.update()
    assert kinds(p) == ["scoreboard", "card", "card"]
    cards = [i["player"]["last"] for i in p.items[manager_mod.LIVE_MODE] if i["kind"] == "card"]
    assert cards == ["Valtonen", "Kjellberg"]  # TOR is the favorite: its players lead


def test_cards_per_game_zero_leaves_only_the_rink():
    p = make({"cards_per_game": 0})
    p.update()
    assert set(kinds(p)) == {"scoreboard"}


def test_favorites_players_lead_the_cards():
    p = make({"favorite_teams": ["BOS"], "lock_to_favorites": True, "cards_per_game": 1})
    p.update()
    assert p.items[manager_mod.LIVE_MODE][1]["player"]["team"] == "BOS"


def test_screens_rotate_by_dwell_time():
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True, "live_game_duration": 10, "card_seconds": 4})
    p.update()
    assert p._locate(manager_mod.LIVE_MODE, 0.0)[0] == 0 and p._locate(manager_mod.LIVE_MODE, 9.9)[0] == 0
    assert p._locate(manager_mod.LIVE_MODE, 10.0)[0] == 1 and p._locate(manager_mod.LIVE_MODE, 14.0)[0] == 2
    assert p._locate(manager_mod.LIVE_MODE, 18.0)[0] == 0  # wraps
    assert p.get_display_duration() == 18.0


def test_display_draws_every_screen_kind():
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True, "live_game_duration": 10, "card_seconds": 4})
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

def test_favorite_go_ahead_goal_queues_a_burst_naming_the_scorer():
    p = make({"favorite_teams": ["TOR"]})
    p.update()
    score_now(p)
    assert len(p._alerts) == 1
    a = p._alerts[0]
    assert (a["kind"], a["team"], a["player"]["last"]) == ("GO-AHEAD", "TOR", "Valtonen")  # "V.Valtonen scores on a wrist shot"


def test_the_run_is_tracked_and_flames_on_the_rink():
    p = make({"favorite_teams": ["TOR"]})
    p.update()
    assert n2(p)["run"] is None
    score_now(p)
    assert n2(p)["run"] == {"side": "home", "points": 3}
    assert model.run_on_fire(n2(p)) == "home"
    assert next(g for g in p.all_games if g["id"] == "n1")["run"] is None


def test_burst_interrupts_the_rotation_then_hands_back_and_glows():
    p = make({"favorite_teams": ["TOR"], "advanced": {"alert_seconds": 3}})
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
    payload["events"][0]["competitions"][0]["competitors"][0]["score"] = "3"  # MTL 2 -> 3
    p = make({"favorite_teams": ["TOR"]})
    p.update()
    clear_scoreboards(p)
    p.data.session = FakeSession(payload)
    p._refresh(manager_mod.time.time() + 1)
    assert p._alerts == []
    q = make({"favorite_teams": ["TOR"], "celebrate_all_teams": True})
    q.update()
    clear_scoreboards(q)
    q.data.session = FakeSession(payload)
    q._refresh(manager_mod.time.time() + 1)
    assert [(a["team"], a["kind"]) for a in q._alerts] == [("MTL", "GOAL")]


def test_lead_changes_only_keeps_the_ordinary_goals_quiet():
    payload = demo.scoreboard(0)
    payload["events"][0]["competitions"][0]["competitors"][0]["score"] = "3"  # MTL 2 -> 3, extending a lead
    p = make({"celebrate_all_teams": True, "alert_lead_changes_only": True})
    p.update()
    clear_scoreboards(p)
    p.data.session = FakeSession(payload)
    p._refresh(manager_mod.time.time() + 1)
    assert p._alerts == []
    q = make({"celebrate_all_teams": True})  # default: every goal bursts
    q.update()
    clear_scoreboards(q)
    q.data.session = FakeSession(payload)
    q._refresh(manager_mod.time.time() + 1)
    assert [a["kind"] for a in q._alerts] == ["GOAL"]
    payload["events"][0]["competitions"][0]["competitors"][1]["score"] = "2"  # EDM 1 -> 2: an equalizer
    payload["events"][0]["competitions"][0]["competitors"][0]["score"] = "2"
    r = make({"celebrate_all_teams": True, "alert_lead_changes_only": True})
    r.update()
    clear_scoreboards(r)
    r.data.session = FakeSession(payload)
    r._refresh(manager_mod.time.time() + 1)
    assert [(a["team"], a["kind"]) for a in r._alerts] == [("EDM", "EQUALIZER")]


def test_alerts_can_be_turned_off():
    p = make({"favorite_teams": ["TOR"], "score_alerts": False})
    p.update()
    score_now(p)
    assert p._alerts == []
    assert n2(p)["run"] is not None  # the flame does not depend on the bursts


def test_no_favorites_means_every_burst_is_celebrated():
    p = make()
    p.update()
    score_now(p)
    assert [a["team"] for a in p._alerts] == ["TOR"]


def test_locked_out_games_never_burst():
    p = make({"favorite_teams": ["MTL"], "lock_to_favorites": True, "celebrate_all_teams": True})
    p.update()
    score_now(p)  # TOR runs, but the lock hides TOR-BOS
    assert p._alerts == []


def test_a_long_gap_between_polls_is_not_a_run():
    t0 = manager_mod.time.time()
    p = make({"favorite_teams": ["TOR"]})
    p._refresh(t0)
    clear_scoreboards(p)
    p.data.session = FakeSession(demo.scoreboard(3))
    p._refresh(t0 + 900)   # a restart or an outage: those goals were not "just scored"
    assert p._alerts == [] and n2(p)["run"] is None


def test_spoiler_delay_holds_back_scores_and_bursts():
    t0 = manager_mod.time.time()
    p = make({"favorite_teams": ["TOR"], "spoiler_delay_seconds": 60})
    p._refresh(t0)
    assert p.live == [] and not p.has_live_content()  # nothing is old enough yet
    p._sync(t0 + 61)
    assert sorted(set(live_ids(p))) == ["n1", "n2", "n3"]  # n2 appears twice: TOR's 2x boost
    clear_scoreboards(p)
    p.data.session = FakeSession(demo.scoreboard(3))
    p._refresh(t0 + 62)
    p._sync(t0 + 62)
    assert n2(p)["home"]["score"] == 1  # still the old score
    assert p._alerts == []
    p._sync(t0 + 130)
    assert n2(p)["home"]["score"] == 4
    assert len(p._alerts) == 1


# --- photos ---------------------------------------------------------------------

def test_update_downloads_photos_for_the_cards_on_screen():
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True, "advanced": {"headshot_downloads": True}})
    p.update()
    urls = [u for u in p.data.session.urls if "headshots" in u]
    assert urls and all(u.startswith("https://a.espncdn.com/") for u in urls)
    star = p.items[manager_mod.LIVE_MODE][1]["player"]
    assert p.headshots.has_image(star["id"], manager_mod.time.time())
    assert p.headshots.portrait(star, 24, 30).size == (24, 30)


def test_photo_downloads_are_bounded_and_only_from_espn():
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True, "cards_per_game": 4, "advanced": {"headshot_downloads": True}})
    p.update()
    assert len([u for u in p.data.session.urls if "headshots" in u]) <= 4
    evil = dict(p.items[manager_mod.LIVE_MODE][1]["player"], id="999", headshot_url="https://evil.example.com/x.png")
    p.data.session.urls.clear()
    assert p.headshots.prefetch([evil]) == 0 and p.data.session.urls == []


def test_photos_off_never_fetches():
    p = make({"favorite_teams": ["TOR"], "show_headshots": False, "advanced": {"headshot_downloads": True}})
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
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True, "advanced": {"test_mode": True}})
    p.data.session = FakeSession(ConnectionError("must not be called"))
    for _ in range(3):
        p.update()
    assert p.data.session.calls == 0
    assert live_ids(p) == ["n2"] and len(p._alerts) == 1 and p._alerts[0]["kind"] == "GO-AHEAD"
    assert p.headshots.portrait(p.items[manager_mod.LIVE_MODE][1]["player"], 24, 30) is not None


def test_test_mode_never_asks_espn_for_the_made_up_players_photos():
    p = make({"favorite_teams": ["TOR"], "advanced": {"test_mode": True, "headshot_downloads": True}})
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


def test_update_interval_speeds_up_just_before_puck_drop():
    soon = demo.scoreboard(0, now=manager_mod.time.time())
    soon["events"] = [soon["events"][4]]
    soon["events"][0]["date"] = demo._iso(manager_mod.time.time() + 300)
    p = make(payload=soon)
    p.update()
    assert p.get_update_interval() == 30.0


def test_bad_config_values_are_clamped_not_fatal():
    p = make({"live_game_duration": "abc", "favorite_live_boost": 99, "favorite_teams": "MTL", "cards_per_game": -3,
              "game_limits": {"recent_games_to_show": "many"}})
    assert p.field_seconds == 12 and p.boost == 5 and p.cards_per_game == 0 and p.alert_lead_changes_only is False
    assert p.recent_games_to_show == 2


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
    p = make({"favorite_teams": ["COL"], "recent_cards_per_game": 1})
    p.update()
    assert [g["id"] for g in p.recent] == ["n4"]
    items = p.items[manager_mod.RECENT_MODE]
    assert items[0]["kind"] == "scoreboard" and items[0]["game"]["final"] is True
    assert items[1]["kind"] == "card" and items[1]["player"]["team"] == "COL"


def test_upcoming_mode_shows_preview_items_only():
    p = make({"favorite_teams": ["FLA"], "game_limits": {"favorite_rotation_boost": 1, "other_upcoming_games_to_show": 0}})
    p.update()
    assert [g["id"] for g in p.upcoming] == ["n5"]
    items = p.items[manager_mod.UPCOMING_MODE]
    assert items and all(i["kind"] == "preview" for i in items)


def test_favorite_rotation_boost_repeats_the_favorite_in_recent_and_upcoming():
    p = make({"favorite_teams": ["FLA"], "game_limits": {"favorite_rotation_boost": 3}})
    p.update()
    assert [g["id"] for g in p.upcoming].count("n5") == 3


def test_lock_applies_to_recent_and_upcoming_too():
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True})
    p.update()
    assert p.items[manager_mod.RECENT_MODE] == []   # TOR has no recent (final) game in the demo slate
    assert p.items[manager_mod.UPCOMING_MODE] == []  # nor an upcoming one


def test_other_games_show_when_lock_is_off():
    p = make({"favorite_teams": ["TOR"], "game_limits": {"other_upcoming_games_to_show": 2}})
    p.update()
    assert [g["id"] for g in p.recent] == ["n4"]
    assert set(g["id"] for g in p.upcoming) == {"n5", "n6"}


def test_other_upcoming_games_default_to_a_single_rotating_slot():
    p = make({"favorite_teams": ["TOR"]})  # default other_upcoming_games_to_show is 1
    p.update()
    assert len(set(g["id"] for g in p.upcoming)) == 1
    assert set(g["id"] for g in p.upcoming) <= {"n5", "n6"}


def test_recent_game_screen_shows_final_not_live():
    p = make({"favorite_teams": ["COL"]})
    p.update()
    assert p.display(display_mode=manager_mod.RECENT_MODE, force_clear=True) is True
    assert p.display_manager.pushes == 1


def test_upcoming_screen_draws():
    p = make({"favorite_teams": ["FLA"]})
    p.update()
    assert p.display(display_mode=manager_mod.UPCOMING_MODE, force_clear=True) is True


def test_display_mode_gating():
    p = make({"favorite_teams": ["TOR"], "lock_to_favorites": True})  # only live has content
    p.update()
    assert p.display(display_mode=manager_mod.LIVE_MODE, force_clear=True) is True
    assert p.display(display_mode=manager_mod.RECENT_MODE, force_clear=True) is False
    assert p.display(display_mode=manager_mod.UPCOMING_MODE, force_clear=True) is False
    assert p.display(display_mode="nhl_bogus") is False


def test_get_display_duration_reflects_the_mode_last_shown():
    p = make({"favorite_teams": ["FLA"], "recent_game_duration": 10, "upcoming_game_duration": 8})
    p.update()
    p.display(display_mode=manager_mod.UPCOMING_MODE, force_clear=True)
    assert p.get_display_duration() == 8.0 * len(p.items[manager_mod.UPCOMING_MODE])


def test_exclude_hides_a_team_from_recent_and_upcoming():
    p = make({"exclude_teams": ["COL"]})
    p.update()
    assert p.recent == []


def test_all_three_modes_cost_three_scoreboard_days_and_no_more():
    p = make({"favorite_teams": ["COL", "FLA"]})
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
    p = make({"favorite_teams": ["TOR"]})
    p.update()
    info = p.get_info()
    assert info["games_fetched"] == 6 and info["games_live"] == 3 and info["favorite_teams"] == ["TOR"]
    assert info["recent_matchups"] == ["VGK@COL"] and p.get_live_modes() == [manager_mod.LIVE_MODE]


# --- the scoreboard logo ------------------------------------------------------------

def test_logo_setting_resolves_to_a_file(tmp_path):
    here = os.path.dirname(os.path.abspath(manager_mod.__file__))
    p = make()
    default = os.path.join(here, "logo.png")
    assert p._logo_file() == (default if os.path.exists(default) else "")      # nothing configured
    mine = str(tmp_path / "mine.png")                                          # absolute on any OS
    q = make({"scoreboard_logo": mine})
    assert q._logo_file() == mine
    r = make({"scoreboard_logo": "assets/mine.png"})
    assert r._logo_file() == os.path.join(here, "assets", "mine.png")           # relative: inside the plugin folder
    s = make({"scoreboard_logo": "~/mine.png"})
    assert s._logo_file() == os.path.expanduser("~/mine.png")


def test_the_configured_logo_reaches_the_frame(tmp_path):
    logo = tmp_path / "mine.png"
    Image.new("RGBA", (40, 20), (0, 200, 255, 255)).save(logo)
    p = make({"scoreboard_logo": str(logo), "favorite_teams": ["TOR"], "lock_to_favorites": True})
    p.update()
    assert p._ctx().logo_path == str(logo)
    assert p.display() is True
    assert (0, 200, 255) in {c for _, c in p.display_manager.image.crop((58, 31, 128, 53)).getcolors(5000)}
    q = make({"favorite_teams": ["TOR"], "lock_to_favorites": True})
    q.update()
    assert q.display() is True
    assert (0, 200, 255) not in {c for _, c in q.display_manager.image.crop((58, 31, 128, 53)).getcolors(5000)}
