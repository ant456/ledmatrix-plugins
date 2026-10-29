"""The NFL Live Field screens, arcade style.

Three screens, each drawn as a *short* (32 rows) or *tall* (64 rows) design
that adapts to narrow (under 96 px), normal (96-191) and wide (192 and up,
where the field sits beside a player card) panels. Panels with spare rows get
a title band and a status bar, and very large panels draw the 128x64 design at
2x, exactly as the Fantasy Blitz plugin does.

* ``field``  -- score rows in team colours, a striped football field with the
  ball, marching drive chevrons, a first-down line and a red-zone flame.
* ``card``   -- a game leader as a collectible card: ESPN headshot, rarity
  frame (gold / purple / blue), count-up yardage, stat line and an XP bar that
  fills toward the next rarity.
* ``alert``  -- the burst when someone scores: spinning sunburst, confetti on
  touchdowns, the scorer's photo and the new score.

Renderers take ``t``, seconds since the item came on screen, for the intro
animations (count-up, bar fill, slide-in) and the continuous ones (foil
shimmer, sunburst, chevrons, confetti). With animation off they draw the final
frame.
"""

import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_sin, _pi = math.sin, math.pi

from PIL import Image

import nfllive_draw as d
import nfllive_font as font
import nfllive_model as model

RGB = Tuple[int, int, int]

TURF: RGB = (11, 50, 21)
TURF_ALT: RGB = (14, 59, 26)
TURF_LINE: RGB = (36, 96, 50)
RED_TINT: RGB = (120, 24, 24)
BALL: RGB = (176, 92, 34)


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


_ease = ease


def team_dict(side: Dict[str, Any]) -> Dict[str, Any]:
    """A game side (ESPN hex colours) in the shape the art kit expects."""
    return {"abbr": side["abbr"], "primary": model.hex_to_rgb(side["color"]),
            "accent": model.hex_to_rgb(side["alt_color"], (230, 230, 235))}


def side_of(game: Dict[str, Any], abbr: str) -> Dict[str, Any]:
    return game["home"] if game["home"]["abbr"] == abbr else game["away"]


