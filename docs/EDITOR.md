# Foundry editor

A local web app for editing a mod. It runs on your own PC, reads your own copy of
the game, and saves ordinary mod files, so editing in it and editing files by
hand work on the same mod.

```bash
python -m mercfoundry edit            # or: Foundry Editor.cmd in the game folder
```

This starts a server on 127.0.0.1 (port 8740; `--port` changes it) and opens the
editor in your browser. Nothing is served to other computers. Players with the
toolkit bundle run it the same way: `foundry\python\python.exe foundry\mercfoundry.pyz edit ...`.

## Starting it

- In a game folder with the toolkit (the launcher's, or a playtest package): run `Foundry Editor.cmd`. A page opens in your browser with the game's Foundry mods: open one, or make a new one. The editor's Switch mod goes back to the list. Close the window to stop it.
- From the command line: `python -m mercfoundry edit` (the game folder is found: the one the toolkit is in, or the current one; `--game <folder>` names another), or `python -m mercfoundry edit "mods\My Mod"` for one mod. The game's DATAxbox is found from the game folder (`--data` names another).

## The layout

- **Top bar:** the mod you're saving to (click for the Mods panel), **Switch mod** (back to the game's list of mods),
  the **Map**, **Find** (an object, path, zone or squad by name), the live game link, the status line (hover for all
  of it), **Undo**, **Redo**, **Save** and **Changes** (the mod's changed files, git and builds).
- **Tool rail** (left), by what each tool is for:
  - *World:* **Place** (type or pick a template, named object or `model:<model>`, then click the ground; a new map's
    own template copies are here too), **New path**, **New zone**, **New water**, **New squad**, **Terrain**.
  - *Map setup:* **Map** (make a new map; its arrival point, message, layers and travel pilots), **HQs & medevac**,
    **Sky**.
  - *Content:* **Missions**, **Deck of 52**, **Factions**, **Text & mail**, **Look**, **Animations**.
  - *Project:* **Limits**.
- **Show** (top left of the view): which of objects, paths, zones, water and squads the view draws.
- One tool panel at a time: opening a tool closes the one that was open (unsaved edits stay until you save or leave).

### HQs & medevac (a new map)
Everything about a new map's HQs in one place, saved to `maps/<map>.json`, checked as the build checks it:
the **medevac point**, the **HQ respawn points** (one per faction), the **HQ flags** on the radar and PDA map, and
each faction's **interior**: the guard (where, name, template, prompt), the room, the leaders and the second in
command, the way out (an exit object, or a point with its prompt, delay and distance), the bribe when the faction is
hostile, and the greeting and idle lines. **Here** puts a point at the middle of the view; **Look** goes to it.
A raw JSON box at the bottom edits anything the form doesn't show. Keys the mod's own tools write (mod.json
`generated`) are flagged: the tools' next run replaces edits to them.

### Deck of 52 (a new map)
A tab per suit file (`deck/<suit>.json`; **+ Suit** makes one): each card's name, alias, role, bounty, description or
bio, portrait (preview, and a PNG to upload: `deck/<suit><rank>.png`), and for number cards the placed character
(**Find** goes to it) and the layer loaded while it's at large. **Other suits** sets the stand-in for suits no deck on
the map uses. Saving runs the build's own check, and the file's other keys (yours or your tools') are kept.

### Text & mail
The mod's **welcome mail** (`mod.json`), and its reworded game text: a tab per `text/*.json`, each **everywhere**
or **only on** one of the mod's new maps. **Find the game's text** searches keys and words; click a result to add it
to the open file with its current words, then change them.

### Look (a new map)
The HUD and PDA art drawn instead of the game's while the map is loaded (`hud_textures`): each row shows the game's
sheet and yours, takes a PNG (saved as `textures/<name>.png`), and **Add a sheet** lists the ones worth repainting
(radar markers, flags, faction icons, the Deck screen, PDA frames and buttons).

### Generators
What Gaza's tools did by script, in the editor (World > Generators):
- **Kits:** select objects (Shift+click, or Shift+drag a box) and **Save the selection as a kit** (`kits/<name>.json`:
  each object's offset from the group's middle, its height above the ground and its heading). **Stamp near the
  view's centre** puts the kit on the nearest free ground: no object or drawn road under it, the ground no steeper
  than **Steepest** (rise over run) and, if given, above **Ground above** (keep it off the beach), turned to
  **Heading**. **Fill a zone** stamps it on a grid (**Spacing**, at most **most**) wherever the zone's ground is
  free. Stamped objects are placed objects like any other: one Undo takes a whole stamp or fill back, and Save
  keeps them.
- **Traffic from roads** (a new map with drawn roads, `terrain/<map>.decals.json`): **Walkers** along both kerbs, or
  **Cars** one lane each way on the right, on the roads within **Around** metres of the view's centre, clear of the
  map's buildings and props, in paths of up to 9 points linked at junctions: a traffic zone (its density and who
  spawns) written into an encounters layer (the zone's old paths replaced), and the map reloads to show it.
- **Budgets:** the map's traffic routes (1,024 at most) and names (2,048 at most), counted over the mod's layers.
### Sounds & effects
- **Sounds:** pick a bank, find a cue, **Hear** it (each of its sounds). **Replace with a .wav** puts your sound in
  its place (`sounds/<bank>/<cue>.wav`); **Copy as a new cue** keeps every track and setting under a new name
  (`sounds/<bank>/new/<cue>.json` `{"like"}`), and each track then takes a .wav of yours (`<cue>.<n>.wav`);
  **New cue from a .wav** adds one (`new/<cue>.wav`). The build fits each .wav to the bank's format.
- **Effects:** find an entry of the game's effects table (a template's `effectset` names one), change its values, or
  **Copy as a new entry** (e.g. a helicopter set whose `rotor` is your own engine cue). Saved to `effects.json`,
  checked as the build checks it.

### Zones and paths
A zone or path of the mod's own shows its properties as a table: change, rename or remove a key, add one, or start
from a preset (**Ambience zone**: `type` ambience, `sound` a soundscape, `width` the fade in metres; **Allowed area**:
a killbox; **Trigger**). Each change is one Undo step. The game's own show theirs read only.

### Models as built
The view draws a mod's models as the build makes them: a copy (`models/<name>.new.json` `"copy"`) as its base
model with the swapped textures, a game model the mod replaces (`models/<game model>.glb`), and every texture the
mod adds or replaces (`textures/<name>.png`).
### Limits
The open map's object count against the game's 16,384, and the other limits that freeze or hang the game rather than
fail the build (traffic routes, the name table, texture sizes and memory, model sizes, objectives at once).

## What it does

- **Saving to** (top left) names the mod everything you save goes into.
  The editor's server edits one mod at a time for every tab: if another tab
  switches mods, or the server is restarted (on the mod it was started
  with), a page still showing the old mod has its saves refused ("Nothing
  was saved") instead of going into the other mod; reload it, or switch
  back in Mods. Saving leaves a layer file that nothing changed in as it is,
  and keeps the keys of one it rewrites that the editor doesn't edit
  (`unnamed`, `boundaries`, an object's own extra keys).
  Click it for the Mods panel: the game's source mods, **Edit this mod** to
  switch to another (the map reloads from it), **Built by the launcher**
  (its place in the launcher's list), the mod's name and description
  (`mod.json`), **Make the mod** (a new source mod in the mods folder,
  built by the launcher, switched to) and **Delete** (the folder moves to
  the game's `foundry-trash` folder and off the launcher's list; move it
  back to restore it). The mod being edited can't be deleted. The view
  still shows what the other enabled mods add; saving only writes to the
  one named.
- **Changes** (beside Saving to; its count updates after each save) lists
  the mod's changed files when the mod is under git of its own: git run in
  its folder finds a repository whose work tree is that folder (a `.git`
  folder, or a `.git` file `gitdir: C:/somewhere/mod.git`; a repository
  higher up, such as a checkout the mods folder sits in, doesn't count).
  Tick the files, write a message and **Commit** (only the ticked files,
  with the repository's own git identity and your message as written);
  **Push** sends commits to its upstream, or the first time to `origin`
  (or its only remote). Files the mod's own tools write are orange, from
  `mod.json` "generated":
  `"generated": ["encounters/gaza_*.json", {"file": "maps/gaza.json", "keys": ["hqs", "interiors"]}]`
  (a glob, relative to the mod folder; or a JSON file with only some keys
  generated): an edit to them is overwritten when those tools run again.
  **Build** builds the game's source mods as the launcher does before a
  modded launch (`mod sync`), in the background, its output shown in the
  panel; **Build + playtest zip** then packages the game folder for
  another PC (`packaging/playtest.py`; only when the editor runs from the
  toolkit's source tree).
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
- **Bullets hit its branches** (a selected tree): a tree's collision is a thin
  box round its trunk, so shots into its branches pass. This adds a box over
  them that only bullets hit, for every tree of that model
  (`models/<model>.collision.json` `"crown"`); walking and driving through
  stay as they are.
- **Tilt to slope** leans the selected objects to the ground's slope where
  each stands (keeping its heading); **Upright** stands them straight again. A
  tilted object is saved with its whole rotation (`"rotation"`, three rows, in
  its move rule or placed object); upright ones keep a heading. The live link
  sends headings only, so the game's copy stays upright until the next start.
- **Several at once**: Shift+click adds an object to the selection (or takes it
  away), Shift+drag draws a box round more. The gizmo then moves or turns them
  together about their middle, and Drop to ground, Tilt, Upright, Copy, Remove
  and Undo changes act on all of them.
- **Undo** and **Redo** (top bar, Ctrl+Z and Ctrl+Y or Ctrl+Shift+Z) step
  through the object edits: moves, turns, drops, tilts, copies, placing,
  removing, Undo changes and renames; an edit of several objects is one step.
  Paths and zones (moving, adding and deleting points, pauses, animations,
  links, new and deleted ones) and squads (moving members and spawn points,
  adding, turning and deleting them, placing squads) are on the same history,
  and so are the Missions, Sky and Factions panels' edits (a pause in typing
  ends a step).
  The history is cleared when you switch maps. (In terrain mode Ctrl+Z steps
  back terrain strokes, as before.)
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
    a zone, its type (trigger, killbox, radiation, water, weather), and press
    Enter or Finish. A zone can also go in **the map's own zones** (see Water
    below). A script loads the layer with `Utility_ReadLevelFile('<layer>')`;
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
  - Live terrain and Lua (with a Foundry Console built from 0.123's source,
    not yet tested in game): with Live on, each Raise, Lower, Flatten or
    Smooth stroke, and from 0.132's source each Paint and Erase paint stroke
    (and their undos) change the running game's ground as well,
    when the game is on the map being edited; **Lua in the game** (under
    Game replies) runs a line of Lua there, e.g.
    `Utility_ReadLevelFile('editor_test_sw')` to load a built layer.
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
  **Add texture layer** (under Paint) gives the map another ground texture to
  paint with: any of the 40 the game's maps use, with the scale and
  projection the game gives it most, up to 16 layers a map. It's written
  straight away as `terrain/<map>.layers.json` (the whole layer table), and
  the build adds the texture to the map's load list. A copy of blank has one
  layer (dirt) until you add more. **Use for selected layer** gives the selected layer the texture
  in the box (its paint stays); **Remove selected layer** takes off a layer
  the mod added, with its paint (the map's own layers can't go: paint them
  out). Adding, changing and removing layers go on the undo history. Removing
  one moves its paint to the mod's `.trash/`, and Undo brings the layer
  and its paint back.
- **New maps**: **Map** > New map makes one: `maps/<name>.json`, a copy
  of a game map (blank: flat and nearly empty), and switches to it. **Ground**
  picks its size: as the copy's, or flat ground 256, 512 or 1024 cells a side
  (2, 4 or 8 km at 8 m; `"size"`), with the copy's texture layers and no
  roads. Every retail map is 512 or smaller; on Gaza's 1024 map players met
  walls inside the map in the north and south (not yet explained). The build
  puts invisible walls along a new map's edge (`"edge": "open"` for none). It
  appears in the Map list like the game's maps, with the copied map's ground
  to reshape and paint (Terrain), its sky (Sky), and objects, squads and
  zones to add. On a new map, the Map panel sets where the player arrives
  (the middle of the view, and a heading), a message on arrival, the
  **weather on arrival** (a preset from its config, SW's or the mod's new
  ones, switched on with `Mission_EnableAtmosphere`: weather seems to carry
  over a map switch; `"weather"` in `maps/<name>.json`), and which
  of the mod's layers on it load with it (objects placed in the editor go in
  one that does). The build writes its master script (run as it loads)
  from these; with none of them set, it's a copy of the source map's
  (blank's prints "Hello World!"). Write `scripts/<name>.lua` to make your
  own instead, or name scripts for it to run (`"scripts": ["<script>"]`:
  loaded into it after its own set-up with `Utility_LoadUtilityScript`, so
  their timers call back into it, and started with `<script>_Start()` if
  they have one; don't give them a `ScriptInit`). Its config names the script (`MainMasterScript <name>`): a level
  runs only the script its config names, and blank's names none. Enter it with `Utility_ResetToBaseLayerOnly('<name>')` from
  Foundry Console.
  **Radar** (the PDA map and the minimap): put the map's picture in
  `textures/rdr<name>.png` (or `maps/<name>.radar.png`, or name it with
  `"radar": "<texture>"`).
  - **Image:** square, 512 pixels a side. Bigger powers of two are halved
    down to 512.
  - **Area:** it covers the whole terrain, whatever its size: on a
    1,024-cell map with 8 m cells, the 8,192 m square centred on 0.
  - **Orientation:** like the heightmap PNG. Columns run along world x from
    -x on the left, rows along z from -z at the top.
  - **Build:** cuts it into the 16 x 16 tiles the game draws
    (`<radar>_x<i>y<j>`, 32 x 32 each, as retail's) and writes
    `RadarTexture <radar>` into the map's config.
  - **A game radar:** `"radar": "rdrSW"` alone uses that radar's tiles.
  - **Changing a game radar:** `textures/rdrNW.png` also replaces its tiles.

  **Soundscapes** (the background sound, from the config's `[SoundAmbience]`).
  Blank has none, so a map copied from it is silent.
  - `"ambience": "sw"` copies a game map's 21 soundscapes (SW, NW and CDDN have
    them).
  - `"ambience_default": "farm"` sets the one heard outside any ambience zone.
  - `"ambience_add": {"market": {"TYPE_NORMAL": "ambient.city_s_dist_war",
    "TYPE_HVY_WAR": "...", "thresholds": [0.3, 0.3, 0.4]}}` adds soundscapes of
    its own from existing cues. TYPE_NORMAL is required; the others are
    TYPE_DIST_WAR, TYPE_HVY_WAR, TYPE_RAIN, TYPE_RAIN_DIST_WAR and
    TYPE_RAIN_HVY_WAR.
  - Zones of type `ambience` (`sound` = a soundscape, `width` = fade metres,
    e.g. in `maps/<name>/boundaries.json`) switch soundscapes where they lie.
  **Travel**: on SW or NW, the Map panel puts a travel pilot (retail's
  quadrant pilot) at the middle of the view that takes the player to one of
  the mod's new maps: use it, say yes, and the game saves and goes there, to
  the map's arrival point (`"travel"` in `maps/<name>.json`). On the new
  map, **Pilot back** puts one that takes them back to SW or NW, beside the
  pilot that brought them (`"return"`).
  **Missions on a new map**: the Missions panel works there as on SW. A
  mission made there is a SW contract to the game's mission code (it gets a
  number in SW's arc, `sw_<faction><n>`) with `"map": "<new map>"`: its
  giver stands on the new map (or its contact is at the HQ), and taking the
  contract saves and goes to the new map, whose own script starts it (as
  global does on SW). When it ends the player is in free roam on the new
  map, its givers offered again; a retry starts it there again.
  **HQ flags** (`"hqs"` in `maps/<name>.json`, file only for now):
  `{"allies": [x, y, z], "china": [...], "mafia": [...], "sk": [...]}` puts
  each faction's HQ icon on the radar and PDA map there while you're in free
  roam on that map, as the HQ bouncers do on SW (an objective at the point
  with the faction's HQ target type and blip). SW's and NW's own HQs don't
  move.
  The flags' art is in the texture `radarmapels`: 14 x 9 flags at x 112-125,
  China at y 52, SK 65, Allied 78, Mafia 91, NK 104 (found by the Gaza mod,
  which repaints them).
  **HQ interiors** (`"interiors"` in `maps/<name>.json`, file only):
  `{"allies": {"guard": {"position", "heading", "name", "template"?,
  "prompt"? ("Enter HQ"), "side"? ("civ"), "marker"? ("hq" or "none")},
  "room": {"position", "heading"}, "leaders": ["<actor>", ...],
  "starter"? ("<actor>", or false), "accept_prompt"? ("Accept contract"),
  "bribe"? (true, false or {"cost": <money>}),
  "exit": {"object": "<name>" or "position": [x, y, z], "prompt"? ("Leave"), "delay"? (5), "distance"? (3)},
  "talk_prompt"? ("Talk"), "greeting": [line], "idle": [line]}}`. A line is `{"speaker", "text",
  "seconds", "sound"? (<bank>.<cue>)}` or just text; seconds default to the
  text's length. The guard (a layer the map loads; `template_<faction>_bouncer`
  unless set) is marked as the bouncers are, with the faction's HQ flag,
  unless `"hqs"` gives that faction a point: then the point has the flag
  (always there, even before the guard is) and the guard a ring and name
  in the world. The guards are looked for every second at first, then
  every 5 s while free roam lasts, so one far from where you arrive is
  armed once it's there. The interior plays as retail's HQs (`bouncer`,
  `briefing` and `briefing_utilities`):
  - While the faction is hostile to the player, the guard offers a bribe
    instead, as the bouncers do: the cost is `util_GetBribeCost` for the
    chapter (25,000 to 1,000,000) unless `"bribe"` gives one, in retail's
    bribe boxes; paid, the faction's mood goes to -40 and the guard lets you
    in. `"bribe": false` turns that off.
  - Using the guard fades to black and puts the player at the room's position.
  - Inside, the player has retail's briefing controls: no firing or weapon
    switching (control mode 13, which `Briefing_Init` sets), no HUD, and, with
    Foundry Console, the weapon put away and walking pace (`Foundry_HqMode`).
    Both are put back on leaving or taking the contract.
  - The leaders (actors placed in a map layer, by name) stand where they are
    and never fight, even when the faction is hostile. Each is under
    cinematic control, as retail's briefing boss and starter, looks at the
    player, can't be hurt and has no hostile range. Nothing in their
    template needs setting.
  - The greeting plays, then the faction's next contract here has its
    briefing lines. Then the second in command (`"starter"`, the second
    leader unless set) gets a gold ring and the accept prompt, as retail's
    starter; using them starts the contract as a giver does. With no starter
    (`false`, or one leader), a yes/no box. With no contract, the idle lines.
    The first leader offers Talk (the briefing again) when it isn't the way
    out or the starter.
  - The HUD stays on (the lines and rings are HUD). `foundry_<map>_HqLog()` in
    Foundry Console's Lua box lists the last steps inside (entered, greeting,
    briefing, starter and door armed, accept, leave).
  - A door that's scenery isn't an actor a script can use: give the exit a
    `"position"` instead, and it gets a gold ring there; coming within
    `"distance"` asks to leave.
  - After the exit's delay the exit object (the first leader if none is
    given) gets a ring and its prompt, as retail's exit after 5 s. Using it
    fades and puts the player back where they stood by the guard. A mission is offered there
  with `"map": "<new map>"` and `"contact": {"interior": "<faction>",
  "briefing": [line]}` (no position: the guard is its way in).
  **Medevac point** (`"medevac": {"position": [x, y, z], "heading"?}` in
  `maps/<name>.json`): where the game puts the player back on the map: a
  medevac from the pause menu, a contract given up after dying, free roam
  starting. The game respawns at the location scribble memory's
  `PlayerStartLocationName` names, which retail's scripts set to
  `loc_playerstart` (on SW the Allied MASH); the build moves the map's copy of
  its source's `loc_playerstart` (blank's is at about -73, 20, -70) there.
  **HQ respawn points** (`"hq_respawn": {"<faction>": {"position", "heading"?}}`):
  locations `loc_hq-<faction>-respawn` added to the map's world, where a
  death during a contract from that faction puts the player (retail's scripts
  set `PlayerStartLocationName` to it while a contract runs).
  **Welcome mail** (`"welcome_mail"` in `mod.json`): `{"sender", "subject",
  "body", "map"? ("sw" or "nw")}` posts a mail once per save, 5 seconds
  after free roam first starts on that map (after the game's own first-day
  mails), marked done by a scribble flag. `"sender"` is a game sender
  (`mail.senders.techgal`, `mail.senders.mercsteam`, ...) or a name of your
  own.
  Pilots and a new map's arrival point show in the view like objects (a
  pilot, or a marker figure for the arrival point): move and turn them as
  objects, and Save writes them back to their `maps/<name>.json`. Add and
  remove them in the Map panel.
  On SW or NW, "Where you arrive back from <map>" is where the pilot back
  puts the player (and which way they face): move it into the open.
- **Factions**: new names for the four employers and North Korea
  (`factions.json`, `"nk"`): name, adjective, short form and people, per
  faction. North Korea also has `"army"`, for the Korean People's Army and
  "KPA Soldier", and DPRK counts as its name. **Everywhere** for mission
  text too, and voiced lines' subtitles if wanted. **Preview** lists the
  strings that change. The game's eight faction slots are fixed in its code,
  so a faction can be renamed but not added.
  **Look** (under each faction): the models its characters draw, each with
  another to draw instead (any character model, or a mod's new one);
  **Look like** fills them from another faction's, role by role (soldier,
  heavy, officer, elite; others keep their own). The build puts the new
  model's geometry under the old model's name (`"looks"` in factions.json),
  so every template, squad member and placed character that draws it
  changes; a model marked * is drawn by another faction's characters too.
  Vehicles too: each of a faction's vehicles can be drawn as another of the
  same kind that has all its working joints (seats, doors, wheels, guns,
  rotors, treads: anything less leaves the template's seat or gun with
  nothing to attach to), and its ruin and wheel models go with it. **Look
  like** picks the other faction's most used one that fits; one with no fit
  keeps its own.
  With new names and a new look, a faction is as new as the game allows.
- **Sky**: the map's sky and weather. **Base sky** is the map's own (sun
  angle and colour, sky colour, fog, sky texture, rain). **Weather presets**
  are the named weather the game switches to inside a weather zone, or when
  a mission forces it. Each setting is listed with a colour swatch for
  colours: sun, ambient, horizon, sky and zenith colours, fog, sky texture
  and rotation, rain, snow and leaf chances, glow, and the five
  colour-grading lines. Changed settings are highlighted. **New from this**
  copies a preset under a new name; **Revert** puts one back. A **weather
  zone** is a zone of type Weather (New zone), named after its preset.
  Missions switch weather at a moment on the Events tab (**Weather**). Save
  writes `sky/<map>.json`.
  **Sun** sets where the sun is: its compass bearing (0 north, 90 east, 180
  south, 270 west; north is up on the minimap) and height above the horizon,
  written as the config's `SunAngle` (`90 + height`, `bearing - 90`; see
  [formats/dsk.md](formats/dsk.md)). It lights objects and characters. While
  the panel is open the 3D view shows the sun as a yellow disc, with a line
  to it from the view's centre. **From a place and time** sets it from a
  latitude, longitude, date, local time and UTC offset (filled in for Gaza:
  31.5 N, 34.45 E).
  **Ground lighting** bakes the ground's shading from that sun: **Light the
  ground from the sun** writes `terrain/<map>.lighting.json` with its
  **Ambient** colour (everywhere, in shadow too), **Sun** colour (on ground
  facing the sun), **Shadow** (how much of the sun hills' shadows take away,
  0-1), **Keep painted tint**, and a **Tint map** (a PNG in the mod's
  `terrain/` folder, one pixel per height sample, 128 grey neutral) with its
  **Tint strength**. **Preview** lights the ground in the 3D
  view as the build will (a few seconds on a big map); Save keeps it, and the
  ground is then reloaded as the build lit it (about 20 seconds). The ground
  follows the panel's sun, so moving the sun and saving relights it.
  **Seasons** shows on a map whose config has season skies (`[Winter]`,
  `[Fall]`, `[Summer]`: blank's, and so every new map's; SW and NW have
  none). A season's sky texture, colours and glow are drawn instead of the
  base sky's, so a new map's sky is set here. Pick a season and edit its
  settings like a preset's; **Use for every season** gives the others this
  season's changes, and **Revert** puts a season back. Saved per season as
  `sky/<map>.json` `"seasons"`; a hand-written `"all"` shows in every
  season and is saved per season once edited.
- **Animations**: a character's animation table, which clip plays for each
  handle the game asks for (`lb_upright_run_forward`, `ub_reload`,
  `fb_die`...), per set (default, rifle, smg, driver, ...; the game looks in
  the character's set, then default, then the table it falls back to). Pick
  a table (the ones the mod changes are marked); the 3D view plays any
  entry's clip (**▶**) on a character that uses the table, in place, and
  you can turn and zoom it. Change an entry's **clip** (any of the game's
  1,835, typed or picked), its **priority** (a clip cuts in over a lower
  one, or an equal one with flag 0x4; 0-255) or its **flags** (hover the
  box for what each does, listed in words below it); changed entries are
  highlighted and **↺** puts one back. Edits are steps on the undo history.
  Save writes `animtables/<table>.changes.json` (only the entries that
  differ; none left removes it), or the whole table when the mod already
  replaces it (`animtables/<table>.json`). It changes every character with
  that table.
  **Update rates by distance** (folded under the entries) shows `xbox.ini`'s
  four tables of how often a playing clip is updated by the character's
  distance (Low, Medium, High, Lender; a line each: max distance, every N
  frames, interpolation; -1 is out of view). Edit the lines and **Save
  rates** (`config/xbox.ini.json`; tables left as the game's aren't
  written), **Put back the game's**, or **Fix the Medium typo** (retail's
  15 m Medium line is written as Low). They apply to every character.
- **Missions**: make a contract on the map. Pick a mission, or **New
  mission...** with a faction (it takes that faction's next free number on the
  map). The panel is in tabs (the one last open opens again, and each counts
  what's in it): **Overview** (the outline, Check mission, the debug option,
  the Lua line to start it), **Contract** (title, offer, briefing, fee,
  bonus, mood), **Giver & start**, **Objectives**, **Events** (squads that
  appear later, squad orders, radio lines), **Fail** and **Rewards**
  (support items, shop unlocks, the follow-up mail). Objective cards fold to
  one line (click the heading; new ones open). Drag the panel's left edge to
  make it wider (double-click it for the normal width). Give the mission:
  - a title, a description (the contact's offer), a briefing (the contract
    mail), a fee and a flat bonus (paid on completion on top of the fee; 0
    unless you set one), and **Mood on completion**: how each faction's mood
    changes when it's done (-100 to 100; a new mission gives its own faction
    20, as retail contracts give 20 to 40; set another faction below 0 to
    anger it). **Keep the contract if its faction turns hostile**: the game
    cancels a contract when its own faction turns on you (killing their
    people, or civilians in their area); with this it goes on;
  - objectives: **Destroy** (pick a squad, or objects selected on the map
    with **+ Add selected object**; **How many** makes it done after that
    many, "kill 10 of the 20"), **Go to zone** (pick a zone; the zone
    gets a marker on the minimap and GPS), **Escort squad** (the squad
    follows the player; done when the survivors reach the zone, failed if
    they all die, or once fewer than **At least** can still arrive),
    **Deliver vehicle** (a vehicle selected on the map, to a
    zone; failed if it's destroyed), **Capture alive** (subdue a squad or
    objects; killing one fails it; tick **Extract them** to need them flown
    out by the extraction chopper), **Hold zone** (stay in a zone for a
    number of seconds; leaving starts the clock again), **Use object** (walk
    up to objects and use them, with your own prompt; failed if one is
    destroyed) and **Verify target** (kill and photograph the body, or
    capture alive, as the Deck of 52; a marker of your choice, a card suit or
    a person, and an optional **Alive $** paid for each one taken alive),
    and **Finish fast**, a bonus paid if the contract is completed before a
    clock runs out (it counts down from when the bonus is shown; one per
    mission), **Defend** (keep a squad or people and objects picked on the
    map alive until a clock runs out; **At least** sets how many must live)
    **Collect items** (pick up items picked on the map; **How many** of
    them) and **Kill N of a kind** (so many die of the kinds of the objects
    you pick on the map, anywhere, however they die).
    **Show** looks at an objective's squad or zone.
    Main objectives are done in order; one ticked **Together with the one
    before** starts at the same time as the main objective before it, and
    the next waits until the whole group is done, in any order. An
    objective given a **Bonus $** is a
    bonus objective: optional, marked as a bonus, and **Shown** at the start,
    when another objective starts or is done, or when the player enters a
    zone; the contract pays its reward on top if it's done, shows it failed if
    it was shown and not done, and leaves it out if it never was;
  - **Radio lines**: each plays **When** the mission starts, an objective
    starts or is done, the player enters a zone, a squad is wiped out
    (every member dead), the player comes within so many metres of a squad
    (a runner given a path order then flees), or the contract completes (it completes after the
    line). Squads that appear later, squad orders, a bonus objective's
    **Shown** and a fail condition's **Begins** take the same moments, all
    but the last. Radio lines, squads that appear later and orders can also
    wait a number of seconds after their moment (**after … s**: "the ambush
    arrives 30 s after you reach the village");
  - **Music**: at a moment (and delay), one of the game's 26 music tracks
    (the card themes, each faction's action and explore themes, the win and
    fail stingers; ▶ plays one here), or **Back to normal** (the faction's
    own music). The normal music comes back when the contract completes or
    fails; A voice (`bank.cue`, the
    game's or one the mod adds) plays through the game's radio queue with the
    text as its subtitle; with no voice, the text shows as a message. The
    contract completes after the last voiced line. **Find voice** searches
    the game's 2,555 subtitled voice lines (and the mod's own new ones) by
    the words said or the cue; ▶ plays one, **Use** puts it on the line.
    With one of the game's voices, leave the text empty to keep its own
    subtitle (text there replaces that subtitle wherever the game plays it);
  - **Fail conditions**: **Protect squad** (fail if one of them dies), **Time
    limit** (an on-screen clock), **Stay in zone** (fail on leaving it),
    **Alarm raised** (alarms picked on the map), **Spotted trespassing** (by a
    faction you pick), **Turns hostile** (a squad or people picked on the
    map turn on the player) and **Target escapes** (one of them reaches a
    zone: a chase, with a destroy objective and a path order),
    each with the message the failure screen shows. Each **Begins** at the
    start, when an objective starts or is done, or when the player enters a
    zone (a time limit's clock starts then: "two minutes once objective 2
    starts"), and lasts the whole mission or until an objective is done. A
    Stay in zone condition should begin once the player is in the zone (on
    entering it, say);
  - **On completion**: **Shop unlocks** (support items, such as a vehicle
    drop or an airstrike, that become buyable in the shop, as retail
    contracts reward), and a **follow-up mail** that arrives afterwards;
  - **Support**: support items the player gets for the mission (an
    airstrike, a vehicle drop, a supply crate: any of the game's
    `template_support_*`, picked from a list), each with an optional count;
    their usual support comes back when the mission ends;
  - **Squads that appear later**: a squad of the mission's own layer that
    isn't there at the start arrives **When** the mission starts, an
    objective starts or is done, or the player enters a zone (an ambush,
    reinforcements). The build moves it into a layer of its own, loaded at
    that moment;
  - **Squad orders**: at their **When**, a squad **attacks the player**,
    **holds position**, **moves to a point set**, **closes in on the player**
    or **walks a path**. An order for a squad that appears later is given
    once it's there;
  - a **Player start**: **Set here** puts it at the middle of the view (a
    yellow arrow pointing the way the player faces) and grabs it: drag to
    move it (it stays on the ground), press E and drag the ring to turn it;
    **Move/turn** grabs it later, or type a heading. Without one, the player
    starts where the contract leaves them (the HQ);
  - a **Mission giver**: a contact at the faction's HQ (name and prompt can be
    set); someone **placed on the map** (any character template, name,
    prompt, side, marker, and whether they **stay put**, **can't be hurt**,
    or **lose the contract when killed**; a cyan figure you drag and turn like the start
    arrow); **in the faction's HQ contract sequence** (the faction's own HQ
    bouncer offers it after the retail contract you pick, optionally with an
    HQ contact too); or no one (started by the Lua line). **Available after**
    ticks other missions that must be done before a contact or placed giver
    appears, for chains of side contracts. Moving or deleting objectives keeps
    every "objective N" reference (bonus Shown, radio When, fail Lasts) on the
    same objective.

  **Make a feature test mission here** lays out a mission around the middle
  of the view (about 150 m across; open, flat-ish ground is best) that runs
  through most mission features in one playthrough, with debug messages on:
  go to and hold a zone (weather switches, music plays), kill 2 of 3 guards
  together with talking to an officer, an ambush and a bonus when the guards
  are wiped out, capture a civilian, deliver a Humvee, verify a target, a
  finish-fast bonus, a time limit that begins at step 3, support, a shop
  unlock, a follow-up mail and radio lines. Only templates already tested in
  game are used. Play it, and read `foundry_Log()` if a step doesn't happen.
  **Test 2** lays out the rest: a contact placed on the map that stays put
  and can't be hurt, escort (at least 1 of 2 to arrive), capture and extract,
  hold an area, and squad orders (a friendly squad moves to a point set,
  walks a path, closes in, holds), with radio lines saying which. Its fail
  conditions are armed to set off on purpose: let an escortee die (step 2),
  leave the larger area (step 4), shoot the officer (step 5). Play it
  through once, then use Start at (for testing) to try each.

  **Test 3** lays out the newer objective types: defend three civilians
  for 60 seconds while an NK squad attacks (at least 2 must live), collect
  2 of 3 money pickups, and a chase: a runner walks a path to a zone, and
  reaching it fails the mission (target escapes). Play it once through, then
  again letting the civilians die or the runner get away.

  **Duplicate** copies the saved mission and its layer to the next free
  number (of its faction, or another one picked beside it); names made from
  the mission's id, such as its encounters, squads and people, take the new
  id. A giver placed on the map and a place in an HQ contract sequence aren't
  copied: an HQ contact offers the copy. **Delete** moves the mission's files
  (its mission file, its layer and its own script, if any) to `.trash/` in the
  mod; moving them back undoes it, and it warns if another mission lists it
  under "Available after".

  **Show debug messages in game** puts each step of the mission on screen as
  it happens (objectives starting and done, targets down, fail conditions
  coming on and failing, squads arriving, orders, radio lines, completion),
  for test runs. With it or without, the last dozen steps are kept in the
  game's scribble memory, so they outlive the mission: type `foundry_Log()`
  in Foundry Console's Lua box to read them, even after it failed. For a
  mission played on a new map, `foundry_MapStart()` says how far its start
  got (accepted and loading, began, running the mission's script, ran), and
  a contract the map can't start is said on screen and leaves you in free
  roam.

  **Start at (for testing)** starts the mission at a later main objective,
  skipping the ones before it, to test a later part without playing the
  whole mission again. The dropdown turns orange, the outline says so, and
  Check mission notes it, so it isn't shared by accident. Set it back to the
  first objective when done. (Things set to happen when a skipped objective
  starts or is done don't happen.)

  **Mission outline** shows the mission as a timeline in words, updated as
  you edit: what happens at the start, when each objective starts and is
  done, on entering each zone (radio lines, squads arriving, orders, bonus
  objectives shown, fail conditions starting and ending), and what the
  contract pays and does to faction moods. Anything tied to an objective
  that isn't there is flagged.

  **Check mission** runs the build's checks on the saved mission: what would
  stop the build writing its script (unfinished objectives, zones or paths
  that aren't there, a giver's marker), the script it writes checked as the
  game's Lua 5.0 will compile it (syntax, and any function or event callback
  it names that neither it nor the game's scripts define), and notes such as
  the map layers it loads.

  **Add squad** and **Add zone** put a squad or zone in the mission's own
  layer, `encounters/<id>.json`, which loads only during the mission; squads
  and zones in the mod's map layers can be picked too (the mission loads those
  layers). Save writes `missions/<id>.json` first; a mission that can't be
  saved yet doesn't hold up saving the rest. With objectives and no script of
  its own, the build writes the mission's script (see ENCOUNTERS.md). The panel
  shows the Lua line that starts the mission (paste it in Foundry Console's Lua
  box). Clicking the start arrow or the giver selects it even with a building
  behind it.
- **Whole squad** (on by default): selecting a member of one of the mod's
  squads puts the gizmo at the squad's middle. **Move** (W) drags everyone
  together, each kept on the ground; **Turn** (E) and the ring turn the
  formation and every member's facing. The running game gets the moves when
  the drag ends. Untick it to move one member. **Turn squad** turns the
  squad by the given degrees. After a search finds something, the keys go
  back to the view (W and E were typed into the search box before).
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
- **New water** draws a water area: click its outline along the shore, then
  Finish. The game makes water only from a map's own zones (all 26 of the
  game's water zones are in one; a water zone in a layer file gives no water),
  so New water puts it there: on a game map in
  `layout/<map>.lineregion.changes.json`, on a new map in
  `maps/<map>/boundaries.json` (saved from the mod that makes the map; other
  mods' show read only). Its surface starts at the points' average height.
  Selecting it shows its **Surface height** (**To the ground at its points**
  sets it from the ground), **Colour**, **Thickness** (alpha, 0-255; the
  game's is 180-185), **Waves** (the game's .2) and **Texture** (default
  `oceanwater`); edits are undoable. These zones show in light blue, can
  gain and lose points and be deleted. The game holds 16 water zones a map
  (SW uses 7), and Save refuses more. A water zone already in a layer file
  has **Move to the map's own zones**.
- **Place** puts down anything from any map: a template (a vehicle, prop or
  character), one of the game's other named objects, or `model:<model>`, a
  copy of a building or prop that has no name. Type or pick it in the toolbar
  box (the list shows each one's kind and where it's from), click **Place**
  (or press P), then click the ground.
  - With the live link on, the list starts with what the running game has
    loaded where the player is ("spawns live now"): those appear in the game
    as you place them. Anything else shows in game after Save and the next
    game start (the game loads models only with a map or layer).
  - Its layer loads what it needs: its home layer's loads, or, for one from a
    map's own layer (SW, NW), just its model, ruin model and textures.
  - On a new map, characters, vehicles and weapons work too: a new map loads
    what SW loads (its animations and models), apart from SW's own ground and
    layers. `"loads_like": null` in `maps/<name>.json` turns that off. It
    leaves out SW's scenery: models only SW's own layer draws now (not a
    template, not a mod's layer; so a vehicle a mod redraws drops its old
    model), the textures only they draw, and sky textures the new map's own
    config doesn't name.
  - A placed object shows its real model. It moves, turns, renames, copies and
    removes like a copy.
  - With the live link on, it spawns in the running game if the game has that
    template loaded there.
  - It's saved in the mod's layer for the map, `encounters/<mod>_<map>.json`,
    under `"objects"`.
- **Properties** (under a selected object) lists its properties: the game's
  own objects read only, and a copy's or placed object's to change (hit
  points, destructible, material, faction...). A change is that copy's own
  (`"set"`, saved with it), highlighted; changing `GeometryFile` changes the
  model it draws. A new object made from a building (one with a
  `geometryNameRuined`) gets **Ruin model...**: its own ruin, a `.glb` built
  on the original's ruin model, shown when it's destroyed. Without one it
  keeps the original's ruin.
- **New object from a model...** (on a selected object) makes a new object
  from your own model, a `.glb`. Name it, and the editor writes
  `models/<name>.glb` and `models/<name>.new.json`, built on the selected
  object's model; the build makes the model under the new name. It then
  places a copy of the selected object that uses it (`GeometryFile`), beside
  the original: move it and Save. Its properties (type, hit points,
  material...) are the selected object's, so start from something like it:
  a crate for a prop, a wall for a wall. Its textures come from the file: a
  material named like one of the game's textures (as in an exported model)
  keeps it, and any other becomes a new texture, `<model>_<material>`, from
  its PNG image (scaled to power-of-two sides, at most 1024) or, with none, a
  swatch of its base colour. JPEG images can't be read: export them as PNG.
  See-through parts (leaves, fronds, fences) follow each material's
  **alphaMode**: `MASK` or `BLEND` makes its pieces cut out where the image's
  alpha is under half (the game alpha-tests them: MTRL word 5, bit 0x10, an
  image with alpha becomes a DXT3 texture), and `OPAQUE` makes them solid.
  A material with no alphaMode takes the base model's setting for that
  texture, or else its first piece's, so set it on every material when the
  base mixes the two (a tree's leaves and trunk). The 3D view shows them as
  the build makes them. This holds for edited models' new pieces too.
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

## A new map's own look (the game's maps left as they are)

A mod that adds a map can give it characters and vehicles of its own without
changing the game's anywhere else (these are files, not editor tools yet):

- **A copied model:** `models/<name>.new.json`
  `{"base": "sk_hum_soldier", "copy": true, "textures": {"sk_hum_soldier_body": "isle_guard_body"}}`
  is the base model as it is (pieces, skin, skeleton, collision) under a new
  name, drawing other textures: each one the game has or the mod adds
  (`textures/isle_guard_body.png`), and its `_nm` normal map too when both
  exist. Wherever the copy is loaded, the textures it draws are loaded with it.
  No `.glb` is needed (for new shapes, leave out `"copy"` and give one).
  `models/<name>.glb` (and `.lod<N>.glb`) beside it edits the copy as it
  would edit the game's model (pieces edited or added on its skin and
  skeleton); the copy's textures still apply to the pieces it edits.
- **Sounds of its own:** a copied sound cue (`sounds/<bank>/new/<cue>.json`
  `{"like": "air_heli_engine"}`, SOUND.md) with its own waves, and a copied
  effects entry naming it, in `effects.json`:
  `{"isle_heli_h": {"copy": "littlebird_h", "rotor": "vh_lbird.isle_heli_engine"}, "isle_heli_set": {"copy": "littlebird_set", "high_detail": "isle_heli_h"}}`;
  a template copy with `"set": {"effectset": "isle_heli_set"}` then sounds
  its own (the game's helicopters keep theirs).
- **A template under its own name:** a new map's `maps/<name>.json`
  `"templates"` takes `{"template": "template_sk_soldier", "as": "template_isle_guard", "set": {"GeometryFile": "isle_guard"}}`
  as well as plain names: a copy of a game template (even one every map
  loads, such as template_vehicles_ch2's) under the new name, with those
  properties, in the map's template layer. The map's squads, placed objects
  and missions then use `template_isle_guard`; the game's soldier is unchanged.
  Anything the build places from a template by name (`"from"` in
  encounters, HQ guards, travel pilots, mission givers) takes the copy's
  name too: the game template with the copy's properties.
- **Text of its own:** `text/<file>.json`
  `{"map": "isle", "strings": {"faction.allies": "an Isle Guard", ...}}`, and
  `factions.json` with `"map": "isle"`, change the game's texts on that map
  only. Each goes in beside the game's (`<key>.foundry_map_isle`), and the
  map's master script asks Foundry Console (`Foundry_MapText`, OVERLAY.md) to
  show it there; the next level load puts the game's back. Without Foundry
  Console the game's texts show. Keys the game doesn't have are simply
  added. Not yet tried in game: texts the game reads once and keeps (some
  HUD labels may) won't change.
- **HUD art of its own:** `maps/<name>.json`
  `"hud_textures": {"bigflags": "isle_bigflags", "icons01": "isle_icons01", "radarmapels": "isle_radarmapels"}`
  makes the HUD and PDA draw the map's textures (`textures/isle_*.png`,
  loaded with the map) where they drew the game's, while the map is loaded
  (Foundry Console, `Foundry_MapTexture`). Keep the same layout: each sprite
  draws the same part of the image (its place as a fraction of the size), so
  a different size works if everything scales with it. A texture drawn on a
  3D model (global_skflag, the SK flag) isn't HUD art: give the model a copy
  with its own texture instead.
- **Rules for one map:** an objects rule with `"map"` changes only that map:
  `"map": "<a mod's new map>"` changes (`"set"` only) the map's template
  copies it matches by `"template"` or `"model"` (a template every map loads
  isn't copied, so a rule for it warns: give it a copy with `"as"`);
  `"map": "sw"` (a game map) only the layers that map's load list loads (not
  the ones its scripts load by chapter). A rule without `"map"` changes every
  layer in the game.

## A new map's own Deck of 52

`deck/<suit>.json` (one per suit: a map can have several; also in the editor: Deck of 52) gives a new map a suit of its own cards, played
as retail's deck. Shape:

```json
{"suit": "clubs", "map": "gaza",
 "cards": [{"card": "2", "name": "Iyad Kassab", "alias": "The Mole", "role": "Tunnel foreman",
            "description": "...", "bounty": 40000,
            "actor": "gaza_card_clubs02", "layer": "gaza_card_clubs02", "face": "deck/clubs2.png"},
           {"card": "Q", "name": "...", "description": "...", "bounty": 300000}]}
```

- `"card"`: 2 to 10, J, Q, K or A. `"map"` can be left out when the mod has one new map.
- **Text:** the PDA's Deck screen and the verify screen show `"name"` and a bio.
  The bio is `"bio"`, or "Known as <alias>. <role>. <description>". They show
  instead of the game's card (`cards.<suit>.<rank>.Name/.charName/.bio`) while
  the map is loaded, with Foundry Console (map text). Give the card
  character's template the name `cards.<suit>.<rank>.charName` to show it
  above them.
- **Portrait:** `"face"` (or `deck/<suit><rank>.png` if it's there) is the card's
  picture in the PDA: 128x128, with the rank and suit drawn in the top left
  corner as the game's are (export `<suit><rank>_face` to see one). It's
  added as `<map>_<suit><rank>_face` and drawn instead of the game's while
  the map is loaded (Foundry Console: `Foundry_MapTexture`, through the
  texture table, since the PDA draws portraits by name).
- **Number cards (2 to 10)** are characters you place in an encounters layer
  with the editor, guards and all. `"actor"` is the card's name there.
  `"layer"` (optional) is loaded only while the card is at large, as retail
  loads its `<map>_deck_<suit>NN` layers; leave it out if the layer loads
  with the map anyway. In free roam on the map each card at large gets
  retail's verify objective: unmarked until it's in view or within 30 m,
  then marked with the suit's blip. Killed or taken down, its state is
  written, the verify screen shows the card, and `"bounty"` is paid for a
  kill, double alive (default 12,500, retail's chapter 1).
- **The other suits** on the map: `"others": {"name", "bio", "face"}` shows that
  name, bio and portrait for every card of the three suits the deck doesn't
  use, so the PDA shows only the map's people there.
- **Face cards** (J, Q, K, A) get their text and portrait; they come through
  contracts.
- **The PDA's card states** are the game's own save keys
  (`<suit><rank>status` and friends), which the PDA reads itself. Free roam
  on the map swaps in the map's own states (kept under
  `foundry_deck_<map>_*`), and free roam on SW or NW puts the game's back.
  So the map's clubs don't count for retail's clubs, nor the other way.

## Not yet

- Undo for making a new map; the history covers objects, paths, zones, squads, missions,
  sky, factions, the Map panel's settings and travel pilots (saved straight away: Undo
  saves them as they were), and adding, changing and removing ground layers.

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
