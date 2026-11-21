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
CREATE TABLE IF NOT EXISTS p360_pods (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS p360_locations (
  id INTEGER PRIMARY KEY, pod_id INTEGER NOT NULL, name TEXT NOT NULL, code TEXT DEFAULT '', practice_id INTEGER,
  confirmed INTEGER DEFAULT 0, UNIQUE(pod_id, name)
);
CREATE TABLE IF NOT EXISTS p360_loc_data (name TEXT PRIMARY KEY, data TEXT NOT NULL, at TEXT);
CREATE TABLE IF NOT EXISTS p360_pod_metrics (
  pod_id INTEGER NOT NULL, metric TEXT NOT NULL, period TEXT NOT NULL, value REAL, num REAL, den REAL,
  shown TEXT DEFAULT '', source TEXT DEFAULT '', refresh_id INTEGER, PRIMARY KEY (pod_id, metric, period)
);
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
        if "seen_at" not in {r[1] for r in c.execute("PRAGMA table_info(p360_locations)")}:
            c.execute("ALTER TABLE p360_locations ADD COLUMN seen_at TEXT")
        if not c.execute("SELECT 1 FROM p360_pods LIMIT 1").fetchone():
            seed_pods(c)


# ------------------------------------------------------------------ pods (as MCDP Reports groups locations)
# Read from MCDP Reports › Practice Scorecard › Pods on 2026-11-21. Location names as drapp shows them (number = its code).
DRAPP_PODS = [
    ("Dr. A. Havens", ["Canton - 104", "Clarkston - 106", "Flushing - 105"]),
    ("Dr. Ancona", ["Clawson - 112", "Farmington Hills - 111"]),
    ("Dr. Baker", ["Waterford (Lazarus) - 121"]),
    ("Dr. Bowman", ["KO - Kalamazoo (129)", "KO - Paw Paw (131)", "KO - Portage (130)"]),
    ("Dr. Charcut", ["Charchut - Lansing (132)", "Charchut - St Johns (133)"]),
    ("Dr. Crouse", ["Brighton (Crouse) - 128", "Brighton (Peds Dent) - 301"]),
    ("Dr. D. Havens", ["Imlay City - 102", "Romeo - 109", "Washington - 100"]),
    ("Dr. Dellinger", ["Orthodontic Specialty Services - FW(135)", "Orthodontic Specialty Services - WS(136)"]),
    ("Dr. Driscoll", ["Driscoll Orthodontics (134)"]),
    ("Dr. Fenn", ["Heights - 114", "St Clair Shores - 103"]),
    ("Dr. Frenchi / Dr. Jezdimir", ["Lakepointe Orthodontics (137)"]),
    ("Dr. Gregg", ["Gregg Orthodontics (138)"]),
    ("Dr. Lawrence", ["Ken Lawrence Ortho (403)"]),
    ("Dr. Mika", ["Chelsea (734) - 110", "Hartland (313) - 107", "Howell (313) - 108"]),
    ("Dr. Norwick", ["Dearborn (Norwick) - 127"]),
    ("Dr. Paron", ["Farmington (Havens) -113", "Grand Blanc (OS) - 115", "Lapeer (Havens) - 116"]),
    ("Dr. Robinson", ["Findlay Orthodontics (404)"]),
    ("Dr. Schulz", ["Charlevoix (Schulz) - 123", "Traverse City (Schulz) - 122"]),
    ("Dr. Wesley", ["Fort Gratiot (Wesley) - 126", "Marysville (Wesley) - 125", "Richmond (Wesley) - 124"]),
    ("Dr. Wheeler", ["Wheeler Ortho - Archbold (402)", "Wheeler Ortho - Toledo (401)"]),
    ("Drs. Bandeen", ["Battle Creek (Bandeen) - 118", "Marshall - 117", "Portage (Bandeen) - 119", "Three Rivers (Bandeen) - 120"]),
]
_SKIP = {"ortho", "orthodontics", "orthodontic", "dental", "dentistry", "pediatric", "peds", "dent", "of", "the", "and", "family",
         "dr", "drs", "ko", "specialty", "services", "smile", "smiles", "oral", "surgery", "st"}


def seed_pods(c=None):
    import re
    def run(c):
        for i, (pod, locs) in enumerate(DRAPP_PODS):
            c.execute("INSERT OR IGNORE INTO p360_pods(name, sort) VALUES (?,?)", (pod, i))
            pid = c.execute("SELECT id FROM p360_pods WHERE name=?", (pod,)).fetchone()[0]
            for loc in locs:
                code = (re.findall(r"\b(\d{3})\b", loc) or [""])[-1]
                c.execute("INSERT OR IGNORE INTO p360_locations(pod_id, name, code) VALUES (?,?,?)", (pid, loc, code))
    if c is None:
        with db.conn() as c2:
            run(c2)
    else:
        run(c)


def _words(s):
    import re
    return [w for w in re.findall(r"[a-z]+", (s or "").lower()) if w not in _SKIP and len(w) > 2]


# Matches Mark confirmed (2026-11-21), used before guessing from names.
KNOWN = {"Canton - 104": "Havens Orthodontics", "Romeo - 109": "Havens Orthodontics", "Clarkston - 106": "Havens Orthodontics",
         "Imlay City - 102": "Havens Orthodontics", "Washington - 100": "Havens Orthodontics",
         "Brighton (Peds Dent) - 301": "Pediatric Dentistry of Brighton",
         "Orthodontic Specialty Services - FW(135)": "OSSFW", "Orthodontic Specialty Services - WS(136)": "OSSFW"}


def suggest_practice(loc_name, plist):
    """The one practice whose distinctive name words appear in the location name (words in brackets first), or None."""
    import re
    want = KNOWN.get(loc_name)
    if want:
        hit = next((p["id"] for p in plist if p["name"].strip().lower() == want.lower()), None)
        if hit:
            return hit
    inner = " ".join(re.findall(r"\(([^)]*)\)", loc_name))
    for text in (inner, re.sub(r"\([^)]*\)", " ", loc_name), loc_name):
        ws = set(_words(text))
        if not ws:
            continue
        hits = [p for p in plist if set(_words(p["name"])) & ws]
        if len(hits) == 1:
            return hits[0]["id"]
    return None


def pods():
    """[{id, name, locations: [{id, name, code, practice_id, confirmed, suggested}], practice_ids}]"""
    plist = db.practices(include_hidden=True)
    with db.conn() as c:
        ps = [dict(r) for r in c.execute("SELECT * FROM p360_pods ORDER BY sort, name")]
        ls = [dict(r) for r in c.execute("SELECT * FROM p360_locations ORDER BY name")]
    if any(l.get("seen_at") for l in ls):          # after an import: only what MCDP Reports lists now
        last = max(l["seen_at"] or "" for l in ls)
        ls = [l for l in ls if l.get("seen_at") == last]
    for p in ps:
        p["locations"] = [l for l in ls if l["pod_id"] == p["id"]]
        for l in p["locations"]:
            l["suggested"] = l["practice_id"] if l["confirmed"] else suggest_practice(l["name"], plist)
        p["practice_ids"] = sorted({l["suggested"] for l in p["locations"] if l["suggested"]})
    return ps


def pod(pod_id):
    return next((p for p in pods() if p["id"] == pod_id), None)


def set_location_practice(loc_id, practice_id):
    with db.conn() as c:
        c.execute("UPDATE p360_locations SET practice_id=?, confirmed=1 WHERE id=?", (practice_id or None, loc_id))


def drapp_practice_ids():
    """Practices that have at least one MCDP Reports location (matched or guessed)."""
    return {i for p in pods() for i in p["practice_ids"]}


def pods_of_practice(pid):
    return [p for p in pods() if pid in p["practice_ids"]]


def pod_pin(pod_id, metric):
    with db.conn() as c:
        m = c.execute("SELECT m.*, r.at AS refreshed FROM p360_pod_metrics m LEFT JOIN p360_refreshes r ON r.id = m.refresh_id "
                      "WHERE pod_id=? AND metric=? ORDER BY period DESC LIMIT 1", (pod_id, metric)).fetchone()
    if not m:
        return {"metric": metric, "period": "", "shown": ""}
    return {"metric": metric, "period": m["period"], "shown": m["shown"] or _fmt(m["value"]), "refreshed": m["refreshed"] or ""}


def save_pod_metrics(rows, source, by=""):
    """rows: [{pod_id, metric, period, value, num, den, shown}]"""
    with db.conn() as c:
        rid = c.execute("INSERT INTO p360_refreshes(source, at, by, rows) VALUES (?,?,?,?)", (source, now(), by, len(rows))).lastrowid
        for r in rows:
            c.execute("INSERT OR REPLACE INTO p360_pod_metrics(pod_id, metric, period, value, num, den, shown, source, refresh_id) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (r["pod_id"], r["metric"], r["period"], r.get("value"), r.get("num"),
                                                      r.get("den"), r.get("shown") or "", source, rid))
    return rid


