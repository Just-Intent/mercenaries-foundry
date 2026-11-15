"""Real-world map data (GIS) as a starting point for a new map.

A plan file (`gis/<map>.json` in a mod) places districts of the real world on
a game map: each takes a square of ground around a latitude and longitude,
turns it, and puts it at a point on the map.  `gis fetch` downloads what the
districts need (OpenStreetMap through the Overpass API; elevation as
Terrarium PNG tiles from the AWS open terrain tiles) into `gis/cache/`, and
`gis build` turns it into ordinary mod files, all editable afterwards:

  terrain/<map>.heights.png (+ .heights.json)   ground from real elevation
  terrain/<map>.layers.json, .layerNN.*.png     ground paint from land use
  terrain/<map>.decals.json                     roads from OSM highways
  encounters/<map>_gis.json                     buildings as game models
  gis/<map>.preview.png                         the map from above, to check

The map must already exist (`maps/<map>.json`; the editor's Map panel makes
one).  Buildings are stand-ins: each real footprint becomes the game model of
its kind (see KIT) closest to it in size, turned to the footprint, with the
model's own ruin.  OSM data is (c) OpenStreetMap contributors, ODbL: say so
wherever the map is shared.

Map coordinates: x east, z south (lower z is north), metres, the map's centre
at 0, 0; a 512-sample map at 8 m runs from -2048 to 2048.
"""
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

from . import png

SIZE = 512
SPACING = 8.0
STEP = 0.3499999940395355
LEVELS = 16
HALF = SIZE * SPACING / 2

OVERPASS = "https://overpass-api.de/api/interpreter"
TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
USER_AGENT = "mercfoundry-gis/1 (map importer for a game mod toolkit)"
ZOOM = 14

# Ground layers, bottom to top: texture, scale, and what paints it.
LAYERS = [
    {"key": "sand", "texture": "sw_tt_sand", "scale": 16.0},
    {"key": "dirt", "texture": "sw_tt_dirt", "scale": 16.0},
    {"key": "scrub", "texture": "swn_tt_yellowgrass", "scale": 8.0},
    {"key": "farm", "texture": "nk_tt_farm", "scale": 16.0},
    {"key": "green", "texture": "nk_tt_greengrass", "scale": 16.0},
    {"key": "urban", "texture": "nk_tt_concrete", "scale": 16.0},
]

# OSM tags -> ground layer
LANDUSE = {
    ("landuse", "farmland"): "farm", ("landuse", "farmyard"): "farm", ("landuse", "greenhouse_horticulture"): "farm",
    ("landuse", "orchard"): "green", ("landuse", "grass"): "green", ("leisure", "park"): "green",
    ("landuse", "meadow"): "scrub", ("natural", "scrub"): "scrub", ("natural", "grassland"): "scrub",
    ("landuse", "cemetery"): "dirt", ("landuse", "construction"): "dirt", ("landuse", "brownfield"): "dirt",
    ("natural", "sand"): "sand", ("natural", "beach"): "sand",
    ("landuse", "residential"): "urban", ("landuse", "commercial"): "urban", ("landuse", "retail"): "urban",
    ("landuse", "industrial"): "urban", ("amenity", "parking"): "urban",
}

# OSM highway class -> (decal texture, width in metres); classes not listed are left out.
ROADS = {
    "motorway": ("sw_decal_road_straight", 16.0), "trunk": ("sw_decal_road_straight", 16.0),
    "primary": ("sw_decal_road_straight", 14.0), "secondary": ("sw_decal_road_straight", 11.0),
    "tertiary": ("sw_decal_road_straightalpha", 9.0), "residential": ("sw_decal_road_straightalpha", 7.0),
    "unclassified": ("sw_decal_road_straightalpha", 7.0),
}
ROAD_ORDER = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential"]

