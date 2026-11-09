"""The flyer's promises, as tests: nothing leaves the device, nothing is tracked."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daftar.ollama import check_local
from daftar.server import create_app

PKG = Path(__file__).resolve().parents[1] / "daftar"


def test_ollama_must_be_loopback():
    for ok in ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"):
        check_local(ok)
    for bad in ("http://192.168.1.5:11434", "https://api.example.com", "http://127.0.0.1.evil.com"):
        with pytest.raises(ValueError):
            check_local(bad)


def test_settings_reject_remote_ollama(client):
    r = client.put("/api/settings", json={"ollama_url": "http://10.0.0.2:11434"})
    assert r.status_code == 400


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
