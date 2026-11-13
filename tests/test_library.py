import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from daftar.library import (DB_PATH, FILES_PREFIX, FORMAT_VERSION, MANIFEST, SETTINGS_PATH,
                            LibraryError, create_archive, list_backups, prune_backups,
                            read_manifest, restore_archive)
from daftar.store import SCHEMA_VERSION, Store


def make_library(tmp_path: Path):
    home = tmp_path / "home"
    (home / "files").mkdir(parents=True)
    store = Store(home / "daftar.db")
    cid = store.run("INSERT INTO collections(name, created) VALUES (?, ?)", ("Contracts", 1.0))
    content = "The termination notice period is ninety days in writing."
    doc = store.run("INSERT INTO documents(name, ext, size, sha256, created) VALUES (?,?,?,?,?)",
                    ("msa.txt", ".txt", len(content), hashlib.sha256(content.encode()).hexdigest(), 1.0))
    (home / "files" / f"{doc}.txt").write_text(content, encoding="utf-8")
    entry = store.run("INSERT INTO entries(document_id, collection_id, status, chunks, created) "
                      "VALUES (?,?,?,?,?)", (doc, cid, "ready", 1, 1.0))
    store.replace_chunks(entry, [(1, content, "Contract > Term")])
    chat = store.run("INSERT INTO chats(collection_id, title, created, updated) VALUES (?,?,?,?)",
                     (cid, "Notice", 2.0, 2.0))
    store.run("INSERT INTO messages(chat_id, role, content, created) VALUES (?,?,?,?)",
              (chat, "user", "What is the notice period?", 2.0))
    (home / "settings.json").write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    return home, store, doc, chat


@pytest.fixture
def library(tmp_path):
    home, store, doc, chat = make_library(tmp_path)
    yield home, store, doc, chat
    store.close()


def rewrite_archive(source: Path, dest: Path, replace=None, change_manifest=None):
    replace = replace or {}
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(dest, "w") as dst:
        for info in src.infolist():
            data = replace.get(info.filename, src.read(info.filename))
            if info.filename == MANIFEST and change_manifest:
                manifest = json.loads(data)
                change_manifest(manifest)
                data = json.dumps(manifest).encode()
            dst.writestr(info, data)


def test_export_has_a_manifest_and_every_part(library, tmp_path):
    home, store, doc, chat = library
    archive = tmp_path / "library.daftar"
    manifest = create_archive(home, store, archive)

    assert manifest["app"] == "Daftar" and manifest["format"] == FORMAT_VERSION
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["counts"] == {"collections": 1, "documents": 1, "entries": 1,
                                  "chunks": 1, "chats": 1, "messages": 1}
    assert manifest["database"]["path"] == DB_PATH
    assert manifest["settings"]["path"] == SETTINGS_PATH
    assert manifest["files"][0]["path"] == FILES_PREFIX + f"{doc}.txt"
    with zipfile.ZipFile(archive) as z:
        assert set(z.namelist()) == {MANIFEST, DB_PATH, SETTINGS_PATH, FILES_PREFIX + f"{doc}.txt"}
        assert json.loads(z.read(MANIFEST))["counts"]["messages"] == 1


