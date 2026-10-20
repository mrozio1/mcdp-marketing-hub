"""Reviews & comments: inbox (Google reviews + Facebook / Instagram comments), actions, goals, scoreboard."""
import importlib
import io
import os
import re
from datetime import date, timedelta

import pytest


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_", "GBP_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1", "AI_API_KEY": "test"})
    from app import ai, connectors, db, field, places, referrals, reviews, users
    for m in (db, users, connectors, places, referrals, ai, field, reviews):
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
        editor = person(admin, "Ed Editor", "ed@example.com", "editor")
        viewer = person(admin, "Vi Viewer", "vi@example.com", "viewer")
        heights = db.add_practice("Heights Orthodontics", 0, 0)
        havens = db.add_practice("Havens Orthodontics", 0, 0)
        db.set_practice_social(heights, ["pg1"], ["ig1"])
        db.set_practice_social(havens, ["pg2"], [])
        with db.conn() as c:
            c.execute("INSERT INTO gbp_locations(name, title, address, practice_id, rating, total, full_at) "
                      "VALUES ('accounts/1/locations/11', 'Heights Orthodontics', 'Defiance, OH', ?, 4.8, 120, ?)",
                      (heights, reviews._iso(reviews.utcnow())))
        yield {"main": main, "db": db, "reviews": reviews, "cx": connectors, "ai": ai, "admin": admin, "editor": editor,
               "viewer": viewer, "heights": heights, "havens": havens}


def ago_iso(rv, **kw):
    return rv._iso(rv.utcnow() - timedelta(**kw))


def test_new_items_land_open_and_answered_ones_close(env):
    rv = env["reviews"]
    rv.upsert_items("facebook", "pg1", [
        {"id": "c1", "author": "Amanda P.", "text": "Do you take Delta Dental?", "created": ago_iso(rv, hours=5),
         "post_text": "Debond day!", "link": "https://facebook.com/c1", "replied": False},
        {"id": "c2", "author": "Tom H.", "text": "Thanks!", "created": ago_iso(rv, hours=30), "replied": True},
        {"id": "c-old", "author": "Old", "text": "Long ago", "created": ago_iso(rv, days=90), "replied": False},
    ], "Answered on Facebook")
    rows, counts = rv.inbox(status="open")
    keys = {r["external_id"] for r in rows}
    assert "c1" in keys and "c2" not in keys and "c-old" not in keys      # before the inbox started: skipped
    done, _ = rv.inbox(status="done")
    assert any(r["external_id"] == "c2" and r["outcome"] == "outside" for r in done)
    # answered later on Facebook itself -> closes
    rv.upsert_items("facebook", "pg1", [{"id": "c1", "author": "Amanda P.", "text": "Do you take Delta Dental?",
                                         "created": ago_iso(rv, hours=5), "replied": True}], "Answered on Facebook")
    assert not any(r["external_id"] == "c1" for r in rv.inbox(status="open")[0])
    with env["db"].conn() as c:
        c.execute("UPDATE inbox SET status='open', outcome='', handled_at=NULL WHERE external_id='c1'")


def test_unhappy_first_then_oldest_and_flags(env):
    rv = env["reviews"]
    rv.upsert_items("google", "accounts/1/locations/11", [
        {"id": "accounts/1/locations/11/reviews/r1", "author": "J. R.", "stars": 2, "text": "Waited 40 minutes.",
         "created": ago_iso(rv, hours=3), "replied": False},
        {"id": "accounts/1/locations/11/reviews/r2", "author": "Melissa T.", "stars": 5, "text": "Lovely team!",
         "created": ago_iso(rv, hours=60), "replied": False},
    ], "Answered on Google")
    rv.upsert_items("instagram", "ig1", [
        {"id": "i-spam", "author": "@promo", "text": "Get 10K followers FAST, DM us", "created": ago_iso(rv, hours=1)},
        {"id": "i-tag", "author": "@maddie", "text": "@sarah_t we should go here", "created": ago_iso(rv, hours=2)},
    ], "Answered on Instagram")
    rows, counts = rv.inbox(status="open")
    assert rows[0]["external_id"].endswith("/r1") and rows[0]["flag"] == "unhappy"
    flags = {r["external_id"]: r["flag"] for r in rows}
    assert flags["accounts/1/locations/11/reviews/r2"] == "overdue"
    assert flags["i-spam"] == "spam"
    assert rv.no_reply_hint(next(r for r in rows if r["external_id"] == "i-tag"))
    assert counts["google"] == 2 and counts["instagram"] == 2
    only, _ = rv.inbox(practice_id=env["havens"])
    assert only == []


