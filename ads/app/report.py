"""Master Reports: a monthly (or weekly snapshot) health report across every practice.

Cover: the period's headline numbers with the change against the period before, a short summary and suggestions
for the next period. Overview: each category (paid ads, organic posts, Google reviews, field visits) with the
practices ranked and the highs and lows called out. Then half a page per practice, with its locations.

Everything is computed from data the hub already keeps (the daily ad archive, posts, reviews, visits), so the
words and numbers always agree; the summary and suggestions are written from rules, not guessed.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from html import escape

from markupsafe import Markup

from . import db, field, organic, reviews, scope

CATS = (("paid", "Paid ads"), ("organic", "Organic posts"), ("reviews", "Google reviews"), ("field", "Field visits"))
COLOR = {"google": "#3B6FB6", "meta": "#6B5BC4", "paid": "#0F7471", "organic": "#2E8B7A", "reviews": "#C98A1B",
         "field": "#9A5B2E", "muted": "#CFC9BD", "good": "#0F7471", "bad": "#B5334A"}
PLATFORMS = (("google", "Google Ads"), ("meta", "Meta Ads"))


# ------------------------------------------------------------------ periods
def month_key(d):
    return d.strftime("%Y-%m")


def monday(d):
    return d - timedelta(days=d.weekday())


def default_key(kind, today=None):
    """Monthly: the last complete month. Weekly: the last complete Monday-to-Sunday week."""
    today = today or date.today()
    if kind == "week":
        return (monday(today) - timedelta(days=7)).isoformat()
    return month_key(today.replace(day=1) - timedelta(days=1))


def period(kind, key=None, today=None):
    today = today or date.today()
    key = key or default_key(kind, today)
    if kind == "week":
        try:
            start = monday(date.fromisoformat(key))
        except ValueError:
            start = monday(today) - timedelta(days=7)
        end = start + timedelta(days=6)
        partial = end >= today
        end = min(end, today)
        prev_start, prev_end = start - timedelta(days=7), start - timedelta(days=7) + (end - start)
        label = f"Week of {start.strftime('%b')} {start.day}–{'' if end.month == start.month else end.strftime('%b') + ' '}{end.day}, {end.year}"
        prev_label = "the week before"
        nxt = "next week"
        months = {month_key(start), month_key(end)}
    else:
        try:
            y, m = (int(x) for x in key.split("-"))
            start = date(y, m, 1)
        except ValueError:
            start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        last = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        partial = last >= today
        end = min(last, today)
        prev_start = (start - timedelta(days=1)).replace(day=1)
        prev_last = start - timedelta(days=1)
        # a whole month compares with the whole month before; a month in progress with the same days of it
        prev_end = min(prev_last, prev_start + (end - start)) if partial else prev_last
        label = start.strftime("%B %Y") + (" (to date)" if partial else "")
        prev_label = prev_start.strftime("%B") + (" (same days)" if partial else "")
        nxt = (last + timedelta(days=1)).strftime("%B")
        months = {month_key(start)}
    days = (end - start).days + 1
    return {"kind": kind, "key": start.isoformat() if kind == "week" else month_key(start), "start": start, "end": end,
            "days": days, "partial": partial, "label": label, "prev_start": prev_start, "prev_end": prev_end,
            "prev_label": prev_label, "next": nxt, "months": sorted(months),
            "title": "Weekly Snapshot" if kind == "week" else "Monthly Master Report"}


def month_options(today=None, n=12):
    today = today or date.today()
    out, d = [], today.replace(day=1)
    for _ in range(n):
        out.append((month_key(d), d.strftime("%B %Y") + (" (to date)" if d.month == today.month and d.year == today.year else "")))
        d = (d - timedelta(days=1)).replace(day=1)
    return out


def week_options(today=None, n=12):
    today = today or date.today()
    out, d = [], monday(today)
    for _ in range(n):
        end = d + timedelta(days=6)
        out.append((d.isoformat(), f"{d.strftime('%b')} {d.day} – {end.strftime('%b')} {end.day}"
                    + (" (to date)" if end >= today else "")))
        d -= timedelta(days=7)
    return out


# ------------------------------------------------------------------ numbers
def _pct_change(cur, prev):
    if prev in (None, 0) or cur is None:
        return None
    return (cur - prev) / prev


def _budget_share(p, platform, per):
    """The part of a monthly budget that belongs to this period's days."""
    b = float(p.get(f"{platform}_budget") or 0)
    if not b:
        return 0.0
    total = 0.0
    d = per["start"]
    while d <= per["end"]:
        total += b / calendar.monthrange(d.year, d.month)[1]
        d += timedelta(days=1)
    return round(total, 2)


