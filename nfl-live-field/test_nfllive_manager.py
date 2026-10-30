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
import nfllive_model as model  # noqa: E402
import nfllive_demo as demo  # noqa: E402


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

    def get(self, url, timeout=None, stream=False):
        self.calls += 1
        self.urls.append(url)
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
    plugin = manager_mod.NflLiveFieldPlugin("nfl-live-field", cfg, FakeDisplay(*size), FakeCache(), types.SimpleNamespace())
    plugin.data.session = FakeSession(payload)
    return plugin


def live_ids(plugin):
    return [g["id"] for g in plugin.live]


def kinds(plugin, mode=None):
    return [i["kind"] for i in plugin.items[mode or manager_mod.LIVE_MODE]]


def score_now(plugin, step=3, now=None):
    """Feed the plugin the slate in which KC has just scored."""
    plugin.data.cache.store.clear()
    plugin.data.session = FakeSession(demo.scoreboard(step))
    plugin._refresh(manager_mod.time.time() if now is None else now)


# --- the favorites lock ------------------------------------------------------

def test_no_favorites_shows_every_live_game():
    p = make()
    p.update()
    assert sorted(live_ids(p)) == ["t1", "t2", "t3"]
    assert p.has_live_content()


def test_lock_shows_only_favorites():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True})
    p.update()
    assert live_ids(p) == ["t2"]
    assert p.get_info()["live_matchups"] == ["BUF@KC"]


def test_lock_with_idle_favorite_returns_false_so_the_core_moves_on():
    p = make({"favorite_teams": ["SF"], "lock_to_favorites": True})  # SF's game is final, not live
    p.update()
    assert p.live == [] and p.items[manager_mod.LIVE_MODE] == [] and not p.has_live_content()
    assert p.display(display_mode=manager_mod.LIVE_MODE) is False
    assert p.display_manager.pushes == 0
    # SF's *recent* game still shows under the recent mode -- the lock is per
    # favorite team, not "only ever show live content".
    assert p.display(display_mode=manager_mod.RECENT_MODE, force_clear=True) is True


def test_lock_fallback_shows_others_until_favorite_plays():
    p = make({"favorite_teams": ["SF"], "lock_to_favorites": True, "lock_fallback_to_all": True})
    p.update()
    assert sorted(live_ids(p)) == ["t1", "t2", "t3"]


def test_lock_can_be_switched_off_live_via_config_change():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True})
    p.update()
    assert live_ids(p) == ["t2"]
    p.on_config_change({"enabled": True, "favorite_teams": ["KC"], "lock_to_favorites": False,
                        "advanced": {"animations": False}})
    assert live_ids(p) == ["t2", "t2", "t1", "t3"]  # KC first with the default 2x boost


def test_exclude_hides_a_game_even_from_the_lock():
    p = make({"favorite_teams": ["KC", "PHI"], "lock_to_favorites": True, "exclude_teams": ["PHI"]})
    p.update()
    assert live_ids(p) == ["t2"]


# --- the arcade screens ------------------------------------------------------

def test_each_game_gets_a_field_screen_then_its_player_cards():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True})
    p.update()
    assert kinds(p) == ["field", "card", "card"]
    live_items = p.items[manager_mod.LIVE_MODE]
    assert [i["player"]["last"] for i in live_items if i["kind"] == "card"] == ["Mahomes", "Kelce"]  # KC is the favorite: its players lead


def test_cards_per_game_zero_leaves_only_the_field():
    p = make({"cards_per_game": 0})
    p.update()
    assert set(kinds(p)) == {"field"}


def test_favorites_players_lead_the_cards():
    p = make({"favorite_teams": ["BUF"], "lock_to_favorites": True, "cards_per_game": 1})
    p.update()
    assert p.items[manager_mod.LIVE_MODE][1]["player"]["team"] == "BUF"


def test_screens_rotate_by_dwell_time():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True, "live_game_duration": 10, "card_seconds": 4})
    p.update()
    assert p._locate(manager_mod.LIVE_MODE, 0.0)[0] == 0 and p._locate(manager_mod.LIVE_MODE, 9.9)[0] == 0
    assert p._locate(manager_mod.LIVE_MODE, 10.0)[0] == 1 and p._locate(manager_mod.LIVE_MODE, 14.0)[0] == 2
    assert p._locate(manager_mod.LIVE_MODE, 18.0)[0] == 0  # wraps
    assert p.get_display_duration() == 18.0


def test_display_draws_every_screen_kind():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True, "live_game_duration": 10, "card_seconds": 4})
    p.update()
    demo.seed_headshots(p.headshots, p.live)
    seen = set()
    for elapsed in (1, 11, 15):
        p._mode_started = manager_mod.time.time() - elapsed
        p._entered = True
        assert p.display() is True
        seen.add(p.display_manager.image.tobytes())
    assert p.display_manager.pushes == 3
    assert len(seen) == 3


def test_display_returns_false_when_disabled():
    p = make({"enabled": False})
    p.update()
    assert p.display() is False


# --- score bursts --------------------------------------------------------------

