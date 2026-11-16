"""Master Reports: periods, the daily ad archive, totals without double counting, highs/lows, suggestions,
the page and the front-page button."""
from datetime import date, timedelta

import pytest

from tests.test_scope import client  # noqa: F401  (pytest fixture)

TODAY = date(2026, 10, 27)


def test_periods():
    from app import report
    m = report.period("month", None, TODAY)
    assert m["key"] == "2026-09" and m["start"] == date(2026, 9, 1) and m["end"] == date(2026, 9, 30)
    assert m["prev_start"] == date(2026, 8, 1) and m["prev_end"] == date(2026, 8, 31) and m["next"] == "October"
    cur = report.period("month", "2026-10", TODAY)
    assert cur["partial"] and cur["end"] == TODAY and cur["prev_end"] == date(2026, 9, 27)   # same days last month
    w = report.period("week", None, TODAY)              # Tue Oct 27 -> last full week Mon Oct 19 - Sun Oct 25
    assert w["start"] == date(2026, 10, 19) and w["end"] == date(2026, 10, 25) and w["prev_start"] == date(2026, 10, 12)
    assert report.period("week", "2026-10-21", TODAY)["start"] == date(2026, 10, 19)   # any day -> its Monday
    assert report.money(1234.5) == "$1,234" and report.money(12.5) == "$12.50" and report.num(14800) == "14.8k"


def _seed(db):
    m = db.add_practice("MCDP Ads", 5000, 1000, "")
    h = db.add_practice("Heights Orthodontics", 1500, 0, "heights")
    w = db.add_practice("Waterford Orthodontics", 1500, 1000, "waterford")
    for pid in (m, h, w):
        db.set_practice_accounts(pid, "111", "999")
    rows_g, rows_m = [], []
    for month, scale in ((9, 1.0), (8, 1.5)):
        for day in range(1, 31 if month == 9 else 32):
            d = date(2026, month, day)
            rows_g += [{"campaign": "Heights - Search", "date": d, "spend": 50 * scale, "impressions": 900, "clicks": 30,
                        "conversions": 1, "account": "999"},
                       {"campaign": "Waterford - Search", "date": d, "spend": 20 * scale, "impressions": 400, "clicks": 9,
                        "conversions": 0, "account": "999"},
                       {"campaign": "Brand", "date": d, "spend": 5, "impressions": 100, "clicks": 2, "conversions": 0,
                        "account": "999"}]
            rows_m += [{"campaign": "Post: \"Smile month\"", "date": d, "spend": 3, "impressions": 500, "clicks": 4,
                        "conversions": 0, "account": "111"}]
    for pid in (m, h, w):     # shared accounts are stored under every practice that uses them
        db.archive_rows(pid, "google", rows_g, date(2026, 8, 1), date(2026, 9, 30))
        db.archive_rows(pid, "meta", rows_m, date(2026, 8, 1), date(2026, 9, 30))
    db.set_setting("campaign_owners", '{"meta": {"post: \\"smile month\\"": {"name": "Post", "practices": [%d], "boost": true}}}' % w)
    db.set_practice_social(w, ["PG_W"], [])
    with db.conn() as c:
        for i in range(5):
            c.execute("INSERT INTO posts(post_id, network, account_id, created, views, likes, comments, shares, interactions, "
                      "synced_at) VALUES (?, 'facebook', 'PG_W', ?, 400, 20, 2, 1, 23, '2026-10-01')", (f"p{i}", f"2026-09-{i + 3:02d}T10:00:00"))
        c.execute("INSERT INTO gbp_locations(name, title, address, practice_id, rating, total, full_at) VALUES "
                  "('accounts/1/locations/9', 'Heights Orthodontics', '43767 Van Dyke Ave, Sterling Heights, MI', ?, 4.8, 200, '2026-10-01')", (h,))
        for i in range(2):
            c.execute("INSERT INTO gbp_reviews(id, location, stars, created) VALUES (?, 'accounts/1/locations/9', 5, ?)",
                      (f"r{i}", f"2026-09-{10 + i}T10:00:00"))
    return m, h, w


