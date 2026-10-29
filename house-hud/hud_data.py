"""House HUD data: what each tile asks Home Assistant, how it reads the answer, and what counts as an event.

Tiles are described in config (see DEFAULT_SCREENS). One call to HA's template API per poll reads every
tile, so a poll is a single small request.

Tile types
    lock      an HA lock                       LOCK / OPEN
    opening   a door or window sensor          SHUT / OPEN
    safe      a list of alarms (binary_sensor) SAFE / ALARM when any is on
    presence  a person or device_tracker       HOME / AWAY
    temp      a temperature (state, or an attribute such as a climate's current_temperature)
    count     how many entities of a domain are in a state (e.g. lights that are on)
"""

import json
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

RGB = Tuple[int, int, int]

GREEN: RGB = (70, 240, 126)
ORANGE: RGB = (255, 146, 40)
RED: RGB = (255, 50, 70)
YELLOW: RGB = (255, 214, 60)
CYAN: RGB = (70, 200, 255)
BLUE: RGB = (90, 140, 255)
DIM: RGB = (70, 76, 92)

DEFAULT_SCREENS: List[Dict[str, Any]] = [
    {"tiles": [
        {"type": "lock", "entity": "lock.front_door", "label": "FRONT"},
        {"type": "opening", "entity": "binary_sensor.front_door_sensor", "label": "DOOR"},
        {"type": "safe", "label": "SMOKE", "entities": [
            "binary_sensor.avas_room_alarm_status", "binary_sensor.basement_alarm_status",
            "binary_sensor.dining_room_alarm_status", "binary_sensor.upstairs_hall_alarm_status",
            "binary_sensor.smoke_alarm_alarm_status"]},
        {"type": "presence", "entity": "person.your_name", "label": "HOME"},
    ]},
    {"tiles": [
        {"type": "temp", "entity": "sensor.nest_thermostat_temperature", "label": "INSIDE", "icon": "thermo"},
        {"type": "temp", "entity": "sensor.openweathermap_temperature", "label": "OUTSIDE", "icon": "sun"},
        {"type": "count", "domain": "light", "state": "on", "label": "LIGHTS"},
        {"type": "temp", "entity": "climate.hot_tub_spa_thermostat", "attr": "current_temperature",
         "label": "TUB", "icon": "tub"},
    ]},
]

_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_WORD = re.compile(r"^[a-z_]+$")
NA = ("unavailable", "unknown", "", "none", None)


def clean_screens(screens: Any) -> List[Dict[str, Any]]:
    """Keep only well-formed tiles (entity ids go into a template, so they are validated)."""
    out = []
    for screen in screens if isinstance(screens, list) and screens else DEFAULT_SCREENS:
        tiles = []
        for t in (screen.get("tiles") if isinstance(screen, dict) else None) or []:
            if not isinstance(t, dict):
                continue
            kind = t.get("type")
            ents = t.get("entities") if kind == "safe" else [t.get("entity")]
            if kind in ("lock", "opening", "presence", "temp", "safe"):
                if not ents or not all(isinstance(e, str) and _ENTITY.match(e) for e in ents):
                    continue
            elif kind == "count":
                if not (isinstance(t.get("domain"), str) and _WORD.match(t["domain"])
                        and isinstance(t.get("state", "on"), str) and re.match(r"^[a-z_]+$", t.get("state", "on"))):
                    continue
            else:
                continue
            attr = t.get("attr")
            if attr is not None and not (isinstance(attr, str) and _WORD.match(attr)):
                continue
            tiles.append(dict(t, label=str(t.get("label") or "").upper()[:7]))
        if tiles:
            out.append({"tiles": tiles[:4]})
    return out or clean_screens(None)


def _expr(t: Dict[str, Any]) -> str:
    kind = t["type"]
    if kind in ("lock", "opening", "presence"):
        return 'states("%s")' % t["entity"]
    if kind == "safe":
        ids = json.dumps(t["entities"])
        return ('{"on": (%s | select("is_state", "on") | list | count), '
                '"na": (%s | select("is_state", "unavailable") | list | count), "n": %d}' % (ids, ids, len(t["entities"])))
    if kind == "temp":
        if t.get("attr"):
            return 'state_attr("%s", "%s")' % (t["entity"], t["attr"])
        return 'states("%s")' % t["entity"]
    return '(states.%s | selectattr("state", "eq", "%s") | list | count)' % (t["domain"], t.get("state", "on"))


