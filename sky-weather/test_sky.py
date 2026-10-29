"""Tests for Sky Weather (no hardware, no network)."""

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# a stand-in for LEDMatrix's BasePlugin (the real one has no plugin_dir, so this one has none either)
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

import sky_astro as astro  # noqa: E402
import sky_data as data  # noqa: E402
import sky_render as render  # noqa: E402
import manager  # noqa: E402

UTC = timezone.utc
LAT, LON = 39.9526, -75.1652          # Philadelphia City Hall
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    TZ = timezone(timedelta(hours=-4))


# ── astronomy ────────────────────────────────────────────────────────────────

def test_sun_times_match_home_assistant():
    """Home Assistant reported rise 11:28:23Z, set 22:00:48Z for 30 Oct 2026 at this place."""
    t = astro.sun_times(datetime(2026, 10, 30, 4, 0, tzinfo=UTC), LAT, LON)
    assert abs((t["sunrise"] - datetime(2026, 10, 30, 11, 28, 23, tzinfo=UTC)).total_seconds()) < 90
    assert abs((t["sunset"] - datetime(2026, 10, 30, 22, 0, 48, tzinfo=UTC)).total_seconds()) < 90
    assert t["dawn"] < t["sunrise"] < t["sunset"] < t["dusk"]


def test_sun_is_up_at_noon_and_down_at_midnight():
    assert astro.sun_altaz(datetime(2026, 10, 29, 17, 0, tzinfo=UTC), LAT, LON)[0] > 25
    assert astro.sun_altaz(datetime(2026, 10, 30, 5, 0, tzinfo=UTC), LAT, LON)[0] < -20


def test_moon_phase_new_and_full():
    age, illum, name = astro.moon_phase(datetime(2026, 11, 9, 7, 0, tzinfo=UTC))
    assert illum < 0.01 and name == "NEW MOON"
    _, illum, name = astro.moon_phase(datetime(2026, 11, 24, 15, 0, tzinfo=UTC))
    assert illum > 0.99 and name == "FULL MOON"


def test_moon_phase_matches_home_assistant_sensor():
    """sensor.moon_phase said waning_gibbous on 29 Oct 2026."""
    assert astro.moon_phase(datetime(2026, 10, 29, 18, 30, tzinfo=UTC))[2] == "WANING GIBBOUS"


def test_next_phase_events_are_ordered_and_in_future():
    now = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
    ev = astro.next_phase_events(now, 4)
    assert [n for n, _ in ev] == ["LAST QUARTER", "NEW MOON", "FIRST QUARTER", "FULL MOON"]
    assert all(t > now for _, t in ev) and [t for _, t in ev] == sorted(t for _, t in ev)
    assert abs((ev[1][1] - datetime(2026, 11, 9, 7, 3, tzinfo=UTC)).total_seconds()) < 1200


def test_day_info_shape_and_moon_pass():
    info = astro.day_info(datetime(2026, 10, 29, 23, 0, tzinfo=UTC), LAT, LON, TZ)
    assert info["sunrise"] < info["sunset"] and info["next_sunrise"] > info["sunset"]
    assert 9 * 3600 < info["daylight"] < 12 * 3600
    assert info["moon"]["pass"] is None and not info["moon"]["up"]          # moonrise is after midnight
    up = astro.day_info(datetime(2026, 10, 30, 6, 0, tzinfo=UTC), LAT, LON, TZ)["moon"]
    assert up["up"] and up["pass"][0] < datetime(2026, 10, 30, 6, 0, tzinfo=UTC) < up["pass"][1]


# ── data ─────────────────────────────────────────────────────────────────────

def test_parse_alerts_sorts_by_severity_and_reads_either_key_case():
    attrs = {"Alerts": [
        {"Event": "Frost Advisory", "Severity": "Minor", "Ends": "2026-10-30T13:00:00-04:00"},
        {"event": "Tornado Warning", "severity": "Extreme", "headline": "TAKE COVER"},
        {"Event": "", "Severity": "Severe"},                     # no event name: dropped
        "garbage",
    ]}
    out = data.parse_alerts(attrs)
    assert [a["event"] for a in out] == ["Tornado Warning", "Frost Advisory"]
    assert out[0]["severity"] == "extreme" and out[1]["expires"].hour == 13


