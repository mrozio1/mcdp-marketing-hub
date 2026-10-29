"""Seasonal look: which theme shows on which day, the admin panel, previews, and each person's own switch."""
import importlib
import json
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
    from app import db, seasons, users
    for m in (db, users, seasons):
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
        viewer = person(admin, "Vi Viewer", "vi@example.com", "viewer")
        yield {"db": db, "seasons": seasons, "admin": admin, "viewer": viewer}


def test_which_theme_shows_when(env):
    s = env["seasons"]
    cfg = json.loads(json.dumps(s.DEFAULTS))
    on = lambda d: s.current(date.fromisoformat(d), cfg)[0]
    assert s.thanksgiving(2026) == date(2026, 11, 26) and s.thanksgiving(2027) == date(2027, 11, 25)
    assert on("2026-10-29") == "halloween" and s.current(date(2026, 10, 29), cfg)[1] == date(2026, 10, 31)
    assert on("2026-11-26") == "thanksgiving" and on("2026-11-27") is None and on("2026-12-15") == "christmas"
    assert on("2026-12-31") == "newyear" and on("2027-01-04") == "newyear"          # across the new year
    assert on("2026-07-04") == "july4" and on("2026-07-20") == "summer" and on("2026-08-20") == "school"   # the shorter one wins
    assert on("2026-02-14") == "valentines" and on("2026-03-17") == "stpatricks" and on("2026-04-10") == "spring"
    assert on("2026-05-15") is None
    cfg["auto"]["halloween"] = False
    assert on("2026-10-29") is None
    cfg["mode"] = "christmas"
    assert on("2026-06-01") == "christmas"
    cfg["mode"] = "off"
    assert on("2026-12-15") is None


def test_admin_panel_preview_and_personal_switch(env, monkeypatch):
    a, v, s, db = env["admin"], env["viewer"], env["seasons"], env["db"]
    monkeypatch.setattr(s, "current", lambda today=None, s=None: ("halloween", date(2026, 10, 31)))
    team = a.get("/team").text
    assert "Seasonal look" in team and "Showing now: Halloween" in team and "St. Patrick" in team and "Back to School" in team
    assert "?season_preview=thanksgiving" in team
    page = v.get("/").text
    assert '"theme": "halloween"' in page and "/static/seasonal.js" in page
    # previews are for admins only
    assert '"theme": "thanksgiving"' in a.get("/?season_preview=thanksgiving").text
    assert '"theme": "halloween"' in v.get("/?season_preview=thanksgiving").text
    # each person can switch it off on their Account page
    acct = v.get("/account").text
    assert "Seasonal decorations" in acct and "Off for me" in acct
    v.post("/account/seasonal", data={"on": "0"})
    assert "seasonal.js" not in v.get("/").text and "seasonal.js" in a.get("/").text
    v.post("/account/seasonal", data={"on": "1"})
    assert "seasonal.js" in v.get("/").text
    # admin settings: off, one theme, pieces, dates
    r = a.post("/team/seasonal", data={"mode": "off", "buddy": "1", "amount": "normal"}, follow_redirects=True)
    assert "Seasonal look saved" in r.text
    cfg = s.settings()
    assert cfg["mode"] == "off" and cfg["buddy"] and not cfg["falling"] and cfg["amount"] == "normal" and not cfg["optout"]
    assert "Seasonal decorations" not in v.get("/account").text     # no switch when people can't opt out
    r = a.post("/team/seasonal", data={"mode": "auto", "buddy": "1", "falling": "1", "corners": "1", "optout": "1",
                                       "start_halloween": "10-15", "end_halloween": "10-31", "auto_halloween": "1",
                                       "start_spring": "13-40", "end_spring": "04-30"}, follow_redirects=True)
    assert "Spring" in r.text and "weren" in r.text
    cfg = s.settings()
    assert cfg["dates"]["halloween"] == ["10-15", "10-31"] and cfg["dates"]["spring"] == ["03-20", "04-30"]
    assert cfg["auto"]["halloween"] and not cfg["auto"]["christmas"]       # unticked seasons are skipped
    # viewers can't change it
    v.post("/team/seasonal", data={"mode": "off"})
    assert s.settings()["mode"] == "auto"
    db.set_setting("seasonal", None)