def test_report_numbers_highs_lows_and_suggestions(client):
    c, db, sync, mp = client
    from app import report
    m, h, w = _seed(db)
    rep = report.build("month", "2026-09", TODAY)
    t = rep["totals"]
    # every campaign-day counted once even though three practices store the shared accounts
    assert t["spend"] == round(30 * (50 + 20 + 5) + 30 * 3, 2)
    assert t["prev_spend"] == round(31 * (75 + 30 + 5) + 31 * 3, 2)
    assert t["unassigned"] == 30 * 5                          # "Brand" belongs to no practice
    assert [r["p"]["name"] for r in rep["rows"]] == ["Heights Orthodontics", "Waterford Orthodontics"]   # master left out
    heights, water = rep["rows"]
    assert heights["paid"]["spend"] == 1500 and heights["paid"]["conversions"] == 30
    assert water["paid"]["spend"] == 600 + 90                # its search campaign + the boost of its own post
    assert heights["reviews"]["new"] == 2 and heights["locations"][0]["name"].startswith("43767 Van Dyke")
    assert water["organic"]["posts"] == 5
    assert rep["cats"]["paid"]["highs"][0]["name"] == "Heights Orthodontics"
    assert any(l["name"] == "Waterford Orthodontics" and "no conversions" in l["text"] for l in rep["cats"]["paid"]["lows"])
    joined = " ".join(rep["suggestions"])
    assert "Waterford Orthodontics spent $690.00 on ads with no conversions" in joined
    assert "isn't assigned to any practice" in joined
    assert "spent $2,340 on ads" in rep["summary"][0] and "down" in rep["summary"][0]
    tiles = {k["label"]: k for k in rep["kpis"]}
    assert tiles["Conversions"]["change"] < 0 and tiles["Conversions"]["good"] is False

    page = c.get("/reports?kind=month&key=2026-09").text
    assert "Monthly Master Report" in page and "September 2026" in page and "Suggestions for October" in page
    assert "Master overview" in page and "Highs" in page and "Lows" in page and "<svg" in page
    assert "43767 Van Dyke Ave" in page and "Save as PDF" in page and "mcdp-mark.png" in page or "rp-cover-mark" in page
    wk = c.get("/reports?kind=week&key=2026-09-14").text
    assert "Weekly Snapshot" in wk and "Week of Sep 14–20, 2026" in wk and "Suggestions for next week" in wk


def test_front_page_button_and_empty_report(client):
    c, db, sync, mp = client
    home = c.get("/").text
    assert "Master Reports" in home and 'href="/reports?kind=month"' in home and 'href="/reports?kind=week"' in home
    r = c.get("/reports?kind=week")
    assert r.status_code == 200 and "Weekly Snapshot" in r.text
    assert c.get("/reports?kind=month&key=nonsense").status_code == 200


def test_archive_keeps_history_and_replaces_days(client):
    c, db, sync, mp = client
    pid = db.add_practice("Solo", 0, 0, "")
    d1, d2 = date(2026, 9, 1), date(2026, 9, 2)
    db.archive_rows(pid, "google", [{"campaign": "A", "date": d1, "spend": 10, "account": "1"},
                                    {"campaign": "B", "date": d2, "spend": 5, "account": "1"}], d1, d2)
    db.archive_rows(pid, "google", [{"campaign": "A", "date": d2, "spend": 7, "account": "1"}], d2, d2)   # B gone on day 2
    got = sorted((r["campaign"], r["date"], r["spend"]) for r in db.archived(pid, "google", d1, d2))
    assert got == [("A", d1, 10), ("A", d2, 7)]
    assert "2026-09" in db.archive_months()


