"""Departments on field visits: leads approve or send back their department's visits and hear about every new one;
Management sees everything and needs no approval; Operations' checklists save with a visit and only Operations,
Management and admins see them."""
import importlib
import os
import re
from datetime import date

import pytest


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
    from app import db, dept, field, p360, users
    for m in (db, users, field, p360, dept):
        importlib.reload(m)
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    def person(admin, name, email, role):
        r = admin.post("/team/add", data={"name": name, "email": email, "role": role, "field_on": "1", "social_on": "1"})
        tok = re.search(r"/setup/([\w-]+)", r.text).group(1)
        c = TestClient(main.app)
        c.post(f"/setup/{tok}", data={"password": "long-password-1", "confirm": "long-password-1"})
        return c

    with TestClient(main.app) as boss:
        boss.post("/login", data={"password": "shared-pw"})
        admin = person(boss, "Ann Admin", "ann@example.com", "admin")
        people = {k: person(admin, n, e, r) for k, n, e, r in (
            ("amber", "Amber Lead", "amber@example.com", "editor"), ("mel", "Mel Marketer", "mel@example.com", "viewer"),
            ("jer", "Jer Lead", "jer@example.com", "viewer"), ("olly", "Olly Ops", "olly@example.com", "viewer"),
            ("mgr", "Max Manager", "max@example.com", "editor"), ("ed", "Ed Editor", "ed@example.com", "editor"))}
        ids = {u["email"].split("@")[0]: u["id"] for u in users.all_users()}
        for key, d, lead in (("amber", "marketing", 1), ("mel", "marketing", 0), ("jer", "operations", 0), ("olly", "operations", 0),
                             ("max", "management", 0)):
            admin.post(f"/team/{ids[key]}/department", data={"department": d})
            if lead:
                admin.post(f"/team/{ids[key]}/department", data={"department": d, "lead": "1"})
        admin.post(f"/team/{ids['jer']}/department", data={"department": "operations", "lead": "1"})
        havens = db.add_practice("Havens Orthodontics", 0, 0, "havens")
        with db.conn() as c:
            c.execute("INSERT INTO ref_practices(key, name, address, city, state, zip, lat, lng, place_id, seen_at) "
                      "VALUES ('npi-oak', 'Oak Dental', '9 Oak Ave', 'Imlay City', 'MI', '48444', 43.02, -83.07, '', ?)", (db.now(),))
        yield {"main": main, "db": db, "dept": dept, "field": field, "p360": p360, "users": users, "admin": admin, "ids": ids,
               "havens": havens, **people}


def log(c, **kw):
    data = {"npi_key": "npi-oak", "name": "Oak Dental", "virtual": "1", "notes": "Stopped in",
            "visited_at": date.today().isoformat() + "T10:15"}
    data.update(kw)
    return c.post("/field/visits", data=data, follow_redirects=False)


def last_visit(env):
    with env["db"].conn() as c:
        return dict(c.execute("SELECT * FROM visits ORDER BY id DESC LIMIT 1").fetchone())


def test_team_page_sets_departments_and_leads(env):
    u = env["users"].get(env["ids"]["amber"])
    assert u["department"] == "marketing" and u["dept_lead"] == 1
    assert env["users"].get(env["ids"]["jer"])["dept_lead"] == 1
    page = env["admin"].get("/team").text
    assert "Leads Marketing" in page and "Approves Marketing visits" in page
    # changing department starts them as a member
    env["admin"].post(f"/team/{env['ids']['mel']}/department", data={"department": "hr", "lead": "1"})
    assert env["users"].get(env["ids"]["mel"])["dept_lead"] == 0
    env["admin"].post(f"/team/{env['ids']['mel']}/department", data={"department": "marketing"})
    assert env["mel"].post(f"/team/{env['ids']['mel']}/department", data={"department": "management"},
                           follow_redirects=False).status_code in (303, 403)
    assert env["users"].get(env["ids"]["mel"])["department"] == "marketing"           # only admins change it


def test_marketing_visit_needs_the_leads_approval(env, monkeypatch):
    sent = []
    from app import microsoft
    monkeypatch.setattr(microsoft, "connected", lambda: True)
    monkeypatch.setattr(microsoft, "send_mail", lambda to, subject, body, reply_to=None: sent.append((to, subject)))
    log(env["mel"])
    v = last_visit(env)
    assert v["department"] == "marketing" and v["approval"] == "pending"
    assert sent and sent[-1][0] == ["amber@example.com"] and "New visit" in sent[-1][1]   # the lead hears about it
    d = env["dept"]
    assert d.attention_count(env["users"].get(env["ids"]["amber"])) == 1
    assert d.attention_count(env["users"].get(env["ids"]["jer"])) == 0                    # not Operations' business
    page = env["amber"].get("/field?period=day").text
    assert "1 visit waiting for your approval" in page and f'action="/field/visits/{v["id"]}/approve"' in page
    jer_page = env["jer"].get("/field?period=day").text
    assert f'action="/field/visits/{v["id"]}/approve"' not in jer_page                    # another lead can't approve it
    assert env["jer"].post(f"/field/visits/{v['id']}/approve").status_code in (200, 303)
    assert last_visit(env)["approval"] == "pending"
    # Amber can edit Mel's visit; Jer (Operations lead) and Ed (an editor in no department... keeps the old rule) differ
    assert env["amber"].get(f"/field/visits/{v['id']}/edit", follow_redirects=False).status_code == 200
    assert env["jer"].get(f"/field/visits/{v['id']}/edit", follow_redirects=False).status_code == 303
    # send back needs a note; the owner hears; a correction goes back to the lead
    env["amber"].post(f"/field/visits/{v['id']}/return", data={"note": ""})
    assert last_visit(env)["approval"] == "pending"
    env["amber"].post(f"/field/visits/{v['id']}/return", data={"note": "Add the receipt please"})
    assert last_visit(env)["approval"] == "returned" and sent[-1][0] == ["mel@example.com"]
    assert "Add the receipt please" in env["mel"].get("/field?period=day").text
    env["mel"].post(f"/field/visits/{v['id']}", data={"visited_at": v["visited_at"], "notes": "Stopped in, receipt added",
                                                       "virtual": "1"})
    assert last_visit(env)["approval"] == "pending"
    env["amber"].post(f"/field/visits/{v['id']}/approve")
    assert last_visit(env)["approval"] == "approved" and last_visit(env)["approval_by"] == "Amber Lead"
    # the department filter
    assert f'id="v{v["id"]}"' in env["amber"].get("/field?period=day&dept=marketing").text
    assert f'id="v{v["id"]}"' not in env["amber"].get("/field?period=day&dept=operations").text


