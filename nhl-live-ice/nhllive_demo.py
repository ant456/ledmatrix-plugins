"""Offline demo data for ``advanced.test_mode``, the harness and the tests.

Six ESPN-shaped NHL games -- three live (one between periods), one final that
went to overtime, two upcoming -- with per-team leaders (so the player cards
have something to show), period-by-period scores, shots, records and a line,
plus a procedurally drawn headshot per player (so the photo path runs without
a network). The Maple Leafs-Bruins game flips between two states every few
updates so the go-ahead burst and the run flame can be seen on a quiet Tuesday.

The teams are real; **the players are made up**, so test mode never shows a
real person's name next to numbers they did not score, and never needs a photo
download. The payload shape (``leaders[].name`` of ``goals`` / ``assists`` /
``points`` / ``saves``, ``linescores``, ``records``, ``situation.isPowerPlay``)
follows ESPN's public scoreboard as far as it is known.
"""

import io
import time as _time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

# abbr -> (ESPN team id, colour, alternate colour)
TEAMS = {
    "TOR": ("21", "00205b", "ffffff"), "BOS": ("1", "fcb514", "000000"),
    "MTL": ("10", "a6192e", "001e62"), "EDM": ("22", "041e42", "fc4c02"),
    "CGY": ("3", "c8102e", "f1be48"), "NYR": ("13", "0038a8", "ce1126"),
    "PIT": ("16", "000000", "fcb514"), "COL": ("17", "6f263d", "236192"),
    "VGK": ("37", "b4975a", "333f42"), "FLA": ("26", "c8102e", "041e42"),
    "TB": ("20", "002868", "ffffff"), "CAR": ("7", "cc0000", "000000"),
    "NJ": ("11", "ce1126", "000000"),
}

# abbr -> category -> (id, full name, jersey, position). Invented players.
PLAYERS = {
    "TOR": {"goals": ("920001", "Mikko Valtonen", "16", "RW"), "assists": ("920002", "Declan Marchetti", "34", "C"),
            "points": ("920002", "Declan Marchetti", "34", "C"), "saves": ("920003", "Anders Kjellberg", "35", "G")},
    "BOS": {"goals": ("920011", "Tyler Brandvold", "88", "LW"), "assists": ("920012", "Rory Castellan", "73", "D"),
            "points": ("920011", "Tyler Brandvold", "88", "LW"), "saves": ("920013", "Joel Haverkamp", "1", "G")},
    "MTL": {"goals": ("920021", "Luc Beaudry", "14", "C"), "assists": ("920022", "Etienne Marchand", "8", "D"),
            "points": ("920021", "Luc Beaudry", "14", "C"), "saves": ("920023", "Samuel Girard-Roy", "31", "G")},
    "EDM": {"goals": ("920031", "Connor Draper", "97", "C"), "assists": ("920031", "Connor Draper", "97", "C"),
            "points": ("920031", "Connor Draper", "97", "C"), "saves": ("920032", "Stuart Lindqvist", "74", "G")},
    "CGY": {"goals": ("920041", "Brett Hollingsworth", "13", "LW"), "assists": ("920042", "Nolan Pascoe", "5", "D"),
            "points": ("920041", "Brett Hollingsworth", "13", "LW"), "saves": ("920043", "Dmitri Volkov", "30", "G")},
    "NYR": {"goals": ("920051", "Artemi Sokolov", "10", "LW"), "assists": ("920052", "Jake Winterbourne", "23", "D"),
            "points": ("920051", "Artemi Sokolov", "10", "LW"), "saves": ("920053", "Henrik Dahl", "30", "G")},
    "PIT": {"goals": ("920061", "Cole Whitaker", "87", "C"), "assists": ("920062", "Matt Ostrowski", "58", "D"),
            "points": ("920061", "Cole Whitaker", "87", "C"), "saves": ("920063", "Tomas Reznicek", "29", "G")},
    "COL": {"goals": ("920071", "Nate Ferraro", "29", "C"), "assists": ("920072", "Cale Brennan", "8", "D"),
            "points": ("920071", "Nate Ferraro", "29", "C"), "saves": ("920073", "Alexei Bobrov", "40", "G")},
    "VGK": {"goals": ("920081", "Jonah Pruitt", "75", "RW"), "assists": ("920082", "Mark Estevez", "61", "RW"),
            "points": ("920081", "Jonah Pruitt", "75", "RW"), "saves": ("920083", "Logan Teague", "31", "G")},
}

_UNITS = {"goals": "G", "assists": "A", "points": "PTS", "saves": "SV"}
_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


def _leaders(abbr: str, stats: Optional[Dict[str, int]]) -> List[Dict[str, Any]]:
    out = []
    for cat, (pid, name, jersey, pos) in PLAYERS.get(abbr, {}).items():
        if not stats or cat not in stats:
            continue
        out.append({"name": cat, "displayName": cat.title(), "abbreviation": _UNITS[cat], "leaders": [{
            "displayValue": str(stats[cat]), "value": float(stats[cat]),
            "athlete": {"id": pid, "fullName": name, "displayName": name, "shortName": name,
                        "jersey": jersey, "position": {"abbreviation": pos},
                        "headshot": f"https://a.espncdn.com/i/headshots/nhl/players/full/{pid}.png"}}]})
    return out


