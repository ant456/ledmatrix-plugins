"""govee_fx: flash Govee lights in team colors when something happens in a game.

Shared helper (each sports plugin carries its own copy). Two ways to reach a light:

  * LAN: Govee's local UDP API (turn on "LAN Control" for the light in the Govee Home app).
    Needs no Home Assistant and no cloud. Colour only, so the effects are played by us.
  * Home Assistant: any `light.*` entity, through the REST API.

A device entry that starts with "light." goes to Home Assistant; anything else is a LAN address
("192.168.0.50", or "192.168.0.50:4003").

Usage from a plugin:

    fx = GoveeFX(config.get("govee"), logger)
    fx.trigger("score", primary="013369", accent="ffb612")      # never blocks, never raises
    fx.close()

Command line, to try it without a game:

    python govee_fx.py scan
    python govee_fx.py test 192.168.0.50 --effect fireworks --colors 013369,ffb612
"""

from __future__ import annotations

import colorsys
import json
import logging
import math
import random
import socket
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

RGB = Tuple[int, int, int]
WHITE: RGB = (255, 255, 255)
BLACK: RGB = (0, 0, 0)

MULTICAST, SCAN_PORT, LISTEN_PORT, CONTROL_PORT = "239.255.255.250", 4001, 4002, 4003
HZ = 8.0                       # steps per second in a fade; Govee lights cope well with ~10 packets/s
MIN_GAP = 3.0                  # seconds between two effects, so a flurry of scores does not strobe all night

DEFAULT_EVENTS: Dict[str, Dict[str, Any]] = {
    "score": {"effect": "burst", "seconds": 6},
    "big_score": {"effect": "fireworks", "seconds": 10},
    "against": {"effect": "off", "seconds": 4},
}


# ----------------------------------------------------------------------
# colour
# ----------------------------------------------------------------------

def parse_color(value: Any, default: RGB = WHITE) -> RGB:
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return default
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return default


def vivid(rgb: RGB) -> RGB:
    """Team colours are print colours; LEDs need them at full value and a little more saturated."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    if s > 0.08:
        s = max(s, 0.7)
    return tuple(int(round(c * 255)) for c in colorsys.hsv_to_rgb(h, s, 1.0))  # type: ignore[return-value]


def team_colors(primary: Any, accent: Any) -> Tuple[RGB, RGB]:
    """Two colours that read as different on a light; white stands in when a team's two are alike."""
    p, a = vivid(parse_color(primary)), vivid(parse_color(accent))
    if sum(abs(x - y) for x, y in zip(p, a)) < 120:
        a = WHITE if sum(p) < 600 else (255, 150, 0)
    return p, a


def mix(a: RGB, b: RGB, f: float) -> RGB:
    f = max(0.0, min(1.0, f))
    return tuple(int(round(x + (y - x) * f)) for x, y in zip(a, b))  # type: ignore[return-value]


def scale(c: RGB, f: float) -> RGB:
    return tuple(int(round(max(0.0, min(255.0, x * f)))) for x in c)  # type: ignore[return-value]


# ----------------------------------------------------------------------
# effects: each returns [(seconds_from_start, rgb)] for ONE light; the engine staggers several lights
# ----------------------------------------------------------------------

Steps = List[Tuple[float, RGB]]


def _ramp(c0: RGB, c1: RGB, t0: float, t1: float) -> Steps:
    n = max(1, int(round((t1 - t0) * HZ)))
    return [(t0 + (t1 - t0) * k / n, mix(c0, c1, k / n)) for k in range(1, n + 1)]


def _tail(steps: Steps, t: float, colour: RGB, dur: float) -> Steps:
    """Hold the colour, then fade it down over the last second and a half."""
    fade = min(1.5, max(0.5, dur - t))
    steps.append((t, colour))
    steps += _ramp(colour, scale(colour, 0.12), max(t, dur - fade), dur)
    return steps


