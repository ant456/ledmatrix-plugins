"""Pure-logic tests: parsing ESPN's NBA scoreboard, the favorites lock, runs, bursts and player cards."""

import copy
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nbalive_demo as demo  # noqa: E402
import nbalive_model as model  # noqa: E402


def games(step=0, now=1_000_000.0):
    return {g["id"]: g for g in model.parse_scoreboard(demo.scoreboard(step, now=now))}


# --- teams ---------------------------------------------------------------------

def test_team_aliases_fold_to_espn_codes():
    assert model.norm_team(" gsw ") == "GS"
    assert [model.norm_team(x) for x in ("NYK", "NOP", "SAS", "UTA", "WAS", "PHO", "BRK")] == \
        ["NY", "NO", "SA", "UTAH", "WSH", "PHX", "BKN"]
    assert model.norm_team_list(["lal", "LAL", "GSW", "", None]) == ["LAL", "GS"]
    assert model.norm_team_list("PHI") == ["P", "H", "I"]  # a bare string is not a list; callers get junk, not a crash


def test_the_thirty_teams():
    assert len(model.NBA_TEAMS) == 30 and len(set(model.NBA_TEAMS)) == 30


# --- clock and period --------------------------------------------------------------

@pytest.mark.parametrize("period,name,detail,state,expected", [
    (1, "STATUS_IN_PROGRESS", "", "in", "Q1"),
    (4, "STATUS_IN_PROGRESS", "", "in", "Q4"),
    (5, "STATUS_IN_PROGRESS", "", "in", "OT"),
    (7, "STATUS_IN_PROGRESS", "", "in", "OT3"),
    (2, "STATUS_HALFTIME", "Halftime", "in", "HALF"),
    (4, "STATUS_FINAL", "Final", "post", "FINAL"),
    (0, "STATUS_SCHEDULED", "", "pre", "PRE"),
])
def test_period_text(period, name, detail, state, expected):
    assert model.period_text(period, name, detail, state) == expected


@pytest.mark.parametrize("clock,expected", [("7:42", 462.0), ("0:09", 9.0), ("45.3", 45.3), ("12:00", 720.0), ("", None), ("abc", None)])
def test_clock_seconds(clock, expected):
    assert model.clock_seconds(clock) == expected


def test_clutch_needs_fourth_quarter_five_minutes_five_points():
    g = games()["n2"]
    assert g["clutch"] is True                                    # Q4 2:03, 95-100
    far = copy.deepcopy(g)
    far["clock"] = "7:10"
    assert model.is_clutch(far) is False
    wide = copy.deepcopy(g)
    wide["home"]["score"] = 80
    assert model.is_clutch(wide) is False
    early = copy.deepcopy(g)
    early["period"] = 3
    assert model.is_clutch(early) is False
    ot = copy.deepcopy(g)
    ot["period"], ot["clock"] = 5, "1:30"
    assert model.is_clutch(ot) is True
    done = copy.deepcopy(g)
    done["live"] = False
    assert model.is_clutch(done) is False


# --- parsing ---------------------------------------------------------------------------

def test_demo_slate_parses_into_every_state():
    g = games()
    assert sorted(g) == ["n1", "n2", "n3", "n4", "n5", "n6"]
    assert [g[i]["state"] for i in ("n1", "n4", "n5")] == ["in", "post", "pre"]
    n2 = g["n2"]
    assert (n2["away"]["abbr"], n2["home"]["abbr"], n2["period_text"], n2["clock"]) == ("DEN", "LAL", "Q4", "2:03")
    assert n2["home"]["linescores"] == [22, 27, 24, 13] and n2["home"]["record"] == "18-14"
    assert n2["odds"] == "DEN -1.5" and "layup" not in n2["last_play"] and n2["last_play"]
    assert n2["home"]["color"] == "552583"


def test_pre_games_carry_no_stars_even_if_espn_sends_season_leaders():
    n5 = demo.scoreboard(0)["events"][4]
    n5["competitions"][0]["competitors"][0]["leaders"] = demo._leaders("PHI", {"points": 27, "rebounds": 9, "assists": 6})
    game = model.parse_event(n5)
    assert game["stars"] == [] and game["home"]["leaders"] == []


def test_bad_events_are_skipped_not_fatal():
    payload = {"events": [{}, {"competitions": []}, {"competitions": [{"competitors": [{"homeAway": "home"}]}]},
                          demo.scoreboard(0)["events"][0]]}
    assert [g["id"] for g in model.parse_scoreboard(payload)] == ["n1"]
    assert model.parse_scoreboard(None) == [] and model.parse_scoreboard({}) == []