def _paid(p, start, end):
    out = {"spend": 0.0, "clicks": 0, "impressions": 0, "conversions": 0.0, "by": {}, "daily": {}}
    for key, _ in PLATFORMS:
        rows = scope.apply(db.archived(p["id"], key, start, end), p, platform=key)
        sp = round(sum(r["spend"] for r in rows), 2)
        cv = round(sum(r["conversions"] for r in rows), 2)
        out["by"][key] = {"spend": sp, "conversions": cv, "clicks": sum(r["clicks"] for r in rows),
                          "campaigns": len({r["campaign"] for r in rows if r["spend"]})}
        out["spend"] += sp
        out["conversions"] += cv
        out["clicks"] += sum(r["clicks"] for r in rows)
        out["impressions"] += sum(r["impressions"] for r in rows)
        for r in rows:
            k = r["date"].isoformat()
            out["daily"].setdefault(k, {"google": 0.0, "meta": 0.0})[key] += r["spend"]
    out["spend"] = round(out["spend"], 2)
    out["conversions"] = round(out["conversions"], 2)
    out["cpa"] = out["spend"] / out["conversions"] if out["conversions"] else None
    camps = {}
    for key, _ in PLATFORMS:
        for r in scope.apply(db.archived(p["id"], key, start, end), p, platform=key):
            c = camps.setdefault((key, r["campaign"]), {"platform": key, "name": r["campaign"], "spend": 0.0, "conversions": 0.0})
            c["spend"] += r["spend"]
            c["conversions"] += r["conversions"]
    out["campaigns"] = sorted((c for c in camps.values() if c["spend"]), key=lambda c: -c["spend"])
    return out


def _organic(p, start, end):
    ids = organic.own_accounts(p)
    posts = db.posts_for(ids, start, end) if ids else []
    eng = sum(organic.engagement(x) for x in posts)
    views = sum(x["views"] or 0 for x in posts if x.get("views") is not None)
    by = {}
    for net in ("facebook", "instagram"):
        mine = [x for x in posts if x["network"] == net]
        by[net] = {"posts": len(mine), "views": sum(x["views"] or 0 for x in mine),
                   "engagement": sum(organic.engagement(x) for x in mine)}
    return {"linked": bool(ids), "posts": len(posts), "views": views, "engagement": eng,
            "rate": eng / views if views else None, "by": by}


def _reviews(p, start, end, locs, goal):
    names = [l["name"] for l in locs]
    out = {"linked": bool(names), "new": 0, "stars": None, "rating": None, "total": None, "goal": goal, "replied": 0}
    if not names:
        return out
    q = ",".join("?" * len(names))
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT stars FROM gbp_reviews WHERE location IN ({q}) AND substr(created, 1, 10) BETWEEN ? AND ?",
            (*names, start.isoformat(), end.isoformat()))]
        out["replied"] = c.execute(
            f"SELECT COUNT(*) FROM inbox WHERE source='google' AND account_id IN ({q}) AND outcome IN ('replied', 'outside') "
            "AND substr(handled_at, 1, 10) BETWEEN ? AND ?", (*names, start.isoformat(), end.isoformat())).fetchone()[0]
    out["new"] = len(rows)
    stars = [r["stars"] for r in rows if r["stars"]]
    out["stars"] = sum(stars) / len(stars) if stars else None
    rated = [(l["rating"], l["total"]) for l in locs if l.get("rating") and l.get("total")]
    n = sum(t for _, t in rated)
    out["rating"] = sum(r * t for r, t in rated) / n if n else None
    out["total"] = n or None
    return out


def _field(p, start, end):
    try:
        rows = field.visits_between(start, end + timedelta(days=1), practice_id=p["id"])
    except Exception:
        rows = []
    return {"visits": len(rows), "spend": round(sum(r.get("cost") or 0 for r in rows), 2),
            "offices": len({r["office_id"] for r in rows}), "agents": len({r["user_name"] for r in rows if r.get("user_name")})}


def _locations(p, gbp_by_p, office_by_p):
    """The practice's offices for reference: Google Business locations (name and address), else its map offices.
    When a listing is just the practice's own name, only its address is shown."""
    locs = [{"name": l["title"], "address": l.get("address") or ""} for l in gbp_by_p.get(p["id"], [])]
    if not locs:
        locs = [{"name": o["label"], "address": ""} for o in office_by_p.get(p["id"], [])]
    pn = " ".join((p["name"] or "").lower().split())
    for l in locs:
        if l["address"] and " ".join(l["name"].lower().split()) == pn:
            l["name"], l["address"] = l["address"], ""
    return sorted(locs, key=lambda l: l["name"].lower())


