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
- `TNAM`: territory name, NUL-terminated (e.g. `Aclubs`)
- `INFO` (16 bytes): `u32` object count, `u32` sum of every object's `INFO` value, two `u32` zeros. Exact in all 824 layers; our tools recompute both when objects change.
- `TABL`: string table, `count` then `count` × (`hash`, `length`, chars, NUL). Each hash must match its string.
- `inst` (repeated): object instance, always `INFO`, `XFRM`, `PROP` (51,731 in retail):
  - `INFO`: `u16`, usually the property-pair count + 1 (87%); kept as an offset from the pair count so added properties stay consistent.
  - `XFRM`: `float[9]` rotation (rows; all orthonormal) then `float[3]` world position in metres. Plotting every south-west object from above draws the map: roads, town grids, compounds. Tested in-game (2026-11-08): a moved location and copied objects appear where placed.
  - `PROP`: pairs of string-table hashes. Word 0 is the model; word 1 is the object's own name (0 = unnamed; in template layers this is the template's name, e.g. `template_allies_humvee`); the rest are (property, value) pairs such as `GeometryFile`, `objectType`, `destructible`, `hitPoints`, `Faction`, `DisplayName`.

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

Skinned pieces (stride bit 16): each vertex's bone index refers to the model's joints whose names start with `bone`, in skeleton order (e.g. `DummyRoot` is skipped). Inferred from retail data: it fits all 97 skinned models, beats "joint list" and "joint list + 1" where they differ, and posing the exported skeleton in a glTF viewer moves the expected body parts (head, arm with its hand, leg). One bone per vertex.

glTF import (`gltf_model`) evaluates the file's node transforms (and skins, for skinned meshes) to put every vertex back in model space, so it doesn't depend on how a tool re-orients bones or re-nests nodes. All 2,297 models (2,330 exports, counting damaged views) read back to their original positions and bones and re-import byte-identical. Files restructured the way Blender does (bones re-oriented with compensated children and skins, matrices, reversed vertex order, extra root node) also re-import unchanged. Tested in-game (2026-11-08): a top hat added in glTF under `bone_head` on the three `prokat_hum_*` mercenaries followed the head.

Skeleton edits. A joint node that moved in the file gets its new position. It keeps its rotation, because tools may re-orient bones on export. Its new translation is stored relative to its parent's new position, so everything hanging from it follows: rigid pieces and collision stay byte-identical in joint space, and child joints keep their offsets. An empty node with a new name under a game joint becomes a new joint, with its place and rotation from the file. It goes at the end of `SKEL` (retail skeletons don't keep parents before children either), and `NBNS` and the `INFO` joint count (offset 56; equal to the joint list in all 2,954 skeletons) are updated. Templates name joints directly (`RiderSeatpoint_A = hp_seat_lf`, turret and dock points), so a new joint can become a new seat or mount. Checks: all 2,954 unchanged skeletons rebuild byte-identical, and 192 sampled models exported and re-read show no joint changes. Moving skin bones (`bone*`) re-reads the skinned pieces, and animations that set a bone's translation override the move. Tested in-game (2026-11-08): the Humvee's `turret_a` raised 0.5 m carried the turret, barrel and gunner seat up, and a new joint `hp_seat_foundry` 0.8 m above `hp_seat_lf`, set as `RiderSeatpoint_A`, seated the driver there.

Axes: the game's world is right-handed (like glTF and OBJ): on a Kaesong plaza, ground markings on the +z side of the monument show on the left looking toward -x. The game draws each model turned half round from its decoded data (front +z), so exports negate X and Z (quaternion x and z) and keep triangle winding: a turn, not a mirror. Checks: a Humvee's left-side joints (`hp_seat_lf`, `door_lf`) come out on its left, and a road sign (`global_locationsign_sw_09`) reads 평양 시내 the right way round in the editor, which draws models from these exports. Exports before 0.67 negated only Z, which mirrored models; their glTF files say so (no `"axes": "turned"` in `asset.extras.mercfoundry`) and imports read them the old way. A file re-saved by another tool loses that record and is read the new way.

