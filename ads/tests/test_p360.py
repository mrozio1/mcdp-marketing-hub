"""Practice 360: departments switched on per person, owners, the ledger (notes, action items, follow-ups,
screenshots) and pins that keep what the dashboard showed when something was written."""
import html
import importlib
import io
import os
import re

import pytest
from PIL import Image


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw", "ADS_DISABLE_SCHEDULER": "1",
                       "ADS_VIEWER_DEFAULTS": "real"})
    from app import db, p360, users
    for m in (db, users, p360):
        importlib.reload(m)
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    def person(admin, name, email, role):
        r = admin.post("/team/add", data={"name": name, "email": email, "role": role, "social_on": "1"})
        tok = re.search(r"/setup/([\w-]+)", r.text).group(1)
        c = TestClient(main.app)
        c.post(f"/setup/{tok}", data={"password": "long-password-1", "confirm": "long-password-1"})
        return c

    with TestClient(main.app) as boss:
        boss.post("/login", data={"password": "shared-pw"})
        admin = person(boss, "Ann Admin", "ann@example.com", "admin")
        ed = person(admin, "Ed Editor", "ed@example.com", "editor")
        vi = person(admin, "Vi Viewer", "vi@example.com", "viewer")
        havens = db.add_practice("Havens Orthodontics", 0, 0, "havens")
        other = db.add_practice("Heights Orthodontics", 0, 0, "heights")
        ids = {u["email"]: u["id"] for u in users.all_users()}
        yield {"main": main, "db": db, "p360": p360, "users": users, "admin": admin, "ed": ed, "vi": vi, "havens": havens,
               "other": other, "ids": ids}
    os.environ["ADS_VIEWER_DEFAULTS"] = "legacy"


def png():
    b = io.BytesIO()
    Image.new("RGB", (40, 30), (44, 99, 150)).save(b, "PNG")
    return b.getvalue()


def test_access_follows_team_settings(env):
    ad, ed, vi, ids = env["admin"], env["ed"], env["vi"], env["ids"]
    assert "Practice 360" in ad.get("/p360").text and "Revenue Management" in ad.get(f"/p360/{env['havens']}").text
    assert vi.get("/p360", follow_redirects=False).status_code in (303, 403)          # viewers start with it off
    # the Team drawer has the department switches; give the viewer the tab, Operations and Havens only
    assert "Practice 360 departments" in ad.get("/team").text
    ad.post(f"/team/{ids['vi@example.com']}/access", data={"p360": "1", "d_operations": "1", "practice": str(env["havens"])})
    page = vi.get(f"/p360/{env['havens']}").text
    assert 'id="lane-operations"' in page and 'id="lane-finance"' not in page
    assert vi.get(f"/p360/{env['other']}", follow_redirects=False).status_code == 303   # not one of their practices
    # a viewer can't add, unless they own the department
    r = vi.post(f"/p360/{env['havens']}/ledger", data={"dept": "operations", "kind": "note", "title": "Hi"}, follow_redirects=True)
    assert "can't add to Operations here" in html.unescape(r.text)
    ad.post("/p360/settings", data={"owner_operations": str(ids["vi@example.com"]), "owner_revenue": str(ids["ed@example.com"])})
    assert env["p360"].owners()["operations"] == [ids["vi@example.com"]]
    r = vi.post(f"/p360/{env['havens']}/ledger", data={"dept": "operations", "kind": "note", "title": "Same-day starts"}, follow_redirects=True)
    assert "Added to the ledger" in html.unescape(r.text)
    r = vi.post(f"/p360/{env['havens']}/ledger", data={"dept": "finance", "kind": "note", "title": "Sneaky"}, follow_redirects=True)
    assert "can't add to Finance here" in html.unescape(r.text)                                                       # owner of Operations only
    assert ed.get("/p360/settings", follow_redirects=False).status_code == 303          # owners are set by admins