def test_page_shows_inbox_tabs_and_nav_badge(env):
    r = env["viewer"].get("/reviews")
    assert r.status_code == 200
    assert "Reviews &amp; comments" in r.text and "Google scoreboard" in r.text
    assert 'class="nav-count"' in r.text
    assert "You have view-only access" in r.text
    assert "Post reply" not in r.text
    r = env["editor"].get("/reviews?src=instagram")
    assert "Post reply" in r.text and "Instagram" in r.text and "Waited 40 minutes" not in r.text
    r = env["editor"].get(f"/reviews?practice={env['heights']}&tab=score")
    assert "Heights Orthodontics" in r.text and "month by month" in r.text


def test_viewer_cannot_act(env):
    rv = env["reviews"]
    iid = next(r["id"] for r in rv.inbox()[0] if r["external_id"] == "c1")
    r = env["viewer"].post(f"/reviews/items/{iid}/done", data={"back": "/reviews"}, follow_redirects=False)
    assert r.status_code == 303 and rv.item(iid)["status"] == "open"


def test_reply_to_facebook_posts_as_page_and_records_who(env, monkeypatch):
    rv, cx = env["reviews"], env["cx"]
    calls = []
    monkeypatch.setattr(rv, "_meta_tokens", lambda force=False: {"facebook": {"pg1": ["T-system", "T-postiz"]},
                                                                  "instagram": {}, "ig_names": {}})
    monkeypatch.setattr(cx, "comment_action", lambda net, cid, action, tokens, message="": calls.append((net, cid, action, tokens, message)))
    iid = next(r["id"] for r in rv.inbox()[0] if r["external_id"] == "c1")
    r = env["editor"].post(f"/reviews/items/{iid}/reply", data={"message": "Thanks for asking! Please call us.", "back": "/reviews"})
    assert r.status_code == 200
    assert calls == [("facebook", "c1", "reply", ["T-system", "T-postiz"], "Thanks for asking! Please call us.")]
    it = rv.item(iid)
    assert it["status"] == "done" and it["outcome"] == "replied" and it["handled_by"] == "Ed Editor"
    # reopen puts it back
    env["editor"].post(f"/reviews/items/{iid}/reopen", data={"back": "/reviews?status=done"})
    assert rv.item(iid)["status"] == "open"


def test_failed_reply_keeps_the_text(env, monkeypatch):
    rv, cx = env["reviews"], env["cx"]
    monkeypatch.setattr(rv, "_meta_tokens", lambda force=False: {"facebook": {"pg1": ["T"]}, "instagram": {}, "ig_names": {}})

    def refuse(*a, **k):
        raise cx.ConnectorError("Meta refused: needs pages_manage_engagement")
    monkeypatch.setattr(cx, "comment_action", refuse)
    iid = next(r["id"] for r in rv.inbox()[0] if r["external_id"] == "c1")
    r = env["editor"].post(f"/reviews/items/{iid}/reply", data={"message": "My careful words", "back": "/reviews"})
    assert "pages_manage_engagement" in r.text
    assert rv.item(iid)["status"] == "open" and rv.item(iid)["draft"] == "My careful words"


def test_like_only_for_facebook_and_hide(env, monkeypatch):
    rv, cx = env["reviews"], env["cx"]
    calls = []
    monkeypatch.setattr(rv, "_meta_tokens", lambda force=False: {"facebook": {"pg1": ["T"]}, "instagram": {"ig1": ["I"]}, "ig_names": {}})
    monkeypatch.setattr(cx, "comment_action", lambda net, cid, action, tokens, message="": calls.append((net, cid, action)))
    spam = next(r["id"] for r in rv.inbox()[0] if r["external_id"] == "i-spam")
    with pytest.raises(rv.ReviewsError):
        rv.act(spam, "like", "Ed")
    rv.act(spam, "hide", "Ed")
    assert calls[-1] == ("instagram", "i-spam", "hide") and rv.item(spam)["outcome"] == "hidden"
    c1 = next(r["id"] for r in rv.inbox()[0] if r["external_id"] == "c1")
    rv.act(c1, "like", "Ed")
    assert calls[-1] == ("facebook", "c1", "like") and rv.item(c1)["outcome"] == "liked"


def test_google_reply_goes_through_business_profile(env, monkeypatch):
    rv, cx = env["reviews"], env["cx"]
    sent = []

    class FakeGBP:
        def __init__(self, token):
            pass

        def reply(self, name, text):
            sent.append((name, text))
    monkeypatch.setattr(cx, "GBPClient", FakeGBP)
    iid = next(r["id"] for r in rv.inbox()[0] if r["external_id"].endswith("/r2"))
    rv.act(iid, "reply", "Ann Admin", "Thank you so much, Melissa!")
    assert sent == [("accounts/1/locations/11/reviews/r2", "Thank you so much, Melissa!")]
    with pytest.raises(rv.ReviewsError):
        rv.act(next(r["id"] for r in rv.inbox()[0] if r["external_id"].endswith("/r1")), "hide", "Ann")


