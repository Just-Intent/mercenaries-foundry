"""Terrain decals (roads, concrete, parking): export, replace, and lay roads.

A decal covers one cell. DCAL stores it in patch order (PTCH holds each
patch's count) as a cell offset inside the patch (signed bytes; about 1% of
retail decals lie just outside their patch), a DTEX texture index, and texture
coordinates for the cell's four corners in the order (x, z+1), (x+1, z+1),
(x, z), (x+1, z), in 1/2048ths of the texture.  (That order is the one under
which neighbouring retail decals share their touching corners: about half of
all neighbour pairs on swn match exactly, no other order comes close; the
rest are where separate roads meet.)

The mod format, <name>.decals.json:
  {"textures": [up to 16 DTEX names],
   "decals": [{"cell": [x, z], "texture": "sw_decal_road_straight",
               "uv": [[u, v] x 4]}, ...],
   "roads": [{"texture": ..., "points": [[x, z], ...], "span": 25}]}
"cell" is the map cell (the heights PNG's pixel).  Exported decals that lie
outside the patch that stores them carry "patch": [px, pz], so the map
rebuilds exactly.  "roads" are laid by the build: "points" are world x, z in
metres (the Foundry Console's Position), and one texture width covers "span"
metres across the road (25 m, like the game's own roads: about 11 m of
asphalt), with the texture running along it at the same scale.
"""

import math
import struct

from . import terrain_layers as tl

MAX_TEXTURES = 16
MAX_PER_PATCH = 64      # retail's most, one per cell of an 8 x 8 patch; past it the game crashes or hangs
EXTRA = b"\0\xff\xff\xff\xff"   # bytes 3..7 of every retail decal
CORNERS = ((1, 0), (1, 1), (0, 0), (0, 1))   # (dz, dx) of each stored corner


def _s8(v):
    return v - 256 if v > 127 else v


def _info(chunks):
    spacing, = struct.unpack_from("<f", chunks[b"INFO"], 0)
    size, patch, _, _, dtex, decals = struct.unpack_from("<6h", chunks[b"INFO"], 16)
    return spacing, size, size // patch, dtex, decals


def _texture_ref(names, k):
    """A decal's texture by name, or by DTEX slot when the name is blank or
    repeated (some retail maps list a texture twice)."""
    if k < len(names) and names[k] and names.count(names[k]) == 1:
        return names[k]
    return k


def export(chunks):
    """The map's decals as a JSON-ready dict."""
    _, _, side, dtex, _ = _info(chunks)
    names = tl.decode_names(chunks.get(b"DTEX", b""), dtex)
    patches = tl.decode_ptch(chunks[b"PTCH"])
    by_patch = tl.decals_by_patch(tl.decode_dcal(chunks.get(b"DCAL", b"")), patches)
    out = []
    for n, decals in enumerate(by_patch):
        px, pz = n % side, n // side
        for d in decals:
            x, z = _s8(d.x), _s8(d.y)
            e = {"cell": [px * tl.PATCH + x, pz * tl.PATCH + z],
                 "texture": _texture_ref(names, d.texture),
                 "uv": [list(c) for c in d.uv]}
            if not (0 <= x < tl.PATCH and 0 <= z < tl.PATCH):
                e["patch"] = [px, pz]
            if bytes(d.extra) != EXTRA:
                e["extra"] = bytes(d.extra).hex()
            out.append(e)
    return {"textures": names, "decals": out}


def _wrap16(v):
    return (int(v) + 32768) % 65536 - 32768


def road_decals(road, size, spacing):
    """Decal entries for one road: cells near the polyline, with texture
    coordinates from each corner's distance along and across the road."""
    pts = [(float(x), float(z)) for x, z in road["points"]]
    if len(pts) < 2:
        raise ValueError("a road needs at least two points")
    span = float(road.get("span", 25.0))
    half = size * spacing / 2
    ox, oz = half, half - spacing            # the game's world offsets (x + 2048, z + 2040 on a 4 km map)
    segs, s0 = [], 0.0
    for (ax, az), (bx, bz) in zip(pts, pts[1:]):
        length = math.hypot(bx - ax, bz - az)
        if length > 0:
            segs.append((ax, az, (bx - ax) / length, (bz - az) / length, length, s0))
            s0 += length

    total = s0

    def project(x, z):
        """(distance, along, signed across) to the road; the first and last
        segments extend past the ends, so end cells keep straight texture."""
        best = None
        for k, (ax, az, dx, dz, length, start) in enumerate(segs):
            t = (x - ax) * dx + (z - az) * dz
            t = max(t, 0.0) if k else t
            t = min(t, length) if k < len(segs) - 1 else t
            px, pz = ax + dx * t, az + dz * t
            dist = math.hypot(x - px, z - pz)
            if best is None or dist < best[0]:
                side = (x - ax) * dz - (z - az) * dx      # + to the road's right
                best = (dist, start + t, math.copysign(dist, side) if dist else 0.0)
        return best

    reach = span / 2 + spacing * 0.75
    xs = [x for x, _ in pts]
    zs = [z for _, z in pts]
    c0, c1 = int(math.floor((min(xs) - reach + ox) / spacing)), int(math.ceil((max(xs) + reach + ox) / spacing))
    r0, r1 = int(math.floor((min(zs) - reach + oz) / spacing)), int(math.ceil((max(zs) + reach + oz) / spacing))
    out = []
    for r in range(r0, r1):
        for c in range(c0, c1):
            cx, cz = (c + 0.5) * spacing - ox, (r + 0.5) * spacing - oz
            dist, along, _ = project(cx, cz)
            if dist > reach or not 0 <= along <= total:
                continue
            uv = []
            for dz, dx in CORNERS:
                _, s, d = project((c + dx) * spacing - ox, (r + dz) * spacing - oz)
                d = max(-span / 2, min(span / 2, d))     # past the texture's edge: stay on it, not on the next repeat
                uv.append([d / span * 2048 + 1024, s / span * 2048])
            shift = math.floor(min(v for _, v in uv) / 2048) * 2048   # keep v small: the texture repeats every 2048
            out.append({"cell": [c, r], "texture": road["texture"],
                        "uv": [[int(round(u)), int(round(v - shift))] for u, v in uv]})
    return out


