# Govee Lights

**A web page for the Govee light effects that your sports plugins play when a team scores.** Pick the lights, pick an effect for each moment, press Test.

This plugin shows nothing on the LED panel. It hosts a settings page (port 5020 by default) for the score effects built into **NFL Live Field**, **NBA Live Court** and **NHL Live Ice**. Everything you set is saved to one file, `config/govee_fx.json`, that all three plugins share and re-read the moment it changes, so there is nothing to restart.

- **Light picker:** scan the network for Govee lights with LAN Control, add Home Assistant lights from a searchable list, or type an address. Name each light, switch it on or off for games, and press **Blink** to find out which is which.
- **Effect picker:** seven effects (burst, fireworks, flash, pulse, chase, strobe, glow), each with an animated preview in your team's colors. Set the length, and use team colors or your own two colors.
- **Per-sport settings:** set everything once for all sports, then override a moment for a single sport (a different effect for hockey, say).
- **Three moments:** your team scores, a big moment (touchdown, NBA lead change, hockey overtime or go-ahead goal), and scored against you (off until you turn it on).
- **Test button:** plays exactly what is on the page, saved or not, on the lights you tick, for the team you choose, then puts every light back as it was.
- **Works with or without Home Assistant.** Lights that support Govee LAN Control need nothing else. Others go through Home Assistant, whose address and token are read on the Pi from LEDMatrix's own config and never sent to the browser.

## Setup

```json
"govee-lights": {
  "enabled": true,
  "portal_port": 5020
}
```

Then open `http://<your-pi>:5020`.

1. Press **Scan for LAN lights**. For a light to show up, switch on **LAN Control** in the Govee Home app (the light's settings, gear icon). Not every model has it. Use **Add from Home Assistant** for the rest.
2. Name your lights and use **Blink** to tell them apart. Switch off any you want left alone.
3. Choose an effect for each moment. Click **Custom** for your own colors.
4. Pick a sport, team, moment and the lights to use, then press **TEST**.
5. Press **Save**. The sports plugins use it from the next score.

The page checks that each light answers (green dot) and shows whether it is on or off right now.

## How the lights behave

Govee's local API can only set colors, so the plugin plays each effect itself, a step at a time. Before an effect it reads each light's state (on or off, color, brightness). Afterwards it puts that back, checks the light took it and retries if not. A light that is off stays off, and remembers the color it had. Only one effect plays at a time, even if two sports score together, and a short cooldown stops a flurry of scores from strobing all evening. With several lights, each runs the effect a moment after the one before, so the color seems to travel.

## Notes

- The page has no password, like the other LEDMatrix portals: anyone on your network can open it. It can flash your lights and change this one settings file, nothing else.
- Light addresses are limited to your local network.
- The settings file keeps a `.bak` copy of the previous version each time you save.
- Team names and colors come from ESPN's public scoreboard service.

Files: `manager.py` (plugin), `portal.py` (the portal's logic and web routes), `static/portal.html` (the page), `govee_fx.py` (the shared effects engine, also copied into each sports plugin). Tests: `test_portal.py`, `test_govee_fx.py`, `test_manager.py`.
