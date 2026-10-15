"""Market map: our offices, nearby dental practices (potential referrers) and competing orthodontists.

Built to stay inside Google Maps Platform terms:
- Places are found with the official Places API (New) Text Search, never by scraping.
- We store only what Google allows: place IDs (kept) and latitude/longitude (deleted after
  30 days, refreshed monthly). Names, addresses, phones, ratings are never stored; the map page
  asks Google for them live when someone clicks a pin.
- Results are shown only on a Google map (Maps JavaScript API), never on another map.
Our own layer label (referrer / competitor / ours) and the distance to the nearest office are
our data, worked out at refresh time.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta

from . import connectors as cx
from . import db

PLACES_BASE = os.environ.get("PLACES_BASE", "https://places.googleapis.com").rstrip("/")
KEEP_DAYS = 30            # Google: lat/lng may be cached for up to 30 consecutive days
REFRESH_DAYS = 27         # refresh before the 30-day limit
DEFAULT_RADIUS = 15       # miles around each office
DEFAULT_MAX_REQUESTS = 2000   # per refresh; Text Search Pro has 5,000 free requests a month
MAX_DEPTH = 3             # a busy tile is split into 4, up to 3 times
SAME_SPOT_MILES = 0.1     # a search result this close to one of our offices is that office
QUERIES = ("dentist", "orthodontist")
FIELDS = "places.id,places.displayName,places.location,places.types,places.businessStatus,nextPageToken"
DENTAL_TYPES = {"dentist", "dental_clinic", "orthodontist"}
ORTHO_NAME = re.compile(r"ortho|braces|invisalign|aligner", re.I)
LAYERS = {"ours": "Our offices", "referrer": "Dental practices (potential referrers)",
          "competitor": "Other orthodontists (competitors)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS map_offices (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL,
  place_id TEXT NOT NULL UNIQUE,
  lat REAL, lng REAL,
  coords_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS map_places (
  place_id TEXT PRIMARY KEY,
  layer TEXT NOT NULL,
  offers_ortho INTEGER DEFAULT 0,
  office_id INTEGER,
  miles REAL,
  lat REAL NOT NULL, lng REAL NOT NULL,
  fetched_at TEXT NOT NULL
);
"""

_lock = threading.Lock()


class MapError(RuntimeError):
    pass


# ------------------------------------------------------------------ settings
def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


def server_key():
    return cx.setting("MAPS_SERVER_KEY")


def browser_key():
    return cx.setting("MAPS_BROWSER_KEY")


def map_id():
    return cx.setting("MAPS_MAP_ID") or "DEMO_MAP_ID"


def radius():
    try:
        return min(max(float(db.get_setting("map_radius_miles") or DEFAULT_RADIUS), 1), 50)
    except ValueError:
        return DEFAULT_RADIUS


def max_requests():
    try:
        return max(int(db.get_setting("map_max_requests") or DEFAULT_MAX_REQUESTS), 10)
    except ValueError:
        return DEFAULT_MAX_REQUESTS


def brand_terms():
    raw = db.get_setting("map_brand_terms") or ""
    return [t.strip().lower() for t in re.split(r"[,\n]", raw) if len(t.strip()) >= 3]


def status():
    try:
        return json.loads(db.get_setting("map_status") or "{}")
    except ValueError:
        return {}


def _set_status(**kw):
    db.set_setting("map_status", json.dumps(kw))


# ------------------------------------------------------------------ geometry
def miles_between(a_lat, a_lng, b_lat, b_lng):
    r = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(h)))


def box_around(lat, lng, miles):
    dlat = miles / 69.0
    dlng = miles / (69.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - dlat, lng - dlng, lat + dlat, lng + dlng)


def split(box):
    s, w, n, e = box
    mlat, mlng = (s + n) / 2, (w + e) / 2
    return [(s, w, mlat, mlng), (s, mlng, mlat, e), (mlat, w, n, mlng), (mlat, mlng, n, e)]


def box_touches_circle(box, lat, lng, miles):
    s, w, n, e = box
    near_lat, near_lng = min(max(lat, s), n), min(max(lng, w), e)
    return miles_between(lat, lng, near_lat, near_lng) <= miles


# ------------------------------------------------------------------ Google Places API (New)
def _post(path, body, key):
    return cx._http(f"{PLACES_BASE}{path}", data=body, headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS})


def _get_location(place_id, key):
    j = cx._http(f"{PLACES_BASE}/v1/places/{place_id}", headers={
        "X-Goog-Api-Key": key, "X-Goog-FieldMask": "location"})
    loc = j.get("location") or {}
    if "latitude" not in loc:
        raise MapError("Google didn't return a location for that place.")
    return loc["latitude"], loc["longitude"]


