"""Build a standalone Daftar app for the current OS (run on Windows, macOS and Linux separately).

    python scripts/build.py      ->  dist/Daftar/  (Daftar.exe / Daftar.app / Daftar)
"""
import os
import sys
from pathlib import Path

import PyInstaller.__main__


def add_url_scheme(app: Path) -> None:
    """PyInstaller has no CLI option for custom Info.plist keys, so edit the built bundle."""
    import plistlib
    import subprocess

    plist = app / "Contents" / "Info.plist"
    if not plist.exists():
        print(f"Note: {plist} not found; daftar:// was not added.")
        return
    with plist.open("rb") as fh:
        info = plistlib.load(fh)
    types = info.setdefault("CFBundleURLTypes", [])
    if not any("daftar" in item.get("CFBundleURLSchemes", []) for item in types):
        types.append({
            "CFBundleURLName": "com.daftar.app",
            "CFBundleTypeRole": "Viewer",
            "CFBundleURLSchemes": ["daftar"],
        })
    with plist.open("wb") as fh:
        plistlib.dump(info, fh)
    try:
        result = subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)],
                                check=False, capture_output=True, text=True)
    except OSError as exc:
        print(f"Note: could not re-sign the bundle ({exc}); macOS may reject it.")
        return
    if result.returncode:
        print(f"Note: codesign failed; macOS may reject the edited bundle:\n{result.stderr.strip()}")


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
if sys.platform == "darwin":
    args += ["--argv-emulation", "--osx-bundle-identifier", "com.daftar.app"]
PyInstaller.__main__.run(args)

if sys.platform == "darwin":
    add_url_scheme(root / "dist" / "Daftar.app")
