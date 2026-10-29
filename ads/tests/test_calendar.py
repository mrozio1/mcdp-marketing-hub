"""Calendar: Practice → Category → Type events, the built subject, Outlook invites through a (fake) Microsoft Graph,
replies, cancelling, repeats, field follow-ups, who can schedule, and "Log this visit"."""
import importlib
import json
import os
import re
from datetime import date, timedelta

import pytest

MAILBOX = "marketing-events@mcdentalpartners.com"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
    from app import ai, cal, connectors, db, field, microsoft, places, referrals, users
    for m in (db, users, connectors, places, referrals, ai, field, cal, microsoft):
        importlib.reload(m)
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    def person(admin, name, email, role, field_on=False):
        r = admin.post("/team/add", data={"name": name, "email": email, "role": role, "field_on": "1" if field_on else "",
                                          "social_on": "1"})
        tok = re.search(r"/setup/([\w-]+)", r.text).group(1)
        c = TestClient(main.app)
        c.post(f"/setup/{tok}", data={"password": "long-password-1", "confirm": "long-password-1"})
        return c

    with TestClient(main.app) as boss:
        boss.post("/login", data={"password": "shared-pw"})
        admin = person(boss, "Ann Admin", "ann@example.com", "admin")
        agent = person(admin, "Abe Agent", "abe@example.com", "viewer", field_on=True)
        viewer = person(admin, "Vi Viewer", "vi@example.com", "viewer")
        master = db.add_practice("MCDP Ads", 5000, 1000, "")
        db.set_practice_accounts(master, "111", "999")
        heights = db.add_practice("Heights Orthodontics", 1000, 500, "heights")
        db.set_practice_accounts(heights, "", "")
        places.add_office("Heights – Sterling", "ChIJ_office_heights01", 42.58, -83.03)
        with db.conn() as c:
            c.execute("INSERT INTO gbp_locations(name, title, address, practice_id, rating, total, full_at) VALUES "
                      "('accounts/1/locations/9', 'Heights Orthodontics', '43767 Van Dyke Ave, Sterling Heights, MI', ?, 4.8, 200, '')",
                      (heights,))
        oid = field.ensure_office(name="Maple Street Family Dental", address="12 Maple St, Sterling Heights, MI", lat=42.6, lng=-83.0)["id"]
        yield {"main": main, "db": db, "cal": cal, "ms": microsoft, "field": field, "admin": admin, "agent": agent,
               "viewer": viewer, "heights": heights, "oid": oid}


def form(heights, **kw):
    d = (date.today() + timedelta(days=2)).isoformat()
    data = {"practice_id": str(heights), "category": "referral", "kind": "cookies", "where_kind": "referral",
            "place_label": "Maple Street Family Dental", "place_address": "12 Maple St, Sterling Heights, MI",
            "date": d, "start": "10:00", "end": "10:45", "repeat": "", "reminder": "60", "show_as": "busy", "teams": "0",
            "extra": "2 dozen cookies, 50 referral pads", "cost": "$45", "details": "Park in the side lot.",
            "log_reminder": "1", "action": "send"}
    data.update(kw)
    return data


class FakeGraph:
    def __init__(self):
        self.calls, self.n, self.responses = [], 0, {}

    def __call__(self, method, path, body=None, params=None, headers=None):
        self.calls.append((method, path, body))
        if method == "POST" and path.endswith("/events"):
            self.n += 1
            return {"id": f"AAMk{self.n}", "webLink": f"https://outlook.office365.com/owa/?itemid=AAMk{self.n}"}
        if method == "GET" and "/events/" in path:
            return {"attendees": [{"emailAddress": {"address": e}, "status": {"response": r}} for e, r in self.responses.items()],
                    "webLink": "", "isCancelled": False}
        if method == "POST" and path.endswith("/calendar/getSchedule"):
            return {"value": [{"scheduleId": "abe@example.com", "scheduleItems": [{"status": "busy"}]},
                              {"scheduleId": "pam@maple.com", "error": {"message": "not found"}}]}
        if method == "GET" and path == "/users":
            return {"value": [{"displayName": "Dr. Rivera", "mail": "rivera@mcdentalpartners.com", "jobTitle": "Orthodontist"}]}
        return {}


