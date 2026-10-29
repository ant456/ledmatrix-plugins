"""Create the media-marquee config block from your existing plex-marquee one.

    python3 copy_config.py [path/to/config.json]

Copies credentials (tmdb/fanart/plex/HA) across, sets entities to the Shield +
philcofinal + whatever plex-marquee already had, and backs the file up first.
Existing media-marquee settings are never overwritten. Secrets are not printed.
"""
import json, os, shutil, sys, time

CFG = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/LEDMatrix/config/config.json")
COPY = ["tmdb_token", "fanart_api_key", "plex_url", "plex_token", "ha_url", "ha_token"]
DEFAULT_ENTITIES = ["media_player.shield_2", "media_player.philcofinal"]


def find_parent(node, key):
    if isinstance(node, dict):
        if isinstance(node.get(key), dict):
            return node
        for v in node.values():
            hit = find_parent(v, key)
            if hit is not None:
                return hit
    return None


with open(CFG) as f:
    cfg = json.load(f)

parent = find_parent(cfg, "plex-marquee")
if parent is None:
    sys.exit("no 'plex-marquee' block found in " + CFG + " - add the example block by hand instead")

old = parent["plex-marquee"]
new = parent.setdefault("media-marquee", {})
added = []
for k in COPY:
    if old.get(k) and not new.get(k):
        new[k] = old[k]; added.append(k)
ents = list(new.get("entities") or [])
for e in DEFAULT_ENTITIES + list(old.get("plex_entities") or []):
    if e not in ents:
        ents.append(e)
new["entities"] = ents
for k, v in (("enabled", True), ("display_duration", 12),
             ("live_priority", old.get("live_priority", True))):
    new.setdefault(k, v)

bak = f"{CFG}.bak-media-marquee-{time.strftime('%Y%m%d_%H%M%S')}"
shutil.copy2(CFG, bak)
with open(CFG, "w") as f:
    json.dump(cfg, f, indent=4)
print("backup :", bak)
print("copied :", ", ".join(added) or "(nothing new)")
print("entities:", ents)
print("done - restart with: sudo systemctl restart ledmatrix")