def test_favorite_touchdown_queues_a_burst_naming_the_scorer():
    p = make({"favorite_teams": ["KC"]})
    p.update()
    score_now(p)
    assert len(p._alerts) == 1
    a = p._alerts[0]
    assert (a["kind"], a["team"], a["player"]["last"]) == ("TOUCHDOWN", "KC", "Kelce")


def test_burst_interrupts_the_rotation_then_hands_back_and_glows():
    p = make({"favorite_teams": ["KC"], "alert_seconds": 3, "advanced": {"alert_seconds": 3}})
    p.update()
    score_now(p)
    now = manager_mod.time.time()
    assert p._next_alert(now)["game"] == "t2"
    assert p.display() is True and p._alert is not None
    assert p._next_alert(now + 2)["game"] == "t2"
    assert p._next_alert(now + 4) is None and p._alert is None
    assert p._glow["game"] == "t2" and p._glow["side"] == "home"


def test_non_favorite_score_is_quiet_unless_asked():
    p = make({"favorite_teams": ["KC"]})
    p.update()
    payload = demo.scoreboard(0)
    payload["events"][0]["competitions"][0]["competitors"][0]["score"] = "24"  # PHI scores
    p.data.cache.store.clear()
    p.data.session = FakeSession(payload)
    p._refresh(NOW)
    assert p._alerts == []
    q = make({"favorite_teams": ["KC"], "celebrate_all_teams": True})
    q.update()
    q.data.cache.store.clear()
    q.data.session = FakeSession(payload)
    q._refresh(NOW)
    assert [a["team"] for a in q._alerts] == ["PHI"]


def test_alerts_can_be_turned_off():
    p = make({"favorite_teams": ["KC"], "score_alerts": False})
    p.update()
    score_now(p)
    assert p._alerts == []


def test_no_favorites_means_every_score_is_celebrated():
    p = make()
    p.update()
    score_now(p)
    assert [a["team"] for a in p._alerts] == ["KC"]


def test_locked_out_games_never_burst():
    p = make({"favorite_teams": ["PHI"], "lock_to_favorites": True, "celebrate_all_teams": True})
    p.update()
    score_now(p)  # KC scores, but the lock hides KC-BUF
    assert p._alerts == []


def test_spoiler_delay_holds_back_scores_and_bursts():
    t0 = manager_mod.time.time()
    p = make({"favorite_teams": ["KC"], "spoiler_delay_seconds": 60})
    p._refresh(t0)
    assert p.live == [] and not p.has_live_content()  # nothing is old enough yet
    p._sync(t0 + 61)
    assert sorted(set(live_ids(p))) == ["t1", "t2", "t3"]  # t2 appears twice: KC's 2x boost
    p.data.cache.store.clear()
    p.data.session = FakeSession(demo.scoreboard(3))
    p._refresh(t0 + 62)
    p._sync(t0 + 62)
    assert next(g for g in p.all_games if g["id"] == "t2")["home"]["score"] == 24  # still the old score
    assert p._alerts == []
    p._sync(t0 + 130)
    assert next(g for g in p.all_games if g["id"] == "t2")["home"]["score"] == 31
    assert len(p._alerts) == 1


# --- photos ---------------------------------------------------------------------

def test_update_downloads_photos_for_the_cards_on_screen():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True, "advanced": {"headshot_downloads": True}})
    p.update()
    urls = [u for u in p.data.session.urls if "headshots" in u]
    assert urls and all(u.startswith("https://a.espncdn.com/") for u in urls)
    star = p.items[manager_mod.LIVE_MODE][1]["player"]
    assert p.headshots.has_image(star["id"], manager_mod.time.time())
    assert p.headshots.portrait(star, 24, 30).size == (24, 30)


def test_photo_downloads_are_bounded_and_only_from_espn():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True, "cards_per_game": 4, "advanced": {"headshot_downloads": True}})
    p.update()
    assert len([u for u in p.data.session.urls if "headshots" in u]) <= 4
    evil = dict(p.items[manager_mod.LIVE_MODE][1]["player"], id="999", headshot_url="https://evil.example.com/x.png")
    p.data.session.urls.clear()
    assert p.headshots.prefetch([evil]) == 0 and p.data.session.urls == []


def test_photos_off_never_fetches():
    p = make({"favorite_teams": ["KC"], "show_headshots": False, "advanced": {"headshot_downloads": True}})
    p.update()
    assert not [u for u in p.data.session.urls if "headshots" in u]


# --- robustness -------------------------------------------------------------------

def test_fetch_failure_keeps_last_good_data_and_does_not_raise():
    p = make()
    p.update()
    p.data.session = FakeSession(ConnectionError("offline"))
    p.data.cache.store[p.data._key]["fetched_at"] = 0
    p.update()
    assert sorted(live_ids(p)) == ["t1", "t2", "t3"]
    assert "ConnectionError" in p.get_info()["last_error"]


def test_fetch_failure_with_empty_cache_is_quiet():
    p = make(payload=ConnectionError("offline"))
    p.update()
    assert p.live == [] and p.display() is False


