"""The arena scoreboard: LED digits, what the clock and period say, shots, the PP lamp and your logo."""

import logging
import os
import sys
import time

import pytest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nhllive_board as board  # noqa: E402
import nhllive_demo as demo  # noqa: E402
import nhllive_headshots as heads  # noqa: E402
import nhllive_model as model  # noqa: E402
import nhllive_render as render  # noqa: E402


def games(step=0):
    return {g["id"]: g for g in model.parse_scoreboard(demo.scoreboard(step, now=time.time()))}


class FakeData:
    def __init__(self):
        self.store = {}

    def key(self, *parts):
        return ":".join(["t"] + [str(p) for p in parts])

    def read(self, key):
        return self.store.get(key)

    def write(self, key, data, now=None):
        self.store[key] = {"fetched_at": now or 0, "data": data}


def ctx(logo_path="", animate=False):
    store = heads.HeadshotStore(FakeData(), logging.getLogger("t"))
    demo.seed_headshots(store, list(games().values()), now=1.0)
    return render.RenderContext(store, animate, True, "x", logo_path)


def frame(game, size=(128, 64), logo_path="", t=99.0, animate=False):
    return render.render_frame(render.scoreboard, ctx(logo_path, animate), {"game": game}, *size, t, "NHL LIVE")


def make_logo(path, color=(0, 200, 255), size=(40, 20)):
    Image.new("RGBA", size, color + (255,)).save(path)
    return str(path)


# --- what it says -------------------------------------------------------------

@pytest.mark.parametrize("clock,period_text,expected", [
    ("12:45", "P2", "12:45"), ("7:05", "P1", " 7:05"), ("20:00", "P1", "20:00"), ("0:09", "P3", " 0:09"),
    ("45.3", "P3", "45.3"), ("0:45.3", "P3", " 0:45"), ("", "P1", " 0:00"), ("abc", "P1", " 0:00"),
    ("5:00", "INT", "INT"), ("0:00", "SO", "SO"),
])
def test_clock_face(clock, period_text, expected):
    assert board.clock_face({"clock": clock, "period_text": period_text}) == expected


@pytest.mark.parametrize("period,period_text,expected", [
    (1, "P1", "1"), (2, "P2", "2"), (3, "P3", "3"), (4, "OT", "OT"), (6, "OT3", "OT"), (5, "SO", "SO"), (0, "PRE", "-"),
    (2, "INT", "2"),
])
def test_period_face(period, period_text, expected):
    assert board.period_face({"period": period, "period_text": period_text}) == expected


def test_shot_counts_are_capped_and_unknown_ones_show_dashes():
    assert board._num(7) == " 7" and board._num(33) == "33" and board._num(140) == "99" and board._num(None) == "--"


# --- LED digits ------------------------------------------------------------------

def test_led_widths():
    assert board.led_width("88:88", 3) == 51 and board.led_width("88:88", 2) == 34 and board.led_width("", 3) == 0
    assert board.led_width("1", 1) == 3 and board.led_width(" 1", 1) > board.led_width("1", 1)  # a blank slot is a digit wide


def test_digits_light_their_segments_and_ghost_the_rest():
    red = (255, 56, 44)
    img = Image.new("RGB", (30, 20), (0, 0, 0))
    board.led_text(img, "1", 1, 1, red, 3)
    colours = {c for _, c in img.getcolors(1000)}
    assert red in colours and board.GHOST in colours
    bare = Image.new("RGB", (30, 20), (0, 0, 0))
    board.led_text(bare, "1", 1, 1, red, 3, ghost=False)
    assert board.GHOST not in {c for _, c in bare.getcolors(1000)}
    lit = lambda im: sum(n for n, c in im.getcolors(1000) if c == red)  # noqa: E731
    one, eight = Image.new("RGB", (30, 20)), Image.new("RGB", (30, 20))
    board.led_text(one, "1", 1, 1, red, 3)
    board.led_text(eight, "8", 1, 1, red, 3)
    assert lit(eight) > lit(one)


def test_every_character_the_board_uses_can_be_drawn():
    img = Image.new("RGB", (120, 20))
    assert board.led_text(img, "0123456789:. INTSOP", 0, 0, (255, 0, 0), 1) > 0


# --- the board in the frame ---------------------------------------------------------

def test_the_board_follows_the_clock_period_and_shots():
    g = games()["n2"]
    base = frame(g).tobytes()
    assert frame(dict(g, clock="11:11")).tobytes() != base
    assert frame(dict(g, period=2, period_text="P2")).tobytes() != base
    assert frame(dict(g, home=dict(g["home"], shots=40))).tobytes() != base
    assert frame(dict(g, away=dict(g["away"], shots=None))).tobytes() != base


@pytest.mark.parametrize("size", [(128, 32), (128, 64)])
def test_the_pp_lamp_blinks_with_animation_on(size):
    g = games()["n1"]
    on = frame(g, size, t=0.0, animate=True)
    off = frame(g, size, t=0.5, animate=True)
    assert on.tobytes() != off.tobytes()


@pytest.mark.parametrize("size", [(64, 32), (64, 64), (96, 48), (128, 32), (128, 64), (128, 96), (192, 64), (256, 32), (256, 128)])
@pytest.mark.parametrize("which", ["n1", "n2", "n3"])
def test_the_board_fits_every_panel_in_every_state(size, which):
    g = games()[which]
    for variant in (g, dict(g, clock="0:45.3", power_play=True), dict(g, period=6, period_text="OT3", clock="20:00"),
                    dict(g, period_text="SO", period=5), dict(g, home=dict(g["home"], shots=None), away=dict(g["away"], shots=None))):
        img = frame(variant, size)
        assert img.size == size


# --- your logo ----------------------------------------------------------------------------

def test_a_custom_logo_replaces_the_default(tmp_path):
    g = games()["n2"]
    mine = make_logo(tmp_path / "logo.png")
    default = frame(g)
    custom = frame(g, logo_path=mine)
    assert default.tobytes() != custom.tobytes()
    assert (0, 200, 255) in {c for _, c in custom.crop((58, 31, 128, 53)).getcolors(5000)}


def test_a_missing_or_broken_logo_falls_back_quietly(tmp_path):
    g = games()["n2"]
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not a png")
    default = frame(g).tobytes()
    assert frame(g, logo_path=str(tmp_path / "nope.png")).tobytes() == default
    assert frame(g, logo_path=str(broken)).tobytes() == default


def test_replacing_the_logo_file_is_picked_up_without_a_restart(tmp_path):
    store = heads.HeadshotStore(FakeData(), logging.getLogger("t"))
    path = tmp_path / "logo.png"
    make_logo(path, (255, 0, 0))
    first = store.file_logo(str(path), 20, 10)
    assert first is not None and first.getpixel((first.width // 2, first.height // 2))[:3] == (255, 0, 0)
    make_logo(path, (0, 255, 0))
    os.utime(path, (time.time() + 5, time.time() + 5))
    second = store.file_logo(str(path), 20, 10)
    assert second.getpixel((second.width // 2, second.height // 2))[:3] == (0, 255, 0)
    assert store.file_logo(str(path) + ".x", 20, 10) is None


def test_the_logo_keeps_its_proportions(tmp_path):
    store = heads.HeadshotStore(FakeData(), logging.getLogger("t"))
    wide = store.file_logo(make_logo(tmp_path / "w.png", size=(80, 20)), 22, 16)
    assert wide.width == 22 and wide.height == 5
    tall = store.file_logo(make_logo(tmp_path / "t.png", size=(20, 80)), 22, 16)
    assert tall.height == 16 and tall.width == 4
