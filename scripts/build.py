"""Build a standalone Daftar app for the current OS (run on Windows, macOS and Linux separately).

    python scripts/build.py      ->  dist/Daftar/  (Daftar.exe / Daftar.app / Daftar)
"""
import os
from pathlib import Path

import PyInstaller.__main__

root = Path(__file__).resolve().parents[1]
vendor = root / "vendor" / "llama.cpp"
args = [
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
]
if vendor.is_dir():
    # Copied as data, not binaries: the launcher points the loader at this folder, so PyInstaller must
    # not rewrite or re-collect the engine. It keeps the executable bit when the source has it.
    args += ["--add-data", f"{vendor}{os.pathsep}vendor/llama.cpp"]
else:
    print(f"Note: {vendor} does not exist; this build will not include the built-in engine.")
PyInstaller.__main__.run(args)