def build_template(screens: List[Dict[str, Any]]) -> str:
    items = []
    for si, screen in enumerate(screens):
        for ti, t in enumerate(screen["tiles"]):
            items.append('"s%dt%d": %s' % (si, ti, _expr(t)))
    return "{{ {%s} | to_json }}" % ", ".join(items)


def num(value: Any) -> Optional[float]:
    try:
        if value in NA:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def temp_color(v: float) -> RGB:
    if v < 45:
        return BLUE
    if v < 65:
        return CYAN
    if v < 78:
        return GREEN
    if v < 90:
        return YELLOW
    return ORANGE


def view(t: Dict[str, Any], raw: Any) -> Dict[str, Any]:
    """How a tile looks for a raw answer: icon, label, a word or a number, and a colour."""
    kind, label = t["type"], t["label"]
    out: Dict[str, Any] = {"label": label, "word": None, "number": None, "color": DIM, "blink": False, "icon": kind}
    if kind == "lock":
        s = str(raw)
        out["icon"] = "lock_open" if s in ("unlocked", "unlocking", "open", "jammed") else "lock"
        if s == "locked":
            out.update(word="LOCK", color=GREEN)
        elif s in ("unlocked", "unlocking", "open"):
            out.update(word="OPEN", color=ORANGE)
        elif s == "jammed":
            out.update(word="JAM", color=RED, blink=True)
        else:
            out.update(word="N/A")
    elif kind == "opening":
        s = str(raw)
        out["icon"] = "door_open" if s == "on" else "door"
        if s == "on":
            out.update(word="OPEN", color=ORANGE)
        elif s == "off":
            out.update(word="SHUT", color=GREEN)
        else:
            out.update(word="N/A")
    elif kind == "safe":
        r = raw if isinstance(raw, dict) else {}
        on, na, n = r.get("on", 0), r.get("na", 0), r.get("n", 0)
        out["icon"] = "smoke"
        if on:
            out.update(word="ALARM", color=RED, blink=True)
        elif n and na >= n:
            out.update(word="N/A")
        else:
            out.update(word="SAFE", color=GREEN)
    elif kind == "presence":
        s = str(raw)
        out["icon"] = "house"
        if s == "home":
            out.update(word="HOME", color=GREEN)
        elif s in NA:
            out.update(word="N/A")
        else:
            out.update(word="AWAY", color=DIM)
    elif kind == "temp":
        out["icon"] = t.get("icon", "thermo")
        v = num(raw)
        if v is None:
            out.update(word="N/A")
        else:
            out.update(number=v, color=temp_color(v), ring=True)
    else:                                                   # count
        out["icon"] = t.get("icon", "bulb")
        v = num(raw)
        if v is None:
            out.update(word="N/A")
        else:
            out.update(number=v, color=YELLOW if v > 0 else DIM)
    return out


def events(tiles: List[Dict[str, Any]], prev: Dict[str, Any], cur: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What just happened, by comparing the last poll with this one. ``tiles`` maps key -> tile config."""
    found = []
    for key, t in tiles.items():
        if key not in prev or key not in cur:
            continue
        a, b = prev[key], cur[key]
        label = t["label"]
        if t["type"] == "lock":
            if str(a) == "locked" and str(b) in ("unlocked", "unlocking"):
                found.append({"top": label, "text": "UNLOCKED!", "color": ORANGE})
            elif str(a) in ("unlocked", "unlocking") and str(b) == "locked":
                found.append({"top": label, "text": "LOCKED!", "color": GREEN})
        elif t["type"] == "opening":
            if str(a) == "off" and str(b) == "on":
                found.append({"top": label, "text": "OPENED!", "color": ORANGE})
        elif t["type"] == "presence":
            if str(a) != "home" and str(a) not in NA and str(b) == "home":
                found.append({"top": "", "text": "WELCOME HOME!", "color": GREEN})
    return found


def fetch(ha_url: str, token: str, template: str, timeout: float = 8.0) -> Dict[str, Any]:
    req = urllib.request.Request(ha_url.rstrip("/") + "/api/template",
                                 data=json.dumps({"template": template}).encode(),
                                 headers={"Authorization": "Bearer %s" % token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - the user's own HA URL
        return json.loads(resp.read().decode("utf-8"))