def fx_flash(p: RGB, a: RGB, dur: float) -> Steps:
    st: Steps = []
    t = 0.0
    for _ in range(3):
        st += [(t, WHITE), (t + 0.12, p), (t + 0.32, scale(p, 0.1))]
        t += 0.5
    return _tail(st, t, p, dur)


def fx_pulse(p: RGB, a: RGB, dur: float) -> Steps:
    st: Steps = []
    n = int(dur * HZ)
    for k in range(n + 1):
        t = k / HZ
        f = 0.15 + 0.85 * (0.5 - 0.5 * math.cos(2 * math.pi * t / 1.5))
        st.append((t, mix(scale(p, f), scale(a, f), 0.25 * f)))
    return st


def fx_chase(p: RGB, a: RGB, dur: float) -> Steps:
    st: Steps = []
    t, k = 0.0, 0
    while t < dur - 1.2:
        st.append((t, p if k % 2 == 0 else a))
        t += 0.3
        k += 1
    return _tail(st, t, p, dur)


def fx_strobe(p: RGB, a: RGB, dur: float) -> Steps:
    st: Steps = []
    t = 0.0
    while t < min(2.0, dur - 1.0):
        st += [(t, p), (t + 0.07, BLACK)]
        t += 0.14
    return _tail(st, t, p, dur)


def fx_fireworks(p: RGB, a: RGB, dur: float) -> Steps:
    rng = random.Random(7)
    st: Steps = []
    t = 0.0
    while t < dur - 1.6:
        c = rng.choice([p, a, WHITE, p])
        st.append((t, c))
        st += _ramp(c, scale(c, 0.04), t, t + 0.55)
        t += rng.uniform(0.6, 0.9)
    return _tail(st, t, p, dur)


def fx_glow(p: RGB, a: RGB, dur: float) -> Steps:
    st = _ramp(BLACK, p, 0.0, 1.2)
    return _tail(st, 1.2, p, dur)


def fx_burst(p: RGB, a: RGB, dur: float) -> Steps:
    """A white pop, then the two colours trading places and settling on the first."""
    st: Steps = [(0.0, WHITE), (0.15, p)]
    t = 0.35
    for k in range(5):
        st.append((t, a if k % 2 == 0 else p))
        t += 0.32 if k < 3 else 0.5
    return _tail(st, t, p, dur)


EFFECTS: Dict[str, Callable[[RGB, RGB, float], Steps]] = {
    "flash": fx_flash, "pulse": fx_pulse, "chase": fx_chase, "strobe": fx_strobe,
    "fireworks": fx_fireworks, "glow": fx_glow, "burst": fx_burst,
}


def timeline(effect: str, p: RGB, a: RGB, dur: float, lights: int, stagger: float) -> List[Tuple[float, int, RGB, bool]]:
    """(time, light index, colour, is_last_for_that_light), in time order. Several lights run the same
    effect a little apart, so a colour appears to travel along them."""
    fn = EFFECTS.get(effect, fx_burst)
    base = fn(p, a, max(2.0, float(dur)))
    out = []
    for i in range(lights):
        off = i * stagger
        for k, (t, c) in enumerate(base):
            out.append((t + off, i, c, k == len(base) - 1))
    out.sort(key=lambda x: x[0])
    return out


# ----------------------------------------------------------------------
# LAN transport (one per process: every plugin shares the listening port)
# ----------------------------------------------------------------------

