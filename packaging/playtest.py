"""Package the game folder for playtesting on another PC.

    python packaging\\playtest.py                  dist/playtest/<name>.zip
    python packaging\\playtest.py --mods "Mercenaries Gaza" "Foundry Console"

Takes the port's Release folder (the launcher, the game exe and its DLLs, the
bundled Python and toolkit) and the mods turned on in its launcher, and writes
one zip. On the other PC: unzip it anywhere, run Mercenaries Recompiled.exe,
pick the Mercenaries ISO the first time (the launcher extracts and checks it),
then Launch Modded. Foundry source mods are built from that PC's own game data
before the first modded launch (a few minutes for a big mod).

No game data, saves or disc images go in the zip. Unzipping a newer package
over an older one keeps the imported game data, saves and logs.

Two scripts in the package, for sending results back:
- Collect-Logs.cmd   gathers every log into "Logs for Claude\\logs-<time>.zip"
- Freeze-Snapshot.cmd  while the game is frozen: snapshots its threads and
                       memory into hangsnap\\, then collects the logs
"""
import argparse
import datetime
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "external" / "Mercenaries-Recompiled" / "build" / "mercenaries" / "bin" / "Release"
BUILT_MOD = "Foundry Mods"          # rebuilt from source mods on the other PC
ROOT_FILES = ("Mercenaries Recompiled.exe", "mercenaries_recomp.exe", "mercenaries_recomp.map",
              "developer.ini", "modcompatibility.ini", "mercenaries_recomp.ini", "foundry-sync.cmd",
              "mercenaries-build-provenance.json", "SDL2-LICENSE.txt")
ROOT_DIRS = ("compat", "tools", "licenses")
SKIP_SUFFIXES = (".bak", ".preparing")

FREEZE = r"""@echo off
rem Run this while the game is frozen (before closing it). It snapshots the
rem game's threads and memory into the hangsnap folder, then collects the logs.
setlocal
cd /d "%~dp0"
if not exist hangsnap mkdir hangsnap
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set T=%%t
echo Taking a snapshot of the frozen game...
"foundry\python\python.exe" "foundry\mercfoundry.pyz" hangsnap --tool "foundry\hangsnap.exe" --map "foundry\map\functions.json" --save "hangsnap\raw-%T%.txt" > "hangsnap\hangsnap-%T%.txt" 2>&1
type "hangsnap\hangsnap-%T%.txt" | more
"foundry\python\python.exe" "foundry\mercfoundry.pyz" logs . --open
echo.
echo Send the newest zip in "Logs for Claude". You can close the game now.
pause
"""

COLLECT = r"""@echo off
rem Gathers every log into one zip in "Logs for Claude" and opens that folder.
cd /d "%~dp0"
"foundry\python\python.exe" "foundry\mercfoundry.pyz" logs . --open
echo.
echo Send the newest zip in "Logs for Claude".
pause
"""

README = """Mercenaries Foundry playtest build {name}
{rule}

Toolkit {version}, game exe sha256 {exe_hash}
Mods: {mods}

First time on a PC
  1. Unzip this folder anywhere (not inside Program Files).
  2. Run "Mercenaries Recompiled.exe" and pick your Mercenaries ISO when it
     asks. It extracts and checks the game data once.
  3. The mods above are already turned on: choose Launch Modded. The first
     modded launch builds the Foundry mods from your game data, which takes a
     few minutes ("Preparing..."); later launches are quick.

Updating to a newer playtest build
  Unzip the new one over this folder and replace files when asked. Your game
  data, saves and logs stay.

Sending results back
  - Something went wrong (crash, odd behaviour, a launch error): run
    Collect-Logs.cmd and send the newest zip in "Logs for Claude".
  - The game froze (stopped responding): leave it open, run
    Freeze-Snapshot.cmd, then send the newest zip in "Logs for Claude".
  Logging is on in developer.ini. F8 in game marks a moment in the logs;
  F9 opens the developer menu.

If Windows blocks the game ("could not start" in the launcher), Smart App
Control or antivirus refused the unsigned exe on that PC.
"""


def enabled_mods(release: Path):
    raw = (release / "mod-loader.ini").read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    on, folder = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("folder="):
            folder = line.split("=", 1)[1].strip('"')
        elif line.lower() == "enabled=1" and folder:
            on.append(folder)
    return on


def loader_ini(mods):
    lines = ["[Loader]", f"Count={len(mods)}", "SeparateSaves=1", ""]
    for i, m in enumerate(mods):
        lines += [f"[Mod.{i}]", f'Folder="{m}"', "Enabled=1", ""]
    return ("﻿" + "\r\n".join(lines)).encode("utf-16-le")


