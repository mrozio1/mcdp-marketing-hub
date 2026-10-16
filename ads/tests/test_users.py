"""Individual logins with Viewer / Editor / Admin access."""
import importlib
import os
import re

import pytest


@pytest.fixture(scope="module")
def app_mod(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1", "ADS_PUBLIC_URL": "https://ads.example.test"})
    from app import connectors, db, places, referrals, users
    for m in (db, users, connectors, places, referrals):
        importlib.reload(m)
    from app import main
    importlib.reload(main)
    return main


def client(main):
    from fastapi.testclient import TestClient
    return TestClient(main.app)


def link_token(html):
    return re.search(r"/setup/([A-Za-z0-9_\-]+)", html).group(1)


def add_person(c, name, email, role):
    r = c.post("/team/add", data={"name": name, "email": email, "role": role})
    assert r.status_code == 200, r.text[:300]
    assert f"Setup link for {name}" in r.text
    return link_token(r.text)


def setup(main, token, pw="a-long-password"):
    c = client(main)
    r = c.post(f"/setup/{token}", data={"password": pw, "confirm": pw})
    assert r.status_code == 200 and "You&#39;re signed in" in r.text, r.text[:300]
    return c


@pytest.fixture(scope="module")
def people(app_mod):
    main = app_mod
    from app import db
    with client(main) as boss:
        # Before anyone has an account, the shared password signs in as a temporary admin.
        r = boss.post("/login", data={"password": "shared-pw"})
        assert "Add yourself below as an" in r.text
        admin_tok = add_person(boss, "Ann Admin", "ann@example.com", "admin")
        ann = setup(main, admin_tok)
        # The shared password stops working once an admin account is set up.
        assert boss.get("/", follow_redirects=False).headers["location"] == "/login"
        r = client(main).post("/login", data={"password": "shared-pw"})
        assert "isn&#39;t right" in r.text
        ed = setup(main, add_person(ann, "Ed Editor", "ed@example.com", "editor"))
        vi = setup(main, add_person(ann, "Vi Viewer", "vi@example.com", "viewer"))
        pid = db.add_practice("Heights", 1000, 500)
        yield {"main": main, "db": db, "ann": ann, "ed": ed, "vi": vi, "pid": pid}


def test_login_by_email(people):
    main = people["main"]
    c = client(main)
    assert "don&#39;t match" in c.post("/login", data={"email": "vi@example.com", "password": "nope-nope-nope"}).text
    r = c.post("/login", data={"email": "VI@example.com", "password": "a-long-password"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "view only" in c.get("/").text


def test_viewer_sees_and_exports_but_changes_nothing(people):
    vi, db, pid = people["vi"], people["db"], people["pid"]
    for url in ("/", "/practices", f"/p/{pid}", "/map", "/map/list", f"/p/{pid}/report", f"/p/{pid}/email",
                "/map/referrals.xlsx", "/map/referrals.json?office=&kind=referrer", "/account"):
        r = vi.get(url, follow_redirects=False)
        assert r.status_code == 200, (url, r.status_code)
    html = vi.get(f"/p/{pid}").text
    assert 'action="/p/%d/upload"' % pid not in html and "Delete this practice" not in html
    assert "Add a practice" not in vi.get("/practices").text
    assert 'href="/connections"' not in html and 'href="/team"' not in html
    m = vi.get("/map").text
    assert 'action="/map/setup"' not in m and 'action="/map/offices"' not in m
    assert "Build the lists" not in vi.get("/map/list").text
    # Changes are refused on the server, not just hidden.
    r = vi.post("/practices", data={"name": "Sneaky"}, headers={"referer": "http://testserver/practices"})
    assert "You have view-only access" in r.text
    assert not [p for p in db.practices() if p["name"] == "Sneaky"]
    r = vi.post("/map/referrals/mark", json={"key": "x", "starred": True})
    assert r.status_code == 403 and "view-only" in r.json()["error"]
    for url in ("/sync", f"/p/{pid}/settings", f"/p/{pid}/ai/report", "/map/refresh", "/team/add"):
        assert vi.post(url, follow_redirects=False).status_code == 303
    assert vi.get("/connections", follow_redirects=False).headers["location"] == "/"
    assert vi.get("/team", follow_redirects=False).headers["location"] == "/"


def test_editor_changes_but_not_admin_things(people):
    ed, db, pid = people["ed"], people["db"], people["pid"]
    ed.post("/practices", data={"name": "Havens", "google_budget": "0", "meta_budget": "0"})
    assert [p for p in db.practices() if p["name"] == "Havens"]
    html = ed.get(f"/p/{pid}").text
    assert f'action="/p/{pid}/upload"' in html and "Delete this practice" not in html
    r = ed.post(f"/p/{pid}/delete", follow_redirects=True)
    assert "That needs admin access" in r.text and db.practice(pid)
    assert ed.get("/connections", follow_redirects=False).status_code == 303
    assert 'action="/map/setup"' not in ed.get("/map").text


def test_admin_manages_people(people):
    ann, main = people["ann"], people["main"]
    from app import users
    team = ann.get("/team").text
    assert "Ed Editor" in team and "Vi Viewer" in team and "Team" in team
    vi_id = users.by_email("vi@example.com")["id"]
    # Promote: takes effect on the person's next click, no sign-out needed.
    ann.post(f"/team/{vi_id}/role", data={"role": "editor"})
    assert "view only" not in people["vi"].get("/").text
    ann.post(f"/team/{vi_id}/role", data={"role": "viewer"})
    # Switching someone off signs them out straight away.
    ann.post(f"/team/{vi_id}/active", data={"active": ""})
    assert people["vi"].get("/", follow_redirects=False).headers["location"] == "/login"
    ann.post(f"/team/{vi_id}/active", data={"active": "1"})
    # The last admin can't be demoted or switched off.
    ann_id = users.by_email("ann@example.com")["id"]
    r = ann.post(f"/team/{ann_id}/role", data={"role": "viewer"}, follow_redirects=True)
    assert "Keep at least one admin" in r.text and users.get(ann_id)["role"] == "admin"
    assert "can&#39;t switch off your own" in ann.post(f"/team/{ann_id}/active", data={"active": ""}, follow_redirects=True).text


def test_setup_links(people):
    ann, main = people["ann"], people["main"]
    tok = add_person(ann, "Sam Short", "sam@example.com", "viewer")
    c = client(main)
    assert "at least 10" in c.post(f"/setup/{tok}", data={"password": "short", "confirm": "short"}).text
    assert "don&#39;t match" in c.post(f"/setup/{tok}", data={"password": "a-long-password", "confirm": "different-one"}).text
    setup(main, tok)
    assert "expired or has already been used" in client(main).get(f"/setup/{tok}").text   # single use
    assert "already has an account" in ann.post("/team/add", data={"name": "X", "email": "SAM@example.com",
                                                                  "role": "viewer"}, follow_redirects=True).text
    # Reset password: old password stops working, the new link works.
    from app import users
    sid = users.by_email("sam@example.com")["id"]
    tok2 = link_token(ann.post(f"/team/{sid}/link").text)
    assert client(main).post("/login", data={"email": "sam@example.com", "password": "a-long-password"},
                             follow_redirects=False).status_code == 200      # refused (page re-shown)
    setup(main, tok2, "another-long-password")


def test_change_own_password(people):
    main = people["main"]
    vi = client(main)          # (switched off and on again above, which signed the old session out)
    vi.post("/login", data={"email": "vi@example.com", "password": "a-long-password"})
    vi2 = client(main)
    vi2.post("/login", data={"email": "vi@example.com", "password": "a-long-password"})
    assert "isn&#39;t right" in vi.post("/account/password", data={"current": "wrong", "password": "x" * 12, "confirm": "x" * 12}).text
    r = vi.post("/account/password", data={"current": "a-long-password", "password": "new-long-password",
                                           "confirm": "new-long-password"}, follow_redirects=True)
    assert "Password changed" in r.text
    assert vi.get("/", follow_redirects=False).status_code == 200                       # this device stays in
    assert vi2.get("/", follow_redirects=False).headers["location"] == "/login"           # others signed out


def test_cli_reset(people, capsys):
    from app import users
    assert users._cli(["reset", "ann@example.com"]) == 0
    assert "https://ads.example.test/setup/" in capsys.readouterr().out
    assert users._cli(["reset", "nobody@example.com"]) == 1


def test_applied_by_is_recorded(people):
    db, pid = people["db"], people["pid"]
    from app import actions
    with db.conn() as conn:
        cid = conn.execute("INSERT INTO changes(practice_id, platform, account, kind, title, payload, created_at) "
                           "VALUES (?, 'google', '1', 'negative', 'Block a search', '{}', ?)", (pid, db.now())).lastrowid
    actions._apply_google = lambda ch: {"ok": True}
    ok, _ = actions.apply_change(db.change(cid), "Ed Editor")
    assert ok and db.change(cid)["applied_by"] == "Ed Editor"


def test_upgrades_accounts_from_the_earlier_build(tmp_path, monkeypatch):
    """The other access build may already have run on the server; its people keep their sign-ins."""
    import hashlib
    import sqlite3
    from app import db, users
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
      role TEXT NOT NULL DEFAULT 'viewer', pw_hash TEXT DEFAULT '', setup_token TEXT DEFAULT '',
      setup_expires TEXT DEFAULT '', disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_login TEXT DEFAULT '');""")
    salt = b"0123456789abcdef"
    dk = hashlib.pbkdf2_hmac("sha256", b"old-long-password", salt, 1000)
    con.execute("INSERT INTO users(name,email,role,pw_hash,created_at) VALUES ('Old Admin','old@example.com','admin',?, '2026-10-16')",
                (f"pbkdf2_sha256$1000${salt.hex()}${dk.hex()}",))
    con.execute("INSERT INTO users(name,email,role,setup_token,setup_expires,disabled,created_at) "
                "VALUES ('Pending','pend@example.com','viewer','tok123','2099-01-01',0,'2026-10-16')")
    con.execute("INSERT INTO users(name,email,role,disabled,created_at) VALUES ('Off','off@example.com','viewer',1,'2026-10-16')")
    con.commit(); con.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))
    users.init()
    assert users.check_login("OLD@example.com", "old-long-password")["role"] == "admin"
    assert users.any_admin()
    assert users.by_token("tok123")["email"] == "pend@example.com"
    assert not users.by_email("off@example.com")["active"]
    users.init()   # running again changes nothing
    assert users.check_login("old@example.com", "old-long-password")
