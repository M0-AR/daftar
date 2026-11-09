from fpdf import FPDF

from conftest import CONTRACT, wait_ingest

TXT = ("msa.txt", CONTRACT.encode(), "text/plain")
RECIPE = ("bread.txt", b"Recipe: mix flour, water and salt, then bake for forty minutes.", "text/plain")


# --- documents & collections -------------------------------------------------------------
def test_upload_index_and_ask_with_match_scores(api, fake):
    cid = api.collection()
    res = api.upload(cid, TXT)
    assert res[0]["added_to"] == [cid]
    doc = api.c.get(f"/api/documents?collection_id={cid}").json()[0]
    entry = doc["entries"][0]
    assert entry["status"] == "ready" and entry["chunks"] > 1 and entry["embed_model"] == "nomic-embed-text"

    events = api.ask(api.chat(cid), "What is the termination notice period?")
    meta = events[0]
    assert meta["type"] == "meta" and meta["mode"] == "hybrid" and meta["model"] == "fake-chat:latest"
    top = meta["sources"][0]
    assert "ninety days" in top["text"] and top["document"] == "msa.txt" and top["collection"] == "Contracts"
    assert all(0 <= s["match"] <= 100 for s in meta["sources"])
    assert top["match"] == max(s["match"] for s in meta["sources"])
    assert "".join(e["text"] for e in events if e["type"] == "token") == "The answer is in the passages [1]."
    assert events[-1]["type"] == "done"


def test_one_document_in_several_collections(api):
    legal, archive = api.collection("Legal"), api.collection("Archive")
    api.upload([legal, archive], TXT)
    docs = api.c.get("/api/documents").json()
    assert len(docs) == 1  # stored once
    entries = {e["collection"]: e for e in docs[0]["entries"]}
    assert set(entries) == {"Legal", "Archive"} and all(e["chunks"] > 0 for e in entries.values())

    info = api.c.get(f"/api/documents/{docs[0]['id']}").json()
    assert {e["collection"] for e in info["entries"]} == {"Legal", "Archive"}
    assert info["words"] > 100 and info["read_minutes"] >= 1

    # removing from one collection keeps it in the other
    api.c.delete(f"/api/collections/{legal}/documents/{docs[0]['id']}")
    left = api.c.get("/api/documents").json()[0]["entries"]
    assert [e["collection"] for e in left] == ["Archive"]
    # removing from the last collection deletes the document and its file
    api.c.delete(f"/api/collections/{archive}/documents/{docs[0]['id']}")
    assert api.c.get("/api/documents").json() == []


def test_add_existing_document_to_another_collection(api):
    a, b = api.collection("A"), api.collection("B")
    doc_id = api.upload(a, TXT)[0]["id"]
    assert api.c.post(f"/api/documents/{doc_id}/collections", json={"collection_ids": [a, b]}).json() == {"added_to": [b]}
    wait_ingest(api.app)
    assert api.c.get(f"/api/documents?collection_id={b}").json()[0]["id"] == doc_id


def test_same_file_twice_is_deduplicated(api):
    cid = api.collection()
    api.upload(cid, TXT)
    res = api.upload(cid, ("copy.txt", TXT[1], "text/plain"), ("x.exe", b"MZ", "application/octet-stream"))
    assert res[0]["skipped"] == "already in the chosen collection(s)"
    assert "not supported" in res[1]["skipped"]
    assert len(api.c.get("/api/documents").json()) == 1


def test_documents_filter_by_collection_and_type(api):
    a, b = api.collection("A"), api.collection("B")
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "Warranty period is twenty-four months.")
    api.upload(a, TXT, ("w.pdf", bytes(pdf.output()), "application/pdf"))
    api.upload(b, RECIPE)
    names = lambda q: sorted(d["name"] for d in api.c.get(f"/api/documents{q}").json())  # noqa: E731
    assert names("") == ["bread.txt", "msa.txt", "w.pdf"]
    assert names(f"?collection_id={a}") == ["msa.txt", "w.pdf"]
    assert names("?type=.pdf") == ["w.pdf"]
    assert names(f"?collection_id={b}&type=.pdf") == []


