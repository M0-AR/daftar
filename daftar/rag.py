"""Retrieval with match scores, optional reranking, and prompt building."""
import re
import threading

import numpy as np

WORD = re.compile(r"\w+", re.UNICODE)
MIN_VECTOR_SIM = 0.2
STOP = set("""a an and are as at be by can do does for from has have how i in is it its me my of on or
our that the their this to was what when where which who why will with you your about into than then
there these those were been being please tell give show much many need needed some any all also just""".split())


def terms(text: str) -> list[str]:
    return list(dict.fromkeys(w for w in WORD.findall(text.lower()) if len(w) > 1 and w not in STOP))


STEM = 6  # words this long match on their first 6 letters: clause/clauses, terminate/termination


def fts_query(question: str) -> str:
    return " OR ".join(f'"{w[:STEM]}"*' if len(w) >= STEM else f'"{w}"' for w in terms(question))


def coverage(query_terms: list[str], text: str) -> float:
    """Share of the question's terms that appear in the passage (long words match by prefix)."""
    if not query_terms:
        return 0.0
    words = set(WORD.findall(text.lower()))
    stems = {w[:STEM] for w in words if len(w) >= STEM}
    return sum(t in words or (len(t) >= STEM and t[:STEM] in stems) for t in query_terms) / len(query_terms)


class Retriever:
    def __init__(self, store):
        self.store = store
        self._cache = {}
        self._lock = threading.Lock()

    @staticmethod
    def _scope(collection_id):
        return ("AND e.collection_id = ?", (collection_id,)) if collection_id is not None else ("", ())

    def lexical(self, question: str, collection_id, n: int) -> list[int]:
        query = fts_query(question)
        if not query:
            return []
        where, args = self._scope(collection_id)
        rows = self.store.all(f"""
          SELECT ch.id FROM chunks_fts f JOIN chunks ch ON ch.id = f.rowid JOIN entries e ON e.id = ch.entry_id
          WHERE chunks_fts MATCH ? {where} ORDER BY bm25(chunks_fts) LIMIT ?""", (query, *args, n))
        return [r["id"] for r in rows]

    def _matrix(self, collection_id, embed_model: str):
        where, args = self._scope(collection_id)
        base = f"""FROM chunks ch JOIN entries e ON e.id = ch.entry_id
                   WHERE e.embed_model = ? AND e.status = 'ready' AND ch.embedding IS NOT NULL {where}"""
        sig = self.store.one(f"SELECT COUNT(*) AS n, COALESCE(MAX(ch.id), 0) AS m, COALESCE(SUM(ch.id), 0) AS s {base}",
                             (embed_model, *args))
        key, signature = (collection_id, embed_model), (sig["n"], sig["m"], sig["s"])
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == signature:
                return cached[1], cached[2]
        rows = self.store.all(f"SELECT ch.id, ch.embedding {base}", (embed_model, *args))
        index = {r["id"]: i for i, r in enumerate(rows)}
        mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]) if rows else None
        with self._lock:
            self._cache[key] = (signature, index, mat)
        return index, mat

    def warm(self, collection_id, embed_model: str) -> int:
        index, _ = self._matrix(collection_id, embed_model)
        return len(index)

    def search(self, question: str, collection_id, top_k: int, scan_k: int = 24, query_vec=None,
               embed_model: str | None = None, rerank: bool = True) -> tuple[list[dict], dict]:
        """Return (sources, info). Each source has a 0-100 `match`."""
        keyword_ids = self.lexical(question, collection_id, scan_k)
        vector_ids, sims = [], {}
        if query_vec is not None and embed_model:
            index, mat = self._matrix(collection_id, embed_model)
            if mat is not None:
                q = np.asarray(query_vec, dtype=np.float32)
                q /= np.linalg.norm(q) or 1.0
                scores = mat @ q
                ids = list(index)
                vector_ids = [ids[i] for i in np.argsort(-scores)[:scan_k]]
                sims = {cid: float(scores[index[cid]]) for cid in set(vector_ids) | set(keyword_ids) if cid in index}
                # Nearest neighbours always exist; without keyword support they must be at least somewhat similar.
                vector_ids = [cid for cid in vector_ids if cid in keyword_ids or sims[cid] >= MIN_VECTOR_SIM]

        fused = {}
        for ranking in (keyword_ids, vector_ids):
            for rank, cid in enumerate(ranking):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (60 + rank)
        info = {"keyword_hits": len(keyword_ids), "vector_hits": len(vector_ids), "candidates": len(fused)}
        if not fused:
            return [], info

        marks = ",".join("?" * len(fused))
        rows = {r["id"]: r for r in self.store.all(f"""
          SELECT ch.id, ch.page, ch.text, d.id AS document_id, d.name AS document, d.ext,
                 e.collection_id, c.name AS collection
          FROM chunks ch JOIN entries e ON e.id = ch.entry_id JOIN documents d ON d.id = e.document_id
          JOIN collections c ON c.id = e.collection_id WHERE ch.id IN ({marks})""", list(fused))}
        qterms = terms(question)
        for cid, row in rows.items():
            cov = coverage(qterms, row["text"])
            relevance = 0.7 * max(sims[cid], 0.0) + 0.3 * cov if cid in sims else cov
            row["match"] = int(round(min(max(relevance, 0.0), 1.0) * 100))
            row["_score"] = relevance

        if rerank:
            ordered = sorted(rows.values(), key=lambda r: (r["_score"], fused[r["id"]]), reverse=True)
            best = ordered[0]["_score"]
            ordered = [r for r in ordered if r["_score"] >= 0.5 * best] or ordered[:1]
        else:
            ordered = [rows[cid] for cid in sorted(fused, key=fused.get, reverse=True) if cid in rows]
        picked = ordered[:top_k]
        for r in picked:
            r.pop("_score", None)
        info["dropped_as_weak"] = max(0, min(len(rows), top_k) - len(picked)) if rerank else 0
        return picked, info


SYSTEM = """You are Daftar, a private assistant that answers questions about the user's own documents.
The current collection is "{collection}". Files in it: {files}.

Rules:
- Base every factual statement ONLY on the numbered passages. Cite the passage number in square brackets right after the fact, like [1] or [2][3].
- If the passages do not contain the answer, say plainly that the documents do not say. Never invent facts, names, numbers or dates.
- For greetings or questions about which files are available, answer briefly and naturally using the file list above.
- Reply in the same language as the question."""


def build_messages(question: str, sources: list[dict], history: list[dict], collection: str,
                   files: list[str]) -> list[dict]:
    blocks = []
    for i, s in enumerate(sources, 1):
        where = f", page {s['page']}" if s["ext"] == ".pdf" else ""
        blocks.append(f"[{i}] ({s['document']}{where})\n{s['text']}")
    context = "\n\n".join(blocks) if blocks else "(no passage in the documents matched this question)"
    file_list = ", ".join(files[:60]) + (f" and {len(files) - 60} more" if len(files) > 60 else "") if files else "none yet"
    messages = [{"role": "system", "content": SYSTEM.format(collection=collection, files=file_list)}]
    for m in history[-6:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": f"Passages:\n\n{context}\n\nQuestion: {question}"})
    return messages
