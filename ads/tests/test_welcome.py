"""Welcome page at /, practices list at /practices, and setup sections folded into drop-downs."""
import importlib
import os
import re

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:   # other test files set these
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
    from app import connectors, db, places, referrals
    for m in (db, connectors, places, referrals):
        importlib.reload(m)
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c, db, places


def test_needs_login(client):
    c, db, places = client
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_welcome_empty(client):
    c, db, places = client
    c.post("/login", data={"password": "pw"})
    html = c.get("/").text
    assert "From ad to start" in html and "The patient journey" in html and "Postiz" not in html
    assert "Upload LeadSigma&#39;s export" in html or "Upload LeadSigma's export" in html
    assert html.count(">Connect<") == 2                                # exams and starts wait for Power BI
    assert "Not filled in yet" in html and "Fill in from Power BI" in html
    for href in ('href="/practices"', 'href="https://social.mcdp.com"', 'href="/map"', 'href="/map/list"',
                 'href="/reports?kind=week"', 'href="/reports?kind=month"'):
        assert href in html
    assert "Nothing stands out" in html


def test_welcome_with_data(client):
    c, db, places = client
    from datetime import date, timedelta
    from app import report
    pid = db.add_practice("Heights", 1000, 500, "heights")
    wid = db.add_practice("Waterford", 1000, 0, "waterford")
    master = db.add_practice("MCDP Ads", 0, 0, "")
    for q in (master, pid, wid):
        db.set_practice_accounts(q, "", "999")
    per = report.period("week")
    rows = []
    d = per["start"]
    while d <= per["end"]:
        rows += [{"campaign": "Heights - Search", "date": d, "spend": 60, "impressions": 900, "clicks": 30, "conversions": 1,
                  "account": "999"},
                 {"campaign": "Waterford - Search", "date": d, "spend": 20, "impressions": 400, "clicks": 9, "conversions": 0,
                  "account": "999"}]
        d += timedelta(days=1)
    for q in (master, pid, wid):
        db.archive_rows(q, "google", rows, per["start"], per["end"])
    places.add_office("Heights – Defiance", "ChIJ_office_defiance01", 41.28, -84.35)
    oid = places.offices()[0]["id"]
    with db.conn() as conn:
        for i in range(3):
            conn.execute("INSERT INTO map_places(place_id, layer, office_id, miles, lat, lng, coords_at, ranked) "
                         "VALUES (?, 'referrer', ?, 1.0, 41.29, -84.36, ?, 1)", (f"ChIJ_ref_{i:08d}", oid, db.now()))
    html = c.get("/").text
    n = per["days"]
    assert report.money(80 * n) in html and f'href="/p/{pid}">Heights</a>' in html
    assert "3 practices mapped" in html
    assert "with no ad conversions" in html and "Waterford" in html        # zero-conversion spend is flagged
    # LeadSigma: a bookings export (one row per booking, with personal columns that must not be kept)
    day = per["start"].strftime("%m/%d/%Y")
    csv_text = ("Patient Name,Email,Phone,Account,Office,Channel,UTM Source,UTM Medium,UTM Campaign,Booking Status,"
                "How did you hear about us?,Referring Dentist,Created At\n"
                + "".join(f"Pat {i},p{i}@x.com,555-000{i},Heights Orthodontics,Heights,Direct / Unattributed,,,,Confirmed,"
                          f"Friend/Family Referral,Heights Website,{day} 9:30 AM\n" for i in range(3))
                + f"Pat 9,p9@x.com,555-0009,Heights Orthodontics,Heights,Other,adwords,{{adname}},{{campaignname}},Confirmed,Google Search,,{day}\n")
    r = c.post("/dashboard/leadsigma", files={"file": ("export.csv", csv_text.encode(), "text/csv")}, follow_redirects=True)
    assert "Imported 4 bookings from LeadSigma" in r.text
    with db.conn() as conn:
        stored = " ".join(" ".join(str(x) for x in tuple(r)) for r in conn.execute("SELECT * FROM ls_counts"))
    assert "Pat 0" not in stored and "@x.com" not in stored and "555" not in stored        # counts only
    html = c.get("/").text
    assert ">4<" in html and "Friend/family referral" in html
    assert "75% of online bookings have no source (3 of 4)" in html and "{campaignname}" in html
    # budget, typed in from Power BI
    c.post("/dashboard/budget", data={"promo_budget": "410,000", "promo_ytd": "294468", "adv_budget": "$462,600",
                                      "adv_ytd": "275956", "through": "2026-10"})
    html = c.get("/").text
    assert "$294,468" in html and "72% used" in html and "Through 2026-10" in html


def test_practices_moved_and_drawers(client):
    c, db, places = client
    html = c.get("/practices").text
    assert "<h1>Practices</h1>" in html and 'aria-current="page">Practices' in html
    assert '<details class="drawer add" id="add-practice" >' in html  # tucked away once practices exist
    pid = db.practices()[0]["id"]
    page = c.get(f"/p/{pid}").text
    assert '<p class="crumb"><a href="/practices">Practices</a></p>' in page
    for drawer in ('id="upload"', 'id="settings"', 'id="campaigns"', 'id="uploads"'):
        assert f'<details class="drawer" {drawer}' in page
    assert re.search(r'<details class="drawer" id="settings">', page)              # closed by default
    assert "Delete this practice" in page.split('id="settings"', 1)[1].split("</details>", 1)[0]
    m = c.get("/map").text
    assert re.search(r'<details class="drawer" id="offices" >', m)                 # offices exist: closed
    assert re.search(r'<details class="drawer" id="setup" open>', m)               # no keys yet: open
    conn_page = c.get("/connections").text
    assert 'id="meta-token" open' in conn_page and "Postiz" not in conn_page
    r = c.post("/practices", data={"name": ""}, follow_redirects=False)
    assert r.headers["location"] == "/practices"
    r = c.post(f"/p/{pid}/delete", follow_redirects=False)
    assert r.headers["location"] == "/practices"
