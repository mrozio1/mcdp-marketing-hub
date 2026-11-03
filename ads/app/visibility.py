"""Search & Maps: how each practice shows up on Google, month by month.

Two free Google sources, read with the same Google sign-in the Reviews page uses:
- Search Console: the practice website in Google search (searches it shows for, average position,
  appearances, clicks).
- Business Profile Performance: the practice's Google profile (views on Search and Maps, calls,
  direction requests, website clicks, and the searches that showed it).

A daily refresh re-reads the current and previous month (Google keeps adjusting the last few days);
the first run fills the last 12 months. Everything is stored by month, so any month can be looked at
or exported later.
"""
import io
import json
import re
import threading
import time
from datetime import date, timedelta

from . import connectors as cx
from . import db, reviews

SCHEMA = """
CREATE TABLE IF NOT EXISTS sc_sites (
  site TEXT PRIMARY KEY,
  permission TEXT DEFAULT '',
  practice_id INTEGER,
  seen_at TEXT
);
CREATE TABLE IF NOT EXISTS sc_month (
  site TEXT NOT NULL, month TEXT NOT NULL,
  clicks INTEGER DEFAULT 0, impressions INTEGER DEFAULT 0, pos_weight REAL DEFAULT 0,
  days INTEGER DEFAULT 0, fetched_at TEXT,
  PRIMARY KEY (site, month)
);
CREATE TABLE IF NOT EXISTS sc_queries (
  site TEXT NOT NULL, month TEXT NOT NULL, query TEXT NOT NULL,
  clicks INTEGER DEFAULT 0, impressions INTEGER DEFAULT 0, position REAL,
  PRIMARY KEY (site, month, query)
);
CREATE TABLE IF NOT EXISTS gbp_perf (
  location TEXT NOT NULL, month TEXT NOT NULL,
  search_views INTEGER DEFAULT 0, maps_views INTEGER DEFAULT 0, calls INTEGER DEFAULT 0,
  directions INTEGER DEFAULT 0, website INTEGER DEFAULT 0, days INTEGER DEFAULT 0, fetched_at TEXT,
  PRIMARY KEY (location, month)
);
CREATE TABLE IF NOT EXISTS gbp_keywords (
  location TEXT NOT NULL, month TEXT NOT NULL, keyword TEXT NOT NULL,
  views INTEGER, under INTEGER,
  PRIMARY KEY (location, month, keyword)
);
"""
QUERY_ROWS = 500        # top searches kept per site per month
BACKFILL_MONTHS = 12
STATUS_KEY = "visibility_status"
_lock = threading.Lock()


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


# ------------------------------------------------------------------ months
def months_back(n, month=None, today=None):
    end = date.fromisoformat((month or date.today().isoformat()[:7]) + "-01")
    return reviews.months_back(n, end)


def bounds(month, today=None):
    """(first day, last day) of a month; the current month ends today."""
    today = today or date.today()
    first = date.fromisoformat(month + "-01")
    return first, reviews.month_end(month, today)


def prev_month(month):
    d = date.fromisoformat(month + "-01") - timedelta(days=1)
    return d.isoformat()[:7]


def label(month):
    return reviews.month_label(month)


# ------------------------------------------------------------------ status & connection
def status():
    try:
        return json.loads(db.get_setting(STATUS_KEY) or "{}")
    except ValueError:
        return {}


def connected():
    return reviews.gbp_connected()


def client():
    return cx.GBPClient(db.get_setting("gbp_refresh_token"))


# ------------------------------------------------------------------ sites → practices
GENERIC = {"orthodontics", "orthodontic", "ortho", "dental", "dentistry", "family", "pediatric", "smile", "smiles",
           "the", "and", "of", "for", "dds", "pc", "pllc", "llc", "oral", "surgery", "care", "group", "center"}


def domain(site):
    s = site.replace("sc-domain:", "")
    s = re.sub(r"^https?://", "", s).split("/")[0]
    return re.sub(r"^www\.", "", s.lower())


