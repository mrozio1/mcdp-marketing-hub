"""Practice 360: one page per practice where each department shows its numbers, notes and action items.

The ledger keeps every note and action item for a practice, and every follow-up, each time-stamped and
pinned to what the dashboard showed at that moment (metric, period, value, which data refresh). Pins are
copies, so a note keeps its meaning after the numbers change. Entries lock an hour after they're written;
corrections go in as follow-ups.

Who sees what follows the Team page: the Practice 360 tab, one switch per department, and the person's
practices. Department owners are picked by an admin from the Hub's users.
"""
from __future__ import annotations

import io
import json
import os
import secrets
from datetime import datetime, timedelta

from . import db

DEPTS = [("finance", "Finance"), ("revenue", "Revenue Management"), ("procurement", "Procurement"),
         ("operations", "Operations"), ("marketing", "Marketing")]
DEPT_LABEL = dict(DEPTS)
KINDS = {"note": "Note", "action": "Action item"}
STATUSES = ("open", "progress", "scheduled", "done")
STATUS_LABEL = {"open": "Open", "progress": "In progress", "scheduled": "Scheduled", "done": "Done", "note": "Note", "overdue": "Overdue"}
LOCK_MINUTES = 60
MAX_SHOT = 8 * 1024 * 1024
SHOT_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
# The numbers each department would pin to. Values arrive from MCDP Reports (Power BI) or the weekly export.
METRICS = {
    "finance": ["Net production", "Production growth", "Wages %", "Labs %", "YTD provider discretionary"],
    "revenue": ["Collections", "Collections over 90 days", "Net production", "Production growth"],
    "procurement": ["Supplies %", "Supplies spend"],
    "operations": ["Starts", "Exams", "Success rate", "RPP rate", "RPP count"],
    "marketing": ["Online bookings", "Cost per booking", "New Google reviews", "Referring office visits", "Exams"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS p360_owners (dept TEXT NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (dept, user_id));
CREATE TABLE IF NOT EXISTS p360_metrics (
  practice_id INTEGER NOT NULL, metric TEXT NOT NULL, period TEXT NOT NULL, value REAL, num REAL, den REAL,
  shown TEXT DEFAULT '', source TEXT DEFAULT '', refresh_id INTEGER, PRIMARY KEY (practice_id, metric, period)
);
CREATE TABLE IF NOT EXISTS p360_refreshes (id INTEGER PRIMARY KEY, source TEXT, at TEXT, by TEXT DEFAULT '', rows INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS p360_entries (
  id INTEGER PRIMARY KEY, practice_id INTEGER NOT NULL, dept TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
  status TEXT DEFAULT 'open', owner_id INTEGER, due TEXT, review INTEGER DEFAULT 0,
  created_by INTEGER, created_name TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS p360_entries_p ON p360_entries(practice_id, dept);
CREATE TABLE IF NOT EXISTS p360_events (
  id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL, type TEXT NOT NULL, text TEXT DEFAULT '', status TEXT DEFAULT '',
  pin TEXT DEFAULT '', by_id INTEGER, by_name TEXT DEFAULT '', at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS p360_events_e ON p360_events(entry_id, at);
CREATE TABLE IF NOT EXISTS p360_shots (
  id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL, entry_id INTEGER NOT NULL, file TEXT NOT NULL, mime TEXT,
  caption TEXT DEFAULT '', at TEXT
);
"""


class P360Error(ValueError):
    pass


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


def now():
    return datetime.now().isoformat(timespec="seconds")


def shot_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "p360")
    os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------------ owners and access
def owners():
    """{dept: [user ids]}"""
    out = {k: [] for k, _ in DEPTS}
    with db.conn() as c:
        for r in c.execute("SELECT dept, user_id FROM p360_owners ORDER BY dept, user_id"):
            out.setdefault(r["dept"], []).append(r["user_id"])
    return out


def set_owners(dept, user_ids):
    if dept not in DEPT_LABEL:
        raise P360Error("No such department.")
    with db.conn() as c:
        c.execute("DELETE FROM p360_owners WHERE dept=?", (dept,))
        for uid in sorted(set(user_ids)):
            c.execute("INSERT INTO p360_owners(dept, user_id) VALUES (?,?)", (dept, uid))


def sees_dept(u, dept):
    from . import users
    return users.can(u, "p360") and users.can(u, "d_" + dept)


def depts_for(u):
    return [(k, l) for k, l in DEPTS if sees_dept(u, k)]


def can_write(u, dept):
    """Editors and admins who see the department, and the department's owners."""
    if not u or not sees_dept(u, dept):
        return False
    return u.get("role") in ("editor", "admin") or u["id"] in owners().get(dept, [])


def can_manage(u, entry):
    """Change status, owner or due date: admins, the department's owners, the author and the assignee."""
    if not u:
        return False
    return (u.get("role") == "admin" or u["id"] in owners().get(entry["dept"], [])
            or u["id"] in (entry.get("created_by"), entry.get("owner_id")))


# ------------------------------------------------------------------ numbers to pin
def latest(pid, metric):
    with db.conn() as c:
        r = c.execute("SELECT m.*, r.at AS refreshed FROM p360_metrics m LEFT JOIN p360_refreshes r ON r.id = m.refresh_id "
                      "WHERE practice_id=? AND metric=? ORDER BY period DESC LIMIT 1", (pid, metric)).fetchone()
    return dict(r) if r else None


def pin_for(pid, metric):
    """A copy of what the dashboard shows for this number right now (or that it has no value yet)."""
    if not metric:
        return None
    m = latest(pid, metric)
    if not m:
        return {"metric": metric, "period": "", "shown": "", "refreshed": "", "note": "No value in the Hub yet"}
    return {"metric": metric, "period": m["period"], "shown": m["shown"] or _fmt(m["value"]), "refreshed": m["refreshed"] or "",
            "source": m["source"]}


def _fmt(v):
    if v is None:
        return ""
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:g}"


def save_metrics(rows, source, by=""):
    """rows: [{practice_id, metric, period ('2026-10'), value, num, den, shown}]. Returns the refresh id."""
    with db.conn() as c:
        rid = c.execute("INSERT INTO p360_refreshes(source, at, by, rows) VALUES (?,?,?,?)", (source, now(), by, len(rows))).lastrowid
        for r in rows:
            c.execute("INSERT OR REPLACE INTO p360_metrics(practice_id, metric, period, value, num, den, shown, source, refresh_id) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (r["practice_id"], r["metric"], r["period"], r.get("value"), r.get("num"),
                                                      r.get("den"), r.get("shown") or "", source, rid))
    return rid


# ------------------------------------------------------------------ ledger
def _entry_row(r, today=None):
    e = dict(r)
    today = today or datetime.now().date().isoformat()
    st = e["status"] if e["kind"] == "action" else "note"
    if e["kind"] == "action" and st != "done" and e.get("due") and e["due"] < today:
        st = "overdue"
    e["state"] = st
    e["state_label"] = STATUS_LABEL[st]
    e["dept_label"] = DEPT_LABEL.get(e["dept"], e["dept"])
    e["kind_label"] = KINDS.get(e["kind"], e["kind"])
    return e


def add_entry(pid, dept, kind, title, text, u, metric="", owner_id=None, due="", review=False, shots=()):
    if dept not in DEPT_LABEL:
        raise P360Error("Pick a department.")
    if kind not in KINDS:
        raise P360Error("Pick a note or an action item.")
    title = (title or "").strip()[:200]
    if not title:
        raise P360Error("Give it a headline.")
    if not can_write(u, dept):
        raise P360Error(f"You can't add to {DEPT_LABEL[dept]} here. Ask an admin.")
    due = (due or "").strip()[:10]
    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except ValueError:
            raise P360Error("The due date isn't a date.")
    t = now()
    with db.conn() as c:
        eid = c.execute("INSERT INTO p360_entries(practice_id, dept, kind, title, status, owner_id, due, review, created_by, "
                        "created_name, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, dept, kind, title, "open", owner_id if kind == "action" else None, due if kind == "action" else None,
                         1 if review else 0, u["id"], u["name"], t, t)).lastrowid
    _event(eid, "created", text, u, pin=pin_for(pid, metric), shots=shots)
    return eid


def _event(eid, typ, text, u, status="", pin=None, shots=()):
    t = now()
    with db.conn() as c:
        vid = c.execute("INSERT INTO p360_events(entry_id, type, text, status, pin, by_id, by_name, at) VALUES (?,?,?,?,?,?,?,?)",
                        (eid, typ, (text or "").strip()[:8000], status, json.dumps(pin) if pin else "",
                         u["id"] if u else None, u["name"] if u else "Hub", t)).lastrowid
        c.execute("UPDATE p360_entries SET updated_at=? WHERE id=?", (t, eid))
    for data, mime, caption in shots:
        save_shot(vid, eid, data, mime, caption)
    return vid


def save_shot(vid, eid, data, mime, caption=""):
    if not data:
        return
    if len(data) > MAX_SHOT:
        raise P360Error("A screenshot is over 8 MB.")
    ext = SHOT_TYPES.get(mime)
    if not ext:
        raise P360Error("Screenshots must be PNG, JPG, WebP or GIF.")
    from PIL import Image
    try:
        Image.open(io.BytesIO(data)).verify()
    except Exception:
        raise P360Error("That screenshot couldn't be read.")
    name = f"{eid}-{vid}-{secrets.token_hex(6)}.{ext}"
    with open(os.path.join(shot_dir(), name), "wb") as f:
        f.write(data)
    with db.conn() as c:
        c.execute("INSERT INTO p360_shots(event_id, entry_id, file, mime, caption, at) VALUES (?,?,?,?,?,?)",
                  (vid, eid, name, mime, (caption or "")[:200], now()))


def follow_up(eid, text, u, status="", pin_now=True, shots=()):
    e = entry(eid)
    if not e:
        raise P360Error("That entry is gone.")
    if not can_write(u, e["dept"]) and u["id"] != e.get("owner_id"):
        raise P360Error("You can't add to this entry.")
    if status and status not in STATUSES:
        status = ""
    if status and (e["kind"] != "action" or not can_manage(u, e)):
        status = ""
    if not (text or "").strip() and not status and not shots:
        raise P360Error("Write what happened, attach a screenshot or change the status.")
    metric = (first_pin(eid) or {}).get("metric", "")
    _event(eid, "status" if status and not (text or "").strip() else "update", text, u, status=status,
           pin=pin_for(e["practice_id"], metric) if pin_now and metric else None, shots=shots)
    if status:
        with db.conn() as c:
            c.execute("UPDATE p360_entries SET status=? WHERE id=?", (status, eid))


def set_plan(eid, u, owner_id=None, due=None):
    e = entry(eid)
    if not e or e["kind"] != "action" or not can_manage(u, e):
        raise P360Error("You can't change this action item.")
    bits = []
    with db.conn() as c:
        if owner_id is not None and owner_id != e.get("owner_id"):
            c.execute("UPDATE p360_entries SET owner_id=? WHERE id=?", (owner_id or None, eid))
            bits.append("owner")
        if due is not None and (due or None) != e.get("due"):
            if due:
                datetime.strptime(due, "%Y-%m-%d")
            c.execute("UPDATE p360_entries SET due=? WHERE id=?", (due or None, eid))
            bits.append("due date")
    if bits:
        _event(eid, "plan", "Changed the " + " and ".join(bits) + ".", u)


def edit_first(eid, u, title, text):
    """The author can fix wording for an hour; after that, corrections are follow-ups."""
    e = entry(eid)
    if not e or e["created_by"] != u["id"]:
        raise P360Error("Only the author can edit this.")
    if datetime.now() - datetime.fromisoformat(e["created_at"]) > timedelta(minutes=LOCK_MINUTES):
        raise P360Error("Entries lock after an hour. Add a follow-up with the correction instead.")
    with db.conn() as c:
        c.execute("UPDATE p360_entries SET title=? WHERE id=?", ((title or e["title"]).strip()[:200], eid))
        c.execute("UPDATE p360_events SET text=? WHERE entry_id=? AND type='created'", ((text or "").strip()[:8000], eid))


def entry(eid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM p360_entries WHERE id=?", (eid,)).fetchone()
    return _entry_row(r) if r else None


def first_pin(eid):
    with db.conn() as c:
        r = c.execute("SELECT pin FROM p360_events WHERE entry_id=? AND pin != '' ORDER BY id LIMIT 1", (eid,)).fetchone()
    return json.loads(r["pin"]) if r else None


def events(eid):
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM p360_events WHERE entry_id=? ORDER BY at, id", (eid,))]
        shots = [dict(r) for r in c.execute("SELECT * FROM p360_shots WHERE entry_id=? ORDER BY id", (eid,))]
    for r in rows:
        r["pin"] = json.loads(r["pin"]) if r["pin"] else None
        r["shots"] = [s for s in shots if s["event_id"] == r["id"]]
        r["status_label"] = STATUS_LABEL.get(r["status"], "")
    return rows


def entries(pid, depts, kind="", only="", me_id=None, dept=""):
    """Entries for one practice in the given departments, newest activity first. only: open | overdue | mine."""
    if not depts:
        return []
    q = ",".join("?" * len(depts))
    with db.conn() as c:
        rows = [_entry_row(r) for r in c.execute(f"SELECT * FROM p360_entries WHERE practice_id=? AND dept IN ({q}) "
                                                  "ORDER BY updated_at DESC, id DESC", (pid, *depts))]
        counts = {r[0]: r[1] for r in c.execute("SELECT entry_id, COUNT(*) FROM p360_events GROUP BY entry_id")}
    for r in rows:
        r["follow_ups"] = max(0, counts.get(r["id"], 1) - 1)
        r["pin"] = first_pin(r["id"])
    if kind in KINDS:
        rows = [r for r in rows if r["kind"] == kind]
    if dept:
        rows = [r for r in rows if r["dept"] == dept]
    if only == "open":
        rows = [r for r in rows if r["state"] not in ("done", "note")]
    elif only == "overdue":
        rows = [r for r in rows if r["state"] == "overdue"]
    elif only == "mine" and me_id:
        rows = [r for r in rows if me_id in (r["created_by"], r["owner_id"])]
    return rows


def lanes(pid, u):
    """Per department the viewer can see: owners, open action items, the latest note."""
    from . import users
    names = {x["id"]: x["name"] for x in users.all_users()}
    own = owners()
    out = []
    for k, label in depts_for(u):
        rows = entries(pid, [k])
        out.append({"key": k, "label": label, "owners": [names[i] for i in own.get(k, []) if i in names],
                    "open": [r for r in rows if r["state"] not in ("done", "note")][:4],
                    "overdue": sum(1 for r in rows if r["state"] == "overdue"),
                    "latest": next((r for r in rows if r["kind"] == "note"), None),
                    "metrics": [dict(pin_for(pid, m) or {}, metric=m) for m in METRICS[k]],
                    "can_write": can_write(u, k)})
    return out


def open_count(pid, depts):
    return sum(1 for r in entries(pid, depts) if r["state"] not in ("done", "note"))
