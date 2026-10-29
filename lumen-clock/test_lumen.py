"""Tests for Lumen Clock (no hardware, no network)."""

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

src = types.ModuleType("src")
sps = types.ModuleType("src.plugin_system")
bp = types.ModuleType("src.plugin_system.base_plugin")


class BasePlugin:
    def __init__(self, plugin_id, config, display_manager, cache_manager, plugin_manager):
        import logging
        self.plugin_id, self.config = plugin_id, config
        self.display_manager, self.cache_manager, self.plugin_manager = display_manager, cache_manager, plugin_manager
        self.logger = logging.getLogger("test")

    def on_config_change(self, new_config):
        pass

    def get_info(self):
        return {}


bp.BasePlugin = BasePlugin
sys.modules.update({"src": src, "src.plugin_system": sps, "src.plugin_system.base_plugin": bp})

import lumen_astro as astro  # noqa: E402
import lumen_data as data  # noqa: E402
import lumen_scenes as scenes  # noqa: E402
import lumen_type as ty  # noqa: E402
import manager  # noqa: E402

UTC = timezone.utc
LAT, LON = 39.9526, -75.1652
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    TZ = timezone(timedelta(hours=-5))


def font_path():
    return ty.find_font([str(HERE / "fonts")], ty.FONT_NAMES) or ty.find_font(
        ["C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu"], ("arialbd.ttf", "DejaVuSans-Bold.ttf"))


def faces():
    p = font_path()
    return ty.Face(p, 28, 500, 0.70), ty.Face(p, 22, 500, 0.70), ty.Face(p, 10, 600)


# ── typography ───────────────────────────────────────────────────────────────

def test_face_has_fixed_digit_slots_and_squeezes():
    p = font_path()
    wide, slim = ty.Face(p, 28, 500, 1.0), ty.Face(p, 28, 500, 0.6)
    assert slim.slot < wide.slot and slim.digit_h == wide.digit_h
    assert wide.width("12:45") == 4 * wide.slot + wide.colon_w
    assert wide.glyph("8").size == wide.cell and wide.glyph("8") is wide.glyph("8")        # cached, same cell for every digit


def test_time_mask_is_steady_when_nothing_changes_and_rolls_when_a_digit_does():
    big = faces()[0]
    steady, box = ty.time_mask(big, "12:45", "12:45", 9.0, 50, 15)
    assert steady.getbbox() is not None and 0 < box[0] < box[2] < 128 and box[3] - box[1] == big.digit_h
    after, _ = ty.time_mask(big, "12:45", "12:44", ty.ROLL_SECONDS + 0.01, 50, 15)
    assert after.tobytes() == steady.tobytes()                                             # the roll has finished
    mid, _ = ty.time_mask(big, "12:45", "12:44", ty.ROLL_SECONDS / 2, 50, 15)
    assert mid.tobytes() != steady.tobytes()                                               # halfway through it differs
    start, _ = ty.time_mask(big, "12:45", "12:44", 0.0, 50, 15)
    assert start.getbbox() is not None                                                     # the old digit is still there at t=0
    colon_only, _ = ty.time_mask(big, "12:45", "12:45", 0.1, 50, 15)
    assert colon_only.tobytes() == steady.tobytes()                                        # unchanged digits and colon never move


def test_a_change_in_text_length_does_not_crash():
    big = faces()[0]
    for old, new in (("9:59", "10:00"), ("12:59", "1:00"), ("", "1:00")):
        mask, _ = ty.time_mask(big, new, old, 0.1, 50, 15)
        assert mask.size == (128, 32)


def test_glow_adds_light_and_shadow_darkens():
    big = faces()[0]
    mask, _ = ty.time_mask(big, "12:45", "12:45", 9, 50, 15)
    img = Image.new("RGB", (128, 32), (10, 10, 10))
    ty.glow(img, mask, (255, 0, 0), 3.0, 1.0)
    assert max(p[0] for p in img.getdata()) > 100 and all(p[1] == 10 for p in img.getdata())
    lit = Image.new("RGB", (128, 32), (200, 200, 200))
    ty.shadow(lit, mask, (0, 0), (0, 0, 0), 0.8, 1.0)
    assert min(p[0] for p in lit.getdata()) < 120


def test_missing_font_still_gives_a_working_face():
    f = ty.Face("/nonexistent/font.ttf", 28, 500, 0.7)
    assert f.width("12:45") > 0 and ty.time_mask(f, "12:45", "12:45", 9, 50, 15)[0].getbbox() is not None