class LanTransport:
    _inst: Optional["LanTransport"] = None
    _guard = threading.Lock()

    @classmethod
    def get(cls) -> "LanTransport":
        with cls._guard:
            if cls._inst is None:
                cls._inst = cls()
            return cls._inst

    def __init__(self) -> None:
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.status: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self.found: Dict[str, str] = {}
        self._rx: Optional[socket.socket] = None
        self._lock = threading.Lock()
        try:
            rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            rx.bind(("", LISTEN_PORT))
            try:
                rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                              socket.inet_aton(MULTICAST) + socket.inet_aton("0.0.0.0"))
            except OSError:
                pass
            rx.settimeout(0.5)
            self._rx = rx
            threading.Thread(target=self._listen, name="govee-lan-rx", daemon=True).start()
        except OSError:
            self._rx = None

    def _listen(self) -> None:
        while self._rx is not None:
            try:
                data, addr = self._rx.recvfrom(4096)
                msg = json.loads(data.decode("utf-8", "replace")).get("msg", {})
            except socket.timeout:
                continue
            except (OSError, ValueError):
                if self._rx is None:
                    return
                continue
            body = msg.get("data") or {}
            if msg.get("cmd") == "scan" and body.get("ip"):
                self.found[body["ip"]] = body.get("sku", "?")
            elif msg.get("cmd") == "devStatus":
                self.status[addr[0]] = (time.time(), body)

    def send(self, ip: str, cmd: str, data: Dict[str, Any], port: int = CONTROL_PORT) -> None:
        payload = json.dumps({"msg": {"cmd": cmd, "data": data}}).encode()
        self.tx.sendto(payload, (ip, port))

    def query(self, ip: str, port: int = CONTROL_PORT, timeout: float = 1.2) -> Optional[Dict[str, Any]]:
        started = time.time()
        self.send(ip, "devStatus", {}, port)
        while time.time() - started < timeout:
            got = self.status.get(ip)
            if got and got[0] >= started:
                return got[1]
            time.sleep(0.05)
        return None

    def scan(self, timeout: float = 4.0, iface: Optional[str] = None) -> Dict[str, str]:
        self.found = {}
        msg = json.dumps({"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}).encode()
        if iface:
            self.tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
        end = time.time() + timeout
        while time.time() < end:
            self.tx.sendto(msg, (MULTICAST, SCAN_PORT))
            time.sleep(1.0)
        return dict(self.found)

    def close(self) -> None:
        rx, self._rx = self._rx, None
        if rx is not None:
            rx.close()


# ----------------------------------------------------------------------
# lights
# ----------------------------------------------------------------------

class LanLight:
    kind = "lan"

    def __init__(self, address: str) -> None:
        host, _, port = address.partition(":")
        self.name, self.ip, self.port = address, host, int(port) if port else CONTROL_PORT
        self.net = LanTransport.get()

    def snapshot(self) -> Optional[Dict[str, Any]]:
        return self.net.query(self.ip, self.port)

    def prepare(self) -> None:
        self.net.send(self.ip, "turn", {"value": 1}, self.port)
        self.net.send(self.ip, "brightness", {"value": 100}, self.port)

    def show(self, rgb: RGB) -> None:
        self.net.send(self.ip, "colorwc", {"color": {"r": rgb[0], "g": rgb[1], "b": rgb[2]}, "colorTemInKelvin": 0}, self.port)

    def restore(self, snap: Optional[Dict[str, Any]], fallback: str) -> None:
        if snap is None:
            if fallback == "off":
                self.net.send(self.ip, "turn", {"value": 0}, self.port)
            elif fallback == "warm":
                self.net.send(self.ip, "colorwc", {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": 2700}, self.port)
                self.net.send(self.ip, "brightness", {"value": 60}, self.port)
            return
        if not snap.get("onOff"):
            self.net.send(self.ip, "turn", {"value": 0}, self.port)
            return
        col = snap.get("color") or {}
        self.net.send(self.ip, "colorwc", {"color": {"r": col.get("r", 0), "g": col.get("g", 0), "b": col.get("b", 0)},
                                           "colorTemInKelvin": snap.get("colorTemInKelvin", 0)}, self.port)
        self.net.send(self.ip, "brightness", {"value": snap.get("brightness", 100)}, self.port)


class HaLight:
    kind = "ha"

    def __init__(self, entity: str, url: str, token: str, session: Any = None) -> None:
        self.name, self.url, self.token = entity, url.rstrip("/"), token
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}

    def _call(self, service: str, data: Dict[str, Any]) -> None:
        data = dict(data, entity_id=self.name)
        self.session.post("%s/api/services/light/%s" % (self.url, service), headers=self._headers(), json=data, timeout=5)

    def snapshot(self) -> Optional[Dict[str, Any]]:
        r = self.session.get("%s/api/states/%s" % (self.url, self.name), headers=self._headers(), timeout=5)
        if getattr(r, "status_code", 200) != 200:
            return None
        j = r.json()
        return {"state": j.get("state"), "attributes": j.get("attributes") or {}}

    def prepare(self) -> None:
        pass

    def show(self, rgb: RGB) -> None:
        self._call("turn_on", {"rgb_color": list(rgb), "brightness": 255})

    def restore(self, snap: Optional[Dict[str, Any]], fallback: str) -> None:
        if snap is None or snap.get("state") in (None, "unavailable", "unknown"):
            if fallback == "off":
                self._call("turn_off", {})
            return
        if snap["state"] == "off":
            self._call("turn_off", {})
            return
        at = snap["attributes"]
        data: Dict[str, Any] = {}
        if at.get("brightness") is not None:
            data["brightness"] = at["brightness"]
        if at.get("color_mode") == "color_temp" and at.get("color_temp_kelvin"):
            data["color_temp_kelvin"] = at["color_temp_kelvin"]
        elif at.get("rgb_color"):
            data["rgb_color"] = list(at["rgb_color"])
        self._call("turn_on", data)


# ----------------------------------------------------------------------
# engine
# ----------------------------------------------------------------------

class GoveeFX:
    """Plays effects on a worker thread. trigger() is safe to call from a plugin's display loop."""

    time_scale = 1.0           # tests set this above 1 to run quickly

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, logger: Optional[logging.Logger] = None,
                 session: Any = None) -> None:
        self.log = logger or logging.getLogger("govee_fx")
        self._session = session
        self._cv = threading.Condition()
        self._job: Optional[Tuple[str, RGB, RGB, float]] = None
        self._gen = 0
        self._stop = False
        self._last_end = 0.0
        self._snaps: Optional[List[Optional[Dict[str, Any]]]] = None
        self.lights: List[Any] = []
        self.events: Dict[str, Dict[str, Any]] = {}
        self.enabled = False
        self.history: List[str] = []
        self._thread: Optional[threading.Thread] = None
        self.configure(cfg)

    # -- config ---------------------------------------------------------

    def configure(self, cfg: Optional[Dict[str, Any]]) -> None:
        cfg = cfg if isinstance(cfg, dict) else {}
        self.enabled = bool(cfg.get("enabled", False))
        self.fallback = str(cfg.get("restore_fallback", "off"))
        self.stagger = float(cfg.get("stagger_seconds", 0.15))
        self.cooldown = float(cfg.get("cooldown_seconds", MIN_GAP))
        self.colors = cfg.get("colors") if isinstance(cfg.get("colors"), dict) else {}
        events = {k: dict(v) for k, v in DEFAULT_EVENTS.items()}
        for k, v in (cfg.get("events") or {}).items():
            if isinstance(v, str):
                v = {"effect": v}
            if isinstance(v, dict):
                events[k] = dict(events.get(k, {"seconds": 6}), **v)
        self.events = events
        self.lights = []
        if not self.enabled:
            return
        for entry in cfg.get("devices") or []:
            entry = str(entry).strip()
            try:
                if entry.startswith("light."):
                    if not (cfg.get("ha_url") and cfg.get("ha_token")):
                        self.log.warning("govee: %s needs ha_url and ha_token", entry)
                        continue
                    self.lights.append(HaLight(entry, cfg["ha_url"], cfg["ha_token"], self._session))
                elif entry:
                    self.lights.append(LanLight(entry))
            except Exception as exc:  # noqa: BLE001 - a bad entry must not stop the plugin
                self.log.warning("govee: could not set up %s: %s", entry, exc)
        if self.lights and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="govee-fx", daemon=True)
            self._thread.start()

    # -- API ------------------------------------------------------------

    def trigger(self, event: str, primary: Any = "ffffff", accent: Any = "ffffff") -> bool:
        """Queue an effect for an event ('score', 'big_score', 'against', ...). Returns whether one was queued."""
        try:
            spec = self.events.get(event)
            if not self.enabled or not self.lights or not spec or spec.get("effect", "off") == "off":
                return False
            now = time.monotonic()
            if now - self._last_end < self.cooldown and event != "big_score":
                return False
            over = self.colors.get(event) if isinstance(self.colors.get(event), dict) else {}
            p, a = team_colors(over.get("primary", primary), over.get("accent", accent))
            with self._cv:
                self._job = (str(spec.get("effect", "burst")), p, a, float(spec.get("seconds", 6)))
                self._gen += 1
                self._cv.notify()
            self.history.append(event)
            return True
        except Exception as exc:  # noqa: BLE001
            self.log.warning("govee: trigger failed: %s", exc)
            return False

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._gen += 1
            self._cv.notify()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    # -- worker ---------------------------------------------------------

    def _sleep_until(self, due: float, gen: int) -> bool:
        while True:
            left = (due - time.monotonic())
            if self._gen != gen or self._stop:
                return False
            if left <= 0:
                return True
            time.sleep(min(left, 0.05))

    def _safe(self, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 - one unreachable light must not stop the others
            self.log.debug("govee: %s: %s", getattr(fn, "__name__", "call"), exc)
            return None

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._job is None and not self._stop:
                    self._cv.wait(timeout=1.0)
                if self._stop:
                    return
                job, gen = self._job, self._gen
                self._job = None
            self._play(job, gen)

    def _play(self, job: Tuple[str, RGB, RGB, float], gen: int) -> None:
        effect, p, a, dur = job
        if self._snaps is None:
            self._snaps = [self._safe(l.snapshot) for l in self.lights]
        for l in self.lights:
            self._safe(l.prepare)
        steps = timeline(effect, p, a, dur, len(self.lights), self.stagger)
        start = time.monotonic()
        for t, i, rgb, last in steps:
            if not self._sleep_until(start + t / self.time_scale, gen):
                return                      # a newer effect (or close) took over; it will restore
            if not last and time.monotonic() - (start + t / self.time_scale) > 0.5:
                continue                    # running behind (slow Home Assistant): skip, catch up
            self._safe(self.lights[i].show, rgb)
        if self._gen != gen:
            return
        time.sleep(0.25 / self.time_scale)
        snaps, self._snaps = self._snaps, None
        for l, s in zip(self.lights, snaps or []):
            self._safe(l.restore, s, self.fallback)
        self._last_end = time.monotonic()


# ----------------------------------------------------------------------
# command line
# ----------------------------------------------------------------------

def _main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Govee effects for LEDMatrix plugins")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="find Govee lights with LAN Control on")
    sc.add_argument("--iface", help="this computer's LAN address, if the scan finds nothing")
    ts = sub.add_parser("test", help="play an effect on one or more lights")
    ts.add_argument("devices", nargs="+", help="LAN address, or light.entity with --ha-url/--ha-token")
    ts.add_argument("--effect", default="burst", choices=sorted(EFFECTS))
    ts.add_argument("--colors", default="013369,ffb612", help="primary,accent as hex")
    ts.add_argument("--seconds", type=float, default=8.0)
    ts.add_argument("--ha-url")
    ts.add_argument("--ha-token")
    args = ap.parse_args(argv)
    if args.cmd == "scan":
        found = LanTransport.get().scan(4.0, args.iface)
        if not found:
            print("No lights answered. Turn on LAN Control for the light in the Govee Home app "
                  "(light settings > gear icon), and run this from a computer on the same network.")
            return 1
        for ip, sku in sorted(found.items()):
            print(ip, sku)
        return 0
    prim, _, acc = args.colors.partition(",")
    fx = GoveeFX({"enabled": True, "devices": args.devices, "ha_url": args.ha_url, "ha_token": args.ha_token,
                  "events": {"test": {"effect": args.effect, "seconds": args.seconds}}, "cooldown_seconds": 0})
    fx.trigger("test", prim, acc or prim)
    time.sleep(args.seconds + 2.0)
    fx.close()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