def test_leads_and_managers_need_no_approval(env):
    log(env["amber"])
    assert last_visit(env)["approval"] == ""
    log(env["mgr"])
    v = last_visit(env)
    assert v["department"] == "management" and v["approval"] == ""
    # Management can edit and approve anyone's visit
    log(env["olly"])
    o = last_visit(env)
    assert env["mgr"].get(f"/field/visits/{o['id']}/edit", follow_redirects=False).status_code == 200
    env["mgr"].post(f"/field/visits/{o['id']}/approve")
    assert last_visit(env)["approval"] == "approved"


def test_operations_checklist(env):
    h, p = env["havens"], env["p360"]
    form = env["jer"].get("/field/log").text
    assert "Operations checklist" in form and "Sterilizer biological (spore) testing" in form
    assert "Operations checklist" not in env["mel"].get("/field/log").text              # Marketing doesn't see it
    data = {"ck_kind": "onsite", "practice_id": str(h), "ck_onsite_01_0": "P", "ck_onsite_01_1": "P",
            "ck_onsite_02_0": "P", "ck_onsite_02_1": "A", "ckn_onsite_02_1": "Restroom 2 out of paper goods",
            "ck_escalation": "Spore test log missing for October",
            "ap_gap": ["Restroom restock", ""], "ap_action": ["Add to daily close", ""], "ap_goal": ["", ""],
            "ap_owner": ["Office lead", ""], "ap_due": ["2026-12-02", ""]}
    log(env["olly"], **data)
    v = last_visit(env)
    f = env["dept"].form_for(v["id"])
    assert f["kind"] == "onsite" and f["combined"] == 75 and f["escalation"].startswith("Spore")
    assert f["data"]["notes"]["onsite_02_1"] == "Restroom 2 out of paper goods"
    # the action plan row became one Operations action item for Havens
    rows = [e for e in p.entries(h, ["operations"], "", "", 0, "") if e["title"] == "Restroom restock"]
    assert len(rows) == 1
    # Jer (Operations lead) gets an escalation alert and sees the checklist summary; Marketing doesn't see it
    a = env["dept"].alerts(env["ids"]["jer"])
    assert a and a[0]["kind"] == "escalation"
    assert "Combined 75%" in env["jer"].get("/field?period=day").text
    assert "Combined 75%" in env["mgr"].get("/field?period=day").text
    assert "Combined 75%" not in env["amber"].get("/field?period=day").text
    # finishing it later from the visit keeps one action item, not two
    data2 = {**data, "ck_onsite_02_2": "P", "visited_at": v["visited_at"], "virtual": "1", "notes": "x"}
    env["olly"].post(f"/field/visits/{v['id']}", data=data2)
    assert env["dept"].form_for(v["id"])["data"]["marks"]["onsite_02_2"] == "P"
    assert len([e for e in p.entries(h, ["operations"], "", "", 0, "") if e["title"] == "Restroom restock"]) == 1
    # the weekly / monthly to-do
    log(env["olly"], ck_kind="recurring", ck_recurring_W_0="done", ckt_ops_wins="Same-day starts up")
    f2 = env["dept"].form_for(last_visit(env)["id"])
    assert f2["kind"] == "recurring" and f2["data"]["marks"] == {"recurring_W_0": "done"} and f2["data"]["texts"]["ops_wins"]
    env["dept"].mark_seen(env["ids"]["jer"])
    assert env["dept"].alerts(env["ids"]["jer"]) == []


def test_office_size(env):
    form = env["mel"].get("/field/log").text
    assert 'name="doctors"' in form and "Pick doctors first" in form
    log(env["mel"], doctors="2", staff="8")
    v = last_visit(env)
    assert (v["doctors"], v["staff"]) == (2, 8)
    assert "2 doctors · 8 staff" in env["amber"].get("/field?period=day").text
    r = log(env["mel"], doctors="1", staff="12")                       # out of range for 1 doctor
    assert "error=" in r.headers["location"] and last_visit(env)["id"] == v["id"]
    # the next visit to the same office starts from the last size
    assert 'value="2" selected' in env["mel"].get(f"/field/log?office={v['office_id']}").text
