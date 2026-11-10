"""Studio: library by brand and folder, chunked uploads, statuses, trash, share links, SharePoint storage, Canva import."""
import importlib
import io
import json
import os
import re

import pytest


def png_bytes(w=800, h=600, color=(200, 60, 60)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
    from app import db, users, images, studio, canva, microsoft
    for m in (db, users, images, studio, canva, microsoft):
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
        schulz = db.add_practice("Schulz Orthodontics", 0, 0)
        havens = db.add_practice("Havens Orthodontics", 0, 0)
        yield {"main": main, "db": db, "studio": studio, "canva": canva, "microsoft": microsoft, "admin": admin,
               "ed": ed, "vi": vi, "schulz": schulz, "havens": havens, "TestClient": TestClient}


def upload(c, pid, name, data, mime, folder=None, chunk=5000, extra=None):
    r = c.post("/studio/upload/start", json={"name": name, "size": len(data), "mime": mime, "pid": pid, "folder": folder})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    for off in range(0, len(data), chunk):
        r = c.post(f"/studio/upload/{uid}/chunk?offset={off}", content=data[off:off + chunk],
                   headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
    files = {"thumb": ("t.jpg", extra.pop("thumb"), "image/jpeg")} if extra and "thumb" in extra else None
    r = c.post(f"/studio/upload/{uid}/done", data=extra or {}, files=files)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_roles(env):
    m = env["main"]
    assert m.needed_role("GET", "/s/abcdefghijklmnopqrstuv") is None
    assert m.needed_role("POST", "/s/abcdefghijklmnopqrstuv/review") is None
    assert m.needed_role("GET", "/canva/connect") == "admin"
    assert m.needed_role("POST", "/connections/studio-sharepoint") == "admin"
    assert m.needed_role("GET", "/studio") == "viewer"
    assert m.needed_role("POST", "/studio/upload/start") == "editor"
    assert m.needed_role("GET", "/search") == "viewer"           # /s/ doesn't swallow /search


def test_upload_in_pieces_and_library(env):
    ed, studio, pid = env["ed"], env["studio"], env["schulz"]
    fid = studio.add_folder(pid, "November social", "Ed")
    data = png_bytes()
    iid = upload(ed, pid, "Thanksgiving post.png", data, "image/png", folder=fid, chunk=1000)
    it = studio.item(iid)
    assert it["title"] == "Thanksgiving post" and it["kind"] == "image" and it["size"] == len(data)
    assert (it["width"], it["height"]) == (800, 600) and it["has_thumb"] and it["folder_id"] == fid
    assert it["storage"] == "local" and studio.local_path(it)
    # the file comes back byte for byte, and supports ranges (video seeking)
    r = ed.get(f"/studio/f/{iid}")
    assert r.status_code == 200 and r.content == data
    r = ed.get(f"/studio/f/{iid}", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == data[:10]
    assert "attachment" in ed.get(f"/studio/f/{iid}?dl=1").headers["content-disposition"]
    assert ed.get(f"/studio/t/{iid}").headers["content-type"] == "image/jpeg"
    page = ed.get(f"/studio?b={pid}&f={fid}").text
    assert "Thanksgiving post" in page and "November social" in page and "Drafts" not in page
    assert f"/studio/i/{iid}" in page
    # search finds it by folder name too
    assert "Thanksgiving post" in ed.get(f"/studio?b={pid}&q=november").text
    assert "Thanksgiving post" not in ed.get(f"/studio?b={pid}&q=halloween").text


def test_upload_needs_a_brand_and_editor(env):
    r = env["ed"].post("/studio/upload/start", json={"name": "x.png", "size": 10, "mime": "image/png", "pid": 99999})
    assert r.status_code == 422 and "brand" in r.json()["error"]
    r = env["vi"].post("/studio/upload/start", json={"name": "x.png", "size": 10, "mime": "image/png", "pid": 0})
    assert r.status_code == 403
    r = env["ed"].post("/studio/upload/start", json={"name": "big.mp4", "size": 5 * 1024 ** 3, "mime": "video/mp4", "pid": 0})
    assert r.status_code == 422 and "4 GB" in r.json()["error"]


def test_chunk_resend_and_wrong_order(env):
    ed = env["ed"]
    r = ed.post("/studio/upload/start", json={"name": "a.txt", "size": 10, "mime": "text/plain", "pid": 0})
    uid = r.json()["id"]
    assert ed.post(f"/studio/upload/{uid}/chunk?offset=5", content=b"zzzzz").status_code == 422     # gap
    ed.post(f"/studio/upload/{uid}/chunk?offset=0", content=b"01234")
    ed.post(f"/studio/upload/{uid}/chunk?offset=0", content=b"01234")                                # sent twice
    r = ed.post(f"/studio/upload/{uid}/done")
    assert r.status_code == 422                                                                       # not complete
    ed.post(f"/studio/upload/{uid}/chunk?offset=5", content=b"56789")
    iid = ed.post(f"/studio/upload/{uid}/done").json()["id"]
    assert ed.get(f"/studio/f/{iid}").content == b"0123456789"
    # someone else can't add to your upload
    r = ed.post("/studio/upload/start", json={"name": "b.txt", "size": 3, "mime": "text/plain", "pid": 0})
    assert env["admin"].post(f"/studio/upload/{r.json()['id']}/chunk?offset=0", content=b"abc").status_code == 422


def test_video_keeps_browser_thumbnail_and_length(env):
    ed, studio = env["ed"], env["studio"]
    thumb = png_bytes(320, 568, (20, 40, 80))
    iid = upload(ed, env["schulz"], "Debond day reel.mp4", b"\x00" * 3000, "video/mp4",
                 extra={"width": "1080", "height": "1920", "duration": "24.2", "thumb": thumb})
    it = studio.item(iid)
    assert it["kind"] == "video" and it["has_thumb"] and it["duration"] == pytest.approx(24.2)
    assert (it["width"], it["height"]) == (1080, 1920)
    assert "0:24" in ed.get(f"/studio?b={env['schulz']}").text
    assert "<video" in ed.get(f"/studio/i/{iid}").text


def test_status_folder_move_bulk_and_trash(env):
    ed, studio, pid = env["ed"], env["studio"], env["schulz"]
    a = upload(ed, pid, "Promo A.png", png_bytes(), "image/png")
    b = upload(ed, pid, "Promo B.png", png_bytes(), "image/png")
    r = ed.post(f"/studio/i/{a}", data={"title": "Free consult promo", "status": "review", "notes": "FB + Google",
                                         "brand": str(pid), "folder": ""})
    assert r.status_code == 200
    it = studio.item(a)
    assert it["title"] == "Free consult promo" and it["status"] == "review" and it["status_by"] == "Ed Editor"
    assert any(e["kind"] == "status" and e["comment"] == "In review" for e in studio.events(a))
    # a folder from another brand is refused
    other = studio.add_folder(env["havens"], "Ads", "Ed")
    ed.post(f"/studio/i/{a}", data={"brand": str(pid), "folder": str(other)})
    assert studio.item(a)["folder_id"] != other
    # moving brands drops the folder
    ed.post(f"/studio/i/{a}", data={"brand": str(env["havens"]), "folder": str(other)})
    assert studio.item(a)["practice_id"] == env["havens"] and studio.item(a)["folder_id"] == other
    ed.post(f"/studio/i/{a}", data={"brand": str(pid)})
    assert studio.item(a)["practice_id"] == pid and studio.item(a)["folder_id"] is None
    # bulk status and trash
    ed.post("/studio/bulk", data={"ids": [str(a), str(b)], "action": "approved"})
    assert studio.item(a)["status"] == studio.item(b)["status"] == "approved"
    counts = studio.status_counts(pid)
    assert counts["approved"] >= 2 and counts["all"] >= 2
    ed.post("/studio/bulk", data={"ids": [str(b)], "action": "trash"})
    assert studio.item(b)["trashed_at"]
    assert "Promo B" not in ed.get(f"/studio?b={pid}").text and "Promo B" in ed.get("/studio?b=trash").text
    # can't delete for good before it's in the trash
    ed.post(f"/studio/i/{a}/delete")
    assert studio.item(a)
    ed.post(f"/studio/i/{b}/restore")
    assert not studio.item(b)["trashed_at"]
    ed.post(f"/studio/i/{b}/trash")
    path = studio.local_path(studio.item(b))
    ed.post(f"/studio/i/{b}/delete")
    assert studio.item(b) is None and not os.path.exists(path)
    # viewers look but don't change
    env["vi"].post(f"/studio/i/{a}", data={"status": "posted"})
    assert studio.item(a)["status"] == "approved"
    assert env["vi"].get(f"/studio/f/{a}").status_code == 200


def test_folder_rename_and_delete_keep_files(env):
    ed, studio, pid = env["ed"], env["studio"], env["schulz"]
    r = ed.post("/studio/folders", data={"pid": str(pid), "name": "Lobby / print"}, follow_redirects=False)
    fid = int(re.search(r"f=(\d+)", r.headers["location"]).group(1))
    assert studio.folder(fid)["name"] == "Lobby - print"          # SharePoint-safe
    iid = upload(ed, pid, "Poster.png", png_bytes(), "image/png", folder=fid)
    ed.post(f"/studio/folders/{fid}/rename", data={"name": "Lobby and print"})
    assert studio.folder(fid)["name"] == "Lobby and print"
    ed.post(f"/studio/folders/{fid}/delete")
    assert studio.folder(fid) is None and studio.item(iid)["folder_id"] is None and not studio.item(iid)["trashed_at"]


def test_brand_assets_show_in_studio(env):
    from app import images
    pid = env["havens"]
    images.add_upload(pid, png_bytes(900, 900), "office.png", "photo", "Ann")
    page = env["ed"].get(f"/studio?b={pid}").text
    assert "Brand assets" in page and "/brand-images/" in page


# ------------------------------------------------------------------ share links
def make_share(c, iid, **kw):
    data = {"title": "", "message": "Please approve", "days": "30", "allow_download": "1", "allow_review": "1", **kw}
    r = c.post(f"/studio/i/{iid}/share", data=data)
    assert r.status_code == 200
    return r


def test_share_link_public_view_download_approve(env):
    ed, studio, TC, main = env["ed"], env["studio"], env["TestClient"], env["main"]
    iid = upload(ed, env["schulz"], "Thanksgiving final.png", png_bytes(), "image/png")
    studio.update(iid, "Ed", status="review")
    make_share(ed, iid)
    s = studio.shares_for(item_id=iid)[0]
    assert s["live"] and s["created_by"] == "Ed Editor" and s["created_by_email"] == "ed@example.com"
    page = ed.get(f"/studio/i/{iid}").text
    assert f"/s/{s['token']}" in page and "Copy link" in page
    with TC(main.app) as guest:                                   # no sign-in
        r = guest.get(f"/s/{s['token']}")
        assert r.status_code == 200 and "Thanksgiving final" in r.text and "Please approve" in r.text
        assert "Schulz Orthodontics" in r.text and "noindex" in r.text
        guest.get(f"/s/{s['token']}")                             # same visitor within the hour: one view
        assert guest.get(f"/s/{s['token']}/f/{iid}").status_code == 200
        assert guest.get(f"/s/{s['token']}/f/{iid}?dl=1").status_code == 200
        assert guest.get(f"/s/{s['token']}/logo").status_code in (200, 302)
        # a file that isn't on this link
        other = upload(ed, env["schulz"], "Secret.png", png_bytes(), "image/png")
        assert guest.get(f"/s/{s['token']}/f/{other}").status_code == 404
        assert guest.get(f"/studio/f/{iid}").status_code == 401
        # asking for changes needs a comment and a name
        r = guest.post(f"/s/{s['token']}/review", data={"item_id": iid, "decision": "changes", "name": "Jordyn"}, follow_redirects=False)
        assert "Say what should change" in urlunq(r.headers["location"])
        guest.post(f"/s/{s['token']}/review", data={"item_id": iid, "decision": "changes", "name": "Jordyn",
                                                  "comment": "Use the hallway photo"})
        assert studio.item(iid)["status"] == "draft"
        guest.post(f"/s/{s['token']}/review", data={"item_id": iid, "decision": "approved", "name": "Jordyn"})
        it = studio.item(iid)
        assert it["status"] == "approved" and "Jordyn" in it["status_by"]
        r = guest.get(f"/s/{s['token']}")
        assert "Jordyn approved this" in r.text
    s = studio.share(s["id"])
    assert s["views"] == 1 and s["downloads"] == 1 and s["approveds"] == 1 and s["changess"] == 1
    fb = ed.get("/studio?b=feedback").text
    assert "Use the hallway photo" in fb and "Jordyn" in fb


def urlunq(s):
    import urllib.parse
    return urllib.parse.unquote(s)


def test_share_password_expiry_revoke_no_download(env):
    ed, studio, TC, main = env["ed"], env["studio"], env["TestClient"], env["main"]
    iid = upload(ed, env["havens"], "Ad.png", png_bytes(), "image/png")
    make_share(ed, iid, password="tooth-fairy", allow_download="")
    s = studio.shares_for(item_id=iid)[0]
    assert s["pw_hash"] and not s["allow_download"]
    with TC(main.app) as guest:
        r = guest.get(f"/s/{s['token']}")
        assert "Enter the password" in r.text and "Ad.png" not in r.text
        assert guest.get(f"/s/{s['token']}/f/{iid}").status_code == 404
        r = guest.post(f"/s/{s['token']}/unlock", data={"password": "wrong"}, follow_redirects=False)
        assert "error=password" in r.headers["location"]
        guest.post(f"/s/{s['token']}/unlock", data={"password": "tooth-fairy"})
        r = guest.get(f"/s/{s['token']}")
        assert "Ad" in r.text and "?dl=1" not in r.text
        assert guest.get(f"/s/{s['token']}/f/{iid}").status_code == 200
        assert guest.get(f"/s/{s['token']}/f/{iid}?dl=1").status_code == 403
        ed.post(f"/studio/shares/{s['id']}/revoke")
        assert guest.get(f"/s/{s['token']}").status_code == 410
    ed.post(f"/studio/i/{iid}/share", data={"password": "abc"})
    assert len(studio.shares_for(item_id=iid)) == 1                # too short: refused
    # expired
    s2 = studio.create_share("Ed", item_id=iid, days=1)
    with env["db"].conn() as c:
        c.execute("UPDATE studio_shares SET expires_at='2020-01-01T00:00:00' WHERE id=?", (s2["id"],))
    with TC(main.app) as guest:
        r = guest.get(f"/s/{s2['token']}")
        assert r.status_code == 410 and "expired" in r.text
        assert guest.get("/s/not-a-real-token-at-all-xx").status_code == 404


def test_folder_share_shows_its_files(env):
    ed, studio, TC, main = env["ed"], env["studio"], env["TestClient"], env["main"]
    pid = env["havens"]
    fid = studio.add_folder(pid, "Referral office kit", "Ed")
    a = upload(ed, pid, "Kit cover.png", png_bytes(), "image/png", folder=fid)
    b = upload(ed, pid, "Kit back.png", png_bytes(), "image/png", folder=fid)
    r = ed.post(f"/studio/folders/{fid}/share", data={"days": "7", "allow_download": "1"})
    assert r.status_code == 200
    s = studio.shares_for(folder_id=fid)[0]
    assert not s["allow_review"]
    assert f"/s/{s['token']}" in ed.get(f"/studio?b={pid}&f={fid}").text
    with TC(main.app) as guest:
        page = guest.get(f"/s/{s['token']}").text
        assert "Kit cover" in page and "Kit back" in page and "Approve" not in page
        r = guest.post(f"/s/{s['token']}/review", data={"item_id": a, "decision": "approved", "name": "X"}, follow_redirects=False)
        assert "approvals" in urlunq(r.headers["location"])
        ed.post(f"/studio/i/{b}/trash")
        assert "Kit back" not in guest.get(f"/s/{s['token']}").text


def test_email_share_needs_mail_then_sends(env, monkeypatch):
    ed, studio, ms = env["ed"], env["studio"], env["microsoft"]
    iid = upload(ed, env["schulz"], "Email me.png", png_bytes(), "image/png")
    ed.post(f"/studio/i/{iid}/share", data={"to": "jordyn@schulzortho.com", "days": "30", "allow_review": "1"})
    s = studio.shares_for(item_id=iid)[0]
    assert s["live"] and s["sents"] == 0                          # link made, email not sent
    sent = []
    monkeypatch.setattr(ms, "studio_on", lambda: True)
    monkeypatch.setattr(ms, "send_mail", lambda to, subject, body, reply_to=None: sent.append((to, subject, body, reply_to)))
    r = ed.post(f"/studio/shares/{s['id']}/send", data={"to": "jordyn@schulzortho.com, dr@schulzortho.com", "note": "Hi!"})
    assert r.status_code == 200
    to, subject, body, reply_to = sent[0]
    assert to == ["jordyn@schulzortho.com", "dr@schulzortho.com"] and "Email me" in subject
    assert f"/s/{s['token']}" in body and "Hi!" in body and reply_to == "ed@example.com"
    assert studio.share(s["id"])["sents"] == 1
    ed.post(f"/studio/shares/{s['id']}/send", data={"to": "not-an-email"})
    assert len(sent) == 1
    # someone outside approves: the person who made the link hears about it
    import time
    from fastapi.testclient import TestClient
    with TestClient(env["main"].app) as guest:
        guest.post(f"/s/{s['token']}/review", data={"item_id": iid, "decision": "approved", "name": "Dr. Schulz"})
    for _ in range(20):
        if len(sent) > 1:
            break
        time.sleep(0.05)
    assert sent[1][0] == ["ed@example.com"] and "Dr. Schulz approved" in sent[1][1]


# ------------------------------------------------------------------ SharePoint
class FakeDrive:
    def __init__(self):
        self.files, self.n, self.calls = {}, 0, []

    def graph(self, method, path, body=None, params=None, headers=None):
        self.calls.append((method, path))
        if path.startswith("/sites/") and path.endswith("/drive"):
            return {"id": "drive1"}
        if path.startswith("/sites/"):
            return {"id": "site1", "displayName": "Marketing", "webUrl": "https://mcdp.sharepoint.com/sites/Marketing"}
        if path.endswith(":/createUploadSession"):
            return {"uploadUrl": "https://upload.example/" + path.split(":/")[1]}
        if method == "GET" and "/items/" in path:
            iid = path.rsplit("/", 1)[1]
            return {"id": iid, "@microsoft.graph.downloadUrl": f"https://dl.example/{iid}", "webUrl": f"https://sp/{iid}"}
        if method == "GET" and ":/" in path:
            from app.microsoft import MSError
            raise MSError("not found (HTTP 404)")
        if method == "POST" and path.endswith("/children"):
            self.n += 1
            return {"id": f"folder{self.n}"}
        if method in ("PATCH", "DELETE"):
            return {}
        raise AssertionError(path)

    def graph_bytes(self, method, path, data, content_type="", headers=None):
        self.n += 1
        self.files[path] = data
        return {"id": f"item{self.n}", "webUrl": f"https://sp/item{self.n}"}

    def put_chunk(self, url, data, start, total):
        self.files.setdefault(url, b"")
        self.files[url] += data
        if start + len(data) == total:
            self.n += 1
            return {"id": f"item{self.n}", "webUrl": f"https://sp/item{self.n}"}
        return {"nextExpectedRanges": [f"{start + len(data)}-"]}


def test_sharepoint_link_push_serve_and_move(env, monkeypatch):
    ed, admin, studio, ms = env["ed"], env["admin"], env["studio"], env["microsoft"]
    fake = FakeDrive()
    monkeypatch.setattr(ms, "graph", fake.graph)
    monkeypatch.setattr(ms, "graph_bytes", fake.graph_bytes)
    monkeypatch.setattr(ms, "put_chunk", fake.put_chunk)
    monkeypatch.setattr(studio, "kick_push", lambda: None)           # run it by hand below
    # not yet allowed: needs the Studio permissions
    monkeypatch.setattr(ms, "studio_on", lambda: False)
    admin.post("/connections/studio-sharepoint", data={"site": "https://mcdp.sharepoint.com/sites/Marketing"})
    assert not studio.sp_drive()
    monkeypatch.setattr(ms, "studio_on", lambda: True)
    with pytest.raises(studio.StudioError):
        studio.parse_site("https://example.com/sites/x")
    assert studio.parse_site("mcdp.sharepoint.com/sites/Marketing/Shared%20Documents/Forms") == ("mcdp.sharepoint.com", "sites/Marketing")
    r = admin.post("/connections/studio-sharepoint", data={"site": "https://mcdp.sharepoint.com/sites/Marketing/SitePages/Home.aspx"})
    assert r.status_code == 200 and studio.sp_drive() == "drive1" and studio.sp_ready()
    assert "Marketing" in admin.get("/connections").text
    pid = env["schulz"]
    fid = studio.add_folder(pid, "November social", "Ed")
    small = upload(ed, pid, "Small post.png", png_bytes(), "image/png", folder=fid)
    monkeypatch.setattr(studio, "SP_SIMPLE", 1000)
    monkeypatch.setattr(studio, "SP_CHUNK", 1500)
    big_data = bytes(range(256)) * 20
    big = upload(ed, pid, "Reel.mp4", big_data, "video/mp4", folder=fid)
    local_big = studio.local_path(studio.item(big))
    assert studio.push_pending() >= 2
    it = studio.item(big)
    assert it["storage"] == "sp" and it["local_name"] is None and not os.path.exists(local_big)
    assert it["sp_path"] == f"MCDP Studio/Schulz Orthodontics/November social/Reel ({big}).mp4"
    up_url = "https://upload.example/" + studio._q(it["sp_path"])
    assert fake.files[up_url] == big_data                          # sent in pieces, in order
    assert studio.item(small)["storage"] == "sp"
    # the hub still serves it: a short-lived SharePoint link
    r = ed.get(f"/studio/f/{big}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://dl.example/")
    assert ed.get(f"/studio/t/{big}").status_code == 200           # thumbnails stay local
    assert "Open in SharePoint" in ed.get(f"/studio/i/{big}").text
    # renaming moves it on SharePoint too
    studio.update(big, "Ed", title="Debond day reel")
    studio.push_pending()
    assert ("PATCH", f"/drives/drive1/items/{it['sp_item']}") in fake.calls
    assert studio.item(big)["sp_path"].endswith(f"Debond day reel ({big}).mp4")
    # deleting for good removes it from SharePoint
    studio.trash(big)
    studio.delete_forever(big)
    assert ("DELETE", f"/drives/drive1/items/{it['sp_item']}") in fake.calls
    admin.post("/connections/studio-sharepoint/disconnect")
    assert not studio.sp_drive()


def test_microsoft_studio_scopes_only_when_asked(env, monkeypatch):
    ms, db = env["microsoft"], env["db"]
    assert "Files.ReadWrite.All" not in ms.scopes(False) and "Mail.Send" in ms.scopes(True)
    db.set_setting("ms_tenant_id", "t")
    db.set_setting("ms_client_id", "c")
    db.set_setting("ms_client_secret", "s")
    r = env["admin"].get("/microsoft/connect?studio=1", follow_redirects=False)
    assert "Files.ReadWrite.All" in urlunq(r.headers["location"])
    r = env["admin"].get("/microsoft/connect", follow_redirects=False)
    assert "Files.ReadWrite.All" not in urlunq(r.headers["location"])
    posted = {}

    def fake_post(fields, scope=None):
        posted.update(fields, scope=scope)
        return {"access_token": "a", "refresh_token": "r", "expires_in": 3600,
                "scope": "Calendars.ReadWrite Files.ReadWrite.All Sites.ReadWrite.All Mail.Send Mail.Send.Shared"}
    monkeypatch.setattr(ms, "_token_post", fake_post)
    monkeypatch.setattr(ms, "graph", lambda *a, **k: {"displayName": "Mark", "mail": "mark@example.com"})
    ms.exchange_code("code", "https://x/cb", "v", True)
    assert "Mail.Send" in posted["scope"] and ms.studio_on()
    ms.disconnect()
    assert not ms.studio_on()


# ------------------------------------------------------------------ Canva
def test_suggest_brand(env):
    canva, db = env["canva"], env["db"]
    plist = db.practices()
    assert canva.suggest_brand("Schulz - Thanksgiving IG post", plist) == env["schulz"]
    assert canva.suggest_brand("HAVENS ortho fall flyer", plist) == env["havens"]
    assert canva.suggest_brand("Untitled design", plist) is None
    assert canva.suggest_brand("Orthodontics promo", plist) is None          # only generic words
    assert canva.suggest_brand("MCDP all-hands deck", plist) == 0


def test_canva_import_files_by_brand_and_queue(env, monkeypatch):
    canva, studio, ed = env["canva"], env["studio"], env["ed"]
    monkeypatch.setattr(canva, "EXPORT_GAP", 0)
    designs = [
        {"id": "D1", "title": "Schulz Thanksgiving post", "page_count": 1, "updated_at": 100,
         "thumbnail": {"url": "https://thumb/D1", "width": 1080, "height": 1080}, "urls": {"edit_url": "https://canva/D1/edit"}},
        {"id": "D2", "title": "Havens referral brochure", "page_count": 4, "updated_at": 100, "thumbnail": {"url": "https://thumb/D2"}},
        {"id": "D3", "title": "Fall lobby poster", "page_count": 1, "updated_at": 100, "thumbnail": {}},
        {"id": "D4", "title": "Schulz debond reel", "page_count": 1, "updated_at": 100, "thumbnail": {"width": 1080, "height": 1920}},
    ]
    exported = []

    def fake_call(method, path, body=None, params=None):
        if path == "/designs":
            return {"items": designs}
        if path.endswith("/export-formats"):
            did = path.split("/")[2]
            return {"formats": {"mp4": {}} if did == "D4" else {"png": {}, "pdf": {}, "pptx": {}}}
        if path == "/exports":
            exported.append((body["design_id"], body["format"]))
            return {"job": {"id": "J" + body["design_id"], "status": "in_progress"}}
        if path.startswith("/exports/"):
            return {"job": {"id": path.split("/")[2], "status": "success", "urls": ["https://export/" + path.split("/")[2]]}}
        raise AssertionError(path)

    def fake_download(url, suffix):
        import tempfile
        fd, p = tempfile.mkstemp(suffix=suffix, dir=studio._dir("tmp"))
        os.write(fd, png_bytes(50, 50) if suffix == ".png" else b"%PDF-1.4 fake" if suffix == ".pdf" else b"\x00" * 100)
        os.close(fd)
        return p

    monkeypatch.setattr(canva, "call", fake_call)
    monkeypatch.setattr(canva, "_download", fake_download)
    monkeypatch.setattr(canva, "_fetch_bytes", lambda url, limit=0: png_bytes(64, 64, (10, 120, 110)))
    monkeypatch.setattr(canva, "time", type("T", (), {"sleep": staticmethod(lambda s: None), "time": staticmethod(__import__("time").time)}))
    canva.run_import("Ed Editor")
    st = canva.status()
    assert st["state"] == "done" and st["new"] == 4 and st["failed"] == 0, st
    by_id = {it["canva_id"]: it for it in studio.items(all_brands=True, limit=1000) if it["canva_id"]}
    assert by_id["D1"]["practice_id"] == env["schulz"] and by_id["D1"]["kind"] == "image"
    assert studio.folder(by_id["D1"]["folder_id"])["name"] == "Imported from Canva"
    assert by_id["D1"]["canva_url"] == "https://canva/D1/edit" and by_id["D1"]["has_thumb"]
    assert by_id["D2"]["kind"] == "pdf" and by_id["D2"]["pages"] == 4 and by_id["D2"]["practice_id"] == env["havens"]
    assert by_id["D3"]["practice_id"] is None                                   # waits for a brand
    assert by_id["D4"]["kind"] == "video"
    assert ("D2", {"type": "pdf"}) in exported and ("D4", {"type": "mp4", "quality": "vertical_1080p"}) in exported
    q = ed.get("/studio?b=none").text
    assert "Fall lobby poster" in q and "Schulz Thanksgiving" not in q
    ed.post("/studio/bulk", data={"ids": [str(by_id["D3"]["id"])], "action": "brand", "pid": str(env["havens"])})
    d3 = studio.item(by_id["D3"]["id"])
    assert d3["practice_id"] == env["havens"] and studio.folder(d3["folder_id"])["name"] == "Imported from Canva"
    # again: nothing new; a design changed in Canva comes in as a new version of the same file
    exported.clear()
    designs[0]["updated_at"] = 200
    canva.run_import("Ed Editor")
    st = canva.status()
    assert st["new"] == 0 and st["updated"] == 1 and [e[0] for e in exported] == ["D1"]
    assert any(e["kind"] == "new_version" for e in studio.events(by_id["D1"]["id"]))
    # another format on demand, next to the original
    nid = canva.export_more(by_id["D1"]["id"], "pptx", "Ed")
    extra = studio.item(nid)
    assert extra["title"] == "Schulz Thanksgiving post (PowerPoint)" and extra["folder_id"] == by_id["D1"]["folder_id"]
    with pytest.raises(canva.CanvaError):
        canva.export_more(by_id["D4"]["id"], "pptx", "Ed")


def test_canva_connect_flow(env, monkeypatch):
    admin, canva = env["admin"], env["canva"]
    admin.post("/connections/canva", data={"client_id": "OC-abc", "client_secret": "sec"})
    assert canva.configured()
    r = admin.get("/canva/connect", follow_redirects=False)
    loc = urlunq(r.headers["location"])
    assert loc.startswith("https://www.canva.com/api/oauth/authorize?") and "code_challenge_method=s256" in loc
    assert "design:content:read" in loc and "/canva/callback" in loc
    state = re.search(r"state=([0-9a-f]+)", loc).group(1)
    monkeypatch.setattr(canva, "_token_post", lambda f: {"access_token": "a", "refresh_token": "r2", "expires_in": 14400})
    monkeypatch.setattr(canva, "call", lambda *a, **k: {"profile": {"display_name": "MCDP Marketing"}})
    r = admin.get(f"/canva/callback?code=xyz&state={state}", follow_redirects=False)
    assert canva.connected() and canva.user()["name"] == "MCDP Marketing"
    assert env["db"].get_setting("canva_refresh_token") == "r2"
    page = admin.get("/connections").text
    assert "MCDP Marketing" in page and 'id="canva"' in page
    assert "Import from Canva" in env["ed"].get(f"/studio?b={env['schulz']}").text
    assert env["ed"].get("/canva/connect").status_code in (200, 303)        # editors can't reach the sign-in
    assert env["ed"].get("/canva/connect", follow_redirects=False).headers["location"] != loc