def test_draft_endpoint_uses_ai_and_privacy_rules(env, monkeypatch):
    rv, ai = env["reviews"], env["ai"]
    seen = {}

    def fake_chat(messages, **kw):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[1]["content"]
        return "We're sorry about the wait. Please call our office so we can help."
    monkeypatch.setattr(ai, "_chat", fake_chat)
    iid = next(r["id"] for r in rv.inbox()[0] if r["external_id"].endswith("/r1"))
    r = env["editor"].post(f"/reviews/items/{iid}/draft", headers={"Accept": "application/json"})
    assert r.json()["draft"].startswith("We're sorry")
    assert "HIPAA" in seen["system"] and "2 of 5" in seen["user"]
    assert rv.item(iid)["draft"].startswith("We're sorry")
    r = env["viewer"].post(f"/reviews/items/{iid}/draft", headers={"Accept": "application/json"})
    assert r.status_code == 403


def test_goals_keep_their_history(env):
    rv = env["reviews"]
    h = env["heights"]
    with env["db"].conn() as c:
        c.execute("INSERT INTO review_goals(practice_id, month, goal) VALUES (?, '2026-03', 6)", (h,))
    assert rv.goal_for(h, "2026-02") == rv.DEFAULT_GOAL
    assert rv.goal_for(h, "2026-05") == 6
    cur = rv.month_key(date.today())
    env["editor"].post("/reviews/goals", data={f"g_{h}": "9", f"g_{env['havens']}": str(rv.goal_for(env["havens"], cur))})
    assert rv.goal_for(h, cur) == 9 and rv.goal_for(h, "2026-05") == 6
    env["editor"].post("/reviews/goals", data={"apply": "all", "all_goal": "5"})
    assert rv.goal_for(h, cur) == 5 and rv.goal_for(env["havens"], cur) == 5 and rv.goal_for(h, "2026-05") == 6
    r = env["viewer"].post("/reviews/goals", data={"apply": "all", "all_goal": "1"})
    assert rv.goal_for(h, cur) == 5


def test_scoreboard_counts_from_google_and_by_hand(env):
    rv = env["reviews"]
    months = rv.months_back(12)
    with env["db"].conn() as c:
        for i, m in enumerate(months[-3:]):
            for n in range(i + 4):     # 4, 5, 6 reviews in the last three months
                c.execute("INSERT INTO gbp_reviews(id, location, stars, created) VALUES (?, 'accounts/1/locations/11', 5, ?)",
                          (f"accounts/1/locations/11/reviews/s{m}{n}", f"{m}-02T12:00:00"))
    env["editor"].post("/reviews/manual", data={"month": months[-2], f"m_{env['havens']}": "2"})
    board = rv.scoreboard()
    heights_row = next(r for r in board["rows"] if r["id"] == env["heights"])
    havens_row = next(r for r in board["rows"] if r["id"] == env["havens"])
    assert heights_row["source"] == "google" and havens_row["source"] == "manual"
    assert board["series"][-2] == 5 + 2                     # both practices last month
    assert board["series"][-4] == 0                         # Heights synced (0), Havens no count
    assert board["history"][1]["partial"] is False
    one = rv.scoreboard(env["havens"])
    assert one["series"][-2] == 2 and one["series"][-3] is None
    r = env["viewer"].get("/reviews.xlsx")
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.active["A3"].value in ("Havens Orthodontics", "Heights Orthodontics")


def test_comment_readers_parse_meta_shapes(env, monkeypatch):
    cx = env["cx"]
    since = date.today() - timedelta(days=60)
    now = date.today().isoformat() + "T10:00:00+0000"

    def fake_paged(path, params, limit_pages=50, token=None, stop=None):
        assert token == "T-good"
        if path == "pg1/published_posts":
            assert "comments.filter(toplevel)" in params["fields"]
            return [{"id": "pg1_9", "message": "Debond day!", "permalink_url": "https://fb/p9", "comments": {"data": [
                {"id": "cA", "message": "So proud!", "created_time": now, "from": {"id": "u1", "name": "Rachel G."}},
                {"id": "cB", "message": "Thanks all", "created_time": now, "from": {"id": "pg1", "name": "Heights"}},
                {"id": "cC", "message": "Hours?", "created_time": now, "from": {"id": "u2", "name": "Lisa"},
                 "comments": {"data": [{"from": {"id": "pg1"}}]}},
            ]}}]
        if path == "ig1/media":
            return [{"id": "m1", "caption": "Reel", "permalink": "https://ig/p/1", "timestamp": now, "comments": {"data": [
                {"id": "x1", "text": "Love this", "timestamp": now, "username": "carlos.r"},
                {"id": "x2", "text": "Thank you!", "timestamp": now, "username": "heightsortho"},
                {"id": "x3", "text": "How long?", "timestamp": now, "username": "jen",
                 "replies": {"data": [{"username": "HeightsOrtho"}]}},
            ]}}]
        raise AssertionError(path)

    def bad_then_good(path, params, limit_pages=50, token=None, stop=None):
        if token == "T-bad":
            raise cx.ConnectorError("(#10) permission")
        return fake_paged(path, params, limit_pages, token, stop)
    monkeypatch.setattr(cx, "_meta_paged", bad_then_good)
    fb, tok = cx.facebook_comments("pg1", ["T-bad", "T-good"], since)
    assert tok == "T-good" and [c["id"] for c in fb] == ["cA", "cC"]
    assert fb[0]["author"] == "Rachel G." and not fb[0]["page_replied"] and fb[1]["page_replied"]
    ig, _ = cx.instagram_comments("ig1", "@heightsortho", ["T-good"], since)
    assert [c["id"] for c in ig] == ["x1", "x3"] and ig[1]["page_replied"] and ig[0]["author"] == "@carlos.r"


