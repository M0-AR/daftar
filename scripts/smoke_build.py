"""Smoke-test a PyInstaller build on the current OS (used by CI on Windows, macOS and Linux).

    python scripts/smoke_build.py            # looks for dist/Daftar/...

Checks: --self-test passes (bundled native libraries work), the headless server starts, serves the UI,
accepts a local write, and rejects a request from a foreign web page.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ["dist/Daftar/Daftar.exe", "dist/Daftar.app/Contents/MacOS/Daftar", "dist/Daftar/Daftar"]


def request(url, data=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main() -> int:
    exe = next((ROOT / c for c in CANDIDATES if (ROOT / c).exists()), None)
    if not exe:
        print("no build found in", [str(ROOT / c) for c in CANDIDATES])
        return 1
    print("binary:", exe)
    test = subprocess.run([str(exe), "--self-test"], capture_output=True, text=True, timeout=300)
    print(test.stdout, test.stderr[-2000:], sep="")
    if test.returncode != 0:
        return 1

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    home = ROOT / "build" / "smoke-home"
    env = {**os.environ, "DAFTAR_HOME": str(home)}
    server = subprocess.Popen([str(exe), "--headless", "--port", str(port)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            try:
                if request(base + "/api/stats")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            print("server did not start")
            return 1
        checks = {
            "ui served": "<title>Daftar</title>" in request(base + "/")[1],
            "status endpoint": request(base + "/api/status")[0] == 200,
            "local write accepted": request(base + "/api/collections", {"name": "Smoke"}, {"X-Daftar": "1"})[0] == 200,
            "foreign origin rejected": request(base + "/api/collections", {"name": "X"},
                                               {"X-Daftar": "1", "Origin": "https://evil.example"})[0] == 403,
        }
        for name, ok in checks.items():
            print("ok  " if ok else "FAIL", name)
        return 0 if all(checks.values()) else 1
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