def test_report_uses_mcdp_brand_kit(client):
    c, db, sync, mp = client
    from app import field_report, report
    m, h, w = _seed(db)
    look = report.brand()
    assert look["name"] == "Motor City Dental Partners" and look["master_id"] == m and not look["kit_set"]
    page = c.get("/reports?kind=month&key=2026-09").text
    assert "Motor City Dental Partners" in page and "/static/mcdp-mark.png" in page
    assert "Add MCDP&#39;s logo and colours" in page or "Add MCDP's logo and colours" in page
    field_report.set_colors(m, "#1C2B4A", "#C9A227")
    look = report.brand()
    assert look["kit_set"] and look["pal"]["primary"] == "#1C2B4A" and look["pal"]["meta"] == "#C9A227"
    page = c.get("/reports?kind=month&key=2026-09").text
    assert "--rp-p: #1C2B4A" in page and 'fill="#1C2B4A"' in page and "logo and colours" not in page
    pal = report.palette("#F4E04D", "#F4E04D")              # a pale main colour still gives readable text
    assert report._lum(pal["dark"]) <= 0.32 and pal["accent"] != pal["primary"] and pal["on_primary"] == "#1A1A1A"


def test_hidden_practice_leaves_every_page_and_report(client):
    c, db, sync, mp = client
    from app import report, scope, cal
    m, h, w = _seed(db)
    assert "Waterford Orthodontics" in c.get("/practices").text
    r = c.post(f"/p/{w}/hide", follow_redirects=True)
    assert "Hid Waterford Orthodontics" in r.text
    assert c.post(f"/p/{m}/hide", follow_redirects=True).text.count("master record can&#39;t be hidden") == 1
    assert w in db.hidden_ids() and w not in {p["id"] for p in db.practices()}
    assert w in {p["id"] for p in db.practices(include_hidden=True)}
    page = c.get("/practices").text
    assert f'href="/p/{w}"' in page and "Hidden practices (1)" in page and "Show again" in page   # only in the drawer
    assert page.count(f'href="/p/{w}"') == 1
    rep = report.build("month", "2026-09", TODAY)
    assert [x["p"]["name"] for x in rep["rows"]] == ["Heights Orthodontics"]
    t = rep["totals"]
    assert t["spend"] == round(30 * (50 + 5), 2)              # Waterford's search campaign and its boost are gone
    assert t["unassigned"] == 30 * 5 and t["organic"]["posts"] == 0
    rp = c.get("/reports?kind=month&key=2026-09").text
    assert "Waterford" not in rp
    # the master record's own page leaves Waterford's campaigns out too
    rows = scope.apply(db.archived(m, "google", date(2026, 9, 1), date(2026, 9, 30)), db.practice(m), platform="google")
    assert {x["campaign"] for x in rows} == {"Heights - Search", "Brand"}
    assert "Waterford - Search" not in c.get(f"/p/{m}").text
    # its page still opens, says it's hidden, and Show again brings everything back
    pp = c.get(f"/p/{w}").text
    assert "This practice is hidden" in pp and "Show it again" in pp
    c.post(f"/p/{w}/show")
    assert not db.hidden_ids()
    assert report.build("month", "2026-09", TODAY)["totals"]["spend"] == round(30 * (50 + 20 + 5) + 30 * 3, 2)


def test_hidden_practice_inbox_and_calendar(client):
    c, db, sync, mp = client
    from app import reviews, cal
    m, h, w = _seed(db)
    db.set_hidden(w, True, "test")
    owners = reviews.account_practice(db.practices(include_hidden=True))
    rows = [{"source": "facebook", "account_id": "PG_W"}, {"source": "google", "account_id": "accounts/1/locations/9"}]
    assert reviews.drop_hidden(rows, owners) == rows[1:]
    with db.conn() as cn:
        cn.execute("INSERT INTO cal_events(practice_id, category, kind, subject, starts, ends, created_at, updated_at) "
                   "VALUES (?, 'meeting', 'review', 'Waterford review', '2026-09-10T10:00', '2026-09-10T11:00', 'x', 'x')", (w,))
    got = cal.items_between(date(2026, 9, 1), date(2026, 9, 30), {"id": 1, "role": "admin"})
    assert not any(i["title"] == "Waterford review" for i in got)
    db.set_hidden(w, False)
    got = cal.items_between(date(2026, 9, 1), date(2026, 9, 30), {"id": 1, "role": "admin"})
    assert any(i["title"] == "Waterford review" for i in got)
