"""Market map: free ID sweep, labelling only new places, new/gone tracking, 30-day rule, office import, page."""
import importlib
import io
import json
import os
import threading
import urllib.parse
import zipfile
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

PORT = 9961
KEY = "AIzaServerKey123"
OFFICE = {"id": "ChIJ_office_defiance01", "lat": 41.2845, "lng": -84.3558}
BRYAN = {"id": "ChIJ_office_bryan00001", "lat": 41.4748, "lng": -84.5525}
LOG = []
SLEPT = []
THROTTLE = {"details": 0, "fail_after": None, "served": 0}   # 429s to send, and a hard failure after N lookups


def P(pid, name, lat, lng, types=("dentist", "health"), status="OPERATIONAL", q=("dentist",)):
    return {"id": pid, "displayName": {"text": name}, "location": {"latitude": lat, "longitude": lng},
            "types": list(types), "businessStatus": status, "_q": set(q)}


PLACES = {}


def reset_places():
    PLACES.clear()
    for p in [
        P("ChIJ_dent_smile_family", "Smile Family Dentistry", 41.30, -84.36),
        P("ChIJ_dent_braces_too01", "Main Street Dental", 41.27, -84.40, q=("dentist", "orthodontist")),
        P("ChIJ_dent_closed_00001", "Old Dental Office", 41.29, -84.33, status="CLOSED_PERMANENTLY"),
        P("ChIJ_dent_far_away_001", "Far Dental", 41.70, -84.36),          # ~29 mi north
        P("ChIJ_pharmacy_0000001", "Dental Supply Pharmacy", 41.28, -84.37, ("pharmacy",)),
        P(OFFICE["id"], "Heights Orthodontics", OFFICE["lat"], OFFICE["lng"], ("dentist",), q=("dentist", "orthodontist")),
        P(BRYAN["id"], "Heights Orthodontics Bryan", BRYAN["lat"], BRYAN["lng"], ("dentist",), q=("orthodontist",)),
        P("ChIJ_ortho_rival_00001", "Bright Orthodontics", 41.31, -84.30, q=("orthodontist",)),
    ]:
        PLACES[p["id"]] = p


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _denied(self):
        if self.headers.get("X-Goog-Api-Key") != KEY:
            self._send(403, {"error": {"code": 403, "message": "API key not valid.", "status": "PERMISSION_DENIED"}})
            return True
        return False

    def do_GET(self):
        mask = self.headers.get("X-Goog-FieldMask")
        pid = urllib.parse.urlparse(self.path).path.rsplit("/", 1)[-1]
        LOG.append(("details", mask, pid))
        if self._denied():
            return
        if THROTTLE["details"] > 0:
            THROTTLE["details"] -= 1
            return self._send(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message":
                              "Quota exceeded for quota metric 'GetPlaceRequest' and limit 'GetPlaceRequest per minute'"}})
        THROTTLE["served"] += 1
        if THROTTLE["fail_after"] is not None and THROTTLE["served"] > THROTTLE["fail_after"]:
            return self._send(403, {"error": {"code": 403, "message": "This API key was deleted.",
                                              "status": "PERMISSION_DENIED"}})
        p = PLACES.get(pid)
        if not p:
            return self._send(404, {"error": {"code": 404, "message": "Not found.", "status": "NOT_FOUND"}})
        if mask == "location":
            return self._send(200, {"location": p["location"]})
        return self._send(200, {k: v for k, v in p.items() if not k.startswith("_")})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mask = self.headers.get("X-Goog-FieldMask")
        LOG.append(("search", mask, body))
        if self._denied():
            return
        if "locationRestriction" not in body:   # office import: name + address match
            q = body["textQuery"].lower()
            hits = [p for p in PLACES.values() if p["displayName"]["text"].lower() in q]
            hit = max(hits, key=lambda p: len(p["displayName"]["text"])) if hits else None
            return self._send(200, {"places": [{"id": hit["id"], "location": hit["location"]}] if hit else []})
        rect = body["locationRestriction"]["rectangle"]
        lo, hi = rect["low"], rect["high"]
        inside = [p for p in PLACES.values() if body["textQuery"] in p["_q"]
                  and lo["latitude"] <= p["location"]["latitude"] <= hi["latitude"]
                  and lo["longitude"] <= p["location"]["longitude"] <= hi["longitude"]]
        out = [{"id": p["id"]} for p in inside]
        if mask != "places.id,nextPageToken":
            return self._send(400, {"error": {"code": 400, "message": "unexpected mask " + str(mask)}})
        # A full-size dentist box pretends to be busy: 3 pages and still more, so it must be split.
        if body["textQuery"] == "dentist" and hi["latitude"] - lo["latitude"] > 0.3:
            page = int(body.get("pageToken", "0"))
            return self._send(200, {"places": out[:1], "nextPageToken": str(page + 1)})
        return self._send(200, {"places": out})


