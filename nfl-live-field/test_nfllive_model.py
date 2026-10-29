"""Pure-logic tests: parsing, ball position, the favorites lock, rotation."""

import os
import time
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nfllive_model as model  # noqa: E402


def team(tid, abbr, score=0, color="112233", alt="ffffff"):
    return {"id": tid, "abbr": abbr, "name": abbr, "score": score, "color": color, "alt_color": alt}


def event(eid, home, away, state="in", situation=None, period=2, clock="5:00"):
    h_abbr, h_id, h_score = home
    a_abbr, a_id, a_score = away
    comp = {
        "status": {"period": period, "displayClock": clock,
                   "type": {"state": state, "name": "STATUS_IN_PROGRESS", "shortDetail": "x"}},
        "competitors": [
            {"homeAway": "home", "id": h_id, "score": str(h_score),
             "team": {"id": h_id, "abbreviation": h_abbr, "color": "004c54", "alternateColor": "a5acaf"}},
            {"homeAway": "away", "id": a_id, "score": str(a_score),
             "team": {"id": a_id, "abbreviation": a_abbr, "color": "002244", "alternateColor": "869397"}},
        ],
    }
    if situation is not None:
        comp["situation"] = situation
    return {"id": eid, "date": "2026-01-14T01:15Z", "competitions": [comp]}


def games(*specs):
    return model.parse_scoreboard({"events": [event(*s) for s in specs]})


# --- parsing ---------------------------------------------------------------

def test_parse_live_game_basics():
    g = model.parse_event(event("1", ("PHI", "21", 17), ("DAL", "6", 14), period=3, clock="8:12"))
    assert g["live"] and not g["final"]
    assert (g["home"]["abbr"], g["home"]["score"]) == ("PHI", 17)
    assert (g["away"]["abbr"], g["away"]["score"]) == ("DAL", 14)
    assert g["period_text"] == "Q3" and g["clock"] == "8:12"
    assert g["start"] == 1768353300.0


def test_bad_events_are_skipped_not_fatal():
    payload = {"events": [{"id": "x"}, {"competitions": []}, event("1", ("PHI", "21", 0), ("DAL", "6", 0))]}
    assert [g["id"] for g in model.parse_scoreboard(payload)] == ["1"]
    assert model.parse_scoreboard({}) == []
    assert model.parse_scoreboard(None) == []


def test_aliases_fold_to_espn_codes():
    assert model.norm_team("was") == "WSH"
    assert model.norm_team_list(["was", "WSH", " phi ", ""]) == ["WSH", "PHI"]


def test_period_text():
    assert model.period_text(1, "STATUS_IN_PROGRESS", "") == "Q1"
    assert model.period_text(5, "STATUS_IN_PROGRESS", "") == "OT"
    assert model.period_text(6, "STATUS_IN_PROGRESS", "") == "OT2"
    assert model.period_text(2, "STATUS_HALFTIME", "Halftime") == "HALF"


# --- ball position ---------------------------------------------------------

def test_ball_from_possession_text_home_side():
    sit = {"possession": "6", "possessionText": "PHI 20", "distance": 10}
    g = model.parse_event(event("1", ("PHI", "21", 0), ("DAL", "6", 0), situation=sit))
    # 20 yards from PHI's (home, right) goal line = 80 yards from the away (left) goal
    assert g["ball"] == 80.0
    assert g["possession"] == "away"
    assert g["first_down"] == 90.0  # away drives right, 10 to go


def test_ball_from_possession_text_away_side():
    sit = {"possession": "21", "possessionText": "DAL 35", "distance": 7}
    g = model.parse_event(event("1", ("PHI", "21", 0), ("DAL", "6", 0), situation=sit))
    assert g["ball"] == 35.0
    assert g["first_down"] == 28.0  # home drives left


def test_ball_falls_back_to_yard_line_when_no_text():
    sit = {"possession": "6", "yardLine": 25, "distance": 5}
    g = model.parse_event(event("1", ("PHI", "21", 0), ("DAL", "6", 0), situation=sit))
    assert g["ball"] == 25.0  # away team's own 25 = 25 from the left goal


def test_no_situation_means_no_ball_and_no_crash():
    g = model.parse_event(event("1", ("PHI", "21", 0), ("DAL", "6", 0)))
    assert g["ball"] is None and g["first_down"] is None and g["possession"] is None