def _side(kind: str, abbr: str, score: int, lines: List[int], record: str,
          stats: Optional[Dict[str, int]], shots: Optional[int]) -> Dict[str, Any]:
    tid, primary, alt = TEAMS[abbr]
    side = {"homeAway": kind, "id": tid, "score": str(score), "leaders": _leaders(abbr, stats),
            "linescores": [{"value": float(v)} for v in lines],
            "records": [{"name": "overall", "type": "total", "summary": record}] if record else [],
            "team": {"id": tid, "abbreviation": abbr, "color": primary, "alternateColor": alt,
                     "displayName": abbr, "shortDisplayName": abbr}}
    if shots is not None:
        side["statistics"] = [{"name": "shotsTotal", "displayValue": str(shots)}]
    return side


def event(eid: str, home: str, away: str, hs: int, as_: int, period: int, clock: str, state: str = "in",
          date: str = "2026-01-01T00:00Z", home_lines=(), away_lines=(), home_stats=None, away_stats=None,
          home_record: str = "", away_record: str = "", odds: str = "", last_play: str = "",
          intermission: bool = False, final_detail: str = "Final", shots=(None, None),
          power_play: bool = False) -> Dict[str, Any]:
    ordinal = _ORDINALS.get(period, "OT")
    if intermission:
        name, short_detail = "STATUS_END_PERIOD", f"End of {ordinal} Period"
    else:
        name, short_detail = {
            "in": ("STATUS_IN_PROGRESS", f"{clock} - {ordinal} Period"),
            "post": ("STATUS_FINAL", final_detail),
            "pre": ("STATUS_SCHEDULED", "Scheduled"),
        }.get(state, ("STATUS_IN_PROGRESS", clock))
    comp: Dict[str, Any] = {
        "status": {"period": period, "displayClock": clock,
                   "type": {"state": state, "name": name, "shortDetail": short_detail}},
        "competitors": [_side("home", home, hs, list(home_lines), home_record, home_stats, shots[0]),
                        _side("away", away, as_, list(away_lines), away_record, away_stats, shots[1])],
    }
    if odds:
        comp["odds"] = [{"details": odds}]
    situation: Dict[str, Any] = {}
    if last_play:
        situation["lastPlay"] = {"text": last_play}
    if power_play:
        situation["isPowerPlay"] = True
    if situation:
        comp["situation"] = situation
    return {"id": eid, "date": date, "competitions": [comp]}


def _iso(epoch: float) -> str:
    return _time.strftime("%Y-%m-%dT%H:%MZ", _time.gmtime(epoch))


def scoreboard(step: int = 0, now: Optional[float] = None) -> Dict[str, Any]:
    """The demo slate. Every sixth ``step`` the Maple Leafs score three goals to take the lead.

    Steps 0-2 show TOR trailing 1-3 in the third; steps 3-5 show them ahead 4-3
    after the surge, which is a go-ahead goal *and* a 3-0 run (the flame).
    A final that went to overtime and two upcoming games (puck drop a few hours
    and a day out, relative to ``now``) sit beside the three live ones, so
    recent- and upcoming-game screens have something to show too.
    """
    now = _time.time() if now is None else now
    surged = (step % 6) >= 3
    tor = 4 if surged else 1
    slate = {"events": [
        event("n1", "MTL", "EDM", 2, 1, 2, "12:45", home_lines=(1, 1), away_lines=(0, 1),
              home_stats={"goals": 1, "assists": 1, "points": 2, "saves": 24},
              away_stats={"goals": 1, "assists": 1, "points": 2, "saves": 19},
              home_record="14-9-2", away_record="16-8-1", odds="EDM -130", shots=(21, 20),
              power_play=True),
        event("n2", "TOR", "BOS", tor, 3, 3, "2:10", home_lines=(1, 0, 3 if surged else 0), away_lines=(2, 1, 0),
              home_stats={"goals": 2, "assists": 1, "points": 3, "saves": 27},
              away_stats={"goals": 2, "assists": 0, "points": 2, "saves": 31},
              home_record="15-8-2", away_record="13-10-3", odds="TOR -115", shots=(33, 28),
              last_play=("V.Valtonen scores on a wrist shot" if surged else "T.Brandvold shot blocked")),
        event("n3", "NYR", "PIT", 3, 2, 2, "0:00", intermission=True, home_lines=(1, 2), away_lines=(1, 1),
              home_stats={"goals": 1, "assists": 2, "points": 3, "saves": 26},
              away_stats={"goals": 1, "assists": 1, "points": 2, "saves": 30},
              home_record="17-6-2", away_record="12-11-4", shots=(31, 28)),
        event("n4", "COL", "VGK", 4, 3, 4, "1:42", state="post", date=_iso(now - 3 * 3600),
              final_detail="Final/OT", home_lines=(1, 1, 1, 1), away_lines=(2, 0, 1, 0),
              home_stats={"goals": 3, "assists": 1, "points": 4, "saves": 33},
              away_stats={"goals": 2, "assists": 1, "points": 3, "saves": 29},
              home_record="19-5-3", away_record="16-9-4", shots=(36, 33)),
        event("n5", "FLA", "TB", 0, 0, 0, "0:00", state="pre", date=_iso(now + 5 * 3600),
              home_record="16-9-1", away_record="14-10-3", odds="FLA -120"),
        event("n6", "CAR", "NJ", 0, 0, 0, "0:00", state="pre", date=_iso(now + 26 * 3600),
              home_record="15-9-2", away_record="12-13-2"),
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
    draw.ellipse([w // 2 - 32, 8, w // 2 + 32, 44], fill=(28, 28, 34, 255))  # helmet
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