class RenderContext:
    """What every renderer needs besides the item: art, animation and status."""

    def __init__(self, headshots=None, animate: bool = True, show_headshots: bool = True,
                 status: str = ""):
        self.headshots = headshots
        self.animate = animate
        self.show_headshots = show_headshots
        self.status = status
        #: Set by a frame that scrolls a long line: not a still frame, so the
        #: plugin must keep drawing it rather than cache it.
        self.moving = False

    def label(self, img: Image.Image, text: object, x: int, y: int, max_w: int, color,
              t: float, align: str = "left", outline: Optional[RGB] = None) -> int:
        """Text that always fits ``max_w``: whole, scrolling, or cut at a letter."""
        s = font.normalize(text)
        width = font.text_width(s)
        if width <= max_w or max_w <= 0:
            return d.text(img, s, x, y, color, 1, align, outline)
        left = x if align == "left" else (x - max_w // 2 if align == "center" else x - max_w)
        if not self.animate:
            return d.text(img, font.fit(s, max_w), left, y, color, 1, "left", outline)
        self.moving = True
        travel = width - max_w
        pause, speed = 1.2, 14.0
        cycle = pause * 2 + travel / speed
        phase = t % cycle
        offset = 0 if phase < pause else min(travel, int((phase - pause) * speed))
        strip = Image.new("RGB", (width + 2, font.GLYPH_HEIGHT + 2), d.BLACK)
        mask = Image.new("L", strip.size, 0)
        d.text(strip, s, 1, 1, color, 1, "left", outline)
        font.draw_text(mask, s, 1, 1, 255, 1, "left", 255 if outline is not None else None)
        box = (offset + 1, 0, offset + 1 + max_w, strip.height)
        img.paste(strip.crop(box), (left, y - 1), mask.crop(box))
        return max_w

    def photo(self, player: Dict[str, Any], w: int, h: int) -> Optional[Image.Image]:
        if not self.show_headshots or self.headshots is None:
            return None
        try:
            return self.headshots.portrait(player, w, h)
        except Exception:  # noqa: BLE001 - art is never worth a crash
            return None

    def logo(self, abbr: str, w: int, h: int) -> Optional[Image.Image]:
        if self.headshots is None:
            return None
        try:
            return self.headshots.logo(abbr, w, h)
        except Exception:  # noqa: BLE001
            return None

    def picture(self, img: Image.Image, player: Dict[str, Any], game: Dict[str, Any],
                x: int, y: int, w: int, h: int) -> None:
        """The player's picture box: photo, else a club-colour silhouette with the jersey number."""
        side = side_of(game, player.get("team", ""))
        d.portrait(img, x, y, w, h, team_dict(side), player.get("jersey", ""), self.photo(player, w, h))


# ----------------------------------------------------------------------
# small shared pieces
# ----------------------------------------------------------------------

def _ball(img: Image.Image, cx: float, cy: float, r: float = 2.0) -> None:
    d.ellipse(img, cx, cy, r * 1.7, r, BALL)
    d.rect(img, int(cx) - 1, int(cy), 3, 1, d.WHITE)


def _pips(img: Image.Image, x: int, y: int, remaining: int, color: RGB) -> None:
    for i in range(3):
        d.rect(img, x + i * 4, y, 3, 2, color if i < remaining else d.DIM)


def _chevrons(img: Image.Image, x0: float, cy: int, direction: int, t: float, color: RGB,
              count: int = 3, spacing: int = 5, max_h: int = 5) -> None:
    """Drive arrows marching away from the ball, fading with distance."""
    span = count * spacing
    half = max(1, min(2, (max_h - 1) // 2))
    for k in range(count):
        travel = (k * spacing + t * 9.0) % span
        fade = 1.0 - travel / span
        px = int(round(x0 + direction * (4 + travel)))
        c = d.scale(color, 0.35 + 0.65 * fade)
        for i in range(-half, half + 1):
            d.rect(img, px + direction * (half - abs(i)), cy + i, 1, 1, c)  # tip (i=0) leads, wings trail


def _header(img: Image.Image, x: int, y: int, w: int, game: Dict[str, Any], t: float,
            animate: bool, with_dd: bool = True) -> None:
    """The 7-row band: a status light (blinking for live, solid for final) plus
    the clock/score-state and, room permitting, the down and distance."""
    d.hgrad(img, x, y, w, 7, (30, 34, 46), (12, 14, 20))
    live = game["live"]
    on = (not live) or (not animate) or int(t * 1.4) % 2 == 0
    tag_color = d.RED if live else d.scale(d.GRAY, 1.15)
    d.dot(img, x + 3, y + 3, tag_color if on else d.scale(tag_color, 0.35))
    used = 7
    if w >= 56:
        used += d.text(img, "LIVE" if live else "FINAL", x + 7, y + 1, tag_color) + 3
    clock = "HALF" if game["period_text"] == "HALF" else f"{game['period_text']} {game['clock']}".strip()
    if not live:
        clock = game["period_text"]
    cw = font.text_width(clock)
    dd = game["down_distance"] if (with_dd and live) else ""
    right_w = font.text_width(dd) + 4 if dd else 0
    if used + cw + right_w + 4 <= w:
        d.text(img, clock, x + used + max(0, (w - used - right_w - cw) // 2), y + 1, d.GOLD)
        if dd:
            d.text(img, dd, x + w - 2, y + 1, d.WHITE, 1, "right")
    else:
        d.text(img, font.fit(clock, w - used - 2), x + used, y + 1, d.GOLD)


def _score_row(img: Image.Image, x: int, y: int, w: int, h: int, side: Dict[str, Any],
               has_ball: bool, timeouts: int, glow: float, t: float, animate: bool) -> None:
    primary, accent = d.team_colors(team_dict(side))
    lit = max(0.0, min(1.0, glow))
    d.hgrad(img, x, y, w, h, d.mix(d.scale(primary, 0.42), d.WHITE, 0.45 * lit), d.mix(d.scale(primary, 0.16), primary, 0.5 * lit))
    d.rect(img, x, y, 2, h, primary)
    big = h >= 11
    ax = x + 5
    label_scale = 2 if (h >= 12 and w >= 46) else 1
    ay = y + max(0, (h - font.text_height(label_scale)) // 2)
    d.text(img, side["abbr"], ax, ay, d.WHITE, label_scale, "left", d.BLACK if lit > 0.4 else None)
    ax += font.text_width(side["abbr"], label_scale) + 2
    if has_ball:
        _ball(img, ax + 2, y + h // 2, 1.4)
    score = str(side["score"])
    s_scale = 2 if big and font.text_width(score, 2) + ax + 8 - x <= w else 1
    sy = y + max(0, (h - font.text_height(s_scale)) // 2)
    if s_scale == 2 and h >= 12:
        d.big_number(img, score, x + w - 2, sy, d.mix(d.WHITE, accent, 0.0), 2, "right")
    else:
        d.text(img, score, x + w - 2, sy, d.WHITE, s_scale, "right")


def _yard_x(fx: int, fw: int, ez: int, yards: float) -> int:
    """x of a position given as yards from the left goal line (0-100)."""
    return fx + ez + int(round((fw - 2 * ez) * yards / 100.0))


def _field(img: Image.Image, x: int, y: int, w: int, h: int, game: Dict[str, Any], t: float,
           animate: bool) -> None:
    """The football field, end zones in team colours, ball and drive."""
    away, home = game["away"], game["home"]
    ez = max(4, min(11, w // 9))
    pa, _ = d.team_colors(team_dict(away))
    ph, _ = d.team_colors(team_dict(home))
    d.rect(img, x, y, w, h, TURF)
    play_w = w - 2 * ez
    for i in range(10):  # alternating 10-yard stripes
        if i % 2:
            d.rect(img, x + ez + int(play_w * i / 10), y, int(play_w * (i + 1) / 10) - int(play_w * i / 10), h, TURF_ALT)
    ball, fd = game.get("ball"), game.get("first_down")
    if game.get("red_zone") and ball is not None:
        pulse = 0.45 + 0.35 * math.sin(t * 5) if animate else 0.6
        lo, hi = (0, 20) if ball <= 50 else (80, 100)
        x0, x1 = _yard_x(x, w, ez, lo), _yard_x(x, w, ez, hi)
        d.rect(img, x0, y, x1 - x0, h, RED_TINT, pulse)
    d.rect(img, x, y, ez, h, d.scale(pa, 0.55))
    d.rect(img, x + w - ez, y, ez, h, d.scale(ph, 0.55))
    for yard in range(10, 100, 10):
        lx = _yard_x(x, w, ez, yard)
        d.rect(img, lx, y, 1, h, (70, 150, 84) if yard == 50 else TURF_LINE)
    d.frame(img, x, y, w, h, (150, 158, 172))
    if h >= 20:  # end-zone lettering
        for side, ex in ((away, x), (home, x + w - ez)):
            letter = side["abbr"]
            if font.text_width(letter) <= ez - 1:
                d.text(img, letter, ex + ez // 2, y + (h - 5) // 2, d.WHITE, 1, "center")
            else:
                d.text(img, letter[:1], ex + ez // 2, y + (h - 5) // 2, d.WHITE, 1, "center")
    if w >= 110 and h >= 20:  # yard numbers on wide fields
        for yard, label in ((20, "2"), (30, "3"), (40, "4"), (50, "5"), (60, "4"), (70, "3"), (80, "2")):
            d.text(img, label + "0", _yard_x(x, w, ez, yard), y + h - 7, d.scale(d.WHITE, 0.32), 1, "center")
    if ball is None:
        return
    cy = y + h // 2
    direction = 1 if game.get("possession") == "away" else -1 if game.get("possession") == "home" else 0
    bx = _yard_x(x, w, ez, ball)
    if fd is not None:
        fx = _yard_x(x, w, ez, fd)
        d.rect(img, fx, y + 1, 1, h - 2, d.GOLD)
        d.rect(img, fx, y + 1, 3, 2, d.GOLD)
    d.rect(img, bx, y + 1, 1, h - 2, (110, 170, 255))
    if direction:
        pa_color, ph_color = pa, ph
        mine = pa_color if direction == 1 else ph_color
        _chevrons(img, bx, cy, direction, t if animate else 0.4, d.mix(mine, d.WHITE, 0.5), max_h=min(5, h - 2))
    r = 2.4 + (0.5 * math.sin(t * 6) if animate else 0)
    _ball(img, bx, cy, r if h >= 20 else 1.6)
    if game.get("red_zone") and h >= 22 and w >= 60:
        fx0 = x + w - ez + 1 if ball > 50 else x + 1
        d.flame(img, fx0 + max(0, (ez - 6) // 2), y + 1, flicker=animate and int(t * 6) % 2 == 0)


def _recap_panel(img: Image.Image, x: int, y: int, w: int, h: int, game: Dict[str, Any],
                 t: float, animate: bool) -> None:
    """Where the field would be on a finished game: an animated stat ticker of
    the game's leaders instead -- a staggered count-up reveal per row, with a
    spotlight that cycles between rows so the panel keeps moving even once
    every number has landed.
    """
    stars = [p for p in game.get("stars", []) if p.get("value", 0) > 0][:3]
    if w < 4 or h < 4:
        return
    if not stars:
        d.text(img, "NO STAT LEADERS", x + w // 2, y + max(0, h // 2 - 2), d.GRAY, 1, "center")
        return
    rows = max(1, min(len(stars), h // 10))
    stars = stars[:rows]
    row_h = h // rows
    cycle = 2.2
    active = int(t / cycle) % rows if animate else 0
    for i, p in enumerate(stars):
        ry = y + i * row_h
        rh = row_h - (1 if i < rows - 1 else 0)
        color = d.TIER_COLORS[p["tier"]]
        k = _ease((t - i * 0.25) / 0.9) if animate else 1.0
        is_active = animate and i == active
        pulse = 0.5 + 0.5 * _sin(t * 2 * _pi / 1.0) if is_active else 0.0
        bg = d.mix(d.BLACK, d.scale(color, 0.5), (0.18 + 0.22 * pulse) * max(0.15, k))
        d.rect(img, x, ry, w, rh, bg)
        d.rect(img, x, ry, 2, rh, d.scale(color, 0.4 + 0.6 * k))
        dx = int(round((1 - k) * 5))
        tx = x + 4 + dx
        avail = w - 6
        if rh >= 9 and avail - (tx - x) > 18:
            tag_w = d.pos_tag(img, tx, ry + max(0, (rh - 7) // 2), p["pos"] or "?")
            name_x = tx + tag_w + 2
        else:
            name_x = tx
        text_color = d.mix(d.BLACK, d.WHITE, min(1.0, k * 1.3))
        name_w = max(6, avail - (name_x - x) - 20)
        d.text(img, p["last"], name_x, ry + max(0, (rh - 5) // 2), text_color)
        shown = str(int(round(p["yards"] * min(1.0, k))))
        d.text(img, shown, x + w - 2, ry + max(0, (rh - 5) // 2), d.scale(color, 0.5 + 0.5 * k), 1, "right")


def _play_bar(ctx: RenderContext, img: Image.Image, x: int, y: int, w: int, h: int,
              game: Dict[str, Any], t: float) -> None:
    d.rect(img, x, y, w, h, (6, 8, 12))
    d.hline(img, x, y, w, (30, 36, 50))
    text = game.get("last_play") or game.get("possession_text") or game.get("detail") or ""
    ctx.label(img, text, x + 3, y + max(1, (h - 5) // 2 + 1), w - 6, d.GRAY, t)


def _glow_for(item: Dict[str, Any], t: float) -> Dict[str, float]:
    """Row-flash for a side that just scored (item['celebrate'] = (side, started_offset))."""
    out = {"home": 0.0, "away": 0.0}
    cel = item.get("celebrate")
    if cel and cel[0] in out:
        out[cel[0]] = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.2)
    return out


# ----------------------------------------------------------------------
# frame assembly
# ----------------------------------------------------------------------

ScreenFn = Callable[["RenderContext", Dict[str, Any], int, int, float], Image.Image]


def base_size(width: int, height: int) -> Tuple[int, int, int]:
    """``(scale, base_w, base_h)``: very large panels draw a smaller design at 2x, 3x..."""
    s = max(1, min(int(width) // 128, int(height) // 64))
    return s, max(64, int(width) // s), max(32, int(height) // s)


def render_frame(fn: ScreenFn, ctx: RenderContext, item: Dict[str, Any], width: int, height: int,
                 t: float = 99.0, title: str = "", title_color: RGB = d.GOLD) -> Image.Image:
    """A finished ``width`` x ``height`` frame of one item."""
    width, height = max(1, int(width)), max(1, int(height))
    s, bw, bh = base_size(width, height)
    ctx.moving = False
    design_h = 64 if bh >= 64 else 32
    body = fn(ctx, item, bw, design_h, t)
    base = Image.new("RGB", (bw, bh), d.BLACK)
    extra = bh - design_h
    if extra >= 12:
        head = extra // 2
        base.paste(body, (0, head))
        d.hgrad(base, 0, 0, bw, head, d.scale(title_color, 0.32), d.scale(title_color, 0.10))
        d.text(base, title, bw // 2, max(0, (head - 5) // 2), title_color, 1, "center")
        foot = bh - head - design_h
        d.rect(base, 0, head + design_h, bw, foot, (6, 8, 12))
        d.dot(base, 4, head + design_h + foot // 2, d.RED)
        d.text(base, ctx.status or "NFL LIVE", 9, head + design_h + max(0, (foot - 5) // 2), d.GRAY)
    else:
        base.paste(body, (0, extra // 2))
    if s > 1:
        base = base.resize((bw * s, bh * s), Image.NEAREST)
    if base.size == (width, height):
        return base
    out = Image.new("RGB", (width, height), d.BLACK)
    out.paste(base, ((width - base.width) // 2, (height - base.height) // 2))
    return out


def side_by_side(ctx: RenderContext, left: ScreenFn, left_item: Dict[str, Any], right: ScreenFn,
                 right_item: Dict[str, Any], w: int, h: int, t: float, left_w: int = 128) -> Image.Image:
    img = Image.new("RGB", (w, h), d.BLACK)
    img.paste(left(ctx, left_item, left_w, h, t), (0, 0))
    d.rect(img, left_w, 0, 1, h, d.DIM)
    img.paste(right(ctx, right_item, w - left_w - 1, h, t), (left_w + 1, 0))
    return img


# ----------------------------------------------------------------------
# screen 1: the field
# ----------------------------------------------------------------------

def field(ctx: RenderContext, item: Dict[str, Any], w: int, h: int, t: float) -> Image.Image:
    """``item``: ``{"game", "star"?, "celebrate"?}``."""
    game = item["game"]
    if w >= 192 and item.get("star"):
        return side_by_side(ctx, field, dict(item, star=None), card,
                            {"game": game, "player": item["star"]}, w, h, t, 128)
    img = Image.new("RGB", (w, h), d.BLACK)
    glow = _glow_for(item, t)
    animate = ctx.animate
    tall = h >= 64
    away, home = game["away"], game["home"]
    if w >= 96:
        lw = 56 if w < 150 else 68
        _header(img, 0, 0, w, game, t, animate, with_dd=not tall)
        row_h = 12 if tall else 11
        y0 = 9 if tall else 8
        _score_row(img, 0, y0, lw, row_h, away, game["possession"] == "away", game["away_timeouts"], glow["away"], t, animate)
        _score_row(img, 0, y0 + row_h + 1, lw, row_h, home, game["possession"] == "home", game["home_timeouts"], glow["home"], t, animate)
        fy = y0
        fh = (h - 11 - fy) if tall else (h - fy)
        if game["live"]:
            _field(img, lw + 2, fy, w - lw - 2, fh, game, t, animate)
        else:
            _recap_panel(img, lw + 2, fy, w - lw - 2, fh, game, t, animate)
        if tall:
            ty = y0 + 2 * row_h + 2
            if game["live"]:
                dd = game["down_distance"] or "-"
                d.text(img, dd, lw // 2, ty, d.WHITE, 1, "center")
                ball_at = game["possession_text"]
                if ball_at:
                    d.text(img, font.fit("BALL " + ball_at, lw - 2), lw // 2, ty + 7, d.GRAY, 1, "center")
                ty += 14  # timeouts: TO, then one row of pips per team, side by side
                d.text(img, "TO", 2, ty, d.DIM)
                _pips(img, 12, ty + 1, game["away_timeouts"], d.mix(d.team_colors(team_dict(away))[0], d.WHITE, 0.3))
                _pips(img, 12 + 16, ty + 1, game["home_timeouts"], d.mix(d.team_colors(team_dict(home))[0], d.WHITE, 0.3))
            elif game["final"]:
                d.text(img, "FINAL", lw // 2, ty + 8, d.scale(d.GRAY, 1.15), 1, "center")
            _play_bar(ctx, img, 0, h - 10, w, 10, game, t)
        return img
    # narrow panels: stacked
    _header(img, 0, 0, w, game, t, animate, with_dd=True)
    if tall:
        row_h = 14
        _score_row(img, 0, 9, w, row_h, away, game["possession"] == "away", game["away_timeouts"], glow["away"], t, animate)
        _score_row(img, 0, 9 + row_h + 1, w, row_h, home, game["possession"] == "home", game["home_timeouts"], glow["home"], t, animate)
        if game["live"]:
            _field(img, 0, 40, w, 13, game, t, animate)
        else:
            _recap_panel(img, 0, 40, w, 13, game, t, animate)
        _play_bar(ctx, img, 0, h - 10, w, 10, game, t)
    else:
        half = w // 2
        _score_row(img, 0, 8, half - 1, 10, away, game["possession"] == "away", game["away_timeouts"], glow["away"], t, animate)
        _score_row(img, half, 8, w - half, 10, home, game["possession"] == "home", game["home_timeouts"], glow["home"], t, animate)
        if game["live"]:
            _field(img, 0, 19, w, h - 19, game, t, animate)
        else:
            _recap_panel(img, 0, 19, w, h - 19, game, t, animate)
    return img


# ----------------------------------------------------------------------
# screen 2: the player card
# ----------------------------------------------------------------------

def _card_values(ctx: RenderContext, item: Dict[str, Any], t: float):
    p = item["player"]
    k = ease((t - 0.2) / 1.2) if ctx.animate else 1.0
    shown = str(int(round(p["yards"] * k)))
    color = d.TIER_COLORS[p["tier"]]
    progress = max(0.0, min(1.0, (t - 1.3) / 1.2)) if ctx.animate else 1.0
    return p, shown, color, progress


def _shimmer(ctx: RenderContext, img: Image.Image, w: int, h: int, tier: str, t: float) -> None:
    if tier == "legendary":
        pos = ((t * 55) % (w + 120)) - 40 if ctx.animate else w * 0.55
        d.shimmer(img, 0, 0, w, h, pos, 5, 0.35 if ctx.animate else 0.22)


def _extra_parts(p: Dict[str, Any]):
    """The stat line without the yardage, which the card shows big."""
    return [(n, lab) for n, lab in p["parts"] if lab != "YD"]


def _target_text(p: Dict[str, Any]) -> str:
    """What the XP bar is filling toward."""
    names = {"common": "RARE", "rare": "EPIC", "epic": "LEGEND"}
    return "NEXT " + names[p["tier"]] if p["tier"] in names else "MAX RARITY"


def draw_parts(img: Image.Image, parts: Sequence[Tuple[str, str]], x: int, y: int, max_w: int,
               num_color: RGB = d.WHITE, label_color: RGB = d.GRAY) -> int:
    """``2 TD 1 INT`` -- numbers bright, labels dim, whole parts only."""
    cursor, first = x, True
    for number, label in parts:
        piece = font.text_width(number) + 2 + font.text_width(label)
        gap = 0 if first else 4
        if cursor + gap + piece - x > max_w:
            break
        cursor += gap
        cursor += d.text(img, number, cursor, y, num_color) + 2
        cursor += d.text(img, label, cursor, y, label_color)
        first = False
    return cursor - x


def card(ctx: RenderContext, item: Dict[str, Any], w: int, h: int, t: float) -> Image.Image:
    """``item``: ``{"game", "player"}``."""
    game = item["game"]
    p, shown, color, progress = _card_values(ctx, item, t)
    img = Image.new("RGB", (w, h), d.BLACK)
    narrow = w < 96
    tall = h >= 64
    d.frame(img, 0, 0, w, h, d.scale(color, 0.9))
    opp = game["away" if side_of(game, p["team"]) is game["home"] else "home"]["abbr"]
    clock = "HALF" if game["period_text"] == "HALF" else f"{game['period_text']} {game['clock']}".strip()
    tier_name = d.TIER_NAMES[p["tier"]]
    cat = model.CATEGORY_LABELS.get(p["category"], "")
    if tall:
        # banner
        d.hgrad(img, 1, 1, w - 2, 7, d.mix(color, d.WHITE, 0.2), d.scale(color, 0.5))
        d.text(img, tier_name, 3, 2, d.INK)
        if not narrow and font.text_width(tier_name) + font.text_width(cat) + 10 <= w:
            d.text(img, cat, w - 3, 2, d.INK, 1, "right")
        pw = 41 if w >= 120 else 32 if not narrow else 24
        ph = 55 if not narrow else 33
        ctx.picture(img, p, game, 1, 8, pw, ph)
        d.rect(img, pw + 1, 8, 1, ph, d.scale(color, 0.35))
        x0, avail = pw + 4, w - pw - 7
        if narrow:
            x0, avail = pw + 3, w - pw - 5
        ctx.label(img, p["last"], x0, 10 if not narrow else 11, avail, d.WHITE, t)
        tag_w = d.pos_tag(img, x0, 17 if not narrow else 20, p["pos"] or "?")
        matchup = f"{p['team']} VS {opp}"
        if font.text_width(matchup) > avail - tag_w - 3:
            matchup = p["team"]
        d.text(img, matchup, x0 + tag_w + 3, (18 if not narrow else 21), d.GRAY)
        s3 = 3 if font.text_width(shown, 3) + 4 + font.text_width("YDS") <= avail else 2
        yy = 26 if not narrow else 29
        num_w = d.big_number(img, shown, x0, yy if s3 == 3 else yy + 2, color, s3)
        unit = "YDS" if x0 + num_w + 4 + font.text_width("YDS") <= w - 2 else "YD"
        d.text(img, unit, x0 + num_w + 4, yy + (10 if s3 == 3 else 7), d.scale(color, 0.75))
        parts = _extra_parts(p)
        if narrow:
            compact = " ".join(f"{n} {lab}" for n, lab in parts[:2])
            d.text(img, font.fit(compact, w - 4), w // 2, 47, d.WHITE, 1, "center")
            d.xp_bar(img, 3, 55, w - 6, 3, model.next_threshold(p["category"], p["value"]), p["value"], progress)
        else:
            draw_parts(img, parts, x0, 44, avail)
            d.xp_bar(img, x0, 51, avail, 4, model.next_threshold(p["category"], p["value"]), p["value"], progress)
            d.text(img, font.fit(_target_text(p), avail - font.text_width(clock) - 4), x0, 57, d.GRAY)
            d.text(img, clock, w - 3, 57, d.GOLD, 1, "right")
    elif not narrow:
        # 32-row, >=96 wide: matches Fantasy Blitz's _card_short -- no top
        # banner, so the picture runs almost the full card height (30/32 px,
        # not squeezed under a 7px band).
        pw = 26
        ctx.picture(img, p, game, 1, 1, pw, 30)
        x0, avail = pw + 4, w - pw - 6
        label = tier_name
        if font.text_width(p["last"]) + 6 + font.text_width(label) > avail:
            label = ""
        ctx.label(img, p["last"], x0, 2, avail - (font.text_width(label) + 6 if label else 0), d.WHITE, t)
        if label:
            d.text(img, label, w - 3, 2, color, 1, "right")
        s2 = 2 if font.text_width(shown, 2) + 3 + font.text_width("YDS") <= avail else 1
        num_w = d.big_number(img, shown, x0, 8, color, s2)
        unit = "YDS" if s2 == 2 else "YD"
        d.text(img, unit, x0 + num_w + 3, 13, d.scale(color, 0.75))
        tag_w = font.text_width(p["pos"] or "?") + 4 + 2 + font.text_width(p["team"])
        if x0 + num_w + 3 + font.text_width(unit) + 6 + tag_w <= w - 3:
            tx = w - 3 - tag_w
            tw = d.pos_tag(img, tx, 9, p["pos"] or "?")
            d.text(img, p["team"], tx + tw + 2, 10, d.GRAY)
        parts = _extra_parts(p)
        if parts:
            draw_parts(img, parts, x0, 20, avail)
        d.xp_bar(img, x0, 27, avail, 3, model.next_threshold(p["category"], p["value"]), p["value"], progress)
    else:
        # 32-row, <96 wide: matches Fantasy Blitz's _card_short_narrow.
        ctx.label(img, p["last"], w // 2, 1, w - 2, d.WHITE, t, "center")
        d.rect(img, 0, 7, w, 1, color)
        ctx.picture(img, p, game, 0, 8, 19, 24)
        x0, avail = 22, w - 23
        s2 = 2 if font.text_width(shown, 2) + 3 + font.text_width("YD") <= avail else 1
        num_w = d.big_number(img, shown, x0, 9, color, s2)
        if s2 == 2:
            d.text(img, "YD", x0 + num_w + 3, 14, d.scale(color, 0.75))
        compact = " ".join(f"{n} {lab}" for n, lab in _extra_parts(p)[:1])
        if compact:
            d.text(img, font.fit(compact, avail), x0, 21, d.WHITE)
        d.xp_bar(img, x0, 28, avail, 3, model.next_threshold(p["category"], p["value"]), p["value"], progress)
    _shimmer(ctx, img, w, h, p["tier"], t)
    return img


# ----------------------------------------------------------------------
# screen 3: the score burst
# ----------------------------------------------------------------------

def alert(ctx: RenderContext, item: Dict[str, Any], w: int, h: int, t: float) -> Image.Image:
    """``item``: a queued alert from :func:`model.build_alert` plus ``game``."""
    img = Image.new("RGB", (w, h), d.BLACK)
    game = item["game"]
    side = game[item["side"]]
    team = team_dict(side)
    primary, accent = d.team_colors(team)
    tt = t if ctx.animate else 2.3
    tall = h >= 64
    title = item["kind"] + "!"
    scorer = item.get("player")
    pic_w = 0
    if w >= 192 or (not tall and w >= 96) or (tall and w >= 96):
        pic_w = 44 if tall else 26
    if w < 96 and not tall:
        pic_w = 0
    slide = ease(tt / 0.5) if ctx.animate else 1.0
    d.rays(img, pic_w, 0, w - pic_w, h, pic_w + (w - pic_w) / 2, h * (0.34 if tall else 0.45),
           tt * 0.45 if ctx.animate else 0.3, accent, primary)
    if item["kind"] == "TOUCHDOWN":
        d.confetti(img, pic_w, 0, w - pic_w, h - 11 if tall else h, tt, [accent, d.WHITE, d.GOLD, (255, 90, 160)])
    if pic_w:
        px = int(round(-pic_w + (pic_w + 1) * slide))
        if scorer:
            ctx.picture(img, scorer, game, px, 1, pic_w - 2, h - (12 if tall else 2))
        else:
            box = Image.new("RGB", (pic_w - 2, h - (12 if tall else 2)), d.BLACK)
            d.vgrad(box, 0, 0, box.width, box.height, d.scale(primary, 0.5), (6, 7, 10))
            logo = ctx.logo(side["abbr"], box.width - 4, box.height - 4)
            if logo is not None:
                box.paste(logo, ((box.width - logo.width) // 2, (box.height - logo.height) // 2), logo)
            else:
                d.text(box, side["abbr"], box.width // 2, box.height // 2 - 2, d.WHITE, 1, "center")
            img.paste(box, (px, 1))
    cw = w - pic_w
    cx = pic_w + cw // 2
    pulse = 0.86 + 0.14 * math.sin(t * 2 * math.pi / 1.7) if ctx.animate else 1.0
    other = game["away" if item["side"] == "home" else "home"]
    score_line = f"{side['score']}-{other['score']}"
    name = scorer["last"] if scorer else side["abbr"]
    desc = item.get("text") or f"{side['abbr']} +{item['points']}"
    gold = lambda r, hh: d.mix(d.GOLD_HI, d.GOLD, r / max(1, hh - 1))  # noqa: E731
    if tall:
        ts = 2 if font.text_width(title, 2) <= cw - 4 else 1
        d.text(img, title, cx, 3, gold, ts, "center", d.BLACK)
        d.text(img, side["abbr"], cx, 15, d.scale(accent, pulse), 3 if cw >= 80 else 2, "center", d.BLACK)
        matchup = f"{side['abbr']} {side['score']}  {other['abbr']} {other['score']}"
        d.text(img, font.fit(matchup, cw - 4), cx, 33, d.WHITE, 1, "center", d.BLACK)
        ctx.label(img, name, cx, 40, cw - 4, d.WHITE, t, "center", d.BLACK)
        ctx.label(img, desc, cx, 47, cw - 4, accent, t, "center", d.BLACK)
        d.rect(img, 0, 53, w, 11, (6, 8, 12))
        d.hline(img, 0, 53, w, (30, 36, 50))
        d.dot(img, 4, 58, d.RED)
        d.text(img, "LIVE", 8, 56, d.RED)
        clock = f"{game['period_text']} {game['clock']}".strip()
        d.text(img, clock, w - 3, 56, d.GRAY, 1, "right")
        return img
    if cw >= 96:
        tx = pic_w + 3
        d.text(img, title, tx, 1, d.GOLD, 1, "left", d.BLACK)
        gw = d.text(img, side["abbr"], tx, 8, d.scale(accent, pulse), 2, "left", d.BLACK)
        d.big_number(img, score_line, tx + gw + 5, 8, d.WHITE, 2)
        ctx.label(img, name, tx, 20, cw - 6, d.WHITE, t, "left", d.BLACK)
        ctx.label(img, desc, tx, 26, cw - 6, accent, t, "left", d.BLACK)
        return img
    d.text(img, font.fit(title, cw - 2), cx, 1, d.GOLD, 1, "center", d.BLACK)
    ab_w = font.text_width(side["abbr"]) + 3
    sc_w = font.text_width(score_line, 2)
    sx = cx - (ab_w + sc_w) // 2
    d.text(img, side["abbr"], sx, 11, d.scale(accent, pulse), 1, "left", d.BLACK)
    d.big_number(img, score_line, sx + ab_w, 8, d.WHITE, 2)
    ctx.label(img, name, cx, 20, cw - 2, d.WHITE, t, "center", d.BLACK)
    ctx.label(img, desc, cx, 26, cw - 2, accent, t, "center", d.BLACK)
    return img


# ----------------------------------------------------------------------
# screen: upcoming game preview
# ----------------------------------------------------------------------

def preview(ctx: RenderContext, item: Dict[str, Any], w: int, h: int, t: float) -> Image.Image:
    """``item``: ``{"game"}`` -- a game whose kickoff hasn't happened yet."""
    game = item["game"]
    img = Image.new("RGB", (w, h), d.BLACK)
    away, home = game["away"], game["home"]
    a_color, a_accent = d.team_colors(team_dict(away))
    h_color, h_accent = d.team_colors(team_dict(home))
    kickoff = model.kickoff_text(game.get("start"), time.time())
    tall = h >= 64
    narrow = w < 96
    half = w // 2
    band_h = 9 if tall else 8

    def side_block(x0, x1, side, color, accent, outer_left):
        cw = x1 - x0
        d.vgrad(img, x0, 0, cw, h, d.scale(color, 0.22), (8, 9, 13))
        d.rect(img, x0, 0, cw, 3, color)
        abbr_scale = 3 if cw >= 46 and h >= 40 else 2 if cw >= 30 else 1
        ty = h // 2 - font.text_height(abbr_scale) // 2
        if h >= 40:     # tall: the crest sits above the letters
            d.big_number(img, side["abbr"], x0 + cw // 2, ty, accent, abbr_scale, "center", shadow=False)
            logo = ctx.logo(side["abbr"], min(cw - 4, 22), 16)
            if logo is not None:
                img.paste(logo, (x0 + (cw - logo.width) // 2, 4), logo)
        else:           # short: crest on the outer edge, letters toward the middle, never on top of each other
            side_h = h - band_h - 3
            logo = ctx.logo(side["abbr"], min(side_h - 2, 22), side_h - 2)
            tw = font.text_width(side["abbr"], abbr_scale)
            if logo is None:
                d.big_number(img, side["abbr"], x0 + cw // 2, ty, accent, abbr_scale, "center", shadow=False)
            else:
                gap = 4
                group = logo.width + gap + tw
                gx = x0 + max(2, (cw - group) // 2)
                ly = 3 + (side_h - logo.height) // 2
                if outer_left:
                    img.paste(logo, (gx, ly), logo)
                    d.big_number(img, side["abbr"], gx + logo.width + gap, ty, accent, abbr_scale, "left", shadow=False)
                else:
                    d.big_number(img, side["abbr"], gx, ty, accent, abbr_scale, "left", shadow=False)
                    img.paste(logo, (gx + tw + gap, ly), logo)

    side_block(0, half, away, a_color, a_accent, True)
    side_block(half, w, home, h_color, h_accent, False)
    at_text = "AT" if w >= 64 else "-"
    at_w = font.text_width(at_text) + 4
    d.rect(img, half - at_w // 2, h // 2 - 4, at_w, 8, d.BLACK)
    d.text(img, at_text, half, h // 2 - 2, d.GOLD, 1, "center")

    d.rect(img, 0, h - band_h, w, band_h, (10, 11, 16))
    d.hline(img, 0, h - band_h, w, (34, 38, 52))
    label = kickoff if font.text_width(kickoff) <= w - 4 else font.fit(kickoff, w - 4)
    d.text(img, label, w // 2, h - band_h + max(0, (band_h - 5) // 2), d.WHITE, 1, "center")
    if not narrow and tall:
        d.text(img, "UPCOMING", w // 2, 2, d.scale(d.GOLD, 0.85), 1, "center")
    return img
