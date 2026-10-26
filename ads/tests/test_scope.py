"""Shared ad accounts: master record, campaign filters, one-click split, single pull per account."""
import importlib
import os
from datetime import date

import pytest

from app import scope


def _row(campaign, spend, conv=0.0, day=1):
    d = date(2026, 10, day)
    return {"campaign": campaign, "name": "", "date": d, "date_start": d, "date_end": d, "spend": spend,
            "impressions": 1000, "clicks": 50, "conversions": conv, "conv_value": 0.0, "status": "",
            "budget": 0.0, "match_type": ""}


ROWS = [_row("Heights Orthodontics", 1001.50, 7), _row("Heights - Invisalign", 200, 2),
        _row("Bandeen Orthodontics", 610.83, 2), _row("Bandeen Orthodontics Battle", 50),
        _row("Havens Orthodontics", 400, 20), _row("Brand - MCDP", 100, 1)]


def test_filter_matching():
    assert scope.matches("Heights - Invisalign", "heights")
    assert scope.matches("Bandeen Orthodontics", "Heights, bandeen")
    assert not scope.matches("Bandeen Orthodontics Battle", "bandeen, -battle")
    assert scope.matches("anything", "")                       # no filter = everything (master)
    assert not scope.matches("Bandeen", "-bandeen")             # exclusions only
    assert scope.group_key("Heights - Invisalign") == "Heights"
    assert scope.group_key("Heights | Search") == "Heights"
    assert scope.group_key("Bandeen Orthodontics") == "Bandeen Orthodontics"


def test_breakdown_for_master():
    master = {"id": 1, "campaign_filter": "", "google_customer_id": "351", "meta_account_id": ""}
    practices = [master,
                 {"id": 2, "name": "Heights", "campaign_filter": "heights", "google_customer_id": "351"},
                 {"id": 3, "name": "Bandeen", "campaign_filter": "bandeen, -battle", "google_customer_id": "351"},
                 {"id": 4, "name": "Elsewhere", "campaign_filter": "havens", "google_customer_id": "999"}]
    b = scope.breakdown(master, "google", ROWS, practices)
    by = {x["practice"]["name"]: x for x in b["by_practice"]}
    assert set(by) == {"Heights", "Bandeen"}                    # other account's practice left out
    assert by["Heights"]["spend"] == 1201.5 and by["Heights"]["count"] == 2
    assert by["Bandeen"]["spend"] == 610.83
    assert b["unassigned"]["count"] == 3 and b["overlap"] == []
    names = [s["name"] for s in b["suggestions"]]
    assert names == ["Havens Orthodontics", "Brand", "Bandeen Orthodontics Battle"]   # by spend
    assert b["total"]["spend"] == round(sum(r["spend"] for r in ROWS), 2)


def test_overlap_is_flagged():
    master = {"id": 1, "campaign_filter": ""}
    practices = [master, {"id": 2, "name": "A", "campaign_filter": "orthodontics"},
                 {"id": 3, "name": "B", "campaign_filter": "heights"}]
    b = scope.breakdown(master, "google", ROWS, practices)
    assert b["overlap"] == ["Heights Orthodontics"]


# ---------------- end to end through the web pages ----------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    from tests import mock_platforms as mp
    srv = mp.start(9966)
    base = "http://127.0.0.1:9966"
    for k, v in {"ADS_DB": str(tmp_path / "ads.db"), "ADS_PASSWORD": "test-password-1",
                 "ADS_DISABLE_SCHEDULER": "1", "GOOGLE_ADS_BASE": base, "GOOGLE_OAUTH_TOKEN_URL": base + "/token",
                 "GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
                 "GOOGLE_ADS_DEVELOPER_TOKEN": mp.GOOGLE_DEV, "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "",
                 "META_ACCESS_TOKEN": "", "META_GRAPH_BASE": base}.items():
        monkeypatch.setenv(k, v)
    from app import connectors, db, main, sync
    for m in (connectors, db, sync, main):
        importlib.reload(m)
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    c.__enter__()
    c.post("/login", data={"password": "test-password-1"})
    db.set_setting("google_refresh_token", mp.REFRESH)
    yield c, db, sync, mp
    c.__exit__(None, None, None)
    srv.shutdown()
    srv.server_close()


