"""Recompute a terrain's baked vertex lighting (COLR) after a height edit.

The retail colours behave like one sun per map: each channel is roughly
ambient + diffuse * max(0, normal . sun), and ground a hill hides from the
sun is darker.  For the opening map (swn) the fit is clean: sun 47.5 degrees
up from -x/-z, brightness 36 + 53 * lambert, shadowed ground about halfway
back to ambient.  Other maps also carry painted tint in their colours.

So we don't repaint: `fit` estimates the map's sun, ambient, diffuse and
shadow strength from its own colours and heights, and `relight` scales each
vertex whose shading changed by new shade / old shade, per channel.  Painted
tint survives, and vertices whose shading didn't change keep their exact bytes.
"""

import math
import struct

from . import terrain_layers as tl

MAX_MARCH = 96          # cells a shadow ray travels at most


def _normal(H, size, spacing, x, z):
    dx = (H[z][(x + 1) % size] - H[z][(x - 1) % size]) / (2 * spacing)
    dz = (H[(z + 1) % size][x] - H[(z - 1) % size][x]) / (2 * spacing)
    k = 1 / math.sqrt(dx * dx + 1 + dz * dz)
    return -dx * k, k, -dz * k


def _sun(el, az):
    el, az = math.radians(el), math.radians(az)
    return math.cos(el) * math.cos(az), math.sin(el), math.cos(el) * math.sin(az)


