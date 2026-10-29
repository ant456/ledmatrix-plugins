"""Pure-logic tests: parsing ESPN's NHL scoreboard, the favorites lock, runs, bursts and player cards."""

import copy
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nhllive_demo as demo  # noqa: E402
import nhllive_model as model  # noqa: E402


def games(step=0, now=1_000_000.0):
    return {g["id"]: g for g in model.parse_scoreboard(demo.scoreboard(step, now=now))}


# --- teams ---------------------------------------------------------------------

def test_team_aliases_fold_to_espn_codes():
    assert model.norm_team(" lak ") == "LA"
    assert [model.norm_team(x) for x in ("TBL", "NJD", "SJS", "VEG", "WAS", "UTA", "ARI", "MON")] == \
        ["TB", "NJ", "SJ", "VGK", "WSH", "UTAH", "UTAH", "MTL"]
    assert model.norm_team_list(["tor", "TOR", "LAK", "", None]) == ["TOR", "LA"]
    assert model.norm_team_list("BOS") == ["B", "O", "S"]  # a bare string is not a list; callers get junk, not a crash


def test_the_thirty_two_teams():
    assert len(model.NHL_TEAMS) == 32 and len(set(model.NHL_TEAMS)) == 32


# --- clock and period --------------------------------------------------------------

@pytest.mark.parametrize("period,name,detail,state,expected", [
    (1, "STATUS_IN_PROGRESS", "", "in", "P1"),
    (3, "STATUS_IN_PROGRESS", "", "in", "P3"),
    (4, "STATUS_IN_PROGRESS", "", "in", "OT"),
    (6, "STATUS_IN_PROGRESS", "", "in", "OT3"),
    (2, "STATUS_END_PERIOD", "End of 2nd Period", "in", "INT"),
    (1, "STATUS_IN_PROGRESS", "Intermission", "in", "INT"),
    (5, "STATUS_IN_PROGRESS", "Shootout", "in", "SO"),
    (3, "STATUS_FINAL", "Final", "post", "FINAL"),
    (0, "STATUS_SCHEDULED", "", "pre", "PRE"),
])
def test_period_text(period, name, detail, state, expected):
    assert model.period_text(period, name, detail, state) == expected


@pytest.mark.parametrize("period,detail,expected", [
    (3, "Final", ""), (4, "Final/OT", "OT"), (6, "Final/3OT", "3OT"), (5, "Final/SO", "SO"), (5, "Final/Shootout", "SO"),
])
def test_how_a_game_ended(period, detail, expected):
    assert model.ending_text(period, detail) == expected


@pytest.mark.parametrize("clock,expected", [("7:42", 462.0), ("0:09", 9.0), ("45.3", 45.3), ("20:00", 1200.0), ("", None), ("abc", None)])
def test_clock_seconds(clock, expected):
    assert model.clock_seconds(clock) == expected


def test_the_clock_slot_reads_for_every_state():
    g = games()
    assert g["n2"]["clock_text"] == "P3 2:10"
    assert g["n3"]["clock_text"] == "END P2" and g["n3"]["period_text"] == "INT"
    assert g["n4"]["clock_text"] == "FINAL/OT" and g["n4"]["ending"] == "OT"
    assert g["n5"]["clock_text"] == ""
    ot = copy.deepcopy(g["n3"])
    ot["period"] = 4
    assert model.clock_text(ot) == "END OT"
    so = copy.deepcopy(g["n2"])
    so["period_text"] = "SO"
    assert model.clock_text(so) == "SO"


