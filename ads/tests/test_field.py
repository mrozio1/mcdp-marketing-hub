"""Field visits: logging (with on-site check and receipts), activity, budgets, map overlay and routes."""
import importlib
import io
import json
import os
import re
from datetime import date, timedelta

import pytest

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1", "AI_API_KEY": "test"})
    from app import ai, connectors, db, field, places, referrals, users
    for m in (db, users, connectors, places, referrals, ai, field):
        importlib.reload(m)
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    def person(admin, name, email, role, field_on=False):
        r = admin.post("/team/add", data={"name": name, "email": email, "role": role, "field_on": "1" if field_on else "", "social_on": "1"})
        tok = re.search(r"/setup/([\w-]+)", r.text).group(1)
        c = TestClient(main.app)
        c.post(f"/setup/{tok}", data={"password": "long-password-1", "confirm": "long-password-1"})
        return c

    with TestClient(main.app) as boss:
        boss.post("/login", data={"password": "shared-pw"})
        admin = person(boss, "Ann Admin", "ann@example.com", "admin")
        agent = person(admin, "Abe Agent", "abe@example.com", "viewer", field_on=True)   # a viewer with field visits on
        viewer = person(admin, "Vi Viewer", "vi@example.com", "viewer")
        # Our office, a Google dental pin near it, and an NPI practice.
        heights = db.add_practice("Heights Orthodontics", 1000, 500)
        db.add_practice("Havens Orthodontics", 0, 0)
        places.add_office("Heights – Defiance", "ChIJ_office_defiance01", 41.28, -84.35)
        oid = places.offices()[0]["id"]
        with db.conn() as c:
            c.execute("INSERT INTO map_places(place_id, layer, office_id, miles, lat, lng, coords_at, ranked) "
                      "VALUES ('ChIJ_dentist_maple001', 'referrer', ?, 1.2, 41.30, -84.36, ?, 1)", (oid, db.now()))
            c.execute("INSERT INTO ref_practices(key, name, address, city, state, zip, lat, lng, place_id, seen_at) "
                      "VALUES ('npi-maple', 'Maple Street Family Dental', '12 Maple St', 'Defiance', 'OH', '43512', "
                      "41.30, -84.36, 'ChIJ_dentist_maple001', ?)", (db.now(),))
            c.execute("INSERT INTO ref_practices(key, name, address, city, state, zip, lat, lng, place_id, seen_at) "
                      "VALUES ('npi-oak', 'Oak Dental', '9 Oak Ave', 'Defiance', 'OH', '43512', 41.27, -84.40, '', ?)",
                      (db.now(),))
        yield {"main": main, "db": db, "field": field, "users": users, "ai": ai, "admin": admin, "agent": agent,
               "viewer": viewer, "heights": heights, "oid": oid}


def log(c, **kw):
    data = {"place_id": "", "npi_key": "", "name": "", "rating": "warm", "met_name": "Pam", "met_role": "Office manager",
            "dropped": "Lunch", "cost": "48.20", "followup": "", "notes": "Asked about Invisalign",
            "visited_at": date.today().isoformat() + "T10:15"}
    data.update(kw)
    files = {"receipt": ("r.jpg", JPEG, "image/jpeg")} if kw.pop("with_receipt", False) or data.pop("with_receipt", None) else None
    return c.post("/field/visits", data=data, files=files)


def test_who_can_log(env):
    v, a = env["viewer"], env["agent"]
    assert v.get("/field").status_code == 200                                  # everyone sees the Field page
    assert "Log a visit" not in v.get("/field").text
    r = v.get("/field/log?place=ChIJ_dentist_maple001", follow_redirects=False)
    assert r.status_code == 303                                                # viewers can't log
    assert a.get("/field/log?place=ChIJ_dentist_maple001").status_code == 200
    assert "view + field" in a.get("/").text and 'href="/field"' in a.get("/").text
    # Field visits don't add editing rights: still a viewer otherwise.
    r = a.post("/practices", data={"name": "X"}, follow_redirects=True)
    assert "view-only access" in r.text
    # The switch: off stops logging straight away, on brings it back; any access level can have it.
    uid = env["users"].by_email("abe@example.com")["id"]
    env["admin"].post(f"/team/{uid}/switch/field", data={"on": ""})
    r = a.get("/field/log?place=ChIJ_dentist_maple001", follow_redirects=True)
    assert "Field visits aren" in r.text
    env["admin"].post(f"/team/{uid}/switch/field", data={"on": "1"})
    assert a.get("/field/log?place=ChIJ_dentist_maple001").status_code == 200
    ann = env["users"].by_email("ann@example.com")["id"]
    assert env["admin"].get("/field/log", follow_redirects=False).status_code == 303   # admins too need it on
    env["admin"].post(f"/team/{ann}/switch/field", data={"on": "1"})
    assert env["admin"].get("/field/log").status_code == 200
    env["admin"].post(f"/team/{ann}/switch/field", data={"on": ""})