def test_missing_optional_fields_default_sanely():
    ev = demo.scoreboard(0)["events"][0]
    comp = ev["competitions"][0]
    for c in comp["competitors"]:
        c.pop("linescores", None)
        c.pop("records", None)
    comp.pop("odds", None)
    comp.pop("situation", None)
    g = model.parse_event(ev)
    assert g["home"]["linescores"] == [] and g["home"]["record"] == "" and g["odds"] == "" and g["last_play"] == ""


def test_records_prefer_the_overall_one():
    assert model._record([{"type": "home", "summary": "10-2"}, {"type": "total", "summary": "21-9"}]) == "21-9"
    assert model._record([{"summary": "bad"}, {"summary": "5-5"}]) == "5-5"
    assert model._record(None) == ""


def test_start_time_accepts_minutes_and_seconds():
    assert model._epoch("2026-01-15T00:30Z") == model._epoch("2026-01-15T00:30:00Z") == 1768437000.0
    assert model._epoch("nonsense") is None and model._epoch(None) is None


# --- player cards ------------------------------------------------------------------------

def test_a_player_leading_several_categories_is_one_card():
    g = games()["n2"]
    names = [p["name"] for p in g["stars"]]
    assert names.count("Viktor Hargrove") == 1 and names.count("Marcus Hale") == 1
    hargrove = next(p for p in g["stars"] if p["last"] == "Hargrove")
    assert (hargrove["category"], hargrove["value"], hargrove["unit"]) == ("pts", 33, "PTS")
    assert hargrove["parts"] == [("14", "REB"), ("11", "AST")] and hargrove["badge"] == "TRIPLE-DBL"
    hale = next(p for p in g["stars"] if p["last"] == "Hale")
    assert hale["badge"] == "DBL-DBL" and hale["parts"] == [("12", "REB")]


@pytest.mark.parametrize("cat,value,tier", [
    ("pts", 40, "legendary"), ("pts", 39, "epic"), ("pts", 30, "epic"), ("pts", 29, "rare"), ("pts", 20, "rare"), ("pts", 19, "common"),
    ("reb", 18, "legendary"), ("reb", 14, "epic"), ("reb", 10, "rare"), ("reb", 9, "common"),
    ("ast", 14, "legendary"), ("ast", 10, "epic"), ("ast", 7, "rare"), ("ast", 6, "common"),
])
def test_rarity_thresholds(cat, value, tier):
    assert model.tier_for(cat, value) == tier


def test_card_headline_is_the_stat_with_the_best_rarity():
    leaders = demo._leaders("DEN", {"points": 18, "rebounds": 19, "assists": 4})  # one man leading all three
    (p,) = model.parse_leaders(leaders, "DEN")
    assert (p["category"], p["value"], p["tier"]) == ("reb", 19, "legendary")
    assert ("18", "PTS") in p["parts"] and p["badge"] == "DBL-DBL"


def test_xp_bar_target_is_the_next_rarity():
    assert model.next_threshold("pts", 12) == 20.0
    assert model.next_threshold("pts", 25) == 30.0
    assert model.next_threshold("pts", 35) == 40.0
    assert model.next_threshold("pts", 55) == 40.0  # already maxed: the bar is simply full


def test_badges():
    assert model.badge_for({"pts": 22, "reb": 9}) == ""
    assert model.badge_for({"pts": 22, "reb": 10}) == "DBL-DBL"
    assert model.badge_for({"pts": 22, "reb": 10, "ast": 10}) == "TRIPLE-DBL"


def test_per_game_and_short_leader_names_are_accepted():
    for name in ("pointsPerGame", "pts", "points"):
        group = {"name": name, "leaders": [{"displayValue": "27", "value": 27.0, "athlete": {"id": "5", "fullName": "A B"}}]}
        assert model.parse_leaders([group], "X")[0]["value"] == 27


def test_stat_value_falls_back_from_display_to_number():
    assert model._stat_value({"displayValue": "31"}) == 31
    assert model._stat_value({"displayValue": "31 PTS"}) == 31
    assert model._stat_value({"displayValue": "", "value": 12.0}) == 12
    assert model._stat_value({"displayValue": "n/a"}) == 0


def test_junk_leaders_are_ignored():
    groups = [{"name": "steals", "leaders": [{"displayValue": "5", "athlete": {"id": "9"}}]},
              {"name": "points", "leaders": [{"displayValue": "9", "athlete": {}}]},
              {"name": "points", "leaders": []}, {"name": "assists"}]
    assert model.parse_leaders(groups, "X") == [] and model.parse_leaders(None, "X") == []


def test_headshot_url_prefers_espns_and_falls_back_to_the_pattern():
    assert model._headshot_url({"id": "7", "headshot": {"href": "https://a.espncdn.com/x.png"}}) == "https://a.espncdn.com/x.png"
    assert model._headshot_url({"id": "7"}) == "https://a.espncdn.com/i/headshots/nba/players/full/7.png"
    assert model._headshot_url({}) == ""


