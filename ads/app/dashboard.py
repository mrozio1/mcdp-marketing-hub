"""The master dashboard on the home page: one view from ad spend to starts.

- Ad spend, conversions, reviews, posts and visits come from the hub (report.build).
- Online booking numbers come from LeadSigma's Marketing Attribution CSV export, uploaded here. Only counts are kept:
  rows are rolled up by day, practice account, office, channel, source, medium, campaign, status, the "how did you
  hear about us" answer and the referring link. Names, emails and phone numbers in the file are never stored.
- Exams and starts come from MCDP Reports (Power BI Practice Scorecard) through Practice 360's weekly import;
  before the first import they show as "Connect".
- The marketing budget (year to date) is typed in from Power BI's Marketing page by an admin.
"""
import csv
import io
import json
import re
from datetime import date, datetime, timedelta

from . import db, report

ROLLUP = ("section", "day", "account", "office", "channel", "source", "medium", "campaign", "status", "heard", "dentist")
# header words -> our column (first match wins; checked against the lower-cased header)
HEADERS = [
    ("heard", ("how did", "hear about", "heard")),
    ("dentist", ("referring dentist", "referral link", "referring", "dentist")),
    ("source", ("utm source", "utm_source")),
    ("medium", ("utm medium", "utm_medium")),
    ("campaign", ("utm campaign", "utm_campaign")),
    ("channel", ("channel",)),
    ("account", ("account", "practice")),
    ("office", ("office", "location")),
    ("status", ("booking status", "status")),
    ("day", ("created", "booked at", "booking date", "requested", "submitted", "date")),
    ("views", ("started views", "views")),
    ("bookings", ("bookings",)),
    ("confirmed", ("confirmed",)),
    ("cancelled", ("cancelled", "canceled")),
]
PRIVATE = re.compile(r"name|e-?mail|phone|mobile|address|birth|dob|note|comment|patient", re.I)
UNFILLED = re.compile(r"\{[a-z_]+\}")


class DashError(ValueError):
    pass


def init():
    with db.conn() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS ls_counts (
  section TEXT DEFAULT '', day TEXT, account TEXT DEFAULT '', office TEXT DEFAULT '', channel TEXT DEFAULT '', source TEXT DEFAULT '',
  medium TEXT DEFAULT '', campaign TEXT DEFAULT '', status TEXT DEFAULT '', heard TEXT DEFAULT '', dentist TEXT DEFAULT '',
  views INTEGER DEFAULT 0, bookings INTEGER DEFAULT 0, confirmed INTEGER DEFAULT 0, cancelled INTEGER DEFAULT 0,
  upload_id INTEGER
);
CREATE INDEX IF NOT EXISTS ls_counts_day ON ls_counts(day);
CREATE TABLE IF NOT EXISTS ls_uploads (
  id INTEGER PRIMARY KEY, filename TEXT, kind TEXT, rows INTEGER, first_day TEXT, last_day TEXT,
  period_start TEXT, period_end TEXT, by TEXT, at TEXT
);""")
        if "section" not in {r["name"] for r in c.execute("PRAGMA table_info(ls_counts)")}:
            c.execute("ALTER TABLE ls_counts ADD COLUMN section TEXT DEFAULT ''")


# ------------------------------------------------------------------ LeadSigma import
def _day(v):
    v = (v or "").strip()
    if not v:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", v)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", v)
    if m:
        y = int(m.group(3))
        try:
            return date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m = re.match(r"([A-Za-z]{3})[a-z]* (\d{1,2}),? (\d{4})", v)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
        except ValueError:
            return None
    return None


def _num(v):
    try:
        return int(float(str(v).replace(",", "").replace("%", "").strip() or 0))
    except ValueError:
        return 0


def map_headers(headers):
    """{our column: index in the file}. Personal columns (names, emails, phones, notes) are never mapped."""
    out = {}
    for i, h in enumerate(headers):
        low = (h or "").strip().lower()
        if not low or PRIVATE.search(low) and not any(w in low for w in ("referring", "hear", "referral")):
            continue
        for col, words in HEADERS:
            if col not in out and any(w in low for w in words):
                if col == "source" and "referral source" in low:
                    continue
                out[col] = i
                break
    return out


def parse(data, filename=""):
    """The rows of a LeadSigma CSV as count rows: (kind, [{rollup cols..., views, bookings, confirmed, cancelled}])."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any((x or "").strip() for x in r)]
    if len(rows) < 2:
        raise DashError("That file has no rows.")
    head = [(h or "").strip().lower() for h in rows[0]]
    if "report" in head and "label" in head:
        return "summary", _parse_sections(rows, head)
    # some exports put a title or date line above the header: take the first row that maps 2+ columns
    hi = next((i for i, r in enumerate(rows[:10]) if len(map_headers(r)) >= 2), None)
    if hi is None:
        raise DashError("That doesn't look like a LeadSigma export (no source, status or booking columns found).")
    cols = map_headers(rows[hi])
    summary = "bookings" in cols or "views" in cols
    agg = {}
    for r in rows[hi + 1:]:
        g = lambda k: (r[cols[k]].strip() if k in cols and cols[k] < len(r) else "")
        key = {k: g(k)[:200] for k in ROLLUP if k not in ("day", "section")}
        key["section"] = "source" if summary else "booking"
        d = _day(g("day")) if "day" in cols else None
        key["day"] = d.isoformat() if d else ""
        if summary:
            if (g("channel") or g("source") or g("dentist")).lower() in ("total", "totals"):
                continue
            n = {"views": _num(g("views")), "bookings": _num(g("bookings")), "confirmed": _num(g("confirmed")),
                 "cancelled": _num(g("cancelled"))}
        else:
            st = key["status"].lower()
            n = {"views": 0, "bookings": 1, "confirmed": int("confirm" in st), "cancelled": int("cancel" in st)}
        k = tuple(key[c] for c in ROLLUP)
        cur = agg.setdefault(k, {**key, "views": 0, "bookings": 0, "confirmed": 0, "cancelled": 0})
        for f, v in n.items():
            cur[f] += v
    out = [v for v in agg.values() if v["views"] or v["bookings"]]
    if not out:
        raise DashError("No bookings or visits found in that file.")
    return ("summary" if summary else "bookings"), out


