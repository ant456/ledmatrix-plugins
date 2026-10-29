"""The NHL Live Ice screens, arcade style.

Three screens (plus an upcoming-game preview), each drawn as a *short* (32
rows) or *tall* (64 rows) design that adapts to narrow (under 96 px), normal
(96-191) and wide (192 and up, where the scoreboard sits beside a player card)
panels. Panels with spare rows get a title band and a status bar, and very
large panels draw the 128x64 design at 2x, exactly as the Fantasy Blitz plugin
does.

* ``scoreboard`` -- score rows in team colours and a period-by-period score
  table on the left, an arena scoreboard on the right (LED-dot clock and
  period, shots on goal, a PP lamp and your logo), and a flame on a scoring run.
* ``card``   -- a game leader as a collectible card: ESPN headshot, rarity
  frame (gold / purple / blue), count-up stat, the rest of the stat line, a
  hat-trick badge and an XP bar that fills toward the next rarity.
* ``alert``  -- the burst for a goal: spinning sunburst, confetti, the new score
  and what the goal did (go-ahead, equalizer, overtime winner).

Renderers take ``t``, seconds since the item came on screen, for the intro
animations (count-up, bar fill, slide-in) and the continuous ones (foil
shimmer, sunburst, confetti, the PP lamp). With animation off they draw the
final frame.
"""

import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_sin, _pi = math.sin, math.pi

from PIL import Image

import nhllive_board as board
import nhllive_draw as d
import nhllive_font as font
import nhllive_model as model

RGB = Tuple[int, int, int]



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
                 status: str = "", logo_path: str = ""):
        self.headshots = headshots
        #: A PNG for the scoreboard's logo box ('' = the NHL shield).
        self.logo_path = logo_path
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

    def board_logo(self, w: int, h: int) -> Optional[Image.Image]:
        """The scoreboard's logo scaled to fit ``w`` x ``h``: yours if set, else the NHL shield."""
        if self.headshots is None:
            return None
        try:
            if self.logo_path:
                own = self.headshots.file_logo(self.logo_path, w, h)
                if own is not None:
                    return own
            return self.headshots.logo("NHL", w, h)
        except Exception:  # noqa: BLE001 - art is never worth a crash
            return None

    def picture(self, img: Image.Image, player: Dict[str, Any], game: Dict[str, Any],
                x: int, y: int, w: int, h: int) -> None:
        """The player's picture box: photo, else a club-colour silhouette with the jersey number."""
        side = side_of(game, player.get("team", ""))
        d.portrait(img, x, y, w, h, team_dict(side), player.get("jersey", ""), self.photo(player, w, h))


# ----------------------------------------------------------------------
# small shared pieces
# ----------------------------------------------------------------------

def _lead_text(game: Dict[str, Any]) -> str:
    side, pts = model.lead(game)
    return "TIED" if side is None else f"{game[side]['abbr']} +{pts}"


