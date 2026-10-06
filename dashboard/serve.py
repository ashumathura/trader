#!/usr/bin/env python3
"""Runs the dashboard on your own computer.

Yahoo rate-limits GitHub's servers, so the public data is easier to fetch from here. This script
  1. builds docs/data/*.json with the same pipeline GitHub runs (public market data only),
  2. refreshes it every 20 minutes,
  3. serves the dashboard and that data on http://localhost:8765 (this computer only).
Your holdings are never read here: pick your CSV (or paste the sheet link) in the dashboard itself.

Usage:  python3 dashboard/serve.py [--port 8765] [--no-open]
"""
import argparse, http.server, os, subprocess, sys, threading, time, webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED = ("/dashboard/index.html", "/docs/")  # nothing else (e.g. dashboard/local/) is served

def build():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "pipeline", "build_data.py")], cwd=ROOT, capture_output=True, text=True)
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    print(time.strftime("%H:%M:%S"), "data refresh", "ok" if r.returncode == 0 else "FAILED", "|", " / ".join(tail))

def refresher():
    while True:
        time.sleep(20 * 60)
        build()

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=ROOT, **k)
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/": self.send_response(302); self.send_header("Location", "/dashboard/index.html"); self.end_headers(); return
        if ".." in path or not path.startswith(ALLOWED): self.send_error(404); return
        super().do_GET()
    def end_headers(self): self.send_header("Cache-Control", "no-store"); super().end_headers()
    def log_message(self, *a): pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8765); ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    build()
    threading.Thread(target=refresher, daemon=True).start()
    url = "http://localhost:%d/" % a.port
    print("Dashboard:", url, "(Ctrl+C to stop)")
    if not a.no_open: webbrowser.open(url)
    http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
