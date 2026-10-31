"""Tests for govee_fx against a fake Govee light (UDP) and a fake Home Assistant session."""
import json
import socket
import threading
import time
import unittest

import os
os.environ["LEDMATRIX_GOVEE_CONFIG"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "no-such-central.json")
import govee_fx as g

g.GoveeFX.time_scale = 20.0


class FakeLight:
    """Answers like a Govee light: records commands, replies to devStatus on port 4002."""

    def __init__(self, port, on=True, color=(10, 20, 30), brightness=42):
        self.port, self.cmds = port, []
        self.state = {"onOff": 1 if on else 0, "brightness": brightness,
                      "color": {"r": color[0], "g": color[1], "b": color[2]}, "colorTemInKelvin": 0}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.2)
        self.run = True
        self.t = threading.Thread(target=self.loop, daemon=True)
        self.t.start()

    def loop(self):
        while self.run:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            m = json.loads(data)["msg"]
            if m["cmd"] == "devStatus":
                reply = json.dumps({"msg": {"cmd": "devStatus", "data": self.state}}).encode()
                self.sock.sendto(reply, (addr[0], g.LISTEN_PORT))
            else:
                self.cmds.append((m["cmd"], m["data"]))
                d = m["data"]
                if m["cmd"] == "turn":
                    self.state["onOff"] = d["value"]
                elif m["cmd"] == "brightness":
                    self.state["brightness"] = d["value"]
                elif m["cmd"] == "colorwc":
                    self.state["color"] = d["color"]

    def stop(self):
        self.run = False
        self.sock.close()


class FakeResp:
    def __init__(self, j=None, code=200):
        self._j, self.status_code = j, code

    def json(self):
        return self._j


class FakeSession:
    def __init__(self, states):
        self.states, self.calls = states, []

    def get(self, url, **kw):
        ent = url.rsplit("/", 1)[1]
        return FakeResp(self.states[ent]) if ent in self.states else FakeResp(code=404)

    def post(self, url, json=None, **kw):
        self.calls.append((url.rsplit("/", 1)[1], json))
        return FakeResp({})