def _review_goal(pid, per, goals):
    if per["kind"] == "week":
        g = reviews.goal_for(pid, per["months"][0], goals)
        return max(1, round(g * per["days"] / 30))
    g = reviews.goal_for(pid, per["months"][0], goals)
    if per["partial"]:
        last = calendar.monthrange(per["start"].year, per["start"].month)[1]
        return max(1, round(g * per["days"] / last))
    return g


def _is_master(p, plist):
    return (not scope.has_filter(p) and bool(p.get("meta_account_id") or p.get("google_customer_id"))
            and any(scope.has_filter(q) for q in plist if q["id"] != p["id"]))


# ------------------------------------------------------------------ the report
def build(kind="month", key=None, today=None):
    per = period(kind, key, today)
    plist = db.practices()
    masters = [p for p in plist if _is_master(p, plist)]
    shown = [p for p in plist if p not in masters]
    goals = reviews._goal_rows()
    locs = reviews.locations()
    gbp_by_p = {}
    for l in locs:
        if l.get("practice_id"):
            gbp_by_p.setdefault(l["practice_id"], []).append(l)
    office_by_p = {}
    try:
        links = field.practice_links()
        from . import places
        for o in places.offices():
            if links.get(o["id"]):
                office_by_p.setdefault(links[o["id"]], []).append(o)
    except Exception:
        pass
    open_by_p = {}
    try:
        owners = reviews.account_practice(plist)
        with db.conn() as c:
            for r in c.execute("SELECT source, account_id FROM inbox WHERE status='open'"):
                pid = owners.get((r["source"], r["account_id"]))
                open_by_p[pid] = open_by_p.get(pid, 0) + 1
    except Exception:
        pass

    rows = []
    for p in shown:
        cur = _paid(p, per["start"], per["end"])
        prev = _paid(p, per["prev_start"], per["prev_end"])
        budget = {k: _budget_share(p, k, per) for k, _ in PLATFORMS}
        bt = sum(budget.values())
        org, org_prev = _organic(p, per["start"], per["end"]), _organic(p, per["prev_start"], per["prev_end"])
        g = _review_goal(p["id"], per, goals)
        rv, rv_prev = (_reviews(p, per["start"], per["end"], gbp_by_p.get(p["id"], []), g),
                       _reviews(p, per["prev_start"], per["prev_end"], gbp_by_p.get(p["id"], []), g))
        fv, fv_prev = _field(p, per["start"], per["end"]), _field(p, per["prev_start"], per["prev_end"])
        row = {"p": p, "paid": cur, "paid_prev": prev, "budget": budget, "budget_total": bt,
               "pace": cur["spend"] / bt if bt else None,
               "organic": org, "organic_prev": org_prev, "reviews": rv, "reviews_prev": rv_prev,
               "field": fv, "field_prev": fv_prev, "locations": _locations(p, gbp_by_p, office_by_p),
               "open": open_by_p.get(p["id"], 0)}
        row["active"] = bool(cur["spend"] or bt or org["linked"] or rv["linked"] or fv["visits"])
        rows.append(row)
    rows = [r for r in rows if r["active"]] + [r for r in rows if not r["active"]]
    for r in rows:
        r["notes"] = practice_notes(r, per)
        r["chart"] = daily_bars(r["paid"]["daily"], per, height=70, width=300)

    tot = totals(rows, masters, plist, per)
    rep = {"period": per, "rows": rows, "totals": tot, "masters": masters,
           "cats": categories(rows, per), "generated": db.now()}
    rep["has_prev"] = {"paid": bool(tot["prev_spend"]), "organic": bool(tot["organic_prev"]["posts"]),
                       "reviews": True, "field": bool(tot["visits_prev"] or tot["visits"])}
    rep["kpis"] = kpis(tot, per)
    rep["summary"] = summary(rep)
    rep["suggestions"] = suggestions(rep)
    rep["cover_chart"] = daily_bars(tot["daily"], per, height=110, width=640, legend=True)
    rep["mix_chart"] = mix_bars(tot)
    return rep


