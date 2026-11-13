"""Built-in engine: run llama.cpp's server as a private child process.

The user does not need Ollama or any other app: Daftar finds a `llama-server` program and a `.gguf`
model file, starts the server on a free loopback port when the first question is asked, and stops it
when Daftar exits. It speaks the same OpenAI-compatible API as the other engines.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from daftar.config import data_dir
from daftar.ollama import OllamaError
from daftar.openai_compat import OpenAICompat

log = logging.getLogger(__name__)
CTX = 8192
START_TIMEOUT = 180  # a 2B model on a processor can take a minute to load


def _roots() -> list[Path]:
    return [Path(__file__).resolve().parent.parent / "vendor" / "llama.cpp",
            data_dir() / "llama.cpp", Path.home() / "models" / "llama.cpp"]


def _model_roots() -> list[Path]:
    return [Path.home() / "models", data_dir() / "models"]


def find_binaries(configured: str = "", roots: list[Path] | None = None) -> list[Path]:
    """llama-server programs to try, best first. An explicitly configured path comes first."""
    chosen: list[Path] = []
    found: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path, explicit: bool = False):
        try:
            path = path.expanduser().resolve()
        except OSError:
            return
        if path in seen or not path.is_file() or path.name not in ("llama-server", "llama-server.exe"):
            return
        if os.name != "nt" and not os.access(path, os.X_OK):
            return
        seen.add(path)
        (chosen if explicit else found).append(path)

    for explicit in (configured, os.environ.get("DAFTAR_LLAMA_SERVER", "")):
        if explicit:
            add(Path(explicit), True)
    for root in roots if roots is not None else _roots():
        if root.is_dir():
            for path in root.rglob("llama-server*"):
                add(path)
    # A Vulkan/CUDA/Metal build is faster than the processor-only one, so try it first.
    return chosen + sorted(found, key=lambda p: ("cpu" in str(p.parent).lower(), str(p)))


def find_models(configured: str = "", roots: list[Path] | None = None) -> list[Path]:
    """Chat model files (.gguf), best guess first. Projector files (mmproj) are not chat models."""
    chosen: list[Path] = []
    found: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path, explicit: bool = False):
        try:
            path = path.expanduser().resolve()
        except OSError:
            return
        if path in seen or not path.is_file() or path.suffix.lower() != ".gguf":
            return
        if "mmproj" in path.name.lower():
            return
        seen.add(path)
        (chosen if explicit else found).append(path)

    for explicit in (configured, os.environ.get("DAFTAR_LLAMA_MODEL", "")):
        if explicit:
            add(Path(explicit), True)
    for root in roots if roots is not None else _model_roots():
        if root.is_dir():
            for path in root.rglob("*.gguf"):
                add(path)

    def score(path: Path) -> float:
        name = path.name.lower()
        value = 0
        if "qwen3.5" in name:
            value += 100
        if "instruct" in name or "chat" in name:
            value += 20
        if any(k in name for k in ("-vl", "vl-", "vision", "llava", "minicpm-v", "internvl", "smolvlm", "ocr")):
            value -= 50
        if "embed" in name:
            value -= 200
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return -value, -size

    return chosen + sorted(found, key=score)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _library_path(binary: Path) -> dict[str, str]:
    """llama.cpp's tarballs ship their shared libraries next to the program, but a stale copy in the
    system loader cache can win (measured: an old /opt/llama build crashed a b12360 server). Point the
    loader at the program's own folder first."""
    env = os.environ.copy()
    parent = str(binary.parent)
    if os.name == "nt":
        env["PATH"] = parent + os.pathsep + env.get("PATH", "")
    else:
        env["LD_LIBRARY_PATH"] = parent + (os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        if sys.platform == "darwin":
            env["DYLD_LIBRARY_PATH"] = parent + (os.pathsep + env["DYLD_LIBRARY_PATH"] if env.get("DYLD_LIBRARY_PATH") else "")
    return env


def command(binary: Path, model: Path, port: int, ctx: int = CTX) -> list[str]:
    return [str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-ngl", "99", "-c", str(ctx), "--parallel", "1", "--no-webui", "--alias", model.stem]


class LlamaServer:
    """Owns one llama-server process. Restarts it if it died between questions."""

    def __init__(self, binaries: list[Path], model: Path, home: Path, ctx: int = CTX):
        self.binaries = [Path(b) for b in binaries]
        self.model = Path(model)
        self.alias = self.model.stem
        self.ctx = ctx
        self.log_path = Path(home) / "llama-server.log"
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._log = None
        self._lock = threading.Lock()

    def healthy(self) -> bool:
        if not self._url:
            return False
        try:
            return httpx.get(self._url + "/health", timeout=2, trust_env=False).status_code == 200
        except httpx.HTTPError:
            return False

    def ensure(self) -> str:
        with self._lock:
            if self._proc and self._proc.poll() is None and self.healthy():
                return self._url
            self._stop_locked()
            errors = []
            for binary in self.binaries:
                try:
                    self._start(binary)
                    log.info("built-in engine ready: %s (%s) on %s", self.alias, binary, self._url)
                    return self._url
                except Exception as e:
                    errors.append(f"{binary}: {e}")
                    log.warning("built-in engine failed to start with %s: %s", binary, e)
                    self._stop_locked()
            raise OllamaError("The built-in engine could not start. " + " · ".join(errors[-2:])
                              + f" Details: {self.log_path}")

    def _start(self, binary: Path):
        port = free_port()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "ab")
        cmd = command(binary, self.model, port, self.ctx)
        self._log.write(f"\n=== {time.ctime()} {' '.join(cmd)}\n".encode())
        self._log.flush()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._proc = subprocess.Popen(cmd, env=_library_path(binary), stdin=subprocess.DEVNULL,
                                      stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags)
        self._url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise OllamaError(f"it stopped with code {self._proc.returncode}")
            try:
                if httpx.get(self._url + "/health", timeout=2, trust_env=False).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise OllamaError("it did not finish loading within 3 minutes")

    def _stop_locked(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None
        self._url = None
        if self._log:
            self._log.close()
            self._log = None

    def stop(self):
        with self._lock:
            self._stop_locked()

    def running(self) -> bool:
        return bool(self._proc and self._proc.poll() is None and self.healthy())


class LlamaClient:
    """Same interface as the Ollama/OpenAI clients. Metadata comes from the files on disk, so the
    status page and setup card do not pay for a model load; the server starts with the first answer."""

    def __init__(self, server: LlamaServer):
        self.server = server

    def version(self) -> str:
        return "llama.cpp (built-in)"

    def model_list(self) -> list[dict]:
        try:
            size = self.server.model.stat().st_size
        except OSError:
            size = 0
        return [{"name": self.server.alias, "digest": "", "size": size, "family": "gguf",
                 "parameters": "", "quantization": ""}]

    def models(self) -> list[str]:
        return [self.server.alias]

    def show(self, model: str) -> dict:
        return {"context_length": self.server.ctx, "capabilities": ["completion"], "architecture": "gguf"}

    def running(self) -> list[dict]:
        return []  # llama-server does not report per-model GPU memory

    def embed(self, model: str, texts: list[str], num_ctx: int | None = None) -> list[list[float]]:
        raise OllamaError("The built-in engine cannot compute embeddings yet; Daftar searches exact words. "
                          "Use Ollama for meaning-based search.")

    def vision(self, model: str, prompt: str, image_b64: str) -> str:
        raise OllamaError("The built-in engine has no vision model; scanned pages need Ollama and a vision model.")

    def pull(self, name: str):
        raise OllamaError("The built-in engine uses .gguf model files; choose one in Settings → AI engine.")

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2, num_ctx: int = 8192,
             think: bool = False):
        inner = OpenAICompat(self.server.ensure(), allow_remote=False)
        # Per-request template kwargs avoid a restart when "Let the model reason first" is toggled.
        yield from inner.chat(self.server.alias, messages, temperature, num_ctx, think,
                              extra={"chat_template_kwargs": {"enable_thinking": bool(think)}})