def test_test_mode_needs_no_network_and_fires_a_burst_every_third_update():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True, "advanced": {"test_mode": True}})
    p.data.session = FakeSession(ConnectionError("must not be called"))
    for _ in range(3):
        p.update()
    assert p.data.session.calls == 0
    assert live_ids(p) == ["t2"] and len(p._alerts) == 1
    assert p.headshots.portrait(p.items[manager_mod.LIVE_MODE][1]["player"], 24, 30) is not None


def test_update_interval_is_fast_when_live_and_slow_when_idle():
    p = make()
    p.update()
    assert p.get_update_interval() == 30.0
    idle = make(payload={"events": [SLATE["events"][3]]})
    idle.update()
    assert idle.get_update_interval() == 600.0


def test_bad_config_values_are_clamped_not_fatal():
    p = make({"live_game_duration": "abc", "favorite_live_boost": 99, "favorite_teams": "PHI", "cards_per_game": -3})
    assert p.field_seconds == 12 and p.boost == 5 and p.cards_per_game == 0


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
    p = make({"favorite_teams": ["SF"], "recent_cards_per_game": 1})
    p.update()
    assert [g["id"] for g in p.recent] == ["t4"]
    items = p.items[manager_mod.RECENT_MODE]
    assert items[0]["kind"] == "field" and items[0]["game"]["final"] is True
    assert items[1]["kind"] == "card" and items[1]["player"]["team"] in ("SF", "SEA")


def test_upcoming_mode_shows_preview_items_only():
    p = make({"favorite_teams": ["DEN"], "game_limits": {"favorite_rotation_boost": 1, "other_upcoming_games_to_show": 0}})
    p.update()
    assert [g["id"] for g in p.upcoming] == ["t5"]
    items = p.items[manager_mod.UPCOMING_MODE]
    assert items and all(i["kind"] == "preview" for i in items)


def test_favorite_rotation_boost_repeats_the_favorite_in_recent_and_upcoming():
    p = make({"favorite_teams": ["DEN"], "game_limits": {"favorite_rotation_boost": 3}})
    p.update()
    assert [g["id"] for g in p.upcoming].count("t5") == 3


def test_lock_applies_to_recent_and_upcoming_too():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True})
    p.update()
    assert p.items[manager_mod.RECENT_MODE] == []  # KC has no recent (final) game in the demo slate
    assert p.items[manager_mod.UPCOMING_MODE] == []  # nor an upcoming one


def test_other_games_show_when_lock_is_off():
    # lock off -> other recent/upcoming games still fill in, up to their own limits
    p = make({"favorite_teams": ["KC"], "game_limits": {"other_upcoming_games_to_show": 2}})
    p.update()
    assert [g["id"] for g in p.recent] == ["t4"]
    assert set(g["id"] for g in p.upcoming) == {"t5", "t6"}


def test_other_upcoming_games_default_to_a_single_rotating_slot():
    p = make({"favorite_teams": ["KC"]})  # default other_upcoming_games_to_show is 1
    p.update()
    assert len(set(g["id"] for g in p.upcoming)) == 1
    assert set(g["id"] for g in p.upcoming) <= {"t5", "t6"}


def test_recent_game_screen_shows_final_not_live():
    p = make({"favorite_teams": ["SF"]})
    p.update()
    assert p.display(display_mode=manager_mod.RECENT_MODE, force_clear=True) is True
    assert p.display_manager.pushes == 1


def test_display_mode_gating():
    p = make({"favorite_teams": ["KC"], "lock_to_favorites": True})  # only live has content
    p.update()
    assert p.display(display_mode=manager_mod.LIVE_MODE, force_clear=True) is True
    assert p.display(display_mode=manager_mod.RECENT_MODE, force_clear=True) is False
    assert p.display(display_mode=manager_mod.UPCOMING_MODE, force_clear=True) is False
    assert p.display(display_mode="nfl_bogus") is False


def test_get_display_duration_reflects_the_mode_last_shown():
    p = make({"favorite_teams": ["DEN"], "recent_game_duration": 10, "upcoming_game_duration": 8})
    p.update()
    p.display(display_mode=manager_mod.UPCOMING_MODE, force_clear=True)
    up_duration = p.get_display_duration()
    p.display(display_mode=manager_mod.LIVE_MODE, force_clear=True)
    assert p.get_display_duration() != up_duration or not p.items[manager_mod.LIVE_MODE]


def test_exclude_hides_a_team_from_recent_and_upcoming():
    p = make({"exclude_teams": ["SF"]})
    p.update()
    assert p.recent == []


def test_recent_and_upcoming_need_no_extra_network_calls():
    p = make({"favorite_teams": ["SF", "DEN"]})
    calls_before = p.data.session.calls
    p.update()
    assert p.data.session.calls == calls_before + 1  # one scoreboard fetch covers all three modes
    assert p.recent and p.upcoming


# ---- Govee lights follow the score ---------------------------------------------------------

class GoveeRecorder:
    def __init__(self):
        self.calls = []

    def trigger(self, event, primary="ffffff", accent="ffffff"):
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
    p, a = govee_scene(lambda a: [a["team"]], score_alerts=False)
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
