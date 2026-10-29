"""Offline demo data for ``advanced.test_mode``, the harness and the tests.

Six ESPN-shaped NBA games -- three live, one final, two upcoming -- with
per-team leaders (so the player cards have something to show), quarter-by-quarter
scores, records and a spread, plus a procedurally drawn headshot per player (so
the photo path runs without a network). The Lakers-Nuggets game flips between
two states every few updates so the big-run burst, the lead-change burst and
the run flame can be seen on a quiet Tuesday.

The teams are real; **the players are made up**, so test mode never shows a
real person's name next to numbers they did not score, and never needs a photo
download. The payload shape (``leaders[].name`` of ``points`` / ``rebounds`` /
``assists``, ``linescores``, ``records``) follows ESPN's public scoreboard.
"""

import io
import time as _time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

# abbr -> (ESPN team id, colour, alternate colour)
TEAMS = {
    "PHI": ("20", "006bb6", "ed174c"), "BOS": ("2", "008248", "bb9753"),
    "LAL": ("13", "552583", "fdb927"), "DEN": ("7", "0d2240", "fec524"),
    "MIL": ("15", "00471b", "eee1c6"), "MIA": ("14", "98002e", "f9a01b"),
    "GS": ("9", "1d428a", "ffc72c"), "OKC": ("25", "007ac1", "ef3b24"),
    "NY": ("18", "006bb6", "f58426"), "DAL": ("6", "0053bc", "b8c4ca"),
    "PHX": ("21", "1d1160", "e56020"), "MIN": ("16", "236192", "9ea2a2"),
}

# abbr -> category -> (id, full name, jersey, position). Invented players.
PLAYERS = {
    "PHI": {"points": ("910001", "Darnell Quigley", "3", "G"), "rebounds": ("910002", "Omar Valdivia", "21", "C"),
            "assists": ("910003", "Reggie Tolliver", "7", "G")},
    "BOS": {"points": ("910011", "Caleb Montrose", "9", "F"), "rebounds": ("910012", "Anton Pelletier", "44", "C"),
            "assists": ("910013", "Isaiah Brandt", "4", "G")},
    "LAL": {"points": ("910021", "Marcus Hale", "23", "F"), "rebounds": ("910021", "Marcus Hale", "23", "F"),
            "assists": ("910022", "Devin Okafor", "1", "G")},
    "DEN": {"points": ("910031", "Viktor Hargrove", "15", "C"), "rebounds": ("910031", "Viktor Hargrove", "15", "C"),
            "assists": ("910031", "Viktor Hargrove", "15", "C")},
    "MIL": {"points": ("910041", "Andre Castellano", "34", "F"), "rebounds": ("910042", "Lamar Fitch", "12", "C"),
            "assists": ("910043", "Joey Brannigan", "2", "G")},
    "MIA": {"points": ("910051", "Rashad Whitlock", "22", "G"), "rebounds": ("910052", "Kenji Morrow", "13", "C"),
            "assists": ("910053", "Tobias Lang", "0", "G")},
    "GS": {"points": ("910061", "Sterling Okonkwo", "30", "G"), "rebounds": ("910062", "Declan Ruiz", "23", "F"),
           "assists": ("910063", "Mateo Vasquez", "11", "G")},
    "OKC": {"points": ("910071", "Jabari Thornton", "2", "G"), "rebounds": ("910072", "Chet Holloway", "7", "C"),
            "assists": ("910073", "Ezra Whitfield", "5", "G")},
}

_UNITS = {"points": "PTS", "rebounds": "REB", "assists": "AST"}


def _leaders(abbr: str, stats: Optional[Dict[str, int]]) -> List[Dict[str, Any]]:
    out = []
    for cat, (pid, name, jersey, pos) in PLAYERS.get(abbr, {}).items():
        if not stats or cat not in stats:
            continue
        out.append({"name": cat, "displayName": cat.title(), "abbreviation": _UNITS[cat], "leaders": [{
            "displayValue": str(stats[cat]), "value": float(stats[cat]),
            "athlete": {"id": pid, "fullName": name, "displayName": name, "shortName": name,
                        "jersey": jersey, "position": {"abbreviation": pos},
                        "headshot": f"https://a.espncdn.com/i/headshots/nba/players/full/{pid}.png"}}]})
    return out


def _side(kind: str, abbr: str, score: int, lines: List[int], record: str,
          stats: Optional[Dict[str, int]]) -> Dict[str, Any]:
    tid, primary, alt = TEAMS[abbr]
    return {"homeAway": kind, "id": tid, "score": str(score), "leaders": _leaders(abbr, stats),
            "linescores": [{"value": float(v)} for v in lines],
            "records": [{"name": "overall", "type": "total", "summary": record}] if record else [],
            "team": {"id": tid, "abbreviation": abbr, "color": primary, "alternateColor": alt,
                     "displayName": abbr, "shortDisplayName": abbr}}