class Searcher:
    """Runs Text Search over a box, splitting busy boxes so we don't miss places past Google's 60-result cap."""

    def __init__(self, key, budget):
        self.key, self.budget, self.requests, self.capped = key, budget, 0, False

    def search_box(self, query, box):
        out, token = [], None
        for _ in range(3):  # Google returns at most 3 pages of 20
            if self.requests >= self.budget:
                self.capped = True
                break
            body = {"textQuery": query, "pageSize": 20,
                    "locationRestriction": {"rectangle": {
                        "low": {"latitude": box[0], "longitude": box[1]},
                        "high": {"latitude": box[2], "longitude": box[3]}}}}
            if token:
                body["pageToken"] = token
            self.requests += 1
            j = _post("/v1/places:searchText", body, self.key)
            out.extend(j.get("places") or [])
            token = j.get("nextPageToken")
            if not token:
                return out, False
        return out, bool(token)  # still more: this box is too busy

    def sweep(self, query, lat, lng, miles):
        found, todo = {}, [(box_around(lat, lng, miles), 0)]
        while todo and not self.capped:
            box, depth = todo.pop(0)
            places, busy = self.search_box(query, box)
            for p in places:
                if p.get("id"):
                    found[p["id"]] = p
            if (busy or len(places) >= 60) and depth < MAX_DEPTH:
                todo.extend((b, depth + 1) for b in split(box) if box_touches_circle(b, lat, lng, miles))
        return found


# ------------------------------------------------------------------ classification
def classify(place, query, brands):
    """Our label for a search result: 'ours', 'competitor', 'referrer' or None (not a dental practice / closed).
    Uses the name only in memory, at refresh time; names are not stored."""
    if place.get("businessStatus") == "CLOSED_PERMANENTLY":
        return None, False
    name = ((place.get("displayName") or {}).get("text") or "").lower()
    types = set(place.get("types") or [])
    ortho_name = bool(ORTHO_NAME.search(name))
    if not (types & DENTAL_TYPES or ortho_name):
        return None, False
    if any(b in name for b in brands):
        return "ours", False
    if ortho_name or "orthodontist" in types:
        return "competitor", False
    # a general dentist that shows up when people search "orthodontist" likely offers braces/aligners
    return "referrer", query == "orthodontist"


# ------------------------------------------------------------------ storage
def offices():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM map_offices ORDER BY label COLLATE NOCASE")]


def add_office(label, place_id, lat, lng):
    label, place_id = (label or "").strip()[:120], (place_id or "").strip()
    if not label or not re.fullmatch(r"[A-Za-z0-9_-]{10,300}", place_id):
        raise MapError("Pick the office from the Google search box so it has a place ID.")
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        raise MapError("That place came without a location; try picking it again.")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise MapError("That location isn't valid.")
    with db.conn() as c:
        c.execute("INSERT INTO map_offices(label, place_id, lat, lng, coords_at, created_at) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT(place_id) DO UPDATE SET label=excluded.label, lat=excluded.lat, lng=excluded.lng, "
                  "coords_at=excluded.coords_at", (label, place_id, lat, lng, db.now(), db.now()))


def rename_office(oid, label):
    if (label or "").strip():
        with db.conn() as c:
            c.execute("UPDATE map_offices SET label=? WHERE id=?", (label.strip()[:120], oid))


def remove_office(oid):
    with db.conn() as c:
        c.execute("DELETE FROM map_offices WHERE id=?", (oid,))
        c.execute("DELETE FROM map_places WHERE office_id=?", (oid,))


