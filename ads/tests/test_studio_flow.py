"""Studio phase 3: approval chains (teammates and outside links), comments with pins and @mentions,
and posting as the brand through Social (Postiz), with states read back."""
import importlib
import io
import os
import re
import time
from datetime import datetime, timedelta, timezone

import pytest


def png_bytes(w=1080, h=1080):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), (15, 116, 113)).save(b, "PNG")
    return b.getvalue()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
    os.environ.pop("POSTIZ_DATABASE_URL", None)
    from app import db, users, images, studio, studio_flow, social, microsoft
    for m in (db, users, images, studio, studio_flow, social, microsoft):
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
        admin = person(boss, "Mark Johnson", "mark@example.com", "admin")
        amber = person(admin, "Amber Fonnegra", "amber@example.com", "editor")
        vi = person(admin, "Vi Viewer", "vi@example.com", "viewer")
        schulz = db.add_practice("Schulz Orthodontics", 0, 0)
        ids = {u["email"]: u["id"] for u in users.all_users()}
        yield {"main": main, "db": db, "studio": studio, "flow": studio_flow, "social": social, "ms": microsoft,
               "admin": admin, "amber": amber, "vi": vi, "schulz": schulz, "ids": ids, "TC": TestClient}


def add_image(env, title="Thanksgiving post", w=1080, h=1080):
    import tempfile
    fd, p = tempfile.mkstemp(suffix=".png")
    os.write(fd, png_bytes(w, h))
    os.close(fd)
    return env["studio"].add_file(p, title + ".png", env["schulz"], None, by="Amber Fonnegra", mime="image/png")


@pytest.fixture
def mail(env, monkeypatch):
    sent = []
    monkeypatch.setattr(env["ms"], "studio_on", lambda: True)
    monkeypatch.setattr(env["ms"], "send_mail", lambda to, subject, body, reply_to=None: sent.append((to, subject, body)))
    return sent


def wait_for(cond, n=40):
    for _ in range(n):
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_approval_chain_teammate_then_outside(env, mail):
    amber, admin, studio, flow, ids = env["amber"], env["admin"], env["studio"], env["flow"], env["ids"]
    iid = add_image(env)
    page = amber.get(f"/studio/i/{iid}").text
    assert "Ask for approval" in page and "Someone outside" in page
    r = amber.post(f"/studio/i/{iid}/approval", data={
        "step_kind": ["person", "outside"], "step_user": [str(ids["mark@example.com"]), ""],
        "step_name": ["", "Jordyn (practice manager)"], "step_email": ["", "jordyn@schulzortho.com"],
        "note": "Thanksgiving week"})
    assert r.status_code == 200
    ch = flow.chain(iid)
    assert [s["status"] for s in ch] == ["pending", "waiting"] and studio.item(iid)["status"] == "review"
    assert wait_for(lambda: any(m[0] == ["mark@example.com"] for m in mail))
    assert "Your approval" in [m for m in mail if m[0] == ["mark@example.com"]][0][1]
    # it's waiting for Mark: his badge, his list, his buttons; not Amber's
    assert flow.waiting_count(ids["mark@example.com"]) == 1
    assert "Thanksgiving post" in admin.get("/studio?b=mine").text
    assert "It's your turn" in admin.get(f"/studio/i/{iid}").text
    assert "It's your turn" not in amber.get(f"/studio/i/{iid}").text
    amber.post(f"/studio/steps/{ch[0]['id']}", data={"decision": "approved"})
    assert flow.step(ch[0]["id"])["status"] == "pending"                       # not hers to answer
    # changes need a reason
    admin.post(f"/studio/steps/{ch[0]['id']}", data={"decision": "changes", "comment": ""})
    assert flow.step(ch[0]["id"])["status"] == "pending"
    admin.post(f"/studio/steps/{ch[0]['id']}", data={"decision": "approved", "comment": "Looks great"})
    ch = flow.chain(iid)
    assert [s["status"] for s in ch] == ["approved", "pending"] and ch[1]["share_id"]
    assert studio.item(iid)["status"] == "review"
    sh = studio.share(ch[1]["share_id"])
    assert sh["allow_review"] and sh["message"] == "Thanksgiving week"
    assert wait_for(lambda: any(m[0] == ["jordyn@schulzortho.com"] for m in mail))
    jm = [m for m in mail if m[0] == ["jordyn@schulzortho.com"]][0]
    assert f"/s/{sh['token']}" in jm[2] and "Please approve" in jm[1]
    assert f"/s/{sh['token']}" in amber.get(f"/studio/i/{iid}").text          # link to copy, too
    # Jordyn approves on the share page: that finishes the chain
    with env["TC"](env["main"].app) as guest:
        guest.post(f"/s/{sh['token']}/review", data={"item_id": iid, "decision": "approved", "name": "Jordyn"})
    ch = flow.chain(iid)
    assert [s["status"] for s in ch] == ["approved", "approved"] and "Jordyn" in ch[1]["decided_by"]
    assert studio.item(iid)["status"] == "approved"
    assert wait_for(lambda: any(m[0] == ["amber@example.com"] and "Approved" in m[1] for m in mail))
    assert any(c["body"] == "Looks great" and c["source"] == "approval" for c in flow.comments(iid))