def test_stars_rank_by_rarity_then_closeness_to_the_top():
    ranked = [p["last"] for p in games()["n2"]["stars"]]
    assert ranked[:2] == ["Hargrove", "Hale"] and ranked[-1] == "Okafor"


def test_stars_for_puts_favorites_first_and_skips_zeros():
    g = games()["n4"]
    assert model.stars_for(g, 1, ["GS"])[0]["team"] == "GS"
    assert model.stars_for(g, 1, [])[0]["team"] == "OKC"
    assert model.stars_for(g, 0, []) == []
    g2 = copy.deepcopy(g)
    for p in g2["stars"]:
        p["value"] = 0
    assert model.stars_for(g2, 3, []) == []


def test_split_name_drops_suffixes():
    assert model.split_name("Jaren Jackson Jr.") == ("Jaren", "Jackson")
    assert model.split_name("Nene") == ("", "Nene")
    assert model.split_name("") == ("", "")


# --- favorites lock and rotation ------------------------------------------------------------

def ids(gs):
    return [g["id"] for g in gs]


def test_select_live_without_favorites_shows_everything_live():
    assert ids(model.select_live(list(games().values()), [], False)) == ["n1", "n2", "n3"]


def test_select_live_lock_and_fallback():
    gs = list(games().values())
    assert ids(model.select_live(gs, ["LAL"], True)) == ["n2"]
    assert model.select_live(gs, ["GS"], True) == []                       # GS's game is over
    assert ids(model.select_live(gs, ["GS"], True, fallback_when_idle=True)) == ["n1", "n2", "n3"]
    assert ids(model.select_live(gs, ["LAL"], False)) == ["n2", "n1", "n3"]


def test_exclude_beats_the_lock():
    gs = list(games().values())
    assert ids(model.select_live(gs, ["LAL", "PHI"], True, exclude=["PHI"])) == ["n2"]
    assert model.select_live(gs, ["LAL"], True, exclude=["DEN"]) == []


def test_rotation_boost_spreads_favorites():
    gs = model.select_live(list(games().values()), ["LAL"], False)
    assert ids(model.rotation(gs, ["LAL"], 1)) == ["n2", "n1", "n3"]
    assert ids(model.rotation(gs, ["LAL"], 2)) == ["n2", "n2", "n1", "n3"]
    assert ids(model.rotation(gs, ["LAL"], 3)) == ["n2", "n2", "n1", "n2", "n3"]
    assert ids(model.rotation(gs[:1], ["LAL"], 3)) == ["n2"]  # nothing else to spread between


def test_select_by_state_orders_and_limits():
    gs = list(games().values())
    assert ids(model.select_by_state(gs, "post", [], 2, 1, 1800, now=0)) == ["n4"]
    assert ids(model.select_by_state(gs, "pre", ["PHX"], 2, 0, 1800, now=0)) == ["n6"]
    assert ids(model.select_by_state(gs, "pre", [], 2, 5, 1800, now=0)) == ["n5", "n6"]
    assert ids(model.select_by_state(gs, "pre", [], 2, 0, 1800, now=0)) == []
    assert model.select_by_state(gs, "pre", [], 2, 5, 1800, exclude=["NY", "PHX"], now=0) == []


def test_other_games_rotate_through_the_pool_over_time():
    pool = [{"id": str(i), "state": "pre", "start": float(i), "home": {"abbr": f"H{i}"}, "away": {"abbr": f"A{i}"}} for i in range(5)]
    seen = {model.select_by_state(pool, "pre", [], 0, 1, 1800, now=k * 1800.0)[0]["id"] for k in range(5)}
    assert seen == {"0", "1", "2", "3", "4"}
    now = 7 * 1800 + 5.0
    assert ids(model.select_by_state(pool, "pre", [], 0, 2, 1800, now=now)) == ids(model.select_by_state(pool, "pre", [], 0, 2, 1800, now=now + 60))


# --- runs and bursts --------------------------------------------------------------------------

def snap(home, away, live=True, period=4, clock="5:00", **extra):
    base = copy.deepcopy(games()["n2"])
    base["home"]["score"], base["away"]["score"] = home, away
    base.update(live=live, period=period, clock=clock, run=None)
    base.update(extra)
    return base


def test_lead_helper():
    assert model.lead(snap(100, 95)) == ("home", 5)
    assert model.lead(snap(90, 95)) == ("away", 5)
    assert model.lead(snap(90, 90)) == (None, 0)


def test_scoring_change_picks_the_side_that_outscored():
    assert model.scoring_change(snap(90, 90), snap(95, 90)) == "home"
    assert model.scoring_change(snap(90, 90), snap(92, 95)) == "away"
    assert model.scoring_change(snap(90, 90), snap(94, 94)) is None
    assert model.scoring_change(None, snap(94, 94)) is None