def test_clutch_is_overtime_or_the_last_five_minutes_of_a_one_goal_third():
    g = games(3)["n2"]
    assert g["clutch"] is True                                    # P3 2:10, TOR 4-3
    assert games(0)["n2"]["clutch"] is False                      # a two-goal game
    far = copy.deepcopy(g)
    far["clock"] = "7:10"
    assert model.is_clutch(far) is False
    wide = copy.deepcopy(g)
    wide["home"]["score"] = 6
    assert model.is_clutch(wide) is False
    early = copy.deepcopy(g)
    early["period"] = 2
    assert model.is_clutch(early) is False
    ot = copy.deepcopy(g)
    ot["period"], ot["clock"], ot["home"]["score"] = 4, "19:30", 9
    assert model.is_clutch(ot) is True                            # sudden death is always clutch
    so = copy.deepcopy(g)
    so["period_text"] = "SO"
    assert model.is_clutch(so) is True
    intermission = copy.deepcopy(g)
    intermission["period_text"] = "INT"
    assert model.is_clutch(intermission) is False
    done = copy.deepcopy(g)
    done["live"] = False
    assert model.is_clutch(done) is False


# --- parsing ---------------------------------------------------------------------------

def test_demo_slate_parses_into_every_state():
    g = games()
    assert sorted(g) == ["n1", "n2", "n3", "n4", "n5", "n6"]
    assert [g[i]["state"] for i in ("n1", "n3", "n4", "n5")] == ["in", "in", "post", "pre"]
    n2 = g["n2"]
    assert (n2["away"]["abbr"], n2["home"]["abbr"], n2["period_text"], n2["clock"]) == ("BOS", "TOR", "P3", "2:10")
    assert n2["home"]["linescores"] == [1, 0, 0] and n2["home"]["record"] == "15-8-2"
    assert n2["odds"] == "TOR -115" and n2["last_play"] == "T.Brandvold shot blocked"
    assert n2["home"]["color"] == "00205b"


def test_shots_and_power_play_are_read():
    g = games()
    assert (g["n1"]["home"]["shots"], g["n1"]["away"]["shots"]) == (21, 20) and g["n1"]["power_play"] is True
    assert g["n2"]["power_play"] is False
    assert g["n5"]["home"]["shots"] is None                                     # nothing sent: unknown, not 0
    done = demo.scoreboard(0)["events"][3]
    done["competitions"][0]["situation"] = {"isPowerPlay": True}
    assert model.parse_event(done)["power_play"] is False                       # only a live game has a power play


def test_shots_derive_from_saves_when_espn_sends_no_total():
    opposing = [{"name": "saves", "displayValue": "30"}, {"name": "savePct", "displayValue": ".909"}]
    assert model.team_shots([], opposing) == 33
    assert model.team_shots([], [{"name": "saves", "displayValue": "30"}, {"name": "savePct", "displayValue": "90.9"}]) == 33
    assert model.team_shots([{"name": "shotsTotal", "value": 28.0}], opposing) == 28
    assert model.team_shots([], [{"name": "saves", "displayValue": "30"}]) is None
    assert model.team_shots(None, None) is None
    assert model.team_shots([{"name": "shots", "displayValue": "n/a"}], None) is None


def test_a_shootout_is_recognised_live_and_final():
    ev = demo.scoreboard(0)["events"][3]
    ev["competitions"][0]["status"]["period"] = 5
    ev["competitions"][0]["status"]["type"]["shortDetail"] = "Final/SO"
    g = model.parse_event(ev)
    assert (g["ending"], g["shootout"], g["clock_text"]) == ("SO", True, "FINAL/SO")
    live = demo.scoreboard(0)["events"][1]
    live["competitions"][0]["status"]["period"] = 5
    live["competitions"][0]["status"]["type"]["shortDetail"] = "Shootout"
    assert model.parse_event(live)["clock_text"] == "SO"


def test_pre_games_carry_no_stars_even_if_espn_sends_season_leaders():
    n5 = demo.scoreboard(0)["events"][4]
    n5["competitions"][0]["competitors"][0]["leaders"] = demo._leaders("TOR", {"goals": 20, "assists": 15, "points": 35})
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
        c.pop("statistics", None)
    comp.pop("odds", None)
    comp.pop("situation", None)
    g = model.parse_event(ev)
    assert g["home"]["linescores"] == [] and g["home"]["record"] == "" and g["odds"] == "" and g["last_play"] == ""
    assert g["home"]["shots"] is None and g["power_play"] is False


