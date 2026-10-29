"""Print Monitor data: one Home Assistant call per poll, and what the printer is doing.

The Bambu Lab integration exposes the printer as entities that share a prefix, e.g.
``sensor.p1s_YOURSERIAL_print_progress``. A single call to HA's template API
reads everything the screens need, so a poll is one small request.
"""

import json
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import urllib.request

RGB = Tuple[int, int, int]

# Phases the plugin shows.
PRINTING, PREPARING, PAUSED, FAILED, DONE, IDLE, OFFLINE = (
    "PRINTING", "PREPARING", "PAUSED", "FAILED", "DONE", "IDLE", "OFFLINE")

TEMPLATE = (
    '{% set p = "{P}_" %}'
    '{% set ns = namespace(color="") %}'
    '{% for i in range(1, 5) %}{% if state_attr("sensor." ~ p ~ "ams_1_tray_" ~ i, "active") %}'
    '{% set ns.color = state_attr("sensor." ~ p ~ "ams_1_tray_" ~ i, "color") or "" %}{% endif %}{% endfor %}'
    '{{ {'
    '"status": states("sensor." ~ p ~ "print_status"),'
    '"stage": states("sensor." ~ p ~ "current_stage"),'
    '"progress": states("sensor." ~ p ~ "print_progress"),'
    '"remaining_h": states("sensor." ~ p ~ "remaining_time"),'
    '"layer": states("sensor." ~ p ~ "current_layer"),'
    '"layers": states("sensor." ~ p ~ "total_layer_count"),'
    '"task": states("sensor." ~ p ~ "task_name"),'
    '"nozzle": states("sensor." ~ p ~ "nozzle_temperature"),'
    '"nozzle_t": states("sensor." ~ p ~ "nozzle_target_temperature"),'
    '"bed": states("sensor." ~ p ~ "bed_temperature"),'
    '"bed_t": states("sensor." ~ p ~ "bed_target_temperature"),'
    '"weight": states("sensor." ~ p ~ "print_weight"),'
    '"end": states("sensor." ~ p ~ "end_time"),'
    '"error": states("binary_sensor." ~ p ~ "print_error"),'
    '"hms": states("binary_sensor." ~ p ~ "hms_errors"),'
    '"online": states("binary_sensor." ~ p ~ "online"),'
    '"cover": states("image." ~ p ~ "cover_image"),'
    '"color": ns.color'
    '} | to_json }}'
)

PREP_LABELS = {
    "heating_hotend": "HEATING NOZZLE", "heatbed_preheating": "HEATING BED",
    "waiting_for_heatbed_temperature": "WAITING FOR BED", "checking_extruder_temperature": "CHECKING NOZZLE",
    "auto_bed_leveling": "LEVELING BED", "bed_level_phase_1": "LEVELING BED", "bed_level_phase_2": "LEVELING BED",
    "homing_toolhead": "HOMING", "cleaning_nozzle_tip": "CLEANING NOZZLE", "scanning_bed_surface": "SCANNING BED",
    "changing_filament": "CHANGING FILAMENT", "filament_loading": "LOADING FILAMENT",
    "filament_unloading": "UNLOADING FILAMENT", "preparing_ams": "PREPARING AMS",
    "inspecting_first_layer": "CHECKING 1ST LAYER", "thermal_preconditioning": "PRECONDITIONING",
    "identifying_build_plate_type": "CHECKING PLATE", "heating_chamber": "HEATING CHAMBER",
    "cooling_chamber": "COOLING CHAMBER", "preparing_hotend": "PREPARING NOZZLE",
    "pre_extrusion_before_printing": "PRIMING NOZZLE", "print_calibration_lines": "CALIBRATION LINES",
    "calibrating_extrusion_flow": "CALIBRATING FLOW", "calibrating_extrusion": "CALIBRATING FLOW",
}
PAUSE_LABELS = {
    "paused_filament_runout": "FILAMENT RUNOUT", "paused_nozzle_clog": "NOZZLE CLOG",
    "paused_user": "PAUSED BY USER", "paused_front_cover_falling": "FRONT COVER OFF",
    "paused_ams_lost": "AMS LOST", "paused_first_layer_error": "FIRST LAYER ERROR",
    "paused_cutter_error": "CUTTER ERROR", "paused_nozzle_temperature_malfunction": "NOZZLE TEMP FAULT",
    "paused_heat_bed_temperature_malfunction": "BED TEMP FAULT", "paused_skipped_step": "SKIPPED STEP",
    "paused_low_fan_speed_heat_break": "LOW FAN SPEED", "paused_user_gcode": "PAUSED BY GCODE",
    "m400_pause": "PAUSED BY GCODE",
}