Vehicles carry two copies of breakable body panels, on joints named `segment_pristine_*` and `segment_damage_*`. Exports show the pristine set by default.

The `model` command exports a model to OBJ with a material file that names its textures, and can write a shaded preview image. Exported OBJ files are turned half round like the glTF export (X and Z negated) and flip V; OBJ files from before 0.67 (Z negated only) import mirrored. Each object is named `p<index>_<joint>` after the piece it came from; vertex colours are written as `v x y z r g b`.

Joint quaternions are unit length only to about 1e-3; we normalise them before use so joint transforms are pure rotations and exactly invertible.

#### Importing edited models

`model-import` matches OBJ objects to pieces by the `p<index>` prefix. A matched object replaces that piece's vertices and triangles and keeps its joint, material, level-of-detail mask and vertex format; positions are converted back into the joint's space. Bone indices (skinned pieces), the 8 extra bytes of 28-byte vertices and vertex alpha are copied from the nearest original vertex; colours come from the OBJ when present. Objects without a `p<index>` name become new pieces: joint from the name (`new_<joint>`, otherwise the root), texture from the material name, drawn at every level of detail. Exported pieces missing from the OBJ are removed. Triangles are written as one strip joined by repeated indices, each triangle at an even position so its winding is kept.

If new geometry falls outside the quantization box, the box grows and every piece is re-quantized (untouched vertices move by less than one grid step, e.g. 0.05 mm on a Humvee). Bounds only ever grow. Collision for new pieces is generated as boxes (see Collision); collision of edited existing pieces is not changed. Importing an unedited export returns the original records: checked across every model, level of detail and damage state. Tested in-game (2026-11-08): a stretched gun barrel and an added crate showed on the player's Humvee.

#### Collision: `cshp`

1,997 models carry one `cshp` chunk in their segments tree (after the `segm` pieces). All of them decode. Established from retail data.

```
cshp
  cinf   u16 shapes, u16 box shapes of kind 2 or 3, u16 shapes with a CMOP tree,
         u16 total boxes, u16 kind-4 meshes, u16 kind-5 hulls, u16 0, then
         u16 mesh vertices + u16 mesh triangles (summed over CMSH) if any mesh,
         then u32 tree memory if any tree: sum of align16(stream size + 0x30)
  shap   (one per shape)
    sinf u32 material, u32 joint name hash, u32 kind | count << 16, u32 1 = mesh / 0 = boxes
    CBOX count x 64 bytes: u32 material, float[3] half size, float[9] rotation (rows are the
         box axes), float[3] centre, in the joint's space
    CMSH u32 flags, u16 vertices, u16 triangles, u16 planes, float[3] per vertex, then
         u16[3] per triangle (triangle meshes, 149) or float[4] per plane (convex hulls,
         176: normal and offset, n.p + d <= 0 inside)
    CMOP bounding-volume tree over the shape's boxes or triangles (below)
```

| Check | Result |
|---|---|
| `cinf` shape count | matches in 1,997 of 1,997 |
| `cinf` kind-2/3 and tree counts | match in all |
| Box count in `sinf` | matches `CBOX` length in all 2,071 box shapes |
| Box material = `sinf` material | 28,294 of 28,294 boxes |
| Box rotations orthonormal | 28,294 of 28,294 |
| `sinf` joint is one of the model's joints | 2,392 of 2,392 shapes |

Kinds: boxes 0 and 2 (plain), 3 (with `CMOP`); triangle meshes 4 (with `CMOP`); convex hulls 5. Drivable vehicles use kind 2 for boxes (with a kind-4 hull mesh, or alone on cargo trucks); kind 0 appears only on static objects such as train cars and props. In-game, kind-0 boxes added to a vehicle had no effect; kind-2 boxes collided. Material codes (3, 6, 7, 9, 11, 15, ...) are not yet mapped to surfaces. Vehicles typically use a simplified hull mesh; buildings use rotated boxes.