def test_changes_send_it_back(env, mail):
    amber, admin, studio, flow, ids = env["amber"], env["admin"], env["studio"], env["flow"], env["ids"]
    iid = add_image(env, "Holiday hours")
    amber.post(f"/studio/i/{iid}/approval", data={"step_kind": ["outside", "person"], "step_user": ["", str(ids["mark@example.com"])],
                                                  "step_name": ["Dr. Schulz", ""], "step_email": ["", ""]})
    ch = flow.chain(iid)
    sh = studio.share(ch[0]["share_id"])
    with env["TC"](env["main"].app) as guest:
        guest.post(f"/s/{sh['token']}/review", data={"item_id": iid, "decision": "changes", "name": "Dr. Schulz",
                                                     "comment": "Use the hallway photo"})
    ch = flow.chain(iid)
    assert [s["status"] for s in ch] == ["changes", "cancelled"] and studio.item(iid)["status"] == "draft"
    assert wait_for(lambda: any(m[0] == ["amber@example.com"] and "Changes" in m[1] for m in mail))
    assert flow.waiting_count(ids["mark@example.com"]) == 0
    # ask again: a new round
    amber.post(f"/studio/i/{iid}/approval", data={"step_kind": ["person"], "step_user": [str(ids["mark@example.com"])],
                                                  "step_name": [""], "step_email": [""]})
    assert len(flow.chain(iid)) == 1 and flow.chain(iid)[0]["round"] == 2
    amber.post(f"/studio/i/{iid}/approval/stop")
    assert flow.chain(iid)[0]["status"] == "cancelled" and studio.item(iid)["status"] == "draft"
    # nobody picked
    r = amber.post(f"/studio/i/{iid}/approval", data={"step_kind": ["person"], "step_user": [""], "step_name": [""], "step_email": [""]})
    assert "Add at least one person" in r.text


def test_comments_pins_mentions(env, mail):
    amber, admin, vi, flow = env["amber"], env["admin"], env["vi"], env["flow"]
    iid = add_image(env, "Meet the team")
    amber.post(f"/studio/i/{iid}/comments", data={"body": "Can we brighten this, @Mark?", "x": "42.5", "y": "130"})
    c = flow.comments(iid)[0]
    assert c["who"] == "Amber Fonnegra" and c["x"] == 42.5 and c["y"] == 100.0
    assert wait_for(lambda: any(m[0] == ["mark@example.com"] and "mentioned you" in m[1] for m in mail))
    page = admin.get(f"/studio/i/{iid}").text
    assert "Can we brighten this" in page and 'class="st-pin"' in page and "left: 42.5%" in page
    vi.post(f"/studio/i/{iid}/comments", data={"body": "viewer note"})
    assert len(flow.comments(iid)) == 1                                          # viewers can't comment (editor action)
    admin.post(f"/studio/comments/{c['id']}/resolve", data={"item": iid})
    assert flow.comments(iid)[0]["resolved_by"] == "Mark Johnson"
    assert "left: 42.5%" not in admin.get(f"/studio/i/{iid}").text               # resolved pins leave the picture
    amber.post(f"/studio/comments/{c['id']}/delete", data={"item": iid})
    assert not flow.comments(iid)


