"""Portable backups: one .daftar archive holds the database, settings and original files."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from daftar import APP_NAME, __version__
from daftar.store import SCHEMA_VERSION, Store

FORMAT_VERSION = 1
MANIFEST = "manifest.json"
DB_PATH = "library/daftar.db"
SETTINGS_PATH = "library/settings.json"
FILES_PREFIX = "library/files/"
BACKUP_RE = re.compile(r"^Daftar-(\d{8})-(\d{6})(?:-(\d+))?\.daftar$")
DAY = 24 * 60 * 60
KEEP_DAYS = 7
KEEP_WEEKS = 4
log = logging.getLogger(__name__)
_lock = threading.RLock()


class LibraryError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _snapshot(store: Store, dest: Path) -> None:
    dest.unlink(missing_ok=True)
    with store.lock:
        target = sqlite3.connect(str(dest))
        try:
            store.db.backup(target)
        finally:
            target.close()


def _counts(db_path: Path) -> dict:
    tables = ("collections", "documents", "entries", "chunks", "chats", "messages")
    with sqlite3.connect(str(db_path)) as db:
        return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


def _schema_version(db_path: Path) -> int:
    with sqlite3.connect(str(db_path)) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def _member(path: Path, name: str) -> dict:
    return {"path": name, "size": path.stat().st_size, "sha256": _sha256(path)}


def _manifest(db_path: Path, settings_path: Path, files: list[Path]) -> dict:
    entries = [_member(path, FILES_PREFIX + path.name) for path in files]
    settings = _member(settings_path, SETTINGS_PATH) if settings_path.is_file() else None
    return {
        "app": APP_NAME,
        "format": FORMAT_VERSION,
        "app_version": __version__,
        "schema_version": _schema_version(db_path),
        "created_at": _iso_now(),
        "database": _member(db_path, DB_PATH),
        "settings": settings,
        "files": entries,
        "counts": _counts(db_path),
        "total_bytes": sum(item["size"] for item in entries)
        + (settings["size"] if settings else 0)
        + db_path.stat().st_size,
    }


def _data_files(files_dir: Path) -> list[Path]:
    if not files_dir.is_dir():
        return []
    return sorted(p for p in files_dir.iterdir() if p.is_file() and not p.name.startswith("."))


def create_archive(home: Path, store: Store, dest: Path) -> dict:
    """Write a consistent snapshot to ``dest`` atomically; return its manifest."""
    home, dest = Path(home), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _lock, tempfile.TemporaryDirectory(prefix="daftar-export-") as temporary:
        work = Path(temporary)
        db_path = work / "daftar.db"
        _snapshot(store, db_path)
        _check_db(db_path)
        settings_path = home / "settings.json"
        files = _data_files(home / "files")
        manifest = _manifest(db_path, settings_path, files)
        temporary_archive = dest.parent / f".{dest.name}.{uuid.uuid4().hex}.tmp"
        try:
            with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as archive:
                archive.write(db_path, DB_PATH)
                if settings_path.is_file():
                    archive.write(settings_path, SETTINGS_PATH)
                for path in files:
                    archive.write(path, FILES_PREFIX + path.name, compress_type=zipfile.ZIP_STORED)
                archive.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
            os.replace(temporary_archive, dest)
        finally:
            temporary_archive.unlink(missing_ok=True)
        return manifest


def read_manifest(archive: Path) -> dict:
    archive = Path(archive)
    if not zipfile.is_zipfile(archive):
        raise LibraryError("That file is not a Daftar library archive.")
    try:
        with zipfile.ZipFile(archive) as z:
            manifest = json.loads(z.read(MANIFEST))
    except (KeyError, json.JSONDecodeError, OSError) as exc:
        raise LibraryError("The archive has no readable manifest.") from exc
    if not isinstance(manifest, dict):
        raise LibraryError("The archive manifest is not valid.")
    if manifest.get("app") != APP_NAME:
        raise LibraryError("That archive was not created by Daftar.")
    try:
        fmt = int(manifest.get("format", 0))
        schema = int(manifest.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise LibraryError("The archive manifest has invalid version numbers.") from exc
    if fmt < 1:
        raise LibraryError("The archive manifest has an invalid format.")
    if fmt > FORMAT_VERSION:
        raise LibraryError("That archive was created by a newer version of Daftar.")
    if schema > SCHEMA_VERSION:
        raise LibraryError("That archive uses a newer database; update Daftar first.")
    database = manifest.get("database")
    if not isinstance(database, dict) or database.get("path") != DB_PATH:
        raise LibraryError("The archive has no library database.")
    for key in ("size", "sha256"):
        if key not in database:
            raise LibraryError("The archive database entry is incomplete.")
    settings = manifest.get("settings")
    if settings is not None and (not isinstance(settings, dict) or settings.get("path") != SETTINGS_PATH):
        raise LibraryError("The archive settings entry is invalid.")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise LibraryError("The archive file list is invalid.")
    seen = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise LibraryError("The archive file list is invalid.")
        path = PurePosixPath(item["path"])
        if path.parent != PurePosixPath(FILES_PREFIX.rstrip("/")) or path.name in seen:
            raise LibraryError("The archive contains an unsafe file path.")
        if not path.name or "\\" in path.name or path.name in (".", ".."):
            raise LibraryError("The archive contains an unsafe file path.")
        seen.add(path.name)
    return manifest


def _check_db(db_path: Path) -> None:
    required = {"collections", "documents", "entries", "chunks", "chats", "messages"}
    try:
        with sqlite3.connect(str(db_path)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = required - tables
            if missing:
                raise LibraryError("The archive database is missing tables: " + ", ".join(sorted(missing)))
            check = db.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise LibraryError("The archive database did not pass its integrity check.")
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise LibraryError("The archive database has broken document links.")
    except sqlite3.Error as exc:
        raise LibraryError(f"The archive database cannot be read: {exc}") from exc


def _pull(archive: zipfile.ZipFile, names: set[str], member: dict, target: Path) -> None:
    name = member.get("path")
    if name not in names:
        raise LibraryError(f"The archive is missing {name}.")
    info = archive.getinfo(name)
    if info.file_size != member.get("size"):
        raise LibraryError(f"The archive size for {name} does not match its manifest.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info) as source, target.open("wb") as output:
        shutil.copyfileobj(source, output, 1 << 20)
    if _sha256(target) != member.get("sha256"):
        raise LibraryError(f"The archive checksum for {name} does not match.")


def _extract(archive: Path, dest: Path) -> tuple[dict, Path, Path | None, Path]:
    manifest = read_manifest(archive)
    db_path = dest / "library" / "daftar.db"
    settings_path = dest / "library" / "settings.json" if manifest.get("settings") else None
    files_dir = dest / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        _pull(z, names, manifest["database"], db_path)
        if settings_path is not None:
            _pull(z, names, manifest["settings"], settings_path)
        for item in manifest["files"]:
            _pull(z, names, item, files_dir / PurePosixPath(item["path"]).name)
    return manifest, db_path, settings_path, files_dir


def _migrated_copy(db_path: Path, dest: Path) -> Path:
    shutil.copy2(db_path, dest)
    try:
        store = Store(dest)
        try:
            version = store.schema_version()
        finally:
            store.close()
    except Exception as exc:
        raise LibraryError(f"The archive database cannot be opened: {exc}") from exc
    if version > SCHEMA_VERSION:
        raise LibraryError("That archive uses a newer database; update Daftar first.")
    for suffix in ("-wal", "-shm"):
        Path(str(dest) + suffix).unlink(missing_ok=True)
    return dest


@contextmanager
def _swap_files(files_dir: Path, staging: Path):
    files_dir.mkdir(parents=True, exist_ok=True)
    old = files_dir.with_name(f".files-old-{uuid.uuid4().hex}")
    os.rename(files_dir, old)
    try:
        os.rename(staging, files_dir)
    except Exception:
        os.rename(old, files_dir)
        raise
    try:
        yield
    except Exception:
        shutil.rmtree(files_dir, ignore_errors=True)
        os.rename(old, files_dir)
        raise
    else:
        shutil.rmtree(old, ignore_errors=True)


def _copy_database(store: Store, source: Path) -> None:
    alias = "incoming"
    with store.lock:
        try:
            store.db.execute(f"ATTACH DATABASE ? AS {alias}", (str(source),))
        except sqlite3.Error as exc:
            raise LibraryError(f"The archive database cannot be attached: {exc}") from exc
        try:
            with store.db:
                for table in ("messages", "chats", "chunks", "entries", "documents", "collections"):
                    store.db.execute(f"DELETE FROM {table}")
                store.db.execute("DELETE FROM chunks_fts")
                for table in ("collections", "documents", "entries", "chunks", "chats", "messages"):
                    columns = [row[1] for row in store.db.execute(f"PRAGMA table_info({table})")]
                    source_columns = {row[1] for row in store.db.execute(
                        f"PRAGMA {alias}.table_info({table})")}
                    missing = set(columns) - source_columns
                    if missing:
                        raise LibraryError(f"The archive is missing {table} columns: {', '.join(sorted(missing))}")
                    names = ", ".join(f'"{name}"' for name in columns)
                    store.db.execute(f"INSERT INTO {table}({names}) SELECT {names} FROM {alias}.{table}")
                if store.db.execute("PRAGMA foreign_key_check").fetchall():
                    raise LibraryError("The archive has broken document links.")
                chunks = store.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                indexed = store.db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
                if chunks != indexed:
                    raise LibraryError("The search index could not be rebuilt from the archive.")
        except sqlite3.Error as exc:
            raise LibraryError(f"The archive database could not be restored: {exc}") from exc
        finally:
            try:
                store.db.execute(f"DETACH DATABASE {alias}")
            except sqlite3.Error:
                pass


def _prune_files(files_dir: Path, store: Store) -> None:
    keep = {f"{row['id']}{row['ext']}" for row in store.all("SELECT id, ext FROM documents")}
    for path in files_dir.iterdir():
        if path.is_file() and not path.name.startswith(".") and path.name not in keep:
            path.unlink(missing_ok=True)


def backup_dir(home: Path, settings=None) -> Path:
    raw = str(settings["backup_dir"]).strip() if settings is not None else ""
    path = Path(raw).expanduser() if raw else Path(home) / "backups"
    if not path.is_absolute():
        path = Path(home) / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_backups(folder: Path) -> list[dict]:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for path in folder.glob("Daftar-*.daftar"):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        items.append({"name": path.name, "size": stat.st_size, "mtime": stat.st_mtime})
    return sorted(items, key=lambda item: item["mtime"], reverse=True)


def prune_backups(folder: Path) -> int:
    dated = []
    for path in Path(folder).glob("Daftar-*.daftar"):
        match = BACKUP_RE.match(path.name)
        if not match:
            continue
        try:
            created = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        dated.append((created, path))
    if not dated:
        return 0
    by_day, by_week = {}, {}
    for created, path in dated:
        by_day.setdefault(created.date(), []).append((created, path))
        by_week.setdefault(created.isocalendar()[:2], []).append((created, path))
    keep = set()
    for day in sorted(by_day)[-KEEP_DAYS:]:
        keep.add(max(by_day[day], key=lambda item: item[0])[1])
    for week in sorted(by_week)[-KEEP_WEEKS:]:
        keep.add(max(by_week[week], key=lambda item: item[0])[1])
    deleted = 0
    for _, path in dated:
        if path not in keep:
            try:
                path.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


def create_backup(home: Path, store: Store, settings=None) -> dict:
    folder = backup_dir(home, settings)
    base = f"Daftar-{_stamp()}.daftar"
    dest = folder / base
    counter = 2
    while dest.exists():
        dest = folder / f"{base[:-7]}-{counter}.daftar"
        counter += 1
    manifest = create_archive(home, store, dest)
    prune_backups(folder)
    return {"name": dest.name, "path": str(dest), "size": dest.stat().st_size, "manifest": manifest}


def ensure_daily_backup(home: Path, store: Store, settings=None) -> dict | None:
    if settings is not None and not settings["backup_auto"]:
        return None
    folder = backup_dir(home, settings)
    backups = list_backups(folder)
    if backups and time.time() - backups[0]["mtime"] < DAY:
        return None
    return create_backup(home, store, settings)


def restore_archive(home: Path, archive: Path, store: Store, settings=None) -> dict:
    home, archive = Path(home), Path(archive)
    with _lock, tempfile.TemporaryDirectory(prefix="daftar-restore-") as temporary:
        work = Path(temporary)
        _, db_path, _, extracted_files = _extract(archive, work)
        _check_db(db_path)
        migrated = _migrated_copy(db_path, work / "daftar-migrated.db")
        safety = backup_dir(home, settings) / f"Daftar-before-restore-{_stamp()}.daftar"
        counter = 2
        while safety.exists():
            safety = safety.with_name(f"Daftar-before-restore-{_stamp()}-{counter}.daftar")
            counter += 1
        create_archive(home, store, safety)
        with _swap_files(home / "files", extracted_files):
            _copy_database(store, migrated)
            try:
                _prune_files(home / "files", store)
            except OSError as exc:
                log.warning("Could not remove old library files: %s", exc)
        return {"restored": True, "safety_backup": safety.name, "counts": store.stats()}
