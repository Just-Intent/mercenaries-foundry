"""The game's root config files, beside DATAxbox: xbox.ini, rs.ini,
vehicles.ini and msh2temp.ini.

A mod's config/<file>.json sets lines in one: {"<section>": {key: value}},
"" being the top block (before the first [section]). A value is the line's
text after the key; a list sets a key that repeats, one line each (it
replaces all of that key's lines in the section). The build writes the
whole file as a loose file, which the launcher puts in place of the game's.

Lines the game reads in xbox.ini's [Humans]: AnimUpdateLow, AnimUpdateMedium,
AnimUpdateHigh and AnimUpdateLender, how often a character's animations are
updated (read by 0x4C380 into 0x320678; AnimChannel_UpdateRate, 0x4E790,
picks the table by a playing clip's flags: High with 0x4000, Medium with
0x2000, else Low; Lender for a character lending its pose). Each line is
"<max distance> <update every N vblanks> <interpolation 0/1>"; a distance
below 0 is for characters outside the view; at most four distances, in
ascending order (the file's own comments). Retail's Medium table has a typo
(its 15 m line says AnimUpdateLow), so Medium has three distances.
"""

ROOT_CONFIGS = ("xbox.ini", "rs.ini", "vehicles.ini", "msh2temp.ini")
ANIM_UPDATE = ("AnimUpdateLow", "AnimUpdateMedium", "AnimUpdateHigh", "AnimUpdateLender")


def sections(text):
    """[(name, start, end)]: line ranges of the top block ("") and each [section]."""
    from .sky import _lines
    lines = _lines(text)
    heads = [i for i, l in enumerate(lines) if l.strip().startswith("[")]
    out = [("", 0, heads[0] if heads else len(lines))]
    for k, i in enumerate(heads):
        name = lines[i].strip().strip("[]").strip()
        out.append((name, i + 1, heads[k + 1] if k + 1 < len(heads) else len(lines)))
    return out


def _check_anim_update(key, value, where):
    rows = value if isinstance(value, list) else [value]
    dists = []
    for row in rows:
        parts = str(row).split()
        try:
            d, n, interp = float(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            raise ValueError(f"{where}: each {key} line is \"<max distance> <every N vblanks> <interpolation 0/1>\", "
                             f"not {row!r}") from None
        if n < 1:
            raise ValueError(f"{where}: {key}: updating every {n} vblanks; the game needs 1 or more")
        if interp not in (0, 1):
            raise ValueError(f"{where}: {key}: interpolation is 0 or 1")
        if d >= 0:
            dists.append(d)
    if len(dists) > 4:
        raise ValueError(f"{where}: {key}: at most four distances (the game reads no more), not {len(dists)}")
    if dists != sorted(dists):
        raise ValueError(f"{where}: {key}: the distances go in ascending order")


def apply(text, doc, where="config"):
    """The config text with doc's lines set. Returns (text, [changes])."""
    from .sky import _lines, _set_keys
    if not isinstance(doc, dict):
        raise ValueError(f"{where}: an object of sections, each an object of settings")
    nl = "\r\n" if "\r\n" in text else "\n"
    changes = []
    for section, keys in doc.items():
        if not isinstance(keys, dict):
            raise ValueError(f"{where}: [{section}] is an object of settings")
        for key, value in keys.items():
            if key.lower() in {k.lower() for k in ANIM_UPDATE}:
                _check_anim_update(key, value, where)
        hit = next(((s, e) for name, s, e in sections(text) if name.lower() == str(section).lower()), None)
        if hit is None:
            have = ", ".join(f"[{n}]" for n, _, _ in sections(text) if n)
            raise ValueError(f"{where}: the file has no [{section}] section ({have or 'none'}; \"\" is the top block)")
        lines = _set_keys(_lines(text), hit[0], hit[1], keys, f"{where} [{section}]" if section else where)
        text = nl.join(lines) + nl
        changes.append((f"[{section}] " if section else "") + ", ".join(keys))
    return text, changes


def file_name(stem):
    """config/<stem>.json -> the root config file it changes (as the game spells it), or None."""
    return next((c for c in ROOT_CONFIGS if c.lower() == stem.lower()), None)


__all__ = ["ROOT_CONFIGS", "ANIM_UPDATE", "apply", "sections", "file_name"]
