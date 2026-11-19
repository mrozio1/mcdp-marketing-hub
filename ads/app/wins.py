"""The home page's good-news section: a rotating spotlight of wins, four trend tiles you can open for detail,
a live feed of what just happened, the field team's week and three shout-outs. Worked out from the hub's own
data (field visits, Google reviews, posts, applied fixes, ad results) plus LeadSigma's latest export.
Counts only, never patient names.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from . import db, field, organic, users

NET = {"facebook": "Facebook", "instagram": "Instagram", "fb": "Facebook", "ig": "Instagram"}


# ------------------------------------------------------------------ periods
def _history(per, n=None):
    """Earlier periods like this one, oldest first, ending with this one: [(start, end, label)] (end inclusive)."""
    out = []
    if per["kind"] == "week":
        for k in range((n or 7) - 1, -1, -1):
            s = per["start"] - timedelta(days=7 * k)
            out.append((s, per["end"] if k == 0 else s + timedelta(days=6), f"{s:%b} {s.day}"))
    else:
        y, m = per["start"].year, per["start"].month
        for k in range((n or 6) - 1, -1, -1):
            yy, mm = y, m - k
            while mm < 1:
                yy, mm = yy - 1, mm + 12
            s = date(yy, mm, 1)
            e = per["end"] if k == 0 else date(yy, mm, calendar.monthrange(yy, mm)[1])
            out.append((s, e, f"{s:%b}"))
    return out


# ------------------------------------------------------------------ counts
def _accounts(plist):
    """Social account id -> practice name."""
    out = {}
    for p in plist:
        for a in organic.own_accounts(p):
            out.setdefault(a, p["name"])
    return out


def _review_rows(plist, start, end_incl):
    ids = [p["id"] for p in plist]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    with db.conn() as c:
        try:
            return [dict(r) for r in c.execute(
                f"SELECT r.stars, r.created, l.title, l.practice_id FROM gbp_reviews r JOIN gbp_locations l ON l.name=r.location "
                f"WHERE l.practice_id IN ({q}) AND substr(r.created, 1, 10) BETWEEN ? AND ? ORDER BY r.created DESC",
                (*ids, start.isoformat(), end_incl.isoformat()))]
        except Exception:
            return []


def _visits(start, end_incl):
    try:
        return field.visits_between(start, end_incl + timedelta(days=1))
    except Exception:
        return []


def _posts(accounts, start, end_incl):
    try:
        return db.posts_for(list(accounts), start, end_incl) if accounts else []
    except Exception:
        return []


def _fixes(plist, start, end_incl):
    ids = [p["id"] for p in plist]
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            f"SELECT id, practice_id, kind, title, applied_at FROM changes WHERE status='applied' AND practice_id IN ({q}) "
            "AND substr(applied_at, 1, 10) BETWEEN ? AND ? ORDER BY applied_at DESC", (*ids, start.isoformat(), end_incl.isoformat()))]


def _first_visits(visits, start):
    """Offices in these visits that had never been visited before this period."""
    offices = {v["office_id"] for v in visits}
    if not offices:
        return set()
    q = ",".join("?" * len(offices))
    with db.conn() as c:
        before = {r[0] for r in c.execute(f"SELECT DISTINCT office_id FROM visits WHERE office_id IN ({q}) AND visited_at < ?",
                                          (*offices, start.isoformat()))}
    return offices - before


# ------------------------------------------------------------------ helpers
def _top(pairs, n=5):
    pairs = sorted([p for p in pairs if p[1]], key=lambda p: -p[1])[:n]
    mx = max([v for _, v in pairs] or [1])
    return [{"name": k, "v": v, "w": round(v / mx * 100)} for k, v in pairs]


def _delta(cur, prev, per):
    if prev is None:
        return None
    d = cur - prev
    if not d:
        return {"up": None, "text": f"same as {per['prev_label']}"}
    return {"up": d > 0, "text": f"{abs(d):,} {'more' if d > 0 else 'fewer'} than {per['prev_label']}"}


def _spark(vals, w=120, h=34):
    if not vals or max(vals) == 0:
        return ""
    mx, n = max(vals), len(vals)
    pts = [(2 + (w - 4) * i / max(n - 1, 1), h - 3 - (h - 6) * v / mx) for i, v in enumerate(vals)]
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)


def _bars(hist, vals):
    mx = max(vals or [0]) or 1
    return [{"label": h[2], "v": v, "h": max(2, round(v / mx * 120)) if v else 2, "last": i == len(vals) - 1}
            for i, (h, v) in enumerate(zip(hist, vals))]


def _ago(ts, now):
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00").split("+")[0])
    except ValueError:
        try:
            t = datetime.fromisoformat(str(ts)[:10])
        except ValueError:
            return ""
    s = (now - t).total_seconds()
    if s < 3600:
        return f"{max(1, int(s // 60))} minutes ago" if s >= 120 else "just now"
    if s < 86400 and t.date() == now.date():
        h = int(s // 3600)
        return f"{h} hour{'' if h == 1 else 's'} ago"
    if (now.date() - t.date()).days == 1:
        return "yesterday"
    return f"{t:%b} {t.day}"


def _initials(name):
    parts = [p for p in (name or "?").split() if p[:1].isalpha()]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


# ------------------------------------------------------------------ the section
def build(rep, ls, user=None, now=None):
    per = rep["period"]
    now = now or datetime.now()
    plist = [r["p"] for r in rep["rows"]] + list(rep.get("masters") or [])
    names = {p["id"]: p["name"] for p in plist}
    accounts = _accounts(plist)
    rows = rep["rows"]
    unit = "week" if per["kind"] == "week" else "month"
    hist = _history(per)
    team_ok = bool(user) and users.rank(user["role"]) >= users.rank("editor") and users.sees_all_field(user)
    hide = users.hidden_people(user) if user else set()

    # history for the tiles
    hv, hr, hp = [], [], []
    for s, e, _ in hist:
        hv.append(len([v for v in _visits(s, e) if v.get("user_id") not in hide]))
        hr.append(len(_review_rows(plist, s, e)))
        hp.append(len(_posts(accounts, s, e)))
    visits = [v for v in _visits(per["start"], per["end"]) if v.get("user_id") not in hide]
    reviews_now = _review_rows(plist, per["start"], per["end"])
    five = sum(1 for r in reviews_now if (r["stars"] or 0) >= 5)
    posts_now = _posts(accounts, per["start"], per["end"])
    firsts = _first_visits(visits, per["start"])
    fixes = _fixes(plist, per["start"], per["end"])

    # bookings: a trend only when LeadSigma's rows carry dates (a summary export covers one span)
    hb = None
    if ls is not None:
        from . import dashboard
        vals = []
        for s, e, _ in hist:
            r, summary = dashboard._ls_rows(s, e)
            if summary is not None:
                vals = None
                break
            vals.append(sum(x["bookings"] for x in r if x["section"] in ("", "source", "booking")))
        hb = vals

    by_p = lambda key: [(r["p"]["name"], key(r)) for r in rows]
    tiles = [
        {"key": "visits", "label": "Field visits", "value": f"{len(visits):,}", "delta": _delta(hv[-1], hv[-2], per),
         "spark": _spark(hv), "bars": _bars(hist, hv), "link": ("/field", "Open the Field page"),
         "split_title": "By practice", "split": _top(by_p(lambda r: r["field"]["visits"]))},
        {"key": "reviews", "label": "New reviews", "value": f"{len(reviews_now):,}", "delta": _delta(hr[-1], hr[-2], per),
         "spark": _spark(hr), "bars": _bars(hist, hr), "link": ("/reviews", "Open Reviews"),
         "split_title": "By practice", "split": _top(by_p(lambda r: r["reviews"]["new"]))},
    ]
    if ls is not None:
        heard = [(h, n) for h, n in (ls.get("heard") or []) if h != "Everything else"]
        tiles.append({"key": "bookings", "label": "Online bookings", "value": f"{ls['bookings']:,}",
                      "delta": _delta(hb[-1], hb[-2], per) if hb else None, "spark": _spark(hb) if hb else "",
                      "bars": _bars(hist, hb) if hb else [], "link": ("/#leadsigma", "How patients found us"),
                      "note": "" if hb else "From LeadSigma's latest export. A trend shows once exports carry dates.",
                      "split_title": "By practice" if ls.get("by_practice") else "How they found us",
                      "split": _top([(names.get(pid, "?"), n) for pid, n in ls["by_practice"].items()]) if ls.get("by_practice")
                      else _top(heard)})
    nets = {}
    for x in posts_now:
        nets[NET.get(x["network"], x["network"].title())] = nets.get(NET.get(x["network"], x["network"].title()), 0) + 1
    tiles.append({"key": "posts", "label": "Posts published", "value": f"{len(posts_now):,}", "delta": _delta(hp[-1], hp[-2], per),
                  "spark": _spark(hp), "bars": _bars(hist, hp), "link": ("/studio", "Open Studio"),
                  "split_title": "By practice", "split": _top(by_p(lambda r: r["organic"]["posts"])),
                  "split2_title": "By platform", "split2": _top(list(nets.items()))})
    for t in tiles:
        t["history"] = (f"last {len(hist)} weeks" if per["kind"] == "week" else f"last {len(hist)} months")

    # spotlight: good news only
    spots = []
    if reviews_now:
        top = _top(by_p(lambda r: r["reviews"]["new"]), 1)
        rated = [(r["reviews"]["rating"], r["reviews"]["total"]) for r in rows if r["reviews"].get("rating") and r["reviews"].get("total")]
        n = sum(t for _, t in rated)
        avg = sum(a * t for a, t in rated) / n if n else None
        body = " ".join(x for x in (f"{top[0]['name']} led with {top[0]['v']}." if top else "",
                                    f"The group's Google rating is {avg:.1f} stars." if avg else "") if x)
        spots.append({"icon": "star", "kicker": "Reviews",
                      "title": f"{five:,} new 5-star review{'' if five == 1 else 's'} this {unit}" if five
                      else f"{len(reviews_now):,} new review{'' if len(reviews_now) == 1 else 's'} this {unit}", "body": body})
    if visits:
        offices = len({v["office_id"] for v in visits})
        spots.append({"icon": "pin", "kicker": "Field team",
                      "title": f"{offices:,} referring office{'' if offices == 1 else 's'} visited this {unit}",
                      "body": f"{len(visits):,} visit{'' if len(visits) == 1 else 's'} in all"
                              + (f", {len(firsts)} of them first visits." if firsts else ".")})
    if ls is not None and ls.get("by_practice"):
        pid, n = max(ls["by_practice"].items(), key=lambda x: x[1])
        if n:
            spots.append({"icon": "cal", "kicker": "Bookings", "title": f"{names.get(pid, 'A practice')} booked {n:,} consults online",
                          "body": f"{ls['bookings']:,} online bookings across the group in LeadSigma's latest export."})
    if fixes:
        kinds = {}
        from .actions import KIND_LABELS
        for f in fixes:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
        what = [KIND_LABELS.get(k, k).lower() for k, _ in sorted(kinds.items(), key=lambda x: -x[1])[:2]]
        np_ = len({f["practice_id"] for f in fixes})
        spots.append({"icon": "wrench", "kicker": "Ads", "title": f"{len(fixes):,} ad fix{'' if len(fixes) == 1 else 'es'} applied "
                      f"across {np_} practice{'' if np_ == 1 else 's'}", "body": "Mostly: " + " and ".join(what) + "."})
    t = rep["totals"]
    if t.get("conversions") and t.get("prev_conversions") is not None and t["conversions"] > t["prev_conversions"]:
        up = t["conversions"] - t["prev_conversions"]
        spots.append({"icon": "chart", "kicker": "Ad results", "title": f"{up:,.0f} more ad conversions than {per['prev_label']}",
                      "body": f"{t['conversions']:,.0f} in all this {unit}."})
    if posts_now:
        top = _top(by_p(lambda r: r["organic"]["posts"]), 1)
        spots.append({"icon": "image", "kicker": "Social", "title": f"{len(posts_now):,} post{'' if len(posts_now) == 1 else 's'} published this {unit}",
                      "body": f"{top[0]['name']} posted the most ({top[0]['v']})." if top else ""})
    if not spots:
        spots.append({"icon": "star", "kicker": "This " + unit, "title": "Wins show up here as they happen",
                      "body": "New reviews, field visits, bookings, posts and ad fixes."})

    # live feed: the last two weeks, newest first
    since = now.date() - timedelta(days=14)
    feed = []
    for r in _review_rows(plist, since, now.date())[:10]:
        if (r["stars"] or 0) >= 4:
            feed.append((r["created"], "star", f"New {r['stars']}-star review for {r['title'] or names.get(r['practice_id'], '')}"))
    mine_only = not (user and users.sees_all_field(user))
    for v in _visits(since, now.date())[:15]:
        if v.get("user_id") in hide or (mine_only and user and v.get("user_id") != user["id"]):
            continue
        p = names.get(v.get("practice_id"))
        who = f" by {v['user_name']}" if v.get("user_name") and not mine_only else ""
        feed.append((v["visited_at"], "pin", f"Field visit at {v['office_name']}" + (f" for {p}" if p else "") + who))
    for x in _posts(accounts, since, now.date())[:10]:
        feed.append((x["created"], "image", f"{NET.get(x['network'], x['network'].title())} post published for "
                                            f"{accounts.get(x['account_id'], 'a practice')}"))
    for f in _fixes(plist, since, now.date())[:10]:
        title = f["title"] if len(f["title"]) <= 80 else f["title"][:77] + "…"
        feed.append((f["applied_at"], "wrench", f"{names.get(f['practice_id'], '')}: {title}"))
    from . import dashboard
    up = dashboard.last_upload()
    if up and ls is not None and up.get("at", "")[:10] >= since.isoformat():
        feed.append((up["at"], "cal", "LeadSigma numbers updated"))
    feed.sort(key=lambda x: str(x[0]), reverse=True)
    feed = [{"icon": i, "text": txt, "when": _ago(ts, now)} for ts, i, txt in feed[:6]]

    # the field team (editors and admins who see everyone's visits)
    team = []
    if team_ok:
        by = {}
        for v in visits:
            k = v.get("user_id") or v.get("user_name")
            d = by.setdefault(k, {"name": v.get("user_name") or "Someone", "visits": 0, "first": 0, "offices": set()})
            d["visits"] += 1
            d["offices"].add(v["office_id"])
        for v in visits:
            if v["office_id"] in firsts:
                by[v.get("user_id") or v.get("user_name")]["first"] += 1
        mx = max([d["visits"] for d in by.values()] or [1])
        for d in sorted(by.values(), key=lambda d: -d["visits"])[:6]:
            note = (f"{d['first']} first visit{'' if d['first'] == 1 else 's'}" if d["first"]
                    else f"{len(d['offices'])} office{'' if len(d['offices']) == 1 else 's'}")
            team.append({"name": d["name"], "initials": _initials(d["name"]), "visits": d["visits"], "note": note,
                         "w": round(d["visits"] / mx * 100)})

    # shout-outs
    shouts = []
    if ls is not None and ls.get("by_practice") and max(ls["by_practice"].values() or [0]):
        pid, n = max(ls["by_practice"].items(), key=lambda x: x[1])
        shouts.append({"icon": "cup", "kicker": f"Practice of the {unit}", "title": names.get(pid, "?"),
                       "body": f"{n:,} online consults booked, the most in the group (LeadSigma)."})
    else:
        best = max(rows, key=lambda r: r["paid"]["conversions"], default=None)
        if best and best["paid"]["conversions"]:
            shouts.append({"icon": "cup", "kicker": f"Practice of the {unit}", "title": best["p"]["name"],
                           "body": f"{best['paid']['conversions']:,.0f} ad conversions, the most in the group."})
    if team:
        s = team[0]
        shouts.append({"icon": "pin", "kicker": "Field star", "title": f"{s['name']}: {s['visits']} visit{'' if s['visits'] == 1 else 's'}",
                       "body": f"{s['note'][:1].upper() + s['note'][1:]} this {unit}."})
    elif visits:
        shouts.append({"icon": "pin", "kicker": "Field team", "title": f"{len(visits):,} visit{'' if len(visits) == 1 else 's'} this {unit}",
                       "body": f"{len(firsts)} first visit{'' if len(firsts) == 1 else 's'} to new offices." if firsts else "Keep them coming."})
    gain = [(r["reviews"]["new"] - r["reviews_prev"]["new"], r) for r in rows if r["reviews"]["linked"]]
    gain = [g for g in gain if g[0] > 0]
    if gain:
        g, r = max(gain, key=lambda x: x[0])
        shouts.append({"icon": "star", "kicker": "Most improved", "title": r["p"]["name"],
                       "body": f"{r['reviews']['new']} new Google review{'' if r['reviews']['new'] == 1 else 's'}, "
                               f"up from {r['reviews_prev']['new']} {per['prev_label']}."})
    else:
        rated = [r for r in rows if r["reviews"].get("rating") and (r["reviews"].get("total") or 0) >= 20]
        if rated:
            r = max(rated, key=lambda r: (r["reviews"]["rating"], r["reviews"]["total"]))
            shouts.append({"icon": "star", "kicker": "Top rated", "title": r["p"]["name"],
                           "body": f"{r['reviews']['rating']:.1f} stars from {r['reviews']['total']:,} Google reviews."})

    return {"spots": spots, "tiles": tiles, "feed": feed, "team": team, "team_ok": team_ok, "shouts": shouts,
            "unit": unit}
