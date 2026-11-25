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
import json
import os
import hmac
import re
import secrets
import sys
from datetime import datetime, timedelta

from . import db

ROLES = ("viewer", "editor", "admin")
ROLE_LABELS = {"viewer": "Viewer", "editor": "Editor", "admin": "Admin"}
ROLE_HELP = {"viewer": "Looks only; can't change anything. Starts with nothing switched on: pick what they can open below.",
             "editor": "Also uploads, fixes, offices and list stars/notes. Starts with every tab, practice and person.",
             "admin": "Also keys and connections, people, map setup and deleting practices. Starts with everything."}
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
           "seasonal": "INTEGER NOT NULL DEFAULT 1",
           "field_manager": "INTEGER NOT NULL DEFAULT 0",
           "perms": "TEXT DEFAULT ''",          # JSON of what this person can open; '' = their access level's defaults
           "practice_ids": "TEXT DEFAULT ''",   # JSON list of the practices they're associated with
           "cal_color": "TEXT DEFAULT ''",
           "department": "TEXT DEFAULT ''",      # one of dept.DEPARTMENTS (Team page)
           "dept_lead": "INTEGER NOT NULL DEFAULT 0"}   # leads that department: approves its visits, gets new-visit alerts      # their colour on the calendar   # sees and reports on every field agent's visits   # Social link shown in the hub (on for existing people)   # field visits switched on for this person (any access level)


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
            if "perms" in added:
                _keep_current_access(c)
        # Anyone given the short-lived "Field agent" level becomes a viewer with field visits on.
        c.execute("UPDATE users SET role='viewer', field=1 WHERE role='agent'")


SWITCHES = {"field": "Field visits", "social": "Social", "seasonal": "Seasonal decorations", "field_manager": "Field manager"}

# ---- what each person can open ----
# Tabs, in the order the hub falls back to when the dashboard is off (no tab at all: their Account page).
TABS = [("dashboard", "Dashboard", "/"), ("practices", "Practices", "/practices"), ("p360", "Practice 360", "/p360"), ("reviews", "Reviews", "/reviews"),
        ("search", "Search", "/search"), ("studio", "Studio", "/studio"), ("map", "Map", "/map"),
        ("field", "Field", "/field"), ("calendar", "Calendar", "/calendar")]
EXTRAS = [("reports", "Reports & exports", "Master reports, practice and field reports, PDFs, emails and spreadsheet downloads"),
          ("refresh", "Refresh & sync", "Sync ads, refresh reviews, search, map and schools, fix ad tracking"),
          ("team_field", "Everyone's field visits", "Other people's visits, spend, follow-ups and field reports (off: only their own)"),
          ("team_calendar", "Everyone's calendar", "Other people's events and time off (off: only what they made or are invited to)"),
          ("see_admins", "Admins' activity", "Admins' field visits and calendar events (off: hidden from this person)"),
          ("email", "Send email", "Email campaigns in Studio, sent as the practices they can open (editors and admins)")]
# Practice 360: which departments' numbers, notes and action items they see (and, as editors, add to).
DEPT_FLAGS = [("d_finance", "Finance"), ("d_revenue", "Revenue Management"), ("d_procurement", "Procurement"),
              ("d_operations", "Operations"), ("d_marketing", "Marketing"), ("d_hr", "HR")]
FLAGS = [k for k, *_ in TABS] + [k for k, *_ in EXTRAS] + [k for k, _ in DEPT_FLAGS] + ["all_practices"]
CAL_COLORS = ["#2995D2", "#E0703A", "#3E9E6E", "#8E5BC9", "#D24C7A", "#C9A227", "#1E4A73", "#4AA3A2", "#A0522D", "#6B7A8F"]


def role_defaults(role):
    """Admins and editors start with everything (every practice and person); viewers start with nothing."""
    on = role in ("admin", "editor")
    if role == "viewer" and os.environ.get("ADS_VIEWER_DEFAULTS") == "legacy":
        # only for the older tests, written when viewers could open everything but refresh
        return {**{k: True for k in FLAGS}, "refresh": False, "team_field": False}
    d = {k: on for k in FLAGS}
    d["d_hr"] = role == "admin"          # HR numbers: admins only until someone is given HR on the Team page
    return d


