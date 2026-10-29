"""Private and charter schools near our offices, for the Map.

Data: the U.S. Department of Education's NCES EDGE program, through its public ArcGIS services:
  - Private School Locations (Private School Universe Survey): every private school, with coordinates.
  - Public School Characteristics (Common Core of Data): every public school with its charter flag; we keep
    the charter schools.
Free, no key, public domain. For each of our offices we ask for schools within the Map radius, keep the
nearest office for each school, and refresh monthly (NCES updates yearly, private schools every two years).
"""
import json
import math
import os
import threading
import time
import urllib.parse

from . import connectors as cx, db, places

BASE = "https://services1.arcgis.com/Ua5sjt3LWTPigjyD/arcgis/rest/services"
PRIVATE_URL = os.environ.get("NCES_PRIVATE_URL", f"{BASE}/Private_School_Locations_Current/FeatureServer/0/query")
PUBLIC_URL = os.environ.get("NCES_PUBLIC_URL", f"{BASE}/School_Characteristics_Current/FeatureServer/1/query")
KINDS = {"school_private": "Private schools", "school_charter": "Charter schools"}
PAGE = 1000
REFRESH_DAYS = 30
_lock = threading.Lock()


class SchoolError(RuntimeError):
    pass


def init():
    with db.conn() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS schools (
  key TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  street TEXT DEFAULT '', city TEXT DEFAULT '', state TEXT DEFAULT '', zip TEXT DEFAULT '',
  lat REAL, lng REAL,
  grades TEXT DEFAULT '',
  enrollment INTEGER,
  office_id INTEGER,
  miles REAL,
  year TEXT DEFAULT '',
  seen_at TEXT
);
CREATE INDEX IF NOT EXISTS schools_office ON schools(office_id);""")


def status():
    try:
        return json.loads(db.get_setting("schools_status") or "{}")
    except ValueError:
        return {}


def _miles(a_lat, a_lng, b_lat, b_lng):
    r, t = 3958.8, math.radians
    h = math.sin(t(b_lat - a_lat) / 2) ** 2 + math.cos(t(a_lat)) * math.cos(t(b_lat)) * math.sin(t(b_lng - a_lng) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _get(url, params):
    q = urllib.parse.urlencode({**params, "f": "json"})
    try:
        j = cx._http(f"{url}?{q}", timeout=90)
    except cx.ConnectorError as e:
        raise SchoolError(f"NCES school data didn't answer: {e}")
    if isinstance(j, dict) and j.get("error"):
        raise SchoolError(f"NCES school data: {j['error'].get('message') or j['error']}")
    return j


PUBLIC_FIELDS = "NCESSCH,SCH_NAME,LSTREET1,LCITY,LSTATE,LZIP,CHARTER_TEXT,GSLO,GSHI,TOTAL,SY_STATUS_TEXT,VIRTUAL,SURVYEAR"


def near(url, lat, lng, miles, where="1=1", fields="*"):
    """Every feature within `miles` of the point (paged)."""
    out, offset = [], 0
    while True:
        j = _get(url, {"where": where, "geometry": f"{lng},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
                       "spatialRel": "esriSpatialRelIntersects", "distance": miles, "units": "esriSRUnit_StatuteMile",
                       "outFields": fields, "outSR": 4326, "returnGeometry": "true", "resultOffset": offset,
                       "resultRecordCount": PAGE})
        feats = j.get("features") or []
        out += feats
        if not j.get("exceededTransferLimit") or not feats or offset > 20000:
            return out
        offset += len(feats)


def _a(attrs, *names):
    for n in names:
        v = attrs.get(n)
        if v not in (None, "", " "):
            return v
    return ""


def _grade(g):
    g = str(g or "").strip().upper()
    return {"PK": "Pre-K", "KG": "K", "UG": "", "N": "", "M": ""}.get(g, g.lstrip("0") or g)


def is_charter(attrs):
    """None when the data has no charter field at all (so a renamed field doesn't silently show nothing)."""
    for n in ("CHARTER_TEXT", "CHARTR", "CHARTER", "CHARTER_SCH"):
        if n in attrs:
            return str(attrs[n]).strip().lower() in ("yes", "y", "1", "true", "charter")
    return None


def closed(attrs):
    """Closed, not yet open, or online-only (no building to visit)."""
    s = str(_a(attrs, "SY_STATUS_TEXT", "UPDATED_STATUS_TEXT")).lower()
    v = str(attrs.get("VIRTUAL") or "").lower()
    return "closed" in s or "inactive" in s or "future" in s or v in ("full virtual", "exclusively virtual")


def parse(f, kind):
    a = {k.upper(): v for k, v in (f.get("attributes") or {}).items()}
    g = f.get("geometry") or {}
    lat = g.get("y") if g.get("y") is not None else _a(a, "LAT", "LATCOD", "LATITUDE")
    lng = g.get("x") if g.get("x") is not None else _a(a, "LON", "LONCOD", "LONGITUDE")
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    ident = _a(a, "PPIN", "NCESSCH", "NCESSCH_NUM", "SCHID")
    name = str(_a(a, "NAME", "SCH_NAME", "SCHNAM")).strip()
    if not ident or not name:
        return None
    lo, hi = _grade(_a(a, "GSLO", "LOGRADE", "LOW_GRADE")), _grade(_a(a, "GSHI", "HIGRADE", "HIGH_GRADE"))
    enr = _a(a, "TOTAL", "MEMBER", "ENROLLMENT", "NUMSTUDS")
    try:
        enr = int(float(enr)) if enr != "" and float(enr) >= 0 else None
    except (TypeError, ValueError):
        enr = None
    return {"key": f"{'pss' if kind == 'school_private' else 'ccd'}:{ident}", "kind": kind, "name": name.title() if name.isupper() else name,
            "street": str(_a(a, "STREET", "LSTREET1", "LSTREE")).title(), "city": str(_a(a, "CITY", "LCITY")).title(),
            "state": str(_a(a, "STATE", "LSTATE", "STABR")).strip(), "zip": str(_a(a, "ZIP", "LZIP"))[:5], "lat": lat, "lng": lng,
            "grades": f"{lo}–{hi}" if lo and hi and lo != hi else (lo or hi), "enrollment": enr,
            "year": str(_a(a, "SCHOOLYEAR", "SURVYEAR", "SY"))}


def refresh(offices=None):
    """Fetch schools around every office. Returns the status dict."""
    if not _lock.acquire(blocking=False):
        raise SchoolError("A school refresh is already running.")
    try:
        _set_status(busy=True)
        offs = [o for o in (offices or places.offices()) if o.get("lat") is not None]
        if not offs:
            raise SchoolError("Add our offices to the Map first.")
        miles = places.radius()
        found = {}
        no_charter_field = True
        for o in offs:
            for kind, url in (("school_private", PRIVATE_URL), ("school_charter", PUBLIC_URL)):
                if kind == "school_charter":
                    try:      # ask NCES for charters only; if the field's been renamed, take all and sort them here
                        feats = near(url, o["lat"], o["lng"], miles, "CHARTER_TEXT='Yes'", PUBLIC_FIELDS)
                    except SchoolError:
                        feats = near(url, o["lat"], o["lng"], miles)
                else:
                    feats = near(url, o["lat"], o["lng"], miles)
                for f in feats:
                    attrs = {k.upper(): v for k, v in (f.get("attributes") or {}).items()}
                    if kind == "school_charter":
                        ch = is_charter(attrs)
                        if ch is not None:
                            no_charter_field = False
                        if not ch or closed(attrs):
                            continue
                    s = parse(f, kind)
                    if not s:
                        continue
                    d = _miles(o["lat"], o["lng"], s["lat"], s["lng"])
                    if d > miles + 0.05:
                        continue
                    if s["key"] not in found or d < found[s["key"]]["miles"]:
                        found[s["key"]] = {**s, "office_id": o["id"], "miles": round(d, 2)}
                time.sleep(0.2)
        if no_charter_field and not any(s["kind"] == "school_charter" for s in found.values()):
            raise SchoolError("The NCES public school data no longer has a charter flag the hub recognises; "
                              "private schools weren't changed either. Tell whoever maintains the hub.")
        stamp = db.now()
        with db.conn() as c:
            c.execute("DELETE FROM schools")
            c.executemany("INSERT INTO schools(key, kind, name, street, city, state, zip, lat, lng, grades, enrollment, "
                          "office_id, miles, year, seen_at) VALUES (:key, :kind, :name, :street, :city, :state, :zip, :lat, "
                          ":lng, :grades, :enrollment, :office_id, :miles, :year, :seen)",
                          [{**s, "seen": stamp} for s in found.values()])
        st = {"at": stamp, "private": sum(1 for s in found.values() if s["kind"] == "school_private"),
              "charter": sum(1 for s in found.values() if s["kind"] == "school_charter"), "offices": len(offs),
              "busy": False, "error": ""}
        db.set_setting("schools_status", json.dumps(st))
        return st
    except SchoolError as e:
        _set_status(busy=False, error=str(e))
        raise
    except Exception as e:
        _set_status(busy=False, error=f"School refresh failed: {e}")
        raise SchoolError(str(e))
    finally:
        _lock.release()


def _set_status(**kw):
    db.set_setting("schools_status", json.dumps({**status(), **kw}))


def refresh_in_background():
    threading.Thread(target=lambda: _quiet(refresh), name="schools-refresh", daemon=True).start()


def _quiet(fn):
    try:
        fn()
    except Exception:
        pass


def due():
    from datetime import datetime, timedelta
    st = status()
    if not st.get("at"):
        return bool(places.offices())
    try:
        return datetime.now() - datetime.fromisoformat(st["at"]) > timedelta(days=REFRESH_DAYS)
    except ValueError:
        return True


def start_scheduler():
    def loop():
        time.sleep(240)
        while True:
            if due():
                _quiet(refresh)
            time.sleep(24 * 3600)
    threading.Thread(target=loop, name="schools-monthly", daemon=True).start()


def listing(office_id=None, kind=None):
    miles = places.radius()
    q, args = "SELECT * FROM schools WHERE miles <= ?", [miles]
    if office_id:
        q, args = q + " AND office_id = ?", args + [office_id]
    if kind in KINDS:
        q, args = q + " AND kind = ?", args + [kind]
    with db.conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY office_id, miles", args)]


def map_data():
    return {"schools": [{"k": s["key"], "layer": s["kind"], "name": s["name"],
                         "addr": ", ".join(x for x in (s["street"], s["city"], " ".join(y for y in (s["state"], s["zip"]) if y)) if x),
                         "grades": s["grades"], "enr": s["enrollment"], "office": s["office_id"], "mi": round(s["miles"], 1),
                         "lat": s["lat"], "lng": s["lng"]} for s in listing()],
            "schools_status": {k: v for k, v in status().items() if k in ("at", "private", "charter", "error", "busy")}}


def counts():
    out = {}
    for s in listing():
        out.setdefault(s["office_id"], {"school_private": 0, "school_charter": 0})[s["kind"]] += 1
    return out


def export_xlsx(office_id=None, kind=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    offs = {o["id"]: o["label"] for o in places.offices()}
    rows = listing(office_id, kind)
    wb = Workbook()
    ws = wb.active
    ws.title = "Schools"
    ws.append(["Our office", "Miles", "Type", "School", "Street", "City", "State", "ZIP", "Grades", "Students", "NCES year"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for s in rows:
        ws.append([offs.get(s["office_id"], ""), s["miles"], "Private" if s["kind"] == "school_private" else "Charter",
                   s["name"], s["street"], s["city"], s["state"], s["zip"], s["grades"], s["enrollment"], s["year"]])
    for col, w in zip("ABCDEFGHIJK", (26, 7, 9, 40, 30, 18, 6, 7, 9, 9, 10)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), len(rows)
