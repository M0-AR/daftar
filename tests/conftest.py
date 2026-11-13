import hashlib
import json
import re

import pytest
from fastapi.testclient import TestClient

from daftar.ollama import check_url
from daftar.server import create_app


class FakeOllama:
    """Stands in for Ollama: deterministic bag-of-words embeddings and a canned, citing answer."""
    models_installed = ["fake-chat:latest", "nomic-embed-text:latest"]
    calls = []
    vision_calls = []
    loaded = []
    context_length = 32768

    def __init__(self, url, allow_remote=False):
        check_url(url, allow_remote)
        FakeOllama.url = url

    def version(self):
        return "fake"

    def model_list(self):
        return [{"name": m, "digest": f"{m}@{self.context_length}", "size": 1, "family": "", "parameters": "",
                 "quantization": ""} for m in self.models_installed]

    def models(self):
        return list(self.models_installed)

    def show(self, model):
        caps = ["embedding"] if "embed" in model else ["completion", "vision"] if "ocr" in model else ["completion"]
        return {"context_length": self.context_length, "capabilities": caps,
                "architecture": "paddleocr" if "paddleocr" in model else "fake"}

    def running(self):
        return list(FakeOllama.loaded)

    def vision(self, model, prompt, image_b64):
        import base64 as _b64
        FakeOllama.vision_calls.append({"model": model, "prompt": prompt, "png": _b64.b64decode(image_b64)[:8]})
        return "Scanned clause: the warranty period is thirty-six months."

    def embed(self, model, texts, num_ctx=None):
        out = []
        for t in texts:
            v = [0.0] * 128
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 128] += 1.0
            out.append(v)
        return out

    def chat(self, model, messages, temperature=0.2, num_ctx=8192, think=False):
        FakeOllama.calls.append({"model": model, "messages": messages, "temperature": temperature,
                                 "num_ctx": num_ctx, "think": think})
        yield from ["The answer ", "is in the passages [1]."]

    def pull(self, name):
        yield {"status": "pulling manifest"}
        yield {"status": "downloading", "total": 100, "completed": 50}
        yield {"status": "downloading", "total": 100, "completed": 100}


def wait_ingest(app):
    app.state.worker.submit(lambda: None).result(timeout=30)


CONTRACT = """Master Services Agreement between Northwind Traders and Contoso Ltd.
The termination notice period is ninety days in writing.
Invoices are payable within forty-five days of receipt.
""" + "\n".join(f"Filler clause {i} about general obligations and miscellaneous terms." for i in range(60))


class Api:
    """Small helper around TestClient for readable tests."""

    def __init__(self, client):
        self.c = client
        self.app = client.app

    def collection(self, name="Contracts"):
        r = self.c.post("/api/collections", json={"name": name})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def upload(self, cids, *files):
        cids = cids if isinstance(cids, list) else [cids]
        r = self.c.post("/api/documents", files=[("files", f) for f in files],
                        data={"collection_ids": [str(c) for c in cids]})
        assert r.status_code == 200, r.text
        wait_ingest(self.app)
        return r.json()

    def chat(self, cid):
        return self.c.post("/api/chats", json={"collection_id": cid}).json()["id"]

    def ask(self, chat_id, question="", resend=False):
        r = self.c.post(f"/api/chats/{chat_id}/ask", json={"question": question, "resend": resend})
        assert r.status_code == 200, r.text
        return [json.loads(line) for line in r.text.splitlines() if line]


_ORIGINAL = {name: FakeOllama.__dict__[name] for name in ("show", "model_list", "chat", "embed", "pull", "vision", "running")}


@pytest.fixture
def fake():
    for name, fn in _ORIGINAL.items():  # tests may patch methods on the class; start every test clean
        setattr(FakeOllama, name, fn)
    FakeOllama.models_installed = ["fake-chat:latest", "nomic-embed-text:latest"]
    FakeOllama.calls = []
    FakeOllama.vision_calls = []
    FakeOllama.loaded = []
    FakeOllama.context_length = 32768
    return FakeOllama


@pytest.fixture
def client(tmp_path, fake):
    app = create_app(tmp_path, ollama_factory=fake)
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-Daftar": "1"}) as c:
        yield c


@pytest.fixture
def api(client):
    return Api(client)
