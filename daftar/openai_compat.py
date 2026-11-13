"""Client for any OpenAI-compatible local server: LM Studio, Jan, llama.cpp's llama-server, vLLM, LocalAI,
KoboldCpp, GPT4All, or Ollama's own /v1. Same methods as the Ollama client, so the rest of Daftar does not care
which engine runs the models."""
import json

import httpx

from daftar.ollama import LOOPBACK, OllamaError, _error_text, check_url

# Default addresses of well-known local servers (probed by "Detect").
KNOWN_SERVERS = [
    ("Ollama", "http://127.0.0.1:11434"),
    ("LM Studio", "http://127.0.0.1:1234"),
    ("Jan", "http://127.0.0.1:1337"),
    ("llama.cpp / LocalAI", "http://127.0.0.1:8080"),
    ("vLLM", "http://127.0.0.1:8000"),
    ("KoboldCpp", "http://127.0.0.1:5001"),
    ("GPT4All", "http://127.0.0.1:4891"),
]


def base_v1(url: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


def guess_capabilities(name: str) -> list[str]:
    """OpenAI's /v1/models does not report capabilities, so infer them from common naming."""
    n = name.lower()
    if any(k in n for k in ("embed", "bge-", "e5-", "gte-", "minilm", "nomic")):
        return ["embedding"]
    gemma3_vision = ("gemma-3" in n or "gemma3" in n) and "1b" not in n  # Gemma 3 1B is text-only
    if gemma3_vision or any(k in n for k in ("ocr", "-vl", "vl-", "vision", "llava", "minicpm-v", "pixtral", "moondream")):
        return ["completion", "vision"]
    return ["completion"]


class OpenAICompat:
    def __init__(self, url: str, allow_remote: bool = False, api_key: str = ""):
        check_url(url, allow_remote)
        headers = {"Authorization": f"Bearer {api_key or 'local'}"}  # many local servers want any non-empty key
        self.http = httpx.Client(base_url=base_v1(url), headers=headers, trust_env=False,
                                 timeout=httpx.Timeout(10.0, read=600.0))

    def _post(self, path: str, body: dict, timeout=None) -> dict:
        r = self.http.post(path, json=body, **({"timeout": timeout} if timeout else {}))
        if r.status_code != 200:
            raise OllamaError(_error_text(r.text))
        return r.json()

    def version(self) -> str:
        r = self.http.get("/models")
        if r.status_code != 200:
            raise OllamaError(_error_text(r.text))
        return "OpenAI-compatible"

    def model_list(self) -> list[dict]:
        data = self.http.get("/models").json().get("data", [])
        return sorted(({"name": m["id"], "digest": "", "size": 0, "family": "", "parameters": "", "quantization": ""}
                       for m in data if m.get("id")), key=lambda m: m["name"])

    def models(self) -> list[str]:
        return [m["name"] for m in self.model_list()]

    def show(self, model: str) -> dict:
        return {"context_length": None, "capabilities": guess_capabilities(model), "architecture": ""}

    def running(self) -> list[dict]:
        return []  # not exposed by the OpenAI API; the GPU check only works with Ollama

    def embed(self, model: str, texts: list[str], num_ctx: int | None = None) -> list[list[float]]:
        data = self._post("/embeddings", {"model": model, "input": texts})["data"]
        return [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2, num_ctx: int = 8192,
             think: bool = False):
        """Stream answer text (server-sent events). The context window and reasoning are set in the server app itself."""
        body = {"model": model, "messages": messages, "temperature": temperature, "stream": True}
        with self.http.stream("POST", "/chat/completions", json=body) as r:
            if r.status_code != 200:
                raise OllamaError(_error_text(r.read().decode()))
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                part = json.loads(payload)
                if part.get("error"):
                    raise OllamaError(str(part["error"]))
                choices = part.get("choices") or [{}]
                yield (choices[0].get("delta") or {}).get("content") or ""

    def vision(self, model: str, prompt: str, image_b64: str) -> str:
        # Images go inline as a base64 data URL: the form local servers accept (remote URLs often are not).
        body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}]}]}
        data = self._post("/chat/completions", body, timeout=httpx.Timeout(10.0, read=1800.0))
        return data["choices"][0]["message"].get("content") or ""

    def pull(self, name: str):
        raise OllamaError("This engine downloads models in its own app (for example LM Studio's model search). "
                          "Download there, then pick the model in Daftar.")


def detect_servers(timeout: float = 0.6) -> list[dict]:
    """Probe the default addresses of known local servers; return the ones that answer."""
    found = []
    for name, url in KNOWN_SERVERS:
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as c:
                r = c.get(base_v1(url) + "/models")
                if r.status_code == 200:
                    models = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
                    found.append({"name": name, "url": url, "models": len(models)})
        except (httpx.HTTPError, ValueError):
            continue
    return found


assert all(httpx.URL(u).host in LOOPBACK for _, u in KNOWN_SERVERS)
