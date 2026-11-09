"""Where Daftar keeps its data, and the user's settings."""
import json
import os
import sys
from pathlib import Path

DEFAULTS = {
    "ollama_url": "http://127.0.0.1:11434",
    "chat_model": "gemma3:1b",
    "embed_model": "nomic-embed-text",
    "top_k": 6,
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


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.values = dict(DEFAULTS)
        if path.exists():
            self.values.update({k: v for k, v in json.loads(path.read_text()).items() if k in DEFAULTS})

    def __getitem__(self, key):
        return self.values[key]

    def update(self, changes: dict) -> dict:
        self.values.update({k: v for k, v in changes.items() if k in DEFAULTS})
        self.path.write_text(json.dumps(self.values, indent=2))
        return self.values
