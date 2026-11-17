"""Real-world map data (GIS) as a starting point for a new map.

A plan file (`gis/<map>.json` in a mod) says how the real world is laid on a
game map, in one of two ways:

  "strip":     one piece of the world, bent onto the map: its coast becomes a
               near-straight edge on the map's west side, and its length is
               squeezed unevenly (towns near real scale, fields between them
               squeezed hardest).  Made for a long coastal territory such as
               the Gaza Strip; an OSM boundary relation clips it.
  "districts": squares of the real world, each turned and set down at a
               point on the map, the gaps blended.

`gis fetch` downloads what the plan needs (OpenStreetMap through the Overpass
API; elevation, with the sea floor, as Terrarium PNG tiles from the AWS open
terrain tiles) into `gis/cache/`, and `gis build` turns it into ordinary mod
files, all editable afterwards:

  terrain/<map>.heights.png (+ .heights.json)   ground from real elevation
  terrain/<map>.layers.json, .layerNN.*.png     ground paint from land use
  terrain/<map>.decals.json                     main roads as road decals
  encounters/<map>_gis.json                     buildings as game models, and
                                                the sea as a water zone
  gis/<map>.preview.png                         the map from above, to check

The map must already exist (`maps/<map>.json`, with "size" for a map other
than 512 cells a side).  Buildings are stand-ins: each real footprint becomes
the game model of its kind (see KIT) closest to it in size, turned to the
footprint, with the model's own ruin; they're written unnamed (the game holds
2048 object names in all).  OSM data is (c) OpenStreetMap contributors, ODbL:
say so wherever the map is shared.

Map coordinates: x east, z south (lower z is north), metres, the map's centre
at 0, 0.  Height sample (col, row) is at x = col * 8 - size * 4,
z = row * 8 - (size * 4 - 8), as the game places them.
"""
import json
import math
import random
import urllib.parse
import urllib.request
from pathlib import Path

from . import png

SPACING = 8.0
STEP = 0.3499999940395355
LEVELS = 16

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
    {"key": "urban", "texture": "sw_tt_concrete", "scale": 16.0},
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

# OSM highway class -> (decal texture, span in metres: the texture's width
# across the road, about half of it asphalt; retail roads use about 37);
# classes not listed are left out.
ROADS = {
    "motorway": ("sw_decal_road_straight", 34.0), "trunk": ("sw_decal_road_straight", 32.0),
    "primary": ("sw_decal_road_straight", 30.0), "secondary": ("sw_decal_road_straight", 26.0),
    "tertiary": ("sw_decal_road_straightalpha", 22.0),
}
ROAD_ORDER = ["motorway", "trunk", "primary", "secondary", "tertiary"]
# Smaller streets are painted on the ground (urban layer) at this width: as
# decals they'd fill most of a city's cells (a map holds 32767 decals; SW,
# the biggest retail map, has 27,444).
PAINTED_ROADS = {"residential": 7.0, "unclassified": 7.0, "living_street": 5.0, "service": 4.0}

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

# The sea as a water zone (properties as SW's harbour, nampowater)
WATER = {"type": "water", "numgrids": "16", "gridsize": "2", "amplitude": ".2",
         "red": "86", "green": "101", "blue": "100", "alpha": "180"}


class Grid:
    """A map's height samples: size a side, 8 m apart."""

    def __init__(self, size=512):
        self.size = int(size)
        self.half = self.size * SPACING / 2
        self.x0 = -self.half
        self.z0 = -self.half + SPACING            # the game's own offset (z + 2040 on 512-cell maps)

    def x(self, col):
        return self.x0 + col * SPACING

    def z(self, row):
        return self.z0 + row * SPACING

    def col(self, x):
        return (x - self.x0) / SPACING

    def row(self, z):
        return (z - self.z0) / SPACING


# ---------------------------------------------------------------- places

def _local(lat0):
    return 111320.0 * math.cos(math.radians(lat0)), 110574.0


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
        self.kx, self.ky = _local(self.lat)

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

    def contains_map(self, x, z):
        return self.inside(x, z)

    def bbox(self, margin=None):
        """(south, west, north, east) in degrees covering the square and its feather."""
        h = self.size / 2 + (self.feather if margin is None else margin)
        pts = [self.to_world(self.at[0] + dx, self.at[1] + dz) for dx in (-h, h) for dz in (-h, h)]
        return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))

    def chunks(self):
        return [(self.name, self.bbox(), None)]


