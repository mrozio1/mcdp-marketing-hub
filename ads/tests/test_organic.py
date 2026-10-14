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
    assert '<option value="pg1" selected>Heights Orthodontics (suggested)</option>' in html
    assert '<option value="ig1" selected>@heightsortho (suggested)</option>' in html
    assert "Connections" in html


def test_sync_skips_retired_metrics_and_old_posts(hub):
    c, db, sync, cx, _ = hub
    heights, havens, master, status = _setup(c, db, sync)
    r = status["results"][str(heights)]
    assert r["facebook"] == {"ok": True, "posts": 2} and r["instagram"] == {"ok": True, "posts": 2}
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
    assert "Organic (unpaid) social posts" in seen["ctx"] and "instagram | 5200 | 210" in seen["ctx"]


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
    assert '<option value="pg3" selected>Bandeen Orthodontics (suggested)</option>' in html
    assert "Pinnacle Ortho — no access yet" in html
    db.set_practice_social(bandeen, "pg3", "ig3")
    db.set_practice_social(pinnacle, "pg4", "")
    status = sync.sync_all(today=date(2026, 10, 12))
    assert status["results"][str(bandeen)]["facebook"] == {"ok": True, "posts": 1}     # read with Postiz's token
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
