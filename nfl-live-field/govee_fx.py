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

import base64
import colorsys
import json
import logging
import math
import os
import random
import re
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path
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


def fx_rocket(p: RGB, a: RGB, dur: float) -> Steps:
    """A light with no sections can't show a climb, so it swells brighter as the rocket rises, pops, and sparkles out."""
    rng = random.Random(11)
    st: Steps = []
    t, k = 0.0, 0
    while t < dur - 2.4:
        c = p if k % 2 == 0 else a
        st += _ramp(scale(c, 0.1), mix(c, WHITE, 0.5), t, t + 0.9)
        t += 0.9
        st.append((t, WHITE))
        for j in range(4):
            st.append((t + 0.12 + j * 0.13, scale(rng.choice([c, p, a, WHITE]), 1.0 - 0.2 * j)))
        st += _ramp(c, scale(c, 0.04), t + 0.7, t + 1.1)
        t += 1.3
        k += 1
    return _tail(st, t, p, dur)


def fx_identify(p: RGB, a: RGB, dur: float) -> Steps:
    """Three slow white blinks, to tell which light is which."""
    st: Steps = []
    for k in range(3):
        st += [(k * 0.9, WHITE), (k * 0.9 + 0.5, BLACK)]
    st.append((2.7, WHITE))
    return st


EFFECTS: Dict[str, Callable[[RGB, RGB, float], Steps]] = {
    "flash": fx_flash, "pulse": fx_pulse, "chase": fx_chase, "strobe": fx_strobe,
    "fireworks": fx_fireworks, "glow": fx_glow, "burst": fx_burst, "rocket": fx_rocket, "identify": fx_identify,
}

# What the portal offers, in order, with a plain description (identify is internal)
EFFECT_INFO: Dict[str, str] = {
    "burst": "A white pop, then the two team colors trade places and settle",
    "rocket": "A rocket shoots up the light from the bottom, bursts at the top and showers down like fireworks",
    "fireworks": "Random bursts of both colors and white, each fading out",
    "flash": "Three white-to-team-color flashes",
    "pulse": "Slow breathing in the team colors",
    "chase": "The two colors alternate; with several lights it travels along them",
    "strobe": "Fast strobing for two seconds, then the team color",
    "glow": "Fades up to the team color and holds",
}
PICKABLE = list(EFFECT_INFO)
EVENT_NAMES = ("score", "big_score", "against")
KNOWN_SPORTS = ("nfl", "nba", "nhl")


def preview(effect: str, primary: Any, accent: Any, seconds: float, custom: bool = False, segments: int = 1) -> Dict[str, Any]:
    """One light's colours over time, for the portal's animated swatch. Each step is [time, [rgb per segment]]."""
    if custom:
        p, a = parse_color(primary), parse_color(accent)
    else:
        p, a = team_colors(primary, accent)
    secs = max(2.0, min(30.0, float(seconds)))
    n = max(1, min(MAX_SEGMENTS, int(segments)))
    fr = frames(effect, p, a, secs, n)
    return {"seconds": secs, "segments": n, "steps": [[round(t, 3), [list(c) for c in cols]] for t, cols in fr],
            "primary": list(p), "accent": list(a)}



# ----------------------------------------------------------------------
# segment-aware effects (RGBIC lights): each returns [(seconds, [rgb per segment])]
# ----------------------------------------------------------------------

SegFrames = List[Tuple[float, List[RGB]]]
MAX_SEGMENTS = 16
_LEVELS = (1.0, 0.55, 0.25, 0.08)


def _seg_tail(frames: SegFrames, t: float, colours: List[RGB], dur: float) -> SegFrames:
    fade = min(1.5, max(0.5, dur - t))
    frames.append((t, colours))
    n = max(1, int(round(fade * HZ)))
    start = max(t, dur - fade)
    for k in range(1, n + 1):
        f = 1.0 - 0.88 * k / n
        frames.append((start + (dur - start) * k / n, [scale(c, f) for c in colours]))
    return frames


def seg_burst(p: RGB, a: RGB, dur: float, n: int) -> SegFrames:
    fr: SegFrames = [(0.0, [WHITE] * n), (0.15, [p] * n)]
    t = 0.35
    for k in range(5):
        fr.append((t, [a if (i + k) % 2 == 0 else p for i in range(n)]))     # the two colours trade places
        t += 0.32 if k < 3 else 0.5
    return _seg_tail(fr, t, [p] * n, dur)