def totals(rows, masters, plist, per):
    """Whole-group numbers. Paid spend counts each account's campaign-day once (shared accounts are stored under
    every practice that uses them), so practices on the master account aren't double counted."""
    seen, spend, conv, clicks, daily = set(), 0.0, 0.0, 0, {}
    prev_seen, prev_spend, prev_conv = set(), 0.0, 0.0
    for p in plist:
        for key, _ in PLATFORMS:
            for r in db.archived(p["id"], key, per["start"], per["end"]):
                k = (key, r["account"], r["campaign"], r["date"])
                if k in seen:
                    continue
                seen.add(k)
                spend += r["spend"]
                conv += r["conversions"]
                clicks += r["clicks"]
                daily.setdefault(r["date"].isoformat(), {"google": 0.0, "meta": 0.0})[key] += r["spend"]
            for r in db.archived(p["id"], key, per["prev_start"], per["prev_end"]):
                k = (key, r["account"], r["campaign"], r["date"])
                if k in prev_seen:
                    continue
                prev_seen.add(k)
                prev_spend += r["spend"]
                prev_conv += r["conversions"]
    budget = 0.0
    for key, _ in PLATFORMS:
        mb = sum(_budget_share(m, key, per) for m in masters)
        on_master = {str(m.get("meta_account_id" if key == "meta" else "google_customer_id") or "") for m in masters}
        own = sum(r["budget"][key] for r in rows
                  if str(r["p"].get("meta_account_id" if key == "meta" else "google_customer_id") or "") not in on_master)
        budget += (mb + own) if mb else sum(r["budget"][key] for r in rows)
    by_platform = {}
    for key, _ in PLATFORMS:
        by_platform[key] = round(sum(v[key] for v in daily.values()), 2)
    assigned = sum(r["paid"]["spend"] for r in rows)
    organic_ids = sorted({a for q in plist for a in organic.own_accounts(q)})
    posts = db.posts_for(organic_ids, per["start"], per["end"]) if organic_ids else []
    posts_prev = db.posts_for(organic_ids, per["prev_start"], per["prev_end"]) if organic_ids else []

    def osum(ps):
        e = sum(organic.engagement(x) for x in ps)
        v = sum(x["views"] or 0 for x in ps if x.get("views") is not None)
        return {"posts": len(ps), "views": v, "engagement": e, "rate": e / v if v else None,
                "facebook": sum(1 for x in ps if x["network"] == "facebook"),
                "instagram": sum(1 for x in ps if x["network"] == "instagram")}
    rv_new = sum(r["reviews"]["new"] for r in rows)
    rv_prev = sum(r["reviews_prev"]["new"] for r in rows)
    rated = [(r["reviews"]["rating"], r["reviews"]["total"]) for r in rows if r["reviews"]["rating"] and r["reviews"]["total"]]
    n = sum(t for _, t in rated)
    linked_rv = [r for r in rows if r["reviews"]["linked"]]
    return {"spend": round(spend, 2), "conversions": round(conv, 2), "clicks": clicks,
            "cpa": spend / conv if conv else None, "budget": round(budget, 2), "pace": spend / budget if budget else None,
            "prev_spend": round(prev_spend, 2), "prev_conversions": round(prev_conv, 2),
            "prev_cpa": prev_spend / prev_conv if prev_conv else None,
            "unassigned": round(max(0.0, spend - assigned), 2) if masters else 0.0,
            "by_platform": by_platform, "daily": daily,
            "organic": osum(posts), "organic_prev": osum(posts_prev),
            "reviews_new": rv_new, "reviews_prev": rv_prev,
            "rating": sum(r * t for r, t in rated) / n if n else None, "reviews_total": n or None,
            "goal_met": sum(1 for r in linked_rv if r["reviews"]["new"] >= r["reviews"]["goal"]),
            "goal_of": len(linked_rv),
            "visits": sum(r["field"]["visits"] for r in rows), "visits_prev": sum(r["field_prev"]["visits"] for r in rows),
            "field_spend": round(sum(r["field"]["spend"] for r in rows), 2),
            "open": sum(r["open"] for r in rows),
            "practices": len([r for r in rows if r["active"]])}


def _tile(label, value, sub, cur, prev, up_is_good=True):
    c = _pct_change(cur, prev)
    if c is not None and abs(c) < 0.005:
        c = None                                   # "▼ 0%" says nothing
    good = None if c is None or abs(c) < 0.03 or up_is_good is None else ((c > 0) == up_is_good)
    return {"label": label, "value": value, "sub": sub, "change": c, "good": good}