def test_preview_text_and_exports(api):
    cid = api.collection()
    doc_id = api.upload(cid, TXT)[0]["id"]
    pages = api.c.get(f"/api/documents/{doc_id}/text").json()["pages"]
    assert "ninety days" in pages[0]["text"]
    for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK"), ("md", b"# msa"), ("txt", b"Master"), ("html", b"<!doctype")):
        r = api.c.get(f"/api/documents/{doc_id}/export?format={fmt}")
        assert r.status_code == 200 and r.content.startswith(magic), fmt
        assert f'filename="msa.{fmt}"' in r.headers["content-disposition"]
    assert api.c.get(f"/api/documents/{doc_id}/export?format=exe").status_code == 400


def test_pdf_sources_carry_page_numbers(api):
    pdf = FPDF()
    for text in ("Introduction and parties.", "The warranty period is twenty-four months."):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, text)
    cid = api.collection()
    api.upload(cid, ("w.pdf", bytes(pdf.output()), "application/pdf"))
    top = api.ask(api.chat(cid), "How long is the warranty?")[0]["sources"][0]
    assert top["page"] == 2 and "twenty-four" in top["text"]


def test_collections_are_isolated(api):
    a, b = api.collection("A"), api.collection("B")
    api.upload(a, TXT)
    api.upload(b, RECIPE)
    sources = api.ask(api.chat(b), "termination notice period")[0]["sources"]
    assert all(s["document"] == "bread.txt" for s in sources)


def test_collection_status_readiness_and_optimize(api, fake):
    fake.models_installed = ["fake-chat:latest"]  # no embedding model yet
    cid = api.collection()
    api.upload(cid, TXT, RECIPE)
    col = api.c.get("/api/collections").json()[0]
    # keyword search works and re-indexing could not add vectors, so nothing is stale yet
    assert col["documents"] == 2 and col["readiness"] == 100 and col["stale"] == 0
    status = api.c.get(f"/api/collections/{cid}/status").json()
    assert status["vectors"] == 0 and status["embed_ready"] is False
    assert api.c.post(f"/api/collections/{cid}/optimize").json()["requeued"] == 0

    fake.models_installed = ["fake-chat:latest", "nomic-embed-text:latest"]
    api.c.get("/api/status")  # refreshes the cached model check
    col = api.c.get("/api/collections").json()[0]
    assert col["readiness"] == 0 and col["stale"] == 2  # now re-indexing would add semantic search
    assert api.c.post(f"/api/collections/{cid}/optimize").json()["requeued"] == 2
    wait_ingest(api.app)
    col = api.c.get("/api/collections").json()[0]
    assert col["readiness"] == 100 and col["stale"] == 0
    assert api.c.get(f"/api/collections/{cid}/status").json()["vectors"] == col["chunks"]
    assert api.c.post(f"/api/collections/{cid}/optimize").json() == {"requeued": 0, "vectors_cached": col["chunks"]}


def test_changing_block_size_marks_index_stale_and_reindex_applies_it(api):
    cid = api.collection()
    api.upload(cid, TXT)
    before = api.c.get("/api/documents").json()[0]["entries"][0]["chunks"]
    api.c.put("/api/settings", json={"chunk_size": 400, "chunk_overlap": 50})
    assert api.c.get("/api/status").json()["stale_entries"] == 1
    assert api.c.post("/api/reindex").json() == {"queued": 1}
    wait_ingest(api.app)
    entry = api.c.get("/api/documents").json()[0]["entries"][0]
    assert entry["chunks"] > before and entry["chunk_size"] == 400


def test_keyword_fallback_without_embedding_model(api, fake):
    fake.models_installed = ["fake-chat:latest"]
    cid = api.collection()
    api.upload(cid, TXT)
    assert api.c.get("/api/documents").json()[0]["entries"][0]["embed_model"] is None
    meta = api.ask(api.chat(cid), "invoices payable")[0]
    assert meta["mode"] == "keyword" and "forty-five" in meta["sources"][0]["text"]
    assert meta["sources"][0]["match"] == 100  # both query words present


def test_delete_document_removes_file_chunks_and_search_index(api, tmp_path):
    cid = api.collection()
    doc_id = api.upload(cid, TXT)[0]["id"]
    assert (tmp_path / "files" / f"{doc_id}.txt").exists()
    api.c.delete(f"/api/documents/{doc_id}")
    store = api.app.state.store
    assert not (tmp_path / "files" / f"{doc_id}.txt").exists()
    assert store.one("SELECT COUNT(*) AS n FROM chunks")["n"] == 0
    assert store.all("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'termination'") == []


