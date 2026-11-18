"""Target zip codes: Census income and families by zip, ranked per office, page and export."""
import io
import json
import zipfile

from tests.test_scope import client  # noqa: F401  (pytest fixture)

ACS = json.dumps([
    ["B19013_001E", "B11005_001E", "B11005_002E", "B01003_001E", "zip code tabulation area"],
    ["142000", "9000", "4100", "26000", "48306"],     # Rochester Hills: high income, lots of families
    ["118000", "12000", "4300", "33000", "48309"],
    ["96000", "10000", "3000", "27000", "48307"],
    ["61000", "15000", "3900", "36000", "48341"],     # Pontiac
    ["45000", "8000", "2600", "21000", "48342"],
    ["250001", "120", "40", "300", "48304"],          # too few households to rank
    ["-666666666", "900", "100", "2000", "48357"],    # no estimate
    ["99000", "9000", "3000", "25000", "49001"],      # far away (Kalamazoo)
]).encode()
GAZ = ("GEOID\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG                                     \n"
       "48306\t1\t1\t1\t1\t42.724\t-83.140\n48309\t1\t1\t1\t1\t42.662\t-83.183\n48307\t1\t1\t1\t1\t42.659\t-83.121\n"
       "48341\t1\t1\t1\t1\t42.627\t-83.294\n48342\t1\t1\t1\t1\t42.645\t-83.280\n48304\t1\t1\t1\t1\t42.587\t-83.236\n"
       "48357\t1\t1\t1\t1\t42.659\t-83.420\n49001\t1\t1\t1\t1\t42.273\t-85.585\n")


def _zipped(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("2023_Gaz_zcta_national.txt", text)
    return buf.getvalue()


def test_parse_rank_and_page(client, monkeypatch):
    c, db, sync, mp = client
    from app import zips, places
    zips.init()
    acs, gaz = zips.parse_acs(ACS), zips.parse_gazetteer(_zipped(GAZ))
    assert acs["48357"]["income"] is None and gaz["48306"] == (42.724, -83.14)
    monkeypatch.setattr(zips, "_fetch", lambda url, timeout=120: ACS if "api.census" in url else _zipped(GAZ))
    assert zips.refresh() == 8 and zips.status()["zips"] == 8
    places.add_office("Waterford", "ChIJ_office_waterford1", 42.660, -83.200)
    r = zips.for_offices()[0]
    got = [z["zip"] for z in r["zips"]]
    assert got == ["48306", "48309", "48307", "48341", "48342"]          # by income; tiny, unknown and far zips left out
    assert [z["zip"] for z in r["target"]] == ["48306", "48309"] and r["income_floor"] == 118000
    assert r["families"] == 8400
    page = c.get("/map/zips").text
    assert "48306, 48309" in page and "Copy target zips" in page and "$142,000" in page and "(46%)" in page
    x = c.get("/map/zips.xlsx")
    assert x.headers["content-type"].startswith("application/vnd.openxmlformats")
    c.post("/map/zips/miles", data={"miles": "5"})
    assert zips.miles_setting() == 5
