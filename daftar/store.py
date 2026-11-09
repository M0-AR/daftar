"""SQLite storage: collections, documents, chunks (+ FTS5 index and embeddings), chats."""
import sqlite3
import threading
import time

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS collections(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  name TEXT NOT NULL, ext TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
  pages INTEGER NOT NULL DEFAULT 0, chunks INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued', error TEXT, embed_model TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL, embedding BLOB);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TABLE IF NOT EXISTS chats(
  id INTEGER PRIMARY KEY, collection_id INTEGER REFERENCES collections(id) ON DELETE SET NULL,
  title TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT, created REAL NOT NULL);
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
          SELECT c.*, COUNT(d.id) AS documents, COALESCE(SUM(d.chunks), 0) AS chunks,
                 COALESCE(SUM(d.size), 0) AS size
          FROM collections c LEFT JOIN documents d ON d.collection_id = c.id
          GROUP BY c.id ORDER BY c.created DESC""")

    def add_collection(self, name: str, description: str = "") -> int:
        return self.run("INSERT INTO collections(name, description, created) VALUES (?,?,?)",
                        (name, description, time.time()))

    # --- documents ---------------------------------------------------------
    def documents(self, collection_id: int | None = None) -> list[dict]:
        sql = """SELECT d.*, c.name AS collection FROM documents d
                 JOIN collections c ON c.id = d.collection_id"""
        if collection_id is None:
            return self.all(sql + " ORDER BY d.created DESC")
        return self.all(sql + " WHERE d.collection_id = ? ORDER BY d.created DESC", (collection_id,))

    def document(self, doc_id: int) -> dict | None:
        return self.one("SELECT * FROM documents WHERE id = ?", (doc_id,))

    def set_document(self, doc_id: int, **fields):
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.run(f"UPDATE documents SET {cols} WHERE id = ?", (*fields.values(), doc_id))

    def replace_chunks(self, doc_id: int, chunks: list[tuple[int, str]]) -> list[int]:
        with self.lock, self.db:
            self.db.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            return [self.db.execute("INSERT INTO chunks(document_id, ord, page, text) VALUES (?,?,?,?)",
                                    (doc_id, i, page, text)).lastrowid
                    for i, (page, text) in enumerate(chunks)]

    def set_embeddings(self, ids: list[int], vectors: list[list[float]]):
        rows = []
        for cid, vec in zip(ids, vectors):
            v = np.asarray(vec, dtype=np.float32)
            rows.append(((v / (np.linalg.norm(v) or 1.0)).tobytes(), cid))
        with self.lock, self.db:
            self.db.executemany("UPDATE chunks SET embedding = ? WHERE id = ?", rows)

    # --- chats -------------------------------------------------------------
    def chats(self) -> list[dict]:
        return self.all("""SELECT ch.*, c.name AS collection FROM chats ch
                           LEFT JOIN collections c ON c.id = ch.collection_id ORDER BY ch.created DESC""")

    def messages(self, chat_id: int) -> list[dict]:
        return self.all("SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,))

    def add_message(self, chat_id: int, role: str, content: str, sources: str | None = None) -> int:
        return self.run("INSERT INTO messages(chat_id, role, content, sources, created) VALUES (?,?,?,?,?)",
                        (chat_id, role, content, sources, time.time()))

    def stats(self) -> dict:
        row = self.one("""SELECT (SELECT COUNT(*) FROM collections) AS collections,
                                 (SELECT COUNT(*) FROM documents) AS documents,
                                 (SELECT COUNT(*) FROM chunks) AS chunks,
                                 (SELECT COALESCE(SUM(size), 0) FROM documents) AS size""")
        return row
