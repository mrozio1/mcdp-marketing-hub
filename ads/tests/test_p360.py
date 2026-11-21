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
    assert "Operations" in page and "Finance" not in page
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
