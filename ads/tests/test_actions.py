"""Fixes end to end: prepare from live state, apply only on click, verify, undo, guardrails."""
import importlib
import re
from datetime import date

import pytest

from tests import mock_platforms as mp


@pytest.fixture
def hub(tmp_path, monkeypatch):
    srv = mp.start(9977)
    mp.reset()
    base = "http://127.0.0.1:9977"
    for k, v in {"ADS_DB": str(tmp_path / "ads.db"), "ADS_PASSWORD": "test-password-1",
                 "ADS_DISABLE_SCHEDULER": "1", "GOOGLE_ADS_BASE": base, "GOOGLE_OAUTH_TOKEN_URL": base + "/token",
                 "GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
                 "GOOGLE_ADS_DEVELOPER_TOKEN": mp.GOOGLE_DEV, "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "",
                 "META_ACCESS_TOKEN": mp.META_TOKEN, "META_GRAPH_BASE": base,
                 "ADS_SYNC_JOBS": "1"}.items():
        monkeypatch.setenv(k, v)
    from app import actions, ai, connectors, db, main, sync
    for m in (connectors, db, sync, actions, main):
        importlib.reload(m)
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    c.__enter__()
    c.post("/login", data={"password": "test-password-1"})
    db.set_setting("google_refresh_token", mp.REFRESH)
    pid = db.add_practice("Heights Orthodontics", 3000, 900)
    db.set_practice_accounts(pid, "555001", "4445556666")
    sync.sync_all(today=date(2026, 10, 12))
    yield c, db, pid, ai
    c.__exit__(None, None, None)
    srv.shutdown()
    srv.server_close()
    mp.reset()


def _open(db, pid):
    return {ch["title"]: ch for ch in db.changes(pid, ["proposed", "failed", "stale"])}


def test_prepare_proposes_exact_changes_and_changes_nothing(hub):
    c, db, pid, _ = hub
    before = repr(mp.STATE)
    c.post(f"/p/{pid}/fixes/prepare")
    assert repr(mp.STATE) == before                      # preparing never writes
    t = _open(db, pid)
    assert "Block “invisalign jobs” in Invisalign - Search" in t          # wasted, never converted
    assert not any("near me" in k for k in t)                           # converting term left alone
    assert "Pause PMax - Smile Promo" in t                              # zero-conversion campaign
    assert "Raise Invisalign - Search to $75.00/day (from $50.00)" in t  # +64% needed, capped at +50%
    assert "Raise PMax - Smile Promo to $30.00/day (from $20.00)" in t
    assert "Pause Heights - Smile Gallery Video" in t                   # Meta, zero results
    assert "Lower Heights - Free Consult Leads to $17.79/day (from $20.00)" in t       # Meta CBO campaign
    assert "Lower Heights - Smile Gallery Video › Video viewers to $13.34/day (from $15.00)" in t  # ad set budget
    assert "Capped at a 50% change" in t["Raise PMax - Smile Promo to $30.00/day (from $20.00)"]["detail"]
    html = c.get(f"/p/{pid}").text
    assert "Make the fixes" in html and "Apply selected" in html


def test_apply_then_undo_each_kind(hub):
    c, db, pid, _ = hub
    c.post(f"/p/{pid}/fixes/prepare")
    ids = [str(ch["id"]) for ch in db.changes(pid, ["proposed"])]
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": ids})
    assert r.status_code == 200 and "Applied 7 changes" in r.text
    S = mp.STATE
    assert list(S["g_negatives"].values()) == [("901", "invisalign jobs")]
    assert S["g_campaigns"]["902"]["status"] == "PAUSED"
    assert S["g_budgets"]["b1"]["micros"] == 75_000_000 and S["g_budgets"]["b2"]["micros"] == 30_000_000
    assert S["m_objects"]["c2"]["status"] == "PAUSED"
    assert S["m_objects"]["c1"]["daily_budget"] == "1779" and S["m_objects"]["as2"]["daily_budget"] == "1334"

    # Prepare again: already-applied things aren't proposed twice
    c.post(f"/p/{pid}/fixes/prepare")
    assert not any(k.startswith(("Block", "Pause")) for k in _open(db, pid))

    done = {ch["title"]: ch for ch in db.changes(pid, ["applied"])}
    for title in ("Block “invisalign jobs” in Invisalign - Search", "Pause PMax - Smile Promo",
                  "Raise Invisalign - Search to $75.00/day (from $50.00)", "Pause Heights - Smile Gallery Video",
                  "Lower Heights - Free Consult Leads to $17.79/day (from $20.00)"):
        r = c.post(f"/p/{pid}/fixes/{done[title]['id']}/undo")
        assert "Undone" in r.text, title
    assert S["g_negatives"] == {} and S["g_campaigns"]["902"]["status"] == "ENABLED"
    assert S["g_budgets"]["b1"]["micros"] == 50_000_000
    assert S["m_objects"]["c2"]["status"] == "ACTIVE" and S["m_objects"]["c1"]["daily_budget"] == "2000"
    assert "undone" in c.get(f"/p/{pid}").text