def _line_fit(xs, ys):
    """Least squares y = a + b x."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0.0
    return my - b * mx, b


def vertex_rgb(colours, size):
    """rgb[z][x] per height sample from the per-patch COLR grids."""
    side = size // tl.PATCH
    out = [[None] * size for _ in range(size)]
    for pi, vals in enumerate(colours):
        px, pz = pi % side, pi // side
        for j in range(tl.PATCH):
            for i in range(tl.PATCH):
                out[pz * tl.PATCH + j][px * tl.PATCH + i] = tl.colr_rgb(vals[j * tl.VERTS + i])
    return out


def shadowed(H, size, spacing, sun, x, z, top):
    """True if terrain between (x, z) and the sun rises above the sun ray."""
    horiz = math.hypot(sun[0], sun[2])
    if horiz < 1e-6:
        return False
    ux, uz, rise = sun[0] / horiz, sun[2] / horiz, sun[1] / horiz * spacing
    h0 = H[z][x] + 0.5
    for k in range(1, 2 * MAX_MARCH):
        t = k * 0.5
        ray = h0 + t * rise
        if ray > top:
            return False
        if H[math.floor(z + uz * t) % size][math.floor(x + ux * t) % size] > ray:
            return True
    return False


class Light:
    def __init__(self, sun, ambient, diffuse, shadow, rms):
        self.sun, self.ambient, self.diffuse, self.shadow, self.rms = sun, ambient, diffuse, shadow, rms

    @property
    def usable(self):
        return all(d > 0 for d in self.diffuse)

    def shade(self, H, size, spacing, x, z, top):
        """Sun term at sample (x, z): lambert, reduced where shadowed."""
        n = _normal(H, size, spacing, x, z)
        lam = max(0.0, n[0] * self.sun[0] + n[1] * self.sun[1] + n[2] * self.sun[2])
        if lam and shadowed(H, size, spacing, self.sun, x, z, top):
            lam *= 1 - self.shadow
        return lam


def fit(H, rgb, size, spacing, stride=3):
    """Estimate a map's sun and colour response from its heights H[z][x]
    (metres) and baked colours rgb[z][x]."""
    pts = [(x, z) for z in range(0, size, stride) for x in range(0, size, stride)]
    normals = [_normal(H, size, spacing, x, z) for x, z in pts]
    lum = [sum(rgb[z][x]) / 3 for x, z in pts]

    def err(el, az):
        s = _sun(el, az)
        lams = [max(0.0, n[0] * s[0] + n[1] * s[1] + n[2] * s[2]) for n in normals]
        a, b = _line_fit(lams, lum)
        return sum((a + b * l - y) ** 2 for l, y in zip(lams, lum)) / len(lum)

    best = min((err(el, az), el, az) for el in range(5, 90, 10) for az in range(0, 360, 10))
    _, el0, az0 = best
    best = min((err(el, az), el, az) for el in [el0 + d / 2 for d in range(-20, 21, 5)] if 1 <= el <= 89
               for az in [az0 + d / 2 for d in range(-20, 21, 5)])
    e, el, az = best
    sun = _sun(el, az)
    top = max(max(r) for r in H)
    lams, shade_flags = [], []
    for (x, z), n in zip(pts, normals):
        lam = max(0.0, n[0] * sun[0] + n[1] * sun[1] + n[2] * sun[2])
        lams.append(lam)
        shade_flags.append(lam > 0 and shadowed(H, size, spacing, sun, x, z, top))
    lit = [i for i, s in enumerate(shade_flags) if not s]
    ambient, diffuse = [], []
    for c in range(3):
        a, b = _line_fit([lams[i] for i in lit], [rgb[pts[i][1]][pts[i][0]][c] for i in lit])
        ambient.append(a)
        diffuse.append(b)
    # shadow strength: how much of the sun term shadowed ground loses
    la, lb = sum(ambient) / 3, sum(diffuse) / 3
    ks = [1 - (lum[i] - la) / (lb * lams[i]) for i, s in enumerate(shade_flags) if s and lams[i] > 0.2] if lb > 0 else []
    shadow = min(1.0, max(0.0, sorted(ks)[len(ks) // 2])) if ks and lb > 0 else 0.5
    return Light(sun, ambient, diffuse, shadow, math.sqrt(e))


def relight(colours, old_m, new_m, size, spacing, light, changed):
    """New COLR grids for heights new_m (metres), given the old heights and
    colours.  `changed` lists the (x, z) samples whose height changed; only
    vertices whose shading can depend on them are recomputed.  Returns
    (colours, vertices changed)."""
    if not changed:
        return colours, 0
    xs = [x for x, _ in changed]
    zs = [z for _, z in changed]
    top_old = max(max(r) for r in old_m)
    top_new = max(max(r) for r in new_m)
    horiz = math.hypot(light.sun[0], light.sun[2])
    # shadows fall away from the sun: a raised sample can darken ground up to
    # MAX_MARCH cells down-sun of it
    reach = MAX_MARCH if horiz > 1e-6 else 1
    ux, uz = (-light.sun[0] / horiz, -light.sun[2] / horiz) if horiz > 1e-6 else (0, 0)
    x0 = min(xs) - 1 + min(0, int(math.floor(ux * reach)))
    x1 = max(xs) + 1 + max(0, int(math.ceil(ux * reach)))
    z0 = min(zs) - 1 + min(0, int(math.floor(uz * reach)))
    z1 = max(zs) + 1 + max(0, int(math.ceil(uz * reach)))
    side = size // tl.PATCH
    out = [list(c) for c in colours]
    count = 0
    seen = set()
    for zz in range(z0, z1 + 1):
        for xx in range(x0, x1 + 1):
            x, z = xx % size, zz % size
            if (x, z) in seen:
                continue
            seen.add((x, z))
            lo = light.shade(old_m, size, spacing, x, z, top_old)
            ln = light.shade(new_m, size, spacing, x, z, top_new)
            if abs(ln - lo) < 1e-3:
                continue
            # every COLR copy of this sample (patch edges are shared)
            for pz, j in _copies(z, side):
                for px, i in _copies(x, side):
                    pi, vi = pz * side + px, j * tl.VERTS + i
                    r, g, b = tl.colr_rgb(colours[pi][vi])
                    new = []
                    for c, v in enumerate((r, g, b)):
                        so = light.ambient[c] + light.diffuse[c] * lo
                        sn = light.ambient[c] + light.diffuse[c] * ln
                        new.append(min(31, round(v * sn / so / 8)) * 8 if so > 1 else v)   # nearest 5-bit level
                    nv = tl.rgb_colr(*new) | (colours[pi][vi] & 0x8000)
                    if nv != out[pi][vi]:
                        out[pi][vi] = nv
                        count += 1
    return out, count


def _copies(s, side):
    """(patch, index) pairs whose 9-vertex row holds sample s."""
    p, k = divmod(s, tl.PATCH)
    out = [(p, k)]
    if k == 0:
        out.append(((p - 1) % side, tl.PATCH))
    return out


def colr_blobs(data, patch_count):
    """The COLR chunk split into its per-patch blobs (bytes each)."""
    out, pos = [], 0
    for _ in range(patch_count):
        _, end = tl.read_blob(data, pos)
        out.append(data[pos:end])
        pos = end
    return out


FIT_VERSION = b"terrain-light-fit-1"


def cache_dir():
    """Where builds keep results worth reusing (never in game or mod folders):
    MERCFOUNDRY_CACHE, else the user's local app data."""
    import os
    from pathlib import Path
    base = os.environ.get("MERCFOUNDRY_CACHE")
    if base:
        return Path(base)
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / ".cache") / "mercfoundry"


