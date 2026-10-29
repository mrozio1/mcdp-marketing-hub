"""People who can sign in, and what each role may do.

Roles: viewer (look and export), editor (also uploads, fixes, map refresh, offices, lists),
admin (also keys and connections, people, deleting practices, map setup).

New people get a one-time setup link (no email needed: the admin copies it to them) and pick
their own password, so nobody else ever knows it. Lost admin access:
    docker exec postiz-ads python -m app.users reset you@example.com
prints a fresh setup link.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sys
from datetime import datetime, timedelta

from . import db

ROLES = ("viewer", "editor", "admin")
ROLE_LABELS = {"viewer": "Viewer", "editor": "Editor", "admin": "Admin"}
ROLE_HELP = {"viewer": "Sees everything and exports; can't change anything.",
             "editor": "Also uploads, fixes, map refresh, offices and list stars/notes.",
             "admin": "Also keys and connections, people, map setup and deleting practices."}
LINK_DAYS = 7
MIN_PASSWORD = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'viewer',
  pw_hash TEXT DEFAULT '',
  setup_hash TEXT DEFAULT '',
  setup_expires TEXT DEFAULT '',
  gen INTEGER NOT NULL DEFAULT 1,
  active INTEGER NOT NULL DEFAULT 1,
  ever_set INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_login TEXT DEFAULT ''
);
"""


# Columns this version needs. If an earlier build already created the users table (with
# "disabled", a plain "setup_token" and PBKDF2 passwords), it's brought up to date in place,
# so anyone set up there keeps their sign-in.
COLUMNS = {"setup_hash": "TEXT DEFAULT ''", "setup_expires": "TEXT DEFAULT ''", "gen": "INTEGER NOT NULL DEFAULT 1",
           "active": "INTEGER NOT NULL DEFAULT 1", "ever_set": "INTEGER NOT NULL DEFAULT 0",
           "last_login": "TEXT DEFAULT ''", "field_budget": "REAL DEFAULT 0", "home_address": "TEXT DEFAULT ''",
           "home_lat": "REAL", "home_lng": "REAL",
           "field": "INTEGER NOT NULL DEFAULT 0",
           "social": "INTEGER NOT NULL DEFAULT 1",
           "seasonal": "INTEGER NOT NULL DEFAULT 1"}   # Social link shown in the hub (on for existing people)   # field visits switched on for this person (any access level)


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        added = [col for col in COLUMNS if col not in have]
        for col in added:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {COLUMNS[col]}")
        if added:
            if "disabled" in have and "active" in added:
                c.execute("UPDATE users SET active = CASE WHEN disabled THEN 0 ELSE 1 END")
            if "ever_set" in added:
                c.execute("UPDATE users SET ever_set=1 WHERE pw_hash!=''")
            if "setup_token" in have and "setup_hash" in added:
                for r in c.execute("SELECT id, setup_token FROM users WHERE setup_token!=''").fetchall():
                    c.execute("UPDATE users SET setup_hash=?, setup_token='' WHERE id=?",
                              (_token_hash(r["setup_token"]), r["id"]))
        # Anyone given the short-lived "Field agent" level becomes a viewer with field visits on.
        c.execute("UPDATE users SET role='viewer', field=1 WHERE role='agent'")


SWITCHES = {"field": "Field visits", "social": "Social", "seasonal": "Seasonal decorations"}


def set_switch(uid, name, on):
    if name not in SWITCHES:
        raise ValueError("Unknown switch.")
    with db.conn() as c:
        c.execute(f"UPDATE users SET {name}=? WHERE id=?", (1 if on else 0, uid))


def set_field(uid, on):
    set_switch(uid, "field", on)


def rank(role):
    return ROLES.index(role) if role in ROLES else -1


# ---- passwords and links ----
def _hash_pw(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def _check_pw(pw, stored):
    parts = (stored or "").split("$")
    try:
        if parts[0] == "scrypt" and len(parts) == 3:
            return hmac.compare_digest(_hash_pw(pw, bytes.fromhex(parts[1])).split("$")[2], parts[2])
        if parts[0] == "pbkdf2_sha256" and len(parts) == 4:   # passwords set with the earlier build
            dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(parts[2]), int(parts[1]))
            return hmac.compare_digest(dk.hex(), parts[3])
    except ValueError:
        pass
    return False


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_problem(pw, confirm=None):
    if len(pw) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if confirm is not None and pw != confirm:
        return "The two passwords don't match."
    return None


# ---- queries ----
def _row(r):
    return dict(r) if r else None


def all_users():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY active DESC, lower(name)")]


def get(uid):
    with db.conn() as c:
        return _row(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


def by_email(email):
    with db.conn() as c:
        return _row(c.execute("SELECT * FROM users WHERE lower(email)=lower(?)", ((email or "").strip(),)).fetchone())


def count_admins(exclude=None):
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND ever_set=1 AND id!=?",
                         (exclude or 0,)).fetchone()[0]