def calls(kind, mask=None):
    return [x for x in LOG if x[0] == kind and (mask is None or x[1] == mask)]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"),
                       "PLACES_BASE": f"http://127.0.0.1:{PORT}", "ADS_PASSWORD": "pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
    from app import connectors, db, places
    for m in (db, connectors, places):
        importlib.reload(m)
    places._sleep = lambda secs: SLEPT.append(secs)
    places.PACE_SECONDS = 0
    db.init()
    places.init()
    reset_places()
    yield db, places
    srv.shutdown()


T0 = datetime(2026, 11, 1, 6, 0)


def test_classify():
    from app.places import classify
    brands = ["heights orthodontics"]
    assert classify(PLACES_SAMPLE["smile"], brands) == "referrer"
    assert classify(PLACES_SAMPLE["rival"], brands) == "competitor"
    assert classify(PLACES_SAMPLE["ours"], brands) == "ours"
    assert classify(PLACES_SAMPLE["pharmacy"], brands) == "skip"
    assert classify(PLACES_SAMPLE["closed"], brands) == "skip"


PLACES_SAMPLE = {
    "smile": P("a", "Smile Family Dentistry", 0, 0), "rival": P("b", "Bright Orthodontics", 0, 0),
    "ours": P("c", "Heights Orthodontics - Bryan", 0, 0), "pharmacy": P("d", "Dental Supply", 0, 0, ("pharmacy",)),
    "closed": P("e", "Old Dental", 0, 0, status="CLOSED_PERMANENTLY")}


def test_refresh_needs_setup(env):
    db, places = env
    with pytest.raises(places.MapError, match="server API key"):
        places.refresh()


def test_first_refresh_labels_everything_once(env):
    db, places = env
    db.set_setting("maps_server_key", KEY)
    db.set_setting("map_brand_terms", "Heights Orthodontics")
    db.set_setting("map_radius_miles", "25")
    places.add_office("Heights – Defiance", OFFICE["id"], OFFICE["lat"], OFFICE["lng"])
    LOG.clear()
    res = places.refresh(T0)
    assert res["ok"] and not res["capped"] and res["gone"] == 0
    got = {p["place_id"]: p for p in places.places()}
    assert got["ChIJ_dent_smile_family"]["layer"] == "referrer" and not got["ChIJ_dent_smile_family"]["offers_ortho"]
    assert got["ChIJ_dent_braces_too01"]["layer"] == "referrer" and got["ChIJ_dent_braces_too01"]["offers_ortho"]
    assert got["ChIJ_ortho_rival_00001"]["layer"] == "competitor"
    assert got[BRYAN["id"]]["layer"] == "ours"                       # brand match, another of our locations
    assert got["ChIJ_pharmacy_0000001"]["layer"] == "skip"           # remembered, so never paid for again
    assert OFFICE["id"] not in got
    assert 1 < got["ChIJ_dent_smile_family"]["miles"] < 2
    # every search asked for IDs only (the free tier); the busy dentist box was paged 3 times then split
    dent = [b for _, m, b in calls("search") if b["textQuery"] == "dentist"]
    assert {m for _, m, _ in calls("search")} == {"places.id,nextPageToken"}
    first_busy = next(i for i, b in enumerate(dent) if b.get("pageToken") == "2")
    assert [b.get("pageToken") for b in dent[first_busy - 2:first_busy + 1]] == [None, "1", "2"]
    assert len(dent) > first_busy + 1                                 # ...then split into smaller boxes
    # one Pro lookup per new place, one location renewal for the office
    pro = calls("details", places.NEW_FIELDS)
    assert len(pro) == len({pid for *_, pid in pro}) == len(got)
    assert [pid for *_, pid in calls("details", "location")] == [OFFICE["id"]]
    c = places.counts()[places.offices()[0]["id"]]
    assert (c["referrer"], c["competitor"], c["offers_ortho"], c["ours"], c["new"], c["gone"]) == (2, 1, 1, 1, 0, 0)


