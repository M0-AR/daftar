"""Local HTTP API + static UI. Binds to loopback; talks only to the configured Ollama server."""
import base64
import collections as pycollections
import hashlib
import json
import logging
import math
import time
import uuid
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anyio
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from pydantic import BaseModel

from daftar import APP_NAME, __version__
from daftar.config import Settings, data_dir
from daftar.exporter import FORMATS, export
from daftar.ingest import SUPPORTED, chunk_sections, clean_pdf_text, extract, page_image_png, scanned_pages, word_count
from daftar.ollama import LOOPBACK, Ollama, check_url, prefixes
from daftar.rag import Retriever, fit_prompt
from daftar.store import Store

log = logging.getLogger("daftar")
STATIC = Path(__file__).parent / "static"
READ_WPM = 200
MAX_UPLOAD_BYTES = 200 << 20  # per file


class RingLog(logging.Handler):
    """Keeps the last N log lines in memory for the in-app debug view (never written anywhere else)."""

    def __init__(self, size=400):
        super().__init__()
        self.lines = pycollections.deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.lines.append(self.format(record))


class LocalOnly:
    """Pure ASGI guard (BaseHTTPMiddleware would hide client disconnects from streaming responses,
    so a stopped answer would keep generating; Starlette recommends pure ASGI middleware).

    - Host must be loopback: blocks DNS-rebinding pages that resolve to 127.0.0.1.
    - A present Origin must be our own page (MCP transport rule for local servers).
    - Writes need the X-Daftar header: browsers cannot send it cross-origin without a CORS preflight,
      which is never granted."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        problem = None
        if (urlsplit("//" + host).hostname or "") not in LOOPBACK:
            problem = f"{APP_NAME} only answers on this computer"
        elif "origin" in headers and headers["origin"] != f"{scope['scheme']}://{host}":
            problem = "Requests from other websites are not allowed"
        elif scope["method"] not in ("GET", "HEAD") and headers.get("x-daftar") != "1":
            problem = "Missing X-Daftar header"
        if problem:
            return await JSONResponse({"detail": problem}, status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


def bare(name: str | None) -> str | None:
    """Model name without Ollama's implicit ':latest' tag (how index entries record it)."""
    return name[:-7] if name and name.endswith(":latest") else name


def installed(name: str, models: list[str]) -> str | None:
    """Return the installed Ollama name for `name` (handles the implicit :latest tag)."""
    for candidate in (name, f"{name}:latest"):
        if candidate in models:
            return candidate
    return None


class CollectionIn(BaseModel):
    name: str
    description: str = ""


class ChatIn(BaseModel):
    collection_id: int | None = None
    all_collections: bool | None = None
    title: str | None = None


class AskIn(BaseModel):
    question: str = ""
    resend: bool = False


class MessageEdit(BaseModel):
    content: str


class AddToCollections(BaseModel):
    collection_ids: list[int]


class PullIn(BaseModel):
    name: str


