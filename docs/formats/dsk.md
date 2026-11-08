# `.dsk` archive format

Status: **directory layout confirmed against the port's loader; not yet checked against our own retail files.** Everything here comes from the retail game's data and from the open (MIT) Mercenaries Recompiled tools, never from leaked source.

The game's data lives in `DATAxbox/` as a few large `.dsk` archives: `assets.dsk`, `english.dsk`, `locked.dsk` and `streamed.dsk` (plus other languages).

## Layout

All numbers are little-endian `u32`.

| Offset | Field | Notes |
|---|---|---|
| 0 | `count` | Number of records |
| 4 | `extra` | Optional offset to grid data. **Not** a version number. 0 in merged archives. |
| 8 | directory | `count` × 12 bytes: `size`, `key`, `kind` |
| 8 + 12·count | data | Each record's bytes, back to back, in directory order. No per-record offsets or alignment. |
| after data | tail | Empty, a 2048-byte zero pad (last 4 bytes may hold a cache signature), or grid-index data starting with `0x00FF8040`. |

Rules the game's loader relies on:

- `(key, kind)` is unique within an archive.
- Record offsets are implied by the running total of sizes.
- The grid-index tail is not decoded yet. Our tool keeps it byte-for-byte.

## Names and hashes

`key` and `kind` are 32-bit FNV-1a hashes of names, folded to lowercase:

```
h = 2166136261
for each byte b: h = ((h ^ (b | 0x20)) * 16777619) mod 2^32
empty string -> 0
```

Confirmed type names (`kind`):

| Hash | Name | Contents |
|---|---|---|
| `203E6FAA` | `script` | Lua scripts (`scr_` chunk) |
| `37A3E893` | `world` | World layers, object instances and their properties (`wrld`) |
| `3884598E` | `registry` | Localized text (`reg_` → `SCPE`/`BIN_`, UTF-16) |

Other types are shown as hex until identified. Names can be recovered from `NAME` chunks inside records, string tables (`TABL`) and strings in the game executable.

## Record contents: `ucfb` chunk trees

Most records are one Pandemic chunk tree, the same family as Star Wars Battlefront's "munged" files:

```
tag   4 bytes (ASCII)
size  u32
data  size bytes, then padding to a 4-byte boundary
```

The root chunk is `ucfb`; its data is a list of child chunks.

### `script` → `scr_`

- `NAME`: script name, NUL-terminated
- `INFO`: byte 0 unknown; bytes 1–4 = decompressed size (0 = stored uncompressed)
- `BODY`: Lua 5.0 text, LZ-compressed when `INFO` size is non-zero

Compression (established by Mercenaries Recompiled from retail code at `0x21A750`–`0x21A857`; our decoder and encoder are written from that description). Bits come from 16-bit little-endian control words, low bit first. The next control word is read as soon as the 16th bit is used, before that step's payload bytes. Payload bytes follow in the order they are needed.

| Bits | Bytes | Meaning |
|---|---|---|
| `1` | `b` | Literal byte |
| `0 0 a b` | `d` | Copy `3 + 2a + b` bytes from distance `256 − d` (`d = 0` → 256) |
| `0 1` | `lo hi` | Distance `4096 − ((hi & 0xF0) << 4) − lo`, count `3 + (hi & 15)` |
| `0 1` | `lo hi e` | When `hi & 15 = 0`: `e = 0` ends the stream, else count is `e + 1` |

Copies may overlap the bytes they produce. The stream must end exactly at the declared size.

Unverified for our encoder: whether the game accepts every encoding it produces (it decodes correctly with the retail algorithm), and whether stored (uncompressed) scripts load in-game.

### `world` → `wrld`

- `NAME`: layer name (its hash equals the record key)
- `TABL`: string table, `count` then `count` × (`hash`, `length`, chars, NUL). Each hash must match its string.
- `inst` (repeated): object instance; `PROP` holds pairs of string-table hashes. Words 0–1 are model and template (0 = none); the rest are (property, value) pairs, e.g. `GeometryFile`, `ObjectType`, `Faction`, `DisplayName`.

Our editable form keeps the string table's order and appends new strings at the end. Whether the game requires a particular table order is unknown.

### `registry` → `reg_`

Nested `SCPE` (scope) and `BIN_` (value) chunks, each with `INFO` (`name\0<name>\0...`) and `DATA`. String values are UTF-16LE.

## Open questions

- The grid-index tail (`0x00FF8040`): structure and what uses it.
- Remaining type IDs: textures, models, animation, sound, ODF classes, terrain.
- `INFO` byte 0 in scripts.
- Whether scripts are Lua source or 5.0 bytecode (the port's decoder returns text).
- The rest of registry `INFO`, needed to add new strings.