def test_delete_collection_cascades_and_drops_orphans(api):
    a, b = api.collection("A"), api.collection("B")
    api.upload([a, b], TXT)
    api.upload(a, RECIPE)
    api.c.delete(f"/api/collections/{a}")
    assert [d["name"] for d in api.c.get("/api/documents").json()] == ["msa.txt"]  # still in B


def test_empty_file_reports_error(api):
    cid = api.collection()
    api.upload(cid, ("blank.txt", b"   ", "text/plain"))
    entry = api.c.get("/api/documents").json()[0]["entries"][0]
    assert entry["status"] == "error" and "No text" in entry["error"]


def test_duplicate_collection_name_rejected(api):
    api.collection("Legal")
    assert api.c.post("/api/collections", json={"name": "Legal"}).status_code == 409


# --- chat ---------------------------------------------------------------------
def test_follow_up_sends_history_and_file_list(api, fake):
    cid = api.collection()
    api.upload(cid, TXT, RECIPE)
    chat_id = api.chat(cid)
    api.ask(chat_id, "What is the termination notice period?")
    api.ask(chat_id, "And the payment terms?")
    msgs = fake.calls[-1]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert "msa.txt" in msgs[0]["content"] and "bread.txt" in msgs[0]["content"]


def test_small_talk_still_reaches_the_model(api, fake):
    cid = api.collection()
    api.upload(cid, TXT)
    events = api.ask(api.chat(cid), "hi")
    assert events[0]["sources"] == [] and fake.calls  # model answers the greeting, with the file list
    assert "msa.txt" in fake.calls[-1]["messages"][0]["content"]


def test_creativity_level_is_sent_as_temperature(api, fake):
    cid = api.collection()
    api.upload(cid, TXT)
    api.c.put("/api/settings", json={"temperature": 1.4})
    api.ask(api.chat(cid), "termination")
    assert fake.calls[-1]["temperature"] == 1.4


def test_passages_to_use_limits_sources(api):
    cid = api.collection()
    api.upload(cid, TXT)
    api.c.put("/api/settings", json={"top_k": 2, "rerank": False})
    assert len(api.ask(api.chat(cid), "general obligations clause terms")[0]["sources"]) == 2


def test_rerank_drops_weak_passages(api):
    cid = api.collection()
    api.upload(cid, TXT, RECIPE)
    api.c.put("/api/settings", json={"top_k": 10, "rerank": True})
    with_rerank = api.ask(api.chat(cid), "termination notice ninety days writing obligations")[0]["sources"]
    api.c.put("/api/settings", json={"rerank": False})
    without = api.ask(api.chat(cid), "termination notice ninety days writing obligations")[0]["sources"]
    assert len(with_rerank) < len(without)
    assert with_rerank[0]["match"] >= with_rerank[-1]["match"] >= with_rerank[0]["match"] / 2