def create_app(home: Path | None = None, ollama_factory=Ollama) -> FastAPI:
    home = home or data_dir()
    files_dir = home / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    store = Store(home / "daftar.db")
    settings = Settings(home / "settings.json")
    retriever = Retriever(store)
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
    ring = RingLog()
    for old in [h for h in log.handlers if isinstance(h, RingLog)]:
        log.removeHandler(old)
    log.addHandler(ring)
    log.setLevel(logging.DEBUG)
    app = FastAPI(title=APP_NAME, version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.worker, app.state.settings = store, worker, settings

    def ollama():
        return ollama_factory(settings["ollama_url"], settings["allow_remote_ollama"])

    def file_path(doc: dict) -> Path:
        return files_dir / f"{doc['id']}{doc['ext']}"

    embed_cache = {"at": 0.0, "key": None, "model": None}

    def active_embed() -> str | None:
        """The search model actually in use (configured one, else any installed embedder), without ':latest'.
        Cached briefly so list views don't hammer Ollama."""
        key = (settings["ollama_url"], settings["embed_model"])
        if embed_cache["key"] != key or time.time() - embed_cache["at"] > 10:
            try:
                client = ollama()
                embed_cache["model"] = bare(resolve_embed_model(client, installed_models(client)))
            except Exception:
                embed_cache["model"] = None
            embed_cache.update(at=time.time(), key=key)
        return embed_cache["model"]

    def embed_ready() -> bool:
        return active_embed() is not None

    def is_stale(entry: dict) -> bool:
        """Would re-indexing change this entry? (Missing vectors only count if a search model is installed.)"""
        if entry["chunk_size"] != settings["chunk_size"] or entry["chunk_overlap"] != settings["chunk_overlap"]:
            return True
        active = active_embed()
        return active is not None and entry["embed_model"] != active

    def need(cond, code: int, msg: str):
        if not cond:
            raise HTTPException(code, msg)

    # --- ingestion ------------------------------------------------------------
    progress = {}  # entry id -> "Reading scanned page 3 of 12" (shown while indexing)

    def resolve_ocr_model(client, models: list[str]) -> str | None:
        """Configured OCR model if installed, else an installed vision model (OCR-named first, then largest)."""
        chosen = installed(settings["ocr_model"], models)
        if chosen:
            return chosen
        seeing = [m for m in models if "vision" in model_meta(client, m)["capabilities"]]
        return max(seeing, key=lambda m: ("ocr" in m.lower(), sizes.get(m, 0)), default=None)

    def ocr_prompt(client, model: str) -> str:
        # PaddleOCR-VL is trained on fixed task prompts ("OCR:", "Table Recognition:", ...); general VLMs need words.
        if model_meta(client, model).get("architecture") == "paddleocr" or "paddleocr" in model.lower():
            return "OCR:"
        return ("Transcribe all text in this image exactly as written, in reading order, keeping the original "
                "language. Output only the transcription.")

    def read_with_ocr(entry_id: int, doc: dict) -> list[tuple[int, str]]:
        """Extract text; pages without a text layer (scans, photos) go through the local OCR model."""
        path = file_path(doc)
        pages = extract(path)
        todo = scanned_pages(pages, doc["ext"])
        if not todo:
            return pages
        try:
            client = ollama()
            model = resolve_ocr_model(client, installed_models(client))
        except Exception as e:
            log.warning("OCR unavailable for %s: %s", doc["name"], e)
            model = None
        if not model:
            log.info("%s has %d page(s) without text and no OCR model is installed", doc["name"], len(todo))
            return pages
        prompt, texts = ocr_prompt(client, model), dict(pages)
        for n, page in enumerate(todo, 1):
            progress[entry_id] = f"Reading scanned page {n} of {len(todo)}"
            started = time.perf_counter()
            image = base64.b64encode(page_image_png(path, page)).decode()
            texts[page] = clean_pdf_text(client.vision(model, prompt, image))
            log.info("OCR %s page %d with %s: %d chars in %.1fs", doc["name"], page, model, len(texts[page]),
                     time.perf_counter() - started)
        progress.pop(entry_id, None)
        return sorted(texts.items())

    def process(entry_id: int):
        entry = store.entry(entry_id)
        if not entry:
            return
        store.set_entry(entry_id, status="processing", error=None)
        started = time.perf_counter()
        try:
            doc = store.document(entry["document_id"])
            pages = store.pages(doc["id"])
            if pages is None:
                pages = read_with_ocr(entry_id, doc)
                store.set_pages(doc["id"], pages, sum(word_count(t) for _, t in pages))
            size, overlap = settings["chunk_size"], settings["chunk_overlap"]
            chunks = chunk_sections(pages, size, overlap)
            if not chunks:
                raise ValueError("No text found. This looks like a scanned document or image: install an OCR model "
                                 "(Settings → Reading scanned pages) and re-index it.")
            ids = store.replace_chunks(entry_id, chunks)
            embed_model = None
            try:
                client = ollama()
                model = resolve_embed_model(client, installed_models(client))
                if model:
                    prefix = prefixes(model)[1]
                    for i in range(0, len(chunks), 32):
                        # Contextual chunk header: the section path is embedded with the passage text.
                        batch = [prefix + (f"{sec}\n" if sec else "") + text for _, text, sec in chunks[i:i + 32]]
                        store.set_embeddings(ids[i:i + 32], client.embed(model, batch, embed_ctx(client, model)))
                    embed_model = bare(model)
                else:
                    log.info("no search model installed; %s is keyword-only", doc["name"])
            except Exception as e:  # Ollama down or embedding failed: keyword search still works.
                log.warning("embedding skipped for %s: %s", doc["name"], e)
            store.set_entry(entry_id, status="ready", chunks=len(chunks), embed_model=embed_model,
                            chunk_size=size, chunk_overlap=overlap)
            log.info("indexed %s into %s: %d passages in %.2fs", doc["name"], entry["collection_id"], len(chunks),
                     time.perf_counter() - started)
        except Exception as e:
            log.exception("indexing failed for entry %s", entry_id)
            store.set_entry(entry_id, status="error", error=str(e))
        finally:
            progress.pop(entry_id, None)

    def queue(entry_id: int):
        store.set_entry(entry_id, status="queued", error=None)
        worker.submit(process, entry_id)

    for e in store.all("SELECT id FROM entries WHERE status IN ('queued', 'processing')"):
        worker.submit(process, e["id"])  # resume work interrupted by a previous shutdown

    # --- guards ----------------------------------------------------------------
    app.add_middleware(LocalOnly)

    # --- status, settings, models, logs -----------------------------------------------
    meta_cache = {}

    digests, sizes = {}, {}

    def model_meta(client, name: str) -> dict:
        """Context length + capabilities from Ollama /api/show, cached per model digest
        (re-creating a model under the same name changes its digest)."""
        key = (settings["ollama_url"], name, digests.get(name, ""))
        if key not in meta_cache:
            try:
                meta_cache[key] = client.show(name)
            except Exception as e:
                log.debug("show %s failed: %s", name, e)
                return {"context_length": None, "capabilities": []}
        return meta_cache[key]

    def can_chat(client, name: str) -> bool:
        caps = model_meta(client, name)["capabilities"]
        return "completion" in caps if caps else "embed" not in name  # old Ollama: fall back to the name

    def installed_models(client) -> list[str]:
        listing = client.model_list()
        digests.update({m["name"]: m.get("digest", "") for m in listing})
        sizes.update({m["name"]: m.get("size", 0) for m in listing})
        return [m["name"] for m in listing]

    def resolve_embed_model(client, models: list[str]) -> str | None:
        """Configured search model if installed, else the largest installed embedding model."""
        chosen = installed(settings["embed_model"], models)
        if chosen:
            return chosen
        embedders = [m for m in models if "embedding" in model_meta(client, m)["capabilities"]
                     or (not model_meta(client, m)["capabilities"] and "embed" in m)]
        return max(embedders, key=lambda m: sizes.get(m, 0), default=None)

    def resolve_chat_model(models: list[str], client=None) -> str | None:
        chosen = installed(settings["chat_model"], models)
        if chosen:
            return chosen
        capable = [m for m in models if (can_chat(client, m) if client else "embed" not in m)]
        # Fallback: the largest model. Measured: qwen2:0.5b obeyed an injected instruction 6/6 times, 1.5b 0/6.
        return max(capable, key=lambda m: sizes.get(m, 0), default=None)

    @app.get("/api/status")
    def status():
        info = {"app": APP_NAME, "version": __version__, "settings": settings.values, "ollama": False,
                "models": [], "chat_model": None, "embed_ready": False, "data_dir": str(home)}
        try:
            client = ollama()
            info["ollama_version"] = client.version()
            info["models"] = installed_models(client)
            info["ollama"] = True
            info["chat_model"] = resolve_chat_model(info["models"], client)
            info["chat_models"] = [m for m in info["models"] if can_chat(client, m)]
            info["embed_models"] = [m for m in info["models"] if "embedding" in model_meta(client, m)["capabilities"]
                                    or (not model_meta(client, m)["capabilities"] and "embed" in m)]
            info["embed_active"] = bare(resolve_embed_model(client, info["models"]))
            info["embed_ready"] = info["embed_active"] is not None
            info["ocr_active"] = resolve_ocr_model(client, info["models"])
            loaded = [m for m in client.running() if m["size"]]
            # "cpu": a loaded model has nothing in GPU memory -> every answer is computed on the processor.
            info["compute"] = (None if not loaded else "cpu" if all(m["size_vram"] == 0 for m in loaded)
                               else "gpu" if all(m["size_vram"] >= m["size"] for m in loaded) else "partial")
            info["vision_models"] = [m for m in info["models"] if "vision" in model_meta(client, m)["capabilities"]]
            embed_cache.update(at=time.time(), key=(settings["ollama_url"], settings["embed_model"]),
                               model=info["embed_active"])
        except Exception as e:
            info["error"] = str(e)
        info["stale_entries"] = sum(1 for e in store.all("SELECT * FROM entries WHERE status = 'ready'") if is_stale(e))
        return info

    @app.put("/api/settings")
    def put_settings(changes: dict):
        allow = changes.get("allow_remote_ollama", settings["allow_remote_ollama"])
        allow = allow if isinstance(allow, bool) else str(allow).lower() in ("1", "true", "on", "yes")
        if "ollama_url" in changes:
            try:
                changes["ollama_url"] = check_url(str(changes["ollama_url"]), allow)
            except ValueError as e:
                raise HTTPException(400, str(e))
        elif not allow:
            try:
                check_url(settings["ollama_url"], False)
            except ValueError:
                changes["ollama_url"] = "http://127.0.0.1:11434"  # turning remote off snaps back to local
        log.info("settings changed: %s", ", ".join(sorted(changes)))
        return settings.update(changes)

    @app.post("/api/settings/reset")
    def reset_settings():
        log.info("settings reset to defaults")
        return settings.reset()

    @app.get("/api/models")
    def list_models():
        try:
            return ollama().model_list()
        except Exception as e:
            raise HTTPException(503, f"Ollama is not reachable: {e}")

    @app.post("/api/models/pull")
    def pull_model(body: PullIn):
        name = body.name.strip()
        need(name and " " not in name, 400, "Enter a model name like gemma3:1b")
        log.info("model download requested: %s", name)

        def stream():
            try:
                for part in ollama().pull(name):
                    yield json.dumps({k: part.get(k) for k in ("status", "total", "completed") if k in part}) + "\n"
                yield json.dumps({"status": "success", "done": True}) + "\n"
            except Exception as e:
                log.warning("model download %s failed: %s", name, e)
                yield json.dumps({"error": str(e), "done": True}) + "\n"
        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/api/logs")
    def logs():
        return {"lines": list(ring.lines)}

    @app.get("/api/stats")
    def stats():
        s = store.stats()
        s["db_size"] = sum(p.stat().st_size for p in home.glob("daftar.db*"))
        return s

    # --- collections ---------------------------------------------------------------
    def readiness(entries: list[dict]) -> dict:
        total = len(entries)
        ready = sum(1 for e in entries if e["status"] == "ready" and not is_stale(e))
        return {"total": total, "ready": ready,
                "indexing": sum(1 for e in entries if e["status"] in ("queued", "processing")),
                "failed": sum(1 for e in entries if e["status"] == "error"),
                "stale": sum(1 for e in entries if e["status"] == "ready" and is_stale(e)),
                "readiness": round(100 * ready / total) if total else None}

    @app.get("/api/collections")
    def list_collections():
        cols = store.collections()
        by_col = {}
        for e in store.entries():
            by_col.setdefault(e["collection_id"], []).append(e)
        for c in cols:
            c.update(readiness(by_col.get(c["id"], [])))
        return cols

    @app.post("/api/collections")
    def create_collection(body: CollectionIn):
        name = body.name.strip()
        need(name, 400, "Name is required")
        need(not store.one("SELECT id FROM collections WHERE name = ?", (name,)), 409,
             "A collection with that name already exists")
        cid = store.run("INSERT INTO collections(name, description, created) VALUES (?,?,?)",
                        (name, body.description.strip(), time.time()))
        return {"id": cid}

    @app.patch("/api/collections/{cid}")
    def update_collection(cid: int, body: CollectionIn):
        need(body.name.strip(), 400, "Name is required")
        clash = store.one("SELECT id FROM collections WHERE name = ? AND id != ?", (body.name.strip(), cid))
        need(not clash, 409, "A collection with that name already exists")
        store.run("UPDATE collections SET name = ?, description = ? WHERE id = ?",
                  (body.name.strip(), body.description.strip(), cid))
        return {"ok": True}

    @app.delete("/api/collections/{cid}")
    def delete_collection(cid: int):
        docs = store.documents(cid)
        store.run("DELETE FROM collections WHERE id = ?", (cid,))
        for d in docs:
            drop_if_orphan(d["id"])
        return {"ok": True}

    @app.get("/api/collections/{cid}/status")
    def collection_status(cid: int):
        col = store.collection(cid)
        need(col, 404, "Collection not found")
        entries = store.entries(collection_id=cid)
        vectors = store.one("""SELECT COUNT(*) AS n FROM chunks ch JOIN entries e ON e.id = ch.entry_id
                               WHERE e.collection_id = ? AND ch.embedding IS NOT NULL""", (cid,))["n"]
        for e in entries:
            e["stale"] = e["status"] == "ready" and is_stale(e)
        return {**col, **readiness(entries), "entries": entries, "vectors": vectors,
                "chunks": sum(e["chunks"] for e in entries), "embed_model": active_embed() or settings["embed_model"],
                "embed_ready": embed_ready()}

    @app.post("/api/collections/{cid}/optimize")
    def optimize_collection(cid: int):
        need(store.collection(cid), 404, "Collection not found")
        requeued = 0
        for e in store.entries(collection_id=cid):
            if e["status"] == "error" or (e["status"] == "ready" and is_stale(e)):
                queue(e["id"])
                requeued += 1
        with store.lock:
            store.db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('optimize')")
            store.db.execute("ANALYZE")
            store.db.commit()
        vectors = retriever.warm(cid, active_embed() or settings["embed_model"])
        store.run("UPDATE collections SET optimized = ? WHERE id = ?", (time.time(), cid))
        log.info("optimized collection %s: %d re-queued, %d vectors cached", cid, requeued, vectors)
        return {"requeued": requeued, "vectors_cached": vectors}

    # --- documents ------------------------------------------------------------------
    def drop_if_orphan(doc_id: int):
        doc = store.document(doc_id)
        if doc and not store.one("SELECT id FROM entries WHERE document_id = ?", (doc_id,)):
            file_path(doc).unlink(missing_ok=True)
            store.run("DELETE FROM documents WHERE id = ?", (doc_id,))

    def add_entries(doc_id: int, collection_ids: list[int]) -> list[int]:
        added = []
        for cid in dict.fromkeys(collection_ids):
            if store.one("SELECT id FROM entries WHERE document_id = ? AND collection_id = ?", (doc_id, cid)):
                continue
            eid = store.run("INSERT INTO entries(document_id, collection_id, created) VALUES (?,?,?)",
                            (doc_id, cid, time.time()))
            worker.submit(process, eid)
            added.append(cid)
        return added

    @app.get("/api/documents")
    def list_documents(collection_id: int | None = None, type: str | None = None):
        docs = store.documents(collection_id, type)
        for d in docs:
            for e in d["entries"]:
                if e["id"] in progress:
                    e["progress"] = progress[e["id"]]
        return docs

    @app.post("/api/documents")
    async def upload(files: list[UploadFile], collection_ids: list[int] = Form(...)):
        for cid in collection_ids:
            need(store.collection(cid), 404, f"Collection {cid} not found")
        results = []
        for f in files:
            name = Path(f.filename or "file").name
            ext = Path(name).suffix.lower()
            if ext not in SUPPORTED:
                results.append({"name": name, "skipped": f"{ext or 'this file type'} is not supported"})
                continue
            # Stream to disk in 1 MB pieces with a running size check (never the whole file in memory).
            tmp = files_dir / f".upload-{uuid.uuid4().hex}{ext}"
            sha, size = hashlib.sha256(), 0
            try:
                with tmp.open("wb") as out:
                    while piece := await f.read(1 << 20):
                        size += len(piece)
                        if size > MAX_UPLOAD_BYTES:
                            break
                        sha.update(piece)
                        out.write(piece)
                if size > MAX_UPLOAD_BYTES:
                    results.append({"name": name, "skipped": f"larger than {MAX_UPLOAD_BYTES >> 20} MB"})
                    continue
                digest = sha.hexdigest()
                doc = store.one("SELECT * FROM documents WHERE sha256 = ?", (digest,))
                if not doc:
                    doc_id = store.run("INSERT INTO documents(name, ext, size, sha256, created) VALUES (?,?,?,?,?)",
                                       (name, ext, size, digest, time.time()))
                    doc = store.document(doc_id)
                    tmp.replace(file_path(doc))
            finally:
                tmp.unlink(missing_ok=True)
            added = add_entries(doc["id"], collection_ids)
            if added:
                results.append({"name": name, "id": doc["id"], "added_to": added})
            else:
                results.append({"name": name, "id": doc["id"], "skipped": "already in the chosen collection(s)"})
        return results

    @app.get("/api/documents/{doc_id}")
    def document_info(doc_id: int):
        doc = store.document(doc_id)
        need(doc, 404, "Document not found")
        doc.pop("content", None)
        entries = store.entries(document_id=doc_id)
        for e in entries:
            e["stale"] = e["status"] == "ready" and is_stale(e)
        return {**doc, "entries": entries, "read_minutes": max(1, math.ceil(doc["words"] / READ_WPM)) if doc["words"] else 0}

    @app.get("/api/documents/{doc_id}/text")
    def document_text(doc_id: int):
        need(store.document(doc_id), 404, "Document not found")
        pages = store.pages(doc_id)
        need(pages is not None, 409, "This document has not been read yet")
        return {"pages": [{"page": p, "text": t} for p, t in pages]}

    @app.get("/api/documents/{doc_id}/export")
    def export_document(doc_id: int, format: str = "txt"):
        doc = store.document(doc_id)
        need(doc, 404, "Document not found")
        need(format in FORMATS, 400, f"Format must be one of {', '.join(FORMATS)}")
        pages = store.pages(doc_id)
        need(pages is not None, 409, "This document has not been read yet")
        data, media, filename = export(pages, doc["name"], format)
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/documents/{doc_id}/file")
    def open_document(doc_id: int):
        doc = store.document(doc_id)
        need(doc, 404, "Document not found")
        return FileResponse(file_path(doc), filename=doc["name"], content_disposition_type="inline")

    @app.get("/api/documents/{doc_id}/chunks")
    def document_chunks(doc_id: int, collection_id: int | None = None):
        where, args = ("AND e.collection_id = ?", (collection_id,)) if collection_id else ("", ())
        return store.all(f"""SELECT ch.id, ch.ord, ch.page, ch.text, e.collection_id FROM chunks ch
                             JOIN entries e ON e.id = ch.entry_id WHERE e.document_id = ? {where}
                             ORDER BY e.collection_id, ch.ord""", (doc_id, *args))

    @app.post("/api/documents/{doc_id}/collections")
    def add_to_collections(doc_id: int, body: AddToCollections):
        need(store.document(doc_id), 404, "Document not found")
        for cid in body.collection_ids:
            need(store.collection(cid), 404, f"Collection {cid} not found")
        return {"added_to": add_entries(doc_id, body.collection_ids)}

    @app.delete("/api/collections/{cid}/documents/{doc_id}")
    def remove_from_collection(cid: int, doc_id: int):
        store.run("DELETE FROM entries WHERE document_id = ? AND collection_id = ?", (doc_id, cid))
        drop_if_orphan(doc_id)
        return {"ok": True}

    @app.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: int):
        doc = store.document(doc_id)
        if doc:
            file_path(doc).unlink(missing_ok=True)
            store.run("DELETE FROM documents WHERE id = ?", (doc_id,))
        return {"ok": True}

    @app.post("/api/entries/{entry_id}/reindex")
    def reindex_entry(entry_id: int):
        need(store.entry(entry_id), 404, "Not found")
        queue(entry_id)
        return {"ok": True}

    @app.post("/api/reindex")
    def reindex_stale():
        targets = [e for e in store.all("SELECT * FROM entries") if e["status"] == "error" or
                   (e["status"] == "ready" and is_stale(e))]
        for e in targets:
            queue(e["id"])
        return {"queued": len(targets)}

    # --- search -----------------------------------------------------------------
    def embed_ctx(client, model: str) -> int | None:
        ctx = model_meta(client, model)["context_length"]
        return min(ctx, 8192) if ctx else None

    def query_vector(client, models, text) -> tuple[list | None, str | None]:
        """(vector, stored model name) for a question, or (None, None) without a search model."""
        model = resolve_embed_model(client, models)
        if not model:
            return None, None
        try:
            return client.embed(model, [prefixes(model)[0] + text], embed_ctx(client, model))[0], bare(model)
        except Exception as e:
            log.warning("query embedding failed: %s", e)
            return None, None

    @app.get("/api/search")
    def search(q: str, collection_id: int | None = None, limit: int = 20):
        need(q.strip(), 400, "Type something to search for")
        vec, emb = None, None
        try:
            client = ollama()
            vec, emb = query_vector(client, installed_models(client), q)
        except Exception as e:
            log.warning("search without embeddings: %s", e)
        results, info = retriever.search(q, collection_id, max(1, min(limit, 50)), max(settings["scan_k"], limit),
                                         vec, emb, settings["rerank"])
        return {"results": results, "mode": "hybrid" if vec is not None else "keyword", **info}

    # --- chats --------------------------------------------------------------------
    @app.get("/api/chats")
    def list_chats(q: str = ""):
        return store.chats(q.strip())

    @app.post("/api/chats")
    def create_chat(body: ChatIn):
        now = time.time()
        everything = bool(body.all_collections)
        return {"id": store.run("INSERT INTO chats(collection_id, all_collections, title, created, updated) "
                                "VALUES (?,?,?,?,?)", (None if everything else body.collection_id, int(everything),
                                                      (body.title or "New chat").strip() or "New chat", now, now))}

    @app.patch("/api/chats/{chat_id}")
    def update_chat(chat_id: int, body: ChatIn):
        need(store.one("SELECT id FROM chats WHERE id = ?", (chat_id,)), 404, "Chat not found")
        if "title" in body.model_fields_set and body.title and body.title.strip():
            store.run("UPDATE chats SET title = ? WHERE id = ?", (body.title.strip()[:120], chat_id))
        if body.all_collections:
            store.run("UPDATE chats SET collection_id = NULL, all_collections = 1 WHERE id = ?", (chat_id,))
        elif "collection_id" in body.model_fields_set:
            store.run("UPDATE chats SET collection_id = ?, all_collections = 0 WHERE id = ?", (body.collection_id, chat_id))
        return {"ok": True}

    @app.get("/api/chats/{chat_id}")
    def get_chat(chat_id: int):
        chat = store.one("""SELECT ch.*, c.name AS collection FROM chats ch
                            LEFT JOIN collections c ON c.id = ch.collection_id WHERE ch.id = ?""", (chat_id,))
        need(chat, 404, "Chat not found")
        return {**chat, "messages": store.messages(chat_id)}

    @app.delete("/api/chats/{chat_id}")
    def delete_chat(chat_id: int):
        store.run("DELETE FROM chats WHERE id = ?", (chat_id,))
        return {"ok": True}

    @app.patch("/api/messages/{msg_id}")
    def edit_message(msg_id: int, body: MessageEdit):
        msg = store.one("SELECT * FROM messages WHERE id = ?", (msg_id,))
        need(msg, 404, "Message not found")
        need(msg["role"] == "user", 400, "Only your own messages can be edited")
        need(body.content.strip(), 400, "Message is empty")
        with store.lock, store.db:  # editing a question discards the conversation after it
            store.db.execute("UPDATE messages SET content = ? WHERE id = ?", (body.content.strip(), msg_id))
            store.db.execute("DELETE FROM messages WHERE chat_id = ? AND id > ?", (msg["chat_id"], msg_id))
        return {"ok": True}

    @app.delete("/api/messages/{msg_id}")
    def delete_message(msg_id: int):
        store.run("DELETE FROM messages WHERE id = ?", (msg_id,))
        return {"ok": True}

    @app.post("/api/chats/{chat_id}/ask")
    def ask(chat_id: int, body: AskIn):
        chat = store.one("""SELECT ch.*, c.name AS collection FROM chats ch
                            LEFT JOIN collections c ON c.id = ch.collection_id WHERE ch.id = ?""", (chat_id,))
        need(chat, 404, "Chat not found")
        history = [m for m in store.messages(chat_id) if m["role"] in ("user", "assistant")]
        if body.resend:  # regenerate / edited question: answer the last user message again
            need(history and history[-1]["role"] == "user", 400, "Nothing to answer")
            question, history = history[-1]["content"], history[:-1]
        else:
            question = body.question.strip()
            need(question, 400, "Question is empty")
            store.add_message(chat_id, "user", question)
            if not history and chat["title"] == "New chat":
                title = question if len(question) <= 60 else question[:60].rsplit(" ", 1)[0] + "…"
                store.run("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))

        def line(obj) -> str:
            return json.dumps(obj) + "\n"

        def stream():
            answer, sources, meta, saved, stopped = "", [], {}, {}, True
            t0 = time.perf_counter()
            timings = {}
            try:
                everything = bool(chat["all_collections"])
                if chat["collection_id"] is None and not everything:
                    raise RuntimeError("Pick a collection for this chat first.")
                scope = None if everything else chat["collection_id"]
                client = ollama()
                models = installed_models(client)
                model = resolve_chat_model(models, client)
                if not model:
                    raise RuntimeError("No chat model is installed in Ollama. Open Settings to add one.")
                vec, emb = query_vector(client, models, question)
                timings["embed_ms"] = round((time.perf_counter() - t0) * 1000)
                t1 = time.perf_counter()
                sources, info = retriever.search(question, scope, settings["top_k"],
                                                 settings["scan_k"], vec, emb, settings["rerank"])
                timings["search_ms"] = round((time.perf_counter() - t1) * 1000)
                files = list(dict.fromkeys(e["document"] for e in store.entries(collection_id=scope)))
                found = len(sources)
                messages, num_ctx, sources, est = fit_prompt(question, sources, history, "all collections" if everything else chat["collection"], files,
                                                             model_meta(client, model)["context_length"])
                if len(sources) < found:
                    log.info("prompt budget: kept %d of %d passages for %s", len(sources), found, model)
                meta = {"model": model, "mode": "hybrid" if vec is not None else "keyword"}
                if settings["debug"]:
                    meta["debug"] = {**info, "temperature": settings["temperature"], "top_k": settings["top_k"],
                                     "scan_k": settings["scan_k"], "rerank": settings["rerank"], "timings": timings,
                                     "num_ctx": num_ctx, "estimated_prompt_tokens": est,
                                     "passages_trimmed_to_fit": found - len(sources), "prompt": messages}
                yield line({"type": "meta", "sources": sources, **meta})
                t2 = time.perf_counter()
                pieces = client.chat(model, messages, settings["temperature"], num_ctx)
                try:
                    for piece in pieces:
                        if not answer and piece:
                            timings["first_token_ms"] = round((time.perf_counter() - t2) * 1000)
                        answer += piece
                        yield line({"type": "token", "text": piece})
                finally:
                    pieces.close()  # closes the HTTP stream, which makes Ollama stop generating
                timings["total_ms"] = round((time.perf_counter() - t0) * 1000)
                log.info("answered in chat %s with %s: %d sources, %s", chat_id, model, len(sources), timings)
                stopped = False
            except Exception as e:
                stopped = False
                log.warning("ask failed: %s", e)
                yield line({"type": "error", "text": str(e)})
                answer = answer or f"⚠ {e}"
            finally:  # also runs when the client goes away (Stop): keep what was written so far
                if stopped:
                    meta["stopped"] = True
                    log.info("answer in chat %s stopped by the user after %d characters", chat_id, len(answer))
                if meta.get("debug"):
                    meta["debug"]["timings"] = timings
                    meta["debug"].pop("prompt", None)  # keep stored history small
                saved["id"] = store.add_message(chat_id, "assistant", answer, sources, meta)
            yield line({"type": "done", "id": saved["id"], "timings": timings if settings["debug"] else None})

        async def body():
            gen = stream()
            try:
                async for chunk in iterate_in_threadpool(gen):
                    yield chunk
            finally:  # on disconnect Starlette cancels us; close the generator so its cleanup runs now
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(gen.close)

        return StreamingResponse(body(), media_type="application/x-ndjson")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    return app