def _parse_sections(rows, head):
    """LeadSigma's Marketing Attribution export: Report, Label, Account, Started views, Bookings, Confirmed, Cancelled,
    Share or rate. Sections: "How did you hear" (Label = the answer), "Referring dentist" (Label = the link or dentist),
    "Source / campaign" (Label = "Channel / source / medium / campaign")."""
    ix = {h: i for i, h in enumerate(head)}
    col = lambda r, name: (r[ix[name]].strip() if name in ix and ix[name] < len(r) else "")
    out = []
    for r in rows[1:]:
        rep, label = col(r, "report").lower(), col(r, "label")[:200]
        if not label:
            continue
        row = {k: "" for k in ROLLUP}
        row.update({"account": col(r, "account")[:200], "views": _num(col(r, "started views")),
                    "bookings": _num(col(r, "bookings")), "confirmed": _num(col(r, "confirmed")),
                    "cancelled": _num(col(r, "cancelled"))})
        if rep.startswith("how did"):
            row.update(section="heard", heard=label)
        elif rep.startswith("referring"):
            row.update(section="link", dentist=label)
        elif rep.startswith("source"):
            parts = [x.strip() for x in label.split(" / ", 3)] + ["", "", "", ""]
            ch, src, med, camp = parts[:4]
            none = lambda v: "" if v in ("(none)", "(not set)") else v
            row.update(section="source", channel=ch, source=none(src), medium=none(med), campaign=none(camp))
        else:
            continue
        if row["views"] or row["bookings"]:
            out.append(row)
    if not any(r["section"] == "source" for r in out) and not any(r["section"] == "heard" for r in out):
        raise DashError("No bookings or visits found in that file.")
    return out


