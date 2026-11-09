"""Hybrid retrieval (FTS5 keywords + embeddings, fused by reciprocal rank) and prompt building."""
import re
import threading

import numpy as np

WORD = re.compile(r"\w+", re.UNICODE)
STOP = set("""a an and are as at be by can do does for from has have how i in is it its me my of on or
our that the their this to was what when where which who why will with you your""".split())

SYSTEM = """You are Daftar, a private assistant that answers questions about the user's own documents.
Use ONLY the numbered sources provided. Cite the source number in square brackets after each fact, like [1] or [2][3].
If the sources do not contain the answer, say you could not find it in the documents. Never invent facts.
Answer in the same language as the question. Be clear and concise."""


def fts_query(question: str) -> str:
    words = [w for w in WORD.findall(question.lower()) if len(w) > 1 and w not in STOP]
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))


class Retriever:
    def __init__(self, store):
        self.store = store
        self._cache = {}
        self._lock = threading.Lock()

    def lexical(self, question: str, collection_id: int, n: int = 20) -> list[int]:
        query = fts_query(question)
        if not query:
            return []
        rows = self.store.all("""
          SELECT ch.id FROM chunks_fts f JOIN chunks ch ON ch.id = f.rowid
          JOIN documents d ON d.id = ch.document_id
          WHERE chunks_fts MATCH ? AND d.collection_id = ? ORDER BY bm25(chunks_fts) LIMIT ?""",
                              (query, collection_id, n))
        return [r["id"] for r in rows]

    def _matrix(self, collection_id: int, embed_model: str):
        sig = self.store.one("""
          SELECT COUNT(*) AS n, COALESCE(MAX(ch.id), 0) AS m FROM chunks ch JOIN documents d ON d.id = ch.document_id
          WHERE d.collection_id = ? AND d.embed_model = ? AND ch.embedding IS NOT NULL""",
                             (collection_id, embed_model))
        key = (collection_id, embed_model)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == (sig["n"], sig["m"]):
                return cached[1], cached[2]
        rows = self.store.all("""
          SELECT ch.id, ch.embedding FROM chunks ch JOIN documents d ON d.id = ch.document_id
          WHERE d.collection_id = ? AND d.embed_model = ? AND ch.embedding IS NOT NULL""",
                              (collection_id, embed_model))
        ids = [r["id"] for r in rows]
        mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]) if rows else None
        with self._lock:
            self._cache[key] = ((sig["n"], sig["m"]), ids, mat)
        return ids, mat

    def vector(self, query_vec, collection_id: int, embed_model: str, n: int = 20) -> list[int]:
        ids, mat = self._matrix(collection_id, embed_model)
        if mat is None:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        q /= np.linalg.norm(q) or 1.0
        scores = mat @ q
        return [ids[i] for i in np.argsort(-scores)[:n]]

    def search(self, question: str, collection_id: int, k: int, query_vec=None, embed_model=None) -> list[dict]:
        ranked = [self.lexical(question, collection_id)]
        if query_vec is not None:
            ranked.append(self.vector(query_vec, collection_id, embed_model))
        fused = {}
        for ranking in ranked:
            for rank, cid in enumerate(ranking):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (60 + rank)
        best = sorted(fused, key=fused.get, reverse=True)[:k]
        if not best:
            return []
        marks = ",".join("?" * len(best))
        rows = {r["id"]: r for r in self.store.all(f"""
          SELECT ch.id, ch.page, ch.text, d.id AS document_id, d.name AS document, d.ext
          FROM chunks ch JOIN documents d ON d.id = ch.document_id WHERE ch.id IN ({marks})""", best)}
        return [rows[cid] for cid in best if cid in rows]


def build_messages(question: str, sources: list[dict], history: list[dict]) -> list[dict]:
    blocks = []
    for i, s in enumerate(sources, 1):
        where = f", page {s['page']}" if s["ext"] == ".pdf" else ""
        blocks.append(f"[{i}] ({s['document']}{where})\n{s['text']}")
    context = "\n\n".join(blocks) if blocks else "(no matching passages were found)"
    messages = [{"role": "system", "content": SYSTEM}]
    for m in history[-6:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": f"Sources:\n\n{context}\n\nQuestion: {question}"})
    return messages
