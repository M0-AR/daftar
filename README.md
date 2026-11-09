# Daftar

Private AI document intelligence. Nothing leaves the device.

A desktop app that lets you chat with contracts, reports and files fully offline. Documents are
indexed on your computer, questions are answered by a local model through [Ollama], and every
answer cites the passages it came from.

- **100% local**: the app listens only on `127.0.0.1` and talks only to your local Ollama.
- **Zero telemetry**: no accounts, no analytics, no update checks, no CDN assets.
- **Works offline** once models are installed.
- **Lightweight**: about 1 GB with the default `gemma3:1b`; swap in any Ollama model in Settings.

## Run

Requires Python 3.11+ and [Ollama] running locally.

```bash
uv venv --system-site-packages .venv        # system site-packages: GTK/WebKit for the native window on Linux
uv pip install -p .venv -e ".[desktop]"
.venv/bin/daftar                           # native window (falls back to the browser)
.venv/bin/daftar --browser                 # in the web browser
.venv/bin/daftar --headless --port 8765    # server only
```

## Models

| Role | Default | Fallback when missing |
|---|---|---|
| Chat | `gemma3:1b` | first installed non-embedding Ollama model |
| Embeddings | `nomic-embed-text` | keyword search only (SQLite FTS5) |

Install with `ollama pull <name>`, or import a GGUF you downloaded yourself:

```bash
printf 'FROM ./gemma-3-1b-it-Q4_K_M.gguf\n' > Modelfile && ollama create gemma3:1b -f Modelfile
printf 'FROM ./nomic-embed-text-v1.5.Q8_0.gguf\n' > Modelfile && ollama create nomic-embed-text -f Modelfile
```

Documents indexed before the embedding model existed can be re-indexed from Settings.

## How it works

1. **Ingest** (`daftar/ingest.py`): PDF (per page), DOCX (incl. tables), TXT, MD, CSV → ~1000-character
   chunks that break on whitespace and overlap by ~150 characters.
2. **Index** (`daftar/store.py`): SQLite with an FTS5 keyword index and normalized float32 embeddings.
3. **Retrieve** (`daftar/rag.py`): BM25 keyword hits + cosine similarity, fused by reciprocal rank,
   always scoped to one collection.
4. **Answer** (`daftar/server.py`): the top passages go to the chat model with instructions to answer only
   from them and cite `[n]`; tokens stream to the UI as NDJSON.

Data lives in `~/.local/share/daftar` (Linux), `~/Library/Application Support/Daftar` (macOS) or
`%APPDATA%\Daftar` (Windows); override with `DAFTAR_HOME`.

## Test

```bash
uv pip install -p .venv -e ".[dev]"
.venv/bin/python -m pytest          # offline suite (fake Ollama)
.venv/bin/python -m pytest -m live  # end-to-end against the local Ollama
```

## Build a standalone app

Run on each target OS: `python scripts/build.py` → `dist/Daftar/`.

[Ollama]: https://ollama.com
