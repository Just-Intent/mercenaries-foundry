# Mercenaries Foundry

Modding foundation for *Mercenaries: Playground of Destruction*, built on [Mercenaries Recompiled](https://github.com/KraftMacAndChee/Mercenaries-Recompiled).

Goal: make the game deeply moddable. That means open file formats, a shared function map, a stronger plugin interface and an in-game developer overlay, so new content and systems become mod work instead of reverse-engineering projects.

No game data is included. You need your own US retail Xbox copy. See [docs/PROVENANCE.md](docs/PROVENANCE.md) for the clean-room rules.

## Roadmap

1. **Reproducible build** of the port from your disc image. *(done with the port's own build script and the free Build Tools 2022; see docs/STATUS.md)*
2. **`.dsk` archive tool**: list, extract, repack, compare. *(done; all four retail archives rebuild byte-for-byte)*
3. **Asset decoders**: scripts, world/ODF properties, localization *(done; checked on retail data, and edited text, scripts and object properties work in-game)*, models and textures *(done: all 2,297 models export to OBJ with their textures, and edited models import back and show in-game; all 4,715 textures export to PNG, and edited textures go back in-game)*, then animation.
4. **Function and data map**: named functions and structures for entities, factions, spawning, Lua bindings and missions.
5. **Plugin interface v2**: call game functions, hooks, new Lua commands, overlay drawing.
6. **Developer overlay**: inspector, Lua console, spawning by name.

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
| `objects/*.json`: `{"template": "player_humvee", "set": {"physicsenginetorque": "3400"}}` | Change object properties. Match by `template` and/or `model`; `layer` narrows it to one world layer. |
| `textures/<name>.png` | Replace the texture `<name>`, or add a new one (power-of-two sizes) |
| `models/<name>.obj` | Replace a model with an edited export from `model` |

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

Writes `allies_veh_humvee.obj` and `.mtl` (opens in Blender and most 3D tools), each texture it uses as a PNG, and with `--preview` a textured preview image. `--lod N` picks a level of detail; `--damaged` shows a vehicle's damaged body panels. Model names come from `list DATAxbox/streamed.dsk --type Model`.

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

Each object in the export is one piece, named `p<index>_<joint>`; keep those names so edits go back to the right piece. Delete an object to remove a piece. Add an object to add a piece: name it `new_<joint>` to attach it to a joint (for example `new_turret_a` turns with the turret), and give it a material named after a game texture. Many models are in both `locked.dsk` and `streamed.dsk`; pass every folder and `model-import` updates each copy. Use `--lod`/`--damaged` if you exported with them. New pieces get a collision box automatically; put `nocoll` in an object's name to skip it (decoration), or pass `--no-collision`. Collision of existing pieces isn't changed. `model --collision` previews a model's collision shapes.

Format details: [docs/formats/dsk.md](docs/formats/dsk.md). Related Battlefront tools and docs: [docs/RESOURCES.md](docs/RESOURCES.md).

## Tests

```bash
python -m unittest discover -s tests -t .
```

## Layout

- `mercfoundry/`: Python tools
- `docs/`: formats and project rules
- `external/`: local checkout of the port (ignored by git)