def _ignored(src: Path):
    """A test for paths (relative, posix) the folder's .gitignore leaves out,
    plus git's own files: downloads and editor scratch don't ship."""
    import fnmatch
    pats = [".git/", ".git", ".gitignore", "__pycache__/"]
    gi = src / ".gitignore"
    if gi.is_file():
        pats += [l.strip() for l in gi.read_text(encoding="utf-8", errors="replace").splitlines()
                 if l.strip() and not l.lstrip().startswith(("#", "!"))]

    pats = [p.strip("/") for p in pats]

    def test(rel):
        parts = rel.split("/")
        for pat in pats:
            if "/" in pat:                       # a path from the folder's top
                if rel == pat or rel.startswith(pat + "/") or fnmatch.fnmatch(rel, pat):
                    return True
            elif any(fnmatch.fnmatch(part, pat) for part in parts):
                return True
        return False
    return test


def _add_tree(z, src: Path, arc: str):
    ignored = _ignored(src)
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src).as_posix()
        if p.is_file() and not p.name.endswith(SKIP_SUFFIXES) and ".bak-" not in p.name and not ignored(rel):
            z.write(p, f"{arc}/{rel}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", default=str(RELEASE), help="the port's Release folder")
    ap.add_argument("--mods", nargs="+", help="mod folders to include (default: those on in the launcher)")
    ap.add_argument("--out", default=str(ROOT / "dist" / "playtest"), help="folder for the zip")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    from mercfoundry import __version__
    import hashlib

    release = Path(args.release)
    mods = args.mods or [m for m in enabled_mods(release) if m != BUILT_MOD]
    missing = [m for m in mods if not (release / "mods" / m).is_dir()]
    if missing:
        sys.exit(f"not in {release / 'mods'}: {', '.join(missing)}")
    if args.mods is None and BUILT_MOD in enabled_mods(release):
        loader = [m for m in enabled_mods(release) if m in mods or m == BUILT_MOD]   # same order; the sync re-creates it
    else:
        loader = list(mods)

    # the toolkit as committed here, not whatever copy the launcher has
    subprocess.run([sys.executable, str(ROOT / "packaging" / "build.py")], check=True, capture_output=True)
    pyz = ROOT / "dist" / "mercfoundry.pyz"
    hang = ROOT / "tools" / "hangsnap" / "out" / "hangsnap.exe"

    exe_hash = hashlib.sha256((release / "mercenaries_recomp.exe").read_bytes()).hexdigest()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    name = f"Mercenaries-Foundry-Playtest-{__version__}-{stamp}"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.zip"
    top = "Mercenaries Foundry Playtest"
    readme = README.format(name=name, rule="=" * (31 + len(name)), version=__version__, exe_hash=exe_hash,
                           mods=", ".join(mods) or "(none)")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in ROOT_FILES:
            if (release / f).is_file():
                z.write(release / f, f"{top}/{f}")
        for p in sorted(release.glob("*.dll")):
            z.write(p, f"{top}/{p.name}")
        for d in ROOT_DIRS:
            if (release / d).is_dir():
                _add_tree(z, release / d, f"{top}/{d}")
        foundry = release / "foundry"
        for p in sorted(foundry.rglob("*")):
            rel = p.relative_to(foundry).as_posix()
            if p.is_file() and rel != "mercfoundry.pyz" and ".bak" not in p.name:
                z.write(p, f"{top}/foundry/{rel}")
        z.write(pyz, f"{top}/foundry/mercfoundry.pyz")
        if hang.is_file():
            z.write(hang, f"{top}/foundry/hangsnap.exe")
        for m in ("functions.json", "manual.json"):
            z.write(ROOT / "map" / m, f"{top}/foundry/map/{m}")
        for m in mods:
            _add_tree(z, release / "mods" / m, f"{top}/mods/{m}")
        z.writestr(f"{top}/mod-loader.ini", loader_ini(loader))
        z.writestr(f"{top}/Freeze-Snapshot.cmd", FREEZE.replace("\n", "\r\n"))
        z.writestr(f"{top}/Collect-Logs.cmd", COLLECT.replace("\n", "\r\n"))
        z.writestr(f"{top}/README-PLAYTEST.txt", readme.replace("\n", "\r\n"))
        z.writestr(f"{top}/Logs for Claude/README.txt",
                   "Collect-Logs.cmd and Freeze-Snapshot.cmd put zips here. Send the newest one.\r\n")
    print(f"wrote {path} ({path.stat().st_size / 1048576:.0f} MB)")
    print(f"mods: {', '.join(mods)}")
    if not hang.is_file():
        print("note: tools/hangsnap/out/hangsnap.exe not built; Freeze-Snapshot.cmd won't work", file=sys.stderr)


if __name__ == "__main__":
    main()