def test_log_from_a_map_pin_uses_npi_name_not_google(env):
    a, field, db = env["agent"], env["field"], env["db"]
    page = a.get("/field/log?place=ChIJ_dentist_maple001").text
    assert 'value="Maple Street Family Dental"' in page                        # from the NPI match
    r = log(a, place_id="ChIJ_dentist_maple001", name="Maple Street Family Dental",
            phone_lat="41.3001", phone_lng="-84.3602", phone_acc="15", followup=date.today().isoformat())
    assert "logged (on site)" in r.text
    o = field.find_office("ChIJ_dentist_maple001")
    assert o["lat"] is None and o["lng"] is None                               # Google coordinates aren't stored
    assert o["here_lat"] == 41.30 and o["our_office_id"] == env["oid"]
    with db.conn() as c:
        v = dict(c.execute("SELECT * FROM visits ORDER BY id DESC LIMIT 1").fetchone())
    assert v["on_site"] == 1 and v["cost"] == 48.2 and v["user_name"] == "Abe Agent"


def test_far_away_and_name_required(env):
    a = env["agent"]
    r = log(a, place_id="ChIJ_dentist_maple001", name="", phone_lat="41.5", phone_lng="-84.0")
    assert "logged" in r.text and "mi from the office" in r.text               # existing office keeps its name
    with env["db"].conn() as c:
        assert c.execute("SELECT on_site FROM visits ORDER BY id DESC LIMIT 1").fetchone()[0] == 0
    r = log(a, name="", phone_lat="41.4", phone_lng="-84.2")                    # brand-new office needs a name
    assert "Add the office" in r.text


def test_new_office_on_the_spot_and_npi_office(env):
    a, field = env["agent"], env["field"]
    r = log(a, name="Riverside Dental", phone_lat="41.29", phone_lng="-84.38", rating="cold", cost="")
    assert "Riverside Dental logged (on site)" in r.text
    o = [x for x in field.office_status() if x["name"] == "Riverside Dental"][0]
    assert (o["lat"], o["lng"]) == (41.29, -84.38)
    r = log(a, npi_key="npi-oak", name="Oak Dental", rating="hot", cost="12", with_receipt=True)
    assert "Oak Dental logged" in r.text


