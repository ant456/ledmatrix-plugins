"""Tests for govee_fx against a fake Govee light (UDP) and a fake Home Assistant session."""
import json
import socket
import threading
import time
import unittest

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
        self.assertEqual(cmds[-2], ("colorwc", {"color": {"r": 10, "g": 20, "b": 30}, "colorTemInKelvin": 0}))
        self.assertEqual(cmds[-1], ("brightness", {"value": 42}))

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


if __name__ == "__main__":
    unittest.main()
