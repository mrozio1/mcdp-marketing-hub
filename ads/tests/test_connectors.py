import importlib
import os
from datetime import date

import pytest

from tests import mock_platforms as mp

PORT = 9955


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    srv = mp.start(PORT)
    base = f"http://127.0.0.1:{PORT}"
    os.environ.update({
        "ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"),
        "META_GRAPH_BASE": base, "META_ACCESS_TOKEN": mp.META_TOKEN,
        "GOOGLE_ADS_BASE": base, "GOOGLE_OAUTH_TOKEN_URL": base + "/token",
        "GOOGLE_OAUTH_AUTH_URL": base + "/o/oauth2/v2/auth",
        "GOOGLE_ADS_DEVELOPER_TOKEN": mp.GOOGLE_DEV, "GOOGLE_OAUTH_CLIENT_ID": "cid",
        "GOOGLE_OAUTH_CLIENT_SECRET": "secret", "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "111-222-3333",
    })
    from app import connectors, db, sync
    importlib.reload(connectors)
    importlib.reload(db)
    importlib.reload(sync)
    db.init()
    yield connectors, db, sync
    srv.shutdown()
    srv.server_close()


def test_meta_accounts_and_rows(env):
    cx, db, sync = env
    accts = cx.meta_accounts()
    assert [a["name"] for a in accts] == ["Havens Orthodontics", "Heights Orthodontics", "Old Test Account"]
    assert accts[2]["active"] is False
    rows = cx.meta_campaign_rows("act_555001", date(2026, 10, 1), date(2026, 10, 12))
    assert len(rows) == 24                                  # both pages read: 2 campaigns x 12 days
    lead = [r for r in rows if r["campaign"] == "Heights - Free Consult Leads"]
    assert lead[0]["conversions"] == 1                      # 'lead' counted once, not doubled with lead_grouped
    assert lead[0]["spend"] == 14.5 and lead[0]["clicks"] == 22 and lead[0]["date"] == date(2026, 10, 1)
    video = [r for r in rows if r["campaign"] == "Heights - Smile Gallery Video"]
    assert video[0]["conversions"] == 0


def test_meta_bad_token_message(env, monkeypatch):
    cx, *_ = env
    monkeypatch.setenv("META_ACCESS_TOKEN", "wrong")
    with pytest.raises(cx.ConnectorError, match="Invalid OAuth access token"):
        cx.meta_accounts()


def test_google_oauth_accounts_and_rows(env):
    cx, db, sync = env
    assert cx.google_exchange_code("auth-code-1", "https://ads.example/google/callback") == mp.REFRESH
    g = cx.GoogleClient(mp.REFRESH)
    accts = g.accounts()
    assert [a["id"] for a in accts] == ["4445557777", "4445556666"]   # manager account excluded, sorted by name
    rows = g.campaign_rows("444-555-6666", date(2026, 10, 1), date(2026, 10, 12))
    assert len(rows) == 24
    inv = [r for r in rows if r["campaign"] == "Invisalign - Search"][0]
    assert inv["spend"] == 48.25 and inv["impressions"] == 410 and inv["conversions"] == 1.5
    pmax = [r for r in rows if r["campaign"] == "PMax - Smile Promo"][0]
    assert pmax["conversions"] == 0 and pmax["spend"] == 20.0      # missing metrics default to 0
    terms = g.search_term_rows("4445556666", date(2026, 10, 1), date(2026, 10, 12))
    assert {t["name"]: t["spend"] for t in terms} == {"invisalign cost near me": 235.2, "invisalign jobs": 60.45}


def test_google_errors_are_readable(env, monkeypatch):
    cx, *_ = env
    with pytest.raises(cx.ConnectorError, match="expired or revoked"):
        cx.GoogleClient("bad-refresh")
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "unapproved")
    with pytest.raises(cx.ConnectorError, match="developer token is not approved"):
        cx.GoogleClient(mp.REFRESH).accounts()


def test_google_finds_accounts_whatever_manager_id_is_typed(env, monkeypatch):
    cx, *_ = env
    # Wrong "manager" ID (actually a practice account the robot wasn't added to directly)
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "444-555-6666")
    g = cx.GoogleClient(mp.REFRESH)
    assert [a["id"] for a in g.accounts()] == ["4445557777", "4445556666"]
    assert len(g.campaign_rows("4445556666", date(2026, 10, 1), date(2026, 10, 2))) == 4   # routed via the manager
    # No manager ID at all
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")
    g = cx.GoogleClient(mp.REFRESH)
    assert len(g.search_term_rows("4445557777", date(2026, 10, 1), date(2026, 10, 2))) == 2