def import_csv(data, filename, by, period_start=None, period_end=None):
    """Store an export's counts. A file with dates replaces what was stored for those days; a summary file (no dates)
    replaces the summary stored for the same period. Returns a short description."""
    kind, rows = parse(data, filename)
    days = sorted({r["day"] for r in rows if r["day"]})
    with db.conn() as c:
        if days:
            c.execute("DELETE FROM ls_counts WHERE day BETWEEN ? AND ?", (days[0], days[-1]))
        else:
            ps = (period_start or date.today() - timedelta(days=29)).isoformat()
            pe = (period_end or date.today()).isoformat()
            olds = [r["id"] for r in c.execute("SELECT id FROM ls_uploads WHERE kind='summary' AND period_start=? AND period_end=?",
                                               (ps, pe))]
            for o in olds:
                c.execute("DELETE FROM ls_counts WHERE upload_id=?", (o,))
        cur = c.execute("INSERT INTO ls_uploads(filename, kind, rows, first_day, last_day, period_start, period_end, by, at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (filename[:200], kind, len(rows), days[0] if days else "", days[-1] if days else "",
                         "" if days else (period_start or date.today() - timedelta(days=29)).isoformat(),
                         "" if days else (period_end or date.today()).isoformat(), by, db.now()))
        uid = cur.lastrowid
        c.executemany(f"INSERT INTO ls_counts({', '.join(ROLLUP)}, views, bookings, confirmed, cancelled, upload_id) "
                      f"VALUES ({', '.join('?' * len(ROLLUP))}, ?, ?, ?, ?, ?)",
                      [[r[k] for k in ROLLUP] + [r["views"], r["bookings"], r["confirmed"], r["cancelled"], uid] for r in rows])
    total = sum(r["bookings"] for r in rows if r["section"] in ("source", "booking"))
    span = f"{days[0]} to {days[-1]}" if days else "the period it was exported for"
    return f"Imported {total:,} booking{'' if total == 1 else 's'} from LeadSigma ({span})."


HEARD_MIN = 2   # a "How did you hear" answer used fewer times than this in an import is free text: kept only as "Other"


