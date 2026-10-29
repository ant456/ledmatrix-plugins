"""A made-up print job for ``test_mode``: one pass through every screen, no printer needed.

    0-6s   preparing (heating the nozzle)    46-52s  PRINT DONE! burst
    6-10s  preparing (levelling the bed)     52-58s  done card
    10-30s printing 0 -> 60 %                58-64s  print failed
    30-36s paused (filament runout)
    36-46s printing 60 -> 100 %
"""

from typing import Any, Dict, Tuple

import printmon_data as pd

CYCLE = 64.0
BASE = {"task": "esp32_strobe_mount_133x70", "layers": "100", "weight": "22.84", "online": "on", "error": "off",
        "hms": "off", "color": "#995F11FF", "cover": "demo", "bed_t": "60", "nozzle_t": "220"}


def snapshot(t: float) -> Tuple[Dict[str, Any], float, float, float]:
    """(state, burst_age, done_age, failed_age) at ``t`` seconds into the demo; an age of -1 means not active."""
    t = t % CYCLE
    raw = dict(BASE, status="running", stage="printing", progress="0", remaining_h="1.5", layer="0",
               nozzle="220", bed="60")
    burst = done = failed = -1.0
    if t < 6:
        raw.update(status="prepare", stage="heating_hotend", nozzle=str(int(40 + 180 * t / 6)),
                   bed=str(int(35 + 25 * min(1.0, t / 4))))
    elif t < 10:
        raw.update(status="prepare", stage="auto_bed_leveling")
    elif t < 46:
        pause = 30 <= t < 36
        prog = 60.0 if 30 <= t < 36 else (6 + (t - 10) * 3.0 if t < 30 else 60 + (t - 36) * 4.0)
        raw.update(progress=str(int(min(100, prog))), layer=str(int(min(100, prog))),
                   remaining_h=str(round(1.5 * (1 - prog / 100.0), 2)))
        if pause:
            raw.update(status="pause", stage="paused_filament_runout")
    elif t < 58:
        raw.update(status="finish", stage="idle", progress="100", layer="100", remaining_h="0",
                   nozzle="60", bed="45", nozzle_t="0", bed_t="0")
        if t < 52:
            burst = t - 46
        done = t - 46
    else:
        raw.update(status="failed", stage="idle", progress="8", layer="8", nozzle="150", bed="60")
        failed = t - 58
    return pd.parse(raw), burst, done, failed
