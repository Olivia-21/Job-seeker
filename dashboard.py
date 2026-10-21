"""
Local progress dashboard. Reads the CSVs live, so it always shows the latest state.
  python dashboard.py            -> opens http://127.0.0.1:8765 in your browser
  python dashboard.py --port 9000 --no-browser
Only listens on your own machine (127.0.0.1); nothing is exposed to the network.
"""
import argparse
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from common import (
    BASE_DIR, LETTERS_DIR, STATUSES, load_profile, load_queue, save_queue, load_log, load_replies,
    letter_filename, now_str,
)

HTML_FILE = os.path.join(BASE_DIR, "web", "index.html")
_lock = threading.Lock()


def payload():
    profile = load_profile()
    queue = load_queue()
    for r in queue:
        fn = letter_filename(profile, r["company_name"])
        r["letter"] = fn if os.path.exists(os.path.join(LETTERS_DIR, fn)) else ""
    return {
        "generated_at": now_str(),
        "mode": "local",
        "capabilities": {"letters": True, "runSearch": False, "actions": False, "cv": True},
        "bot_run": None,
        "profile": {
            "name": profile["name"], "email": profile["email"], "headline": profile["headline"],
            "cv_file": profile.get("cv_file", ""),
            "limits": profile.get("limits", {}),
            "tracks": {k: {"label": t["label"], "priority": t.get("priority", ""), "role_title": t.get("role_title", "")}
                       for k, t in profile["tracks"].items()},
        },
        "statuses": STATUSES,
        "queue": queue,
        "log": load_log(),
        "replies": load_replies(),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(HTML_FILE, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/session":
            return self._send(200, json.dumps({"authed": True, "mode": "local", "missing": []}))
        if path == "/api/data":
            with _lock:
                return self._send(200, json.dumps(payload()))
        if path == "/api/file":
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("kind", [""])[0] == "cv":
                full = os.path.join(BASE_DIR, load_profile().get("cv_file", ""))
                name = os.path.basename(full)
            else:
                name = os.path.basename(qs.get("name", [""])[0])
                full = os.path.join(LETTERS_DIR, name)
            if name.lower().endswith(".pdf") and os.path.isfile(full):
                with open(full, "rb") as f:
                    return self._send(200, f.read(), "application/pdf")
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if urlparse(self.path).path == "/api/run":
            return self._send(501, json.dumps({"error": "Bot actions run from the online dashboard. Locally use the python commands."}))
        if urlparse(self.path).path != "/api/update":
            return self._send(404, json.dumps({"error": "not found"}))
        # Only accept requests from the dashboard page itself
        origin = self.headers.get("Origin", "")
        if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
            return self._send(403, json.dumps({"error": "forbidden"}))
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, json.dumps({"error": "bad json"}))

        names = body.get("companies") or ([body["company_name"]] if body.get("company_name") else [])
        changes = {k: body[k] for k in ("status", "track", "notes", "email", "recipient_name") if k in body}
        if "status" in changes and changes["status"] not in STATUSES:
            return self._send(400, json.dumps({"error": "bad status"}))
        profile = load_profile()
        if "track" in changes and changes["track"] not in profile["tracks"]:
            return self._send(400, json.dumps({"error": "bad track"}))

        with _lock:
            queue = load_queue()
            wanted = {n.lower() for n in names}
            hit = 0
            for r in queue:
                if r["company_name"].lower() in wanted:
                    r.update({k: str(v) for k, v in changes.items()})
                    if "track" in changes:
                        r["role_title"] = profile["tracks"][changes["track"]]["role_title"]
                    hit += 1
            if hit:
                save_queue(queue)
        return self._send(200, json.dumps({"updated": hit}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}"
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
