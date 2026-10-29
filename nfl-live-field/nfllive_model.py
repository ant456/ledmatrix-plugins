"""Pure logic for NFL Live Field: parse ESPN games, apply the favorites lock,
order the live rotation. No network, no drawing, so it is trivially testable.
"""

import calendar
import datetime
import re
import time
from typing import Any, Dict, List, Optional

#: ESPN abbreviations for the 32 teams (ESPN uses WSH, not WAS).
NFL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
    "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO",
    "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WSH",
]
#: Common alternate spellings people type into a config.
ALIASES = {"WAS": "WSH", "JAC": "JAX", "LVR": "LV", "OAK": "LV", "LA": "LAR", "SD": "LAC"}

FIELD_YARDS = 100
_TERRITORY = re.compile(r"^\s*([A-Za-z]{2,4})\s+(\d{1,2})\s*$")


def norm_team(abbr: Any) -> str:
    """Upper-case an abbreviation and fold aliases (WAS -> WSH)."""
    text = str(abbr or "").strip().upper()
    return ALIASES.get(text, text)


def norm_team_list(values: Any) -> List[str]:
    """A config list of teams -> unique, normalised abbreviations, in order."""
    out: List[str] = []
    for value in values or []:
        team = norm_team(value)
        if team and team not in out:
            out.append(team)
    return out


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def hex_to_rgb(value: Any, fallback=(90, 96, 110)):
    text = str(value or "").strip().lstrip("#")
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except (ValueError, IndexError):
        return fallback


