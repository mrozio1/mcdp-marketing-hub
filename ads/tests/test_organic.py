"""Organic Facebook / Instagram posts: sync, All / Paid / Organic views, combined totals, email, AI context."""
import importlib
import re
from datetime import date

import pytest

from tests import mock_platforms as mp


@pytest.fixture
def hub(tmp_path, monkeypatch):
    srv = mp.start(9983)
    mp.reset()
    base = "http://127.0.0.1:9983"
    for k, v in {"ADS_DB": str(tmp_path / "ads.db"), "ADS_PASSWORD": "test-password-1",
                 "ADS_DISABLE_SCHEDULER": "1", "GOOGLE_ADS_BASE": base, "GOOGLE_OAUTH_TOKEN_URL": base + "/token",
                 "GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
                 "GOOGLE_ADS_DEVELOPER_TOKEN": mp.GOOGLE_DEV, "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "",
                 "META_ACCESS_TOKEN": mp.META_TOKEN, "META_GRAPH_BASE": base}.items():
        monkeypatch.setenv(k, v)
    from app import actions, ai, connectors, db, main, organic, sync
    for m in (connectors, db, sync, organic, actions, main):
        importlib.reload(m)
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    c.__enter__()
    c.post("/login", data={"password": "test-password-1"})
    db.set_setting("google_refresh_token", mp.REFRESH)
    yield c, db, sync, connectors, ai
    c.__exit__(None, None, None)
    srv.shutdown()
    mp.reset()


def _setup(c, db, sync):
    sync.refresh_account_lists()
    heights = db.add_practice("Heights Orthodontics", 3000, 900, "Heights")
    havens = db.add_practice("Havens Orthodontics", 1000, 0, "Havens")
    master = db.add_practice("MCDP Ads", 5000, 0)
    db.set_practice_accounts(heights, "555001", "4445556666")
    db.set_practice_accounts(master, "555001", "4445556666")
    # Link Page + Instagram through the form, like the team would
    c.post(f"/p/{heights}/accounts", data={"meta_account_id": "555001", "google_customer_id": "4445556666",
                                           "fb_page_id": "pg1", "ig_account_id": "ig1"})
    c.post(f"/p/{havens}/accounts", data={"meta_account_id": "", "google_customer_id": "",
                                          "fb_page_id": "pg2", "ig_account_id": ""})
    status = sync.sync_all(today=date(2026, 10, 12))
    return heights, havens, master, status


def test_pages_listed_and_suggested(hub):
    c, db, sync, cx, _ = hub
    res = sync.refresh_account_lists()
    assert res["social"] == {"ok": True, "count": 2, "ig": 1, "no_access": 0}
    assert sync.cached_list("ig_accounts") == [{"id": "ig1", "name": "@heightsortho", "page_id": "pg1",
                                                "via": "system user", "access": True}]
    assert all("token" not in pg for pg in sync.cached_list("fb_pages"))      # page tokens never stored
    pid = db.add_practice("Heights Orthodontics", 0, 0)
    html = c.get(f"/p/{pid}").text
    assert 'name="fb_page_id" value="pg1" checked' in html and 'name="ig_account_id" value="ig1" checked' in html
    assert 'name="fb_page_id" value="pg2" >' in html or 'name="fb_page_id" value="pg2">' in html   # Havens not pre-ticked
    assert "(suggested)" in html
    assert "Connections" in html


def test_sync_skips_retired_metrics_and_old_posts(hub):
    c, db, sync, cx, _ = hub
    heights, havens, master, status = _setup(c, db, sync)
    r = status["results"][str(heights)]
    assert r["facebook"] == {"ok": True, "posts": 2, "accounts": 1} and r["instagram"] == {"ok": True, "posts": 2, "accounts": 1}
    assert status["results"][str(havens)]["facebook"]["ok"]
    assert {"post_impressions", "post_impressions_unique"} <= cx._REJECTED_METRICS     # retired names dropped
    posts = {p["post_id"]: p for p in db.posts_for(["pg1", "ig1"], date(2026, 1, 1), date(2026, 12, 31))}
    assert "pg1_old" not in posts                                              # before the window
    assert posts["pg1_1"]["views"] == 2400 and posts["pg1_1"]["interactions"] == 50 and posts["pg1_1"]["clicks"] == 55
    assert posts["ig1_1"]["views"] == 5200 and posts["ig1_1"]["saves"] == 30 and posts["ig1_1"]["interactions"] == 210
    sync.sync_all(today=date(2026, 10, 12))                                    # re-sync updates, no duplicates
    assert len(db.posts_for(["pg1", "ig1"], date(2026, 1, 1), date(2026, 12, 31))) == 4


