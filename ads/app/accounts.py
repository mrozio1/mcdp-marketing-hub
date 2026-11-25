"""Referral accounts: parent account (a practice brand) → location (Salesforce location account) → child accounts
(referring offices linked to that location in Salesforce, offices visited from the Hub, and nearby dental offices from
the NPI list as prospects).

Salesforce is read as it is: location accounts (accounts that have child accounts) under no parent. The brand level
lives in the Hub: each location is matched to a Hub practice (by name, changeable by an admin), and a practice can
point at a Salesforce account to open as its parent. Nothing about Salesforce's structure is changed from here.
Account notes are kept in the Hub and copied to Salesforce as a Note on that account.
"""
from __future__ import annotations

import json
import math
import re
import threading
from datetime import date, datetime, timedelta

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS acct_locations (
  sf_id TEXT PRIMARY KEY, name TEXT NOT NULL, practice_id INTEGER, manual INTEGER DEFAULT 0,
  street TEXT DEFAULT '', city TEXT DEFAULT '', state TEXT DEFAULT '', zip TEXT DEFAULT '',
  lat REAL, lng REAL, map_office_id INTEGER, children INTEGER DEFAULT 0, synced_at TEXT, gone_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS acct_children (
  sf_id TEXT PRIMARY KEY, name TEXT NOT NULL, location_sf_id TEXT NOT NULL, type TEXT DEFAULT '', phone TEXT DEFAULT '',
  street TEXT DEFAULT '', city TEXT DEFAULT '', state TEXT DEFAULT '', zip TEXT DEFAULT '',
  lat REAL, lng REAL, geo TEXT DEFAULT '', geo_key TEXT DEFAULT '', synced_at TEXT, gone_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS acct_children_loc ON acct_children(location_sf_id);
CREATE TABLE IF NOT EXISTS acct_notes (
  id INTEGER PRIMARY KEY, acct_key TEXT NOT NULL, practice_id INTEGER, text TEXT NOT NULL, by_id INTEGER, by_name TEXT,
  at TEXT NOT NULL, sf_note_id TEXT DEFAULT '', sf_status TEXT DEFAULT '', sf_error TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS acct_notes_k ON acct_notes(acct_key);
"""
PROSPECT_MILES = 3.0          # NPI dental offices within this of a location show as prospects
HUB_OFFICE_MILES = 15.0       # a visited office with no Salesforce account joins its brand's nearest location within this
STALE_DAYS = 90
_lock = threading.Lock()


class AcctError(Exception):
    pass


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


# ------------------------------------------------------------------ helpers
def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


STOP = {"orthodontics", "orthodontic", "ortho", "dental", "dentistry", "the", "of", "and", "pc", "pllc", "llc", "dds", "dr"}


def _words(s):
    return set(_norm(s).split()) - STOP


def miles(a_lat, a_lng, b_lat, b_lng):
    if None in (a_lat, a_lng, b_lat, b_lng):
        return None
    r = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def short_date(d):
    """'2026-11-22' -> 'Nov 22' (this year) or 'Nov 22, 2025'."""
    try:
        x = date.fromisoformat((d or "")[:10])
    except ValueError:
        return d or ""
    return x.strftime("%b ") + str(x.day) + ("" if x.year == date.today().year else f", {x.year}")


def parents():
    try:
        return {int(k): v for k, v in json.loads(db.get_setting("acct_parents") or "{}").items()}
    except (ValueError, TypeError):
        return {}


def set_parent(pid, sf_id):
    p = parents()
    sf_id = (sf_id or "").strip()
    if sf_id and not re.fullmatch(r"[A-Za-z0-9]{15,18}", sf_id):
        raise AcctError("That isn't a Salesforce account ID (15 or 18 letters and numbers).")
    if sf_id:
        p[pid] = sf_id
    else:
        p.pop(pid, None)
    db.set_setting("acct_parents", json.dumps({str(k): v for k, v in p.items()}))


def status():
    try:
        return json.loads(db.get_setting("acct_sync") or "{}")
    except ValueError:
        return {}


def _set_status(**kw):
    db.set_setting("acct_sync", json.dumps({**status(), **kw}))


# ------------------------------------------------------------------ sync from Salesforce
def _query_all(soql):
    from . import salesforce
    got = salesforce.api("GET", "/query", params={"q": soql})
    rows = list(got.get("records", []))
    while got.get("nextRecordsUrl"):
        nxt = got["nextRecordsUrl"]
        got = salesforce.api("GET", nxt if nxt.startswith("http") else salesforce.instance_url().rstrip("/") + nxt, absolute=True)
        rows += got.get("records", [])
    return rows


def _match_practice(name, practices):
    """'Havens Orthodontics Canton' -> Havens Orthodontics; 'Orthodontics Specialists Grand Blanc' -> its practice."""
    n = _norm(name)
    best = None
    for p in practices:
        pn = _norm(p["name"])
        if pn and (n == pn or n.startswith(pn + " ")):
            if not best or len(pn) > len(_norm(best["name"])):
                best = p
    if best:
        return best["id"]
    w = _words(name)
    scored = sorted(((len(w & _words(p["name"])), p["id"]) for p in practices), reverse=True)
    if scored and scored[0][0] and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


def sync(by="Hub"):
    """Read every child account with a parent from Salesforce; store locations and children; place them on the map."""
    from . import salesforce
    if not salesforce.connected():
        raise AcctError("Salesforce isn't connected (Connections › Salesforce).")
    if not _lock.acquire(blocking=False):
        raise AcctError("A Salesforce account sync is already running.")
    try:
        _set_status(busy=True, step="Reading Salesforce accounts", error="")
        rows = _query_all("SELECT Id, Name, Type, Phone, BillingStreet, BillingCity, BillingState, BillingPostalCode, ParentId, "
                          "Parent.Name, Parent.BillingStreet, Parent.BillingCity, Parent.BillingState, Parent.BillingPostalCode "
                          "FROM Account WHERE ParentId != null")
        t = db.now()
        practices = db.practices(include_hidden=True)
        locs = {}
        for r in rows:
            p = r.get("Parent") or {}
            locs.setdefault(r["ParentId"], {"name": p.get("Name") or "", "street": p.get("BillingStreet") or "",
                                             "city": p.get("BillingCity") or "", "state": p.get("BillingState") or "",
                                             "zip": (p.get("BillingPostalCode") or "")[:10], "n": 0})["n"] += 1
        with db.conn() as c:
            have = {x["sf_id"]: dict(x) for x in c.execute("SELECT * FROM acct_locations")}
            for sid, l in locs.items():
                old = have.get(sid)
                pid = old["practice_id"] if old and old["manual"] else _match_practice(l["name"], practices)
                c.execute("INSERT INTO acct_locations(sf_id, name, practice_id, street, city, state, zip, children, synced_at, gone_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?, '') ON CONFLICT(sf_id) DO UPDATE SET name=excluded.name, "
                          "practice_id=CASE WHEN acct_locations.manual THEN acct_locations.practice_id ELSE excluded.practice_id END, "
                          "street=excluded.street, city=excluded.city, state=excluded.state, zip=excluded.zip, "
                          "children=excluded.children, synced_at=excluded.synced_at, gone_at=''",
                          (sid, l["name"], pid, l["street"], l["city"], l["state"], l["zip"], l["n"], t))
            c.execute("UPDATE acct_locations SET gone_at=? WHERE synced_at != ? AND gone_at = ''", (t, t))
            for r in rows:
                c.execute("INSERT INTO acct_children(sf_id, name, location_sf_id, type, phone, street, city, state, zip, synced_at, gone_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?, '') ON CONFLICT(sf_id) DO UPDATE SET name=excluded.name, "
                          "location_sf_id=excluded.location_sf_id, type=excluded.type, phone=excluded.phone, street=excluded.street, "
                          "city=excluded.city, state=excluded.state, zip=excluded.zip, synced_at=excluded.synced_at, gone_at=''",
                          (r["Id"], r.get("Name") or "", r["ParentId"], r.get("Type") or "", r.get("Phone") or "",
                           r.get("BillingStreet") or "", r.get("BillingCity") or "", r.get("BillingState") or "",
                           (r.get("BillingPostalCode") or "")[:10], t))
            c.execute("UPDATE acct_children SET gone_at=? WHERE synced_at != ? AND gone_at = ''", (t, t))
        _set_status(step="Placing accounts on the map")
        placed = geocode_missing()
        link_map_offices()
        _set_status(busy=False, step="", at=db.now(), by=by, locations=len(locs), children=len(rows), placed=placed)
        return {"locations": len(locs), "children": len(rows), "placed": placed}
    except AcctError:
        _set_status(busy=False, step="")
        raise
    except Exception as e:
        _set_status(busy=False, step="", error=str(e)[:300])
        raise AcctError(f"Salesforce sync failed: {e}")
    finally:
        _lock.release()


def sync_in_background(by="Hub"):
    def run():
        try:
            sync(by)
        except AcctError:
            pass
    threading.Thread(target=run, name="acct-sync", daemon=True).start()


def sync_if_stale(hours=24):
    from . import salesforce
    at = status().get("at") or ""
    if salesforce.connected() and not status().get("busy") and (not at or at < (datetime.now() - timedelta(hours=hours)).isoformat()):
        sync_in_background("Daily sync")


def geocode_missing(limit=5000):
    """Census geocoder for addresses not placed yet (or changed); the ZIP's centre when it can't match."""
    from . import referrals
    with db.conn() as c:
        kids = [dict(r) for r in c.execute("SELECT sf_id, street, city, state, zip, geo_key FROM acct_children WHERE gone_at=''")]
        locs = [dict(r) for r in c.execute("SELECT sf_id, street, city, state, zip FROM acct_locations WHERE lat IS NULL")]
    todo = {}
    for k in kids:
        key = "|".join((k["street"], k["city"], k["state"], k["zip"])).lower()
        if key != k["geo_key"] and (k["street"] or k["zip"]):
            todo["c:" + k["sf_id"]] = (k["street"], k["city"], k["state"] or "MI", k["zip"])
    for l in locs:
        if l["street"] or l["zip"]:
            todo["l:" + l["sf_id"]] = (l["street"], l["city"], l["state"] or "MI", l["zip"])
    items = dict(list(todo.items())[:limit])
    found = referrals.geocode(items) if items else {}
    n = 0
    with db.conn() as c:
        for k, (street, city, state, z) in items.items():
            pt, how = found.get(k), "address"
            if not pt and z:
                try:
                    zp = referrals.zip_point(z[:5])
                    pt, how = (zp, "zip") if zp and zp[0] is not None else (None, "")
                except Exception:
                    pt = None
            kind, sid = k.split(":", 1)
            if kind == "c":
                c.execute("UPDATE acct_children SET lat=?, lng=?, geo=?, geo_key=? WHERE sf_id=?",
                          (pt[0] if pt else None, pt[1] if pt else None, how if pt else "", "|".join((street, city, state, z)).lower(), sid))
            elif pt:
                c.execute("UPDATE acct_locations SET lat=?, lng=? WHERE sf_id=?", (pt[0], pt[1], sid))
            n += bool(pt)
    return n


def link_map_offices():
    """Each location -> the nearest of our map offices (within 2 miles), for the map's 'Around' filter."""
    from . import places
    offs = [o for o in places.offices() if o.get("lat") is not None]
    with db.conn() as c:
        for l in [dict(r) for r in c.execute("SELECT sf_id, lat, lng FROM acct_locations")]:
            best = min(((miles(l["lat"], l["lng"], o["lat"], o["lng"]) or 1e9, o["id"]) for o in offs), default=(1e9, None))
            c.execute("UPDATE acct_locations SET map_office_id=? WHERE sf_id=?", (best[1] if best[0] <= 2 else None, l["sf_id"]))


def set_location_practice(sf_id, pid):
    with db.conn() as c:
        c.execute("UPDATE acct_locations SET practice_id=?, manual=1 WHERE sf_id=?", (pid or None, sf_id))


# ------------------------------------------------------------------ reading the tree
def brands(only=None):
    """[{practice, locations: n, accounts: n, sf_parent}] for practices that have Salesforce locations."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT practice_id, COUNT(*) n, SUM(children) k FROM acct_locations "
                                           "WHERE gone_at='' AND practice_id IS NOT NULL GROUP BY practice_id")]
    names = {p["id"]: p for p in db.practices(include_hidden=True)}
    par = parents()
    out = [{"id": r["practice_id"], "name": names[r["practice_id"]]["name"], "locations": r["n"], "accounts": r["k"] or 0,
            "sf_parent": par.get(r["practice_id"], "")} for r in rows if r["practice_id"] in names]
    if only is not None:
        out = [b for b in out if b["id"] in only]
    return sorted(out, key=lambda b: b["name"].lower())


def locations(pid):
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM acct_locations WHERE practice_id=? AND gone_at='' ORDER BY name", (pid,))]
    for l in rows:
        l["short"] = short_name(l["name"], pid, l["city"])
    return rows


def short_name(loc_name, pid, city=""):
    """'Havens Orthodontics Canton' -> 'Canton'; a brand's only location (named like the brand) -> its city."""
    p = db.practice(pid) if pid else None
    n = loc_name or ""
    if p and _norm(n).startswith(_norm(p["name"])):
        rest = n[len(p["name"]):].strip(" -–·")
        return rest or (city.title() if city else n)
    return n


def _sf_office_links():
    with db.conn() as c:
        return {r["sf_id"]: int(r["local_key"]) for r in c.execute("SELECT sf_id, local_key FROM sf_links WHERE kind='office'")
                if str(r["local_key"]).isdigit()}


def _visit_stats(office_ids):
    if not office_ids:
        return {}
    with db.conn() as c:
        q = ",".join("?" * len(office_ids))
        rows = c.execute(f"SELECT office_id, COUNT(*) n, MAX(visited_at) last, "
                         f"(SELECT user_name FROM visits v2 WHERE v2.office_id=v.office_id ORDER BY visited_at DESC LIMIT 1) who "
                         f"FROM visits v WHERE office_id IN ({q}) GROUP BY office_id", list(office_ids)).fetchall()
    return {r["office_id"]: dict(r) for r in rows}


def _referrals(pid):
    """LeadSigma bookings through each referring-dentist link of this brand, by normalized name (last 12 months)."""
    p = db.practice(pid)
    if not p:
        return {}
    since = (date.today() - timedelta(days=365)).isoformat()
    try:
        with db.conn() as c:
            rows = c.execute("SELECT dentist, account, SUM(bookings) b FROM ls_counts WHERE section IN ('link','booking') "
                             "AND dentist != '' AND (day = '' OR day >= ?) GROUP BY dentist, account", (since,)).fetchall()
    except Exception:
        return {}
    from . import dashboard
    out = {}
    for r in rows:
        if dashboard.match_account(r["account"], [p]) != pid or not r["b"]:
            continue
        label = re.sub(r"\s*\(.*\)\s*$", "", r["dentist"])
        out[_norm(label)] = out.get(_norm(label), 0) + r["b"]
    return out


def _refs_for(name, refs):
    n = _norm(name)
    if n in refs:
        return refs[n]
    w = _words(name)
    for k, v in refs.items():
        kw = _words(k)
        if w and kw and (w <= kw or kw <= w) and len(w & kw) >= 1 and len(kw) >= 1:
            return v
    return 0


def accounts(pid, loc_sf_id="", with_prospects=True):
    """Every account under a brand (or one of its locations): Salesforce children, Hub-visited offices that aren't in
    Salesforce (joined to the nearest location), and NPI dental offices near a location (prospects)."""
    locs = locations(pid)
    if loc_sf_id:
        locs = [l for l in locs if l["sf_id"] == loc_sf_id]
    ids = [l["sf_id"] for l in locs]
    loc_by = {l["sf_id"]: l for l in locs}
    links = _sf_office_links()
    out = []
    if ids:
        with db.conn() as c:
            q = ",".join("?" * len(ids))
            kids = [dict(r) for r in c.execute(f"SELECT * FROM acct_children WHERE gone_at='' AND location_sf_id IN ({q})", ids)]
        for k in kids:
            out.append({"key": "sf:" + k["sf_id"], "sf_id": k["sf_id"], "name": k["name"], "loc": k["location_sf_id"],
                        "loc_name": loc_by[k["location_sf_id"]]["short"], "address": ", ".join(x for x in (k["street"], k["city"]) if x),
                        "phone": k["phone"], "lat": k["lat"], "lng": k["lng"], "office_id": links.get(k["sf_id"]), "source": "sf"})
    # Hub-visited offices for this brand that aren't Salesforce children: nearest location of the brand
    linked = {a["office_id"] for a in out if a["office_id"]}
    all_locs = locations(pid)
    with db.conn() as c:
        hub = [dict(r) for r in c.execute(
            "SELECT DISTINCT o.id, o.name, o.address, o.lat, o.lng FROM visits v JOIN field_offices o ON o.id=v.office_id "
            "WHERE v.practice_id=?", (pid,))]
    for o in hub:
        if o["id"] in linked or _norm(o["name"]).startswith(_norm((db.practice(pid) or {}).get("name", "x"))):
            continue
        near = min(((miles(o["lat"], o["lng"], l["lat"], l["lng"]) or 1e9, l) for l in all_locs), key=lambda x: x[0], default=(1e9, None))
        loc = near[1]["sf_id"] if near[1] and near[0] <= HUB_OFFICE_MILES else (all_locs[0]["sf_id"] if len(all_locs) == 1 else "")
        if loc_sf_id and loc != loc_sf_id:
            continue
        out.append({"key": f"o:{o['id']}", "sf_id": "", "name": o["name"], "loc": loc,
                    "loc_name": next((l["short"] for l in all_locs if l["sf_id"] == loc), "No location"), "address": o["address"],
                    "phone": "", "lat": o["lat"], "lng": o["lng"], "office_id": o["id"], "source": "hub"})
    stats = _visit_stats({a["office_id"] for a in out if a["office_id"]})
    refs = _referrals(pid)
    stale = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    for a in out:
        s = stats.get(a["office_id"]) if a["office_id"] else None
        a["visits"], a["last"], a["last_by"] = (s["n"], s["last"][:10], s["who"]) if s else (0, "", "")
        a["last_label"] = short_date(a["last"])
        a["refs"] = _refs_for(a["name"], refs)
        a["status"] = "referring" if a["refs"] else ("visited" if a["visits"] else "never")
        a["stale"] = bool(a["visits"] and a["last"] < stale) or (a["refs"] and (not a["last"] or a["last"] < stale))
    if with_prospects:
        out += prospects(pid, loc_sf_id, {(_norm(a["name"])) for a in out})
    return out


def prospects(pid, loc_sf_id="", known=frozenset()):
    """NPI-listed dental offices within PROSPECT_MILES of a location that aren't accounts yet (not orthodontists or ours)."""
    locs = [l for l in locations(pid) if l["lat"] is not None and (not loc_sf_id or l["sf_id"] == loc_sf_id)]
    if not locs:
        return []
    lat0, lat1 = min(l["lat"] for l in locs) - 0.06, max(l["lat"] for l in locs) + 0.06
    lng0, lng1 = min(l["lng"] for l in locs) - 0.08, max(l["lng"] for l in locs) + 0.08
    try:
        with db.conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT key, name, address, city, lat, lng FROM ref_practices WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? "
                "AND COALESCE(has_ortho,0)=0 AND COALESCE(ours,0)=0", (lat0, lat1, lng0, lng1))]
    except Exception:
        return []
    out = []
    for r in rows:
        if _norm(r["name"]) in known:
            continue
        d, l = min(((miles(r["lat"], r["lng"], l["lat"], l["lng"]) or 1e9, l) for l in locs), key=lambda x: x[0])
        if d > PROSPECT_MILES:
            continue
        out.append({"key": "n:" + r["key"], "sf_id": "", "name": r["name"], "loc": l["sf_id"], "loc_name": l["short"],
                    "address": ", ".join(x for x in (r["address"], r["city"]) if x), "phone": "", "lat": r["lat"], "lng": r["lng"],
                    "office_id": None, "source": "npi", "visits": 0, "last": "", "last_label": "", "last_by": "", "refs": 0, "status": "prospect",
                    "stale": False, "miles": round(d, 1)})
    return sorted(out, key=lambda a: a["miles"])


FILTERS = [("all", "All"), ("referring", "Referring"), ("visited", "Visited"), ("stale", f"Not visited {STALE_DAYS}+ days"),
           ("never", "Never visited"), ("prospect", "Prospects nearby")]


def filter_accounts(rows, f):
    if f == "referring":
        return [a for a in rows if a["refs"]]
    if f == "visited":
        return [a for a in rows if a["visits"]]
    if f == "stale":
        return [a for a in rows if a["stale"]]
    if f == "never":
        return [a for a in rows if a["source"] != "npi" and not a["visits"]]
    if f == "prospect":
        return [a for a in rows if a["source"] == "npi"]
    return [a for a in rows if a["source"] != "npi"]


def counts(rows):
    return {"all": sum(a["source"] != "npi" for a in rows), "referring": sum(bool(a["refs"]) for a in rows),
            "visited": sum(bool(a["visits"]) for a in rows), "stale": sum(bool(a["stale"]) for a in rows),
            "never": sum(a["source"] != "npi" and not a["visits"] for a in rows), "prospect": sum(a["source"] == "npi" for a in rows)}


def sort_accounts(rows):
    return sorted(rows, key=lambda a: (-(a["refs"] or 0), -(1 if a["last"] else 0), "" if not a["last"] else "~" + a["last"], a["name"].lower()),
                  reverse=False)


def find(pid, key):
    for a in accounts(pid, with_prospects=key.startswith("n:")):
        if a["key"] == key:
            return a
    return None


# ------------------------------------------------------------------ visits and notes for a scope
def activity(pid, rows, office_ids=None, limit=40, kinds=("visit", "note"), keys=None):
    """Visits and notes, newest first. office_ids: the scope's offices (None = the whole brand: also visits made for the
    brand to offices that aren't accounts). keys: account keys whose Hub notes to include."""
    from . import field
    out = []
    if "visit" in kinds:
        with db.conn() as c:
            if office_ids is None:
                vs = [dict(r) for r in c.execute(
                    "SELECT v.*, o.name AS office_name FROM visits v JOIN field_offices o ON o.id=v.office_id "
                    "WHERE v.practice_id=? OR v.office_id IN (SELECT CAST(local_key AS INTEGER) FROM sf_links WHERE kind='office' "
                    "AND sf_id IN (SELECT sf_id FROM acct_children WHERE location_sf_id IN (SELECT sf_id FROM acct_locations "
                    "WHERE practice_id=?))) ORDER BY v.visited_at DESC LIMIT ?", (pid, pid, limit))]
            elif office_ids:
                q = ",".join("?" * len(office_ids))
                vs = [dict(r) for r in c.execute(
                    f"SELECT v.*, o.name AS office_name FROM visits v JOIN field_offices o ON o.id=v.office_id "
                    f"WHERE v.office_id IN ({q}) ORDER BY v.visited_at DESC LIMIT ?", (*office_ids, limit))]
            else:
                vs = []
        for v in field.with_people(vs):
            out.append({"kind": "visit", "at": v["visited_at"], "who": v["user_name"], "where": v["office_name"],
                        "text": v["notes"] or v.get("dropped") or "", "id": v["id"], "office_id": v["office_id"],
                        "met": ", ".join(p["name"] or p["email"] for p in v.get("people") or [])})
    if "note" in kinds:
        with db.conn() as c:
            if keys is None:
                ns = [dict(r) for r in c.execute("SELECT * FROM acct_notes WHERE practice_id=? ORDER BY at DESC LIMIT ?", (pid, limit))]
            elif keys:
                q = ",".join("?" * len(keys))
                ns = [dict(r) for r in c.execute(f"SELECT * FROM acct_notes WHERE acct_key IN ({q}) ORDER BY at DESC LIMIT ?",
                                                 (*keys, limit))]
            else:
                ns = []
        names = {a["key"]: a["name"] for a in rows}
        for n in ns:
            out.append({"kind": "note", "at": n["at"], "who": n["by_name"], "where": names.get(n["acct_key"], ""), "text": n["text"],
                        "id": n["id"], "key": n["acct_key"], "sf": n["sf_status"]})
    return sorted(out, key=lambda e: e["at"], reverse=True)[:limit]


def people_met(office_id):
    if not office_id:
        return []
    with db.conn() as c:
        rows = c.execute("SELECT p.name, p.email, p.role, MAX(v.visited_at) last FROM visit_people p JOIN visits v ON v.id=p.visit_id "
                         "WHERE v.office_id=? AND (p.name != '' OR p.email != '') GROUP BY lower(p.name), lower(p.email) "
                         "ORDER BY last DESC LIMIT 12", (office_id,)).fetchall()
    return [dict(r) for r in rows]


def office_size(office_id):
    if not office_id:
        return None
    with db.conn() as c:
        r = c.execute("SELECT doctors, staff FROM visits WHERE office_id=? AND doctors IS NOT NULL ORDER BY visited_at DESC LIMIT 1",
                      (office_id,)).fetchone()
    return dict(r) if r else None


def add_note(pid, key, text, u, hub_url=""):
    text = (text or "").strip()[:4000]
    if not text:
        raise AcctError("Write the note first.")
    if not re.fullmatch(r"(sf:[A-Za-z0-9]{15,18}|o:\d+|n:[\w.-]+)", key or ""):
        raise AcctError("Pick the account the note is about.")
    with db.conn() as c:
        nid = c.execute("INSERT INTO acct_notes(acct_key, practice_id, text, by_id, by_name, at, sf_status) VALUES (?,?,?,?,?,?,?)",
                        (key, pid, text, u.get("id"), u.get("name", ""), db.now(), "pending" if key.startswith("sf:") else "")).lastrowid
    if key.startswith("sf:"):
        threading.Thread(target=_send_note, args=(nid, hub_url), daemon=True).start()
    return nid


def _send_note(nid, hub_url=""):
    """Copy a Hub note to Salesforce as a Note on the account."""
    from . import salesforce
    with db.conn() as c:
        n = c.execute("SELECT * FROM acct_notes WHERE id=?", (nid,)).fetchone()
    if not n or not n["acct_key"].startswith("sf:") or not salesforce.connected():
        return
    try:
        sid = salesforce.create("Note", {"ParentId": n["acct_key"][3:], "Title": f"Hub note from {n['by_name'] or 'MCDP Hub'}"[:80],
                                         "Body": n["text"] + (f"\n\nFrom the MCDP Hub ({hub_url.rstrip('/')}/field/accounts)" if hub_url else "")})
        with db.conn() as c:
            c.execute("UPDATE acct_notes SET sf_note_id=?, sf_status='sent', sf_error='' WHERE id=?", (sid, nid))
    except Exception as e:
        with db.conn() as c:
            c.execute("UPDATE acct_notes SET sf_status='error', sf_error=? WHERE id=?", (str(e)[:300], nid))


def resend_notes(hub_url=""):
    with db.conn() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM acct_notes WHERE sf_status IN ('pending','error') LIMIT 50")]
    for i in ids:
        _send_note(i, hub_url)


# ------------------------------------------------------------------ search within a brand
def search(pid, q, limit=8):
    """Accounts (name, address, phone), people met and notes across every account under the brand."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"accounts": [], "people": [], "notes": []}
    terms = _norm(q).split()
    rows = accounts(pid)
    hit = lambda s: all(t in _norm(s) for t in terms)
    acc = [a for a in rows if hit(" ".join((a["name"], a["address"] or "", a["phone"] or "", a["loc_name"])))]
    acc.sort(key=lambda a: (a["source"] == "npi", not _norm(a["name"]).startswith(terms[0]), a["name"].lower()))
    by_office = {a["office_id"]: a for a in rows if a["office_id"]}
    people = []
    if by_office:
        with db.conn() as c:
            qs = ",".join("?" * len(by_office))
            for r in c.execute(f"SELECT p.name, p.email, p.role, v.office_id, MAX(v.visited_at) last FROM visit_people p JOIN visits v "
                               f"ON v.id=p.visit_id WHERE v.office_id IN ({qs}) GROUP BY lower(p.name), v.office_id", list(by_office)):
                if hit(f"{r['name']} {r['email']} {r['role']}"):
                    a = by_office[r["office_id"]]
                    people.append({"name": r["name"] or r["email"], "role": r["role"], "account": a["name"], "key": a["key"],
                                   "loc": a["loc"], "last": (r["last"] or "")[:10]})
    notes = []
    for e in activity(pid, rows, None, limit=400) + activity(pid, rows, list(by_office), limit=400, kinds=("visit",)):
        if e["text"] and hit(e["text"]) and not any(n["id"] == e["id"] and n["kind"] == e["kind"] for n in notes):
            a = by_office.get(e.get("office_id")) or next((x for x in rows if x["key"] == e.get("key")), None)
            notes.append({**e, "key": a["key"] if a else "", "loc": a["loc"] if a else ""})
    return {"accounts": acc[:limit], "people": people[:limit], "notes": notes[:limit], "total": len(acc)}


# ------------------------------------------------------------------ map and visit labels
def map_data(only=None):
    """Salesforce child accounts with a place on the map, for the Map's 'Salesforce accounts' layer."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT k.sf_id, k.name, k.street, k.city, k.lat, k.lng, k.geo, l.name AS loc_name, l.practice_id, l.map_office_id "
            "FROM acct_children k JOIN acct_locations l ON l.sf_id=k.location_sf_id WHERE k.gone_at='' AND k.lat IS NOT NULL")]
    links = _sf_office_links()
    return [{"k": r["sf_id"], "name": r["name"], "addr": ", ".join(x for x in (r["street"], r["city"]) if x), "lat": r["lat"],
             "lng": r["lng"], "loc": r["loc_name"], "pid": r["practice_id"], "office": r["map_office_id"],
             "fid": links.get(r["sf_id"]), "approx": r["geo"] == "zip"}
            for r in rows if only is None or r["practice_id"] in only]


