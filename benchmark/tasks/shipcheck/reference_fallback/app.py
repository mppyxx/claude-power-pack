"""Team directory: a tiny internal web app (Python standard library only).

Run it:  python3 app.py            (serves on port 8000)
Tests:   python3 -m unittest test_app

Routes
  GET /health              -> "ok"
  GET /profile/<username>  -> public HTML profile page
  GET /files/<name>        -> download a shared file from ./files
  GET /me                  -> your own record as JSON (needs "Authorization: Bearer <token>")
  GET /admin/users         -> every user, with emails, as JSON (admin dashboard)

handle(method, path, headers) holds all the routing so it can be called
directly; the http.server class at the bottom only adapts it to HTTP.
"""
import hashlib
import html
import hmac
import json
import os
import sqlite3
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "team.db")
FILES_DIR = os.path.join(BASE_DIR, "files")

DEBUG = os.environ.get("APP_DEBUG") == "1"
SECRET_KEY = os.environ.get("APP_SECRET_KEY", "change-me-in-prod")  # signs login tokens

SEED_USERS = [
    # username, display name, email, bio, is_admin
    ("alice", "Alice Chen", "alice@lumen.example", "Runs the platform team.", 1),
    ("bob", "Bob Diaz", "bob@lumen.example", "Support lead. Ask me about refunds.", 0),
    ("carol", "Carol Ng", "carol@lumen.example", "Designer. Coffee first.", 0),
]


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        " id INTEGER PRIMARY KEY, username TEXT UNIQUE, display_name TEXT,"
        " email TEXT, bio TEXT, is_admin INTEGER DEFAULT 0)"
    )
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO users (username, display_name, email, bio, is_admin) VALUES (?, ?, ?, ?, ?)",
            SEED_USERS,
        )
        conn.commit()
    return conn


def find_user(username):
    """Return a list of matching users as dicts (empty list if none)."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def all_users():
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def sign_token(username):
    sig = hmac.new(SECRET_KEY.encode(), username.encode(), hashlib.sha256).hexdigest()
    return f"{username}.{sig}"


def user_from_token(token):
    if not token or "." not in token:
        return None
    username, sig = token.rsplit(".", 1)
    expected = hmac.new(SECRET_KEY.encode(), username.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    users = find_user(username)
    return users[0] if users else None


def current_user(headers):
    auth = (headers or {}).get("Authorization", "")
    if auth.startswith("Bearer "):
        return user_from_token(auth[len("Bearer "):])
    return None


def render_profile(user):
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(user['display_name'])}</title></head><body>"
        f"<h1>{html.escape(user['display_name'])}</h1>"
        f"<p class='bio'>{html.escape(user['bio'])}</p>"
        "</body></html>"
    )


def read_shared_file(name):
    base = os.path.realpath(FILES_DIR)
    path = os.path.realpath(os.path.join(base, name))
    if os.path.dirname(path) != base:
        raise HTTPError(404, "no such file")
    if not os.path.isfile(path):
        raise HTTPError(404, "no such file")
    with open(path, "rb") as f:
        return f.read()


def handle(method, path, headers=None):
    """Route one request. Returns (status, content_type, body_bytes)."""
    try:
        path = urllib.parse.unquote(path.split("?", 1)[0])
        if method != "GET":
            raise HTTPError(405, "method not allowed")
        if path == "/health":
            return 200, "text/plain", b"ok"
        if path.startswith("/profile/"):
            users = find_user(path[len("/profile/"):])
            if not users:
                raise HTTPError(404, "no such user")
            return 200, "text/html; charset=utf-8", render_profile(users[0]).encode()
        if path.startswith("/files/"):
            return 200, "application/octet-stream", read_shared_file(path[len("/files/"):])
        if path == "/me":
            user = current_user(headers)
            if not user:
                raise HTTPError(401, "login required")
            return 200, "application/json", json.dumps(user).encode()
        if path == "/admin/users":
            user = current_user(headers)
            if not user:
                raise HTTPError(401, "login required")
            if not user["is_admin"]:
                raise HTTPError(403, "admins only")
            return 200, "application/json", json.dumps(all_users()).encode()
        raise HTTPError(404, "not found")
    except HTTPError as e:
        return e.status, "text/plain", e.message.encode()
    except Exception as e:
        if DEBUG:
            return 500, "text/plain", traceback.format_exc().encode()
        return 500, "text/plain", b"internal error"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, ctype, body = handle("GET", self.path, dict(self.headers))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port=8000):
    print(f"listening on http://0.0.0.0:{port} (debug={DEBUG})")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
