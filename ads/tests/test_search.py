"""Search & Maps: Search Console + Business Profile Performance pulled by month, matched to practices, shown and exported."""
import importlib
import io
import os
import re
from datetime import date, timedelta

import pytest

TODAY = date(2026, 11, 3)


class FakeGoogle:
    """Stands in for cx.GBPClient: two Search Console properties for Heights, one for Havens, one stray site."""
    calls = []

    def __init__(self, token=None):
        pass

    def sc_sites(self):
        return [{"site": "sc-domain:heightsortho.com", "permission": "siteOwner"},
                {"site": "https://www.heightsortho.com/", "permission": "siteFullUser"},
                {"site": "sc-domain:havensorthodontics.com", "permission": "siteOwner"},
                {"site": "https://www.randomblog.net/", "permission": "siteOwner"},
                {"site": "https://unverified.com/", "permission": "siteUnverifiedUser"}][:4]

    def sc_query(self, site, start, end, dimensions=None, row_limit=1000):
        FakeGoogle.calls.append(("sc", site, start, end, tuple(dimensions or ())))
        boost = 2 if "heights" in site else 1
        if dimensions == ["date"]:
            out, d = [], start
            while d <= end:
                better = 1 if d.month == 10 else 0          # October ranked better than September
                out.append({"keys": [d.isoformat()], "clicks": 3 * boost, "impressions": 100 * boost, "position": 6.0 - better * 2})
                d += timedelta(days=1)
            return out
        if start.month == 10:
            return [{"keys": ["orthodontist near me"], "clicks": 40, "impressions": 900, "position": 3.2},
                    {"keys": ["braces cost"], "clicks": 5, "impressions": 300, "position": 8.4},
                    {"keys": ["invisalign sterling heights"], "clicks": 9, "impressions": 120, "position": 4.0}]
        return [{"keys": ["orthodontist near me"], "clicks": 30, "impressions": 800, "position": 5.0},
                {"keys": ["braces cost"], "clicks": 6, "impressions": 280, "position": 6.1}]

    def perf_daily(self, location, start, end, metrics=None):
        out = {}
        for m in ("BUSINESS_IMPRESSIONS_MOBILE_SEARCH", "BUSINESS_IMPRESSIONS_MOBILE_MAPS", "CALL_CLICKS",
                  "BUSINESS_DIRECTION_REQUESTS", "WEBSITE_CLICKS"):
            vals, d = {}, start
            while d <= min(end, TODAY - timedelta(days=2)):
                vals[d] = {"BUSINESS_IMPRESSIONS_MOBILE_SEARCH": 20, "BUSINESS_IMPRESSIONS_MOBILE_MAPS": 10,
                           "CALL_CLICKS": 2 if d.month == 10 else 1, "BUSINESS_DIRECTION_REQUESTS": 1, "WEBSITE_CLICKS": 1}[m]
                d += timedelta(days=1)
            out[m] = vals
        return out

    def perf_keywords(self, location, year, month):
        from app import connectors as cx
        if (year, month) == (TODAY.year, TODAY.month):
            raise cx.ConnectorError("Month not available yet")
        return [("orthodontist", 310 if month == 10 else 250, None), ("braces near me", 120, None), ("dr smith ortho", None, 15)]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_", "GBP_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
    from app import connectors, db, reviews, users, visibility
    for m in (db, users, connectors, reviews, visibility):
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
        viewer = person(admin, "Vi Viewer", "vi@example.com", "viewer")
        heights = db.add_practice("Heights Orthodontics", 0, 0)
        havens = db.add_practice("Havens Orthodontics", 0, 0)
        db.set_setting("gbp_client_id", "x.apps.googleusercontent.com")
        db.set_setting("gbp_client_secret", "s")
        db.set_setting("gbp_refresh_token", "rt")
        with db.conn() as c:
            for name, title, pid in (("accounts/1/locations/11", "Heights – Sterling", heights),
                                     ("accounts/1/locations/12", "Heights – Utica", heights),
                                     ("accounts/1/locations/21", "Havens", havens)):
                c.execute("INSERT INTO gbp_locations(name, title, address, practice_id, rating, total, full_at) "
                          "VALUES (?, ?, 'MI', ?, 4.8, 100, '')", (name, title, pid))
        yield {"main": main, "db": db, "vis": visibility, "cx": connectors, "admin": admin, "viewer": viewer,
               "heights": heights, "havens": havens}