def test_map_data_has_no_names(env):
    db, places = env
    blob = json.dumps(places.map_data())
    assert "Smile Family" not in blob and "Bright" not in blob and "ChIJ_ortho_rival_00001" in blob
    assert "ChIJ_pharmacy_0000001" not in blob and "ChIJ_dent_far_away_001" not in blob


def test_second_refresh_only_pays_for_new_and_flags_new_and_gone(env):
    db, places = env
    PLACES["ChIJ_dent_new_opened01"] = P("ChIJ_dent_new_opened01", "Fresh Smiles Dental", 41.26, -84.34)
    del PLACES["ChIJ_ortho_rival_00001"]                               # stopped coming up
    LOG.clear()
    res = places.refresh(T0 + timedelta(days=28))
    assert res["new"] == 1 and res["gone"] == 1
    assert [pid for *_, pid in calls("details", places.NEW_FIELDS)] == ["ChIJ_dent_new_opened01"]
    renewed = {pid for *_, pid in calls("details", "location")}
    assert "ChIJ_dent_smile_family" in renewed and "ChIJ_pharmacy_0000001" not in renewed   # non-dental never re-checked
    data = {p["id"]: p for p in places.map_data()["places"]}
    assert data["ChIJ_dent_new_opened01"]["new"] is True and data["ChIJ_dent_smile_family"]["new"] is False
    assert data["ChIJ_ortho_rival_00001"]["gone"] == "2026-11-29"
    c = places.counts()[places.offices()[0]["id"]]
    assert (c["new"], c["gone"], c["competitor"]) == (1, 1, 0)


def test_manual_refresh_soon_after_costs_nothing_extra(env):
    db, places = env
    PLACES["ChIJ_ortho_rival_00001"] = P("ChIJ_ortho_rival_00001", "Bright Orthodontics", 41.31, -84.30,
                                         q=("orthodontist",))
    LOG.clear()
    places.refresh(T0 + timedelta(days=29))
    assert calls("details", places.NEW_FIELDS) == []
    # only the office and the competitor (its location is from day 0) are renewed; recent ones aren't re-paid
    assert {pid for *_, pid in calls("details", "location")} == {OFFICE["id"], "ChIJ_ortho_rival_00001"}
    rival = next(p for p in places.places() if p["place_id"] == "ChIJ_ortho_rival_00001")
    assert rival["gone_at"] is None                                    # back on Google: no longer "gone"


def test_brand_change_relabels(env):
    db, places = env
    db.set_setting("map_brand_terms", "Bright Orthodontics, Heights Orthodontics")
    places.refresh(T0 + timedelta(days=30))
    rival = next(p for p in places.places() if p["place_id"] == "ChIJ_ortho_rival_00001")
    assert rival["layer"] == "ours"
    db.set_setting("map_brand_terms", "Heights Orthodontics")
    places.refresh(T0 + timedelta(days=30, hours=1))


def test_lookup_cap_is_partial_and_marks_nothing_gone(env):
    db, places = env
    for i in range(8):
        pid = f"ChIJ_dent_another_new{i}"
        PLACES[pid] = P(pid, f"Another Dental {i}", 41.25 + i * 0.005, -84.35)
    db.set_setting("map_max_requests", "10")
    db.set_setting("map_brand_terms", "Heights Orthodontics, Zed")    # forces relabel: more lookups than the cap
    del PLACES["ChIJ_dent_smile_family"]
    res = places.refresh(T0 + timedelta(days=31))
    assert res["capped"] and res["gone"] == 0
    assert all(p["gone_at"] is None for p in places.places() if p["place_id"] == "ChIJ_dent_smile_family")
    db.set_setting("map_max_requests", None)
    db.set_setting("map_brand_terms", "Heights Orthodontics")
    PLACES["ChIJ_dent_smile_family"] = P("ChIJ_dent_smile_family", "Smile Family Dentistry", 41.30, -84.36)


def test_bad_key_reports_error(env):
    db, places = env
    db.set_setting("maps_server_key", "wrong")
    with pytest.raises(places.MapError, match="API key not valid"):
        places.refresh()
    st = places.status()
    assert st["ok"] is False and not st["busy"]
    db.set_setting("maps_server_key", KEY)