def wait_done(fx, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if fx._snaps is None and fx._job is None and fx._last_end:
            return True
        time.sleep(0.05)
    return False


class Colours(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(g.parse_color("#013369"), (1, 51, 105))
        self.assertEqual(g.parse_color("f80"), (255, 136, 0))
        self.assertEqual(g.parse_color("zzz"), g.WHITE)
        self.assertEqual(g.parse_color(None, (1, 2, 3)), (1, 2, 3))

    def test_vivid_brightens_navy(self):
        v = g.vivid((1, 51, 105))
        self.assertEqual(max(v), 255)
        self.assertGreater(v[2], v[0])

    def test_similar_accent_becomes_white(self):
        p, a = g.team_colors("013369", "0b2265")
        self.assertEqual(a, g.WHITE)

    def test_black_team_gets_white_light(self):
        p, a = g.team_colors("000000", "a5acaf")
        self.assertEqual(p, g.WHITE)


class Effects(unittest.TestCase):
    def test_every_effect_is_ordered_and_in_range(self):
        p, a = g.team_colors("013369", "ffb612")
        for name, fn in g.EFFECTS.items():
            steps = fn(p, a, 6.0)
            self.assertTrue(steps, name)
            ts = [t for t, _ in steps]
            self.assertEqual(ts, sorted(ts), name)
            self.assertLessEqual(ts[-1], 6.0 + 1e-6, name)
            for _, c in steps:
                self.assertTrue(all(0 <= x <= 255 for x in c), (name, c))

    def test_timeline_staggers_lights(self):
        tl = g.timeline("glow", (255, 0, 0), g.WHITE, 4.0, 3, 0.5)
        first = {i: min(t for t, j, _, _ in tl if j == i) for i in range(3)}
        self.assertAlmostEqual(first[1] - first[0], 0.5 + 0.0, delta=0.2)
        self.assertEqual(sum(1 for *_, last in tl if last), 3)

    def test_unknown_effect_falls_back(self):
        self.assertTrue(g.timeline("nope", g.WHITE, g.WHITE, 4.0, 1, 0))


class Lan(unittest.TestCase):
    def setUp(self):
        self.dev = FakeLight(14003)

    def tearDown(self):
        self.dev.stop()

    def make(self, **extra):
        cfg = {"enabled": True, "devices": ["127.0.0.1:14003"], "cooldown_seconds": 0}
        cfg.update(extra)
        return g.GoveeFX(cfg)

    def test_score_plays_then_restores_previous_state(self):
        fx = self.make()
        self.assertTrue(fx.trigger("score", "013369", "ffb612"))
        self.assertTrue(wait_done(fx), "effect did not finish")
        fx.close()
        cmds = self.dev.cmds
        self.assertEqual(cmds[0][0], "turn")
        self.assertGreater(sum(1 for c in cmds if c[0] == "colorwc"), 6)
        self.assertEqual(cmds[-3], ("colorwc", {"color": {"r": 10, "g": 20, "b": 30}, "colorTemInKelvin": 0}))
        self.assertEqual(cmds[-2], ("brightness", {"value": 42}))
        self.assertEqual(cmds[-1], ("turn", {"value": 1}))
        self.assertEqual(self.dev.state["onOff"], 1)

    def test_light_that_was_off_goes_back_off(self):
        self.dev.state["onOff"] = 0
        fx = self.make()
        fx.trigger("score", "013369", "ffb612")
        wait_done(fx)
        fx.close()
        self.assertEqual(self.dev.cmds[-1], ("turn", {"value": 0}))
        # the colour it was last set to is put back first, so it comes on the same way next time
        self.assertEqual(self.dev.cmds[-3], ("colorwc", {"color": {"r": 10, "g": 20, "b": 30}, "colorTemInKelvin": 0}))
        self.assertEqual(self.dev.cmds[-2], ("brightness", {"value": 42}))

    def test_restore_retries_when_the_light_ignores_turn_off(self):
        self.dev.state["onOff"] = 1
        dropped = {"n": 0}

        fx = self.make()
        light = fx.lights[0]
        net_send = light.net.send

        def flaky(ip, cmd, data, port=g.CONTROL_PORT):
            if cmd == "turn" and data["value"] == 0 and dropped["n"] < 2:
                dropped["n"] += 1          # the first two turn-offs are lost
                return
            net_send(ip, cmd, data, port)
        light.net.send = flaky
        try:
            light.restore({"onOff": 0, "brightness": 42, "color": {"r": 1, "g": 2, "b": 3}, "colorTemInKelvin": 0}, "off")
        finally:
            light.net.send = net_send
        fx.close()
        self.assertEqual(dropped["n"], 2)
        self.assertEqual(self.dev.state["onOff"], 0)

    def test_disabled_and_off_events_do_nothing(self):
        fx = g.GoveeFX({"enabled": False, "devices": ["127.0.0.1:14003"]})
        self.assertFalse(fx.trigger("score"))
        fx = self.make()
        self.assertFalse(fx.trigger("against"))      # default off
        self.assertFalse(fx.trigger("no_such_event"))
        fx.close()
        self.assertEqual(self.dev.cmds, [])

    def test_newer_event_preempts_and_restores_once(self):
        fx = self.make(events={"score": {"effect": "glow", "seconds": 30}})
        fx.trigger("score", "013369", "ffb612")
        time.sleep(0.4)
        fx.trigger("big_score", "ff0000", "ffffff")
        wait_done(fx, 10)
        fx.close()
        restores = [c for c in self.dev.cmds if c == ("brightness", {"value": 42})]
        self.assertEqual(len(restores), 1)

    def test_cooldown_blocks_a_second_score_but_not_a_big_one(self):
        fx = self.make(cooldown_seconds=60)
        fx.trigger("score", "013369", "ffb612")
        wait_done(fx)
        self.assertFalse(fx.trigger("score", "013369", "ffb612"))
        self.assertTrue(fx.trigger("big_score", "013369", "ffb612"))
        fx.close()

    def test_unreachable_light_never_raises(self):
        fx = g.GoveeFX({"enabled": True, "devices": ["127.0.0.1:14999"], "cooldown_seconds": 0})
        self.assertTrue(fx.trigger("score", "013369", "ffb612"))
        wait_done(fx)
        fx.close()

    def test_color_override_per_event(self):
        fx = self.make(colors={"score": {"primary": "00ff00", "accent": "00ff00"}})
        fx.trigger("score", "013369", "ffb612")
        wait_done(fx)
        fx.close()
        seen = {tuple(c[1]["color"].values()) for c in self.dev.cmds if c[0] == "colorwc"}
        self.assertIn((0, 255, 0), seen)

    def test_string_event_shorthand(self):
        fx = self.make(events={"score": "pulse"})
        self.assertEqual(fx.events["score"]["effect"], "pulse")
        self.assertEqual(fx.events["score"]["seconds"], 6)
        fx.close()


class HomeAssistant(unittest.TestCase):
    def test_ha_light_snapshot_effect_restore(self):
        sess = FakeSession({"light.bed_lights": {"state": "on", "attributes": {
            "brightness": 120, "color_mode": "rgb", "rgb_color": [9, 8, 7]}}})
        fx = g.GoveeFX({"enabled": True, "devices": ["light.bed_lights"], "ha_url": "http://ha:8123",
                        "ha_token": "x", "cooldown_seconds": 0}, session=sess)
        fx.trigger("score", "013369", "ffb612")
        self.assertTrue(wait_done(fx))
        fx.close()
        self.assertGreater(len(sess.calls), 6)
        svc, data = sess.calls[-1]
        self.assertEqual(svc, "turn_on")
        self.assertEqual(data["rgb_color"], [9, 8, 7])
        self.assertEqual(data["brightness"], 120)
        self.assertEqual(data["entity_id"], "light.bed_lights")

    def test_ha_light_that_was_off(self):
        sess = FakeSession({"light.x": {"state": "off", "attributes": {}}})
        fx = g.GoveeFX({"enabled": True, "devices": ["light.x"], "ha_url": "http://ha", "ha_token": "t",
                        "cooldown_seconds": 0}, session=sess)
        fx.trigger("score", "013369", "ffb612")
        wait_done(fx)
        fx.close()
        self.assertEqual(sess.calls[-1][0], "turn_off")

    def test_ha_without_credentials_is_skipped(self):
        fx = g.GoveeFX({"enabled": True, "devices": ["light.x"]})
        self.assertEqual(fx.lights, [])
        self.assertFalse(fx.trigger("score"))

    def test_colour_temp_restored(self):
        sess = FakeSession({"light.w": {"state": "on", "attributes": {
            "brightness": 200, "color_mode": "color_temp", "color_temp_kelvin": 3000}}})
        fx = g.GoveeFX({"enabled": True, "devices": ["light.w"], "ha_url": "http://ha", "ha_token": "t",
                        "cooldown_seconds": 0}, session=sess)
        fx.trigger("score", "013369", "ffb612")
        wait_done(fx)
        fx.close()
        self.assertEqual(sess.calls[-1][1]["color_temp_kelvin"], 3000)


class CentralConfig(unittest.TestCase):
    def setUp(self):
        import tempfile
        import pathlib
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.file = self.dir / "govee_fx.json"
        self.dev = FakeLight(14003)

    def tearDown(self):
        self.dev.stop()

    def write(self, **over):
        raw = {"enabled": True, "lights": [{"id": "127.0.0.1:14003", "label": "Desk", "enabled": True}],
               "events": {"score": {"effect": "glow", "seconds": 4}}, "cooldown_seconds": 0}
        raw.update(over)
        g.save_central(self.file, raw)

    def test_valid_light_ids(self):
        for ok in ("192.168.0.5", "10.0.0.2:4003", "172.16.4.4", "light.tv_backlight"):
            self.assertTrue(g.valid_light_id(ok), ok)
        for bad in ("8.8.8.8", "169.254.1.1", "light.", "light.A B", "", None, "192.168.0.5; rm", "http://x"):
            self.assertFalse(g.valid_light_id(bad), bad)

    def test_clean_config_fills_defaults_and_drops_junk(self):
        c = g.clean_config({"lights": [{"id": "8.8.8.8"}, {"id": "192.168.0.9", "label": "x" * 99},
                                      {"id": "192.168.0.9"}, "nope"],
                            "events": {"score": {"effect": "rm -rf", "seconds": 999, "colors": {"primary": "zz"}}},
                            "sports": {"nfl": False, "BAD KEY": True}, "cooldown_seconds": "abc", "restore_fallback": "x"})
        self.assertEqual([l["id"] for l in c["lights"]], ["192.168.0.9"])
        self.assertEqual(len(c["lights"][0]["label"]), 40)
        self.assertEqual(c["events"]["score"]["effect"], "burst")          # unknown effect ignored
        self.assertEqual(c["events"]["score"]["seconds"], 30.0)            # clamped
        self.assertEqual(c["events"]["score"]["colors"], "team")
        self.assertFalse(c["sports"]["nfl"])
        self.assertNotIn("BAD KEY", c["sports"])
        self.assertEqual(c["cooldown_seconds"], g.MIN_GAP)
        self.assertEqual(c["restore_fallback"], "off")
        self.assertFalse(c["enabled"])
        self.assertFalse(c["events"]["against"]["enabled"])

    def test_clean_config_is_idempotent(self):
        c = g.clean_config({"lights": [{"id": "192.168.0.9"}], "events": {"score": {"colors": {"primary": "#AABBCC"}}}})
        self.assertEqual(g.clean_config(c), c)
        self.assertEqual(c["events"]["score"]["colors"], {"primary": "aabbcc", "accent": "ffffff"})

    def test_central_to_cfg_applies_sport_override_and_disabled_flags(self):
        raw = {"enabled": True, "lights": [{"id": "192.168.0.9", "enabled": True}, {"id": "192.168.0.10", "enabled": False}],
               "sports": {"nba": False},
               "events": {"score": {"effect": "burst"}, "against": {"enabled": False}},
               "sport_events": {"nfl": {"score": {"effect": "chase", "seconds": 7}}}}
        nfl = g.central_to_cfg(raw, "nfl")
        self.assertEqual(nfl["devices"], ["192.168.0.9"])                  # the switched-off light is left out
        self.assertEqual(nfl["events"]["score"], {"effect": "chase", "seconds": 7.0})
        self.assertEqual(nfl["events"]["against"]["effect"], "off")
        nhl = g.central_to_cfg(raw, "nhl")
        self.assertEqual(nhl["events"]["score"]["effect"], "burst")
        self.assertFalse(g.central_to_cfg(raw, "nba")["enabled"])          # that sport is switched off

    def test_ha_credentials_come_from_the_ledmatrix_config(self):
        cfg = self.dir / "config.json"
        cfg.write_text(json.dumps({"x": {"a": 1}, "house-hud": {"ha_url": "http://ha", "ha_token": "T"}}))
        self.assertEqual(g.ha_credentials(cfg), ("http://ha", "T"))
        self.assertEqual(g.ha_credentials(self.dir / "missing.json"), ("", ""))
        out = g.central_to_cfg({"enabled": True}, "nfl", cfg)
        self.assertEqual((out["ha_url"], out["ha_token"]), ("http://ha", "T"))
        self.write()
        self.assertNotIn("ha_token", self.file.read_text())                # the token is never copied into the file

    def test_save_is_atomic_and_keeps_a_backup(self):
        self.write()
        first = self.file.read_text()
        self.write(cooldown_seconds=5)
        self.assertTrue((self.dir / "govee_fx.json.bak").exists())
        self.assertEqual((self.dir / "govee_fx.json.bak").read_text(), first)
        self.assertFalse((self.dir / "govee_fx.tmp").exists())

    def test_plugin_uses_the_central_file_and_follows_edits(self):
        self.write()
        fx = g.GoveeFX({"enabled": False}, central=self.file, sport="nfl")
        self.assertTrue(fx.enabled)
        self.assertEqual(len(fx.lights), 1)
        self.assertEqual(fx.events["score"]["effect"], "glow")
        self.write(events={"score": {"effect": "pulse"}})
        os.utime(self.file, (time.time() + 5, time.time() + 5))             # coarse file clocks
        fx.trigger("score", "013369", "ffb612")                             # sees the new file
        self.assertEqual(fx.events["score"]["effect"], "pulse")
        wait_done(fx)
        fx.close()

    def test_switched_off_sport_never_fires(self):
        self.write(sports={"nhl": False})
        fx = g.GoveeFX(None, central=self.file, sport="nhl")
        self.assertFalse(fx.trigger("score", "013369", "ffb612"))
        fx.close()
        self.assertEqual(self.dev.cmds, [])

    def test_custom_colours_are_used_exactly(self):
        self.write(events={"score": {"effect": "glow", "colors": {"primary": "123456", "accent": "654321"}}})
        fx = g.GoveeFX(None, central=self.file, sport="nfl")
        fx.trigger("score", "ff0000", "00ff00")
        wait_done(fx)
        fx.close()
        seen = {tuple(c[1]["color"].values()) for c in self.dev.cmds if c[0] == "colorwc"}
        self.assertIn((0x12, 0x34, 0x56), seen)
        self.assertNotIn((255, 0, 0), seen)

    def test_central_file_removed_falls_back_to_the_plugin_block(self):
        self.write()
        fx = g.GoveeFX({"enabled": False}, central=self.file, sport="nfl")
        self.assertTrue(fx.enabled)
        self.file.unlink()
        fx.trigger("score")
        self.assertFalse(fx.enabled)
        fx.close()

    def test_broken_central_file_does_not_crash(self):
        self.file.write_text("{ not json")
        fx = g.GoveeFX({"enabled": False}, central=self.file, sport="nfl")
        self.assertFalse(fx.trigger("score"))
        fx.close()

    def test_two_plugins_take_turns_instead_of_fighting_over_the_lights(self):
        self.write(events={"score": {"effect": "glow", "seconds": 4}})
        a = g.GoveeFX(None, central=self.file, sport="nfl")
        b = g.GoveeFX(None, central=self.file, sport="nba")
        a.trigger("score", "013369", "ffb612")
        time.sleep(0.2)
        b.trigger("score", "ff0000", "ffffff")
        self.assertTrue(a.wait(10) and b.wait(10))
        a.close()
        b.close()
        self.assertEqual(self.dev.state["onOff"], 1)                        # restored to how the fake started

    def test_preview_matches_the_effect(self):
        pv = g.preview("burst", "013369", "ffb612", 6)
        self.assertEqual(pv["seconds"], 6.0)
        self.assertGreater(len(pv["steps"]), 5)
        self.assertEqual(pv["steps"][0][1], [[255, 255, 255]])
        self.assertEqual(g.preview("nope", "013369", "ffb612", 99)["seconds"], 30.0)

    def test_every_picker_effect_has_a_description(self):
        self.assertEqual(set(g.PICKABLE), set(g.EFFECTS) - {"identify"})

    def test_light_status_reports_reachable_and_not(self):
        st = g.light_status(["127.0.0.1:14003", "127.0.0.2:14999"])
        self.assertTrue(st["127.0.0.1:14003"]["ok"])
        self.assertFalse(st["127.0.0.2:14999"]["ok"])

    def test_list_ha_lights_hides_segments_and_filters_to_govee(self):
        class R:
            def __init__(self, code, body, j=None):
                self.status_code, self.text, self._j = code, body, j

            def json(self):
                return self._j

        class S:
            def post(self, url, **kw):
                return R(200, json.dumps(["light.bed", "light.bed_segments", "light.tv"]))

            def get(self, url, **kw):
                return R(200, "", [{"entity_id": "light.bed", "state": "on", "attributes": {"friendly_name": "Bed"}},
                                   {"entity_id": "light.bed_segments", "state": "on", "attributes": {}},
                                   {"entity_id": "light.hue", "state": "on", "attributes": {}},
                                   {"entity_id": "light.tv_segment_3", "state": "on", "attributes": {}},
                                   {"entity_id": "light.tv", "state": "off", "attributes": {"friendly_name": "TV"}},
                                   {"entity_id": "sensor.x", "state": "1", "attributes": {}}])

        got = g.list_ha_lights("http://ha", "T", True, S())
        self.assertEqual([x["id"] for x in got], ["light.bed", "light.tv"])
        self.assertEqual([x["id"] for x in g.list_ha_lights("http://ha", "T", False, S())], ["light.bed", "light.hue", "light.tv"])


class Segments(unittest.TestCase):
    def test_segment_packet_layout_and_checksum(self):
        import base64
        raw = base64.b64decode(g.segment_packet((10, 20, 30), 0b101))
        self.assertEqual(len(raw), 20)
        self.assertEqual(list(raw[:7]), [0x33, 0x05, 0x15, 0x01, 10, 20, 30])
        self.assertEqual((raw[12], raw[13]), (0b101, 0))
        chk = 0
        for b in raw[:19]:
            chk ^= b
        self.assertEqual(raw[19], chk)
        hi = base64.b64decode(g.segment_packet((1, 2, 3), 1 << 12))     # segment 13 lives in the second mask byte
        self.assertEqual((hi[12], hi[13]), (0, 0x10))

    def test_group_segments_merges_same_colours(self):
        grp = g.group_segments([(1, 1, 1), (2, 2, 2), (1, 1, 1), (0, 0, 0)])
        self.assertEqual(grp, {(1, 1, 1): 0b0101, (2, 2, 2): 0b0010, (0, 0, 0): 0b1000})

    def test_one_segment_plays_the_plain_effect(self):
        p, a = g.team_colors("013369", "ffb612")
        for name in g.PICKABLE:
            plain = g.EFFECTS[name](p, a, 6.0)
            fr = g.frames(name, p, a, 6.0, 1)
            self.assertEqual([(t, c[0]) for t, c in fr], plain, name)

    def test_every_segment_effect_is_ordered_in_range_and_the_right_width(self):
        p, a = g.team_colors("013369", "ffb612")
        for n in (2, 5, 8, 13, 16):
            for name in g.PICKABLE:
                fr = g.frames(name, p, a, 8.0, n)
                ts = [t for t, _ in fr]
                self.assertEqual(ts, sorted(ts), (name, n))
                self.assertLessEqual(ts[-1], 8.0 + 1e-6, (name, n))
                for _, cols in fr:
                    self.assertEqual(len(cols), n, (name, n))
                    for c in cols:
                        self.assertTrue(all(0 <= x <= 255 for x in c), (name, n, c))

    def test_effects_really_use_the_segments(self):
        p, a = g.team_colors("013369", "ffb612")
        for name in ("burst", "rocket", "chase", "fireworks", "pulse", "strobe", "glow"):
            fr = g.frames(name, p, a, 8.0, 8)
            self.assertTrue(any(len(set(cols)) > 1 for _, cols in fr), name + " never varies along the light")

    def test_chase_comet_travels_along_the_light(self):
        p, a = (255, 0, 0), (0, 0, 255)
        fr = g.frames("chase", p, a, 8.0, 10)
        heads = []
        for _, cols in fr[:8]:
            lum = [sum(c) for c in cols]
            heads.append(lum.index(max(lum)))
        self.assertEqual(heads, sorted(heads))                 # moving one way
        self.assertGreater(heads[-1], heads[0])

    def test_glow_fills_from_one_end_and_ends_full(self):
        p = (255, 0, 0)
        fr = g.frames("glow", p, g.WHITE, 6.0, 6)
        early = fr[len(fr) // 12][1]
        self.assertGreater(sum(early[0]), sum(early[-1]))
        full = [cols for t, cols in fr if abs(t - 1.2) < 1e-6][-1]
        self.assertTrue(all(c == p for c in full))

    def test_timeline_gives_lists_only_to_segmented_lights(self):
        tl = g.timeline("burst", (255, 0, 0), g.WHITE, 4.0, 2, 0.1, [1, 6])
        self.assertTrue(all(isinstance(c, tuple) for t, i, c, last in tl if i == 0))
        self.assertTrue(all(isinstance(c, list) and len(c) == 6 for t, i, c, last in tl if i == 1))
        self.assertEqual(sum(1 for *_, last in tl if last), 2)

    def test_clean_config_keeps_and_clamps_segments(self):
        c = g.clean_config({"lights": [{"id": "192.168.0.9", "segments": 99}, {"id": "192.168.0.10", "segments": "x"},
                                      {"id": "192.168.0.11", "segments": 7}, {"id": "192.168.0.12"}]})
        self.assertEqual([l["segments"] for l in c["lights"]], [16, 1, 7, 1])
        cfg = g.central_to_cfg({"enabled": True, "lights": [{"id": "192.168.0.11", "segments": 7}]}, "nfl")
        self.assertEqual(cfg["segments"], {"192.168.0.11": 7})

    def test_preview_carries_every_segment(self):
        pv = g.preview("chase", "013369", "ffb612", 5, False, 6)
        self.assertEqual(pv["segments"], 6)
        self.assertTrue(all(len(cols) == 6 for _, cols in pv["steps"]))
        self.assertEqual(g.preview("burst", "013369", "ffb612", 5)["segments"], 1)


class SegmentsOnLan(unittest.TestCase):
    def setUp(self):
        self.dev = FakeLight(14003)

    def tearDown(self):
        self.dev.stop()

    def test_segmented_light_gets_ptreal_packets_then_is_restored(self):
        import base64
        fx = g.GoveeFX({"enabled": True, "devices": ["127.0.0.1:14003"], "segments": {"127.0.0.1:14003": 8},
                        "events": {"score": {"effect": "chase", "seconds": 4}}, "cooldown_seconds": 0}, central=False)
        self.assertEqual(fx.lights[0].segments, 8)
        fx.trigger("score", "013369", "ffb612")
        self.assertTrue(wait_done(fx))
        fx.close()
        pt = [c for c in self.dev.cmds if c[0] == "ptReal"]
        self.assertGreater(len(pt), 6)
        for _, data in pt:
            for pkt in data["command"]:
                raw = base64.b64decode(pkt)
                self.assertEqual((len(raw), raw[0], raw[2]), (20, 0x33, 0x15))
        self.assertEqual(self.dev.cmds[-3], ("colorwc", {"color": {"r": 10, "g": 20, "b": 30}, "colorTemInKelvin": 0}))
        self.assertEqual(self.dev.cmds[-2], ("brightness", {"value": 42}))
        self.assertEqual(self.dev.state["onOff"], 1)

    def test_a_frame_with_one_colour_uses_the_plain_command(self):
        light = g.LanLight("127.0.0.1:14003", 6)
        light.show_segments([(5, 6, 7)] * 6)
        time.sleep(0.3)
        self.assertEqual(self.dev.cmds[-1], ("colorwc", {"color": {"r": 5, "g": 6, "b": 7}, "colorTemInKelvin": 0}))

    def test_a_single_datagram_carries_every_colour_group(self):
        light = g.LanLight("127.0.0.1:14003", 6)
        light.show_segments([(255, 0, 0), (0, 0, 255), (255, 0, 0), (0, 0, 0), (0, 0, 255), (255, 0, 0)])
        time.sleep(0.3)
        cmd, data = self.dev.cmds[-1]
        self.assertEqual(cmd, "ptReal")
        self.assertEqual(len(data["command"]), 3)


class SegmentsOnHomeAssistant(unittest.TestCase):
    def make(self, states):
        sess = FakeSession(states)
        light = g.HaLight("light.dj", "http://ha", "T", sess, 3)
        return light, sess

    def test_segmented_ha_light_drives_its_numbered_entities(self):
        light, sess = self.make({})
        light.show_segments([(1, 1, 1), (2, 2, 2), (1, 1, 1)])
        got = {tuple(d["rgb_color"]): d["entity_id"] for _, d in sess.calls}
        self.assertEqual(got[(1, 1, 1)], ["light.dj_segment_1", "light.dj_segment_3"])
        self.assertEqual(got[(2, 2, 2)], ["light.dj_segment_2"])

    def test_plain_show_on_a_segmented_light_never_touches_the_parent(self):
        light, sess = self.make({})
        light.show((9, 9, 9))
        self.assertTrue(all("light.dj_segment_" in str(d["entity_id"]) and d["entity_id"] != "light.dj" for _, d in sess.calls))

    def test_snapshot_and_restore_go_part_by_part(self):
        states = {"light.dj_segment_%d" % i: {"state": "on", "attributes": {"brightness": 50 + i, "rgb_color": [i, i, i], "color_mode": "rgb"}}
                  for i in (1, 2, 3)}
        states["light.dj_segment_3"]["state"] = "off"
        light, sess = self.make(states)
        snap = light.snapshot()
        self.assertEqual(len(snap["parts"]), 3)
        light.restore(snap, "off")
        by_ent = {d["entity_id"]: (svc, d) for svc, d in sess.calls}
        self.assertEqual(by_ent["light.dj_segment_1"][1]["rgb_color"], [1, 1, 1])
        self.assertEqual(by_ent["light.dj_segment_3"][0], "turn_off")

    def test_restore_brings_back_the_scene_the_light_was_playing(self):
        sess = FakeSession({"light.x": {"state": "on", "attributes": {"brightness": 90, "rgb_color": [1, 2, 3], "color_mode": "rgb", "effect": "Aurora"}}})
        light = g.HaLight("light.x", "http://ha", "T", sess)
        light.restore(light.snapshot(), "off")
        self.assertEqual(sess.calls[-1][1]["effect"], "Aurora")

    def test_list_ha_lights_reports_segment_counts(self):
        class R:
            def __init__(self, code, body, j=None):
                self.status_code, self.text, self._j = code, body, j

            def json(self):
                return self._j

        class S:
            def post(self, url, **kw):
                return R(404, "")

            def get(self, url, **kw):
                ents = ["light.dj"] + ["light.dj_segment_%d" % i for i in range(1, 13)] + ["light.plain", "light.bed", "light.bed_segments"]
                return R(200, "", [{"entity_id": e, "state": "on", "attributes": {}} for e in ents])

        got = {x["id"]: x["segments"] for x in g.list_ha_lights("http://ha", "T", False, S())}
        self.assertEqual(got, {"light.dj": 12, "light.plain": 1, "light.bed": 1})


class Rocket(unittest.TestCase):
    P, A = (255, 0, 0), (0, 0, 255)

    def test_the_rocket_climbs_from_the_bottom_to_the_top(self):
        n = 10
        fr = g.frames("rocket", self.P, self.A, 8.0, n)
        first_climb = []
        for _, cols in fr[:n]:                                   # the first pass up the light
            lum = [sum(c) for c in cols]
            first_climb.append(lum.index(max(lum)))
        self.assertEqual(first_climb, sorted(first_climb))
        self.assertEqual(first_climb[0], 0)
        self.assertEqual(first_climb[-1], n - 1)

    def test_it_accelerates(self):
        n = 12
        fr = g.frames("rocket", self.P, self.A, 8.0, n)
        heads = []
        for _, cols in fr[:int(n * 1.2)]:
            lum = [sum(c) for c in cols]
            heads.append(lum.index(max(lum)))
        steps = [b - a for a, b in zip(heads, heads[1:]) if b >= a]
        self.assertLessEqual(sum(steps[:3]), sum(steps[-3:]))

    def test_it_bursts_white_at_the_top_then_sparkles_downward(self):
        n = 10
        fr = g.frames("rocket", self.P, self.A, 8.0, n)
        burst = next(cols for _, cols in fr if g.WHITE in cols)
        self.assertEqual(burst[-1], g.WHITE)                      # the top is lit white
        self.assertEqual(burst[0], g.BLACK)                       # the bottom is not, yet
        later = [cols for _, cols in fr[n + 5:n + 18]]
        self.assertTrue(any(cols[n // 2] != g.BLACK for cols in later))

    def test_a_long_effect_fires_more_than_one_rocket(self):
        fr = g.frames("rocket", self.P, self.A, 14.0, 8)
        whites = sum(1 for _, cols in fr if cols[-1] == g.WHITE and cols[0] == g.BLACK)
        self.assertGreater(whites, 3)
        colours = {c for _, cols in fr for c in cols}
        self.assertIn(self.A, colours)                            # the second rocket is the second colour

    def test_a_light_without_sections_swells_pops_and_fades(self):
        st = g.fx_rocket(self.P, self.A, 8.0)
        self.assertIn(g.WHITE, [c for _, c in st])
        ts = [t for t, _ in st]
        self.assertEqual(ts, sorted(ts))
        self.assertLessEqual(ts[-1], 8.0 + 1e-6)

    def test_it_is_offered_in_the_picker(self):
        self.assertIn("rocket", g.PICKABLE)
        self.assertIn("rocket", g.SEG_EFFECTS)


class Reverse(unittest.TestCase):
    def setUp(self):
        self.dev = FakeLight(14003)

    def tearDown(self):
        self.dev.stop()

    def first_mask(self, flip):
        import base64
        cfg = {"enabled": True, "devices": ["127.0.0.1:14003"], "segments": {"127.0.0.1:14003": 8},
               "flips": {"127.0.0.1:14003": True} if flip else {}, "events": {"score": {"effect": "rocket", "seconds": 8}},
               "cooldown_seconds": 0}
        fx = g.GoveeFX(cfg, central=False)
        fx.trigger("score", "013369", "ffb612")
        wait_done(fx)
        fx.close()
        for cmd, data in self.dev.cmds:
            if cmd == "ptReal":
                raw = base64.b64decode(data["command"][0])
                return raw[12] | raw[13] << 8
        self.fail("no segment packet")

    def test_reverse_flips_which_end_the_rocket_starts_from(self):
        normal = self.first_mask(False)
        self.dev.cmds.clear()
        flipped = self.first_mask(True)
        self.assertTrue(normal & 1 or normal & 0b11)              # starts at section 1
        self.assertTrue(flipped & 0x80 or flipped & 0xC0)         # starts at the last section

    def test_config_round_trip(self):
        c = g.clean_config({"lights": [{"id": "192.168.0.9", "segments": 8, "flip": True}, {"id": "192.168.0.10"}]})
        self.assertEqual([l["flip"] for l in c["lights"]], [True, False])
        cfg = g.central_to_cfg({"enabled": True, "lights": c["lights"]}, "nfl")
        self.assertEqual(cfg["flips"], {"192.168.0.9": True})
        fx = g.GoveeFX(None, central=False)
        fx._apply(cfg)
        self.assertTrue(fx.lights[0].flip)
        self.assertFalse(getattr(fx.lights[1], "flip", False))
        fx.close()


class SharedStateAndActivity(unittest.TestCase):
    def setUp(self):
        self.dev = FakeLight(14003)

    def tearDown(self):
        self.dev.stop()

    def test_an_isolated_second_copy_shares_the_listener_the_lock_and_the_log(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("govee_fx_second_copy", g.__file__)
        other = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(other)                       # what the plugin loader does for each plugin
        self.assertIsNot(other, g)
        self.assertIs(other.LanTransport.get(), g.LanTransport.get())      # one socket on the reply port, not several
        self.assertIs(other._PLAY_LOCK, g._PLAY_LOCK)                      # one effect at a time across plugins
        g.record("nfl", "score", "PHI shared-test", "burst", "playing")
        self.assertEqual(other.activity(1)[0]["label"], "PHI shared-test")

    def make(self, **extra):
        cfg = {"enabled": True, "devices": ["127.0.0.1:14003"], "cooldown_seconds": 0,
               "events": {"score": {"effect": "glow", "seconds": 4}}}
        cfg.update(extra)
        return g.GoveeFX(cfg, central=False, sport="nba")

    def last(self):
        return g.activity(1)[0]

    def test_a_played_effect_is_logged_playing_then_done(self):
        fx = self.make()
        fx.trigger("score", "013369", "ffb612", label="PHI +3")
        e = self.last()
        self.assertEqual((e["sport"], e["event"], e["label"], e["status"]), ("NBA", "score", "PHI +3", "playing"))
        self.assertTrue(wait_done(fx))
        self.assertEqual(self.last()["status"], "done")
        fx.close()

    def test_every_reason_for_not_flashing_is_logged(self):
        off = g.GoveeFX({"enabled": False, "devices": ["127.0.0.1:14003"]}, central=False, sport="nhl")
        off.trigger("score", label="TOR goal")
        self.assertEqual((self.last()["status"], "switched off" in self.last()["detail"]), ("skipped", True))

        nolights = g.GoveeFX({"enabled": True, "devices": []}, central=False, sport="nhl")
        nolights.trigger("score", label="x")
        self.assertIn("no light", self.last()["detail"])

        fx = self.make()
        fx.trigger("against", label="y")                    # off by default
        self.assertIn("switched off", self.last()["detail"])
        fx.close()

        cool = self.make(cooldown_seconds=60)
        cool.trigger("score", "013369", "ffb612", label="a")
        wait_done(cool)
        self.assertFalse(cool.trigger("score", "013369", "ffb612", label="b"))
        self.assertIn("cooldown", self.last()["detail"])
        cool.close()

    def test_a_newer_effect_marks_the_older_one_interrupted(self):
        fx = self.make(events={"score": {"effect": "glow", "seconds": 30}})
        fx.trigger("score", "013369", "ffb612", label="first")
        time.sleep(0.3)
        fx.trigger("big_score", "ff0000", "ffffff", label="second")
        wait_done(fx, 10)
        fx.close()
        first = next(e for e in g.activity(10) if e["label"] == "first")
        self.assertEqual(first["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