def test_goal_to_go_line_is_clamped_to_the_end_zone():
    assert model.first_down_marker(97.0, 10, True) == 100.0
    assert model.first_down_marker(3.0, 10, False) == 0.0
    assert model.first_down_marker(50.0, 0, True) is None


# --- the favorites lock ----------------------------------------------------

LIVE = games(
    ("a", ("PHI", "21", 0), ("DAL", "6", 0)),
    ("b", ("KC", "12", 0), ("BUF", "2", 0)),
    ("c", ("NYG", "19", 0), ("WSH", "28", 0)),
    ("d", ("SF", "25", 0), ("SEA", "26", 0), "post"),
)


def ids(selection):
    return [g["id"] for g in selection]


def test_lock_on_shows_only_favorite_games():
    assert ids(model.select_live(LIVE, ["KC"], lock=True)) == ["b"]
    assert ids(model.select_live(LIVE, ["KC", "DAL"], lock=True)) == ["a", "b"]


def test_lock_on_and_no_favorite_playing_shows_nothing():
    assert model.select_live(LIVE, ["SF"], lock=True) == []  # SF's game is final


def test_lock_fallback_shows_everything_else_until_favorite_plays():
    assert ids(model.select_live(LIVE, ["SF"], lock=True, fallback_when_idle=True)) == ["a", "b", "c"]
    assert ids(model.select_live(LIVE, ["KC"], lock=True, fallback_when_idle=True)) == ["b"]


def test_lock_off_puts_favorites_first():
    assert ids(model.select_live(LIVE, ["WSH"], lock=False)) == ["c", "a", "b"]


def test_lock_with_no_favorites_has_nothing_to_lock_to():
    assert ids(model.select_live(LIVE, [], lock=True)) == ["a", "b", "c"]


def test_finished_games_are_never_selected():
    assert "d" not in ids(model.select_live(LIVE, [], lock=False))


def test_exclude_beats_the_lock_and_the_favorites():
    assert model.select_live(LIVE, ["KC"], lock=True, exclude=["BUF"]) == []
    assert ids(model.select_live(LIVE, ["KC", "DAL"], lock=False, exclude=["KC"])) == ["a", "c"]


# --- rotation ---------------------------------------------------------------

def test_rotation_boost_spreads_favorite_turns():
    sel = model.select_live(LIVE, ["KC"], lock=False)  # b, a, c
    assert ids(model.rotation(sel, ["KC"], boost=1)) == ["b", "a", "c"]
    assert ids(model.rotation(sel, ["KC"], boost=2)) == ["b", "b", "a", "c"]


def test_rotation_with_only_favorites_never_repeats_back_to_back():
    sel = model.select_live(LIVE, ["KC"], lock=True)
    assert ids(model.rotation(sel, ["KC"], boost=3)) == ["b"]


# --- score changes ----------------------------------------------------------

def test_scoring_change_detects_the_side():
    before = games(("a", ("PHI", "21", 7), ("DAL", "6", 7)))[0]
    after = games(("a", ("PHI", "21", 7), ("DAL", "6", 14)))[0]
    assert model.scoring_change(before, after) == "away"
    assert model.scoring_change(after, after) is None
    assert model.scoring_change(None, after) is None


# --- players, rarity and score alerts ---------------------------------------

import nfllive_demo as demo  # noqa: E402


def demo_games(step=0):
    return {g["id"]: g for g in model.parse_scoreboard(demo.scoreboard(step))}


def test_leaders_become_player_cards_with_stats():
    kc = demo_games()["t2"]
    mahomes = next(p for p in kc["stars"] if p["last"] == "Mahomes")
    assert (mahomes["pos"], mahomes["team"], mahomes["jersey"], mahomes["category"]) == ("QB", "KC", "15", "pass")
    assert (mahomes["yards"], mahomes["td"]) == (331, 3)
    assert ("27/35", "C/A") in mahomes["parts"] and ("3", "TD") in mahomes["parts"]
    assert mahomes["headshot_url"].startswith("https://a.espncdn.com/")


def test_stat_parts_cover_every_leader_shape():
    assert model.parse_stat_parts("22/31, 245 YDS, 2 TD, 1 INT") == [("22/31", "C/A"), ("245", "YD"), ("2", "TD"), ("1", "INT")]
    assert model.parse_stat_parts("18 CAR, 96 YDS, 1 TD") == [("18", "CAR"), ("96", "YD"), ("1", "TD")]
    assert model.parse_stat_parts("7 REC, 112 YDS") == [("7", "REC"), ("112", "YD")]
    assert model.parse_stat_parts("") == [] and model.parse_stat_parts(None) == []