def test_records_prefer_the_overall_one_and_keep_the_overtime_losses():
    assert model._record([{"type": "home", "summary": "10-2-1"}, {"type": "total", "summary": "21-9-3"}]) == "21-9-3"
    assert model._record([{"summary": "bad"}, {"summary": "5-5"}]) == "5-5"
    assert model._record(None) == ""


def test_start_time_accepts_minutes_and_seconds():
    assert model._epoch("2026-01-15T00:30Z") == model._epoch("2026-01-15T00:30:00Z") == 1768437000.0
    assert model._epoch("nonsense") is None and model._epoch(None) is None


# --- player cards ------------------------------------------------------------------------

def test_a_player_leading_several_categories_is_one_card():
    g = games()["n2"]
    names = [p["name"] for p in g["stars"]]
    assert names.count("Tyler Brandvold") == 1 and names.count("Declan Marchetti") == 1
    brandvold = next(p for p in g["stars"] if p["last"] == "Brandvold")
    assert (brandvold["category"], brandvold["value"], brandvold["unit"], brandvold["tier"]) == ("g", 2, "G", "epic")
    assert brandvold["parts"] == [("2", "PTS")] and brandvold["badge"] == "MULTI-GOAL"
    marchetti = next(p for p in g["stars"] if p["last"] == "Marchetti")
    assert (marchetti["category"], marchetti["value"], marchetti["tier"]) == ("pts", 3, "rare")
    assert marchetti["parts"] == [("1", "A")] and marchetti["badge"] == ""
    goalie = next(p for p in g["stars"] if p["last"] == "Kjellberg")
    assert (goalie["category"], goalie["value"], goalie["unit"], goalie["pos"]) == ("sv", 27, "SV", "G")


@pytest.mark.parametrize("cat,value,tier", [
    ("pts", 5, "legendary"), ("pts", 4, "epic"), ("pts", 3, "rare"), ("pts", 2, "common"),
    ("g", 3, "legendary"), ("g", 2, "epic"), ("g", 1, "rare"), ("g", 0, "common"),
    ("a", 4, "legendary"), ("a", 3, "epic"), ("a", 2, "rare"), ("a", 1, "common"),
    ("sv", 40, "legendary"), ("sv", 32, "epic"), ("sv", 25, "rare"), ("sv", 24, "common"),
])
def test_rarity_thresholds(cat, value, tier):
    assert model.tier_for(cat, value) == tier


def test_card_headline_is_the_stat_with_the_best_rarity():
    leaders = demo._leaders("EDM", {"goals": 3, "assists": 1, "points": 4})  # one man leading all three
    (p,) = model.parse_leaders(leaders, "EDM")
    assert (p["category"], p["value"], p["tier"]) == ("g", 3, "legendary")
    assert p["parts"] == [("4", "PTS"), ("1", "A")] and p["badge"] == "HAT TRICK"


def test_xp_bar_target_is_the_next_rarity():
    assert model.next_threshold("pts", 2) == 3.0
    assert model.next_threshold("pts", 3) == 4.0
    assert model.next_threshold("pts", 4) == 5.0
    assert model.next_threshold("pts", 9) == 5.0   # already maxed: the bar is simply full
    assert model.next_threshold("g", 0) == 1.0 and model.next_threshold("sv", 30) == 32.0


def test_badges():
    assert model.badge_for({"g": 1, "a": 2}) == ""
    assert model.badge_for({"g": 2, "pts": 2}) == "MULTI-GOAL"
    assert model.badge_for({"a": 3}) == "PLAYMAKER"
    assert model.badge_for({"g": 3}) == model.badge_for({"g": 5}) == "HAT TRICK"
    assert set(model.BADGE_SHORT) == {"HAT TRICK", "MULTI-GOAL", "PLAYMAKER"}