def any_admin():
    """True once someone has finished setting up an admin account (the shared password then stops working)."""
    return count_admins() > 0


# ---- changes ----
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def add(name, email, role, field=False, social=True):
    """Returns (user, setup_token) or raises ValueError with a message for the page."""
    name, email = (name or "").strip(), (email or "").strip().lower()
    if not name:
        raise ValueError("Add the person's name.")
    if not EMAIL.match(email):
        raise ValueError("That email doesn't look right.")
    if role not in ROLES:
        raise ValueError("Pick a role.")
    if by_email(email):
        raise ValueError(f"{email} already has an account.")
    with db.conn() as c:
        cur = c.execute("INSERT INTO users(email, name, role, created_at, field, social) VALUES (?,?,?,?,?,?)",
                        (email, name[:80], role, db.now(), 1 if field else 0, 1 if social else 0))
        uid = cur.lastrowid
    return get(uid), new_link(uid)


def new_link(uid):
    """A fresh one-time setup link token. Clears the current password (so a reset really resets)
    and signs the person out everywhere."""
    token = secrets.token_urlsafe(24)
    exp = (datetime.now() + timedelta(days=LINK_DAYS)).isoformat(timespec="seconds")
    with db.conn() as c:
        c.execute("UPDATE users SET setup_hash=?, setup_expires=?, pw_hash='', gen=gen+1 WHERE id=?",
                  (_token_hash(token), exp, uid))
    return token


def by_token(token):
    if not token:
        return None
    with db.conn() as c:
        u = _row(c.execute("SELECT * FROM users WHERE setup_hash=? AND active=1", (_token_hash(token),)).fetchone())
    if not u or not u["setup_expires"] or u["setup_expires"] < db.now():
        return None
    return u


def finish_setup(token, pw):
    u = by_token(token)
    if not u:
        return None
    with db.conn() as c:
        c.execute("UPDATE users SET pw_hash=?, setup_hash='', setup_expires='', ever_set=1, gen=gen+1 WHERE id=?",
                  (_hash_pw(pw), u["id"]))
    return get(u["id"])


def set_password(uid, pw):
    with db.conn() as c:
        c.execute("UPDATE users SET pw_hash=?, gen=gen+1 WHERE id=?", (_hash_pw(pw), uid))
    return get(uid)


def check_login(email, pw):
    u = by_email(email)
    if not u or not u["active"] or not u["pw_hash"] or not _check_pw(pw, u["pw_hash"]):
        return None
    with db.conn() as c:
        c.execute("UPDATE users SET last_login=? WHERE id=?", (db.now(), u["id"]))
    return u


def _last_admin_guard(u, new_role=None, deactivate=False):
    losing = u["role"] == "admin" and (deactivate or (new_role and new_role != "admin"))
    if losing and u["ever_set"] and count_admins(exclude=u["id"]) == 0:
        raise ValueError("Keep at least one admin: make someone else an admin first.")


def set_role(uid, role):
    u = get(uid)
    if not u or role not in ROLES:
        raise ValueError("Pick a role.")
    _last_admin_guard(u, new_role=role)
    with db.conn() as c:
        c.execute("UPDATE users SET role=? WHERE id=?", (role, uid))


def set_active(uid, active):
    u = get(uid)
    if not u:
        raise ValueError("No such person.")
    if not active:
        _last_admin_guard(u, deactivate=True)
    with db.conn() as c:
        c.execute("UPDATE users SET active=?, gen=gen+1 WHERE id=?", (1 if active else 0, uid))


def remove(uid):
    u = get(uid)
    if not u:
        return
    _last_admin_guard(u, deactivate=True)
    with db.conn() as c:
        c.execute("DELETE FROM users WHERE id=?", (uid,))


# ---- command line (lost access) ----
def _cli(argv):
    init()
    if len(argv) >= 2 and argv[0] in ("reset", "add-admin"):
        email = argv[1]
        u = by_email(email)
        if argv[0] == "add-admin" and not u:
            u, token = add(argv[2] if len(argv) > 2 else email.split("@")[0], email, "admin")
        elif not u:
            print(f"No account for {email}. Use: python -m app.users add-admin {email} 'Your Name'")
            return 1
        else:
            token = new_link(u["id"])
            with db.conn() as c:
                c.execute("UPDATE users SET active=1 WHERE id=?", (u["id"],))
        import os
        base = os.environ.get("ADS_PUBLIC_URL", "").rstrip("/") or "https://ads.mcdp.com"
        print(f"Setup link for {u['name']} ({u['role']}), valid {LINK_DAYS} days:\n{base}/setup/{token}")
        return 0
    print("Usage: python -m app.users reset EMAIL | add-admin EMAIL 'Name'")
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