def test_all_paid_organic_switch_and_combined_totals(hub):
    c, db, sync, cx, _ = hub
    heights, havens, master, _ = _setup(c, db, sync)
    allv = c.get(f"/p/{heights}").text
    assert "Paid and organic together" in allv and "Organic posts" in allv and "Meta Ads" in allv
    # The "Heights" filter keeps Meta's "Heights - ..." campaigns (Google's mock campaigns don't match)
    assert "63,800" in allv                     # 54,000 ad impressions + 9,800 post views
    assert "54,000 from ads" in allv and "9,800 from posts" in allv
    assert "771" in allv                        # 432 ad clicks + 339 post interactions
    assert 'aria-current="page">All</a>' in allv
    paid = c.get(f"/p/{heights}?show=paid").text
    assert "Organic posts</h2>" not in paid and "Paid and organic together" not in paid and "Make the fixes" in paid
    org = c.get(f"/p/{heights}?show=organic").text
    assert "Organic posts</h2>" in org and "Budget pacing" not in org and "Make the fixes" not in org
    assert "Before your first adjustment" in org and "https://instagram.com/p/1" in org
    assert org.index("Before your first adjustment") < org.index("Free smile consults")   # top posts by views
    assert "3,300" in org and "6,500" in org    # Facebook views, Instagram views


def test_master_rolls_up_every_linked_account(hub):
    c, db, sync, cx, _ = hub
    heights, havens, master, _ = _setup(c, db, sync)
    html = c.get(f"/p/{master}?show=organic").text
    assert "every Page and Instagram account linked to a practice" in html
    assert "Havens is hiring!" in html and "Free smile consults" in html


def test_organic_in_email_and_ai_context(hub, monkeypatch):
    c, db, sync, cx, ai = hub
    heights, *_ = _setup(c, db, sync)
    html = c.get(f"/p/{heights}/email").text
    assert "Organic posts" in html and "Together with ads, the practice was seen <b>63,800</b> times" in html
    seen = {}
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    monkeypatch.setattr(ai, "generate", lambda kind, ctx: seen.setdefault("ctx", ctx) and "## Report\nok")
    c.post(f"/p/{heights}/ai/report")
    assert "Organic (unpaid) social posts" in seen["ctx"] and "instagram | @heightsortho | 5200 | 210" in seen["ctx"]


def test_page_permission_problem_is_reported(hub, monkeypatch):
    c, db, sync, cx, _ = hub
    heights, *_ = _setup(c, db, sync)
    # Page removed from the system user in Business Settings: it disappears from me/accounts
    monkeypatch.setattr(cx, "meta_pages", lambda: ([], []))
    status = sync.sync_all(today=date(2026, 10, 12))
    fb = status["results"][str(heights)]["facebook"]
    assert not fb["ok"] and "isn't visible any more" in fb["error"] and "Postiz" in fb["error"]


def test_portfolio_pages_and_postiz_fallback(hub, monkeypatch):
    """Like Postiz: find Pages across the business portfolio; read ones the system user can't via Postiz."""
    c, db, sync, cx, _ = hub
    from app import postiz
    mp.BUSINESS["on"] = True
    monkeypatch.setattr(postiz, "channels", lambda: {
        "facebook": {"pg3": {"name": "Bandeen Orthodontics", "token": mp.PAGE_TOKENS["pg3"]}}, "instagram": {}})
    res = sync.refresh_account_lists()
    assert res["meta"] == {"ok": True, "count": 4}                    # 3 assigned + Bandeen from the portfolio
    pages = {p["id"]: p for p in sync.cached_list("fb_pages")}
    assert set(pages) == {"pg1", "pg2", "pg3", "pg4"}
    assert pages["pg3"]["via"] == "Postiz" and pages["pg3"]["access"]
    assert pages["pg4"]["access"] is False                           # client Page nobody can read yet
    assert res["social"]["no_access"] == 2                           # pg4 and Bandeen's Instagram
    bandeen = db.add_practice("Bandeen Orthodontics", 0, 0, "Bandeen")
    pinnacle = db.add_practice("Pinnacle Ortho", 0, 0, "Pinnacle")
    html = c.get(f"/p/{bandeen}").text
    assert 'name="fb_page_id" value="pg3" checked' in html
    assert 'Pinnacle Ortho <span class="danger small">no access yet</span>' in html
    db.set_practice_social(bandeen, "pg3", "ig3")
    db.set_practice_social(pinnacle, "pg4", "")
    status = sync.sync_all(today=date(2026, 10, 12))
    assert status["results"][str(bandeen)]["facebook"] == {"ok": True, "posts": 1, "accounts": 1}   # via Postiz's token
    assert "connect it in Postiz" in status["results"][str(bandeen)]["instagram"]["error"]
    assert "connect it in Postiz" in status["results"][str(pinnacle)]["facebook"]["error"]
    assert "Bandeen open house" in c.get(f"/p/{bandeen}?show=organic").text
    assert all("token" not in x for x in sync.cached_list("fb_pages") + sync.cached_list("ig_accounts"))


