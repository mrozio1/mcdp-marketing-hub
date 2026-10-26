"""Reporting false Google reviews: marked reported (the report itself is made in Google's tool), followed up,
and marked Removed by Google when a complete pull no longer returns them."""
import importlib
import os
import re
from datetime import timedelta

import pytest

LOC = "accounts/1/locations/11"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_", "GBP_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
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
        db.set_practice_social(heights, ["pg1"], [])
        db.set_setting("gbp_refresh_token", "rt")
        db.set_setting("gbp_client_id", "id.apps.googleusercontent.com")
        db.set_setting("gbp_client_secret", "secret-123456")
        with db.conn() as c:
            c.execute("INSERT INTO gbp_locations(name, title, address, practice_id, rating, total, full_at) "
                      "VALUES (?, 'Heights Orthodontics', 'Sterling Heights, MI', ?, 4.8, 2, ?)",
                      (LOC, heights, reviews._iso(reviews.utcnow())))
        yield {"main": main, "db": db, "rv": reviews, "cx": connectors, "editor": editor, "viewer": viewer,
               "heights": heights}


def _review(rv, rid, stars, text, days):
    t = rv._iso(rv.utcnow() - timedelta(days=days))
    return {"id": f"{LOC}/reviews/{rid}", "author": f"Person {rid}", "stars": stars, "text": text, "created": t,
            "updated": t, "reply": "", "reply_at": None}


def _google(monkeypatch, cx, revs, total, calls=None):
    class FakeGBP:
        def __init__(self, token):
            pass

        def reviews(self, location, all_pages=False):
            if calls is not None:
                calls.append(all_pages)
            return list(revs), 4.5, total
    monkeypatch.setattr(cx, "GBPClient", FakeGBP)


def test_report_follow_up_and_removal(env, monkeypatch):
    rv, cx, ed = env["rv"], env["cx"], env["editor"]
    fake = _review(rv, "f1", 1, "Worst place ever, never been there but whatever", 2)
    real = _review(rv, "r1", 5, "Lovely team", 1)
    _google(monkeypatch, cx, [fake, real], 2)
    rv.sync_google()
    iid = next(r["id"] for r in rv.inbox()[0] if r["external_id"] == fake["id"])

    page = ed.get(f"/reviews?item={iid}").text
    assert "Report to Google" in page and "Conflict of interest" in page and "support.google.com/business/workflow" in page
    assert "Person f1" in page and "Report a new review for removal" in page

    # viewers can't; Facebook comments can't be reported; a reason is required
    assert env["viewer"].post(f"/reviews/items/{iid}/report", data={"reason": "spam"}, follow_redirects=False).status_code == 303
    assert rv.item(iid)["status"] == "open"
    r = ed.post(f"/reviews/items/{iid}/report", data={"reason": "nope", "back": "/reviews"}, follow_redirects=True)
    assert "Pick the reason" in r.text

    r = ed.post(f"/reviews/items/{iid}/report", data={"reason": "spam", "note": "No patient by this name",
                                                       "back": "/reviews"}, follow_redirects=True)
    assert "Marked as reported" in r.text
    it = rv.item(iid)
    assert it["status"] == "reported" and it["reported_by"] == "Ed Editor" and it["report_note"] == "No patient by this name"
    assert iid not in [x["id"] for x in rv.inbox()[0]]                         # out of Needs reply
    rep = ed.get("/reviews?status=reported").text
    assert "Reported as Spam by Ed Editor" in rep and "No patient by this name" in rep
    assert rv.scoreboard()["reported"] == 1

    # still up: a pull that misses it but isn't complete never counts as a removal; reported locations pull every page
    calls = []
    _google(monkeypatch, cx, [real], 2, calls)
    rv.sync_google()
    assert calls == [True] and rv.item(iid)["status"] == "reported"

    # a week later it's flagged for follow-up
    with env["db"].conn() as c:
        c.execute("UPDATE inbox SET reported_at=? WHERE id=?", (rv._iso(rv.utcnow() - timedelta(days=8)), iid))
    page = ed.get(f"/reviews?status=reported&item={iid}").text
    assert "Still up 8d" in page and "Check status or appeal" in page

    # Google removes it: a complete pull without it
    _google(monkeypatch, cx, [real], 1)
    out = rv.sync_google()
    assert out[LOC]["removed"] == 1
    it = rv.item(iid)
    assert it["status"] == "done" and it["outcome"] == "removed" and it["removed_at"]
    with env["db"].conn() as c:
        assert c.execute("SELECT COUNT(*) FROM gbp_reviews WHERE id=?", (fake["id"],)).fetchone()[0] == 0
    done = ed.get("/reviews?status=done").text
    assert "Removed by Google" in done
    board = rv.scoreboard()
    row = next(x for x in board["rows"] if x["id"] == env["heights"])
    assert row["removed"] == 1 and row["reported"] == 0
    assert "1 removed" in ed.get("/reviews?tab=score").text


def test_undo_report_and_comments_cannot_be_reported(env, monkeypatch):
    rv, cx, ed = env["rv"], env["cx"], env["editor"]
    other = _review(rv, "u1", 2, "Meh", 1)
    _google(monkeypatch, cx, [other, _review(rv, "r1", 5, "Lovely team", 1)], 2)
    rv.sync_google()
    iid = next(r["id"] for r in rv.inbox()[0] if r["external_id"] == other["id"])
    ed.post(f"/reviews/items/{iid}/report", data={"reason": "off_topic", "back": "/reviews"})
    assert rv.item(iid)["status"] == "reported"
    r = ed.post(f"/reviews/items/{iid}/unreport", data={"back": "/reviews?status=reported"}, follow_redirects=True)
    assert "Report undone" in r.text and rv.item(iid)["status"] == "open" and not rv.item(iid)["reported_at"]

    rv.upsert_items("facebook", "pg1", [{"id": "c1", "author": "Someone", "text": "spam spam", "created":
                                         rv._iso(rv.utcnow())}], "Answered on Facebook")
    cid = next(r["id"] for r in rv.inbox()[0] if r["source"] == "facebook")
    with pytest.raises(rv.ReviewsError):
        rv.report(cid, "spam", "", "Ed")
    assert "Report to Google" not in ed.get(f"/reviews?item={cid}").text