def test_receipts(env, monkeypatch):
    a, field, ai = env["agent"], env["field"], env["ai"]
    monkeypatch.setattr(ai, "_chat", lambda messages, **kw: '{"total": 23.45, "merchant": "Panera Bread"}')
    r = a.post("/field/receipt/read", files={"receipt": ("r.jpg", JPEG, "image/jpeg")})
    assert r.json() == {"total": 23.45, "merchant": "Panera Bread"}
    sent = {}
    monkeypatch.setattr(ai, "_chat", lambda messages, **kw: sent.update(m=messages) or "{}")
    a.post("/field/receipt/read", files={"receipt": ("r.jpg", JPEG, "image/jpeg")})
    assert sent["m"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    sent.clear()
    r = env["viewer"].post("/field/receipt/read", files={"receipt": ("r.jpg", JPEG, "image/jpeg")}, follow_redirects=False)
    assert r.status_code == 303 and not sent                                     # refused before the AI is asked
    assert a.post("/field/receipt/read", files={"receipt": ("x.txt", b"hello", "text/plain")}).status_code == 422
    # The saved receipt from the Oak visit can be opened by anyone signed in, and goes when the visit does.
    with env["db"].conn() as c:
        vid, name = c.execute("SELECT id, receipt FROM visits WHERE receipt!=''").fetchone()
    assert env["viewer"].get(f"/field/receipts/{name}").content == JPEG
    assert env["viewer"].get("/field/receipts/../../ads.db").status_code == 404
    env["admin"].post(f"/field/visits/{vid}/delete")
    assert field.receipt_path(name) is None


def test_activity_page_budgets_and_export(env):
    admin, field, db = env["admin"], env["field"], env["db"]
    # 'Heights – Defiance' is suggested as Heights Orthodontics.
    assert field.practice_links()[env["oid"]] == env["heights"]
    abe = env["users"].by_email("abe@example.com")["id"]
    admin.post("/field/settings/budgets", data={f"pb_{env['heights']}": "300", f"ab_{abe}": "$250"})
    html = admin.get("/field?period=month").text
    assert "Maple Street Family Dental" in html and "Riverside Dental" in html
    assert "Heights Orthodontics" in html and "of $300.00" in html and "of $250.00" in html
    assert "on site" in html and "Asked about Invisalign" in html
    day = admin.get("/field?period=day").text
    assert "Where they" in day
    r = admin.get(f"/field.xlsx?period=month&who={abe}")
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
    assert ws["A2"].value == "Date" and ws.max_row >= 5
    # Editors-only settings stay closed to agents.
    assert env["agent"].post("/field/settings/budgets", data={}, follow_redirects=False).status_code == 303
    assert "Field budgets" not in env["agent"].get("/field").text


def test_followups_and_map_overlay(env):
    field = env["field"]
    due = field.followups()
    assert [f["name"] for f in due] == []      # the Maple follow-up was replaced by a later visit without one
    log(env["agent"], place_id="ChIJ_dentist_maple001", name="",
        followup=(date.today() - timedelta(days=1)).isoformat(), visited_at=date.today().isoformat() + "T15:00")
    due = field.followups()
    assert due[0]["name"] == "Maple Street Family Dental" and due[0]["overdue"]
    data = env["viewer"].get("/map/data.json").json()
    v = data["visits"]["ChIJ_dentist_maple001"]
    assert v["rating"] == "warm" and v["overdue"] and v["n"] >= 3
    assert any(x["name"] == "Riverside Dental" for x in data["visited_offices"])
    html = env["agent"].get("/field").text
    assert "Follow-ups due" in html and "Overdue" in html
    assert "Overdue" not in env["viewer"].get("/field").text                    # the viewer has none of their own
    assert "Overdue" in env["admin"].get("/field").text                         # admins see everyone's


def test_route_ordering_and_links(env):
    field = env["field"]
    stops = [{"id": str(i), "lat": 41.0 + i * 0.1, "lng": -84.0} for i in (3, 1, 4, 2, 0)]
    ordered = field.order_stops((40.9, -84.0), stops)
    assert [s["id"] for s in ordered] == ["0", "1", "2", "3", "4"]
    many = [{"id": str(i), "lat": 41 + i / 100, "lng": -84, "place_id": "ChIJ_x%07d" % i} for i in range(23)]
    links = field.maps_links("", many)
    assert [(l["from"], l["to"]) for l in links] == [(1, 10), (11, 20), (21, 23)]
    assert "origin" not in links[0]["url"] and "origin=41.09,-84" in links[1]["url"]
    assert "destination_place_id=ChIJ_x0000009" in links[0]["url"] and links[0]["url"].count("|") == 8
    r = env["viewer"].post("/field/route-order", json={"start": {"kind": "office", "id": env["oid"]},
                                                       "stops": stops})
    j = r.json()
    assert j["links"][0]["url"].startswith("https://www.google.com/maps/dir/?api=1") and "origin=41.28,-84.35" in j["links"][0]["url"]
    assert "Add your home address" in env["viewer"].post("/field/route-order", json={"start": {"kind": "home"}, "stops": stops}).json()["error"]


def test_home_address(env, monkeypatch):
    field = env["field"]
    monkeypatch.setattr(field, "geocode_one", lambda a: (41.25, -84.3) if "Main" in a else None)
    a = env["agent"]
    assert "Couldn" in a.post("/account/home", data={"address": "nowhere"}, follow_redirects=True).text
    a.post("/account/home", data={"address": "1 Main St, Defiance, OH 43512"})
    u = env["users"].by_email("abe@example.com")
    assert (u["home_lat"], u["home_lng"]) == (41.25, -84.3)
    j = a.post("/field/route-order", json={"start": {"kind": "home"}, "stops": [{"id": "x", "lat": 41.3, "lng": -84.3}]}).json()
    assert "origin=41.25,-84.3" in j["links"][0]["url"]


def test_only_own_visits_removable_by_agents(env):
    with env["db"].conn() as c:
        vid = c.execute("SELECT id FROM visits WHERE user_name='Abe Agent' LIMIT 1").fetchone()[0]
    # A second agent can't remove Abe's visit; Abe can.
    from fastapi.testclient import TestClient
    r = env["admin"].post("/team/add", data={"name": "Bea", "email": "bea@example.com", "role": "viewer", "field_on": "1"})
    tok = re.search(r"/setup/([\w-]+)", r.text).group(1)
    bea = TestClient(env["main"].app)
    bea.post(f"/setup/{tok}", data={"password": "long-password-1", "confirm": "long-password-1"})
    assert "Only the person" in bea.post(f"/field/visits/{vid}/delete", follow_redirects=True).text
    env["agent"].post(f"/field/visits/{vid}/delete")
    assert env["field"].visit(vid) is None


def test_old_field_agent_level_becomes_viewer_with_switch(env):
    db, users = env["db"], env["users"]
    with db.conn() as c:
        c.execute("INSERT INTO users(email, name, role, created_at) VALUES ('old@example.com','Old Agent','agent',?)", (db.now(),))
    users.init()
    u = users.by_email("old@example.com")
    assert u["role"] == "viewer" and u["field"] == 1


def test_social_switch_shows_or_hides_the_link(env):
    users, admin, v = env["users"], env["admin"], env["viewer"]
    vid = users.by_email("vi@example.com")["id"]
    assert users.get(vid)["social"] == 1                                         # on unless switched off
    assert 'href="https://social.mcdp.com"' in v.get("/").text
    admin.post(f"/team/{vid}/switch/social", data={"on": ""})
    home = v.get("/").text
    assert 'href="https://social.mcdp.com"' not in home and "Social posts" not in home
    admin.post(f"/team/{vid}/switch/social", data={"on": "1"})
    assert 'href="https://social.mcdp.com"' in v.get("/").text
    assert admin.post(f"/team/{vid}/switch/nonsense", data={"on": "1"}).status_code == 200   # ignored
    r = admin.post("/team/add", data={"name": "No Social", "email": "nos@example.com", "role": "viewer"})
    assert users.by_email("nos@example.com")["social"] == 0                       # unticked when adding


def test_people_see_only_their_own_field_data_unless_admin(env):
    from fastapi.testclient import TestClient
    admin, a = env["admin"], env["agent"]
    r = admin.post("/team/add", data={"name": "Cy Second", "email": "cy@example.com", "role": "editor",
                                      "field_on": "1", "social_on": "1"})
    tok = re.search(r"/setup/([\w-]+)", r.text).group(1)
    cy = TestClient(env["main"].app)
    cy.post(f"/setup/{tok}", data={"password": "long-password-1", "confirm": "long-password-1"})
    log(cy, name="Cy's Secret Dental", phone_lat="41.31", phone_lng="-84.33", cost="77")
    abe = env["users"].by_email("abe@example.com")["id"]
    mine = a.get("/field?period=month").text
    assert "Your field visits" in mine and "Secret Dental" not in mine and "Riverside Dental" in mine
    assert 'name="who"' not in mine and "By practice" not in mine and "Your field spend" in mine
    cy_id = env["users"].by_email("cy@example.com")["id"]
    assert "Riverside Dental" not in cy.get(f"/field?period=month&who={abe}").text   # can't peek with ?who=
    assert "Field budgets" not in cy.get("/field").text                             # setup is admin-only now
    assert cy.post("/field/settings/budgets", data={}, follow_redirects=False).status_code == 303
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(cy.get(f"/field.xlsx?period=month&who={abe}").content)).active
    offices = {ws.cell(r, 4).value for r in range(3, ws.max_row + 1)}
    assert "Cy's Secret Dental" in offices and "Riverside Dental" not in offices
    every = admin.get("/field?period=month").text
    assert "Cy&#39;s Secret Dental" in every and "Riverside Dental" in every and 'name="who"' in every


