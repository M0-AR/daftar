"""Build a standalone Daftar app for the current OS (run on Windows, macOS and Linux separately).

    python scripts/build.py      ->  dist/Daftar/  (Daftar.exe / Daftar.app / Daftar)
"""
import os
from pathlib import Path

import PyInstaller.__main__

root = Path(__file__).resolve().parents[1]
PyInstaller.__main__.run([
    str(root / "daftar" / "__main__.py"),
    "--name", "Daftar",
    "--windowed",
    "--noconfirm",
    "--paths", str(root),
    "--add-data", f"{root / 'daftar' / 'static'}{os.pathsep}daftar/static",
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "webview",
    "--distpath", str(root / "dist"),
    "--workpath", str(root / "build"),
    "--specpath", str(root / "build"),
])