@pytest.mark.parametrize("name,key", [
    ("goals", "g"), ("goalsPerGame", "g"), ("goalsLeader", "g"), ("Goal", "g"),
    ("assists", "a"), ("assistsPerGame", "a"), ("points", "pts"), ("pointsLeader", "pts"), ("pts", "pts"),
    ("saves", "sv"), ("totalSaves", None), ("steals", None), (None, None), ("", None),
])
def test_leader_category_names_are_matched_loosely(name, key):
    assert model.category_for(name) == key


def test_per_game_and_short_leader_names_are_accepted():
    for name in ("pointsPerGame", "pts", "points"):
        group = {"name": name, "leaders": [{"displayValue": "3", "value": 3.0, "athlete": {"id": "5", "fullName": "A B"}}]}
        assert model.parse_leaders([group], "X")[0]["value"] == 3


def test_stat_value_falls_back_from_display_to_number():
    assert model._stat_value({"displayValue": "3"}) == 3
    assert model._stat_value({"displayValue": "3 PTS"}) == 3
    assert model._stat_value({"displayValue": "", "value": 2.0}) == 2
    assert model._stat_value({"displayValue": "n/a"}) == 0


def test_junk_leaders_are_ignored():
    groups = [{"name": "hits", "leaders": [{"displayValue": "5", "athlete": {"id": "9"}}]},
              {"name": "goals", "leaders": [{"displayValue": "9", "athlete": {}}]},
              {"name": "goals", "leaders": []}, {"name": "assists"}]
    assert model.parse_leaders(groups, "X") == [] and model.parse_leaders(None, "X") == []


def test_headshot_url_prefers_espns_and_falls_back_to_the_pattern():
    assert model._headshot_url({"id": "7", "headshot": {"href": "https://a.espncdn.com/x.png"}}) == "https://a.espncdn.com/x.png"
    assert model._headshot_url({"id": "7"}) == "https://a.espncdn.com/i/headshots/nhl/players/full/7.png"
    assert model._headshot_url({}) == ""


def test_stars_rank_by_rarity_then_closeness_to_the_top():
    ranked = [p["last"] for p in games()["n2"]["stars"]]
    assert ranked == ["Valtonen", "Brandvold", "Haverkamp", "Kjellberg", "Marchetti", "Castellan"]


def test_stars_for_puts_favorites_first_and_skips_zeros():
    g = games()["n4"]
    assert model.stars_for(g, 1, ["VGK"])[0]["team"] == "VGK"
    top = model.stars_for(g, 1, [])[0]
    assert (top["team"], top["last"], top["badge"], top["tier"]) == ("COL", "Ferraro", "HAT TRICK", "legendary")
    assert model.stars_for(g, 0, []) == []
    assert all(p["value"] > 0 for p in model.stars_for(games()["n2"], 9, []))      # Castellan (0 assists) is skipped
    g2 = copy.deepcopy(g)
    for p in g2["stars"]:
        p["value"] = 0
    assert model.stars_for(g2, 3, []) == []


def test_split_name_drops_suffixes():
    assert model.split_name("Ryan Smith Jr.") == ("Ryan", "Smith")
    assert model.split_name("Nene") == ("", "Nene")
    assert model.split_name("") == ("", "")


# --- favorites lock and rotation ------------------------------------------------------------

def ids(gs):
    return [g["id"] for g in gs]


def test_select_live_without_favorites_shows_everything_live():
    assert ids(model.select_live(list(games().values()), [], False)) == ["n1", "n2", "n3"]


def test_select_live_lock_and_fallback():
    gs = list(games().values())
    assert ids(model.select_live(gs, ["TOR"], True)) == ["n2"]
    assert model.select_live(gs, ["COL"], True) == []                      # COL's game is over
    assert ids(model.select_live(gs, ["COL"], True, fallback_when_idle=True)) == ["n1", "n2", "n3"]
    assert ids(model.select_live(gs, ["TOR"], False)) == ["n2", "n1", "n3"]


