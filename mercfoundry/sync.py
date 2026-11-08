"""Build the player's enabled source mods into one mod, from their own game data.

Mods are shared as source folders (mod.json plus text, scripts, objects,
models, ... as `mod new` lays out), installed in the game's mods folder like
any other mod and enabled and ordered in the launcher's mod selector. `mod
sync` reads that selection (mod-loader.ini), builds every enabled source mod
together in load order against the player's retail data, and writes the
result to mods/Foundry Mods, which the launcher loads like a normal mod. No
game data has to be shared, and mods that edit the same shared assets
(mission arcs, map lists, the HQ contact script, terrain) are merged instead
of overwriting each other.

The launcher copies a source mod's own files beside the game when it is
enabled (it treats them as native-mod files); the game never reads them.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

OUTPUT = "Foundry Mods"
SOURCE_DIRS = ("text", "scripts", "objects", "encounters", "missions", "textures", "models", "layout",
               "animations", "animtables", "terrain")


@dataclass
class Mod:
    folder: str
    enabled: bool


@dataclass
class SyncResult:
    sources: list = field(default_factory=list)     # mod folder names, in load order
    built: bool = False
    up_to_date: bool = False
    removed: bool = False
    build: object = None                            # modbuild.Result
    notes: list = field(default_factory=list)


def read_loader(game: Path) -> list:
    """The launcher's mod list (mod-loader.ini) in load order."""
    path = game / "mod-loader.ini"
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    mods, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        m = re.fullmatch(r"\[Mod\.(\d+)\]", line, re.I)
        if m:
            section = int(m.group(1))
            mods[section] = Mod("", False)
            continue
        if line.startswith("["):
            section = None
            continue
        if section is None or "=" not in line:
            continue
        key, value = (s.strip() for s in line.split("=", 1))
        if key.lower() == "folder":
            mods[section].folder = value.strip('"')
        elif key.lower() == "enabled":
            mods[section].enabled = value == "1"
    return [mods[k] for k in sorted(mods) if mods[k].folder]


def _separate_saves(game: Path) -> bool:
    path = game / "mod-loader.ini"
    if path.exists():
        raw = path.read_bytes()
        text = raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else raw.decode("utf-8", "replace")
        m = re.search(r"^\s*SeparateSaves\s*=\s*(\d)", text, re.M | re.I)
        if m:
            return m.group(1) == "1"
    return True


def write_loader(game: Path, mods: list, separate_saves: bool = True):
    """Write mod-loader.ini as the launcher does (UTF-16 with a BOM)."""
    text = f"﻿[Loader]\r\nCount={len(mods)}\r\nSeparateSaves={1 if separate_saves else 0}\r\n"
    for i, m in enumerate(mods):
        text += f"\r\n[Mod.{i}]\r\nFolder=\"{m.folder}\"\r\nEnabled={1 if m.enabled else 0}\r\n"
    tmp = game / "mod-loader.ini.foundry"
    tmp.write_bytes(text.encode("utf-16-le"))
    tmp.replace(game / "mod-loader.ini")


def set_enabled(game: Path, folder: str, enabled: bool) -> bool:
    """Enable or disable a mod in the launcher's list (added last if new).
    Returns whether the file changed."""
    mods = read_loader(game)
    entry = next((m for m in mods if m.folder == folder), None)
    if entry is None:
        if not enabled:
            return False
        mods.append(Mod(folder, True))
    elif entry.enabled == enabled:
        return False
    else:
        entry.enabled = enabled
    write_loader(game, mods, _separate_saves(game))
    return True


HOOK = "foundry-sync.cmd"


def install_hook(game: Path) -> Path:
    """foundry-sync.cmd: the launcher runs it before a modded launch (port
    patch 0007), so enabled source mods are built every time they change."""
    import sys
    game = Path(game)
    pyz = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0].lower().endswith(".pyz") else None
    if pyz is not None:
        # The packaged mercfoundry.pyz: run it with a Python bundled beside it
        # (the embeddable package in python\), else this one.
        bundled = pyz.parent / "python" / "python.exe"
        python = bundled if bundled.exists() else Path(sys.executable)

        def where(p):
            # Inside the game folder: relative to the hook, so moving the
            # game folder keeps it working.
            try:
                return "%~dp0" + str(p.resolve().relative_to(game.resolve()))
            except ValueError:
                return str(p)
        setup, command = "", f"\"{where(python)}\" \"{where(pyz)}\""
    else:
        repo = Path(__file__).resolve().parent.parent
        setup, command = f"set PYTHONPATH={repo}\r\n", f"\"{Path(sys.executable)}\" -m mercfoundry"
    text = ("@echo off\r\n"
            "rem Written by mercfoundry: builds enabled source mods before a modded launch.\r\n"
            + setup +
            f"{command} mod sync \"%~dp0.\" --enable > \"%~dp0foundry-sync.log\" 2>&1\r\n")
    path = game / HOOK
    path.write_text(text, encoding="ascii", newline="")
    return path