def pod_lanes(pod_id, u, visible_pids):
    """Like lanes(), across every practice in the pod the viewer can open; numbers are the pod's own (from drapp)."""
    from . import users
    pd = pod(pod_id)
    if not pd:
        return None, []
    pids = [x for x in pd["practice_ids"] if visible_pids is None or x in visible_pids]
    names = {x["id"]: x["name"] for x in users.all_users()}
    pnames = {p["id"]: p["name"] for p in db.practices(include_hidden=True)}
    own = owners()
    out = []
    for k, label in depts_for(u):
        rows = [dict(r, practice=pnames.get(r["practice_id"], "")) for x in pids for r in entries(x, [k])]
        rows.sort(key=lambda r: r["updated_at"] or "", reverse=True)
        out.append({"key": k, "label": label, "owners": [names[i] for i in own.get(k, []) if i in names],
                    "open": [r for r in rows if r["state"] not in ("done", "note")][:6],
                    "overdue": sum(1 for r in rows if r["state"] == "overdue"),
                    "latest": next((r for r in rows if r["kind"] == "note"), None),
                    "metrics": [pod_pin(pod_id, m) for m in METRICS[k]], "can_write": False})
    return pd, out


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


# ------------------------------------------------------------------ import from MCDP Reports (the weekly run)
IMPORT_MINUTES = 30
MON = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def import_key():
    """A one-use key for the next import, good for 30 minutes."""
    k = secrets.token_urlsafe(24)
    db.set_setting("p360_import_key", json.dumps({"k": k, "until": (datetime.now() + timedelta(minutes=IMPORT_MINUTES)).isoformat()}))
    return k


