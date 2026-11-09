"""Minimal client for a local Ollama server. Refuses anything that is not loopback."""
import json
from urllib.parse import urlparse

import httpx

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class OllamaError(RuntimeError):
    pass


def check_local(url: str) -> str:
    host = urlparse(url).hostname
    if host not in LOOPBACK:
        raise ValueError(f"Ollama must run on this machine (127.0.0.1/localhost), got {host!r}")
    return url.rstrip("/")


# Retrieval-tuned embedding models expect task prefixes on their inputs.
PREFIXES = {
    "nomic": ("search_query: ", "search_document: "),
    "embeddinggemma": ("task: search result | query: ", "title: none | text: "),
}


def prefixes(model: str) -> tuple[str, str]:
    for key, value in PREFIXES.items():
        if key in model:
            return value
    return ("", "")


class Ollama:
    def __init__(self, url: str):
        # trust_env=False: never route local traffic through an HTTP(S)_PROXY from the environment.
        self.http = httpx.Client(base_url=check_local(url), trust_env=False,
                                 timeout=httpx.Timeout(10.0, read=600.0))

    def _post(self, path: str, body: dict) -> dict:
        r = self.http.post(path, json=body)
        if r.status_code != 200:
            raise OllamaError(_error_text(r.text))
        return r.json()

    def version(self) -> str:
        return self.http.get("/api/version").json()["version"]

    def models(self) -> list[str]:
        return sorted(m["name"] for m in self.http.get("/api/tags").json()["models"])

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return self._post("/api/embed", {"model": model, "input": texts})["embeddings"]

    def chat(self, model: str, messages: list[dict]):
        """Yield answer text pieces as Ollama streams them."""
        body = {"model": model, "messages": messages, "stream": True,
                "options": {"num_ctx": 8192, "temperature": 0.2}}
        with self.http.stream("POST", "/api/chat", json=body) as r:
            if r.status_code != 200:
                raise OllamaError(_error_text(r.read().decode()))
            for line in r.iter_lines():
                if not line:
                    continue
                part = json.loads(line)
                if part.get("error"):
                    raise OllamaError(part["error"])
                yield part.get("message", {}).get("content", "")
                if part.get("done"):
                    return


def _error_text(raw: str) -> str:
    try:
        return json.loads(raw)["error"]
    except (ValueError, KeyError, TypeError):
        return raw.strip() or "Ollama request failed"
