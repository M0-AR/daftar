"""SQLite storage.

documents  one row per unique file (deduplicated by SHA-256), with its extracted text
entries    a document's membership in a collection, with that collection's own index state
chunks     passages of one entry (+ FTS5 keyword index, + normalized float32 embedding)
"""
import json
import sqlite3
import threading
import time

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS collections(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, optimized REAL);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, ext TEXT NOT NULL, size INTEGER NOT NULL,
  sha256 TEXT NOT NULL UNIQUE, pages INTEGER NOT NULL DEFAULT 0, words INTEGER NOT NULL DEFAULT 0,
  content TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'queued', error TEXT, chunks INTEGER NOT NULL DEFAULT 0,
  embed_model TEXT, chunk_size INTEGER, chunk_overlap INTEGER, created REAL NOT NULL,
  UNIQUE(document_id, collection_id));
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL, embedding BLOB);
CREATE INDEX IF NOT EXISTS chunks_entry ON chunks(entry_id);
CREATE INDEX IF NOT EXISTS entries_collection ON entries(collection_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TABLE IF NOT EXISTS chats(
  id INTEGER PRIMARY KEY, collection_id INTEGER REFERENCES collections(id) ON DELETE SET NULL,
  title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT, meta TEXT, created REAL NOT NULL);
"""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def all(self, sql, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()) -> dict | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def run(self, sql, args=()) -> int:
        with self.lock, self.db:
            return self.db.execute(sql, args).lastrowid

    # --- collections -------------------------------------------------------
    def collections(self) -> list[dict]:
        return self.all("""
          SELECT c.*, COUNT(e.id) AS documents, COALESCE(SUM(e.chunks), 0) AS chunks,
                 COALESCE(SUM(d.size), 0) AS size
          FROM collections c LEFT JOIN entries e ON e.collection_id = c.id
          LEFT JOIN documents d ON d.id = e.document_id
          GROUP BY c.id ORDER BY c.created DESC""")

    def collection(self, cid: int) -> dict | None:
        return self.one("SELECT * FROM collections WHERE id = ?", (cid,))

    # --- documents & entries -------------------------------------------------
    def entries(self, document_id: int | None = None, collection_id: int | None = None) -> list[dict]:
        sql = """SELECT e.*, c.name AS collection, d.name AS document, d.ext
                 FROM entries e JOIN collections c ON c.id = e.collection_id
                 JOIN documents d ON d.id = e.document_id WHERE 1=1"""
        args = []
        if document_id is not None:
            sql += " AND e.document_id = ?"
            args.append(document_id)
        if collection_id is not None:
            sql += " AND e.collection_id = ?"
            args.append(collection_id)
        return self.all(sql + " ORDER BY e.id", args)

    def entry(self, entry_id: int) -> dict | None:
        rows = self.all("""SELECT e.*, d.name AS document, d.ext FROM entries e
                           JOIN documents d ON d.id = e.document_id WHERE e.id = ?""", (entry_id,))
        return rows[0] if rows else None

    def documents(self, collection_id: int | None = None, ext: str | None = None) -> list[dict]:
        sql = "SELECT id, name, ext, size, sha256, pages, words, created FROM documents d WHERE 1=1"
        args = []
        if collection_id is not None:
            sql += " AND EXISTS (SELECT 1 FROM entries e WHERE e.document_id = d.id AND e.collection_id = ?)"
            args.append(collection_id)
        if ext:
            sql += " AND d.ext = ?"
            args.append(ext)
        docs = self.all(sql + " ORDER BY d.created DESC, d.id DESC", args)
        by_doc = {}
        for e in self.entries():
            by_doc.setdefault(e["document_id"], []).append(
                {k: e[k] for k in ("id", "collection_id", "collection", "status", "error", "chunks", "embed_model",
                                   "chunk_size", "chunk_overlap")})
        for d in docs:
            d["entries"] = by_doc.get(d["id"], [])
        return docs

    def document(self, doc_id: int) -> dict | None:
        return self.one("SELECT * FROM documents WHERE id = ?", (doc_id,))

    def pages(self, doc_id: int) -> list[tuple[int, str]] | None:
        row = self.one("SELECT content FROM documents WHERE id = ?", (doc_id,))
        return [tuple(p) for p in json.loads(row["content"])] if row and row["content"] else None

    def set_pages(self, doc_id: int, pages: list[tuple[int, str]], words: int):
        self.run("UPDATE documents SET content = ?, pages = ?, words = ? WHERE id = ?",
                 (json.dumps(pages), len(pages), words, doc_id))

    def set_entry(self, entry_id: int, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.run(f"UPDATE entries SET {cols} WHERE id = ?", (*fields.values(), entry_id))

    def replace_chunks(self, entry_id: int, chunks: list[tuple[int, str]]) -> list[int]:
        with self.lock, self.db:
            self.db.execute("DELETE FROM chunks WHERE entry_id = ?", (entry_id,))
            return [self.db.execute("INSERT INTO chunks(entry_id, ord, page, text) VALUES (?,?,?,?)",
                                    (entry_id, i, page, text)).lastrowid
                    for i, (page, text) in enumerate(chunks)]

    def set_embeddings(self, ids: list[int], vectors: list[list[float]]):
        rows = []
        for cid, vec in zip(ids, vectors):
            v = np.asarray(vec, dtype=np.float32)
            rows.append(((v / (np.linalg.norm(v) or 1.0)).tobytes(), cid))
        with self.lock, self.db:
            self.db.executemany("UPDATE chunks SET embedding = ? WHERE id = ?", rows)

    # --- chats ---------------------------------------------------------------
    def chats(self, query: str = "") -> list[dict]:
        sql = """SELECT ch.*, c.name AS collection FROM chats ch
                 LEFT JOIN collections c ON c.id = ch.collection_id"""
        if not query:
            return self.all(sql + " ORDER BY ch.updated DESC")
        like = f"%{query}%"
        return self.all(sql + """ WHERE ch.title LIKE ? OR EXISTS
                 (SELECT 1 FROM messages m WHERE m.chat_id = ch.id AND m.content LIKE ?)
                 ORDER BY ch.updated DESC""", (like, like))

    def messages(self, chat_id: int) -> list[dict]:
        rows = self.all("SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,))
        for m in rows:
            m["sources"] = json.loads(m["sources"]) if m["sources"] else []
            m["meta"] = json.loads(m["meta"]) if m["meta"] else {}
        return rows

    def add_message(self, chat_id: int, role: str, content: str, sources=None, meta=None) -> int:
        now = time.time()
        with self.lock, self.db:
            self.db.execute("UPDATE chats SET updated = ? WHERE id = ?", (now, chat_id))
            return self.db.execute(
                "INSERT INTO messages(chat_id, role, content, sources, meta, created) VALUES (?,?,?,?,?,?)",
                (chat_id, role, content, json.dumps(sources) if sources is not None else None,
                 json.dumps(meta) if meta else None, now)).lastrowid

    def stats(self) -> dict:
        return self.one("""SELECT (SELECT COUNT(*) FROM collections) AS collections,
                                  (SELECT COUNT(*) FROM documents) AS documents,
                                  (SELECT COUNT(*) FROM chunks) AS chunks,
                                  (SELECT COALESCE(SUM(size), 0) FROM documents) AS size""")
