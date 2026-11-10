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
    ks = [1 - (lum[i] - la) / (lb * lams[i]) for i, s in enumerate(shade_flags) if s and lams[i] > 0.2]
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
