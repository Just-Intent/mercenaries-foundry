# `.dsk` archive format

Status: **checked against the retail US Xbox files** (`assets.dsk`, `english.dsk`, `locked.dsk`, `streamed.dsk`; Redump MD5 `e340535feeadc0ff9b71506c625bf288`). All four rebuild byte-for-byte with our tools, and every script, world layer and text table rebuilds exactly from its decoded form. Everything here comes from the retail game's data and from the open (MIT) Mercenaries Recompiled tools, never from leaked source.

The game's data lives in `DATAxbox/`:

| Archive | Records | Contents |
|---|---|---|
| `assets.dsk` | 5,380 | Scripts, world layers, paths, regions, encounters, terrain, fonts, sounds |
| `english.dsk` | 91 | Localized text |
| `locked.dsk` | 1,314 | Models and their geometry (always loaded) |
| `streamed.dsk` | 11,144 | Textures, models, geometry, animation (streamed in) |

## Layout

All numbers are little-endian `u32`.

| Offset | Field | Notes |
|---|---|---|
| 0 | `count` | Number of records |
| 4 | `extra` | Optional offset to grid data. **Not** a version number. 0 in all four retail archives. |
| 8 | directory | `count` × 12 bytes: `size`, `key`, `kind` |
| 8 + 12·count | data | Each record's bytes, back to back, in directory order. No per-record offsets or alignment. |
| after data | tail | Empty, a 2048-byte zero pad (last 4 bytes may hold a cache signature), or grid-index data starting with `0x00FF8040`. All four retail archives end in the 2048-byte pad. |

Rules the game's loader relies on:

- `(key, kind)` is unique within an archive.
- Record offsets are implied by the running total of sizes.

## Names and hashes

`key` and `kind` are 32-bit FNV-1a hashes of names, folded to lowercase:

```
h = 2166136261
for each byte b: h = ((h ^ (b | 0x20)) * 16777619) mod 2^32
empty string -> 0
```

`mercfoundry names` recovers names by hashing every string in the executable and data files and keeping matches. On retail data it names 9,618 of 9,723 distinct hashes.

Type names (`kind`). The first three were known from the port; the rest were found by hash match against strings in the retail executable and data, except `segments` (a guess whose hash matched, backed by its contents). The spelling is the game's where it has one; the hash ignores case.

| Hash | Name | Records | Contents |
|---|---|---|---|
| `203E6FAA` | `script` | 208 | Lua scripts (`scr_`) |
| `37A3E893` | `world` | 824 | World layers: object instances and properties (`wrld`) |
| `3884598E` | `registry` | 91 | Localized text (`reg_`) |
| `B08B665A` | `Model` | 2,954 | Model header and skeleton (`modl`: `NAME`, `NODE`, `INFO`, `SKEL` → `JONT`...) |
| `5161D08F` | `segments` | 2,954 | Model geometry, compressed (`CSEG`); one per model, same key |
| `3C6468F4` | `Texture` | 4,715 | Textures |
| `E145EE5D` | `animation` | 1,835 | Animation |
| `F64FFC34` | `animtable` | 95 | |
| `74DBC80E` | `Terrain` | 14 | |
| `84874D36` | `path` | 857 | |
| `0CFB5881` | `list` | 897 | |
| `591AEB7A` | `encounter` | 824 | One per world layer |
| `031AFE7D` | `region` | 824 | One per world layer |
| `F58388CF` | `lineregion` | 824 | One per world layer |
| `FA83847D` | `soundEffect` | 4 | |
| `FF083465` | `effects` | 1 | |
| `45BE0EFF` | `effectstable` | 1 | |
| `274E1290` | `font` | 3 | |
| `E64015F0` | `icon` | 1 | |
| `81E2581C` | `sprite` | 1 | |
| `A7FD5617` | `TGA` | 1 | |
| `A535A9EF` | `TXT` | 1 | |

## Record contents: `ucfb` chunk trees

Most records are one Pandemic chunk tree, the same family as Star Wars Battlefront's "munged" files:

```
tag   4 bytes (ASCII)
size  u32
data  size bytes, then zero padding to a 4-byte boundary
```

The root chunk is `ucfb`; its data is a list of child chunks. Each child is padded inside its parent; when the last child ends unaligned, its padding may be left out of the parent's size. Our tools detect and keep both forms.

### `script` → `scr_`

- `NAME`: script name, NUL-terminated
- `INFO`: byte 0 unknown; bytes 1–4 = decompressed size (0 = stored uncompressed)
- `BODY`: Lua source text (not bytecode), LZ-compressed when the `INFO` size is non-zero. All 208 retail scripts are compressed.

### Compression

Used by scripts and model geometry. Established by Mercenaries Recompiled from retail code at `0x21A750`–`0x21A857`; our decoder and encoder are written from that description. Bits come from 16-bit little-endian control words, low bit first. The next control word is read as soon as the 16th bit is used, before that step's payload bytes. Payload bytes follow in the order they are needed.

| Bits | Bytes | Meaning |
|---|---|---|
| `1` | `b` | Literal byte |
| `0 0 a b` | `d` | Copy `3 + 2a + b` bytes from distance `256 − d` (`d = 0` → 256) |
| `0 1` | `lo hi` | Distance `4096 − ((hi & 0xF0) << 4) − lo`, count `3 + (hi & 15)` |
| `0 1` | `lo hi e` | When `hi & 15 = 0`: `e = 0` ends the stream, else count is `e + 1` |

Copies may overlap the bytes they produce. The stream must end exactly at the declared size.

Not yet tested in-game: whether the game accepts scripts compressed by our encoder (they decode correctly with the retail algorithm), and whether stored (uncompressed) scripts load.

### `world` → `wrld`

- `NAME`: layer name (its hash equals the record key)
- `TNAM`, `INFO`: present in retail layers, not decoded yet (kept as-is)
- `TABL`: string table, `count` then `count` × (`hash`, `length`, chars, NUL). Each hash must match its string.
- `inst` (repeated): object instance. `PROP` holds pairs of string-table hashes: words 0–1 are model and template (0 = none), the rest are (property, value) pairs such as `GeometryFile`, `objectType`, `destructible`, `hitPoints`, `Faction`, `DisplayName`. Retail instances also carry an undecoded `INFO` chunk.

Our editable form keeps the string table's order and appends new strings at the end. Whether the game requires a particular table order is unknown.

### `registry` → `reg_`

```
reg_
  INFO   "name\0english\0num_components\0<n>\0"
  SCPE / BIN_ ...
SCPE     INFO "name\0<name>\0",  DATA = more SCPE / BIN_
BIN_     INFO "name\0<name>\0",  DATA = UTF-16LE text, not NUL-terminated
```

All 6,905 retail values are text. Keys are dotted scope paths, e.g. `veh.allies_apache` = "YAH-56 Gunship". The top-level groups include `vo` (3,070 subtitle lines), `interface`, `mail`, `tutorial`, `challenge` and `veh`.

`num_components` is close to the number of entries but runs 1–7 higher, so it may be a size hint. When we add entries we raise it by the number added; whether the game needs it exact is untested.

### `segments` → `CSEG`

`CSEG` data: `u32` decompressed size, `u32` compressed size, then the compressed stream (see Compression). All 2,954 retail records decompress. The result is a `ucfb` tree of `segm` chunks, one per mesh piece:

- `INFO` (16 bytes): four `u32`s. In samples, word 0 = vertex count, word 1 = index count, word 2 = vertex stride (20), word 3 unknown.
- `MTRL`: material (28 bytes; starts with hashes)
- `VBUF`: vertices (`count × stride` bytes)
- `IBUF`: 16-bit indices

Inferred from a few samples; not yet checked across all records.

## Open questions

- The grid-index tail (`0x00FF8040`): no retail archive uses it.
- Vertex layout in `VBUF`, `MTRL` fields, and the `modl` `INFO` block.
- Texture, animation, terrain, path and region formats.
- `INFO` byte 0 in scripts; `TNAM` and `INFO` in world layers.
- What `num_components` counts exactly.