def test_refresh_backfills_then_keeps_two_months(env, monkeypatch):
    vis, cx, db = env["vis"], env["cx"], env["db"]
    monkeypatch.setattr(cx, "GBPClient", FakeGoogle)
    FakeGoogle.calls = []
    st = vis.refresh(today=TODAY)
    assert not st["errors"] and st["sites"] == 2 and st["locations"] == 3
    sites = {s["site"]: s["practice_id"] for s in vis.sites()}
    assert sites["sc-domain:heightsortho.com"] == env["heights"] and sites["https://www.heightsortho.com/"] is None
    assert sites["sc-domain:havensorthodontics.com"] == env["havens"] and sites["https://www.randomblog.net/"] is None
    monthly = [c for c in FakeGoogle.calls if c[4] == ("query",) and c[1] == "sc-domain:heightsortho.com"]
    assert len(monthly) == 13 and monthly[0][2] == date(2025, 11, 1) and monthly[-1][3] == TODAY   # 12 months + this one
    FakeGoogle.calls = []
    vis.refresh(today=TODAY)
    assert len([c for c in FakeGoogle.calls if c[4] == ("query",) and c[1] == "sc-domain:heightsortho.com"]) == 2


def test_monthly_numbers_and_changes(env):
    vis, h = env["vis"], env["heights"]
    w = vis.website(h, ["2026-10", "2026-09"])
    assert w["2026-10"]["clicks"] == 31 * 6 and w["2026-10"]["impressions"] == 31 * 200 and w["2026-10"]["position"] == 4.0
    assert w["2026-09"]["position"] == 6.0
    d = vis.detail(h, "2026-10", today=TODAY)
    assert d["pos_ch"]["good"] is True and d["pos_ch"]["diff"] == -2.0              # moved up two places
    assert d["prof"]["views"] == 2 * 31 * 30 and d["prof"]["calls"] == 2 * 31 * 2 and d["calls_ch"]["good"]   # both locations
    q = {x["query"]: x for x in d["queries"]}
    assert q["orthodontist near me"]["moved"] == 1.8 and q["braces cost"]["moved"] == -2.3 and q["invisalign sterling heights"]["new"]
    assert [x["query"] for x in d["up"]] == ["orthodontist near me"] and [x["query"] for x in d["down"]] == ["braces cost"]
    kw = {k["keyword"]: k for k in d["keywords"]}
    assert kw["orthodontist"]["views"] == 2 * 310 and kw["dr smith ortho"]["views"] is None and kw["dr smith ortho"]["under"] == 15
    assert len(d["locations"]) == 2 and len(d["trend"]) == 12 and d["trend"][-1]["month"] == "2026-10"
    cur = vis.detail(h, "2026-11", today=TODAY)
    assert cur["is_current"] and cur["keywords"] == [] and cur["prof"]["days"] == 1      # Google runs behind


def test_search_page_overview_practice_and_export(env, monkeypatch):
    a, v, h = env["admin"], env["viewer"], env["heights"]
    page = v.get("/search").text
    assert "Search &amp; Maps" in page and 'href="/search"' in page and "(this month)" in page
    page = v.get("/search?m=2026-10").text
    assert "By practice · Oct 2026" in page and "Heights Orthodontics" in page and "orthodontist near me" in page
    assert "▲ 2.0 higher" in page and "Sign in with Google" not in page                 # viewers can't reconnect
    one = v.get(f"/search?practice={h}&m=2026-10&view=down").text
    assert "Searches the website shows up for" in one and "braces cost" in one and "Slipping" in one
    assert "Heights – Utica" in one and "under 15" in one
    cur = v.get(f"/search?practice={h}").text
    assert "Google publishes these after a month ends" in cur
    assert "Sign in with Google" in a.get("/search").text
    # viewers can't remap; editors can
    sid = "https://www.heightsortho.com/"
    v.post("/search/sites", data={f"site:{sid}": str(h)})
    assert {s["site"]: s["practice_id"] for s in env["vis"].sites()}[sid] is None
    a.post("/search/sites", data={f"site:{sid}": str(h)})
    assert {s["site"]: s["practice_id"] for s in env["vis"].sites()}[sid] == h
    a.post("/search/sites", data={f"site:{sid}": ""})
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(v.get("/search.xlsx?m=2026-10").content))
    assert wb.sheetnames == ["By practice", "Top searches", "Profile searches"]
    assert "Oct 2026" in wb["By practice"]["A1"].value and wb["By practice"]["A4"].value in ("Heights Orthodontics", "Havens Orthodontics")


def test_scope_and_api_hints(env):
    cx = env["cx"]
    assert "webmasters.readonly" in cx.GBP_SCOPE and "business.manage" in cx.GBP_SCOPE
    assert "Sign in with Google" in cx.GBPClient._hint("Request had insufficient authentication scopes.")
    assert "Google Search Console API" in cx.GBPClient._hint("Google Search Console API has not been used in project 1 (searchconsole.googleapis.com)")
    assert "Business Profile Performance API" in cx.GBPClient._hint("businessprofileperformance.googleapis.com is disabled")
    assert env["vis"].suggest_practice("sc-domain:havensorthodontics.com", [{"id": 1, "name": "Heights Orthodontics"},
                                                                           {"id": 2, "name": "Havens Orthodontics"}]) == 2
