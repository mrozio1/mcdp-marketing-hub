"""Calendar: events picked from Practice → Category → Type, sent as Outlook invites through Microsoft 365.

The subject is built from the picks ("Heights Orthodontics · Referral Visit: Cookie drop-off — Maple Street
Family Dental") and can be edited. Events are kept in the hub (so the Calendar shows them even before
Microsoft is connected, and with field follow-ups next to them); sent ones carry the Outlook event id and
the replies read back from it.
"""
import json
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta

from . import db

CATEGORIES = [
    {"key": "referral", "label": "Referral Visit", "hint": "dental offices that refer to us", "color": "#A86A12",
     "where": "referral", "extra": "What you're bringing", "cost": True, "types": [
        ("cookies", "Cookie / treat drop-off", "Cookie drop-off"), ("lunch", "Lunch drop-off", "Lunch drop-off"),
        ("media", "Dropping off physical media", "Physical media drop-off"), ("lunchlearn", "Lunch & learn", "Lunch & learn"),
        ("intro", "New office introduction", "New office introduction"),
        ("drintro", "Doctor-to-doctor introduction", "Doctor-to-doctor introduction"),
        ("followup", "Follow-up visit", "Follow-up visit")]},
    {"key": "office", "label": "Office Visit", "hint": "our own practices", "color": "#0F7471",
     "where": "ours", "extra": "What you're bringing or covering", "cost": True, "types": [
        ("checkin", "Marketing check-in", "Marketing check-in"),
        ("media", "Dropping off physical media", "Physical media drop-off"),
        ("training", "Staff training (reviews, social, front desk)", "Staff training"),
        ("refresh", "Lobby / display refresh", "Lobby refresh"), ("reviews", "Review push", "Review push")]},
    {"key": "media", "label": "Media Visit", "hint": "photo and video", "color": "#7A4E9C",
     "where": "ours", "extra": "Shot list / what to capture", "cost": True, "types": [
        ("photo", "Photoshoot", "Photoshoot"), ("film", "Filming (reels, social)", "Filming"),
        ("headshots", "Doctor headshots", "Doctor headshots"), ("team", "Team photos", "Team photos"),
        ("testimonial", "Patient testimonial", "Patient testimonial"),
        ("facility", "Office / facility shots", "Facility shots")]},
    {"key": "event", "label": "Event", "hint": "", "color": "#2E7D52",
     "where": "address", "extra": "Booth needs / registration link", "cost": True, "types": [
        ("coverage", "Event coverage", "Event coverage"), ("tradeshow", "Trade show", "Trade show"),
        ("ce", "CE event", "CE event"), ("studyclub", "Study club", "Study club"),
        ("community", "Community event / sponsorship", "Community event"),
        ("school", "School or sports event", "School / sports event"),
        ("openhouse", "Open house / patient appreciation", "Open house")]},
    {"key": "meeting", "label": "Meeting", "hint": "internal", "color": "#5B5852",
     "where": "online", "extra": "Agenda", "cost": False, "types": [
        ("review", "Practice marketing review", "Marketing review"), ("report", "Monthly report review", "Monthly report review"),
        ("planning", "Planning / strategy", "Planning"), ("vendor", "Vendor meeting", "Vendor meeting")]},
]
CAT = {c["key"]: c for c in CATEGORIES}
WHERE = [("referral", "Referring office"), ("ours", "One of our locations"), ("address", "Other address"),
         ("online", "Online (Teams)")]
REPEATS = [("", "Doesn't repeat"), ("weekly", "Weekly"), ("biweekly", "Every 2 weeks"), ("monthly", "Monthly")]
REMINDERS = [(15, "15 minutes before"), (60, "1 hour before"), (1440, "1 day before"), (-1, "No reminder")]
SHOW_AS = [("busy", "Busy"), ("oof", "Out of office"), ("tentative", "Tentative"), ("free", "Free")]
RESPONSES = {"accepted": ("Accepted", "y"), "tentativelyAccepted": ("Tentative", "t"), "declined": ("Declined", "d"),
             "organizer": ("Organizer", "o"), "none": ("No reply", "n"), "notResponded": ("No reply", "n"), "": ("No reply", "n")}