def _header(img: Image.Image, x: int, y: int, w: int, game: Dict[str, Any], t: float,
            animate: bool, with_lead: bool = True) -> None:
    """The 7-row band: a status light (blinking for live, solid for final) plus
    the period and clock and, room permitting, who leads (or CLUTCH)."""
    d.hgrad(img, x, y, w, 7, (30, 34, 46), (12, 14, 20))
    live = game["live"]
    on = (not live) or (not animate) or int(t * 1.4) % 2 == 0
    tag_color = d.RED if live else d.scale(d.GRAY, 1.15)
    d.dot(img, x + 3, y + 3, tag_color if on else d.scale(tag_color, 0.35))
    used = 7
    if w >= 56:
        used += d.text(img, "LIVE" if live else "FINAL", x + 7, y + 1, tag_color) + 3
    ending = game.get("ending", "")
    if live:
        clock = model.clock_text(game)
    else:
        clock = ending if w >= 56 else ("FINAL" + (" " + ending if ending else ""))
    cw = font.text_width(clock)
    right = ""
    if with_lead and live:
        right = "CLUTCH" if game.get("clutch") else _lead_text(game)
    right_w = font.text_width(right) + 4 if right else 0
    if used + cw + right_w + 4 <= w:
        d.text(img, clock, x + used + max(0, (w - used - right_w - cw) // 2), y + 1, d.GOLD)
        if right:
            flash = game.get("clutch") and animate and int(t * 2.4) % 2 == 1
            d.text(img, right, x + w - 2, y + 1, d.RED if game.get("clutch") and not flash else d.WHITE, 1, "right")
    else:
        d.text(img, font.fit(clock, w - used - 2), x + used, y + 1, d.GOLD)


def _score_row(img: Image.Image, x: int, y: int, w: int, h: int, side: Dict[str, Any],
               hot: bool, dim: bool, glow: float, t: float, animate: bool) -> None:
    primary, accent = d.team_colors(team_dict(side))
    lit = max(0.0, min(1.0, glow))
    k = 0.6 if dim else 1.0
    d.hgrad(img, x, y, w, h, d.mix(d.scale(primary, 0.42 * k), d.WHITE, 0.45 * lit),
            d.mix(d.scale(primary, 0.16 * k), primary, 0.5 * lit))
    d.rect(img, x, y, 2, h, d.scale(primary, k))
    big = h >= 11
    ax = x + 5
    label_scale = 2 if (h >= 12 and w >= 46) else 1
    ay = y + max(0, (h - font.text_height(label_scale)) // 2)
    text_color = d.scale(d.WHITE, 0.7) if dim else d.WHITE
    d.text(img, side["abbr"], ax, ay, text_color, label_scale, "left", d.BLACK if lit > 0.4 else None)
    ax += font.text_width(side["abbr"], label_scale) + 2
    score = str(side["score"])
    s_scale = 2 if big and font.text_width(score, 2) + ax + 8 - x <= w else 1
    if hot and h >= 8 and ax + 5 + font.text_width(score, s_scale) + 4 <= x + w:
        d.flame(img, ax, y + max(0, (h - 7) // 2), flicker=animate and int(t * 6) % 2 == 0)
    sy = y + max(0, (h - font.text_height(s_scale)) // 2)
    if s_scale == 2 and h >= 12:
        d.big_number(img, score, x + w - 2, sy, text_color, 2, "right")
    else:
        d.text(img, score, x + w - 2, sy, text_color, s_scale, "right")


def _period_label(idx: int, n: int, game: Dict[str, Any]) -> str:
    """Column heading for period ``idx`` (0-based): 1 2 3 OT O2.. or SO for a shootout's last column."""
    if idx < 3:
        return str(idx + 1)
    if game.get("shootout") and idx == n - 1:
        return "SO"
    return "OT" if idx == 3 else f"O{idx - 2}"


def _linescore(img: Image.Image, x: int, y: int, w: int, game: Dict[str, Any], rows: int = 2) -> None:
    """Goals per period: a header (1 2 3 / OT / SO) and one row per team."""
    away, home = game["away"], game["home"]
    n = max(len(away["linescores"]), len(home["linescores"]), 0)
    if n == 0:
        d.text(img, "NO SCORES" if w >= 46 else "-", x + w // 2, y + 8, d.DIM, 1, "center")
        return
    cols = max(1, min(n, w // 11))
    first = n - cols  # show the latest columns when there are overtimes
    col_w = w // cols
    for c in range(cols):
        idx = first + c
        cx = x + c * col_w + col_w // 2
        label = _period_label(idx, n, game)
        current = game["live"] and idx == n - 1
        d.text(img, label, cx, y, d.GOLD if current else d.GRAY, 1, "center")
        for r_, side in enumerate((away, home)):
            val = side["linescores"][idx] if idx < len(side["linescores"]) else None
            d.text(img, "-" if val is None else str(val), cx, y + 7 + 6 * r_,
                   d.WHITE if current else d.scale(d.WHITE, 0.72), 1, "center")


def _recap_panel(img: Image.Image, x: int, y: int, w: int, h: int, game: Dict[str, Any],
                 t: float, animate: bool) -> None:
    """Where the scoreboard would be on a finished game: an animated stat ticker of
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
        unit_w = font.text_width(p["unit"]) + 3
        # the position tag goes only if the whole surname still fits beside it
        tag_cost = font.text_width(p["pos"] or "?") + 4 + 2
        room = avail - (tx - x) - font.text_width(str(int(p["value"]))) - 3
        if rh >= 9 and avail - (tx - x) > 30 and font.text_width(p["last"]) + tag_cost <= room - unit_w:
            tag_w = d.pos_tag(img, tx, ry + max(0, (rh - 7) // 2), p["pos"] or "?")
            name_x = tx + tag_w + 2
        else:
            name_x = tx
        text_color = d.mix(d.BLACK, d.WHITE, min(1.0, k * 1.3))
        name_w = max(6, avail - (name_x - x) - 12 - unit_w)
        d.text(img, font.fit(p["last"], name_w), name_x, ry + max(0, (rh - 5) // 2), text_color)
        shown = str(int(round(p["value"] * min(1.0, k))))
        right_x = x + w - 2
        if avail - (name_x - x) - font.text_width(shown) - unit_w > font.text_width(font.fit(p["last"], name_w)) - 4:
            d.text(img, p["unit"], right_x, ry + max(0, (rh - 5) // 2), d.scale(color, 0.45 + 0.35 * k), 1, "right")
            right_x -= unit_w
        d.text(img, shown, right_x, ry + max(0, (rh - 5) // 2), d.scale(color, 0.5 + 0.5 * k), 1, "right")


def _play_bar(ctx: "RenderContext", img: Image.Image, x: int, y: int, w: int, h: int,
              game: Dict[str, Any], t: float) -> None:
    d.rect(img, x, y, w, h, (6, 8, 12))
    d.hline(img, x, y, w, (30, 36, 50))
    fire = model.run_on_fire(game)
    if game.get("last_play"):
        text = game["last_play"]
    elif fire and game["live"]:
        text = f"{game[fire]['abbr']} ON A {game['run']['points']}-0 RUN"
    elif game["live"] and game.get("power_play"):
        text = "POWER PLAY"
    else:
        text = game.get("odds") or game.get("detail") or ""
    ctx.label(img, text, x + 3, y + max(1, (h - 5) // 2 + 1), w - 6, d.GRAY, t)


def _glow_for(item: Dict[str, Any], t: float) -> Dict[str, float]:
    """Row-flash for a side that just scored a burst (item['celebrate'] = (side, started_offset))."""
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
        d.text(base, ctx.status or "NHL LIVE", 9, head + design_h + max(0, (foot - 5) // 2), d.GRAY)
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
# screen 1: the scoreboard
# ----------------------------------------------------------------------

def _fire_side(game: Dict[str, Any]) -> Optional[str]:
    return model.run_on_fire(game) if game["live"] else None


def scoreboard(ctx: RenderContext, item: Dict[str, Any], w: int, h: int, t: float) -> Image.Image:
    """``item``: ``{"game", "star"?, "celebrate"?}``."""
    game = item["game"]
    if w >= 192 and item.get("star"):
        return side_by_side(ctx, scoreboard, dict(item, star=None), card,
                            {"game": game, "player": item["star"]}, w, h, t, 128)
    img = Image.new("RGB", (w, h), d.BLACK)
    glow = _glow_for(item, t)
    animate = ctx.animate
    tall = h >= 64
    away, home = game["away"], game["home"]
    fire = _fire_side(game)
    loser = None
    if game["final"] and away["score"] != home["score"]:
        loser = "away" if away["score"] < home["score"] else "home"
    if w >= 96:
        lw = 56 if w < 150 else 68
        _header(img, 0, 0, w, game, t, animate, with_lead=True)
        row_h = 12 if tall else 11
        y0 = 9 if tall else 8
        _score_row(img, 0, y0, lw, row_h, away, fire == "away", loser == "away", glow["away"], t, animate)
        _score_row(img, 0, y0 + row_h + 1, lw, row_h, home, fire == "home", loser == "home", glow["home"], t, animate)
        fy = y0
        fh = (h - 11 - fy) if tall else (h - fy)
        if game["live"]:
            board.board(ctx, img, lw + 2, fy, w - lw - 2, fh, game, t, animate)
        else:
            _recap_panel(img, lw + 2, fy, w - lw - 2, fh, game, t, animate)
        if tall:
            ty = y0 + 2 * row_h + 3
            _linescore(img, 1, ty, lw - 2, game)
            _play_bar(ctx, img, 0, h - 10, w, 10, game, t)
        return img
    # narrow panels: stacked
    _header(img, 0, 0, w, game, t, animate, with_lead=True)
    if tall:
        row_h = 14
        _score_row(img, 0, 9, w, row_h, away, fire == "away", loser == "away", glow["away"], t, animate)
        _score_row(img, 0, 9 + row_h + 1, w, row_h, home, fire == "home", loser == "home", glow["home"], t, animate)
        if game["live"]:
            board.board(ctx, img, 0, 40, w, 13, game, t, animate)
        else:
            _recap_panel(img, 0, 40, w, 13, game, t, animate)
        _play_bar(ctx, img, 0, h - 10, w, 10, game, t)
    else:
        half = w // 2
        _score_row(img, 0, 8, half - 1, 10, away, fire == "away", loser == "away", glow["away"], t, animate)
        _score_row(img, half, 8, w - half, 10, home, fire == "home", loser == "home", glow["home"], t, animate)
        if game["live"]:
            board.board(ctx, img, 0, 19, w, h - 19, game, t, animate)
        else:
            _recap_panel(img, 0, 19, w, h - 19, game, t, animate)
    return img


# ----------------------------------------------------------------------
# screen 2: the player card
# ----------------------------------------------------------------------

def _card_values(ctx: RenderContext, item: Dict[str, Any], t: float):
    p = item["player"]
    k = ease((t - 0.2) / 1.2) if ctx.animate else 1.0
    shown = str(int(round(p["value"] * k)))
    color = d.TIER_COLORS[p["tier"]]
    progress = max(0.0, min(1.0, (t - 1.3) / 1.2)) if ctx.animate else 1.0
    return p, shown, color, progress


def _shimmer(ctx: RenderContext, img: Image.Image, w: int, h: int, tier: str, t: float) -> None:
    if tier == "legendary":
        pos = ((t * 55) % (w + 120)) - 40 if ctx.animate else w * 0.55
        d.shimmer(img, 0, 0, w, h, pos, 5, 0.35 if ctx.animate else 0.22)


def _target_text(p: Dict[str, Any]) -> str:
    """What the XP bar is filling toward."""
    names = {"common": "RARE", "rare": "EPIC", "epic": "LEGEND"}
    return "NEXT " + names[p["tier"]] if p["tier"] in names else "MAX RARITY"


def _bottom_note(p: Dict[str, Any]) -> str:
    """The line under the XP bar: a double/triple-double badge, else the next rarity."""
    return p.get("badge") or _target_text(p)


def draw_parts(img: Image.Image, parts: Sequence[Tuple[str, str]], x: int, y: int, max_w: int,
               num_color: RGB = d.WHITE, label_color: RGB = d.GRAY) -> int:
    """``8 REB 5 AST`` -- numbers bright, labels dim, whole parts only."""
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
    clock = model.clock_text(game)
    tier_name = d.TIER_NAMES[p["tier"]]
    cat = model.CATEGORY_LABELS.get(p["category"], "")
    unit = p["unit"]
    parts = p["parts"]
    target = model.next_threshold(p["category"], p["value"])
    if tall:
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
        s3 = 3 if font.text_width(shown, 3) + 4 + font.text_width(unit) <= avail else 2
        yy = 26 if not narrow else 29
        num_w = d.big_number(img, shown, x0, yy if s3 == 3 else yy + 2, color, s3)
        if x0 + num_w + 4 + font.text_width(unit) <= w - 2:
            d.text(img, unit, x0 + num_w + 4, yy + (10 if s3 == 3 else 7), d.scale(color, 0.75))
        if narrow:
            compact = " ".join(f"{n} {lab}" for n, lab in parts[:2])
            d.text(img, font.fit(compact, w - 4), w // 2, 47, d.WHITE, 1, "center")
            d.xp_bar(img, 3, 55, w - 6, 3, target, p["value"], progress)
        else:
            draw_parts(img, parts, x0, 44, avail)
            d.xp_bar(img, x0, 51, avail, 4, target, p["value"], progress)
            d.text(img, font.fit(_bottom_note(p), avail - font.text_width(clock) - 4), x0, 57,
                   d.GOLD if p.get("badge") else d.GRAY)
            d.text(img, clock, w - 3, 57, d.GOLD, 1, "right")
    elif not narrow:
        # 32-row, >=96 wide: no top banner, so the picture runs almost the full card height.
        pw = 26
        ctx.picture(img, p, game, 1, 1, pw, 30)
        x0, avail = pw + 4, w - pw - 6
        label = tier_name
        if font.text_width(p["last"]) + 6 + font.text_width(label) > avail:
            label = ""
        ctx.label(img, p["last"], x0, 2, avail - (font.text_width(label) + 6 if label else 0), d.WHITE, t)
        if label:
            d.text(img, label, w - 3, 2, color, 1, "right")
        s2 = 2 if font.text_width(shown, 2) + 3 + font.text_width(unit) <= avail else 1
        num_w = d.big_number(img, shown, x0, 8, color, s2)
        d.text(img, unit, x0 + num_w + 3, 13 if s2 == 2 else 9, d.scale(color, 0.75))
        tag_w = font.text_width(p["pos"] or "?") + 4 + 2 + font.text_width(p["team"])
        if x0 + num_w + 3 + font.text_width(unit) + 6 + tag_w <= w - 3:
            tx = w - 3 - tag_w
            tw = d.pos_tag(img, tx, 9, p["pos"] or "?")
            d.text(img, p["team"], tx + tw + 2, 10, d.GRAY)
        line = list(parts)
        if p.get("badge") and not line:
            d.text(img, font.fit(p["badge"], avail), x0, 20, d.GOLD)
        elif line:
            used = draw_parts(img, line, x0, 20, avail)
            short = model.BADGE_SHORT.get(p.get("badge"), "")
            if short and avail - used > font.text_width(short) + 6:
                d.text(img, short, x0 + avail, 20, d.GOLD, 1, "right")
        d.xp_bar(img, x0, 27, avail, 3, target, p["value"], progress)
    else:
        # 32-row, <96 wide.
        ctx.label(img, p["last"], w // 2, 1, w - 2, d.WHITE, t, "center")
        d.rect(img, 0, 7, w, 1, color)
        ctx.picture(img, p, game, 0, 8, 19, 24)
        x0, avail = 22, w - 23
        s2 = 2 if font.text_width(shown, 2) + 3 + font.text_width(unit) <= avail else 1
        num_w = d.big_number(img, shown, x0, 9, color, s2)
        if s2 == 2:
            d.text(img, unit, x0 + num_w + 3, 14, d.scale(color, 0.75))
        compact = " ".join(f"{n} {lab}" for n, lab in parts[:1])
        if compact:
            d.text(img, font.fit(compact, avail), x0, 21, d.WHITE)
        d.xp_bar(img, x0, 28, avail, 3, target, p["value"], progress)
    _shimmer(ctx, img, w, h, p["tier"], t)
    return img


# ----------------------------------------------------------------------
# screen 3: the burst
# ----------------------------------------------------------------------

def alert(ctx: RenderContext, item: Dict[str, Any], w: int, h: int, t: float) -> Image.Image:
    """``item``: a queued alert from :func:`model.detect_alert` plus ``game``."""
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
    if w >= 96:
        pic_w = 44 if tall else 26
    slide = ease(tt / 0.5) if ctx.animate else 1.0
    d.rays(img, pic_w, 0, w - pic_w, h, pic_w + (w - pic_w) / 2, h * (0.34 if tall else 0.45),
           tt * 0.45 if ctx.animate else 0.3, accent, primary)
    d.confetti(img, pic_w, 0, w - pic_w, h - 11 if tall else h, tt, [accent, d.WHITE, d.GOLD, (255, 140, 40)])
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
    desc = item.get("desc") or item.get("text") or f"{side['abbr']} +{item['points']}"
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
        clock = model.clock_text(game)
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
    """``item``: ``{"game"}`` -- a game whose puck hasn't dropped yet."""
    game = item["game"]
    img = Image.new("RGB", (w, h), d.BLACK)
    away, home = game["away"], game["home"]
    a_color, a_accent = d.team_colors(team_dict(away))
    h_color, h_accent = d.team_colors(team_dict(home))
    tip = model.puckdrop_text(game.get("start"), time.time())
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
        if side.get("record") and h >= 40:
            d.text(img, side["record"], x0 + cw // 2, h - band_h - 7, d.scale(d.WHITE, 0.7), 1, "center")

    side_block(0, half, away, a_color, a_accent, True)
    side_block(half, w, home, h_color, h_accent, False)
    at_text = "AT" if w >= 64 else "-"
    at_w = font.text_width(at_text) + 4
    d.rect(img, half - at_w // 2, h // 2 - 4, at_w, 8, d.BLACK)
    d.text(img, at_text, half, h // 2 - 2, d.GOLD, 1, "center")

    d.rect(img, 0, h - band_h, w, band_h, (10, 11, 16))
    d.hline(img, 0, h - band_h, w, (34, 38, 52))
    label = tip
    odds = game.get("odds") or ""
    if odds and font.text_width(f"{tip}  {odds}") <= w - 4:
        label = f"{tip}  {odds}"
    label = label if font.text_width(label) <= w - 4 else font.fit(label, w - 4)
    d.text(img, label, w // 2, h - band_h + max(0, (band_h - 5) // 2), d.WHITE, 1, "center")
    if not narrow and tall:
        d.text(img, "UPCOMING", w // 2, 2, d.scale(d.GOLD, 0.85), 1, "center")
    return img
