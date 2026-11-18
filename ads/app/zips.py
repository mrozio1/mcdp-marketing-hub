"""Target zip codes around each office: median household income and how many households have children, from the
U.S. Census Bureau (American Community Survey 5-year estimates, by ZIP Code Tabulation Area), for aiming ads at
mid- to high-income families.

Sources (free, public domain; the Census API needs a free key, kept in the hub's settings):
  - ACS 5-year API: median household income (B19013), households and households with someone under 18 (B11005),
    population (B01003), for every ZCTA in one request.
  - Census Gazetteer: each ZCTA's centre point, to work out which zips are near which office.
Refreshed when asked and once a year after that (the Census publishes new 5-year numbers each December).

Tiers, per office (zips within ZIP_MILES with enough households): the top third by income is "Target"
(where the ads aim), the middle third "Also good", the rest "Other". The lists copy straight into Meta's and
Google's location targeting.
"""
from __future__ import annotations

import io
import json
import math
import os
import threading
import urllib.request
import zipfile

from . import db, places

ACS_YEAR = os.environ.get("ACS_YEAR", "2023")
ACS_URL = os.environ.get("ACS_URL", f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5")
GAZ_URL = os.environ.get("GAZ_URL", f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{ACS_YEAR}_Gazetteer/"
                                    f"{ACS_YEAR}_Gaz_zcta_national.zip")
VARS = {"B19013_001E": "income", "B11005_001E": "households", "B11005_002E": "hh_kids", "B01003_001E": "pop"}
DEFAULT_MILES = 15
MIN_HOUSEHOLDS = 300
REFRESH_DAYS = 365
_lock = threading.Lock()


class ZipError(RuntimeError):
    pass


def init():
    with db.conn() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS zip_stats (
  zip TEXT PRIMARY KEY,
  lat REAL, lng REAL,
  income INTEGER,
  households INTEGER,
  hh_kids INTEGER,
  pop INTEGER,
  year TEXT
);""")


def status():
    try:
        return json.loads(db.get_setting("zips_status") or "{}")
    except ValueError:
        return {}


def _set_status(**kw):
    db.set_setting("zips_status", json.dumps({**status(), **kw}))


def has_key():
    return bool(db.get_setting("census_key") or os.environ.get("CENSUS_KEY"))


def miles_setting():
    try:
        return max(3, min(40, int(db.get_setting("zip_miles") or DEFAULT_MILES)))
    except ValueError:
        return DEFAULT_MILES


def _fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "MCDP Hub (ads.mcdp.com)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        raise ZipError(f"The Census Bureau didn't answer ({e}). Try again later.")


# ------------------------------------------------------------------ parsing (kept apart so it can be tested)
def parse_acs(raw):
    """ACS API answer (a JSON table: header row, then rows) → {zip: {income, households, hh_kids, pop}}."""
    try:
        table = json.loads(raw)
        head = table[0]
    except (ValueError, IndexError, TypeError):
        raise ZipError("The Census answer wasn't the table expected.")
    zi = head.index("zip code tabulation area")
    cols = {VARS[h]: i for i, h in enumerate(head) if h in VARS}
    out = {}
    for row in table[1:]:
        rec = {}
        for k, i in cols.items():
            try:
                v = int(float(row[i]))
            except (TypeError, ValueError):
                v = None
            rec[k] = v if v is not None and v >= 0 else None      # the Census uses -666666666 for "no estimate"
        out[row[zi]] = rec
    return out


def parse_gazetteer(raw):
    """Gazetteer zip (or the text inside it) → {zip: (lat, lng)}."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next(n for n in z.namelist() if n.endswith(".txt"))
            raw = z.read(name)
    lines = raw.decode("utf-8", "replace").splitlines()
    head = [h.strip() for h in lines[0].split("\t")]
    gi, la, lo = head.index("GEOID"), head.index("INTPTLAT"), head.index("INTPTLONG")
    out = {}
    for ln in lines[1:]:
        p = [x.strip() for x in ln.split("\t")]
        try:
            out[p[gi]] = (float(p[la]), float(p[lo]))
        except (IndexError, ValueError):
            continue
    return out


def store(acs, gaz, year=ACS_YEAR):
    rows = [{"zip": z, "lat": gaz[z][0], "lng": gaz[z][1], "year": year, **{k: v.get(k) for k in VARS.values()}}
            for z, v in acs.items() if z in gaz]
    with db.conn() as c:
        c.execute("DELETE FROM zip_stats")
        c.executemany("INSERT INTO zip_stats(zip, lat, lng, income, households, hh_kids, pop, year) "
                      "VALUES (:zip, :lat, :lng, :income, :households, :hh_kids, :pop, :year)", rows)
    return len(rows)


def refresh():
    if not _lock.acquire(blocking=False):
        raise ZipError("Already fetching the Census numbers.")
    try:
        _set_status(busy=True, error="")
        key = db.get_setting("census_key") or os.environ.get("CENSUS_KEY", "")
        if not key:
            raise ZipError("Add the free Census API key first (below).")
        raw = _fetch(f"{ACS_URL}?get={','.join(VARS)}&for=zip%20code%20tabulation%20area:*&key={key}")
        if raw.lstrip()[:1] != b"[":
            low = raw[:2000].decode("utf-8", "replace").lower()
            raise ZipError("The Census Bureau didn't accept the API key. Check it was copied whole, and that you clicked "
                           "the link in their email to activate it." if "key" in low else
                           "The Census answer wasn't the table expected. Try again later.")
        acs = parse_acs(raw)
        gaz = parse_gazetteer(_fetch(GAZ_URL))
        n = store(acs, gaz)
        if not n:
            raise ZipError("The Census numbers came back empty.")
        _set_status(busy=False, at=db.now(), zips=n, year=ACS_YEAR, error="")
        return n
    except ZipError as e:
        _set_status(busy=False, error=str(e))
        raise
    except Exception as e:
        _set_status(busy=False, error=f"Census refresh failed: {e}")
        raise ZipError(str(e))
    finally:
        _lock.release()


def refresh_in_background():
    def run():
        try:
            refresh()
        except Exception:
            pass
    threading.Thread(target=run, name="zips-refresh", daemon=True).start()


def due():
    from datetime import datetime, timedelta
    st = status()
    if not st.get("at"):
        return False                      # first fetch is started from the page
    try:
        return datetime.now() - datetime.fromisoformat(st["at"]) > timedelta(days=REFRESH_DAYS)
    except ValueError:
        return True


# ------------------------------------------------------------------ per office
def _miles(a_lat, a_lng, b_lat, b_lng):
    r, t = 3958.8, math.radians
    h = math.sin(t(b_lat - a_lat) / 2) ** 2 + math.cos(t(a_lat)) * math.cos(t(b_lat)) * math.sin(t(b_lng - a_lng) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _all():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM zip_stats WHERE income IS NOT NULL AND lat IS NOT NULL")]


def near(lat, lng, miles, stats=None):
    out = []
    for z in stats if stats is not None else _all():
        if abs(z["lat"] - lat) > miles / 60 or abs(z["lng"] - lng) > miles / 40:
            continue                                   # quick box first
        d = _miles(lat, lng, z["lat"], z["lng"])
        if d <= miles:
            out.append({**z, "miles": round(d, 1),
                        "kids_share": (z["hh_kids"] / z["households"]) if z.get("households") else None})
    return out


def tiers(zs):
    """Top third by income → 'target', middle third → 'good', rest → 'other' (only zips with enough households)."""
    ok = sorted([z for z in zs if (z.get("households") or 0) >= MIN_HOUSEHOLDS], key=lambda z: -(z["income"] or 0))
    n = len(ok)
    for i, z in enumerate(ok):
        z["tier"] = "target" if i < math.ceil(n / 3) else "good" if i < math.ceil(2 * n / 3) else "other"
    return ok


def for_offices(offices=None, miles=None):
    """[{'office', 'zips': [...], 'target': [...], 'families': n}] for each of our offices with coordinates."""
    miles = miles or miles_setting()
    stats = _all()
    out = []
    for o in offices if offices is not None else places.offices():
        if o.get("lat") is None:
            continue
        zs = tiers(near(o["lat"], o["lng"], miles, stats))
        tgt = [z for z in zs if z["tier"] == "target"]
        out.append({"office": o, "zips": zs, "target": tgt, "good": [z for z in zs if z["tier"] == "good"],
                    "families": sum(z.get("hh_kids") or 0 for z in tgt),
                    "income_floor": min((z["income"] for z in tgt), default=None)})
    return out


def export_xlsx(rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Target zips"
    ws.append(["Our office", "ZIP", "Tier", "Miles", "Median household income", "Households",
               "Households with children", "Share with children", "Population"])
    for c in ws[1]:
        c.font = Font(bold=True)
    label = {"target": "Target", "good": "Also good", "other": "Other"}
    for r in rows:
        for z in r["zips"]:
            ws.append([r["office"]["label"], z["zip"], label[z["tier"]], z["miles"], z["income"], z["households"],
                       z["hh_kids"], round(z["kids_share"], 3) if z["kids_share"] is not None else None, z["pop"]])
    for col, w in zip("ABCDEFGHI", (28, 8, 10, 7, 14, 11, 14, 12, 11)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