def test_purge_after_30_days_keeps_ids(env):
    db, places = env
    places.refresh(T0 + timedelta(days=32))
    n = len(places.places())
    assert places.purge_old(T0 + timedelta(days=70)) > 0
    assert len(places.places()) == n                                   # IDs and our labels stay
    assert all(p["lat"] is None for p in places.places())             # coordinates are gone
    assert places.map_data()["places"] == []
    assert places.offices()[0]["lat"] is None
    assert places.due(T0 + timedelta(days=70))


def test_parse_office_files():
    from app.places import MapError, parse_office_file
    rows = parse_office_file(b"Practice,Street,City,State,Zip\nHeights Orthodontics,1 Main St,Defiance,OH,43512\n"
                             b"No Address Here,,,,\n", "offices.csv")
    assert rows == [{"name": "Heights Orthodontics – Defiance", "address": "1 Main St, Defiance, OH 43512",
                     "lat": None, "lng": None, "group": ""}]
    # A real-world export: "Location Name" header, a doctors column, Windows-1252 bytes, all caps, and a row
    # whose unquoted comma (two doctors) shifts the columns while its last empty cell is missing.
    messy = ("Location Name,Orthodontists,Address,City,State,Zip,Phone#,Website,Booking Link\r\n"
             "SAMPLE ORTHODONTICS,Dr\u2019s A & B Sample,214 WINSTON DR,MARSHALL,MI,49068,555-0100,example.com,\r\n"
             "Lake Sample Ortho,Dr. One, Dr. Two,22006 Greater Mack Ave,St Clair Shores,MI,48080,,example.org\r\n"
             "OSSX,Dr\u00a0Sample,841 S. Union St,Warsaw,IN,46580,555-0101,https://example.net/,\r\n").encode("cp1252")
    rows = parse_office_file(messy, "Practice Map.csv")
    assert [(r["name"], r["address"]) for r in rows] == [
        ("Sample Orthodontics – Marshall", "214 WINSTON DR, Marshall, MI 49068"),
        ("Lake Sample Ortho – St Clair Shores", "22006 Greater Mack Ave, St Clair Shores, MI 48080"),
        ("OSSX – Warsaw", "841 S. Union St, Warsaw, IN 46580")]
    kml = b"""<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <name>MCDP map</name>
      <Folder><name>Our Locations</name>
        <Placemark><name>Heights Orthodontics</name><Point><coordinates>-84.3558,41.2845,0</coordinates></Point></Placemark>
        <Placemark><name>Heights Orthodontics Bryan</name><address>2 Oak St, Bryan, OH</address></Placemark>
      </Folder>
      <Folder><name>Competitors</name>
        <Placemark><name>Bright Orthodontics</name><Point><coordinates>-84.30,41.31,0</coordinates></Point></Placemark>
      </Folder></Document></kml>"""
    rows = parse_office_file(kml, "map.kml")
    assert [(r["name"], r["group"]) for r in rows] == [("Heights Orthodontics", "Our Locations"),
                                                       ("Heights Orthodontics Bryan", "Our Locations"),
                                                       ("Bright Orthodontics", "Competitors")]
    assert rows[0]["lat"] == 41.2845 and rows[1]["address"] == "2 Oak St, Bryan, OH"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc.kml", kml)
    assert len(parse_office_file(buf.getvalue(), "map.kmz")) == 3
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["Office Name", "Address"])
    wb.active.append(["Heights Orthodontics", "1 Main St, Defiance, OH"])
    wb.active["A1"] = "Name"
    xb = io.BytesIO()
    wb.save(xb)
    assert parse_office_file(xb.getvalue(), "offices.xlsx")[0]["address"] == "1 Main St, Defiance, OH"
    with pytest.raises(MapError, match="No offices found"):
        parse_office_file(b"foo,bar\n1,2\n", "x.csv")


def test_import_matches_and_flags(env):
    db, places = env
    rows = [{"name": "Heights Orthodontics Bryan", "address": "2 Oak St, Bryan, OH", "lat": None, "lng": None},
            {"name": "Heights Orthodontics", "address": "", "lat": 41.40, "lng": -84.36},   # pin ~8 mi off
            {"name": "Nowhere Dental", "address": "1 Nope Rd", "lat": None, "lng": None}]
    added, problems = places.import_offices(rows)
    assert added == 2 and problems == ["Google found no match for Nowhere Dental."]
    offs = {o["place_id"]: o for o in places.offices()}
    assert offs[BRYAN["id"]]["address"] == "2 Oak St, Bryan, OH" and offs[BRYAN["id"]]["match_note"] == ""
    assert "check it" in offs[OFFICE["id"]]["match_note"]
    assert offs[OFFICE["id"]]["label"] == "Heights Orthodontics"


