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

- **Saving to** (top left) names the mod everything you save goes into.
  Click it for the Mods panel: the game's source mods, **Edit this mod** to
  switch to another (the map reloads from it), **Built by the launcher**
  (its place in the launcher's list), the mod's name and description
  (`mod.json`), **Make the mod** (a new source mod in the mods folder,
  built by the launcher, switched to) and **Delete** (the folder moves to
  the game's `foundry-trash` folder and off the launcher's list; move it
  back to restore it). The mod being edited can't be deleted. The view
  still shows what the other enabled mods add; saving only writes to the
  one named.
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
  out).
- **New maps**: **Map** > New map makes one: `maps/<name>.json`, a copy
  of a game map (blank: flat and nearly empty), and switches to it. **Ground**
  picks its size: as the copy's, or flat ground 256, 512 or 1024 cells a side
  (2, 4 or 8 km at 8 m; `"size"`), with the copy's texture layers and no
  roads. 1024 is untried in game. It
  appears in the Map list like the game's maps, with the copied map's ground
  to reshape and paint (Terrain), its sky (Sky), and objects, squads and
  zones to add. On a new map, the Map panel sets where the player arrives
  (the middle of the view, and a heading), a message on arrival, and which
  of the mod's layers on it load with it (objects placed in the editor go in
  one that does). The build writes its master script (run as it loads)
  from these; with none of them set, it's a copy of the source map's
  (blank's prints "Hello World!"). Write `scripts/<name>.lua` to make your
  own instead. Its config names the script (`MainMasterScript <name>`): a level
  runs only the script its config names, and blank's names none. Enter it with `Utility_ResetToBaseLayerOnly('<name>')` from
  Foundry Console.
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
  Pilots and a new map's arrival point show in the view like objects (a
  pilot, or a marker figure for the arrival point): move and turn them as
  objects, and Save writes them back to their `maps/<name>.json`. Add and
  remove them in the Map panel.
  On SW or NW, "Where you arrive back from <map>" is where the pilot back
  puts the player (and which way they face): move it into the open.
- **Factions**: new names for the four employers (`factions.json`): name,
  adjective, short form and people, per faction; **Everywhere** for mission
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
    mission).
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
    (every member dead), or the contract completes (it completes after the
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
    faction you pick) and **Turns hostile** (a squad or people picked on the
    map turn on the player),
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
  in Foundry Console's Lua box to read them, even after it failed.

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
    layers. `"loads_like": null` in `maps/<name>.json` turns that off.
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

- Undo for terrain beyond its strokes and the Map panel (saved straight away); the history covers
  objects, paths, zones, squads, missions, sky and factions.

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