def take_key(k):
    try:
        d = json.loads(db.get_setting("p360_import_key") or "{}")
    except ValueError:
        return False
    ok = bool(k) and secrets.compare_digest(str(k), d.get("k", "")) and d.get("until", "") > datetime.now().isoformat()
    if ok:
        db.set_setting("p360_import_key", "{}")
    return ok


def _num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def _money(v):
    if v is None:
        return ""
    a = abs(v)
    s = f"${a / 1e6:.2f}M" if a >= 1e6 else (f"${a / 1e3:.1f}K" if a >= 1e3 else f"${a:,.0f}")
    return ("-" if v < 0 else "") + s


def _pct(v, places=1, sign=False):
    if v is None:
        return ""
    return (f"{v:+.{places}f}%" if sign else f"{v:.{places}f}%")


def _period(date_range):
    """'10/01/2026 - 10/31/2026' -> '2026-10' (a whole month), else ''."""
    import calendar
    import re
    m = re.findall(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_range or "")
    if len(m) == 2 and m[0][1] == "01" and m[0][0] == m[1][0] and m[0][2] == m[1][2]:
        y, mo = int(m[0][2]), int(m[0][0])
        if int(m[1][1]) == calendar.monthrange(y, mo)[1]:
            return f"{y}-{mo:02d}"
    return ""


