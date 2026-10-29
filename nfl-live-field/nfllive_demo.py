"""Offline demo data for ``advanced.test_mode``, the harness and the tests.

Three ESPN-shaped live games with per-team leaders (so the player cards have
something to show), a procedurally drawn headshot per player (so the photo path
runs without a network), and a scoring play that fires every few updates so the
touchdown burst can be seen on a quiet Tuesday.
"""

import io
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

# abbr, id, primary, alternate, score, timeouts
PHI = ("PHI", "21", "004c54", "a5acaf")
DAL = ("DAL", "6", "002244", "869397")
KC = ("KC", "12", "e31837", "ffb612")
BUF = ("BUF", "2", "00338d", "c60c30")
NYG = ("NYG", "19", "0b2265", "a71930")
WSH = ("WSH", "28", "5a1414", "ffb612")

# (id, full name, jersey, position, display value) per category
LEADERS = {
    "PHI": {"passingYards": ("4040715", "Jalen Hurts", "1", "QB", "24/31, 268 YDS, 2 TD", 268),
            "rushingYards": ("4241478", "Saquon Barkley", "26", "RB", "19 CAR, 121 YDS, 1 TD", 121),
            "receivingYards": ("4361741", "A.J. Brown", "11", "WR", "7 REC, 112 YDS, 1 TD", 112)},
    "DAL": {"passingYards": ("3116385", "Dak Prescott", "4", "QB", "19/28, 211 YDS, 1 TD, 1 INT", 211),
            "rushingYards": ("4361411", "Javonte Williams", "33", "RB", "11 CAR, 48 YDS", 48),
            "receivingYards": ("4241389", "CeeDee Lamb", "88", "WR", "8 REC, 96 YDS, 1 TD", 96)},
    "KC": {"passingYards": ("3139477", "Patrick Mahomes", "15", "QB", "27/35, 331 YDS, 3 TD", 331),
           "rushingYards": ("4362887", "Isiah Pacheco", "10", "RB", "14 CAR, 62 YDS", 62),
           "receivingYards": ("15847", "Travis Kelce", "87", "TE", "9 REC, 118 YDS, 2 TD", 118)},
    "BUF": {"passingYards": ("3918298", "Josh Allen", "17", "QB", "22/30, 289 YDS, 2 TD", 289),
            "rushingYards": ("4258173", "James Cook", "4", "RB", "16 CAR, 88 YDS, 1 TD", 88),
            "receivingYards": ("4262921", "Khalil Shakir", "10", "WR", "6 REC, 71 YDS", 71)},
    "NYG": {"passingYards": ("4426385", "Jaxson Dart", "6", "QB", "15/24, 152 YDS, 1 TD", 152),
            "rushingYards": ("4429795", "Cam Skattebo", "44", "RB", "12 CAR, 41 YDS", 41)},
    "WSH": {"passingYards": ("4241479", "Jayden Daniels", "5", "QB", "17/23, 189 YDS, 1 TD", 189),
            "receivingYards": ("3121422", "Terry McLaurin", "17", "WR", "5 REC, 77 YDS, 1 TD", 77)},
    "SF": {"passingYards": ("4361741", "Brock Purdy", "13", "QB", "21/29, 214 YDS, 2 TD", 214),
           "rushingYards": ("4372016", "Christian McCaffrey", "23", "RB", "15 CAR, 74 YDS, 1 TD", 74)},
    "SEA": {"passingYards": ("4360423", "Geno Smith", "7", "QB", "18/27, 176 YDS, 1 TD, 1 INT", 176),
            "receivingYards": ("4262921", "DK Metcalf", "14", "WR", "4 REC, 61 YDS", 61)},
}


#: maps this file's internal category key to the real ESPN per-team leader
#: name (confirmed against a live scoreboard fetch on 2026-10-02).
_ESPN_LEADER_NAME = {"passingYards": "passingLeader", "rushingYards": "rushingLeader",
                     "receivingYards": "receivingLeader"}


def _leaders(abbr: str) -> List[Dict[str, Any]]:
    out = []
    for cat, (pid, name, jersey, pos, shown, value) in LEADERS.get(abbr, {}).items():
        out.append({"name": _ESPN_LEADER_NAME[cat], "leaders": [{
            "displayValue": shown, "value": float(value),
            "athlete": {"id": pid, "fullName": name, "displayName": name, "shortName": name,
                        "jersey": jersey, "position": {"abbreviation": pos},
                        "headshot": f"https://a.espncdn.com/i/headshots/nfl/players/full/{pid}.png"}}]})
    return out