def labels_for_offices(office_ids):
    """{office_id: {pid, brand, loc_sf_id, loc}} for visits' 'child of Brand › Location' line."""
    if not office_ids:
        return {}
    with db.conn() as c:
        q = ",".join("?" * len(office_ids))
        rows = c.execute(f"SELECT CAST(s.local_key AS INTEGER) oid, s.sf_id AS acct, l.sf_id, l.name, l.city, l.practice_id FROM sf_links s "
                         f"JOIN acct_children k ON k.sf_id=s.sf_id JOIN acct_locations l ON l.sf_id=k.location_sf_id "
                         f"WHERE s.kind='office' AND CAST(s.local_key AS INTEGER) IN ({q}) AND l.practice_id IS NOT NULL",
                         list(office_ids)).fetchall()
    names = {p["id"]: p["name"] for p in db.practices(include_hidden=True)}
    return {r["oid"]: {"pid": r["practice_id"], "brand": names.get(r["practice_id"], ""), "loc_sf_id": r["sf_id"],
                       "loc": short_name(r["name"], r["practice_id"], r["city"]), "key": "sf:" + r["acct"]} for r in rows}


def office_for(key, u_name=""):
    """A field office for an account, for logging a visit: existing, or made from the Salesforce account (and linked)."""
    from . import field, salesforce
    if key.startswith("o:"):
        return int(key[2:])
    if key.startswith("sf:"):
        sid = key[3:]
        oid = _sf_office_links().get(sid)
        if oid:
            return oid
        with db.conn() as c:
            k = c.execute("SELECT * FROM acct_children WHERE sf_id=?", (sid,)).fetchone()
        if not k:
            raise AcctError("That account isn't in the Hub's copy of Salesforce. Sync and try again.")
        o = field.ensure_office(None, None, k["name"], ", ".join(x for x in (k["street"], k["city"], k["state"], k["zip"]) if x),
                                k["lat"], k["lng"], u_name)
        salesforce._remember("office", o["id"], sid)
        return o["id"]
    if key.startswith("n:"):
        o = field.ensure_office(None, key[2:], "", "", None, None, u_name)
        return o["id"]
    raise AcctError("Pick an account.")