def test_add_office_validation(env):
    db, places = env
    with pytest.raises(places.MapError):
        places.add_office("x", "bad id!", 1, 2)
    with pytest.raises(places.MapError):
        places.add_office("x", "ChIJ_valid_id_123", "abc", 2)


def test_map_page_and_import_flow(env):
    db, places = env
    from fastapi.testclient import TestClient
    from app import main
    importlib.reload(main)
    db.set_setting("maps_browser_key", "AIzaBrowser1")
    places.refresh(T0 + timedelta(days=60))
    with TestClient(main.app) as cl:
        assert cl.get("/map", follow_redirects=False).status_code == 303
        assert cl.get("/map/data.json").status_code == 401
        cl.post("/login", data={"password": "pw"})
        html = cl.get("/map").text
        assert "Market map" in html and "AIzaBrowser1" in html and KEY not in html
        assert "2 Oak St, Bryan, OH" in html and "No longer listed" in html
        data = cl.get("/map/data.json").json()
        assert data["radius"] == 25 and len(data["offices"]) == 2
        kml = (b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
               b'<Folder><name>Our Locations</name><Placemark><name>Heights Orthodontics Bryan</name>'
               b'<address>2 Oak St, Bryan, OH</address></Placemark></Folder>'
               b'<Folder><name>Competitors</name><Placemark><name>Bright Orthodontics</name>'
               b'<address>9 Elm</address></Placemark></Folder></Document></kml>')
        r = cl.post("/map/import", files={"file": ("export.kml", kml)}, follow_redirects=False)
        assert r.status_code == 303
        html = cl.get("/map").text
        assert "Which of these are our offices?" in html
        assert 'name="row" value="0" checked' in html and 'name="row" value="1" >' in html   # only "Our Locations" ticked
        cl.post("/map/import/confirm", data={"row": ["0"]})
        assert db.get_setting("map_import_preview") is None
        r = cl.post("/map/setup", data={"radius": "15", "brands": "Heights Orthodontics", "map_id": "abc123",
                                         "max_requests": "1500"}, follow_redirects=False)
        assert r.status_code == 303 and places.radius() == 15 and places.map_id() == "abc123"
        assert places.server_key() == KEY        # blank key field keeps the saved key