def _interp(pts, v):
    """Piecewise-linear through [(x, y)] sorted by x, extended past the ends."""
    if v <= pts[0][0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
    elif v >= pts[-1][0]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
    else:
        i = next(i for i in range(1, len(pts)) if v <= pts[i][0])
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
    return y0 + (v - x0) * (y1 - y0) / (x1 - x0)


def point_in_polygon(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def stitch_rings(ways):
    """Closed rings from ways ([(lat, lon)]) joined end to end, as OSM
    boundary relations split them."""
    ways = [list(w) for w in ways if len(w) > 1]
    rings = []
    while ways:
        ring = ways.pop(0)
        while ring[0] != ring[-1]:
            for i, w in enumerate(ways):
                if w[0] == ring[-1]:
                    ring += w[1:]
                elif w[-1] == ring[-1]:
                    ring += w[-2::-1]
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring
                elif w[0] == ring[0]:
                    ring = w[:0:-1] + ring
                else:
                    continue
                ways.pop(i)
                break
            else:
                break                                  # can't close it: keep what joined
        rings.append(ring)
    return rings


class Strip:
    """A long coastal territory bent onto the map.

    Real points are measured along an axis ("bearing", degrees from north,
    through "origin") and across it (positive inland, to the axis's right).
    The real coast (where the ground rises above sea level) gives, for each
    distance along, the across distance of the shore: the map puts the shore
    at x = "coast_x" (plus "curve" of its real bends), and land "across"
    game metres per real metre east of it.  "along" lists [real km, map z]
    pairs (north is lower z): between them the length is squeezed linearly.
    "rotate" (degrees, clockwise) then turns that frame on the map, so a long
    strip can lie along the map's diagonal.
    """

    name = "strip"

    def __init__(self, spec):
        self.spec = spec
        self.lat, self.lon = (float(v) for v in spec["origin"])
        self.bearing = math.radians(float(spec.get("bearing", 0)))
        self.coast_x = float(spec.get("coast_x", 0))
        self.scale = float(spec.get("across", 0.5))
        self.curve = float(spec.get("curve", 0.25))
        self.rot = math.radians(float(spec.get("rotate", 0)))
        self.rc, self.rs = math.cos(self.rot), math.sin(self.rot)
        self.along = sorted((float(a) * 1000, float(z)) for a, z in spec["along"])
        if any(b[1] >= a[1] for a, b in zip(self.along, self.along[1:])):
            raise ValueError("strip \"along\": map z must fall (go north) as the real distance along grows")
        self.along_inv = sorted((z, a) for a, z in self.along)
        self.kx, self.ky = _local(self.lat)
        self.relation = spec.get("relation")
        self.raise_m = 0.0
        self.coast = None            # [(a, across of the shore)], from prepare()
        self.coast_ref = 0.0
        self.outline = None          # the territory in map coordinates
        self.outline_world = None    # and in (along, across) metres

    # real metres <-> along / across
    def _ac(self, lat, lon):
        e, n = (lon - self.lon) * self.kx, (lat - self.lat) * self.ky
        sb, cb = math.sin(self.bearing), math.cos(self.bearing)
        return e * sb + n * cb, e * cb - n * sb

    def _latlon(self, a, c):
        sb, cb = math.sin(self.bearing), math.cos(self.bearing)
        e, n = a * sb + c * cb, a * cb - c * sb
        return self.lat + n / self.ky, self.lon + e / self.kx

    def _shore(self, a):
        """Across distance of the shore at a distance along (coast sampled every 100 m)."""
        if not self.coast:
            return 0.0
        f = (a - self.coast[0][0]) / 100.0
        i = int(min(max(f, 0), len(self.coast) - 2))
        t = min(max(f - i, 0.0), 1.0)
        return self.coast[i][1] * (1 - t) + self.coast[i + 1][1] * t

    # the strip's own frame (coast along x = coast_x, north up) <-> the map
    def unframe(self, fx, fz):
        return fx * self.rc - fz * self.rs, fx * self.rs + fz * self.rc

    def frame(self, x, z):
        return x * self.rc + z * self.rs, -x * self.rs + z * self.rc

    def to_map(self, lat, lon):
        a, c = self._ac(lat, lon)
        shore = self._shore(a)
        x = self.coast_x + self.scale * (c - shore) + self.scale * self.curve * (shore - self.coast_ref)
        return self.unframe(x, _interp(self.along, a))

    def map_ac(self, x, z):
        """A map point's real distances along and across."""
        x, z = self.frame(x, z)
        a = _interp(self.along_inv, z)
        shore = self._shore(a)
        return a, (x - self.coast_x - self.scale * self.curve * (shore - self.coast_ref)) / self.scale + shore

    def to_world(self, x, z):
        return self._latlon(*self.map_ac(x, z))

    def weight(self, x, z):
        return 1.0

    def shore_x(self, z):
        """Frame x of the shore at a frame z: the sea is west of it."""
        shore = self._shore(_interp(self.along_inv, z))
        return self.coast_x + self.scale * self.curve * (shore - self.coast_ref)

    def is_sea(self, x, z):
        fx, fz = self.frame(x, z)
        return fx < self.shore_x(fz)

    def contains_map(self, x, z):
        return self.outline is None or point_in_polygon(x, z, self.outline)

    def contains_world(self, lat, lon):
        if self.outline_world is None:
            return True
        return point_in_polygon(*self._ac(lat, lon), self.outline_world)

    def prepare(self, elevation, boundary=None):
        """Find the shore along the strip from the elevation, and set the
        territory's outline from its boundary rings ([(lat, lon)])."""
        a0, a1 = self.along[0][0] - 3000, self.along[-1][0] + 3000
        raw = []
        a = a0
        while a <= a1:
            shore = None
            c = -15000.0
            while c < 8000:
                e = elevation.at(*self._latlon(a, c))
                if e is not None and e > 0.5:
                    ahead = [elevation.at(*self._latlon(a, c + d)) for d in (100, 200, 300)]
                    if all(v is not None and v > 0.0 for v in ahead):
                        shore = c
                        break
                c += 50.0
            raw.append((a, shore))
            a += 100.0
        known = [(a, c) for a, c in raw if c is not None]
        if len(known) < 2:
            raise ValueError("strip: found no coast in the elevation data (fetch it first)")
        # fill gaps, then smooth over about a kilometre
        filled = [(a, c if c is not None else _interp(known, a)) for a, c in raw]
        sm = []
        for i, (a, _) in enumerate(filled):
            win = [c for _, c in filled[max(0, i - 5):i + 6]]
            sm.append((a, sum(win) / len(win)))
        self.coast = sm
        self.coast_ref = self._shore(0.0)
        if boundary:
            ring = max(boundary, key=len)
            self.outline_world = [self._ac(lat, lon) for lat, lon in ring]
            self.outline = [self.to_map(lat, lon) for lat, lon in ring]

    def bbox(self, margin_deg=0.05):
        if self.outline_world:
            pts = [self._latlon(a, c) for a, c in self.outline_world]
        else:
            pts = [self._latlon(a, c) for a, _ in self.along for c in (-12000, 8000)]
        return (min(p[0] for p in pts) - margin_deg, min(p[1] for p in pts) - margin_deg,
                max(p[0] for p in pts) + margin_deg, max(p[1] for p in pts) + margin_deg)

    def chunks(self, n=None):
        """Bands of the territory to download one at a time."""
        s, w, nn, e = self.bbox(0.0)
        n = n or int(self.spec.get("chunks", 6))
        step = (nn - s) / n
        # Overpass's own area filter makes each query much slower; the build
        # clips to the boundary itself, so it's off unless "area_filter"
        area = 3600000000 + int(self.relation) if self.relation and self.spec.get("area_filter") else None
        return [(f"strip_{i}", (s + i * step, w, s + (i + 1) * step, e), area) for i in range(n)]


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if not plan.get("map"):
        raise ValueError(f"{path}: needs \"map\" (the new map's name)")
    plan["grid"] = Grid(plan.get("size", 512))
    if plan.get("strip"):
        plan["places"] = [Strip(plan["strip"])]
        plan["districts"] = []
    else:
        plan["districts"] = [District(d) for d in plan.get("districts", [])]
        plan["places"] = plan["districts"]
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
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or n == tries - 1:
                raise
            time.sleep(min(600, 30 * 2 ** n))         # back off: 30 s, 1, 2, 4 min ...


def overpass_query(bbox, area=None):
    s, w, n, e = bbox
    b = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    if area:
        b = f"(area.a){b}"
    head = f"[out:json][timeout:600][maxsize:1073741824];" + (f"area(id:{area})->.a;" if area else "")
    return (f"{head}("
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


def read_boundary(path):
    """Outer rings ([(lat, lon)]) of an Overpass relation answer."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    ways = []
    for el in doc.get("elements", []):
        for m in el.get("members", []):
            if m.get("role") == "outer" and m.get("geometry"):
                ways.append([(p["lat"], p["lon"]) for p in m["geometry"]])
    return stitch_rings(ways)


def _fetch_bbox(bbox, area, get, log, name, depth=0, parts=None, key=""):
    """An Overpass answer for a box; a box the server times out on is split
    into quarters (up to three times) and the answers merged.  With `parts`
    (a folder), each answer is kept there, so a later try fetches only what's
    still missing."""
    import urllib.error
    part = Path(parts) / f"{name}{key}.json" if parts else None
    if part is not None and part.exists():
        return json.loads(part.read_text(encoding="utf-8"))
    body = urllib.parse.urlencode({"data": overpass_query(bbox, area)}).encode()
    if get is _get:
        import time
        time.sleep(5)                                 # go easy on the public server
    try:
        doc = json.loads(get(OVERPASS, body, tries=4) if get is _get else get(OVERPASS, body))
        if part is not None:
            part.parent.mkdir(parents=True, exist_ok=True)
            part.write_text(json.dumps(doc), encoding="utf-8")
        return doc
    except urllib.error.HTTPError as e:
        if e.code not in (429, 502, 503, 504) or depth >= 3:
            raise
    s, w, n, e = bbox
    ms, mw = (s + n) / 2, (w + e) / 2
    log(f"{name}: the server timed out; fetching it in quarters")
    seen, elements = set(), []
    for i, q in enumerate(((s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e))):
        for el in _fetch_bbox(q, area, get, log, name, depth + 1, parts, f"{key}_{i}").get("elements", []):
            k = (el.get("type"), el.get("id"))
            if k not in seen:
                seen.add(k)
                elements.append(el)
    return {"elements": elements}


def fetch(plan, cache, only=None, log=print, get=_get):
    """Download OSM data and elevation tiles the plan needs (files already in
    the cache are kept)."""
    cache = Path(cache)
    (cache / "osm").mkdir(parents=True, exist_ok=True)
    (cache / "dem").mkdir(parents=True, exist_ok=True)
    zoom = int(plan.get("zoom", ZOOM))
    for place in plan["places"]:
        if isinstance(place, Strip) and place.relation:
            f = cache / "osm" / "boundary.json"
            if not f.exists():
                q = f"[out:json][timeout:120];relation({int(place.relation)});out geom;"
                f.write_bytes(get(OVERPASS, urllib.parse.urlencode({"data": q}).encode()))
                log("boundary downloaded")
            place.outline_world = None
            rings = read_boundary(f)
            ring = max(rings, key=len)
            place.outline_world = [place._ac(lat, lon) for lat, lon in ring]
        for name, bbox, area in place.chunks():
            if only and name not in only and place.name not in only:
                continue
            f = cache / "osm" / f"{name}.json"
            if f.exists():
                log(f"{name}: OSM data already in the cache")
            else:
                doc = _fetch_bbox(bbox, area, get, log, name, parts=cache / "osm" / "parts")
                data = json.dumps(doc).encode()
                f.write_bytes(data)
                log(f"{name}: OSM data, {len(data) / 1e6:.1f} MB")
        got = 0
        for z, x, y in tiles_for(place.bbox(), zoom):
            t = cache / "dem" / f"{z}_{x}_{y}.png"
            if not t.exists():
                t.write_bytes(get(TERRARIUM.format(z=z, x=x, y=y)))
                got += 1
        log(f"{place.name}: {got} elevation tiles downloaded")
    if plan.get("landcover") or plan.get("satellite"):
        # where the map's corners lie in the world needs the strip's shore
        elev = Elevation(cache / "dem", zoom)
        for place in plan["places"]:
            if isinstance(place, Strip):
                bf = cache / "osm" / "boundary.json"
                place.prepare(elev, read_boundary(bf) if bf.exists() else None)
        if plan.get("landcover") and (not only or "landcover" in only):
            fetch_landcover(plan, cache, log)
        if plan.get("satellite") and (not only or "satellite" in only):
            fetch_satellite(plan, cache, log)


# ---------------------------------------------------------------- land cover and imagery
#
# Both come as cloud-optimised GeoTIFFs (tiled, zlib-compressed), so only the
# tiles over the plan's area are downloaded, by HTTP range requests:
#   - ESA WorldCover 2021 v200 (10 m land cover classes, CC BY 4.0,
#     (c) ESA WorldCover project 2021 / contains modified Copernicus
#     Sentinel data), on a latitude/longitude grid;
#   - a Sentinel-2 L2A true-colour image (TCI, 10 m, Copernicus Sentinel
#     data, free and open), in its UTM zone, from the AWS open data archive.
# The window over the plan is cached as zlib-compressed raw pixels with a
# JSON note of where it lies.

WORLDCOVER = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
SENTINEL_COG = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/{zone}/{band}/{square}/{year}/{month}/{item}/TCI.tif"
# WorldCover classes -> ground layer (None: leave it to the rest of the build)
LANDCOVER = {10: "green", 20: "scrub", 30: "scrub", 40: "farm", 50: "dirt", 60: "sand", 80: None, 90: "green",
             95: "green", 100: "scrub"}


def _get_range(url, start, end, tries=5):
    """Bytes start..end (inclusive) of a remote file."""
    import time
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


class CogFile:
    """A remote tiled GeoTIFF (8-bit samples, zlib, optional horizontal
    predictor): its size, tiles and georeferencing, from its header."""

    def __init__(self, url, get_range=_get_range):
        import struct
        self.url, self.get_range = url, get_range
        head = get_range(url, 0, 262143)
        bo = "<" if head[:2] == b"II" else ">"
        if struct.unpack_from(bo + "H", head, 2)[0] != 42:
            raise ValueError(f"{url}: not a classic TIFF")
        off = struct.unpack_from(bo + "I", head, 4)[0]
        n = struct.unpack_from(bo + "H", head, off)[0]
        tags = {}
        for i in range(n):
            tag, typ, cnt, val = struct.unpack_from(bo + "HHII", head, off + 2 + i * 12)
            tags[tag] = (typ, cnt, val, head[off + 2 + i * 12 + 8:off + 2 + i * 12 + 12])
        size = {1: 1, 2: 1, 3: 2, 4: 4, 12: 8}

        def values(tag):
            typ, cnt, val, raw = tags[tag]
            fmt = {1: "B", 2: "c", 3: "H", 4: "I", 12: "d"}[typ]
            if size[typ] * cnt <= 4:
                return list(struct.unpack_from(bo + fmt * cnt, raw, 0))
            return list(struct.unpack_from(bo + fmt * cnt, head, val))
        self.width, self.height = values(256)[0], values(257)[0]
        self.samples = values(277)[0] if 277 in tags else 1
        if values(259)[0] != 8:
            raise ValueError(f"{url}: compression {values(259)[0]}, not zlib")
        self.predictor = values(317)[0] if 317 in tags else 1
        self.tw, self.th = values(322)[0], values(323)[0]
        self.offsets, self.counts = values(324), values(325)
        self.scale = values(33550)
        self.tie = values(33922)
        self.across = (self.width + self.tw - 1) // self.tw

    def window(self, x0, y0, x1, y1, log=print):
        """Pixels x0..x1-1, y0..y1-1 as bytes (rows of samples)."""
        import itertools
        import zlib
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.width, x1), min(self.height, y1)
        w, h, s = x1 - x0, y1 - y0, self.samples
        out = bytearray(w * h * s)
        tiles = [(tx, ty) for ty in range(y0 // self.th, (y1 - 1) // self.th + 1)
                 for tx in range(x0 // self.tw, (x1 - 1) // self.tw + 1)]
        for k, (tx, ty) in enumerate(tiles):
            i = ty * self.across + tx
            raw = zlib.decompress(self.get_range(self.url, self.offsets[i], self.offsets[i] + self.counts[i] - 1))
            row_len = self.tw * s
            for r in range(self.th):
                y = ty * self.th + r
                if not y0 <= y < y1:
                    continue
                row = raw[r * row_len:(r + 1) * row_len]
                if self.predictor == 2:          # horizontal differencing, per sample
                    rr = bytearray(row_len)
                    for c in range(s):
                        rr[c::s] = bytes(v & 255 for v in itertools.accumulate(row[c::s]))
                    row = rr
                a = max(x0, tx * self.tw)
                b = min(x1, (tx + 1) * self.tw)
                if a >= b:
                    continue
                src = (a - tx * self.tw) * s
                dst = ((y - y0) * w + (a - x0)) * s
                out[dst:dst + (b - a) * s] = row[src:src + (b - a) * s]
            log(f"  tile {k + 1}/{len(tiles)}")
        return w, h, bytes(out)


def utm_from_latlon(lat, lon, zone):
    """(easting, northing) in metres, WGS 84, a northern-hemisphere UTM zone."""
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    k0 = 0.9996
    phi, lam = math.radians(lat), math.radians(lon)
    lam0 = math.radians((zone - 1) * 6 - 180 + 3)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    aa = math.cos(phi) * (lam - lam0)
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    x = k0 * n * (aa + (1 - t + c) * aa ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * aa ** 5 / 120) + 500000.0
    y = k0 * (m + n * math.tan(phi) * (aa * aa / 2 + (5 - t + 9 * c + 4 * c * c) * aa ** 4 / 24
                                       + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * aa ** 6 / 720))
    return x, y


class Raster:
    """A cached window of a GeoTIFF: value(s) at a latitude/longitude.
    meta: {"crs": "latlon" | "utm<zone>", "x0", "y0" (the window's top-left
    corner), "dx", "dy" (pixel size), "w", "h", "samples"}."""

    def __init__(self, folder, name):
        import zlib
        folder = Path(folder)
        self.meta = json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
        self.data = zlib.decompress((folder / f"{name}.bin").read_bytes())
        m = self.meta
        self.w, self.h, self.s = m["w"], m["h"], m["samples"]
        self.zone = int(m["crs"][3:]) if m["crs"].startswith("utm") else None

    def pixel(self, lat, lon):
        m = self.meta
        if self.zone:
            x, y = utm_from_latlon(lat, lon, self.zone)
        else:
            x, y = lon, lat
        return (x - m["x0"]) / m["dx"], (m["y0"] - y) / m["dy"]

    def at(self, lat, lon):
        """The nearest pixel's samples (a tuple), or None outside the window."""
        px, py = self.pixel(lat, lon)
        c, r = int(px), int(py)
        if not (0 <= c < self.w and 0 <= r < self.h):
            return None
        i = (r * self.w + c) * self.s
        return tuple(self.data[i:i + self.s])

    def around(self, lat, lon, radius):
        """Samples of the pixels within radius (in pixels) of a point, as a list."""
        px, py = self.pixel(lat, lon)
        out = []
        for r in range(int(py - radius), int(py + radius) + 1):
            if not 0 <= r < self.h:
                continue
            for c in range(int(px - radius), int(px + radius) + 1):
                if 0 <= c < self.w:
                    i = (r * self.w + c) * self.s
                    out.append(tuple(self.data[i:i + self.s]))
        return out


def _save_raster(folder, name, meta, data):
    import zlib
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.bin").write_bytes(zlib.compress(data, 6))
    (folder / f"{name}.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")


def fetch_landcover(plan, cache, log=print, cog=CogFile):
    """The plan's area from ESA WorldCover 2021 (one 3 x 3 degree tile), as
    cache/landcover/worldcover.*."""
    s, w, n, e = _plan_bbox(plan)
    out = Path(cache) / "landcover"
    if (out / "worldcover.json").exists():
        log("land cover: already in the cache")
        return
    lat0, lon0 = 3 * math.floor(s / 3), 3 * math.floor(w / 3)
    if 3 * math.floor(n / 3) != lat0 or 3 * math.floor(e / 3) != lon0:
        raise ValueError("land cover: the plan's area crosses WorldCover tiles; that isn't supported yet")
    tile = f"{'N' if lat0 >= 0 else 'S'}{abs(lat0):02d}{'E' if lon0 >= 0 else 'W'}{abs(lon0):03d}"
    f = cog(WORLDCOVER.format(tile=tile))
    dx, dy = f.scale[0], f.scale[1]
    left, top = f.tie[3], f.tie[4]
    x0, x1 = int((w - left) / dx), int(math.ceil((e - left) / dx))
    y0, y1 = int((top - n) / dy), int(math.ceil((top - s) / dy))
    log(f"land cover: WorldCover {tile}, {x1 - x0} x {y1 - y0} pixels")
    ww, hh, data = f.window(x0, y0, x1, y1, log=log)
    _save_raster(out, "worldcover", {"source": f.url, "crs": "latlon", "x0": left + max(0, x0) * dx,
                                     "y0": top - max(0, y0) * dy, "dx": dx, "dy": dy, "w": ww, "h": hh,
                                     "samples": 1}, data)


def fetch_satellite(plan, cache, log=print, cog=CogFile):
    """The plan's area from a Sentinel-2 L2A true-colour image (plan
    "satellite": {"item": "S2B_36RXV_20220729_0_L2A"}), as cache/satellite/tci.*."""
    item = str(plan["satellite"]["item"])
    out = Path(cache) / "satellite"
    if (out / "tci.json").exists():
        log("satellite image: already in the cache")
        return
    parts = item.split("_")
    tile, date = parts[1], parts[2]
    zone, band, square = int(tile[:2]), tile[2], tile[3:]
    url = SENTINEL_COG.format(zone=zone, band=band, square=square, year=int(date[:4]), month=int(date[4:6]), item=item)
    f = cog(url)
    s, w, n, e = _plan_bbox(plan)
    corners = [utm_from_latlon(la, lo, zone) for la in (s, n) for lo in (w, e)]
    left, top, dx, dy = f.tie[3], f.tie[4], f.scale[0], f.scale[1]
    x0 = int((min(c[0] for c in corners) - left) / dx)
    x1 = int(math.ceil((max(c[0] for c in corners) - left) / dx))
    y0 = int((top - max(c[1] for c in corners)) / dy)
    y1 = int(math.ceil((top - min(c[1] for c in corners)) / dy))
    log(f"satellite image: {item}, {x1 - x0} x {y1 - y0} pixels")
    ww, hh, data = f.window(x0, y0, x1, y1, log=log)
    _save_raster(out, "tci", {"source": url, "crs": f"utm{zone}", "x0": left + max(0, x0) * dx,
                              "y0": top - max(0, y0) * dy, "dx": dx, "dy": dy, "w": ww, "h": hh,
                              "samples": f.samples}, data)


def satellite_tint(plan, image, sea, log=print, radius=2, keep=0.5, low=0.7, high=1.3):
    """Ground colour from a satellite image as a tint, one RGB pixel per
    height sample (rows north to south), 128 the map's average colour.

    Each sample takes the image's mean over (2 * radius + 1)^2 pixels (about
    50 m), divided by the mean over all land, with only "keep" of its colour
    (the rest grey at its own brightness), held to low..high and blurred once.
    The image's own sunlight and roof shadows so come through only as broad
    colour; the lighting bake does the shading. The sea is neutral."""
    g = plan["grid"]
    n = g.size
    place = plan["places"][0]
    raw = [[None] * n for _ in range(n)]
    tot, cnt = [0.0, 0.0, 0.0], 0
    for r in range(n):
        z = g.z(r)
        for c in range(n):
            if sea[r][c]:
                continue
            px = image.around(*place.to_world(g.x(c), z), radius)
            if not px:
                continue
            m = [sum(p[k] for p in px) / len(px) for k in range(3)]
            raw[r][c] = m
            for k in range(3):
                tot[k] += m[k]
            cnt += 1
    if not cnt:
        return bytes([128] * (n * n * 3))
    mean = [t / cnt for t in tot]
    out = [[(1.0, 1.0, 1.0)] * n for _ in range(n)]
    for r in range(n):
        for c in range(n):
            m = raw[r][c]
            if m is None:
                continue
            ratio = [m[k] / max(mean[k], 1.0) for k in range(3)]
            grey = sum(ratio) / 3
            out[r][c] = tuple(min(high, max(low, grey + keep * (ratio[k] - grey))) for k in range(3))
    blurred = bytearray(n * n * 3)
    for r in range(n):
        for c in range(n):
            acc, k_ = [0.0, 0.0, 0.0], 0
            for rr in (r - 1, r, r + 1):
                for cc in (c - 1, c, c + 1):
                    if 0 <= rr < n and 0 <= cc < n:
                        v = out[rr][cc]
                        acc = [acc[i] + v[i] for i in range(3)]
                        k_ += 1
            i = (r * n + c) * 3
            blurred[i:i + 3] = bytes(max(0, min(255, int(round(128 * a / k_)))) for a in acc)
    log(f"tint: the map's mean ground colour in the image is {tuple(round(v) for v in mean)}")
    return bytes(blurred)


def _plan_bbox(plan, margin=0.02):
    """(south, west, north, east) over the map's whole area, from its corners."""
    g = plan["grid"]
    pts = [p.to_world(x, z) for p in plan["places"] for x in (g.x(0), g.x(g.size - 1))
           for z in (g.z(0), g.z(g.size - 1))]
    pts += [p.to_world(0, 0) for p in plan["places"]]
    return (min(p[0] for p in pts) - margin, min(p[1] for p in pts) - margin,
            max(p[0] for p in pts) + margin, max(p[1] for p in pts) + margin)


# ---------------------------------------------------------------- elevation

class Elevation:
    """Terrarium tiles from the cache: metres above sea level at a point
    (below 0 at sea: the sea floor)."""

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


def heights(plan, elevation):
    """Ground height in metres per sample, heights[row][col], and which
    samples are sea (open water reaching the map's edge).

    Land: the plan's "base" plus real elevation times "vertical" (and a
    district's "raise").  Sea floor: "base" minus "sea_drop", plus the real
    depth times "sea_vertical", no deeper than "sea_floor_min" below base.
    Between districts, their edges' heights blend; elsewhere "base" (or sea
    west of "sea_x")."""
    g = plan["grid"]
    base = float(plan.get("base", 20.0))
    vert = float(plan.get("vertical", 1.0))
    sea_drop = float(plan.get("sea_drop", 1.5))
    sea_vert = float(plan.get("sea_vertical", 0.5))
    sea_min = float(plan.get("sea_floor_min", 12.0))
    sea_x = plan.get("sea_x")
    # land above "knee" metres rises only "above" as fast (hills beyond the
    # strip would otherwise tower over it)
    knee = float(plan.get("knee", 1e9))
    above = float(plan.get("above", 1.0))
    places = plan["places"]
    n = g.size
    raw = []            # real elevation per sample (None: no data)
    for r in range(n):
        z = g.z(r)
        row = []
        for c in range(n):
            x = g.x(c)
            tot, acc = 0.0, 0.0
            for d in places:
                w = d.weight(x, z)
                if w <= 0:
                    continue
                e = elevation.at(*d.to_world(x, z))
                if e is None:
                    continue
                tot += w
                acc += w * (e + d.raise_m / max(vert, 1e-6))
            if tot >= 1:
                row.append(acc / tot)
            elif tot > 0:
                row.append((acc, tot))
            else:
                row.append(None)
        raw.append(row)
    # smooth the real elevation: surface models carry roofs and trees, and a
    # squeezed place makes their bumps steep ("smooth": passes of a 3 x 3 blur)
    for _ in range(int(plan.get("smooth", 0))):
        nxt = [row[:] for row in raw]
        for r in range(1, n - 1):
            for c in range(1, n - 1):
                if not isinstance(raw[r][c], float):
                    continue
                vals = [raw[rr][cc] for rr in (r - 1, r, r + 1) for cc in (c - 1, c, c + 1)]
                if all(isinstance(v, float) for v in vals):
                    nxt[r][c] = (sum(vals) + 3 * raw[r][c]) / 12.0
        raw = nxt
    # sea: a strip's sea is west of its shore line; otherwise below 0 m and
    # joined to the map's west edge (dips inland stay land)
    shore_places = [d for d in places if hasattr(d, "shore_x")]
    if shore_places:
        sea = [[False] * n for _ in range(n)]
        is_sea = shore_places[0].is_sea
        for r in range(n):
            z = g.z(r)
            for c in range(n):
                sea[r][c] = is_sea(g.x(c), z)
        stack = []
    else:
        sea = None
    if sea is None:
        sea = [[False] * n for _ in range(n)]
        stack = [(r, 0) for r in range(n)]
    def low(v):
        if isinstance(v, tuple):                  # blended at a district's edge
            return v[0] / v[1] <= 0.0
        return isinstance(v, float) and v <= 0.0
    while stack:
        r, c = stack.pop()
        if sea[r][c]:
            continue
        v = raw[r][c]
        if not (low(v) or (v is None and sea_x is not None and g.x(c) < float(sea_x))):
            continue
        sea[r][c] = True
        for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= rr < n and 0 <= cc < n and not sea[rr][cc]:
                stack.append((rr, cc))

    # a clean shore: near it, each sample follows most of its 5 x 5
    # neighbourhood (coarse elevation flickers above and below 0 m there)
    for _ in range(0 if shore_places else 2):
        nxt = [row[:] for row in sea]
        for r in range(2, n - 2):
            row = sea[r]
            if not row[0]:
                continue
            first = next((c for c in range(n) if not row[c]), n)
            for c in range(max(2, first - 24), min(n - 2, first + 24)):
                k = sum(sea[rr][cc] for rr in range(r - 2, r + 3) for cc in range(c - 2, c + 3))
                nxt[r][c] = k >= 13
        sea = nxt

    def h_of(e, is_sea):
        if is_sea:
            return base - min(sea_min, sea_drop - min(e, 0.0) * sea_vert)
        e = max(e, 0.05)
        if e > knee:
            e = knee + (e - knee) * above
        return base + e * vert

    out = []
    for r in range(n):
        z = g.z(r)
        row = []
        for c in range(n):
            v = raw[r][c]
            if isinstance(v, float):
                row.append(h_of(v, sea[r][c]))
                continue
            x = g.x(c)
            # between districts: their edges' heights, nearer ones counting more
            fill = None
            if sea[r][c]:
                fill = h_of(-1.0, True)
            else:
                ft, fa = 0.0, 0.0
                for d in plan["districts"]:
                    h = d.size / 2
                    ex = min(max(x, d.at[0] - h), d.at[0] + h)
                    ez = min(max(z, d.at[1] - h), d.at[1] + h)
                    dist = math.hypot(x - ex, z - ez)
                    if dist <= 0 or dist > 600:
                        continue
                    e = elevation.at(*d.to_world(ex, ez))
                    if e is None:
                        continue
                    w = 1 / (1 + (dist / 40) ** 2)
                    ft += w
                    fa += w * h_of(e + d.raise_m / max(vert, 1e-6), e <= 0)
                fill = fa / ft if ft else base
            if isinstance(v, tuple):
                acc, tot = v
                row.append(h_of(acc / tot, sea[r][c]) * tot + fill * (1 - tot))
            else:
                row.append(fill)
        out.append(row)
    # a strip's sea floor also falls away from its shore ("sea_slope" metres
    # down per metre out, to "sea_floor_min" below base): coarse elevation
    # data often reads 0 m for the sea near a coast, and a flat floor 2 or
    # 3 m down shows through the water as sand
    slope = float(plan.get("sea_slope", 0.0))
    if shore_places and slope > 0:
        strip = shore_places[0]
        for r in range(n):
            z = g.z(r)
            for c in range(n):
                if sea[r][c]:
                    fx, fz = strip.frame(g.x(c), z)
                    d = max(0.0, strip.shore_x(fz) - fx)
                    out[r][c] = min(out[r][c], base - min(sea_min, sea_drop + d * slope))
    return out, sea


def heights_png(hm):
    """16-bit heights PNG and its .heights.json metadata."""
    n = len(hm)
    units = [[h / STEP for h in row] for row in hm]
    lo = math.floor(min(min(r) for r in units))
    vals = [max(0, min(65535, int(round((u - lo) * LEVELS)))) for row in units for u in row]
    meta = {"size": n, "spacing_m": SPACING, "step_m": STEP, "base_units": lo, "levels_per_step": LEVELS}
    return png.encode_grey16(n, n, vals), meta


class Ground:
    """Bilinear height lookups in a heights grid."""

    def __init__(self, hm, grid):
        self.hm, self.g = hm, grid

    def at(self, x, z):
        n = self.g.size
        fx = min(max(self.g.col(x), 0), n - 1.001)
        fz = min(max(self.g.row(z), 0), n - 1.001)
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


def fill_polygon(grid, side, pts, value, cell, ox, oz=None, force=False):
    """Set grid cells (side x side, row-major) whose centres fall in a polygon
    of map points to max(current, value) (to value when force). Pixel (c, r)
    is at (ox + c * cell, oz + r * cell)."""
    oz = ox if oz is None else oz
    zs = [z for _, z in pts]
    r0 = max(0, int(math.ceil((min(zs) - oz) / cell)))
    r1 = min(side - 1, int(math.floor((max(zs) - oz) / cell)))
    n = len(pts)
    for r in range(r0, r1 + 1):
        z = oz + r * cell
        xs = []
        for i in range(n):
            (xa, za), (xb, zb) = pts[i], pts[(i + 1) % n]
            if (za <= z < zb) or (zb <= z < za):
                xs.append(xa + (z - za) * (xb - xa) / (zb - za))
        xs.sort()
        for a, b in zip(xs[0::2], xs[1::2]):
            c0 = max(0, int(math.ceil((a - ox) / cell)))
            c1 = min(side - 1, int(math.floor((b - ox) / cell)))
            for c in range(c0, c1 + 1):
                if force or grid[r * side + c] < value:
                    grid[r * side + c] = value


def paint_line(grid, side, pts, width, value, cell, ox, oz=None):
    """Set grid pixels within width/2 (at least half a cell) of a polyline to max(current, value)."""
    oz = ox if oz is None else oz
    r = max(width / 2, cell / 2)
    for (xa, za), (xb, zb) in zip(pts, pts[1:]):
        c0 = max(0, int((min(xa, xb) - r - ox) / cell))
        c1 = min(side - 1, int(math.ceil((max(xa, xb) + r - ox) / cell)))
        r0 = max(0, int((min(za, zb) - r - oz) / cell))
        r1 = min(side - 1, int(math.ceil((max(za, zb) + r - oz) / cell)))
        dx, dz = xb - xa, zb - za
        ll = dx * dx + dz * dz or 1e-9
        for row in range(r0, r1 + 1):
            z = oz + row * cell
            for c in range(c0, c1 + 1):
                x = ox + c * cell
                t = max(0.0, min(1.0, ((x - xa) * dx + (z - za) * dz) / ll))
                if math.hypot(x - xa - t * dx, z - za - t * dz) <= r and grid[row * side + c] < value:
                    grid[row * side + c] = value


def clip_line(pts, inside):
    """Pieces of a polyline (map points) where inside(x, z) holds."""
    out, cur = [], []
    for p in pts:
        if inside(*p):
            cur.append(p)
        elif cur:
            if len(cur) > 1:
                out.append(cur)
            cur = []
    if len(cur) > 1:
        out.append(cur)
    return out


def drop_doubled_roads(roads, share=0.6):
    """Leave out roads lying mostly on another's asphalt (OSM draws dual
    carriageways and links as separate ways; squeezed, they overlap and
    their decals alternate cell by cell).  Bigger classes and longer roads
    are kept first.  roads: [(class, [(x, z)])]."""
    def length(p):
        return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(p, p[1:]))
    order = sorted(roads, key=lambda t: (ROAD_ORDER.index(t[0]) if t[0] in ROAD_ORDER else 99, -length(t[1])))
    cell = 32.0
    grid = {}                   # 32 m cells -> segments of kept roads (ax, az, bx, bz, half width)
    kept = []

    def near(x, z):
        i, j = int(x // cell), int(z // cell)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for ax, az, bx, bz, hw in grid.get((i + di, j + dj), ()):
                    dx, dz = bx - ax, bz - az
                    ll = dx * dx + dz * dz or 1e-9
                    t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / ll))
                    if math.hypot(x - ax - t * dx, z - az - t * dz) <= hw:
                        return True
        return False
    for k, pts in order:
        samples = []
        for (ax, az), (bx, bz) in zip(pts, pts[1:]):
            n = max(1, int(math.hypot(bx - ax, bz - az) / 4))
            samples += [(ax + (bx - ax) * i / n, az + (bz - az) * i / n) for i in range(n)]
        samples.append(pts[-1])
        if samples and sum(near(x, z) for x, z in samples) >= share * len(samples):
            continue
        kept.append((k, pts))
        hw = ROADS[k][1] / 4 + 2.0 if k in ROADS else 6.0      # its asphalt, and a little
        for (ax, az), (bx, bz) in zip(pts, pts[1:]):
            for i in range(int(min(ax, bx) // cell), int(max(ax, bx) // cell) + 1):
                for j in range(int(min(az, bz) // cell), int(max(az, bz) // cell) + 1):
                    grid.setdefault((i, j), []).append((ax, az, bx, bz, hw))
    return kept


def join_roads(roads, tol=1.0, max_turn=50.0):
    """OSM splits a road into many ways: join pieces of a class that meet
    end to end (where exactly two ends meet: not at junctions, and turning
    less than max_turn degrees: a sharp bend's outer cells show the
    texture curled), so the road's texture and markings run on unbroken.
    roads: [(class, [(x, z)])]."""
    def key(pt):
        return round(pt[0] / tol), round(pt[1] / tol)

    def turn(a, b, c):
        """Degrees the way turns at b, coming from a, going to c."""
        h1 = math.atan2(b[1] - a[1], b[0] - a[0])
        h2 = math.atan2(c[1] - b[1], c[0] - b[0])
        return abs((math.degrees(h2 - h1) + 180) % 360 - 180)
    out = []
    for k in dict.fromkeys(c for c, _ in roads):
        pieces = [list(p) for c, p in roads if c == k and len(p) > 1]
        ends = {}
        for i, p in enumerate(pieces):
            ends.setdefault(key(p[0]), []).append(i)
            ends.setdefault(key(p[-1]), []).append(i)
        used = [False] * len(pieces)

        def next_piece(tip):
            at = ends.get(key(tip), [])
            if len(at) != 2:
                return None
            free = [o for o in at if not used[o]]
            return free[0] if free else None
        for i in range(len(pieces)):
            if used[i]:
                continue
            used[i] = True
            line = pieces[i][:]
            while True:                          # grow at the end
                o = next_piece(line[-1])
                if o is None:
                    break
                p = pieces[o] if key(pieces[o][0]) == key(line[-1]) else pieces[o][::-1]
                if len(line) < 2 or turn(line[-2], line[-1], p[1]) > max_turn:
                    break
                used[o] = True
                line += p[1:]
            while True:                          # and at the start
                o = next_piece(line[0])
                if o is None:
                    break
                p = pieces[o] if key(pieces[o][-1]) == key(line[0]) else pieces[o][::-1]
                if len(line) < 2 or turn(p[-2], line[0], line[1]) > max_turn:
                    break
                used[o] = True
                line = p[:-1] + line
            out.append((k, line))
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
    """The kit model of a kind closest to a footprint: (model, quarter turn,
    long side, short side), quarter turn True when the model's long side is its z."""
    best = None
    for model, mx, mz, _ in kit[kind]:
        turned = mz > mx
        ml, mw = (mz, mx) if turned else (mx, mz)
        score = abs(math.log(ml / max(length, 1))) + abs(math.log(mw / max(width, 1)))
        if best is None or score < best[0]:
            best = (score, model, turned, ml, mw)
    return best[1], best[2], best[3], best[4]


def footprint(place, tags, geo):
    """A building as (area, tags, map x, map z, length, width, map angle,
    map outline): its size from the real footprint, its centre and heading
    as the place lays it on the map (a squeezed place turns it with the
    streets)."""
    if len(geo) > 2 and geo[0] == geo[-1]:
        geo = geo[:-1]
    if len(geo) < 3:
        return None
    lat0 = sum(p[0] for p in geo) / len(geo)
    lon0 = sum(p[1] for p in geo) / len(geo)
    kx, ky = _local(lat0)
    real = [((lon - lon0) * kx, -(lat - lat0) * ky) for lat, lon in geo]      # x east, z south, metres
    area = polygon_area(real)
    if area < MIN_AREA:
        return None
    rx, rz, L, W, ang = min_rect(real)
    clat, clon = lat0 - rz / ky, lon0 + rx / kx
    cx, cz = place.to_map(clat, clon)
    step = 5.0
    tx, tz = place.to_map(clat - math.sin(ang) * step / ky, clon + math.cos(ang) * step / kx)
    mang = math.atan2(tz - cz, tx - cx)
    outline = [place.to_map(lat, lon) for lat, lon in geo]
    return area, tags, cx, cz, L, W, mang, outline


def place_buildings(items, ground, kit=KIT, limit=None, prefix="gis"):
    """Encounter-layer objects for footprints (from footprint()): biggest
    first, each skipped if its model would overlap one already placed."""
    items = sorted((i for i in items if i), key=lambda t: -t[0])
    taken = {}       # 32 m cells -> circles (x, z, r) placed there
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
                        "_kind": kind})
    return objects, skipped


def place_buildings_simple(footprints, ground, kit=KIT, limit=None, prefix="gis"):
    """place_buildings for footprints already in map metres ([(tags, [(x, z)])])."""
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
    return place_buildings(items, ground, kit, limit, prefix)


# ---------------------------------------------------------------- layout
#
# Playable towns.  A squeezed strip can't hold its real buildings and still
# leave room to move, so real footprints only say how built-up each place is
# (and how tall, and how industrial).  The buildings are laid out in blocks:
# columns running along the coast with straight streets between them, cut
# into blocks at staggered places, each block filled in a style that matches
# how built-up the real place is.  Main roads, the border fence, the beach,
# open land (parks, fields) and plazas at the plan's marks stay clear.

STYLES = {
    # slot: room per building (m); keep: share of slots built; court: leave
    # the middle of big blocks open; street: around the block (m); sub/alley:
    # split the block into sub-blocks of about sub m with alleys between
    "sparse": {"kinds": ("house",), "slot": 0, "keep": 0.0, "street": 12.0},
    "suburb": {"kinds": ("house",), "slot": 34.0, "keep": 0.5, "street": 12.0},
    "city": {"kinds": ("block", "house"), "slot": 36.0, "keep": 0.9, "court": True, "street": 14.0},
    "towers": {"kinds": ("tower", "block"), "slot": 46.0, "keep": 0.95, "court": True, "street": 16.0},
    "camp": {"kinds": ("house",), "slot": 19.0, "keep": 0.95, "street": 10.0, "sub": 44.0, "alley": 5.0},
    "oldcity": {"kinds": ("house",), "slot": 18.5, "keep": 0.97, "street": 9.0, "sub": 40.0, "alley": 4.5},
    "industry": {"kinds": ("shed",), "slot": 44.0, "keep": 0.8, "street": 14.0},
    "port": {"kinds": ("shed",), "slot": 46.0, "keep": 0.7, "street": 18.0},
    "crossing": {"kinds": ("shed",), "slot": 46.0, "keep": 0.6, "street": 20.0},
}
# with more buildings than "max_buildings", lower priorities go first
PRIORITY = {"towers": 0, "oldcity": 0, "port": 0, "crossing": 0, "camp": 1, "city": 1, "industry": 2,
            "suburb": 3, "sparse": 4}
# share of ground real buildings cover (over ~250 m) -> style; plan "density" overrides
DENSITY = {"sparse": 0.02, "suburb": 0.07, "city": 0.16, "camp": 0.38}
GAP = 3.5            # m: at least this between buildings in a block
ROAD_CLEAR = 6.0     # m: kept clear on each side of a main road
FENCE_CLEAR = 40.0   # m: kept clear inside the border


class Density:
    """How built-up the real ground is: the share covered by building
    footprints over (2 * reach + 1) cells of cell metres, with the average
    number of floors and the share that's sheds and warehouses."""

    def __init__(self, strip, cell=50.0, reach=2):
        self.strip, self.cell, self.reach = strip, cell, reach
        self.raw, self.cache = {}, {}

    def add(self, tags, geo):
        if len(geo) > 2 and geo[0] == geo[-1]:
            geo = geo[:-1]
        if len(geo) < 3:
            return
        lat0 = sum(p[0] for p in geo) / len(geo)
        lon0 = sum(p[1] for p in geo) / len(geo)
        kx, ky = _local(lat0)
        area = polygon_area([((lon - lon0) * kx, (lat - lat0) * ky) for lat, lon in geo])
        a, c = self.strip._ac(lat0, lon0)
        v = self.raw.setdefault((int(a // self.cell), int(c // self.cell)), [0.0, 0.0, 0.0])
        v[0] += area
        v[1] += area * levels_of(tags)
        if (tags.get("building") or "").lower() in SHED_TAGS:
            v[2] += area

    def at_world(self, a, c):
        key = (int(a // self.cell), int(c // self.cell))
        got = self.cache.get(key)
        if got is None:
            area = lv = shed = 0.0
            r = self.reach
            for i in range(-r, r + 1):
                for j in range(-r, r + 1):
                    v = self.raw.get((key[0] + i, key[1] + j))
                    if v:
                        area, lv, shed = area + v[0], lv + v[1], shed + v[2]
            share = min(1.0, area / ((2 * r + 1) ** 2 * self.cell ** 2))
            got = (share, lv / area if area else 0.0, shed / area if area else 0.0)
            self.cache[key] = got
        return got

    def at(self, x, z):
        """(share built, floors, share sheds) at a map point."""
        return self.at_world(*self.strip.map_ac(x, z))


def style_of(share, floors, sheds, th=DENSITY):
    if share < th["sparse"]:
        return None
    if share < th["suburb"]:
        return "sparse"
    if sheds > 0.5:
        return "industry"
    if share < th["city"]:
        return "suburb"
    if floors >= 6:
        return "towers"
    if share >= th["camp"] and floors < 4:
        return "camp"
    return "city"


def _fit(kinds, a, b, rnd, kit=KIT):
    """A model of the first kind that has one fitting an a x b room:
    (kind, model, size x, size z), or None."""
    lo, hi = min(a, b), max(a, b)
    for kind in kinds:
        fits = []
        for model, mx, mz, _ in kit[kind]:
            ml, mw = max(mx, mz), min(mx, mz)
            if ml <= hi and mw <= lo:
                fits.append((ml * mw, model, mx, mz))
        if fits:
            fits.sort(reverse=True)
            _, model, mx, mz = fits[min(len(fits) - 1, int(rnd.random() * min(2, len(fits))))]
            return kind, model, mx, mz
    return None


def _place_in(x0, z0, x1, z1, st, rnd, kit=KIT):
    """Buildings in a rectangle of the strip's frame, a grid of rooms:
    [(kind, model, centre x, centre z, extent x, extent z, frame angle, room)],
    room (x0, z0, x1, z1) the space the building may move or shrink within."""
    out = []
    w, l = x1 - x0, z1 - z0
    if w < 8 or l < 8:
        return out
    nx = max(1, int(w / st["slot"] + 0.35))
    nz = max(1, int(l / st["slot"] + 0.35))
    sw, sl = w / nx, l / nz
    for i in range(nx):
        for j in range(nz):
            if st.get("court") and 0 < i < nx - 1 and 0 < j < nz - 1:
                continue
            if rnd.random() > st["keep"]:
                continue
            got = _fit(st["kinds"], sw - GAP, sl - GAP, rnd, kit)
            if not got:
                continue
            kind, model, mx, mz = got
            # the long side along the room's long side
            long_x = sw >= sl
            ang = 0.0 if (mx >= mz) == long_x else 90.0
            ex, ez = (mx, mz) if ang == 0.0 else (mz, mx)
            if rnd.random() < 0.5:
                ang += 180.0
            jx = max(0.0, sw - GAP - ex) / 2
            jz = max(0.0, sl - GAP - ez) / 2
            cx = x0 + (i + 0.5) * sw + rnd.uniform(-jx, jx)
            cz = z0 + (j + 0.5) * sl + rnd.uniform(-jz, jz)
            room = (x0 + i * sw + GAP / 2, z0 + j * sl + GAP / 2, x0 + (i + 1) * sw - GAP / 2, z0 + (j + 1) * sl - GAP / 2)
            out.append((kind, model, cx, cz, ex, ez, ang, room))
    return out


def fill_block(x0, z0, x1, z1, style, share, rnd, kit=KIT):
    """A block's buildings (as _place_in) in a style."""
    st = STYLES[style]
    if not st["slot"]:
        # scattered houses: at most one, more likely where it's more built up
        if rnd.random() > min(1.0, share / 0.06) * 0.6:
            return []
        got = _fit(st["kinds"], 20, 14, rnd, kit)
        kind, model, mx, mz = got
        ang = rnd.choice((0.0, 90.0, 180.0, 270.0))
        ex, ez = (mx, mz) if ang in (0.0, 180.0) else (mz, mx)
        cx = rnd.uniform(x0 + ex / 2, max(x0 + ex / 2, x1 - ex / 2))
        cz = rnd.uniform(z0 + ez / 2, max(z0 + ez / 2, z1 - ez / 2))
        return [(kind, model, cx, cz, ex, ez, ang, (x0, z0, x1, z1))]
    if st.get("sub"):
        out, alley = [], st["alley"]
        nx = max(1, int((x1 - x0 + alley) / (st["sub"] + alley) + 0.5))
        nz = max(1, int((z1 - z0 + alley) / (st["sub"] + alley) + 0.5))
        sw = (x1 - x0 - alley * (nx - 1)) / nx
        sl = (z1 - z0 - alley * (nz - 1)) / nz
        for i in range(nx):
            for j in range(nz):
                ax, az = x0 + i * (sw + alley), z0 + j * (sl + alley)
                out += _place_in(ax, az, ax + sw, az + sl, st, rnd, kit)
        return out
    return _place_in(x0, z0, x1, z1, st, rnd, kit)


def alternatives(b, kinds, kit=KIT, tries=48):
    """A building, then the same room filled other ways (slid to its sides
    and corners, then smaller models): for when the first way is blocked."""
    kind, model, cx, cz, ex, ez, ang, (x0, z0, x1, z1) = b
    yield kind, model, cx, cz, ex, ez, ang
    options = [(ex * ez, kind, model, ex, ez, ang)]
    for k in tuple(kinds) + ("house",):
        for m, mx, mz, _ in kit[k]:
            for a, (sx, sz) in ((0.0, (mx, mz)), (90.0, (mz, mx))):
                if sx <= x1 - x0 and sz <= z1 - z0 and (m, a) != (model, ang % 180):
                    options.append((sx * sz, k, m, sx, sz, a))
    options.sort(key=lambda t: -t[0])
    n = 0
    for _, k, m, sx, sz, a in options:
        xs = (cx, x0 + sx / 2, x1 - sx / 2)
        zs = (cz, z0 + sz / 2, z1 - sz / 2)
        for px in xs:
            for pz in zs:
                px = min(max(px, x0 + sx / 2), x1 - sx / 2)
                pz = min(max(pz, z0 + sz / 2), z1 - sz / 2)
                n += 1
                if n > tries:
                    return
                yield k, m, px, pz, sx, sz, a


def mark_zones(plan, strip):
    """The plan's marks in the strip's frame: [(mark, frame x, frame z)]."""
    out = []
    for m in plan.get("marks", []):
        x, z = strip.to_map(*m["at"])
        out.append((m, *strip.frame(x, z)))
    return out


def lay_out(plan, strip, dens, blocked, ground, open_at, paint_urban, side, ox, oz, kit=KIT, log=print):
    """Buildings for a strip's towns, laid out in blocks (see above).
    blocked(x, z): no building may touch the point; open_at(x, z): open land
    (no building centred there).  Paints streets and block ground into
    paint_urban.  Returns encounter objects (biggest priority first)."""
    th = dict(DENSITY, **plan.get("density", {}))
    zones = mark_zones(plan, strip)
    fo = [strip.frame(x, z) for x, z in strip.outline]
    fx0, fx1 = min(p[0] for p in fo), max(p[0] for p in fo)
    fz0, fz1 = min(p[1] for p in fo), max(p[1] for p in fo)
    cands, cells, styles = [], [], {}
    x, col = fx0, 0
    while x < fx1:
        cr = random.Random(col * 7919 + 17)
        w = cr.uniform(72.0, 104.0)
        base_l = cr.uniform(84.0, 140.0)
        z, row = fz0 - cr.uniform(0.0, base_l), 0
        while z < fz1:
            l = base_l * cr.uniform(0.8, 1.2)
            bx0, bz0, bx1, bz1 = x, z, x + w, z + l
            z += l
            row += 1
            cx, cz = (bx0 + bx1) / 2, (bz0 + bz1) / 2
            mx, mz = strip.unframe(cx, cz)
            if not strip.contains_map(mx, mz):
                continue
            share, floors, sheds = dens.at(mx, mz)
            style = style_of(share, floors, sheds, th)
            for m, px, pz in zones:
                if m.get("style") and math.hypot(cx - px, cz - pz) <= float(m.get("radius", 150)):
                    style = m["style"]
                    break
            if style is None:
                continue
            styles[style] = styles.get(style, 0) + 1
            st = STYLES[style]
            h = st["street"] / 2
            if style != "sparse":
                cells.append(((bx0, bz0, bx1, bz1), h))
            rnd = random.Random((col * 100003 + row) * 31 + 7)
            for b in fill_block(bx0 + h, bz0 + h, bx1 - h, bz1 - h, style, share, rnd, kit):
                cands.append((PRIORITY[style], rnd.random(), style, b))
        x += w
        col += 1

    # Streets and block ground, painted by each paint point's real distance
    # from the block's edges, fading over "street_feather" metres: the town
    # grid is turned against the paint grid, and a hard 8 m fill there shows
    # as staircase edges.
    street = int(plan.get("street_paint", 200))
    block_v = int(plan.get("block_paint", 90))
    feather = float(plan.get("street_feather", 4.0))

    def ramp(d):                               # 0 at -feather, 1 at +feather, smooth
        t = min(1.0, max(0.0, (d + feather) / (2 * feather)))
        return t * t * (3 - 2 * t)
    for (x0, z0, x1, z1), h in cells:
        corners = [strip.unframe(*p) for p in ((x0, z0), (x1, z0), (x1, z1), (x0, z1))]
        c0 = max(0, int((min(p[0] for p in corners) - feather - ox) / SPACING))
        c1 = min(side - 1, int((max(p[0] for p in corners) + feather - ox) / SPACING) + 1)
        r0 = max(0, int((min(p[1] for p in corners) - feather - oz) / SPACING))
        r1 = min(side - 1, int((max(p[1] for p in corners) + feather - oz) / SPACING) + 1)
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                fx, fz = strip.frame(ox + c * SPACING, oz + r * SPACING)
                d_out = min(fx - x0, x1 - fx, fz - z0, z1 - fz)          # inside the cell, metres
                if d_out < -feather:
                    continue
                d_in = d_out - h                                       # inside the block itself
                v = ramp(d_out) * (street + (block_v - street) * ramp(d_in))
                i = r * side + c
                if v > paint_urban[i]:
                    paint_urban[i] = int(v)

    cands.sort(key=lambda t: (t[0], t[1]))
    limit = plan.get("max_buildings")
    objects, dropped, built = [], {"clear": 0, "open": 0, "slope": 0, "over": 0}, {}
    rot = math.degrees(strip.rot)
    for _, _, style, b in cands:
        if limit is not None and len(objects) >= limit:
            dropped["over"] += 1
            continue
        for kind, model, cx, cz, ex, ez, ang in alternatives(b, STYLES[style]["kinds"], kit):
            pts = [strip.unframe(cx + u * ex / 2, cz + v * ez / 2) for u in (-1, 0, 1) for v in (-1, 0, 1)]
            if not any(blocked(px, pz) for px, pz in pts):
                break
        else:
            dropped["clear"] += 1
            continue
        mx, mz = pts[4]
        if open_at(mx, mz):
            dropped["open"] += 1
            continue
        hs = [ground.at(px, pz) for px, pz in pts]
        if max(hs) - min(hs) > 4.0:
            dropped["slope"] += 1
            continue
        a = math.radians(ang + rot)
        yaw = (-math.degrees(a) + 180) % 360 - 180
        corners = [pts[0], pts[6], pts[8], pts[2]]
        objects.append({"from": f"model:{model}", "name": f"gis_{len(objects) + 1:05d}",
                        "position": [round(mx, 2), round(min(hs), 2), round(mz, 2)], "yaw": round(yaw, 1),
                        "_kind": kind, "_style": style, "_rect": corners})
        built[style] = built.get(style, 0) + 1
    log("blocks: " + ", ".join(f"{v} {k}" for k, v in sorted(styles.items(), key=lambda t: -t[1])))
    log("buildings by style: " + ", ".join(f"{v} {k}" for k, v in sorted(built.items(), key=lambda t: -t[1]))
        + f"; left out: {dropped['clear']} on roads, the fence or the beach, {dropped['open']} on open land, "
        f"{dropped['slope']} on slopes, {dropped['over']} over max_buildings")
    return objects


def _box_blur(hm, radius):
    """A (2 * radius + 1)^2 box average of a heights grid (edges clamped)."""
    n = len(hm)
    k = 2 * radius + 1
    rows = []
    for row in hm:
        pad = [row[0]] * radius + row + [row[-1]] * radius
        acc = sum(pad[:k])
        out = [acc / k]
        for c in range(1, n):
            acc += pad[c + k - 1] - pad[c - 1]
            out.append(acc / k)
        rows.append(out)
    res = [[0.0] * n for _ in range(n)]
    for c in range(n):
        col = [rows[r][c] for r in range(n)]
        pad = [col[0]] * radius + col + [col[-1]] * radius
        acc = sum(pad[:k])
        res[0][c] = acc / k
        for r in range(1, n):
            acc += pad[r + k - 1] - pad[r - 1]
            res[r][c] = acc / k
    return res


def flatten_towns(plan, strip, dens, hm, sea, grid, base):
    """Level the ground where it's built up (a squeezed strip's ridges are
    steep, and towns need room to drive): blend towards a broad average,
    the more the more built up.  "flatten": [radius in samples, passes]."""
    radius, passes = plan.get("flatten", [4, 2])
    if not passes:
        return
    th = dict(DENSITY, **plan.get("density", {}))
    n = grid.size
    step = 4
    coarse = {}
    for r in range(0, n + 2 * step, step):
        for c in range(0, n + 2 * step, step):
            x, z = grid.x(min(c, n - 1)), grid.z(min(r, n - 1))
            share = dens.at(x, z)[0] if strip.contains_map(x, z) else 0.0
            coarse[(r // step, c // step)] = min(1.0, max(0.0, (share - th["sparse"]) / (th["suburb"] - th["sparse"])))
    flat = hm
    for _ in range(int(passes)):
        flat = _box_blur(flat, int(radius))
    floor = base + 1.5
    for r in range(n):
        for c in range(n):
            if sea[r][c]:
                continue
            fr, fc = r / step, c / step
            r0, c0 = int(fr), int(fc)
            a, b = fr - r0, fc - c0
            w = ((coarse[(r0, c0)] * (1 - b) + coarse[(r0, c0 + 1)] * b) * (1 - a)
                 + (coarse[(r0 + 1, c0)] * (1 - b) + coarse[(r0 + 1, c0 + 1)] * b) * a)
            if w <= 0:
                continue
            h = hm[r][c] * (1 - w) + flat[r][c] * w
            if hm[r][c] > floor:
                h = max(h, floor)
            hm[r][c] = h


def _lay_out_strip(plan, strip, dens, roads, mine, hm, ground, side, ox, oz, base, log, kit=KIT):
    """lay_out() with what keeps buildings away: main roads, the border, the
    beach and sea, plazas at marks, and open land from OSM."""
    blocked = bytearray(side * side)
    for k, pts in roads:
        paint_line(blocked, side, pts, ROADS[k][1] / 2 + 2 * ROAD_CLEAR, 1, SPACING, ox, oz)
    inner = bytearray(side * side)
    if strip.outline:
        fill_polygon(inner, side, strip.outline, 1, SPACING, ox, oz)
        paint_line(blocked, side, strip.outline + strip.outline[:1], 2 * FENCE_CLEAR, 1, SPACING, ox, oz)
    n = len(hm)
    for r in range(side):
        for c in range(side):
            i = r * side + c
            if not inner[i] or hm[min(r, n - 1)][min(c, n - 1)] <= base + 1.2:
                blocked[i] = 1
    plazas = bytearray(side * side)
    for m, fx, fz in mark_zones(plan, strip):
        rad = float(m.get("plaza", 40))
        if rad > 0:
            ring = [strip.unframe(fx + rad * math.cos(t * math.pi / 12), fz + rad * math.sin(t * math.pi / 12))
                    for t in range(24)]
            fill_polygon(plazas, side, ring, 1, SPACING, ox, oz)
    for i in range(side * side):
        if plazas[i]:
            blocked[i] = 1

    def at(grid, x, z):
        c, r = int(round((x - ox) / SPACING)), int(round((z - oz) / SPACING))
        return 0 <= c < side and 0 <= r < side and grid[r * side + c]

    def is_blocked(x, z):
        c, r = int(round((x - ox) / SPACING)), int(round((z - oz) / SPACING))
        return not (0 <= c < side and 0 <= r < side) or blocked[r * side + c]

    def open_at(x, z):
        return any(at(mine[k], x, z) >= 128 for k in ("green", "farm", "scrub"))
    objects = lay_out(plan, strip, dens, is_blocked, ground, open_at, mine["urban"], side, ox, oz, kit=kit, log=log)
    for i in range(side * side):              # plazas: open paved squares
        if plazas[i]:
            mine["urban"][i] = 255
    return objects


# ---------------------------------------------------------------- water

def sea_zone(sea, hm, grid, level, step_rows=8, margin=24.0):
    """A water zone over the sea: a polygon from the map's west edge to just
    past the shore, row band by row band. None without sea."""
    n = grid.size
    pts = []
    for r0 in range(0, n, step_rows):
        rows = range(r0, min(n, r0 + step_rows + 1))
        east = -1
        for r in rows:
            for c in range(n - 1, -1, -1):
                if sea[r][c]:
                    east = max(east, c)
                    break
        if east >= 0:
            pts.append((grid.x(east) + margin, grid.z(r0)))
            pts.append((grid.x(east) + margin, grid.z(min(n - 1, r0 + step_rows))))
    if not pts:
        return None
    west = grid.x(0) - 8
    ring = [(west, pts[0][1])] + pts + [(west, pts[-1][1])]
    # drop points that add nothing (same x as both neighbours)
    keep = [ring[0]]
    for i in range(1, len(ring) - 1):
        if not (ring[i - 1][0] == ring[i][0] == ring[i + 1][0]):
            keep.append(ring[i])
    keep.append(ring[-1])
    return {"points": [[round(x, 2), round(level, 2), round(z, 2)] for x, z in keep],
            "properties": dict(WATER, height=f"{level:.2f}")}


def sea_zones(sea, hm, grid, level, tile=2048.0, step_rows=8, margin=24.0, cells=(16, 128), inset=4.0, props=None):
    """The sea as several water zones, one per tile of the map (tile metres
    square) that has sea: the retail game's biggest water zone is about 3 km
    across with 75 points.  Each zone runs from its tile's west side to just
    past the shore, a point every step_rows rows, and stays inset metres
    inside the map's edges (retail seas stop just inside them).

    cells: (numgrids, gridsize).  The game splits a zone into a grid of
    numgrids * gridsize metre cells (retail: 16 x 2 = 32 m) and draws a cell
    only while its centre is within the camera's draw distance plus half its
    diagonal: past that the sea floor shows bare (32 m cells showed no sea
    from Gaza's beach; 128 m ones, water in patches; 512 m ones, a missing
    square here and there from a helicopter).  16 x 128 (2048 m cells)
    reaches about 1.45 km further, and a whole map's sea is 16 of them.  A cell with a corner in the zone is drawn whole,
    so keep the water a little under the lowest land.  [] without sea."""
    n = grid.size
    per = max(1, int(round(tile / SPACING)))
    zones = []
    for t0 in range(0, n, per):                     # tile rows
        for c0 in range(0, n, per):                 # tile columns
            c1 = min(n, c0 + per)
            west = grid.x(c0) + (inset if c0 == 0 else 0)
            east_edge = grid.x(c1) if c1 < n else grid.x(n - 1) - inset
            zlim = (grid.z(0) + inset, grid.z(n - 1) - inset)
            pts = []
            for r in list(range(t0, min(n, t0 + per), step_rows)) + [min(n - 1, t0 + per)]:
                east = None
                for rr in range(max(0, r - step_rows // 2), min(n, r + step_rows // 2 + 1)):
                    for c in range(c1 - 1, c0 - 1, -1):
                        if sea[rr][c]:
                            east = c if east is None else max(east, c)
                            break
                if east is not None:
                    pts.append((min(east_edge, grid.x(east) + margin), min(max(grid.z(r), zlim[0]), zlim[1])))
                elif pts:
                    break
            if len(pts) < 2:
                continue
            ring = [(west, pts[0][1])] + pts + [(west, pts[-1][1])]
            keep = [ring[0]]
            for i in range(1, len(ring) - 1):     # drop points on a straight run
                (ax, az), (bx, bz), (cx, cz) = keep[-1], ring[i], ring[i + 1]
                if abs((bx - ax) * (cz - az) - (bz - az) * (cx - ax)) > 1e-3:
                    keep.append(ring[i])
            keep.append(ring[-1])
            zones.append({"points": [[round(x, 2), round(level, 2), round(z, 2)] for x, z in keep],
                          "properties": dict(WATER, **{k: str(v) for k, v in (props or {}).items()},
                                             numgrids=str(cells[0]), gridsize=str(cells[1]), height=f"{level:.2f}")})
    return zones


# ---------------------------------------------------------------- build

PLANT_KINDS = ("palm", "olive", "citrus")


def load_kit(plan, mod, log=print):
    """The plan's own models (its "kit": {"file": "models/kit.json", "retail":
    false}): (building kit, plants, geometry). The file maps a model name to
    {"kind", "base", "x", "z", "height"}, as a mod's models/*.new.json models
    are made (a tool's output, e.g. the Gaza mod's tools/make_models.py).
    Kinds house, block, tower and shed replace the retail kit's kind ("retail":
    true adds to it); palm, olive and citrus are plants. geometry maps each
    own model to its base model, whose world objects are copied and drawn
    with the own model (GeometryFile), as the editor's New object does."""
    spec = plan.get("kit")
    if not spec:
        return KIT, {}, {}
    spec = {"file": spec} if isinstance(spec, str) else spec
    path = Path(mod) / spec.get("file", "models/kit.json")
    if not path.exists():
        log(f"kit: no {spec.get('file', 'models/kit.json')}; the retail kit")
        return KIT, {}, {}
    own = json.loads(path.read_text(encoding="utf-8"))
    kit = {k: list(v) for k, v in KIT.items()}
    plants, geometry = {}, {}
    replaced = set()
    for name, m in sorted(own.items()):
        kind = m["kind"]
        geometry[name] = m["base"]
        entry = (name, float(m["x"]), float(m["z"]), float(m["height"]))
        if kind in PLANT_KINDS:
            plants.setdefault(kind, []).append(entry)
            continue
        if kind not in replaced and not spec.get("retail"):
            kit[kind] = []
        replaced.add(kind)
        kit.setdefault(kind, []).append(entry)
    log(f"kit: {len(own)} own models from {path.name} ("
        + ", ".join(f"{len(kit[k])} {k}" for k in sorted(replaced))
        + ", " + ", ".join(f"{len(v)} {k}" for k, v in sorted(plants.items())) + ")")
    return kit, plants, geometry


def use_geometry(objects, geometry):
    """Objects of own models: copies of their base model's objects drawing
    the own model."""
    for o in objects:
        model = o["from"][len("model:"):] if o["from"].startswith("model:") else None
        if model in geometry:
            o["from"] = f"model:{geometry[model]}"
            o.setdefault("set", {})["GeometryFile"] = model
    return objects


# The plan's "plants": where its trees go.
PLANTS = {
    "spacing": 9.0,          # m between grove trees (a jittered grid)
    "grove": 0.8,            # share of grid points on tree cover planted
    "fields": 0.02,          # ... on cropland (lone trees)
    "town": 0.03,            # ... in built-up land (yards, courtyards)
    "street_every": 24.0,    # m between palms along main roads in towns
    "street_roads": ["trunk", "primary", "secondary"],
    "max": 3000,
    # by latitude, north first: the first band at or north of its latitude
    # gives each plant kind's share
    "bands": [[31.45, {"citrus": 3, "olive": 3, "palm": 1}],
              [31.37, {"palm": 5, "olive": 1}],
              [-90, {"olive": 3, "palm": 2}]],
}
TREE_COVER, CROPLAND, BUILT = 10, 40, 50
BUILDING_CLEAR = 2.5     # m kept clear around a building


def place_plants(plan, strip, landcover, ground, hm, base, roads, buildings, urban, side, ox, oz, plants, log=print):
    """Trees from land cover: groves on tree cover, a few on cropland and in
    built-up yards, palms along main roads through towns; away from
    buildings, roads, the beach and the border. plants: load_kit's."""
    opts = dict(PLANTS, **(plan.get("plants") if isinstance(plan.get("plants"), dict) else {}))
    if not plants:
        return []
    rnd = random.Random(int(plan.get("seed", 1)) + 7)
    cell = 4.0
    taken = set()

    def mark(x, z, r):
        for i in range(int((x - r) // cell), int((x + r) // cell) + 1):
            for j in range(int((z - r) // cell), int((z + r) // cell) + 1):
                taken.add((i, j))

    def free(x, z):
        return (int(x // cell), int(z // cell)) not in taken
    for b in buildings:
        xs = [p[0] for p in b["_rect"]]
        zs = [p[1] for p in b["_rect"]]
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        r = max(max(xs) - min(xs), max(zs) - min(zs)) / 2 + BUILDING_CLEAR
        for i in range(int((min(xs) - BUILDING_CLEAR) // cell), int((max(xs) + BUILDING_CLEAR) // cell) + 1):
            for j in range(int((min(zs) - BUILDING_CLEAR) // cell), int((max(zs) + BUILDING_CLEAR) // cell) + 1):
                taken.add((i, j))
    clear = bytearray(side * side)
    for k, pts in roads:
        paint_line(clear, side, pts, ROADS[k][1] + 4.0, 1, SPACING, ox, oz)
    inner = bytearray(side * side)
    if strip.outline:
        fill_polygon(inner, side, strip.outline, 1, SPACING, ox, oz)
        fence = bytearray(side * side)
        paint_line(fence, side, strip.outline + strip.outline[:1], 2 * FENCE_CLEAR, 1, SPACING, ox, oz)
        for i in range(side * side):
            if fence[i]:
                inner[i] = 0
    else:
        inner[:] = b"\x01" * (side * side)
    n = len(hm)

    def ok(x, z):
        c, r = int(round((x - ox) / SPACING)), int(round((z - oz) / SPACING))
        if not (0 <= c < side and 0 <= r < side) or not inner[r * side + c] or clear[r * side + c]:
            return False
        return hm[min(r, n - 1)][min(c, n - 1)] > base + 1.5 and free(x, z)

    def kind_at(lat):
        for limit, shares in opts["bands"]:
            if lat >= limit:
                ks = [k for k in shares if k in plants]
                if ks:
                    return rnd.choices(ks, [shares[k] for k in ks])[0]
        return rnd.choice(sorted(plants))

    out = []

    def plant(x, z, kind):
        name, sx, sz, _ = rnd.choice(plants[kind])
        out.append({"from": f"model:{name}", "name": f"plant_{len(out) + 1:05d}",
                    "position": [round(x, 2), round(ground.at(x, z), 2), round(z, 2)],
                    "yaw": round(rnd.uniform(-180, 180), 1), "_kind": kind})
        mark(x, z, 1.5 if kind == "citrus" else 2.5)

    # palms along main roads through towns
    street = []
    for k, pts in roads:
        if k not in opts["street_roads"]:
            continue
        off = ROADS[k][1] / 2 + 3.0
        carry = 0.0
        for (x0, z0), (x1, z1) in zip(pts, pts[1:]):
            seg = math.hypot(x1 - x0, z1 - z0)
            if seg < 1e-6:
                continue
            ux, uz = (x1 - x0) / seg, (z1 - z0) / seg
            t = carry
            while t < seg:
                x, z = x0 + ux * t, z0 + uz * t
                c, r = int(round((x - ox) / SPACING)), int(round((z - oz) / SPACING))
                if 0 <= c < side and 0 <= r < side and urban[r * side + c] >= 128:
                    for s in (-1, 1):
                        street.append((x - uz * off * s, z + ux * off * s))
                t += opts["street_every"]
            carry = t - seg
    # streets' palms stand beside the road: only the road itself is kept clear
    for x, z in street:
        c, r = int(round((x - ox) / SPACING)), int(round((z - oz) / SPACING))
        if 0 <= c < side and 0 <= r < side and inner[r * side + c] and free(x, z) \
                and hm[min(r, n - 1)][min(c, n - 1)] > base + 1.5 and "palm" in plants:
            plant(x, z, "palm")
    n_street = len(out)
    # groves, lone trees and yards, from land cover
    sp = float(opts["spacing"])
    half = (side - 1) * SPACING / 2
    counts = {"grove": 0, "fields": 0, "town": 0}
    if landcover is not None:
        steps = int(2 * half / sp)
        for i in range(steps):
            for j in range(steps):
                x = ox + i * sp + rnd.uniform(-sp / 3, sp / 3)
                z = oz + j * sp + rnd.uniform(-sp / 3, sp / 3)
                c, r = int(round((x - ox) / SPACING)), int(round((z - oz) / SPACING))
                if not (0 <= c < side and 0 <= r < side) or not inner[r * side + c]:
                    continue
                lat, lon = strip.to_world(x, z)
                px = landcover.at(lat, lon)
                cls = px[0] if px else None
                what = {TREE_COVER: "grove", CROPLAND: "fields", BUILT: "town"}.get(cls)
                if what is None or rnd.random() >= float(opts[what]):
                    continue
                if not ok(x, z):
                    continue
                plant(x, z, kind_at(lat))
                counts[what] += 1
    limit = int(opts["max"])
    if len(out) > limit:
        keep = out[:min(n_street, limit // 3)]
        rest = out[len(keep):]
        rnd.shuffle(rest)
        out = keep + rest[:limit - len(keep)]
        for k, o in enumerate(out):
            o["name"] = f"plant_{k + 1:05d}"
    kinds = {}
    for o in out:
        kinds[o["_kind"]] = kinds.get(o["_kind"], 0) + 1
    log(f"plants: {len(out)} ({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))}); "
        f"{n_street} palms along main roads, {counts['grove']} in groves, {counts['fields']} in fields, "
        f"{counts['town']} in towns (before the max of {limit})")
    return out


def build(plan, mod, log=print):
    """Write the plan's terrain, roads, buildings and sea into a mod folder."""
    mod = Path(mod)
    g = plan["grid"]
    name = plan["map"].lower()
    mp = mod / "maps" / f"{name}.json"
    if not mp.exists():
        log(f"note: maps/{name}.json isn't in the mod: make the map first (the editor's Map panel)")
    elif int(json.loads(mp.read_text(encoding="utf-8")).get("size") or 512) != g.size:
        log(f"note: maps/{name}.json's size isn't the plan's {g.size}: set \"size\": {g.size} there")
    cache = mod / "gis" / "cache"
    elev = Elevation(cache / "dem", int(plan.get("zoom", ZOOM)))
    for place in plan["places"]:
        if isinstance(place, Strip):
            bf = cache / "osm" / "boundary.json"
            place.prepare(elev, read_boundary(bf) if bf.exists() else None)
    hm, sea = heights(plan, elev)
    ground = Ground(hm, g)
    tdir = mod / "terrain"
    tdir.mkdir(parents=True, exist_ok=True)

    side = g.size + 1
    ox, oz = g.x0, g.z0
    paint = {l["key"]: bytearray(side * side) for l in LAYERS}
    paint["sand"][:] = b"\xff" * (side * side)
    roads, items, laid = [], [], None
    landcover = None
    # "landcover": {"radius": pixels around each point to blend over (2: about
    # 50 m), "max": {layer: most opacity, 0-255}}: soft edges, and layers that
    # let the sand show through (fields and groves aren't solid carpets)
    lc_opts = plan.get("landcover") if isinstance(plan.get("landcover"), dict) else {}
    lc_radius = int(lc_opts.get("radius", 2))
    lc_max = {str(k): int(v) for k, v in (lc_opts.get("max") or {}).items()}
    if plan.get("landcover"):
        if (cache / "landcover" / "worldcover.json").exists():
            landcover = Raster(cache / "landcover", "worldcover")
        else:
            log("land cover: not in the cache (run gis fetch); painting from OSM alone")
    counts = {}
    base = float(plan.get("base", 20.0))
    kit, plant_kit, geometry = load_kit(plan, mod, log)
    plants = []
    for place in plan["places"]:
        inside = place.contains_map
        mine = {k: bytearray(side * side) for k in paint}
        # a strip's towns are laid out in blocks; footprints only tell how built up
        layout = isinstance(place, Strip) and plan.get("layout", True)
        dens = Density(place) if layout else None
        place_roads = []
        for cname, _, _ in place.chunks():
            f = cache / "osm" / f"{cname}.json"
            if not f.exists():
                log(f"{cname}: no OSM data in the cache (run gis fetch)")
                continue
            n_ways = 0
            for tags, geo in read_osm(f):
                if tags.get("building") and layout:
                    dens.add(tags, geo)
                    n_ways += 1
                    continue
                if tags.get("building"):
                    fp = footprint(place, tags, geo)
                    if fp and inside(fp[2], fp[3]):
                        items.append(fp)
                        fill_polygon(mine["urban"], side, fp[7], 255, SPACING, ox, oz)
                    n_ways += 1
                    continue
                pts = [place.to_map(lat, lon) for lat, lon in geo]
                if tags.get("highway") in ROADS:
                    for piece in clip_line(pts, inside):
                        roads.append((tags["highway"], piece))
                        place_roads.append((tags["highway"], piece))
                elif tags.get("highway") in PAINTED_ROADS and not layout:
                    for piece in clip_line(pts, inside):
                        paint_line(mine["urban"], side, piece, PAINTED_ROADS[tags["highway"]] * float(plan.get("street_scale", 0.7)),
                                   255, SPACING, ox, oz)
                else:
                    k = _ground_key(tags)
                    if layout and k == "urban":
                        k = None
                    if k and len(pts) > 2:
                        fill_polygon(mine[k], side, pts, 255, SPACING, ox, oz)
                n_ways += 1
            counts[cname] = n_ways
        if layout:
            flatten_towns(plan, place, dens, hm, sea, g, base)
            keep = set(plan.get("decal_roads", ROAD_ORDER))
            laid = _lay_out_strip(plan, place, dens, [r for r in place_roads if r[0] in keep], mine, hm, ground, side, ox, oz,
                                  base, log, kit=kit)
            if plan.get("plants") and plant_kit:
                plants += place_plants(plan, place, landcover, ground, hm, base, place_roads, laid, mine["urban"],
                                       side, ox, oz, plant_kit, log)
        # only the place's own ground takes its paint; land no OSM polygon
        # covers gets some dirt, so it isn't all sand; outside a strip's
        # territory, fields
        if isinstance(place, District):
            h = place.size / 2
            c0, c1 = (max(0, int(math.ceil(g.col(place.at[0] - h)))), min(side - 1, int(g.col(place.at[0] + h))))
            r0, r1 = (max(0, int(math.ceil(g.row(place.at[1] - h)))), min(side - 1, int(g.row(place.at[1] + h))))
            cells = ((r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1))
            outside = lambda r, c: False
        else:
            mask = bytearray(side * side)
            if place.outline:
                fill_polygon(mask, side, place.outline, 1, SPACING, ox, oz)
            else:
                mask[:] = b"\x01" * (side * side)
            cells = ((r, c) for r in range(side) for c in range(side))
            outside = lambda r, c: not mask[r * side + c]
        lc = landcover if isinstance(place, Strip) else None
        for r, c in cells:
            i = r * side + c
            if lc is not None:
                # real land cover: each layer as its class's share of the
                # ~30 m around the point (in the strip and around it alike)
                lat, lon = place.to_world(ox + c * SPACING, oz + r * SPACING)
                px = lc.around(lat, lon, lc_radius)
                if px:
                    counts = {}
                    for (k,) in px:
                        counts[k] = counts.get(k, 0) + 1
                    for k, n in counts.items():
                        layer = LANDCOVER.get(k)
                        if layer:
                            v = int(lc_max.get(layer, 255) * n / len(px))
                            paint[layer][i] = max(paint[layer][i], v)
                if outside(r, c):
                    continue
                for k in paint:
                    if mine[k][i] > paint[k][i]:
                        paint[k][i] = mine[k][i]
                continue
            if outside(r, c):
                paint["farm"][i] = max(paint["farm"][i], 200)
                continue
            for k in paint:
                if mine[k][i] > paint[k][i]:
                    paint[k][i] = mine[k][i]
            if paint["dirt"][i] < 140:
                paint["dirt"][i] = 140
            if layout and not mine["urban"][i] and not mine["green"][i]:
                # unbuilt land away from the dunes: fields
                fx, fz = place.frame(ox + c * SPACING, oz + r * SPACING)
                if fx - place.shore_x(fz) > float(plan.get("fields_from_shore", 500)):
                    paint["farm"][i] = max(paint["farm"][i], 170)
        if isinstance(place, Strip) and place.outline:
            # the border: a bare strip along the fence
            ring = place.outline + place.outline[:1]
            paint_line(paint["dirt"], side, ring, 24.0, 255, SPACING, ox, oz)
            for k in ("farm", "green", "scrub", "urban"):
                lane = bytearray(side * side)
                paint_line(lane, side, ring, 24.0, 1, SPACING, ox, oz)
                for i in range(side * side):
                    if lane[i]:
                        paint[k][i] = 0
    if plan.get("satellite"):
        if (cache / "satellite" / "tci.json").exists():
            tint = satellite_tint(plan, Raster(cache / "satellite", "tci"), sea, log)
            (tdir / f"{name}.tint.png").write_bytes(png.encode(g.size, g.size, tint, 3))
            log(f"tint: terrain/{name}.tint.png from the satellite image (128 = as the map's average)")
        else:
            log("tint: the satellite image isn't in the cache (run gis fetch)")
    data, meta = heights_png(hm)
    meta["terrain"] = name
    (tdir / f"{name}.heights.png").write_bytes(data)
    (tdir / f"{name}.heights.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    log(f"heights: {min(min(r) for r in hm):.1f} to {max(max(r) for r in hm):.1f} m")
    # the sea floor and beach stay sand
    for r in range(side):
        for c in range(side):
            rr, cc = min(r, g.size - 1), min(c, g.size - 1)
            if sea[rr][cc] or hm[rr][cc] <= float(plan.get("base", 20.0)) + 0.6:
                i = r * side + c
                for k in paint:
                    if k != "sand":
                        paint[k][i] = 0
    # "textures": {layer key: texture} swaps a layer's texture (a game
    # texture, or a mod's own from textures/<name>.png)
    swap = {k: str(v) for k, v in (plan.get("textures") or {}).items()}
    unknown = set(swap) - {l["key"] for l in LAYERS}
    if unknown:
        raise ValueError(f"plan \"textures\": no layer {sorted(unknown)} (layers: {[l['key'] for l in LAYERS]})")
    layers = [dict(l, texture=swap.get(l["key"], l["texture"])) for l in LAYERS]
    table = [{"texture": l["texture"], "scale": l["scale"], "axis": 0} for l in layers]
    (tdir / f"{name}.layers.json").write_text(json.dumps({"layers": table}, indent=1) + "\n", encoding="utf-8")
    for old in tdir.glob(f"{name}.layer*.png"):
        old.unlink()
    for i, l in enumerate(layers):
        (tdir / f"{name}.layer{i:02d}.{l['texture']}.png").write_bytes(png.encode(side, side, bytes(paint[l["key"]]), 1))

    roads = drop_doubled_roads(join_roads(roads))
    roads.sort(key=lambda t: ROAD_ORDER.index(t[0]), reverse=True)   # big roads last: they win ties
    road_classes = set(plan.get("decal_roads", ROAD_ORDER))
    road_docs = [{"texture": ROADS[k][0], "span": ROADS[k][1],
                  "points": [[round(x, 2), round(z, 2)] for x, z in pts]} for k, pts in roads if k in road_classes]
    for k, pts in roads:
        if k not in road_classes and laid is None:        # laid-out towns have their own streets
            paint_line(paint["urban"], side, pts, ROADS[k][1] * 0.4, 255, SPACING, ox, oz)
    road_docs += plan.get("roads", [])
    # empty textures and decals: the copied map's own roads go
    (tdir / f"{name}.decals.json").write_text(json.dumps({"textures": [], "decals": [], "roads": road_docs}, indent=1) + "\n",
                                              encoding="utf-8")
    log(f"roads: {len(road_docs)} as decals")

    if laid is not None:
        objects, skipped = laid, 0
    else:
        objects, skipped = place_buildings(items, ground, kit=kit, limit=plan.get("max_buildings"))
    use_geometry(objects, geometry)
    kinds = {}
    for o in objects:
        kinds[o["_kind"]] = kinds.get(o["_kind"], 0) + 1
    edir = mod / "encounters"
    edir.mkdir(parents=True, exist_ok=True)
    # unnamed: the game holds 2048 object names in all; its buildings have none
    layer = {"map": name.upper(), "load": "map", "unnamed": True,
             "objects": [{k: v for k, v in o.items() if not k.startswith("_")} for o in objects]}
    level = float(plan.get("base", 20.0)) + float(plan.get("water_level", -0.3))
    # "water_tile": one zone per tile that size (a tile as big as the map: one
    # zone, so no two zones' cells overlap and show as a darker seam);
    # "water_step": a shore point every that many rows; "water": its colour,
    # alpha and waves (red, green, blue, alpha, amplitude)
    zones = sea_zones(sea, hm, g, level, tile=float(plan.get("water_tile", 2048)),
                      step_rows=int(plan.get("water_step", 8)),
                      cells=tuple(plan.get("water_cells", (16, 128))), props=plan.get("water"))
    # the sea goes in the map's own boundaries: the game makes water only
    # from a map's own lineregion record, not from a layer's
    bpath = mod / "maps" / name / "boundaries.json"
    if zones:
        bpath.parent.mkdir(parents=True, exist_ok=True)
        bounds = {f"{name}_sea{'' if len(zones) == 1 else f'_{i + 1}'}": z for i, z in enumerate(zones)}
        bpath.write_text(json.dumps({"boundaries": bounds}, indent=1) + "\n", encoding="utf-8")
    elif bpath.exists():
        bpath.unlink()
    (edir / f"{name}_gis.json").write_text(json.dumps(layer, indent=1) + "\n", encoding="utf-8")
    # plants: a layer of their own (encounters/<map>_plants.json)
    ppath = edir / f"{name}_plants.json"
    if plants:
        use_geometry(plants, geometry)
        ppath.write_text(json.dumps({"map": name.upper(), "load": "map", "unnamed": True,
                                     "objects": [{k: v for k, v in o.items() if not k.startswith("_")} for o in plants]},
                                    indent=1) + "\n", encoding="utf-8")
    elif ppath.exists():
        ppath.unlink()
    if laid is not None:
        log(f"buildings: {len(objects)} placed ({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))})")
    else:
        log(f"buildings: {len(objects)} placed ({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))}), "
            f"{skipped} left out (too close to a bigger one or over max_buildings), from {len(items)} footprints")
    if zones:
        log(f"sea: {len(zones)} water zone(s) at {level:.1f} m, "
            f"{max(len(z['points']) for z in zones)} points at most")

    (mod / "gis").mkdir(exist_ok=True)
    (mod / "gis" / f"{name}.preview.png").write_bytes(preview(plan, hm, sea, paint, road_docs, items, objects))
    log(f"preview: gis/{name}.preview.png")
    return {"objects": len(objects), "plants": len(plants), "roads": len(road_docs), "footprints": len(items), "ways": counts,
            "sea": bool(zones)}


# ---------------------------------------------------------------- preview

def preview(plan, hm, sea, paint, roads, items, objects, side=1024, marks=None):
    """The map from above: shaded ground coloured by paint, sea, footprints,
    roads, placed models' centres, districts' squares and the plan's marks."""
    g = plan["grid"]
    n = g.size
    k = side / (n * SPACING)
    colours = {"sand": (214, 196, 150), "dirt": (170, 140, 100), "scrub": (190, 180, 110),
               "farm": (130, 150, 80), "green": (90, 140, 70), "urban": (175, 172, 165)}
    ps = n + 1
    img = bytearray(side * side * 3)
    for y in range(side):
        r = min(int(y / k / SPACING), n - 2)
        for x in range(side):
            c = min(int(x / k / SPACING), n - 2)
            h = hm[r][c]
            if sea[r][c]:
                col = (60, 100, 150)
            else:
                col = colours["sand"]
                for l in LAYERS[1:]:
                    a = paint[l["key"]][r * ps + c] / 255
                    if a:
                        t = colours[l["key"]]
                        col = tuple(int(p * (1 - a) + q * a) for p, q in zip(col, t))
            shade = 1 + max(-0.45, min(0.45, (hm[r][c + 1] - h + hm[r + 1][c] - h) * -0.12))
            i = (y * side + x) * 3
            img[i:i + 3] = bytes(max(0, min(255, int(v * shade))) for v in col)

    def put(x, z, col):
        px, py = int((x - g.x0) * k), int((z - g.z0) * k)
        if 0 <= px < side and 0 <= py < side:
            i = (py * side + px) * 3
            img[i:i + 3] = bytes(col)

    def line(a, b, col):
        m = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) * k) + 1
        for t in range(m + 1):
            put(a[0] + (b[0] - a[0]) * t / m, a[1] + (b[1] - a[1]) * t / m, col)

    for rd in roads:
        p = rd["points"]
        for a, b in zip(p, p[1:]):
            line(a, b, (40, 40, 40))
    for o in objects:
        x, _, z = o["position"]
        rect = o.get("_rect")
        if rect:
            (ax, az), (bx, bz), _, (dx, dz) = rect
            ux, uz, vx, vz = bx - ax, bz - az, dx - ax, dz - az
            nu = int(math.hypot(ux, uz) * k) + 2
            nv = int(math.hypot(vx, vz) * k) + 2
            for i in range(nu + 1):
                for j in range(nv + 1):
                    put(ax + ux * i / nu + vx * j / nv, az + uz * i / nu + vz * j / nv, (150, 45, 35))
        else:
            put(x, z, (170, 50, 40))
    for place in plan["places"]:
        if isinstance(place, District):
            h = place.size / 2
            cs = [(place.at[0] - h, place.at[1] - h), (place.at[0] + h, place.at[1] - h),
                  (place.at[0] + h, place.at[1] + h), (place.at[0] - h, place.at[1] + h)]
            for a, b in zip(cs, cs[1:] + cs[:1]):
                line(a, b, (255, 255, 255))
        elif place.outline:
            ring = place.outline
            for a, b in zip(ring, ring[1:] + ring[:1]):
                line(a, b, (255, 255, 255))
        for m in plan.get("marks", []):
            x, z = place.to_map(*m["at"])
            for d in range(-24, 25):
                for w in (-8, 0, 8):
                    put(x + d * 2, z + w, (255, 220, 0))
                    put(x + w, z + d * 2, (255, 220, 0))
    return png.encode(side, side, bytes(img), 3)