def test_postiz_channels_reads_integration_table(monkeypatch):
    import sys, types
    from app import postiz
    rows = [("facebook", "pg9", "Havens Orthodontics", "PAGETOKEN9"),
            ("instagram", "ig9", "havensortho", "PAGETOKEN9___USERTOKEN9")]
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql):
            assert '"deletedAt" IS NULL' in sql and '"disabled" = false' in sql
            return types.SimpleNamespace(fetchall=lambda: rows)
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda url, connect_timeout: Conn()))
    monkeypatch.setenv("POSTIZ_DATABASE_URL", "postgresql://x")
    ch = postiz.channels()
    assert ch["facebook"]["pg9"] == {"name": "Havens Orthodontics", "token": "PAGETOKEN9"}
    assert ch["instagram"]["ig9"]["token"] == "PAGETOKEN9"            # page token, not the user token
    monkeypatch.delenv("POSTIZ_DATABASE_URL")
    assert postiz.channels() == {"facebook": {}, "instagram": {}}


def test_post_performance_report_ranks_within_practice(hub):
    c, db, sync, cx, _ = hub
    from app import organic
    heights, havens, master, _ = _setup(c, db, sync)
    html = c.get(f"/p/{heights}/posts?period=90d").text
    assert "Social post performance" in html and "Every post, ranked" in html
    body = html.split("Every post, ranked")[1]
    # Heights' 4 posts only (Havens' post is another practice), ranked by engagement
    order = [t for t in ("Before your first adjustment", "Team photo", "Free smile consults", "Meet Dr. Lee") if t in body]
    assert order == ["Before your first adjustment", "Team photo", "Free smile consults", "Meet Dr. Lee"]
    assert "Havens is hiring" not in body
    assert 'href="https://instagram.com/p/1"' in body and "Top 25%" in body and "Bottom 25%" in body
    assert "Best post: “Before your first adjustment: what to expect” with 210 engagements" in html
    # Engagement = likes + comments + shares + saves: 150 + 12 + 18 + 30 = 210
    by_views = c.get(f"/p/{heights}/posts?period=90d&sort=views").text.split("Every post, ranked")[1]
    assert by_views.index("Free smile consults") < by_views.index("Team photo")     # 2,400 vs 1,300 views
    csv = c.get(f"/p/{heights}/posts.csv?period=90d").text
    lines = csv.strip().splitlines()
    assert lines[0].lstrip("\ufeff").startswith("Rank,Practice,Location (account),Date,Network,Post,Link,Views")
    assert lines[1].startswith("1,Heights Orthodontics,@heightsortho,2026-10-10,Instagram,Before your first adjustment")
    assert len(lines) == 5
    # Titles come from the first sentence/line; empty posts get a type label
    assert organic.post_title({"text": "Free smile consults all October! Book online."}) == "Free smile consults all October!"
    assert organic.post_title({"text": "", "media_type": "REELS"}) == "Reel"


def test_post_report_email_for_leadership(hub):
    c, db, sync, cx, _ = hub
    heights, *_ = _setup(c, db, sync)
    r = c.get(f"/p/{heights}/email?inc=postrank&period=90d")
    assert "Social post performance" in r.text and "Every post" not in r.text
    assert "Heights Orthodontics — social post performance, last 90 days" in r.text
    import email, email.policy
    msg = email.message_from_bytes(c.get(f"/p/{heights}/email.eml?inc=postrank&period=90d").content,
                                   policy=email.policy.default)
    html = msg.get_body(("html",)).get_content()
    assert "Before your first adjustment" in html and "class=" not in html and "<style" not in html


def test_master_post_report_shows_practice_names(hub):
    c, db, sync, cx, _ = hub
    heights, havens, master, _ = _setup(c, db, sync)
    html = c.get(f"/p/{master}/posts?period=90d").text
    assert "all practices" in html and "Havens is hiring" in html and "Havens Orthodontics" in html