def _hex(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lstrip("#")
    return text.lower() if re.fullmatch(r"[0-9a-fA-F]{6}", text) else fallback


def ball_position(situation: Dict[str, Any], home: Dict[str, Any],
                  away: Dict[str, Any]) -> Optional[float]:
    """Where the ball is, as yards from the AWAY team's goal line (0-100).

    The away end zone is drawn on the left, the home end zone on the right.
    ESPN's ``possessionText`` ("DAL 35") names the side of the field the ball is
    on, which is unambiguous, so it is used first. Without it the ball position
    is derived from ``yardLine`` (yards from the possessing team's own goal) and
    who has the ball.
    """
    match = _TERRITORY.match(str(situation.get("possessionText") or ""))
    if match:
        side, yards = norm_team(match.group(1)), _int(match.group(2))
        if 0 <= yards <= 50:
            if side == home["abbr"]:
                return float(FIELD_YARDS - yards)
            if side == away["abbr"]:
                return float(yards)
    if situation.get("yardLine") is None:
        return None
    yard_line = max(0, min(FIELD_YARDS, _int(situation.get("yardLine"), -1)))
    if _int(situation.get("yardLine"), -1) < 0:
        return None
    holder = str(situation.get("possession") or "")
    if holder and holder == away["id"]:
        return float(yard_line)  # away drives toward the home (right) end zone
    if holder and holder == home["id"]:
        return float(FIELD_YARDS - yard_line)
    return None


def first_down_marker(ball: Optional[float], distance: Any, driving_right: Optional[bool]) -> Optional[float]:
    """Yard position of the first-down line, or None when there is no line."""
    if ball is None or driving_right is None:
        return None
    dist = _int(distance, 0)
    if dist <= 0:
        return None
    line = ball + dist if driving_right else ball - dist
    return float(max(0, min(FIELD_YARDS, line)))


def period_text(period: int, state_name: str, detail: str, state: str = "") -> str:
    """Q1..Q4, OT, OT2, HALF, FINAL, or PRE (kickoff not yet happened)."""
    if state == "post":
        return "FINAL"
    name = (state_name or "").upper()
    if "HALFTIME" in name or "halftime" in (detail or "").lower():
        return "HALF"
    if state == "pre" or period <= 0:
        return "PRE"
    if period <= 4:
        return f"Q{period}"
    return "OT" if period == 5 else f"OT{period - 4}"


def _epoch(text: Any) -> Optional[float]:
    """ESPN's '2026-01-14T01:15Z' -> epoch seconds (UTC), or None."""
    try:
        return float(calendar.timegm(time.strptime(str(text), "%Y-%m-%dT%H:%MZ")))
    except (TypeError, ValueError):
        return None


def _game_level_leaders(groups: Any, home: Dict[str, Any], away: Dict[str, Any]) -> None:
    """Fallback: leaders carried on the competition (one list for the whole game)
    instead of on each competitor. Each entry names its team, either directly or
    through the athlete, and is routed to that side. Fills ``home``/``away``
    ``["leaders"]`` in place; a no-op when there is nothing usable."""
    if not isinstance(groups, list):
        return
    routed: Dict[str, List[Dict[str, Any]]] = {home["id"]: [], away["id"]: []}
    seen = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        for entry in group.get("leaders") or []:
            if not isinstance(entry, dict):
                continue
            athlete = entry.get("athlete") or {}
            team_id = str((entry.get("team") or {}).get("id") or (athlete.get("team") or {}).get("id") or "")
            key = (team_id, CATEGORIES.get(group.get("name")))
            if team_id in routed and key not in seen:
                seen.add(key)
                routed[team_id].append({"name": group.get("name"), "leaders": [entry]})
    for side in (home, away):
        if routed.get(side["id"]):
            side["leaders"] = parse_leaders(routed[side["id"]], side["abbr"])


def parse_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One ESPN scoreboard event -> a flat game dict, or None if unusable."""
    try:
        comp = event["competitions"][0]
        status = comp.get("status") or event.get("status") or {}
        stype = status.get("type") or {}
        sides: Dict[str, Dict[str, Any]] = {}
        for c in comp.get("competitors", []):
            team = c.get("team") or {}
            sides[c.get("homeAway")] = {
                "leaders": parse_leaders(c.get("leaders"), norm_team(team.get("abbreviation"))),
                "id": str(c.get("id") or team.get("id") or ""),
                "abbr": norm_team(team.get("abbreviation")),
                "name": team.get("shortDisplayName") or team.get("displayName") or "",
                "score": _int(c.get("score")),
                "color": _hex(team.get("color"), "333333"),
                "alt_color": _hex(team.get("alternateColor"), "ffffff"),
            }
        home, away = sides.get("home"), sides.get("away")
        if not home or not away or not home["abbr"] or not away["abbr"]:
            return None
        if not (home["leaders"] or away["leaders"]):
            _game_level_leaders(comp.get("leaders"), home, away)
        state = stype.get("state", "")
        situation = comp.get("situation") or {}
        ball = ball_position(situation, home, away) if state == "in" else None
        poss_id = str(situation.get("possession") or "")
        poss = "home" if poss_id == home["id"] else "away" if poss_id == away["id"] else None
        driving_right = None if poss is None else (poss == "away")
        period = _int(status.get("period"))
        return {
            "id": str(event.get("id") or comp.get("id") or ""),
            "start": _epoch(event.get("date") or comp.get("date")),
            "state": state,
            "live": state == "in",
            "final": state == "post",
            "period": period,
            "period_text": period_text(period, stype.get("name", ""), stype.get("detail", ""), state),
            "clock": status.get("displayClock", "") or "",
            "detail": stype.get("shortDetail", "") or "",
            "home": home,
            "away": away,
            "possession": poss,
            "down_distance": situation.get("shortDownDistanceText") or "",
            "down_distance_long": situation.get("downDistanceText") or "",
            "possession_text": situation.get("possessionText") or "",
            "red_zone": bool(situation.get("isRedZone")),
            "home_timeouts": _int(situation.get("homeTimeouts"), 3),
            "away_timeouts": _int(situation.get("awayTimeouts"), 3),
            "ball": ball,
            "first_down": first_down_marker(ball, situation.get("distance"), driving_right),
            "last_play": (situation.get("lastPlay") or {}).get("text", ""),
            "last_play_points": _int((situation.get("lastPlay") or {}).get("scoreValue")),
            "stars": rank_stars(home["leaders"] + away["leaders"]),
        }
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def parse_scoreboard(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A whole ESPN scoreboard response -> parsed games (bad events skipped)."""
    games = [parse_event(e) for e in (payload or {}).get("events", [])]
    return [g for g in games if g]


def involves(game: Dict[str, Any], teams: List[str]) -> bool:
    return game["home"]["abbr"] in teams or game["away"]["abbr"] in teams


def select_live(games: List[Dict[str, Any]], favorites: List[str], lock: bool,
                exclude: Optional[List[str]] = None, fallback_when_idle: bool = False,
                ) -> List[Dict[str, Any]]:
    """The live games to show, favorites first.

    ``exclude`` (spoiler protection) beats everything, favorites included.
    ``lock`` shows only games involving a favorite team. If ``lock`` is on but
    no favorite is playing, the result is empty unless ``fallback_when_idle``
    is set, which then shows every other live game until a favorite tips off.
    With no favorites configured the lock has nothing to lock to, so all live
    games show.
    """
    exclude = exclude or []
    live = [g for g in games if g["live"] and not involves(g, exclude)]
    if not favorites:
        return live
    mine = [g for g in live if involves(g, favorites)]
    rest = [g for g in live if not involves(g, favorites)]
    if lock:
        return mine if (mine or not fallback_when_idle) else rest
    return mine + rest


def rotation(games: List[Dict[str, Any]], favorites: List[str], boost: int = 1) -> List[Dict[str, Any]]:
    """Turn order for the live carousel: each favorite game appears ``boost``
    times per lap, spread out so it never runs back-to-back when others exist."""
    boost = max(1, int(boost))
    mine = [g for g in games if involves(g, favorites)] if favorites else []
    mine_ids = {g["id"] for g in mine}
    rest = [g for g in games if g["id"] not in mine_ids]
    if boost == 1 or not mine or not rest:
        return list(mine) + list(rest)
    order: List[Dict[str, Any]] = []
    extras = list(mine) * (boost - 1)
    for game in list(mine) + list(rest):
        order.append(game)
        if extras and rest:
            order.append(extras.pop(0))
    order.extend(extras)
    return order


def scoring_change(previous: Optional[Dict[str, Any]], current: Dict[str, Any]) -> Optional[str]:
    """Which side just scored ('home'/'away'), comparing two polls of one game."""
    if not previous:
        return None
    for side in ("home", "away"):
        if current[side]["score"] > previous[side]["score"]:
            return side
    return None


# ----------------------------------------------------------------------
# players: ESPN's per-team "leaders" become collectible cards
# ----------------------------------------------------------------------

#: ESPN leader category -> (short key, label). Only these three carry a card.
#  ESPN's per-team leaders (competitors[i].leaders) use the "...Leader" names;
#  the game-level leaders (competitions[0].leaders, one per game not per team)
#  use the "...Yards" names. Both are accepted since either may be what a
#  given payload actually carries.
CATEGORIES = {
    "passingLeader": "pass", "rushingLeader": "rush", "receivingLeader": "rec",
    "passingYards": "pass", "rushingYards": "rush", "receivingYards": "rec",
}
CATEGORY_LABELS = {"pass": "PASSING", "rush": "RUSHING", "rec": "RECEIVING"}
#: value (yards + 25 per touchdown) needed for each rarity, best first.
TIER_THRESHOLDS = {
    "pass": (350.0, 275.0, 175.0),
    "rush": (170.0, 110.0, 65.0),
    "rec": (170.0, 110.0, 65.0),
}
TIER_ORDER = ("legendary", "epic", "rare", "common")
TD_YARD_VALUE = 25.0
ESPN_HEADSHOT_URL = "https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"

_STAT_TOKEN = re.compile(r"^\s*(-?\d+(?:/\d+)?)\s*([A-Za-z]*)\s*$")
_STAT_LABELS = {"YDS": "YD", "YD": "YD", "TD": "TD", "INT": "INT", "REC": "REC", "CAR": "CAR", "": ""}


def parse_stat_parts(display_value: Any):
    """'22/31, 245 YDS, 2 TD' -> [('22/31','C/A'), ('245','YD'), ('2','TD')]."""
    parts = []
    for token in str(display_value or "").split(","):
        m = _STAT_TOKEN.match(token)
        if not m:
            continue
        number, word = m.group(1), m.group(2).upper()
        if "/" in number:
            parts.append((number, "C/A"))
        elif word in _STAT_LABELS and word:
            parts.append((number, _STAT_LABELS[word]))
    return parts


def _stat(parts, label: str) -> int:
    for number, lab in parts:
        if lab == label and "/" not in number:
            return _int(number)
    return 0


def tier_for(category: str, value: float) -> str:
    legendary, epic, rare = TIER_THRESHOLDS.get(category, TIER_THRESHOLDS["rec"])
    if value >= legendary:
        return "legendary"
    if value >= epic:
        return "epic"
    if value >= rare:
        return "rare"
    return "common"


def next_threshold(category: str, value: float) -> float:
    """The value the XP bar is filling toward: the next tier up (legendary at the top)."""
    for threshold in reversed(TIER_THRESHOLDS.get(category, TIER_THRESHOLDS["rec"])):
        if value < threshold:
            return threshold
    return TIER_THRESHOLDS.get(category, TIER_THRESHOLDS["rec"])[0]


def _headshot_url(athlete: Dict[str, Any]) -> str:
    raw = athlete.get("headshot")
    if isinstance(raw, dict):
        raw = raw.get("href")
    if raw:
        return str(raw)
    return ESPN_HEADSHOT_URL.format(espn_id=athlete["id"]) if athlete.get("id") else ""


def split_name(full: str):
    bits = [b for b in re.split(r"\s+", (full or "").strip()) if b]
    if not bits:
        return "", ""
    suffixes = {"JR", "JR.", "SR", "SR.", "II", "III", "IV", "V"}
    while len(bits) > 1 and bits[-1].upper() in suffixes:
        bits.pop()
    return " ".join(bits[:-1]), bits[-1]


def parse_leaders(leaders: Any, team_abbr: str):
    """One team's ESPN ``leaders`` block -> a list of player dicts."""
    out = []
    for group in leaders or []:
        category = CATEGORIES.get(group.get("name"))
        if not category:
            continue
        for entry in (group.get("leaders") or [])[:1]:
            athlete = entry.get("athlete") or {}
            if not athlete.get("id"):
                continue
            parts = parse_stat_parts(entry.get("displayValue"))
            yards = _stat(parts, "YD") or _int(entry.get("value"))
            td = _stat(parts, "TD")
            value = yards + TD_YARD_VALUE * td
            first, last = split_name(athlete.get("fullName") or athlete.get("displayName") or "")
            out.append({
                "id": str(athlete["id"]),
                "name": athlete.get("fullName") or athlete.get("displayName") or "",
                "first": first,
                "last": last or athlete.get("shortName", ""),
                "pos": ((athlete.get("position") or {}).get("abbreviation") or "").upper(),
                "jersey": str(athlete.get("jersey") or ""),
                "team": team_abbr,
                "headshot_url": _headshot_url(athlete),
                "category": category,
                "yards": yards,
                "td": td,
                "parts": parts,
                "value": value,
                "tier": tier_for(category, value),
            })
    return out


def apply_summary_leaders(game: Dict[str, Any], summary: Any) -> bool:
    """Give a finished game each team's own leaders, from ESPN's game summary.

    The scoreboard sometimes lists only the game's top passer, rusher and
    receiver, all on the winning side. The summary carries a list per team.
    Returns True when any side was filled in."""
    groups: Dict[str, Any] = {}
    for entry in (summary or {}).get("leaders") or [] if isinstance(summary, dict) else []:
        if isinstance(entry, dict):
            groups[str((entry.get("team") or {}).get("id") or "")] = entry.get("leaders") or []
    changed = False
    for side in (game["home"], game["away"]):
        got = groups.get(str(side.get("id")))
        if got:
            side["leaders"] = parse_leaders(got, side["abbr"])
            changed = True
    if changed:
        game["stars"] = rank_stars(game["home"]["leaders"] + game["away"]["leaders"])
    return changed


def rank_stars(players):
    """Best performers first: rarity, then value."""
    return sorted(players, key=lambda p: (TIER_ORDER.index(p["tier"]), -p["value"], p["name"]))


def stars_for(game: Dict[str, Any], count: int, favorites=None):
    """The cards a game gets: favorites' players first, at most ``count``."""
    favorites = favorites or []
    stars = [p for p in game.get("stars", []) if p["value"] > 0]
    stars.sort(key=lambda p: (p["team"] not in favorites,))  # stable: keeps the rarity order within each group
    return stars[:max(0, count)]


# ----------------------------------------------------------------------
# score alerts (touchdown / field goal bursts)
# ----------------------------------------------------------------------

def alert_kind(points: int, play_points: int = 0) -> str:
    if points >= 6:
        return "TOUCHDOWN"
    if points == 3:
        return "FIELD GOAL"
    if points == 2:
        return "SAFETY"
    if points == 1:
        return "EXTRA POINT"
    return "SCORE"


def match_scorer(text: str, players):
    """The player whose surname appears LAST in the play text ('Hurts pass to Brown' -> Brown)."""
    upper = (text or "").upper()
    best, best_at = None, -1
    for p in players:
        surname = (p.get("last") or "").upper()
        if len(surname) < 3:
            continue
        at = upper.rfind(surname)
        if at > best_at:
            best, best_at = p, at
    return best


def build_alert(previous: Dict[str, Any], current: Dict[str, Any], side: str, now: float):
    """A queued burst for the score that just happened."""
    points = current[side]["score"] - previous[side]["score"]
    text = current.get("last_play") or ""
    team = current[side]
    scorer = match_scorer(text, [p for p in current.get("stars", []) if p["team"] == team["abbr"]])
    return {
        "game": current["id"],
        "side": side,
        "team": team["abbr"],
        "opp": current["away" if side == "home" else "home"]["abbr"],
        "kind": alert_kind(points),
        "points": points,
        "text": text,
        "player": scorer,
        "at": now,
        "away_abbr": current["away"]["abbr"], "home_abbr": current["home"]["abbr"],
        "away_score": current["away"]["score"], "home_score": current["home"]["score"],
        "primary": team["color"], "accent": team["alt_color"],
    }


# ----------------------------------------------------------------------
# recent (final) and upcoming (pre-kickoff) game selection
# ----------------------------------------------------------------------

_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def kickoff_text(start: Optional[float], now: float) -> str:
    """A short local-time kickoff label by calendar day: 'TODAY 8:20 PM', 'TMRW 1:00 PM', 'SUN 1:00 PM'."""
    if start is None:
        return ""
    then = time.localtime(start)
    hour12 = ((then.tm_hour + 11) % 12) + 1
    clock = f"{hour12}:{then.tm_min:02d} {'PM' if then.tm_hour >= 12 else 'AM'}"
    days = (datetime.date.fromtimestamp(start) - datetime.date.fromtimestamp(now)).days
    if days == 0:
        return f"TODAY {clock}"
    if days == 1:
        return f"TMRW {clock}"
    if 2 <= days <= 6:
        return f"{_WEEKDAYS[then.tm_wday]} {clock}"
    return f"{then.tm_mon}/{then.tm_mday} {clock}"


def _matches_state(game: Dict[str, Any], state: str) -> bool:
    return game["state"] == state


def select_by_state(games: List[Dict[str, Any]], state: str, favorites: List[str],
                    favorite_limit: int, other_limit: int, other_window_seconds: int,
                    exclude: Optional[List[str]] = None, now: Optional[float] = None,
                    window_start: int = 0) -> List[Dict[str, Any]]:
    """Recent or upcoming games to show: your favorites, then a rotating slice
    of everyone else's, favorites-first-then-chronological within each part.

    ``other_window_seconds`` advances which slice of the "everyone else" pool
    is showing, so a long schedule is walked over time rather than always
    showing the same handful. Deterministic on ``now`` (no stored state to
    juggle across update() and display()): every caller within the same
    window sees the same slice. ``window_start`` shifts the phase so the
    recent and upcoming pools don't happen to rotate in lockstep.
    """
    exclude = exclude or []
    now = time.time() if now is None else now
    pool = [g for g in games if _matches_state(g, state) and not involves(g, exclude)]
    pool.sort(key=lambda g: (g.get("start") or 0), reverse=(state == "post"))
    mine = [g for g in pool if involves(g, favorites)][:max(0, favorite_limit)] if favorites else []
    mine_ids = {g["id"] for g in mine}
    rest = [g for g in pool if g["id"] not in mine_ids]
    others: List[Dict[str, Any]] = []
    if other_limit > 0 and rest:
        if len(rest) <= other_limit:
            others = rest
        else:
            step = max(1, other_window_seconds)
            offset = (window_start + int(now // step)) * other_limit
            start = offset % len(rest)
            others = (rest + rest)[start:start + other_limit]
    return mine + others
