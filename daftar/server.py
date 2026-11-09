"""Local HTTP API + static UI. Binds to loopback only; talks to nothing but the local Ollama."""
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from daftar import __version__
from daftar.config import Settings, data_dir
from daftar.ingest import SUPPORTED, chunk_pages, extract
from daftar.ollama import LOOPBACK, Ollama, check_local, prefixes
from daftar.rag import Retriever, build_messages
from daftar.store import Store

log = logging.getLogger("daftar")
STATIC = Path(__file__).parent / "static"


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
    title: str = "New chat"


class AskIn(BaseModel):
    question: str


def create_app(home: Path | None = None, ollama_factory=Ollama) -> FastAPI:
    home = home or data_dir()
    files_dir = home / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    store = Store(home / "daftar.db")
    settings = Settings(home / "settings.json")
    retriever = Retriever(store)
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest")
    app = FastAPI(title="Daftar", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.worker = store, worker

    def ollama():
        return ollama_factory(settings["ollama_url"])

    def file_path(doc: dict) -> Path:
        return files_dir / f"{doc['id']}{doc['ext']}"

    # --- ingestion ----------------------------------------------------------
    def process(doc_id: int):
        doc = store.document(doc_id)
        if not doc:
            return
        store.set_document(doc_id, status="processing", error=None)
        try:
            pages = extract(file_path(doc))
            chunks = chunk_pages(pages)
            if not chunks:
                raise ValueError("No text found. Scanned PDFs need OCR, which is not supported yet.")
            ids = store.replace_chunks(doc_id, chunks)
            embed_model = None
            try:
                client = ollama()
                model = installed(settings["embed_model"], client.models())
                if model:
                    prefix = prefixes(model)[1]
                    for i in range(0, len(chunks), 32):
                        batch = [prefix + text for _, text in chunks[i:i + 32]]
                        store.set_embeddings(ids[i:i + 32], client.embed(model, batch))
                    embed_model = settings["embed_model"]
            except Exception as e:  # Ollama down or embed failed: keep keyword search working.
                log.warning("embedding skipped for document %s: %s", doc_id, e)
            store.set_document(doc_id, status="ready", pages=len(pages), chunks=len(chunks),
                               embed_model=embed_model)
        except Exception as e:
            log.exception("ingest failed for document %s", doc_id)
            store.set_document(doc_id, status="error", error=str(e))

    for doc in store.all("SELECT id FROM documents WHERE status IN ('queued', 'processing')"):
        worker.submit(process, doc["id"])  # resume work interrupted by a previous shutdown

    # --- guards --------------------------------------------------------------
    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = (request.url.hostname or "").strip("[]")
        if host not in LOOPBACK:  # also blocks DNS-rebinding pages that resolve to 127.0.0.1
            return JSONResponse({"detail": "Daftar only answers on this machine"}, status_code=403)
        # Browsers cannot send a custom header cross-origin without a CORS preflight, which we never grant.
        if request.method not in ("GET", "HEAD") and request.headers.get("x-daftar") != "1":
            return JSONResponse({"detail": "Missing X-Daftar header"}, status_code=403)
        return await call_next(request)

    # --- status & settings ---------------------------------------------------
    @app.get("/api/status")
    def status():
        info = {"version": __version__, "settings": settings.values, "ollama": False, "models": [],
                "chat_model": None, "embed_ready": False, "data_dir": str(home)}
        try:
            client = ollama()
            info["ollama_version"] = client.version()
            info["models"] = client.models()
            info["ollama"] = True
            info["chat_model"] = resolve_chat_model(info["models"])
            info["embed_ready"] = bool(installed(settings["embed_model"], info["models"]))
        except Exception as e:
            info["error"] = str(e)
        info["stale_documents"] = store.one(
            "SELECT COUNT(*) AS n FROM documents WHERE status = 'ready' AND COALESCE(embed_model, '') != ?",
            (settings["embed_model"],))["n"]
        return info

    def resolve_chat_model(models: list[str]) -> str | None:
        chosen = installed(settings["chat_model"], models)
        if chosen:
            return chosen
        chat_capable = [m for m in models if "embed" not in m]
        return chat_capable[0] if chat_capable else None

    @app.put("/api/settings")
    def put_settings(changes: dict):
        if "ollama_url" in changes:
            try:
                changes["ollama_url"] = check_local(str(changes["ollama_url"]))
            except ValueError as e:
                raise HTTPException(400, str(e))
        if "top_k" in changes:
            changes["top_k"] = max(1, min(20, int(changes["top_k"])))
        return settings.update(changes)

    @app.get("/api/stats")
    def stats():
        s = store.stats()
        s["db_size"] = sum(p.stat().st_size for p in home.glob("daftar.db*"))
        return s

    # --- collections -----------------------------------------------------------
    @app.get("/api/collections")
    def list_collections():
        return store.collections()

    @app.post("/api/collections")
    def create_collection(body: CollectionIn):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Name is required")
        if store.one("SELECT id FROM collections WHERE name = ?", (name,)):
            raise HTTPException(409, "A collection with that name already exists")
        return {"id": store.add_collection(name, body.description.strip())}

    @app.patch("/api/collections/{cid}")
    def rename_collection(cid: int, body: CollectionIn):
        store.run("UPDATE collections SET name = ?, description = ? WHERE id = ?",
                  (body.name.strip(), body.description.strip(), cid))
        return {"ok": True}

    @app.delete("/api/collections/{cid}")
    def delete_collection(cid: int):
        for doc in store.documents(cid):
            file_path(doc).unlink(missing_ok=True)
        store.run("DELETE FROM collections WHERE id = ?", (cid,))
        return {"ok": True}

    # --- documents ---------------------------------------------------------------
    @app.get("/api/documents")
    def list_documents(collection_id: int | None = None):
        return store.documents(collection_id)

    @app.post("/api/collections/{cid}/documents")
    async def upload(cid: int, files: list[UploadFile]):
        if not store.one("SELECT id FROM collections WHERE id = ?", (cid,)):
            raise HTTPException(404, "Collection not found")
        results = []
        for f in files:
            name = Path(f.filename or "file").name
            ext = Path(name).suffix.lower()
            if ext not in SUPPORTED:
                results.append({"name": name, "skipped": f"{ext or 'this type'} is not supported"})
                continue
            data = await f.read()
            digest = hashlib.sha256(data).hexdigest()
            if store.one("SELECT id FROM documents WHERE collection_id = ? AND sha256 = ?", (cid, digest)):
                results.append({"name": name, "skipped": "already in this collection"})
                continue
            doc_id = store.run("""INSERT INTO documents(collection_id, name, ext, size, sha256, created)
                                  VALUES (?,?,?,?,?,?)""", (cid, name, ext, len(data), digest, time.time()))
            (files_dir / f"{doc_id}{ext}").write_bytes(data)
            worker.submit(process, doc_id)
            results.append({"name": name, "id": doc_id})
        return results

    @app.get("/api/documents/{doc_id}/file")
    def open_document(doc_id: int):
        doc = store.document(doc_id)
        if not doc:
            raise HTTPException(404, "Document not found")
        return FileResponse(file_path(doc), filename=doc["name"], content_disposition_type="inline")

    @app.get("/api/documents/{doc_id}/chunks")
    def document_chunks(doc_id: int):
        return store.all("SELECT id, ord, page, text FROM chunks WHERE document_id = ? ORDER BY ord", (doc_id,))

    @app.post("/api/documents/{doc_id}/reindex")
    def reindex(doc_id: int):
        if not store.document(doc_id):
            raise HTTPException(404, "Document not found")
        store.set_document(doc_id, status="queued")
        worker.submit(process, doc_id)
        return {"ok": True}

    @app.post("/api/reindex")
    def reindex_stale():
        stale = store.all("SELECT id FROM documents WHERE COALESCE(embed_model, '') != ?", (settings["embed_model"],))
        for doc in stale:
            store.set_document(doc["id"], status="queued")
            worker.submit(process, doc["id"])
        return {"queued": len(stale)}

    @app.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: int):
        doc = store.document(doc_id)
        if doc:
            file_path(doc).unlink(missing_ok=True)
            store.run("DELETE FROM documents WHERE id = ?", (doc_id,))
        return {"ok": True}

    # --- chats ---------------------------------------------------------------------
    @app.get("/api/chats")
    def list_chats():
        return store.chats()

    @app.post("/api/chats")
    def create_chat(body: ChatIn):
        return {"id": store.run("INSERT INTO chats(collection_id, title, created) VALUES (?,?,?)",
                                (body.collection_id, body.title, time.time()))}

    @app.patch("/api/chats/{chat_id}")
    def update_chat(chat_id: int, body: ChatIn):
        store.run("UPDATE chats SET collection_id = ?, title = ? WHERE id = ?", (body.collection_id, body.title, chat_id))
        return {"ok": True}

    @app.get("/api/chats/{chat_id}")
    def get_chat(chat_id: int):
        chat = store.one("SELECT * FROM chats WHERE id = ?", (chat_id,))
        if not chat:
            raise HTTPException(404, "Chat not found")
        msgs = store.messages(chat_id)
        for m in msgs:
            m["sources"] = json.loads(m["sources"]) if m["sources"] else []
        return {**chat, "messages": msgs}

    @app.delete("/api/chats/{chat_id}")
    def delete_chat(chat_id: int):
        store.run("DELETE FROM chats WHERE id = ?", (chat_id,))
        return {"ok": True}

    @app.post("/api/chats/{chat_id}/ask")
    def ask(chat_id: int, body: AskIn):
        chat = store.one("SELECT * FROM chats WHERE id = ?", (chat_id,))
        question = body.question.strip()
        if not chat:
            raise HTTPException(404, "Chat not found")
        if not question:
            raise HTTPException(400, "Question is empty")
        history = [m for m in store.messages(chat_id) if m["role"] in ("user", "assistant")]
        store.add_message(chat_id, "user", question)
        if not history and chat["title"] == "New chat":
            title = question if len(question) <= 60 else question[:60].rsplit(" ", 1)[0] + "…"
            store.run("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))

        def line(obj) -> str:
            return json.dumps(obj) + "\n"

        def stream():
            answer, sources = "", []
            try:
                if chat["collection_id"] is None:
                    raise RuntimeError("Pick a collection for this chat first.")
                client = ollama()
                models = client.models()
                model = resolve_chat_model(models)
                if not model:
                    raise RuntimeError("No chat model is installed in Ollama. See Settings.")
                query_vec, embed_model = None, installed(settings["embed_model"], models)
                if embed_model:
                    try:
                        query_vec = client.embed(embed_model, [prefixes(embed_model)[0] + question])[0]
                    except Exception as e:
                        log.warning("query embedding failed: %s", e)
                sources = retriever.search(question, chat["collection_id"], settings["top_k"],
                                           query_vec, settings["embed_model"])
                yield line({"type": "meta", "model": model, "sources": sources,
                            "mode": "hybrid" if query_vec is not None else "keyword"})
                if not sources:
                    answer = ("I could not find anything about that in this collection's documents. "
                              "Try other words, or add documents on the Documents page.")
                    yield line({"type": "token", "text": answer})
                else:
                    for piece in client.chat(model, build_messages(question, sources, history)):
                        answer += piece
                        yield line({"type": "token", "text": piece})
            except Exception as e:
                log.warning("ask failed: %s", e)
                yield line({"type": "error", "text": str(e)})
                answer = answer or f"⚠ {e}"
            msg_id = store.add_message(chat_id, "assistant", answer, json.dumps(sources))
            yield line({"type": "done", "id": msg_id})

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    return app
