"""Privacy promises, as tests: nothing leaves the device unless the user explicitly says so."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daftar.ollama import check_url
from daftar.server import create_app

PKG = Path(__file__).resolve().parents[1] / "daftar"


def test_ollama_must_be_loopback_by_default():
    for ok in ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"):
        check_url(ok)
    for bad in ("http://192.168.1.5:11434", "https://api.example.com", "http://127.0.0.1.evil.com", "ftp://127.0.0.1"):
        with pytest.raises(ValueError):
            check_url(bad)
    assert check_url("http://192.168.1.5:11434", allow_remote=True) == "http://192.168.1.5:11434"


def test_remote_server_needs_explicit_opt_in(api):
    assert api.c.put("/api/settings", json={"ollama_url": "http://10.0.0.2:11434"}).status_code == 400
    r = api.c.put("/api/settings", json={"ollama_url": "http://10.0.0.2:11434", "allow_remote_ollama": True})
    assert r.status_code == 200 and r.json()["ollama_url"] == "http://10.0.0.2:11434"
    # turning the opt-in off snaps back to this computer
    assert api.c.put("/api/settings", json={"allow_remote_ollama": False}).json()["ollama_url"] == "http://127.0.0.1:11434"


def test_rejects_non_loopback_host_header(tmp_path, fake):
    with TestClient(create_app(tmp_path, ollama_factory=fake), base_url="http://evil.example") as c:
        assert c.get("/api/stats").status_code == 403


def test_writes_need_custom_header(tmp_path, fake):
    with TestClient(create_app(tmp_path, ollama_factory=fake), base_url="http://127.0.0.1") as c:
        assert c.post("/api/collections", json={"name": "x"}).status_code == 403
        assert c.get("/api/stats").status_code == 200


def test_no_external_urls_in_code_or_ui():
    """No CDN, font, analytics or update URL anywhere in the shipped app."""
    allowed = re.compile(r"https?://(127\.0\.0\.1|localhost|\[::1\])|http://www\.w3\.org/2000/svg")
    offenders = []
    for f in PKG.rglob("*"):
        if f.suffix in {".py", ".js", ".html", ".css", ".svg"}:
            for url in re.findall(r"https?://[^\s\"'`)<>]+", f.read_text()):
                if not allowed.match(url):
                    offenders.append(f"{f.name}: {url}")
    assert offenders == []


def test_ui_is_served_locally(client):
    html = client.get("/").text
    assert "<title>Daftar</title>" in html
    for asset in ("app.js", "app.css", "logo.svg"):
        assert client.get(f"/{asset}").status_code == 200