def test_google_robot_added_to_practice_accounts_directly(env, monkeypatch):
    cx, *_ = env
    monkeypatch.setattr(mp, "ACCESSIBLE", {"4445556666"})
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "111-222-3333")   # typed, but robot isn't on it
    g = cx.GoogleClient(mp.REFRESH)
    assert g.accounts() == [{"id": "4445556666", "name": "Heights Orthodontics", "active": True}]
    assert len(g.campaign_rows("4445556666", date(2026, 10, 1), date(2026, 10, 1))) == 2


def test_google_robot_not_added_anywhere(env, monkeypatch):
    cx, *_ = env
    monkeypatch.setattr(mp, "ACCESSIBLE", set())
    with pytest.raises(cx.ConnectorError, match="can't see any Google Ads accounts yet"):
        cx.GoogleClient(mp.REFRESH).accounts()


def test_sync_window():
    from app.sync import sync_window
    assert sync_window(date(2026, 10, 13)) == (date(2026, 10, 1), date(2026, 10, 13))
    assert sync_window(date(2026, 10, 2)) == (date(2026, 9, 1), date(2026, 9, 30))
    assert sync_window(date(2026, 1, 3)) == (date(2025, 12, 1), date(2025, 12, 31))


def test_sync_all_feeds_analysis_and_prunes(env):
    cx, db, sync = env
    from app.analyze import analyze_campaigns, pacing
    db.set_setting("google_refresh_token", mp.REFRESH)
    pid = db.add_practice("Heights Orthodontics", 3000, 900)
    db.set_practice_accounts(pid, "555001", "4445556666")
    for _ in range(5):
        status = sync.sync_all(today=date(2026, 10, 12))
    r = status["results"][str(pid)]
    assert r["meta"]["ok"] and r["google"]["ok"] and r["google"]["terms"] == 2
    ups = db.uploads(pid)
    assert len([u for u in ups if u["platform"] == "google" and u["level"] == "campaign"]) == 3   # pruned to 3
    g = db.latest_upload(pid, "google", "campaign")
    rows = db.upload_rows(g["id"])
    camps, tot, findings = analyze_campaigns(rows)
    assert round(tot["spend"], 2) == round(12 * (48.25 + 20.0), 2)
    assert any(f["kind"] == "zero-conv-campaign" and f["campaign"] == "PMax - Smile Promo" for f in findings)
    pc = pacing(rows, 3000)
    assert pc["days_elapsed"] == 12 and pc["spend"] == 819.0


def _make_service_account_json():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    import json
    return json.dumps({"type": "service_account", "project_id": "mcdp-ads", "private_key_id": "abc",
                       "private_key": pem, "client_email": mp.SA_EMAIL, "client_id": "1",
                       "token_uri": "https://oauth2.googleapis.com/token", "extra": "ignored"})


def test_service_account_key_validation(env):
    cx, *_ = env
    with pytest.raises(cx.ConnectorError, match="isn't JSON"):
        cx.parse_service_account("not json")
    with pytest.raises(cx.ConnectorError, match="isn't a service account key"):
        cx.parse_service_account('{"installed": {"client_id": "x"}}')
    cleaned = cx.parse_service_account(_make_service_account_json())
    assert '"extra"' not in cleaned and mp.SA_EMAIL in cleaned


def test_google_via_service_account_and_page_keys(env, monkeypatch):
    cx, db, sync = env
    # No developer token and no OAuth client at all: the 2026 Cloud-project + service-account path.
    for k in ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
              "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "META_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert not cx.google_configured() and not cx.meta_configured()
    db.set_setting("google_service_account_json", cx.parse_service_account(_make_service_account_json()))
    db.set_setting("google_ads_login_customer_id", "1112223333")
    db.set_setting("meta_access_token", mp.META_TOKEN)          # saved from the page, not the env
    try:
        assert cx.google_configured() and cx.meta_configured()
        g = cx.GoogleClient()
        assert "developer-token" not in g._headers()
        assert [a["name"] for a in g.accounts()] == ["Havens Orthodontics", "Heights Orthodontics"]
        res = sync.refresh_account_lists()
        assert res["google"] == {"ok": True, "count": 2} and res["meta"] == {"ok": True, "count": 3}
    finally:
        for k in ("google_service_account_json", "google_ads_login_customer_id", "meta_access_token"):
            db.set_setting(k, None)