def test_exclude_beats_the_lock():
    gs = list(games().values())
    assert ids(model.select_live(gs, ["TOR", "MTL"], True, exclude=["MTL"])) == ["n2"]
    assert model.select_live(gs, ["TOR"], True, exclude=["BOS"]) == []


def test_rotation_boost_spreads_favorites():
    gs = model.select_live(list(games().values()), ["TOR"], False)
    assert ids(model.rotation(gs, ["TOR"], 1)) == ["n2", "n1", "n3"]
    assert ids(model.rotation(gs, ["TOR"], 2)) == ["n2", "n2", "n1", "n3"]
    assert ids(model.rotation(gs, ["TOR"], 3)) == ["n2", "n2", "n1", "n2", "n3"]
    assert ids(model.rotation(gs[:1], ["TOR"], 3)) == ["n2"]  # nothing else to spread between


def test_select_by_state_orders_and_limits():
    gs = list(games().values())
    assert ids(model.select_by_state(gs, "post", [], 2, 1, 1800, now=0)) == ["n4"]
    assert ids(model.select_by_state(gs, "pre", ["CAR"], 2, 0, 1800, now=0)) == ["n6"]
    assert ids(model.select_by_state(gs, "pre", [], 2, 5, 1800, now=0)) == ["n5", "n6"]
    assert ids(model.select_by_state(gs, "pre", [], 2, 0, 1800, now=0)) == []
    assert model.select_by_state(gs, "pre", [], 2, 5, 1800, exclude=["FLA", "CAR"], now=0) == []


def test_other_games_rotate_through_the_pool_over_time():
    pool = [{"id": str(i), "state": "pre", "start": float(i), "home": {"abbr": f"H{i}"}, "away": {"abbr": f"A{i}"}} for i in range(5)]
    seen = {model.select_by_state(pool, "pre", [], 0, 1, 1800, now=k * 1800.0)[0]["id"] for k in range(5)}
    assert seen == {"0", "1", "2", "3", "4"}
    now = 7 * 1800 + 5.0
    assert ids(model.select_by_state(pool, "pre", [], 0, 2, 1800, now=now)) == ids(model.select_by_state(pool, "pre", [], 0, 2, 1800, now=now + 60))


# --- runs and bursts --------------------------------------------------------------------------

def snap(home, away, live=True, period=3, clock="5:00", **extra):
    base = copy.deepcopy(games()["n2"])
    base["home"]["score"], base["away"]["score"] = home, away
    base.update(live=live, period=period, clock=clock, run=None)
    base.update(extra)
    return base


def test_lead_helper():
    assert model.lead(snap(3, 1)) == ("home", 2)
    assert model.lead(snap(1, 2)) == ("away", 1)
    assert model.lead(snap(2, 2)) == (None, 0)


def test_scoring_change_picks_the_side_that_outscored():
    assert model.scoring_change(snap(1, 1), snap(2, 1)) == "home"
    assert model.scoring_change(snap(1, 1), snap(1, 3)) == "away"
    assert model.scoring_change(snap(1, 1), snap(2, 2)) is None
    assert model.scoring_change(None, snap(2, 2)) is None


def test_runs_count_unanswered_goals():
    a, b, c, d_ = snap(1, 1), snap(2, 1), snap(4, 1), snap(4, 2)
    run = model.update_run(None, a, b)
    assert run == {"side": "home", "points": 1} and model.run_on_fire({"run": run}) is None
    run = model.update_run(run, b, c)
    assert run == {"side": "home", "points": 3}
    assert model.run_on_fire({"run": run}) == "home"                  # three unanswered goals: the flame
    run = model.update_run(run, c, d_)                                # the other side answers: its own run starts
    assert run == {"side": "away", "points": 1} and model.run_on_fire({"run": run}) is None
    assert model.update_run(run, d_, d_) == run                       # nobody scored
    both = model.update_run(None, snap(1, 1), snap(3, 2))
    assert both == {"side": "home", "points": 1}                      # both scored: only the net margin counts
    assert model.update_run({"side": "home", "points": 2}, snap(1, 1), snap(2, 2)) is None
    assert model.update_run({"side": "home", "points": 3}, snap(3, 1), snap(0, 0)) is None  # a score going down is a reset
    assert model.update_run({"side": "home", "points": 3}, snap(3, 1), snap(3, 1, live=False)) is None