# Building kinds -> game models (with their ruins) to stand in for them, with
# their footprint (x, z) and height in metres, measured from the models.
KIT = {
    "house": [
        ("nkvillage_bld_house02", 16.6, 8.5, 6.6), ("nkvillage_bld_house03", 15.2, 10.1, 7.5),
        ("nkvillage_bld_house01", 19.2, 10.3, 5.8), ("city_bld_market05", 10.1, 9.7, 6.0),
        ("nkvillage_bld_riverhouse01", 15.8, 12.3, 7.6), ("city_bld_market03", 18.5, 8.8, 5.2),
    ],
    "block": [
        ("dandong_bld_house02", 20.1, 12.6, 13.8), ("dandong_bld_house01", 35.5, 14.6, 13.1),
        ("sinuiju_bld_apartment02", 26.0, 16.5, 21.0), ("city_bld_office03", 20.4, 14.5, 31.4),
    ],
    "tower": [
        ("sinuiju_bld_apartment01", 32.0, 16.5, 33.0), ("city_bld_office04", 40.0, 21.0, 20.8),
        ("city_bld_cityblockcornerc", 32.2, 32.0, 20.2), ("city_bld_cityblockcornerb", 32.0, 32.0, 26.1),
    ],
    "shed": [
        ("warehouse_bld_warehouse01", 13.5, 23.1, 6.3), ("warehouse_bld_warehouse02", 21.1, 26.8, 7.7),
        ("warehouse_bld_warehouse04", 29.3, 30.8, 7.9), ("warehouse_bld_truckdepot03", 30.4, 13.3, 8.2),
    ],
}
SHED_TAGS = {"industrial", "warehouse", "garage", "garages", "hangar", "shed", "barn", "greenhouse", "farm_auxiliary"}
MIN_AREA = 40.0      # m^2: smaller footprints are left out


# ---------------------------------------------------------------- districts

class District:
    """A square of the real world placed on the map."""

    def __init__(self, spec):
        self.name = spec["name"]
        self.lat, self.lon = (float(v) for v in spec["center"])
        self.size = float(spec.get("size", 1000))
        self.turn = math.radians(float(spec.get("turn", 0)))       # counter-clockwise, seen from above
        self.at = tuple(float(v) for v in spec.get("at", (0, 0)))
        self.feather = float(spec.get("feather", 80))
        self.raise_m = float(spec.get("raise", 0))
        self.kx = 111320.0 * math.cos(math.radians(self.lat))
        self.ky = 110574.0

    def to_map(self, lat, lon):
        """(x, z) on the map of a real point."""
        e, n = (lon - self.lon) * self.kx, (lat - self.lat) * self.ky
        c, s = math.cos(self.turn), math.sin(self.turn)
        e, n = e * c - n * s, e * s + n * c
        return self.at[0] + e, self.at[1] - n

    def to_world(self, x, z):
        """(lat, lon) of a point on the map."""
        e, n = x - self.at[0], self.at[1] - z
        c, s = math.cos(-self.turn), math.sin(-self.turn)
        e, n = e * c - n * s, e * s + n * c
        return self.lat + n / self.ky, self.lon + e / self.kx

    def weight(self, x, z):
        """1 inside the square, falling to 0 over `feather` metres past it."""
        h = self.size / 2
        d = max(abs(x - self.at[0]) - h, abs(z - self.at[1]) - h)
        if d <= 0:
            return 1.0
        if d >= self.feather or self.feather <= 0:
            return 0.0
        t = d / self.feather
        return 1 - t * t * (3 - 2 * t)

    def inside(self, x, z, margin=0.0):
        h = self.size / 2 + margin
        return abs(x - self.at[0]) <= h and abs(z - self.at[1]) <= h

    def bbox(self, margin=None):
        """(south, west, north, east) in degrees covering the square and its feather."""
        h = self.size / 2 + (self.feather if margin is None else margin)
        pts = [self.to_world(self.at[0] + dx, self.at[1] + dz) for dx in (-h, h) for dz in (-h, h)]
        return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if not plan.get("map"):
        raise ValueError(f"{path}: needs \"map\" (the new map's name)")
    plan["districts"] = [District(d) for d in plan.get("districts", [])]
    names = [d.name for d in plan["districts"]]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: district names must differ")
    return plan


# ---------------------------------------------------------------- fetching

def _get(url, data=None, tries=5):
    """A URL's body; busy servers (429, 502, 503, 504) are tried again after a wait."""
    import time
    import urllib.error
    for n in range(tries):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or n == tries - 1:
                raise
            time.sleep(15 * (n + 1))


