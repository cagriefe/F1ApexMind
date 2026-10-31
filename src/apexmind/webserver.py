# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ApexMind contributors
"""Tiny local web server (standard library only).

It serves the web UI, a live Server-Sent-Events feed of what the coach is doing, and the Python
package itself as a zip so the UI can analyse recordings in the browser (through Pyodide).
"""

from __future__ import annotations

import collections
import io
import json
import logging
import mimetypes
import queue
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__

_LOG = logging.getLogger("apexmind")

PACKAGE_DIR = Path(__file__).resolve().parent
WEBUI_DIR = PACKAGE_DIR / "webui"


def package_zip_bytes() -> bytes:
    """The ``apexmind`` Python package (sources only) as a zip, for in-browser analysis."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(PACKAGE_DIR.rglob("*.py")):
            if "webui" in path.parts or "__pycache__" in path.parts:
                continue
            z.write(path, Path("apexmind") / path.relative_to(PACKAGE_DIR))
    return buf.getvalue()


class LiveHub:
    """Fan-out of live coach state and spoken advice to any number of browser clients."""

    def __init__(self, history: int = 60) -> None:
        self._lock = threading.Lock()
        self._subs: set[queue.Queue[bytes]] = set()
        self.state: dict = {}
        self.meta: dict = {"version": __version__}
        self.history: collections.deque[dict] = collections.deque(maxlen=history)

    def snapshot(self) -> dict:
        return {"live": True, "meta": self.meta, "state": self.state, "events": list(self.history)}

    def publish_state(self, state: dict) -> None:
        self.state = state
        self._fanout("state", state)

    def publish_event(self, event: dict) -> None:
        self.history.append(event)
        self._fanout("advice", event)

    def subscribe(self) -> queue.Queue[bytes]:
        q: queue.Queue[bytes] = queue.Queue(maxsize=300)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[bytes]) -> None:
        with self._lock:
            self._subs.discard(q)

    def _fanout(self, name: str, data: dict) -> None:
        payload = f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass  # a slow client simply misses frames


def _make_handler(hub: LiveHub):
    zip_cache: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ApexMind"

        def log_message(self, *_args) -> None:  # keep the terminal clean
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/events":
                return self._events()
            if path == "/api/live":
                return self._send(200, json.dumps(hub.snapshot()).encode(), "application/json")
            if path == "/apexmind.zip":
                if not zip_cache:
                    zip_cache.append(package_zip_bytes())
                return self._send(200, zip_cache[0], "application/zip")
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (WEBUI_DIR / rel).resolve()
            if WEBUI_DIR.resolve() not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = hub.subscribe()
            try:
                snap = json.dumps(hub.snapshot(), separators=(",", ":"))
                self.wfile.write(f"event: snapshot\ndata: {snap}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        self.wfile.write(q.get(timeout=15))
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionError, OSError):
                pass
            finally:
                hub.unsubscribe(q)

    return Handler


class _QuietServer(ThreadingHTTPServer):
    """A browser closing a tab or dropping a connection is normal: no traceback in the terminal."""

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError, BrokenPipeError)):
            _LOG.debug("web client %s went away: %s", client_address, exc)
            return
        _LOG.warning("web request from %s failed", client_address, exc_info=True)


def start_server(hub: LiveHub, host: str, port: int) -> ThreadingHTTPServer:
    """Start serving in a background thread. Raises OSError if the port is busy."""
    server = _QuietServer((host, port), _make_handler(hub))
    threading.Thread(target=server.serve_forever, name="apexmind-web", daemon=True).start()
    return server
