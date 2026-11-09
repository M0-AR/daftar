"""Where Daftar keeps its data, and the user's settings."""
import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "ollama_url": "http://127.0.0.1:11434",
    "allow_remote_ollama": False,   # a server on another machine means documents leave this one
    "chat_model": "gemma3:1b",
    "embed_model": "nomic-embed-text",
    "temperature": 0.2,             # "Creativity": 0 precise .. 2 varied
    "top_k": 6,                     # passages given to the model
    "scan_k": 24,                   # candidate passages examined before picking top_k
    "chunk_size": 1000,             # characters per passage
    "chunk_overlap": 150,
    "rerank": True,                 # "Improve answer relevance"
    "debug": False,
    "theme": "dark",
}

LIMITS = {  # numeric settings: (min, max, type)
    "temperature": (0.0, 2.0, float),
    "top_k": (1, 20, int),
    "scan_k": (4, 100, int),
    "chunk_size": (200, 4000, int),
    "chunk_overlap": (0, 1000, int),
}


def data_dir() -> Path:
    if os.environ.get("DAFTAR_HOME"):
        base = Path(os.environ["DAFTAR_HOME"])
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home())) / "Daftar"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Daftar"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "daftar"
    base.mkdir(parents=True, exist_ok=True)
    return base


def clean(changes: dict) -> dict:
    """Keep known keys, coerce types, clamp numbers into range."""
    out = {}
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        if key in LIMITS:
            lo, hi, kind = LIMITS[key]
            value = kind(min(hi, max(lo, kind(value))))
        elif isinstance(DEFAULTS[key], bool):
            value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "yes")
        else:
            value = str(value).strip()
        out[key] = value
    if "theme" in out and out["theme"] not in ("dark", "light"):
        out["theme"] = "dark"
    return out


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.values = dict(DEFAULTS)
        if path.exists():
            self.values.update(clean(json.loads(path.read_text())))

    def __getitem__(self, key):
        return self.values[key]

    def update(self, changes: dict) -> dict:
        self.values.update(clean(changes))
        if self.values["chunk_overlap"] >= self.values["chunk_size"]:
            self.values["chunk_overlap"] = self.values["chunk_size"] // 4
        self.path.write_text(json.dumps(self.values, indent=2))
        return self.values

    def reset(self) -> dict:
        self.values = dict(DEFAULTS)
        self.path.write_text(json.dumps(self.values, indent=2))
        return self.values
