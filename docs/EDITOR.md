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
  opening mission, `sw` and `nw` the two free-roam maps). Objects near the view
  show their real models with textures (the model the game draws: an object's
  `GeometryFile`, which for some, such as soldiers and night lampposts, differs
  from the model its record names). Farther ones are see-through stand-ins the
  size of their model, coloured by kind: buildings tan, trees green, props
  orange, characters red, vehicles blue, markers yellow. Hovering over anything
  (object, squad member, spawn point, path or zone) says what it is, and the
  panel's **Key** explains the colours. The game's own spawn points are small,
  faint cones (a map has hundreds). Textures use alpha cut-outs only where the
  game does (leaves, fences), and surfaces show from both sides, so buildings
  look as they do in game.
- The ground is drawn as the game draws it: its texture layers (up to 16), each
  tiled at its scale (top-down, or from the side for cliff layers with
  projection 4 and 5), blended by their painted opacity and multiplied by the
  map's baked lighting (so the opening map is dark: it's night). Plain shading
  shows until the textures arrive. Roads, concrete and parking (the terrain's
  decals, one 8 m cell each) are drawn on top, lit the same way.
- The terrain is what the game will have: when the mod sits in the game's `mods`
  folder, the editor applies the terrain edits of every enabled source mod, in
  load order, as `mod sync` does. Elsewhere it applies the mod's own.
- Other mods: when the mod sits in the game's `mods` folder, objects that other
  enabled source mods add (copies, encounter-layer props and squads) show too,
  and game objects they move or remove are hidden or shown where they put them.
  The editor runs those mods' object rules and encounter layers, in load order,
  as a build would (this mod's own other rule files and layers count too, except
  `editor.json` and its squads, which are edited here). They're read only:
  selecting one says which mod changed it. This runs in the background after the
  map loads (about 20 seconds the first time).
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
- Squads and spawn points: each encounter's spawn point sets show as cones
  (the game's yellow, the mod's orange) and squad members as figures (the
  game's blue, the mod's pink), with a nose showing their heading. The game's
  encounters are read only (most of their squads are spawned by mission scripts
  at those points). The mod's, in its `encounters/*.json` layers, can be edited:
  drag members and spawn points, type positions and headings, add a member (a
  copy beside the selected one), add spawn points (click the ground), and delete
  members, points, squads or whole encounters.
  - **New squad**: pick the layer file, an encounter (or a new one, which gets
    spawn point set A 10 m north of the squad), the squad's name, what its
    members copy (a template such as `template_A6_NK_Soldier`, or a named object
    such as `RoadblockRunner`; the list offers the game's 1,696 templates,
    characters first) and how many (up to 40). Then click the ground: the members
    stand in rows of up to 8 there, facing you, with fresh names
    (`<encounter>_<squad>_<n>`).
  - A script loads the layer (`Utility_ReadLevelFile('<layer>')`) and commands
    its squads (`Squad_Command`); see [ENCOUNTERS.md](ENCOUNTERS.md).
- **Live link**: with the game running and Foundry Console on, the console finds
  the editor by itself (port 8740) and the **Live** button shows the connection.
  A cyan arrow marks the player; **To player** looks there and **Player here**
  teleports the player (on foot) to the middle of the view. With Live on, moving,
  turning, undoing or removing a game object also does it in the running game
  (drags send when they settle); the status line shows the game's replies. The
  saved mod files still decide what the game has when it next starts.
  - Live spawning: new squad members and copies of named objects spawn in the
    game too (the game spawns by name, from templates loaded where the player
    is), and are then moved and removed live by their names. Opening **New
    squad** asks the game which templates are loaded there and lists them first
    ("loaded here"); others appear after a rebuild. Copies of unnamed scenery
    spawn live too: the game finds the original as a live move does and spawns
    its properties under the copy's name (the copy borrows the original's
    properties, so it may not survive its area unloading; the saved mod has it
    for good). Other mods' objects need a rebuild. The panel's **Game replies**
    list keeps the game's last answers.
- **The game's own encounters**: their spawn points can be dragged, typed in,
  added to their set or deleted. A game set keeps at least one point, since
  the game's squads name their sets. The edited encounter is saved whole as
  `layout/<layer>.encounter.changes.json`, which the build applies to the
  game's layer by encounter name. Squad members are world objects: move them
  as objects. Squads and encounters themselves aren't added or removed.