def test_upgrade_from_first_version(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE map_places (place_id TEXT PRIMARY KEY, layer TEXT NOT NULL, offers_ortho INTEGER DEFAULT 0,
        office_id INTEGER, miles REAL, lat REAL NOT NULL, lng REAL NOT NULL, fetched_at TEXT NOT NULL);
        INSERT INTO map_places VALUES ('ChIJ_x_000000001','referrer',0,1,2.0,41.0,-84.0,'2026-10-20T06:00:00');""")
    c.commit()
    c.close()
    from app import db, places
    monkeypatch.setattr(db, "DB_PATH", str(path))
    places.init()
    p = places.places()[0]
    assert p["coords_at"] == p["first_seen"] == "2026-10-20T06:00:00"
    with db.conn() as c2:   # new rows insert fine (no leftover NOT NULL date column)
        c2.execute("INSERT INTO map_places(place_id, layer) VALUES ('ChIJ_y_000000001', 'skip')")


def test_slow_down_is_retried_and_progress_survives_a_failure(env):
    db, places = env
    db.set_setting("maps_server_key", KEY)
    db.set_setting("map_brand_terms", "Heights Orthodontics, Retry")   # forces lookups of every place
    THROTTLE.update(details=2, fail_after=None, served=0)
    SLEPT.clear()
    res = places.refresh(T0 + timedelta(days=90))
    assert res["ok"] and SLEPT[:2] == [20, 40]                        # waited, then carried on
    # a lookup that fails for good part-way: what was found is kept, and the next run carries on
    for i in range(250):
        pid = f"ChIJ_dent_bulk_{i:05d}"
        PLACES[pid] = P(pid, f"Bulk Dental {i}", 41.20 + (i % 50) * 0.002, -84.40 + (i // 50) * 0.01)
    db.set_setting("map_brand_terms", "Heights Orthodontics")
    db.set_setting("map_per_office", "0")                              # every practice in the radius
    THROTTLE.update(details=0, fail_after=230, served=0)
    with pytest.raises(places.MapError, match="deleted"):
        places.refresh(T0 + timedelta(days=91))
    st = places.status()
    assert st["ok"] is False and not st["busy"] and st["kept"] >= 200
    THROTTLE.update(fail_after=None, served=0)
    LOG.clear()
    res = places.refresh(T0 + timedelta(days=91, hours=1))
    assert res["ok"]
    redone = {pid for *_, pid in calls("details", places.NEW_FIELDS)}
    assert len(redone) < 100                                          # only what wasn't saved is looked up again
    assert sum(1 for p in places.places() if p["place_id"].startswith("ChIJ_dent_bulk_")) == 250


def test_closest_n_per_office(env):
    """Only the closest N dental practices per office are looked up and shown; competitors stay complete."""
    db, places = env
    db.set_setting("map_per_office", "100")
    db.set_setting("map_radius_miles", "25")
    with db.conn() as c:   # start from a clean slate so every place is new
        c.execute("DELETE FROM map_places")
    for o in places.offices():   # one office, so only its own widening search counts
        if o["place_id"] != OFFICE["id"]:
            places.remove_office(o["id"])
    for i in range(250):   # a dense ring of dentists: 0.5 to ~10 miles out from Defiance
        pid = f"ChIJ_dent_ring_{i:05d}"
        PLACES[pid] = P(pid, f"Ring Dental {i}", OFFICE["lat"] + 0.007 + i * 0.0006, OFFICE["lng"] - 0.004)
    PLACES["ChIJ_ortho_far_comp001"] = P("ChIJ_ortho_far_comp001", "Far Orthodontics", OFFICE["lat"] - 0.25,
                                         OFFICE["lng"] + 0.05, q=("orthodontist",))     # ~17 mi south
    THROTTLE.update(details=0, fail_after=None, served=0)
    LOG.clear()
    res = places.refresh(T0 + timedelta(days=120))
    assert res["ok"] and not res["capped"]
    looked_up = {pid for *_, pid in calls("details", places.NEW_FIELDS)}
    ring = [p for p in places.map_data()["places"] if p["id"].startswith("ChIJ_dent_ring_")]
    defiance = places.offices()[[o["place_id"] for o in places.offices()].index(OFFICE["id"])]["id"]
    shown_near_defiance = [p for p in places.map_data()["places"] if p["layer"] == "referrer"]
    assert len([x for x in looked_up if x.startswith("ChIJ_dent_ring_")]) < 250     # didn't pay for all of them
    assert ring and max(int(p["id"][-5:]) for p in ring) < 110      # the ones shown are the nearest
    assert len(shown_near_defiance) == 100
    assert any(p["id"] == "ChIJ_ortho_far_comp001" for p in places.map_data()["places"])   # competitors in full
    # nothing outside where we looked is marked "no longer listed"
    far = [p for p in places.places() if p["place_id"].startswith("ChIJ_dent_ring_") and p["gone_at"]]
    assert far == []
    assert defiance


def test_stuck_running_status_clears_after_a_restart(env):
    """An update restarts the app mid-refresh; the page mustn't say 'Refreshing…' forever."""
    db, places = env
    from app import referrals
    try:
        long_ago = "2026-10-16T00:24:00"
        for mod, key in ((places, "map_status"), (referrals, "ref_status")):
            db.set_setting(key, json.dumps({"busy": True, "started": long_ago, "beat": long_ago, "step": "Looked up 257"}))
            st = mod.status()
            assert st["busy"] is False and st["ok"] is False and "restarted" in st["error"]
            assert json.loads(db.get_setting(key))["busy"] is False          # saved, so the button works again
        # A refresh genuinely running in this app (lock held) is left alone, however long it takes.
        places._lock.acquire()
        db.set_setting("map_status", json.dumps({"busy": True, "started": long_ago, "beat": long_ago}))
        assert places.status()["busy"] is True
        places._lock.release()
        # Busy since after this app started, and recently active: still treated as running.
        fresh = db.now()
        places._started_at = "2000-01-01T00:00:00"
        db.set_setting("map_status", json.dumps({"busy": True, "started": fresh, "beat": fresh}))
        assert places.status()["busy"] is True
    finally:
        db.set_setting("map_status", None)
        db.set_setting("ref_status", None)
