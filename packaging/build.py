"""Package the toolkit for players: dist/mercfoundry.pyz, one file holding the
whole toolkit (it uses only the standard library).

    python packaging\\build.py

Run it with any Python 3.10+:  python mercfoundry.pyz mod sync <port folder>

A stand-alone .exe (PyInstaller) is not used: Windows application control and
Smart App Control block unsigned executables, while Python's own python.exe is
signed. To ship without requiring a Python install, put the official
"Windows embeddable package" from python.org in a python\\ folder beside the
.pyz; `mod sync --install-hook` uses it when the .pyz runs from there.
"""
import shutil
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    out = ROOT / "dist" / "mercfoundry.pyz"
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "app"
        shutil.copytree(ROOT / "mercfoundry", src / "mercfoundry",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (src / "__main__.py").write_text("from mercfoundry.cli import main\nmain()\n", encoding="utf-8")
        zipapp.create_archive(src, out, compressed=True)
    print(f"built {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
