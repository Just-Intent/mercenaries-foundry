# `.dsk` archive format

Status: **checked against the retail US Xbox files, and edits tested in-game** (`assets.dsk`, `english.dsk`, `locked.dsk`, `streamed.dsk`; Redump MD5 `e340535feeadc0ff9b71506c625bf288`). All four rebuild byte-for-byte with our tools, and every script, world layer and text table rebuilds exactly from its decoded form. Everything here comes from the retail game's data and from the open (MIT) Mercenaries Recompiled tools, never from leaked source.

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
| `B08B665A` | `Model` | 2,954 | Model header and skeleton (`modl`); see Models |
| `5161D08F` | `segments` | 2,954 | Model geometry, compressed (`CSEG`); see Models |
| `3C6468F4` | `Texture` | 4,715 | Textures (`tex_`); see Textures |
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

Tested in-game (2026-11-08): the game loads scripts compressed by our encoder. Whether stored (uncompressed) scripts load is untested.

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

`num_components` is close to the number of entries but runs 1–7 higher, so it may be a size hint. When we add entries we raise it by the number added; an added key resolved and displayed in-game (2026-11-08).

### Models: `Model` (`modl`) + `segments` (`CSEG`)

A model is two records with the same key: `Model` (header and skeleton) and `segments` (geometry). Our decoder reads all 2,297 retail models (1.35 million triangles). Everything below was established from retail data, as noted.

`modl` children:

- `NAME`: model name. `NODE`: root node name.
- `INFO` (77 bytes): `u16` version (9), `u16` piece count, `u16` 0, `u16` flags (`0x100`); `float[6]` quantization box (min xyz, max xyz); `float[6]` bounds; `u32` joint count; `float[4]` level-of-detail switch distances (e.g. 0, 30, 300, 1000); one more `float`; one byte.
- `SKEL`: `NBNS` (`u32` joint count), `FSBI` (4 bytes, `ff ff ff ff` in samples), then one `JONT` per joint: `name\0`, rotation quaternion `float[4]` (x y z w), translation `float[3]`, `parent\0`. All 10,138 retail joints parse with unit quaternions and no leftover bytes.

`segments`: `CSEG` data is `u32` decompressed size, `u32` compressed size, then the compressed stream (see Compression). All 2,954 records decompress. The result is a `ucfb` tree of `segm` chunks (one per piece) and, in some models, `cshp` (collision; not decoded).

`segm` children:

| Chunk | Contents |
|---|---|
| `INFO` | `u32` vertex count, `u32` index count, `u32` stride word (low 16 bits = vertex size, 20 or 28; bit 16 = vertices carry a bone index, used by skinned character bodies), `u32` level-of-detail bitmask (bit *n* = drawn at level *n*) |
| `MTRL` | `u32` words (28 bytes, 36 in 18 pieces). Word 1: hash of the joint the piece hangs from (matches in all 19,755 pieces). Word 3: hash of the texture name (matches a `Texture` record in 98.5%). Word 0: probably a shader or material type. Word 2: often the same joint, or the panel group for damage states. The rest are not understood. |
| `VBUF` | Vertices, see below |
| `IBUF` | `u16` triangle strip. Strips are joined by repeating indices (`… 2, 2, 5, 5 …`). |

Vertex layout (20 bytes; 28-byte vertices add 8 bytes not yet understood):

| Bytes | Contents |
|---|---|
| 0–5 | Position, `s16` × 3: `centre + halfsize × value / 32767` per axis, using the quantization box |
| 6–7 | Bone index for skinned vertices, otherwise 0 |
| 8–11 | Normal, packed 11:11:10 signed (x = bits 0–10 / 1023, y = bits 11–21 / 1023, z = bits 22–31 / 511). All sampled normals decode to unit length. |
| 12–15 | Vertex colour, B G R A |
| 16–19 | Texture coordinates, `s16` × 2, divided by 2048 |

