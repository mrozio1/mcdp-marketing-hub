"""Departments for field work: each person is in one department (set on the Team page), and a department can have
leads. Leads approve their department's visits (or send them back with a note) and are told about every new visit,
in the Hub and by email. Management sees every department's visits and forms; managers' and leads' own visits need no
approval. Admins can do everything. Operations' checklists (app/checklists.py) are filled in with a visit and only
Operations, Management and admins see them.
"""
from __future__ import annotations

import html
import json

from . import checklists, db

DEPARTMENTS = [("marketing", "Marketing"), ("operations", "Operations"), ("hr", "HR"), ("finance", "Finance"),
               ("revenue", "Revenue Management"), ("procurement", "Procurement"), ("management", "Management")]
LABEL = dict(DEPARTMENTS)
MANAGEMENT = "management"

SCHEMA = """
CREATE TABLE IF NOT EXISTS visit_forms (
  visit_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}', combined INTEGER,
  escalation TEXT DEFAULT '', updated_at TEXT, updated_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS visit_alerts (
  id INTEGER PRIMARY KEY, visit_id INTEGER NOT NULL, user_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'new',
  text TEXT DEFAULT '', created_at TEXT NOT NULL, seen_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS visit_alerts_u ON visit_alerts(user_id, seen_at);
"""
VISIT_COLUMNS = {"department": "TEXT DEFAULT ''", "approval": "TEXT DEFAULT ''", "approval_by": "TEXT DEFAULT ''",
                 "approval_at": "TEXT DEFAULT ''", "approval_note": "TEXT DEFAULT ''"}


class DeptError(Exception):
    pass


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("PRAGMA table_info(visits)")}
        for col, typ in VISIT_COLUMNS.items():
            if col not in have:
                c.execute(f"ALTER TABLE visits ADD COLUMN {col} {typ}")


# ------------------------------------------------------------------ who is who
def of(u):
    return (u or {}).get("department") or ""


def is_admin(u):
    return bool(u) and u.get("role") == "admin"


def is_management(u):
    return of(u) == MANAGEMENT


def is_lead(u, dept=None):
    return bool(u) and bool(u.get("dept_lead")) and bool(of(u)) and of(u) != MANAGEMENT and (dept is None or of(u) == dept)


def leads(dept):
    from . import users
    return [x for x in users.all_users() if x.get("active") and x.get("dept_lead") and x.get("department") == dept]


def set_department(uid, dept, lead):
    if dept and dept not in LABEL:
        raise DeptError("Pick a department from the list.")
    with db.conn() as c:
        c.execute("UPDATE users SET department=?, dept_lead=? WHERE id=?",
                  (dept or "", 1 if (lead and dept and dept != MANAGEMENT) else 0, uid))


def needs_approval(u):
    """Visits by someone in a department need their lead's approval, except managers' and leads' own."""
    return bool(of(u)) and not is_management(u) and not is_lead(u)


# ------------------------------------------------------------------ what someone may do with a visit
def can_change(u, v):
    """Edit or remove: their own visit (with field visits on), admins, Management, and the lead of the visit's
    department. Editors who aren't in any department keep the old rule (any visit)."""
    from . import users
    if not (v and u):
        return False
    if v["user_id"] == u["id"] and u.get("field"):
        return True
    if is_admin(u) or is_management(u):
        return True
    if of(u):
        return is_lead(u, v.get("department") or "")
    return users.rank(u["role"]) >= users.rank("editor")


def can_approve(u, v):
    if not (v and u) or v.get("approval") not in ("pending", "returned", "approved"):
        return False
    if v["user_id"] == u["id"] and not is_admin(u):
        return False
    return is_admin(u) or is_management(u) or is_lead(u, v.get("department") or "")


def sees_forms(u, dept=checklists.DEPT):
    return is_admin(u) or is_management(u) or of(u) == dept


def uses_forms(u):
    """Whether the log form offers the checklists (Operations people; admins and Management can fill them in too)."""
    return sees_forms(u)


# ------------------------------------------------------------------ when visits are logged, changed and approved
def on_logged(vid, u, public_url=""):
    """Stamp the visit with the logger's department, mark it for approval if it needs it, and tell the leads."""
    dept = of(u)
    approval = "pending" if needs_approval(u) else ""
    with db.conn() as c:
        c.execute("UPDATE visits SET department=?, approval=? WHERE id=?", (dept, approval, vid))
    if dept and dept != MANAGEMENT:
        _alert_leads(vid, u, dept, "new", public_url)