def test_master_split_and_filtered_practice(client):
    c, db, sync, mp = client
    master = db.add_practice("MCDP Ads", 3500, 0)
    db.set_practice_accounts(master, "", "4445556666")
    sync.sync_all(today=date(2026, 10, 12))

    # Before any split: master shows every campaign and suggests practices from campaign names
    html = c.get(f"/p/{master}").text
    assert "Create practice" in html and "Invisalign - Search" in html and "PMax" in html

    # One click: a practice for the "Invisalign" campaigns, with data straight away
    r = c.post(f"/p/{master}/split", data={"name": "Invisalign - Search", "campaign_filter": "invisalign"},
               follow_redirects=False)
    new_id = int(r.headers["location"].rsplit("/", 1)[1])
    p = db.practice(new_id)
    assert p["campaign_filter"] == "invisalign" and p["google_customer_id"] == "4445556666"
    html = c.get(f"/p/{new_id}").text
    assert "1 of 2 campaigns" in html and "<td>PMax - Smile Promo</td>" not in html
    assert "$579.00" in html                                   # 12 days x $48.25 only
    assert "invisalign jobs" in html                            # its search terms carried over

    # Master is labelled, keeps everything, and breaks spend down by practice
    html = c.get(f"/p/{master}").text
    assert "Master record" in html and "By practice" in html and "$819.00" in html
    dash = c.get("/practices").text
    assert dash.index("MCDP Ads") < dash.index("Invisalign - Search")   # master listed first
    assert "Master</span>" in dash

    # A filter that matches nothing explains itself instead of showing an empty page
    c.post(f"/p/{new_id}/settings", data={"name": "Invisalign - Search", "google_budget": 1000,
                                          "meta_budget": 0, "notes": "", "campaign_filter": "nomatch"})
    assert "None of the 2 campaigns" in c.get(f"/p/{new_id}").text


def test_shared_account_pulled_once_per_sync(client):
    c, db, sync, mp = client
    for name, f in (("Master", ""), ("Heights", "invisalign"), ("Havens", "pmax")):
        pid = db.add_practice(name, 0, 0, f)
        db.set_practice_accounts(pid, "", "4445556666")
    mp.LOG.clear()
    status = sync.sync_all(today=date(2026, 10, 12))
    assert all(r["google"]["ok"] for r in status["results"].values())
    pulls = [l for l in mp.LOG if l[0] == "POST" and l[1].endswith("searchStream") and "FROM campaign" in l[3]
             and "4445556666" in l[1]]
    assert len(pulls) == 1


def test_suggestions_group_related_campaigns():
    mk = lambda n, s: {"name": n, "spend": s, "conversions": 1}
    sg = scope.suggest_practices([mk("Bandeen Orthodontics", 610), mk("Bandeen Orthodontics Battle Creek", 194),
                                  mk("Havens Orthodontics", 420), mk("Havens - Braces Promo", 180),
                                  mk("Brand - MCDP", 154)])
    assert [(s["name"], s["filter"], s["count"]) for s in sg] == [
        ("Bandeen Orthodontics", "Bandeen Orthodontics", 2), ("Havens Orthodontics", "Havens", 2), ("Brand", "Brand", 1)]
    for s in sg:   # a suggested filter always picks up its own campaigns
        assert all(scope.matches(c["name"], s["filter"]) for c in s["campaigns"])


# ---------------- campaigns added to a practice by exact name ----------------
PROMO = "Price is right at Heights campaign 10-1-2026 Campaign"


