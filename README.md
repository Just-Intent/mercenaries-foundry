# Mercenaries Foundry

Modding foundation for *Mercenaries: Playground of Destruction*, built on [Mercenaries Recompiled](https://github.com/KraftMacAndChee/Mercenaries-Recompiled).

Goal: make the game deeply moddable. That means open file formats, a shared function map, a stronger plugin interface and an in-game developer overlay, so new content and systems become mod work instead of reverse-engineering projects.

No game data is included. You need your own US retail Xbox copy. See [docs/PROVENANCE.md](docs/PROVENANCE.md) for the clean-room rules.

## Roadmap

1. **Reproducible build** of the port from your disc image.
2. **`.dsk` archive tool**: list, extract, repack, compare. *(v0.1, tested on synthetic archives)*
3. **Asset decoders**: scripts, world/ODF properties, localization *(v0.2, tested on synthetic records)*, then textures and models.
4. **Function and data map**: named functions and structures for entities, factions, spawning, Lua bindings and missions.
5. **Plugin interface v2**: call game functions, hooks, new Lua commands, overlay drawing.
6. **Developer overlay**: inspector, Lua console, spawning by name.

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
python -m mercfoundry verify DATAxbox
python -m mercfoundry names default.xbe DATAxbox/*.dsk --match DATAxbox/assets.dsk -o names.txt
python -m mercfoundry info DATAxbox/assets.dsk
```

`verify` checks that every archive, script, world layer and text table rebuilds exactly, and lists anything that doesn't. `names` scans the executable and archives (including decompressed scripts) for strings whose hash matches a real asset or type ID, and writes them to a file you can pass to other commands with `--names`. Types shown with a `?` are guessed from their hash.

### Editing scripts, objects and text

```bash
python -m mercfoundry extract DATAxbox/assets.dsk work/assets --decode
# edit work/assets/decoded/*.lua, *.world.json or *.text.json
python -m mercfoundry pack work/assets out/assets.dsk
```

`--decode` writes a readable copy of each Lua script (decompressed), world layer (object instances with named properties) and text table (strings by dotted key). `pack` re-encodes any decoded file you changed back into its record first; untouched files leave the archive byte-identical. Run `rebuild` to do that step on its own, or `pack --no-rebuild` to skip it. Text editing changes existing strings only; adding new ones isn't supported yet.

Format details: [docs/formats/dsk.md](docs/formats/dsk.md). Related Battlefront tools and docs: [docs/RESOURCES.md](docs/RESOURCES.md).

## Tests

```bash
python -m unittest discover -s tests -t .
```

## Layout

- `mercfoundry/`: Python tools
- `docs/`: formats and project rules
- `external/`: local checkout of the port (ignored by git)
