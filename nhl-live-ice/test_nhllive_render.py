"""Render safety and the arcade look: right size, no crash, nothing blank, and the animations move."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nhllive_demo as demo  # noqa: E402
import nhllive_draw as d  # noqa: E402
import nhllive_headshots as heads  # noqa: E402
import nhllive_model as model  # noqa: E402
import nhllive_render as render  # noqa: E402

SIZES = [(64, 32), (128, 32), (128, 64), (256, 32), (192, 64), (64, 64), (256, 128)]


def games(step=0):
    return {g["id"]: g for g in model.parse_scoreboard(demo.scoreboard(step))}


class FakeData:
    """Just enough of NhlLiveData for a HeadshotStore: an in-memory keyed cache."""

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


def alert_item(kind=None):
    g0, g3 = games(0)["n2"], games(3)["n2"]
    item = dict(model.detect_alert(g0, g3, False, 0), game=g3)
    return dict(item, kind=kind) if kind else item


ITEMS = {
    "scoreboard": lambda g: (render.scoreboard, {"game": g["n2"], "star": g["n2"]["stars"][0]}, "NHL LIVE"),
    "card": lambda g: (render.card, {"game": g["n2"], "player": g["n2"]["stars"][0]}, "TOP"),
    "alert": lambda g: (render.alert, alert_item(), "GOAL!"),
    "preview": lambda g: (render.preview, {"game": g["n5"]}, "UPCOMING"),
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
    lean = dict(g["n1"], last_play="", odds="", run=None, clutch=False)
    lean["home"] = dict(lean["home"], linescores=[], record="")
    lean["away"] = dict(lean["away"], linescores=[], record="")
    render.render_frame(render.scoreboard, ctx(photos=False), {"game": lean}, *size, 1.0, "NHL LIVE")
    player = dict(g["n1"]["stars"][0], parts=[], jersey="", pos="", badge="")
    render.render_frame(render.card, ctx(photos=False), {"game": g["n1"], "player": player}, *size, 1.0, "TOP")
    render.render_frame(render.alert, ctx(photos=False), dict(alert_item(), player=None, desc="", text=""), *size, 1.0, "GOAL!")
    render.render_frame(render.preview, ctx(photos=False), {"game": dict(g["n5"], odds="", start=None)}, *size, 1.0, "UPCOMING")


@pytest.mark.parametrize("size", SIZES)
def test_overtime_shootout_and_intermission_render(size):
    g = games()["n2"]
    ot = dict(g, period=6, period_text="OT3", clock="0:41.2")
    ot["home"] = dict(g["home"], linescores=[1, 1, 1, 0, 0, 0], score=3)
    ot["away"] = dict(g["away"], linescores=[1, 1, 1, 0, 0, 0], score=3)
    so = dict(g, period=5, period_text="SO", clock="0:00", shootout=True)
    so["home"] = dict(g["home"], linescores=[1, 1, 1, 0, 1], score=4)
    so["away"] = dict(g["away"], linescores=[1, 1, 1, 0, 0], score=3)
    intermission = dict(games()["n3"])
    for game in (ot, so, intermission):
        render.render_frame(render.scoreboard, ctx(), {"game": game}, *size, 1.0, "NHL LIVE")
        render.render_frame(render.card, ctx(), {"game": game, "player": game["stars"][0]}, *size, 1.0, "TOP")


def test_animation_moves_and_stills_do_not():
    fn, item, title = ITEMS["scoreboard"](games(3))
    a = render.render_frame(fn, ctx(), item, 128, 64, 1.0, title)
    b = render.render_frame(fn, ctx(), item, 128, 64, 1.3, title)
    assert a.tobytes() != b.tobytes()  # the puck slides, the flame flickers
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
    legend = render.render_frame(render.card, ctx(animate=False), {"game": g["n4"], "player": g["n4"]["stars"][0]}, 128, 64)
    epic = render.render_frame(render.card, ctx(animate=False), {"game": g["n2"], "player": g["n2"]["stars"][0]}, 128, 64)
    assert legend.getpixel((0, 30)) == d.scale(d.GOLD, 0.9)
    assert epic.getpixel((0, 30)) == d.scale(d.EPIC, 0.9)


def test_photo_changes_the_card():
    g = games()
    item = {"game": g["n2"], "player": g["n2"]["stars"][0]}
    with_photo = render.render_frame(render.card, ctx(animate=False), item, 128, 64)
    silhouette = render.render_frame(render.card, ctx(animate=False, photos=False), item, 128, 64)
    assert with_photo.tobytes() != silhouette.tobytes()


def test_a_badge_changes_the_card():
    g = games()
    for game, star, badge in ((g["n2"], g["n2"]["stars"][0], "MULTI-GOAL"), (g["n4"], g["n4"]["stars"][0], "HAT TRICK")):
        assert star["badge"] == badge
        for size in ((128, 64), (128, 32)):
            plain = render.render_frame(render.card, ctx(animate=False), {"game": game, "player": dict(star, badge="")}, *size)
            badged = render.render_frame(render.card, ctx(animate=False), {"game": game, "player": star}, *size)
            assert plain.tobytes() != badged.tobytes()


def test_each_burst_kind_has_its_own_headline():
    base = alert_item()
    a = render.render_frame(render.alert, ctx(), base, 128, 64, 1.0, "GOAL!")
    b = render.render_frame(render.alert, ctx(), dict(base, kind="GOAL", desc="TOR SCORES"), 128, 64, 1.0, "GOAL!")
    c = render.render_frame(render.alert, ctx(), dict(base, kind="OT WINNER", desc="TOR WINS IT"), 128, 64, 1.0, "GOAL!")
    assert base["kind"] == "GO-AHEAD" and len({a.tobytes(), b.tobytes(), c.tobytes()}) == 3


def test_a_run_puts_a_flame_on_the_rink_and_the_score_row():
    g = games(3)["n2"]
    frame = lambda points: render.render_frame(  # noqa: E731
        render.scoreboard, ctx(animate=False), {"game": dict(g, run={"side": "home", "points": points})}, 128, 64, 99.0)
    one, two, three, four = frame(1), frame(2), frame(3), frame(4)
    assert one.tobytes() == two.tobytes()                  # under three unanswered goals there is no flame
    assert two.tobytes() != three.tobytes() and three.tobytes() == four.tobytes()


def test_the_power_play_lights_the_board():
    g = games()["n1"]
    assert g["power_play"]
    for size in ((128, 32), (128, 64)):
        on = render.render_frame(render.scoreboard, ctx(animate=False), {"game": g}, *size, 99.0)
        off = render.render_frame(render.scoreboard, ctx(animate=False), {"game": dict(g, power_play=False)}, *size, 99.0)
        assert on.tobytes() != off.tobytes()


def test_the_play_bar_falls_back_from_the_last_play_to_the_run_then_power_play():
    g = dict(games(3)["n1"], last_play="", run=None)
    bar = lambda game: render.render_frame(render.scoreboard, ctx(animate=False), {"game": game}, 128, 64, 99.0).crop((0, 54, 56, 64)).tobytes()  # noqa: E731
    pp, plain = bar(g), bar(dict(g, power_play=False, odds="", detail=""))
    run = bar(dict(g, power_play=False, run={"side": "home", "points": 3}))
    assert len({pp, plain, run}) == 3


def test_period_column_headings():
    g = {"shootout": False}
    assert [render._period_label(i, 5, g) for i in range(5)] == ["1", "2", "3", "OT", "O2"]
    assert render._period_label(4, 5, {"shootout": True}) == "SO" and render._period_label(3, 5, {"shootout": True}) == "OT"


def test_clutch_time_changes_the_header():
    g = games(3)["n2"]
    assert g["clutch"]
    on = render.render_frame(render.scoreboard, ctx(animate=False), {"game": g}, 128, 32, 99.0)
    off = render.render_frame(render.scoreboard, ctx(animate=False), {"game": dict(g, clutch=False)}, 128, 32, 99.0)
    assert on.tobytes() != off.tobytes()


def test_period_scores_show_on_tall_panels():
    g = games()["n1"]
    bare = dict(g, home=dict(g["home"], linescores=[]), away=dict(g["away"], linescores=[]))
    with_q = render.render_frame(render.scoreboard, ctx(animate=False), {"game": g}, 128, 64, 99.0)
    without = render.render_frame(render.scoreboard, ctx(animate=False), {"game": bare}, 128, 64, 99.0)
    assert with_q.tobytes() != without.tobytes()


def test_score_row_glow_lights_the_scoring_side():
    g = games()["n2"]
    plain = render.render_frame(render.scoreboard, ctx(animate=False), {"game": g}, 128, 64, 99.0)
    lit = render.render_frame(render.scoreboard, ctx(), {"game": g, "celebrate": ("home", 0.0)}, 128, 64, 0.3)
    assert plain.tobytes() != lit.tobytes()


def test_an_overtime_final_says_so_in_the_header():
    g = games()["n4"]
    assert g["ending"] == "OT"
    ot = render.render_frame(render.scoreboard, ctx(animate=False), {"game": g}, 128, 32, 99.0)
    reg = render.render_frame(render.scoreboard, ctx(animate=False), {"game": dict(g, ending="")}, 128, 32, 99.0)
    assert ot.tobytes() != reg.tobytes()


def test_the_loser_of_a_final_is_dimmed():
    g = games()["n4"]
    flipped = dict(g, home=dict(g["home"], score=g["away"]["score"]), away=dict(g["away"], score=g["home"]["score"]))
    a = render.render_frame(render.scoreboard, ctx(animate=False), {"game": g}, 128, 32)
    b = render.render_frame(render.scoreboard, ctx(animate=False), {"game": flipped}, 128, 32)
    assert a.tobytes() != b.tobytes()


def test_the_preview_shows_puck_drop_records_and_line():
    g = games()["n5"]
    full = render.render_frame(render.preview, ctx(animate=False), {"game": g}, 128, 64)
    bare = render.render_frame(render.preview, ctx(animate=False), {"game": dict(g, odds="")}, 128, 64)
    norec = render.render_frame(render.preview, ctx(animate=False),
                                {"game": dict(g, home=dict(g["home"], record=""), away=dict(g["away"], record=""))}, 128, 64)
    assert full.tobytes() != bare.tobytes() and full.tobytes() != norec.tobytes()


def test_very_dark_team_colour_is_lifted_visible():
    assert max(d.lift((0, 0, 0))) >= 150 and max(d.lift((2, 34, 68))) >= 150


def test_long_names_and_big_scores_still_fit():
    g = dict(games()["n2"])
    g["home"] = dict(g["home"], score=144)
    star = dict(g["stars"][0], last="Vanderbilt-Featherstonehaugh", value=1234, parts=[("1234", "PTS"), ("999", "A")])
    for size in SIZES:
        render.render_frame(render.scoreboard, ctx(), {"game": g}, *size, 1.0)
        render.render_frame(render.card, ctx(), {"game": g, "player": star}, *size, 1.0)


# --- the recap panel (replaces the scoreboard on finished games) -----------------

def test_recap_panel_replaces_the_rink_on_a_final_game():
    g = games()["n4"]
    assert g["final"] and g["stars"]
    live_frame = render.render_frame(render.scoreboard, ctx(animate=False), {"game": games()["n2"]}, 128, 32)
    final_frame = render.render_frame(render.scoreboard, ctx(animate=False), {"game": g}, 128, 32)
    assert live_frame.tobytes() != final_frame.tobytes()


def test_recap_panel_counts_up_and_cascades_between_rows():
    g = games()["n4"]
    early = render.render_frame(render.scoreboard, ctx(), {"game": g}, 128, 32, 0.1, "FINAL")
    later = render.render_frame(render.scoreboard, ctx(), {"game": g}, 128, 32, 3.0, "FINAL")
    assert early.tobytes() != later.tobytes()


@pytest.mark.parametrize("size", SIZES)
def test_recap_panel_renders_at_every_size_with_no_stars(size):
    g = dict(games()["n4"], stars=[])
    render.render_frame(render.scoreboard, ctx(animate=False), {"game": g}, *size)


@pytest.mark.parametrize("size", SIZES)
def test_recap_panel_renders_at_every_size_with_stars(size):
    g = games()["n4"]
    img = render.render_frame(render.scoreboard, ctx(), {"game": g}, *size, 2.0, "FINAL")
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


@pytest.mark.parametrize("size", [(64, 32), (96, 48), (128, 32), (128, 64), (192, 64)])
def test_recap_panel_name_never_runs_into_the_stat(size):
    g = games()["n4"]
    long_star = dict(g["stars"][0], last="Antetokounmpo-Vanderbilt", value=41, unit="PTS")
    short_star = dict(long_star, last="Lee")
    frames = [render.render_frame(render.scoreboard, ctx(animate=False), {"game": dict(g, stars=[s])}, *size, 99.0, "FINAL")
              for s in (long_star, short_star)]
    box = (size[0] - 16, 0, size[0] - 2, size[1])   # where "41" (and its unit, when it fits) sit
    assert frames[0].crop(box).tobytes() == frames[1].crop(box).tobytes()
