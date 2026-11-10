"""Foundry Test Range: a flat pad on the south map, far from the game's AI,
for in-game tests whose results must be clear.

`test-range DATAxbox <mods>/Foundry Test Range` writes a source mod, built
from the player's own terrain:

  terrain/sw.heights.changes.json  a 240 m square at (-480, 500) flattened to
                                   its median height (135 m), blended into
                                   the hills over 40 m
  encounters/foundry_range.json    the range layer (the current test's
                                   objects, paths and traffic zones)
  range.lua                        what Foundry_Range() runs

The site was chosen by scoring the playable area (inside the map's
mainkillbox, 150 m from its edge, outside Blackgate's killbox) for distance
to every traffic route point, human and vehicle: it is 220 m or more from
all of them. The game spawns traffic only within about 45 m ahead of the
camera (0x16A800: 20 m ahead and a 25 m radius on foot, 30 and 38 m in a
vehicle), so the game's own traffic doesn't reach it.

In game, Foundry Console's Foundry_Range() runs range.lua in the calling
script's Lua state: it loads the range layer once, moves the player to the
pad's north edge and starts FoundryRangeTest after 3 s. Editing range.lua in
the mod folder takes effect the next time Foundry_Range() runs; changes to the
layer need the launcher's build. The game's north is -z.
"""
import json
from pathlib import Path

from . import dsk, modpack, terrain
from .hashing import name_hash

CENTRE = (-480.0, 500.0)
HALF, BLEND = 120.0, 40.0
NAME = "Foundry Test Range"
LAYER = "foundry_range"

README = """# Foundry Test Range

A flat 240 m pad on the south map at (-480, 500), 220 m or more from the
game's traffic routes and AI, for in-game tests whose results must be clear.

In game (free roam on the south map), open Foundry Console (F7) and run
`Foundry_Range()`. It runs `range.lua` from this folder: loads the range layer
(`foundry_range`), puts you on the pad and starts the current test.

- `terrain/sw.heights.changes.json`: the flattened pad.
- `encounters/foundry_range.json`: the range layer (test objects, paths, zones).
- `range.lua`: what `Foundry_Range()` does; edit it to change the test (it
  applies the next time you run the command). Layer changes need a relaunch.

Made by `python -m mercfoundry test-range` from your own game files.
"""

TRAFFIC_TEST = """function FoundryRangeTest()
  -- Traffic: soldiers from the range layer's own template walk its loop.
  Traffic_ResetZoneSpawners('foundry-range')
  Traffic_ResetZoneCivSpawners('foundry-range')
  Traffic_AddZoneSpawner('foundry-range', 'template_foundry_range_gunner')
  Traffic_SetZoneDensity('foundry-range', 10)
  Traffic_SetZoneEnabled('foundry-range', true)
  Ui_MessageBoxPrint('[yellow]Foundry range: traffic test started (zone foundry-range)', 0)
end
"""


def flatten(units, info):
    """(new height units, pad height in metres)."""
    size, sp, step = info["size"], info["spacing"], info["step"]
    x0, z0 = -size * sp / 2, -(size * sp / 2 - sp)
    cx, cz = CENTRE
    inner = sorted(units[int((z - z0) / sp)][int((x - x0) / sp)]
                   for x in range(int(cx - HALF), int(cx + HALF), 8) for z in range(int(cz - HALF), int(cz + HALF), 8))
    target = inner[len(inner) // 2]
    new = [list(row) for row in units]
    for iz in range(size):
        z = z0 + iz * sp
        for ix in range(size):
            x = x0 + ix * sp
            out = max(abs(x - cx) - HALF, abs(z - cz) - HALF, 0.0)
            if out < BLEND:
                w = 1.0 - out / BLEND
                new[iz][ix] = round(units[iz][ix] + (target - units[iz][ix]) * w)
    return new, target * step


def range_lua(height, test=TRAFFIC_TEST):
    cx, cz = CENTRE
    return f"""-- Foundry Test Range: run by Foundry_Range() (Foundry Console) in the
-- script state the console uses. Loads the range layer once, moves the player
-- to the pad's north edge (the game's north is -z), then runs the current
-- test (FoundryRangeTest, after 3 s). Edit this file to change the test.
FOUNDRY_RANGE = {{ x = {cx}, y = {height + 1.0:.1f}, z = {cz} }}
if not g_foundry_range_loaded then
  Utility_ReadLevelFile('{LAYER}')
  g_foundry_range_loaded = 1
end
local v = Utility_GetActorsVehicle('player0')
if v then
  local px, py, pz = Actor_GetPosition('player0')
  local vx, vy, vz = Actor_GetPosition(v)
  if px and vx and (px - vx) * (px - vx) + (pz - vz) * (pz - vz) < 9 then
    ActorVehicle_KickOutAllPassengers(v)
  end
end
Actor_SetPosition('player0', FOUNDRY_RANGE.x, FOUNDRY_RANGE.y, FOUNDRY_RANGE.z - 100)

{test}Event_RelativeTimer('FoundryRangeTest', 'b', 3)
Ui_MessageBoxPrint('[yellow]Foundry range: you are on the pad', 0)
"""


def _loop(zone, cx, cz, half, y):
    """A square walking loop: four paths p_<zone>_0Na, each linked to the next."""
    corners = [(cx - half, cz - half), (cx + half, cz - half), (cx + half, cz + half), (cx - half, cz + half)]
    names = [f"p_{zone}_{i + 1:02d}a" for i in range(4)]
    paths = {}
    for i in range(4):
        (ax, az), (bx, bz) = corners[i], corners[(i + 1) % 4]
        pts = [[round(ax + (bx - ax) * t / 6, 2), round(y, 2), round(az + (bz - az) * t / 6, 2)] for t in range(7)]
        paths[names[i]] = {"points": pts, "properties": {"width": 3.0},
                           "links": [{"path": names[(i + 1) % 4], "their_point": 0}]}
    return paths


def layer(height):
    cx, cz = CENTRE
    return {
        "map": "SW",
        "objects": [{"from": "template_allies_gunner", "name": "template_foundry_range_gunner",
                     "position": [cx - 110, round(height + 0.3, 2), cz - 110], "yaw": 0}],
        "paths": _loop("foundry-range", cx, cz, 50.0, height + 0.3),
        "traffic": {"foundry-range": {"density": 10, "urgency": 10}},
    }


def make(data_dir, out):
    """Write the range mod to `out` from the game's terrain; returns the pad height."""
    data_dir, out = Path(data_dir), Path(out)
    arc = dsk.Archive(data_dir / "assets.dsk")
    rec = next(r for r in arc.records if r.kind == name_hash("Terrain") and r.key == name_hash("sw"))
    info, units, _ = terrain.decode_height_units(terrain.tern_chunks(arc.read(rec)))
    new, height = flatten(units, info)
    (out / "terrain").mkdir(parents=True, exist_ok=True)
    (out / "encounters").mkdir(parents=True, exist_ok=True)
    (out / "mod.json").write_text(json.dumps({"name": NAME, "description": "A flat test pad, far from the game's AI"},
                                             indent=1) + "\n", encoding="utf-8")
    (out / "README.md").write_text(README, encoding="utf-8")
    (out / "terrain" / "sw.heights.changes.json").write_text(
        json.dumps(modpack.heights_changes("sw", units, new)) + "\n", encoding="utf-8")
    (out / "encounters" / f"{LAYER}.json").write_text(json.dumps(layer(height), indent=1) + "\n", encoding="utf-8")
    lua = out / "range.lua"
    if not lua.exists():                # keep a test someone is editing
        lua.write_text(range_lua(height), encoding="utf-8")
    return height