def test_ledger_pins_followups_and_screenshots(env):
    p, ed, ids, pid = env["p360"], env["ed"], env["ids"], env["havens"]
    p.save_metrics([{"practice_id": pid, "metric": "Supplies %", "period": "2026-10", "value": 8, "shown": "8%"}], "test")
    r = ed.post(f"/p360/{pid}/ledger", data={"dept": "procurement", "kind": "action", "title": "Aligner pricing",
                                            "text": "Old account", "metric": "Supplies %", "owner_id": str(ids["ann@example.com"]),
                                            "due": "2020-01-01"}, files=[("shot", ("a.png", png(), "image/png"))], follow_redirects=False)
    eid = int(r.headers["location"].split("e=")[1])
    e = p.entry(eid)
    assert e["state"] == "overdue" and e["owner_id"] == ids["ann@example.com"]
    first = p.events(eid)[0]
    assert first["pin"]["shown"] == "8%" and first["pin"]["period"] == "2026-10" and len(first["shots"]) == 1
    sid = first["shots"][0]["id"]
    assert ed.get(f"/p360/shot/{sid}").content[:4] == b"\x89PNG"
    # numbers move; the first pin keeps what was shown, the follow-up pins today's
    p.save_metrics([{"practice_id": pid, "metric": "Supplies %", "period": "2026-11", "value": 7, "shown": "7%"}], "test")
    ed.post(f"/p360/entry/{eid}/follow", data={"text": "Approved", "status": "done", "pin_now": "1"})
    ev = p.events(eid)
    assert ev[0]["pin"]["shown"] == "8%" and ev[-1]["pin"]["shown"] == "7%" and ev[-1]["status"] == "done"
    assert p.entry(eid)["state"] == "done"
    page = ed.get(f"/p360/{pid}/ledger?e={eid}").text
    assert "Dashboard then: Supplies % 8%" in html.unescape(page) and "Approved" in page
    # someone without Procurement can't see the entry or its screenshot
    assert env["vi"].get(f"/p360/shot/{sid}", follow_redirects=False).status_code in (303, 404)
    bad = ed.post(f"/p360/{pid}/ledger", data={"dept": "procurement", "kind": "note", "title": "x"},
                  files=[("shot", ("a.txt", b"hello", "text/plain"))], follow_redirects=True)
    assert "PNG, JPG" in html.unescape(bad.text)


def test_pods_from_mcdp_reports(env):
    p, db, ad, ed = env["p360"], env["db"], env["admin"], env["ed"]
    flushing = db.add_practice("Flushing Orthodontics", 0, 0, "flushing")
    wesley = db.add_practice("Wesley Orthodontics", 0, 0, "wesley")
    pods = {x["name"]: x for x in p.pods()}
    assert len(pods) == 21 and pods["Dr. Wesley"]["practice_ids"] == [wesley]          # guessed from "(Wesley)"
    assert flushing in pods["Dr. A. Havens"]["practice_ids"]                              # "Flushing - 105"
    assert env["havens"] in pods["Dr. Paron"]["practice_ids"]                              # "Farmington (Havens) - 113"
    page = ad.get(f"/p360?pod={pods['Dr. Wesley']['id']}").text
    assert "Wesley Orthodontics" in page and "Flushing Orthodontics" not in page.split("data-p3-list")[1]
    # an admin corrects a guess; the pod page shows the pod's own numbers and its practices' items
    loc = next(l for l in pods["Dr. Paron"]["locations"] if l["name"].startswith("Lapeer"))
    ad.post("/p360/pods", data={f"loc_{loc['id']}": ""})
    assert all(l["practice_id"] is None for l in p.pod(pods["Dr. Paron"]["id"])["locations"] if l["id"] == loc["id"])
    p.save_pod_metrics([{"pod_id": pods["Dr. Wesley"]["id"], "metric": "Starts", "period": "2026-10", "value": 40, "shown": "40"}], "test")
    eid = p.add_entry(wesley, "operations", "action", "Wesley follow-up calls", "", env["users"].get(env["ids"]["ed@example.com"]))
    page = ed.get(f"/p360/pod/{pods['Dr. Wesley']['id']}").text
    assert "Wesley follow-up calls" in page and ">40<" in page.replace(" ", "")
    assert ed.get("/p360/pods", follow_redirects=False).status_code == 303               # admins only