def seg_chase(p: RGB, a: RGB, dur: float, n: int) -> SegFrames:
    """A comet runs along the light, alternating colour on each pass."""
    fr: SegFrames = []
    speed = max(6.0, n / 1.1)                      # segments per second
    base_of = lambda c: scale(c, 0.18)
    t, tail = 0.0, 2
    while t < dur - 1.4:
        pas = int(t * speed // (n + tail))
        comet = p if pas % 2 == 0 else a
        head = (t * speed) % (n + tail)
        cols = []
        for i in range(n):
            d = head - i
            if 0 <= d < 1:
                cols.append(comet)
            elif 1 <= d < 1 + tail:
                cols.append(mix(base_of(comet), comet, 0.5 if d < 2 else 0.22))
            else:
                cols.append(base_of(p if pas % 2 == 0 else a))
        fr.append((t, cols))
        t += 1.0 / HZ
    return _seg_tail(fr, t, [p] * n, dur)


def seg_fireworks(p: RGB, a: RGB, dur: float, n: int) -> SegFrames:
    rng = random.Random(7)
    level = [0] * n                                # 0 = off, else index into _LEVELS + 1
    colour = [p] * n
    fr: SegFrames = []
    t = 0.0
    while t < dur - 1.6:
        if rng.random() < 0.55:
            for _ in range(rng.choice((1, 1, 2, 3))):
                i = rng.randrange(n)
                level[i], colour[i] = 1, rng.choice([p, a, WHITE, p])
        fr.append((t, [scale(colour[i], _LEVELS[level[i] - 1]) if level[i] else BLACK for i in range(n)]))
        level = [(l + 1 if 0 < l < len(_LEVELS) else 0) for l in level]
        t += 1.0 / HZ
    return _seg_tail(fr, t, [p] * n, dur)


def seg_pulse(p: RGB, a: RGB, dur: float, n: int) -> SegFrames:
    """Breathing, with the breath rippling along the light."""
    fr: SegFrames = []
    for k in range(int(dur * HZ) + 1):
        t = k / HZ
        cols = []
        for i in range(n):
            f = 0.15 + 0.85 * (0.5 - 0.5 * math.cos(2 * math.pi * t / 1.5 - i * 0.55))
            f = round(f * 5) / 5.0                 # few distinct levels means few packets
            cols.append(mix(scale(p, f), scale(a, f), 0.25 * f))
        fr.append((t, cols))
    return fr


def seg_flash(p: RGB, a: RGB, dur: float, n: int) -> SegFrames:
    return [(t, [c] * n) for t, c in fx_flash(p, a, dur)]


def seg_strobe(p: RGB, a: RGB, dur: float, n: int) -> SegFrames:
    fr: SegFrames = []
    t, k = 0.0, 0
    while t < min(2.0, dur - 1.0):
        fr.append((t, [p if (i + k) % 2 == 0 else BLACK for i in range(n)]))   # alternate segments, swapping fast
        t += 0.09
        k += 1
    return _seg_tail(fr, t, [p] * n, dur)


def seg_glow(p: RGB, a: RGB, dur: float, n: int) -> SegFrames:
    """Fills up segment by segment like a meter, then holds."""
    fr: SegFrames = []
    steps = max(n, int(1.2 * HZ))
    for k in range(steps + 1):
        t = 1.2 * k / steps
        reach = (k / steps) * (n + 1.5)
        fr.append((t, [mix(BLACK, p, max(0.0, min(1.0, reach - i))) for i in range(n)]))
    return _seg_tail(fr, 1.2, [p] * n, dur)


def seg_rocket(p: RGB, a: RGB, dur: float, n: int) -> SegFrames:
    """Climbs from section 1 (the bottom) to the last, accelerating; bursts at the top; sparkles fall back down."""
    rng = random.Random(11)
    fr: SegFrames = []
    t, k = 0.0, 0
    climb = min(1.3, max(0.7, 0.08 * n))
    while t < dur - 2.6:
        c = p if k % 2 == 0 else a
        steps = max(n, int(climb * HZ))
        for s in range(steps):
            u = (s + 1) / steps
            head = (u ** 1.5) * (n - 1)                                  # speeds up as it climbs
            cols = []
            for i in range(n):
                d = head - i
                if d < 0:
                    cols.append(BLACK)
                elif d < 0.8:
                    cols.append(mix(c, WHITE, 0.7))                      # the hot head
                elif d < 2.0:
                    cols.append(scale(mix(c, WHITE, 0.2), 0.6))
                elif d < 3.2:
                    cols.append(scale(c, 0.22))
                else:
                    cols.append(BLACK)
            fr.append((t + climb * u, cols))
        t += climb
        for f in range(5):                                               # the burst spreads down from the top
            r = (f + 1) / 5.0 * n * 0.8
            fr.append((t + f * 0.07, [WHITE if (n - 1 - i) <= r * 0.4 else mix(c, WHITE, 0.35) if (n - 1 - i) <= r else BLACK
                                      for i in range(n)]))
        t += 0.35
        level = [0] * n
        colour = [c] * n
        for f in range(10):                                              # sparkles drift down and die out
            if rng.random() < 0.7 * (1.0 - f / 10.0):
                for _ in range(rng.choice((1, 2, 2, 3))):
                    i = max(0, min(n - 1, n - 1 - int(abs(rng.gauss(0, n * 0.35)))))
                    level[i], colour[i] = 1, rng.choice([c, a, WHITE, p])
            fr.append((t, [scale(colour[i], _LEVELS[level[i] - 1]) if level[i] else BLACK for i in range(n)]))
            level = [(l + 1 if 0 < l < len(_LEVELS) else 0) for l in level]
            t += 1.0 / HZ
        fr.append((t, [BLACK] * n))
        t += 0.25
        k += 1
    return _seg_tail(fr, t, [p] * n, dur)


SEG_EFFECTS: Dict[str, Callable[[RGB, RGB, float, int], SegFrames]] = {
    "rocket": seg_rocket, "burst": seg_burst, "chase": seg_chase, "fireworks": seg_fireworks, "pulse": seg_pulse,
    "flash": seg_flash, "strobe": seg_strobe, "glow": seg_glow,
}


def frames(effect: str, p: RGB, a: RGB, dur: float, n: int = 1) -> SegFrames:
    """One light's frames: a colour per segment. A one-segment light plays the plain effect."""
    dur = max(2.0, float(dur))
    n = max(1, min(MAX_SEGMENTS, int(n)))
    if n == 1 or effect not in SEG_EFFECTS:
        return [(t, [c] * n) for t, c in EFFECTS.get(effect, fx_burst)(p, a, dur)]
    return SEG_EFFECTS[effect](p, a, dur, n)


def timeline(effect: str, p: RGB, a: RGB, dur: float, lights: int, stagger: float,
             segments: Optional[List[int]] = None) -> List[Tuple[float, int, Any, bool]]:
    """(time, light index, colour, is_last_for_that_light), in time order. Several lights run the same
    effect a little apart, so a colour appears to travel along them. A light with segments gets a list
    of colours (one per segment) instead of a single colour."""
    segs = list(segments or []) + [1] * max(0, lights - len(segments or []))
    out = []
    for i in range(lights):
        off = i * stagger
        fr = frames(effect, p, a, dur, segs[i])
        for k, (t, cols) in enumerate(fr):
            out.append((t + off, i, cols[0] if segs[i] == 1 else cols, k == len(fr) - 1))
    out.sort(key=lambda x: x[0])
    return out


# ----------------------------------------------------------------------
# state shared by every copy of this module in the process. The core loads each plugin's modules in an
# isolated namespace, so the NFL, NBA, NHL and portal plugins each hold their own copy of this file; the
# listening socket, the one-effect-at-a-time lock and the activity log must still be single.
# ----------------------------------------------------------------------

def _shared() -> Dict[str, Any]:
    reg = getattr(threading, "_govee_fx_shared", None)
    if reg is None:
        reg = {"guard": threading.Lock(), "play": threading.Lock(), "net": None, "log": deque(maxlen=60)}
        threading._govee_fx_shared = reg          # type: ignore[attr-defined]
    return reg


def record(sport: Optional[str], event: str, label: str, effect: str, status: str, detail: str = "") -> Dict[str, Any]:
    """Add a line to the activity log shown in the portal; returns it so its status can be updated later."""
    entry = {"time": time.time(), "sport": (sport or "").upper(), "event": event, "label": label,
             "effect": effect, "status": status, "detail": detail}
    _shared()["log"].appendleft(entry)
    return entry


def activity(limit: int = 30) -> List[Dict[str, Any]]:
    return [dict(e) for e in list(_shared()["log"])[:limit]]


# ----------------------------------------------------------------------
# LAN transport (one per process: every plugin shares the listening port)
# ----------------------------------------------------------------------

class LanTransport:
    _inst: Optional["LanTransport"] = None
    _guard = threading.Lock()

    @classmethod
    def get(cls) -> "LanTransport":
        reg = _shared()
        with reg["guard"]:
            if reg["net"] is None:
                reg["net"] = cls()
            return reg["net"]

    def __init__(self) -> None:
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.status: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self.found: Dict[str, str] = {}
        self.ids: Dict[str, str] = {}
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
                self.ids[body["ip"]] = body.get("device", "")
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

    def scan(self, timeout: float = 8.0, iface: Optional[str] = None) -> Dict[str, str]:
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

def segment_packet(rgb: RGB, mask: int) -> str:
    """Govee's 20-byte "segment colour" packet (0x33 0x05 0x15 0x01 R G B ... segment bitmask ... xor), base64."""
    data = [0x33, 0x05, 0x15, 0x01, rgb[0], rgb[1], rgb[2], 0, 0, 0, 0, 0, mask & 0xFF, (mask >> 8) & 0xFF]
    data += [0] * (19 - len(data))
    chk = 0
    for b in data:
        chk ^= b
    data.append(chk)
    return base64.b64encode(bytes(data)).decode()


def group_segments(colours: List[RGB]) -> Dict[RGB, int]:
    """{colour: bitmask of the segments that show it}, so one packet can colour many segments."""
    groups: Dict[RGB, int] = {}
    for i, c in enumerate(colours):
        groups[tuple(c)] = groups.get(tuple(c), 0) | (1 << i)      # type: ignore[index]
    return groups


class LanLight:
    kind = "lan"

    def __init__(self, address: str, segments: int = 1) -> None:
        host, _, port = address.partition(":")
        self.name, self.ip, self.port = address, host, int(port) if port else CONTROL_PORT
        self.segments = max(1, min(MAX_SEGMENTS, int(segments)))
        self.net = LanTransport.get()

    def show_segments(self, colours: List[RGB]) -> None:
        groups = group_segments(colours)
        if len(groups) == 1:                                       # one colour everywhere: the plain, dependable command
            self.show(next(iter(groups)))
            return
        self.net.send(self.ip, "ptReal", {"command": [segment_packet(c, m) for c, m in groups.items()]}, self.port)

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
        col = snap.get("color") or {}      # an "off" light still remembers its colour, so put that back first
        want_on = 1 if snap.get("onOff") else 0
        for _ in range(3):                 # UDP can drop a packet, and a light is deaf for a moment after a burst
            self.net.send(self.ip, "colorwc", {"color": {"r": col.get("r", 0), "g": col.get("g", 0), "b": col.get("b", 0)},
                                               "colorTemInKelvin": snap.get("colorTemInKelvin", 0)}, self.port)
            time.sleep(0.15)
            self.net.send(self.ip, "brightness", {"value": snap.get("brightness", 100)}, self.port)
            time.sleep(0.15)
            self.net.send(self.ip, "turn", {"value": want_on}, self.port)
            time.sleep(0.4)
            now = self.net.query(self.ip, self.port, timeout=1.0)
            if now is None or bool(now.get("onOff")) == bool(want_on):
                return


class HaLight:
    kind = "ha"

    def __init__(self, entity: str, url: str, token: str, session: Any = None, segments: int = 1) -> None:
        self.name, self.url, self.token = entity, url.rstrip("/"), token
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.segments = max(1, min(MAX_SEGMENTS, int(segments)))
        # Home Assistant's Govee integration names a light's sections <light>_segment_1 ... _segment_N
        self.parts = [HaLight("%s_segment_%d" % (entity, i + 1), url, token, session) for i in range(self.segments)] \
            if self.segments > 1 else []

    def show_segments(self, colours: List[RGB]) -> None:
        for colour, mask in group_segments(colours).items():
            ids = [self.parts[i].name for i in range(len(self.parts)) if mask >> i & 1]
            self.session.post("%s/api/services/light/turn_on" % self.url, headers=self._headers(), timeout=5,
                              json={"entity_id": ids, "rgb_color": list(colour), "brightness": 255})

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}

    def _call(self, service: str, data: Dict[str, Any]) -> None:
        data = dict(data, entity_id=self.name)
        self.session.post("%s/api/services/light/%s" % (self.url, service), headers=self._headers(), json=data, timeout=5)

    def snapshot(self) -> Optional[Dict[str, Any]]:
        if self.parts:
            return {"parts": [p.snapshot() for p in self.parts]}
        r = self.session.get("%s/api/states/%s" % (self.url, self.name), headers=self._headers(), timeout=5)
        if getattr(r, "status_code", 200) != 200:
            return None
        j = r.json()
        return {"state": j.get("state"), "attributes": j.get("attributes") or {}}

    def prepare(self) -> None:
        pass

    def show(self, rgb: RGB) -> None:
        if self.parts:                                  # touch only the sections, never the parent light
            self.show_segments([rgb] * len(self.parts))
            return
        self._call("turn_on", {"rgb_color": list(rgb), "brightness": 255})

    def restore(self, snap: Optional[Dict[str, Any]], fallback: str) -> None:
        if self.parts:
            for part, ps in zip(self.parts, (snap or {}).get("parts") or [None] * len(self.parts)):
                part.restore(ps, fallback)
            return
        if snap is None or snap.get("state") in (None, "unavailable", "unknown"):
            if fallback == "off":
                self._call("turn_off", {})
            return
        at = snap["attributes"]            # an off light's attributes are empty in HA, so only restore when on
        if snap["state"] == "off":
            self._call("turn_off", {})
            return
        data: Dict[str, Any] = {}
        if at.get("brightness") is not None:
            data["brightness"] = at["brightness"]
        if at.get("color_mode") == "color_temp" and at.get("color_temp_kelvin"):
            data["color_temp_kelvin"] = at["color_temp_kelvin"]
        elif at.get("rgb_color"):
            data["rgb_color"] = list(at["rgb_color"])
        if at.get("effect") not in (None, "", "None", "none"):         # a scene the light was playing
            data["effect"] = at["effect"]
        self._call("turn_on", data)


