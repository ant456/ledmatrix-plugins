"""NHL Live Ice -- an arcade-style NHL show for LEDMatrix: live, recent and
upcoming games, each its own display mode.

**Live** (``nhl_live``) cycles through every live game with three kinds of
screen, in the style of the Fantasy Blitz plugin:

* the scoreboard: score rows in team colours and a period-by-period score
  table on the left, an arena scoreboard on the right (LED-dot clock and period,
  shots on goal, a blinking PP lamp and a logo box you can fill with your own
  logo), a CLUTCH warning in a one-goal third period or overtime and a flame
  on a team's run of unanswered goals;
* player cards: each game's leaders (goals, assists, points, goalie saves) as
  collectible cards with ESPN headshots, rarity frames, a count-up stat,
  hat-trick badges and an XP bar;
* bursts: a sunburst and confetti for every goal, headlined as a go-ahead
  goal, an equalizer or an overtime winner when it is one, interrupting the
  rotation for a few seconds.

**Recent** (``nhl_recent``) shows the same scoreboard screen (final score, a stat
ticker of the game's leaders where the clock board would be) for games that have
finished, with the same player cards for their leaders.

**Upcoming** (``nhl_upcoming``) shows a matchup preview -- team colours,
records, puck-drop time and the line -- for games that haven't started.

All three read the same ESPN scoreboard days (yesterday, today and tomorrow,
US Eastern -- ESPN rejects date ranges), so recent and upcoming cost almost no
extra network calls.

The favorites lock: with ``lock_to_favorites`` on, only games involving one of
your ``favorite_teams`` are ever shown, in any mode. Off, favorites come first
and get extra turns (``favorite_live_boost`` for live, ``favorite_rotation_
boost`` for recent/upcoming); a rotating slice of everyone else's games fills
the rest, matching the ``other_*_games_to_show`` / ``other_rotation_interval_
seconds`` convention the repo's other scoreboards use.

Division of labour, per the project's rules: ``update()`` does every network
call (ESPN's public scoreboard, then a few headshots) and builds what is on
screen; ``display()`` only draws from memory and returns False when the
current mode has nothing to show so the core rotates straight past.
"""

import os
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from src.plugin_system.base_plugin import BasePlugin

import nhllive_demo as demo
import nhllive_draw as draw
import nhllive_model as model
import nhllive_render as render
from govee_fx import GoveeFX
from nhllive_data import NhlLiveData
from nhllive_headshots import HeadshotStore

LIVE_MODE = "nhl_live"
RECENT_MODE = "nhl_recent"
UPCOMING_MODE = "nhl_upcoming"

DEFAULT_FIELD_SECONDS = 12
DEFAULT_CARD_SECONDS = 6
DEFAULT_ALERT_SECONDS = 8
DEFAULT_RECENT_SECONDS = 10
DEFAULT_UPCOMING_SECONDS = 8
GLOW_SECONDS = 6

#: Per-mode title/accent colour for the frame's title band (rarely visible --
#: only on panel heights the title-band math gives spare rows to -- but cheap
#: to get right).
MODE_TITLE = {LIVE_MODE: ("NHL LIVE", draw.GOLD), RECENT_MODE: ("FINAL", draw.GRAY),
             UPCOMING_MODE: ("UPCOMING", draw.GOLD)}


