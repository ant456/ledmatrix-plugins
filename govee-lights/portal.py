"""The Govee Lights portal: pick lights, pick effects, press test.

`Portal` holds the logic (no Flask, so it is easy to test); `create_app` is a thin web layer over it.
Nothing here ever sends the Home Assistant token to the browser: it is read from LEDMatrix's own
config.json on the server when it is needed.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import govee_fx as g

SPORTS = [
    {"key": "nfl", "label": "NFL", "path": "football/nfl"},
    {"key": "nba", "label": "NBA", "path": "basketball/nba"},
    {"key": "nhl", "label": "NHL", "path": "hockey/nhl"},
]
EVENTS = [
    {"key": "score", "label": "Your team scores", "help": "A favorite team scores (any team, if you have no favorites set)"},
    {"key": "big_score", "label": "Big moment", "help": "A touchdown, an NBA lead change, a hockey overtime or go-ahead goal"},
    {"key": "against", "label": "Scored against you", "help": "The other side scores on one of your favorites"},
]
TEAM_CACHE_SECONDS = 12 * 3600


class Portal:
    def __init__(self, central: Optional[Path] = None, lm_config: Optional[Path] = None,
                 logger: Any = None, session: Any = None) -> None:
        self.central = Path(central) if central else g.default_central_path()
        self.lm_config = Path(lm_config) if lm_config else (self.central.parent / "config.json")
        self.log = logger
        self.session = session
        self._lock = threading.Lock()
        self._scan: Dict[str, Any] = {"running": False, "found": [], "done": False, "error": ""}
        self._test: Dict[str, Any] = {"running": False, "message": "", "ok": None, "started": 0.0}
        self._teams: Dict[str, Any] = {}

    # -- config -----------------------------------------------------------

    def load(self) -> Dict[str, Any]:
        try:
            return g.clean_config(json.loads(self.central.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return g.clean_config({})

    def exists(self) -> bool:
        return self.central.exists()

    def save(self, raw: Any) -> Dict[str, Any]:
        return g.save_central(self.central, raw)

    def ha(self) -> Dict[str, Any]:
        url, token = g.ha_credentials(self.lm_config, self.load().get("ha_source", ""))
        return {"available": bool(url and token), "url": url, "token": token}

    def state(self) -> Dict[str, Any]:
        ha = self.ha()
        return {"config": self.load(), "saved": self.exists(), "ha": {"available": ha["available"]},
                "effects": [{"key": k, "info": v} for k, v in g.EFFECT_INFO.items()],
                "events": EVENTS, "sports": [{"key": s["key"], "label": s["label"]} for s in SPORTS]}

    # -- finding lights ----------------------------------------------------

    def scan_start(self) -> Dict[str, Any]:
        with self._lock:
            if self._scan["running"]:
                return dict(self._scan)
            self._scan = {"running": True, "found": [], "done": False, "error": ""}
        threading.Thread(target=self._scan_run, daemon=True, name="govee-scan").start()
        return dict(self._scan)

    def _scan_run(self) -> None:
        try:
            found = g.discover_lan(8.0)
            self._scan.update(found=found, error="")
        except Exception as exc:  # noqa: BLE001
            self._scan.update(error=str(exc))
        self._scan.update(running=False, done=True)

    def scan_state(self) -> Dict[str, Any]:
        return dict(self._scan)

    def ha_lights(self, show_all: bool = False) -> Dict[str, Any]:
        ha = self.ha()
        if not ha["available"]:
            return {"available": False, "lights": [], "error": "Home Assistant is not configured in LEDMatrix"}
        try:
            return {"available": True, "lights": g.list_ha_lights(ha["url"], ha["token"], not show_all, self.session), "error": ""}
        except Exception as exc:  # noqa: BLE001
            return {"available": True, "lights": [], "error": "Could not reach Home Assistant (%s)" % type(exc).__name__}

    def status(self, ids: List[str]) -> Dict[str, Any]:
        ids = [i for i in ids if g.valid_light_id(i)][:40]
        ha = self.ha()
        return g.light_status(ids, ha["url"], ha["token"], self.session)

    # -- playing -----------------------------------------------------------

    def _fx(self, devices: List[str], events: Dict[str, Any], colors: Dict[str, Any],
            segments: Optional[Dict[str, int]] = None) -> g.GoveeFX:
        ha = self.ha()
        cfg = self.load()
        return g.GoveeFX({"enabled": True, "devices": devices, "events": events, "colors": colors, "segments": segments or {},
                          "ha_url": ha["url"], "ha_token": ha["token"], "cooldown_seconds": 0,
                          "stagger_seconds": cfg["stagger_seconds"], "restore_fallback": cfg["restore_fallback"]},
                         self.log, self.session, central=False)

    def identify(self, light: str) -> Dict[str, Any]:
        if not g.valid_light_id(light):
            return {"ok": False, "error": "Not a light address"}
        if self._test["running"]:
            return {"ok": False, "error": "A test is already playing"}
        threading.Thread(target=self._play, args=([light], "identify", 3.0, {}, "Blinking %s" % light), daemon=True).start()
        return {"ok": True}

    def sweep(self, light: str, count: Any = 16) -> Dict[str, Any]:
        """Light each section of one light in turn, so you can count how many it has."""
        if not g.valid_light_id(light):
            return {"ok": False, "error": "Not a light address"}
        if self._test["running"]:
            return {"ok": False, "error": "A test is already playing"}
        try:
            asked = int(count)
        except (TypeError, ValueError):
            asked = g.MAX_SEGMENTS
        if light.startswith("light.") and asked < 2:        # a Home Assistant light only has sections if it says so
            return {"ok": False, "error": "Home Assistant shows no separate sections for this light"}
        n = max(2, min(g.MAX_SEGMENTS, asked))
        threading.Thread(target=self._sweep, args=(light, n), daemon=True).start()
        return {"ok": True}

    def _sweep(self, light: str, n: int) -> None:
        self._test.update(running=True, ok=None, message="Getting ready…", started=time.time())
        obj = None
        snap = None
        try:
            ha = self.ha()
            if light.startswith("light."):
                if not ha["available"]:
                    self._test.update(ok=False, message="Home Assistant is not configured")
                    return
                obj = g.HaLight(light, ha["url"], ha["token"], self.session, n)
            else:
                obj = g.LanLight(light, n)
            snap = obj.snapshot()
            obj.prepare()
            for k in range(n):
                cols = [g.BLACK] * n
                cols[k] = g.WHITE
                obj.show_segments(cols)
                self._test["message"] = "Section %d of %d. Count how many sections light up; nothing lights past the last one." % (k + 1, n)
                time.sleep(0.8)
            self._test.update(ok=True, message="Done. Enter how many sections lit up, then Save. The light is back how it was.")
        except Exception as exc:  # noqa: BLE001
            self._test.update(ok=False, message="Section test failed: %s" % exc)
        finally:
            if obj is not None:
                try:
                    obj.restore(snap, self.load()["restore_fallback"])
                except Exception:  # noqa: BLE001
                    pass
            self._test["running"] = False

    def test(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Play what the form currently says, whether or not it has been saved."""
        if self._test["running"]:
            return {"ok": False, "error": "A test is already playing"}
        lights = [i for i in (req.get("lights") or []) if g.valid_light_id(i)]
        if not lights:
            return {"ok": False, "error": "Pick at least one light to test on"}
        spec = g.clean_event(req.get("spec"), g.DEFAULT_SPEC["score"])
        colors: Dict[str, Any] = {}
        team = req.get("team") if isinstance(req.get("team"), dict) else {}
        if isinstance(spec["colors"], dict):
            colors = {"primary": spec["colors"]["primary"], "accent": spec["colors"]["accent"]}
        else:
            p, a = g.team_colors(team.get("primary", "013369"), team.get("accent", "ffb612"))
            colors = {"primary": "%02x%02x%02x" % p, "accent": "%02x%02x%02x" % a}
        label = "%s on %d light%s" % (spec["effect"], len(lights), "" if len(lights) == 1 else "s")
        raw_segs = req.get("segments") if isinstance(req.get("segments"), dict) else {}
        segs = {i: max(1, min(g.MAX_SEGMENTS, int(raw_segs.get(i, 1)))) for i in lights if str(raw_segs.get(i, 1)).isdigit()}
        threading.Thread(target=self._play, args=(lights, spec["effect"], spec["seconds"], colors, label, segs), daemon=True).start()
        return {"ok": True, "message": "Playing " + label}

    def _play(self, lights: List[str], effect: str, seconds: float, colors: Dict[str, Any], label: str,
              segments: Optional[Dict[str, int]] = None) -> None:
        self._test.update(running=True, ok=None, message="Playing: " + label, started=time.time())
        fx = None
        try:
            fx = self._fx(lights, {"test": {"effect": effect, "seconds": seconds}}, {"test": colors} if colors else {}, segments)
            if not fx.lights:
                self._test.update(ok=False, message="None of those lights could be set up (is Home Assistant configured?)")
                return
            fx.trigger("test")
            done = fx.wait(seconds + 25.0)
            self._test.update(ok=done, message="Done. The lights are back how they were." if done else "Timed out waiting for the lights")
        except Exception as exc:  # noqa: BLE001
            self._test.update(ok=False, message="Test failed: %s" % exc)
        finally:
            if fx is not None:
                fx.close()
            self._test["running"] = False

    def test_state(self) -> Dict[str, Any]:
        return dict(self._test)

    # -- helpers for the page ----------------------------------------------

    def teams(self, sport: str) -> Dict[str, Any]:
        info = next((s for s in SPORTS if s["key"] == sport), None)
        if info is None:
            return {"teams": [], "error": "Unknown sport"}
        cached = self._teams.get(sport)
        if cached and time.time() - cached[0] < TEAM_CACHE_SECONDS:
            return {"teams": cached[1], "error": ""}
        try:
            import requests
            s = self.session or requests
            r = s.get("https://site.api.espn.com/apis/site/v2/sports/%s/teams?limit=100" % info["path"], timeout=10)
            raw = r.json()["sports"][0]["leagues"][0]["teams"]
            teams = sorted(({"abbr": t["team"].get("abbreviation", ""), "name": t["team"].get("displayName", ""),
                             "primary": t["team"].get("color", "013369"), "accent": t["team"].get("alternateColor", "ffffff")}
                            for t in raw), key=lambda t: t["name"])
            self._teams[sport] = (time.time(), teams)
            return {"teams": teams, "error": ""}
        except Exception as exc:  # noqa: BLE001
            return {"teams": cached[1] if cached else [], "error": "Could not load teams (%s)" % type(exc).__name__}

    def previews(self, primary: Any, accent: Any, custom: bool, segments: Any = 1) -> Dict[str, Any]:
        try:
            n = max(1, min(g.MAX_SEGMENTS, int(segments)))
        except (TypeError, ValueError):
            n = 1
        return {k: g.preview(k, primary, accent, 5.0, custom, n) for k in g.PICKABLE}


