# Waze Travel Time (arcade)

<!-- hero -->
**Your commute as a traffic light: how long it takes, and how bad it is.**

A big number, the word MIN, and a verdict. The drive-time screen compares your live time against free-flow traffic and colours the whole panel to match: green CLEAR, orange HEAVY, red JAM, with the minutes of delay (+26) beside it, a little car sprite, a trend arrow, and a segmented bar that fills as traffic builds.

Waze retired its public routing service in 2026, so the data now comes from Home Assistant's **HERE Travel Time** sensors (a live-traffic sensor plus a free-flow sensor). When a route turns heavy, the plugin polls faster so you notice early.

- Drive time in large 10x20 digits with a centred MIN
- CLEAR / BUSY / HEAVY / JAM verdicts from free-flow comparison
- Delay minutes, trend arrow and severity bar
- Learns your best time of day
- Burst polling when a route worsens
- Several destinations page automatically; a single destination gets the full-height layout

<p align="center"><img src="screenshots/01-clear.png" width="768" alt="Clear road"><br><sub>Clear road</sub></p>
<p align="center"><img src="screenshots/02-heavy.png" width="768" alt="Heavy traffic"><br><sub>Heavy traffic</sub></p>
<p align="center"><img src="screenshots/03-jam.png" width="768" alt="Jam, with the delay"><br><sub>Jam, with the delay</sub></p>
<p align="center"><img src="screenshots/04-offline.png" width="768" alt="Sensor offline"><br><sub>Sensor offline</sub></p>

*Screenshots are real frames from the plugin's renderer, shown 8x on a 128x32 panel.*
<!-- /hero -->

Drive times on a 128x32 LEDMatrix panel, from Home Assistant travel-time sensors (HERE Travel Time; Waze retired its routing API in 2026).

Each destination is a row:

```
[car] WORK      [######..]  ^ 31 MIN
      HEAVY +13
```

- **Band colour = traffic**: CLEAR (green), BUSY (yellow), HEAVY (orange), JAM (red). It compares the drive time with *free flow*, taken in order from: `typical_minutes`, the sensor named by `free_flow_entity` (HERE's plain *duration* sensor next to *duration in traffic*), then the shortest time seen. Under 3 minutes over is always CLEAR.
- **Meter**: six segments from free flow to a jam.
- **Arrow**: red up or green down when the time moved 2+ minutes compared with about 15 minutes ago.
- **Burst**: when a route turns HEAVY or JAM, a sunburst interrupts for 5 seconds (`traffic_alerts`).
- **Honest failures**: a sensor that is `unavailable` shows NO DATA on its row after 10 minutes (it keeps the last good time until then); if every sensor is down the screen says NO WAZE / CHECK HOME ASSISTANT. Nothing sits on "Loading".
- More than two destinations take turns, two per screen.

## Setup

Needs a travel-time integration in Home Assistant that gives a sensor in minutes: **HERE Travel Time** (use its *duration in traffic* sensor) or Google Travel Time. The Waze Travel Time integration no longer works: Waze retired the service it used (HTTP 410) and its sensors went `unavailable` on 2026-09-29. The plugin reads each sensor's **state** as minutes (older HA versions: the `duration` attribute). If the sensors show `unavailable` in HA, fix the integration there first: this plugin can only show what HA reports.

```json
"waze-travel": {
  "enabled": true,
  "display_duration": 20,
  "update_interval": 120,
  "ha_url": "http://homeassistant.local:8123",
  "ha_token": "your-long-lived-token",
  "destinations": [
    {"label": "WORK", "entity": "sensor.work_duration_in_traffic", "free_flow_entity": "sensor.work_duration", "color": [255, 160, 0]}
  ]
}
```

Display mode: `waze_travel`. `update()` only starts a background refresh; `display()` draws from memory.

Files: `manager.py`, `wazearc_render.py` (screens), `wazearc_draw.py` / `wazearc_font.py` (shared arcade kit), `bdf_face.py` (BDF fonts).