def _keep_current_access(c):
    """First run of this version: nobody loses what they could already open. Viewers could see every tab,
    practice and report before; field managers saw everyone's visits."""
    for r in c.execute("SELECT id, role, field_manager FROM users").fetchall():
        if r["role"] == "viewer":
            a = {k: True for k in FLAGS}
            a.update(refresh=False, team_field=bool(r["field_manager"]))
            c.execute("UPDATE users SET perms=? WHERE id=?", (json.dumps(a), r["id"]))


def access(u):
    """What this person can open: {flag: bool}, with their access level's defaults for anything not set."""
    if not u:
        return {k: False for k in FLAGS}
    a = role_defaults(u.get("role"))
    try:
        a.update({k: bool(v) for k, v in json.loads(u.get("perms") or "{}").items() if k in a})
    except ValueError:
        pass
    return a


def can(u, flag):
    return bool(access(u).get(flag))


def practice_ids(u):
    """None: every practice. Otherwise the set of practice ids they're associated with."""
    if not u or can(u, "all_practices"):
        return None
    return chosen_practices(u)


def chosen_practices(u):
    """The practices ticked for them (used when 'Every practice' is off)."""
    try:
        return {int(x) for x in json.loads((u or {}).get("practice_ids") or "[]")}
    except (ValueError, TypeError):
        return set()


def sees_practice(u, pid):
    ids = practice_ids(u)
    return ids is None or pid in ids


def home(u):
    """Where 'home' is: the dashboard if it's on, else the first tab they have, else their Account page."""
    a = access(u)
    return next((path for k, _, path in TABS if a.get(k)), "/account")


def cal_color(u):
    if not u:
        return ""
    return u.get("cal_color") or CAL_COLORS[(u.get("id") or 0) % len(CAL_COLORS)]


def set_access(uid, flags, practices=None, color=None):
    """flags: {flag: bool} for every flag shown; practices: list of ids (used when all_practices is off)."""
    u = get(uid)
    if not u:
        raise ValueError("No such person.")
    a = access(u)
    a.update({k: bool(v) for k, v in flags.items() if k in a})
    with db.conn() as c:
        c.execute("UPDATE users SET perms=? WHERE id=?", (json.dumps(a), uid))
        if practices is not None:
            c.execute("UPDATE users SET practice_ids=? WHERE id=?", (json.dumps(sorted({int(p) for p in practices})), uid))
        if color is not None:
            color = color.strip()
            if color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                raise ValueError("Pick a colour.")
            c.execute("UPDATE users SET cal_color=? WHERE id=?", (color, uid))


def reset_access(uid):
    """Back to the access level's defaults (used when someone's access level changes)."""
    with db.conn() as c:
        c.execute("UPDATE users SET perms='' WHERE id=?", (uid,))


def sees_all_field(u):
    """Whether they see (and report on) everyone's field visits, not just their own. Department leads and Management
    always do: they approve and follow their teams' visits."""
    if u and (u.get("department") == "management" or (u.get("dept_lead") and u.get("department"))):
        return True
    return can(u, "team_field")


def hidden_people(u):
    """Ids of people whose field visits and calendar events this person doesn't see (admins, when that's off)."""
    if not u or can(u, "see_admins"):
        return set()
    with db.conn() as c:
        return {r[0] for r in c.execute("SELECT id FROM users WHERE role='admin' AND id!=?", (u["id"],))}


def set_switch(uid, name, on):
    if name not in SWITCHES:
        raise ValueError("Unknown switch.")
    with db.conn() as c:
        c.execute(f"UPDATE users SET {name}=? WHERE id=?", (1 if on else 0, uid))
    # the older switches also open what they need: logging visits needs the Field tab; a field manager sees everyone
    if name == "field" and on:
        set_access(uid, {"field": True})
    elif name == "field_manager":
        set_access(uid, {"team_field": bool(on)})


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
    if field and not role_defaults(role)["field"]:
        set_access(uid, {"field": True})     # logging visits needs the Field tab
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
        if role != u["role"]:
            c.execute("UPDATE users SET perms='' WHERE id=?", (uid,))   # the new level's defaults


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
