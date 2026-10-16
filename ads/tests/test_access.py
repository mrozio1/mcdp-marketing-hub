"""Personal sign-ins and access levels: viewer / editor / admin."""
import importlib
import html as _html
import re
from datetime import date

import pytest


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    for k, v in {"ADS_DB": str(tmp_path / "ads.db"), "ADS_PASSWORD": "team-password-1",
                 "ADS_DISABLE_SCHEDULER": "1", "META_ACCESS_TOKEN": "", "GOOGLE_ADS_LOGIN_CUSTOMER_ID": ""}.items():
        monkeypatch.setenv(k, v)
    from app import auth, db, main
    for m in (db, auth, main):
        importlib.reload(m)
    from fastapi.testclient import TestClient
    admin = TestClient(main.app)
    admin.__enter__()
    admin.post("/login", data={"password": "team-password-1"})          # shared team password = admin
    pid = db.add_practice("Heights Orthodontics", 3000, 900)
    rows = [{"campaign": "Heights - Search", "name": "", "date": date(2026, 10, 5), "date_start": date(2026, 10, 5),
             "date_end": date(2026, 10, 5), "spend": 120.0, "impressions": 900, "clicks": 40, "conversions": 3.0,
             "conv_value": 0.0, "status": "", "budget": 0.0, "match_type": ""}]
    db.save_upload(pid, {"platform": "google", "level": "campaign", "rows": rows, "date_start": date(2026, 10, 5),
                         "date_end": date(2026, 10, 5), "warnings": []}, "oct.csv")
    yield admin, main, db, auth, pid
    admin.__exit__(None, None, None)


def _invite(admin, name, email, role):
    html = admin.post("/team/add", data={"name": name, "email": email, "role": role}).text   # lands on /team
    return re.search(r'id="setup-link" value="[^"]*?(/setup/[A-Za-z0-9_-]+)"', html).group(1)


def _person(main, admin, name, email, role, pw="Correct-horse-9"):
    from fastapi.testclient import TestClient
    link = _invite(admin, name, email, role)
    c = TestClient(main.app)
    c.__enter__()
    r = c.post(link, data={"password": pw, "confirm": pw})
    assert r.status_code == 200 and "Welcome" in r.text
    return c, link


def test_viewer_can_look_run_and_export_but_not_change(app_env, monkeypatch):
    admin, main, db, auth, pid = app_env
    viewer, _ = _person(main, admin, "Pat Viewer", "pat@mcdp.com", "viewer")
    # Sees and exports
    for url in ("/", "/practices", f"/p/{pid}", f"/p/{pid}/report", f"/p/{pid}/posts", f"/p/{pid}/posts.csv",
                f"/p/{pid}/email", f"/p/{pid}/email.eml", "/map", "/map/list"):
        assert viewer.get(url).status_code == 200, url
    page = viewer.get(f"/p/{pid}").text
    assert "view-only" in page and "Practice details" in page
    assert 'action="/p/{}/upload"'.format(pid) not in page and 'action="/p/{}/settings"'.format(pid) not in page
    assert 'class="viewer-ok"' in page                                     # write-up buttons stay
    assert ">Team<" not in page and ">Connections<" not in page and "Viewer</span>" in page
    # Admin-only pages
    assert viewer.get("/connections").status_code == 403 and viewer.get("/team").status_code == 403
    # Changes are refused (and nothing changes)
    r = viewer.post("/practices", data={"name": "Sneaky", "google_budget": 0, "meta_budget": 0})
    assert "needs an Editor or Admin" in r.text and not any(p["name"] == "Sneaky" for p in db.practices())
    viewer.post(f"/p/{pid}/settings", data={"name": "Renamed", "google_budget": 1, "meta_budget": 1, "notes": ""})
    assert db.practice(pid)["name"] == "Heights Orthodontics"
    for url in (f"/p/{pid}/delete", "/sync", f"/p/{pid}/fixes/prepare", f"/p/{pid}/ai/report/delete", "/map/refresh"):
        viewer.post(url)
    assert db.practice(pid) is not None
    r = viewer.post("/map/referrals/mark", json={"key": "x", "starred": True})
    assert r.status_code == 403
    # Running a report is allowed
    from app import ai
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    monkeypatch.setattr(ai, "generate", lambda kind, ctx: "## Report\nAll good.")
    r = viewer.post(f"/p/{pid}/ai/report")
    assert r.status_code == 200 and db.latest_ai(pid, "report")["content"].startswith("## Report")