def p360_summary(pid):
    """For Practice 360: per location counts and the accounts worth a visit."""
    rows = accounts(pid, with_prospects=False)
    if not rows and not locations(pid):
        return None
    by = {}
    for l in locations(pid):
        by[l["sf_id"]] = {"sf_id": l["sf_id"], "name": l["short"], "n": 0, "referring": 0, "refs": 0, "visits": 0}
    since = (date.today() - timedelta(days=90)).isoformat()
    stats90 = {}
    oids = [a["office_id"] for a in rows if a["office_id"]]
    if oids:
        with db.conn() as c:
            q = ",".join("?" * len(oids))
            stats90 = {r[0]: r[1] for r in c.execute(f"SELECT office_id, COUNT(*) FROM visits WHERE office_id IN ({q}) AND visited_at >= ? "
                                                     f"GROUP BY office_id", (*oids, since))}
    for a in rows:
        b = by.get(a["loc"])
        if not b:
            continue
        b["n"] += 1
        b["referring"] += bool(a["refs"])
        b["refs"] += a["refs"] or 0
        b["visits"] += stats90.get(a["office_id"], 0)
    worth = sorted([a for a in rows if a["stale"] and a["refs"]], key=lambda a: -a["refs"])[:3]
    if len(worth) < 3:
        worth += sorted([a for a in rows if a["stale"] and not a["refs"]], key=lambda a: a["last"])[:3 - len(worth)]
    return {"locations": sorted(by.values(), key=lambda b: (-b["refs"], -b["n"])), "total": len(rows),
            "referring": sum(bool(a["refs"]) for a in rows), "worth": worth}
