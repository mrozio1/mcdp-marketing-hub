"""Referral accounts: Salesforce location accounts and their children, matched to practices, on the Field page, the
Map and Practice 360, with search, visits and notes (copied to Salesforce)."""
import importlib
import os
import re
import time
from datetime import date

import pytest

LOC_CANTON = "001i1000008bgDNAAY"
LOC_ROMEO = "001i1000008bgDSAAY"
LOC_BRIGHTON = "001iw000000s83vAAA"


def sf_rows():
    p = lambda sid, name, city: {"Name": name, "BillingStreet": "1 Main St", "BillingCity": city, "BillingState": "MI",
                                 "BillingPostalCode": "48187"}
    kid = lambda sid, name, loc, ploc, street="9 Oak Ave", city="Canton": {
        "Id": sid, "Name": name, "Type": "Referral office", "Phone": "555-0100", "BillingStreet": street, "BillingCity": city,
        "BillingState": "MI", "BillingPostalCode": "48187", "ParentId": loc, "Parent": ploc}
    canton, romeo, brighton = (p(LOC_CANTON, "Havens Orthodontics Canton", "Canton"), p(LOC_ROMEO, "Havens Orthodontics Romeo", "Romeo"),
                               p(LOC_BRIGHTON, "Brighton Orthodontics", "Brighton"))
    return [kid("001KID000000001AAA", "Childrens Dental Care of Canton", LOC_CANTON, canton),
            kid("001KID000000002AAA", "Cherry Hill Dental", LOC_CANTON, canton, "5 Cherry Hill Rd"),
            kid("001KID000000003AAA", "Pediatric Dentistry of Romeo", LOC_ROMEO, romeo, "2 Main", "Romeo"),
            kid("001KID000000004AAA", "Brighton Family Dental", LOC_BRIGHTON, brighton, "3 Grand River", "Brighton")]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
    from app import accounts, db, field, p360, referrals, salesforce, users
    for m in (db, users, field, p360, salesforce, referrals, accounts):
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
        amber = person(admin, "Amber Field", "amber@example.com", "editor")
        havens = db.add_practice("Havens Orthodontics", 0, 0, "havens")
        brighton = db.add_practice("Brighton Orthodontics", 0, 0, "brighton")
        db.add_practice("Heights Orthodontics", 0, 0, "heights")
        yield {"main": main, "db": db, "accounts": accounts, "salesforce": salesforce, "referrals": referrals, "field": field,
               "admin": admin, "amber": amber, "havens": havens, "brighton": brighton}


@pytest.fixture
def sf(env, monkeypatch):
    sf, made = env["salesforce"], []
    monkeypatch.setattr(sf, "connected", lambda: True)
    monkeypatch.setattr(sf, "instance_url", lambda: "https://mcdp.my.salesforce.com")
    monkeypatch.setattr(sf, "api", lambda method, path, body=None, params=None, absolute=False: {"records": sf_rows(), "done": True})
    monkeypatch.setattr(sf, "create", lambda obj, fields: made.append((obj, fields)) or "002NOTE00000001AAA")
    pts = {"Childrens": (42.31, -83.48), "Cherry": (42.30, -83.47), "Pediatric": (42.80, -83.01), "Brighton Family": (42.53, -83.78)}
    def geocode(items):
        out = {}
        for k in items:
            if k.startswith("l:"):
                out[k] = {LOC_CANTON: (42.31, -83.49), LOC_ROMEO: (42.80, -83.02), LOC_BRIGHTON: (42.53, -83.77)}[k[2:]]
        return out
    monkeypatch.setattr(env["referrals"], "geocode", geocode)
    monkeypatch.setattr(env["referrals"], "zip_point", lambda z: (42.32, -83.48))
    return made


def test_sync_matches_locations_to_practices(env, sf):
    a = env["accounts"]
    got = a.sync("test")
    assert got == {"locations": 3, "children": 4, "placed": 7}
    locs = {l["sf_id"]: l for l in a.locations(env["havens"])}
    assert set(locs) == {LOC_CANTON, LOC_ROMEO} and locs[LOC_CANTON]["short"] == "Canton"
    assert [l["short"] for l in a.locations(env["brighton"])] == ["Brighton"]          # one location named like the brand
    assert {b["name"] for b in a.brands()} == {"Havens Orthodontics", "Brighton Orthodontics"}
    # an admin's correction survives the next sync
    a.set_location_practice(LOC_ROMEO, None)
    a.sync("test")
    assert [l["sf_id"] for l in a.locations(env["havens"])] == [LOC_CANTON]
    a.set_location_practice(LOC_ROMEO, env["havens"])