def test_touchdowns_count_toward_rarity():
    assert model.tier_for("pass", 300.0) == "epic"
    assert model.tier_for("pass", 260.0 + 2 * model.TD_YARD_VALUE) == "epic"
    assert model.tier_for("rush", 60.0) == "common" and model.tier_for("rush", 65.0) == "rare"
    assert model.tier_for("rec", 400.0) == "legendary"


def test_next_threshold_walks_up_the_tiers():
    assert model.next_threshold("rush", 10) == 65.0
    assert model.next_threshold("rush", 70) == 110.0
    assert model.next_threshold("rush", 120) == 170.0
    assert model.next_threshold("rush", 500) == 170.0


def test_stars_rank_by_rarity_and_favorites_lead():
    kc = demo_games()["t2"]
    assert [p["last"] for p in kc["stars"][:2]] == ["Mahomes", "Allen"]
    assert [p["last"] for p in model.stars_for(kc, 2)] == ["Mahomes", "Allen"]
    assert [p["last"] for p in model.stars_for(kc, 2, ["BUF"])][0] in ("Allen", "Cook", "Shakir")
    assert model.stars_for(kc, 0) == []


def test_names_split_with_suffixes():
    assert model.split_name("Amon-Ra St. Brown Jr.") == ("Amon-Ra St.", "Brown")
    assert model.split_name("Kelce") == ("", "Kelce")
    assert model.split_name("") == ("", "")


def test_alert_kinds():
    assert [model.alert_kind(n) for n in (7, 6, 3, 2, 1)] == ["TOUCHDOWN", "TOUCHDOWN", "FIELD GOAL", "SAFETY", "EXTRA POINT"]


def test_build_alert_names_the_scorer_from_the_play_text():
    before, after = demo_games(0)["t2"], demo_games(3)["t2"]
    assert model.scoring_change(before, after) == "home"
    alert = model.build_alert(before, after, "home", 5.0)
    assert (alert["team"], alert["opp"], alert["kind"], alert["points"]) == ("KC", "BUF", "TOUCHDOWN", 7)
    assert alert["player"]["last"] == "Kelce"  # 'Mahomes pass ... to Kelce': the LAST name mentioned
    assert alert["home_score"] == 31 and alert["away_score"] == 27


def test_build_alert_without_a_matching_name_has_no_player():
    before = demo_games(0)["t1"]
    after = dict(before, last_play="Team penalty", home=dict(before["home"], score=before["home"]["score"] + 3))
    alert = model.build_alert(before, after, "home", 0.0)
    assert alert["kind"] == "FIELD GOAL" and alert["player"] is None


def test_real_espn_leader_category_names_are_recognized():
    """Confirmed against a live site.api.espn.com scoreboard fetch (2026-10-02):
    a team's own leader is named 'passingLeader'/'rushingLeader'/'receivingLeader',
    not 'passingYards'/'rushingYards'/'receivingYards'. A regression here means
    every real game's stat cards silently go empty, as happened before this test
    was added -- the demo data matched the wrong assumption, so nothing caught it.
    """
    real_shape = [{
        "name": "passingLeader", "displayName": "Passing Leader",
        "leaders": [{"displayValue": "245 YDS, 2 TD", "value": 245.0,
                    "athlete": {"id": "1", "fullName": "Test Passer", "jersey": "1",
                                "position": {"abbreviation": "QB"},
                                "headshot": "https://a.espncdn.com/i/headshots/nfl/players/full/1.png"}}],
    }]
    players = model.parse_leaders(real_shape, "KC")
    assert len(players) == 1
    assert players[0]["category"] == "pass" and players[0]["yards"] == 245


def test_kickoff_text_counts_calendar_days_not_24_hour_blocks():
    now = time.mktime((2026, 1, 15, 12, 0, 0, 0, 0, -1))
    at = lambda days, hour, minute=0: time.mktime((2026, 1, 15 + days, hour, minute, 0, 0, 0, -1))  # noqa: E731
    assert model.kickoff_text(at(0, 20, 20), now) == "TODAY 8:20 PM"
    assert model.kickoff_text(at(1, 1, 0), now) == "TMRW 1:00 AM"      # 13 hours away, but tomorrow
    assert model.kickoff_text(at(1, 23, 0), now) == "TMRW 11:00 PM"    # 35 hours away
    assert model.kickoff_text(at(3, 13, 0), now) == "SUN 1:00 PM"
    assert model.kickoff_text(at(10, 13, 0), now) == "1/25 1:00 PM"
    assert model.kickoff_text(None, now) == ""


