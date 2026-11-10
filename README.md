# Mercenaries Foundry

Modding foundation for *Mercenaries: Playground of Destruction*, built on [Mercenaries Recompiled](https://github.com/KraftMacAndChee/Mercenaries-Recompiled).

Goal: make the game deeply moddable. That means open file formats, a shared function map, a stronger plugin interface and an in-game developer overlay, so new content and systems become mod work instead of reverse-engineering projects.

No game data is included. You need your own US retail Xbox copy. See [docs/PROVENANCE.md](docs/PROVENANCE.md) for the clean-room rules.

## Roadmap

1. **Reproducible build** of the port from your disc image. *(done with the port's own build script and the free Build Tools 2022; see docs/STATUS.md)*
2. **`.dsk` archive tool**: list, extract, repack, compare. *(done; all four retail archives rebuild byte-for-byte)*
3. **Asset decoders**: scripts, world/ODF properties, localization *(done; checked on retail data, and edited text, scripts and object properties work in-game)*, models, textures and animation *(done: all 2,297 models export to OBJ with their textures, and edited models import back and show in-game; all 4,715 textures export to PNG, and edited textures go back in-game; all 1,835 animations decode, export to glTF, and edited clips go back in-game)*.
4. **Function and data map**: named functions and structures for entities, factions, spawning, Lua bindings and missions. *(in progress: 626 named functions, including all 360 script commands' C functions and the engine code behind them (spawning, actor lookup, money, AI commands, factions), the level config reader, terrain, surfaces, hit materials and vehicle handling, source files for 1,500 functions, 24 globals and the first structure fields; see [docs/FUNCTION_MAP.md](docs/FUNCTION_MAP.md))*
5. **Plugin interface v2**: call game functions, hooks, new Lua commands, overlay drawing. *(done and tested in game: calls (money given, a Humvee spawned), hooks (every money gain doubled), script commands (a mission script calling plugin commands), tool windows, and overlay drawing into the game's own frames (Foundry Console's F8 panel, shown in fullscreen with no mode switch). See [docs/PLUGINS.md](docs/PLUGINS.md) and the port patches in [port/](port/README.md))*
6. **Developer overlay**: inspector, Lua console, spawning by name. *(done, first version: the Foundry Console plugin (F7 window, F8 in-game panel with a Lua line) inspects the player and nearby objects with their properties, spawns any of the game's 1,611 templates by name, and runs Lua lines in the game's script states; tested in game. See [docs/OVERLAY.md](docs/OVERLAY.md))*

## Making a mod

A mod is a folder of readable files. One command turns it into a folder you drop into Mercenaries Recompiled's `mods/`.

```bash
python -m mercfoundry mod new my_mod
# add your edits (see below), then:
python -m mercfoundry mod build my_mod --data game_files/DATAxbox --out "<port>/mods/My Mod"
```

| Put this in your mod folder | To |
|---|---|
| `text/*.json`: `{"interface.shell.newgame": "NEW GAME"}` | Change text. New keys are added. |
| `scripts/<name>.lua` | Replace the Lua script `<name>`, or add a new one |
| `objects/*.json`: `{"name": "player_humvee", "set": {"physicsenginetorque": "3400"}}` | Change objects in the world. Match by `name` (or `template`), `model`, and/or `near: [x, y, z]` (with `radius`); `layer` narrows it to one world layer. Actions: `set` properties, `move: [dx, dy, dz]`, `position: [x, y, z]`, `turn` (degrees about the vertical), `copy: true` with `as: "new_name"` to place a duplicate (added at the end of the layer), and `remove: true` to take the object out (it stays as an invisible location marker, and removing a named object warns, since scripts may use it). Saved games keep each object's state by its place in its layer, so the game's objects never change places. For vehicle handling, `vehicles DATAxbox <model> -o objects/<model>.json` writes a rule holding all of a vehicle's `physics…` values ([docs/VEHICLES.md](docs/VEHICLES.md)). `obstaclematerialtype` sets what an object counts as when hit (`wood`, `solidmetal`, `sheetmetal`, `concrete`, `glass`, ...; see [formats/dsk.md](docs/formats/dsk.md)). |
| `textures/<name>.png` | Replace the texture `<name>`, or add a new one (power-of-two sizes) |
| `models/<name>.obj` or `.glb`/`.gltf` | Replace a model with an edited export from `model`. In glTF, moved joints (seats, mounts, doors) take their new positions with everything attached, and empties added under a joint become new joints that templates can use (e.g. `RiderSeatpoint_A`). |
| `models/<name>.collision.json`: `{"shapes": {"0": 7}}` | Set a model's collision flags per shape: bit 1 makes it solid to people and vehicles, bit 2 makes bullets hit it (see [formats/dsk.md](docs/formats/dsk.md)). Shape numbers are as `model --collision` lists them. |
| `animations/*.glb` or `.gltf` | Replace game animations with clips of the same name (Blender's `Armature\|` prefix and `.001` suffixes are ignored). Export them with `model --glb --anim`, edit, and drop the file here. Joints a clip doesn't animate keep the game's motion. |
| `animations/new.json`: `{"my_run": "all_american_lb_upright_run_forward"}` | Add the clips named here (from the `.glb` files beside it) as new animations, built on the template clip's skeleton and loaded wherever it is. |
| `encounters/<layer>.json` | Add a new world layer: objects copied from existing ones, placed where you say, grouped into encounters with point sets and squads, plus paths (patrols) and boundaries (trigger zones, restricted areas). Load it from a script with `Utility_ReadLevelFile('<layer>')` and command it with `Squad_Command`. See [docs/ENCOUNTERS.md](docs/ENCOUNTERS.md). |
| `missions/<map>_<faction><n>.json` | Add a contract mission (e.g. `sw_allies9`): the build adds it to the faction's mission arc, lists its script in the map, adds its `mission.ini` section and, if given, its title, description, contract mail and objective strings. A contact beside the faction's HQ bouncer offers it as a side contract. Supply `scripts/sw_allies9.lua` and `encounters/sw_allies9.json`. See [docs/ENCOUNTERS.md](docs/ENCOUNTERS.md#new-missions). |
| `terrain/<name>.heights.png` | Reshape a map's ground. Export with `terrain DATAxbox swn` (the first level's map), edit the 16-bit PNG in any image editor, and keep the `.heights.json` beside it. The build relights changed slopes and the shadows they cast. |
| `terrain/<name>.layerNN.*.png` | Repaint the ground: each greyscale image is one texture layer's opacity (from the same `terrain` export; ship only the layers you change). `<name>.layers.json` changes a layer's texture, scale or projection, or adds layers (16 at most). |
| `terrain/<name>.decals.json` | Replace a map's decals (roads, concrete, parking; exported by `terrain`), or lay roads from a few world points: `{"roads": [{"texture": "sw_decal_road_straight", "points": [[x, z], ...]}]}`. Roads keep the map's own decals. |
| `terrain/<name>.surfaces.json` | Set each ground or decal texture's surface (`dirt`, `road`, `snow`, `grass`, `rock`: what shots and blasts kick up), dust, debris and effect. Exported by `terrain`; the build writes the level's config file (e.g. `DATAxbox/SW/SWN.ini`) into the mod. |
| `animtables/<name>.json` | Replace a character's animation table: which clip plays for each handle the game asks for (e.g. `lb_upright_run_forward`), per weapon or seat set. Export one with `animtable DATAxbox player_american`. |
| `sounds/<bank>/<cue>.wav` | Replace a sound: every wave the cue plays, or one with `<cue>.<n>.wav`. Export them with `sound export DATAxbox fio SWNfio01`; any 16-bit PCM `.wav` works and the build converts it to the game's format. See [docs/SOUND.md](docs/SOUND.md). |
| `sounds/<bank>/new/<cue>.wav` | Add a new cue, e.g. `sounds/fio/new/MyLine01.wav` for a new Fiona line that scripts play with `Audio_PlayVoiceover('fio.MyLine01')`. |
| `layout/<layer>.<kind>.json` | Replace a layer's paths, boundaries, regions or encounters (`kind` = `path`, `lineregion`, `region`, `encounter`), or `layout/<layer>.list.txt` for its load list. Export them with `layout DATAxbox <layer>`. |

### Editor

```bash
python -m mercfoundry edit my_mod --data game_files/DATAxbox
```

Opens the Foundry editor in your browser (a local web app; nothing leaves your PC): a 3D view of a map's terrain, objects, paths and zones. Move, turn, copy and remove objects; drag the points of paths and zones; draw new patrol paths and trigger zones; place squads and their spawn points. With the game running, a live link moves, turns and removes objects in game as you edit and shows where the player is. Edits are saved as ordinary mod files (object rules, layout changes, encounter layers). See [docs/EDITOR.md](docs/EDITOR.md).

### Sharing mods

Share a mod as its source folder, packed first:

```bash
python -m mercfoundry mod pack my_mod my_mod_shared --data game_files/DATAxbox --check
```

Files made from game exports hold the game's own content, so `mod pack` rewrites them as just your changes. The build applies those changes to each player's own copy of the game:

| You edited | Shared as |
|---|---|
| a game script (`scripts/<name>.lua`) | `<name>.lua.diff`, a diff against the game's script |
| the map's heights or ground paint | `<map>.heights.changes.json` (changed samples) and `<map>.layerNN.<texture>.changes.png` (changed pixels) |
| a game texture | `<name>.changes.png` plus `<name>.mask.png` (changed pixels) |
| a model, as glTF | `<name>.changes.glb`: edited and new pieces, the joint tree and the removed pieces' numbers; no textures |
| animation clips | the same `.glb` names, holding only the channels that change the game's clip (or a new clip's template) |
| animation tables, layouts, load lists | `.changes.json` with only the changed entries or items; load lists as diffs |

`--check` builds both folders and confirms they produce the same archives. Model edits made as OBJ can't be packed; edit models as glTF. Diffs and changes files also merge better: two mods' diffs to one script both apply when they change different lines.

Don't share a build: it holds complete copies of the game's archives. `mod pack` also records the toolkit version in `mod.json` (`"requires": "0.51"`), so an older toolkit reports that it's too old instead of failing on a newer feature. A build holds complete copies of the game's archives, so it can't be shared, and two built mods that edit the same shared assets (mission arcs, map script lists, the HQ contact script) overwrite each other.

Players put source folders in the game's `mods/` like any mod and turn them on in the launcher. `mod sync` builds every enabled source mod together, in load order, from the player's own game data into `mods/Foundry Mods`:

```bash
python -m mercfoundry mod sync "<port folder>" --install-hook
```

Players don't need Python: `python packaging/build.py --bundle python-3.13.17-embed-amd64.zip` makes `dist/foundry/`, a folder holding the toolkit (`mercfoundry.pyz`), python.org's embeddable Python and `install.cmd`. Players copy it into the game folder and run `install.cmd` once. Download the embeddable package from python.org and check its published SHA-256 first. A standalone `.exe` isn't used, because Windows application control and Smart App Control block unsigned programs, while python.org's `python.exe` is signed. With Python installed, `python mercfoundry.pyz mod sync <port folder> --install-hook` does the same. `--install-hook` writes `foundry-sync.cmd`, which the launcher runs before each modded launch (port patch `0007`), so changing the selection is enough. Without the hook, run `mod sync <port folder> --enable` after changing it. Mods merge where they can: text, object rules, missions and their HQ contacts, map lists, level configs and terrain (only the samples and pixels each mod changed). Missions are added in the order their arcs need, whatever the load order. When a later mod replaces something an earlier one changed (a script, model, texture, animation or layout), the later one wins and the sync reports the conflict. Two mods adding an asset with the same name is an error. `mod build` takes several folders the same way.

### Finding objects

```bash
python -m mercfoundry find game_files/DATAxbox --near-name loc_drop_start --layer swn --radius 25
python -m mercfoundry find game_files/DATAxbox --model "*rock*" --layer swn --limit 20
python -m mercfoundry find game_files/DATAxbox --property Faction=China --model "*veh*"
```

Add `--layout` to search paths, boundaries (e.g. mission kill zones), regions, encounters and spawn point sets instead of objects (pass `--names names.txt` to show layer names).

Lists matching objects with their layer, model, name, position and heading (and distance with `--near`/`--near-name`). Patterns match part of a name, or the whole name when they contain `*` or `?`; case is ignored. `--rule` prints a mod rule for each match, ready to paste into `objects/` and edit (it targets that one object by name, or by position if it has none). `--json` prints full details. Pass a mod's `assets.dsk` instead of `DATAxbox` to search a built mod.

`mod build` writes only the archives your edits change, checks them, and records what changed in `foundry-mod.json`. Get originals to start from with `extract --decode`, `texture` and `model`. See [examples/faster-humvee](examples/faster-humvee) for a small working mod.

## Archive tool

Requires Python 3.10+. No other dependencies.

```bash
python -m mercfoundry info   DATAxbox/assets.dsk
python -m mercfoundry list   DATAxbox/assets.dsk --type script
python -m mercfoundry tree   DATAxbox/assets.dsk <index|name|key>
python -m mercfoundry extract DATAxbox/assets.dsk extracted/assets
python -m mercfoundry pack   extracted/assets out/assets.dsk
python -m mercfoundry diff   DATAxbox/assets.dsk out/assets.dsk
python -m mercfoundry hash   script world
```

`extract` writes one file per record plus `manifest.json`. `pack` rebuilds the archive exactly. To mod it, replace record files, or add manifest entries with a `name` and `type`, then repack. Put the result in a Mercenaries Recompiled `mods/<YourMod>/` folder.

### First run on your game files

```bash
python -m mercfoundry unpack-iso "Mercenaries.iso" game_files
cd game_files
python -m mercfoundry verify DATAxbox
python -m mercfoundry names default.xbe DATAxbox/*.dsk --match DATAxbox/assets.dsk --match DATAxbox/streamed.dsk -o names.txt
python -m mercfoundry info DATAxbox/assets.dsk
```

`unpack-iso` extracts a full or trimmed (xiso) disc image; `game_files/` is ignored by git. `verify` checks that every archive, script, world layer and text table rebuilds exactly, and lists anything that doesn't. `names` scans the executable and archives (including decompressed scripts) for strings whose hash matches a real asset or type ID, and writes them to a file you can pass to other commands with `--names`. Types shown with a `?` are guessed from their hash.

### Editing scripts, objects and text

```bash
python -m mercfoundry extract DATAxbox/assets.dsk work/assets --decode
# edit work/assets/decoded/*.lua, *.world.json or *.text.json
python -m mercfoundry pack work/assets out/assets.dsk
```

`--decode` writes a readable copy of each Lua script (decompressed), world layer (object instances with named properties) and text table (strings by dotted key). Add `--textures` to also write textures as PNG (all of them takes a few minutes for `streamed.dsk`; `--textures humvee` writes only textures whose names contain "humvee"). Edited PNGs are re-encoded in the original format with new mip levels; sizes must be powers of two. `pack` re-encodes any decoded file you changed back into its record first; untouched files leave the archive byte-identical. Run `rebuild` to do that step on its own, or `pack --no-rebuild` to skip it. Putting a decoded file back exactly as it was restores the original record. In a text file, change a value to edit a string or add a new key to add one; keys you delete are left unchanged in the archive.

### Exporting models

```bash
python -m mercfoundry model DATAxbox allies_veh_humvee -o models --preview --names names.txt
```

Writes `allies_veh_humvee.obj` and `.mtl` (opens in Blender and most 3D tools), each texture it uses as a PNG, and with `--preview` a textured preview image. Add `--anim "all_american_lb_*"` (with `--glb`) to include the game's animations as glTF clips, and `--in-place` to drop root motion. Add `--glb` for a glTF file with the skeleton as bones, each piece parented to its joint, character skinning and embedded textures; it is checked vertex by vertex against the game model before it's written. `model-import` reads both OBJ and glTF. `--lod N` picks a level of detail; `--damaged` shows a vehicle's damaged body panels. Model names come from `list DATAxbox/streamed.dsk --type Model`.

```bash
python -m mercfoundry texture DATAxbox allies_avehicle_humvee_001 -o textures
```

Exports one texture as PNG (all six faces for cube maps; `--mip N` for smaller levels).

### Editing models

```bash
python -m mercfoundry extract DATAxbox/locked.dsk work/locked
python -m mercfoundry extract DATAxbox/streamed.dsk work/streamed
python -m mercfoundry model DATAxbox allies_veh_humvee -o models
# edit models/allies_veh_humvee.obj in Blender, export OBJ (keep object names)
python -m mercfoundry model-import models/allies_veh_humvee.obj allies_veh_humvee work/locked work/streamed
python -m mercfoundry pack work/locked out/locked.dsk
python -m mercfoundry pack work/streamed out/streamed.dsk
```

Each object in the export is one piece, named `p<index>_<joint>`; keep those names so edits go back to the right piece. Delete an object to remove a piece. Add an object to add a piece: name it `new_<joint>` to attach it to a joint (for example `new_turret_a` turns with the turret), and give it a material named after a game texture. Many models are in both `locked.dsk` and `streamed.dsk`; pass every folder and `model-import` updates each copy. Use `--lod`/`--damaged` if you exported with them. New pieces get a collision box automatically. It joins the model's box shape; on vehicles like the Humvee, whose second shape is a convex hull, that hull is replaced by boxes approximating it so the new box can join (a third shape breaks vehicle physics, so none is added); put `nocoll` in an object's name to skip it (decoration), or pass `--no-collision`. Collision of existing pieces isn't changed. `model --collision` previews a model's collision shapes.

glTF works the same way (`model-import allies_veh_humvee.glb ...`, or a `.glb` in a mod's `models/` folder), with more kept from Blender: each piece goes back where it sits in the file, a new object belongs to the bone it is parented to (no `new_<joint>` name needed), and skinned meshes keep each vertex's strongest bone, so reweighting works and new skinned pieces can be added. The export records its level of detail and damage state, so `--lod`/`--damaged` aren't needed. Bones themselves aren't imported; moving one gives a warning.

Format details: [docs/formats/dsk.md](docs/formats/dsk.md). Related Battlefront tools and docs: [docs/RESOURCES.md](docs/RESOURCES.md).

## Tests

```bash
python -m unittest discover -s tests -t .
```

## Layout

- `mercfoundry/`: Python tools
- `docs/`: formats and project rules
- `external/`: local checkout of the port (ignored by git)
