"""Remove write-ups with (x); export the practice page as an Outlook-friendly email."""
import email
import email.policy
import importlib
from datetime import date

import pytest

from tests import mock_platforms as mp


@pytest.fixture
def hub(tmp_path, monkeypatch):
    srv = mp.start(9981)
    mp.reset()
    base = "http://127.0.0.1:9981"
    for k, v in {"ADS_DB": str(tmp_path / "ads.db"), "ADS_PASSWORD": "test-password-1",
                 "ADS_DISABLE_SCHEDULER": "1", "GOOGLE_ADS_BASE": base, "GOOGLE_OAUTH_TOKEN_URL": base + "/token",
                 "GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
                 "GOOGLE_ADS_DEVELOPER_TOKEN": mp.GOOGLE_DEV, "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "",
                 "META_ACCESS_TOKEN": mp.META_TOKEN, "META_GRAPH_BASE": base}.items():
        monkeypatch.setenv(k, v)
    from app import actions, connectors, db, main, sync
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
    db.save_ai(pid, "fixes", "## Summary\nBlock **invisalign jobs**.\n- Pause PMax\n- Raise budgets\n\n| Term | Cost |\n|---|---|\n| invisalign jobs | $60.45 |")
    db.save_ai(pid, "report", "## This month at a glance\nSpend was on track.")
    yield c, db, pid
    c.__exit__(None, None, None)
    srv.shutdown()
    srv.server_close()
    mp.reset()


def test_remove_writeups_with_x(hub):
    c, db, pid = hub
    db.save_ai(pid, "fixes", "older version")            # an older one must not reappear
    html = c.get(f"/p/{pid}").text
    assert f'action="/p/{pid}/ai/fixes/delete"' in html and 'aria-label="Remove this write-up"' in html
    assert f'action="/p/{pid}/ai/report/delete"' in html
    r = c.post(f"/p/{pid}/ai/fixes/delete")
    assert "Fix list removed" in r.text and db.latest_ai(pid, "fixes") is None
    c.post(f"/p/{pid}/ai/report/delete", data={"back": "report"})
    assert db.latest_ai(pid, "report") is None
    assert "Open last report" not in c.get(f"/p/{pid}").text


def test_email_preview_and_sections(hub):
    c, db, pid = hub
    assert f'href="/p/{pid}/email"' in c.get(f"/p/{pid}").text
    html = c.get(f"/p/{pid}/email").text
    assert "Copy for Outlook" in html and "Download Outlook draft" in html
    assert "Heights Orthodontics — ad results for October 2026" in html
    for bit in ("Budget pacing", "What needs attention", "Search terms", "Campaigns", "What to fix", "Monthly report",
                "invisalign jobs"):
        assert bit in html, bit
    only = c.get(f"/p/{pid}/email?inc=summary,report&picked=1").text
    body = only.split('id="email-content"')[1]
    assert "Monthly report" in body and "What needs attention" not in body and "What to fix" not in body


def test_eml_is_an_outlook_ready_draft(hub):
    c, db, pid = hub
    r = c.get(f"/p/{pid}/email.eml")
    assert r.headers["content-type"].startswith("message/rfc822")
    assert 'filename="Heights-Orthodontics' in r.headers["content-disposition"]
    msg = email.message_from_bytes(r.content, policy=email.policy.default)
    assert msg["X-Unsent"] == "1" and msg["Subject"] == "Heights Orthodontics — ad results for October 2026"
    html = msg.get_body(("html",)).get_content()
    text = msg.get_body(("plain",)).get_content()
    # Outlook (Word engine) safety: tables + inline styles only
    assert "<style" not in html and "class=" not in html and "<script" not in html
    for bad in ("display:flex", "display: flex", "grid", "position:"):
        assert bad not in html, bad
    assert 'bgcolor=' in html                                   # pacing bar drawn with table cells
    assert '<li style=' in html and '<td style=' in html        # write-up Markdown got inline styles
    assert "invisalign jobs" in text and "Budget pacing" in text


def test_email_includes_applied_changes(hub):
    c, db, pid = hub
    c.post(f"/p/{pid}/fixes/prepare")
    ch = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "pause"][0]
    c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(ch["id"])]})
    db.update_change(ch["id"], applied_at=date.today().isoformat() + "T09:00:00")
    html = c.get(f"/p/{pid}/email").text
    assert "Changes made" in html and ch["title"] in html