def test_editor_manages_but_not_keys_or_people(app_env):
    admin, main, db, auth, pid = app_env
    editor, _ = _person(main, admin, "Erin Editor", "erin@mcdp.com", "editor")
    editor.post(f"/p/{pid}/settings", data={"name": "Heights Ortho", "google_budget": 2500, "meta_budget": 800,
                                            "notes": "", "campaign_filter": ""})
    assert db.practice(pid)["name"] == "Heights Ortho"
    assert editor.get("/connections").status_code == 403 and editor.get("/team").status_code == 403
    editor.post("/map/setup", data={"radius": "5"})
    assert db.get_setting("map_radius_miles") is None
    page = editor.get(f"/p/{pid}").text
    assert "view-only" not in page and 'action="/p/{}/upload"'.format(pid) in page


def test_role_change_and_turning_off_take_effect_immediately(app_env):
    admin, main, db, auth, pid = app_env
    viewer, _ = _person(main, admin, "Pat Viewer", "pat@mcdp.com", "viewer")
    uid = auth.by_email("pat@mcdp.com")["id"]
    assert viewer.get("/connections").status_code == 403
    admin.post(f"/team/{uid}/role", data={"role": "admin"})
    assert viewer.get("/connections").status_code == 200                  # no need to sign in again
    admin.post(f"/team/{uid}/access", data={"action": "disable"})
    r = viewer.get("/practices", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = viewer.post("/login", data={"email": "pat@mcdp.com", "password": "Correct-horse-9"})
    assert "don't match" in _html.unescape(r.text)


def test_sign_in_setup_links_and_passwords(app_env):
    admin, main, db, auth, pid = app_env
    from fastapi.testclient import TestClient
    link = _invite(admin, "Sam Smith", "Sam@MCDP.com", "viewer")
    c = TestClient(main.app)
    c.__enter__()
    assert "Choose a password for <strong>sam@mcdp.com</strong>" in c.get(link).text
    assert "at least 10" in c.post(link, data={"password": "short", "confirm": "short"}).text
    assert "don't match" in _html.unescape(c.post(link, data={"password": "Long-enough-1", "confirm": "Long-enough-2"}).text)
    c.post(link, data={"password": "Long-enough-1", "confirm": "Long-enough-1"})
    assert "expired or was already used" in TestClient(main.app).get(link).text   # one use only
    fresh = TestClient(main.app)
    assert "don't match" in _html.unescape(fresh.post("/login", data={"email": "sam@mcdp.com", "password": "wrong-password"}).text)
    r = fresh.post("/login", data={"email": " SAM@mcdp.com ", "password": "Long-enough-1"}, follow_redirects=False)
    assert r.status_code == 303
    assert auth.by_email("sam@mcdp.com")["last_login"]
    stored = auth.by_email("sam@mcdp.com")["pw_hash"]
    assert stored.startswith("pbkdf2_sha256$") and "Long-enough-1" not in stored
    # Duplicate email refused; admins can't change their own access
    admin.post("/team/add", data={"name": "Sam Again", "email": "sam@mcdp.com", "role": "admin"})
    assert len([u for u in auth.users() if u["email"] == "sam@mcdp.com"]) == 1


def test_fix_history_records_who_applied(app_env, monkeypatch):
    admin, main, db, auth, pid = app_env
    editor, _ = _person(main, admin, "Erin Editor", "erin@mcdp.com", "editor")
    from app import actions
    with db.conn() as c:
        c.execute("INSERT INTO changes(practice_id, platform, account, kind, title, payload, created_at) "
                  "VALUES (?,?,?,?,?,?,?)", (pid, "google", "1", "pause", "Pause Heights - Search", "{}", db.now()))
        cid = c.execute("SELECT max(id) FROM changes").fetchone()[0]
    monkeypatch.setattr(actions, "apply_change",
                        lambda ch: (db.update_change(ch["id"], status="applied", applied_at=db.now()), (True, ""))[1])
    editor.post(f"/p/{pid}/fixes/apply", data={"ids": [str(cid)]})
    assert db.change(cid)["applied_by"] == "Erin Editor"
    assert "Pause Heights - Search" in admin.get(f"/p/{pid}/email").text and "(Erin Editor)" in admin.get(f"/p/{pid}/email").text


def test_old_sessions_and_team_password_stay_admin(app_env):
    admin, main, db, auth, pid = app_env
    assert admin.get("/team").status_code == 200 and admin.get("/connections").status_code == 200
    assert auth.needed_role("GET", "/static/style.css") is None
    assert auth.needed_role("POST", "/p/3/ai/report") == "viewer"
    assert auth.needed_role("POST", "/p/3/ai/report/delete") == "editor"
    assert auth.needed_role("POST", "/map/setup") == "admin"
    assert auth.current({"ok": True}) == {"uid": 0, "role": "admin", "name": "Team password"}