# ----------------------------------------------------------------------
# central config: one file for every sports plugin, written by the govee-lights portal
# ----------------------------------------------------------------------

_LAN_RE = re.compile(r"^(10\.\d{1,3}|192\.168|127\.\d{1,3}|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(:\d{1,5})?$")
_HA_RE = re.compile(r"^light\.[a-z0-9_]{1,80}$")
_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")
_SPORT_RE = re.compile(r"^[a-z0-9_-]{2,20}$")
DEFAULT_SPEC: Dict[str, Dict[str, Any]] = {
    "score": {"enabled": True, "effect": "burst", "seconds": 6.0, "colors": "team"},
    "big_score": {"enabled": True, "effect": "fireworks", "seconds": 10.0, "colors": "team"},
    "against": {"enabled": False, "effect": "pulse", "seconds": 4.0, "colors": "team"},
}


def default_central_path() -> Optional[Path]:
    """<LEDMatrix>/config/govee_fx.json, found from the plugin's own folder (plugin-repos/<plugin>/)."""
    env = os.environ.get("LEDMATRIX_GOVEE_CONFIG")
    if env:
        return Path(env)
    try:
        return Path(__file__).resolve().parents[2] / "config" / "govee_fx.json"
    except IndexError:
        return None


def valid_light_id(value: Any) -> bool:
    v = str(value or "")
    return bool(_LAN_RE.match(v) or _HA_RE.match(v))


