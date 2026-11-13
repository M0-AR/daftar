"""Open Daftar from a daftar:// link, and keep one copy of the app running.

Registration is per-user and best-effort: Windows uses HKEY_CURRENT_USER, Linux writes a desktop
entry under XDG_DATA_HOME, and macOS relies on CFBundleURLTypes in the .app bundle. The URL is only
a wake-up signal: its contents are never used as instructions or data.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from daftar.config import data_dir

SCHEME = "daftar"
_DESKTOP_RESERVED = set(' \t\n"\'\\><~|&;$*?#()`')
_URL_RE = re.compile(r"^" + "http://" + r"127\.0\.0\.1:(\d{1,5})/$")

log = logging.getLogger(__name__)


def is_activation_url(value: str | None) -> bool:
    return bool(value) and value.lower().startswith(SCHEME + ":")


def _quote_desktop(arg: str) -> str:
    if not arg or any(ch in _DESKTOP_RESERVED for ch in arg):
        escaped = (arg.replace("\\", "\\\\").replace('"', '\\"')
                   .replace("`", "\\`").replace("$", "\\$"))
        return f'"{escaped}"'
    return arg


def launcher_command() -> list[str]:
    """The command a scheme handler should run, using the same environment as this process."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            return [str(pythonw), "-m", "daftar"]
        return [sys.executable, "-m", "daftar"]
    script = Path(sys.executable).with_name("daftar")
    if script.exists():
        return [str(script)]
    return [sys.executable, "-m", "daftar"]


def _desktop_entry(command: list[str]) -> str:
    exec_line = " ".join(_quote_desktop(part) for part in command) + " %u"
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Daftar",
        "Comment=Private document chat on your own computer",
        f"Exec={exec_line}",
        "Terminal=false",
        "MimeType=x-scheme-handler/daftar;",
        "Categories=Office;",
        "NoDisplay=true",
        "",
    ])


def _windows_command(command: list[str]) -> str:
    return f'"{command[0]}"' + "".join(f' "{part}"' for part in command[1:]) + ' "%1"'


def _applications_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "applications"


def _query_linux_default() -> str | None:
    if not shutil.which("xdg-mime"):
        return None
    try:
        out = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/daftar"],
                             capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _set_linux_default(directory: Path) -> None:
    commands = (["xdg-mime", "default", "daftar.desktop", "x-scheme-handler/daftar"],
                ["update-desktop-database", str(directory)])
    for command in commands:
        if not shutil.which(command[0]):
            continue
        try:
            subprocess.run(command, check=False, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("%s failed: %s", command[0], exc)


def register_linux(force: bool = False) -> bool:
    desktop = _applications_dir() / "daftar.desktop"
    wrote = False
    if force or not desktop.exists():
        try:
            desktop.parent.mkdir(parents=True, exist_ok=True)
            desktop.write_text(_desktop_entry(launcher_command()), encoding="utf-8")
            wrote = True
        except OSError as exc:
            log.warning("Could not write %s: %s", desktop, exc)
            return False
    if force or not _query_linux_default():
        _set_linux_default(desktop.parent)
    return wrote


def register_windows(force: bool = False) -> bool:
    import winreg

    key_path = rf"Software\Classes\{SCHEME}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path):
            exists = True
    except FileNotFoundError:
        exists = False
    except OSError as exc:
        log.warning("Could not inspect the daftar:// registry key: %s", exc)
        return False
    if exists and not force:
        return False

    command = launcher_command()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Daftar Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path + r"\DefaultIcon",
                                0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{command[0]}",0')
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path + r"\shell\open\command",
                                0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _windows_command(command))
    except OSError as exc:
        log.warning("Could not register daftar:// on Windows: %s", exc)
        return False
    return True


def register_macos(force: bool = False) -> bool:
    """A running .app is registered by LaunchServices from its Info.plist; source runs have no bundle."""
    return False


def register_protocol(force: bool = False) -> bool:
    if sys.platform == "win32":
        return register_windows(force)
    if sys.platform == "darwin":
        return register_macos(force)
    return register_linux(force)


class InstanceLock:
    """A per-user file lock. The OS releases it when the process exits, including after a crash."""

    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = open(self.path, "a+b")
        except OSError as exc:
            log.warning("Could not open %s: %s", self.path, exc)
            return True  # run without the lock rather than refusing to start
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b" ")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


def _url_path() -> Path:
    return data_dir() / "server.url"


def write_running_url(url: str) -> None:
    match = _URL_RE.match(url)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        raise ValueError("refusing to advertise a non-loopback URL")
    path = _url_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(url + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def read_running_url(timeout: float = 5.0) -> str | None:
    path = _url_path()
    deadline = time.monotonic() + timeout
    while True:
        try:
            url = path.read_text(encoding="utf-8").strip()
        except OSError:
            url = ""
        match = _URL_RE.match(url)
        if match and 1 <= int(match.group(1)) <= 65535:
            return url
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


def clear_running_url() -> None:
    try:
        _url_path().unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.debug("Could not remove server.url: %s", exc)


def open_running_instance(timeout: float = 5.0) -> bool:
    url = read_running_url(timeout)
    if not url:
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception as exc:  # webbrowser raises platform-specific errors
        log.warning("Could not open %s: %s", url, exc)
        return False