def apply(chunks, doc):
    """New INFO, DTEX, PTCH and DCAL bodies for a decals document."""
    spacing, size, side, _, _ = _info(chunks)
    if "decals" not in doc or "textures" not in doc:
        base = export(chunks)          # no list given: keep the map's own (and add any roads)
        doc = {"textures": base["textures"], "decals": base["decals"], **doc}
    names = list(doc.get("textures") or [])
    entries = list(doc.get("decals", []))
    # A road replaces what's on its cells (the map's own decals or an earlier
    # road's): stacked decals would overflow a patch (the game holds at most
    # MAX_PER_PATCH, one per cell) and corrupt the game's memory.
    laid = {}
    for road in doc.get("roads", []):
        for e in road_decals(road, size, spacing):
            laid[tuple(int(v) for v in e["cell"])] = e
    if laid:
        entries = [e for e in entries if tuple(int(v) for v in e["cell"]) not in laid] + list(laid.values())

    def index(t):
        if isinstance(t, int):
            return t
        if t not in names:
            if len([n for n in names if n]) >= MAX_TEXTURES:
                raise ValueError(f"decal texture {t!r}: a map has at most {MAX_TEXTURES} decal textures")
            free = names.index("") if "" in names else len(names)
            names[free:free + 1] = [t]
        return names.index(t)

    per_patch = [[] for _ in range(side * side)]
    for e in entries:
        x, z = (int(v) for v in e["cell"])
        if "patch" in e:
            px, pz = (int(v) for v in e["patch"])
        else:
            px, pz = (x % size) // tl.PATCH, (z % size) // tl.PATCH
            x, z = px * tl.PATCH + (x % size) % tl.PATCH, pz * tl.PATCH + (z % size) % tl.PATCH
        dx, dz = x - px * tl.PATCH, z - pz * tl.PATCH
        if not (-128 <= dx < 128 and -128 <= dz < 128):
            raise ValueError(f"decal at cell {e['cell']} is too far from patch {[px, pz]}")
        uv = e["uv"]
        if len(uv) != 4:
            raise ValueError(f"decal at cell {e['cell']}: uv needs four [u, v] corners")
        extra = bytes.fromhex(e["extra"]) if "extra" in e else EXTRA
        per_patch[pz * side + px].append(tl.Decal(dx & 255, dz & 255, index(e["texture"]), extra,
                                                  [(_wrap16(u), _wrap16(v)) for u, v in uv]))
    if len(names) > MAX_TEXTURES:
        raise ValueError(f"a map has at most {MAX_TEXTURES} decal textures")
    for n, ds in enumerate(per_patch):
        if len(ds) > MAX_PER_PATCH:
            px, pz = n % side, n // side
            x0, z0 = px * tl.PATCH * spacing - size * spacing / 2, pz * tl.PATCH * spacing - (size * spacing / 2 - spacing)
            raise ValueError(f"{len(ds)} decals in the patch at world x {x0:.0f}..{x0 + tl.PATCH * spacing:.0f}, "
                             f"z {z0:.0f}..{z0 + tl.PATCH * spacing:.0f}: the game holds at most {MAX_PER_PATCH} "
                             f"(one per cell); more corrupt its memory")
    decals = [d for ds in per_patch for d in ds]
    patches = [tl.Patch(p.layer_mask, len(ds)) for p, ds in zip(tl.decode_ptch(chunks[b"PTCH"]), per_patch)]
    info = bytearray(chunks[b"INFO"])
    struct.pack_into("<2h", info, 24, len(names), len(decals))
    # keep DTEX's spare names past the count, as retail data has them
    spare = tl.decode_names(chunks.get(b"DTEX", b""))[len(names):]
    new = {b"INFO": bytes(info), b"DTEX": tl.encode_names(names + spare),
           b"PTCH": tl.encode_ptch(patches), b"DCAL": tl.encode_dcal(decals)}
    return {k: v for k, v in new.items() if chunks.get(k) != v}, len(decals)