def kpis(t, per):
    o, op = t["organic"], t["organic_prev"]
    return [
        _tile("Ad spend", money(t["spend"]), (f"{pct(t['pace'])} of {money(t['budget'])} budget" if t["budget"] else "no budget set"),
              t["spend"], t["prev_spend"], up_is_good=None),
        _tile("Conversions", num(t["conversions"]), f"{money(t['cpa'])} each" if t["cpa"] else "—",
              t["conversions"], t["prev_conversions"]),
        _tile("Cost per conversion", money(t["cpa"]), f"was {money(t['prev_cpa'])}" if t["prev_cpa"] else "—",
              t["cpa"], t["prev_cpa"], up_is_good=False),
        _tile("Organic engagements", num(o["engagement"]), f"{o['posts']} posts · {num(o['views'])} views",
              o["engagement"], op["engagement"]),
        _tile("New Google reviews", str(t["reviews_new"]),
              (f"{t['goal_met']} of {t['goal_of']} met goal" + (f" · ★ {t['rating']:.1f}" if t["rating"] else "")) if t["goal_of"] else "no locations linked",
              t["reviews_new"], t["reviews_prev"]),
        _tile("Field visits", str(t["visits"]), f"{money(t['field_spend'])} on drop-offs" if t["field_spend"] else "—",
              t["visits"], t["visits_prev"]),
    ]


# ------------------------------------------------------------------ highs and lows
def _rank(rows, val, label, fmt, reverse=True, cond=lambda r: True):
    items = [(r, val(r)) for r in rows if cond(r)]
    items = [(r, v) for r, v in items if v is not None]
    items.sort(key=lambda x: x[1], reverse=reverse)
    return [{"name": r["p"]["name"], "value": v, "text": fmt(v), "id": r["p"]["id"]} for r, v in items]


def money(v):
    return "—" if v is None else (f"${v:,.0f}" if abs(v) >= 1000 else f"${v:,.2f}")


def num(v):
    if v is None:
        return "—"
    v = float(v)
    for div, suf in ((1e6, "M"), (1e3, "k")):
        if abs(v) >= div:
            return f"{v / div:.1f}".rstrip("0").rstrip(".") + suf
    return f"{v:,.0f}" if v == int(v) else f"{v:,.1f}"


def pct(v, signed=False):
    if v is None:
        return "—"
    return (f"{v * 100:+.0f}%" if signed else f"{v * 100:.0f}%")


def categories(rows, per):
    act = [r for r in rows if r["active"]]
    paid = [r for r in act if r["paid"]["spend"] or r["budget_total"]]
    org = [r for r in act if r["organic"]["linked"]]
    rv = [r for r in act if r["reviews"]["linked"]]
    fv = [r for r in act if r["field"]["visits"] or r["field_prev"]["visits"]]
    out = {}
    out["paid"] = {
        "chart": _rank(paid, lambda r: r["paid"]["spend"], "Spend", money),
        "chart_label": "Ad spend",
        "highs": [h for h in [
            _first(_rank(paid, lambda r: r["paid"]["conversions"] or None, "", lambda v: f"{num(v)} conversions")),
            _first(_rank(paid, lambda r: r["paid"]["cpa"], "", lambda v: f"{money(v)} per conversion", reverse=False,
                         cond=lambda r: r["paid"]["conversions"] >= 3)),
        ] if h],
        "lows": [h for h in [
            _first(_rank(paid, lambda r: r["paid"]["cpa"], "", lambda v: f"{money(v)} per conversion",
                         cond=lambda r: r["paid"]["conversions"] >= 3)),
            _first(_rank(paid, lambda r: r["paid"]["spend"], "", lambda v: f"{money(v)} spent, no conversions",
                         cond=lambda r: r["paid"]["spend"] >= 100 and not r["paid"]["conversions"])),
            _first(_rank(paid, lambda r: r["pace"], "", lambda v: f"{pct(v)} of budget",
                         cond=lambda r: r["pace"] is not None and r["pace"] > 1.1)),
            _first(_rank(paid, lambda r: r["pace"], "", lambda v: f"only {pct(v)} of budget", reverse=False,
                         cond=lambda r: r["pace"] is not None and r["pace"] < 0.7)),
        ] if h],
    }
    out["organic"] = {
        "chart": _rank(org, lambda r: r["organic"]["engagement"], "Engagements", num),
        "chart_label": "Engagements on posts",
        "highs": [h for h in [
            _first(_rank(org, lambda r: r["organic"]["engagement"] or None, "", lambda v: f"{num(v)} engagements")),
            _first(_rank(org, lambda r: r["organic"]["rate"], "", lambda v: f"{pct(v)} engagement rate",
                         cond=lambda r: r["organic"]["posts"] >= (1 if per["kind"] == "week" else 3))),
        ] if h],
        "lows": [h for h in [
            _first(_rank(org, lambda r: r["organic"]["posts"], "", lambda v: f"{v} post{'' if v == 1 else 's'}",
                         reverse=False, cond=lambda r: r["organic"]["posts"] < (1 if per["kind"] == "week" else 4))),
            _first(_rank(org, lambda r: r["organic"]["rate"], "", lambda v: f"{pct(v)} engagement rate", reverse=False,
                         cond=lambda r: r["organic"]["posts"] >= 3 and r["organic"]["rate"] is not None)),
        ] if h],
    }
    out["reviews"] = {
        "chart": _rank(rv, lambda r: r["reviews"]["new"], "New reviews", lambda v: f"{v}"),
        "chart_label": "New Google reviews",
        "highs": [h for h in [
            _first(_rank(rv, lambda r: r["reviews"]["new"] or None, "", lambda v: f"{v} new review{'' if v == 1 else 's'}")),
            _first(_rank(rv, lambda r: r["reviews"]["rating"], "", lambda v: f"★ {v:.1f} rating",
                         cond=lambda r: (r["reviews"]["total"] or 0) >= 10)),
        ] if h],
        "lows": [h for h in [
            _first(_rank(rv, lambda r: r["reviews"]["new"] - r["reviews"]["goal"], "", lambda v: f"{-v} short of goal",
                         reverse=False, cond=lambda r: r["reviews"]["new"] < r["reviews"]["goal"])),
            _first(_rank(rv, lambda r: r["reviews"]["rating"], "", lambda v: f"★ {v:.1f} rating", reverse=False,
                         cond=lambda r: r["reviews"]["rating"] is not None and r["reviews"]["rating"] < 4.6)),
        ] if h],
    }
    out["field"] = {
        "chart": _rank(fv, lambda r: r["field"]["visits"], "Visits", lambda v: f"{v}"),
        "chart_label": "Field visits",
        "highs": [h for h in [_first(_rank(fv, lambda r: r["field"]["visits"] or None, "", lambda v: f"{v} visits"))] if h],
        "lows": [h for h in [
            _first(_rank(fv, lambda r: r["field"]["visits"], "", lambda v: "no visits", reverse=False,
                         cond=lambda r: not r["field"]["visits"] and r["field_prev"]["visits"])),
        ] if h],
    }
    for k, c in out.items():
        c["bars"] = hbars(c["chart"][:12], COLOR[k])
    return out


