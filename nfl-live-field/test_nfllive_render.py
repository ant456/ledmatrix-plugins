"""Render safety and the arcade look: right size, no crash, nothing blank, and the animations move."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nfllive_demo as demo  # noqa: E402
import nfllive_draw as d  # noqa: E402
import nfllive_headshots as heads  # noqa: E402
import nfllive_model as model  # noqa: E402
import nfllive_render as render  # noqa: E402

SIZES = [(64, 32), (128, 32), (128, 64), (256, 32), (192, 64), (64, 64), (256, 128)]


def games(step=0):
    return {g["id"]: g for g in model.parse_scoreboard(demo.scoreboard(step))}


class FakeData:
    """Just enough of NflLiveData for a HeadshotStore: an in-memory keyed cache."""

    def __init__(self):
        self.store = {}

    def key(self, *parts):
        return ":".join(["t"] + [str(p) for p in parts])

    def read(self, key):
        return self.store.get(key)

    def write(self, key, data, now=None):
        self.store[key] = {"fetched_at": now or 0, "data": data}


def ctx(animate=True, photos=True):
    import logging
    store = None
    if photos:
        store = heads.HeadshotStore(FakeData(), logging.getLogger("t"))
        demo.seed_headshots(store, list(games().values()), now=1.0)
    return render.RenderContext(store, animate, True, "3 LIVE")


def colors(img):
    return len(img.getcolors(1 << 20))


ITEMS = {
    "field": lambda g: (render.field, {"game": g["t2"], "star": g["t2"]["stars"][0]}, "NFL LIVE"),
    "card": lambda g: (render.card, {"game": g["t2"], "player": g["t2"]["stars"][0]}, "TOP"),
    "alert": lambda g: (render.alert, dict(model.build_alert(games(0)["t2"], games(3)["t2"], "home", 0), game=games(3)["t2"]), "SCORE!"),
}


@pytest.mark.parametrize("kind", sorted(ITEMS))
@pytest.mark.parametrize("size", SIZES)
def test_every_screen_renders_at_exact_size_with_content(kind, size):
    fn, item, title = ITEMS[kind](games(3))
    for t in (0.0, 0.7, 2.5, 99.0):
        img = render.render_frame(fn, ctx(), item, size[0], size[1], t, title)
        assert img.size == size
        assert colors(img) > 8


@pytest.mark.parametrize("size", SIZES)
def test_screens_survive_missing_art_and_data(size):
    g = games()
    lean = dict(g["t1"], ball=None, first_down=None, possession=None, last_play="", possession_text="", down_distance="")
    render.render_frame(render.field, ctx(photos=False), {"game": lean}, *size, 1.0, "NFL LIVE")
    player = dict(g["t1"]["stars"][0], parts=[], jersey="", pos="")
    render.render_frame(render.card, ctx(photos=False), {"game": g["t1"], "player": player}, *size, 1.0, "TOP")
    render.render_frame(render.alert, ctx(photos=False), dict(model.build_alert(g["t1"], g["t1"], "home", 0), game=g["t1"], player=None), *size, 1.0, "SCORE!")


def test_animation_moves_and_stills_do_not():
    fn, item, title = ITEMS["field"](games(3))
    a = render.render_frame(fn, ctx(), item, 128, 64, 1.0, title)
    b = render.render_frame(fn, ctx(), item, 128, 64, 1.3, title)
    assert a.tobytes() != b.tobytes()  # chevrons march, the ball bobs
    still = ctx(animate=False)
    s1 = render.render_frame(fn, still, item, 128, 64, 1.0, title)
    s2 = render.render_frame(fn, still, item, 128, 64, 5.0, title)
    assert s1.tobytes() == s2.tobytes()


def test_card_counts_up_and_bar_fills():
    fn, item, title = ITEMS["card"](games(3))
    early = render.render_frame(fn, ctx(), item, 128, 64, 0.3, title)
    late = render.render_frame(fn, ctx(), item, 128, 64, 4.0, title)
    assert early.tobytes() != late.tobytes()


def test_card_frame_colour_is_the_rarity():
    g = games()
    legend = render.render_frame(render.card, ctx(animate=False), {"game": g["t2"], "player": g["t2"]["stars"][0]}, 128, 64)
    epic = render.render_frame(render.card, ctx(animate=False), {"game": g["t1"], "player": g["t1"]["stars"][0]}, 128, 64)
    assert legend.getpixel((0, 30)) == d.scale(d.GOLD, 0.9)
    assert epic.getpixel((0, 30)) == d.scale(d.EPIC, 0.9)


def test_photo_changes_the_card():
    g = games()
    item = {"game": g["t2"], "player": g["t2"]["stars"][0]}
    with_photo = render.render_frame(render.card, ctx(animate=False), item, 128, 64)
    silhouette = render.render_frame(render.card, ctx(animate=False, photos=False), item, 128, 64)
    assert with_photo.tobytes() != silhouette.tobytes()


def test_touchdown_has_confetti_but_a_field_goal_does_not():
    g = games(3)["t2"]
    base = dict(model.build_alert(games(0)["t2"], g, "home", 0), game=g)
    td = render.render_frame(render.alert, ctx(), base, 128, 64, 1.0, "SCORE!")
    fg = render.render_frame(render.alert, ctx(), dict(base, kind="FIELD GOAL"), 128, 64, 1.0, "SCORE!")
    assert td.tobytes() != fg.tobytes()


def test_score_row_glow_lights_the_scoring_side():
    g = games()["t2"]
    plain = render.render_frame(render.field, ctx(animate=False), {"game": g}, 128, 64, 99.0)
    lit = render.render_frame(render.field, ctx(), {"game": g, "celebrate": ("home", 0.0)}, 128, 64, 0.3)
    assert plain.tobytes() != lit.tobytes()


def test_very_dark_team_colour_is_lifted_visible():
    assert max(d.lift((0, 0, 0))) >= 150 and max(d.lift((2, 34, 68))) >= 150


def test_long_names_and_big_scores_still_fit():
    g = dict(games()["t2"])
    g["home"] = dict(g["home"], score=144)
    star = dict(g["stars"][0], last="Vanderbilt-Featherstonehaugh", yards=1234)
    for size in SIZES:
        render.render_frame(render.field, ctx(), {"game": g}, *size, 1.0)
        render.render_frame(render.card, ctx(), {"game": g, "player": star}, *size, 1.0)


# --- the recap panel (replaces the field on finished games) -----------------

def test_recap_panel_replaces_the_field_on_a_final_game():
    g = games()["t4"]
    assert g["final"] and g["stars"]
    live_frame = render.render_frame(render.field, ctx(animate=False), {"game": games()["t2"]}, 128, 32)
    final_frame = render.render_frame(render.field, ctx(animate=False), {"game": g}, 128, 32)
    assert live_frame.tobytes() != final_frame.tobytes()


def test_recap_panel_counts_up_and_cascades_between_rows():
    g = games()["t4"]
    early = render.render_frame(render.field, ctx(), {"game": g}, 128, 32, 0.1, "FINAL")
    later = render.render_frame(render.field, ctx(), {"game": g}, 128, 32, 3.0, "FINAL")
    assert early.tobytes() != later.tobytes()


@pytest.mark.parametrize("size", SIZES)
def test_recap_panel_renders_at_every_size_with_no_stars(size):
    g = dict(games()["t4"], stars=[])
    render.render_frame(render.field, ctx(animate=False), {"game": g}, *size)


@pytest.mark.parametrize("size", SIZES)
def test_recap_panel_renders_at_every_size_with_stars(size):
    g = games()["t4"]
    img = render.render_frame(render.field, ctx(), {"game": g}, *size, 2.0, "FINAL")
    assert img.size == size


def test_prefetch_one_failing_photo_does_not_stop_the_rest():
    """Regression: a single network error used to abort the whole pass, so later players never loaded."""
    import logging

    store = heads.HeadshotStore(FakeData(), logging.getLogger("t"))
    tried = []

    def fake_download(player, now):
        tried.append(player["id"])
        if player["id"] == "1":
            raise OSError("boom")
        return True

    store._download = fake_download
    players = [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}, {"id": "3", "name": "C"}]
    assert store.prefetch(players) == 2
    assert tried == ["1", "2", "3"]


def test_upcoming_label_uses_the_wall_clock_not_the_animation_clock():
    """Regression: preview() passed seconds-into-screen as 'now', so every kickoff read as a bare date."""
    import time as _time
    seen = []
    real = model.kickoff_text
    model.kickoff_text = lambda start, now: seen.append(now) or real(start, now)
    try:
        g = dict(games()["t5"])
        render.render_frame(render.preview, ctx(photos=False), {"game": g}, 128, 64, 3.0, "UPCOMING")
    finally:
        model.kickoff_text = real
    assert seen and abs(seen[0] - _time.time()) < 5


@pytest.mark.parametrize("direction", [1, -1])
def test_drive_arrows_point_the_way_the_offense_is_going(direction):
    """Regression: the chevrons were drawn mirrored, so '>' read '<' on every drive."""
    from PIL import Image
    img = Image.new("RGB", (60, 9), d.BLACK)
    render._chevrons(img, 30, 4, direction, 0.0, (255, 255, 255), count=1, spacing=5, max_h=5)
    lit = [(x, y) for x in range(60) for y in range(9) if img.getpixel((x, y)) != d.BLACK]
    tip_x = [x for x, y in lit if y == 4][0]
    wing_x = [x for x, y in lit if y != 4]
    assert wing_x
    if direction == 1:       # driving right: the point is the rightmost pixel
        assert tip_x > max(wing_x)
    else:
        assert tip_x < min(wing_x)
