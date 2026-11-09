# Foundry editor

A local web app for editing a mod. It runs on your own PC, reads your own copy of
the game, and saves ordinary mod files, so editing in it and editing files by
hand work on the same mod.

```bash
python -m mercfoundry edit my_mod --data game_files/DATAxbox
```

This starts a server on 127.0.0.1 (port 8740; `--port` changes it) and opens the
editor in your browser. Nothing is served to other computers. Players with the
toolkit bundle run it the same way: `foundry\python\python.exe foundry\mercfoundry.pyz edit ...`.

## What it does

- Shows a map's terrain and objects. Pick the map at the top (`swn` is the
  opening mission, `sw` and `nw` the two free-roam maps). Objects are boxes until
  you're near them, then their models load with textures.
- The terrain is what the game will have: when the mod sits in the game's `mods`
  folder, the editor applies the terrain edits of every enabled source mod, in
  load order, as `mod sync` does. Elsewhere it applies the mod's own.
- Find objects by name or model (search box), or double-click the ground to look
  there. Drag to orbit, right-drag to pan, scroll to zoom.
- Select an object to move it (W), turn it about the vertical (E), drop it to the
  ground (G) or copy it (C), or type its position and heading in the panel.
  Copies get a name you can change in the panel; names must be new.
- Remove an object (Remove, or the Delete key). It disappears; pick it in the
  change list to see a red ghost where it was, and **Undo changes** to bring it
  back. Removing a named object asks first, since scripts may use it by name.
  Saving a removal sets the mod's `"requires"` to 0.51, the first toolkit with
  `remove` rules.
- Paths (blue for the game's, green for the mod's) and zones (orange outlines;
  kill zones red; the mod's yellow) show with the objects; the header boxes hide
  objects, paths or zones. Click a line to select it: its points get handles,
  and a selected zone shows its walls. Click a point to drag it or type its
  position.
  - The game's paths and zones can have their points moved. The number of
    points stays, since a path's orientations, pauses and links are numbered by
    point. Edits are saved in `layout/<layer>.path.changes.json` or
    `layout/<layer>.lineregion.changes.json`, and **Undo changes** puts the
    game's points back.
  - The mod's own (in its `encounters/*.json` layers) can also gain points (Add
    point, or Insert: after the selected point), lose them (Delete point, or the
    Delete key) and be deleted. Pauses and links stay on the same points.
  - **New path** and **New zone**: click the ground to add points, then name it,
    pick the mod layer file (a new one is offered: `<mod>_<map>.json`) and, for
    a zone, its type (trigger, killbox, radiation, water), and press Enter or
    Finish. A script loads the layer with `Utility_ReadLevelFile('<layer>')`;
    then `Event_ActorInBoundary` watches zones and `Ai_Command(..., 'Patrol',
    '<path>', ...)` follows paths (see [ENCOUNTERS.md](ENCOUNTERS.md)).
- **Save** (Ctrl+S) writes `objects/editor.json`: one rule per moved object
  (matched by name, or by model and original position), one `copy` rule per
  copy and one `remove` rule per removed object. The build applies them like any object rule, and the editor reads them
  back when it starts. Path and zone edits are saved with them.

## Not yet

- Tilting objects to the slope: objects turn about the vertical only.
- Objects added or moved by other mods aren't shown; only terrain is.
- Undo history: **Undo changes** puts one object back as the game has it.

## How it works

`mercfoundry/editor/server.py` serves the page (`static/`), three.js 0.187.0
(`vendor/three/`, MIT, with its LICENSE) and a small JSON API:
`/api/info`, `/api/terrain/<map>.json` and `.bin` (float32 heights),
`/api/objects/<map>` (objects plus the editor's saved edits),
`/api/model/<name>.glb`, `/api/layout/<map>` (paths and zones) and
`POST /api/save` and `/api/layout/save`. Saving refuses requests from
other web origins.

The 3D view uses three.js axes, which are the game's with Z flipped, as in the
glTF export. `scene.py` converts rotations (`to_three`, `from_three`) and turns
an edited rotation back into a rule's `turn` (a three.js turn of +θ about the
vertical is a game turn of -θ); tests in `tests/test_editor.py`.
