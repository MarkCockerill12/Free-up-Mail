"""Builds the single-file releases into dist/.

  FreeUpMail.pyz   any OS with Python 3.11+: double-click it, or `python FreeUpMail.pyz`
  FreeUpMail(.exe) a standalone app for the OS it is built on, no Python needed
                   (needs `pip install pyinstaller`)

python build.py [pyz] [exe]   (default: both)
"""

import shutil
import subprocess
import sys
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
PACKAGE = ROOT / "freeupmail"


def build_pyz() -> Path:
    staging = BUILD / "pyz"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(PACKAGE, staging / "freeupmail", ignore=shutil.ignore_patterns("__pycache__"))
    target = DIST / "FreeUpMail.pyz"
    zipapp.create_archive(staging, target, interpreter="/usr/bin/env python3",
                          main="freeupmail.server:main", compressed=True)
    return target


def build_exe() -> Path:
    windowed = ["--windowed"] if sys.platform == "win32" else []  # macOS would also make an .app
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--onefile", *windowed, "--noconfirm",
        "--name", "FreeUpMail", "--icon", str(PACKAGE / "web" / "icon.ico"),
        "--add-data", f"{PACKAGE / 'web'}{';' if sys.platform == 'win32' else ':'}freeupmail/web",
        "--paths", str(ROOT), "--distpath", str(DIST), "--workpath", str(BUILD / "exe"),
        "--specpath", str(BUILD), str(PACKAGE / "__main__.py"),
    ], check=True)
    return DIST / ("FreeUpMail.exe" if sys.platform == "win32" else "FreeUpMail")


def main() -> None:
    wanted = set(sys.argv[1:]) or {"pyz", "exe"}
    DIST.mkdir(exist_ok=True)
    for name, build in (("pyz", build_pyz), ("exe", build_exe)):
        if name in wanted:
            path = build()
            print(f"built {path} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
