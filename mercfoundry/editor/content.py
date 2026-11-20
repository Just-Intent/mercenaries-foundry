"""The editor's panels for what used to be file-only: a new map's HQs (flags,
interiors, medevac, HQ respawn points), its own look (HUD art), and its Deck
of 52. Each reads and writes the mod's own files, checked by the build's own
code so the editor never saves what the build would refuse."""
import base64
import json
import re
from pathlib import Path

from . import gitpanel

# maps/<name>.json keys these panels edit
MAP_KEYS = ("hqs", "interiors", "medevac", "hq_respawn", "hud_textures")


def _read(p):
    return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else None


def _write(p, doc):
    """As the mod's other JSON (indent 1), keeping the file's line endings."""
    p = Path(p)
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    if p.exists() and b"\r\n" in p.read_bytes()[:4096]:
        text = text.replace("\n", "\r\n")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


def _generated_keys(mod, rel):
    """The keys of a mod file its own tools write (mod.json "generated")."""
    g = gitpanel.generated(rel, gitpanel.generated_rules(mod))
    return True if g is True else list(g or [])


def map_keys(mod, name):
    """A new map's HQ, medevac and HUD art keys, and which of them the mod's
    tools write (editing those is undone by the tools' next run)."""
    rel = f"maps/{name.lower()}.json"
    doc = _read(Path(mod) / rel)
    if doc is None:
        return {"new": False}
    gen = _generated_keys(mod, rel)
    out = {"new": True, "keys": {k: doc.get(k) for k in MAP_KEYS},
           "generated": list(MAP_KEYS) if gen is True else [k for k in MAP_KEYS if k in gen]}
    # the HQ factions' names as the mod has them (factions.json), for labels
    fx = _read(Path(mod) / "factions.json") or {}
    out["faction_names"] = {f: (fx.get(f) or {}).get("name") for f in ("allies", "china", "mafia", "sk")}
    return out


def check_map_keys(mod, name, keys):
    """The build's own checks on the keys (raises ValueError as the build would)."""
    from .. import missions, modbuild
    where = f"maps/{name.lower()}.json"
    if keys.get("hqs"):
        missions.hq_flags(where, keys["hqs"])
    if keys.get("interiors"):
        missions.interiors(where, name.lower(), {"interiors": keys["interiors"]})
    if keys.get("medevac") or keys.get("hq_respawn"):
        modbuild.check_spawn_points(keys.get("medevac"), keys.get("hq_respawn"), where)
    hud = keys.get("hud_textures")
    if hud is not None and (not isinstance(hud, dict)
                            or not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in hud.items())):
        raise ValueError("\"hud_textures\" is {<game texture>: <texture>}")


def save_map_keys(mod, name, changes):
    """changes: {key: value | null}: set or remove those keys of maps/<name>.json."""
    p = Path(mod) / "maps" / f"{name.lower()}.json"
    doc = _read(p)
    if doc is None:
        raise ValueError(f"{name} isn't one of this mod's new maps")
    bad = [k for k in changes if k not in MAP_KEYS]
    if bad:
        raise ValueError(f"not a key this panel edits: {', '.join(bad)}")
    after = dict(doc)
    for k, v in changes.items():
        if v in (None, {}, []):
            after.pop(k, None)
        else:
            after[k] = v
    check_map_keys(mod, name, {k: after.get(k) for k in MAP_KEYS})
    _write(p, after)
    return {"file": f"maps/{name.lower()}.json", "keys": sorted(changes)}


# ---------------------------------------------------------------- the Deck

def deck_files(mod):
    """Every deck/<suit>.json as written, with the build's check of each."""
    from .. import deck
    folder = Path(mod) / "deck"
    maps = {f.stem.lower() for f in (Path(mod) / "maps").glob("*.json")} if (Path(mod) / "maps").is_dir() else set()
    out = []
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        rel = f"deck/{f.name}"
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            out.append({"file": rel, "error": f"not JSON: {e}"})
            continue
        problem = None
        try:
            deck.parse(rel, doc, maps, Path(mod))
        except ValueError as e:
            problem = str(e)
        portraits = {}
        for c in doc.get("cards") or []:
            rank = deck.RANKS.get(str(c.get("card", "")).upper())
            if rank is None:
                continue
            face = c.get("face")
            if not face:
                for cand in (f"deck/{doc.get('suit')}{rank}.png", f"deck/{doc.get('suit')}_{rank}.png",
                             f"deck/{doc.get('suit')}/{rank}.png"):
                    if (Path(mod) / cand).is_file():
                        face = cand
                        break
            portraits[str(rank)] = face if face and (Path(mod) / face).is_file() else None
        out.append({"file": rel, "doc": doc, "problem": problem, "portraits": portraits,
                    "generated": _generated_keys(mod, rel)})
    return {"files": out, "maps": sorted(maps), "suits": list(deck.SUITS),
            "ranks": ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]}


def save_deck(mod, rel, doc):
    from .. import deck
    rel = rel.replace("\\", "/")
    if not re.fullmatch(r"deck/[A-Za-z0-9_\-]+\.json", rel):
        raise ValueError("a deck file is deck/<name>.json")
    maps = {f.stem.lower() for f in (Path(mod) / "maps").glob("*.json")} if (Path(mod) / "maps").is_dir() else set()
    deck.parse(rel, doc, maps, Path(mod))            # as the build checks it
    _write(Path(mod) / rel, doc)
    return {"file": rel}


def save_portrait(mod, suit, rank, data_b64):
    """deck/<suit><rank>.png from an uploaded PNG (128x128 as the game's)."""
    from .. import deck, png
    if suit not in deck.SUITS:
        raise ValueError("not a suit")
    r = deck.RANKS.get(str(rank).upper())
    if r is None:
        raise ValueError("not a card")
    raw = base64.b64decode(data_b64)
    w, h, _ = png.decode(raw)                         # it must be a PNG the build can read
    rel = f"deck/{suit}{r}.png"
    (Path(mod) / "deck").mkdir(parents=True, exist_ok=True)
    (Path(mod) / rel).write_bytes(raw)
    return {"file": rel, "size": [w, h], "warning": None if (w, h) == (128, 128) else
            f"{w}x{h}: the game's portraits are 128x128 (the PDA draws them at that shape)"}


def save_texture(mod, name, data_b64):
    """textures/<name>.png from an uploaded PNG (a map's own HUD art)."""
    from .. import png
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name or ""):
        raise ValueError("a texture's name is letters, digits, _ and -")
    raw = base64.b64decode(data_b64)
    w, h, _ = png.decode(raw)
    (Path(mod) / "textures").mkdir(parents=True, exist_ok=True)
    (Path(mod) / "textures" / f"{name}.png").write_bytes(raw)
    return {"file": f"textures/{name}.png", "size": [w, h]}


def mod_png(mod, rel):
    """The bytes of one of the mod's own PNGs (read only, inside the mod)."""
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel.lower().endswith(".png") or ".." in rel.split("/"):
        return None
    p = (Path(mod) / rel).resolve()
    if Path(mod).resolve() not in p.parents or not p.is_file():
        return None
    return p.read_bytes()