def overpass_query(bbox):
    s, w, n, e = bbox
    b = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    return (f"[out:json][timeout:240];("
            f"way[building]{b};relation[building]{b};"
            f"way[highway]{b};"
            f"way[landuse]{b};way[natural]{b};way[leisure]{b};way[amenity=parking]{b};"
            f");out geom;")


def tile_xy(lat, lon, z=ZOOM):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def tiles_for(bbox, z=ZOOM):
    s, w, n, e = bbox
    x0, y0 = tile_xy(n, w, z)
    x1, y1 = tile_xy(s, e, z)
    return [(z, x, y) for x in range(int(x0), int(x1) + 1) for y in range(int(y0), int(y1) + 1)]


def fetch(plan, cache, only=None, log=print, get=_get):
    """Download OSM data and elevation tiles the plan's districts need (files
    already in the cache are kept)."""
    cache = Path(cache)
    (cache / "osm").mkdir(parents=True, exist_ok=True)
    (cache / "dem").mkdir(parents=True, exist_ok=True)
    for d in plan["districts"]:
        if only and d.name not in only:
            continue
        f = cache / "osm" / f"{d.name}.json"
        if f.exists():
            log(f"{d.name}: OSM data already in the cache")
        else:
            body = urllib.parse.urlencode({"data": overpass_query(d.bbox())}).encode()
            data = get(OVERPASS, body)
            json.loads(data)
            f.write_bytes(data)
            log(f"{d.name}: OSM data, {len(data) / 1e6:.1f} MB")
        got = 0
        for z, x, y in tiles_for(d.bbox()):
            t = cache / "dem" / f"{z}_{x}_{y}.png"
            if not t.exists():
                t.write_bytes(get(TERRARIUM.format(z=z, x=x, y=y)))
                got += 1
        log(f"{d.name}: {got} elevation tiles downloaded")


# ---------------------------------------------------------------- elevation

