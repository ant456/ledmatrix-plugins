# NFL Live Field

<!-- hero -->
**NFL Sunday, with an animated field and a touchdown burst.**

The live game plays out on a tiny football field: the ball sits at the yard line, the first-down marker glows, the arrows point the way. Stats cards show the quarterback's line with a headshot and a rarity tier, and a scoring play erupts in a TOUCHDOWN! burst with the passer, the receiver and the play.

Live, recent and upcoming games each have their own rotation, with favourites and lock-to-favourites.

- Animated field with ball, down and distance
- Player cards with headshots and rarity
- Touchdown bursts
- Final scores with top performers
- Upcoming matchups

<p align="center"><img src="screenshots/01-field.png" width="768" alt="Live game on the field"><br><sub>Live game on the field</sub></p>
<p align="center"><img src="screenshots/02-card.png" width="768" alt="Player card"><br><sub>Player card</sub></p>
<p align="center"><img src="screenshots/03-touchdown.png" width="768" alt="Touchdown!"><br><sub>Touchdown!</sub></p>
<p align="center"><img src="screenshots/04-final.png" width="768" alt="Final"><br><sub>Final</sub></p>
<p align="center"><img src="screenshots/05-upcoming.png" width="768" alt="Upcoming matchup"><br><sub>Upcoming matchup</sub></p>

*Screenshots are real frames from the plugin's renderer, shown 8x on a 128x32 panel.*
<!-- /hero -->

An arcade-style NFL show for your LED scoreboard, built like Fantasy Blitz:
an animated field, collectible player cards with real headshots and stat lines,
and a touchdown burst when your team scores -- for **live**, **recent** (final
score) and **upcoming** (matchup preview) games, each its own rotation. A
**favorites lock** limits all three to your teams. No login or API key: one
ESPN scoreboard call covers live, recent and upcoming together.

## The screens