def on_edited(vid, u, public_url=""):
    """A returned visit that its owner changed goes back to the lead."""
    from . import field
    v = field.visit(vid)
    if v and v.get("approval") == "returned" and v["user_id"] == u["id"]:
        with db.conn() as c:
            c.execute("UPDATE visits SET approval='pending' WHERE id=?", (vid,))
        _alert_leads(vid, u, v.get("department") or "", "resubmitted", public_url)


def approve(vid, u, note=""):
    from . import field
    v = field.visit(vid)
    if not can_approve(u, v):
        raise DeptError("Only the department's lead, Management or an admin can approve this visit.")
    with db.conn() as c:
        c.execute("UPDATE visits SET approval='approved', approval_by=?, approval_at=?, approval_note=? WHERE id=?",
                  (u["name"], db.now(), (note or "").strip()[:1000], vid))
        c.execute("UPDATE visit_alerts SET seen_at=? WHERE visit_id=? AND seen_at=''", (db.now(), vid))


def send_back(vid, u, note, public_url=""):
    from . import field
    v = field.visit(vid)
    if not can_approve(u, v):
        raise DeptError("Only the department's lead, Management or an admin can send this visit back.")
    note = (note or "").strip()[:1000]
    if not note:
        raise DeptError("Say what needs correcting, so they know what to change.")
    with db.conn() as c:
        c.execute("UPDATE visits SET approval='returned', approval_by=?, approval_at=?, approval_note=? WHERE id=?",
                  (u["name"], db.now(), note, vid))
        c.execute("UPDATE visit_alerts SET seen_at=? WHERE visit_id=? AND seen_at=''", (db.now(), vid))
        c.execute("INSERT INTO visit_alerts(visit_id, user_id, kind, text, created_at) VALUES (?,?,?,?,?)",
                  (vid, v["user_id"], "returned", note, db.now()))
    owner = _user(v["user_id"])
    if owner:
        _email([owner], f"Visit sent back: {v['office_name']}",
               f"<p>{html.escape(u['name'])} sent back your visit to <strong>{html.escape(v['office_name'])}</strong> "
               f"({v['visited_at'][:10]}) for a correction:</p><blockquote>{html.escape(note)}</blockquote>"
               + _link(public_url, f"/field/visits/{vid}/edit", "Open the visit"))


def _user(uid):
    from . import users
    x = users.get(uid) if uid else None
    return x if x and x.get("active") else None


def _alert_leads(vid, u, dept, kind, public_url):
    from . import field
    v = field.visit(vid)
    if not v:
        return
    to = [x for x in leads(dept) if x["id"] != u["id"]]
    form = form_for(vid)
    esc = (form or {}).get("escalation") or ""
    sc = checklists.score(form["kind"], form["data"]) if form else None
    bits = []
    if form:
        bits.append(f"{checklists.FORMS[form['kind']]['title']}" + (f", combined {sc['combined']}%" if sc and sc["combined"] is not None else ""))
    text = ("Sent again after a correction. " if kind == "resubmitted" else "") + " · ".join(bits)
    with db.conn() as c:
        for x in to:
            c.execute("INSERT INTO visit_alerts(visit_id, user_id, kind, text, created_at) VALUES (?,?,?,?,?)",
                      (vid, x["id"], "escalation" if esc else kind, text, db.now()))
    if to:
        what = "sent back in after a correction" if kind == "resubmitted" else "logged"
        body = (f"<p>{html.escape(u['name'])} {what} a {html.escape(LABEL.get(dept, dept))} visit to "
                f"<strong>{html.escape(v['office_name'])}</strong> on {v['visited_at'][:16].replace('T', ' ')}.</p>"
                + (f"<p>{html.escape(text)}</p>" if text else "")
                + (f"<p style=\"color:#a33\"><strong>Safety escalation:</strong> {html.escape(esc)}</p>" if esc else "")
                + (f"<p>{html.escape(v['notes'][:600])}</p>" if v.get("notes") else "")
                + ("<p>It's waiting for your approval.</p>" if v.get("approval") == "pending" else "")
                + _link(public_url, f"/field?period=day&d={v['visited_at'][:10]}#v{vid}", "Open it in the Hub"))
        _email(to, ("Safety escalation: " if esc else "New visit: ") + f"{v['office_name']} ({u['name']})", body)


def _link(public_url, path, label):
    return f'<p><a href="{public_url.rstrip("/")}{path}">{label}</a></p>' if public_url else ""


def _email(people, subject, body):
    from . import microsoft
    to = [x["email"] for x in people if x.get("email")]
    if not to:
        return
    try:
        if microsoft.connected():
            microsoft.send_mail(to, subject, body + '<p style="color:#54636e;font-size:13px">From the MCDP Hub.</p>')
    except Exception:
        pass                    # the in-app alert is still there


