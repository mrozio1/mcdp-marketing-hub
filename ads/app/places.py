"""Market map: our offices, nearby dental practices (potential referrers) and competing orthodontists.

Built to stay inside Google Maps Platform terms:
- Places are found with the official Places API (New), never by scraping.
- We store only what Google allows: place IDs (kept indefinitely) and latitude/longitude
  (deleted after 30 days, renewed monthly). Names, addresses, phones and ratings from Google are
  never stored; the map page asks Google for them live when someone clicks a pin.
- Results are shown only on a Google map (Maps JavaScript API), never on another map.
Our own data is kept indefinitely: our label for each place ID (referrer / competitor / ours /
not dental), when we first and last saw it, and the offices our team typed or uploaded.

Each refresh, cheapest first:
1. Text Search returning place IDs only (free, unlimited) sweeps the area around each office.
2. IDs we've never seen: one Place Details call (Pro) to label them.
3. IDs we already know: one Place Details call (Essentials) to renew the location, skipped when
   the stored location is still recent.
4. Known IDs that no longer come up are marked "no longer listed".
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import threading
import time
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from . import connectors as cx
from . import db

PLACES_BASE = os.environ.get("PLACES_BASE", "https://places.googleapis.com").rstrip("/")
KEEP_DAYS = 30            # Google: lat/lng may be cached for up to 30 consecutive days
REFRESH_DAYS = 27         # refresh before the 30-day limit
RENEW_AFTER_DAYS = 20     # a manual refresh doesn't re-pay for locations fetched recently
DEFAULT_RADIUS = 15       # miles around each office (the most we'll look)
DEFAULT_PER_OFFICE = 100  # closest dental practices kept per office; 0 = every one within the radius
START_MILES = 2.0         # the dental-practice search starts this far out and widens until it has enough
GROW = 1.6
DEFAULT_MAX_REQUESTS = 3000   # paid Place Details calls per refresh (5,000 Pro / 10,000 Essentials free a month)
MAX_SEARCHES = 20000      # free ID-only searches per refresh; just a runaway guard
MAX_DEPTH = 3             # a busy box is split into 4, up to 3 times
SAME_SPOT_MILES = 0.1     # a result this close to one of our offices is that office
MATCH_OK_MILES = 0.5      # imported office vs. Google's match
QUERIES = ("dentist", "orthodontist")
SEARCH_FIELDS = "places.id,nextPageToken"                       # Text Search Essentials (IDs Only): free
NEW_FIELDS = "id,displayName,location,types,businessStatus"     # Place Details Pro
RENEW_FIELDS = "location"                                       # Place Details Essentials
MATCH_FIELDS = "places.id,places.location"                      # Text Search Pro, office import only
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
  lat REAL, lng REAL,
  coords_at TEXT
);
"""
MIGRATIONS = {
    "map_offices": {"address": "TEXT DEFAULT ''", "match_note": "TEXT DEFAULT ''"},
    "map_places": {"brands_key": "TEXT DEFAULT ''", "first_seen": "TEXT", "last_seen": "TEXT", "gone_at": "TEXT",
                   "ranked": "INTEGER DEFAULT 1"},
}

_lock = threading.Lock()


class MapError(RuntimeError):
    pass


# ------------------------------------------------------------------ settings
def init():
    with db.conn() as c:
        have = {r["name"] for r in c.execute("PRAGMA table_info(map_places)")}
        if "fetched_at" in have:   # first version: rebuild without its NOT NULL date column
            c.execute("ALTER TABLE map_places RENAME TO map_places_v1")
            c.executescript(SCHEMA)
            c.execute("INSERT INTO map_places(place_id, layer, offers_ortho, office_id, miles, lat, lng, coords_at) "
                      "SELECT place_id, layer, offers_ortho, office_id, miles, lat, lng, fetched_at FROM map_places_v1")
            c.execute("DROP TABLE map_places_v1")
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        c.execute("UPDATE map_places SET first_seen=coords_at, last_seen=coords_at WHERE first_seen IS NULL")


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