def test_parse_alerts_tolerates_nothing():
    assert data.parse_alerts(None) == [] and data.parse_alerts({}) == [] and data.parse_alerts({"Alerts": "x"}) == []


def test_parse_now_and_forecast():
    now = data.parse_now({"state": "Sunny", "attributes": {"temperature": 65, "temperature_unit": "°F", "wind_speed": "9.2", "humidity": None}})
    assert now["cond"] == "sunny" and now["temp"] == 65 and now["wind"] == 9.2 and now["humidity"] is None and now["unit"] == "F"
    fc = data.parse_forecast([{"datetime": "2026-10-29T22:00:00+00:00", "condition": "rainy", "temperature": 60, "templow": 50},
                              {"datetime": "nonsense"}, "x"], True)
    assert len(fc) == 1 and fc[0]["low"] == 50 and fc[0]["cond"] == "rainy"


def test_entities_are_validated_and_conditions_have_fallbacks():
    assert data.valid_entity("weather.home", "weather.x") == "weather.home"
    assert data.valid_entity("weather.home; rm -rf /", "weather.x") == "weather.x"
    assert data.cond("something-new")["label"] == "--"
    assert data.compass(230) == "SW" and data.compass(None) == ""


# ── colours and sprites ──────────────────────────────────────────────────────

def lum(c):
    return 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]


def test_sky_gets_brighter_with_the_sun_and_never_jumps():
    tops = [lum(render.sky_colors(e, True)[0]) for e in range(-20, 40)]
    assert tops[0] < 10 and tops[-1] > 70
    assert all(b >= a - 6 for a, b in zip(tops, tops[1:]))               # roughly monotone
    assert all(abs(b - a) < 25 for a, b in zip(tops, tops[1:]))          # smooth


def test_golden_hour_is_warm_at_the_horizon():
    _, hor = render.sky_colors(1.0, False)
    assert hor[0] > hor[2] + 60                                           # red well above blue