def test_edit_regenerate_delete_messages(api, fake):
    cid = api.collection()
    api.upload(cid, TXT)
    chat_id = api.chat(cid)
    api.ask(chat_id, "What is the notice period?")
    api.ask(chat_id, "And invoices?")
    msgs = api.c.get(f"/api/chats/{chat_id}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] * 2

    # edit the first question: everything after it goes away, then re-answer
    assert api.c.patch(f"/api/messages/{msgs[0]['id']}", json={"content": "What is the termination notice?"}).status_code == 200
    assert [m["content"] for m in api.c.get(f"/api/chats/{chat_id}").json()["messages"]] == ["What is the termination notice?"]
    api.ask(chat_id, resend=True)
    assert fake.calls[-1]["messages"][-1]["content"].endswith("Question: What is the termination notice?")

    # regenerate: delete the answer, answer again
    msgs = api.c.get(f"/api/chats/{chat_id}").json()["messages"]
    api.c.delete(f"/api/messages/{msgs[-1]['id']}")
    api.ask(chat_id, resend=True)
    msgs = api.c.get(f"/api/chats/{chat_id}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["meta"]["model"] == "fake-chat:latest" and msgs[1]["sources"]

    assert api.c.patch(f"/api/messages/{msgs[1]['id']}", json={"content": "x"}).status_code == 400  # not yours
    assert api.c.post(f"/api/chats/{chat_id}/ask", json={"resend": True}).status_code == 400  # last is an answer


def test_rename_chat_and_search_history(api):
    cid = api.collection()
    api.upload(cid, TXT)
    first, second = api.chat(cid), api.chat(cid)
    api.ask(first, "What about invoices?")
    api.ask(second, "Termination?")
    assert api.c.get(f"/api/chats/{first}").json()["title"] == "What about invoices?"
    api.c.patch(f"/api/chats/{second}", json={"title": "Exit terms"})
    assert [c["title"] for c in api.c.get("/api/chats?q=exit").json()] == ["Exit terms"]
    assert [c["id"] for c in api.c.get("/api/chats?q=passages").json()] == [second, first]  # matches answer text
    assert api.c.get("/api/chats?q=zzz").json() == []
    # switching collection keeps the title
    api.c.patch(f"/api/chats/{second}", json={"collection_id": None})
    chat = api.c.get(f"/api/chats/{second}").json()
    assert chat["title"] == "Exit terms" and chat["collection_id"] is None


def test_chat_without_collection_explains(api):
    events = api.ask(api.c.post("/api/chats", json={}).json()["id"], "hello")
    assert events[0]["type"] == "error" and "collection" in events[0]["text"]


def test_debug_mode_adds_retrieval_details(api):
    cid = api.collection()
    api.upload(cid, TXT)
    assert "debug" not in api.ask(api.chat(cid), "termination")[0]
    api.c.put("/api/settings", json={"debug": True})
    events = api.ask(api.chat(cid), "termination")
    dbg = events[0]["debug"]
    assert dbg["candidates"] >= 1 and dbg["prompt"][0]["role"] == "system" and "search_ms" in dbg["timings"]
    assert "total_ms" in events[-1]["timings"]
    assert any("answered in chat" in line for line in api.c.get("/api/logs").json()["lines"])


# --- search, settings, models ----------------------------------------------------------
def test_search_page_across_all_collections(api):
    a, b = api.collection("A"), api.collection("B")
    api.upload(a, TXT)
    api.upload(b, RECIPE)
    res = api.c.get("/api/search", params={"q": "bake flour"}).json()
    assert res["results"][0]["document"] == "bread.txt" and res["mode"] == "hybrid"
    only_a = api.c.get("/api/search", params={"q": "bake flour", "collection_id": a}).json()["results"]
    assert all(r["collection"] == "A" for r in only_a)
    assert api.c.get("/api/search", params={"q": " "}).status_code == 400


def test_settings_are_clamped_and_reset(api):
    s = api.c.put("/api/settings", json={"temperature": 9, "top_k": 0, "chunk_size": 300, "chunk_overlap": 900,
                                         "theme": "light", "unknown": 1}).json()
    assert s["temperature"] == 2.0 and s["top_k"] == 1 and s["theme"] == "light" and "unknown" not in s
    assert s["chunk_overlap"] < s["chunk_size"]
    d = api.c.post("/api/settings/reset").json()
    assert d["temperature"] == 0.2 and d["theme"] == "dark" and d["chat_model"] == "gemma3:1b"


def test_chat_model_falls_back_when_default_missing(api):
    status = api.c.get("/api/status").json()
    assert status["settings"]["chat_model"] == "gemma3:1b" and status["chat_model"] == "fake-chat:latest"


def test_model_download_streams_progress(api):
    r = api.c.post("/api/models/pull", json={"name": "gemma3:1b"})
    import json as _json
    events = [_json.loads(x) for x in r.text.splitlines()]
    assert events[1] == {"status": "downloading", "total": 100, "completed": 50}
    assert events[-1] == {"status": "success", "done": True}
    assert api.c.post("/api/models/pull", json={"name": "bad name"}).status_code == 400
    assert api.c.get("/api/models").json()[0]["name"] == "fake-chat:latest"


def test_keyword_search_matches_word_variants(api, fake):
    fake.models_installed = ["fake-chat:latest"]
    cid = api.collection()
    api.upload(cid, ("c.txt", b"Either party may terminate the agreement. Each clause is binding.", "text/plain"))
    top = api.ask(api.chat(cid), "termination clauses")[0]["sources"][0]
    assert top["match"] == 100  # terminate~termination, clause~clauses


def test_prompt_is_sized_to_fit_the_model(api, fake):
    """Ollama silently drops the start of prompts longer than num_ctx, so the window must cover the prompt."""
    cid = api.collection()
    api.upload(cid, TXT)
    api.ask(api.chat(cid), "termination notice")
    assert fake.calls[-1]["num_ctx"] in (4096, 8192)
    fake.context_length = 2048  # a tiny model: passages are trimmed instead of silently truncated
    api.c.put("/api/settings", json={"top_k": 20, "rerank": False, "debug": True})
    events = api.ask(api.chat(cid), "general obligations clause terms")
    assert fake.calls[-1]["num_ctx"] == 2048
    assert events[0]["debug"]["estimated_prompt_tokens"] <= 2048 or len(events[0]["sources"]) == 1
    assert events[0]["debug"]["passages_trimmed_to_fit"] > 0


def test_chat_models_are_classified_by_capability(api, fake):
    fake.models_installed = ["all-minilm:latest", "tiny-llm:latest"]  # an embedder without "embed" in its name

    def show(self, model):
        return {"context_length": 8192, "capabilities": ["embedding"] if "minilm" in model else ["completion"]}
    fake.show = show
    status = api.c.get("/api/status").json()
    assert status["chat_model"] == "tiny-llm:latest" and status["chat_models"] == ["tiny-llm:latest"]
    assert status["embed_models"] == ["all-minilm:latest"]


def test_fallback_prefers_the_largest_chat_model(api, fake):
    fake.models_installed = ["a-tiny:0.5b", "b-small:1.5b", "nomic-embed-text:latest"]
    real = fake.model_list
    fake.model_list = lambda self: [{**m, "size": {"a-tiny:0.5b": 300, "b-small:1.5b": 900}.get(m["name"], 1)} for m in real(self)]
    try:
        assert api.c.get("/api/status").json()["chat_model"] == "b-small:1.5b"
    finally:
        fake.model_list = real


def test_oversized_upload_is_rejected_without_leftovers(api, tmp_path, monkeypatch):
    import daftar.server
    monkeypatch.setattr(daftar.server, "MAX_UPLOAD_BYTES", 3 << 20)
    cid = api.collection()
    res = api.upload(cid, ("big.txt", b"a" * (4 << 20), "text/plain"), ("ok.txt", b"b " * (1 << 20), "text/plain"))
    assert res[0]["skipped"] == "larger than 3 MB" and "added_to" in res[1]
    assert [d["name"] for d in api.c.get("/api/documents").json()] == ["ok.txt"]
    assert not list((tmp_path / "files").glob(".upload-*"))  # no temp files left behind
    assert (tmp_path / "files" / f"{res[1]['id']}.txt").stat().st_size == 2 << 20


def test_stop_saves_partial_answer_and_closes_the_model_stream(tmp_path, fake):
    """Pressing Stop (client disconnect) must end generation upstream and keep what was written so far.
    Runs a real uvicorn server: TestClient never reports a disconnect."""
    import socket
    import threading

    import httpx
    import uvicorn

    from daftar.server import create_app
    closed = threading.Event()

    def slow_chat(self, model, messages, temperature=0.2, num_ctx=8192):
        try:
            for i in range(10_000):
                yield f"word{i} "
                threading.Event().wait(0.005)
        finally:
            closed.set()  # the real client closes its HTTP stream to Ollama here
    original, fake.chat = fake.chat, slow_chat
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path, ollama_factory=fake), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            threading.Event().wait(0.02)
        c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"X-Daftar": "1"}, timeout=30)
        cid = c.post("/api/collections", json={"name": "C"}).json()["id"]
        c.post("/api/documents", files=[("files", TXT)], data={"collection_ids": [str(cid)]})
        c.app_state = None
        chat_id = c.post("/api/chats", json={"collection_id": cid}).json()["id"]
        with c.stream("POST", f"/api/chats/{chat_id}/ask", json={"question": "termination"}) as r:
            for n, _ in enumerate(r.iter_lines()):
                if n == 5:
                    break
        assert closed.wait(5), "model stream was not closed after the client went away"
        for _ in range(100):
            msgs = c.get(f"/api/chats/{chat_id}").json()["messages"]
            if msgs[-1]["role"] == "assistant":
                break
            threading.Event().wait(0.05)
        assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"].startswith("word0")
        assert msgs[-1]["meta"].get("stopped") is True
    finally:
        fake.chat = original
        server.should_exit = True
        thread.join(5)