def _cached_fit(chunks, compute):
    """fit() for a terrain's heights and colours, from the cache when it was
    worked out before: it depends only on them and takes seconds."""
    import hashlib
    import json
    h = hashlib.sha256(FIT_VERSION)
    for tag in sorted(chunks):
        if tag in (b"INFO", b"HEXP", b"HGT8", b"COLR"):
            h.update(tag + len(chunks[tag]).to_bytes(4, "little") + chunks[tag])
    path = cache_dir() / "terrain-light" / (h.hexdigest() + ".json")
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return Light(d["sun"], d["ambient"], d["diffuse"], d["shadow"], d["rms"])
    except (OSError, ValueError, KeyError):
        pass
    light = compute()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sun": list(light.sun), "ambient": list(light.ambient),
                                    "diffuse": list(light.diffuse), "shadow": light.shadow,
                                    "rms": light.rms}), encoding="utf-8")
    except OSError:
        pass
    return light


def relight_chunks(chunks, new_height_chunks):
    """New COLR body for a terrain whose INFO/HEXP/HGT8 become
    new_height_chunks, or (None, note) if lighting is left alone.
    Returns (colr_or_None, note)."""
    from . import terrain
    if b"COLR" not in chunks:
        return None, "no baked lighting"
    info, old_u, _ = terrain.decode_height_units(chunks)
    _, new_u, _ = terrain.decode_height_units({**chunks, **new_height_chunks})
    size, spacing, step = info["size"], info["spacing"], info["step"]
    changed = [(x, z) for z in range(size) for x in range(size) if new_u[z][x] != old_u[z][x]]
    if not changed:
        return None, "heights unchanged"
    old_m = [[u * step for u in row] for row in old_u]
    new_m = [[u * step for u in row] for row in new_u]
    side = size // tl.PATCH
    colours = tl.decode_colr(chunks[b"COLR"], side * side)
    light = _cached_fit(chunks, lambda: fit(old_m, vertex_rgb(colours, size), size, spacing))
    if not light.usable:
        return None, "lighting left as is (its colours don't follow a sun this map's slopes explain)"
    new, count = relight(colours, old_m, new_m, size, spacing, light, changed)
    blobs = colr_blobs(chunks[b"COLR"], side * side)
    body = b"".join(blobs[i] if new[i] == colours[i] else tl.pack_blob(struct.pack("<81H", *new[i]))
                    for i in range(side * side))
    el = math.degrees(math.asin(max(-1.0, min(1.0, light.sun[1]))))
    return body, f"relit {count} vertex colours (sun {el:.0f} degrees up)"


# ---------------------------------------------------------------- a chosen sun
#
# A level config's "SunAngle a b" is in degrees.  The config reader (0x211C90)
# multiplies both by pi/180 and 0x211C00 makes the light's direction from them,
# stored at 0x841FCC and read by the drawing code: the light travels along
# (-sin a cos b, cos a, -sin a sin b) (the game's sin table, 0x1F9FD0, and its
# lookups 0x1F9D70 sin, 0x1F9DA0 cos, 0x1F9DE0 sin and cos).  So the sun stands
# a - 90 degrees above the horizon, toward (cos b, sin b) in (x, z): what
# _sun(a - 90, b) points at.  The maps have x east and z south, so the sun's
# compass bearing (clockwise from north) is b + 90.  SW's "140 180": 50 degrees
# up in the west; blank's and NW's "130 80": 40 degrees up, a little east of
# south.

