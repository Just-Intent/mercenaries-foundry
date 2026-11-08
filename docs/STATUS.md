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
- Model decoder: all 2,297 models decode (1.35 million triangles): skeleton, joint placement, levels of detail, pristine/damaged panels, normals, texture coordinates, vertex colours, texture names. `model` exports OBJ plus a preview image. 2,285 models match their stored bounds exactly; the other 12 differ only around helicopter rotors.
- Texture decoder: all 4,715 textures (DXT1, DXT2/3, swizzled A8R8G8B8, 4 cube maps). `texture` exports PNG; `model` now exports textures with the OBJ and draws them in its preview, which confirms the texture coordinates.
- Texture re-import: PNG reader, DXT1/DXT3 and swizzled A8R8G8B8 encoders with mip generation, hooked into `extract --decode --textures` and `pack`. Re-encoding the game's own textures is lossless for 97% of a sample. `rebuild` now tracks decoded-file hashes and restores originals when an edit is undone.
- Model import: `model-import` brings an edited OBJ back (edit, add and remove pieces; positions re-quantized; strips rebuilt; geometry recompressed). Unedited exports re-import byte-identical for every model, level of detail and damage state.
- 75 tests pass.

## Fixed from retail data
- Text values aren't NUL-terminated. The decoder had assumed they were and found no strings.
- Text header layout is now known, so new strings can be added.

## In-game tests (all passed)
- 2026-11-08, text: an `english.dsk` rebuilt by our tools, installed as `mods/Foundry Test/`, showed edited "PRESS START" and "NEW GAME" labels. The edited `veh.allies_apache` name isn't shown during play (the player spawns in it), so it's unconfirmed; it may appear in the shop.
- 2026-11-08, script, object and added text, in the opening mission (`allies0`, after the C-17 briefing):
  - A recompressed `allies0` script with two new functions ran: its message box text appeared on schedule.
  - A newly added text key (`foundry.test.scriptmsg`) resolved and displayed.
  - Raising `physicsenginetorque` from 850 to 3400 on `player_humvee` (in `swn_hq`) and the Humvee templates made the Humvee visibly faster.
- So the game accepts our compressor's output, rebuilt world layers with edited properties, and added text entries (with `num_components` raised).
- 2026-11-08, texture: `allies_avehicle_humvee_001` tinted red, re-encoded by our DXT3 encoder with a full mip chain and packed into `streamed.dsk`, showed on the player's Humvee in the opening mission.
- Lesson: a new game starts in `playable_intro` (C-17 briefing, then `allies0`), not the Ace of Clubs, which is Allied mission 4. Script tests belong there.

## Port build (2026-11-08)
- Built Mercenaries Recompiled `f513ae4` from the disc image with `Build-From-Iso.ps1`: about 14,000 functions translated, then compiled with MSVC 19.44 (v143) and SDK 10.0.26100.0. Output: `external/Mercenaries-Recompiled/build/mercenaries/bin/Release/` (`Mercenaries Recompiled.exe` launcher, `mercenaries_recomp.exe` runtime, linker `.map`).
- Gotcha: in Windows PowerShell 5.1, don't capture the script's output with `*>&1`. Warnings from Python then become fatal errors. Run it in its own process and redirect there: `cmd /c 'powershell -File ... > log 2>&1'`.
- The extraction step refuses to unpack over an existing `game_files/mercenaries-retail`; delete it first when rerunning.

## Toolchain (ready)
- Build Tools for Visual Studio 2022 17.14 (free) at `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`, with the pinned MSVC 14.44.35207 and Windows SDK 10.0.26100.0. No paid Visual Studio edition is needed.
- Python 3.12.10 alongside 3.13. The port's `.venv` has its pinned CMake 3.31.6, Ninja 1.13.0 and Capstone 5.0.6.

## Next steps
1. In-game test of an imported model (Humvee with 3x gun barrel and a rear crate, in `mods/Foundry Test`).
2. Models: glTF export with the skeleton and skin weights.
3. A `mod` command that builds a mod folder from a list of edits, so tests like these are one step.
4. Decode the remaining world-layer chunks (`TNAM`, `INFO`) and `path`/`region`/`encounter` records.

## Notes
- Several port tools mention replacing "source-informed" versions. Use only versions re-derived from retail (see PROVENANCE.md).
- The port has a debug-symbol tool (`tools/debug_symbols`) that recovers original source file names from strings in the executable. That's useful for the function map.