def test_several_people_with_emails(env):
    a, f = env["agent"], env["field"]
    r = a.post("/field/visits", data={
        "place_id": "ChIJ_dentist_maple001", "name": "Maple Street Family Dental", "rating": "warm",
        "visited_at": date.today().isoformat() + "T09:00",
        "person_name": ["Pam Beesly", "Dr. Oak", ""], "person_email": ["Pam@Maple.com ", "", ""],
        "person_role": ["Office manager", "Doctor", ""]}, follow_redirects=True)
    assert r.status_code == 200
    rows = f.visits_between(date.today(), date.today() + timedelta(days=1))
    v = [x for x in rows if x["met_name"].startswith("Pam Beesly")][0]
    assert v["people"] == [{"name": "Pam Beesly", "email": "pam@maple.com", "role": "Office manager"},
                           {"name": "Dr. Oak", "email": "", "role": "Doctor"}]
    assert v["met_name"] == "Pam Beesly, Dr. Oak"
    page = a.get("/field?period=day").text
    assert "pam@maple.com" in page and "Dr. Oak (Doctor)" in page
    # Export carries names, roles and emails in the same order.
    import openpyxl, io
    ws = openpyxl.load_workbook(io.BytesIO(a.get("/field.xlsx?period=day").content)).active
    hdr = [c.value for c in ws[2]]
    row = [[c.value for c in r] for r in ws.iter_rows(min_row=3) if r[hdr.index("Met")].value == "Pam Beesly; Dr. Oak"][0]
    assert row[hdr.index("Met")] == "Pam Beesly; Dr. Oak"
    assert row[hdr.index("Email")] == "pam@maple.com; –"
    # A bad email is turned back with a clear message, nothing saved.
    n = len(f.visits_between(date.today(), date.today() + timedelta(days=1)))
    r = a.post("/field/visits", data={"place_id": "ChIJ_dentist_maple001", "name": "Maple", "person_name": ["X"],
                                      "person_email": ["not-an-email"], "person_role": [""]}, follow_redirects=True)
    assert "look like an email" in r.text
    assert len(f.visits_between(date.today(), date.today() + timedelta(days=1))) == n
    # Deleting the visit removes its people too.
    env["admin"].post(f"/field/visits/{v['id']}/delete")
    with env["db"].conn() as c:
        assert c.execute("SELECT COUNT(*) FROM visit_people WHERE visit_id=?", (v["id"],)).fetchone()[0] == 0