def _side(kind: str, spec: Tuple[str, str, str, str], score: int) -> Dict[str, Any]:
    abbr, tid, primary, alt = spec
    return {"homeAway": kind, "id": tid, "score": str(score), "leaders": _leaders(abbr),
            "team": {"id": tid, "abbreviation": abbr, "color": primary, "alternateColor": alt,
                     "displayName": abbr, "shortDisplayName": abbr}}


def event(eid: str, home, away, hs: int, as_: int, period: int, clock: str, poss: str,
          poss_text: str, distance: int, short_dd: str, home_to: int = 3, away_to: int = 3,
          red_zone: bool = False, last_play: str = "", play_points: int = 0, state: str = "in",
          date: str = "2026-01-01T00:00Z") -> Dict[str, Any]:
    situation = {"possession": poss, "possessionText": poss_text, "distance": distance,
                 "shortDownDistanceText": short_dd, "downDistanceText": short_dd,
                 "isRedZone": red_zone, "homeTimeouts": home_to, "awayTimeouts": away_to,
                 "lastPlay": {"text": last_play, "scoreValue": play_points}}
    status_by_state = {
        "in": ("STATUS_IN_PROGRESS", f"Q{period} {clock}"),
        "post": ("STATUS_FINAL", "Final"),
        "pre": ("STATUS_SCHEDULED", "Scheduled"),
    }
    name, short_detail = status_by_state.get(state, ("STATUS_IN_PROGRESS", f"Q{period} {clock}"))
    comp = {
        "status": {"period": period, "displayClock": clock,
                   "type": {"state": state, "name": name, "shortDetail": short_detail}},
        "competitors": [_side("home", home, hs), _side("away", away, as_)],
    }
    if state == "in":
        comp["situation"] = situation
    return {"id": eid, "date": date, "competitions": [comp]}


def _iso(epoch: float) -> str:
    import time as _time
    return _time.strftime("%Y-%m-%dT%H:%MZ", _time.gmtime(epoch))


def scoreboard(step: int = 0, now: Optional[float] = None) -> Dict[str, Any]:
    """The demo slate. Every third ``step`` KC scores a touchdown and the play text says who.

    A final (SF-SEA) and two upcoming games (kickoff a few hours and a few
    days out, relative to ``now``) are included alongside the three live
    games, so recent- and upcoming-game screens have something to show too.
    """
    import time as _time
    now = _time.time() if now is None else now
    kc_score = 24 + (7 * (step // 3))
    scored = step % 3 == 0 and step > 0
    slate = {"events": [
        event("t1", PHI, DAL, 17, 14, 3, "8:12", "21", "DAL 35", 7, "2nd & 7", 2, 3,
              last_play="J.Hurts pass short right to A.Brown for 11 yards"),
        event("t2", KC, BUF, kc_score, 27, 4, "2:03", "12", "BUF 12", 4, "3rd & 4", 3, 1, red_zone=True,
              last_play=("P.Mahomes pass short middle to T.Kelce for 12 yards, TOUCHDOWN" if scored
                         else "P.Mahomes pass incomplete deep left to R.Rice"),
              play_points=6 if scored else 0),
        event("t3", NYG, WSH, 10, 13, 1, "11:20", "28", "NYG 41", 10, "1st & 10", 3, 3,
              last_play="J.Daniels scrambles right end for 6 yards"),
        event("t4", ("SF", "25", "aa0000", "b3995d"), ("SEA", "26", "002244", "69be28"), 27, 23, 4, "0:00", "", "", 0, "",
              state="post", date=_iso(now - 3 * 3600)),
        event("t5", ("DEN", "7", "fb4f14", "002244"), ("LAC", "24", "0080c6", "ffc20e"), 0, 0, 0, "0:00", "", "", 0, "",
              state="pre", date=_iso(now + 5 * 3600)),
        event("t6", ("MIN", "16", "4f2683", "ffc62f"), ("GB", "9", "203731", "ffb612"), 0, 0, 0, "0:00", "", "", 0, "",
              state="pre", date=_iso(now + 2 * 86400 + 3 * 3600)),
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