def alerts(uid, unseen_only=True, limit=20):
    with db.conn() as c:
        q = ("SELECT a.*, v.user_name, v.visited_at, v.department, v.approval, o.name AS office_name FROM visit_alerts a "
             "JOIN visits v ON v.id=a.visit_id JOIN field_offices o ON o.id=v.office_id WHERE a.user_id=?"
             + (" AND a.seen_at=''" if unseen_only else "") + " ORDER BY a.id DESC LIMIT ?")
        return [dict(r) for r in c.execute(q, (uid, limit))]


def unseen_count(uid):
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM visit_alerts WHERE user_id=? AND seen_at=''", (uid,)).fetchone()[0]


def attention_count(u):
    """Visits needing this person: unseen alerts plus visits waiting for their approval (each visit once)."""
    if not u or not u.get("id"):
        return 0
    with db.conn() as c:
        ids = {r[0] for r in c.execute("SELECT visit_id FROM visit_alerts WHERE user_id=? AND seen_at=''", (u["id"],))}
    return len(ids | {r["id"] for r in pending_for(u)})


def mark_seen(uid):
    with db.conn() as c:
        c.execute("UPDATE visit_alerts SET seen_at=? WHERE user_id=? AND seen_at=''", (db.now(), uid))


def pending_for(u):
    """Visits waiting for this person's approval."""
    if not u:
        return []
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT v.id, v.user_id, v.user_name, v.visited_at, v.department, v.approval, "
                                           "o.name AS office_name FROM visits v JOIN field_offices o ON o.id=v.office_id "
                                           "WHERE v.approval='pending' ORDER BY v.visited_at DESC")]
    return [r for r in rows if r["user_id"] != u["id"] and (is_admin(u) or is_management(u) or is_lead(u, r["department"]))]


# ------------------------------------------------------------------ checklists saved with a visit
def form_for(vid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM visit_forms WHERE visit_id=?", (vid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["data"] = checklists.loads(d["data"])
    return d


def forms_for(vids):
    if not vids:
        return {}
    with db.conn() as c:
        q = ",".join("?" * len(vids))
        rows = [dict(r) for r in c.execute(f"SELECT * FROM visit_forms WHERE visit_id IN ({q})", list(vids))]
    out = {}
    for r in rows:
        r["data"] = checklists.loads(r["data"])
        r["score"] = checklists.score(r["kind"], r["data"])
        r["title"] = checklists.FORMS[r["kind"]]["title"]
        out[r["visit_id"]] = r
    return out


def save_form(vid, kind, form, u):
    """Save (or clear, with kind '') the visit's checklist. Action-plan rows become Operations action items in Practice 360
    for the visit's practice, once each."""
    if not sees_forms(u):
        return None
    if not kind:
        with db.conn() as c:
            c.execute("DELETE FROM visit_forms WHERE visit_id=?", (vid,))
        return None
    data = checklists.from_form(kind, form)
    if data is None:
        return None
    old = form_for(vid)
    made = (old or {}).get("data", {}).get("made", {}) if old and old["kind"] == kind else {}
    data["made"] = made
    sc = checklists.score(kind, data)
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO visit_forms(visit_id, kind, data, combined, escalation, updated_at, updated_by) "
                  "VALUES (?,?,?,?,?,?,?)", (vid, kind, checklists.dumps(data), sc["combined"], data["escalation"], db.now(), u["name"]))
    _plan_to_p360(vid, kind, data, u)
    return sc


def _plan_to_p360(vid, kind, data, u):
    from . import field, p360
    v = field.visit(vid)
    if not v or not v.get("practice_id") or not data.get("plan"):
        return
    made = data.setdefault("made", {})
    changed = False
    for row in data["plan"]:
        key = (row["gap"] + "|" + row["action"]).lower()[:200]
        if key in made:
            continue
        title = (row["gap"] or row["action"])[:150]
        text = "\n".join(x for x in (f"Tactic / action: {row['action']}" if row["action"] else "",
                                       f"Goal: {row['goal']}" if row["goal"] else "", f"Owner: {row['owner']}" if row["owner"] else "",
                                       f"From the {checklists.FORMS[kind]['title'].lower()} at {v['office_name']} on {v['visited_at'][:10]}.") if x)
        due = row["due"] if len(row["due"]) == 10 and row["due"][4] == "-" else ""
        try:
            eid = p360.add_entry(v["practice_id"], checklists.DEPT, "action", title, text, u, due=due, force=True)
        except Exception:
            continue
        made[key] = eid
        changed = True
    if changed:
        with db.conn() as c:
            c.execute("UPDATE visit_forms SET data=? WHERE visit_id=?", (checklists.dumps(data), vid))