class FakeSF:
    """Just enough of the Salesforce REST API: SOQL lookups by exact value, and creates."""
    def __init__(self):
        self.rows = {"Account": [{"Id": "001OLD", "Name": "Oak Dental", "BillingPostalCode": "43512"}],
                     "Contact": [{"Id": "003OLD", "Email": "pam@maple.com", "AccountId": "001X", "Name": "Pam B"}],
                     "User": [{"Id": "005ABE", "Email": "abe@example.com", "IsActive": True}], "Task": []}
        self.n = 0

    def __call__(self, method, path, body=None, params=None, absolute=False):
        if method == "POST":
            obj = path.rsplit("/", 1)[1]
            self.n += 1
            rec = {"Id": f"{obj[:3].upper()}{self.n}", **body}
            self.rows[obj].append(rec)
            return {"id": rec["Id"], "success": True}
        q = params["q"]
        obj = re.search(r"FROM (\w+)", q).group(1)
        conds = dict(re.findall(r"(\w+) = '((?:[^'\\]|\\.)*)'", q))
        out = [r for r in self.rows[obj] if all(str(r.get(k, "")).lower() == v.replace("\\'", "'").lower() for k, v in conds.items())]
        return {"records": out}


def test_salesforce_sync(env, monkeypatch):
    a, f, db = env["agent"], env["field"], env["db"]
    from app import salesforce as sf
    fake = FakeSF()
    monkeypatch.setattr(sf, "api", fake)
    db.set_setting("sf_client_id", "x" * 30)
    db.set_setting("sf_client_secret", "y" * 20)
    db.set_setting("sf_refresh_token", "rt")
    db.set_setting("sf_instance_url", "https://mcdp.my.salesforce.com")
    monkeypatch.setattr(sf, "send_in_background", lambda *a: None)   # send explicitly below
    with db.conn() as c:
        c.execute("UPDATE visits SET sf_status='sent'")              # earlier tests' visits aren't part of this
    a.post("/field/visits", data={
        "place_id": "ChIJ_dentist_maple001", "name": "Maple Street Family Dental", "rating": "hot",
        "visited_at": date.today().isoformat() + "T09:30", "followup": (date.today() + timedelta(days=7)).isoformat(),
        "notes": "Wants referral pads", "dropped": "Lunch", "cost": "52",
        "person_name": ["Pam Beesly", "Dr. Joe Oak"], "person_email": ["pam@maple.com", ""],
        "person_role": ["Office manager", "Doctor"]})
    a.post("/field/visits", data={"npi_key": "npi-oak", "name": "Oak Dental", "visited_at": date.today().isoformat() + "T11:00",
                                  "person_name": [""], "person_email": [""], "person_role": [""]})
    got = sf.send_pending("https://ads.mcdp.com")
    assert got["sent"] == 2 and got["failed"] == 0
    accs = fake.rows["Account"]
    new = [x for x in accs if x["Id"] != "001OLD"]
    assert len(new) == 1 and new[0]["Name"] == "Maple Street Family Dental" and new[0]["Type"] == "Referral office"
    assert new[0]["BillingCity"] == "Defiance" and new[0]["BillingPostalCode"] == "43512"
    # Pam already existed (matched by email, not duplicated); Dr. Oak is new, with Dr. as salutation.
    assert [c for c in fake.rows["Contact"] if c.get("LastName") == "Oak"][0]["Salutation"] == "Dr."
    assert sum(1 for c in fake.rows["Contact"] if c.get("Email") == "pam@maple.com") == 1
    tasks = fake.rows["Task"]
    visit_t = [t for t in tasks if t["Subject"] == "Field visit: Maple Street Family Dental"][0]
    assert visit_t["Status"] == "Completed" and visit_t["WhoId"] == "003OLD" and visit_t["OwnerId"] == "005ABE"
    assert "Pam Beesly (Office manager) <pam@maple.com>; Dr. Joe Oak (Doctor)" in visit_t["Description"]
    assert "Relationship: Hot" in visit_t["Description"] and "$52.00" in visit_t["Description"]
    fu = [t for t in tasks if t["Subject"].startswith("Follow up:")]
    assert len(fu) == 1 and fu[0]["Status"] == "Open"
    # The existing "Oak Dental" Account was matched, not recreated; a visit with nobody met has no contact.
    oak_t = [t for t in tasks if t["Subject"] == "Field visit: Oak Dental"][0]
    assert oak_t["WhatId"] == "001OLD" and "WhoId" not in oak_t
    # Sending again does nothing new; the Field page links each visit to Salesforce.
    n = len(tasks)
    assert sf.send_pending()["sent"] == 0 and len(fake.rows["Task"]) == n
    assert "in Salesforce" in a.get("/field?period=day").text
    # A failure is kept on the visit and shown, and retried later.
    def broken(*a, **k):
        raise sf.SFError("REQUIRED_FIELD_MISSING (HTTP 400)")
    a.post("/field/visits", data={"place_id": "ChIJ_dentist_maple001", "name": "Maple Street Family Dental",
                                  "visited_at": date.today().isoformat() + "T15:00"})
    monkeypatch.setattr(sf, "api", broken)
    got = sf.send_pending()
    assert got["failed"] == 1 and "REQUIRED_FIELD_MISSING" in got["error"]
    assert "Salesforce: not sent" in a.get("/field?period=day").text
    monkeypatch.setattr(sf, "api", fake)
    assert sf.send_pending()["sent"] == 1
    sf.disconnect()
    for k in ("sf_client_id", "sf_client_secret"):
        db.set_setting(k, None)