# --- beyond the reference: all-collection chats, code files, section citations -----------------
def test_chat_can_search_all_collections(api, fake):
    a, b = api.collection("A"), api.collection("B")
    api.upload(a, TXT)
    api.upload(b, RECIPE)
    chat_id = api.c.post("/api/chats", json={"all_collections": True}).json()["id"]
    docs = {s["document"] for s in api.ask(chat_id, "termination notice bake flour")[0]["sources"]}
    assert docs == {"msa.txt", "bread.txt"}
    system = fake.calls[-1]["messages"][0]["content"]
    assert "all collections" in system and "msa.txt" in system and "bread.txt" in system
    # switch to one collection and back
    api.c.patch(f"/api/chats/{chat_id}", json={"collection_id": b})
    chat = api.c.get(f"/api/chats/{chat_id}").json()
    assert chat["collection_id"] == b and chat["all_collections"] == 0
    assert {s["document"] for s in api.ask(chat_id, "termination notice bake flour")[0]["sources"]} == {"bread.txt"}
    api.c.patch(f"/api/chats/{chat_id}", json={"all_collections": True})
    assert api.c.get(f"/api/chats/{chat_id}").json()["all_collections"] == 1


def test_code_and_html_files_are_indexed(api):
    cid = api.collection()
    code = b"export function buildLoginUrl(redirectUri) {\n  return `/sso/start?redirect=${redirectUri}`;\n}\n"
    page = b"<html><script>steal()</script><h1>Guide</h1><p>Install with the setup wizard.</p></html>"
    res = api.upload(cid, ("session.js", code, "text/javascript"), ("guide.html", page, "text/html"))
    assert all("added_to" in r for r in res)
    hit = api.c.get("/api/search", params={"q": "how is the login url built", "collection_id": cid}).json()["results"][0]
    assert hit["document"] == "session.js"  # camelCase identifier found by its words
    doc_id = res[1]["id"]
    text = api.c.get(f"/api/documents/{doc_id}/text").json()["pages"][0]["text"]
    assert "steal()" not in text and "Install with the setup wizard." in text


