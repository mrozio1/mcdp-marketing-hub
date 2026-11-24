"""Per-person access: tabs, reports, refreshing, practices, everyone's visits and calendar, admins' activity,
calendar colour, and the defaults for each access level (viewers start with nothing)."""
import importlib
import os
import re
from datetime import date, timedelta

import pytest

from tests.test_calendar import form


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1", "ADS_VIEWER_DEFAULTS": "real"})
    from app import cal, db, field, places, users
    for m in (db, users, places, field, cal):
        importlib.reload(m)
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    def person(admin, name, email, role, field_on=False):
        r = admin.post("/team/add", data={"name": name, "email": email, "role": role,
                                          "field_on": "1" if field_on else "", "social_on": "1"})
        tok = re.search(r"/setup/([\w-]+)", r.text).group(1)
        c = TestClient(main.app)
        c.post(f"/setup/{tok}", data={"password": "long-password-1", "confirm": "long-password-1"})
        return c

    with TestClient(main.app) as boss:
        boss.post("/login", data={"password": "shared-pw"})
        admin = person(boss, "Ann Admin", "ann@example.com", "admin")
        heights = db.add_practice("Heights Orthodontics", 1000, 500, "heights")
        havens = db.add_practice("Havens Orthodontics", 0, 0, "havens")
        places.add_office("Heights – Defiance", "ChIJ_office_heights01", 41.28, -84.35)
        places.add_office("Havens – Toledo", "ChIJ_office_havens001", 41.60, -83.55)
        offs = {o["label"]: o["id"] for o in places.offices()}
        field.set_links({offs["Heights – Defiance"]: heights, offs["Havens – Toledo"]: havens})
        vi = person(admin, "Vi Viewer", "vi@example.com", "viewer", field_on=True)
        ed = person(admin, "Ed Editor", "ed@example.com", "editor")
        ids = {u["email"]: u["id"] for u in users.all_users()}
        yield {"main": main, "db": db, "users": users, "cal": cal, "field": field, "admin": admin, "vi": vi, "ed": ed,
               "heights": heights, "havens": havens, "ids": ids, "offs": offs}
    os.environ["ADS_VIEWER_DEFAULTS"] = "legacy"


def flash(r):
    m = re.search(r'class="flash[^"]*"[^>]*>(.*?)</p>', r.text, re.S)
    return m.group(1) if m else ""


def test_role_defaults(env):
    users, ids = env["users"], env["ids"]
    assert all(users.access(users.get(ids["ann@example.com"])).values())          # admins: everything
    ea = users.access(users.get(ids["ed@example.com"]))
    assert all(v for k, v in ea.items() if k != "d_hr") and not ea["d_hr"]       # editors: everything but HR numbers
    vi = users.access(users.get(ids["vi@example.com"]))
    assert vi["field"] and not any(v for k, v in vi.items() if k != "field")       # viewers: nothing (field visits opened Field)
    assert users.practice_ids(users.get(ids["vi@example.com"])) == set()


def test_viewer_with_nothing_lands_on_account(env):
    users, vi_id = env["users"], env["ids"]["vi@example.com"]
    users.set_access(vi_id, {"field": False})
    r = env["vi"].get("/")
    assert r.url.path == "/account"
    nav = re.search(r'<nav aria-label="Main">(.*?)</nav>', r.text, re.S).group(1)
    assert "/practices" not in nav and "/field" not in nav and "/calendar" not in nav and "/account" in nav
    r = env["vi"].get("/practices")
    assert r.url.path == "/account" and "Practices isn&#39;t switched on" in flash(r)


def test_tabs_practices_reports_and_refresh(env):
    users, vi, vi_id = env["users"], env["vi"], env["ids"]["vi@example.com"]
    h, hv = env["heights"], env["havens"]
    env["admin"].post(f"/team/{vi_id}/access", data={"field": "1", "practices": "1", "calendar": "1", "practice": [str(h)]})
    assert users.practice_ids(users.get(vi_id)) == {h}
    assert vi.get("/").url.path == "/practices"                                   # dashboard off: their first tab
    page = vi.get("/practices").text
    assert "Heights Orthodontics" in page and "Havens Orthodontics" not in page
    assert vi.get(f"/p/{h}").status_code == 200 and vi.get(f"/p/{h}").url.path == f"/p/{h}"
    r = vi.get(f"/p/{hv}")
    assert r.url.path != f"/p/{hv}" and "isn&#39;t one of yours" in flash(r)
    r = vi.get("/reports")
    assert r.url.path != "/reports" and "Reports and exports" in flash(r)
    assert "Export these visits" not in vi.get("/field").text
    r = vi.post("/sync", data={"back": "/practices"})
    assert "Refreshing and syncing" in flash(r)
    assert vi.get("/reviews").url.path != "/reviews"                              # tab off
    # field visits: only their practices to pick from, and only those can be logged
    log = vi.get("/field/log?place=ChIJ_office_havens001").text
    assert "Heights Orthodontics" in log and "Havens Orthodontics" not in log
    r = vi.post("/field/visits", data={"place_id": "", "name": "Elm Dental", "rating": "warm", "practice_id": str(hv),
                                       "visited_at": date.today().isoformat() + "T10:00"}, follow_redirects=True)
    assert "only log visits for your own practices" in r.text
    # reports switched on: in they go
    users.set_access(vi_id, {"reports": True})
    assert vi.get("/reports").url.path == "/reports"
    users.set_access(vi_id, {"refresh": True})
    assert "Refreshing and syncing" not in flash(vi.post("/sync", data={"back": "/practices"}))


