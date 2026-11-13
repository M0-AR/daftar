"""Built-in engine: run llama.cpp's server as a private child process.

The user does not need Ollama or any other app: Daftar finds a `llama-server` program and a `.gguf`
model file, starts the server on a free loopback port when the first question is asked, and stops it
when Daftar exits. It speaks the same OpenAI-compatible API as the other engines.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import signal
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
    """Chat model files (.gguf), best guess first. Projector and embedding files are not chat models."""
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
        name = path.name.lower()
        if "mmproj" in name or "embed" in name:
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
    loader at the program's own folder first. In a frozen PyInstaller app, start from the original
    search path (LD_LIBRARY_PATH_ORIG), not the bundle's, so bundled Python libraries cannot leak in."""
    env = os.environ.copy()
    parent = str(binary.parent)
    if os.name == "nt":
        meipass = getattr(sys, "_MEIPASS", "")
        path = env.get("PATH", "")
        if meipass:
            path = os.pathsep.join(p for p in path.split(os.pathsep) if p and not p.startswith(meipass))
        env["PATH"] = parent + (os.pathsep + path if path else "")
    else:
        base = env.get("LD_LIBRARY_PATH_ORIG", env.get("LD_LIBRARY_PATH", ""))
        env["LD_LIBRARY_PATH"] = parent + (os.pathsep + base if base else "")
        if sys.platform == "darwin":
            env["DYLD_LIBRARY_PATH"] = parent + (os.pathsep + env["DYLD_LIBRARY_PATH"] if env.get("DYLD_LIBRARY_PATH") else "")
    return env


def command(binary: Path, model: Path, port: int, ctx: int = CTX) -> list[str]:
    return [str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-ngl", "99", "-c", str(ctx), "--parallel", "1", "--no-webui", "--alias", model.stem]


def _alive(pid: int) -> bool:
    if os.name == "nt":  # os.kill(pid, 0) would terminate the process on Windows
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel32.CloseHandle(handle)
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _cmdline(pid: int) -> str:
    """Best-effort command line, used to confirm a pid still belongs to one of our llama-servers."""
    try:
        if sys.platform.startswith("linux"):
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        if sys.platform == "darwin":
            out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                                 text=True, timeout=2)
            return out.stdout.strip()
        if os.name == "nt":
            # wmic is gone from recent Windows; CIM through PowerShell is the supported reader. If this
            # fails (policy, missing PowerShell), the caller treats the process as "not ours" and never kills it.
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            out = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, timeout=5, creationflags=flags)
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


class LlamaServer:
    """Owns one llama-server process. Restarts it if it died between questions."""

    def __init__(self, binaries: list[Path], model: Path, home: Path, ctx: int = CTX):
        self.binaries = [Path(b) for b in binaries]
        self.model = Path(model)
        self.alias = self.model.stem
        self.ctx = ctx
        self.home = Path(home)
        self.log_path = self.home / "llama-server.log"
        self.pid_path = self.home / "llama-server.pid"
        self.api_key = secrets.token_urlsafe(32)
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._log = None
        self._lock = threading.Lock()
        self._stale_checked = False

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
            if not self._stale_checked:
                self._kill_stale()
                self._stale_checked = True
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

    def _kill_stale(self):
        """Stop a llama-server left behind by an earlier crash. Only a process whose command line still
        names this exact model is touched; a live parent process means another Daftar instance owns it."""
        try:
            data = json.loads(self.pid_path.read_text())
        except (OSError, ValueError):
            return
        pid = data.get("pid")
        parent = data.get("parent")
        if not isinstance(pid, int) or pid <= 0:
            self.pid_path.unlink(missing_ok=True)
            return
        if isinstance(parent, int) and parent != os.getpid() and _alive(parent):
            log.info("built-in engine pid file belongs to another running Daftar instance; leaving it alone")
            return
        cmdline = _cmdline(pid)
        if not cmdline or "llama-server" not in cmdline or str(self.model) not in cmdline:
            self.pid_path.unlink(missing_ok=True)
            return
        if _alive(pid):
            log.warning("stopping an orphaned built-in engine (pid %s)", pid)
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):
                    if not _alive(pid):
                        break
                    time.sleep(0.25)
                else:
                    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except OSError as e:
                log.warning("could not stop orphaned built-in engine %s: %s", pid, e)
        self.pid_path.unlink(missing_ok=True)

    def _write_pid(self, cmd: list[str]):
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
            self.pid_path.write_text(json.dumps({"pid": self._proc.pid, "parent": os.getpid(), "cmd": cmd}))
        except OSError as e:
            log.warning("could not record built-in engine pid: %s", e)

    def _clear_pid(self):
        try:
            data = json.loads(self.pid_path.read_text())
        except (OSError, ValueError):
            return
        if data.get("parent") != os.getpid():
            return  # another Daftar instance owns this file; never delete its record
        if not self._proc or data.get("pid") == self._proc.pid:
            self.pid_path.unlink(missing_ok=True)

    def _rotate_log(self):
        try:
            if self.log_path.stat().st_size > 5_000_000:
                backup = self.log_path.with_suffix(".log.1")
                backup.unlink(missing_ok=True)
                self.log_path.replace(backup)
        except OSError:
            pass

    def _start(self, binary: Path):
        port = free_port()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_log()
        self._log = open(self.log_path, "ab")
        cmd = command(binary, self.model, port, self.ctx)
        self._log.write(f"\n=== {time.ctime()} {' '.join(cmd)}\n".encode())
        self._log.flush()
        env = _library_path(binary)
        env["LLAMA_API_KEY"] = self.api_key  # /health stays public; model and chat routes need this key
        if os.name == "nt":
            # PyInstaller points the DLL search at its bundle for child processes too (its docs
            # recommend undoing this before launching an external program).
            try:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            except (AttributeError, OSError):
                pass
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL,
                                      stdout=self._log, stderr=subprocess.STDOUT, creationflags=flags)
        self._url = f"http://127.0.0.1:{port}"
        self._write_pid(cmd)
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
        self._clear_pid()
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
        inner = OpenAICompat(self.server.ensure(), allow_remote=False, api_key=self.server.api_key)
        # Per-request template kwargs avoid a restart when "Let the model reason first" is toggled.
        yield from inner.chat(self.server.alias, messages, temperature, num_ctx, think,
                              extra={"chat_template_kwargs": {"enable_thinking": bool(think)}})