def per_office():
    try:
        return max(int(db.get_setting("map_per_office") or DEFAULT_PER_OFFICE), 0)
    except ValueError:
        return DEFAULT_PER_OFFICE


def max_requests():
    try:
        return max(int(db.get_setting("map_max_requests") or DEFAULT_MAX_REQUESTS), 10)
    except ValueError:
        return DEFAULT_MAX_REQUESTS


def brand_terms():
    raw = db.get_setting("map_brand_terms") or ""
    return [t.strip().lower() for t in re.split(r"[,\n]", raw) if len(t.strip()) >= 3]


def brands_key(brands):
    return hashlib.sha1("|".join(sorted(brands)).encode()).hexdigest()[:12]


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
# Google limits requests per minute per project (the exact number depends on the account), so calls are
# spaced out, and when Google still says "slow down" (HTTP 429 / quota exceeded) we wait and try again.
PACE_SECONDS = float(os.environ.get("MAPS_PACE_SECONDS", "0.15"))     # ~400 calls a minute per kind
RETRY_WAITS = (20, 40, 60, 60, 60, 90)                                  # seconds, then give up
_sleep = time.sleep
_last_call = {}
QUOTA_HINT = re.compile(r"HTTP 429|RESOURCE_EXHAUSTED|quota exceeded|rate limit", re.I)
TRANSIENT_HINT = re.compile(r"HTTP 50[0234]|Couldn't reach|timed out", re.I)


def _paced(kind, fn):
    for attempt in range(len(RETRY_WAITS) + 1):
        wait = _last_call.get(kind, 0) + PACE_SECONDS - time.monotonic()
        if wait > 0:
            _sleep(wait)
        _last_call[kind] = time.monotonic()
        try:
            return fn()
        except cx.ConnectorError as e:
            busy = QUOTA_HINT.search(str(e))
            if attempt >= len(RETRY_WAITS) or not (busy or (TRANSIENT_HINT.search(str(e)) and attempt < 2)):
                raise
            _sleep(RETRY_WAITS[attempt] if busy else 5)