def create_app(portal: Portal, static_dir: Path):
    from flask import Flask, Response, jsonify, request

    app = Flask("govee_lights")
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    def body() -> Dict[str, Any]:
        j = request.get_json(silent=True)
        return j if isinstance(j, dict) else {}

    @app.get("/")
    def index():
        page = static_dir / "portal.html"
        return Response(page.read_text(encoding="utf-8") if page.exists() else "<h1>Govee Lights</h1><p>static/portal.html missing</p>",
                        mimetype="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/api/state")
    def state():
        return jsonify(portal.state())

    @app.post("/api/config")
    def config():
        try:
            return jsonify({"ok": True, "config": portal.save(body())})
        except OSError as exc:
            return jsonify({"ok": False, "error": "Could not save: %s" % exc}), 500

    @app.post("/api/scan")
    def scan():
        return jsonify(portal.scan_start())

    @app.get("/api/scan")
    def scan_state():
        return jsonify(portal.scan_state())

    @app.get("/api/ha/lights")
    def ha_lights():
        return jsonify(portal.ha_lights(request.args.get("all") == "1"))

    @app.post("/api/status")
    def status():
        return jsonify(portal.status([str(i) for i in (body().get("ids") or [])]))

    @app.post("/api/identify")
    def identify():
        r = portal.identify(str(body().get("id", "")))
        return jsonify(r), (200 if r["ok"] else 409)

    @app.post("/api/segments")
    def segments():
        b = body()
        r = portal.sweep(str(b.get("id", "")), b.get("count", 16))
        return jsonify(r), (200 if r["ok"] else 409)

    @app.post("/api/test")
    def test():
        r = portal.test(body())
        return jsonify(r), (200 if r["ok"] else 409)

    @app.get("/api/test")
    def test_state():
        return jsonify(portal.test_state())

    @app.get("/api/teams")
    def teams():
        return jsonify(portal.teams(request.args.get("sport", "")))

    @app.post("/api/previews")
    def previews():
        b = body()
        return jsonify(portal.previews(b.get("primary", "013369"), b.get("accent", "ffb612"), bool(b.get("custom")),
                                       b.get("segments", 1)))

    return app
