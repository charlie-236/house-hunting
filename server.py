#!/usr/bin/env python3
"""
Tiny static file server + status API for the Walthamstow house-hunt tracker.

Replaces the old `pm2 serve . 8080` (plain static files, no way to save
anything) so that favorite / followed-up / rejected clicks made from any
device or browser hitting this server land in one shared file --
status.json -- instead of each browser's own localStorage. That's what
makes the status survive being viewed from your phone, laptop, whatever:
they're all talking to the same server, which is the one writing the file.

Usage:
    python3 server.py [port]      # default port 8090
    PORT=9001 python3 server.py   # or set the PORT env var

NOTE ON THE PORT: this used to default to 8080, which is also Open WebUI's
default port. Open WebUI is pip-installed on this machine and is a SvelteKit
app, so when house-hunt held 8080 first, Open WebUI couldn't bind and any
browser tab still pointing at localhost:8080 got this static server instead --
which 404s its entire app shell (/_app/..., /static/splash.png, /api/config)
and sends it into an /error retry loop. Keep this off 8080.

Everything else (index.html, archive.json, build_page.py, README.md,
status.json) is served as a plain static file exactly like `pm2 serve`
did. The only new behaviour is two JSON endpoints:

    GET  /api/status            -> the whole status.json contents
    POST /api/status/<id>       -> body {"favorite": bool, "followedUp": bool,
                                          "rejected": bool} (any subset),
                                    merged into that listing's record

No third-party dependencies -- stdlib only, so there's nothing extra to
install on top of what running build_page.py already needs.
"""
import json
import os
import sys
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).parent
STATUS_PATH = ROOT / "status.json"


def load_status():
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_status(data):
    # Write to a temp file then atomically rename over the real one, so a
    # crash or two near-simultaneous requests can't leave status.json
    # half-written/corrupted.
    fd, tmp_path = tempfile.mkstemp(dir=ROOT, prefix=".status-", suffix=".tmp")
    try:
        with open(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        Path(tmp_path).replace(STATUS_PATH)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


ALLOWED_KEYS = {"favorite", "followedUp", "rejected"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        # Keep pm2's log output readable -- default logging is fine, just
        # route it the same way SimpleHTTPRequestHandler already does.
        super().log_message(fmt, *args)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/status":
            body = json.dumps(load_status()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/status/"):
            listing_id = unquote(path[len("/api/status/"):])
            if not listing_id:
                self._json_response(400, {"error": "missing listing id"})
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._json_response(400, {"error": "invalid JSON body"})
                return
            if not isinstance(payload, dict):
                self._json_response(400, {"error": "body must be a JSON object"})
                return

            data = load_status()
            record = data.get(listing_id, {})
            for key in ALLOWED_KEYS:
                if key in payload:
                    record[key] = bool(payload[key])
            data[listing_id] = record
            save_status(data)
            self._json_response(200, {listing_id: record})
            return
        self._json_response(404, {"error": "not found"})

    def _json_response(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


DEFAULT_PORT = 8090  # NOT 8080 -- that is Open WebUI's default. See module docstring.


def main():
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = int(os.environ.get("PORT", DEFAULT_PORT))
    if not STATUS_PATH.exists():
        save_status({})
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Serving {ROOT} on http://0.0.0.0:{port}/  (status stored in {STATUS_PATH.name})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