def test_runs_build_break_and_net_out():
    a, b, c, d_ = snap(50, 50), snap(53, 50), snap(58, 50), snap(58, 52)
    run = model.update_run(None, a, b)
    assert run == {"side": "home", "points": 3}
    run = model.update_run(run, b, c)
    assert run == {"side": "home", "points": 8}
    assert model.run_on_fire({"run": run}) == "home"
    run = model.update_run(run, c, d_)                      # the other side answers: its own run starts
    assert run == {"side": "away", "points": 2} and model.run_on_fire({"run": run}) is None
    assert model.update_run(run, d_, d_) == run              # nobody scored
    both = model.update_run(None, snap(50, 50), snap(56, 53))
    assert both == {"side": "home", "points": 3}             # both scored: only the net margin counts
    assert model.update_run({"side": "home", "points": 4}, snap(50, 50), snap(55, 55)) is None
    assert model.update_run({"side": "home", "points": 9}, snap(60, 50), snap(0, 0)) is None  # a score going down is a reset
    assert model.update_run({"side": "home", "points": 9}, snap(60, 50), snap(60, 50, live=False)) is None


def test_big_run_burst():
    prev, cur = snap(60, 60, period=2), snap(69, 60, period=2)
    a = model.detect_alert(prev, cur, 8, 123.0)
    assert (a["kind"], a["team"], a["opp"], a["points"], a["desc"]) == ("BIG RUN", "LAL", "DEN", 9, "LAL 9-0 RUN")
    assert a["away_score"] == 60 and a["home_score"] == 69 and a["at"] == 123.0
    assert model.detect_alert(prev, snap(67, 60, period=2), 8, 0) is None          # +7 is not a burst
    assert model.detect_alert(prev, snap(67, 60, period=2), 7, 0)["kind"] == "BIG RUN"
    assert model.detect_alert(prev, snap(69, 60, period=2), 0, 0) is None          # 0 turns run bursts off
    traded = model.detect_alert(prev, snap(72, 64, period=2), 8, 0)                # 12-4: a net swing of 8
    assert traded["points"] == 8 and traded["desc"] == "LAL 12-4 RUN"


def test_lead_change_burst_only_in_the_fourth_or_later():
    prev, cur = snap(95, 100), snap(104, 100)
    a = model.detect_alert(prev, cur, 8, 0)
    assert a["kind"] == "LEAD CHANGE" and a["desc"] == "LAL TAKES THE LEAD"
    small = model.detect_alert(snap(98, 100), snap(101, 100), 8, 0)
    assert small["kind"] == "LEAD CHANGE"                       # even a 3-point swing counts, late
    assert model.detect_alert(snap(98, 100, period=2), snap(101, 100, period=2), 8, 0) is None
    assert model.detect_alert(snap(98, 100, period=5), snap(101, 100, period=5), 8, 0)["kind"] == "LEAD CHANGE"
    tie_to_lead = model.detect_alert(snap(100, 100), snap(102, 100), 8, 0)
    assert tie_to_lead["kind"] == "LEAD CHANGE"
    extend = model.detect_alert(snap(105, 100), snap(107, 100), 8, 0)
    assert extend is None                                      # already ahead: no change of lead


def test_no_burst_when_nothing_scored_or_game_not_live():
    s = snap(60, 60)
    assert model.detect_alert(s, s, 8, 0) is None
    assert model.detect_alert(None, s, 8, 0) is None
    assert model.detect_alert(snap(60, 60), snap(80, 60, live=False), 8, 0) is None


def test_burst_names_the_scorer_only_when_the_play_text_does():
    prev, cur = snap(95, 100), snap(104, 100)
    cur["last_play"] = "D.Okafor assists M.Hale driving layup"
    assert model.detect_alert(prev, cur, 8, 0)["player"]["last"] == "Hale"      # the surname named last
    cur["last_play"] = ""
    assert model.detect_alert(prev, cur, 8, 0)["player"] is None


# --- tip-off labels ---------------------------------------------------------------------------------

def test_tipoff_text():
    now = time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, -1))
    at = lambda days, hour=19, minute=30: time.mktime((2026, 1, 15 + days, hour, minute, 0, 0, 0, -1))  # noqa: E731
    assert model.tipoff_text(at(0), now) == "TODAY 7:30 PM"
    assert model.tipoff_text(at(0, 0, 5), now - 1) == "TODAY 12:05 AM"
    assert model.tipoff_text(at(1, 13, 0), now) == "TMRW 1:00 PM"
    assert model.tipoff_text(at(3, 19, 0), now) == "SUN 7:00 PM"
    assert model.tipoff_text(at(10, 19, 0), now) == "1/25 7:00 PM"
    assert model.tipoff_text(None, now) == ""