def test_calendar_page_and_who_can_schedule(env):
    a, v, ag = env["admin"], env["viewer"], env["agent"]
    page = v.get("/calendar").text
    assert "Calendar" in page and 'href="/calendar"' in page and "+ New event" not in page
    assert v.get("/calendar/new", follow_redirects=True).text.count("needs editor access or field visits") == 1
    assert "Practice" in ag.get("/calendar/new").text            # field agents can schedule
    f = a.get("/calendar/new").text
    assert "Referral Visit" in f and "Cookie / treat drop-off" in f and "Heights Orthodontics" in f
    assert "MCDP Ads" not in re.search(r'id="f-practice".*?</select>', f, re.S).group(0)   # the master record isn't a practice
    assert "Microsoft 365 isn't connected yet" in f


def test_save_without_microsoft_builds_subject(env):
    a, cal, h = env["admin"], env["cal"], env["heights"]
    r = a.post("/calendar/events", data={**form(h, office_id=str(env["oid"])), "person_name": ["Pam Beesly"],
                                         "person_email": ["Pam@Maple.com"], "person_required": ["1"], "person_source": ["contact"]},
               follow_redirects=True)
    assert "Microsoft 365 isn&#39;t connected" in r.text
    with env["db"].conn() as c:
        eid = c.execute("SELECT MAX(id) FROM cal_events").fetchone()[0]
    ev = cal.get(eid)
    assert ev["subject"] == "Heights Orthodontics · Referral Visit: Cookie drop-off — Maple Street Family Dental"
    assert ev["status"] == "saved" and ev["cost"] == 45 and ev["people"][0]["email"] == "pam@maple.com"
    page = a.get(f"/calendar?event={eid}&d={ev['starts'][:10]}").text
    assert "Cookie drop-off · Maple Street Family Dental" in page and "Saved in the hub only" in page
    assert "Bringing" in page and "$45.00" in page


def test_validation(env):
    a, h = env["admin"], env["heights"]
    assert "Pick which practice" in a.post("/calendar/events", data=form(h, practice_id=""), follow_redirects=True).text
    assert "Pick the type" in a.post("/calendar/events", data=form(h, kind="photo"), follow_redirects=True).text
    assert "end time should be after" in a.post("/calendar/events", data=form(h, end="09:00"), follow_redirects=True).text
    r = a.post("/calendar/events", data={**form(h), "person_name": ["x"], "person_email": ["not-an-email"]}, follow_redirects=True)
    assert "an email address" in r.text and "2 dozen cookies" in r.text      # what was typed comes back
    assert "Pick the office" in a.post("/calendar/events", data=form(h, place_label=""), follow_redirects=True).text