def test_new_post_without_stats_still_shows_and_status_is_visible(hub):
    """A post from yesterday (no stats from Meta yet) must still appear; the page says what the sync found."""
    c, db, sync, cx, _ = hub
    mp.INSIGHTS_DENIED.add("pg1_1")
    heights, havens, master, status = _setup(c, db, sync)
    fb = status["results"][str(heights)]["facebook"]
    assert fb["ok"] and fb["posts"] == 2 and "views not available yet for 1 post" in fb["note"]
    posts = {p["post_id"]: p for p in db.posts_for(["pg1"], date(2026, 1, 1), date(2026, 12, 31))}
    assert posts["pg1_1"]["views"] is None and posts["pg1_1"]["likes"] == 40      # kept, counts still read
    assert any(l[1] == "/v25.0/pg1/published_posts" for l in mp.LOG)              # published_posts edge used
    html = c.get(f"/p/{heights}?show=organic").text
    assert "Facebook · Heights Orthodontics:" in html and "2 posts found in the last sync" in html
    conn = c.get("/connections").text
    assert "Facebook posts ✓" in conn and "Instagram posts ✓" in conn


def test_old_ad_upload_does_not_hide_recent_posts(hub, monkeypatch):
    c, db, sync, cx, _ = hub
    heights, *_ = _setup(c, db, sync)
    # An ad export from August uploaded later must not narrow the organic window
    rows = [{"campaign": "Heights - Old", "name": "", "date": date(2026, 8, 5), "date_start": date(2026, 8, 5),
             "date_end": date(2026, 8, 5), "spend": 10.0, "impressions": 100, "clicks": 5, "conversions": 0.0,
             "conv_value": 0.0, "status": "", "budget": 0.0, "match_type": ""}]
    db.save_upload(heights, {"platform": "google", "level": "campaign", "rows": rows, "date_start": date(2026, 8, 5),
                             "date_end": date(2026, 8, 5), "warnings": []}, "august.csv")
    monkeypatch.setattr(sync, "sync_window", lambda today=None: (date(2026, 10, 1), date(2026, 10, 12)))
    html = c.get(f"/p/{heights}?show=organic").text
    assert "Free smile consults" in html and "2026-10-01 to 2026-10-12" in html


def test_several_locations_merge_into_one_practice(hub):
    c, db, sync, cx, _ = hub
    sync.refresh_account_lists()
    pid = db.add_practice("Waterford Orthodontics", 0, 0, "Waterford")
    # Tick two Facebook Pages (two locations) and one Instagram account on the practice's form
    c.post(f"/p/{pid}/accounts", data={"meta_account_id": "", "google_customer_id": "", "social": "1",
                                       "fb_page_id": ["pg1", "pg2"], "ig_account_id": ["ig1"]})
    p = db.practice(pid)
    assert db.social_ids(p, "fb") == ["pg1", "pg2"] and db.social_ids(p, "ig") == ["ig1"]
    status = sync.sync_all(today=date(2026, 10, 12))
    fb = status["results"][str(pid)]["facebook"]
    assert fb == {"ok": True, "posts": 3, "accounts": 2}                      # 2 + 1 posts, merged

    html = c.get(f"/p/{pid}?show=organic").text
    assert "3 locations combined" in html and "By location" in html
    loc = html.split("By location")[1].split("Top posts")[0]
    assert "Heights Orthodontics" in loc and "Havens Orthodontics" in loc and "@heightsortho" in loc
    assert "Facebook · Heights Orthodontics:" in html and "Facebook · Havens Orthodontics:" in html
    assert html.count('name="fb_page_id" value="pg1" checked') == 1 and 'value="pg2" checked' in html

    rep = c.get(f"/p/{pid}/posts?period=90d").text
    ranked = rep.split("Every post, ranked")[1]
    assert '<span class="loc">Havens Orthodontics</span>' in ranked and '<span class="loc">@heightsortho</span>' in ranked
    assert "Havens is hiring" in ranked                                       # the second location's post merged in
    assert "5 posts" not in rep and ">5<" in rep                               # 5 posts total across locations
    csv = c.get(f"/p/{pid}/posts.csv?period=90d").text
    assert ",Havens Orthodontics,2026-10-05,Facebook,Havens is hiring!" in csv

    email_html = c.get(f"/p/{pid}/email?inc=postrank&period=90d").text
    assert "By location" in email_html and "Havens Orthodontics" in email_html

    # Unticking everything unlinks
    c.post(f"/p/{pid}/accounts", data={"meta_account_id": "", "google_customer_id": "", "social": "1"})
    p = db.practice(pid)
    assert db.social_ids(p, "fb") == [] and db.social_ids(p, "ig") == []
