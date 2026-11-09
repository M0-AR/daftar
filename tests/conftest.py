import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from daftar.ollama import check_local
from daftar.server import create_app


class FakeOllama:
    """Stands in for Ollama: deterministic bag-of-words embeddings and a canned answer."""
    models_installed = ["fake-chat:latest", "nomic-embed-text:latest"]

    def __init__(self, url):
        check_local(url)

    def version(self):
        return "fake"

    def models(self):
        return list(self.models_installed)

    def embed(self, model, texts):
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(v)
        return out

    def chat(self, model, messages):
        FakeOllama.last_messages = messages
        yield from ["The answer ", "is in the sources [1]."]


def wait_ingest(app):
    app.state.worker.submit(lambda: None).result(timeout=30)


@pytest.fixture
def fake():
    FakeOllama.models_installed = ["fake-chat:latest", "nomic-embed-text:latest"]
    return FakeOllama


@pytest.fixture
def client(tmp_path, fake):
    app = create_app(tmp_path, ollama_factory=fake)
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-Daftar": "1"}) as c:
        c.app_ref = app
        yield c