def test_leaders_are_read_from_the_competition_when_competitors_carry_none():
    """If ESPN only sends game-level leaders (one list for the game), each entry is routed to its team."""
    ev = demo.scoreboard(0)["events"][0]
    comp = ev["competitions"][0]
    home_id = next(c["id"] for c in comp["competitors"] if c["homeAway"] == "home")
    away_id = next(c["id"] for c in comp["competitors"] if c["homeAway"] == "away")
    entry = lambda pid, name, shown, value, team: {  # noqa: E731
        "displayValue": shown, "value": value, "team": {"id": team},
        "athlete": {"id": pid, "fullName": name, "position": {"abbreviation": "QB"}, "jersey": "1"}}
    for c in comp["competitors"]:
        c.pop("leaders", None)
    comp["leaders"] = [
        {"name": "passingYards", "leaders": [entry("1", "Home Passer", "20/30, 250 YDS, 2 TD", 250.0, home_id),
                                             entry("2", "Away Passer", "18/29, 190 YDS, 1 TD", 190.0, away_id)]},
        {"name": "rushingYards", "leaders": [entry("3", "Home Runner", "15 CAR, 90 YDS", 90.0, home_id)]},
    ]
    game = model.parse_event(ev)
    assert [p["name"] for p in game["home"]["leaders"]] == ["Home Passer", "Home Runner"]
    assert [p["name"] for p in game["away"]["leaders"]] == ["Away Passer"]
    assert {p["name"] for p in game["stars"]} == {"Home Passer", "Away Passer", "Home Runner"}
    assert next(p for p in game["stars"] if p["name"] == "Home Passer")["yards"] == 250


def test_competitor_leaders_win_over_game_level_ones():
    ev = demo.scoreboard(0)["events"][0]
    ev["competitions"][0]["leaders"] = [{"name": "passingYards", "leaders": [
        {"displayValue": "1 YDS", "value": 1.0, "team": {"id": "21"}, "athlete": {"id": "999", "fullName": "Nobody"}}]}]
    game = model.parse_event(ev)
    assert "Nobody" not in {p["name"] for p in game["stars"]} and game["stars"]


def test_game_level_leaders_survive_junk():
    ev = demo.scoreboard(0)["events"][0]
    comp = ev["competitions"][0]
    for c in comp["competitors"]:
        c.pop("leaders", None)
    comp["leaders"] = ["x", {"name": "passingYards"}, {"name": "passingYards", "leaders": ["y", {"athlete": {"id": "1"}}]}]
    assert model.parse_event(ev)["stars"] == []
    comp["leaders"] = "nonsense"
    assert model.parse_event(ev)["stars"] == []


def _entry(team_id, name, value, text):
    return {"team": {"id": team_id}, "leaders": [{"name": name, "leaders": [{
        "value": value, "displayValue": text,
        "athlete": {"id": f"{team_id}{name}", "fullName": f"Test {name}", "position": {"abbreviation": "QB"}}}]}]}


def test_summary_leaders_give_the_losing_team_a_player():
    game = {"home": {"id": "21", "abbr": "PHI", "leaders": []}, "away": {"id": "6", "abbr": "DAL", "leaders": []}, "stars": []}
    summary = {"leaders": [_entry("21", "passingYards", 211, "23/35, 211 YDS, 1 TD"),
                           _entry("6", "passingYards", 212, "15/27, 212 YDS, 3 TD")]}
    assert model.apply_summary_leaders(game, summary) is True
    assert {p["team"] for p in game["stars"]} == {"PHI", "DAL"}
    assert game["home"]["leaders"][0]["yards"] == 211


def test_summary_leaders_ignore_junk():
    game = {"home": {"id": "1", "abbr": "A", "leaders": []}, "away": {"id": "2", "abbr": "B", "leaders": []}, "stars": []}
    assert model.apply_summary_leaders(game, None) is False
    assert model.apply_summary_leaders(game, {"leaders": ["x", {"team": {"id": "9"}, "leaders": []}]}) is False
    assert game["stars"] == []