def event(eid: str, home: str, away: str, hs: int, as_: int, period: int, clock: str, state: str = "in",
          date: str = "2026-01-01T00:00Z", home_lines=(), away_lines=(), home_stats=None, away_stats=None,
          home_record: str = "", away_record: str = "", odds: str = "", last_play: str = "",
          halftime: bool = False) -> Dict[str, Any]:
    if halftime:
        name, short_detail = "STATUS_HALFTIME", "Halftime"
    else:
        name, short_detail = {
            "in": ("STATUS_IN_PROGRESS", f"{clock} - {period}Q"),
            "post": ("STATUS_FINAL", "Final"),
            "pre": ("STATUS_SCHEDULED", "Scheduled"),
        }.get(state, ("STATUS_IN_PROGRESS", clock))
    comp: Dict[str, Any] = {
        "status": {"period": period, "displayClock": clock,
                   "type": {"state": state, "name": name, "shortDetail": short_detail}},
        "competitors": [_side("home", home, hs, list(home_lines), home_record, home_stats),
                        _side("away", away, as_, list(away_lines), away_record, away_stats)],
    }
    if odds:
        comp["odds"] = [{"details": odds}]
    if last_play:
        comp["situation"] = {"lastPlay": {"text": last_play}}
    return {"id": eid, "date": date, "competitions": [comp]}


def _iso(epoch: float) -> str:
    return _time.strftime("%Y-%m-%dT%H:%MZ", _time.gmtime(epoch))


def scoreboard(step: int = 0, now: Optional[float] = None) -> Dict[str, Any]:
    """The demo slate. Every sixth ``step`` the Lakers have a 9-0 surge to take the lead.

    Steps 0-2 show LAL trailing 95-100; steps 3-5 show them leading 104-100 after
    the surge, which is a lead change in the fourth quarter *and* a big run.
    A final and two upcoming games (tip-off a few hours and a day out, relative
    to ``now``) sit beside the three live ones, so recent- and upcoming-game
    screens have something to show too.
    """
    now = _time.time() if now is None else now
    surged = (step % 6) >= 3
    lal = 104 if surged else 95
    slate = {"events": [
        event("n1", "PHI", "BOS", 71, 68, 3, "5:42", home_lines=(24, 21, 26), away_lines=(19, 25, 24),
              home_stats={"points": 24, "rebounds": 9, "assists": 8}, away_stats={"points": 22, "rebounds": 8, "assists": 6},
              home_record="21-9", away_record="23-7", odds="BOS -2.5",
              last_play="D.Quigley makes 26-foot three point jumper"),
        event("n2", "LAL", "DEN", lal, 100, 4, "2:03", home_lines=(22, 27, 24, 22 if surged else 13),
              away_lines=(28, 20, 26, 26),
              home_stats={"points": 31, "rebounds": 12, "assists": 7}, away_stats={"points": 33, "rebounds": 14, "assists": 11},
              home_record="18-14", away_record="20-12", odds="DEN -1.5",
              last_play=("M.Hale makes driving layup" if surged else "V.Hargrove makes 14-foot pullup jump shot")),
        event("n3", "MIL", "MIA", 22, 19, 1, "8:20", home_lines=(22,), away_lines=(19,),
              home_stats={"points": 9, "rebounds": 4, "assists": 3}, away_stats={"points": 8, "rebounds": 5, "assists": 2},
              home_record="16-15", away_record="17-13"),
        event("n4", "GS", "OKC", 118, 112, 4, "0:00", state="post", date=_iso(now - 3 * 3600),
              home_lines=(31, 27, 30, 30), away_lines=(28, 30, 25, 29),
              home_stats={"points": 38, "rebounds": 11, "assists": 12}, away_stats={"points": 41, "rebounds": 13, "assists": 9},
              home_record="19-12", away_record="26-5"),
        event("n5", "NY", "DAL", 0, 0, 0, "0:00", state="pre", date=_iso(now + 5 * 3600),
              home_record="22-9", away_record="15-17", odds="NY -6.5"),
        event("n6", "PHX", "MIN", 0, 0, 0, "0:00", state="pre", date=_iso(now + 26 * 3600),
              home_record="17-14", away_record="20-11"),
    ]}
    return slate


def headshot_png(seed: int, jersey_color: Tuple[int, int, int] = (60, 70, 120)) -> bytes:
    """A stand-in head-and-shoulders PNG (transparent background, ESPN's 600x436 framing scaled down)."""
    w, h = 200, 145
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    skins = [(232, 190, 160), (198, 145, 108), (141, 96, 66), (96, 64, 44)]
    skin = skins[seed % len(skins)]
    draw.polygon([(30, h), (48, 96), (100 - 46, 92), (100 + 46, 92), (w - 48, 96), (w - 30, h)], fill=jersey_color + (255,))
    draw.rectangle([w // 2 - 14, 74, w // 2 + 14, 98], fill=tuple(int(c * 0.85) for c in skin) + (255,))
    draw.ellipse([w // 2 - 30, 14, w // 2 + 30, 88], fill=skin + (255,))
    draw.ellipse([w // 2 - 32, 8, w // 2 + 32, 44], fill=(28, 28, 34, 255))  # helmet-ish hair
    draw.rectangle([w // 2 - 18, 52, w // 2 - 10, 56], fill=(30, 24, 20, 255))
    draw.rectangle([w // 2 + 10, 52, w // 2 + 18, 56], fill=(30, 24, 20, 255))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def seed_headshots(store, games: List[Dict[str, Any]], now: Optional[float] = None) -> int:
    """Put a synthetic photo in the store's cache for every star of ``games`` (no network)."""
    import base64
    import time as _time
    now = _time.time() if now is None else now
    done = 0
    for game in games:
        for p in game.get("stars", []):
            side = game["home"] if game["home"]["abbr"] == p["team"] else game["away"]
            color = tuple(int(side["color"][i:i + 2], 16) for i in (0, 2, 4))
            png = headshot_png(int(p["id"]) % 97, color)
            store.data.write(store.data.key("headshot", p["id"]),
                             {"png_b64": base64.b64encode(png).decode("ascii")}, now)
            done += 1
    return done
