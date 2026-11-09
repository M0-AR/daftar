"""Minimal Ollama client. Loopback only, unless the user explicitly allows a remote server."""
import json
from urllib.parse import urlparse

import httpx

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class OllamaError(RuntimeError):
    pass


def check_url(url: str, allow_remote: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Not a valid server address: {url!r}")
    if parsed.hostname not in LOOPBACK and not allow_remote:
        raise ValueError(f"Ollama must run on this computer (127.0.0.1/localhost), got {parsed.hostname!r}. "
                         "Turn on 'Allow a server on another computer' to use it.")
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
    def __init__(self, url: str, allow_remote: bool = False):
        # trust_env=False: never route traffic through an HTTP(S)_PROXY from the environment.
        self.http = httpx.Client(base_url=check_url(url, allow_remote), trust_env=False,
                                 timeout=httpx.Timeout(10.0, read=600.0))

    def _post(self, path: str, body: dict) -> dict:
        r = self.http.post(path, json=body)
        if r.status_code != 200:
            raise OllamaError(_error_text(r.text))
        return r.json()

    def version(self) -> str:
        return self.http.get("/api/version").json()["version"]

    def model_list(self) -> list[dict]:
        out = []
        for m in self.http.get("/api/tags").json()["models"]:
            d = m.get("details") or {}
            out.append({"name": m["name"], "size": m.get("size", 0), "family": d.get("family", ""),
                        "parameters": d.get("parameter_size", ""), "quantization": d.get("quantization_level", "")})
        return sorted(out, key=lambda m: m["name"])

    def models(self) -> list[str]:
        return [m["name"] for m in self.model_list()]

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return self._post("/api/embed", {"model": model, "input": texts})["embeddings"]

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2):
        """Yield answer text pieces as Ollama streams them."""
        body = {"model": model, "messages": messages, "stream": True,
                "options": {"num_ctx": 8192, "temperature": temperature}}
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

    def pull(self, name: str):
        """Yield Ollama's download progress events for a model."""
        with self.http.stream("POST", "/api/pull", json={"model": name, "stream": True},
                              timeout=httpx.Timeout(10.0, read=None)) as r:
            if r.status_code != 200:
                raise OllamaError(_error_text(r.read().decode()))
            for line in r.iter_lines():
                if line:
                    part = json.loads(line)
                    if part.get("error"):
                        raise OllamaError(part["error"])
                    yield part


def _error_text(raw: str) -> str:
    try:
        return json.loads(raw)["error"]
    except (ValueError, KeyError, TypeError):
        return raw.strip() or "Ollama request failed"