- **Path pauses, animations and links** (the mod's own paths). Select a
  point to set:
  - a pause in seconds;
  - animations a character plays on reaching it. These are clip names, with
    an optional chance in percent: `all_allies_fb_fidget01, all_allies_fb_idle:50`.
    The box suggests idle, fidget and similar clips.

  The game never has both pauses and animations on one path, so the panel
  refuses that. **Links** joins the selected point (or the last one) to a
  point of another path, so characters can go on from there; links can be
  removed again. Adding or deleting points keeps pauses, animations and links
  on the same points.
- **Minimap** (bottom left): the whole map from above, north (−z) up.
  Terrain is hill-shaded and tinted by height, water areas are drawn in blue,
  and a yellow dot with a wedge shows where the camera looks and which way.
  With the live link on, a red dot shows the player and the way they face.
  Click it to look there; M (or its ×) hides it.
- **Water** is drawn where the game has it: every boundary with `type water`
  (the south map's coast, bays, rivers and ponds; the north map's oceans and
  rivers) is filled at its `height`. The fill uses its `texture` (default
  `oceanwater`) and its `red`/`green`/`blue`/`alpha` colour, leaned toward
  blue so it reads as water. The **Water** box hides it. A mod's own water
  zones are drawn the same way, and move with their points.
- **Place** puts down any template: a vehicle, prop or character. Type or pick
  the template in the toolbar box, click **Place** (or press P), then click the
  ground.
  - A placed object shows its real model. It moves, turns, renames, copies and
    removes like a copy.
  - With the live link on, it spawns in the running game if the game has that
    template loaded there.
  - It's saved in the mod's layer for the map, `encounters/<mod>_<map>.json`,
    under `"objects"`.
- **Editor layers load with their map.** Layer files the editor makes (squads,
  paths, zones, placed objects) get `"load": "map"`. The build then loads them
  whenever free roam starts on their map, so they appear in the game with no
  script. This works on the south and north maps; the opening map has no free
  roam, so its layers still need a script.
- **Save** (Ctrl+S) writes `objects/editor.json`: one rule per moved object
  (matched by name, or by model and original position), one `copy` rule per
  copy and one `remove` rule per removed object. The build applies them like
  any object rule, and the editor reads them back when it starts. Path, zone,
  squad and placed-object edits are saved with them. Rules for other maps are
  kept; before 0.65, saving on one map dropped the edits on every other map.

Tested in game (2026-11-10): an allied Humvee and an allied soldier placed in
the editor on the south map appeared there in free roam.

## Not yet

- Tilting objects to the slope: objects turn about the vertical only.
- Undo history: **Undo changes** puts one object back as the game has it.

## How it works

`mercfoundry/editor/server.py` serves the page (`static/`), three.js 0.187.0
(`vendor/three/`, MIT, with its LICENSE) and a small JSON API:
`/api/info`, `/api/terrain/<map>.json` and `.bin` (float32 heights),
`/api/objects/<map>` (objects plus the editor's saved edits),
`/api/model/<name>.glb`, `/api/look/<map>.json`, `.paint`, `.light` and `.decals`
(the ground's layers, paint, baked lighting and road decals), `/api/texture/<name>.png`,
`/api/bounds/<map>` (model sizes, from each model's
header), `/api/layout/<map>` (paths and zones),
`/api/encounters/<map>`, `/api/templates`, `/api/context/<map>` (other mods'
objects), the live link (`/api/live/poll` for the plugin, `/api/live/state` and
`/api/live/send` for the page; the line protocol is in `live.py`), and `POST /api/save`,
`/api/layout/save` and `/api/encounters/save`. Saving refuses requests from
other web origins. Requests take one of three locks (objects, terrain, other
mods), so a slow first build of one doesn't hold up the others; models and
textures take none.

The 3D view uses three.js axes, which are the game's with Z flipped, as in the
glTF export. `scene.py` converts rotations (`to_three`, `from_three`) and turns
an edited rotation back into a rule's `turn` (a three.js turn of +θ about the
vertical is a game turn of -θ); tests in `tests/test_editor.py`.