def _search(body, key, fields):
    return _paced("search", lambda: cx._http(f"{PLACES_BASE}/v1/places:searchText", data=body, headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": fields}))


def _details(place_id, key, fields):
    return _paced("details", lambda: cx._http(f"{PLACES_BASE}/v1/places/{place_id}", headers={
        "X-Goog-Api-Key": key, "X-Goog-FieldMask": fields}))


def _latlng(j):
    loc = j.get("location") or {}
    return (loc["latitude"], loc["longitude"]) if "latitude" in loc else (None, None)


class Searcher:
    """Free ID-only Text Search over a box, splitting busy boxes so nothing is missed past Google's 60-result cap."""

    def __init__(self, key, limit=MAX_SEARCHES, progress=None):
        self.key, self.limit, self.requests, self.capped, self.progress = key, limit, 0, False, progress

    def search_box(self, query, box):
        ids, token = [], None
        for _ in range(3):  # Google returns at most 3 pages of 20
            if self.requests >= self.limit:
                self.capped = True
                break
            body = {"textQuery": query, "pageSize": 20,
                    "locationRestriction": {"rectangle": {
                        "low": {"latitude": box[0], "longitude": box[1]},
                        "high": {"latitude": box[2], "longitude": box[3]}}}}
            if token:
                body["pageToken"] = token
            self.requests += 1
            if self.progress and self.requests % 50 == 0:
                self.progress(self.requests)
            j = _search(body, self.key, SEARCH_FIELDS)
            ids.extend(p["id"] for p in j.get("places") or [] if p.get("id"))
            token = j.get("nextPageToken")
            if not token:
                return ids, False
        return ids, bool(token)  # still more: this box is too busy

    def sweep(self, query, lat, lng, miles):
        found, todo = set(), [(box_around(lat, lng, miles), 0)]
        while todo and not self.capped:
            box, depth = todo.pop(0)
            ids, busy = self.search_box(query, box)
            found.update(ids)
            if (busy or len(ids) >= 60) and depth < MAX_DEPTH:
                todo.extend((b, depth + 1) for b in split(box) if box_touches_circle(b, lat, lng, miles))
        return found


# ------------------------------------------------------------------ classification
def classify(place, brands):
    """Our label for a place: 'ours', 'competitor', 'referrer' or 'skip' (not a dental practice, or closed).
    Uses the name only in memory, at refresh time; names are not stored."""
    if place.get("businessStatus") == "CLOSED_PERMANENTLY":
        return "skip"
    name = ((place.get("displayName") or {}).get("text") or "").lower()
    types = set(place.get("types") or [])
    ortho_name = bool(ORTHO_NAME.search(name))
    if not (types & DENTAL_TYPES or ortho_name):
        return "skip"
    if any(b in name for b in brands):
        return "ours"
    if ortho_name or "orthodontist" in types:
        return "competitor"
    return "referrer"


# ------------------------------------------------------------------ offices
def offices():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM map_offices ORDER BY label COLLATE NOCASE")]


def _valid_place_id(pid):
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{10,300}", pid or ""))


def add_office(label, place_id, lat, lng, address="", match_note=""):
    label, place_id = (label or "").strip()[:120], (place_id or "").strip()
    if not label or not _valid_place_id(place_id):
        raise MapError("Pick the office from the Google search box so it has a place ID.")
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        raise MapError("That place came without a location; try picking it again.")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise MapError("That location isn't valid.")
    with db.conn() as c:
        c.execute("INSERT INTO map_offices(label, place_id, lat, lng, coords_at, created_at, address, match_note) "
                  "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(place_id) DO UPDATE SET label=excluded.label, "
                  "lat=excluded.lat, lng=excluded.lng, coords_at=excluded.coords_at, "
                  "address=CASE WHEN excluded.address != '' THEN excluded.address ELSE address END, "
                  "match_note=excluded.match_note",
                  (label, place_id, lat, lng, db.now(), db.now(), (address or "").strip()[:300], match_note[:200]))


def rename_office(oid, label):
    if (label or "").strip():
        with db.conn() as c:
            c.execute("UPDATE map_offices SET label=?, match_note='' WHERE id=?", (label.strip()[:120], oid))


def remove_office(oid):
    with db.conn() as c:
        c.execute("DELETE FROM map_offices WHERE id=?", (oid,))
        c.execute("UPDATE map_places SET office_id=NULL WHERE office_id=?", (oid,))


# ------------------------------------------------------------------ office import (our own list)
NAME_COLS = ("name", "office", "practice", "location", "title", "clinic")
ADDR_COLS = ("address", "full address", "street address", "street", "address 1", "address1")
CITY_COLS, STATE_COLS, ZIP_COLS = ("city", "town"), ("state", "st", "province"), ("zip", "zip code", "postal code", "zipcode", "postcode")
LAT_COLS, LNG_COLS = ("lat", "latitude"), ("lng", "lon", "long", "longitude")
MAX_IMPORT = 300


def _norm(h):
    return " ".join(re.findall(r"[a-z0-9]+", str(h or "").lower()))


NOT_NAME = re.compile(r"\b(dr|doctor|doctors|orthodontist|orthodontists|dentist|contact|owner|file|user|domain)\b")
NOT_ADDR = re.compile(r"\b(email|e mail|web|ip)\b")


def _pick(row, names, loose=None, avoid=None):
    """A column's value by exact header name, else (if `loose`) by a header containing that word,
    e.g. "Location Name" or "Practice name" for the name, "Street Address" for the address."""
    cells = [(_norm(k), str(v or "").strip()) for k, v in row.items() if k is not None]
    for n in names:
        for k, v in cells:
            if k == n and v:
                return v
    if loose:
        for k, v in cells:
            if v and loose in k.split() and not (avoid and avoid.search(k)):
                return v
    return ""


def _tidy(text):
    """BANDEEN ORTHODONTICS -> Bandeen Orthodontics; single all-caps words (acronyms) are left alone."""
    text = " ".join(text.split())
    if text.isupper() and " " in text:
        text = " ".join(w if len(w) <= 3 and w.isalpha() and w in ("DDS", "DMD", "MS", "PC", "LLC", "OMS")
                        else w.capitalize() for w in text.split())
    return text


def _rows_to_offices(rows, group=""):
    out = []
    for r in rows:
        name = _tidy(_pick(r, NAME_COLS, "name", NOT_NAME))
        addr = _pick(r, ADDR_COLS, "address", NOT_ADDR)
        city = _pick(r, CITY_COLS)
        city = city.title() if city.isupper() else city
        tail = ", ".join(x for x in (city, " ".join(
            x for x in (_pick(r, STATE_COLS), _pick(r, ZIP_COLS)) if x)) if x)
        if tail and tail.lower() not in addr.lower():
            addr = f"{addr}, {tail}" if addr else tail
        try:
            lat, lng = float(_pick(r, LAT_COLS)), float(_pick(r, LNG_COLS))
        except ValueError:
            lat = lng = None
        if name and (addr or lat is not None):
            label = f"{name} – {city}" if city and city.lower() not in name.lower() else name
            out.append({"name": label[:120], "address": addr[:300], "lat": lat, "lng": lng, "group": group})
    return out


def _csv_dicts(text, dialect):
    """Rows as dicts. A row with more cells than headers usually has an unquoted comma inside one
    cell (e.g. two doctors' names); join two neighbouring cells so the address and zip line up."""
    rows = list(csv.reader(io.StringIO(text), dialect=dialect))
    if not rows:
        return []
    head = rows[0]
    keys = [_norm(h) for h in head]
    addr_i = next((i for i, k in enumerate(keys) if k in ADDR_COLS or "address" in k.split()), None)
    zip_i = next((i for i, k in enumerate(keys) if k in ZIP_COLS), None)

    def lines_up(cells):
        ok_addr = addr_i is None or bool(re.search(r"\d", cells[addr_i]))
        ok_zip = zip_i is None or bool(re.fullmatch(r"\s*\d{5}(-\d{4})?\s*", cells[zip_i]))
        return ok_addr and ok_zip

    out = []
    for cells in rows[1:]:
        while len(cells) > len(head) and cells[-1].strip() == "":
            cells = cells[:-1]
        cells = cells + [""] * (len(head) - len(cells))
        if not lines_up(cells):
            for extra in (1, 2):
                fixed = next((t for i in range(1, len(head))
                              for t in [cells[:i] + [",".join(cells[i:i + extra + 1])] + cells[i + extra + 1:]]
                              if len(t) >= max(addr_i or 0, zip_i or 0) + 1 and lines_up(t)), None)
                if fixed:
                    cells = fixed + [""] * (len(head) - len(fixed))
                    break
        out.append(dict(zip(head, cells)))
    return out


def _kml_offices(raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise MapError("That KML file couldn't be read.")
    strip = lambda t: t.split("}", 1)[-1]  # noqa: E731  drop the XML namespace
    out = []

    def walk(node, group):
        for ch in node:
            tag = strip(ch.tag)
            if tag == "Folder":
                fname = next((x.text or "" for x in ch if strip(x.tag) == "name"), "")
                walk(ch, fname.strip() or group)
            elif tag == "Document":
                walk(ch, group)
            elif tag == "Placemark":
                vals = {strip(x.tag): (x.text or "").strip() for x in ch.iter() if x.text and x.text.strip()}
                data = {(d.get("name") or "").lower(): "".join(v.text or "" for v in d.iter() if strip(v.tag) == "value").strip()
                        for d in ch.iter() if strip(d.tag) == "Data"}
                lat = lng = None
                coords = vals.get("coordinates", "")
                if coords:
                    try:
                        lng, lat = (float(x) for x in coords.split()[0].split(",")[:2])
                    except ValueError:
                        pass
                addr = vals.get("address") or data.get("address") or ""
                name = vals.get("name") or data.get("name") or ""
                if name and (addr or lat is not None):
                    out.append({"name": name[:120], "address": addr[:300], "lat": lat, "lng": lng, "group": group})

    walk(root, "")
    return out


def parse_office_file(data, filename):
    """Read our own office list: CSV, Excel, or a KML/KMZ export from Google My Maps."""
    fn = (filename or "").lower()
    if fn.endswith(".kmz") or data[:2] == b"PK" and not fn.endswith(".xlsx"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                kml = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
                if not kml:
                    raise MapError("That KMZ file has no map inside.")
                rows = _kml_offices(z.read(kml))
        except zipfile.BadZipFile:
            raise MapError("That file couldn't be opened.")
    elif fn.endswith(".kml") or data.lstrip()[:5] == b"<?xml" or b"<kml" in data[:500]:
        rows = _kml_offices(data)
    elif fn.endswith(".xlsx"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            raise MapError("That Excel file couldn't be opened. Save it as .xlsx or CSV and try again.")
        rows = []
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            head = next(it, None)
            if not head:
                continue
            keys = [str(h or "").strip() for h in head]
            rows += _rows_to_offices([dict(zip(keys, r)) for r in it], ws.title if len(wb.worksheets) > 1 else "")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:   # Excel's "CSV" on Windows saves in Windows-1252
            text = data.decode("cp1252", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = _rows_to_offices(_csv_dicts(text, dialect))
    if not rows:
        raise MapError("No offices found. The file needs a name column plus an address (or latitude/longitude) "
                       "for each office; a My Maps export (KML/KMZ) works as-is.")
    return rows[:MAX_IMPORT]


def match_office(row, key):
    """Find our office on Google (one Text Search Pro call). Returns (place_id, lat, lng, note)."""
    body = {"textQuery": " ".join(x for x in (row["name"], row.get("address") or "") if x), "pageSize": 1}
    if row.get("lat") is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": row["lat"], "longitude": row["lng"]},
                                           "radius": 2000.0}}
    j = _search(body, key, MATCH_FIELDS)
    hit = (j.get("places") or [None])[0]
    if not hit:
        raise MapError(f"Google found no match for {row['name']}.")
    lat, lng = _latlng(hit)
    if lat is None:
        raise MapError(f"Google's match for {row['name']} has no location.")
    note = ""
    if row.get("lat") is not None:
        off = miles_between(row["lat"], row["lng"], lat, lng)
        if off > MATCH_OK_MILES:
            note = f"Google's match is {off:.1f} mi from the pin in your file; check it."
    elif not row.get("address"):
        note = "Matched by name only; check it."
    return hit["id"], lat, lng, note


def import_offices(rows):
    key = server_key()
    if not key:
        raise MapError("Add the server API key in Map setup first.")
    added, problems = 0, []
    for row in rows:
        try:
            pid, lat, lng, note = match_office(row, key)
            add_office(row["name"], pid, lat, lng, row.get("address", ""), note)
            added += 1
        except (MapError, cx.ConnectorError) as e:
            problems.append(str(e))
    return added, problems


# ------------------------------------------------------------------ places storage
def purge_old(now=None):
    """Delete coordinates older than Google's 30-day limit. Place IDs and our labels stay."""
    cutoff = ((now or datetime.now()) - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    with db.conn() as c:
        n = c.execute("UPDATE map_places SET lat=NULL, lng=NULL, coords_at=NULL "
                      "WHERE coords_at IS NOT NULL AND coords_at < ?", (cutoff,)).rowcount
        c.execute("UPDATE map_offices SET lat=NULL, lng=NULL WHERE coords_at < ?", (cutoff,))
    return n


def places():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM map_places")]


def _is_new(p, offs_by_id, prev):
    """Found at the latest refresh, near an office that was already on the map at the one before."""
    last = db.get_setting("map_last_refresh")
    if not prev or not last or p["first_seen"] != last:
        return False
    o = offs_by_id.get(p["office_id"])
    return bool(o and o["created_at"] <= prev)


def visible(p, miles=None):
    return (p["layer"] in LAYERS and p["lat"] is not None and p["office_id"] is not None
            and p["miles"] is not None and p["miles"] <= (miles or radius()) and p.get("ranked", 1) != 0)


def counts():
    offs = {o["id"]: o for o in offices()}
    prev = db.get_setting("map_prev_refresh")
    out = {oid: {"referrer": 0, "competitor": 0, "offers_ortho": 0, "ours": 0, "new": 0, "gone": 0} for oid in offs}
    miles = radius()
    for p in places():
        if not visible(p, miles) or p["office_id"] not in out:
            continue
        c = out[p["office_id"]]
        if p["gone_at"]:
            c["gone"] += 1
            continue
        c[p["layer"]] += 1
        c["offers_ortho"] += p["offers_ortho"]
        c["new"] += _is_new(p, offs, prev)
    return out


def map_data():
    """Everything the map page draws: IDs, coordinates, our labels. No Google names/addresses."""
    offs = offices()
    by_id = {o["id"]: o for o in offs}
    prev, miles = db.get_setting("map_prev_refresh"), radius()
    return {"radius": miles,
            "offices": [{"id": o["id"], "label": o["label"], "place_id": o["place_id"], "lat": o["lat"],
                         "lng": o["lng"]} for o in offs if o["lat"] is not None],
            "places": [{"id": p["place_id"], "layer": p["layer"], "ortho": bool(p["offers_ortho"]),
                        "office": p["office_id"], "mi": round(p["miles"], 1), "lat": p["lat"], "lng": p["lng"],
                        "new": _is_new(p, by_id, prev), "gone": (p["gone_at"] or "")[:10] or None,
                        "since": (p["first_seen"] or "")[:10]}
                       for p in places() if visible(p, miles)]}


# ------------------------------------------------------------------ refresh
class _Budget:
    def __init__(self, limit):
        self.limit, self.pro, self.essentials, self.capped = limit, 0, 0, False

    def take(self):
        if self.pro + self.essentials >= self.limit:
            self.capped = True
            return False
        return True


def refresh(now=None):
    """Sweep for place IDs (free), label new ones, renew known locations, mark ones no longer listed."""
    key = server_key()
    if not key:
        raise MapError("Add the server API key in Map setup first.")
    offs = offices()
    if not offs:
        raise MapError("Add at least one of our offices first.")
    if not _lock.acquire(blocking=False):
        raise MapError("A refresh is already running. Check back in a few minutes.")
    started = time.time()
    began = db.now()
    pending = {"new": [], "upd": []}
    saved = {"new": 0, "upd": 0, "new_layer": 0}
    stamp = None

    def flush():
        if not (pending["new"] or pending["upd"]):
            return
        with db.conn() as c:
            c.executemany("INSERT INTO map_places(layer, offers_ortho, office_id, miles, lat, lng, coords_at, "
                          "brands_key, last_seen, place_id, first_seen, gone_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL) "
                          "ON CONFLICT(place_id) DO UPDATE SET layer=excluded.layer, offers_ortho=excluded.offers_ortho, "
                          "office_id=excluded.office_id, miles=excluded.miles, lat=excluded.lat, lng=excluded.lng, "
                          "coords_at=excluded.coords_at, brands_key=excluded.brands_key, last_seen=excluded.last_seen, "
                          "gone_at=NULL", [r + (stamp,) for r in pending["new"]])
            c.executemany("UPDATE map_places SET layer=?, offers_ortho=?, office_id=?, miles=?, lat=?, lng=?, "
                          "coords_at=?, brands_key=?, last_seen=?, gone_at=NULL WHERE place_id=?", pending["upd"])
        saved["new"] += len(pending["new"])
        saved["upd"] += len(pending["upd"])
        saved["new_layer"] += sum(1 for r in pending["new"] if r[0] in LAYERS)
        pending["new"].clear()
        pending["upd"].clear()

    try:
        _set_status(busy=True, started=began, step="Finding our offices on Google")
        moment = now or datetime.now()
        stamp = moment.isoformat(timespec="seconds")
        renew_before = (moment - timedelta(days=RENEW_AFTER_DAYS)).isoformat(timespec="seconds")
        miles, brands = radius(), brand_terms()
        bkey = brands_key(brands)
        budget = _Budget(max_requests())
        for o in offs:   # office locations (Essentials)
            lat, lng = _latlng(_details(o["place_id"], key, RENEW_FIELDS))
            if lat is None:
                raise MapError(f"Google didn't return a location for {o['label']}.")
            budget.essentials += 1
            o["lat"], o["lng"] = lat, lng
            with db.conn() as c:
                c.execute("UPDATE map_offices SET lat=?, lng=?, coords_at=? WHERE id=?", (lat, lng, stamp, o["id"]))

        s = Searcher(key, progress=lambda n: _set_status(
            busy=True, started=began, step=f"Searching around our offices ({n:,} searches so far)"))
        office_ids = {o["place_id"] for o in offs}
        known = {p["place_id"]: p for p in places()}
        new_rows, updates = pending["new"], pending["upd"]
        found, handled, counter = {}, set(), {"skipped": 0}
        limit = per_office()

        def nearest(lat, lng):
            o = min(offs, key=lambda o: miles_between(o["lat"], o["lng"], lat, lng))
            return o["id"], miles_between(o["lat"], o["lng"], lat, lng)

        def handle(pid, queries):
            if pid in office_ids:
                return
            k = known.get(pid)
            if k and k["layer"] == "skip" and k["brands_key"] == bkey:
                updates.append((k["layer"], 0, k["office_id"], k["miles"], k["lat"], k["lng"], k["coords_at"],
                                bkey, stamp, pid))
                return
            relabel = not k or k["brands_key"] != bkey
            if not relabel and k["lat"] is not None and k["coords_at"] and k["coords_at"] >= renew_before:
                lat, lng, coords_at, layer = k["lat"], k["lng"], k["coords_at"], k["layer"]
            elif not budget.take():
                counter["skipped"] += 1
                if k:   # keep what we have; it's still listed
                    updates.append((k["layer"], k["offers_ortho"], k["office_id"], k["miles"], k["lat"], k["lng"],
                                    k["coords_at"], k["brands_key"], stamp, pid))
                return
            elif relabel:   # never seen, or our brand names changed: label it (Pro)
                j = _details(pid, key, NEW_FIELDS)
                budget.pro += 1
                layer = classify(j, brands)
                lat, lng = _latlng(j)
                coords_at = stamp if lat is not None else None
            else:           # known: just renew the location (Essentials)
                lat, lng = _latlng(_details(pid, key, RENEW_FIELDS))
                budget.essentials += 1
                layer, coords_at = k["layer"], stamp if lat is not None else None
            office_id = d = None
            if lat is not None:
                office_id, d = nearest(lat, lng)
                if d < SAME_SPOT_MILES:
                    layer = "skip"   # one of our own offices under another listing
            ortho = int(layer == "referrer" and "orthodontist" in queries)
            row = (layer, ortho, office_id, d, lat, lng, coords_at, bkey, stamp, pid)
            (updates if k else new_rows).append(row)
            known[pid] = dict(place_id=pid, layer=layer, offers_ortho=ortho, office_id=office_id, miles=d, lat=lat,
                              lng=lng, coords_at=coords_at, brands_key=bkey, gone_at=None, last_seen=stamp)

        def process():
            todo = [pid for pid in found if pid not in handled]
            _set_status(busy=True, started=began, step=f"Found {len(found):,} places; looking up {len(todo):,} of them")
            for pid in todo:
                handled.add(pid)
                handle(pid, found[pid])
                if len(new_rows) + len(updates) >= 200:   # save as we go, so an interruption loses little
                    flush()
                    _set_status(busy=True, started=began, step=f"Looked up {budget.pro + budget.essentials:,} places "
                                f"(limit {budget.limit:,}); {len(found):,} found so far")
            flush()

        def add(ids, q):
            for pid in ids:
                found.setdefault(pid, set()).add(q)

        # 1. Orthodontists: everything within the radius (this search also turns up dentists offering braces).
        for o in offs:
            add(s.sweep("orthodontist", o["lat"], o["lng"], miles), "orthodontist")
        process()
        # 2. Dental practices: everything within the radius, or widen around each office until it has the closest `limit`.
        extent = {}
        size = {o["id"]: (miles if not limit else min(START_MILES, miles)) for o in offs}
        waiting = list(offs)
        while waiting and not (s.capped or budget.capped):
            for o in waiting:
                add(s.sweep("dentist", o["lat"], o["lng"], size[o["id"]]), "dentist")
            process()
            refs = [p for p in known.values() if p["layer"] == "referrer" and p["lat"] is not None and not p["gone_at"]]
            still = []
            for o in waiting:
                n = sum(1 for p in refs if miles_between(o["lat"], o["lng"], p["lat"], p["lng"]) <= size[o["id"]])
                if limit and n < limit and size[o["id"]] < miles:
                    size[o["id"]] = min(miles, size[o["id"]] * GROW)
                    still.append(o)
                else:
                    extent[o["id"]] = size[o["id"]]
            waiting = still
        for o in waiting:
            extent[o["id"]] = 0   # stopped early: we can't say anything around this office is gone

        complete = not (s.capped or budget.capped)
        skipped = counter["skipped"]
        gone = 0
        if complete:   # only a finished sweep can say a place is no longer listed, and only where we looked
            gone_ids = []
            for p in places():
                if (p["gone_at"] or p["layer"] not in LAYERS or p["lat"] is None or not p["last_seen"]
                        or p["last_seen"] >= stamp):
                    continue
                if p["layer"] == "competitor":
                    looked = any(miles_between(o["lat"], o["lng"], p["lat"], p["lng"]) <= miles for o in offs)
                else:
                    looked = any(miles_between(o["lat"], o["lng"], p["lat"], p["lng"]) <= extent.get(o["id"], 0)
                                 for o in offs)
                if looked:
                    gone_ids.append(p["place_id"])
            with db.conn() as c:
                c.executemany("UPDATE map_places SET gone_at=? WHERE place_id=?", [(stamp, i) for i in gone_ids])
            gone = len(gone_ids)
        _rank(offs, limit, miles)
        last = db.get_setting("map_last_refresh")
        if last:
            db.set_setting("map_prev_refresh", last)
        db.set_setting("map_last_refresh", stamp)
        res = {"busy": False, "ok": True, "at": stamp, "searches": s.requests, "pro": budget.pro,
               "essentials": budget.essentials, "capped": not complete, "skipped": skipped,
               "new": saved["new_layer"], "gone": gone,
               "seconds": round(time.time() - started)}
        _set_status(**res)
        return res
    except Exception as e:   # always clear "busy", keep what was found, and record why
        try:
            if stamp:
                flush()
        except Exception:
            pass
        kept = saved["new"] + saved["upd"]
        res = {"busy": False, "ok": False, "at": db.now(), "error": str(e)[:300], "kept": kept}
        _set_status(**res)
        raise MapError(str(e))
    finally:
        _lock.release()


def _rank(offs, limit, miles):
    """Mark which dental practices show: the closest `limit` to each office (within the radius), or all of them."""
    rows = [p for p in places() if p["layer"] == "referrer" and p["lat"] is not None]
    keep = set()
    if not limit:
        keep = {p["place_id"] for p in rows}
    else:
        live = [p for p in rows if not p["gone_at"]]
        for o in offs:
            if o.get("lat") is None:
                continue
            near = sorted((miles_between(o["lat"], o["lng"], p["lat"], p["lng"]), p["place_id"]) for p in live)
            keep.update(pid for d, pid in near[:limit] if d <= miles)
        keep.update(p["place_id"] for p in rows if p["gone_at"] and p["office_id"] is not None
                    and p["miles"] is not None and p["miles"] <= miles and p.get("ranked", 1))
    with db.conn() as c:
        c.execute("UPDATE map_places SET ranked=1 WHERE layer != 'referrer'")
        c.executemany("UPDATE map_places SET ranked=? WHERE place_id=?",
                      [(int(p["place_id"] in keep), p["place_id"]) for p in rows])


def run_in_background(fn, *args):
    def run():
        try:
            fn(*args)
        except MapError:
            pass   # status already records the error
    threading.Thread(target=run, name="map-work", daemon=True).start()


def refresh_in_background():
    run_in_background(refresh)


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