def test_send_invite_replies_update_and_cancel(env, monkeypatch):
    a, cal, ms, db, h = env["admin"], env["cal"], env["ms"], env["db"], env["heights"]
    g = FakeGraph()
    monkeypatch.setattr(ms, "graph", g)
    db.set_setting("ms_refresh_token", "rt")
    db.set_setting("ms_user", json.dumps({"name": "Mark Johnson", "email": "mjohnson@mcdentalpartners.com"}))
    db.set_setting("ms_mailbox", MAILBOX)
    r = a.post("/calendar/events", data={**form(h), "person_name": ["Abe Agent", "Pam Beesly", "Dr. Joe Oak"],
                                         "person_email": ["abe@example.com", "pam@maple.com", "joe@maple.com"],
                                         "person_required": ["1", "1", "0"], "person_source": ["team", "contact", ""]},
               follow_redirects=True)
    assert f"Invite sent to 3 people from {MAILBOX}" in r.text
    method, path, body = next(c for c in g.calls if c[0] == "POST" and c[1].endswith("/events"))
    assert method == "POST" and path == f"/users/{MAILBOX.replace('@', '%40')}/events"
    assert body["subject"].startswith("Heights Orthodontics · Referral Visit: Cookie drop-off")
    assert body["start"] == {"dateTime": form(h)["date"] + "T10:00:00", "timeZone": "America/Detroit"}
    assert [x["type"] for x in body["attendees"]] == ["required", "required", "optional"]
    assert body["transactionId"] and body["reminderMinutesBeforeStart"] == 60 and body["categories"] == ["Referral Visit"]
    assert "2 dozen cookies" in body["body"]["content"] and "Scheduled from MCDP Hub" in body["body"]["content"]
    with db.conn() as c:
        eid = c.execute("SELECT MAX(id) FROM cal_events").fetchone()[0]
    ev = cal.get(eid)
    assert ev["status"] == "sent" and ev["ms_id"] == "AAMk1"

    # replies come back from Outlook
    g.responses = {"abe@example.com": "accepted", "pam@maple.com": "tentativelyAccepted"}
    cal.refresh_replies(eid)
    page = a.get(f"/calendar?event={eid}&d={ev['starts'][:10]}").text
    assert "Accepted" in page and "Tentative" in page and "No reply" in page and "Open in Outlook" in page

    # editing sends an update (PATCH), keeping the replies
    a.post(f"/calendar/{eid}", data={**form(h, start="11:00", end="11:45"), "person_name": ["Abe Agent", "Pam Beesly"],
                                     "person_email": ["abe@example.com", "pam@maple.com"], "person_required": ["1", "1"]})
    patch = [c for c in g.calls if c[0] == "PATCH"][-1]
    assert patch[2]["start"]["dateTime"].endswith("T11:00:00")
    assert cal.get(eid)["people"][0]["response"] == "accepted"

    # free/busy and directory lookups
    fb = a.get("/calendar/lookup/freebusy", params={"emails": "abe@example.com,pam@maple.com",
                                                    "start": "2026-11-02T10:00", "end": "2026-11-02T11:00"}).json()
    assert fb == {"ok": True, "people": {"abe@example.com": "busy", "pam@maple.com": "unknown"}}
    ppl = a.get("/calendar/lookup/people", params={"q": "ri"}).json()["people"]
    assert any(p["email"] == "rivera@mcdentalpartners.com" for p in ppl)

    # cancelling tells everyone; then it can be removed
    a.post(f"/calendar/{eid}/delete")
    assert cal.get(eid)                       # a sent event must be cancelled first
    a.post(f"/calendar/{eid}/cancel", data={"comment": ""})
    assert any(c[0] == "POST" and c[1].endswith("/events/AAMk1/cancel") for c in g.calls) and cal.get(eid)["status"] == "cancelled"
    a.post(f"/calendar/{eid}/delete")
    assert cal.get(eid) is None

    # a Graph failure keeps the event, shows why, and can be retried
    def broken(*args, **kw):
        raise ms.MSError("ErrorAccessDenied (HTTP 403)")
    monkeypatch.setattr(ms, "graph", broken)
    r = a.post("/calendar/events", data={**form(h), "person_email": ["abe@example.com"], "person_name": ["Abe"]}, follow_redirects=True)
    assert "Outlook didn&#39;t get it" in r.text
    with db.conn() as c:
        eid = c.execute("SELECT MAX(id) FROM cal_events").fetchone()[0]
    assert cal.get(eid)["status"] == "error" and "Try again" in a.get(f"/calendar?event={eid}").text
    monkeypatch.setattr(ms, "graph", g)
    a.post(f"/calendar/{eid}/retry")
    assert cal.get(eid)["status"] == "sent"
    for k in ("ms_refresh_token", "ms_user", "ms_mailbox"):
        db.set_setting(k, None)


def test_repeats_followups_and_month(env):
    cal, field, a = env["cal"], env["field"], env["admin"]
    ev = {"starts": "2026-11-02T09:00", "repeat": "weekly", "repeat_until": ""}
    assert cal.occurrences(ev, date(2026, 11, 1), date(2026, 11, 30)) == [date(2026, 11, d) for d in (2, 9, 16, 23, 30)]
    ev = {"starts": "2026-01-31T09:00", "repeat": "monthly", "repeat_until": "2026-04-30"}
    assert cal.occurrences(ev, date(2026, 1, 1), date(2026, 12, 31)) == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
                                                                          date(2026, 4, 30)]
    start, end, weeks = cal.month_grid(2026, 10)
    assert start == date(2026, 9, 27) and end == date(2026, 10, 31) and len(weeks) == 5
    d = (date.today() + timedelta(days=3)).isoformat()
    with env["db"].conn() as c:
        c.execute("INSERT INTO visits(office_id, user_id, user_name, visited_at, followup, created_at) VALUES (?, 1, 'Abe Agent', ?, ?, ?)",
                  (env["oid"], date.today().isoformat() + "T09:00", d, env["db"].now()))
    page = a.get(f"/calendar?d={d}").text
    assert "Follow up · Maple Street Family Dental" in page
    assert "Follow up · Maple" not in a.get(f"/calendar?d={d}&fu=0").text
    assert "Week of" in a.get(f"/calendar?view=week&d={d}").text
    assert "Follow up · Maple Street Family Dental" in a.get(f"/calendar?view=list&d={date.today().isoformat()}").text