def test_every_goal_earns_a_burst_headlined_by_what_it_did():
    a = model.detect_alert(snap(1, 3), snap(2, 3), False, 123.0)
    assert (a["kind"], a["team"], a["opp"], a["points"], a["desc"]) == ("GOAL", "TOR", "BOS", 1, "TOR SCORES")
    assert a["away_score"] == 3 and a["home_score"] == 2 and a["at"] == 123.0 and a["side"] == "home"
    assert model.detect_alert(snap(4, 1), snap(6, 1), False, 0)["desc"] == "TOR 2 GOALS"
    away = model.detect_alert(snap(4, 1), snap(4, 2), False, 0)
    assert (away["team"], away["side"], away["desc"]) == ("BOS", "away", "BOS SCORES")


def test_go_ahead_equalizer_and_overtime_winner():
    go = model.detect_alert(snap(3, 3), snap(4, 3), False, 0)
    assert (go["kind"], go["desc"]) == ("GO-AHEAD", "TOR TAKES THE LEAD")
    flip = model.detect_alert(snap(2, 3), snap(4, 3), False, 0)
    assert flip["kind"] == "GO-AHEAD" and flip["points"] == 2
    eq = model.detect_alert(snap(2, 3), snap(3, 3), False, 0)
    assert (eq["kind"], eq["desc"]) == ("EQUALIZER", "TOR TIES IT UP")
    win = model.detect_alert(snap(3, 3, period=4), snap(4, 3, period=4), False, 0)
    assert (win["kind"], win["desc"]) == ("OT WINNER", "TOR WINS IT")


def test_lead_changes_only_keeps_the_goals_that_matter():
    assert model.detect_alert(snap(1, 3), snap(2, 3), True, 0) is None
    assert model.detect_alert(snap(4, 1), snap(5, 1), True, 0) is None
    assert model.detect_alert(snap(2, 3), snap(3, 3), True, 0)["kind"] == "EQUALIZER"
    assert model.detect_alert(snap(3, 3), snap(4, 3), True, 0)["kind"] == "GO-AHEAD"


def test_no_burst_when_nothing_scored_or_game_not_live():
    s = snap(2, 2)
    assert model.detect_alert(s, s, False, 0) is None
    assert model.detect_alert(None, s, False, 0) is None
    assert model.detect_alert(snap(2, 2), snap(3, 2, live=False), False, 0) is None
    assert model.detect_alert(snap(1, 1), snap(2, 2), False, 0) is None          # both scored: no burst


def test_burst_names_the_scorer_only_when_the_play_text_does():
    prev, cur = snap(3, 3), snap(4, 3)
    cur["last_play"] = "V.Valtonen scores on a wrist shot"
    assert model.detect_alert(prev, cur, False, 0)["player"]["last"] == "Valtonen"      # a leader of the scoring team
    cur["last_play"] = "B.Nobody scores"
    assert model.detect_alert(prev, cur, False, 0)["player"] is None
    cur["last_play"] = ""
    assert model.detect_alert(prev, cur, False, 0)["player"] is None


# --- puck-drop labels ---------------------------------------------------------------------------------