`model --collision` writes a preview of a model's collision. Shape limits: no vehicle in the game has more than two collision shapes, and giving the Humvee a third (even a 5 cm box) made it sink into the ground after impacts (tested in-game). On vehicles the hull mesh uses material 6 and the second shape 9.

On import, each new piece gets a box fitted around it, re-expressed in the target shape's joint space with a rotation. The boxes go into the model's box shape when it has one (its `CMOP` tree rebuilt if it has one); otherwise a convex hull is replaced by a box shape approximating it (see Collision trees) and the boxes join that; otherwise a new plain box shape (kind 2, material 9 on vehicles) is added only if the model has fewer than two shapes; a triangle mesh (boxes as triangles, tree rebuilt) is the last resort, with a warning. `cinf` counts and totals are kept in step. Tested in-game (2026-11-08): a box appended to the cargo truck's existing shape collided, and the truck drove normally. Objects whose name contains `nocoll` get no collision; `model-import --no-collision` turns it off entirely. Collision of edited existing pieces is not changed.

### Paths, boundaries, regions, encounters, lists

Every layer has one record of each kind (most are empty). All 3,329 decode and rebuild byte-identical. Established from retail data; count fields verified in every record.

| Kind | Chunks | Contents |
|---|---|---|
| `path` (3,367 paths) | `path`: `NAME`, `INFO`, `PROP`, `PNTS`, `ORNT`, `JNCT`; sometimes `EVNT`, `ROTN`, `PSTM`, `RBRG` | Routes for traffic, patrols and flights (e.g. `t_unhq_patrol04`). `INFO` = (`u16` points, `u16` ?, `u16` properties). `PROP` = repeated `name\0` + 4-byte value (e.g. `width` 1.0, `flow` 1.0, `num_pause_times` 0). `ORNT` = one quaternion per point. `JNCT` = `u16` link count, then per link the other path's name (NUL-terminated), `u16` point on that path, `u16` point on this path. 2,626 of 3,158 retail links join this path's last point to the other's first, and 86% pick the other path's point nearest to ours (typically about 8 m away), so paths chain into networks. `PSTM` = (`u32` point, `f32` seconds) pauses, counted by `num_pause_times`. `RBRG` = (`u32` point, `f32`), counted by `num_respect_bridge_flags`. `ROTN` = per entry a point number as text (NUL-terminated) plus a quaternion. `EVNT` (animations to play at points, counted by `num_nodes_with_events`) stays raw. Retail chunk order: `NAME INFO PROP [PSTM\|EVNT [ROTN]\|RBRG\|ROTN] PNTS ORNT JNCT`. Scripts send actors along paths with `Ai_Command(actor, 'Patrol', path, '1.0', 'loPri')` or `'PathMove'`. |
| `lineregion` (518) | `lrg_`: `NAME`, `INFO`, `PROP`, `PNTS` | Named boundary polygons: `type=killbox` mission areas (`TestInside=false`: acts when you're outside), trigger zones used by scripts (`allies0_nodrive`), weather areas (`type=atmosphere`). `INFO` = (`u16` 0, `u16` properties, `u16` points). `PROP` = `name\0value\0` pairs. |
| `region` (42) | `rgns`: `INFO` (`u32` regions, `u32` sum of region `INFO`), `regn`: `INFO` (`u16` properties), `XFRM`, `SIZE` (`float[3]`), `PROP` | Boxes with properties. |
| `encounter` (646) | `enc_`: `NAME`, `INFO` (`u16`, `u16`, `u16` point sets, `u16` squads), `PSTS` > `PSET` (`NAME` like `A`, `INFO` with point count at byte 4, `PNTS`), `SQDS` > `SQD_` (`NAME` = squad name such as `Squad1`, `INFO` = `u16` point-set names, `u16` 0 (1 in 3 squads), `u32` squad size, then `FPNT` (`PPNT` in 3) = the point-set names, NUL-terminated) | Squads and the point sets they spawn at or move to. Objects join a squad through their own `encounter` and `squad` properties (2,580 objects); scripts command squads by encounter and squad name (`Squad_Command('elite_ambush01', 'elite_left', 'TacticalMove', 'B', 'medPri')`). The squad size usually equals the members placed in the world; some squads get members at runtime. There is no separate squad-type table. See [ENCOUNTERS.md](../ENCOUNTERS.md). |
| `list` | plain text | The layer's asset load list (`[animation]`, `load ...`). |

Paths, boundaries and spawn points plotted over the objects follow roads, town grids and compounds. Tested in-game (2026-11-08): shrinking `allies0_killbox` made the game's restricted-area response (air strikes) trigger at the new edge. Our readable form recomputes the count fields that matched their contents in the original, so adding points or properties keeps them right; a path's points and rotations must stay equal in number.

### Animation tables: `animtable` → `atbl`

95 tables, one per character or vehicle kind (`player_american`, `nk_soldier_ehuman`, `allies_veh_humvee`, ...). The game's code asks for handles (the executable holds strings like `lb_upright_run_forward`, but no clip names); a table maps each handle to a clip. All 95 rebuild byte-for-byte from the JSON form (`animtable` command, `extract --decode`). Established from retail data.

```
ucfb
  atbl
    NAME   table name
    DFLT   optional fallback table for handles this one lacks (24 tables)
    SIZE   u32 variant count, u32 set count
    vanm   per variant: ANAM clip name
    aset   per set (default, rifle, smg, law, gps, driver, gunner, passenger_*, ...)
      NAME, SIZE (u32 entry count)
      entr   HNDL handle; ANAM clip, or VARG u32 count + u32 start (a random pick
             from the variant list, e.g. three ragdoll or fidget clips); PRIO u32; FLGS u32
```

Clips are streamed only when a load list (`list`, text: `[animation]` then `load <clip>` lines) names them; three lists load the American run. A mod's new clips (`animations/new.json`) are added to `streamed.dsk` and to every load list that loads their template. Tested in-game (2026-11-08): new clips `foundry_<merc>_crossleg_run` (the run with thighs turned inward), pointed to by each player table's `default`/`lb_upright_run_forward` entry, played with the game's own run clip unchanged.

### Animation: `animation` → `anim`

All 1,835 retail animations decode (43,153 joint tracks, 234,640 frames). Established from retail data.

- `NAME`; `INFO` (18 bytes): `u16` frames, `u16` joints, `float` translation scale (units per metre; e.g. 68,264.6), `u16` (often 0; maybe events), `u32` `CJNT` size, `u32` unpacked size
- `CJNT`: joint data, compressed with the script scheme unless both sizes are equal (622 are stored raw)
- `jont` per joint: `NAME`, then `SXLT` (`int16[3]` fixed translation ÷ the `INFO` scale = metres) or `DXLT` (`float` scale for per-frame translation), and `QROT` (`u16[4]`: byte length of each rotation channel; right for 99.6% of channels, see below)
- `XLTC`, `EVNT`: not decoded

`CJNT`, joint by joint: per-frame translation (`frames × int16[3]`, × the joint's `DXLT` = metres) when it has `DXLT`, then four rotation channels: quaternion x, y, z, w on a ±2047 scale. A channel is an `int16` start value then, per frame, a signed byte change, `0x80 n` = hold for n frames, or `0x81` + `int16` = new value. About 1.6% of joints (707) are preceded by one stray byte; the decoder finds it because the four start values must form a unit quaternion.

Checks: every joint's rotations stay unit length; `SXLT` ÷ scale matches the skeleton's rest offsets (e.g. forearm 0.288 m); first-frame rotations of an idle match the rest pose except posed limbs. Rotations are full local rotations. The top joint (`dummyroot`) carries root motion: the run cycle moves 4.8 m over 16 frames. Frame rate assumed 30 fps: that gives a 3.7 steps/s sprint cadence. Names: `fb_` full body, `lb_` lower body.

Bone names match the model skeletons (`bone_*`, `hp_*`), so `model --glb --anim <pattern>` adds any matching animations as glTF clips (`--in-place` drops root motion). three.js plays them correctly (run, walk, idle).

Re-encoding: holds are used for runs of 3 or more unchanged frames (split into pieces of up to 255), shorter runs stay as zero changes, changes from −126 to 127 are single bytes, and anything else is an `0x81` absolute value. With that rule all 172,612 retail rotation channels re-encode byte-identical; the only data difference is that stray bytes are dropped. Stored quaternions aren't quite unit length, so the encoder doesn't normalise them, and glTF import keeps the stored values for frames left as exported. A glTF export read back and re-encoded gives the same integers for every frame. Tested in-game (2026-11-08): thighs turned 40° outward in the three `lb_upright_run_forward` clips gave a bow-legged run.

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

### Collision trees: `CMOP`

Re-derived from the retail executable's tree walker (`sub_001B0990`; two siblings share its opcode table). Header: float[3] minimum corner (the geometry's minimum less 5 cm), float scale (`127 * 2^17 / extent`, units per metre), u32 stream size. The stream is bytecode, big-endian operands. The walker carries a query sphere (centre and radius) in 8-bit cells (full resolution >> 16) and reads until a leaf or a failed bounds test ends the stream:

| Opcode | Meaning |
|---|---|
| `00` | end |
| `01`-`04` x y z | zoom in by 2^n around a new origin |
| `05`/`06`/`07` n | jump forward by a 1/2/3-byte distance |
| `09`/`0a` n, `0b` n | add to / set the primitive index base |
| `10`-`12` a b n | split on x/y/z: left child (n bytes) has max a, the right (the rest) min b |
| `13`-`1c` a b n | the same on 10 diagonal planes (values x2 or x3; radius x1.5 or x4) |
| `20`-`22` v n | split at one value |
| `23`-`25` a b nn mm | split with 16-bit child offsets |
| `26`-`28` lo hi | stop unless the query overlaps [lo, hi] on x/y/z |
| `29`-`2b` | the same at full resolution (3-byte bounds) |
| `30`-`4f`, `50`-`53` i | leaf: primitive base + index |
| `60`/`64`/`68` | set a value the walker carries |

A split recurses into its left child, then continues with the right. Checks: a Python copy of the walker (`cmop.query`) parses all 1,402 retail trees, and random sphere queries never miss a primitive they touch; the only primitive any tree leaves out is one degenerate (collinear) triangle. `cmop.build` writes trees with bounds tests, axis splits, 16-bit jumps and leaves; on the same retail geometry they never miss either, and are about a third larger.

How vehicles use the shapes: each has a material-6 triangle hull mesh plus a material-9 shape that is a convex hull (Humvee, cars, trucks) or boxes (tanks, helicopters). In-game, boxes added as triangles to the Humvee's hull mesh did not collide with the world; boxes in a box shape do. So on import a convex hull is replaced by a box shape of slices approximating it (for the Humvee: 10 boxes, 98.9% of its volume, at most 11.5 cm proud), and new boxes join it. Tested in-game (2026-11-08): a plate on the Humvee's front stopped it at walls, a `nocoll` plate on the back passed through, and the Humvee drove and crashed without sinking.

The `sinf` "material" word is not a surface material: it is the shape's collision flags. Every box in a shape repeats the value (28,294 of 28,294). The shape loader (`0x12EBA0`, called by `0x12F560`) keeps it as word 0 of a 12-byte shape entry (flags, joint, geometry). `0x12BB40` picks a shape by flags: an exact match first, else one sharing a wanted bit and none of an excluded mask, optionally on a given joint.

| Bit | Meaning | Evidence |
|---|---|---|
| 1 | Solid to people and vehicles | Static objects (`0x12FCC0`) put a shape with bit 1 in collision layer 1, or layer 2 with bit 8 too, and a shape without bit 1 in layer 7. In game, box shapes with 1, 3, 5 and 7 stopped walking and the Humvee; 2, 4 and 6 didn't. |
| 2 | Bullets hit it | In game, bullets sparked on box shapes with 2, 3, 6 and 7 but passed through 1, 4 and 5. |
| 4 | Not seen to change anything in game | Set with 2 on vehicle hull meshes (6). |
| 8 | Main physics body of a moving object | Vehicles and physics props (`0x1319F0`) take the bit-8 shape as the body that collides with the world. Without one they fall back to a bit-1 shape and treat it as 9. The Humvee's 9 shape stopped it at walls; its 6 shape didn't. |
| 2, 4, 32 | Second body of a moving object | `0x1319F0` builds it from a shape with any of these bits and neither 1 nor 8, e.g. the Humvee's hull (6). |
| 16 | No static body | `0x12FCC0` skips these shapes. |
| 64 | Separate special body | `0x12FCC0` skips these shapes; `0x25C40` builds one bit-64 shape as its own body (`0x12F0B0`) with flags 0x40. |

A body's flags (shape flags plus engine bits, at +0xB0) are what world queries (`0x12D160` via `0x12E440`/`0x12E690`/`0x12E8C0`) test against their mask. Explosions query with mask 2. Retail values: props mostly 7, ruins 15, vehicles 6 plus 9, gates 2 plus 33, fences 41 plus 2. Mods set a model's flags with `models/<name>.collision.json` (`{"shapes": {"0": 7}}`; shape numbers as in `model --collision`). The test used box shapes; mesh shapes (rocks) gave mixed bullet results, and pines (flags 7) let bullets through, so trees or meshes may be handled differently.

What an object counts as when hit is its `obstaclematerialtype` property. `0x12B5F0` maps the name hash to a number: `none` (or anything else) 0, `wood` 1, `solidmetal` 2, `sheetmetal` 3, `vehicle` 4, `dirt` 5, `concrete` 6, `rock` 7, `plant` 8, `snow` 9, `glass` 10, `rubber` 11, `chain` 12. The retail data uses wood, concrete, none, chain, sheetmetal, solidmetal and vehicle; `plant` and `rubber` were recovered from their hashes. Objects store it (`0x3962B`; 6 when absent) and copy it to their physics body at +0xB4 (`0x392E0`, from the body setup `0x393F0` that also reads `physicsFriction` and `physicsRestitution`). A body without the property gets 3 (`0x132542`). The impact code (`0xB5460`, table `0xB58F0`) turns it into an effect: 2, 3 and 12 metal; 5 dirt; 6 road; 9 snow; everything else (wood, rock, plant, glass, rubber, vehicle, none) rock. `0x131230` also special-cases 2, 3 and 6 when bodies touch (not yet traced). Mods set it with an object rule (`"set": {"obstaclematerialtype": "solidmetal"}`); the build warns about names the game doesn't know. Tested in-game (2026-11-08): a concrete gate and guard post set to `solidmetal` sparked like metal when shot. Bullets passed through pine trees while they were set to `solidmetal` (they have one box shape, flags 7); whether retail `wood` pines stop bullets wasn't checked.

### Terrain: `Terrain` → `tern`

Re-derived from the retail loader (`sub_001F07A0`, RedTerrain.cpp) and its height lookups (`sub_001F03C0`, `sub_00146A10`). 14 maps in `assets.dsk`; the opening level `LV_SWN` loads `swn` (levels.ini), not `sw`.

| Chunk | Contents |
|---|---|
| `INFO` | f32 spacing (8 m), height step (0.35 m), min, max; i16 size (512), patch (8), block (16), layer count, decal-texture count, decal count; optionally 3 more f32 |
| `HEXP` | per 16×16 block: i16 shift, base, min, max (height units) |
| `HGT8` | PackBits RLE (control < 0x80: c+1 literals; else repeat next byte c−0x7F times) into a size² byte buffer, block-major |
| `PTCH` | per 8×8 patch: u16 layer mask, u16 decal count |
| `COLR` | per patch an LZ blob of 81 X1R5G5B5 vertex colours (baked lighting) |
| `ALPH` | 4-bit blend weights for patches with two or more layers |
| `DCAL` | decals, 24 bytes each |
| `LTEX`, `DTEX` | layer and decal texture names; `SCAL`, `AXIS` per-layer scale and projection axis |

Height of sample (x, z) = ((byte << shift) + base) × step. The grid has no extra edge row: lookups wrap with size−1. In the game a world position maps to cell col = (x + 2048) / 8, row = (z + 2040) / 8. Terrain far from the player is streamed and not in memory.

`terrain DATAxbox swn` exports the heights as a 16-bit PNG (16 levels per step, so every height round-trips) plus a JSON of the numbers to read it back. Mods replace heights via `terrain/<name>.heights.png`: changed 16×16 blocks get a new shift and base, unchanged ones keep their bytes, and an unchanged map rebuilds byte-identical (all 14). The layer, lighting, blend and decal chunks decode and rebuild exactly too (`terrain_layers`), but mods can't edit them yet. Tested in-game (2026-11-08): a 30 m hill added next to the start of the first level appeared, and the game's own height buffer read 28.7 m at its peak. Baked lighting (`COLR`) follows one sun per map: each channel is about ambient + diffuse × max(0, normal · sun), and ground that a rise hides from the sun is darker. On `swn` the sun is about 48° up from −x/−z, with brightness 36 + 53 × lambert, and shadowed ground loses about a third of the sun term. Swapping the vertex axes destroys the fit, which confirms the patch layout (9×9 vertices per patch, rows along z, edges shared with the neighbouring patch). After a height edit the mod build fits the map's sun from its own heights and colours, then scales each vertex whose shading changed (slope or shadow) by new shade ÷ old shade, per channel and rounded to the nearest 5-bit level. Painted tint survives, and patches it doesn't touch keep their bytes. On `sw` the colours don't follow a sun the slopes explain (the fitted sun term is negative), so its lighting is left alone. `"relight": false` in the `.heights.json` turns relighting off. Tested in-game (2026-11-08): the relit hill showed a lighter sun side and a darker far side, and the player could walk and drive on it.

Ground painting. The ground is up to 16 texture layers (`LTEX`, with `SCAL` scale and `AXIS` projection), drawn bottom to top. A patch's `PTCH` mask lists the layers it uses. With two or more, `ALPH` holds a 9×9 nibble grid per layer: bits 0–2 are opacity, bit 3 is "draw". The patch builder (`0x21CB20`) handles up to four layers per pass and emits cell (j, i) if any of them has the draw bit at the cell's top-left vertex. The ninth row and column of draw bits aren't read. `terrain` exports each layer as an 8-bit greyscale PNG, (patches × 8 + 1) pixels square, in the same orientation as the heights, plus `<name>.layers.json` with each layer's texture, scale and projection. A mod repaints with `terrain/<name>.layerNN*.png`. Only patches whose painting changed are re-encoded (the others keep their bytes; all 14 maps rebuild exactly). In a re-encoded patch, the layer set is the layers with any opacity, and a layer's cell is drawn if any corner has opacity and no single higher layer is opaque at all four corners. On the retail maps that rule never misses a cell the game's data draws (retail just draws some extra cells), and re-encoding every `swn` patch reproduces 99.3% of opacity values (the rest are patch-edge vertices where neighbouring patches disagree). `layers.json` can change a layer's texture, scale or projection, or add layers up to 16. A patch left with one layer is drawn opaque. Tested in-game (2026-11-08): a concrete disc under the first level's start and a scorch mark on a hill both showed, with soft edges painted in.

Decals (roads, concrete, parking) each cover one cell. `DCAL` stores them in patch order (`PTCH` gives each patch's count), 24 bytes each: the cell offset inside the patch as signed bytes (about 1% of retail decals lie just outside their patch), the `DTEX` texture index, five constant bytes (`00 ff ff ff ff`), and four int16 (u, v) pairs, where 2048 = one texture repeat. The corners are stored in the order (x, z+1), (x+1, z+1), (x, z), (x+1, z). That is the only order under which neighbouring retail decals share their touching corners: about half of all neighbour pairs on `swn` match exactly, and the rest are where separate roads meet. `terrain` exports `<name>.decals.json`, giving each decal's map cell, texture and corner coordinates; all 14 maps rebuild exactly from it. Textures are named, or given by slot where a map lists a name twice. A mod's `terrain/<name>.decals.json` replaces the list, or lays `roads` from world points (x, z in metres) while keeping the map's own decals. One texture width covers `span` metres across (25 by default, like retail roads: about 11 m of asphalt), the texture runs along the road at the same scale, and corners past the texture's edge are clamped so its repeat doesn't show. Tested in-game (2026-11-08): a road laid through the first level's start showed continuous asphalt with its centre line, around a bend and over the hill.

Surface types aren't in the archive. Each level's `ConfigFile` (from `levels.ini`; `\SW\SWN.ini` for the opening level, whose terrain is `swn`) sets per-texture values by slot: 0–15 are ground layers, 16–31 decal textures (16 + `DTEX` slot). `terrainPhysicsType <slot> <surface>` is read by the terrain loader (`0x1F0688`) into `g_TerrainSurfaces` (`0x6430C0`, int[32]): `dirt` 1, `road` 2, `snow` 3, `grass` 4, `rock` 5, anything else 0. `Terrain_GetSurfaceAt` (`0x1F28E0`) returns the surface at a world point (the cell's dominant texture via `0x1F0010`); the impact-effect code (`0xB4D80`, `0xB5460`) uses it when a shot or blast hits bare ground. `TerrainDust`, `Dirt` and `layereffect` lines store an effect name's hash per slot (`0xAD250`). The config reader indexes lines by key hash and reads the nth line with a key. `terrain` exports `<name>.surfaces.json` (every slot's texture, surface, dust, debris and effect); a mod's copy rewrites only the changed lines of the config, which the build writes as a loose `DATAxbox/...ini` that the port's mod loader overlays. Tested in-game (2026-11-08): with dirt and concrete set to `snow`, the game's surface table read snow for both slots (logged by Foundry Console). Not yet traced: what else reads per-surface data (a per-surface record at +0xD0 in an object reached only through a pointer built at runtime, `0x145ED0`), so whether surfaces change vehicle handling is unknown.

## Open questions

- The grid-index tail (`0x00FF8040`): no retail archive uses it.
- Models: the extra 8 bytes of 28-byte vertices, and `MTRL` words 0, 2 and 4–6.
- Collision: what flag bit 4 does, and why bullets pass through pines and some rock meshes (see above); what the walker's carried value (`60`-`68`) is for; 2 of 176 convex hulls have a point up to 13 cm outside one plane.
- Terrain: the meaning of `INFO`'s 3 trailing floats and of `ROTN`. What animation-table `PRIO` and `FLGS` values mean. Whether DXT2 textures are premultiplied (we treat them as DXT3).
- `INFO` byte 0 in scripts; `TNAM` and `INFO` in world layers.
- What `num_components` counts exactly.