def test_added_campaigns_belong_only_to_their_practice():
    heights = {"id": 2, "name": "Heights", "campaign_filter": "Heights Orthodontics",
               "campaign_list": '["Price is right at Heights campaign 10-1-2026 Campaign", "Bandeen promo for Heights"]'}
    bandeen = {"id": 3, "name": "Bandeen", "campaign_filter": "bandeen", "campaign_list": "[]"}
    only_list = {"id": 4, "name": "Promo only", "campaign_filter": "", "campaign_list": '["Spring Promo"]'}
    claims = scope.claims_from([heights, bandeen, only_list])
    assert scope.matches(PROMO, heights, claims)
    assert scope.matches("  price IS right at heights campaign 10-1-2026 campaign ", heights, claims)   # spacing/case
    assert scope.matches("Heights Orthodontics", heights, claims)                 # words still work
    assert scope.matches("Bandeen promo for Heights", heights, claims)
    assert not scope.matches("Bandeen promo for Heights", bandeen, claims)       # added by hand to Heights wins
    assert scope.matches("Bandeen Orthodontics", bandeen, claims)
    assert scope.matches("Spring Promo", only_list, claims)
    assert not scope.matches("Anything else", only_list, claims)                 # list-only practice
    assert scope.has_filter(only_list)


def test_add_campaigns_from_master_and_practice_page(client):
    c, db, sync, mp = client
    from datetime import date as _d
    rows = []
    for name, spend, conv in (("Heights Orthodontics", 900, 9), (PROMO, 250, 5), ("Bandeen Orthodontics", 600, 2),
                              ("Spring Promo", 80, 0)):
        rows.append({"campaign": name, "name": "", "date": _d(2026, 10, 5), "date_start": _d(2026, 10, 5),
                     "date_end": _d(2026, 10, 5), "spend": spend, "impressions": 1000, "clicks": 40,
                     "conversions": conv, "conv_value": 0.0, "status": "", "budget": 0.0, "match_type": ""})
    parsed = {"platform": "google", "level": "campaign", "rows": rows, "date_start": _d(2026, 10, 5),
              "date_end": _d(2026, 10, 5), "warnings": []}
    master = db.add_practice("MCDP Ads", 3500, 0)
    db.save_upload(master, parsed, "export.csv")
    heights = db.add_practice("Heights Orthodontics", 1500, 0, "Heights Orthodontics")
    bandeen = db.add_practice("Bandeen Orthodontics", 800, 0, "Bandeen")
    db.copy_latest_uploads(master, bandeen)

    html = c.get(f"/p/{master}").text
    assert "Or add campaigns to an existing practice" in html and PROMO in html

    # From the master: tick the promo and add it to Heights (which had no data yet)
    r = c.post(f"/p/{master}/assign", data={"campaign": [PROMO], "target": str(heights)})
    assert "Added 1 campaign to Heights Orthodontics" in r.text
    assert scope.added_campaigns(db.practice(heights)) == [PROMO]
    page_h = c.get(f"/p/{heights}").text
    assert "$1,150.00" in page_h and "2 of 4 campaigns" in page_h       # data copied over; 900 + 250
    assert "Campaigns added by name" in page_h and f'aria-label="Remove {PROMO}"' in page_h

    # Master breakdown now credits it to Heights; only Spring Promo is left unassigned
    html = c.get(f"/p/{master}").text
    assert "1 campaign not assigned to a practice yet ($80.00)" in html

    # From the practice page: add another, then remove it with the ×
    c.post(f"/p/{bandeen}/campaigns/add", data={"name": "Spring Promo"})
    assert "Spring Promo" in scope.added_campaigns(db.practice(bandeen))
    c.post(f"/p/{bandeen}/campaigns/remove", data={"name": "Spring Promo"})
    assert scope.added_campaigns(db.practice(bandeen)) == []
    assert "Spring Promo" in c.get(f"/p/{bandeen}").text            # offered again in the Add list
