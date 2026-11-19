"""Marketing intake: private links, yes/no form, save and come back, encrypted passwords shown only to admins
(each look logged), switching links off, and editors-only list."""
import importlib
import os
import re

import pytest


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    d = tmp_path_factory.mktemp("db")
    os.environ.update({"ADS_DB": str(d / "ads.db"), "ADS_PASSWORD": "shared-pw", "ADS_DISABLE_SCHEDULER": "1"})
    os.environ.pop("INTAKE_KEY", None)
    from app import db, intake, users
    for m in (db, users, intake):
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
        pid = db.add_practice("Heights Orthodontics", 1000, 500, "heights")
        yield {"main": main, "db": db, "intake": intake, "admin": admin, "ed": ed, "vi": vi, "pid": pid,
               "anon": TestClient(main.app), "dir": d}


def test_flow(env):
    admin, ed, anon, intake, db = env["admin"], env["ed"], env["anon"], env["intake"], env["db"]
    assert 'href="/intake"' in ed.get("/practices").text
    r = ed.post("/intake/new", data={"practice_id": env["pid"]}, follow_redirects=True)
    link = re.search(r'value="([^"]+/i/[^"]+)"', r.text).group(1)
    token = link.rsplit("/i/", 1)[1]

    r = anon.get(f"/i/{token}")
    assert r.status_code == 200 and "Does the practice use Facebook?" in r.text
    assert 'value="Heights Orthodontics"' in r.text          # practice name filled in
    assert "ed@example.com" in r.text                         # access email = whoever made the link
    r = anon.post(f"/i/{token}", data={"about.name": "Pat", "social.facebook.use": "yes",
                                       "social.facebook.login_pass": "s3cret!", "social.facebook.third": "yes",
                                       "social.facebook.third_name": "Agency X", "_save": "1"}, follow_redirects=True)
    assert "Saved." in r.text and "s3cret" not in r.text
    raw = open(os.environ["ADS_DB"], "rb").read()
    assert b"s3cret" not in raw and (env["dir"] / "intake.key").exists()
    f = intake.by_token(token)
    assert f["data"]["about.name"] == "Pat" and f["secret_keys"] == ["social.facebook.login_pass"]

    anon.post(f"/i/{token}", data={"about.name": "Pat", "social.facebook.use": "yes", "social.facebook.login_pass": "",
                                   "social.facebook.third": "yes", "social.facebook.third_name": "Agency X", "_submit": "1"})
    f = intake.get(f["id"])
    assert f["submitted_at"] and f["secret_keys"] == ["social.facebook.login_pass"]   # blank box kept it

    r = ed.get(f"/intake/{f['id']}")
    assert "Agency X" in r.text and "s3cret" not in r.text and "Show password" not in r.text
    assert ed.post(f"/intake/{f['id']}/reveal", data={"field": "social.facebook.login_pass"},
                   follow_redirects=False).status_code in (303, 403)
    assert not intake.reveals(f["id"])
    r = admin.get(f"/intake/{f['id']}")
    assert "Show password" in r.text
    r = admin.post(f"/intake/{f['id']}/reveal", data={"field": "social.facebook.login_pass"}, follow_redirects=True)
    assert "s3cret!" in r.text and len(intake.reveals(f["id"])) == 1
    assert "s3cret!" not in admin.get(f"/intake/{f['id']}").text   # shown once

    admin.post(f"/intake/{f['id']}/revoke")
    assert anon.get(f"/i/{token}").status_code == 410
    ed.post(f"/intake/{f['id']}/extend")
    assert anon.get(f"/i/{token}").status_code == 200


def test_viewers_and_strangers_kept_out(env):
    assert "/intake/new" not in env["vi"].get("/intake").text
    assert env["anon"].get("/intake", follow_redirects=False).status_code in (302, 303, 401)
    assert env["anon"].get("/i/not-a-real-token").status_code == 410


def test_access_email_setting(env):
    env["admin"].post("/intake/email", data={"email": "team@example.com"})
    r = env["ed"].post("/intake/new", data={"practice_id": env["pid"]}, follow_redirects=True)
    token = re.search(r'/i/([\w-]+)"', r.text).group(1)
    assert "team@example.com" in env["anon"].get(f"/i/{token}").text
