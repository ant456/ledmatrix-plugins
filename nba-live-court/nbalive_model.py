"""Pure logic for NBA Live Court: parse ESPN games, apply the favorites lock,
order the live rotation, turn stat leaders into collectible cards. No network,
no drawing, so it is trivially testable.
"""

import calendar
import datetime
import re
import time
from typing import Any, Dict, List, Optional, Tuple

#: ESPN's abbreviations for the 30 teams (GS, NY, NO, SA, UTAH, WSH -- not GSW, NYK...).
NBA_TEAMS = [
    "ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GS", "HOU", "IND",
    "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NO", "NY", "OKC", "ORL", "PHI", "PHX",
    "POR", "SA", "SAC", "TOR", "UTAH", "WSH",
]
#: Common alternate spellings people type into a config.
ALIASES = {
    "GSW": "GS", "NYK": "NY", "NOP": "NO", "NOH": "NO", "SAS": "SA", "UTA": "UTAH",
    "WAS": "WSH", "PHO": "PHX", "BRK": "BKN", "NJN": "BKN", "CHO": "CHA", "CHH": "CHA",
}


def norm_team(abbr: Any) -> str:
    """Upper-case an abbreviation and fold aliases (GSW -> GS)."""
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


# ----------------------------------------------------------------------
# game clock and period
# ----------------------------------------------------------------------

def period_text(period: int, state_name: str, detail: str, state: str = "") -> str:
    """Q1..Q4, OT, OT2, HALF, FINAL, or PRE (tip-off not yet happened)."""
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


def clock_seconds(clock: Any) -> Optional[float]:
    """'7:42' -> 462.0, '45.3' -> 45.3 (ESPN switches to tenths under a minute)."""
    text = str(clock or "").strip()
    m = re.fullmatch(r"(\d+):(\d{1,2})(?:\.\d+)?", text)
    if m:
        return int(m.group(1)) * 60.0 + int(m.group(2))
    try:
        return float(text)
    except ValueError:
        return None


def is_clutch(game: Dict[str, Any]) -> bool:
    """Fourth quarter or overtime, five minutes or less, within five points."""
    if not game.get("live") or game.get("period", 0) < 4 or game.get("period_text") == "HALF":
        return False
    left = clock_seconds(game.get("clock"))
    if left is None or left > 300:
        return False
    return abs(game["home"]["score"] - game["away"]["score"]) <= 5


def _epoch(text: Any) -> Optional[float]:
    """ESPN's '2026-01-14T01:15Z' (or with seconds) -> epoch seconds (UTC), or None."""
    raw = str(text or "")
    for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return float(calendar.timegm(time.strptime(raw, fmt)))
        except ValueError:
            continue
    return None


def _linescores(raw: Any) -> List[int]:
    out: List[int] = []
    for entry in raw or []:
        value = entry.get("value") if isinstance(entry, dict) else entry
        try:
            out.append(int(float(value)))
        except (TypeError, ValueError):
            out.append(0)
    return out


def _record(raw: Any) -> str:
    """The overall W-L record ('21-14') from ESPN's ``records`` list, or ''."""
    best = ""
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        summary = str(entry.get("summary") or "")
        if not re.fullmatch(r"\d+-\d+", summary):
            continue
        if entry.get("type") == "total" or entry.get("name") == "overall":
            return summary
        best = best or summary
    return best


