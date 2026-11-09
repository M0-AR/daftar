# Feature parity with the reference product

Daftar is an independent rebuild of the feature set advertised for a commercial local-RAG desktop app
(reviewed 2026-11-09 from its public website, FAQ and published screenshots). No code, text, name or
artwork from that product is used; only the publicly described capabilities were taken as requirements.

| Advertised capability | Daftar | Where |
|---|---|---|
| 100% local, no cloud, no telemetry, no account | ✅ loopback server, DNS-rebinding + CSRF guards, no external URLs (tested) | `server.py`, `tests/test_privacy.py` |
| Works offline after setup | ✅ only model download touches the internet | `ollama.py` |
| Any Ollama model, switch anytime; default small model | ✅ default `gemma3:1b`, automatic fallback to any installed model | Settings, `resolve_chat_model` |
| Download new models from Settings | ✅ streamed progress via Ollama `/api/pull` | `POST /api/models/pull` |
| Custom Ollama server address | ✅ loopback by default; remote needs an explicit opt-in | `check_url`, Settings → Connection |
| Upload PDF, DOCX, TXT, Markdown (drag & drop) | ✅ + CSV | `ingest.py`, Documents |
| Assign to one or multiple collections in one upload dialog | ✅ | Upload dialog, `POST /api/documents` |
| One document in several collections, each with its own vector store | ✅ stored once, indexed per collection (`entries`) | `store.py` |
| File Information: which collections, pieces per collection | ✅ + add to / remove from a collection, re-index | More info dialog |
| Collections: indexing status, storage, content pieces, readiness % | ✅ | Collections, Check status |
| Optimize search performance | ✅ re-index stale/failed, FTS optimize, ANALYZE, preload vectors | `POST /api/collections/{id}/optimize` |
| Source citations with match % on every answer | ✅ 0–100 score (similarity + keyword coverage), `[n]` links | `rag.py`, chat |
| Click-through from a source to the file preview | ✅ highlights matching words | chat sources |
| File preview + word count + read time | ✅ | Preview dialog |
| Export as PDF, DOCX, Markdown, Plain text, HTML | ✅ generated locally | `exporter.py` |
| Creativity level slider 0–2 | ✅ = temperature | Settings → Answers |
| Passages to use / Passages to scan | ✅ `top_k` / `scan_k` | Settings → Answers |
| Text block size / overlap | ✅ changing them marks indexes stale; one-click re-index | Settings → Reading documents |
| Improve answer relevance toggle | ✅ rerank + drop weak passages | `rag.py` |
| Developer debug mode with technical logs | ✅ per-answer retrieval/timing details + in-memory log view | Settings, chat |
| Dark mode, Reset to defaults | ✅ dark/light themes | Settings |
| Edit, copy, regenerate, delete any message | ✅ editing a question re-asks it | chat |
| Name chats, choose collection per chat, search conversation history | ✅ search covers titles and message text | sidebar, chat header |
| Streamed responses | ✅ NDJSON stream, Stop button | chat |
| Search view, Help, collapsible sidebar | ✅ | sidebar |
| Small talk / "which files do you have?" | ✅ file list in the prompt; unrelated passages are not cited | `rag.build_messages` |
| Windows & macOS builds | ⚠️ PyInstaller script for each OS; only Linux built and tested here | `scripts/build.py` |

## Deliberately not copied
- Pricing tiers, licensing/plan badge, early-access offer: not product features of a local tool.
- Marketing copy, product name, logo, colours and taglines.

## Known limits
- Scanned PDFs need OCR (not implemented).
- Arabic/RTL text exports to PDF without glyph shaping (DOCX/HTML/MD/TXT are fine).
- Match % in keyword-only mode is word coverage; it becomes meaning-aware once a search model is installed.