def sun_from_angle(text):
    """SunAngle's text ("140 180") -> (bearing, height) in degrees."""
    parts = str(text).split()
    if len(parts) < 2:
        raise ValueError(f"SunAngle is two numbers, not {text!r}")
    a, b = float(parts[0]), float(parts[1])
    return (b + 90.0) % 360.0, a - 90.0


def sun_angle(bearing, height):
    """(bearing, height) in degrees -> SunAngle's text."""
    def num(v):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{num(90.0 + float(height))} {num((float(bearing) - 90.0) % 360.0)}"


def sun_vector(bearing, height):
    """The unit vector toward the sun (x east, y up, z south)."""
    return _sun(float(height), float(bearing) - 90.0)


# A lit map's colours, near SW's (its mean vertex colour is about 126 135 139).
DEFAULT_AMBIENT = (70, 72, 76)
DEFAULT_DIFFUSE = (110, 110, 108)
DEFAULT_SHADOW = 0.6
BAKE_VERSION = b"terrain-light-bake-1"


def shadow_mask(H, size, spacing, sun):
    """rows of bytes, 1 where a hill between the sample and the sun hides it.
    One sweep from the sun's side: each sample's horizon is the higher of its
    own height and the horizon one step toward the sun, lowered by the sun's
    rise over that step."""
    out = [bytearray(size) for _ in range(size)]
    sx, sy, sz = sun
    horiz = math.hypot(sx, sz)
    if horiz < 1e-6 or sy <= 0:
        return out
    ux, uz = sx / horiz, sz / horiz
    tan = sy / horiz
    along_x = abs(ux) >= abs(uz)
    major, minor = (ux, uz) if along_x else (uz, ux)
    rise = tan * spacing / abs(major)
    d = minor / abs(major)               # minor-axis offset of the upstream sample
    k = math.floor(d)
    t = d - k
    order = range(size - 1, -1, -1) if major > 0 else range(size)
    prev = None
    for m in order:
        line = [H[m][i] for i in range(size)] if not along_x else [H[i][m] for i in range(size)]
        hor = list(line)
        if prev is not None:
            for i in range(size):
                a = i + k
                if a < 0 or a >= size or (t and a + 1 >= size):
                    continue
                up = (prev[a] * (1 - t) + prev[a + 1] * t if t else prev[a]) - rise
                if up > line[i] + 0.5:
                    if along_x:
                        out[i][m] = 1
                    else:
                        out[m][i] = 1
                if up > hor[i]:
                    hor[i] = up
        prev = hor
    return out


def _rgb3(v, what):
    if not (isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(c, (int, float)) for c in v)):
        raise ValueError(f"{what} is [red, green, blue] (0-255)")
    if not all(0 <= c <= 255 for c in v):
        raise ValueError(f"{what}: each of red, green and blue is 0-255")
    return tuple(float(c) for c in v)


def lighting_settings(doc, sun_text=None):
    """terrain/<map>.lighting.json, checked and filled in: {"bearing",
    "height" (the sun; default the map's SunAngle), "ambient", "diffuse"
    ([r, g, b]), "shadow" (0-1: how much of the sun shadowed ground loses),
    "tint" (keep painted tint where the map's colours show it)}."""
    if not isinstance(doc, dict):
        raise ValueError("lighting: an object of settings")
    unknown = set(doc) - {"bearing", "height", "ambient", "diffuse", "shadow", "tint"}
    if unknown:
        raise ValueError(f"lighting: unknown settings {', '.join(sorted(unknown))}")
    if ("bearing" in doc) != ("height" in doc):
        raise ValueError("lighting: give the sun's bearing and height together (or neither, for the map's SunAngle)")
    if "bearing" in doc:
        bearing, height = float(doc["bearing"]), float(doc["height"])
    elif sun_text:
        bearing, height = sun_from_angle(sun_text)
    else:
        raise ValueError("lighting: the map has no SunAngle; give the sun's bearing and height")
    if not 0 < height <= 90:
        raise ValueError(f"lighting: the sun's height is 0-90 degrees above the horizon, not {height:g}")
    shadow = float(doc.get("shadow", DEFAULT_SHADOW))
    if not 0 <= shadow <= 1:
        raise ValueError("lighting: shadow is 0 (none) to 1 (only ambient light in shadow)")
    return {"bearing": bearing % 360.0, "height": height,
            "ambient": _rgb3(doc.get("ambient", DEFAULT_AMBIENT), "lighting: ambient"),
            "diffuse": _rgb3(doc.get("diffuse", DEFAULT_DIFFUSE), "lighting: diffuse"),
            "shadow": shadow, "tint": bool(doc.get("tint", True))}