def test_sources_carry_their_section(api, fake):
    import docx as _docx
    import io as _io
    d = _docx.Document()
    d.add_heading("Staff Handbook", level=1)
    d.add_heading("Parental Leave", level=2)
    d.add_paragraph("Birth mothers receive sixteen weeks at full pay.")
    buf = _io.BytesIO()
    d.save(buf)
    cid = api.collection()
    api.upload(cid, ("handbook.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
    events = api.ask(api.chat(cid), "How long is parental leave?")
    top = events[0]["sources"][0]
    assert top["section"] == "Staff Handbook > Parental Leave" and top["match_kind"] == "meaning"
    assert '"source": "handbook.docx, Parental Leave"' in fake.calls[-1]["messages"][-1]["content"]


def test_keyword_only_match_is_labelled_as_word_match(api, fake):
    fake.models_installed = ["fake-chat:latest"]
    cid = api.collection()
    api.upload(cid, TXT)
    assert api.ask(api.chat(cid), "invoices payable")[0]["sources"][0]["match_kind"] == "words"


def test_v2_database_is_migrated_and_reindexed(tmp_path, fake):
    """Opening a v2 database adds the new columns and re-queues documents so sections get computed."""
    from fastapi.testclient import TestClient as _TC
    from daftar.server import create_app as _create
    with _TC(_create(tmp_path, ollama_factory=fake), base_url="http://127.0.0.1", headers={"X-Daftar": "1"}) as c:
        cid = c.post("/api/collections", json={"name": "H"}).json()["id"]
        c.post("/api/documents", files=[("files", ("h.md", b"# Handbook\n\n## Leave\n\nSixteen weeks at full pay.", "text/markdown"))],
               data={"collection_ids": [str(cid)]})
        wait_ingest(c.app)
        store = c.app.state.store
        with store.lock, store.db:  # simulate a v2 database: no sections yet, old cached text, old version stamp
            store.db.execute("UPDATE chunks SET section = NULL")
            store.db.execute("""UPDATE documents SET content = '[[1, "Handbook. Leave: sixteen weeks."]]'""")
            store.db.execute("PRAGMA user_version = 2")
    with _TC(_create(tmp_path, ollama_factory=fake), base_url="http://127.0.0.1", headers={"X-Daftar": "1"}) as c:
        wait_ingest(c.app)
        assert c.app.state.store.one("PRAGMA user_version")["user_version"] == 3
        assert c.app.state.store.one("SELECT section FROM chunks")["section"] == "Handbook > Leave"