def suggest_practice(site, plist):
    """A practice whose distinctive name word is in the site's domain (heightsortho.com → Heights), else None."""
    d = re.sub(r"[^a-z0-9]", "", domain(site).rsplit(".", 1)[0])
    best = None
    for p in plist:
        words = [w for w in re.findall(r"[a-z0-9]+", p["name"].lower()) if len(w) > 3 and w not in GENERIC]
        if words and all(w in d for w in words[:1]):
            score = sum(len(w) for w in words if w in d)
            if not best or score > best[0]:
                best = (score, p["id"])
    return best[1] if best else None


def sites():
    with db.conn() as c:
        c.executescript(SCHEMA)
        return [dict(r) for r in c.execute("SELECT * FROM sc_sites ORDER BY site")]


def refresh_sites(cl=None, plist=None):
    """Read the Search Console properties the signed-in person can see. New ones get a suggested practice;
    when a domain has both a domain property and URL properties, only the domain property is suggested."""
    cl = cl or client()
    plist = plist if plist is not None else reviews.practices()
    found = cl.sc_sites()
    have = {s["site"]: s for s in sites()}
    domains_with_dp = {domain(f["site"]) for f in found if f["site"].startswith("sc-domain:")}
    taken = {domain(s["site"]) for s in have.values() if s["practice_id"]}
    now = db.now()
    with db.conn() as c:
        for f in sorted(found, key=lambda f: (not f["site"].startswith("sc-domain:"), f["site"])):
            if f["site"] in have:
                c.execute("UPDATE sc_sites SET permission=?, seen_at=? WHERE site=?", (f["permission"], now, f["site"]))
                continue
            dom = domain(f["site"])
            pid = None
            if dom not in taken and (f["site"].startswith("sc-domain:") or dom not in domains_with_dp):
                pid = suggest_practice(f["site"], plist)
                if pid:
                    taken.add(dom)
            c.execute("INSERT INTO sc_sites(site, permission, practice_id, seen_at) VALUES (?,?,?,?)",
                      (f["site"], f["permission"], pid, now))
    return len(found)


def set_site_practice(site, pid):
    with db.conn() as c:
        c.execute("UPDATE sc_sites SET practice_id=? WHERE site=?", (pid or None, site))


# ------------------------------------------------------------------ refresh
def _backfilled(kind):
    try:
        return set(json.loads(db.get_setting(f"visibility_filled_{kind}") or "[]"))
    except ValueError:
        return set()


def _mark_filled(kind, key):
    done = _backfilled(kind) | {key}
    db.set_setting(f"visibility_filled_{kind}", json.dumps(sorted(done)))


def _months_to_fetch(kind, today, key):
    """The current and previous month; the last 12 as well the first time a site or location is read."""
    cur = today.isoformat()[:7]
    if key in _backfilled(kind):
        return [prev_month(cur), cur]
    return months_back(BACKFILL_MONTHS + 1, cur)


def refresh_search_console(cl, today=None):
    """Monthly totals and top searches for every site linked to a practice."""
    today = today or date.today()
    errors, n = [], 0
    for s in [s for s in sites() if s["practice_id"]]:
        months = _months_to_fetch("sc", today, s["site"])
        start, end = bounds(months[0], today)[0], bounds(months[-1], today)[1]
        try:
            daily = cl.sc_query(s["site"], start, end, ["date"], row_limit=1000)
            agg = {}
            for r in daily:
                m = r["keys"][0][:7]
                a = agg.setdefault(m, [0, 0, 0.0, 0])
                a[0] += int(r.get("clicks") or 0)
                a[1] += int(r.get("impressions") or 0)
                a[2] += float(r.get("position") or 0) * int(r.get("impressions") or 0)
                a[3] += 1
            per_month = {}
            for m in months:
                ms, me = bounds(m, today)
                per_month[m] = cl.sc_query(s["site"], ms, me, ["query"], row_limit=QUERY_ROWS)
            with db.conn() as c:
                for m in months:
                    a = agg.get(m, [0, 0, 0.0, 0])
                    c.execute("INSERT OR REPLACE INTO sc_month(site, month, clicks, impressions, pos_weight, days, fetched_at) "
                              "VALUES (?,?,?,?,?,?,?)", (s["site"], m, a[0], a[1], round(a[2], 2), a[3], db.now()))
                    c.execute("DELETE FROM sc_queries WHERE site=? AND month=?", (s["site"], m))
                    c.executemany("INSERT OR REPLACE INTO sc_queries(site, month, query, clicks, impressions, position) "
                                  "VALUES (?,?,?,?,?,?)",
                                  [(s["site"], m, r["keys"][0], int(r.get("clicks") or 0), int(r.get("impressions") or 0),
                                    round(float(r.get("position") or 0), 2)) for r in per_month[m] if r.get("keys")])
            _mark_filled("sc", s["site"])
            n += 1
        except cx.ConnectorError as e:
            errors.append(f"{domain(s['site'])}: {e}")
    return n, errors


