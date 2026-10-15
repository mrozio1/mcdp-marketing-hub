"""Market map: Places API (New) searches, classification, 30-day storage rule, map page."""
import importlib
import json
import os
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

PORT = 9961
KEY = "AIzaServerKey123"
OFFICE = {"id": "ChIJ_office_defiance01", "lat": 41.2845, "lng": -84.3558}
LOG = []


def P(pid, name, lat, lng, types=("dentist", "health"), status="OPERATIONAL"):
    return {"id": pid, "displayName": {"text": name}, "location": {"latitude": lat, "longitude": lng},
            "types": list(types), "businessStatus": status}


DENTIST = {
    "ChIJ_dent_smile_family": P("ChIJ_dent_smile_family", "Smile Family Dentistry", 41.30, -84.36),
    "ChIJ_dent_braces_too01": P("ChIJ_dent_braces_too01", "Main Street Dental", 41.27, -84.40),
    "ChIJ_dent_closed_00001": P("ChIJ_dent_closed_00001", "Old Dental Office", 41.29, -84.33, status="CLOSED_PERMANENTLY"),
    "ChIJ_dent_far_away_001": P("ChIJ_dent_far_away_001", "Far Dental", 41.70, -84.36),     # ~29 mi north
    "ChIJ_pharmacy_0000001": P("ChIJ_pharmacy_0000001", "Dental Supply Pharmacy", 41.28, -84.37, ("pharmacy",)),
    OFFICE["id"]: P(OFFICE["id"], "Heights Orthodontics", OFFICE["lat"], OFFICE["lng"], ("orthodontist", "dentist")),
}
ORTHO = {
    "ChIJ_ortho_rival_00001": P("ChIJ_ortho_rival_00001", "Bright Orthodontics", 41.31, -84.30, ("dentist",)),
    "ChIJ_ortho_ours_000001": P("ChIJ_ortho_ours_000001", "Heights Orthodontics - Bryan", 41.47, -84.55, ("dentist",)),
    "ChIJ_dent_braces_too01": DENTIST["ChIJ_dent_braces_too01"],
    OFFICE["id"]: DENTIST[OFFICE["id"]],
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        LOG.append(("GET", self.path, dict(self.headers), None))
        if self.headers.get("X-Goog-Api-Key") != KEY:
            return self._send(403, {"error": {"code": 403, "message": "API key not valid.", "status": "PERMISSION_DENIED"}})
        return self._send(200, {"location": {"latitude": OFFICE["lat"], "longitude": OFFICE["lng"]}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        LOG.append(("POST", self.path, dict(self.headers), body))
        if self.headers.get("X-Goog-Api-Key") != KEY:
            return self._send(403, {"error": {"code": 403, "message": "API key not valid.", "status": "PERMISSION_DENIED"}})
        rect = body["locationRestriction"]["rectangle"]
        lo, hi = rect["low"], rect["high"]
        pool = ORTHO if body["textQuery"] == "orthodontist" else DENTIST
        inside = [p for p in pool.values() if lo["latitude"] <= p["location"]["latitude"] <= hi["latitude"]
                  and lo["longitude"] <= p["location"]["longitude"] <= hi["longitude"]]
        # The full-size dentist box pretends to be busy: 3 full pages and still more, so it must be split.
        busy = body["textQuery"] == "dentist" and hi["latitude"] - lo["latitude"] > 0.3
        if busy:
            page = int(body.get("pageToken", "0"))
            return self._send(200, {"places": inside[:1], "nextPageToken": str(page + 1)})
        return self._send(200, {"places": inside})


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
    db.init()
    places.init()
    yield db, places
    srv.shutdown()


def test_classify():
    from app.places import classify
    brands = ["heights orthodontics"]
    assert classify(DENTIST["ChIJ_dent_smile_family"], "dentist", brands) == ("referrer", False)
    assert classify(DENTIST["ChIJ_dent_braces_too01"], "orthodontist", brands) == ("referrer", True)
    assert classify(ORTHO["ChIJ_ortho_rival_00001"], "orthodontist", brands) == ("competitor", False)
    assert classify(ORTHO["ChIJ_ortho_ours_000001"], "orthodontist", brands) == ("ours", False)
    assert classify(DENTIST["ChIJ_pharmacy_0000001"], "dentist", brands) == (None, False)
    assert classify(DENTIST["ChIJ_dent_closed_00001"], "dentist", brands) == (None, False)


def test_refresh_needs_setup(env):
    db, places = env
    with pytest.raises(places.MapError, match="server API key"):
        places.refresh()


def test_refresh_builds_layers(env):
    db, places = env
    db.set_setting("maps_server_key", KEY)
    db.set_setting("map_brand_terms", "Heights Orthodontics")
    db.set_setting("map_radius_miles", "25")
    places.add_office("Heights – Defiance", OFFICE["id"], OFFICE["lat"], OFFICE["lng"])
    LOG.clear()
    res = places.refresh()
    assert res["ok"] and not res["capped"]
    got = {p["place_id"]: p for p in places.places()}
    assert got["ChIJ_dent_smile_family"]["layer"] == "referrer" and not got["ChIJ_dent_smile_family"]["offers_ortho"]
    assert got["ChIJ_dent_braces_too01"]["layer"] == "referrer" and got["ChIJ_dent_braces_too01"]["offers_ortho"]
    assert got["ChIJ_ortho_rival_00001"]["layer"] == "competitor"
    assert got["ChIJ_ortho_ours_000001"]["layer"] == "ours"           # brand match, another of our locations
    for gone in ("ChIJ_dent_closed_00001", "ChIJ_dent_far_away_001", "ChIJ_pharmacy_0000001", OFFICE["id"]):
        assert gone not in got
    assert 1 < got["ChIJ_dent_smile_family"]["miles"] < 2
    # the busy dentist box was paged 3 times then split into smaller boxes
    dent = [b for m, _, _, b in LOG if m == "POST" and b["textQuery"] == "dentist"]
    assert [b.get("pageToken") for b in dent[:3]] == [None, "1", "2"] and len(dent) > 3
    # only the fields we need are asked for, with the server key
    hdr = next(h for m, _, h, _ in LOG if m == "POST")
    assert hdr["X-Goog-Fieldmask"] == places.FIELDS and hdr["X-Goog-Api-Key"] == KEY
    c = places.counts()[places.offices()[0]["id"]]
    assert c == {"referrer": 2, "competitor": 1, "offers_ortho": 1, "ours": 1}


def test_map_data_has_no_names(env):
    db, places = env
    blob = json.dumps(places.map_data())
    assert "Smile Family" not in blob and "Bright" not in blob and "ChIJ_ortho_rival_00001" in blob


def test_request_cap_is_partial_not_wipe(env):
    db, places = env
    before = len(places.places())
    db.set_setting("map_max_requests", "10")
    res = places.refresh()
    assert res["capped"] and len(places.places()) >= before - 1
    db.set_setting("map_max_requests", None)


def test_bad_key_reports_error(env):
    db, places = env
    db.set_setting("maps_server_key", "wrong")
    with pytest.raises(places.MapError, match="API key not valid"):
        places.refresh()
    st = places.status()
    assert st["ok"] is False and not st["busy"]
    db.set_setting("maps_server_key", KEY)


def test_purge_after_30_days(env):
    db, places = env
    places.refresh()
    assert places.places()
    assert places.purge_old(datetime.now() + timedelta(days=31)) > 0
    assert places.places() == []
    assert places.offices()[0]["lat"] is None
    assert places.due(datetime.now() + timedelta(days=28))


def test_add_office_validation(env):
    db, places = env
    with pytest.raises(places.MapError):
        places.add_office("x", "bad id!", 1, 2)
    with pytest.raises(places.MapError):
        places.add_office("x", "ChIJ_valid_id_123", "abc", 2)


def test_map_page_renders(env):
    db, places = env
    from fastapi.testclient import TestClient
    from app import main
    importlib.reload(main)
    db.set_setting("maps_browser_key", "AIzaBrowser1")
    places.refresh()
    with TestClient(main.app) as cl:
        assert cl.get("/map", follow_redirects=False).status_code == 303
        assert cl.get("/map/data.json").status_code == 401
        cl.post("/login", data={"password": "pw"})
        html = cl.get("/map").text
        assert "Market map" in html and "AIzaBrowser1" in html and KEY not in html
        assert "Heights – Defiance" in html
        data = cl.get("/map/data.json").json()
        assert data["radius"] == 25 and len(data["places"]) == 4
        r = cl.post("/map/setup", data={"radius": "15", "brands": "Heights Orthodontics", "map_id": "abc123",
                                         "max_requests": "1500"}, follow_redirects=False)
        assert r.status_code == 303 and places.radius() == 15 and places.map_id() == "abc123"
        assert places.server_key() == KEY        # blank key field keeps the saved key
