# Status (2026-11-08)

## Done
- Project set up: `mercfoundry/` Python tools, docs, MIT license, `.gitignore` that blocks game data.
- Upstream port cloned to `external/Mercenaries-Recompiled` (ignored by git; at commit `f513ae4`, v1.1.1).
- `.dsk` tool: info, list, tree, extract, pack, diff, hash, verify, names, unpack-iso.
- Decoders: Lua scripts (decompress and recompress), world layers (object properties as JSON), localized text (edit and add strings). `extract --decode`, then `pack` re-encodes edited files.
- **Checked on retail data** (US Xbox, Redump MD5 `e340535feeadc0ff9b71506c625bf288`):
  - All four archives rebuild byte-for-byte.
  - All 208 scripts, 824 world layers and 91 text tables (6,905 strings) rebuild exactly from their decoded form.
  - Editing a vehicle name and adding a new string works and changes only that record.
  - 9,618 of 9,723 hashes named from strings in the game; every record type now has a name.
  - Model geometry (`segments`) uses the script compression; all 2,954 records decompress.
- 35 tests pass.

## Fixed from retail data
- Text values aren't NUL-terminated. The decoder had assumed they were and found no strings.
- Text header layout is now known, so new strings can be added.

## Blockers
- Visual Studio 2022 (MSVC 14.44, Windows SDK 10.0.26100.0 is installed) and CMake are missing; both are needed to build the port. Python 3.13 is installed; the port asks for 3.12.
- Nothing has been tested in-game yet; that needs the port built.

## Next steps
1. Model decoder: vertex layout in `VBUF`, materials, skeleton; export to a standard format (glTF or OBJ).
2. Texture decoder; check whether swbf-unmunge's Xbox texture code (MIT) applies.
3. Install the build tools and build the port.
4. In-game tests: a recompressed script, an edited object property, an edited and an added string.
5. Decode the remaining world-layer chunks (`TNAM`, `INFO`) and `path`/`region`/`encounter` records.

## Notes
- Several port tools mention replacing "source-informed" versions. Use only versions re-derived from retail (see PROVENANCE.md).
- The port has a debug-symbol tool (`tools/debug_symbols`) that recovers original source file names from strings in the executable. That's useful for the function map.