def test_salesforce_setup_is_admin_only(env):
    assert env["agent"].get("/salesforce/connect", follow_redirects=False).status_code == 303
    r = env["agent"].post("/connections/salesforce", data={"client_id": "x" * 30, "client_secret": "y" * 20}, follow_redirects=False)
    assert r.status_code == 303 and env["db"].get_setting("sf_client_id") is None
    r = env["admin"].post("/connections/salesforce", data={"client_id": "x" * 30, "client_secret": "y" * 20}, follow_redirects=True)
    assert "Sign in to Salesforce" in r.text
    r = env["admin"].get("/salesforce/connect", follow_redirects=False)
    loc = r.headers["location"]
    assert loc.startswith("https://login.salesforce.com/services/oauth2/authorize") and "code_challenge=" in loc
    assert "salesforce%2Fcallback" in loc
    r = env["admin"].get("/salesforce/callback?code=abc&state=wrong", follow_redirects=True)
    assert "expired" in r.text


def test_salesforce_address_and_names():
    from app import salesforce as sf
    assert sf.split_address("12 Maple St, Defiance, OH 43512, USA") == {
        "BillingStreet": "12 Maple St", "BillingCity": "Defiance", "BillingState": "OH", "BillingPostalCode": "43512"}
    assert sf.split_address("Suite 4, 9 Oak Ave, Troy, MI 48084")["BillingStreet"] == "Suite 4, 9 Oak Ave"
    assert sf.split_name("Dr. Mary Ann Lee") == {"FirstName": "Mary Ann", "LastName": "Lee", "Salutation": "Dr."}
    assert sf.split_name("", "front@maple.com")["LastName"] == "front"
    assert sf._q("O'Brien") == "'O\\'Brien'"