def _shades(H, size, spacing, sun, shadow):
    """Sun term per sample: lambert, reduced in hill shadow."""
    mask = shadow_mask(H, size, spacing, sun)
    out = []
    for z in range(size):
        row, mrow = [], mask[z]
        for x in range(size):
            n = _normal(H, size, spacing, x, z)
            lam = n[0] * sun[0] + n[1] * sun[1] + n[2] * sun[2]
            lam = lam if lam > 0 else 0.0
            if lam and mrow[x]:
                lam *= 1 - shadow
            row.append(lam)
        out.append(row)
    return out


def bake(chunks, settings):
    """The whole COLR lit from a chosen sun (settings from lighting_settings).
    Returns (COLR body, note).  Painted tint is kept where the map's colours
    follow a sun of their own (fit): each vertex keeps old / old shade."""
    from . import terrain
    import hashlib
    import json
    h = hashlib.sha256(BAKE_VERSION + json.dumps(settings, sort_keys=True).encode())
    for tag in (b"INFO", b"HEXP", b"HGT8", b"COLR"):
        h.update(tag + len(chunks[tag]).to_bytes(4, "little") + chunks[tag])
    path = cache_dir() / "terrain-bake" / (h.hexdigest() + ".bin")
    info, units, _ = terrain.decode_height_units(chunks)
    size, spacing, step = info["size"], info["spacing"], info["step"]
    sun = sun_vector(settings["bearing"], settings["height"])
    note = (f"lit from a sun {settings['height']:.0f} degrees up at bearing {settings['bearing']:.0f}"
            f" (SunAngle {sun_angle(settings['bearing'], settings['height'])})")
    try:
        body = path.read_bytes()
        return body, note
    except OSError:
        pass
    H = [[u * step for u in row] for row in units]
    side = size // tl.PATCH
    colours = tl.decode_colr(chunks[b"COLR"], side * side)
    shades = _shades(H, size, spacing, sun, settings["shadow"])
    tint = None
    # tint needs colours that vary (a new map from blank has one colour: nothing to keep)
    if settings["tint"] and len({v & 0x7FFF for patch in colours for v in patch}) > 1:
        old = vertex_rgb(colours, size)
        light = _cached_fit(chunks, lambda: fit(H, old, size, spacing))
        if light.usable:
            old_shades = _shades(H, size, spacing, light.sun, light.shadow)
            tint = (old, old_shades, light)
            note += "; painted tint kept"
    amb, dif = settings["ambient"], settings["diffuse"]
    out = []
    for pi in range(side * side):
        pz, px = divmod(pi, side)
        vals = []
        for j in range(tl.VERTS):
            z = (pz * tl.PATCH + j) % size
            for i in range(tl.VERTS):
                x = (px * tl.PATCH + i) % size
                lam = shades[z][x]
                rgb = [amb[c] + dif[c] * lam for c in range(3)]
                if tint:
                    old, old_shades, light = tint
                    for c in range(3):
                        base = light.ambient[c] + light.diffuse[c] * old_shades[z][x]
                        if base > 1:
                            rgb[c] *= min(4.0, max(0.25, old[z][x][c] / base))
                r, g, b = (min(248, max(0, int(round(v / 8)) * 8)) for v in rgb)
                was = colours[pi][j * tl.VERTS + i]
                vals.append((was & 0x8000) | (tl.rgb_colr(r, g, b) & 0x7FFF))
        out.append(tl.pack_blob(struct.pack("<81H", *vals)))
    body = b"".join(out)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    except OSError:
        pass
    return body, note