def _first(items):
    return items[0] if items else None


# ------------------------------------------------------------------ words
def _chg(cur, prev, good_up=True):
    c = _pct_change(cur, prev)
    if c is None or abs(c) < 0.03:
        return ""
    up = c > 0
    return f" ({'up' if up else 'down'} {abs(c) * 100:.0f}% on {{prev}})"


def summary(rep):
    t, per = rep["totals"], rep["period"]
    prev = per["prev_label"]
    out = []
    if t["spend"]:
        s = f"The practices spent {money(t['spend'])} on ads"
        if t["budget"]:
            s += f", {pct(t['spend'] / t['budget'])} of budget"
        s += _chg(t["spend"], t["prev_spend"]).format(prev=prev)
        if t["conversions"]:
            s += f", bringing in {num(t['conversions'])} conversions at {money(t['cpa'])} each"
            if t["prev_cpa"]:
                c = _pct_change(t["cpa"], t["prev_cpa"])
                if c is not None and abs(c) >= 0.03:
                    s += f" ({'up' if c > 0 else 'down'} {abs(c) * 100:.0f}% from {money(t['prev_cpa'])})"
        out.append(s + ".")
    o = t["organic"]
    if o["posts"]:
        s = f"{o['posts']} organic posts ({o['facebook']} Facebook, {o['instagram']} Instagram) drew {num(o['views'])} views and {num(o['engagement'])} engagements"
        s += _chg(o["engagement"], t["organic_prev"]["engagement"]).format(prev=prev)
        out.append(s + ".")
    if t["goal_of"]:
        s = f"Practices received {t['reviews_new']} new Google review{'' if t['reviews_new'] == 1 else 's'}"
        s += _chg(t["reviews_new"], t["reviews_prev"]).format(prev=prev)
        s += f"; {t['goal_met']} of {t['goal_of']} met their review goal"
        if t["rating"]:
            s += f", and the group's rating stands at ★ {t['rating']:.2f} across {num(t['reviews_total'])} reviews"
        out.append(s + ".")
    if t["visits"] or t["visits_prev"]:
        s = f"Field agents made {t['visits']} office visit{'' if t['visits'] == 1 else 's'}"
        s += _chg(t["visits"], t["visits_prev"]).format(prev=prev)
        if t["field_spend"]:
            s += f", spending {money(t['field_spend'])} on drop-offs"
        out.append(s + ".")
    if not out:
        out.append("There's no data for this period yet. Reports fill in as the hub syncs ads, posts, reviews and visits.")
    return out


