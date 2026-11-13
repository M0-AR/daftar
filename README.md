# Daftar

Private document chat that runs entirely on your computer.

Put contracts, reports and notes into collections, ask questions in plain language, and get answers
that cite the exact passages they came from, with a match score. Everything (files, indexes, chats)
stays in a local data folder; the app only talks to a local AI engine (built-in [llama.cpp], or [Ollama]).

## Features
- **Collections**: separate knowledge bases; one file can be in several, each with its own index.
  Readiness %, status check and one-click "optimize search".
- **Documents**: PDF, Word, Markdown, text, CSV, HTML, notebooks and 40+ code formats; drag & drop; filters by collection and type; preview
  with word count and reading time; export as PDF, DOCX, Markdown, text or HTML.
- **Chat**: one collection or all of them; streamed answers, `[n]` citations, sources with section and match %, edit / copy / regenerate / delete
  messages, rename chats, search all conversations.
- **Search**: find passages without asking the AI.
- **Settings in plain language**: creativity (0–2), passages to use / scan, text block size / overlap,
  improve answer relevance, model picker + download, server address, debug mode with logs, dark/light,
  reset to defaults.
- **Private by design**: listens on 127.0.0.1 only, rejects other host names and cross-site requests,
  no telemetry, no external assets. A non-local Ollama server needs an explicit opt-in.

See [docs/GAP.md](docs/GAP.md) for the feature checklist this build was verified against.

## Run
Requires Python 3.11+. Daftar can run its own [llama.cpp] engine: put `llama-server` and a `.gguf`
chat model anywhere under `~/models` (for example `~/models/llama.cpp/...` and `~/models/qwen3.5-2b-gguf/`)
and it finds them; Settings → AI engine also takes explicit paths. Ollama and other OpenAI-compatible
apps (LM Studio, Jan, vLLM, LocalAI, KoboldCpp) remain supported and are picked in the same place.

```bash
uv venv --system-site-packages .venv        # Linux: system GTK/WebKit for the native window
uv pip install -p .venv -e ".[desktop]"
.venv/bin/daftar                            # native window (falls back to the browser)
.venv/bin/daftar --browser                  # in the web browser
.venv/bin/daftar --headless --port 8765     # server only
```

## Models
| Role | Default | When missing |
|---|---|---|
| Chat | `qwen3.5:2b` | largest installed chat model |
| Search (embeddings) | `qwen3-embedding:0.6b` | any installed embedding model, else keyword search (SQLite FTS5) |
| Reading scans (OCR) | `paddleocr-vl:1.6` | any installed vision model, else scans are reported as unreadable |

The built-in llama.cpp engine uses a `.gguf` chat model directly and needs no Ollama. It is chat-only
for now: search uses exact words (FTS5, no embeddings) and scanned pages are reported as unreadable.
The Ollama rows above apply when Ollama is the chosen engine.

Reasoning models (Qwen3.5) think before answering by default; Daftar turns that off for speed. Re-enable it in Settings → Answers → Let the model reason first. With Ollama, Qwen3.5 needs a recent version (0.40+); if it is not installed, Daftar answers with the largest chat model you have. With no chat model at all, Chat shows a setup checklist sized to this computer's memory, with a one-click download (Ollama) or a detected GGUF file, and a smoke test.

With Ollama, install from Settings → Download a model or with `ollama pull <name>`; the built-in engine
reads the `.gguf` file in place. To add a GGUF to Ollama:

```bash
printf 'FROM ./Qwen3.5-2B-Q4_K_M.gguf\nRENDERER qwen3.5\nPARSER qwen3.5\n' > Modelfile && ollama create qwen3.5:2b -f Modelfile
printf 'FROM ./Qwen3-Embedding-0.6B-Q8_0.gguf\n' > Modelfile && ollama create qwen3-embedding:0.6b -f Modelfile
```
Then Collections → Check status → Optimize search to add semantic search to existing documents.

## How it works
1. **Read** (`ingest.py`): text per page (PDF) or per file; cached once per document.
2. **Index** (`store.py`): per collection, ~1000-character overlapping passages → FTS5 keyword index +
   normalized embeddings.
3. **Retrieve** (`rag.py`): keyword (BM25, prefix-matched) + vector candidates, scored as
   `0.7·similarity + 0.3·keyword coverage` → match %; optional rerank drops passages under half the best score.
4. **Answer** (`server.py`): passages + the collection's file list go to the chat model with instructions
   to cite `[n]` and not invent facts; tokens stream to the UI.

Data folder: `~/.local/share/daftar` (Linux), `~/Library/Application Support/Daftar` (macOS),
`%APPDATA%\Daftar` (Windows); override with `DAFTAR_HOME`.

## Test
```bash
uv pip install -p .venv -e ".[dev]"
.venv/bin/python -m pytest          # offline suite with a fake engine
.venv/bin/python -m pytest -m live  # built-in llama.cpp smoke test + end-to-end against Ollama
```

## Build a standalone app
On each target OS: `python scripts/build.py` → `dist/Daftar/`, then `python scripts/smoke_build.py`.
`dist/Daftar/Daftar --self-test` checks the bundled libraries. `.github/workflows/build.yml` builds
and smoke-tests Windows, macOS and Linux on GitHub Actions.

[Ollama]: https://ollama.com
[llama.cpp]: https://github.com/ggml-org/llama.cpp
