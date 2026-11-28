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
   "roads": [{"texture": ..., "points": [[x, z], ...], "span": 25,
              "priority": 0}]}
"cell" is the map cell (the heights PNG's pixel).  Exported decals that lie
outside the patch that stores them carry "patch": [px, pz], so the map
rebuilds exactly.  "roads" are laid by the build: "points" are world x, z in
metres (the Foundry Console's Position), and one texture width covers "span"
metres across the road (25 m: about 12 m of asphalt; the game's own roads
use about 37), with the texture running along it at the same scale.

A road covers the cells whose centres lie within span / 2 of it, and its
texture coordinates follow each corner's true distance across, running past
the texture's edge into the next repeat's see-through margin as retail roads
do (u from about -500 to 2550).  Clamping them at the edge instead bends the
texture differently in each cell, and a road that doesn't run along the grid
breaks into staggered squares.  Where roads cross, a cell goes to the road
of the highest "priority" (default 0; 0.390), then the one with the most
room from the cell's centre to its band's edge, then the nearer centre line.
A road of priority below 0 (a dirt track) never makes a junction piece.

Where roads of a family with junction textures (sw_decal_road_straight ->
_t, _cross) meet square (within SKEW degrees), the build lays the game's T or
cross piece over the strips' overlap, as retail does ("junctions": false in
the decals file keeps the roads crossing). Skewed junctions and road ends
meeting at an angle, where the square pieces would leave jagged edges, get a
plain asphalt patch over the overlap instead: the road's own texture with no
markings, soft-edged like the strips.
"""

import math
import re
import struct

from . import terrain_layers as tl

MAX_TEXTURES = 16
MAX_PER_PATCH = 64      # retail's most, one per cell of an 8 x 8 patch; past it the game crashes or hangs
EXTRA = b"\0\xff\xff\xff\xff"   # bytes 3..7 of every retail decal
CORNERS = ((1, 0), (1, 1), (0, 0), (0, 1))   # (dz, dx) of each stored corner
MARGIN = 0.21      # share of a road texture's width that's see-through at each side (retail u: -497..2553)
# A road narrower than this (m) also takes every cell its centre line
# passes through (0.386): kept by the cell's centre alone, a road under a
# cell's diagonal wide (11.3 m) showed in patches (Gaza: a 4 m alley missing
# under 43% of its line, a 7 m track 14%). Only the line's cells, not all
# its band touches: that took 50,708 cells on Gaza, past the map's 32,767
NARROW = 12.0


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
    priority = float(road.get("priority", 0))
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

    reach = span / 2
    line_cells = set()
    if span < NARROW:
        # the cells the centre line crosses (sampled every spacing / 16)
        step = spacing / 16
        for ax, az, dx, dz, length, _ in segs:
            for i in range(int(length / step) + 1):
                t = min(length, i * step)
                line_cells.add((int(math.floor((ax + dx * t + ox) / spacing)),
                                int(math.floor((az + dz * t + oz) / spacing))))
    edge = span * MARGIN
    xs = [x for x, _ in pts]
    zs = [z for _, z in pts]
    c0, c1 = int(math.floor((min(xs) - reach + ox) / spacing)), int(math.ceil((max(xs) + reach + ox) / spacing))
    r0, r1 = int(math.floor((min(zs) - reach + oz) / spacing)), int(math.ceil((max(zs) + reach + oz) / spacing))
    out = []
    for r in range(r0, r1):
        for c in range(c0, c1):
            cx, cz = (c + 0.5) * spacing - ox, (r + 0.5) * spacing - oz
            dist, along, _ = project(cx, cz)
            if (dist > reach and (c, r) not in line_cells) or not 0 <= along <= total:
                continue
            uv = []
            for dz, dx in CORNERS:
                _, s, d = project((c + dx) * spacing - ox, (r + dz) * spacing - oz)
                # past the texture's edge into the see-through margin of the
                # next repeat, but no further (that would show its asphalt)
                d = max(-span / 2 - edge, min(span / 2 + edge, d))
                uv.append([d / span * 2048 + 1024, s / span * 2048])
            shift = math.floor(min(v for _, v in uv) / 2048) * 2048   # keep v small: the texture repeats every 2048
            out.append({"cell": [c, r], "texture": road["texture"], "_dist": dist, "_room": span / 2 - dist,
                        "_prio": priority,
                        "uv": [[int(round(u)), int(round(v - shift))] for u, v in uv]})
    return out


# Junctions. Retail lays a junction as one texture square about the road's
# span a side (SW: 2048 units over ~41 m, usually a 5 x 5 block of cells),
# turned to the roads: <family>_t, _cross and _corner beside _straight
# (sw_decal_road_*, nk_decal_road_*). In texture terms (all of SW's and NW's
# junction blocks): a T's arms leave by its u+ edge (the stem) and both v
# edges (the road it joins); a corner's by u+ and v-; a cross's by all four.
JUNCTION_KINDS = ("t", "cross")
ARM_MERGE = 30.0          # degrees: arms closer than this are one
STRAIGHT_THROUGH = 150.0  # degrees: two arms wider than this are a straight road, not a corner
# how far from the square's middle a block cell's centre may be (u, v), as a
# share of the square: a T stops across its through road at that road's strip
BLOCK = {"t": (0.36, 0.5), "cross": (0.5, 0.5)}
# The pieces are drawn for roads meeting square: a T's stem or a cross's
# second road further than this from square keeps the straight roads (a
# square piece on a skewed junction leaves jagged ends)
SKEW = 15.0
# Where no piece fits (skewed junctions, ends meeting at an angle) the strips'
# overlap gets a plain patch (plain_patch): two road ends closer to straight
# than this keep their strips; the patch reaches this many spans from the
# meeting point; and the across coordinate skips the texture's middle this
# far each side (span shares: the yellow line and its shoulder)
STRAIGHT_ENDS = 165.0
PLAIN_REACH = 1.5
PLAIN_FROM = 0.06
PLAIN_INNER = 0.75        # a patch cell is the plain square when all its corners are this deep in asphalt
PLAIN_SQUARE = 0.36      # the cross piece's markings-free middle, a share of its side (SW: 0.31..0.69)


def junction_texture(straight, kind):
    """sw_decal_road_straight(alpha) -> sw_decal_road_<kind>; None for a
    texture with no junction family."""
    m = re.match(r"(?i)^(.*_road)_straight(?:alpha|a?2)?$", straight or "")
    return f"{m.group(1)}_{kind}" if m else None


def _segments(pts):
    return [((ax, az), (bx, bz)) for (ax, az), (bx, bz) in zip(pts, pts[1:]) if (ax, az) != (bx, bz)]


def _hit(p, q, r, s):
    """Where segments p-q and r-s cross: (t on the first, u on the second) or None."""
    dx1, dz1, dx2, dz2 = q[0] - p[0], q[1] - p[1], s[0] - r[0], s[1] - r[1]
    den = dx1 * dz2 - dz1 * dx2
    if abs(den) < 1e-9:
        return None
    t = ((r[0] - p[0]) * dz2 - (r[1] - p[1]) * dx2) / den
    u = ((r[0] - p[0]) * dz1 - (r[1] - p[1]) * dx1) / den
    return (t, u) if -1e-6 <= t <= 1 + 1e-6 and -1e-6 <= u <= 1 + 1e-6 else None


def _nearest_on(pts, x, z):
    """(distance, point, along) of the nearest point of a polyline."""
    best, s0 = None, 0.0
    for p, q in _segments(pts):
        dx, dz = q[0] - p[0], q[1] - p[1]
        l2 = dx * dx + dz * dz
        t = max(0.0, min(1.0, ((x - p[0]) * dx + (z - p[1]) * dz) / l2))
        px, pz = p[0] + dx * t, p[1] + dz * t
        d = math.hypot(x - px, z - pz)
        if best is None or d < best[0]:
            best = (d, (px, pz), s0 + t * math.sqrt(l2))
        s0 += math.sqrt(l2)
    return best


def _length(pts):
    return sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in _segments(pts))


def _point_at(pts, s):
    for p, q in _segments(pts):
        l = math.hypot(q[0] - p[0], q[1] - p[1])
        if s <= l:
            return (p[0] + (q[0] - p[0]) * s / l, p[1] + (q[1] - p[1]) * s / l)
        s -= l
    return pts[-1]


def find_junctions(roads, tol=2.0):
    """[(x, z, [arm directions], span, straight texture, [road indices])]: where roads cross
    or one ends on another (within tol m), with the directions the roads
    leave the point, merged and counted.  Roads of priority below 0 (dirt
    tracks, 0.390) and with fewer than two points take no part; indices are
    the roads list's."""
    lines = [([(float(x), float(z)) for x, z in r["points"]], float(r.get("span", 25.0)), r.get("texture"))
             if len(r.get("points", [])) >= 2 and float(r.get("priority", 0)) >= 0 else None
             for r in roads]
    points = []          # (x, z, road indices)
    for i, line in enumerate(lines):
        if line is None:
            continue
        a = line[0]
        for j in range(i + 1, len(lines)):
            if lines[j] is None:
                continue
            b = lines[j][0]
            for p, q in _segments(a):
                for r, s in _segments(b):
                    h = _hit(p, q, r, s)
                    if h:
                        points.append((p[0] + (q[0] - p[0]) * h[0], p[1] + (q[1] - p[1]) * h[0], {i, j}))
            for end in (a[0], a[-1]):
                d, at, _ = _nearest_on(b, *end)
                if d <= tol:
                    points.append((at[0], at[1], {i, j}))
            for end in (b[0], b[-1]):
                d, at, _ = _nearest_on(a, *end)
                if d <= tol:
                    points.append((at[0], at[1], {i, j}))
    # merge points closer than half the widest span there
    merged = []
    for x, z, ids in points:
        reach = max(lines[k][1] for k in ids) / 2
        for m in merged:
            if math.hypot(m[0] - x, m[1] - z) < reach:
                m[2] |= ids
                break
        else:
            merged.append([x, z, set(ids)])
    out = []
    for x, z, ids in merged:
        arms = []
        for k in ids:
            pts, span, _ = lines[k]
            d, _, s = _nearest_on(pts, x, z)
            total = _length(pts)
            step = max(span / 2, 4.0)
            for t in (s + step, s - step):
                if 0 <= t <= total and (t > s and total - s > span / 4 or t < s and s > span / 4):
                    px, pz = _point_at(pts, t)
                    arms.append(math.atan2(pz - z, px - x))
        dirs = []
        for ang in sorted(arms):
            if not any(abs(math.degrees((ang - b + math.pi) % (2 * math.pi) - math.pi)) < ARM_MERGE for b in dirs):
                dirs.append(ang)
        if len(dirs) < 2:
            continue
        widest = max(ids, key=lambda k: lines[k][1])
        out.append((x, z, dirs, lines[widest][1], lines[widest][2], sorted(ids)))
    return out


def plain_patch(roads, ids, x, z, dirs, span, texture, size, spacing, covered):
    """Decal entries for a plain asphalt patch where roads meet with no
    junction piece to fit (skewed, or ends meeting at an angle): the cells
    near the meeting point that two or more of the roads' strips cover, in
    the road's own straight texture, its across coordinate the distance to
    the nearest of the roads' centre lines with the centre line squeezed
    out. So the patch has no markings, and the strips' union keeps their
    soft edges (round at a road's end) instead of one road's lines crossing
    the other."""
    centre = junction_texture(texture, "cross")
    if not centre:
        return []                            # a texture with no junction family: the roads keep crossing
    half = size * spacing / 2
    ox, oz = half, half - spacing
    lines = [[(float(px), float(pz)) for px, pz in roads[k]["points"]] for k in ids]
    mine = set(ids)
    asphalt = span * (0.5 - MARGIN)          # the texture's asphalt either side of its centre line
    skip = span * PLAIN_FROM                 # what's squeezed out: the centre line and its shoulder
    edge = span * MARGIN
    ux, uz = math.cos(dirs[0]), math.sin(dirs[0])
    radius = span * PLAIN_REACH
    if spacing / span > PLAIN_SQUARE:
        centre = None                        # cells wider than the plain square

    def uv_at(px, pz):
        a = min(_nearest_on(pts, px, pz)[0] for pts in lines)
        if a < asphalt:
            a = skip + a * (asphalt - skip) / asphalt
        a = min(a, span / 2 + edge)
        return (a / span * 2048 + 1024, ((px - x) * ux + (pz - z) * uz) / span * 2048)

    out = []
    c0, c1 = int(math.floor((x - radius + ox) / spacing)), int(math.ceil((x + radius + ox) / spacing))
    r0, r1 = int(math.floor((z - radius + oz) / spacing)), int(math.ceil((z + radius + oz) / spacing))
    for r in range(r0, r1):
        for c in range(c0, c1):
            if len(covered.get((c, r), set()) & mine) < 2:
                continue
            cx, cz = (c + 0.5) * spacing - ox, (r + 0.5) * spacing - oz
            if math.hypot(cx - x, cz - z) > radius:
                continue
            corners = [((c + dx) * spacing - ox, (r + dz) * spacing - oz) for dz, dx in CORNERS]
            if centre and all(min(_nearest_on(pts, px, pz)[0] for pts in lines) <= asphalt * PLAIN_INNER
                              for px, pz in corners):
                # inside the asphalt: the cross piece's plain middle at the
                # road's scale (the distance mapping smears here, where
                # every spot is near some centre line), a little shifted
                # cell to cell so it doesn't tile
                w = spacing / span * 2048
                room = max(0.0, PLAIN_SQUARE * 2048 - w) / 2
                ju, jv = ((c * 73 + r * 151) % 17 / 8 - 1) * room, ((c * 199 + r * 37) % 17 / 8 - 1) * room
                out.append({"cell": [c, r], "texture": centre,
                            "uv": [[int(round(1024 + ju + (dx - 0.5) * w)), int(round(1024 + jv + (dz - 0.5) * w))]
                                   for dz, dx in CORNERS]})
                continue
            uv = [uv_at(px, pz) for px, pz in corners]
            shift = math.floor(min(v for _, v in uv) / 2048) * 2048
            out.append({"cell": [c, r], "texture": texture,
                        "uv": [[int(round(u)), int(round(v - shift))] for u, v in uv]})
    return out


def junction_decals(roads, size, spacing, covered=None):
    """Decal entries for the junctions of a decals document's roads: as
    retail lays them, the cells where the meeting roads' strips overlap
    (a T: the road it joins across, the stem's width along), the junction
    texture's square centred on the meeting point at the roads' own scale
    (2048 a span). covered: {cell: set of road indices whose decals cover
    it} (made here if not given)."""
    half = size * spacing / 2
    ox, oz = half, half - spacing
    if covered is None:
        covered = {}
        for k, road in enumerate(roads):
            if len(road.get("points", [])) >= 2:
                for e in road_decals(road, size, spacing):
                    covered.setdefault(tuple(int(v) for v in e["cell"]), set()).add(k)
    out = []
    for x, z, dirs, span, straight, ids in find_junctions(roads):
        def gap(a, b):
            return abs(math.degrees((a - b + math.pi) % (2 * math.pi) - math.pi))
        def plain():
            out.extend(plain_patch(roads, ids, x, z, dirs, span, straight, size, spacing, covered))
        if len(dirs) == 2:
            # two roads meeting end to end: a street that turns is one road
            # (gis joins them) and bends smoothly. Ends meeting nearly
            # straight keep their strips; at an angle they get a plain patch
            if gap(dirs[0], dirs[1]) < STRAIGHT_ENDS:
                plain()
            continue
        elif len(dirs) == 3:
            kind = "t"
            # the two arms most nearly opposite are the road it joins; the third is the stem (u+)
            pairs = [(gap(dirs[i], dirs[j]), i, j) for i in range(3) for j in range(i + 1, 3)]
            _, i, j = max(pairs)
            stem = ({0, 1, 2} - {i, j}).pop()
            if abs(gap(dirs[stem], dirs[i]) - 90) > SKEW or abs(gap(dirs[stem], dirs[j]) - 90) > SKEW:
                plain()
                continue
            a, b_ang = dirs[stem], dirs[i]
        else:
            kind = "cross"
            if len(dirs) > 4 or any(min(abs(gap(d, dirs[0]) - k) for k in (0, 90, 180)) > SKEW for d in dirs):
                plain()
                continue
            a, b_ang = dirs[0], dirs[0] + math.pi / 2
        texture = junction_texture(straight, kind)
        if not texture:
            plain()
            continue
        au = (math.cos(a), math.sin(a))
        # v axis: perpendicular to u, on the side b_ang points to
        bv = (-au[1], au[0])
        if bv[0] * math.cos(b_ang) + bv[1] * math.sin(b_ang) < 0:
            bv = (au[1], -au[0])

        def uv_at(px, pz):
            dx, dz = px - x, pz - z
            return ((dx * au[0] + dz * au[1]) / span + 0.5) * 2048, ((dx * bv[0] + dz * bv[1]) / span + 0.5) * 2048

        mine = set(ids)
        # the block: cells on the meeting roads whose centres lie within the
        # piece's square, across a T's through road only as far as its strip
        # (retail: centres u 0.18..0.76, v 0.03..0.99 of the square)
        eu, ev = BLOCK[kind]
        reach = span
        c0, c1 = int(math.floor((x - reach + ox) / spacing)), int(math.ceil((x + reach + ox) / spacing))
        r0, r1 = int(math.floor((z - reach + oz) / spacing)), int(math.ceil((z + reach + oz) / spacing))
        for r in range(r0, r1):
            for c in range(c0, c1):
                if not covered.get((c, r), set()) & mine:
                    continue
                cx, cz = (c + 0.5) * spacing - ox, (r + 0.5) * spacing - oz
                u, v = uv_at(cx, cz)
                if abs(u - 1024) > eu * 2048 or abs(v - 1024) > ev * 2048:
                    continue
                uv = [[int(round(k)) for k in uv_at((c + dx) * spacing - ox, (r + dz) * spacing - oz)]
                      for dz, dx in CORNERS]
                out.append({"cell": [c, r], "texture": texture, "uv": uv})
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
    covered = {}
    for n, road in enumerate(doc.get("roads", [])):
        for e in road_decals(road, size, spacing):
            k = tuple(int(v) for v in e["cell"])
            covered.setdefault(k, set()).add(n)
            # 0.386: the road with the most room from the cell's centre to its
            # band's edge (span / 2 - distance), so a wide road keeps its own
            # middle against a narrow one running inside or beside it (a 9 m
            # street's strip, with its see-through sides, was a hole in a 26 m
            # road); equal room: the nearer centre line (crossing roads)
            o = laid.get(k)
            # 0.390: first the higher "priority" (a dirt track, -1, never
            # cuts a paved road's edge where its own band is roomier)
            if o is None or (e["_prio"], e["_room"], -e["_dist"]) >= (o["_prio"], o["_room"], -o["_dist"]):
                laid[k] = e
    for e in laid.values():
        e.pop("_dist", None)
        e.pop("_room", None)
        e.pop("_prio", None)
    # junctions: the game's T, cross and corner pieces over where roads meet
    # ("junctions": false keeps the straight roads crossing as they lay)
    if doc.get("roads") and doc.get("junctions", True):
        for e in junction_decals(doc["roads"], size, spacing, covered):
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