def import_reports(payload, by="weekly run"):
    """payload from the collector: {date_range, locations: [{pod, name, summary: {...}, months: {production|starts|exams: [[Mon, Year, value]]}}]}.
    Adds new locations to their pods, then works out each practice's and pod's numbers from its locations."""
    locs = payload.get("locations") or []
    if not locs:
        raise P360Error("Nothing came in.")
    period = _period(payload.get("date_range"))
    with db.conn() as c:
        for i, l in enumerate(locs):
            c.execute("INSERT OR IGNORE INTO p360_pods(name, sort) VALUES (?, ?)", (l["pod"], 100 + i))
            pid = c.execute("SELECT id FROM p360_pods WHERE name=?", (l["pod"],)).fetchone()[0]
            c.execute("INSERT OR IGNORE INTO p360_locations(pod_id, name) VALUES (?,?)", (pid, l["name"]))
    # each location's numbers: monthly series plus the month the summary tiles cover
    data = {}
    # months after the one the summary covers are still in progress (drapp's charts include this month so far): left out
    last_full = period or (datetime.now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    for l in locs:
        d = data.setdefault(l["name"], {})
        for key, rows in (l.get("months") or {}).items():
            for mon, year, val in rows:
                v = _num(val)
                if v is None or mon not in MON:
                    continue
                per = f"{int(year)}-{MON[mon]:02d}"
                if per <= last_full:
                    d.setdefault(per, {})[key] = v
        s_ = l.get("summary") or {}
        if period:
            row = d.setdefault(period, {})
            for k in ("labs", "wages", "supplies", "rpp", "ytd", "net", "starts", "exams"):
                v = _num(s_.get(k))
                if v is not None:
                    row[{"net": "production"}.get(k, k)] = v
    t = now()
    with db.conn() as c:
        for name, d in data.items():
            c.execute("INSERT OR REPLACE INTO p360_loc_data(name, data, at) VALUES (?,?,?)", (name, json.dumps(d), t))
        for l in locs:
            c.execute("UPDATE p360_locations SET seen_at=? WHERE name=?", (t, l["name"]))
    res = recompute(by)
    return {"locations": len(locs), "period": period, **res}


def recompute(by="Hub"):
    """Practice and pod numbers from each location's stored numbers and the current matches (after an import, and
    whenever an admin changes a match)."""
    with db.conn() as c:
        data = {r["name"]: json.loads(r["data"]) for r in c.execute("SELECT name, data FROM p360_loc_data")}
    if not data:
        return {"practices": 0, "pods": 0, "rows": 0}
    groups_p, groups_pod = {}, {}
    for p in pods():
        for l in p["locations"]:
            if l["name"] in data:
                groups_pod.setdefault(p["id"], []).append(l["name"])
                if l["suggested"]:
                    groups_p.setdefault(l["suggested"], []).append(l["name"])
    rows_p, rows_pod = [], []
    for target, groups in ((rows_p, groups_p), (rows_pod, groups_pod)):
        for gid, names in groups.items():
            for per, ms in _combine([data[n] for n in names]).items():
                for metric, (val, shown) in ms.items():
                    target.append({"id": gid, "metric": metric, "period": per, "value": val, "shown": shown})
    with db.conn() as c:
        rid = c.execute("INSERT INTO p360_refreshes(source, at, by, rows) VALUES (?,?,?,?)",
                        ("MCDP Reports", now(), by, len(rows_p) + len(rows_pod))).lastrowid
        c.execute("DELETE FROM p360_metrics WHERE source='MCDP Reports'")
        c.execute("DELETE FROM p360_pod_metrics WHERE source='MCDP Reports'")
        for r in rows_p:
            c.execute("INSERT OR REPLACE INTO p360_metrics(practice_id, metric, period, value, shown, source, refresh_id) "
                      "VALUES (?,?,?,?,?,?,?)", (r["id"], r["metric"], r["period"], r["value"], r["shown"], "MCDP Reports", rid))
        for r in rows_pod:
            c.execute("INSERT OR REPLACE INTO p360_pod_metrics(pod_id, metric, period, value, shown, source, refresh_id) "
                      "VALUES (?,?,?,?,?,?,?)", (r["id"], r["metric"], r["period"], r["value"], r["shown"], "MCDP Reports", rid))
    return {"practices": len(groups_p), "pods": len(groups_pod), "rows": len(rows_p) + len(rows_pod)}


def _combine(series):
    """Add up locations: counts and dollars summed, rates worked out from the totals (never averaged)."""
    periods = sorted({p for s in series for p in s})
    out = {}
    tot = {p: {} for p in periods}
    for p in periods:
        for k in ("production", "starts", "exams", "rpp", "ytd"):
            vals = [s[p][k] for s in series if p in s and k in s[p]]
            if vals:
                tot[p][k] = sum(vals)
        for k in ("wages", "supplies", "labs"):
            pairs = [(s[p][k], s[p].get("production") or 0) for s in series if p in s and k in s[p]]
            w = sum(b for _, b in pairs)
            if pairs:
                tot[p][k] = sum(a * b for a, b in pairs) / w if w else sum(a for a, _ in pairs) / len(pairs)
    for p in periods:
        t, m = tot[p], {}
        y, mo = p.split("-")
        ly = tot.get(f"{int(y) - 1}-{mo}", {})
        if "production" in t:
            m["Net production"] = (t["production"], _money(t["production"]))
            if ly.get("production"):
                g = (t["production"] / ly["production"] - 1) * 100
                m["Production growth"] = (g, _pct(g, 1, True))
        for k, name in (("starts", "Starts"), ("exams", "Exams"), ("rpp", "RPP count")):
            if k in t:
                m[name] = (t[k], f"{t[k]:,.0f}")
        if t.get("exams") and "starts" in t:
            v = t["starts"] / t["exams"] * 100
            m["Success rate"] = (v, _pct(v))
        if t.get("starts") and "rpp" in t:
            v = t["rpp"] / t["starts"] * 100
            m["RPP rate"] = (v, _pct(v))
        for k, name in (("wages", "Wages %"), ("supplies", "Supplies %"), ("labs", "Labs %")):
            if k in t:
                m[name] = (t[k], _pct(t[k], 0))
        # YTD provider discretionary is left out: drapp works it out per provider, so locations don't add up to its total.
        if m:
            out[p] = m
    return out