def _issues(r, per, subj=None):
    """(priority, text) problems and opportunities for one practice, most important first. subj: how the
    practice is named in the sentence ("Heights Orthodontics" in the summary, "This practice" on its own page)."""
    name = subj or r["p"]["name"]
    its = "Its" if subj else (name + "'" if name.endswith("s") else name + "'s")
    out = []
    pd, pv = r["paid"], r["paid_prev"]
    if r["pace"] is not None and r["pace"] > 1.15:
        out.append((90, f"{name} spent {pct(r['pace'])} of its ad budget for the period. Lower daily budgets or "
                        f"tighten targeting so it doesn't overspend."))
    if r["pace"] is not None and r["pace"] < 0.7 and r["budget_total"] >= 50:
        if not pd["spend"]:
            out.append((85, f"{name} has a {money(r['budget_total'])} ad budget for the period but no ad spend. "
                            f"Turn its campaigns on (or create one) so the budget is used."))
        else:
            out.append((70, f"{name} used only {pct(r['pace'])} of its ad budget. Check its campaigns are running and "
                            f"approved; raise bids or broaden keywords if they are."))
    if pd["spend"] >= 100 and not pd["conversions"]:
        out.append((95, f"{name} spent {money(pd['spend'])} on ads with no conversions. Check conversion tracking, "
                        f"then use Prepare fixes to cut the waste."))
    if pd["cpa"] and pv["cpa"] and pd["conversions"] >= 3:
        c = _pct_change(pd["cpa"], pv["cpa"])
        if c is not None and c > 0.25:
            out.append((80, f"{its} cost per conversion rose {c * 100:.0f}% to {money(pd['cpa'])}. Review search terms "
                            f"and ads (Prepare fixes, Score this ad)."))
    rv = r["reviews"]
    if rv["linked"] and rv["new"] < rv["goal"]:
        out.append((60, f"{name} got {rv['new']} of {rv['goal']} Google reviews. Ask happy patients at checkout and "
                        f"send a review link after debonds."))
    if r["open"]:
        n = r["open"]
        out.append((65, f"{name} has {n} review{'s' if n != 1 else ''} or comment{'s' if n != 1 else ''} "
                        f"waiting for a reply in the inbox."))
    o = r["organic"]
    low = 1 if per["kind"] == "week" else 4
    if o["linked"] and o["posts"] < low:
        out.append((50, f"{name} posted {o['posts']} time{'' if o['posts'] == 1 else 's'}. Aim for at least "
                        f"{'2 a week' if per['kind'] == 'week' else '8 a month'} across Facebook and Instagram."))
    if r["field_prev"]["visits"] and not r["field"]["visits"]:
        out.append((40, f"No field visits {'this period' if subj else 'for ' + name} "
                        f"(there were {r['field_prev']['visits']} the period before)."))
    return sorted(out, key=lambda x: -x[0])


def practice_notes(r, per):
    """Up to three short notes for a practice's half page: what went well, then what to do next."""
    notes = []
    pd, pv = r["paid"], r["paid_prev"]
    if pd["conversions"] and pv["conversions"] and pd["conversions"] > pv["conversions"] * 1.15:
        notes.append(("good", f"Conversions up {(_pct_change(pd['conversions'], pv['conversions']) * 100):.0f}% to {num(pd['conversions'])}."))
    elif r["reviews"]["linked"] and r["reviews"]["new"] >= r["reviews"]["goal"] and r["reviews"]["new"]:
        notes.append(("good", f"Review goal met: {r['reviews']['new']} new reviews."))
    elif r["organic"]["rate"] and r["organic"]["posts"] >= 3 and r["organic"]["rate"] >= 0.05:
        notes.append(("good", f"Strong engagement on posts ({pct(r['organic']['rate'])})."))
    for _, text in _issues(r, per, "This practice")[:3 - len(notes)]:
        notes.append(("todo", text))
    return notes


def suggestions(rep):
    per = rep["period"]
    pool = []
    for r in rep["rows"]:
        if r["active"]:
            pool += _issues(r, per)
    pool.sort(key=lambda x: -x[0])
    t = rep["totals"]
    if t["unassigned"] >= 100:
        pool.append((75, f"{money(t['unassigned'])} of ad spend isn't assigned to any practice. Give those campaigns "
                         f"a practice's filter word, or move them on the master record's page."))
    pool.sort(key=lambda x: -x[0])
    out = [s for _, s in pool[:5]]          # the five that matter most fit the cover; each practice page has its own
    if not out:
        out.append(f"Keep {per['next']}'s plan steady: budgets, posting and review requests are all on track.")
    return out