def test_moon_sprite_phases():
    def lit_columns(sprite):
        px = sprite.load()
        w, h = sprite.size
        left = sum(px[x, y][0] for x in range(w // 2) for y in range(h) if px[x, y][3] > 200)
        right = sum(px[x, y][0] for x in range(w // 2, w) for y in range(h) if px[x, y][3] > 200)
        return left, right
    new = render.moon_sprite(8, 0.0, 90.0)
    full = render.moon_sprite(8, 1.0, 90.0)
    assert max(p[0] for p in new.getdata()) < 60 < 200 < max(p[0] for p in full.getdata())
    wax_l, wax_r = lit_columns(render.moon_sprite(8, 0.3, 90.0))          # waxing: lit on the right
    assert wax_r > wax_l * 2
    wan_l, wan_r = lit_columns(render.moon_sprite(8, 0.3, 270.0))         # waning: lit on the left
    assert wan_l > wan_r * 2


def test_every_icon_renders():
    for cond in data.CONDITIONS:
        for night in (False, True):
            for frame in (0, 1):
                assert render.icon(render.icon_name(cond, night), frame).size == (13, 11)


# ── pages ────────────────────────────────────────────────────────────────────

def make_world(hour=15, cond="sunny", alerts=None, empty=False, **cur_over):
    ts = datetime(2026, 10, 29, hour, 0, tzinfo=TZ).timestamp()
    base = datetime.fromtimestamp(ts, UTC)
    cur = {"cond": cond, "temp": 65.0, "feels": 63.0, "unit": "F", "humidity": 70.0, "cloud": 10.0, "wind": 9.0, "gust": None,
           "wind_unit": "mph", "bearing": 230.0, "uv": 3.0, "pressure": 30.1, "pressure_unit": "inHg", "dew": 50.0,
           "visibility": 9.0, "visibility_unit": "mi"}
    cur.update(cur_over)
    hourly = [] if empty else [{"t": base + timedelta(hours=i), "cond": "rainy" if i % 3 else "sunny", "temp": 60.0 - i, "low": None,
                                "pop": 10.0 * i, "wind": 5.0, "precip": 0.0, "uv": 2.0} for i in range(24)]
    daily = [] if empty else [{"t": base + timedelta(days=i), "cond": "cloudy", "temp": 66.0, "low": 48.0, "pop": 40.0,
                               "wind": 5.0, "precip": 0.0, "uv": 2.0} for i in range(7)]
    return {"ts": ts, "tz": TZ, "lat": LAT, "lon": LON, "cur": cur, "hourly": hourly, "daily": daily, "alerts": alerts or [],
            "info": astro.day_info(base, LAT, LON, TZ), "hourly_step": 2, "detail_tiles": []}


ALERTS = [{"event": "Severe Thunderstorm Warning", "severity": "severe", "headline": "", "expires": datetime(2026, 10, 30, 3, 0, tzinfo=UTC), "urgency": ""},
          {"event": "Flood Watch", "severity": "moderate", "headline": "FLOODING POSSIBLE", "expires": None, "urgency": ""}]


@pytest.mark.parametrize("hour", [3, 6, 7, 12, 17, 18, 22])
@pytest.mark.parametrize("cond", list(data.CONDITIONS) + ["bogus"])
def test_now_page_renders_in_every_light_and_weather(hour, cond):
    img = render.render(make_world(hour, cond, cloud=50.0), "now")
    assert img.size == (128, 32) and img.getbbox() is not None


@pytest.mark.parametrize("page", list(render.PAGES) + ["alert"])
def test_every_page_renders(page):
    w = make_world(21, alerts=ALERTS)
    assert render.render(w, page, None, 1).size == (128, 32)


@pytest.mark.parametrize("page", list(render.PAGES))
def test_pages_survive_missing_data(page):
    w = make_world(15, empty=True, temp=None, feels=None, humidity=None, wind=None, bearing=None, uv=None, pressure=None,
                   dew=None, visibility=None, cloud=None)
    assert render.render(w, page).size == (128, 32)


def test_details_always_fills_up_to_four_tiles():
    img = render.render(make_world(15, uv=None), "details")
    px = img.load()
    assert all(px[x, 16] != (0, 0, 0) for x in (10, 42, 74, 106))


def test_alert_page_marquees_long_names_and_picks_by_index():
    w = make_world(15, alerts=ALERTS)
    a = render.render(dict(w, anim=0.0), "alert", None, 0)
    b = render.render(dict(w, anim=1.0), "alert", None, 0)
    assert a.tobytes() != b.tobytes()
    assert render.render(w, "alert", None, 0).tobytes() != render.render(w, "alert", None, 1).tobytes()


def test_overcast_hides_the_sun():
    clear = render.sky_state(make_world(13, "sunny", cloud=0.0))
    assert clear["sun_frac"] is not None and clear["cover"] == 0.0
    assert render.sky_state(make_world(13, "cloudy", cloud=100.0))["cover"] > 0.9


def test_animation_clock_is_separate_from_sky_time():
    w = make_world(13, "partlycloudy", cloud=50.0)
    assert render.render(dict(w, anim=1.0), "now").tobytes() != render.render(dict(w, anim=9.0), "now").tobytes()


# ── manager ──────────────────────────────────────────────────────────────────

class DM:
    width, height = 128, 32

    def __init__(self):
        self.image = __import__("PIL.Image", fromlist=["Image"]).new("RGB", (128, 32))
        self.updates = 0

    def update_display(self):
        self.updates += 1


class Cache:
    def __init__(self):
        self.store = {}

    def set(self, k, v, ttl=None):
        self.store[k] = v


def make_plugin(**cfg):
    base = {"ha_url": "http://x", "ha_token": "t", "latitude": LAT, "longitude": LON, "timezone": "America/New_York", "test_mode": True}
    base.update(cfg)
    return manager.SkyWeatherPlugin("sky-weather", base, DM(), Cache(), None)


def test_demo_mode_runs_through_every_page_and_asks_for_the_screen():
    p = make_plugin()
    assert p.cache_manager.store["display_on_demand_request"]["mode"] == "sky_weather"
    seen = set()
    t0 = p._demo_t0
    for step in range(0, int(manager.TIMELAPSE + manager.DEMO_PAGE * 6) * 2):
        frame = p.compose(t0 + step * 0.5)
        assert frame is not None and frame.size == (128, 32)
        seen.add(frame.tobytes())
    assert len(seen) > 40                                                  # the time-lapse really changes the picture


def test_page_rotation_alerts_first_and_restarts_each_turn():
    p = make_plugin(test_mode=False, pages=["now", "sun", "bogus"], page_seconds=5, alert_seconds=8)
    assert p.pages == ["now", "sun"]
    w = make_world(15, alerts=ALERTS)
    seq = p.page_sequence(w)
    assert seq == [("alert", 0), ("alert", 1), ("now", 0), ("sun", 0)]
    p._t0 = 1000.0
    picks = [seq[p._index(seq, 1000.0 + s)][0] for s in (0, 7, 8, 15, 16, 20, 21, 25, 26)]
    assert picks == ["alert", "alert", "alert", "alert", "now", "now", "sun", "sun", "alert"]
    assert p.get_display_duration() == 10                                  # no alerts polled yet: just the two pages


def test_new_alert_interrupts_only_when_asked_and_never_on_the_first_poll():
    p = make_plugin(test_mode=False, interrupt_alerts=True)
    p._stop.set()
    quiet = {"now": {}, "alerts": []}
    loud = {"now": {}, "alerts": [ALERTS[0]]}
    p._apply_state(loud, 0.0)                                              # first poll: just learn what is already there
    assert "display_on_demand_request" not in p.cache_manager.store
    p._apply_state(quiet, 1.0)
    p._apply_state(loud, 2.0)                                              # cleared, then issued again: that is a new alert
    assert p.cache_manager.store.pop("display_on_demand_request")["plugin_id"] == "sky-weather"
    p._apply_state(loud, 3.0)                                              # the same alert still active: nothing new
    assert "display_on_demand_request" not in p.cache_manager.store
    p._apply_state({"now": {}, "alerts": [ALERTS[0], ALERTS[1]]}, 4.0)     # a second one appears
    assert p.cache_manager.store["display_on_demand_request"]["plugin_id"] == "sky-weather"
    quiet_plugin = make_plugin(test_mode=False, interrupt_alerts=False)
    quiet_plugin._stop.set()
    quiet_plugin._apply_state(quiet, 0.0)
    quiet_plugin._apply_state({"now": {}, "alerts": [ALERTS[1]]}, 1.0)
    assert "display_on_demand_request" not in quiet_plugin.cache_manager.store


def test_expired_alerts_are_dropped_and_no_data_means_no_frame():
    p = make_plugin(test_mode=False)
    p._stop.set()
    assert p.compose(1.0) is None                                          # nothing polled yet
    now = datetime(2026, 10, 29, 20, 0, tzinfo=UTC).timestamp()
    old = dict(ALERTS[0], expires=datetime(2026, 10, 29, 19, 0, tzinfo=UTC))
    p._state = {"now": make_world(15)["cur"], "alerts": [old, ALERTS[1]], "alerts_ok": True}
    p._place = {"lat": LAT, "lon": LON, "tz": "America/New_York"}
    w = p._world(now)
    assert [a["event"] for a in w["alerts"]] == ["Flood Watch"]
    assert p.compose(now).size == (128, 32)


def test_alerts_can_be_turned_off_and_config_is_cleaned():
    p = make_plugin(test_mode=False, alerts=False, hourly_step=99, page_seconds=1, poll_seconds=1, weather_entity="bad entity!")
    p._stop.set()
    assert p.hourly_step == 6 and p.page_seconds == 3 and p.poll == 15
    assert p.entities["weather_entity"] == "weather.openweathermap"
    p._state = {"now": make_world(15)["cur"], "alerts": [ALERTS[0]]}
    assert p._world(datetime(2026, 10, 29, 20, 0, tzinfo=UTC).timestamp())["alerts"] == []


def test_moon_tilt_faces_the_sun():
    """An evening crescent's bright edge points down-right (the Sun just set in the west); a morning moon's points left."""
    evening = astro.bright_limb_angle(datetime(2026, 11, 13, 23, 30, tzinfo=UTC), 39.9526, -75.1652)
    assert 90 < evening < 180
    morning = astro.bright_limb_angle(datetime(2026, 10, 30, 11, 0, tzinfo=UTC), 39.9526, -75.1652)
    assert 180 < morning < 360


# ── radar ────────────────────────────────────────────────────────────────────

class FakeRadar:
    """Stands in for sky_radar.RadarFetcher: no network, a fixed loop."""

    def __init__(self, frames=6):
        self.frames, self.resets, self.draws, self.smooth_draws, self.refreshes = frames, 0, 0, 0, 0

    def _playback_frames(self):
        return [object()] * self.frames

    def get_loop_duration(self):
        return self.frames * 0.5 + 1.5

    def reset_loop(self):
        self.resets += 1

    def get_radar_image(self, w, h):
        self.draws += 1
        return __import__("PIL.Image", fromlist=["Image"]).new("RGB", (w, h), (0, 60, 0))

    def get_smooth_image(self, w, h):
        self.smooth_draws += 1
        return __import__("PIL.Image", fromlist=["Image"]).new("RGB", (w, h), (0, 60, 0))

    def needs_refresh(self, interval):
        return True

    def refresh_data(self, w, h):
        self.refreshes += 1


def radar_plugin(**cfg):
    p = make_plugin(test_mode=False, page_seconds=7, **cfg)
    p._stop.set()
    p._place = {"lat": LAT, "lon": LON, "tz": "America/New_York"}
    p._state = {"now": make_world(15)["cur"], "alerts": [], "alerts_ok": True}
    return p


def test_radar_page_joins_the_rotation_only_once_it_has_frames():
    p = radar_plugin()
    w = make_world(15)
    assert "radar" in p.pages and ("radar", 0) not in p.page_sequence(w)       # nothing downloaded yet
    p._radar = FakeRadar()
    seq = p.page_sequence(w)
    assert seq[0] == ("now", 0) and seq[1] == ("radar", 0)
    p._radar = FakeRadar(frames=0)                                               # fetcher exists but has no frame yet
    assert ("radar", 0) not in p.page_sequence(w)


def test_radar_page_length_is_two_loops_within_limits():
    p = radar_plugin()
    p._radar = FakeRadar(frames=6)                                               # loop = 4.5 s
    assert p._radar_page_seconds() == 9.0
    p._radar = FakeRadar(frames=2)                                               # loop = 2.5 s -> never shorter than a page
    assert p._radar_page_seconds() == 7.0
    p._radar = FakeRadar(frames=40)                                              # capped at 30 s
    assert p._radar_page_seconds() == 30.0
    assert radar_plugin(radar_seconds=12)._radar_page_seconds() == 12.0
    p._radar = FakeRadar(frames=6)
    assert p.get_display_duration() == 7 * 6 + 9


def test_radar_page_draws_the_fetcher_frame_and_restarts_the_loop_on_entry():
    p = radar_plugin()
    p._radar = FakeRadar()
    now = datetime(2026, 10, 29, 20, 0, tzinfo=UTC).timestamp()
    p._t0 = now
    assert p.compose(now).getpixel((5, 5)) != (0, 60, 0)                         # page 1: the sky
    frame = p.compose(now + 8)                                                   # page 2: the radar
    assert frame.getpixel((5, 5)) == (0, 60, 0) and p._radar.resets == 1
    p.compose(now + 9)
    assert p._radar.resets == 1                                                  # staying on radar does not reset it
    p.compose(now + 7 + 9 + 8)                                                   # on to another page ...
    p.compose(now + 7 + 9 + 7 * 6 + 8)                                           # ... and round to the radar again
    assert p._radar.resets == 2


def test_radar_can_be_turned_off_and_settings_are_cleaned():
    p = radar_plugin(radar=False)
    assert "radar" not in p.pages
    p._radar = FakeRadar()
    assert ("radar", 0) not in p.page_sequence(make_world(15))
    p = radar_plugin(pages=["radar"], radar=False)
    assert p.pages and "radar" not in p.pages                                    # never an empty rotation
    p = radar_plugin(radar_range_miles=-4, radar_past_frames=99, radar_frame_seconds="fast", radar_line_color=[999, "x"],
                     radar_map_brightness=7)
    c = p.radar_cfg
    assert c["range_miles"] == 5.0 and c["past_frames"] == 12 and c["frame_seconds"] == 0.5
    assert c["line_color"] == (0, 130, 70) and c["map_brightness"] == 1.0 and c["tile_server"] == ""


def test_radar_refresh_uses_panel_size_and_waits_for_a_location():
    p = radar_plugin()
    p._radar = FakeRadar()
    p._radar_sig = (LAT, LON) + tuple(sorted((k, str(v)) for k, v in p.radar_cfg.items()))
    p._update_radar()
    assert p._radar.refreshes == 1
    nowhere = radar_plugin(latitude=None, longitude=None)
    nowhere._place = {}
    nowhere._update_radar()                                                      # no location yet: quietly nothing
    assert nowhere._radar is None


# ── smooth radar playback ────────────────────────────────────────────────────

def _smooth_fetcher(monkeypatch, frames=3):
    import sky_radar
    from PIL import Image
    f = sky_radar.RadarFetcher(LAT, LON, 75, None, map_style="vector", frame_seconds=1.0, loop_pause_seconds=2.0)
    size = f._get_background(f._ensure_viewport(128, 32)).size
    colors = [(255, 0, 0, 255), (0, 0, 255, 255), (0, 255, 0, 255), (255, 255, 0, 255)]
    f._frames = {i + 1: sky_radar.RadarFrame(i + 1, "p", False, Image.new("RGBA", size, colors[i])) for i in range(frames)}
    clock = {"t": 100.0}
    monkeypatch.setattr(sky_radar.time, "time", lambda: clock["t"])
    f._smooth_t0 = 100.0
    return f, clock


def _px(f):
    return f.get_smooth_image(128, 32).getpixel((120, 2))                       # clear of the label, crosshair and dots


def test_smooth_radar_melts_one_frame_into_the_next(monkeypatch):
    f, clock = _smooth_fetcher(monkeypatch)
    assert _px(f) == (255, 0, 0)                                                 # t=0: the oldest frame, untouched
    clock["t"] = 100.5
    r, g, b = _px(f)
    assert abs(r - 128) <= 2 and g == 0 and abs(b - 128) <= 2                    # halfway red -> blue
    clock["t"] = 101.5
    r, g, b = _px(f)
    assert r == 0 and abs(g - 128) <= 2 and abs(b - 128) <= 2                    # halfway blue -> green
    clock["t"] = 101.25
    assert _px(f)[2] > 150                                                       # a quarter in is still mostly blue


def test_smooth_radar_holds_the_newest_frame_then_loops(monkeypatch):
    f, clock = _smooth_fetcher(monkeypatch)
    for t in (102.0, 102.5, 103.9):                                              # sweep is 2 s, then a 2 s hold
        clock["t"] = t
        assert _px(f) == (0, 255, 0)
    clock["t"] = 104.0
    assert _px(f) == (255, 0, 0)                                                 # and round again
    clock["t"] = 150.0
    f.reset_loop()
    assert _px(f) == (255, 0, 0)                                                 # a page entry restarts from the first frame


def test_smooth_radar_label_stays_crisp_and_one_frame_falls_back(monkeypatch):
    f, clock = _smooth_fetcher(monkeypatch)
    clock["t"] = 100.5
    img = f.get_smooth_image(128, 32)
    assert img.size == (128, 32) and img.getbbox() is not None
    f._frames = dict(list(f._frames.items())[:1])                                # a single frame cannot be blended
    assert f.get_smooth_image(128, 32).size == (128, 32)
    f._frames = {}
    assert f.get_smooth_image(128, 32).size == (128, 32)                         # nothing loaded: just the map


def test_smooth_is_the_default_and_can_be_switched_off():
    assert radar_plugin().radar_cfg["smooth"] is True
    p = radar_plugin(radar_smooth=False)
    p._radar = FakeRadar()
    now = datetime(2026, 10, 29, 20, 0, tzinfo=UTC).timestamp()
    p._t0 = now
    p.compose(now + 8)
    assert p._radar.draws == 1 and p._radar.smooth_draws == 0
    q = radar_plugin()
    q._radar = FakeRadar()
    q._t0 = now
    q.compose(now + 8)
    assert q._radar.smooth_draws == 1
