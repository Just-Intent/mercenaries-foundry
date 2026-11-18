"""Gather a game folder's logs into one zip to send for diagnosis.

    python -m mercfoundry logs <game folder>

The game writes its logs beside the exe it runs, and the launcher runs a copy
from mod-cache/<hash>/runtime/, so most logs are in there (and a new mod
selection's cache replaces the old one, logs and all). This collects:

- the launcher's foundry-sync.log, xbox_kernel.log, and the loader's settings
  (mod-loader.ini, developer.ini, modcompatibility.ini);
- preview-logs/, crash-logs/ and any *.log beside the launcher and in every
  mod-cache/*/runtime/;
- PoiDump.htm (the game's out-of-memory report) from every mod cache;
- plugin-settings/**/*.log;
- hangsnap/ (freeze snapshots saved by Freeze-Snapshot.cmd);
- the built mod's report (mods/Foundry Mods/foundry-mod.json);
- about.txt: toolkit version, game exe hash, Windows version, mods turned on.

Saves, game data and disc images are never included.
"""
import datetime
import hashlib
import platform
import zipfile
from pathlib import Path

MAX_FILE = 64 * 1024 * 1024        # skip anything bigger (a stray dump)
SETTINGS = ("mod-loader.ini", "developer.ini", "modcompatibility.ini", "foundry-sync.log", "xbox_kernel.log")
LOG_DIRS = ("preview-logs", "crash-logs")


def _files(game: Path):
    """(path, name in the zip) for every log file under the game folder."""
    for name in SETTINGS:
        p = game / name
        if p.is_file():
            yield p, name
    for p in sorted(game.glob("*.log")):
        if p.name not in SETTINGS:
            yield p, p.name
    for d in LOG_DIRS:
        for p in sorted((game / d).rglob("*")) if (game / d).is_dir() else ():
            if p.is_file():
                yield p, f"{d}/{p.relative_to(game / d).as_posix()}"
    for p in sorted((game / "hangsnap").rglob("*")) if (game / "hangsnap").is_dir() else ():
        if p.is_file():
            yield p, f"hangsnap/{p.relative_to(game / 'hangsnap').as_posix()}"
    for p in sorted((game / "plugin-settings").rglob("*.log")) if (game / "plugin-settings").is_dir() else ():
        yield p, f"plugin-settings/{p.relative_to(game / 'plugin-settings').as_posix()}"
    report = game / "mods" / "Foundry Mods" / "foundry-mod.json"
    if report.is_file():
        yield report, "foundry-mod.json"
    cache = game / "mod-cache"
    for c in sorted(cache.iterdir()) if cache.is_dir() else ():
        if not c.is_dir():
            continue
        run = c / "runtime"
        tag = f"mod-cache/{c.name[:12]}"
        for p in sorted(run.glob("*.log")) if run.is_dir() else ():
            yield p, f"{tag}/{p.name}"
        for d in LOG_DIRS:
            for p in sorted((run / d).rglob("*")) if (run / d).is_dir() else ():
                if p.is_file():
                    yield p, f"{tag}/{d}/{p.relative_to(run / d).as_posix()}"
        for p in sorted(c.rglob("PoiDump.htm")):
            yield p, f"{tag}/PoiDump.htm"


def _about(game: Path) -> str:
    from . import __version__
    lines = [f"collected {datetime.datetime.now().isoformat(timespec='seconds')}",
             f"toolkit {__version__}",
             f"windows {platform.platform()} ({platform.machine()})"]
    exe = game / "mercenaries_recomp.exe"
    if exe.is_file():
        lines.append(f"mercenaries_recomp.exe sha256 {hashlib.sha256(exe.read_bytes()).hexdigest()}")
    ini = game / "mod-loader.ini"
    if ini.is_file():
        raw = ini.read_bytes()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
        on, folder = [], None
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("folder="):
                folder = line.split("=", 1)[1].strip('"')
            elif line.lower() == "enabled=1" and folder:
                on.append(folder)
        lines.append("mods on: " + (", ".join(on) or "(none)"))
    return "\n".join(lines) + "\n"


def collect(game, out=None) -> Path:
    """Write <out>/logs-<time>.zip (out defaults to <game>/Logs for Claude)."""
    game = Path(game)
    out = Path(out) if out else game / "Logs for Claude"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out / f"logs-{stamp}.zip"
    seen = set()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("about.txt", _about(game))
        for p, name in _files(game):
            if name in seen or p.resolve() == path.resolve():
                continue
            seen.add(name)
            try:
                if p.stat().st_size > MAX_FILE:
                    z.writestr(name + ".skipped.txt", f"{p.stat().st_size} bytes, too big to include\n")
                    continue
                z.write(p, name)
            except OSError as e:
                z.writestr(name + ".unreadable.txt", f"{e}\n")
    return path