# ------------------------------------------------------------------ posting through Social
class FakeSocial:
    def __init__(self):
        self.uploads, self.creates, self.deleted, self.state = [], [], [], {}

    def install(self, mp, social):
        chans = [{"id": "fb1", "name": "Schulz Orthodontics", "provider": "facebook", "label": "Facebook", "picture": "",
                  "internal_id": "PAGE1", "customer": ""},
                 {"id": "ig1", "name": "schulzortho", "provider": "instagram", "label": "Instagram", "picture": "",
                  "internal_id": "IG1", "customer": ""},
                 {"id": "gmb1", "name": "Schulz Orthodontics - Traverse City", "provider": "gmb", "label": "Google profile",
                  "picture": "", "internal_id": "loc1", "customer": ""},
                 {"id": "fb9", "name": "Other Practice", "provider": "facebook", "label": "Facebook", "picture": "",
                  "internal_id": "PAGE9", "customer": ""}]
        mp.setattr(social, "integrations", lambda fresh=False: chans)
        mp.setattr(social, "configured", lambda: True)
        mp.setattr(social, "upload", lambda path, name, mime="": (self.uploads.append((os.path.getsize(path), name, mime)),
                                                                   {"id": "m1", "path": "https://social.mcdp.com/uploads/x.png"})[1])

        def create(kind, when, items):
            self.creates.append((kind, when, items))
            out = []
            for it in items:
                pid = f"P{len(self.state) + 1}"
                self.state[pid] = {"id": pid, "state": "DRAFT" if kind == "draft" else "QUEUE", "publishDate": when}
                out.append({"postId": pid, "integration": it["integration"]})
            return out
        mp.setattr(social, "create", create)
        mp.setattr(social, "posts_between", lambda a, b: list(self.state.values()))
        mp.setattr(social, "delete", lambda pid: (self.deleted.append(pid), self.state.pop(pid, None)))


def test_settings_and_body(env, monkeypatch):
    social = env["social"]
    assert social.settings_for("instagram", {}) == {"__type": "instagram", "post_type": "post", "collaborators": []}
    assert social.settings_for("instagram-standalone", {"ig_story": True})["post_type"] == "story"
    assert social.settings_for("gmb", {"gmb_cta": "BOOK", "gmb_url": "https://x.com/book"}) == {
        "__type": "gmb", "topicType": "STANDARD", "callToActionType": "BOOK", "callToActionUrl": "https://x.com/book"}
    assert "callToActionUrl" not in social.settings_for("gmb", {"gmb_cta": "CALL"})
    seen = {}
    monkeypatch.setattr(social, "_req", lambda m, path, body=None, **k: seen.update(m=m, path=path, body=body) or [{"postId": "p", "integration": "fb1"}])
    got = social.create("schedule", "2026-11-25T14:00:00.000Z", [{"integration": "fb1", "provider": "facebook", "content": "Hi",
                                                                  "media": [{"id": "m", "path": "u"}]}])
    assert got == [{"postId": "p", "integration": "fb1"}] and seen["path"] == "/posts"
    assert seen["body"] == {"type": "schedule", "date": "2026-11-25T14:00:00.000Z", "shortLink": False, "tags": [],
                            "posts": [{"integration": {"id": "fb1"}, "value": [{"content": "Hi", "image": [{"id": "m", "path": "u"}]}],
                                       "settings": {"__type": "facebook"}}]}
    # no key anywhere: a plain message
    env["db"].set_setting("postiz_api_key", None)
    monkeypatch.undo()
    with pytest.raises(social.SocialError, match="API key"):
        social.integrations(fresh=True)


def test_post_schedule_sync_cancel(env, monkeypatch, mail):
    amber, admin, studio, flow, social, db = env["amber"], env["admin"], env["studio"], env["flow"], env["social"], env["db"]
    fake = FakeSocial()
    fake.install(monkeypatch, social)
    db.set_practice_social(env["schulz"], "PAGE1", "IG1")
    iid = add_image(env, "Free consult promo")
    page = amber.get(f"/studio/i/{iid}").text
    assert 'value="fb1" data-provider="facebook" checked' in page and 'value="ig1" data-provider="instagram" checked' in page
    assert 'value="fb9" data-provider="facebook">' in page                   # another practice's Page: offered, not ticked
    assert "once it’s approved" in page
    day = (datetime.now(flow.local_tz()) + timedelta(days=2)).strftime("%Y-%m-%d")
    form = {"channels": ["fb1", "ig1"], "caption": "Free consults all November.", "when": "schedule", "day": day, "time": "09:00"}
    amber.post(f"/studio/i/{iid}/post", data=form)
    assert not fake.creates                                                  # not approved yet
    studio.update(iid, "Mark", status="approved")
    r = amber.post(f"/studio/i/{iid}/post", data=form)
    assert "Scheduled on 2 channels" in r.text
    kind, when, items = fake.creates[-1]
    assert kind == "schedule" and when.endswith(":00:00.000Z") and [i["integration"] for i in items] == ["fb1", "ig1"]
    assert items[0]["media"] == [{"id": "m1", "path": "https://social.mcdp.com/uploads/x.png"}]
    assert fake.uploads[0][1] == "Free consult promo.png"
    local = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(flow.local_tz())
    assert (local.hour, local.minute) == (9, 0)
    assert studio.item(iid)["status"] == "scheduled"
    rows = flow.posts(iid)
    assert {r["state"] for r in rows} == {"queued"} and {r["channel"] for r in rows} == {"Schulz Orthodontics", "schulzortho"}
    # Social publishes one, the other fails: the file stays Scheduled, the failure shows
    p1, p2 = [r["postiz_id"] for r in sorted(rows, key=lambda r: r["id"])]
    fake.state[p1].update(state="PUBLISHED", releaseURL="https://facebook.com/123")
    fake.state[p2].update(state="ERROR", error="Instagram said no")
    assert flow.sync_posts() == 2
    page = amber.get(f"/studio/i/{iid}").text
    assert "https://facebook.com/123" in page and "Instagram said no" in page
    assert studio.item(iid)["status"] == "scheduled"
    # cancelling the failed one leaves only published posts: Posted
    bad = [r for r in flow.posts(iid) if r["state"] == "error"][0]
    amber.post(f"/studio/posts/{bad['id']}/cancel")
    assert p2 in fake.deleted and studio.item(iid)["status"] == "posted"
    # in the past: refused; now: allowed without a date
    past = (datetime.now(flow.local_tz()) - timedelta(days=1)).strftime("%Y-%m-%d")
    r = amber.post(f"/studio/i/{iid}/post", data={**form, "day": past})
    assert "time in the future" in r.text
    n = len(fake.creates)
    amber.post(f"/studio/i/{iid}/post", data={"channels": ["gmb1"], "caption": "Hi", "when": "now", "gmb_cta": "BOOK", "gmb_url": ""})
    assert len(fake.creates) == n                                            # Google button needs its address
    amber.post(f"/studio/i/{iid}/post", data={"channels": ["gmb1"], "caption": "Hi", "when": "now", "gmb_cta": "BOOK",
                                              "gmb_url": "https://schulzortho.com/book"})
    assert fake.creates[-1][0] == "now" and fake.creates[-1][2][0]["opts"]["gmb_cta"] == "BOOK"
    # Social remembers the Google profile for this practice next time
    assert "gmb1" in social._picked(env["schulz"])