# ── scenes ───────────────────────────────────────────────────────────────────

def ctx_at(hour, minute=32, second=41.5, temp="65", h12=True, place=True, prev=None, age=9.0):
    dt = datetime(2026, 11, 13, hour, minute, int(second), int((second % 1) * 1e6), tzinfo=TZ)
    big, small, tiny = faces()
    text = ("%d:%02d" % (dt.hour % 12 or 12, dt.minute)) if h12 else "%02d:%02d" % (dt.hour, dt.minute)
    return {"now": 7.3, "dt": dt, "sec": second, "text": text, "prev": prev or text, "age": age, "dow": "FRI", "md": "NOV 13",
            "ampm": ("PM" if hour >= 12 else "AM") if h12 else "", "temp": temp, "face": big, "face_s": small, "face_t": tiny,
            "lat": LAT, "lon": LON, "info": astro.day_info(dt.astimezone(UTC), LAT, LON, TZ) if place else None, "show_seconds": True}


@pytest.mark.parametrize("name", list(scenes.SCENES))
@pytest.mark.parametrize("hour", [0, 5, 7, 12, 16, 17, 21, 23])
def test_every_scene_renders_at_every_time_of_day(name, hour):
    img = scenes.SCENES[name](ctx_at(hour))
    assert img.size == (128, 32) and img.getbbox() is not None


@pytest.mark.parametrize("name", list(scenes.SCENES))
def test_scenes_cope_with_24h_no_temperature_and_a_rolling_digit(name):
    assert scenes.SCENES[name](ctx_at(9, h12=False, temp=None)).size == (128, 32)
    assert scenes.SCENES[name](ctx_at(10, 0, prev="9:59", age=0.2)).size == (128, 32)
    assert scenes.SCENES[name](ctx_at(13, 5, second=59.99)).size == (128, 32)                # the very end of a minute
    assert scenes.SCENES[name](ctx_at(13, 5, second=0.0)).size == (128, 32)


@pytest.mark.parametrize("name", list(scenes.SCENES))
def test_scenes_animate_and_follow_the_seconds(name):
    a, b = ctx_at(14, second=10.0), ctx_at(14, second=40.0)
    assert scenes.SCENES[name](a).tobytes() != scenes.SCENES[name](b).tobytes()
    later = dict(a, now=a["now"] + 1.7)
    if name != "orbit":                                                                    # the dial moves only with the time itself
        assert scenes.SCENES[name](a).tobytes() != scenes.SCENES[name](later).tobytes()


def test_tide_rises_through_the_minute_and_drains_at_the_top():
    levels = [scenes.tide_level(s) for s in (1.2, 15, 30, 45, 59.9, 60.0)]
    assert levels == sorted(levels) and abs(levels[0] - 3.0) < 0.01 and abs(levels[-1] - 17.0) < 0.01
    assert scenes.tide_level(0.0) > scenes.tide_level(0.6) > scenes.tide_level(1.2)           # draining


def test_daylight_shows_the_sun_by_day_and_stars_by_night():
    day, night = scenes.SCENES["daylight"](ctx_at(12)), scenes.SCENES["daylight"](ctx_at(23))
    assert sum(day.getpixel((x, 3))[2] for x in range(0, 128, 8)) > sum(night.getpixel((x, 3))[2] for x in range(0, 128, 8)) * 3


def test_orbit_dial_hands_follow_the_time():
    a = scenes.dial(ctx_at(3, 0, 0.0), (255, 120, 60))
    b = scenes.dial(ctx_at(9, 0, 0.0), (255, 120, 60))
    assert a.size == (34, 34) and a.tobytes() != b.tobytes()


# ── data ─────────────────────────────────────────────────────────────────────

def test_entity_validation_and_numbers():
    assert data.valid_entity("weather.home", "weather.x") == "weather.home"
    assert data.valid_entity("weather.home; x", "weather.x") == "weather.x"
    assert data.num("12.5") == 12.5 and data.num(None) is None and data.num("nan") is None


# ── manager ──────────────────────────────────────────────────────────────────

class DM:
    width, height = 128, 32

    def __init__(self):
        self.image = Image.new("RGB", (128, 32))
        self.updates = 0

    def update_display(self):
        self.updates += 1


class Cache:
    def __init__(self):
        self.store = {}

    def set(self, k, v, ttl=None):
        self.store[k] = v


def make(**cfg):
    base = {"timezone": "America/New_York", "latitude": LAT, "longitude": LON}
    base.update(cfg)
    p = manager.LumenClockPlugin("lumen-clock", base, DM(), Cache(), None)
    p._stop.set()
    return p