class Elevation:
    """Terrarium tiles from the cache: metres above sea level at a point."""

    def __init__(self, folder, z=ZOOM):
        self.folder, self.z, self.tiles = Path(folder), z, {}

    def _tile(self, x, y):
        k = (x, y)
        if k not in self.tiles:
            f = self.folder / f"{self.z}_{x}_{y}.png"
            if f.exists():
                w, h, rgba = png.decode(f.read_bytes())
                self.tiles[k] = (w, [rgba[i] * 256 + rgba[i + 1] + rgba[i + 2] / 256 - 32768
                                     for i in range(0, len(rgba), 4)])
            else:
                self.tiles[k] = None
        return self.tiles[k]

    def _px(self, X, Y):
        tx, ty = int(X // 256), int(Y // 256)
        t = self._tile(tx, ty)
        if t is None:
            return None
        w, vals = t
        return vals[int(Y - ty * 256) % w * w + int(X - tx * 256) % w]

    def at(self, lat, lon):
        fx, fy = tile_xy(lat, lon, self.z)
        X, Y = fx * 256 - 0.5, fy * 256 - 0.5
        x0, y0 = math.floor(X), math.floor(Y)
        a, b = X - x0, Y - y0
        v = [self._px(x0 + i, y0 + j) for j in (0, 1) for i in (0, 1)]
        if any(p is None for p in v):
            return None
        return (v[0] * (1 - a) + v[1] * a) * (1 - b) + (v[2] * (1 - a) + v[3] * a) * b


def sample_xz(i):
    """Map coordinate of height sample i (column or row)."""
    return i * SPACING - HALF


def heights(plan, elevation):
    """Ground height in metres per sample, heights[row][col]: each district's
    real elevation (times "vertical", plus "base" and its "raise"), blended
    over its feather into the plan's "base" height elsewhere; the sea (at or
    below 0 m) goes down to "sea_floor"."""
    base = float(plan.get("base", 20.0))
    vert = float(plan.get("vertical", 1.0))
    sea = float(plan.get("sea_floor", base - 6.0))
    sea_x = plan.get("sea_x")        # outside the districts, west of this x is sea
    out = []
    for r in range(SIZE):
        z = sample_xz(r)
        row = []
        for c in range(SIZE):
            x = sample_xz(c)
            fill = sea if sea_x is not None and x < float(sea_x) else base
            tot, acc = 0.0, 0.0
            for d in plan["districts"]:
                w = d.weight(x, z)
                if w <= 0:
                    continue
                e = elevation.at(*d.to_world(x, z))
                if e is None:
                    continue
                h = sea if e <= 0.0 else base + d.raise_m + e * vert
                tot += w
                acc += w * h
            if tot <= 0:
                row.append(fill)
            elif tot < 1:
                row.append(acc + (1 - tot) * fill)
            else:
                row.append(acc / tot)
        out.append(row)
    return out


def heights_png(hm):
    """16-bit heights PNG and its .heights.json metadata."""
    units = [[h / STEP for h in row] for row in hm]
    lo = math.floor(min(min(r) for r in units))
    vals = [max(0, min(65535, int(round((u - lo) * LEVELS)))) for row in units for u in row]
    meta = {"size": SIZE, "spacing_m": SPACING, "step_m": STEP, "base_units": lo, "levels_per_step": LEVELS}
    return png.encode_grey16(SIZE, SIZE, vals), meta


class Ground:
    """Bilinear height lookups in a heights grid."""

    def __init__(self, hm):
        self.hm = hm

    def at(self, x, z):
        fx = min(max((x + HALF) / SPACING, 0), SIZE - 1.001)
        fz = min(max((z + HALF) / SPACING, 0), SIZE - 1.001)
        c, r = int(fx), int(fz)
        a, b = fx - c, fz - r
        h = self.hm
        return ((h[r][c] * (1 - a) + h[r][c + 1] * a) * (1 - b)
                + (h[r + 1][c] * (1 - a) + h[r + 1][c + 1] * a) * b)


# ---------------------------------------------------------------- OSM

def read_osm(path):
    """Ways with geometry from an Overpass `out geom` answer: [(tags, [(lat, lon)])].
    Building relations give their outer rings."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for el in doc.get("elements", []):
        if el.get("type") == "way" and el.get("geometry"):
            out.append((el.get("tags", {}), [(p["lat"], p["lon"]) for p in el["geometry"]]))
        elif el.get("type") == "relation":
            for m in el.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    out.append((el.get("tags", {}), [(p["lat"], p["lon"]) for p in m["geometry"]]))
    return out


def _ground_key(tags):
    for k in ("landuse", "natural", "leisure", "amenity"):
        if (k, tags.get(k)) in LANDUSE:
            return LANDUSE[(k, tags[k])]
    return None


# ---------------------------------------------------------------- geometry

def polygon_area(pts):
    return abs(sum(x0 * z1 - x1 * z0 for (x0, z0), (x1, z1) in zip(pts, pts[1:] + pts[:1]))) / 2


def convex_hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def min_rect(pts):
    """Smallest-area rectangle around points: (cx, cz, length, width, angle),
    the angle (radians, of the long side, from +x towards +z)."""
    hull = convex_hull(pts)
    best = None
    for (x0, z0), (x1, z1) in zip(hull, hull[1:] + hull[:1]):
        a = math.atan2(z1 - z0, x1 - x0)
        c, s = math.cos(a), math.sin(a)
        us = [x * c + z * s for x, z in hull]
        vs = [-x * s + z * c for x, z in hull]
        area = (max(us) - min(us)) * (max(vs) - min(vs))
        if best is None or area < best[0]:
            u, v = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2
            best = (area, u * c - v * s, u * s + v * c, max(us) - min(us), max(vs) - min(vs), a)
    if best is None:
        x, z = pts[0]
        return x, z, 0.0, 0.0, 0.0
    _, cx, cz, L, W, a = best
    if W > L:
        L, W, a = W, L, a + math.pi / 2
    return cx, cz, L, W, a


def fill_polygon(grid, side, pts, value, cell, origin):
    """Set grid cells (side x side, row-major) whose centres fall in a polygon
    of map points to max(current, value). Pixel i is at origin + i * cell."""
    zs = [z for _, z in pts]
    r0 = max(0, int(math.ceil((min(zs) - origin) / cell)))
    r1 = min(side - 1, int(math.floor((max(zs) - origin) / cell)))
    n = len(pts)
    for r in range(r0, r1 + 1):
        z = origin + r * cell
        xs = []
        for i in range(n):
            (xa, za), (xb, zb) = pts[i], pts[(i + 1) % n]
            if (za <= z < zb) or (zb <= z < za):
                xs.append(xa + (z - za) * (xb - xa) / (zb - za))
        xs.sort()
        for a, b in zip(xs[0::2], xs[1::2]):
            c0 = max(0, int(math.ceil((a - origin) / cell)))
            c1 = min(side - 1, int(math.floor((b - origin) / cell)))
            for c in range(c0, c1 + 1):
                if grid[r * side + c] < value:
                    grid[r * side + c] = value


def clip_line(pts, d):
    """Pieces of a polyline (map points) inside a district's square."""
    out, cur = [], []
    for p in pts:
        if d.inside(*p):
            cur.append(p)
        elif cur:
            if len(cur) > 1:
                out.append(cur)
            cur = []
    if len(cur) > 1:
        out.append(cur)
    return out


# ---------------------------------------------------------------- buildings

def building_kind(tags, area, levels):
    b = (tags.get("building") or "").lower()
    if b in SHED_TAGS:
        return "shed"
    if levels >= 7:
        return "tower"
    if levels >= 3 or (levels == 0 and area > 400):
        return "block"
    return "house"


def levels_of(tags):
    for k in ("building:levels", "levels"):
        try:
            return max(0, int(float(tags[k])))
        except (KeyError, ValueError):
            pass
    try:
        return max(0, int(float(str(tags["height"]).split()[0]) / 3))
    except (KeyError, ValueError, IndexError):
        return 0


def pick_model(kind, length, width, kit=KIT):
    """The kit model of a kind closest to a footprint: (model, quarter turn),
    quarter turn True when the model's long side is its z."""
    best = None
    for model, mx, mz, _ in kit[kind]:
        turned = mz > mx
        ml, mw = (mz, mx) if turned else (mx, mz)
        score = abs(math.log(ml / max(length, 1))) + abs(math.log(mw / max(width, 1)))
        if best is None or score < best[0]:
            best = (score, model, turned, ml, mw)
    return best[1], best[2], best[3], best[4]


def place_buildings(footprints, ground, kit=KIT, limit=None, prefix="gis"):
    """Encounter-layer objects for building footprints ([(tags, [(x, z)])]):
    biggest first, each skipped if its model would overlap one already placed."""
    items = []
    for tags, pts in footprints:
        if len(pts) > 2 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        area = polygon_area(pts)
        if area < MIN_AREA:
            continue
        cx, cz, L, W, ang = min_rect(pts)
        items.append((area, tags, cx, cz, L, W, ang, pts))
    items.sort(key=lambda t: -t[0])
    taken = {}       # 8 m cells -> circles (x, z, r) placed there
    objects, skipped = [], 0
    for area, tags, cx, cz, L, W, ang, pts in items:
        if limit is not None and len(objects) >= limit:
            skipped += 1
            continue
        kind = building_kind(tags, area, levels_of(tags))
        model, turned, ml, mw = pick_model(kind, L, W, kit)
        r = math.hypot(ml, mw) / 2 * 0.8
        key = (int(cx // 32), int(cz // 32))
        clash = False
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for ox, oz, orr in taken.get((key[0] + i, key[1] + j), ()):
                    if math.hypot(cx - ox, cz - oz) < (r + orr) * 0.9:
                        clash = True
        if clash:
            skipped += 1
            continue
        taken.setdefault(key, []).append((cx, cz, r))
        # yaw: the model's x axis along the footprint's long side (rows of the
        # world's rotation: local x -> (cos yaw, -sin yaw) in x, z)
        a = ang + (math.pi / 2 if turned else 0.0)
        yaw = (-math.degrees(a) + 180) % 360 - 180
        y = min(ground.at(x, z) for x, z in pts + [(cx, cz)])
        objects.append({"from": f"model:{model}", "name": f"{prefix}_{len(objects) + 1:05d}",
                        "position": [round(cx, 2), round(y, 2), round(cz, 2)], "yaw": round(yaw, 1),
                        "_kind": kind, "_footprint": [L, W]})
    return objects, skipped


# ---------------------------------------------------------------- build

def build(plan, mod, log=print):
    """Write the plan's terrain, roads and buildings into a mod folder."""
    mod = Path(mod)
    name = plan["map"].lower()
    if not (mod / "maps" / f"{name}.json").exists():
        log(f"note: maps/{name}.json isn't in the mod: make the map first (the editor's Map panel)")
    cache = mod / "gis" / "cache"
    elev = Elevation(cache / "dem")
    hm = heights(plan, elev)
    ground = Ground(hm)
    tdir = mod / "terrain"
    tdir.mkdir(parents=True, exist_ok=True)
    data, meta = heights_png(hm)
    meta["terrain"] = name
    (tdir / f"{name}.heights.png").write_bytes(data)
    (tdir / f"{name}.heights.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    log(f"heights: {min(min(r) for r in hm):.1f} to {max(max(r) for r in hm):.1f} m")

    side = SIZE + 1
    paint = {l["key"]: bytearray(side * side) for l in LAYERS}
    paint["sand"][:] = b"\xff" * (side * side)
    roads, foot = [], []
    counts = {}
    for d in plan["districts"]:
        f = cache / "osm" / f"{d.name}.json"
        if not f.exists():
            log(f"{d.name}: no OSM data in the cache (run gis fetch)")
            continue
        n_ways = 0
        mine = {k: bytearray(side * side) for k in paint}
        for tags, geo in read_osm(f):
            pts = [d.to_map(lat, lon) for lat, lon in geo]
            if tags.get("building"):
                cx = sum(p[0] for p in pts) / len(pts)
                cz = sum(p[1] for p in pts) / len(pts)
                if d.inside(cx, cz):
                    foot.append((tags, pts))
                    fill_polygon(mine["urban"], side, pts, 255, SPACING, -HALF)
            elif tags.get("highway") in ROADS:
                for piece in clip_line(pts, d):
                    roads.append((tags["highway"], piece))
            else:
                k = _ground_key(tags)
                if k and len(pts) > 2:
                    fill_polygon(mine[k], side, pts, 255, SPACING, -HALF)
            n_ways += 1
        counts[d.name] = n_ways
        # only the district's own square takes its paint; land no OSM polygon
        # covers gets some dirt, so it isn't all sand
        h = d.size / 2
        c0, c1 = (max(0, int(math.ceil((d.at[0] - h + HALF) / SPACING))),
                  min(side - 1, int((d.at[0] + h + HALF) / SPACING)))
        r0, r1 = (max(0, int(math.ceil((d.at[1] - h + HALF) / SPACING))),
                  min(side - 1, int((d.at[1] + h + HALF) / SPACING)))
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                i = r * side + c
                for k in paint:
                    if mine[k][i] > paint[k][i]:
                        paint[k][i] = mine[k][i]
                if paint["dirt"][i] < 140:
                    paint["dirt"][i] = 140
    # the sea floor and beach stay sand
    sea_top = float(plan.get("base", 20.0)) + 0.5
    for r in range(side):
        for c in range(side):
            if hm[min(r, SIZE - 1)][min(c, SIZE - 1)] <= sea_top:
                i = r * side + c
                for k in paint:
                    if k != "sand":
                        paint[k][i] = 0
    table = [{"texture": l["texture"], "scale": l["scale"], "axis": 0} for l in LAYERS]
    (tdir / f"{name}.layers.json").write_text(json.dumps({"layers": table}, indent=1) + "\n", encoding="utf-8")
    for i, l in enumerate(LAYERS):
        (tdir / f"{name}.layer{i:02d}.{l['texture']}.png").write_bytes(png.encode(side, side, bytes(paint[l["key"]]), 1))

    roads.sort(key=lambda t: ROAD_ORDER.index(t[0]), reverse=True)   # big roads last: they win shared cells
    road_docs = [{"texture": ROADS[k][0], "span": ROADS[k][1],
                  "points": [[round(x, 2), round(z, 2)] for x, z in pts]} for k, pts in roads]
    road_docs += plan.get("roads", [])
    (tdir / f"{name}.decals.json").write_text(json.dumps({"roads": road_docs}, indent=1) + "\n", encoding="utf-8")
    log(f"roads: {len(road_docs)}")

    objects, skipped = place_buildings(foot, ground, limit=plan.get("max_buildings"))
    kinds = {}
    for o in objects:
        kinds[o["_kind"]] = kinds.get(o["_kind"], 0) + 1
    edir = mod / "encounters"
    edir.mkdir(parents=True, exist_ok=True)
    layer = {"map": name.upper(), "load": "map",
             "objects": [{k: v for k, v in o.items() if not k.startswith("_")} for o in objects]}
    (edir / f"{name}_gis.json").write_text(json.dumps(layer, indent=1) + "\n", encoding="utf-8")
    log(f"buildings: {len(objects)} placed ({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))}), "
        f"{skipped} left out (too close to a bigger one or over max_buildings), from {len(foot)} footprints")

    (mod / "gis").mkdir(exist_ok=True)
    (mod / "gis" / f"{name}.preview.png").write_bytes(preview(hm, paint, road_docs, foot, objects, plan))
    log(f"preview: gis/{name}.preview.png")
    return {"objects": len(objects), "roads": len(road_docs), "footprints": len(foot), "ways": counts}


# ---------------------------------------------------------------- preview

def preview(hm, paint, roads, foot, objects, plan, side=1024):
    """The map from above: shaded ground coloured by paint, footprints, roads,
    placed models' centres and the districts' squares."""
    k = side / (SIZE * SPACING)
    colours = {"sand": (214, 196, 150), "dirt": (170, 140, 100), "scrub": (190, 180, 110),
               "farm": (130, 150, 80), "green": (90, 140, 70), "urban": (175, 172, 165)}
    base = float(plan.get("base", 20.0))
    img = bytearray(side * side * 3)
    ps = SIZE + 1
    for y in range(side):
        r = min(int(y / k / SPACING), SIZE - 2)
        for x in range(side):
            c = min(int(x / k / SPACING), SIZE - 2)
            h = hm[r][c]
            if h <= base - 1:
                col = (60, 100, 150)
            else:
                col = colours["sand"]
                for l in LAYERS[1:]:
                    a = paint[l["key"]][r * ps + c] / 255
                    if a:
                        t = colours[l["key"]]
                        col = tuple(int(p * (1 - a) + q * a) for p, q in zip(col, t))
            shade = 1 + max(-0.4, min(0.4, (hm[r][c + 1] - h + hm[r + 1][c] - h) * -0.15))
            i = (y * side + x) * 3
            img[i:i + 3] = bytes(max(0, min(255, int(v * shade))) for v in col)

    def put(x, z, col):
        px, py = int((x + HALF) * k), int((z + HALF) * k)
        if 0 <= px < side and 0 <= py < side:
            i = (py * side + px) * 3
            img[i:i + 3] = bytes(col)

    def line(a, b, col):
        n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) * k) + 1
        for t in range(n + 1):
            put(a[0] + (b[0] - a[0]) * t / n, a[1] + (b[1] - a[1]) * t / n, col)

    for _, pts in foot:
        for a, b in zip(pts, pts[1:] + pts[:1]):
            line(a, b, (110, 100, 95))
    for rd in roads:
        p = rd["points"]
        for a, b in zip(p, p[1:]):
            line(a, b, (40, 40, 40))
    for o in objects:
        x, _, z = o["position"]
        for dx in (-2, 0, 2):
            put(x + dx, z, (200, 40, 40))
            put(x, z + dx, (200, 40, 40))
    for d in plan["districts"]:
        h = d.size / 2
        cs = [(d.at[0] - h, d.at[1] - h), (d.at[0] + h, d.at[1] - h), (d.at[0] + h, d.at[1] + h), (d.at[0] - h, d.at[1] + h)]
        for a, b in zip(cs, cs[1:] + cs[:1]):
            line(a, b, (255, 255, 255))
    return png.encode(side, side, bytes(img), 3)