def refresh_profiles(cl, today=None):
    """Monthly views, calls, directions and website clicks, plus searches that found each linked location."""
    today = today or date.today()
    errors, n = [], 0
    locs = [l for l in reviews.locations() if l["practice_id"]]
    for loc in locs:
        months = _months_to_fetch("gbp", today, loc["name"])
        start, end = bounds(months[0], today)[0], bounds(months[-1], today)[1]
        try:
            series = cl.perf_daily(loc["name"], start, end)
            agg = {}
            for metric, vals in series.items():
                for d, v in vals.items():
                    a = agg.setdefault(d.isoformat()[:7], {"search": 0, "maps": 0, "calls": 0, "dir": 0, "web": 0, "days": set()})
                    a["days"].add(d)
                    if "SEARCH" in metric:
                        a["search"] += v
                    elif "MAPS" in metric:
                        a["maps"] += v
                    elif metric == "CALL_CLICKS":
                        a["calls"] += v
                    elif metric == "BUSINESS_DIRECTION_REQUESTS":
                        a["dir"] += v
                    elif metric == "WEBSITE_CLICKS":
                        a["web"] += v
            kw = {}
            for m in months:
                try:
                    kw[m] = cl.perf_keywords(loc["name"], int(m[:4]), int(m[5:]))
                except cx.ConnectorError:
                    kw[m] = None            # the month isn't available yet (usually the current one)
            with db.conn() as c:
                for m in months:
                    a = agg.get(m)
                    # Google sends zeros for days it hasn't counted yet; an all-zero month isn't in yet
                    if a and (a["search"] or a["maps"] or a["calls"] or a["dir"] or a["web"]):
                        c.execute("INSERT OR REPLACE INTO gbp_perf(location, month, search_views, maps_views, calls, "
                                  "directions, website, days, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (loc["name"], m, a["search"], a["maps"], a["calls"], a["dir"], a["web"], len(a["days"]), db.now()))
                    if kw.get(m) is not None:
                        c.execute("DELETE FROM gbp_keywords WHERE location=? AND month=?", (loc["name"], m))
                        c.executemany("INSERT OR REPLACE INTO gbp_keywords(location, month, keyword, views, under) VALUES (?,?,?,?,?)",
                                      [(loc["name"], m, k, v, t) for k, v, t in kw[m] if k])
            _mark_filled("gbp", loc["name"])
            n += 1
        except cx.ConnectorError as e:
            msg = str(e)
            if "permission" in msg.lower() or "HTTP 403" in msg:
                msg = ("Google won't share this profile's numbers with the signed-in account. Usually that account is "
                       "only a Site manager there, or the profile isn't verified: make it a Manager or Owner in the "
                       "Business Profile's People and access, then refresh.")
            errors.append(f"{loc['title']}: {msg}")
    return n, errors


def refresh(today=None):
    """Everything; returns the status dict it saved. Safe to call from a thread."""
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    try:
        init()
        st = {"at": db.now(), "errors": []}
        if not connected():
            st["errors"].append("Google isn't connected. Connect it on Reviews → Google setup.")
        else:
            try:
                cl = client()
            except cx.ConnectorError as e:
                st["errors"].append(str(e))
                cl = None
            if cl:
                try:
                    st["sites_found"] = refresh_sites(cl)
                except cx.ConnectorError as e:
                    st["errors"].append(f"Search Console: {e}")
                n, errs = refresh_search_console(cl, today)
                st["sites"], st["errors"] = n, st["errors"] + errs
                n, errs = refresh_profiles(cl, today)
                st["locations"], st["errors"] = n, st["errors"] + errs
        db.set_setting(STATUS_KEY, json.dumps(st))
        return st
    finally:
        _lock.release()


def running():
    return _lock.locked()


def refresh_in_background():
    if running():
        return False
    threading.Thread(target=refresh, name="visibility-refresh", daemon=True).start()
    time.sleep(0.05)
    return True


def start_scheduler():
    def loop():
        time.sleep(300)
        while True:
            try:
                if connected():
                    refresh()
            except Exception as e:
                db.set_setting(STATUS_KEY, json.dumps({"at": db.now(), "errors": [str(e)]}))
            time.sleep(24 * 3600)
    threading.Thread(target=loop, name="visibility", daemon=True).start()


# ------------------------------------------------------------------ reading
def _sites_for(pid=None):
    return [s["site"] for s in sites() if s["practice_id"] and (pid is None or s["practice_id"] == pid)]


def _locs_for(pid=None):
    return [l for l in reviews.locations() if l["practice_id"] and (pid is None or l["practice_id"] == pid)]


def _in(col, items):
    return f"{col} IN ({','.join('?' * len(items))})" if items else "0"


def website(pid, months):
    """{month: {'clicks','impressions','position','ctr','days'}} for a practice's sites (position weighted by
    appearances across sites). pid None = every linked site."""
    ss = _sites_for(pid)
    out = {m: None for m in months}
    if not ss:
        return out
    with db.conn() as c:
        c.executescript(SCHEMA)
        for r in c.execute(f"SELECT month, SUM(clicks) cl, SUM(impressions) im, SUM(pos_weight) pw, MAX(days) dy FROM sc_month "
                           f"WHERE {_in('site', ss)} AND {_in('month', months)} GROUP BY month", ss + months):
            if r["dy"]:
                out[r["month"]] = {"clicks": r["cl"], "impressions": r["im"], "days": r["dy"],
                                   "position": round(r["pw"] / r["im"], 1) if r["im"] else None,
                                   "ctr": r["cl"] / r["im"] if r["im"] else None}
    return out


def profile(pid, months, locs=None):
    """{month: {'search','maps','views','calls','directions','website','actions'}} for a practice's locations."""
    names = [l["name"] for l in (locs if locs is not None else _locs_for(pid))]
    out = {m: None for m in months}
    if not names:
        return out
    with db.conn() as c:
        c.executescript(SCHEMA)
        for r in c.execute(f"SELECT month, SUM(search_views) s, SUM(maps_views) mp, SUM(calls) ca, SUM(directions) di, "
                           f"SUM(website) we, MAX(days) dy FROM gbp_perf WHERE {_in('location', names)} AND "
                           f"{_in('month', months)} GROUP BY month", names + months):
            if r["dy"]:
                out[r["month"]] = {"search": r["s"], "maps": r["mp"], "views": r["s"] + r["mp"], "calls": r["ca"],
                                   "directions": r["di"], "website": r["we"], "actions": r["ca"] + r["di"] + r["we"],
                                   "days": r["dy"]}
    return out


def change(now, before, lower_better=False):
    """{'diff', 'pct', 'good'} comparing two numbers, or None."""
    if now is None or before is None:
        return None
    diff = round(now - before, 1)
    pct = (now - before) / before if before else None
    good = None if diff == 0 else ((diff < 0) if lower_better else (diff > 0))
    return {"diff": diff, "pct": pct, "good": good}


def queries(pid, month, limit=None):
    """A practice's searches for a month, best first, with last month's position next to each."""
    ss = _sites_for(pid)
    if not ss:
        return []
    pm = prev_month(month)
    with db.conn() as c:
        c.executescript(SCHEMA)
        rows = {}
        for r in c.execute(f"SELECT month, query, SUM(clicks) cl, SUM(impressions) im, "
                           f"SUM(position * impressions) / NULLIF(SUM(impressions), 0) pos "
                           f"FROM sc_queries WHERE {_in('site', ss)} AND month IN (?, ?) GROUP BY month, query",
                           ss + [month, pm]):
            q = rows.setdefault(r["query"], {"query": r["query"]})
            q["now" if r["month"] == month else "before"] = {"clicks": r["cl"], "impressions": r["im"],
                                                             "position": round(r["pos"], 1) if r["pos"] else None}
    out = []
    for q in rows.values():
        if "now" not in q:
            continue
        b = q.get("before")
        q.update(q.pop("now"))
        q["prev_position"] = b["position"] if b else None
        q["moved"] = round(b["position"] - q["position"], 1) if b and b["position"] and q["position"] else None
        q["new"] = b is None
        out.append(q)
    out.sort(key=lambda q: (-q["clicks"], -q["impressions"]))
    return out[:limit] if limit else out


def keywords(pid, month, limit=25, locs=None):
    """Searches that showed the practice's Google profile, most views first (Google hides exact counts under 15)."""
    names = [l["name"] for l in (locs if locs is not None else _locs_for(pid))]
    if not names:
        return [], False
    pm = prev_month(month)
    with db.conn() as c:
        c.executescript(SCHEMA)
        have = c.execute(f"SELECT COUNT(*) FROM gbp_keywords WHERE {_in('location', names)} AND month=?",
                         names + [month]).fetchone()[0]
        rows = {}
        for r in c.execute(f"SELECT month, keyword, SUM(COALESCE(views, 0)) v, MAX(under) u, COUNT(views) nv FROM gbp_keywords "
                           f"WHERE {_in('location', names)} AND month IN (?, ?) GROUP BY month, keyword", names + [month, pm]):
            k = rows.setdefault(r["keyword"], {"keyword": r["keyword"]})
            k["now" if r["month"] == month else "before"] = r["v"] if r["nv"] else None
            if r["month"] == month:
                k["under"] = r["u"] if not r["nv"] else None
    out = [k for k in rows.values() if "now" in k]
    for k in out:
        k["views"] = k.pop("now")
        k["change"] = change(k["views"], k.get("before"))
    out.sort(key=lambda k: (-(k["views"] or 0), k["keyword"]))
    return out[:limit], bool(have)


def _pack(web, prof, month):
    pm = prev_month(month)
    w, wb, p, pb = web.get(month), web.get(pm), prof.get(month), prof.get(pm)
    g = lambda d, k: d.get(k) if d else None
    return {
        "web": w, "prof": p,
        "clicks_ch": change(g(w, "clicks"), g(wb, "clicks")),
        "impr_ch": change(g(w, "impressions"), g(wb, "impressions")),
        "pos_ch": change(g(w, "position"), g(wb, "position"), lower_better=True),
        "views_ch": change(g(p, "views"), g(pb, "views")),
        "calls_ch": change(g(p, "calls"), g(pb, "calls")),
        "dir_ch": change(g(p, "directions"), g(pb, "directions")),
        "site_ch": change(g(p, "website"), g(pb, "website")),
        "actions_ch": change(g(p, "actions"), g(pb, "actions")),
    }


def trend(web, prof, months):
    """12 bars: website clicks and profile actions (calls + directions + website), scaled together."""
    top = max([(web[m] or {}).get("clicks") or 0 for m in months] + [(prof[m] or {}).get("actions") or 0 for m in months] + [1])
    pos = [(web[m] or {}).get("position") for m in months]
    known = [p for p in pos if p]
    lo, hi = (min(known), max(known)) if known else (0, 1)
    span = max(hi - lo, 1)
    out = []
    for m, p in zip(months, pos):
        c = (web[m] or {}).get("clicks")
        a = (prof[m] or {}).get("actions")
        lab = reviews.month_label(m, short=True) + (f" ’{m[2:4]}" if m == months[0] or m.endswith("-01") else "")
        out.append({"month": m, "label": lab, "clicks": c, "actions": a,
                    "ch": round(100 * (c or 0) / top), "ah": round(100 * (a or 0) / top), "position": p,
                    # higher on the chart = better (smaller) position
                    "py": None if not p else round(10 + 80 * (p - lo) / span)})
    return out


def overview(month, plist=None, today=None):
    """Every practice for a month, plus totals and a 12-month trend for all of them."""
    plist = plist if plist is not None else reviews.practices()
    months = months_back(12, month)
    pm = prev_month(month)
    rows = []
    for p in plist:
        web, prof = website(p["id"], [month, pm]), profile(p["id"], [month, pm])
        top = queries(p["id"], month, limit=1)
        rows.append({"p": p, **_pack(web, prof, month), "top": top[0] if top else None,
                     "has_site": bool(_sites_for(p["id"])), "has_loc": bool(_locs_for(p["id"]))})
    web_all, prof_all = website(None, months), profile(None, months)
    return {"rows": rows, "totals": _pack(web_all, prof_all, month), "trend": trend(web_all, prof_all, months),
            **_meta(month, today)}


def detail(pid, month, today=None):
    """One practice: totals, 12-month trend, searches, profile searches, and each location."""
    months = months_back(12, month)
    web, prof = website(pid, months), profile(pid, months)
    locs = _locs_for(pid)
    by_loc = []
    if len(locs) > 1:
        for l in locs:
            pr = profile(pid, [month, prev_month(month)], [l])
            by_loc.append({"loc": l, "now": pr[month], "ch": change((pr[month] or {}).get("views"),
                                                                    (pr[prev_month(month)] or {}).get("views"))})
    kw, kw_ready = keywords(pid, month)
    qs = queries(pid, month)
    return {**_pack(web, prof, month), "trend": trend(web, prof, months), "queries": qs,
            "up": sorted([q for q in qs if (q["moved"] or 0) >= 1 and q["impressions"] >= 10], key=lambda q: -q["moved"])[:30],
            "down": sorted([q for q in qs if (q["moved"] or 0) <= -1 and q["impressions"] >= 10], key=lambda q: q["moved"])[:30],
            "new": [q for q in qs if q["new"] and q["impressions"] >= 10][:30],
            "keywords": kw, "kw_ready": kw_ready, "locations": by_loc,
            "sites": [domain(s) for s in _sites_for(pid)], "locs": locs, **_meta(month, today)}


def _meta(month, today=None):
    today = today or date.today()
    cur = month == today.isoformat()[:7]
    return {"month": month, "label": reviews.month_label(month), "prev_label": reviews.month_label(prev_month(month)),
            "is_current": cur}


def any_data():
    with db.conn() as c:
        c.executescript(SCHEMA)
        return bool(c.execute("SELECT 1 FROM sc_month LIMIT 1").fetchone() or c.execute("SELECT 1 FROM gbp_perf LIMIT 1").fetchone())


# ------------------------------------------------------------------ export
def export_xlsx(month, plist=None):
    import openpyxl
    from openpyxl.styles import Font
    plist = plist if plist is not None else reviews.practices()
    ov = overview(month, plist)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "By practice"
    ws.append([f"Search & Maps · {ov['label']}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    head = ["Practice", "Website clicks", "vs last month", "Appearances in search", "Average position", "Position change",
            "Top search", "Profile views", "Search views", "Maps views", "Calls", "Directions", "Website clicks from profile"]
    ws.append(head)
    for c in ws[3]:
        c.font = Font(bold=True)
    for r in ov["rows"]:
        w, p = r["web"] or {}, r["prof"] or {}
        ws.append([r["p"]["name"], w.get("clicks"), (r["clicks_ch"] or {}).get("diff"), w.get("impressions"), w.get("position"),
                   (r["pos_ch"] or {}).get("diff"), (r["top"] or {}).get("query"), p.get("views"), p.get("search"), p.get("maps"),
                   p.get("calls"), p.get("directions"), p.get("website")])
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["G"].width = 32
    qs = wb.create_sheet("Top searches")
    qs.append(["Practice", "Search", "Clicks", "Appearances", "Position", f"Position in {ov['prev_label']}"])
    for c in qs[1]:
        c.font = Font(bold=True)
    for p in plist:
        for q in queries(p["id"], month, limit=50):
            qs.append([p["name"], q["query"], q["clicks"], q["impressions"], q["position"], q["prev_position"]])
    qs.column_dimensions["A"].width = 30
    qs.column_dimensions["B"].width = 40
    ks = wb.create_sheet("Profile searches")
    ks.append(["Practice", "Search", "Views (blank = under the number shown)", "Under"])
    for c in ks[1]:
        c.font = Font(bold=True)
    for p in plist:
        for k in keywords(p["id"], month, limit=50)[0]:
            ks.append([p["name"], k["keyword"], k["views"], k.get("under")])
    ks.column_dimensions["A"].width = 30
    ks.column_dimensions["B"].width = 40
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
