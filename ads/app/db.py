"""SQLite storage for practices, uploaded exports and AI write-ups."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime

DB_PATH = os.environ.get("ADS_DB", "/data/ads.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS practices (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  google_budget REAL DEFAULT 0,
  meta_budget REAL DEFAULT 0,
  notes TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY,
  practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  platform TEXT NOT NULL,
  level TEXT NOT NULL,
  filename TEXT,
  date_start TEXT,
  date_end TEXT,
  row_count INTEGER,
  uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rows (
  upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
  date TEXT, date_start TEXT, date_end TEXT,
  campaign TEXT, name TEXT,
  spend REAL, impressions INTEGER, clicks INTEGER, conversions REAL, conv_value REAL,
  status TEXT, budget REAL, match_type TEXT
);
CREATE INDEX IF NOT EXISTS rows_upload ON rows(upload_id);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS ai_outputs (
  id INTEGER PRIMARY KEY,
  practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


@contextmanager
def conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


MIGRATIONS = {
    "practices": {"meta_account_id": "TEXT DEFAULT ''", "google_customer_id": "TEXT DEFAULT ''"},
    "uploads": {"source": "TEXT DEFAULT 'upload'"},
}


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


# ---- settings (small key/value store) ----
def get_setting(key, default=None):
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_setting(key, value):
    with conn() as c:
        if value is None:
            c.execute("DELETE FROM settings WHERE key=?", (key,))
        else:
            c.execute("INSERT INTO settings(key, value) VALUES (?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def now():
    return datetime.now().isoformat(timespec="seconds")


def _d(v):
    return date.fromisoformat(v) if v else None


# ---- practices ----
def practices():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM practices ORDER BY name COLLATE NOCASE")]


def practice(pid):
    with conn() as c:
        r = c.execute("SELECT * FROM practices WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def add_practice(name, google_budget=0.0, meta_budget=0.0):
    with conn() as c:
        cur = c.execute("INSERT INTO practices(name, google_budget, meta_budget, created_at) VALUES (?,?,?,?)",
                        (name.strip(), google_budget, meta_budget, now()))
        return cur.lastrowid


def update_practice(pid, name, google_budget, meta_budget, notes):
    with conn() as c:
        c.execute("UPDATE practices SET name=?, google_budget=?, meta_budget=?, notes=? WHERE id=?",
                  (name.strip(), google_budget, meta_budget, notes, pid))


def set_practice_accounts(pid, meta_account_id, google_customer_id):
    with conn() as c:
        c.execute("UPDATE practices SET meta_account_id=?, google_customer_id=? WHERE id=?",
                  (meta_account_id or "", google_customer_id or "", pid))


def delete_practice(pid):
    with conn() as c:
        c.execute("DELETE FROM practices WHERE id=?", (pid,))


# ---- uploads ----
def save_upload(pid, parsed, filename, source="upload"):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO uploads(practice_id, platform, level, filename, date_start, date_end, row_count, "
            "uploaded_at, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, parsed["platform"], parsed["level"], filename,
             parsed["date_start"].isoformat() if parsed["date_start"] else None,
             parsed["date_end"].isoformat() if parsed["date_end"] else None,
             len(parsed["rows"]), now(), source))
        uid = cur.lastrowid
        c.executemany(
            "INSERT INTO rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(uid,
              r["date"].isoformat() if r["date"] else None,
              r["date_start"].isoformat() if r["date_start"] else None,
              r["date_end"].isoformat() if r["date_end"] else None,
              r["campaign"], r["name"], r["spend"], r["impressions"], r["clicks"], r["conversions"],
              r["conv_value"], r["status"], r["budget"], r["match_type"]) for r in parsed["rows"]])
        return uid


def prune_synced(pid, platform, level, keep=3):
    """Nightly syncs replace each other; keep only the newest few."""
    with conn() as c:
        old = [r["id"] for r in c.execute(
            "SELECT id FROM uploads WHERE practice_id=? AND platform=? AND level=? AND source='sync' "
            "ORDER BY uploaded_at DESC, id DESC LIMIT -1 OFFSET ?", (pid, platform, level, keep))]
        c.executemany("DELETE FROM uploads WHERE id=?", [(i,) for i in old])


def uploads(pid):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM uploads WHERE practice_id=? ORDER BY uploaded_at DESC, id DESC", (pid,))]


def delete_upload(pid, uid):
    with conn() as c:
        c.execute("DELETE FROM uploads WHERE id=? AND practice_id=?", (uid, pid))


def latest_upload(pid, platform, level):
    with conn() as c:
        r = c.execute("SELECT * FROM uploads WHERE practice_id=? AND platform=? AND level=? "
                      "ORDER BY uploaded_at DESC, id DESC LIMIT 1", (pid, platform, level)).fetchone()
        return dict(r) if r else None


def upload_rows(uid):
    with conn() as c:
        out = []
        for r in c.execute("SELECT * FROM rows WHERE upload_id=?", (uid,)):
            d = dict(r)
            for k in ("date", "date_start", "date_end"):
                d[k] = _d(d[k])
            out.append(d)
        return out


# ---- AI write-ups ----
def save_ai(pid, kind, content):
    with conn() as c:
        c.execute("INSERT INTO ai_outputs(practice_id, kind, content, created_at) VALUES (?,?,?,?)",
                  (pid, kind, content, now()))


def latest_ai(pid, kind):
    with conn() as c:
        r = c.execute("SELECT * FROM ai_outputs WHERE practice_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                      (pid, kind)).fetchone()
        return dict(r) if r else None
