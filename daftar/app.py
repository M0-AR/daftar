"""Desktop launcher: local server on a free loopback port, shown in a native window (or the browser)."""
import argparse
import logging
import socket
import threading
import time
import webbrowser

import uvicorn

from daftar.server import create_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(prog="daftar", description="Private AI document intelligence.")
    parser.add_argument("--port", type=int, default=0, help="loopback port (default: any free port)")
    parser.add_argument("--browser", action="store_true", help="open in the web browser instead of a window")
    parser.add_argument("--headless", action="store_true", help="only run the local server")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}/"
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port,
                                           log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    print(f"Daftar running at {url}", flush=True)

    if not args.headless and not args.browser:
        try:
            import webview
        except ImportError:
            print("pywebview not installed; opening the browser instead.")
        else:
            webview.create_window("Daftar", url, width=1320, height=860, min_size=(900, 600),
                                  background_color="#14161f")
            webview.start()
            server.should_exit = True
            return
    if not args.headless:
        webbrowser.open(url)
    try:
        thread.join()
    except KeyboardInterrupt:
        server.should_exit = True


if __name__ == "__main__":
    main()