MAX_PEOPLE = 40
MAX_ATTACHMENT = 3 * 1024 * 1024    # Graph's limit for a simple upload
FOLLOWUP_COLOR = "#A86A12"
RSVP_MINUTES = 20


class CalError(ValueError):
    pass


def init():
    with db.conn() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS cal_events (
  id INTEGER PRIMARY KEY,
  practice_id INTEGER,
  category TEXT NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  subject_custom INTEGER DEFAULT 0,
  where_kind TEXT DEFAULT '',
  place_label TEXT DEFAULT '',
  place_address TEXT DEFAULT '',
  office_id INTEGER, npi_key TEXT DEFAULT '', place_id TEXT DEFAULT '',
  starts TEXT NOT NULL,
  ends TEXT NOT NULL,
  all_day INTEGER DEFAULT 0,
  repeat TEXT DEFAULT '', repeat_until TEXT DEFAULT '',
  reminder INTEGER DEFAULT 60,
  show_as TEXT DEFAULT 'busy',
  teams INTEGER DEFAULT 0,
  extra TEXT DEFAULT '',
  cost REAL,
  details TEXT DEFAULT '',
  private INTEGER DEFAULT 0,
  log_reminder INTEGER DEFAULT 1,
  attachment_name TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'saved',
  ms_id TEXT DEFAULT '', ms_link TEXT DEFAULT '', ms_txn TEXT DEFAULT '', ms_error TEXT DEFAULT '', ms_at TEXT DEFAULT '',
  rsvp_at TEXT DEFAULT '',
  visit_id INTEGER,
  created_by INTEGER, created_name TEXT DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cal_events_starts ON cal_events(starts);
CREATE TABLE IF NOT EXISTS cal_people (
  event_id INTEGER NOT NULL REFERENCES cal_events(id) ON DELETE CASCADE,
  pos INTEGER NOT NULL,
  name TEXT DEFAULT '',
  email TEXT NOT NULL,
  required INTEGER DEFAULT 1,
  source TEXT DEFAULT '',
  response TEXT DEFAULT '',
  PRIMARY KEY (event_id, pos)
);""")


# ------------------------------------------------------------------ helpers
def types_for(cat):
    return CAT.get(cat, {}).get("types", [])


def type_subject(cat, kind):
    return next((s for k, _, s in types_for(cat) if k == kind), "")


def type_label(cat, kind):
    return next((label for k, label, _ in types_for(cat) if k == kind), "")


def build_subject(practice_name, cat, kind, place=""):
    c = CAT.get(cat)
    if not c:
        return ""
    s = f"{practice_name} · {c['label']}: {type_subject(cat, kind)}" if practice_name else f"{c['label']}: {type_subject(cat, kind)}"
    return f"{s} — {place}" if place else s


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _people(form):
    names, emails, req, src = (form.get(k) or [] for k in ("person_name", "person_email", "person_required", "person_source"))
    out, seen = [], set()
    for i, em in enumerate(emails):
        em = (em or "").strip().lower()
        if not em:
            continue
        if not EMAIL.match(em):
            raise CalError(f"“{em}” isn't an email address.")
        if em in seen:
            continue
        seen.add(em)
        out.append({"name": ((names[i] if i < len(names) else "") or "").strip()[:120], "email": em[:160],
                    "required": 0 if i < len(req) and req[i] == "0" else 1,
                    "source": ((src[i] if i < len(src) else "") or "")[:30]})
    if len(out) > MAX_PEOPLE:
        raise CalError(f"Invite up to {MAX_PEOPLE} people per event.")
    return out


def _dt(d, t):
    try:
        return datetime.fromisoformat(f"{d}T{t or '00:00'}")
    except (TypeError, ValueError):
        raise CalError("Pick a date and a start and end time.")


def _money(s):
    s = (s or "").replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        v = round(float(s), 2)
    except ValueError:
        raise CalError("The estimated cost should be a number, like 45 or 45.00.")
    if v < 0 or v > 100000:
        raise CalError("The estimated cost looks off.")
    return v


def parse(form, practices):
    """form: {field: str} plus lists for person_*; practices: list of practice dicts. Returns (event dict, people)."""
    def g(k):
        v = form.get(k)
        return (v if isinstance(v, str) else "" if v is None else str(v)).strip()
    pid = g("practice_id")
    p = next((q for q in practices if str(q["id"]) == str(pid)), None)
    if not p:
        raise CalError("Pick which practice this is for.")
    cat, kind = g("category"), g("kind")
    if cat not in CAT:
        raise CalError("Pick a category.")
    if kind not in {k for k, _, _ in types_for(cat)}:
        raise CalError("Pick the type of " + CAT[cat]["label"].lower() + ".")
    where = g("where_kind") or CAT[cat]["where"]
    if where not in dict(WHERE):
        where = CAT[cat]["where"]
    all_day = g("all_day") in ("1", "on", "true")
    day = g("date")
    start = _dt(day, "00:00" if all_day else g("start"))
    end = _dt(g("end_date") or day, "00:00" if all_day else g("end"))
    if not all_day and end <= start:
        raise CalError("The end time should be after the start time.")
    if all_day and end < start:
        raise CalError("The last day should be on or after the first day.")
    if (end - start).days > 31:
        raise CalError("An event can't be longer than a month. Use Repeat for regular ones.")
    rep = g("repeat")
    if rep not in dict(REPEATS):
        rep = ""
    until = g("repeat_until") if rep else ""
    if until:
        try:
            if date.fromisoformat(until) < start.date():
                raise CalError("“Repeat until” should be after the first date.")
        except ValueError:
            raise CalError("Pick a valid “Repeat until” date.")
    try:
        reminder = int(g("reminder") or 60)
    except ValueError:
        reminder = 60
    show_as = g("show_as") if g("show_as") in dict(SHOW_AS) else "busy"
    label = g("place_label")[:200]
    address = g("place_address")[:300]
    if where in ("referral", "ours") and not label:
        raise CalError("Pick the office." if where == "referral" else "Pick which of our locations.")
    if where == "address" and not (label or address):
        raise CalError("Add the address or the venue name.")
    auto = build_subject(p["name"], cat, kind, label if where != "online" else "")
    subject = g("subject")[:255]
    custom = bool(subject) and subject != auto and g("subject_custom") == "1"
    ev = {"practice_id": p["id"], "category": cat, "kind": kind, "subject": subject if custom else auto,
          "subject_custom": int(custom), "where_kind": where, "place_label": label if where != "online" else "",
          "place_address": address if where != "online" else "",
          "office_id": int(g("office_id")) if str(g("office_id")).isdigit() and where == "referral" else None,
          "npi_key": g("npi_key")[:80] if where == "referral" else "", "place_id": g("place_id")[:300] if where == "referral" else "",
          "starts": start.isoformat(timespec="minutes"), "ends": end.isoformat(timespec="minutes"),
          "all_day": int(all_day), "repeat": rep, "repeat_until": until, "reminder": reminder, "show_as": show_as,
          "teams": int(where == "online" or g("teams") in ("1", "on")),
          "extra": g("extra")[:500], "cost": _money(g("cost")) if CAT[cat]["cost"] else None,
          "details": g("details")[:4000], "private": int(g("private") in ("1", "on")),
          "log_reminder": int(g("log_reminder") in ("1", "on"))}
    return ev, _people(form)


def draft(form, old=None):
    """The form as typed, to show again with an error (nothing checked)."""
    g = lambda k: str(form.get(k) or "").strip()
    pid = g("practice_id")
    d = g("date") or date.today().isoformat()
    names, emails, req, src = (form.get(k) or [] for k in ("person_name", "person_email", "person_required", "person_source"))
    ev = dict(old or {})
    ev.update({"id": (old or {}).get("id"), "practice_id": int(pid) if pid.isdigit() else None, "category": g("category") or "referral",
               "kind": g("kind"), "subject": g("subject"), "subject_custom": int(g("subject_custom") == "1"),
               "where_kind": g("where_kind"), "place_label": g("place_label"), "place_address": g("place_address"),
               "office_id": int(g("office_id")) if g("office_id").isdigit() else None, "npi_key": g("npi_key"),
               "place_id": g("place_id"), "starts": f"{d}T{g('start') or '10:00'}", "ends": f"{g('end_date') or d}T{g('end') or '10:45'}",
               "all_day": int(g("all_day") in ("1", "on")), "repeat": g("repeat"), "repeat_until": g("repeat_until"),
               "reminder": int(g("reminder")) if g("reminder").lstrip("-").isdigit() else 60, "show_as": g("show_as") or "busy",
               "teams": int(g("teams") in ("1", "on")), "extra": g("extra"), "cost": g("cost") or None, "details": g("details"),
               "private": int(g("private") in ("1", "on")), "log_reminder": int(g("log_reminder") in ("1", "on")),
               "status": (old or {}).get("status", "new"), "ms_id": (old or {}).get("ms_id", "")})
    ev["people"] = [{"name": names[i] if i < len(names) else "", "email": e, "required": 0 if i < len(req) and req[i] == "0" else 1,
                     "source": src[i] if i < len(src) else ""} for i, e in enumerate(emails) if e]
    return ev


# ------------------------------------------------------------------ storage
COLS = ("practice_id", "category", "kind", "subject", "subject_custom", "where_kind", "place_label", "place_address",
        "office_id", "npi_key", "place_id", "starts", "ends", "all_day", "repeat", "repeat_until", "reminder", "show_as",
        "teams", "extra", "cost", "details", "private", "log_reminder")


def save(ev, people, user, eid=None):
    now = db.now()
    with db.conn() as c:
        if eid:
            c.execute(f"UPDATE cal_events SET {', '.join(k + '=?' for k in COLS)}, updated_at=? WHERE id=?",
                      [ev[k] for k in COLS] + [now, eid])
            old = {r["email"]: r["response"] for r in c.execute("SELECT email, response FROM cal_people WHERE event_id=?", (eid,))}
            c.execute("DELETE FROM cal_people WHERE event_id=?", (eid,))
        else:
            cur = c.execute(f"INSERT INTO cal_events({', '.join(COLS)}, ms_txn, created_by, created_name, created_at, updated_at) "
                            f"VALUES ({', '.join('?' * len(COLS))}, ?, ?, ?, ?, ?)",
                            [ev[k] for k in COLS] + [str(uuid.uuid4()), (user or {}).get("id"), (user or {}).get("name", ""), now, now])
            eid, old = cur.lastrowid, {}
        c.executemany("INSERT INTO cal_people(event_id, pos, name, email, required, source, response) VALUES (?,?,?,?,?,?,?)",
                      [(eid, i, p["name"], p["email"], p["required"], p.get("source", ""), old.get(p["email"], ""))
                       for i, p in enumerate(people)])
    return eid


def get(eid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM cal_events WHERE id=?", (eid,)).fetchone()
        if not r:
            return None
        ev = dict(r)
        ev["people"] = [dict(p) for p in c.execute("SELECT * FROM cal_people WHERE event_id=? ORDER BY pos", (eid,))]
    return decorate(ev)


def decorate(ev):
    c = CAT.get(ev["category"], {})
    ev["category_label"] = c.get("label", ev["category"])
    ev["color"] = c.get("color", "#5B5852")
    ev["type_label"] = type_label(ev["category"], ev["kind"])
    ev["type_subject"] = type_subject(ev["category"], ev["kind"])
    ev["extra_label"] = c.get("extra", "Details")
    ev["when_text"] = when_text(ev)
    ev["repeat_text"] = repeat_text(ev)
    for p in ev.get("people", []):
        p["response_label"], p["response_cls"] = RESPONSES.get(p.get("response") or "", RESPONSES[""])
    return ev


def _clock(t):
    h, m = int(t[:2]), int(t[3:5])
    return f"{h % 12 or 12}:{m:02d}" + (" AM" if h < 12 else " PM")


def when_text(ev):
    s, e = datetime.fromisoformat(ev["starts"]), datetime.fromisoformat(ev["ends"])
    day = f"{s:%a}, {s:%b} {s.day}"
    if ev.get("all_day"):
        return day + (" · all day" if s.date() == e.date() else f" – {e:%a}, {e:%b} {e.day} · all day")
    a, b = _clock(ev["starts"][11:16]), _clock(ev["ends"][11:16])
    if a[-2:] == b[-2:] and s.date() == e.date():
        a = a[:-3]
    return f"{day} · {a}–{b}" if s.date() == e.date() else f"{day} {a} – {e:%a}, {e:%b} {e.day} {b}"


def repeat_text(ev):
    r = dict(REPEATS).get(ev.get("repeat") or "", "")
    if not ev.get("repeat"):
        return ""
    return r + (f" until {date.fromisoformat(ev['repeat_until']):%b %-d, %Y}" if ev.get("repeat_until") else "")


def set_status(eid, **cols):
    with db.conn() as c:
        c.execute(f"UPDATE cal_events SET {', '.join(k + '=?' for k in cols)} WHERE id=?", list(cols.values()) + [eid])


def delete(eid):
    with db.conn() as c:
        c.execute("DELETE FROM cal_people WHERE event_id=?", (eid,))
        c.execute("DELETE FROM cal_events WHERE id=?", (eid,))


def can_change(ev, user):
    from . import users
    return bool(user) and (ev.get("created_by") == user.get("id") or users.rank(user["role"]) >= users.rank("editor"))


def visible(ev, user):
    """Private events: details only for whoever made it, the people invited, and admins."""
    if not ev.get("private") or not user:
        return True
    em = (user.get("email") or "").lower()
    return user.get("role") == "admin" or ev.get("created_by") == user.get("id") or any(p["email"] == em for p in ev.get("people", []))


# ------------------------------------------------------------------ Microsoft
def send(eid, attachment=None):
    """Create or update the Outlook event. Returns '' or the error."""
    from . import microsoft as ms
    ev = get(eid)
    if not ev:
        return "That event is gone."
    if not ms.connected():
        set_status(eid, status="saved", ms_error="Microsoft 365 isn't connected yet; saved in the hub only.")
        return "Microsoft 365 isn't connected yet, so it's saved in the hub only. An admin can connect it on the Connections page."
    if not ev["people"]:
        set_status(eid, status="saved")
    try:
        if ev["ms_id"]:
            link = ms.update_event(ev["ms_id"], ev, ev["people"]) or ev["ms_link"]
            set_status(eid, status="sent", ms_link=link, ms_error="", ms_at=db.now())
        else:
            try:
                ms_id, link = ms.create_event(ev, ev["people"], attachment)
            except ms.MSError as e:
                if ev["teams"] and "online" in str(e).lower():   # the mailbox can't host Teams meetings: send without it
                    ev["teams"] = 0
                    ms_id, link = ms.create_event(ev, ev["people"], attachment)
                    set_status(eid, teams=0)
                else:
                    raise
            set_status(eid, status="sent", ms_id=ms_id, ms_link=link, ms_error="", ms_at=db.now(),
                       attachment_name=attachment[0] if attachment else "")
        with db.conn() as c:
            c.execute("UPDATE cal_people SET response='organizer' WHERE event_id=? AND email=?", (eid, ms.mailbox().lower()))
        return ""
    except ms.MSError as e:
        set_status(eid, status="error", ms_error=str(e)[:500], ms_at=db.now())
        return str(e)


def cancel(eid, comment=""):
    from . import microsoft as ms
    ev = get(eid)
    if ev and ev["ms_id"] and ms.connected():
        ms.cancel_event(ev["ms_id"], comment)
    set_status(eid, status="cancelled", ms_at=db.now())


def refresh_replies(eid):
    from . import microsoft as ms
    ev = get(eid)
    if not ev or not ev["ms_id"] or not ms.connected() or ev["status"] == "cancelled":
        return
    got, link, cancelled = ms.responses(ev["ms_id"])
    with db.conn() as c:
        for p in ev["people"]:
            if p["email"] in got:
                c.execute("UPDATE cal_people SET response=? WHERE event_id=? AND email=?", (got[p["email"]], eid, p["email"]))
        c.execute("UPDATE cal_events SET rsvp_at=?, ms_link=COALESCE(NULLIF(?, ''), ms_link) WHERE id=?", (db.now(), link, eid))
        if cancelled:
            c.execute("UPDATE cal_events SET status='cancelled' WHERE id=?", (eid,))


def refresh_upcoming(limit=100):
    today = date.today()
    with db.conn() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM cal_events WHERE ms_id != '' AND status='sent' AND ends >= ? AND starts <= ? "
            "ORDER BY starts LIMIT ?", ((today - timedelta(days=1)).isoformat(), (today + timedelta(days=90)).isoformat(), limit))]
    for i in ids:
        try:
            refresh_replies(i)
        except Exception:
            continue


def refresh_soon(eid):
    """Replies for one event in the background when someone opens it (at most every few minutes)."""
    ev = get(eid)
    if not ev or not ev["ms_id"]:
        return
    if ev["rsvp_at"] and datetime.now() - datetime.fromisoformat(ev["rsvp_at"]) < timedelta(minutes=3):
        return
    threading.Thread(target=lambda: _quiet(refresh_replies, eid), name="cal-rsvp", daemon=True).start()


def _quiet(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass


def start_scheduler():
    from . import microsoft as ms

    def loop():
        time.sleep(180)
        while True:
            if ms.connected():
                _quiet(refresh_upcoming)
                _quiet(ms.keep_alive)
            time.sleep(RSVP_MINUTES * 60)
    threading.Thread(target=loop, name="calendar-replies", daemon=True).start()


# ------------------------------------------------------------------ what's on
def occurrences(ev, start, end):
    """Start dates of an event (and its repeats) that fall in [start, end]."""
    d0 = date.fromisoformat(ev["starts"][:10])
    last = date.fromisoformat(ev["repeat_until"]) if ev.get("repeat_until") else None
    out, d, n = [], d0, 0
    while d <= end and n < 800:
        if last and d > last:
            break
        if d >= start:
            out.append(d)
        if not ev.get("repeat"):
            break
        n += 1
        if ev["repeat"] == "monthly":
            m = d0.month - 1 + n
            y, mo = d0.year + m // 12, m % 12 + 1
            import calendar as _c
            d = date(y, mo, min(d0.day, _c.monthrange(y, mo)[1]))
        else:
            d = d + timedelta(days=14 if ev["repeat"] == "biweekly" else 7)
    return out


def items_between(start, end, user, practice_id=None, category="", mine=False, followups=True):
    """Calendar entries for [start, end]: events (repeats expanded, multi-day ones on each day) and field follow-ups."""
    em = ((user or {}).get("email") or "").lower()
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM cal_events WHERE (starts <= ? AND (ends >= ? OR repeat != '')) ORDER BY starts",
            ((end + timedelta(days=1)).isoformat(), start.isoformat()))]
        people = {}
        for p in c.execute("SELECT * FROM cal_people WHERE event_id IN (%s) ORDER BY pos" % ",".join(str(r["id"]) for r in rows)
                           if rows else "SELECT * FROM cal_people WHERE 0"):
            people.setdefault(p["event_id"], []).append(dict(p))
        names = {p["id"]: p["name"] for p in db.practices()}
    out = []
    for ev in rows:
        ev["people"] = people.get(ev["id"], [])
        decorate(ev)
        if practice_id and ev["practice_id"] != practice_id:
            continue
        if category and ev["category"] != category:
            continue
        if mine and not (ev["created_by"] == (user or {}).get("id") or any(p["email"] == em for p in ev["people"])):
            continue
        ev["practice_name"] = names.get(ev["practice_id"], "")
        show = visible(ev, user)
        span = (date.fromisoformat(ev["ends"][:10]) - date.fromisoformat(ev["starts"][:10])).days
        for d in occurrences(ev, start - timedelta(days=max(span, 0)), end):
            for k in range(span + 1):
                day = d + timedelta(days=k)
                if start <= day <= end:
                    out.append({"day": day, "time": "" if ev["all_day"] or k else ev["starts"][11:16],
                                "end_time": "" if ev["all_day"] else ev["ends"][11:16],
                                "title": ev["subject"] if show else "Busy (private)",
                                "short": (f"{ev['type_subject']} · {ev['place_label']}" if show and ev['place_label'] and ev['place_label'] != ev['practice_name']
                                          else ev['type_subject'] if show else "Busy"),
                                "practice": ev["practice_name"], "color": ev["color"], "category": ev["category"],
                                "id": ev["id"], "kind": "event", "cancelled": ev["status"] == "cancelled",
                                "people": len(ev["people"]), "status": ev["status"], "private": not show,
                                "accepted": sum(1 for p in ev["people"] if p.get("response") == "accepted")})
    if followups:
        out += followups_between(start, end, user, practice_id, mine)
    out.sort(key=lambda i: (i["day"], i["time"] == "" and i["kind"] == "event" and 0 or 1, i["time"] or "99", i["title"]))
    return out


def followups_between(start, end, user, practice_id=None, mine=False):
    from . import field
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT v.id, v.office_id, v.user_id, v.user_name, v.followup, v.practice_id, o.our_office_id, o.name AS office_name "
            "FROM visits v JOIN field_offices o ON o.id=v.office_id WHERE v.followup != '' AND v.followup BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()))]
    links = field.practice_links() if rows else {}
    out = []
    for r in rows:
        pid = r.get("practice_id") or links.get(r.get("our_office_id"))
        if practice_id and pid != practice_id:
            continue
        if mine and r["user_id"] != (user or {}).get("id"):
            continue
        out.append({"day": date.fromisoformat(r["followup"][:10]), "time": "", "end_time": "",
                    "title": f"Follow up · {r['office_name']}", "short": f"Follow up · {r['office_name']}",
                    "practice": "", "color": FOLLOWUP_COLOR, "category": "followup", "id": r["id"], "kind": "followup",
                    "office_id": r["office_id"], "who": r["user_name"], "cancelled": False, "people": 0, "status": "",
                    "private": False, "accepted": 0})
    return out


def upcoming(user, n=6, practice_id=None, mine=False):
    today = date.today()
    items = [i for i in items_between(today, today + timedelta(days=45), user, practice_id, "", mine, followups=False)
             if not i["cancelled"]]
    seen, out = set(), []
    for i in items:
        if (i["id"], i["day"]) in seen:
            continue
        seen.add((i["id"], i["day"]))
        out.append(i)
    return out[:n]


def month_grid(year, month):
    import calendar as _c
    first = date(year, month, 1)
    start = first - timedelta(days=(first.weekday() + 1) % 7)      # weeks start on Sunday
    last = date(year, month, _c.monthrange(year, month)[1])
    end = last + timedelta(days=(5 - last.weekday()) % 7)            # ...and end on Saturday
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return start, end, [days[i:i + 7] for i in range(0, len(days), 7)]


# ------------------------------------------------------------------ lookups for the form
def people_search(q, user=None):
    """Hub team and contacts met on field visits, by name or email."""
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    out, seen = [], set()
    with db.conn() as c:
        for r in c.execute("SELECT name, email FROM users WHERE active=1 AND (lower(name) LIKE ? OR lower(email) LIKE ?) "
                           "ORDER BY name LIMIT 8", (like, like)):
            out.append({"name": r["name"], "email": r["email"].lower(), "role": "Hub team", "source": "team"})
        for r in c.execute("SELECT p.name, p.email, p.role, o.name AS office FROM visit_people p JOIN visits v ON v.id=p.visit_id "
                           "JOIN field_offices o ON o.id=v.office_id WHERE p.email != '' AND (lower(p.name) LIKE ? OR "
                           "lower(p.email) LIKE ?) ORDER BY v.visited_at DESC LIMIT 20", (like, like)):
            out.append({"name": r["name"], "email": r["email"].lower(), "role": " · ".join(x for x in (r["office"], r["role"]) if x),
                        "source": "contact"})
    res = []
    for p in out:
        if p["email"] not in seen:
            seen.add(p["email"])
            res.append(p)
    return res[:12]


def office_contacts(office_id=None, npi_key="", place_id=""):
    """People met at an office on earlier visits (with an email), newest first."""
    with db.conn() as c:
        ids = []
        if office_id:
            ids = [office_id]
        else:
            ids = [r["id"] for r in c.execute("SELECT id FROM field_offices WHERE (npi_key=? AND ?!='') OR (place_id=? AND ?!='')",
                                              (npi_key, npi_key, place_id, place_id))]
        if not ids:
            return []
        rows = c.execute("SELECT p.name, p.email, p.role FROM visit_people p JOIN visits v ON v.id=p.visit_id "
                         f"WHERE v.office_id IN ({','.join('?' * len(ids))}) AND p.email != '' ORDER BY v.visited_at DESC", ids)
        out, seen = [], set()
        for r in rows:
            if r["email"].lower() not in seen:
                seen.add(r["email"].lower())
                out.append({"name": r["name"], "email": r["email"].lower(), "role": r["role"], "source": "contact"})
    return out[:8]


def office_search(q):
    """Referring offices: ones visited before, then the Map's dental practices."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    like = f"%{q.lower()}%"
    out, seen = [], set()
    with db.conn() as c:
        for r in c.execute("SELECT o.id, o.name, o.address, o.npi_key, o.place_id, MAX(v.visited_at) AS last FROM field_offices o "
                           "LEFT JOIN visits v ON v.office_id=o.id WHERE lower(o.name) LIKE ? GROUP BY o.id ORDER BY last DESC LIMIT 8",
                           (like,)):
            out.append({"label": r["name"], "address": r["address"] or "", "office_id": r["id"], "npi_key": r["npi_key"] or "",
                        "place_id": r["place_id"] or "", "note": f"Last visit {r['last'][:10]}" if r["last"] else "Visited office"})
            seen.update(k for k in (r["npi_key"], r["place_id"]) if k)
        try:
            ref = c.execute("SELECT key, name, address, city, state, zip, place_id FROM ref_practices WHERE lower(name) LIKE ? "
                            "AND ours=0 AND has_ortho=0 ORDER BY name LIMIT 12", (like,)).fetchall()
        except Exception:   # the Map's offices table isn't there in a fresh install
            ref = []
        for r in ref:
            if r["key"] in seen or (r["place_id"] and r["place_id"] in seen):
                continue
            addr = ", ".join(x for x in (r["address"], r["city"], " ".join(y for y in (r["state"], r["zip"]) if y)) if x)
            out.append({"label": r["name"], "address": addr, "office_id": None, "npi_key": r["key"], "place_id": r["place_id"] or "",
                        "note": "From the Map"})
    return out[:12]


def our_locations(practice_id):
    with db.conn() as c:
        try:
            rows = c.execute("SELECT title, address FROM gbp_locations WHERE practice_id=? ORDER BY address", (practice_id,)).fetchall()
        except Exception:
            rows = []
    p = db.practice(practice_id) or {}
    return [{"label": r["title"] or p.get("name", ""), "address": r["address"] or ""} for r in rows]


def form_json():
    """What the form's script needs to build dropdowns and the subject."""
    return json.dumps({c["key"]: {"label": c["label"], "where": c["where"], "extra": c["extra"], "cost": c["cost"],
                                  "color": c["color"], "types": [[k, label, s] for k, label, s in c["types"]]}
                       for c in CATEGORIES})