def parse_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One ESPN scoreboard event -> a flat game dict, or None if unusable."""
    try:
        comp = event["competitions"][0]
        status = comp.get("status") or event.get("status") or {}
        stype = status.get("type") or {}
        state = stype.get("state", "")
        sides: Dict[str, Dict[str, Any]] = {}
        for c in comp.get("competitors", []):
            team = c.get("team") or {}
            abbr = norm_team(team.get("abbreviation"))
            sides[c.get("homeAway")] = {
                "leaders": parse_leaders(c.get("leaders"), abbr) if state != "pre" else [],
                "id": str(c.get("id") or team.get("id") or ""),
                "abbr": abbr,
                "name": team.get("shortDisplayName") or team.get("displayName") or "",
                "score": _int(c.get("score")),
                "color": _hex(team.get("color"), "333333"),
                "alt_color": _hex(team.get("alternateColor"), "ffffff"),
                "linescores": _linescores(c.get("linescores")),
                "record": _record(c.get("records")),
            }
        home, away = sides.get("home"), sides.get("away")
        if not home or not away or not home["abbr"] or not away["abbr"]:
            return None
        situation = comp.get("situation") or {}
        odds = (comp.get("odds") or [{}])[0] if isinstance(comp.get("odds"), list) and comp.get("odds") else {}
        period = _int(status.get("period"))
        game = {
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
            "last_play": (situation.get("lastPlay") or {}).get("text", "") if isinstance(situation, dict) else "",
            "odds": str(odds.get("details") or "") if isinstance(odds, dict) else "",
            "run": None,
            "stars": rank_stars(home["leaders"] + away["leaders"]),
        }
        game["clutch"] = is_clutch(game)
        return game
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


def lead(game: Dict[str, Any]) -> Tuple[Optional[str], int]:
    """``(side, points)`` for whoever leads, ``(None, 0)`` when tied."""
    diff = game["home"]["score"] - game["away"]["score"]
    if diff == 0:
        return None, 0
    return ("home" if diff > 0 else "away"), abs(diff)


# ----------------------------------------------------------------------
# scoring runs and alerts
# ----------------------------------------------------------------------

#: A run shows its flame once a team has outscored the other side by this much.
RUN_FLAME_POINTS = 8


def scoring_change(previous: Optional[Dict[str, Any]], current: Dict[str, Any]) -> Optional[str]:
    """Which side outscored the other between two polls of one game ('home'/'away')."""
    if not previous:
        return None
    dh = current["home"]["score"] - previous["home"]["score"]
    da = current["away"]["score"] - previous["away"]["score"]
    if dh > da and dh > 0:
        return "home"
    if da > dh and da > 0:
        return "away"
    return None


def update_run(run: Optional[Dict[str, Any]], previous: Optional[Dict[str, Any]],
               current: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The scoring run after a poll: ``{"side", "points"}`` or None.

    A run is unanswered points. A side scoring alone extends (or starts) its run;
    both sides scoring in one poll leaves only the net margin; nobody scoring
    leaves it as it was. Only polls are visible, so a basket-for-basket trade
    inside one 30 s window shows up as the net, never as a longer run than
    actually happened.
    """
    if not previous or not current.get("live"):
        return None if not current.get("live") else run
    dh = current["home"]["score"] - previous["home"]["score"]
    da = current["away"]["score"] - previous["away"]["score"]
    if dh < 0 or da < 0:      # a score going down is a correction (or a new game), not play
        return None
    if dh == 0 and da == 0:
        return run
    if dh > 0 and da > 0:
        net = dh - da
        return None if net == 0 else {"side": "home" if net > 0 else "away", "points": abs(net)}
    side, pts = ("home", dh) if dh > 0 else ("away", da)
    if run and run.get("side") == side:
        return {"side": side, "points": run["points"] + pts}
    return {"side": side, "points": pts}


def run_on_fire(game: Dict[str, Any]) -> Optional[str]:
    """The side whose run is big enough to flame, or None."""
    run = game.get("run")
    if run and run.get("points", 0) >= RUN_FLAME_POINTS:
        return run["side"]
    return None


def detect_alert(previous: Optional[Dict[str, Any]], current: Dict[str, Any],
                 min_swing: int, now: float) -> Optional[Dict[str, Any]]:
    """A queued burst for what just happened between two polls, or None.

    Basketball scores constantly, so a burst for every basket would never
    stop. Two things earn one: a team *taking the lead* in the fourth quarter
    or overtime, and a swing of ``min_swing`` or more points between polls.
    """
    side = scoring_change(previous, current)
    if not side or not current.get("live") or not previous:
        return None
    other = "away" if side == "home" else "home"
    pts = current[side]["score"] - previous[side]["score"]
    opp = current[other]["score"] - previous[other]["score"]
    swing = pts - opp
    before = previous[side]["score"] - previous[other]["score"]
    after = current[side]["score"] - current[other]["score"]
    if before <= 0 < after and current.get("period", 0) >= 4:
        kind, desc = "LEAD CHANGE", f"{current[side]['abbr']} TAKES THE LEAD"
    elif min_swing > 0 and swing >= min_swing:
        kind, desc = "BIG RUN", f"{current[side]['abbr']} {pts}-{opp} RUN"
    else:
        return None
    team = current[side]
    text = current.get("last_play") or ""
    scorer = match_scorer(text, [p for p in current.get("stars", []) if p["team"] == team["abbr"]])
    return {
        "game": current["id"],
        "side": side,
        "team": team["abbr"],
        "opp": current[other]["abbr"],
        "kind": kind,
        "points": swing,
        "desc": desc,
        "text": text,
        "player": scorer,
        "at": now,
        "away_abbr": current["away"]["abbr"], "home_abbr": current["home"]["abbr"],
        "away_score": current["away"]["score"], "home_score": current["home"]["score"],
        "primary": team["color"], "accent": team["alt_color"],
    }


def match_scorer(text: str, players):
    """The player whose surname appears LAST in the play text ('Smith assist to Jones' -> Jones)."""
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


# ----------------------------------------------------------------------
# players: ESPN's per-team "leaders" become collectible cards
# ----------------------------------------------------------------------

