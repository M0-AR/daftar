"""Desktop launcher: local server on a loopback port, shown in a native window (or the browser)."""
import argparse
import logging
import signal
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from daftar import APP_NAME
from daftar.activation import (InstanceLock, browser_requested, clear_running_url, is_activation_url,
                               open_running_instance, register_protocol, write_running_url)
from daftar.config import data_dir
from daftar.library import ensure_daily_backup
from daftar.server import create_app

log = logging.getLogger(__name__)


def bind_socket(port: int) -> tuple[socket.socket, int]:
    """Take the requested loopback port, or the first free one when it is busy."""
    def bind(value: int) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", value))
            sock.listen(128)
        except OSError:
            sock.close()
            raise
        return sock

    try:
        sock = bind(port)
    except OSError:
        if port == 0:
            raise
        log.warning("Port %s is already in use; using a free port", port)
        sock = bind(0)
    return sock, sock.getsockname()[1]


def backup_loop(home, application):
    """Make at most one automatic backup a day while Daftar runs."""
    while True:
        try:
            item = ensure_daily_backup(home, application.state.store, application.state.settings)
            if item:
                log.info("automatic backup created: %s", item["name"])
        except Exception:
            log.exception("automatic backup failed")
        time.sleep(3600)


def self_test() -> int:
    """Exercise the parts that break when packaging goes wrong: native PDF rendering (pdfium), PDF/DOCX
    reading, PDF export with a Unicode font, the SQLite keyword index. Prints one line per check."""
    import tempfile
    from pathlib import Path

    failures = 0

    def check(name, fn):
        nonlocal failures
        try:
            detail = fn()
            print(f"ok   {name}{': ' + detail if detail else ''}")
        except Exception as e:  # report every failure, keep going
            failures += 1
            print(f"FAIL {name}: {e!r}")

    tmp = Path(tempfile.mkdtemp())
    from daftar.exporter import export
    pdf_bytes, _, _ = export([(1, "Invoices are payable within 45 days. عقد المرابحة")], "check.txt", "pdf")
    (tmp / "check.pdf").write_bytes(pdf_bytes)

    def render():
        from daftar.ingest import page_image_png
        png = page_image_png(tmp / "check.pdf", 1)
        assert png.startswith(b"\x89PNG")
        return f"{len(png)} bytes"

    def read_pdf():
        from daftar.ingest import extract
        text = extract(tmp / "check.pdf")[0][1]
        assert "45 days" in text, text
        return "text layer found"

    def read_docx():
        import docx
        from daftar.ingest import extract
        d = docx.Document()
        d.add_heading("Title", 1)
        d.add_paragraph("Hello")
        d.save(str(tmp / "check.docx"))
        assert "# Title" in extract(tmp / "check.docx")[0][1]

    def keyword_index():
        from daftar.store import Store
        store = Store(tmp / "check.db")
        store.run("INSERT INTO collections(name, created) VALUES ('c', 0)")
        store.run("INSERT INTO documents(name, ext, size, sha256, created) VALUES ('d', '.txt', 1, 'x', 0)")
        store.run("INSERT INTO entries(document_id, collection_id, created) VALUES (1, 1, 0)")
        store.replace_chunks(1, [(1, "كَتَبَ الدَّرْسَ", None)])
        assert store.all("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '\"كتب\"'")

    check("pdf export (unicode font)", lambda: f"{len(pdf_bytes)} bytes")
    check("pdf page rendering (pdfium)", render)
    check("pdf text extraction", read_pdf)
    check("word documents", read_docx)
    check("keyword index (sqlite fts5, arabic)", keyword_index)
    check("static ui", lambda: str(len(list((Path(__file__).parent / "static").iterdir()))) + " files")
    print("SELF-TEST", "PASSED" if not failures else f"FAILED ({failures})")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(prog="daftar", description="Private document chat on your own computer.")
    parser.add_argument("--port", type=int, default=8765,
                        help="loopback port (default: 8765; falls back to a free port if busy)")
    parser.add_argument("--browser", action="store_true", help="open in the web browser instead of a window")
    parser.add_argument("--headless", action="store_true", help="only run the local server")
    parser.add_argument("--self-test", action="store_true", help="check bundled libraries and exit (for builds)")
    parser.add_argument("--register-protocol", action="store_true",
                        help="refresh the daftar:// link handler for this user")
    parser.add_argument("activation", nargs="?", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        raise SystemExit(self_test())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.activation and not is_activation_url(args.activation):
        parser.error(f"unexpected argument: {args.activation}")
    if browser_requested(args.activation):
        args.browser = True

    if args.register_protocol or not args.headless:
        try:
            register_protocol(force=args.register_protocol)
        except Exception:
            log.exception("Could not register the daftar:// link handler")

    home = data_dir()
    lock = InstanceLock(home / "daftar.lock")
    if not lock.acquire():
        if open_running_instance():
            return
        print(f"{APP_NAME} is already running, but its address is unavailable.", file=sys.stderr)
        return
    try:
        application = create_app(home)
        sock, port = bind_socket(args.port)
        url = f"http://127.0.0.1:{port}/"
        server = uvicorn.Server(uvicorn.Config(application, log_level="warning", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive():
                print(f"{APP_NAME} could not start.", file=sys.stderr)
                return
            if time.monotonic() > deadline:
                server.should_exit = True
                print(f"{APP_NAME} did not start in time.", file=sys.stderr)
                return
            time.sleep(0.05)
        try:
            write_running_url(url)
        except OSError as exc:
            log.warning("Could not publish the running address: %s", exc)
        print(f"{APP_NAME} running at {url}", flush=True)
        threading.Thread(target=backup_loop, args=(home, application), daemon=True).start()

        if not args.headless and not args.browser:
            try:
                import webview
            except ImportError:
                print("pywebview not installed; opening the browser instead.")
            else:
                webview.settings["ALLOW_DOWNLOADS"] = True  # document exports
                webview.create_window(APP_NAME, url, width=1320, height=860, min_size=(900, 600),
                                      background_color="#101418")
                webview.start()
                server.should_exit = True
                return

        def stop(*_):
            server.should_exit = True

        try:
            signal.signal(signal.SIGTERM, stop)
        except (ValueError, OSError):
            pass
        if not args.headless:
            webbrowser.open(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            server.should_exit = True
    finally:
        clear_running_url()
        lock.release()


if __name__ == "__main__":
    main()