def test_restore_replaces_data_and_files_but_keeps_local_settings(library, tmp_path):
    home, store, doc, chat = library
    archive = tmp_path / "library.daftar"
    create_archive(home, store, archive)

    store.run("DELETE FROM collections")
    store.run("DELETE FROM chats")
    (home / "files" / f"{doc}.txt").unlink()
    (home / "settings.json").write_text(json.dumps({"theme": "dark"}), encoding="utf-8")

    result = restore_archive(home, archive, store)

    assert result["restored"] is True
    assert result["counts"]["collections"] == 1 and result["counts"]["chats"] == 1
    assert store.one("SELECT name FROM collections")["name"] == "Contracts"
    assert store.one("SELECT content FROM messages")["content"] == "What is the notice period?"
    assert (home / "files" / f"{doc}.txt").read_text(encoding="utf-8").startswith("The termination")
    assert store.all("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("termination",))
    assert json.loads((home / "settings.json").read_text())["theme"] == "dark"
    assert (home / "backups" / result["safety_backup"]).is_file()


def test_corrupt_file_is_rejected_before_anything_changes(library, tmp_path):
    home, store, doc, chat = library
    archive = tmp_path / "library.daftar"
    create_archive(home, store, archive)
    bad = tmp_path / "bad.daftar"
    original = (home / "files" / f"{doc}.txt").read_bytes()
    rewrite_archive(archive, bad, replace={FILES_PREFIX + f"{doc}.txt": b"x" * len(original)})

    with pytest.raises(LibraryError, match="checksum"):
        restore_archive(home, bad, store)
    assert store.one("SELECT name FROM collections")["name"] == "Contracts"
    assert (home / "files" / f"{doc}.txt").read_text().startswith("The termination")


def test_unsafe_manifest_path_is_rejected(library, tmp_path):
    home, store, doc, chat = library
    archive = tmp_path / "library.daftar"
    create_archive(home, store, archive)
    bad = tmp_path / "bad.daftar"

    def unsafe(manifest):
        manifest["files"][0]["path"] = FILES_PREFIX + "../../escape.txt"

    rewrite_archive(archive, bad, change_manifest=unsafe)
    with pytest.raises(LibraryError, match="unsafe"):
        read_manifest(bad)
    with pytest.raises(LibraryError, match="unsafe"):
        restore_archive(home, bad, store)


def test_a_failed_export_leaves_no_partial_archive(library, tmp_path, monkeypatch):
    home, store, doc, chat = library
    dest = tmp_path / "interrupted.daftar"

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("daftar.library.os.replace", fail)
    with pytest.raises(OSError):
        create_archive(home, store, dest)
    assert not dest.exists()
    assert not list(tmp_path.glob(".interrupted.daftar.*.tmp"))


def test_large_original_file_round_trips(library, tmp_path):
    home, store, doc, chat = library
    payload = b"0123456789abcdef" * (512 * 1024)  # 8 MiB, stored without compression
    (home / "files" / f"{doc}.txt").write_bytes(payload)
    archive = tmp_path / "large.daftar"
    create_archive(home, store, archive)
    (home / "files" / f"{doc}.txt").unlink()
    restore_archive(home, archive, store)
    assert (home / "files" / f"{doc}.txt").read_bytes() == payload


def test_prune_keeps_seven_days_and_four_weeks(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    for day in range(1, 11):
        (folder / f"Daftar-202601{day:02d}-120000.daftar").write_bytes(b"x")
    (folder / "Daftar-20260110-130000.daftar").write_bytes(b"x")
    (folder / "Daftar-before-restore-20260109-120000.daftar").write_bytes(b"x")
    (folder / "notes.txt").write_text("keep me")

    assert prune_backups(folder) == 4
    remaining = {p.name for p in folder.iterdir()}
    assert remaining == {f"Daftar-202601{day:02d}-120000.daftar" for day in range(4, 10)} | {
        "Daftar-20260110-130000.daftar", "Daftar-before-restore-20260109-120000.daftar", "notes.txt"}


def test_an_older_schema_is_migrated_on_restore(library, tmp_path):
    home, store, doc, chat = library
    archive = tmp_path / "library.daftar"
    create_archive(home, store, archive)
    old_db = tmp_path / "old.db"
    with zipfile.ZipFile(archive) as z:
        old_db.write_bytes(z.read(DB_PATH))
    with sqlite3.connect(old_db) as db:
        db.execute("PRAGMA journal_mode = DELETE")
        db.execute("PRAGMA user_version = 2")
    old = tmp_path / "old.daftar"

    def older(manifest):
        manifest["schema_version"] = 2
        manifest["database"]["size"] = old_db.stat().st_size
        manifest["database"]["sha256"] = hashlib.sha256(old_db.read_bytes()).hexdigest()

    rewrite_archive(archive, old, replace={DB_PATH: old_db.read_bytes()}, change_manifest=older)
    result = restore_archive(home, old, store)
    assert result["counts"]["documents"] == 1
    assert store.schema_version() == SCHEMA_VERSION
    assert store.one("SELECT status FROM entries")["status"] == "queued"


def test_a_live_database_is_snapshotted_before_migration(tmp_path):
    home, store, doc, chat = make_library(tmp_path)
    store.close()
    path = home / "daftar.db"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode = DELETE")
        db.execute("PRAGMA user_version = 2")

    store = Store(path)
    try:
        assert store.schema_version() == SCHEMA_VERSION
        assert store.one("SELECT status FROM entries")["status"] == "queued"
    finally:
        store.close()

    snapshot = path.with_name(path.name + ".v2.bak")
    assert snapshot.is_file()
    with sqlite3.connect(snapshot) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT status FROM entries").fetchone()[0] == "ready"


# --- API ------------------------------------------------------------------------
def test_api_export_and_import_round_trip(client, api):
    cid = api.collection()
    api.upload(cid, ("msa.txt", b"The termination notice period is ninety days.", "text/plain"))
    api.ask(api.chat(cid), "What is the notice period?")
    exported = client.get("/api/library/export")
    assert exported.status_code == 200 and exported.content[:2] == b"PK"

    api.upload(api.collection("Other"), ("other.txt", b"Other document.", "text/plain"))
    assert len(client.get("/api/documents").json()) == 2

    imported = client.post("/api/library/import",
                           files={"file": ("Daftar-test.daftar", exported.content, "application/zip")})
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["counts"]["collections"] == 1 and body["counts"]["documents"] == 1
    assert body["counts"]["messages"] >= 1
    docs = client.get("/api/documents").json()
    assert [d["name"] for d in docs] == ["msa.txt"]


def test_api_backups_create_list_download_and_restore(client, api):
    cid = api.collection()
    api.upload(cid, ("msa.txt", b"The termination notice period is ninety days.", "text/plain"))
    made = client.post("/api/backups")
    assert made.status_code == 200, made.text
    item = made.json()
    assert item["name"].startswith("Daftar-") and item["size"] > 0

    listed = client.get("/api/backups").json()
    assert listed["items"][0]["name"] == item["name"] and listed["folder"].startswith(str(client.app.state.settings.path.parent))
    download = client.get("/api/backups/download", params={"name": item["name"]})
    assert download.status_code == 200 and download.content[:2] == b"PK"

    api.upload(api.collection("Other"), ("other.txt", b"Other document.", "text/plain"))
    restored = client.post("/api/backups/restore", json={"name": item["name"]})
    assert restored.status_code == 200, restored.text
    assert [d["name"] for d in client.get("/api/documents").json()] == ["msa.txt"]


def test_api_rejects_bad_archives_and_paths(client, api):
    bad = client.post("/api/library/import",
                      files={"file": ("bad.daftar", b"not a zip", "application/zip")})
    assert bad.status_code == 400
    assert "Daftar library" in bad.json()["detail"]
    assert client.post("/api/backups/restore", json={"name": "../daftar.db"}).status_code == 400
    assert client.get("/api/backups/download", params={"name": ".."}).status_code == 400
    assert client.get("/api/backups/download", params={"name": "Daftar-missing.daftar"}).status_code == 404


def test_new_backup_settings_are_visible_and_saved(client, tmp_path):
    status = client.get("/api/status").json()
    assert status["settings"]["backup_auto"] is True and status["settings"]["backup_dir"] == ""
    saved = client.put("/api/settings", json={"backup_auto": False}).json()
    assert saved["backup_auto"] is False
    assert client.get("/api/backups").json()["auto"] is False

    folder = tmp_path / "chosen-backups"
    assert client.put("/api/settings", json={"backup_dir": str(folder)}).status_code == 200
    made = client.post("/api/backups").json()
    assert (folder / made["name"]).is_file()
    not_a_folder = tmp_path / "settings.json"
    not_a_folder.write_text("x")
    assert client.put("/api/settings", json={"backup_dir": str(not_a_folder)}).status_code == 400