def test_instagram_shape_and_admin_override(env, monkeypatch):
    amber, admin, studio, flow, social = env["amber"], env["admin"], env["studio"], env["flow"], env["social"]
    fake = FakeSocial()
    fake.install(monkeypatch, social)
    iid = add_image(env, "Holiday story", 1080, 1920)
    studio.update(iid, "Mark", status="approved")
    r = amber.post(f"/studio/i/{iid}/post", data={"channels": ["ig1"], "caption": "x", "when": "now"})
    assert "4:5" in r.text and not fake.creates
    amber.post(f"/studio/i/{iid}/post", data={"channels": ["ig1"], "caption": "x", "when": "now", "ig_story": "1"})
    assert fake.creates[-1][2][0]["opts"]["ig_story"] is True
    # drafts in Social don't change the file's status
    did = add_image(env, "Draft only")
    admin.post(f"/studio/i/{did}/post", data={"channels": ["fb1"], "caption": "x", "when": "draft", "override": "1"})
    assert fake.creates[-1][0] == "draft" and studio.item(did)["status"] == "draft"
    # only admins can skip approval
    n = len(fake.creates)
    amber.post(f"/studio/i/{did}/post", data={"channels": ["fb1"], "caption": "x", "when": "now", "override": "1"})
    assert len(fake.creates) == n


def test_ai_caption(env, monkeypatch):
    from app import ai
    amber = env["amber"]
    iid = add_image(env, "Thanksgiving hours")
    seen = {}
    monkeypatch.setattr(ai, "json_chat", lambda msgs, **k: seen.update(p=msgs[1]["content"]) or
                        {"captions": ["Happy Thanksgiving from our team!", "Closed Thursday and Friday; back Monday. Guaranteed fun."]})
    r = amber.post(f"/studio/i/{iid}/caption", json={"brief": "closed Thu/Fri"})
    caps = r.json()["captions"]
    assert caps[0]["notes"] == [] and any("guarantee" in n for n in caps[1]["notes"])
    assert "Schulz Orthodontics" in seen["p"] and "closed Thu/Fri" in seen["p"] and "Thanksgiving hours" in seen["p"]


def test_posting_panel_on_connections(env, monkeypatch):
    social = env["social"]
    page = env["admin"].get("/connections").text
    assert 'id="posting"' in page and "Not set up" in page
    monkeypatch.setattr(social, "check", lambda: True)
    monkeypatch.setattr(social, "integrations", lambda fresh=False: [1, 2, 3])
    r = env["admin"].post("/connections/posting", data={"api_key": "abc123", "api_url": ""})
    assert "3 Facebook, Instagram and Google channels" in r.text
    assert social.key_source() == "pasted" and "Ready" in env["admin"].get("/connections").text
    env["admin"].post("/connections/posting", data={"clear": "1"})
    assert social.key_source() is None
