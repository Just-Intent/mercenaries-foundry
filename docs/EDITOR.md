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
  there. The box also finds zones and paths (it frames the whole zone),
  encounters, and squads as `encounter:squad`, and suggests names as you type.
  Drag to orbit, right-drag to pan, scroll to zoom.
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
    for good). Moving or turning a copy removes it in the game and spawns it
    again where it now is (buildings moved by the game stop drawing). The
    game's own objects move the same way: copied to the new place from their
    own properties and removed (Foundry Console's `respawn`), then known by a
    name of their own (`fmv_<layer>_<index>`, or their own name); a gate
    moved this way comes with its doors. Other mods' objects need a
    rebuild. The panel's **Game replies** list keeps the game's last answers.
  - Saved games keep the state of objects (position included) by their place
    in their layer, so the built mod moves an object the same way too: its
    place becomes an invisible location marker and the object goes at the end
    of the layer, where a save made before the move can't put it back.
- **Terrain** (T): reshape, paint and lay roads on the map's ground. In
  terrain mode left-drag paints, middle-drag orbits, right-drag pans; Esc
  leaves. **Raise**, **Lower**, **Flatten** (to the height where the stroke
  starts) and **Smooth** change the height samples, with a radius and a
  strength (the longer you hold, the more); **Paint** and **Erase paint**
  raise or lower one texture layer's opacity (later layers draw over earlier
  ones); **Road** lays a road through clicked points (texture and width; Enter
  finishes). Undo stroke (Ctrl+Z) steps back. Save writes
  `terrain/<map>.heights.changes.json` (height units; the build relights
  changed slopes), `terrain/<map>.layerNN.<texture>.changes.png` (changed
  pixels, merged with what's there) and `terrain/<map>.decals.json` "roads"
  (the view redraws the ground with them). Samples back at their loaded value
  aren't saved; a mod's whole-map heights or layer image takes precedence, so
  painting over it is refused. Objects keep their height: Drop to ground.
  A road replaces whatever decal is on its cells (a patch holds one per cell;
  more hung or crashed the game), and paint hidden under opaque paint is left
  out of the patch (see dsk.md).
- **Missions**: make a contract on the map. Pick a mission, or **New
  mission...** with a faction (it takes that faction's next free number on the
  map), then give it:
  - a title, a description (the contact's offer), a briefing (the contract
    mail), a fee and a bonus (paid on completion on top of the fee; 0 unless
    you set one);
  - objectives, done in order: **Destroy squad** (pick a squad) and **Go to
    zone** (pick a zone; the zone gets a marker on the minimap and GPS).
    **Show** looks at an objective's squad or zone;
  - a **Player start**: **Set here** puts it at the middle of the view (a
    yellow arrow pointing the way the player faces). Click the arrow to move
    it (it stays on the ground) or press E and drag the ring to turn it; or
    type a heading. Without one, the player starts where the contract leaves
    them (the HQ).

  **Add squad** and **Add zone** put a squad or zone in the mission's own
  layer, `encounters/<id>.json`, which loads only during the mission; squads
  and zones in the mod's map layers can be picked too (the mission loads those
  layers). Save writes `missions/<id>.json` first; a mission that can't be
  saved yet doesn't hold up saving the rest. With objectives and no script of
  its own, the build writes the mission's script (see ENCOUNTERS.md). The panel
  shows the Lua line that starts the mission (paste it in Foundry Console's Lua
  box); a contact at the faction's HQ offers it too.
- **Turn squad** turns a selected squad of the mod's about its middle by the
  given degrees: its formation and every member's facing.
- **Gates** draw with their doors: a gate's doors are models of their own
  (`geometryNameGateA`/`B`), hung on its `k_gateclosedA`/`B` joints. In the
  game they are separate objects too, which the game makes for each gate,
  spawned ones included.
- **The game's own encounters**: their spawn points can be dragged, typed in,
  added to their set or deleted. A game set keeps at least one point, since
  the game's squads name their sets. The edited encounter is saved whole as
  `layout/<layer>.encounter.changes.json`, which the build applies to the
  game's layer by encounter name. The game's squad members are world
  objects: move them as objects. To add a squad to a game encounter, select
  one of its spawn points and click **New squad**: the form offers that
  encounter ("the game's"). The members go in the mod's layer for the map
  (which loads with it) and the squad joins the encounter when the mod builds
  (`"game_squads"`, see ENCOUNTERS.md); its members can be moved, added and
  deleted like the mod's own. The game's encounters aren't removed. Search
  finds encounters by name.
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

The 3D view uses the game's axes as they are (both right-handed), and models
come from the glTF export, which is already in the game's object axes.
`scene.py` passes rotations through (`to_three`, `from_three`) and turns an
edited rotation back into a rule's `turn`; tests in `tests/test_editor.py`.
