"""End-to-end against the real local Ollama. Run with: pytest -m live"""
import json

import pytest
from fastapi.testclient import TestClient

from conftest import wait_ingest
from daftar.server import create_app

DOC = """Lease Agreement - Unit 4B, Harbor Point.
The monthly rent is 2,350 euros, due on the fifth day of each month.
The security deposit equals two months of rent.
Pets are allowed only with written consent of the landlord.
""" + "\n".join(f"Clause {i}: standard maintenance and access provisions apply." for i in range(40))


@pytest.mark.live
def test_real_ollama_answers_from_the_document(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1", headers={"X-Daftar": "1"}) as c:
        status = c.get("/api/status").json()
        assert status["ollama"] and status["chat_model"], status
        cid = c.post("/api/collections", json={"name": "Lease"}).json()["id"]
        c.post("/api/documents", files=[("files", ("lease.txt", DOC.encode(), "text/plain"))],
               data={"collection_ids": [str(cid)]})
        wait_ingest(c.app)
        assert c.get("/api/documents").json()[0]["entries"][0]["status"] == "ready"
        chat_id = c.post("/api/chats", json={"collection_id": cid}).json()["id"]
        r = c.post(f"/api/chats/{chat_id}/ask", json={"question": "How much is the monthly rent?"})
        events = [json.loads(line) for line in r.text.splitlines() if line]
        answer = "".join(e["text"] for e in events if e["type"] == "token")
        print(f"\nmodel={events[0].get('model')} mode={events[0].get('mode')} "
              f"top match={events[0]['sources'][0]['match']}%\nanswer={answer}")
        assert not [e for e in events if e["type"] == "error"]
        assert "2,350" in answer or "2350" in answer
        assert events[0]["sources"][0]["match"] > 0
