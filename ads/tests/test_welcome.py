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
    assert 'id="greeting">Welcome.</h1>' in html                     # fallback before the browser greets by time
    assert "Good ${word}." in html                                    # ...which the page's script then does
    assert "Everything for your practices" in html and "Postiz" not in html
    assert "Plan and schedule Facebook and Instagram posts." in html
    assert "Practices mapped" in html and "Competitors" not in html and "Dentists mapped" not in html
    for href in ('href="/practices"', 'href="https://social.mcdp.com"', 'href="/map"', 'href="/map/list"'):
        assert href in html
    assert "Nothing needs you right now." in html


def test_welcome_with_data(client):
    c, db, places = client
    pid = db.add_practice("Heights", 1000, 500)
    db.save_upload(pid, {"platform": "google", "level": "campaign", "date_start": None, "date_end": None,
                         "rows": [{"date": None, "date_start": None, "date_end": None, "campaign": "Heights - Search",
                                   "name": "Heights - Search", "spend": 1234.5, "impressions": 1000, "clicks": 90,
                                   "conversions": 17, "conv_value": 0, "status": "", "budget": 0, "match_type": ""}],
                         "warnings": []}, "g.csv")
    with db.conn() as conn:
        conn.execute("INSERT INTO changes(practice_id, platform, account, kind, title, payload, created_at) "
                     "VALUES (?, 'google', '1', 'negative', 'Block a search', '{}', ?)", (pid, db.now()))
    places.add_office("Heights – Defiance", "ChIJ_office_defiance01", 41.28, -84.35)
    oid = places.offices()[0]["id"]
    with db.conn() as conn:
        for i in range(3):
            conn.execute("INSERT INTO map_places(place_id, layer, office_id, miles, lat, lng, coords_at, ranked) "
                         "VALUES (?, 'referrer', ?, 1.0, 41.29, -84.36, ?, 1)", (f"ChIJ_ref_{i:08d}", oid, db.now()))
        conn.execute("INSERT INTO map_places(place_id, layer, office_id, miles, lat, lng, coords_at, ranked) "
                     "VALUES ('ChIJ_comp_00000001', 'competitor', ?, 1.0, 41.29, -84.36, ?, 1)", (oid, db.now()))
    html = c.get("/").text
    assert "$1,234.50" in html and ">17<" in html
    dd = re.search(r"<dt>Practices mapped</dt><dd>([^<]+)</dd>", html).group(1)
    assert dd == "3"                                                  # dental practices only, not competitors
    assert f'href="/p/{pid}#fixes">1 fix waiting for your approval (Heights)' in html
    assert 'href="/map/list">Build the referral and competition lists once' in html


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
