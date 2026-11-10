"""The OpenAI-compatible engine (LM Studio, Jan, llama.cpp, vLLM, LocalAI, KoboldCpp, Ollama /v1)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import CONTRACT, FakeOllama, wait_ingest
from daftar.openai_compat import OpenAICompat, base_v1, guess_capabilities
from daftar.server import create_app

SEEN = []


def fake_server(request: httpx.Request) -> httpx.Response:
    """A minimal OpenAI-compatible server."""
    SEEN.append((request.method, request.url.path, request.headers.get("authorization")))
    if request.url.path == "/v1/models":
        return httpx.Response(200, json={"data": [{"id": "my-chat-7b"}, {"id": "text-embed-small"}, {"id": "ocr-vl-1b"}]})
    body = json.loads(request.content)
    if request.url.path == "/v1/embeddings":
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(len(t)), 1.0, float(i)]}
                                                  for i, t in reversed(list(enumerate(body["input"])))]})
    if request.url.path == "/v1/chat/completions":
        if not body.get("stream"):  # vision / OCR call
            image = body["messages"][0]["content"][1]["image_url"]["url"]
            assert image.startswith("data:image/png;base64,")
            return httpx.Response(200, json={"choices": [{"message": {"content": "Scanned text"}}]})
        events = [{"choices": [{"delta": {"role": "assistant"}}]}, {"choices": [{"delta": {"content": "Ninety "}}]},
                  {"choices": [{"delta": {"content": "days [1]."}}]}]
        sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
    return httpx.Response(404, json={"error": "not found"})


def client(url="http://127.0.0.1:1234", key=""):
    c = OpenAICompat(url, api_key=key)
    c.http = httpx.Client(base_url=base_v1(url), headers=c.http.headers, transport=httpx.MockTransport(fake_server))
    return c


def test_openai_client_speaks_the_protocol():
    SEEN.clear()
    c = client(key="sk-local")
    assert c.version() == "OpenAI-compatible"
    assert c.models() == ["my-chat-7b", "ocr-vl-1b", "text-embed-small"]
    assert c.embed("text-embed-small", ["ab", "abcd"]) == [[2.0, 1.0, 0.0], [4.0, 1.0, 1.0]]  # re-ordered by index
    assert "".join(c.chat("my-chat-7b", [{"role": "user", "content": "q"}])) == "Ninety days [1]."
    assert c.vision("ocr-vl-1b", "OCR:", "aGVsbG8=") == "Scanned text"
    assert all(h == "Bearer sk-local" for _, _, h in SEEN)
    with pytest.raises(Exception):
        list(c.pull("x"))


def test_openai_url_must_be_local_unless_allowed():
    with pytest.raises(ValueError):
        OpenAICompat("http://192.168.1.9:1234")
    assert base_v1("http://127.0.0.1:1234/") == "http://127.0.0.1:1234/v1"
    assert base_v1("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080/v1"


def test_capabilities_are_guessed_from_names():
    assert guess_capabilities("nomic-embed-text-v1.5") == ["embedding"]
    assert guess_capabilities("Qwen3-Embedding-0.6B") == ["embedding"]
    assert "vision" in guess_capabilities("paddleocr-vl-1.6") and "vision" in guess_capabilities("qwen2.5-vl-7b")
    assert guess_capabilities("llama-3.2-3b-instruct") == ["completion"]
    assert guess_capabilities("gemma3:1b") == ["completion"] and "vision" in guess_capabilities("gemma-3-4b-it")


def test_daftar_runs_on_an_openai_compatible_engine(tmp_path):
    app = create_app(tmp_path, ollama_factory=FakeOllama,
                     openai_factory=lambda url, allow, key: client(url, key))
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-Daftar": "1"}) as c:
        assert c.put("/api/settings", json={"engine": "openai", "openai_url": "http://127.0.0.1:1234"}).status_code == 200
        status = c.get("/api/status").json()
        assert status["ollama"] and status["chat_model"] == "my-chat-7b"
        assert status["embed_active"] == "text-embed-small" and status["ocr_active"] == "ocr-vl-1b"
        cid = c.post("/api/collections", json={"name": "C"}).json()["id"]
        c.post("/api/documents", files=[("files", ("msa.txt", CONTRACT.encode(), "text/plain"))],
               data={"collection_ids": [str(cid)]})
        wait_ingest(c.app)
        assert c.get("/api/documents").json()[0]["entries"][0]["embed_model"] == "text-embed-small"
        chat = c.post("/api/chats", json={"collection_id": cid}).json()["id"]
        events = [json.loads(x) for x in c.post(f"/api/chats/{chat}/ask", json={"question": "termination notice"}).text.splitlines()]
        assert events[0]["model"] == "my-chat-7b" and events[0]["mode"] == "hybrid"
        assert "".join(e.get("text", "") for e in events if e["type"] == "token") == "Ninety days [1]."
        # remote servers need the same explicit opt-in as a remote Ollama
        assert c.put("/api/settings", json={"openai_url": "http://10.0.0.5:1234"}).status_code == 400
        # unknown engine names fall back to Ollama
        assert c.put("/api/settings", json={"engine": "something"}).json()["engine"] == "ollama"


@pytest.mark.live
def test_live_openai_engine_against_ollama_v1(tmp_path):
    """Ollama also serves the OpenAI API at /v1: exercise the real protocol end to end."""
    c = OpenAICompat("http://127.0.0.1:11434")
    models = c.models()
    assert models
    chat_model = next(m for m in models if "gemma3" in m or "qwen2.5" in m)
    answer = "".join(c.chat(chat_model, [{"role": "user", "content": "Reply with exactly the word: ready"}]))
    assert "ready" in answer.lower()
    if any("qwen3-embedding" in m for m in models):
        vecs = c.embed(next(m for m in models if "qwen3-embedding" in m), ["a", "b"])
        assert len(vecs) == 2 and len(vecs[0]) > 100