def test_puckdrop_text():
    now = time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, -1))
    at = lambda days, hour=19, minute=30: time.mktime((2026, 1, 15 + days, hour, minute, 0, 0, 0, -1))  # noqa: E731
    assert model.puckdrop_text(at(0), now) == "TODAY 7:30 PM"
    assert model.puckdrop_text(at(0, 0, 5), now - 1) == "TODAY 12:05 AM"
    assert model.puckdrop_text(at(1, 13, 0), now) == "TMRW 1:00 PM"
    assert model.puckdrop_text(at(3, 19, 0), now) == "SUN 7:00 PM"
    assert model.puckdrop_text(at(10, 19, 0), now) == "1/25 7:00 PM"
    assert model.puckdrop_text(None, now) == ""


def _strip_team_leaders(ev):
    comp = ev["competitions"][0]
    for c in comp["competitors"]:
        c.pop("leaders", None)
    return comp


def test_leaders_are_read_from_the_competition_when_competitors_carry_none():
    """ESPN sends finished games' leaders only at game level (as it does for the NFL): route each to its team."""
    ev = demo.scoreboard(0)["events"][3]            # the final, COL (home) v VGK
    comp = _strip_team_leaders(ev)
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")["id"]
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")["id"]
    entry = lambda pid, name, shown, team: {  # noqa: E731
        "displayValue": shown, "value": float(shown), "team": {"id": team},
        "athlete": {"id": pid, "fullName": name, "position": {"abbreviation": "C"}, "jersey": "1"}}
    comp["leaders"] = [
        {"name": "goals", "leaders": [entry("1", "Home Scorer", "3", home), entry("2", "Away Scorer", "2", away)]},
        {"name": "points", "leaders": [entry("1", "Home Scorer", "4", home)]},
        {"name": "assists", "leaders": [entry("3", "Away Dime", "3", away)]},
    ]
    game = model.parse_event(ev)
    assert [(p["name"], p["category"], p["value"]) for p in game["home"]["leaders"]] == [("Home Scorer", "g", 3)]
    assert game["home"]["leaders"][0]["parts"] == [("4", "PTS")] and game["home"]["leaders"][0]["badge"] == "HAT TRICK"
    assert sorted(p["name"] for p in game["away"]["leaders"]) == ["Away Dime", "Away Scorer"]
    assert len(game["stars"]) == 3 and game["stars"][0]["name"] == "Home Scorer"


def test_game_level_leaders_find_the_team_through_the_athlete():
    ev = demo.scoreboard(0)["events"][3]
    comp = _strip_team_leaders(ev)
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")["id"]
    comp["leaders"] = [{"name": "goalsLeader", "leaders": [
        {"displayValue": "2", "value": 2.0, "athlete": {"id": "8", "fullName": "Via Athlete", "team": {"id": home}}}]}]
    game = model.parse_event(ev)
    assert [p["name"] for p in game["home"]["leaders"]] == ["Via Athlete"] and game["away"]["leaders"] == []


def test_competitor_leaders_win_over_game_level_ones_and_pre_games_ignore_them():
    ev = demo.scoreboard(0)["events"][0]
    ev["competitions"][0]["leaders"] = [{"name": "goals", "leaders": [
        {"displayValue": "1", "value": 1.0, "team": {"id": "10"}, "athlete": {"id": "999", "fullName": "Nobody"}}]}]
    game = model.parse_event(ev)
    assert "Nobody" not in {p["name"] for p in game["stars"]} and game["stars"]
    pre = demo.scoreboard(0)["events"][4]
    _strip_team_leaders(pre)["leaders"] = ev["competitions"][0]["leaders"]
    assert model.parse_event(pre)["stars"] == []


def test_game_level_leaders_survive_junk():
    ev = demo.scoreboard(0)["events"][3]
    comp = _strip_team_leaders(ev)
    comp["leaders"] = ["x", {"name": "goals"}, {"name": "goals", "leaders": ["y", {"athlete": {"id": "1"}}]}]
    assert model.parse_event(ev)["stars"] == []
    comp["leaders"] = "nonsense"
    assert model.parse_event(ev)["stars"] == []