def test_log_this_visit_and_lookups(env):
    a, ag, cal, h, db = env["admin"], env["agent"], env["cal"], env["heights"], env["db"]
    ag.post("/calendar/events", data={**form(h, office_id=str(env["oid"]), action="save"),
                                      "person_name": ["Abe Agent", "Pam Beesly"], "person_email": ["abe@example.com", "pam@maple.com"],
                                      "person_required": ["1", "1"], "person_source": ["team", "contact"]})
    with db.conn() as c:
        eid = c.execute("SELECT MAX(id) FROM cal_events").fetchone()[0]
    ev = cal.get(eid)
    page = ag.get(f"/calendar?event={eid}&d={ev['starts'][:10]}").text
    assert f"/field/log?office={env['oid']}&practice={h}&event={eid}" in page
    form_page = ag.get(f"/field/log?office={env['oid']}&practice={h}&event={eid}").text
    assert 'value="pam@maple.com"' in form_page and 'value="abe@example.com"' not in form_page     # the hub team isn't "who you met"
    assert 'value="2 dozen cookies, 50 referral pads"' in form_page and 'value="45.00"' in form_page
    assert f'name="event_id" value="{eid}"' in form_page
    ag.post("/field/visits", data={"office_id": str(env["oid"]), "name": "Maple Street Family Dental", "practice_id": str(h),
                                   "practice_required": "1", "visited_at": date.today().isoformat() + "T10:15",
                                   "event_id": str(eid), "person_name": ["Pam Beesly"], "person_email": ["pam@maple.com"],
                                   "person_role": ["Office manager"]})
    assert cal.get(eid)["visit_id"]
    assert "Logged ✓" in ag.get(f"/calendar?event={eid}&d={ev['starts'][:10]}").text
    # lookups
    offices = a.get("/calendar/lookup/offices", params={"q": "maple"}).json()["offices"]
    assert offices[0]["label"] == "Maple Street Family Dental" and offices[0]["office_id"] == env["oid"]
    assert a.get("/calendar/lookup/people", params={"office_id": env["oid"]}).json()["people"][0]["email"] == "pam@maple.com"
    assert a.get("/calendar/lookup/people", params={"q": "abe"}).json()["people"][0]["source"] == "team"
    locs = a.get("/calendar/lookup/locations", params={"practice": h}).json()["locations"]
    assert locs == [{"label": "Heights Orthodontics", "address": "43767 Van Dyke Ave, Sterling Heights, MI"}]
    # private events show as busy to others
    a.post("/calendar/events", data={**form(h, private="1", action="save", kind="lunch")})
    with db.conn() as c:
        pid = c.execute("SELECT MAX(id) FROM cal_events").fetchone()[0]
    assert "Busy (private)" in env["viewer"].get(f"/calendar?d={cal.get(pid)['starts'][:10]}").text


def test_microsoft_connection_settings(env, monkeypatch):
    a, ms, db = env["admin"], env["ms"], env["db"]
    page = a.get("/connections").text
    assert "Microsoft 365" in page and "/microsoft/callback" in page
    r = a.post("/connections/microsoft", data={"tenant_id": "nope", "client_id": "x", "client_secret": "s"}, follow_redirects=True)
    assert "tenant) ID and Application (client) ID" in r.text
    t, cid = "11111111-aaaa-4bbb-8ccc-222222222222", "33333333-dddd-4eee-8fff-444444444444"
    r = a.post("/connections/microsoft", data={"tenant_id": t, "client_id": cid, "client_secret": "11111111-2222-3333-4444-555555555555"},
               follow_redirects=True)
    assert "Secret ID" in r.text
    r = a.post("/connections/microsoft", data={"tenant_id": t, "client_id": cid, "client_secret": "abc~DEF.secret-value",
                                               "mailbox": MAILBOX, "timezone_name": "America/Detroit"}, follow_redirects=True)
    assert "Sign in with Microsoft" in r.text and ms.configured() and ms.mailbox() == MAILBOX
    go = a.get("/microsoft/connect", follow_redirects=False)
    assert go.status_code == 303 and go.headers["location"].startswith(f"https://login.microsoftonline.com/{t}/oauth2/v2.0/authorize?")
    assert "Calendars.ReadWrite.Shared" in go.headers["location"] and "code_challenge" in go.headers["location"]
    assert env["viewer"].get("/connections", follow_redirects=True).text.count("Connect Microsoft 365") == 0
    assert "33333333-dddd" not in env["viewer"].get("/calendar").text
    # without the user's own mailbox, events go to /me
    db.set_setting("ms_user", json.dumps({"email": MAILBOX}))
    assert ms.base() == "/me"
    db.set_setting("ms_user", json.dumps({"email": "mjohnson@mcdentalpartners.com"}))
    assert ms.base() == "/users/marketing-events%40mcdentalpartners.com"
    for k in ("ms_tenant_id", "ms_client_id", "ms_client_secret", "ms_mailbox", "ms_user", "ms_timezone"):
        db.set_setting(k, None)
