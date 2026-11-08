"""Package the toolkit for players.

    python packaging\\build.py                 dist/mercfoundry.pyz
    python packaging\\build.py --bundle ZIP    also dist/foundry/, a folder for
                                              players, with Python bundled

mercfoundry.pyz is one file holding the whole toolkit (it uses only the
standard library); run it with any Python 3.10+.

The bundle is for players without Python. ZIP is the official "Windows
embeddable package (64-bit)" from python.org (e.g.
python-3.13.17-embed-amd64.zip); check it against the SHA-256 python.org
publishes. dist/foundry/ holds:

    mercfoundry.pyz
    python/        the embeddable package (with Python's LICENSE.txt)
    install.cmd    sets up the launcher hook; run it from <game>/foundry/
    README.txt

Players copy the folder into the game folder (beside Mercenaries
Recompiled.exe) and run install.cmd once. A standalone .exe (PyInstaller) is
not used: Windows application control and Smart App Control block unsigned
executables, while python.org's python.exe is signed.
"""
import argparse
import shutil
import tempfile
import zipapp
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INSTALL = """@echo off
rem Mercenaries Foundry: build enabled source mods before every modded launch.
rem Run once from the foundry folder inside the game folder.
if not exist "%~dp0..\\mods" (
  echo Put this foundry folder inside the game folder, beside Mercenaries Recompiled.exe.
  pause
  exit /b 1
)
"%~dp0python\\python.exe" "%~dp0mercfoundry.pyz" mod sync "%~dp0.." --install-hook
echo.
echo Done. Turn mods on in the launcher and choose Launch Modded; they are built before the game starts.
pause
"""

README = """Mercenaries Foundry toolkit for players
=======================================

1. Copy this "foundry" folder into the Mercenaries Recompiled game folder
   (beside Mercenaries Recompiled.exe).
2. Run foundry\\install.cmd once.
3. Put Foundry mods (folders with a mod.json) in the game's mods folder, turn
   them on in the launcher and choose Launch Modded. They are built from your
   own game data before the game starts, into a mod called "Foundry Mods".

If a launch fails, foundry-sync.log beside the launcher says why.

Needs a Mercenaries Recompiled build with the launcher sync hook (Foundry's
port patch 0007). Python is included (python\\, from python.org; see
python\\LICENSE.txt).
"""


def build_pyz() -> Path:
    out = ROOT / "dist" / "mercfoundry.pyz"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "app"
        shutil.copytree(ROOT / "mercfoundry", src / "mercfoundry",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (src / "__main__.py").write_text("from mercfoundry.cli import main\nmain()\n", encoding="utf-8")
        zipapp.create_archive(src, out, compressed=True)
    print(f"built {out} ({out.stat().st_size // 1024} KB)")
    return out


def build_bundle(pyz: Path, python_zip: Path) -> Path:
    out = ROOT / "dist" / "foundry"
    if out.exists():
        shutil.rmtree(out)
    (out / "python").mkdir(parents=True)
    with zipfile.ZipFile(python_zip) as z:
        names = z.namelist()
        if "python.exe" not in names or not any(n.endswith("._pth") for n in names):
            raise SystemExit(f"{python_zip} isn't a Windows embeddable Python package")
        z.extractall(out / "python")
    shutil.copy2(pyz, out / "mercfoundry.pyz")
    (out / "install.cmd").write_text(INSTALL.replace("\n", "\r\n"), encoding="ascii", newline="")
    (out / "README.txt").write_text(README.replace("\n", "\r\n"), encoding="ascii", newline="")
    shutil.copy2(ROOT / "LICENSE", out / "LICENSE.txt")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"built {out} ({size // (1024 * 1024)} MB)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", metavar="ZIP", help="python.org's Windows embeddable package (64-bit) zip")
    args = ap.parse_args()
    pyz = build_pyz()
    if args.bundle:
        build_bundle(pyz, Path(args.bundle))


if __name__ == "__main__":
    main()