def at(h, m=0, s=0):
    return datetime(2026, 11, 13, h, m, s, tzinfo=TZ).timestamp()


def test_each_appearance_shows_the_next_scene_and_fades_up():
    p = make()
    assert p.scene_names == list(scenes.SCENES)
    shown = []
    t = at(14)
    for turn in range(6):
        t += 120                                                       # a gap longer than 1.5 s starts a new turn
        assert p.display(display_mode="lumen_clock") is True
        shown.append(p._turn)
        first = p.compose(p._turn_t0 + 0.01)
        later = p.compose(p._turn_t0 + 2.0)
        assert sum(map(sum, first.getdata())) < sum(map(sum, later.getdata())) or turn % 5 == 2    # fade-in from black
        p._last_display = 0.0
    assert shown == [0, 1, 2, 3, 4, 5]


def test_scene_order_follows_the_turn_and_wraps():
    p = make()
    info = p._info_for(datetime.fromtimestamp(at(14), TZ), at(14))
    names = p._available(info)
    assert names == list(scenes.SCENES)
    for turn in range(7):
        p._turn = turn
        p.compose(at(14, 0, turn))
    assert p._turn == 6


def test_no_location_means_no_daylight_scene():
    p = make(latitude=None, longitude=None)
    assert p._available(p._info_for(datetime.fromtimestamp(at(14), TZ), at(14))) == [s for s in scenes.SCENES if s != "daylight"]
    for turn in range(6):
        p._turn = turn
        assert p.compose(at(14, 0, turn)).size == (128, 32)


def test_pinned_style_and_scene_list_and_config_cleaning():
    assert make(style="neon").scene_names == ["neon"]
    assert make(scenes=["orbit", "bogus", "tide"]).scene_names == ["orbit", "tide"]
    assert make(style="bogus").scene_names == list(scenes.SCENES)
    p = make(display_duration=1, demo_speed=99999, fade_seconds=99, time_format="24h", ha_url="http://x/")
    assert p.display_duration == 8 and p.demo_speed == 600 and p.fade == 3.0 and not p.h12 and p.ha_url == "http://x"
    assert make(weather_entity="bad entity!").weather_entity == "weather.openweathermap"


def test_the_digits_roll_on_a_minute_change_but_not_when_a_turn_starts():
    p = make(style="aurora", fade_seconds=0)
    p.display()                                                        # a fresh turn at the real time: nothing rolls
    assert p._prev == p._text
    t = at(14, 32, 59)
    p._text = None
    p.compose(t)
    p.compose(t + 1.0)                                                 # 14:33:00 -> the change is noticed
    assert p._prev == "2:32" and p._text == "2:33" and p._change_t == t + 1.0
    steady = p.compose(t + 1.0 + 5.0)
    rolling = p.compose(t + 1.0 + 0.1)
    assert rolling.tobytes() != steady.tobytes()


def test_24h_format_and_am_pm():
    p = make(style="aurora", time_format="24h")
    p.compose(at(9, 5, 0))
    assert p._text == "09:05"
    q = make(style="aurora")
    q.compose(at(0, 5, 0))
    assert q._text == "12:05"


def test_temperature_shows_when_fresh_and_hides_when_stale_or_off():
    p = make(style="aurora", fade_seconds=0)
    now = at(14)
    assert p._temp_text(now) is None                                   # nothing fetched yet
    p._temp, p._temp_ts = 63, now - 60
    assert p._temp_text(now) == "63"
    assert p._temp_text(now + 7200) is None                            # two hours old: do not show a stale number
    assert make(show_temperature=False)._temp_text(now) is None


def test_demo_runs_a_whole_day_through_every_scene():
    p = make(test_mode=True, demo_speed=300)
    assert p.cache_manager.store["display_on_demand_request"]["mode"] == "lumen_clock"
    assert p.get_display_duration() == manager.DEMO_SCENE_SECONDS * 5
    seen, texts = set(), set()
    t0 = p._demo_t0
    for step in range(0, int(p.get_display_duration() * 4)):
        frame = p.compose(t0 + step * 0.25)
        assert frame.size == (128, 32)
        seen.add(frame.tobytes())
        texts.add(p._text)
    assert len(seen) > 100 and len(texts) > 20                         # time really moved on, and the picture with it


def test_display_draws_to_the_panel_and_reports_its_length():
    p = make()
    assert p.display() is True and p.display_manager.updates == 1
    assert p.get_display_duration() == 20 and p.validate_config() is True