| Screen | What it shows | When |
|--------|---------------|------|
| **Field** | Score rows in each team's colors with timeout pips and a ball marker for who has it, a striped football field with the ball bobbing on the line of scrimmage, drive arrows marching the way the offense is going, a gold first-down line, a pulsing red tint and flame in the red zone, and the last play scrolling along the bottom | One per live game |
| **Player card** | A game leader (passing, rushing or receiving) as a trading card: ESPN headshot, name, position tag, count-up yardage, the stat line (`27/35 C/A  3 TD`) and an XP bar filling toward the next rarity. The frame color is the rarity: gold (Legendary), purple (Epic), blue (Rare) | After each game's field screen, `cards_per_game` of them |
| **Score burst** | A spinning sunburst in the scoring team's colors, confetti on touchdowns, the scorer's photo sliding in (named from ESPN's play text, e.g. "pass to T.Kelce"), the team and the new score. It interrupts the rotation for a few seconds | When a favorite scores |

After a burst the scoring team's row keeps pulsing on the field screen for a few
seconds.

## The recent-game stat ticker

On a finished game, the space where the field would be shows the game's
leaders instead: each row's yardage counts up in turn, a colored bar cascades
in with it, and a spotlight cycles between rows to keep the panel moving.
With no leaders data (rare -- ESPN sometimes drops it shortly after a game
ends) it just says so, rather than showing an empty box.

## Live, recent and upcoming

The plugin has three separate display modes, so it fits into your rotation as
three entries -- disable the ones you don't want from the plugin's mode list
in the web UI, independent of `show_recent`/`show_upcoming` below.

| Mode | Shows | Screens |
|------|-------|---------|
| **Live** (`nfl_live`) | Games in progress | Field, player cards, touchdown bursts |
| **Recent** (`nfl_recent`) | Finished games | Score rows + an animated stat-leader ticker where the field would be, plus player cards |
| **Upcoming** (`nfl_upcoming`) | Games that haven't kicked off | Matchup + kickoff time |

Recent and upcoming games use the ESPN response's own `state`, so there's no
extra network call for them -- the one scoreboard fetch that live already
makes covers all three.

Each mode picks games the same way: your favorite teams first (up to
`game_limits.recent_games_to_show` / `upcoming_games_to_show`), then a
rotating slice of everyone else's games (`game_limits.other_recent_games_to_
show` / `other_upcoming_games_to_show`), which advances to the next slice of
the schedule every `game_limits.other_rotation_interval_seconds`. A favorite's
game gets `game_limits.favorite_rotation_boost` turns for every one turn
another game gets. These are the same setting names football-scoreboard and
the repo's other scoreboards use.

The **favorites lock** (below) applies to all three modes: with it on, recent
and upcoming only ever show your teams' games too.

### Rarity

A player's value is their yards plus 25 per touchdown. Thresholds by category:

| | Rare | Epic | Legendary |
|--|------|------|-----------|
| Passing | 175 | 275 | 350 |
| Rushing / receiving | 65 | 110 | 170 |

The XP bar fills toward the next tier, and goes gold once a player is Legendary.

## The favorites lock

Pick your teams in **Favorite teams**, then choose how strict to be:

| Lock | Your team is live | Your team is not playing |
|------|-------------------|--------------------------|
| **Off** (default) | Your games come first and get extra turns (`favorite_live_boost`), and your players' cards lead each game | Every live game rotates |
| **On** | Only your games are shown (field, cards and bursts) | Nothing is shown and the display moves on to the next plugin |
| **On + `lock_fallback_to_all`** | Only your games are shown | Other live games show until yours kick off, then it locks back |

- With **no favorite teams selected** the lock has nothing to lock to, so every
  live game shows (and every score gets a burst).
- **`exclude_teams`** beats everything, including the lock.
- The lock takes effect as soon as you save the setting.
- Bursts follow the lock: a game the lock hides never bursts.

### Watching on a delay

`spoiler_delay_seconds` holds back scores, cards and bursts by that many seconds
(up to 5 minutes), so a delayed stream isn't spoiled. `exclude_teams` hides a
team entirely.

## Setting it up

1. Install NFL Live Field and enable it.
2. Choose your **Favorite teams** and, if you want only them, turn on **Lock to favorites**.
3. Try the look on a quiet day with **Test mode** (Advanced): made-up games with
   leaders and stand-in photos, and a KC touchdown burst every third refresh.

If you run this alongside football-scoreboard, turn off its NFL live mode so
only one plugin claims NFL live games.

## Settings

| Setting | Type | Default | What it does |
|---------|------|---------|--------------|
| `enabled` | boolean | `false` | Enable the plugin. |
| `favorite_teams` | array | `[]` | Your teams (ESPN codes; `WAS`, `JAC`, `LVR` are folded to `WSH`, `JAX`, `LV`). |
| `lock_to_favorites` | boolean | `false` | Show only your teams' games -- live, recent and upcoming alike. |
| `lock_fallback_to_all` | boolean | `false` | With the lock on, show other live games while none of yours is playing. |
| `live_game_duration` | integer (5-120) | `12` | Seconds on each live game's field screen. |
| `cards_per_game` | integer (0-4) | `2` | Player cards after each live field screen (0 = none). |
| `card_seconds` | integer (3-30) | `6` | Seconds per card, live or recent. |
| `favorite_live_boost` | integer (1-5) | `2` | Turns a favorite's *live* game gets per 1 turn of another live game. Ignored when the lock is on. |
| `live_priority` | boolean | `true` | Let live games take over the rotation. |
| `show_headshots` | boolean | `true` | ESPN photos on cards and bursts; off draws a team-color silhouette with the jersey number. |
| `score_alerts` | boolean | `true` | The burst when a favorite scores. |
| `show_recent` | boolean | `true` | Enable the recent-games (final score) mode. |
| `recent_game_duration` | integer (5-120) | `10` | Seconds on each recent game's final-score screen. |
| `recent_cards_per_game` | integer (0-4) | `1` | Player cards after each recent game's score (0 = none). |
| `show_upcoming` | boolean | `true` | Enable the upcoming-games (preview) mode. |
| `upcoming_game_duration` | integer (5-60) | `8` | Seconds on each upcoming game's preview screen. |
| `game_limits.recent_games_to_show` | integer (0-20) | `2` | Your favorites' recent games to include. |
| `game_limits.upcoming_games_to_show` | integer (0-20) | `2` | Your favorites' upcoming games to include. |
| `game_limits.other_recent_games_to_show` | integer (0-20) | `1` | Non-favorite recent games to include, from a rotating slice. |
| `game_limits.other_upcoming_games_to_show` | integer (0-20) | `1` | Non-favorite upcoming games to include, from a rotating slice. |
| `game_limits.other_rotation_interval_seconds` | integer (0-86400) | `1800` | How often the non-favorite slice advances. |
| `game_limits.favorite_rotation_boost` | integer (1-5) | `2` | Turns a favorite's *recent/upcoming* game gets per 1 turn of another. |
| `spoiler_delay_seconds` | integer (0-300) | `0` | Hold everything back this long. |
| `exclude_teams` | array | `[]` | **Advanced.** Never show these teams' games, in any mode. |
| `celebrate_all_teams` | boolean | `false` | **Advanced.** Burst for any team's score. |
| `display_duration` | number (5-300) | `20` | Seconds for a mode when it has nothing to show. |
| `advanced.live_poll_seconds` | integer (10-300) | `30` | Refresh interval while a game is live (or within 10-15 minutes of kickoff). |
| `advanced.idle_poll_seconds` | integer (60-21600) | `600` | Refresh interval otherwise. |
| `advanced.request_timeout` | integer (3-60) | `10` | Seconds to wait for ESPN. |
| `advanced.alert_seconds` | integer (3-30) | `8` | How long a burst stays up. |
| `advanced.animations` | boolean | `true` | Animate the screens. Off draws each once, lighter on an older Pi. |
| `advanced.animation_fps` | integer (5-60) | `30` | Animation frame rate. |
| `advanced.headshot_downloads` | boolean | `true` | Download player photos. Off uses only photos already saved. |
| `advanced.test_mode` | boolean | `false` | Use built-in demo games (live, final and upcoming) instead of ESPN. |

## How it updates

| Data | Source | Refresh |
|------|--------|---------|
| Scores, clock, down and distance, ball position, timeouts, last play, team leaders | ESPN scoreboard (`site.api.espn.com/.../football/nfl/scoreboard`) | Every `live_poll_seconds` while live or near kickoff, else every `idle_poll_seconds` |
| Player photos | ESPN headshot URLs from the scoreboard's leaders | A few at a time during `update()`, kept for a month |
| Club crests (scorer fallback) | The core's `assets/sports/nfl_logos/` | Read from disk |

Every request is cached under the plugin's id, and a failed fetch falls back to
the last good copy (the reason shows as `last_error` in the plugin info).
`update()` does all the fetching; `display()` only draws from memory.

Where the ball is comes from ESPN's `possessionText` ("DAL 35" = Dallas's 35),
which names the side of the field. If that is missing the plugin uses `yardLine`
plus who has possession, and with neither the field is drawn without a ball.

## Panel sizes

Each screen has a 32-row and a 64-row design that adapts to narrow (under 96
wide), normal and wide panels. On a 192+ wide panel the field sits beside the
game's top player card. Panels with spare rows get a title band and a status bar
("FAVS ONLY" when locked), and large panels draw the 128x64 design at 2x.

## Troubleshooting

- **Nothing shows:** no game is live, or the lock is on and none of yours is.
  Try `advanced.test_mode`, then check `live_matchups` and `last_error` in the
  plugin info.
- **Silhouettes instead of photos:** photos download a few at a time, so a fresh
  install fills them in over the first few updates. Check `show_headshots` and
  `advanced.headshot_downloads`.
- **No ball on the field:** ESPN omits the situation between some plays.
- **No cards for a game:** `cards_per_game` is 0, or ESPN has no leaders yet
  (early in the first quarter).

## Development

```bash
# Logic, render and manager tests (no core needed; a stub stands in for BasePlugin)
python -m pytest plugins/nfl-live-field

# From a LEDMatrix core checkout: every size against the safety harness
python scripts/check_plugin.py --plugin nfl-live-field --plugin-dir /path/to/ledmatrix-plugins/plugins
```

Files: `manager.py` (plugin class), `nfllive_data.py` (ESPN fetch + cache),
`nfllive_model.py` (parsing, ball position, leaders and rarity, the lock, alerts),
`nfllive_headshots.py` (photo download and cropping), `nfllive_render.py`
(the three screens), `nfllive_draw.py` and `nfllive_font.py` (the arcade drawing
kit and 3x5 pixel font, adapted from Fantasy Blitz), `nfllive_demo.py` (test mode data).