def import_days(days, by):
    """The scheduled LeadSigma run: {"YYYY-MM-DD": that day's Marketing Attribution CSV}. Each day's counts replace what
    was stored for that day. Free-text "How did you hear" answers (rare ones, which can hold names) become "Other"."""
    if not isinstance(days, dict) or not days:
        raise DashError("No days were sent.")
    rows = []
    for day, text in days.items():
        d = date.fromisoformat(str(day)[:10]).isoformat()
        if not isinstance(text, str) or len(text) > 2 * 1024 * 1024:
            raise DashError(f"The file for {d} isn't readable.")
        lines = list(csv.reader(io.StringIO(text)))
        lines = [r for r in lines if any((x or "").strip() for x in r)]
        if len(lines) < 2:
            continue            # nothing happened that day
        head = [(h or "").strip().lower() for h in lines[0]]
        if "report" not in head or "label" not in head:
            raise DashError(f"The file for {d} isn't LeadSigma's Marketing Attribution export.")
        try:
            got = _parse_sections(lines, head)
        except DashError:
            continue
        for r in got:
            r["day"] = d
        rows += got
    used = {}
    for r in rows:
        if r["section"] == "heard":
            k = _norm(r["heard"])
            used[k] = used.get(k, 0) + r["bookings"]
    for r in rows:
        if r["section"] == "heard" and used.get(_norm(r["heard"]), 0) < HEARD_MIN:
            r["heard"] = "Other"
    agg = {}
    for r in rows:
        k = tuple(r[c] for c in ROLLUP)
        cur = agg.setdefault(k, {**r, "views": 0, "bookings": 0, "confirmed": 0, "cancelled": 0})
        for f in ("views", "bookings", "confirmed", "cancelled"):
            cur[f] += r[f]
    rows = list(agg.values())
    first, last = min(days)[:10], max(days)[:10]
    with db.conn() as c:
        c.execute("DELETE FROM ls_counts WHERE day BETWEEN ? AND ?", (first, last))
        uid = c.execute("INSERT INTO ls_uploads(filename, kind, rows, first_day, last_day, period_start, period_end, by, at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        ("Scheduled LeadSigma run", "daily", len(rows), first, last, "", "", by, db.now())).lastrowid
        c.executemany(f"INSERT INTO ls_counts({', '.join(ROLLUP)}, views, bookings, confirmed, cancelled, upload_id) "
                      f"VALUES ({', '.join('?' * len(ROLLUP))}, ?, ?, ?, ?, ?)",
                      [[r[k] for k in ROLLUP] + [r["views"], r["bookings"], r["confirmed"], r["cancelled"], uid] for r in rows])
    total = sum(r["bookings"] for r in rows if r["section"] == "source")
    return {"bookings": total, "first": first, "last": last, "days": len(days), "rows": len(rows)}


def last_upload():
    with db.conn() as c:
        r = c.execute("SELECT * FROM ls_uploads ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def _ls_rows(start, end):
    """Count rows for a period: dated rows in it, or the latest summary upload when no dated rows exist."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM ls_counts WHERE day != '' AND day BETWEEN ? AND ?",
                                           (start.isoformat(), end.isoformat()))]
        if rows:
            return rows, None
        up = c.execute("SELECT * FROM ls_uploads WHERE kind='summary' ORDER BY id DESC LIMIT 1").fetchone()
        if not up:
            return [], None
        return [dict(r) for r in c.execute("SELECT * FROM ls_counts WHERE upload_id=?", (up["id"],))], dict(up)


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def match_account(name, practices):
    """A LeadSigma account name -> practice id (exact, then one name containing the other, then shared words)."""
    n = _norm(name)
    if not n:
        return None
    by = {_norm(p["name"]): p["id"] for p in practices}
    if n in by:
        return by[n]
    hit = [pid for pn, pid in by.items() if pn and (pn in n or n in pn)]
    if len(hit) == 1:
        return hit[0]
    stop = {"orthodontics", "orthodontic", "dental", "dentistry", "pediatric", "of", "by", "dr", "the", "family", "specialists",
            "specialty", "services"}
    words = set(n.split()) - stop
    best = [(len(words & (set(pn.split()) - stop)), pid) for pn, pid in by.items()]
    best = [b for b in best if b[0]]
    best.sort(reverse=True)
    if best and (len(best) == 1 or best[0][0] > best[1][0]):
        return best[0][1]
    return None


def link_name(label):
    """'BandeenWebsite (BandeenWebsite)' -> 'Bandeen website'; 'MCAD (MCAD)' -> 'MCAD'; names of dentists unchanged."""
    m = re.match(r"^(.*?) \((.*)\)$", label or "")
    if m and m.group(1).replace(" ", "").lower() == m.group(2).replace(" ", "").lower():
        label = m.group(1)
    return re.sub(r"(?<=[a-z])\s?Website$", " website", label)


def leadsigma(start, end, practices):
    rows, summary = _ls_rows(start, end)
    if not rows:
        return None
    main = [r for r in rows if r["section"] in ("", "source", "booking")]
    heard_rows = [r for r in rows if r["section"] in ("heard", "booking")]
    link_rows = [r for r in rows if r["section"] in ("link", "booking")]
    tot = {k: sum(r[k] for r in main) for k in ("views", "bookings", "confirmed", "cancelled")}
    direct = sum(r["bookings"] for r in main
                 if not (r["source"] or r["medium"] or r["campaign"]) or (r["channel"] or "").lower().startswith("direct"))
    unfilled = sorted({v for r in main for v in (r["campaign"], r["source"], r["medium"]) if UNFILLED.search(v or "")})
    heard = {}
    for r in heard_rows:
        h = (r["heard"] or "").strip()
        if h and r["bookings"]:
            key = h[:1].upper() + h[1:].lower()
            heard[key] = heard.get(key, 0) + r["bookings"]
    heard_list = sorted(heard.items(), key=lambda x: -x[1])
    answered = sum(heard.values())
    top_heard = heard_list[:4]
    rest = answered - sum(n for _, n in top_heard)
    if rest > 0:
        top_heard.append(("Everything else", rest))
    links, camps = {}, {}
    for r in link_rows:
        if r["dentist"]:
            d = links.setdefault((r["dentist"], r["account"]), {"name": link_name(r["dentist"]), "account": r["account"],
                                                                "views": 0, "bookings": 0})
            d["views"] += r["views"]
            d["bookings"] += r["bookings"]
    for r in main:
        if r["campaign"] and not UNFILLED.search(r["campaign"]) and not r["campaign"].isdigit():
            d = camps.setdefault(r["campaign"], {"campaign": r["campaign"], "sources": [], "views": 0, "bookings": 0})
            d["views"] += r["views"]
            d["bookings"] += r["bookings"]
            if r["source"] and r["views"]:
                d["sources"].append((r["source"], r["views"]))
    link_list = sorted([l for l in links.values() if l["views"] >= 10],
                       key=lambda l: (-l["bookings"], -l["views"]))[:6]
    for l in link_list:
        l["rate"] = l["bookings"] / l["views"] if l["views"] else None
    dead = sorted([d for d in camps.values() if d["views"] >= 200 and not d["bookings"]], key=lambda d: -d["views"])
    dead_links = sorted([l for l in links.values() if l["views"] >= 200 and not l["bookings"]], key=lambda l: -l["views"])
    by_practice = {}
    for r in (link_rows if any(r["section"] == "link" for r in rows) else main):
        pid = match_account(r["account"], practices)
        if pid:
            by_practice[pid] = by_practice.get(pid, 0) + r["bookings"]
    return {**tot, "rate": tot["bookings"] / tot["views"] if tot["views"] else None,
            "confirm_rate": tot["confirmed"] / tot["bookings"] if tot["bookings"] else None,
            "direct": direct, "unfilled": unfilled, "heard": top_heard, "answered": answered,
            "ad_heard": sum(n for h, n in heard.items() if re.search(r"facebook|instagram|tiktok|advert|\bad\b|google ad", h, re.I)),
            "links": link_list, "dead": dead, "dead_links": dead_links, "by_practice": by_practice,
            "summary": summary}


# ------------------------------------------------------------------ Power BI (typed in until connected)
def budget():
    try:
        b = json.loads(db.get_setting("dash_budget") or "{}")
    except ValueError:
        b = {}
    lines = []
    for key, label in (("promo", "Promotion / M&E"), ("adv", "Advertising / SEO / Web")):
        yb, ytd = b.get(f"{key}_budget"), b.get(f"{key}_ytd")
        if yb:
            lines.append({"label": label, "budget": yb, "ytd": ytd or 0, "left": yb - (ytd or 0),
                          "used": (ytd or 0) / yb if yb else 0})
    raw = {k: (int(v) if isinstance(v, float) and v.is_integer() else v) for k, v in b.items()}
    return {"lines": lines, "through": b.get("through", ""), "at": b.get("at", ""), "by": b.get("by", ""), "raw": raw}


def set_budget(form, by):
    def money(k):
        v = str(form.get(k) or "").replace("$", "").replace(",", "").strip()
        if not v:
            return None
        try:
            return round(float(v), 2)
        except ValueError:
            raise DashError("Budget amounts should be numbers, like 410000.")
    vals = {k: money(k) for k in ("promo_budget", "promo_ytd", "adv_budget", "adv_ytd")}
    through = str(form.get("through") or "").strip()[:7]
    if through and not re.match(r"^\d{4}-\d{2}$", through):
        raise DashError("Pick the month the numbers run through.")
    db.set_setting("dash_budget", json.dumps({**vals, "through": through, "at": db.now(), "by": by}))


# ------------------------------------------------------------------ the dashboard
def _money(v):
    return report.money(v)


def attention(rep, ls, per):
    """Up to five short findings, most useful first: (title, body, source)."""
    out = []
    t = rep["totals"]
    rows = [r for r in rep["rows"] if r["active"]]
    fix_href, fix_on = None, False
    if ls and ls["bookings"]:
        from . import tracking
        st = tracking.status()
        fix_on = bool(st["on"])
        fix_href = "/practices#tracking"
        share = ls["direct"] / ls["bookings"]
        if share >= 0.5 or ls["unfilled"]:
            if ls["unfilled"] and fix_on:
                extra = (f" Google Ads tracking was fixed in the hub, but this export still shows {', '.join(ls['unfilled'][:2])}: "
                         "upload an export covering days after the fix to see campaigns by name.")
            elif ls["unfilled"]:
                extra = (f" Ad links reach LeadSigma with {', '.join(ls['unfilled'][:2])} as plain text, so bookings from ads "
                         "can't be tied to a campaign. “Fix tracking in every account” on the Practices page repairs every Google Ads account in one click.")
            else:
                extra = " Add UTM tags to ad and post links so bookings can be tied to them."
            out.append((f"{share:.0%} of online bookings have no source ({ls['direct']} of {ls['bookings']})",
                        "Without a source, cost per booking and cost per start can't be worked out." + extra, "LeadSigma",
                        fix_href if ls["unfilled"] and not fix_on else None))
        for d in ls["dead"][:1]:
            names = {"an": "Meta's Audience Network (ads in other apps)", "fb": "Facebook", "ig": "Instagram", "adwords": "Google Ads"}
            src = ", ".join(f"{v:,} from {names.get(s_, s_)}" for s_, v in sorted(d["sources"], key=lambda x: -x[1]))
            out.append((f"“{d['campaign'][:70]}”: {d['views']:,} visits, no bookings",
                        f"{src + '. ' if src else ''}People reached the booking page but nobody booked: check the audience, "
                        "placements and the landing page.", "LeadSigma"))
        for l in ls["dead_links"][:1]:
            if True:
                out.append((f"{l['name']} link: {l['views']:,} visits, no bookings",
                            f"For {l['account'] or 'this practice'}. People open the booking page from this link but don't finish.",
                            "LeadSigma"))
    if t["cpa"]:
        worst = max((r for r in rows if r["paid"]["spend"] >= 100 and r["paid"]["conversions"] >= 1),
                    key=lambda r: r["paid"]["cpa"] or 0, default=None)
        if worst and worst["paid"]["cpa"] and worst["paid"]["cpa"] > 2.5 * t["cpa"]:
            out.append((f"{worst['p']['name']}: {_money(worst['paid']['cpa'])} per ad conversion",
                        f"The group average is {_money(t['cpa'])}. {_money(worst['paid']['spend'])} spent for "
                        f"{report.num(worst['paid']['conversions'])} conversions.", "Hub"))
    zero = [r for r in rows if r["paid"]["spend"] >= 50 and not r["paid"]["conversions"]]
    if zero:
        amt = sum(r["paid"]["spend"] for r in zero)
        names = ", ".join(r["p"]["name"].replace(" Orthodontics", "") for r in sorted(zero, key=lambda r: -r["paid"]["spend"])[:6])
        out.append((f"{_money(amt)} ({amt / t['spend']:.0%} of spend) went to {len(zero)} practice{'' if len(zero) == 1 else 's'} "
                    "with no ad conversions", f"{names}{' and others' if len(zero) > 6 else ''}. Check conversion tracking "
                    "before cutting: some may be booking through their website instead.", "Hub"))
    idle = [r for r in rows if r["budget_total"] and not r["paid"]["spend"]]
    if idle:
        names = ", ".join(r["p"]["name"].replace(" Orthodontics", "") for r in idle[:5])
        out.append((f"{len(idle)} practice{'' if len(idle) == 1 else 's'} ha{'s' if len(idle) == 1 else 've'} a budget but spent nothing",
                    f"{names}. Campaigns are off, or the budgets need updating.", "Hub"))
    if t["visits_prev"] >= 6 and t["visits"] <= t["visits_prev"] * 0.4:
        out.append((f"Field visits fell to {t['visits']} (from {t['visits_prev']})",
                    "Possibly visits not logged rather than not made. Visits can be logged straight from the Calendar.", "Hub"))
    if t["goal_of"] and t["goal_met"] < t["goal_of"] / 2:
        out.append((f"{t['goal_met']} of {t['goal_of']} practices met their review goal",
                    f"{t['reviews_new']} new Google reviews in the {'week' if per['kind'] == 'week' else 'month'}.", "Hub"))
    return [{"n": i + 1, "title": x[0], "body": x[1], "src": x[2], "href": x[3] if len(x) > 3 else None}
            for i, x in enumerate(out[:5])]


def build(kind="week", key=None, today=None):
    kind = "month" if kind == "month" else "week"
    rep = report.build(kind, key, today)
    per = rep["period"]
    plist = db.keep_mine(db.practices())
    # LeadSigma's export is company-wide, so it's only for people who see every practice
    ls = leadsigma(per["start"], per["end"], plist) if db.only_ids() is None else None
    t = rep["totals"]
    chg = lambda cur, prev: report._pct_change(cur, prev)
    funnel = [
        {"step": "Ad spend", "value": _money(t["spend"]),
         "sub": " · ".join(x for x in (_arrow(chg(t["spend"], t["prev_spend"]), per),
                                       f"{report.pct(t['pace'])} of budget" if t["budget"] else "") if x), "src": "Hub · Google & Meta"},
        {"step": "Ad conversions", "value": report.num(t["conversions"]),
         "sub": " · ".join(x for x in (f"{_money(t['cpa'])} each" if t["cpa"] else "", _arrow(chg(t["conversions"], t["prev_conversions"]), per)) if x),
         "src": "Hub · Google & Meta"},
    ]
    note = ""
    if ls:
        if ls["summary"]:
            f = lambda v: f"{date.fromisoformat(v):%b} {date.fromisoformat(v).day}" if v else "?"
            note = (f"LeadSigma numbers are from the export covering {f(ls['summary']['period_start'])}–"
                    f"{f(ls['summary']['period_end'])}")
        funnel += [
            {"step": "Booking page visits", "value": f"{ls['views']:,}" if ls["views"] else "—",
             "sub": "people who opened online booking" if ls["views"] else "not in this export", "src": "LeadSigma"},
            {"step": "Consults booked", "value": f"{ls['bookings']:,}",
             "sub": f"{ls['rate']:.1%} of visits" if ls["rate"] else "online bookings", "src": "LeadSigma"},
            {"step": "Confirmed", "value": f"{ls['confirmed']:,}",
             "sub": f"{ls['confirm_rate']:.1%} of bookings" if ls["confirm_rate"] is not None else "", "src": "LeadSigma"},
        ]
    else:
        funnel += [{"step": s, "value": "Upload", "sub": "LeadSigma export", "src": "LeadSigma", "todo": True}
                   for s in ("Booking page visits", "Consults booked", "Confirmed")]
    # Exams and starts: MCDP Reports (Practice Scorecard) through Practice 360's import. It's monthly, so a month view
    # uses that month when it's complete and in, otherwise the latest complete month, labelled.
    from . import p360
    months = p360.drapp_months()
    dm = None
    if months:
        want = per["start"].strftime("%Y-%m")
        dm = want if (kind == "month" and want in months) else months[0]
    only = db.only_ids()
    gt = p360.group_totals(dm, sorted(only) if only is not None else None) if dm else None
    mlabel = date.fromisoformat(dm + "-01").strftime("%B %Y") if dm else ""
    same = bool(dm) and kind == "month" and dm == per["start"].strftime("%Y-%m")
    if gt and gt["starts"] is not None:
        funnel += [{"step": "Exams", "value": f"{gt['exams']:,.0f}" if gt["exams"] is not None else "—",
                    "sub": "new-patient exams" + ("" if same else f" in {mlabel}"), "src": "MCDP Reports · Scorecard"},
                   {"step": "Starts", "value": f"{gt['starts']:,.0f}",
                    "sub": (f"{gt['success']:.1f}% of exams" if gt["success"] is not None else "") + ("" if same else f" · {mlabel}"),
                    "src": "MCDP Reports · Scorecard"}]
    else:
        funnel += [{"step": "Exams", "value": "Connect", "sub": "kept consults, from Dolphin", "src": "Power BI · Ortho KPIs", "todo": True},
                   {"step": "Starts", "value": "Connect", "sub": "and exam-to-start rate", "src": "Power BI · Ortho KPIs", "todo": True}]
    # ad spend per start, for the same month as the starts
    north = None
    if gt and gt["starts"]:
        spend = t["spend"] if same else None
        if spend is None:
            with db.conn() as c:
                q = "" if only is None else f" AND practice_id IN ({','.join(str(int(i)) for i in only) or 'NULL'})"
                r_ = c.execute(f"SELECT SUM(value) v FROM p360_metrics WHERE source='Hub' AND metric='Ad spend' AND period=?{q}", (dm,)).fetchone()
            spend = r_["v"] if r_ and r_["v"] else None
        if spend:
            north = {"value": _money(spend / gt["starts"]), "month": mlabel, "spend": _money(spend), "starts": f"{gt['starts']:,.0f}"}
    active = [r for r in rep["rows"] if r["active"]]
    table = sorted(active, key=lambda r: -r["paid"]["spend"])[:10]
    rows = [{"id": r["p"]["id"], "name": r["p"]["name"], "spend": r["paid"]["spend"], "conv": r["paid"]["conversions"],
             "cpa": r["paid"]["cpa"], "bookings": (ls or {}).get("by_practice", {}).get(r["p"]["id"]) if ls else None,
             "starts": p360.practice_month(r["p"]["id"], "Starts", dm) if dm else None,
             "reviews": r["reviews"]["new"] if r["reviews"]["linked"] else None,
             "posts": r["organic"]["posts"] if r["organic"]["linked"] else None,
             "high": bool(r["paid"]["cpa"] and t["cpa"] and r["paid"]["cpa"] > 2.5 * t["cpa"])
             or (r["paid"]["spend"] >= 50 and not r["paid"]["conversions"])} for r in table]
    return {"period": per, "rep": rep, "totals": t, "ls": ls, "ls_note": note, "funnel": funnel,
            "attention": attention(rep, ls, per), "rows": rows, "practices": len(active), "budget": budget(),
            "upload": last_upload(), "limited": db.only_ids() is not None, "north": north, "drapp_month": mlabel,
            "drapp_same": same}


def _arrow(c, per):
    if c is None or abs(c) < 0.005:
        return ""
    return f"{'▲' if c > 0 else '▼'} {abs(c):.0%} on {per['prev_label']}"
