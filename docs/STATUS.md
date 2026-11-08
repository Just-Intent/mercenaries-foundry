# Status (2026-11-08)

## Done
- Project set up: `mercfoundry/` Python tools, docs, MIT license, `.gitignore` that blocks game data.
- Upstream port cloned to `external/Mercenaries-Recompiled` (ignored by git; at commit `f513ae4`, v1.1.1).
- `.dsk` tool v0.1: info, list, tree, extract, pack, diff, hash.
- Decoders v0.2: Lua scripts (decompress and recompress), world layers (object properties as JSON), localized text (edit existing strings). `extract --decode`, `rebuild`, and `pack` re-encodes edited files. 27 tests pass on synthetic data.
- `verify` (whole-folder rebuild check) and `names` (recover names from the executable and data by hash). 32 tests pass.
- Format notes in `docs/formats/dsk.md`; Battlefront overlap in `docs/RESOURCES.md`; rules in `docs/PROVENANCE.md`.

## Blockers
- No game data yet. Needs the US retail Xbox disc image, extracted to `DATAxbox/`. (The port's launcher can extract it, or use xdvdfs.)
- Visual Studio 2022 (MSVC 14.44, Windows SDK 10.0.26100.0 is installed) and CMake are missing; both are needed to build the port. Python 3.13 is installed; the port asks for 3.12.

## Next steps
1. Run `verify DATAxbox` and fix anything it reports.
2. Run `names` on `default.xbe` and the archives, then `info` with `--names`, and record which type IDs are still unknown.
3. Test whether swbf-unmunge's Xbox texture/model readers (MIT) handle Mercenaries records.
4. Test in-game: a recompressed script, an edited object property and an edited string.
5. Work out the rest of registry `INFO` so new strings can be added.
6. Install the build tools and build the port from the disc image.

## Notes
- Several port tools mention replacing "source-informed" versions. Use only versions re-derived from retail (see PROVENANCE.md).
- The port has a debug-symbol tool (`tools/debug_symbols`) that recovers original source file names from strings in the executable. That's useful for the function map.