def clean_event(raw: Any, base: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    if not isinstance(raw, dict):
        return out
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    if raw.get("effect") in PICKABLE:
        out["effect"] = raw["effect"]
    try:
        if "seconds" in raw:
            out["seconds"] = max(2.0, min(30.0, float(raw["seconds"])))
    except (TypeError, ValueError):
        pass
    col = raw.get("colors")
    if col == "team":
        out["colors"] = "team"
    elif isinstance(col, dict) and _HEX_RE.match(str(col.get("primary", ""))):
        acc = str(col.get("accent", "ffffff"))
        out["colors"] = {"primary": str(col["primary"]).lstrip("#").lower(),
                         "accent": (acc if _HEX_RE.match(acc) else "ffffff").lstrip("#").lower()}
    return out


def clean_config(raw: Any) -> Dict[str, Any]:
    """Whatever the portal (or a hand edit) wrote, reduced to a safe, complete config."""
    raw = raw if isinstance(raw, dict) else {}
    lights: List[Dict[str, Any]] = []
    seen = set()
    for l in raw.get("lights") or []:
        if not isinstance(l, dict) or not valid_light_id(l.get("id")) or l["id"] in seen:
            continue
        seen.add(l["id"])
        try:
            nseg = max(1, min(MAX_SEGMENTS, int(l.get("segments", 1))))
        except (TypeError, ValueError):
            nseg = 1
        lights.append({"id": l["id"], "label": str(l.get("label") or "")[:40], "enabled": bool(l.get("enabled", True)),
                       "model": str(l.get("model") or "")[:20], "segments": nseg, "flip": bool(l.get("flip", False))})
        if len(lights) >= 40:
            break
    sports = {k: True for k in KNOWN_SPORTS}
    for k, v in (raw.get("sports") or {}).items():
        if _SPORT_RE.match(str(k)):
            sports[str(k)] = bool(v)
    events = {k: clean_event((raw.get("events") or {}).get(k), v) for k, v in DEFAULT_SPEC.items()}
    sport_events: Dict[str, Dict[str, Any]] = {}
    for sp, evs in (raw.get("sport_events") or {}).items():
        if not _SPORT_RE.match(str(sp)) or not isinstance(evs, dict):
            continue
        for ev, spec in evs.items():
            if ev in DEFAULT_SPEC and isinstance(spec, dict):
                sport_events.setdefault(str(sp), {})[ev] = clean_event(spec, events[ev])

    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            return max(lo, min(hi, float(raw.get(key, default))))
        except (TypeError, ValueError):
            return default

    fb = str(raw.get("restore_fallback", "off"))
    return {"enabled": bool(raw.get("enabled", False)), "ha_source": str(raw.get("ha_source") or "")[:40],
            "lights": lights, "sports": sports, "events": events, "sport_events": sport_events,
            "cooldown_seconds": num("cooldown_seconds", MIN_GAP, 0, 60), "stagger_seconds": num("stagger_seconds", 0.15, 0, 2),
            "restore_fallback": fb if fb in ("off", "warm", "leave") else "off"}


def ha_credentials(lm_config: Optional[Path], source: str = "") -> Tuple[str, str]:
    """Home Assistant address and token, read from LEDMatrix's own config.json so the portal never stores a copy."""
    try:
        blocks = json.loads(Path(lm_config).read_text(encoding="utf-8")) if lm_config else {}
    except (OSError, ValueError):
        return "", ""
    order = ([source] if source else []) + ["house-hud", "ha-notifications", "media-marquee", "sky-weather"]
    order += [k for k in blocks if k not in order]
    for k in order:
        b = blocks.get(k)
        if isinstance(b, dict) and b.get("ha_url") and b.get("ha_token"):
            return str(b["ha_url"]), str(b["ha_token"])
    return "", ""


def central_to_cfg(raw: Any, sport: Optional[str], lm_config: Optional[Path] = None) -> Dict[str, Any]:
    """The central file as the plain config GoveeFX applies, for one sport."""
    c = clean_config(raw)
    events: Dict[str, Any] = {}
    colors: Dict[str, Any] = {}
    for ev in EVENT_NAMES:
        spec = dict(c["events"][ev])
        spec.update(c["sport_events"].get(sport or "", {}).get(ev, {}))
        events[ev] = {"effect": spec["effect"] if spec["enabled"] else "off", "seconds": spec["seconds"]}
        if isinstance(spec["colors"], dict):
            colors[ev] = spec["colors"]
    url, token = ha_credentials(lm_config, c["ha_source"])
    return {"enabled": c["enabled"] and (sport is None or c["sports"].get(sport, True)),
            "devices": [l["id"] for l in c["lights"] if l["enabled"]], "events": events, "colors": colors,
            "segments": {l["id"]: l["segments"] for l in c["lights"]},
            "flips": {l["id"]: True for l in c["lights"] if l["flip"]},
            "ha_url": url, "ha_token": token, "cooldown_seconds": c["cooldown_seconds"],
            "stagger_seconds": c["stagger_seconds"], "restore_fallback": c["restore_fallback"]}


def save_central(path: Path, raw: Any) -> Dict[str, Any]:
    """Validate and write atomically, keeping the previous file as .bak."""
    clean = clean_config(raw)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    if path.exists():
        try:
            path.replace(path.with_suffix(".json.bak"))
        except OSError:
            pass
    tmp.replace(path)
    return clean


# ----------------------------------------------------------------------
# discovery and status, for the portal
# ----------------------------------------------------------------------

def discover_lan(timeout: float = 8.0, iface: Optional[str] = None) -> List[Dict[str, str]]:
    found = LanTransport.get().scan(timeout, iface)
    return [{"id": ip, "model": sku} for ip, sku in sorted(found.items(), key=lambda kv: tuple(int(x) for x in kv[0].split(".")))]


_SEGMENT_RE = re.compile(r"(_segments?|_segment_\d+)$")


def list_ha_lights(url: str, token: str, govee_only: bool = True, session: Any = None) -> List[Dict[str, str]]:
    """Light entities (never the per-segment ones). With govee_only, just those from Home Assistant's Govee integration."""
    import requests
    s = session or requests
    hd = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    base = url.rstrip("/")
    keep = None
    if govee_only:
        try:
            r = s.post(base + "/api/template", headers=hd, timeout=10,
                       json={"template": "{{ integration_entities('govee') | select('match','light[.]') | list | tojson }}"})
            if r.status_code == 200:
                keep = set(json.loads(r.text))
        except (OSError, ValueError, requests.RequestException):
            keep = None
    r = s.get(base + "/api/states", headers=hd, timeout=15)
    states = r.json()
    counts: Dict[str, int] = {}
    for st in states:                                       # <light>_segment_N entities say how many sections it has
        m = re.match(r"^(light\..+?)_segment_(\d+)$", st.get("entity_id", ""))
        if m:
            counts[m.group(1)] = max(counts.get(m.group(1), 0), int(m.group(2)))
    out = []
    for st in states:
        eid = st.get("entity_id", "")
        if not eid.startswith("light.") or _SEGMENT_RE.search(eid):
            continue
        if keep is not None and eid not in keep:
            continue
        out.append({"id": eid, "name": (st.get("attributes") or {}).get("friendly_name") or eid, "state": st.get("state", ""),
                    "segments": min(MAX_SEGMENTS, counts.get(eid, 1))})
    return sorted(out, key=lambda x: x["name"].lower())


def light_status(ids: List[str], url: str = "", token: str = "", session: Any = None) -> Dict[str, Dict[str, Any]]:
    """{id: {"ok": reachable, "on": bool|None}}, asked in parallel so one dead light does not stall the rest."""
    result: Dict[str, Dict[str, Any]] = {}

    def ask(i: str) -> None:
        try:
            if i.startswith("light."):
                snap = HaLight(i, url, token, session).snapshot() if url and token else None
                st = (snap or {}).get("state")
                result[i] = {"ok": st not in (None, "unavailable", "unknown"), "on": (st == "on") if st in ("on", "off") else None}
            else:
                snap = LanLight(i).snapshot()
                result[i] = {"ok": snap is not None, "on": bool(snap.get("onOff")) if snap else None}
        except Exception:  # noqa: BLE001
            result[i] = {"ok": False, "on": None}

    threads = [threading.Thread(target=ask, args=(i,), daemon=True) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=8.0)
    for i in ids:
        result.setdefault(i, {"ok": False, "on": None})
    return result


_PLAY_LOCK = _shared()["play"]          # one effect at a time across every plugin in this process


# ----------------------------------------------------------------------
# engine
# ----------------------------------------------------------------------

class GoveeFX:
    """Plays effects on a worker thread. trigger() is safe to call from a plugin's display loop."""

    time_scale = 1.0           # tests set this above 1 to run quickly

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, logger: Optional[logging.Logger] = None,
                 session: Any = None, sport: Optional[str] = None, central: Any = None) -> None:
        """cfg is the plugin's own `govee` block. If the portal's central file exists it wins, and is re-read
        whenever it changes. central=False ignores any central file; a path uses that file."""
        self.log = logger or logging.getLogger("govee_fx")
        self._session = session
        self._cv = threading.Condition()
        self._job: Optional[Tuple[str, RGB, RGB, float, Dict[str, Any]]] = None
        self._gen = 0
        self._stop = False
        self._last_end = 0.0
        self._snaps: Optional[List[Optional[Dict[str, Any]]]] = None
        self.lights: List[Any] = []
        self.events: Dict[str, Dict[str, Any]] = {}
        self.enabled = False
        self.history: List[str] = []
        self._thread: Optional[threading.Thread] = None
        self.idle = threading.Event()
        self.idle.set()
        self.sport = sport
        self._plugin_cfg: Dict[str, Any] = cfg if isinstance(cfg, dict) else {}
        self._central: Optional[Path] = None if central is False else (Path(central) if central else default_central_path())
        self._central_mtime: Optional[float] = None
        self._sync_central(force=True)

    # -- config ---------------------------------------------------------

    def configure(self, cfg: Optional[Dict[str, Any]]) -> None:
        """The plugin's own block changed."""
        self._plugin_cfg = cfg if isinstance(cfg, dict) else {}
        self._sync_central(force=True)

    def _sync_central(self, force: bool = False) -> None:
        """Use the portal's file when there is one, picking up edits as they are saved."""
        try:
            mtime = self._central.stat().st_mtime if self._central else None
        except OSError:
            mtime = None
        if mtime is None:
            if force or self._central_mtime is not None:
                self._central_mtime = None
                self._apply(self._plugin_cfg)
            return
        if mtime == self._central_mtime and not force:
            return
        try:
            raw = json.loads(self._central.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.log.warning("govee: could not read %s: %s", self._central, exc)
            if self._central_mtime is None:
                self._apply(self._plugin_cfg)
            self._central_mtime = mtime
            return
        self._central_mtime = mtime
        self._apply(central_to_cfg(raw, self.sport, self._central.parent / "config.json"))

    def _apply(self, cfg: Optional[Dict[str, Any]]) -> None:
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
        seg_map = cfg.get("segments") if isinstance(cfg.get("segments"), dict) else {}
        flip_map = cfg.get("flips") if isinstance(cfg.get("flips"), dict) else {}
        for entry in cfg.get("devices") or []:
            entry = str(entry).strip()
            try:
                nseg = max(1, min(MAX_SEGMENTS, int(seg_map.get(entry, 1))))
            except (TypeError, ValueError):
                nseg = 1
            try:
                if entry.startswith("light."):
                    if not (cfg.get("ha_url") and cfg.get("ha_token")):
                        self.log.warning("govee: %s needs ha_url and ha_token", entry)
                        continue
                    self.lights.append(HaLight(entry, cfg["ha_url"], cfg["ha_token"], self._session, nseg))
                elif entry:
                    self.lights.append(LanLight(entry, nseg))
                if self.lights and entry in flip_map and self.lights[-1].name == entry:
                    self.lights[-1].flip = bool(flip_map[entry])
            except Exception as exc:  # noqa: BLE001 - a bad entry must not stop the plugin
                self.log.warning("govee: could not set up %s: %s", entry, exc)
        if self.lights and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="govee-fx", daemon=True)
            self._thread.start()

    # -- API ------------------------------------------------------------

    def trigger(self, event: str, primary: Any = "ffffff", accent: Any = "ffffff", label: str = "") -> bool:
        """Queue an effect for an event ('score', 'big_score', 'against', ...). Returns whether one was queued.
        label says what happened ('PHI touchdown'); it only appears in the portal's activity list."""
        try:
            self._sync_central()
            spec = self.events.get(event)
            why = ""
            if not self.enabled:
                why = "lights are switched off (master switch, or this sport)"
            elif not self.lights:
                why = "no light is switched on"
            elif not spec or spec.get("effect", "off") == "off":
                why = "that moment is switched off"
            elif time.monotonic() - self._last_end < self.cooldown and event != "big_score":
                why = "cooldown (another effect just played)"
            if why:
                record(self.sport, event, label, (spec or {}).get("effect", ""), "skipped", why)
                self.log.info("govee[%s]: %s %s skipped: %s", self.sport or "-", event, label, why)
                return False
            over = self.colors.get(event) if isinstance(self.colors.get(event), dict) else {}
            if over.get("primary"):                  # colours the user picked are used as picked
                p, a = parse_color(over["primary"]), parse_color(over.get("accent", "ffffff"))
            else:
                p, a = team_colors(primary, accent)
            entry = record(self.sport, event, label, str(spec.get("effect", "burst")), "playing",
                           "%d light%s" % (len(self.lights), "" if len(self.lights) == 1 else "s"))
            self.log.info("govee[%s]: %s %s -> %s on %d light(s)", self.sport or "-", event, label, spec.get("effect"), len(self.lights))
            with self._cv:
                self._job = (str(spec.get("effect", "burst")), p, a, float(spec.get("seconds", 6)), entry)
                self._gen += 1
                self.idle.clear()
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
        self.idle.set()

    def wait(self, timeout: float = 30.0) -> bool:
        """Block until the current effect has finished and the lights are restored."""
        return self.idle.wait(timeout)

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
            with _PLAY_LOCK:
                self._play(job, gen)

    def _play(self, job: Tuple[str, RGB, RGB, float, Dict[str, Any]], gen: int) -> None:
        effect, p, a, dur, entry = job
        lights = self.lights                # a config reload mid-effect must not change who we restore
        if self._snaps is None:
            self._snaps = [self._safe(l.snapshot) for l in lights]
        for l in lights:
            self._safe(l.prepare)
        steps = timeline(effect, p, a, dur, len(lights), self.stagger, [getattr(l, "segments", 1) for l in lights])
        start = time.monotonic()
        for t, i, rgb, last in steps:
            if not self._sleep_until(start + t / self.time_scale, gen):
                entry["status"] = "interrupted"
                return                      # a newer effect (or close) took over; it will restore
            if not last and time.monotonic() - (start + t / self.time_scale) > 0.5:
                continue                    # running behind (slow Home Assistant): skip, catch up
            if isinstance(rgb, list):
                self._safe(lights[i].show_segments, rgb[::-1] if getattr(lights[i], "flip", False) else rgb)
            else:
                self._safe(lights[i].show, rgb)
        if self._gen != gen:
            return
        time.sleep(0.25 / self.time_scale)
        snaps, self._snaps = self._snaps, None
        for l, s in zip(lights, snaps or []):
            self._safe(l.restore, s, self.fallback)
        self._last_end = time.monotonic()
        entry["status"] = "done"
        if self._gen == gen:
            self.idle.set()


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
        found = LanTransport.get().scan(8.0, args.iface)
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
