"""Personal sign-ins and access levels.

Roles (each includes the one before):
  viewer  - sees everything, runs/prints/emails/exports reports and AI write-ups; changes nothing.
  editor  - also manages practices, uploads, linked accounts, fixes and map content.
  admin   - also the Connections page (keys, tokens), map setup (keys) and the Team page.

The original shared team password (ADS_PASSWORD) still signs in as an admin, so nobody is
locked out; admins then create personal sign-ins on the Team page. There's no email service,
so a new person gets a one-time setup link that the admin sends them.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta

from . import db

ROLES = ("viewer", "editor", "admin")
ROLE_LABELS = {"viewer": "Viewer", "editor": "Editor", "admin": "Admin"}
ROLE_HELP = {
    "viewer": "Sees everything and can run, print, email and export reports. Can't add, change or delete anything.",
    "editor": "Also manages practices, uploads, linked accounts, fixes and the map's offices and lists.",
    "admin": "Also the Connections page (keys and tokens), map keys, and who has access.",
}
LINK_DAYS = 7
ITERATIONS = 240_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL DEFAULT 'viewer',
  pw_hash TEXT DEFAULT '',
  setup_token TEXT DEFAULT '',
  setup_expires TEXT DEFAULT '',
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_login TEXT DEFAULT ''
);
"""


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


def rank(role):
    return ROLES.index(role) if role in ROLES else -1


def at_least(role, needed):
    return rank(role) >= rank(needed)


# ---- passwords ----
def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def check_password(pw, stored):
    try:
        algo, its, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        test = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(its))
        return hmac.compare_digest(test.hex(), dk)
    except (ValueError, AttributeError):
        return False


def password_problem(pw):
    if len(pw) < 10:
        return "Use at least 10 characters."
    if pw.lower() == pw and pw.isalpha():
        return "Mix in a number, symbol or capital letter."
    return None


# ---- users ----
def _clean_email(email):
    return (email or "").strip().lower()


def valid_email(email):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", _clean_email(email)))


def users():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY disabled, name COLLATE NOCASE")]


def user(uid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None


def by_email(email):
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (_clean_email(email),)).fetchone()
        return dict(r) if r else None


def _new_link():
    return secrets.token_urlsafe(24), (datetime.now() + timedelta(days=LINK_DAYS)).isoformat(timespec="seconds")


def add_user(name, email, role):
    """Returns (user_id, setup_token)."""
    token, expires = _new_link()
    with db.conn() as c:
        cur = c.execute("INSERT INTO users(name, email, role, setup_token, setup_expires, created_at) "
                        "VALUES (?,?,?,?,?,?)", (name.strip()[:80], _clean_email(email),
                                                 role if role in ROLES else "viewer", token, expires, db.now()))
        return cur.lastrowid, token


def new_setup_link(uid):
    token, expires = _new_link()
    with db.conn() as c:
        c.execute("UPDATE users SET setup_token=?, setup_expires=? WHERE id=?", (token, expires, uid))
    return token


def by_setup_token(token):
    if not token:
        return None
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE setup_token=? AND disabled=0", (token,)).fetchone()
    if not r:
        return None
    u = dict(r)
    try:
        if datetime.fromisoformat(u["setup_expires"]) < datetime.now():
            return None
    except ValueError:
        return None
    return u


def set_password(uid, pw):
    with db.conn() as c:
        c.execute("UPDATE users SET pw_hash=?, setup_token='', setup_expires='' WHERE id=?", (hash_password(pw), uid))


def set_role(uid, role):
    if role in ROLES:
        with db.conn() as c:
            c.execute("UPDATE users SET role=? WHERE id=?", (role, uid))


def set_disabled(uid, disabled):
    with db.conn() as c:
        c.execute("UPDATE users SET disabled=?, setup_token=CASE WHEN ? THEN '' ELSE setup_token END WHERE id=?",
                  (1 if disabled else 0, 1 if disabled else 0, uid))


def delete_user(uid):
    with db.conn() as c:
        c.execute("DELETE FROM users WHERE id=?", (uid,))


def admins_left(excluding=None):
    return sum(1 for u in users() if u["role"] == "admin" and not u["disabled"] and u["id"] != excluding
               and u["pw_hash"])


def login(email, password, team_password):
    """Returns a session dict or None. Blank email + the team password = admin (the original login)."""
    email = _clean_email(email)
    if not email:
        if team_password and hmac.compare_digest(password.encode(), team_password.encode()):
            return {"ok": True, "uid": 0, "role": "admin", "name": "Team password"}
        return None
    u = by_email(email)
    if not u or u["disabled"] or not u["pw_hash"] or not check_password(password, u["pw_hash"]):
        return None
    with db.conn() as c:
        c.execute("UPDATE users SET last_login=? WHERE id=?", (db.now(), u["id"]))
    return {"ok": True, "uid": u["id"], "role": u["role"], "name": u["name"]}


def current(session):
    """The signed-in person from the session, re-checked against the database each request so a
    role change or disabled account takes effect immediately. None if not signed in."""
    if session.get("ok") is not True:
        return None
    uid = session.get("uid")
    if not uid:                      # team password, or a session from before personal sign-ins
        return {"uid": 0, "role": "admin", "name": session.get("name") or "Team password"}
    u = user(uid)
    if not u or u["disabled"]:
        return None
    return {"uid": u["id"], "role": u["role"], "name": u["name"], "email": u["email"]}


# ---- what each role may do ----
ADMIN_ONLY = re.compile(r"^/(connections|google/|team|map/setup)")
VIEWER_POSTS = re.compile(r"^/(logout|p/\d+/ai/(fixes|creative|report))$")


def needed_role(method, path):
    """Minimum role for a request; None = public."""
    if path in ("/login", "/health") or path.startswith(("/static/", "/setup/")):
        return None
    if ADMIN_ONLY.match(path):
        return "admin"
    if method in ("GET", "HEAD", "OPTIONS"):
        return "viewer"
    if VIEWER_POSTS.match(path):
        return "viewer"          # running a write-up / report is allowed for everyone
    return "editor"