# ------------------------------------------------------------------ charts (inline SVG, same look everywhere)
def hbars(items, color, width=340):
    if not items:
        return Markup('<p class="rp-none">No data for this period.</p>')
    top = max((i["value"] or 0) for i in items) or 1
    row, lw, h = 18, 128, 0
    parts = []
    for n, i in enumerate(items):
        y = n * row
        w = max(1.5, (width - lw - 70) * (i["value"] or 0) / top)
        parts.append(f'<text x="0" y="{y + 12}" class="rp-l">{escape(i["name"][:22])}</text>'
                     f'<rect x="{lw}" y="{y + 3}" width="{w:.1f}" height="11" rx="2" fill="{color}"/>'
                     f'<text x="{lw + w + 6:.1f}" y="{y + 12}" class="rp-v">{escape(i["text"])}</text>')
        h = y + row
    return Markup(f'<svg class="rp-svg" viewBox="0 0 {width} {h}" width="100%" role="img" aria-label="Ranking">{"".join(parts)}</svg>')


def daily_bars(daily, per, height=60, width=300, legend=False):
    """Daily ad spend as stacked columns (Google, Meta) across the period."""
    days = []
    d = per["start"]
    while d <= per["end"]:
        v = daily.get(d.isoformat(), {"google": 0.0, "meta": 0.0})
        days.append((d, v.get("google", 0.0), v.get("meta", 0.0)))
        d += timedelta(days=1)
    top = max([g + m for _, g, m in days] + [0])
    if not top:
        return Markup('<p class="rp-none">No ad spend in this period.</p>')
    pad_b = 14
    bw = width / max(1, len(days))
    parts = []
    for n, (d, g, m) in enumerate(days):
        x = n * bw
        hg = (height - pad_b) * g / top
        hm = (height - pad_b) * m / top
        y0 = height - pad_b
        if hg:
            parts.append(f'<rect x="{x + bw * 0.15:.1f}" y="{y0 - hg:.1f}" width="{bw * 0.7:.1f}" height="{hg:.1f}" fill="{COLOR["google"]}"/>')
        if hm:
            parts.append(f'<rect x="{x + bw * 0.15:.1f}" y="{y0 - hg - hm:.1f}" width="{bw * 0.7:.1f}" height="{hm:.1f}" fill="{COLOR["meta"]}"/>')
        if len(days) <= 8 or d.day in (1, 8, 15, 22, 29) or n == len(days) - 1:
            lab = d.strftime("%a") if per["kind"] == "week" else str(d.day)
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{height - 2}" text-anchor="middle" class="rp-ax">{lab}</text>')
    parts.append(f'<line x1="0" y1="{height - pad_b}" x2="{width}" y2="{height - pad_b}" class="rp-base"/>')
    leg = ""
    if legend:
        leg = (f'<span class="rp-key"><i style="background:{COLOR["google"]}"></i>Google Ads</span>'
               f'<span class="rp-key"><i style="background:{COLOR["meta"]}"></i>Meta Ads</span>'
               f'<span class="rp-key muted">peak day {money(top)}</span>')
    return Markup(f'<svg class="rp-svg" viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="none" '
                  f'role="img" aria-label="Daily ad spend">{"".join(parts)}</svg>' + (f'<div class="rp-legend">{leg}</div>' if leg else ""))


def mix_bars(t):
    """One bar showing where the period's ad money went: Google vs Meta."""
    g, m = t["by_platform"].get("google", 0), t["by_platform"].get("meta", 0)
    tot = g + m
    if not tot:
        return Markup("")
    gw = 100 * g / tot
    return Markup(f'<div class="rp-mix"><span style="width:{gw:.1f}%;background:{COLOR["google"]}"></span>'
                  f'<span style="width:{100 - gw:.1f}%;background:{COLOR["meta"]}"></span></div>'
                  f'<div class="rp-mix-l"><span>Google {money(g)} · {gw:.0f}%</span><span>Meta {money(m)} · {100 - gw:.0f}%</span></div>')


def meter(value, goal, color):
    """A small progress bar (e.g. reviews against goal, spend against budget)."""
    if not goal:
        return Markup("")
    w = max(0.0, min(100.0, 100 * (value or 0) / goal))
    over = (value or 0) > goal
    return Markup(f'<span class="rp-meter"><span style="width:{w:.0f}%;background:{COLOR["bad"] if over and color == COLOR["paid"] else color}"></span></span>')
