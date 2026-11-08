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
- Nothing has been tested in-game yet.

## Port build (2026-11-08)
- Built Mercenaries Recompiled `f513ae4` from the disc image with `Build-From-Iso.ps1`: about 14,000 functions translated, then compiled with MSVC 19.44 (v143) and SDK 10.0.26100.0. Output: `external/Mercenaries-Recompiled/build/mercenaries/bin/Release/` (`Mercenaries Recompiled.exe` launcher, `mercenaries_recomp.exe` runtime, linker `.map`).
- Gotcha: in Windows PowerShell 5.1, don't capture the script's output with `*>&1`. Warnings from Python then become fatal errors. Run it in its own process and redirect there: `cmd /c 'powershell -File ... > log 2>&1'`.
- The extraction step refuses to unpack over an existing `game_files/mercenaries-retail`; delete it first when rerunning.

## Toolchain (ready)
- Build Tools for Visual Studio 2022 17.14 (free) at `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`, with the pinned MSVC 14.44.35207 and Windows SDK 10.0.26100.0. No paid Visual Studio edition is needed.
- Python 3.12.10 alongside 3.13. The port's `.venv` has its pinned CMake 3.31.6, Ninja 1.13.0 and Capstone 5.0.6.

## Next steps
1. Model decoder: vertex layout in `VBUF`, materials, skeleton; export to a standard format (glTF or OBJ).
2. Texture decoder; check whether swbf-unmunge's Xbox texture code (MIT) applies.
3. Install the build tools and build the port.
4. In-game tests: a recompressed script, an edited object property, an edited and an added string.
5. Decode the remaining world-layer chunks (`TNAM`, `INFO`) and `path`/`region`/`encounter` records.

## Notes
- Several port tools mention replacing "source-informed" versions. Use only versions re-derived from retail (see PROVENANCE.md).
- The port has a debug-symbol tool (`tools/debug_symbols`) that recovers original source file names from strings in the executable. That's useful for the function map.