def test_business_profile_reviews_parse(env, monkeypatch):
    cx = env["cx"]
    monkeypatch.setattr(cx, "setting", lambda name: {"GBP_CLIENT_ID": "id.apps.googleusercontent.com",
                                                     "GBP_CLIENT_SECRET": "secret-123"}.get(name, ""))
    pages = []

    def fake_http(url, data=None, headers=None, method=None, timeout=60):
        if url.endswith("/token"):
            return {"access_token": "ya29.x"}
        pages.append(url)
        if "pageToken" not in url:
            return {"averageRating": 4.7, "totalReviewCount": 3, "nextPageToken": "p2", "reviews": [
                {"name": "accounts/1/locations/11/reviews/a", "reviewer": {"displayName": "Kim"}, "starRating": "FIVE",
                 "comment": "Great", "createTime": "2026-10-01T12:00:00.123Z", "updateTime": "2026-10-01T12:00:00Z",
                 "reviewReply": {"comment": "Thanks Kim!", "updateTime": "2026-10-02T09:00:00Z"}}]}
        return {"reviews": [{"name": "accounts/1/locations/11/reviews/b", "reviewer": {"isAnonymous": True},
                             "starRating": "TWO", "createTime": "2026-09-01T12:00:00Z"}]}
    monkeypatch.setattr(cx, "_http", fake_http)
    g = cx.GBPClient("1//refresh")
    revs, avg, total = g.reviews("accounts/1/locations/11", all_pages=False)
    assert len(revs) == 1 and avg == 4.7 and total == 3 and revs[0]["reply"] == "Thanks Kim!" and revs[0]["stars"] == 5
    revs, _, _ = g.reviews("accounts/1/locations/11", all_pages=True)
    assert [r["stars"] for r in revs] == [5, 2] and revs[1]["author"] == "A Google user"


def test_sync_inbox_pulls_comments_and_reports(env, monkeypatch):
    rv, cx = env["reviews"], env["cx"]
    monkeypatch.setattr(cx, "meta_configured", lambda: True)
    monkeypatch.setattr(cx, "meta_pages", lambda: (
        [{"id": "pg1", "name": "Heights", "token": "T1", "tokens": ["T1"], "via": "system user"},
         {"id": "pg2", "name": "Havens", "token": None, "tokens": [], "via": "portfolio"}],
        [{"id": "ig1", "name": "@heightsortho", "token": "I1", "tokens": ["I1"], "via": "system user"}]))
    monkeypatch.setattr(cx, "facebook_comments", lambda pid, tokens, since: ([
        {"id": "new-1", "post_id": "p", "post_text": "Post", "post_link": "", "author": "Kevin M.",
         "text": "Called three times and no one answered.", "created": rv._iso(rv.utcnow()), "link": "",
         "hidden": False, "page_replied": False}], tokens[0]))
    monkeypatch.setattr(cx, "instagram_comments", lambda iid, user, tokens, since: ([], tokens[0]))
    st = rv.sync_inbox()
    assert st["accounts"] == 3 and st["new"] == 1 and st["error_count"] == 1     # Havens Page has no access
    it = next(r for r in rv.inbox()[0] if r["external_id"] == "new-1")
    assert it["flag"] == "unhappy" and it["practice_name"] == "Heights Orthodontics"
    r = env["editor"].post("/reviews/sync", data={"back": "/reviews"})
    assert "Checked 3 accounts" in r.text


def test_welcome_and_nav_point_to_reviews(env):
    r = env["viewer"].get("/")
    assert "Reviews &amp; comments" in r.text and "waiting for a reply" in r.text