#: ESPN leader category name -> short key. Live and final games name them
#  "points"/"rebounds"/"assists"; the "...PerGame" forms are accepted too in
#  case a payload carries those, and "pts"/"reb"/"ast" for good measure.
CATEGORIES = {
    "points": "pts", "pointsPerGame": "pts", "pts": "pts",
    "rebounds": "reb", "reboundsPerGame": "reb", "reb": "reb", "totalRebounds": "reb",
    "assists": "ast", "assistsPerGame": "ast", "ast": "ast",
}
CATEGORY_LABELS = {"pts": "SCORING", "reb": "REBOUNDS", "ast": "PLAYMAKING"}
UNITS = {"pts": "PTS", "reb": "REB", "ast": "AST"}
STAT_ORDER = ("pts", "reb", "ast")
#: stat needed for each rarity, best first: legendary, epic, rare.
TIER_THRESHOLDS = {
    "pts": (40.0, 30.0, 20.0),
    "reb": (18.0, 14.0, 10.0),
    "ast": (14.0, 10.0, 7.0),
}
TIER_ORDER = ("legendary", "epic", "rare", "common")
ESPN_HEADSHOT_URL = "https://a.espncdn.com/i/headshots/nba/players/full/{espn_id}.png"


def tier_for(category: str, value: float) -> str:
    legendary, epic, rare = TIER_THRESHOLDS.get(category, TIER_THRESHOLDS["pts"])
    if value >= legendary:
        return "legendary"
    if value >= epic:
        return "epic"
    if value >= rare:
        return "rare"
    return "common"


def next_threshold(category: str, value: float) -> float:
    """The value the XP bar is filling toward: the next tier up (legendary at the top)."""
    for threshold in reversed(TIER_THRESHOLDS.get(category, TIER_THRESHOLDS["pts"])):
        if value < threshold:
            return threshold
    return TIER_THRESHOLDS.get(category, TIER_THRESHOLDS["pts"])[0]


def _stat_value(entry: Dict[str, Any]) -> int:
    """The leader's number: the first integer in ``displayValue`` ('31'), else ``value``."""
    m = re.match(r"\s*(-?\d+)", str(entry.get("displayValue") or ""))
    if m:
        return int(m.group(1))
    try:
        return int(round(float(entry.get("value"))))
    except (TypeError, ValueError):
        return 0


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


def _ratio(category: str, value: float) -> float:
    return value / TIER_THRESHOLDS[category][0]


def badge_for(stats: Dict[str, int]) -> str:
    """'TRIPLE-DBL' / 'DBL-DBL' from the stats ESPN named this player a leader in."""
    tens = sum(1 for v in stats.values() if v >= 10)
    if tens >= 3:
        return "TRIPLE-DBL"
    if tens == 2:
        return "DBL-DBL"
    return ""


def parse_leaders(leaders: Any, team_abbr: str):
    """One team's ESPN ``leaders`` block -> player dicts, one per athlete.

    A player leading several categories (points *and* rebounds) is one card
    carrying both numbers; the card's headline is whichever stat earns the
    highest rarity.
    """
    players: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for group in leaders or []:
        category = CATEGORIES.get(group.get("name"))
        if not category:
            continue
        for entry in (group.get("leaders") or [])[:1]:
            athlete = entry.get("athlete") or {}
            if not athlete.get("id"):
                continue
            pid = str(athlete["id"])
            if pid not in players:
                full = athlete.get("fullName") or athlete.get("displayName") or ""
                first, last = split_name(full)
                players[pid] = {
                    "id": pid, "name": full, "first": first,
                    "last": last or athlete.get("shortName", ""),
                    "pos": ((athlete.get("position") or {}).get("abbreviation") or "").upper(),
                    "jersey": str(athlete.get("jersey") or ""),
                    "team": team_abbr,
                    "headshot_url": _headshot_url(athlete),
                    "stats": {},
                }
                order.append(pid)
            players[pid]["stats"][category] = _stat_value(entry)
    return [_finish_player(players[pid]) for pid in order]


def _finish_player(p: Dict[str, Any]) -> Dict[str, Any]:
    stats = p["stats"]
    best = min(stats, key=lambda c: (TIER_ORDER.index(tier_for(c, stats[c])), -_ratio(c, stats[c])))
    value = stats[best]
    p.update({
        "category": best,
        "value": value,
        "unit": UNITS[best],
        "tier": tier_for(best, value),
        "ratio": _ratio(best, value),
        "parts": [(str(stats[c]), UNITS[c]) for c in STAT_ORDER if c in stats and c != best],
        "badge": badge_for(stats),
    })
    return p


def rank_stars(players):
    """Best performers first: rarity, then how close to the top rarity."""
    return sorted(players, key=lambda p: (TIER_ORDER.index(p["tier"]), -p["ratio"], p["name"]))


def stars_for(game: Dict[str, Any], count: int, favorites=None):
    """The cards a game gets: favorites' players first, at most ``count``."""
    favorites = favorites or []
    stars = [p for p in game.get("stars", []) if p["value"] > 0]
    stars.sort(key=lambda p: (p["team"] not in favorites,))  # stable: keeps the rarity order within each group
    return stars[:max(0, count)]


# ----------------------------------------------------------------------
# recent (final) and upcoming (pre-tip-off) game selection
# ----------------------------------------------------------------------

_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def tipoff_text(start: Optional[float], now: float) -> str:
    """A short local-time tip-off label: 'TODAY 7:30 PM', 'SUN 1:00 PM'."""
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
    pool = [g for g in games if g["state"] == state and not involves(g, exclude)]
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