class NhlLiveIcePlugin(BasePlugin):
    """Arcade live/recent/upcoming NHL games, optionally locked to your favorite teams."""

    NO_DATA_RETRY_SECONDS = 120

    def __init__(self, plugin_id: str, config: Dict[str, Any], display_manager,
                 cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self._load_config(config)
        self.govee = GoveeFX(config.get("govee"), self.logger)
        self.data = NhlLiveData(cache_manager, self.logger, plugin_id, self.request_timeout)
        self.headshots = HeadshotStore(self.data, self.logger)

        self.modes = self._available_modes()
        self.current_mode_index = 0

        self.all_games: List[Dict[str, Any]] = []
        self.live: List[Dict[str, Any]] = []
        self.recent: List[Dict[str, Any]] = []
        self.upcoming: List[Dict[str, Any]] = []
        self.items: Dict[str, List[Dict[str, Any]]] = {LIVE_MODE: [], RECENT_MODE: [], UPCOMING_MODE: []}
        self.last_update = 0.0
        self._snapshots: Deque[Tuple[float, List[Dict[str, Any]]]] = deque(maxlen=60)
        self._shown_ts: Optional[float] = None
        self._previous: Dict[str, Dict[str, Any]] = {}
        self._previous_ts: Optional[float] = None
        self._runs: Dict[str, Optional[Dict[str, Any]]] = {}
        self._alerts: List[Dict[str, Any]] = []
        self._alert: Optional[Dict[str, Any]] = None
        self._glow: Optional[Dict[str, Any]] = None
        self._demo_step = 0
        self._current_mode: Optional[str] = None
        self._mode_started = 0.0
        self._last_frame_at = 0.0
        self._frame_cache: Dict[Tuple[Any, ...], Any] = {}
        self._shown_key: Optional[Tuple[Any, ...]] = None
        self.data_version = 0

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------

    def _available_modes(self) -> List[str]:
        modes = [LIVE_MODE]
        if self.show_recent:
            modes.append(RECENT_MODE)
        if self.show_upcoming:
            modes.append(UPCOMING_MODE)
        return modes

    def _load_config(self, config: Dict[str, Any]) -> None:
        self.favorites = model.norm_team_list(config.get("favorite_teams"))
        self.lock = bool(config.get("lock_to_favorites", False))
        self.lock_fallback = bool(config.get("lock_fallback_to_all", False))
        self.exclude = model.norm_team_list(config.get("exclude_teams"))
        self.field_seconds = _safe_int(config.get("live_game_duration", DEFAULT_FIELD_SECONDS), DEFAULT_FIELD_SECONDS, 5, 120)
        self.cards_per_game = _safe_int(config.get("cards_per_game", 2), 2, 0, 4)
        self.card_seconds = _safe_int(config.get("card_seconds", DEFAULT_CARD_SECONDS), DEFAULT_CARD_SECONDS, 3, 30)
        self.boost = _safe_int(config.get("favorite_live_boost", 2), 2, 1, 5)
        self.live_priority = bool(config.get("live_priority", True))
        self.show_headshots = bool(config.get("show_headshots", True))
        self.score_alerts = bool(config.get("score_alerts", True))
        self.board_logo = str(config.get("scoreboard_logo") or "").strip()
        self.alert_lead_changes_only = bool(config.get("alert_lead_changes_only", False))
        self.alert_all = bool(config.get("celebrate_all_teams", False))
        self.spoiler_delay = float(_safe_int(config.get("spoiler_delay_seconds", 0), 0, 0, 300))
        self.display_duration = _safe_int(config.get("display_duration", 20), 20, 5, 300)

        self.show_recent = bool(config.get("show_recent", True))
        self.show_upcoming = bool(config.get("show_upcoming", True))
        self.recent_game_duration = _safe_int(config.get("recent_game_duration", DEFAULT_RECENT_SECONDS),
                                              DEFAULT_RECENT_SECONDS, 5, 120)
        self.recent_cards_per_game = _safe_int(config.get("recent_cards_per_game", 1), 1, 0, 4)
        self.upcoming_game_duration = _safe_int(config.get("upcoming_game_duration", DEFAULT_UPCOMING_SECONDS),
                                                DEFAULT_UPCOMING_SECONDS, 5, 60)

        limits = config.get("game_limits") or {}
        self.recent_games_to_show = _safe_int(limits.get("recent_games_to_show", 2), 2, 0, 20)
        self.upcoming_games_to_show = _safe_int(limits.get("upcoming_games_to_show", 2), 2, 0, 20)
        self.other_recent_games_to_show = _safe_int(limits.get("other_recent_games_to_show", 1), 1, 0, 20)
        self.other_upcoming_games_to_show = _safe_int(limits.get("other_upcoming_games_to_show", 1), 1, 0, 20)
        self.other_rotation_interval_seconds = _safe_int(
            limits.get("other_rotation_interval_seconds", 1800), 1800, 0, 86400)
        self.favorite_rotation_boost = _safe_int(limits.get("favorite_rotation_boost", 2), 2, 1, 5)

        adv = config.get("advanced") or {}
        self.live_poll = _safe_int(adv.get("live_poll_seconds", 30), 30, 10, 300)
        self.idle_poll = _safe_int(adv.get("idle_poll_seconds", 600), 600, 60, 21600)
        self.request_timeout = _safe_int(adv.get("request_timeout", 10), 10, 3, 60)
        self.alert_seconds = _safe_int(adv.get("alert_seconds", DEFAULT_ALERT_SECONDS), DEFAULT_ALERT_SECONDS, 3, 30)
        self.animations = bool(adv.get("animations", True))
        self.animation_fps = _safe_int(adv.get("animation_fps", 30), 30, 5, 60)
        self.headshot_downloads = bool(adv.get("headshot_downloads", True))
        self.test_mode = bool(adv.get("test_mode", False))

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        super().on_config_change(new_config)
        self.config = new_config
        self._load_config(new_config)
        self.govee.configure(new_config.get("govee"))
        self.data.timeout = float(self.request_timeout)
        self.modes = self._available_modes()
        self._frame_cache.clear()
        self._shown_key = None
        self.last_update = 0.0
        self._shown_ts = None
        self._sync(time.time(), announce=False)

    # ------------------------------------------------------------------
    # update: the only place with network calls
    # ------------------------------------------------------------------

    def get_update_interval(self) -> Optional[float]:
        """Poll fast while a game is live or about to start. Attribute reads only."""
        if self.live or self.test_mode:
            return float(self.live_poll)
        now = time.time()
        for game in self.all_games:
            start = game.get("start")
            if game["state"] == "pre" and start and -600 <= start - now <= 900:
                return float(self.live_poll)
        if not self.all_games:
            return float(min(self.idle_poll, self.NO_DATA_RETRY_SECONDS))
        return float(self.idle_poll)

    def update(self) -> None:
        try:
            self._refresh(time.time())
        except Exception as exc:  # noqa: BLE001 - a bad payload must not take the loop down
            self.logger.error("NHL Live Ice update failed: %s", exc, exc_info=True)

    def _refresh(self, now: float) -> None:
        if self.test_mode:
            self._demo_step += 1
            games = model.parse_scoreboard(demo.scoreboard(self._demo_step, now=now))
        else:
            games = self.data.games(max_age=max(5.0, self.live_poll - 5.0), now=now,
                                    yesterday=self.show_recent, tomorrow=self.show_upcoming)
        self._snapshots.append((now, games))
        self.last_update = now
        self._sync(now)
        self._prefetch_photos()

    def _prefetch_photos(self) -> None:
        if not self.show_headshots:
            return
        players = [i["player"] for i in self.items[LIVE_MODE] if i["kind"] == "card"]
        players += [i["player"] for i in self.items[RECENT_MODE] if i["kind"] == "card"]
        players += [i["player"] for i in self._alerts if i.get("player")]
        if self.test_mode:  # the demo players are made up: never ask ESPN for their photos
            demo.seed_headshots(self.headshots, self.live + self.recent)
        elif self.headshot_downloads and players:
            if self.headshots.prefetch(players):
                self._frame_cache.clear()
                self._shown_key = None

    # ------------------------------------------------------------------
    # state: what is on screen (cheap; no network; safe from display())
    # ------------------------------------------------------------------

    def _sync(self, now: float, announce: bool = True) -> None:
        """Point at the snapshot the spoiler delay allows, then rebuild what is shown."""
        target = None
        for ts, games in self._snapshots:
            if ts <= now - self.spoiler_delay:
                target = (ts, games)
        if target is None:
            if self.spoiler_delay and self._snapshots:
                self.all_games = []
                self.live = self.recent = self.upcoming = []
                self.items = {LIVE_MODE: [], RECENT_MODE: [], UPCOMING_MODE: []}
            return
        if target[0] == self._shown_ts:
            return
        self._shown_ts = target[0]
        if announce:
            self._detect_scores(target[1], now, target[0])
        else:
            self._previous = {g["id"]: g for g in target[1]}
            self._previous_ts = target[0]
            for g in target[1]:
                g["run"] = self._runs.get(g["id"])
        self.all_games = target[1]
        self._select(now)
        self.data_version += 1
        self._frame_cache.clear()
        self._shown_key = None

    def _select(self, now: float) -> None:
        """Apply the favorites lock, spoiler excludes and rotation, for every mode."""
        chosen = model.select_live(self.all_games, self.favorites, self.lock, self.exclude, self.lock_fallback)
        self.live = model.rotation(chosen, self.favorites, self.boost)
        live_items: List[Dict[str, Any]] = []
        for game in self.live:
            stars = model.stars_for(game, self.cards_per_game, self.favorites)
            live_items.append({"kind": "scoreboard", "game": game, "star": stars[0] if stars else None})
            for star in stars:
                live_items.append({"kind": "card", "game": game, "player": star})

        other_recent = 0 if (self.lock and self.favorites) else self.other_recent_games_to_show
        recent_games = model.select_by_state(
            self.all_games, "post", self.favorites, self.recent_games_to_show, other_recent,
            self.other_rotation_interval_seconds, self.exclude, now, window_start=0)
        self.recent = model.rotation(recent_games, self.favorites, self.favorite_rotation_boost)
        recent_items: List[Dict[str, Any]] = []
        for game in self.recent:
            stars = model.stars_for(game, self.recent_cards_per_game, self.favorites)
            recent_items.append({"kind": "scoreboard", "game": game, "star": stars[0] if stars else None})
            for star in stars:
                recent_items.append({"kind": "card", "game": game, "player": star})

        other_upcoming = 0 if (self.lock and self.favorites) else self.other_upcoming_games_to_show
        upcoming_games = model.select_by_state(
            self.all_games, "pre", self.favorites, self.upcoming_games_to_show, other_upcoming,
            self.other_rotation_interval_seconds, self.exclude, now, window_start=1)
        self.upcoming = model.rotation(upcoming_games, self.favorites, self.favorite_rotation_boost)
        upcoming_items = [{"kind": "preview", "game": g} for g in self.upcoming]

        self.items = {LIVE_MODE: live_items, RECENT_MODE: recent_items, UPCOMING_MODE: upcoming_items}

    #: Polls further apart than this (a restart, a long outage) are not compared:
    #: the points between them were not "just scored".
    STALE_GAP_SECONDS = 180.0

    def _detect_scores(self, games: List[Dict[str, Any]], now: float, snapshot_ts: float) -> None:
        """Track each game's run of unanswered goals and queue a burst for each goal scored."""
        current = {g["id"]: g for g in games}
        fresh = self._previous_ts is not None and 0 <= snapshot_ts - self._previous_ts <= self.STALE_GAP_SECONDS
        for game_id, game in current.items():
            previous = self._previous.get(game_id) if fresh else None
            run = model.update_run(self._runs.get(game_id), previous, game) if game["live"] else None
            self._runs[game_id] = run
            game["run"] = run
            if not previous or not self.score_alerts or model.involves(game, self.exclude):
                continue
            alert = model.detect_alert(previous, game, self.alert_lead_changes_only, now)
            if not alert:
                continue
            self._govee_alert(game, alert)
            if game[alert["side"]]["abbr"] in self.favorites or self.alert_all or not self.favorites:
                if self.lock and self.favorites and not model.involves(game, self.favorites):
                    continue
                self._alerts.append(alert)
        for gone in [k for k in self._runs if k not in current]:
            self._runs.pop(gone, None)
        self._previous = current
        self._previous_ts = snapshot_ts

    GOVEE_BIG = {"OT WINNER", "GO-AHEAD"}

    def _govee_alert(self, game: Dict[str, Any], alert: Dict[str, Any]) -> None:
        """Flash the Govee lights for this score: 'score' for a favorite (or any team when none are set),
        'against' when the other side scores on a favorite. Never raises."""
        try:
            abbr = game[alert["side"]]["abbr"]
            if self.favorites and abbr not in self.favorites:
                event = "against" if model.involves(game, self.favorites) else ("score" if self.alert_all else None)
            else:
                event = "score"
            if event == "score" and alert.get("kind") in self.GOVEE_BIG:
                event = "big_score"
            if event:
                self.govee.trigger(event, alert.get("primary"), alert.get("accent"))
        except Exception as exc:  # noqa: BLE001 - lights must never break the display
            self.logger.debug("Govee trigger skipped: %s", exc)

    def _game_now(self, game_id: str) -> Optional[Dict[str, Any]]:
        for g in self.all_games:
            if g["id"] == game_id:
                return g
        return None

    def _next_alert(self, now: float) -> Optional[Dict[str, Any]]:
        """The burst on screen now, promoting the next queued one when the last has finished."""
        if self._alert and now - self._alert["shown_at"] >= self.alert_seconds:
            self._glow = {"game": self._alert["game"], "side": self._alert["side"], "until": now + GLOW_SECONDS}
            self._alert = None
        if self._alert is None and self._alerts:
            queued = self._alerts.pop(0)
            game = self._game_now(queued["game"])
            if game is not None and now - queued["at"] < 120:
                self._alert = dict(queued, kind_="alert", game_obj=game, shown_at=now)
        return self._alert

    # ------------------------------------------------------------------
    # display: draws from memory only
    # ------------------------------------------------------------------

    @property
    def needs_high_fps(self) -> bool:
        mode = self._current_mode or LIVE_MODE
        return bool(self.animations and (self.items.get(mode) or (mode == LIVE_MODE and self._alert)))

    @staticmethod
    def _durations(mode: str, items: List[Dict[str, Any]], seconds: Dict[str, int]) -> List[int]:
        return [seconds["card"] if i["kind"] == "card" else seconds[mode] for i in items]

    def _seconds_by_mode(self) -> Dict[str, int]:
        return {LIVE_MODE: self.field_seconds, RECENT_MODE: self.recent_game_duration,
                UPCOMING_MODE: self.upcoming_game_duration, "card": self.card_seconds}

    def _locate(self, mode: str, elapsed: float) -> Tuple[int, float]:
        """(index of the item on screen, seconds into it) for ``elapsed`` seconds in ``mode``'s cycle."""
        items = self.items.get(mode) or []
        durations = self._durations(mode, items, self._seconds_by_mode())
        total = sum(durations)
        pos = elapsed % total if total else 0.0
        for index, dur in enumerate(durations):
            if pos < dur:
                return index, pos
            pos -= dur
        return 0, 0.0

    def _logo_file(self) -> str:
        """The scoreboard logo to use: the configured path (relative ones live in the plugin folder),
        else ``logo.png`` in the plugin folder if it exists, else '' (the NHL shield)."""
        here = os.path.dirname(os.path.abspath(__file__))
        if self.board_logo:
            path = os.path.expanduser(self.board_logo)
            return path if os.path.isabs(path) else os.path.join(here, path)
        default = os.path.join(here, "logo.png")
        return default if os.path.exists(default) else ""

    def _ctx(self) -> render.RenderContext:
        status = "FAVS ONLY" if (self.lock and self.favorites) else f"{len({g['id'] for g in self.live})} LIVE"
        return render.RenderContext(self.headshots, self.animations, self.show_headshots, status,
                                    self._logo_file())

    def display(self, force_clear: bool = False, display_mode: Optional[str] = None) -> bool:
        if not self.enabled:
            return False
        mode = display_mode or LIVE_MODE
        if mode not in self.items:
            return False
        now = time.time()
        self._sync(now)
        alert = self._next_alert(now) if mode == LIVE_MODE else None
        items = self.items.get(mode) or []
        if not items and not alert:
            return False
        entering = mode != self._current_mode or force_clear
        if entering:
            self._current_mode = mode
            self._mode_started = now
            self._shown_key = None
        w, h = self.display_manager.width, self.display_manager.height
        if alert:
            game = alert["game_obj"]
            fn, item, t = render.alert, dict(alert, game=game), now - alert["shown_at"]
            key_id, title, color = ("alert", alert["game"], alert["at"]), "SCORE!", draw.GOLD
        else:
            index, t = self._locate(mode, now - self._mode_started)
            item = dict(items[index])
            game = item["game"]
            title, color = MODE_TITLE[mode]
            if mode == LIVE_MODE:
                glow = self._glow
                if glow and now < glow["until"] and glow["game"] == game["id"]:
                    item["celebrate"] = (glow["side"], t)
            if item["kind"] == "card":
                fn, title, color = render.card, "TOP PERFORMER", draw.TIER_COLORS[item["player"]["tier"]]
                key_id = ("card", mode, game["id"], item["player"]["id"], index)
            elif item["kind"] == "preview":
                fn = render.preview
                key_id = ("preview", game["id"])
            else:
                fn = render.scoreboard
                key_id = ("scoreboard", mode, game["id"], index, bool(item.get("celebrate")))
        key = key_id + (self.data_version, w, h)
        cached = None if force_clear else self._frame_cache.get(key)
        if cached is not None:
            if self._shown_key == key:
                return True  # the panel already holds this exact frame
            frame = cached
        else:
            if self.animations and not force_clear and now - self._last_frame_at < 1.0 / self.animation_fps:
                return True
            ctx = self._ctx()
            try:
                frame = render.render_frame(fn, ctx, item, w, h, t if self.animations else 99.0, title, color)
            except Exception as exc:  # noqa: BLE001 - never lose the loop to a draw
                self.logger.error("Could not draw %s: %s", key_id[0], exc, exc_info=True)
                return False
            still = not self.animations and not ctx.moving and key_id[0] != "alert"
            if still:
                self._frame_cache[key] = frame
        self._last_frame_at = now
        self.display_manager.image.paste(frame, (0, 0))
        self.display_manager.update_display()
        self._shown_key = key if key in self._frame_cache else None
        return True

    def get_display_duration(self) -> float:
        """Long enough to show every screen of the current mode once."""
        mode = self._current_mode or LIVE_MODE
        items = self.items.get(mode) or []
        if not items:
            return float(self.display_duration)
        total = sum(self._durations(mode, items, self._seconds_by_mode()))
        floor = self._seconds_by_mode().get(mode, self.field_seconds)
        return float(max(floor, min(300, total)))

    def has_live_priority(self) -> bool:
        return bool(self.live_priority and self.enabled)

    def has_live_content(self) -> bool:
        """A game (that passes the lock and the spoiler delay) is live. No network."""
        if not self.has_live_priority():
            return False
        self._sync(time.time())
        return bool(self.live)

    def get_live_modes(self) -> List[str]:
        return [LIVE_MODE]

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update({
            "favorite_teams": list(self.favorites),
            "lock_to_favorites": self.lock,
            "modes": list(self.modes),
            "games_fetched": len(self.all_games),
            "games_live": len({g["id"] for g in self.live}),
            "live_matchups": [f"{g['away']['abbr']}@{g['home']['abbr']}" for g in self.live],
            "recent_matchups": [f"{g['away']['abbr']}@{g['home']['abbr']}" for g in self.recent],
            "upcoming_matchups": [f"{g['away']['abbr']}@{g['home']['abbr']}" for g in self.upcoming],
            "screens": {m: len(i) for m, i in self.items.items()},
            "pending_alerts": len(self._alerts) + (1 if self._alert else 0),
            "last_update": self.last_update,
            "last_error": self.data.last_error,
        })
        return info

    def cleanup(self) -> None:
        self.govee.close()
        self._frame_cache.clear()
        try:
            self.data.session.close()
        except Exception as exc:  # noqa: BLE001 - closing is best effort
            self.logger.debug("Could not close the HTTP session: %s", exc)
        super().cleanup()


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default