Placement: positions are local to the piece's joint. Applying the joint chain (rotation, then translation, up to the root) puts every piece in model space. Checked by comparing the result with the stored bounds: 2,285 of 2,297 models match exactly. The other 12 are helicopters whose stored bounds differ only around the rotor.

Vehicles carry two copies of breakable body panels, on joints named `segment_pristine_*` and `segment_damage_*`. Exports show the pristine set by default.

The `model` command exports a model to OBJ with a material file that names its textures, and can write a shaded preview image. Exported OBJ files negate Z (the game's axes are left-handed) and flip V. Each object is named `p<index>_<joint>` after the piece it came from; vertex colours are written as `v x y z r g b`.

Joint quaternions are unit length only to about 1e-3; we normalise them before use so joint transforms are pure rotations and exactly invertible.

#### Importing edited models

`model-import` matches OBJ objects to pieces by the `p<index>` prefix. A matched object replaces that piece's vertices and triangles and keeps its joint, material, level-of-detail mask and vertex format; positions are converted back into the joint's space. Bone indices (skinned pieces), the 8 extra bytes of 28-byte vertices and vertex alpha are copied from the nearest original vertex; colours come from the OBJ when present. Objects without a `p<index>` name become new pieces: joint from the name (`new_<joint>`, otherwise the root), texture from the material name, drawn at every level of detail. Exported pieces missing from the OBJ are removed. Triangles are written as one strip joined by repeated indices, each triangle at an even position so its winding is kept.

If new geometry falls outside the quantization box, the box grows and every piece is re-quantized (untouched vertices move by less than one grid step, e.g. 0.05 mm on a Humvee). Bounds only ever grow. Collision (`cshp`) is not updated. Importing an unedited export returns the original records: checked across every model, level of detail and damage state.

### Textures: `Texture` → `tex_`

All 4,715 retail textures decode. Established from retail data.

- `NAME`: texture name (present in 540 records; the rest are named by their key's hash)
- `INFO` (24 bytes): `u16` width, `u16` height, `u16` 1, `u16` mip levels, `u16` kind (1 = 2D, 2 = cube map), `u16` 1, `u32` Xbox D3D format, `u32` 0, `u32` `BODY` size
- `BODY`: mip levels in order, largest first. Cube maps (4 textures) hold 6 faces, each with its own full mip chain. Every record's size matches its format, size and mip count exactly.

| Format | Code | Count | Storage |
|---|---|---|---|
| DXT1 | `0x0C` | 3,526 | 8-byte 4×4 blocks, left to right, top to bottom |
| DXT2/3 | `0x0E` | 1,097 | 16-byte blocks: 4-bit alpha, then a DXT1 colour block. The Xbox uses one code for DXT2 and DXT3. |
| A8R8G8B8 | `0x06` | 92 | 32-bit B G R A, **swizzled**: pixels in Morton (Z-order) as the Xbox GPU stores them, interleaving x and y address bits starting with x |

Models name their textures by hash (`MTRL` word 3), so `model` exports each texture used as a PNG beside the OBJ.

Re-encoding (`extract --decode --textures`, then `pack`): an edited PNG is encoded in the original's format with a full mip chain built by 2×2 averaging. Sizes must be powers of two and may differ from the original; the mip count follows the original. Our DXT encoder picks endpoints along each block's main colour axis, then refines them by least squares. On the game's own textures it is effectively lossless: 124 of 128 sampled textures re-encode to identical pixels and the rest exceed 52 dB. Unchanged records rebuild byte-identical (all 4,715). Cube maps can't be replaced yet. Tested in-game (2026-11-08): a repainted DXT3 vehicle texture displayed correctly.

## Open questions

- The grid-index tail (`0x00FF8040`): no retail archive uses it.
- Models: the extra 8 bytes of 28-byte vertices, `MTRL` words 0, 2 and 4–6, `cshp` collision, and how skinned bone indices map to joints.
- Animation, terrain, path and region formats. Whether DXT2 textures are premultiplied (we treat them as DXT3).
- `INFO` byte 0 in scripts; `TNAM` and `INFO` in world layers.
- What `num_components` counts exactly.