def test_accounts_page_search_notes_and_logging(env, sf):
    a, h = env["accounts"], env["havens"]
    am = env["amber"]
    page = am.get(f"/field/accounts?p={h}").text
    assert "Childrens Dental Care of Canton" in page and "Pediatric Dentistry of Romeo" in page and "Canton" in page
    assert "Brighton Family Dental" not in page                                           # another brand
    page = am.get(f"/field/accounts?p={h}&loc={LOC_CANTON}").text
    assert "Cherry Hill Dental" in page and "Pediatric Dentistry of Romeo" not in page
    # log a visit from an account: a field office is made and linked to the Salesforce account
    r = am.get(f"/field/accounts/log?p={h}&key=sf:001KID000000001AAA", follow_redirects=False)
    oid = int(re.search(r"office=(\d+)", r.headers["location"]).group(1))
    am.post("/field/visits", data={"office_id": str(oid), "name": "Childrens Dental Care of Canton", "virtual": "1", "practice_id": str(h),
                                   "notes": "Dropped off referral pads for Dr. Shah", "person_name": "Pam Ortiz", "person_role": "Office manager",
                                   "visited_at": date.today().isoformat() + "T10:00"})
    acct = next(x for x in a.accounts(h) if x["key"] == "sf:001KID000000001AAA")
    assert acct["office_id"] == oid and acct["visits"] == 1 and acct["status"] == "visited"
    # the visit shows at every level: brand, location, account
    for q in ("", f"&loc={LOC_CANTON}", f"&loc={LOC_CANTON}&a=sf:001KID000000001AAA"):
        assert "referral pads for Dr. Shah" in am.get(f"/field/accounts?p={h}{q}").text
    assert "referral pads" not in am.get(f"/field/accounts?p={h}&loc={LOC_ROMEO}").text
    # a note on the account is kept and copied to Salesforce as a Note
    am.post("/field/accounts/note", data={"p": str(h), "key": "sf:001KID000000001AAA", "text": "Prefers the referral form by email",
                                         "back": f"/field/accounts?p={h}"})
    for _ in range(50):
        if sf:
            break
        time.sleep(0.05)
    assert sf and sf[0][0] == "Note" and sf[0][1]["ParentId"] == "001KID000000001AAA"
    assert "Prefers the referral form by email" in am.get(f"/field/accounts?p={h}&a=sf:001KID000000001AAA").text
    # search covers accounts, people met and notes under the brand only
    res = am.get(f"/field/accounts/search.json?p={h}&q=pam").json()
    assert res["people"] and res["people"][0]["account"] == "Childrens Dental Care of Canton"
    assert am.get(f"/field/accounts/search.json?p={h}&q=cherry").json()["accounts"][0]["name"] == "Cherry Hill Dental"
    assert am.get(f"/field/accounts/search.json?p={h}&q=email").json()["notes"]
    assert am.get(f"/field/accounts/search.json?p={h}&q=brighton family").json()["accounts"] == []
    assert "Pam Ortiz" in am.get(f"/field/accounts?p={h}&q=pam").text
    # the visit says whose child account it is, and the Map shows the Salesforce accounts
    fp = am.get("/field?period=day").text
    assert "Child of" in fp and f"loc={LOC_CANTON}" in fp
    m = am.get("/map/data.json").json()
    assert {x["name"] for x in m["sf_accounts"]} >= {"Childrens Dental Care of Canton", "Brighton Family Dental"}
    # Practice 360 gets a Referral network section
    with env["db"].conn() as c:
        c.execute("INSERT OR IGNORE INTO p360_pods(name) VALUES ('Dr. A. Havens')")
        pod = c.execute("SELECT id FROM p360_pods WHERE name='Dr. A. Havens'").fetchone()[0]
        c.execute("INSERT OR IGNORE INTO p360_locations(pod_id, name, practice_id, confirmed) VALUES (?, 'Canton - 104', ?, 1)", (pod, h))
        c.execute("UPDATE p360_locations SET practice_id=?, confirmed=1 WHERE name='Canton - 104'", (h,))
    p3 = env["admin"].get(f"/p360/{h}").text
    assert "Referral network" in p3 and "Open referral accounts" in p3


def test_admin_settings(env, sf):
    h = env["havens"]
    env["admin"].post("/field/accounts/settings", data={f"parent_{h}": "001i100000BIRg9AAH", f"loc_{LOC_ROMEO}": str(h)})
    assert env["accounts"].parents()[h] == "001i100000BIRg9AAH"
    assert "parent account in Salesforce" in env["admin"].get(f"/field/accounts?p={h}").text
    env["admin"].post("/field/accounts/settings", data={f"parent_{h}": "not an id"})
    assert env["accounts"].parents()[h] == "001i100000BIRg9AAH"