def test_import_from_mcdp_reports(env):
    p, db, ad = env["p360"], env["db"], env["admin"]
    from fastapi.testclient import TestClient
    anon = TestClient(env["main"].app)
    key = ad.post("/p360/import-key").json()["key"]
    assert env["ed"].post("/p360/import-key").status_code == 403                       # admins only
    wesley = next(x for x in db.practices(include_hidden=True) if x["name"] == "Wesley Orthodontics")["id"]
    locs = [{"pod": "Dr. Wesley", "name": n, "summary": {"labs": "5%", "wages": w, "supplies": "7%", "exams": e, "starts": s,
                                                          "net": net, "rpp": "3", "ytd": "$100"},
             "months": {"production": [["Oct", "2025", ly], ["Oct", "2026", net], ["Nov", "2026", "$5"]], "starts": [["Oct", "2026", s]],
                        "exams": [["Oct", "2026", e]]}}
            for n, w, e, s, net, ly in (("Fort Gratiot (Wesley) - 126", "30%", "20", "15", "$60000", "$54000"),
                                        ("Marysville (Wesley) - 125", "40%", "10", "9", "$40000", "$36000"))]
    body = {"date_range": "10/01/2026 - 10/31/2026", "locations": locs}
    assert anon.post("/p360-in/import?k=wrong", content=json_dumps(body)).status_code == 403
    r = anon.post(f"/p360-in/import?k={key}", content=json_dumps(body))
    assert r.json()["ok"] and r.json()["period"] == "2026-10" and r.headers["access-control-allow-origin"] == "https://drapp.mcdentalpartners.com"
    assert anon.post(f"/p360-in/import?k={key}", content=json_dumps(body)).status_code == 403     # one use
    pin = lambda m: p.pin_for(wesley, m)
    assert pin("Net production")["shown"] == "$100.0K" and pin("Starts")["shown"] == "24"     # November (in progress) left out
    assert pin("Success rate")["shown"] == "80.0%"                                    # 24 starts / 30 exams, not an average
    assert pin("Wages %")["shown"] == "34%"                                           # weighted by production: (30*60 + 40*40) / 100
    assert pin("Production growth")["shown"] == "+11.1%"                             # 100K vs 90K last October
    assert pin("RPP rate")["shown"] == "25.0%"                                        # 6 / 24
    pod = next(x for x in p.pods() if x["name"] == "Dr. Wesley")
    assert p.pod_pin(pod["id"], "Net production")["shown"] == "$100.0K"
    # only practices in MCDP Reports are listed
    db.add_practice("MCDP Ads", 0, 0, "")
    assert "MCDP Ads" not in ad.get("/p360").text


def json_dumps(x):
    import json
    return json.dumps(x)


def test_changing_a_match_moves_the_numbers(env):
    p, db, ad = env["p360"], env["db"], env["admin"]
    wesley = next(x for x in db.practices(include_hidden=True) if x["name"] == "Wesley Orthodontics")["id"]
    assert p.pin_for(wesley, "Starts")["shown"] == "24"
    pod = next(x for x in p.pods() if x["name"] == "Dr. Wesley")
    assert {l["name"] for l in pod["locations"]} == {"Fort Gratiot (Wesley) - 126", "Marysville (Wesley) - 125"}   # only what drapp listed
    mary = next(l for l in pod["locations"] if l["name"].startswith("Marysville"))
    ad.post("/p360/pods", data={f"loc_{mary['id']}": ""})                              # Marysville isn't Wesley's
    assert p.pin_for(wesley, "Starts")["shown"] == "15"                               # no new import needed


def test_marketing_numbers_from_the_hub(env):
    p, db = env["p360"], env["db"]
    from datetime import date
    wesley = next(x for x in db.practices(include_hidden=True) if x["name"] == "Wesley Orthodontics")["id"]
    with db.conn() as c:
        c.execute("INSERT INTO ls_counts(section, day, account, bookings, views) VALUES ('source', '2026-10-05', 'Wesley Orthodontics', 4, 40)")
    n = p.hub_marketing(today=date(2026, 11, 21))
    assert n > 0
    assert p.pin_for(wesley, "Online bookings")["shown"] == "4" and p.pin_for(wesley, "Online bookings")["period"] == "2026-10"
    assert p.pin_for(wesley, "Referring office visits")["period"] == "2026-10"
    assert p.pin_for(wesley, "Starts")["shown"] == "15"                                # drapp's numbers untouched
    assert "Refresh marketing now" in env["ed"].get(f"/p360/{wesley}").text


def test_home_dashboard_uses_mcdp_reports(env):
    page = env["admin"].get("/").text
    assert "MCDP Reports · Scorecard" in page and "Ad spend per start" not in page or "MCDP Reports" in page
    assert ">24<" in page.replace(" ", "")                                            # starts in the funnel (Fort Gratiot + Marysville were 15 + 9)