def purge_old(now=None):
    """Delete coordinates older than Google's 30-day limit."""
    cutoff = ((now or datetime.now()) - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    with db.conn() as c:
        n = c.execute("DELETE FROM map_places WHERE fetched_at < ?", (cutoff,)).rowcount
        c.execute("UPDATE map_offices SET lat=NULL, lng=NULL WHERE coords_at < ?", (cutoff,))
    return n


def places():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM map_places")]


def counts():
    out = {o["id"]: {"referrer": 0, "competitor": 0, "offers_ortho": 0, "ours": 0} for o in offices()}
    for p in places():
        if p["office_id"] in out:
            out[p["office_id"]][p["layer"]] += 1
            out[p["office_id"]]["offers_ortho"] += p["offers_ortho"]
    return out


def map_data():
    """Everything the map page draws: IDs, coordinates, our labels. No Google names/addresses."""
    offs = [o for o in offices() if o["lat"] is not None]
    return {"radius": radius(),
            "offices": [{"id": o["id"], "label": o["label"], "place_id": o["place_id"], "lat": o["lat"],
                         "lng": o["lng"]} for o in offs],
            "places": [{"id": p["place_id"], "layer": p["layer"], "ortho": bool(p["offers_ortho"]),
                        "office": p["office_id"], "mi": round(p["miles"], 1), "lat": p["lat"], "lng": p["lng"]}
                       for p in places()]}


# ------------------------------------------------------------------ refresh
def refresh(now=None):
    """Re-run the searches for every office and replace the stored places. Returns a status dict."""
    key = server_key()
    if not key:
        raise MapError("Add the server API key in Map setup first.")
    offs = offices()
    if not offs:
        raise MapError("Add at least one of our offices first.")
    if not _lock.acquire(blocking=False):
        raise MapError("A refresh is already running. Check back in a few minutes.")
    started = time.time()
    try:
        _set_status(busy=True, started=db.now())
        stamp = (now or datetime.now()).isoformat(timespec="seconds")
        miles, brands = radius(), brand_terms()
        s = Searcher(key, max_requests())
        # keep office coordinates fresh (Place Details, location only)
        for o in offs:
            lat, lng = _get_location(o["place_id"], key)
            o["lat"], o["lng"] = lat, lng
            with db.conn() as c:
                c.execute("UPDATE map_offices SET lat=?, lng=?, coords_at=? WHERE id=?", (lat, lng, stamp, o["id"]))
        found = {}
        for o in offs:
            for q in QUERIES:
                for pid, p in s.sweep(q, o["lat"], o["lng"], miles).items():
                    found.setdefault(pid, []).append((q, p))
        office_ids = {o["place_id"] for o in offs}
        rows = []
        for pid, hits in found.items():
            if pid in office_ids:
                continue
            p = hits[0][1]
            loc = p.get("location") or {}
            if "latitude" not in loc:
                continue
            lat, lng = loc["latitude"], loc["longitude"]
            near = min(offs, key=lambda o: miles_between(o["lat"], o["lng"], lat, lng))
            d = miles_between(near["lat"], near["lng"], lat, lng)
            if d > miles or d < SAME_SPOT_MILES:
                continue
            labels = [classify(hp, q, brands) for q, hp in hits]
            layers = {lab for lab, _ in labels if lab}
            if not layers:
                continue
            layer = next(x for x in ("ours", "competitor", "referrer") if x in layers)
            ortho = layer == "referrer" and any(o for lab, o in labels if lab == "referrer")
            rows.append((pid, layer, int(ortho), near["id"], d, lat, lng, stamp))
        if s.capped and not rows:
            raise MapError("Hit the request limit before finding anything; raise it in Map setup.")
        with db.conn() as c:
            if s.capped:   # partial run: add/update what we found, keep the rest until it expires
                c.executemany("INSERT OR REPLACE INTO map_places VALUES (?,?,?,?,?,?,?,?)", rows)
            else:
                c.execute("DELETE FROM map_places")
                c.executemany("INSERT INTO map_places VALUES (?,?,?,?,?,?,?,?)", rows)
        res = {"busy": False, "ok": True, "at": stamp, "requests": s.requests, "capped": s.capped,
               "found": len(rows), "seconds": round(time.time() - started)}
        db.set_setting("map_last_refresh", stamp)
        _set_status(**res)
        return res
    except Exception as e:   # always clear "busy" and record why
        res = {"busy": False, "ok": False, "at": db.now(), "error": str(e)[:300]}
        _set_status(**res)
        raise MapError(str(e))
    finally:
        _lock.release()


def refresh_in_background():
    def run():
        try:
            refresh()
        except MapError:
            pass   # status already records the error
    threading.Thread(target=run, name="map-refresh", daemon=True).start()


def due(now=None):
    last = db.get_setting("map_last_refresh")
    if not last:
        return True
    return (now or datetime.now()) - datetime.fromisoformat(last) >= timedelta(days=REFRESH_DAYS)


def start_scheduler():
    """Once a day: delete expired coordinates, and refresh when the last refresh is ~a month old."""
    def loop():
        time.sleep(120)
        while True:
            try:
                purge_old()
                if server_key() and offices() and due():
                    refresh()
            except Exception:
                pass
            time.sleep(24 * 3600)
    threading.Thread(target=loop, name="map-monthly", daemon=True).start()