def test_salesforce_refresh_token_rotation(env, monkeypatch):
    from app import salesforce as sf
    db = env["db"]
    calls = []

    def fake_post(url, fields):
        calls.append(fields["refresh_token"])
        return {"access_token": f"at{len(calls)}", "instance_url": "https://x.my.salesforce.com",
                "refresh_token": f"rt{len(calls) + 1}"}
    monkeypatch.setattr(sf, "_form_post", fake_post)
    db.set_setting("sf_refresh_token", "rt1")
    sf._token_cache.clear()
    assert sf._access() == ("at1", "https://x.my.salesforce.com")
    assert db.get_setting("sf_refresh_token") == "rt2"            # the rotated token is kept
    assert sf._access()[0] == "at1" and len(calls) == 1           # cached access token reused
    db.set_setting("sf_last_refresh", "2000-01-01T00:00:00")
    sf.keep_alive()
    assert calls == ["rt1", "rt2"] and db.get_setting("sf_refresh_token") == "rt3"
    sf.keep_alive()                                               # refreshed today already: nothing
    assert len(calls) == 2
    sf.disconnect()
    db.set_setting("sf_last_refresh", None)


def test_sync_now_answers_at_once(env, monkeypatch):
    import threading, time as _t
    from app import sync
    release = threading.Event()
    calls = []

    def slow_sync(today=None, refresh_lists=False):
        with sync._lock:
            calls.append(1)
            release.wait(5)
            sync.db.set_setting("last_sync", json.dumps({"at": "2026-10-21T17:40:00", "since": "2026-10-01",
                                                         "until": "2026-10-21", "results": {}}))
    monkeypatch.setattr(sync, "sync_all", slow_sync)
    a = env["admin"]
    t0 = _t.time()
    r = a.post("/sync", data={"back": "/practices"}, follow_redirects=True)
    assert _t.time() - t0 < 3 and "Sync started" in r.text and "/sync/status" in r.text   # page polls until done
    assert a.get("/sync/status").json()["running"] is True
    r = a.post("/sync", data={"back": "/practices"}, follow_redirects=True)
    assert "already running" in r.text and len(calls) == 1
    release.set()
    for _ in range(50):
        if not a.get("/sync/status").json()["running"]:
            break
        _t.sleep(0.05)
    r = a.get("/sync/done?back=/practices", follow_redirects=True)
    assert "/sync/status" not in r.text