def test_map_keeps_to_their_practices_and_visits(env):
    users, vi, vi_id = env["users"], env["vi"], env["ids"]["vi@example.com"]
    data = vi.get("/map/data.json").json()               # Field on: the map's data is open to them
    labels = {o["label"] for o in data["offices"]}
    assert labels == {"Heights – Defiance"}
    assert len(env["admin"].get("/map/data.json").json()["offices"]) == 2


def test_calendar_own_items_invites_and_colour(env):
    users, cal, ids = env["users"], env["cal"], env["ids"]
    a, vi, ed = env["admin"], env["vi"], env["ed"]
    d = (date.today() + timedelta(days=2))
    a.post("/calendar/events", data=form(env["heights"], place_label="Oak Family Dental"), follow_redirects=True)
    a.post("/calendar/events", data={**form(env["heights"], place_label="Birch Dental"), "person_name": ["Vi Viewer"],
                                     "person_email": ["vi@example.com"], "person_required": ["1"],
                                     "person_source": ["team"]}, follow_redirects=True)
    page = vi.get(f"/calendar?view=month&d={d.isoformat()}").text
    assert "Birch Dental" in page and "Oak Family Dental" not in page             # only what they're invited to
    users.set_access(ids["vi@example.com"], {"team_calendar": True})
    assert "Oak Family Dental" not in vi.get(f"/calendar?view=month&d={d.isoformat()}").text   # still not admins'
    users.set_access(ids["vi@example.com"], {"see_admins": True})
    assert "Oak Family Dental" in vi.get(f"/calendar?view=month&d={d.isoformat()}").text
    # editors see everyone's by default; an admin can hide admins' activity from them
    assert "Oak Family Dental" in ed.get(f"/calendar?view=month&d={d.isoformat()}").text
    users.set_access(ids["ed@example.com"], {"see_admins": False})
    page = ed.get(f"/calendar?view=month&d={d.isoformat()}").text
    assert "Oak Family Dental" not in page and "Birch Dental" not in page
    with env["db"].conn() as c:
        eid = c.execute("SELECT id FROM cal_events WHERE place_label='Oak Family Dental'").fetchone()[0]
    assert "Oak Family Dental" not in ed.get(f"/calendar?event={eid}&d={d.isoformat()}").text
    # a colour of their own: the admin's events show in it, with their name in the key
    a.post(f"/team/{ids['ann@example.com']}/access", data={**{k: "1" for k in users.FLAGS}, "own_color": "1",
                                                          "cal_color": "#8E5BC9"})
    page = vi.get(f"/calendar?view=month&d={d.isoformat()}").text
    assert "--c: #8E5BC9" in page and ">Ann Admin<" in page


def test_role_change_and_reset(env):
    users, ids, a = env["users"], env["ids"], env["admin"]
    vid = ids["vi@example.com"]
    a.post(f"/team/{vid}/role", data={"role": "editor"})
    assert all(v for k, v in users.access(users.get(vid)).items() if k != "d_hr")   # a new level starts from its defaults
    a.post(f"/team/{vid}/role", data={"role": "viewer"})
    assert not any(users.access(users.get(vid)).values())
    users.set_access(vid, {"map": True})
    a.post(f"/team/{vid}/access", data={"reset": "1"})
    assert not users.access(users.get(vid))["map"]
    team = a.get(f"/team?open={vid}").text
    assert "What Vi can open" in team and "No tabs (Account page only)" in team


def test_existing_viewers_keep_their_access(env):
    users, db = env["users"], env["db"]
    with db.conn() as c:
        c.execute("INSERT INTO users(email, name, role, created_at, field_manager, perms) "
                  "VALUES ('old@example.com', 'Old Viewer', 'viewer', ?, 1, '')", (db.now(),))
        users._keep_current_access(c)
    a = users.access(users.by_email("old@example.com"))
    assert a["practices"] and a["reports"] and a["all_practices"] and a["team_field"] and not a["refresh"]
