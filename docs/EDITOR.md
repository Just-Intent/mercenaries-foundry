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
- **Save** (Ctrl+S) writes `objects/editor.json`: one rule per moved object
  (matched by name, or by model and original position) and one `copy` rule per
  copy. The build applies them like any object rule, and the editor reads them
  back when it starts.

## Not yet

- Removing objects (object rules have no remove action yet).
- Tilting objects to the slope: objects turn about the vertical only.
- Objects added or moved by other mods aren't shown; only terrain is.
- Undo history: **Undo changes** puts one object back as the game has it.

## How it works

`mercfoundry/editor/server.py` serves the page (`static/`), three.js 0.187.0
(`vendor/three/`, MIT, with its LICENSE) and a small JSON API:
`/api/info`, `/api/terrain/<map>.json` and `.bin` (float32 heights),
`/api/objects/<map>` (objects plus the editor's saved edits),
`/api/model/<name>.glb` and `POST /api/save`. Saving refuses requests from
other web origins.

The 3D view uses three.js axes, which are the game's with Z flipped, as in the
glTF export. `scene.py` converts rotations (`to_three`, `from_three`) and turns
an edited rotation back into a rule's `turn` (a three.js turn of +θ about the
vertical is a game turn of -θ); tests in `tests/test_editor.py`.
