import subprocess
import sys
from pathlib import Path

import pytest

from daftar import activation

ROOT = Path(__file__).resolve().parents[1]


def test_activation_url_detection():
    assert activation.is_activation_url("daftar://open")
    assert activation.is_activation_url("DAFTAR://anything")
    assert not activation.is_activation_url("http://127.0.0.1:8000/")
    assert not activation.is_activation_url("daftar")
    assert not activation.is_activation_url("")


def test_desktop_entry_quotes_the_executable():
    entry = activation._desktop_entry(["/opt/Daftar App/daftar", "--flag"])
    assert 'Exec="/opt/Daftar App/daftar" --flag %u' in entry
    assert "MimeType=x-scheme-handler/daftar;" in entry
    assert "NoDisplay=true" in entry


def test_windows_command_quotes_every_argument():
    assert activation._windows_command([r"C:\Program Files\Daftar\Daftar.exe"]) == \
        r'"C:\Program Files\Daftar\Daftar.exe" "%1"'
    assert activation._windows_command([r"C:\Python\pythonw.exe", "-m", "daftar"]) == \
        r'"C:\Python\pythonw.exe" "-m" "daftar" "%1"'


def test_frozen_launcher_uses_the_bundle(monkeypatch):
    monkeypatch.setattr(activation.sys, "frozen", True, raising=False)
    assert activation.launcher_command() == [activation.sys.executable]


def test_register_linux_writes_a_desktop_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(activation, "launcher_command", lambda: ["/usr/bin/daftar"])
    monkeypatch.setattr(activation.shutil, "which", lambda name: None)
    assert activation.register_linux(force=True)
    desktop = tmp_path / "applications" / "daftar.desktop"
    text = desktop.read_text(encoding="utf-8")
    assert "Exec=/usr/bin/daftar %u" in text
    assert "x-scheme-handler/daftar" in text


def test_lock_is_exclusive(tmp_path):
    lock_path = tmp_path / "daftar.lock"
    first = activation.InstanceLock(lock_path)
    assert first.acquire()
    code = ("import sys\n"
            "from pathlib import Path\n"
            "from daftar.activation import InstanceLock\n"
            "print(InstanceLock(Path(sys.argv[1])).acquire())\n")
    result = subprocess.run([sys.executable, "-c", code, str(lock_path)],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False"
    first.release()
    second = activation.InstanceLock(lock_path)
    assert second.acquire()
    second.release()


def test_running_url_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DAFTAR_HOME", str(tmp_path))
    activation.write_running_url("http://127.0.0.1:54321/")
    assert activation.read_running_url(0) == "http://127.0.0.1:54321/"
    with pytest.raises(ValueError):
        activation.write_running_url("http://example.com/")
    activation._url_path().write_text("http://127.0.0.1:99999/\n", encoding="utf-8")
    assert activation.read_running_url(0) is None
    activation.clear_running_url()
    assert not activation._url_path().exists()