def test_out_of_date_proposal_changes_nothing(hub):
    c, db, pid, _ = hub
    c.post(f"/p/{pid}/fixes/prepare")
    ch = _open(db, pid)["Raise Invisalign - Search to $75.00/day (from $50.00)"]
    mp.STATE["g_budgets"]["b1"]["micros"] = 60_000_000          # someone edited it in Google Ads meanwhile
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(ch["id"])]})
    assert "is now $60.00, not $50.00" in r.text and "Nothing was changed" in r.text
    assert mp.STATE["g_budgets"]["b1"]["micros"] == 60_000_000
    assert db.change(ch["id"])["status"] == "stale"


def test_read_only_access_explains_the_fix(hub):
    c, db, pid, _ = hub
    c.post(f"/p/{pid}/fixes/prepare")
    t = _open(db, pid)
    mp.READ_ONLY.update(google=True, meta=True)
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(t["Pause PMax - Smile Promo"]["id"]),
                                                      str(t["Pause Heights - Smile Gallery Video"]["id"])]})
    assert "Nothing was applied" in r.text
    assert "change mcdp-ads-reader&#39;s access to Standard" in r.text or "access to Standard" in r.text
    meta = db.change(t["Pause Heights - Smile Gallery Video"]["id"])
    assert meta["status"] == "failed" and "ads_management" in meta["error"]
    mp.READ_ONLY.update(google=False, meta=False)                 # after fixing access, retry works
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(meta["id"])]})
    assert "Applied 1 change" in r.text


def test_dismiss(hub):
    c, db, pid, _ = hub
    c.post(f"/p/{pid}/fixes/prepare")
    ch = _open(db, pid)["Pause PMax - Smile Promo"]
    c.post(f"/p/{pid}/fixes/{ch['id']}/dismiss")
    assert db.change(ch["id"])["status"] == "dismissed"


def test_draft_ads_are_created_paused_and_removable(hub, monkeypatch):
    c, db, pid, ai = hub
    def fake(kind, ctx):
        assert "Heights Orthodontics" in ctx
        if kind == "google_ad":
            assert "Invisalign in Heights" in ctx                 # sees existing headlines
            return {"headlines": ["Invisalign in Heights MI", "Free Smile Consult", "Clear Aligners Near You",
                                  "This headline is far too long to be used in an ad"],
                    "descriptions": ["Straighter teeth with clear aligners. Book a free consult today.",
                                     "Flexible payment plans. Evening and Saturday appointments."]}
        return {"primary_text": "Thinking about braces? Meet our team for a free consult.",
                "headline": "Free Orthodontic Consult", "description": "Book online"}
    monkeypatch.setattr(ai, "generate_json", fake)
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    r = c.post(f"/p/{pid}/fixes/draft", data={"target": "google|Invisalign - Search"})
    assert "New ad written" in r.text
    r = c.post(f"/p/{pid}/fixes/draft", data={"target": "meta|Heights - Free Consult Leads"})
    assert "New ad written" in r.text
    drafts = [ch for ch in db.changes(pid, ["proposed"]) if ch["kind"] == "draft_ad"]
    g = next(d for d in drafts if d["platform"] == "google")
    assert len(g["payload"]["headlines"]) == 3                       # over-long headline dropped
    assert "Free Smile Consult" in c.get(f"/p/{pid}").text            # shown for review
    c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(d["id"]) for d in drafts]})
    (gad,) = mp.STATE["g_ads"].values()
    assert gad["status"] == "PAUSED" and gad["ad"]["finalUrls"] == ["https://heightsortho.example/invisalign"]
    ads = [o for o in mp.STATE["m_created"].values() if o["type"] == "ad"]
    assert ads[0]["status"] == "PAUSED" and ads[0]["adset_id"] == "as1"
    cr = [o for o in mp.STATE["m_created"].values() if o["type"] == "creative"][0]["spec"]
    assert cr["link_data"]["message"].startswith("Thinking about braces") and cr["link_data"]["image_hash"] == "abc123"
    assert cr["instagram_user_id"] == "888"                            # placements preserved
    for d in db.changes(pid, ["applied"]):
        assert "Undone" in c.post(f"/p/{pid}/fixes/{d['id']}/undo").text
    assert mp.STATE["g_ads"] == {} and mp.STATE["m_created"] == {}


def test_draft_refuses_unsuitable_campaigns(hub, monkeypatch):
    c, db, pid, ai = hub
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    monkeypatch.setattr(ai, "generate_json", lambda k, ctx: pytest.fail("AI shouldn't be called"))
    assert "isn&#39;t a Search campaign" in c.post(f"/p/{pid}/fixes/draft", data={"target": "google|PMax - Smile Promo"}).text \
        or "isn't a Search campaign" in c.post(f"/p/{pid}/fixes/draft", data={"target": "google|PMax - Smile Promo"}).text
    r = c.post(f"/p/{pid}/fixes/draft", data={"target": "meta|Heights - Smile Gallery Video"})
    assert "can" in r.text and "be copied" in r.text
