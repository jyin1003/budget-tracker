"""
dashboard/server.py — HTTP server for the budget analytics dashboard.

Serves static files from dashboard/static/ and JSON from /api/*.

Usage:
    python dashboard/server.py             # http://localhost:8766
    python dashboard/server.py --port 9000
"""

import argparse
import json
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import DB_PATH
from dashboard.data import (
    load_config, fetch_dashboard_data, available_months,
    fetch_transactions_for_category,
)

STATIC_DIR = _HERE / "static"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css",
    ".js":   "application/javascript",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path):
        mime = MIME_TYPES.get(path.suffix, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path   = parsed.path

        try:
            # ── API ──────────────────────────────────────────────
            if path == "/api/months":
                self.send_json({"months": available_months()})

            elif path == "/api/dashboard":
                ym = params.get("month", [None])[0] or ""
                if not ym:
                    now = datetime.now()
                    ym  = f"{now.year}-{now.month:02d}"
                    months = available_months()
                    if months and ym not in months:
                        ym = months[0]
                cfg  = load_config()
                data = fetch_dashboard_data(ym, cfg)
                self.send_json(data)

            elif path == "/api/category_transactions":
                ym       = params.get("month",    [None])[0] or ""
                category = params.get("category", [None])[0] or ""
                if not ym or not category:
                    self.send_json({"error": "month and category required"}, 400)
                    return
                rows = fetch_transactions_for_category(ym, category)
                self.send_json({"month": ym, "category": category, "transactions": rows})

            # ── Static files ─────────────────────────────────────
            elif path == "/" or path == "":
                self.send_file(STATIC_DIR / "index.html")

            elif path.startswith("/static/"):
                file_path = STATIC_DIR / path[len("/static/"):]
                if file_path.exists() and file_path.is_file():
                    self.send_file(file_path)
                else:
                    self.send_json({"error": "not found"}, 404)

            else:
                self.send_json({"error": "not found"}, 404)

        except Exception as e:
            import traceback
            self.send_json({"error": str(e), "trace": traceback.format_exc()}, 500)


def main():
    parser = argparse.ArgumentParser(description="Budget analytics dashboard")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[!] DB not found at {DB_PATH}. Run ingest.py first.")
        sys.exit(1)

    url = f"http://localhost:{args.port}"
    print(f"[budget-dash] Serving at {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        webbrowser.open(url)

    server = HTTPServer(("", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[budget-dash] Stopped.")


if __name__ == "__main__":
    main()