"""Portal logic and web layer, against a fake Govee light and a fake Home Assistant / ESPN."""
import json
import os
import pathlib
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
os.environ["LEDMATRIX_GOVEE_CONFIG"] = str(HERE / "no-such-central.json")

import govee_fx as g                      # noqa: E402
import portal as P                        # noqa: E402
from test_govee_fx import FakeLight       # noqa: E402

g.GoveeFX.time_scale = 20.0
TOKEN = "SECRET-TOKEN-VALUE"


class Resp:
    def __init__(self, j=None, text="", code=200):
        self._j, self.text, self.status_code = j, text, code

    def json(self):
        return self._j


class FakeNet:
    """Stands in for Home Assistant and ESPN."""

    def __init__(self):
        self.fail = False

    def post(self, url, **kw):
        return Resp(text=json.dumps(["light.bed", "light.tv"]))

    def get(self, url, **kw):
        if self.fail:
            raise OSError("down")
        if "espn" in url:
            return Resp({"sports": [{"leagues": [{"teams": [
                {"team": {"abbreviation": "PHI", "displayName": "Philadelphia Eagles", "color": "004c54", "alternateColor": "a5acaf"}},
                {"team": {"abbreviation": "DAL", "displayName": "Dallas Cowboys", "color": "003594"}}]}]}]})
        if url.endswith("/api/states"):
            return Resp([{"entity_id": "light.bed", "state": "on", "attributes": {"friendly_name": "Bed"}},
                         {"entity_id": "light.tv", "state": "off", "attributes": {"friendly_name": "TV"}},
                         {"entity_id": "light.hue", "state": "on", "attributes": {}}])
        return Resp({"state": "on", "attributes": {"brightness": 100, "rgb_color": [1, 2, 3], "color_mode": "rgb"}})


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        (self.dir / "config.json").write_text(json.dumps({"house-hud": {"ha_url": "http://ha:8123", "ha_token": TOKEN}}))
        self.net = FakeNet()
        self.portal = P.Portal(self.dir / "govee_fx.json", self.dir / "config.json", session=self.net)
        self.app = P.create_app(self.portal, HERE / "static")
        self.c = self.app.test_client()
        self.dev = FakeLight(14003)

    def tearDown(self):
        self.dev.stop()

    def wait_test(self, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if not self.portal.test_state()["running"] and self.portal.test_state()["started"]:
                return self.portal.test_state()
            time.sleep(0.05)
        self.fail("test did not finish")


class State(Base):
    def test_fresh_state_has_everything_the_page_needs_and_no_secrets(self):
        r = self.c.get("/api/state")
        j = r.get_json()
        self.assertFalse(j["saved"])
        self.assertEqual([e["key"] for e in j["events"]], ["score", "big_score", "against"])
        self.assertEqual([s["key"] for s in j["sports"]], ["nfl", "nba", "nhl"])
        self.assertEqual({e["key"] for e in j["effects"]}, set(g.PICKABLE))
        self.assertTrue(j["ha"]["available"])
        self.assertNotIn(TOKEN, r.get_data(as_text=True))

    def test_index_serves_the_page(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("GOVEE LIGHTS", r.get_data(as_text=True))
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_save_validates_and_persists(self):
        r = self.c.post("/api/config", json={"enabled": True, "lights": [
            {"id": "192.168.0.5", "label": "Desk"}, {"id": "8.8.8.8"}, {"id": "light.tv"}],
            "events": {"score": {"effect": "chase", "seconds": 8}}})
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual([l["id"] for l in j["config"]["lights"]], ["192.168.0.5", "light.tv"])
        on_disk = json.loads((self.dir / "govee_fx.json").read_text())
        self.assertEqual(on_disk["events"]["score"]["effect"], "chase")
        self.assertTrue(self.c.get("/api/state").get_json()["saved"])
        self.assertNotIn(TOKEN, (self.dir / "govee_fx.json").read_text())

    def test_garbage_bodies_are_survivable(self):
        self.assertEqual(self.c.post("/api/config", data="not json", content_type="application/json").status_code, 200)
        self.assertEqual(self.c.post("/api/config", json=[1, 2, 3]).status_code, 200)
        self.assertEqual(self.c.post("/api/config", data="x" * 70000, content_type="application/json").status_code, 413)

    def test_a_saved_file_feeds_a_sports_plugin(self):
        self.c.post("/api/config", json={"enabled": True, "lights": [{"id": "127.0.0.1:14003"}],
                                         "events": {"score": {"effect": "glow", "seconds": 4}}, "cooldown_seconds": 0})
        fx = g.GoveeFX(None, central=self.dir / "govee_fx.json", sport="nfl")
        self.assertTrue(fx.trigger("score", "013369", "ffb612"))
        self.assertTrue(fx.wait(10))
        fx.close()
        self.assertTrue(any(c[0] == "colorwc" for c in self.dev.cmds))


class Finding(Base):
    def test_scan_runs_in_the_background_and_reports(self):
        orig = g.discover_lan
        g.discover_lan = lambda t=8.0, iface=None: [{"id": "192.168.0.172", "model": "H6046"}]
        try:
            self.assertEqual(self.c.post("/api/scan").status_code, 200)
            end = time.time() + 5
            while time.time() < end and self.c.get("/api/scan").get_json()["running"]:
                time.sleep(0.05)
            j = self.c.get("/api/scan").get_json()
        finally:
            g.discover_lan = orig
        self.assertEqual(j["found"], [{"id": "192.168.0.172", "model": "H6046"}])
        self.assertTrue(j["done"])

    def test_ha_lights_list(self):
        j = self.c.get("/api/ha/lights").get_json()
        self.assertEqual([l["id"] for l in j["lights"]], ["light.bed", "light.tv"])
        self.assertEqual(len(self.c.get("/api/ha/lights?all=1").get_json()["lights"]), 3)

    def test_ha_unreachable_gives_a_message_not_a_500(self):
        self.net.fail = True
        r = self.c.get("/api/ha/lights?all=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Could not reach", r.get_json()["error"])

    def test_ha_not_configured(self):
        (self.dir / "config.json").write_text("{}")
        j = self.c.get("/api/ha/lights").get_json()
        self.assertFalse(j["available"])
        self.assertFalse(self.c.get("/api/state").get_json()["ha"]["available"])

    def test_status_ignores_bad_ids_and_marks_dead_lights(self):
        j = self.c.post("/api/status", json={"ids": ["127.0.0.1:14003", "127.0.0.2:14999", "8.8.8.8", "light.bed"]}).get_json()
        self.assertTrue(j["127.0.0.1:14003"]["ok"])
        self.assertFalse(j["127.0.0.2:14999"]["ok"])
        self.assertNotIn("8.8.8.8", j)
        self.assertTrue(j["light.bed"]["ok"])

    def test_teams_come_from_espn_and_are_cached(self):
        j = self.c.get("/api/teams?sport=nfl").get_json()
        self.assertEqual([t["abbr"] for t in j["teams"]], ["DAL", "PHI"])
        self.assertEqual(j["teams"][0]["accent"], "ffffff")                  # missing alternate colour gets a default
        self.net.fail = True
        self.assertEqual(len(self.c.get("/api/teams?sport=nfl").get_json()["teams"]), 2)   # served from cache
        self.assertEqual(self.c.get("/api/teams?sport=curling").get_json()["teams"], [])
        self.assertIn("Could not load", self.c.get("/api/teams?sport=nba").get_json()["error"])

    def test_previews_cover_every_effect(self):
        j = self.c.post("/api/previews", json={"primary": "013369", "accent": "ffb612"}).get_json()
        self.assertEqual(set(j), set(g.PICKABLE))
        self.assertTrue(all(v["steps"] for v in j.values()))


class Playing(Base):
    def test_test_plays_what_the_form_says_and_restores_the_light(self):
        r = self.c.post("/api/test", json={"lights": ["127.0.0.1:14003"], "team": {"primary": "004c54", "accent": "a5acaf"},
                                           "spec": {"enabled": True, "effect": "glow", "seconds": 4, "colors": "team"}})
        self.assertEqual(r.status_code, 200)
        st = self.wait_test()
        self.assertTrue(st["ok"], st)
        self.assertIn("back how they were", st["message"])
        self.assertGreater(sum(1 for c in self.dev.cmds if c[0] == "colorwc"), 4)
        self.assertEqual(self.dev.state["onOff"], 1)
        self.assertEqual(self.dev.state["brightness"], 42)
        self.assertEqual(self.dev.state["color"], {"r": 10, "g": 20, "b": 30})

    def test_custom_colours_are_what_plays(self):
        self.c.post("/api/test", json={"lights": ["127.0.0.1:14003"], "team": {"primary": "ff0000", "accent": "00ff00"},
                                       "spec": {"effect": "glow", "seconds": 4, "colors": {"primary": "123456", "accent": "654321"}}})
        self.wait_test()
        seen = {tuple(c[1]["color"].values()) for c in self.dev.cmds if c[0] == "colorwc"}
        self.assertIn((0x12, 0x34, 0x56), seen)
        self.assertNotIn((255, 0, 0), seen)

    def test_no_lights_is_a_clear_error(self):
        r = self.c.post("/api/test", json={"lights": [], "spec": {}})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Pick at least one", r.get_json()["error"])

    def test_bad_light_ids_are_dropped_not_sent(self):
        r = self.c.post("/api/test", json={"lights": ["8.8.8.8", "http://evil"], "spec": {}})
        self.assertEqual(r.status_code, 409)

    def test_second_test_while_one_is_playing_is_refused(self):
        self.c.post("/api/test", json={"lights": ["127.0.0.1:14003"], "spec": {"effect": "glow", "seconds": 30}})
        time.sleep(0.2)
        r = self.c.post("/api/test", json={"lights": ["127.0.0.1:14003"], "spec": {"effect": "glow", "seconds": 4}})
        self.assertEqual(r.status_code, 409)
        self.wait_test(15)

    def test_unreachable_light_finishes_instead_of_hanging(self):
        self.c.post("/api/test", json={"lights": ["127.0.0.2:14999"], "spec": {"effect": "burst", "seconds": 2}})
        st = self.wait_test()
        self.assertFalse(st["running"])

    def test_identify_blinks_then_restores(self):
        self.assertEqual(self.c.post("/api/identify", json={"id": "127.0.0.1:14003"}).status_code, 200)
        self.wait_test()
        whites = [c for c in self.dev.cmds if c[0] == "colorwc" and c[1]["color"] == {"r": 255, "g": 255, "b": 255}]
        self.assertGreaterEqual(len(whites), 3)
        self.assertEqual(self.dev.state["color"], {"r": 10, "g": 20, "b": 30})

    def test_identify_rejects_what_is_not_a_light(self):
        self.assertEqual(self.c.post("/api/identify", json={"id": "8.8.8.8"}).status_code, 409)
        self.assertEqual(self.c.post("/api/identify", json={}).status_code, 409)

    def test_ha_light_test_uses_server_side_credentials(self):
        calls = []
        net = self.net
        net.post = lambda url, **kw: (calls.append((url, kw.get("headers", {}).get("Authorization"))) or Resp({}))
        self.c.post("/api/test", json={"lights": ["light.bed"], "spec": {"effect": "glow", "seconds": 4}})
        self.wait_test()
        self.assertTrue(calls)
        self.assertTrue(all(a == "Bearer " + TOKEN for _, a in calls))


class SegmentTests(Base):
    def test_segments_survive_save_and_reach_the_sports_plugins(self):
        r = self.c.post("/api/config", json={"enabled": True, "lights": [{"id": "127.0.0.1:14003", "segments": 8}],
                                             "events": {"score": {"effect": "chase", "seconds": 4}}, "cooldown_seconds": 0})
        self.assertEqual(r.get_json()["config"]["lights"][0]["segments"], 8)
        fx = g.GoveeFX(None, central=self.dir / "govee_fx.json", sport="nhl")
        self.assertEqual(fx.lights[0].segments, 8)
        fx.trigger("score", "013369", "ffb612")
        self.assertTrue(fx.wait(10))
        fx.close()
        self.assertTrue(any(c[0] == "ptReal" for c in self.dev.cmds))

    def test_test_button_uses_the_section_counts_on_the_page(self):
        self.c.post("/api/test", json={"lights": ["127.0.0.1:14003"], "segments": {"127.0.0.1:14003": 6},
                                       "spec": {"effect": "chase", "seconds": 4}})
        self.wait_test()
        self.assertTrue(any(c[0] == "ptReal" for c in self.dev.cmds))
        self.assertEqual(self.dev.state["onOff"], 1)

    def test_sweep_lights_each_section_in_turn_then_restores(self):
        import base64
        self.assertEqual(self.c.post("/api/segments", json={"id": "127.0.0.1:14003", "count": 5}).status_code, 200)
        st = self.wait_test(20)
        self.assertTrue(st["ok"], st)
        masks = []
        for cmd, data in self.dev.cmds:
            if cmd == "ptReal":
                for pkt in data["command"]:
                    raw = base64.b64decode(pkt)
                    if raw[4:7] == bytes([255, 255, 255]):
                        masks.append(raw[12] | raw[13] << 8)
        self.assertEqual(masks, [1, 2, 4, 8, 16])
        self.assertEqual(self.dev.state["color"], {"r": 10, "g": 20, "b": 30})

    def test_sweep_refuses_bad_ids_and_plain_ha_lights(self):
        self.assertEqual(self.c.post("/api/segments", json={"id": "8.8.8.8"}).status_code, 409)
        self.assertEqual(self.c.post("/api/segments", json={"id": "light.bed", "count": 1}).status_code, 409)

    def test_ha_lights_list_reports_sections(self):
        net = self.net
        orig = net.get
        net.get = lambda url, **kw: Resp([{"entity_id": "light.dj", "state": "on", "attributes": {}}] +
                                         [{"entity_id": "light.dj_segment_%d" % i, "state": "on", "attributes": {}} for i in range(1, 8)])             if url.endswith("/api/states") else orig(url, **kw)
        net.post = lambda url, **kw: Resp(text="x")
        j = self.c.get("/api/ha/lights?all=1").get_json()
        self.assertEqual([(l["id"], l["segments"]) for l in j["lights"]], [("light.dj", 7)])

    def test_previews_for_a_segmented_light(self):
        j = self.c.post("/api/previews", json={"primary": "013369", "accent": "ffb612", "segments": 6}).get_json()
        self.assertTrue(all(v["segments"] == 6 for v in j.values()))
        self.assertEqual(self.c.post("/api/previews", json={"segments": "junk"}).get_json()["burst"]["segments"], 1)


if __name__ == "__main__":
    unittest.main()
