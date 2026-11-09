import json

from fpdf import FPDF

from conftest import wait_ingest

CONTRACT = """Master Services Agreement between Northwind Traders and Contoso Ltd.
The termination notice period is ninety days in writing.
Invoices are payable within forty-five days of receipt.
""" + "\n".join(f"Filler clause {i} about general obligations and miscellaneous terms." for i in range(60))


def make_collection(client, name="Contracts"):
    r = client.post("/api/collections", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def upload(client, cid, *files):
    r = client.post(f"/api/collections/{cid}/documents", files=[("files", f) for f in files])
    assert r.status_code == 200, r.text
    wait_ingest(client.app_ref)
    return r.json()


def ask(client, chat_id, question):
    r = client.post(f"/api/chats/{chat_id}/ask", json={"question": question})
    assert r.status_code == 200
    return [json.loads(line) for line in r.text.splitlines() if line]


def test_full_flow_upload_index_ask(client):
    cid = make_collection(client)
    res = upload(client, cid, ("msa.txt", CONTRACT.encode(), "text/plain"))
    assert "id" in res[0]

    docs = client.get(f"/api/documents?collection_id={cid}").json()
    assert docs[0]["status"] == "ready" and docs[0]["chunks"] > 1
    assert docs[0]["embed_model"] == "nomic-embed-text"  # hybrid search on

    chat_id = client.post("/api/chats", json={"collection_id": cid}).json()["id"]
    events = ask(client, chat_id, "What is the termination notice period?")
    meta = events[0]
    assert meta["type"] == "meta" and meta["mode"] == "hybrid" and meta["model"] == "fake-chat:latest"
    assert "ninety days" in meta["sources"][0]["text"]
    assert "".join(e["text"] for e in events if e["type"] == "token") == "The answer is in the sources [1]."
    assert events[-1]["type"] == "done"

    chat = client.get(f"/api/chats/{chat_id}").json()
    assert chat["title"] == "What is the termination notice period?"
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    assert chat["messages"][1]["sources"][0]["document"] == "msa.txt"

    stats = client.get("/api/stats").json()
    assert stats["collections"] == 1 and stats["documents"] == 1 and stats["chunks"] == docs[0]["chunks"]


def test_follow_up_sends_history(client, fake):
    cid = make_collection(client)
    upload(client, cid, ("msa.txt", CONTRACT.encode(), "text/plain"))
    chat_id = client.post("/api/chats", json={"collection_id": cid}).json()["id"]
    ask(client, chat_id, "What is the termination notice period?")
    ask(client, chat_id, "And the payment terms?")
    roles = [m["role"] for m in fake.last_messages]
    assert roles == ["system", "user", "assistant", "user"]


def test_pdf_sources_carry_page_numbers(client):
    pdf = FPDF()
    for text in ("Introduction and parties.", "The warranty period is twenty-four months."):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, text)
    cid = make_collection(client)
    upload(client, cid, ("w.pdf", bytes(pdf.output()), "application/pdf"))
    chat_id = client.post("/api/chats", json={"collection_id": cid}).json()["id"]
    top = ask(client, chat_id, "How long is the warranty?")[0]["sources"][0]
    assert top["page"] == 2 and "twenty-four" in top["text"]


def test_keyword_fallback_without_embedding_model(client, fake):
    fake.models_installed = ["fake-chat:latest"]
    cid = make_collection(client)
    upload(client, cid, ("msa.txt", CONTRACT.encode(), "text/plain"))
    assert client.get("/api/documents").json()[0]["embed_model"] is None
    assert client.get("/api/status").json()["stale_documents"] == 1
    chat_id = client.post("/api/chats", json={"collection_id": cid}).json()["id"]
    meta = ask(client, chat_id, "invoices payable")[0]
    assert meta["mode"] == "keyword" and "forty-five" in meta["sources"][0]["text"]

    fake.models_installed = ["fake-chat:latest", "nomic-embed-text:latest"]
    assert client.post("/api/reindex").json() == {"queued": 1}
    wait_ingest(client.app_ref)
    assert client.get("/api/documents").json()[0]["embed_model"] == "nomic-embed-text"


def test_chat_model_falls_back_when_default_missing(client):
    status = client.get("/api/status").json()
    assert status["settings"]["chat_model"] == "gemma3:1b"
    assert status["chat_model"] == "fake-chat:latest"


def test_collections_are_isolated(client):
    a, b = make_collection(client, "A"), make_collection(client, "B")
    upload(client, a, ("msa.txt", CONTRACT.encode(), "text/plain"))
    upload(client, b, ("other.txt", b"Recipe: add flour and water.", "text/plain"))
    chat_id = client.post("/api/chats", json={"collection_id": b}).json()["id"]
    sources = ask(client, chat_id, "termination notice period")[0]["sources"]
    assert all(s["document"] == "other.txt" for s in sources)


def test_no_sources_gives_honest_answer_without_calling_model(client):
    cid = make_collection(client)
    chat_id = client.post("/api/chats", json={"collection_id": cid}).json()["id"]
    events = ask(client, chat_id, "anything?")
    assert "could not find" in "".join(e.get("text", "") for e in events)


def test_skips_unsupported_and_duplicates(client):
    cid = make_collection(client)
    upload(client, cid, ("msa.txt", CONTRACT.encode(), "text/plain"))
    res = upload(client, cid, ("msa-copy.txt", CONTRACT.encode(), "text/plain"), ("x.exe", b"MZ", "application/octet-stream"))
    assert res[0]["skipped"] == "already in this collection"
    assert "not supported" in res[1]["skipped"]


def test_delete_document_removes_file_chunks_and_search_index(client, tmp_path):
    cid = make_collection(client)
    doc_id = upload(client, cid, ("msa.txt", CONTRACT.encode(), "text/plain"))[0]["id"]
    assert (tmp_path / "files" / f"{doc_id}.txt").exists()
    client.delete(f"/api/documents/{doc_id}")
    store = client.app_ref.state.store
    assert not (tmp_path / "files" / f"{doc_id}.txt").exists()
    assert store.one("SELECT COUNT(*) AS n FROM chunks")["n"] == 0
    assert store.all("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'termination'") == []


def test_delete_collection_cascades(client):
    cid = make_collection(client)
    upload(client, cid, ("msa.txt", CONTRACT.encode(), "text/plain"))
    client.delete(f"/api/collections/{cid}")
    stats = client.get("/api/stats").json()
    assert (stats["collections"], stats["documents"], stats["chunks"]) == (0, 0, 0)


def test_empty_file_reports_error(client):
    cid = make_collection(client)
    upload(client, cid, ("blank.txt", b"   ", "text/plain"))
    doc = client.get("/api/documents").json()[0]
    assert doc["status"] == "error" and "No text" in doc["error"]