def num(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "unknown", "unavailable", "none"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_color(value: Any) -> Optional[RGB]:
    """'#995F11FF' (RRGGBBAA) to (r, g, b)."""
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?", value.strip())
    if not m:
        return None
    h = m.group(1)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def parse_time(value: Any) -> Optional[float]:
    if not isinstance(value, str) or value in ("unknown", "unavailable", ""):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse(raw: Dict[str, Any]) -> Dict[str, Any]:
    """The template's JSON, with numbers as numbers and unknowns as None."""
    rem = num(raw.get("remaining_h"))
    task = str(raw.get("task") or "")
    return {
        "status": str(raw.get("status") or "unknown"),
        "stage": str(raw.get("stage") or "unknown"),
        "progress": num(raw.get("progress")),
        "remaining_s": None if rem is None else rem * 3600.0,
        "layer": num(raw.get("layer")),
        "layers": num(raw.get("layers")),
        "task": "" if task in ("unknown", "unavailable") else task,
        "nozzle": num(raw.get("nozzle")), "nozzle_t": num(raw.get("nozzle_t")),
        "bed": num(raw.get("bed")), "bed_t": num(raw.get("bed_t")),
        "weight": num(raw.get("weight")),
        "end": parse_time(raw.get("end")),
        "error": str(raw.get("error")) == "on",
        "hms": str(raw.get("hms")) == "on",
        "online": str(raw.get("online")) != "off",
        "cover": str(raw.get("cover") or ""),
        "color": parse_color(raw.get("color")),
    }


def phase(d: Dict[str, Any]) -> str:
    """What the printer is doing, from the parsed state."""
    status, stage = d["status"], d["stage"]
    if not d["online"] or status == "offline":
        return OFFLINE
    if status == "failed" or d["error"]:
        return FAILED
    if status == "pause" or stage.startswith("paused_") or stage == "m400_pause":
        return PAUSED
    if status == "running":
        return PRINTING if stage in ("printing", "idle", "unknown") else PREPARING
    if status in ("prepare", "init", "slicing"):
        return PREPARING
    if status == "finish":
        return DONE
    return IDLE


def prep_label(d: Dict[str, Any]) -> str:
    if d["status"] == "slicing":
        return "SLICING"
    label = PREP_LABELS.get(d["stage"])
    if label:
        return label
    if d["stage"].startswith("calibrat"):
        return "CALIBRATING"
    if d["stage"] in ("idle", "unknown", "printing"):
        return "PREPARING"
    return d["stage"].replace("_", " ").upper()


def pause_label(d: Dict[str, Any]) -> str:
    if d["error"]:
        return "PRINT ERROR"
    return PAUSE_LABELS.get(d["stage"], "PAUSED")


def fmt_remaining(secs: Optional[float]) -> str:
    if secs is None:
        return "--"
    mins = int(round(secs / 60.0))
    if mins <= 0:
        return "<1M"
    if mins < 60:
        return "%dM" % mins
    return "%dH%02dM" % (mins // 60, mins % 60)


def fmt_clock(ts: Optional[float]) -> str:
    """Local time of day, e.g. 6:42P."""
    if not ts:
        return "--"
    t = time.localtime(ts)
    hour = t.tm_hour % 12 or 12
    return "%d:%02d%s" % (hour, t.tm_min, "A" if t.tm_hour < 12 else "P")


def eta(d: Dict[str, Any], now: float) -> Optional[float]:
    """When the print should end: now plus the printer's remaining time."""
    if d["remaining_s"] is None:
        return None
    return now + d["remaining_s"]


def fetch(ha_url: str, token: str, prefix: str, timeout: float = 8.0) -> Dict[str, Any]:
    body = json.dumps({"template": TEMPLATE.replace("{P}", prefix)}).encode()
    req = urllib.request.Request(ha_url.rstrip("/") + "/api/template", data=body, headers={
        "Authorization": "Bearer %s" % token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - the user's own HA URL
        return parse(json.loads(resp.read().decode("utf-8")))


def fetch_cover(ha_url: str, token: str, prefix: str, timeout: float = 10.0, limit: int = 2_000_000) -> bytes:
    req = urllib.request.Request("%s/api/image_proxy/image.%s_cover_image" % (ha_url.rstrip("/"), prefix),
                                 headers={"Authorization": "Bearer %s" % token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("cover image larger than %d bytes" % limit)
    return data
