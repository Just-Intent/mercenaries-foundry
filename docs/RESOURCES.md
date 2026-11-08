# Related resources

Mercenaries shares Pandemic's chunk format and name hash with Star Wars Battlefront (Zero engine). Researched November 2026; verify before relying on any of this.

## Verified overlap
- **Name hash is identical**: FNV-1a 32-bit, each byte `| 0x20`. Battlefront name dictionaries may resolve some Mercenaries hashes. ([Gametoast: Hashed strings](https://github.com/Gametoast/Documentation/wiki/Hashed-strings))
- **Shared tags**: `ucfb`, `scr_` (+`NAME`), `wrld` (+`NAME`, `inst`, `PROP`).

## Known differences
- Mercenaries `wrld` has a `TABL` string table, and `PROP` stores pairs of table hashes. In Battlefront, `PROP` is a property-name hash followed by a string value.
- Localization: Battlefront uses `Locl`; Mercenaries uses `reg_`/`SCPE`/`BIN_`.

## Code
| Project | License | Use |
|---|---|---|
| [swbf-unmunge](https://github.com/PrismaticFlower/swbf-unmunge) | MIT | Reusable with attribution. Has **Xbox** texture/model/terrain handlers, a ucfb reader/writer and a hash dictionary. Best next lead. |
| [LibSWBF2](https://github.com/Ben1138/LibSWBF2) | GPL-3.0 | Consult only (copying code would make our tools GPL). |
| [WorldEdit](https://github.com/PrismaticFlower/WorldEdit) | Apache-2.0 | Battlefront 2 world editor; useful reference for our editor. |

## Docs
- Official SWBF2 mod tools (2006) include an ODF guide and the Lua scripting reference ([index](https://sites.google.com/site/swbf2modtoolsdocumentation)). The EULA terms weren't found, so treat them as **consult only, never redistribute**.
- [BAD-AL Xbox sound analysis](https://github.com/BAD-AL/SWBF2_Xbox_mod_effort/wiki/Sound-File-Analysis)

## Mercenaries-specific
- Nothing public on `.dsk` internals beyond [Mercenaries Recompiled](https://github.com/KraftMacAndChee/Mercenaries-Recompiled) (`ports/mercenaries/src/mod_loader.cpp`, `tools/diagnostics/`).