def is_source(folder: Path) -> bool:
    """A mod project (mod.json and source folders), not a built mod."""
    if not (folder / "mod.json").is_file() or (folder / "foundry-mod.json").exists():
        return False
    if any(folder.glob("*.dsk")):
        return False
    return any((folder / d).is_dir() for d in SOURCE_DIRS)


def retail_data(game: Path) -> Path:
    for p in (game / "game_files" / "mercenaries-retail" / "DATAxbox", game / "game_files" / "DATAxbox"):
        if (p / "assets.dsk").exists():
            return p
    raise FileNotFoundError(f"no retail game data under {game / 'game_files'} (pass --data)")


def _fingerprint(sources, data_dir: Path) -> str:
    h = hashlib.sha256()
    from . import __version__
    h.update(__version__.encode())
    for name in ("assets.dsk", "english.dsk", "locked.dsk", "streamed.dsk"):
        p = data_dir / name
        if p.exists():
            st = p.stat()
            h.update(f"{name}:{st.st_size}:{st.st_mtime_ns}".encode())
    for src in sources:
        h.update(b"\0mod:" + src.name.encode())
        for f in sorted(p for p in src.rglob("*") if p.is_file()):
            st = f.stat()
            h.update(f"{f.relative_to(src).as_posix()}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()


def _remove_output(out: Path) -> bool:
    """Remove files an earlier sync wrote (only those)."""
    meta = out / "foundry-mod.json"
    if not meta.exists():
        return False
    doc = json.loads(meta.read_text(encoding="utf-8"))
    if not doc.get("synced"):
        return False
    for rel in list(doc.get("archives", [])) + list(doc.get("files", [])):
        p = out / rel
        if p.is_file():
            p.unlink()
    meta.unlink()
    for d in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()
    if out.is_dir() and not any(out.iterdir()):
        out.rmdir()
    return True


def sync(game, data_dir=None, force=False, enable=False) -> SyncResult:
    from . import modbuild
    game = Path(game)
    mods_dir = game / "mods"
    if not mods_dir.is_dir():
        raise FileNotFoundError(f"{mods_dir} not found; pass the folder holding Mercenaries Recompiled.exe")
    data_dir = Path(data_dir) if data_dir else retail_data(game)
    result = SyncResult()
    loader = read_loader(game)
    sources = [mods_dir / m.folder for m in loader
               if m.enabled and m.folder != OUTPUT and is_source(mods_dir / m.folder)]
    result.sources = [s.name for s in sources]
    out = mods_dir / OUTPUT
    if not sources:
        result.removed = _remove_output(out)
        if enable and set_enabled(game, OUTPUT, False):
            result.notes.append(f"disabled \"{OUTPUT}\" in the launcher's mod list")
        return result
    fp = _fingerprint(sources, data_dir)
    meta = out / "foundry-mod.json"
    if not force and meta.exists():
        doc = json.loads(meta.read_text(encoding="utf-8"))
        if doc.get("synced") and doc.get("fingerprint") == fp:
            result.up_to_date = True
            _enable(game, loader, result, enable)
            return result
    result.build = modbuild.build(sources, data_dir, out, name=OUTPUT,
                                  description="Built on this PC from: " + ", ".join(result.sources))
    doc = json.loads(meta.read_text(encoding="utf-8"))
    doc.update({"synced": True, "fingerprint": fp})
    meta.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    result.built = True
    _enable(game, loader, result, enable)
    return result


def _enable(game, loader, result, enable):
    entry = next((m for m in loader if m.folder == OUTPUT), None)
    if entry is not None and entry.enabled:
        return
    if enable:
        set_enabled(game, OUTPUT, True)
        result.notes.append(f"enabled \"{OUTPUT}\" in the launcher's mod list")
    else:
        result.notes.append(f"enable \"{OUTPUT}\" in the launcher's mod list (it holds the built mods); "
                            "keep it after mods it should override")
