"""Organic (unpaid) Facebook and Instagram post results, and the paid + organic combined view."""
from __future__ import annotations

from . import db

NETWORKS = (("facebook", "Facebook"), ("instagram", "Instagram"))


def own_accounts(practice):
    return db.social_ids(practice, "fb") + db.social_ids(practice, "ig")


def accounts_for(practice, practices, master):
    """Page / Instagram ids whose posts belong on this practice's page (all of its locations).
    The master record shows every account linked to any practice (unless it has its own links)."""
    own = own_accounts(practice)
    if own or not master:
        return own
    return sorted({a for q in practices for a in own_accounts(q)})


def account_names():
    """{account id: display name} from the cached Page / Instagram lists."""
    import json
    names = {}
    for key in ("fb_pages", "ig_accounts"):
        try:
            for it in json.loads(db.get_setting(key) or "[]"):
                names[it["id"]] = it.get("name") or it["id"]
        except (ValueError, KeyError, TypeError):
            continue
    return names


def label_posts(posts, practices=None, master=False):
    """Attach the location (account name) to each post, plus the practice for the master record."""
    names = account_names()
    owner = {}
    if master and practices:
        for q in practices:
            for a in own_accounts(q):
                owner.setdefault(a, q["name"])
    for p in posts:
        p["account_name"] = names.get(p["account_id"], p["account_id"])
        p["practice_name"] = owner.get(p["account_id"], "")
    return posts


def by_location(posts):
    """One row per linked account: how each location's posts did."""
    groups = {}
    for p in posts:
        groups.setdefault((p["network"], p["account_id"]), []).append(p)
    rows = []
    for (net, acct), items in groups.items():
        eng = sum(engagement(x) for x in items)
        views = _sum(items, "views")
        rows.append({"network": net, "account_id": acct, "account_name": items[0].get("account_name", acct),
                     "practice_name": items[0].get("practice_name", ""), "count": len(items), "views": views,
                     "engagement": eng, "per_post": eng / len(items), "rate": (eng / views) if views else None})
    return sorted(rows, key=lambda r: -r["engagement"])


def _sum(posts, key):
    vals = [p[key] for p in posts if p.get(key) is not None]
    return sum(vals) if vals else None


def summarize(posts):
    views, inter = _sum(posts, "views"), _sum(posts, "interactions") or 0
    return {"count": len(posts), "views": views, "reach": _sum(posts, "reach"), "interactions": inter,
            "likes": _sum(posts, "likes") or 0, "comments": _sum(posts, "comments") or 0,
            "shares": _sum(posts, "shares") or 0, "saves": _sum(posts, "saves"), "clicks": _sum(posts, "clicks"),
            "rate": (inter / views) if views else None}


def organic_view(practice, practices, master, since, until):
    ids = accounts_for(practice, practices, master)
    view = {"linked": bool(ids), "since": since, "until": until, "networks": [], "top": [], "totals": None,
            "locations": [], "multi": False}
    if not ids:
        return view
    posts = label_posts(db.posts_for(ids, since, until), practices, master and not own_accounts(practice))
    for key, label in NETWORKS:
        mine = [p for p in posts if p["network"] == key]
        linked_here = db.social_ids(practice, "fb" if key == "facebook" else "ig")
        if mine or linked_here:
            view["networks"].append({"key": key, "label": label, **summarize(mine)})
    view["totals"] = summarize(posts)
    view["locations"] = by_location(posts)
    view["multi"] = len({p["account_id"] for p in posts}) > 1 or len(ids) > 1
    view["top"] = sorted(posts, key=lambda p: (p["views"] if p["views"] is not None else -1, p["interactions"] or 0),
                         reverse=True)[:10]
    return view


def combined(paid_views, org):
    """One set of headline numbers across paid ads and organic posts."""
    paid = [v["totals"] for v in paid_views if v.get("totals")]
    paid_impr = sum(t["impressions"] for t in paid)
    paid_clicks = sum(t["clicks"] for t in paid)
    spend = round(sum(t["spend"] for t in paid), 2)
    conv = round(sum(t["conversions"] for t in paid), 2)
    ot = org.get("totals") or {}
    org_views = ot.get("views") or 0
    org_inter = ot.get("interactions") or 0
    seen = paid_impr + org_views
    return {"has_paid": bool(paid), "has_organic": bool(ot.get("count")),
            "seen": seen, "paid_seen": paid_impr, "org_seen": org_views,
            "paid_share": (paid_impr / seen) if seen else None,
            "engaged": paid_clicks + org_inter, "paid_clicks": paid_clicks, "org_interactions": org_inter,
            "spend": spend, "conversions": conv, "cpa": (spend / conv) if conv else None,
            "posts": ot.get("count") or 0,
            "views_missing": bool(ot.get("count")) and ot.get("views") is None}


# ------------------------------------------------------------------ post performance report
PERIODS = {"month": "This month", "last_month": "Last month", "90d": "Last 90 days"}
SORTS = {"engagement": "Engagement", "views": "Views", "rate": "Engagement rate", "newest": "Newest"}


def period_range(key, today=None):
    from datetime import date, timedelta
    today = today or date.today()
    first = today.replace(day=1)
    if key == "last_month":
        end = first - timedelta(days=1)
        return end.replace(day=1), end
    if key == "90d":
        return today - timedelta(days=89), today
    return first, today


def default_period(today=None):
    from datetime import date
    return "last_month" if (today or date.today()).day <= 3 else "month"


def post_title(post, limit=90):
    """Posts have no title: use the first line / sentence of the text."""
    text = " ".join((post.get("text") or "").split("\n")[0].split())
    if not text:
        kind = (post.get("media_type") or "").replace("_", " ").lower()
        return {"reels": "Reel", "video": "Video post", "added video": "Video post", "carousel album": "Carousel post",
                "image": "Photo post", "added photos": "Photo post", "feed": "Photo post"}.get(kind, "Post (no text)")
    for stop in (". ", "! ", "? "):
        i = text.find(stop)
        if 20 <= i < limit:
            return text[:i + 1]
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def engagement(post):
    return (post.get("likes") or 0) + (post.get("comments") or 0) + (post.get("shares") or 0) + (post.get("saves") or 0)


def ranked(posts, sort="engagement"):
    """Every post with its engagement, rate, rank (1 = best by `sort`) and how it compares with
    the practice's average post."""
    rows = []
    for p in posts:
        e = engagement(p)
        rows.append({**p, "title": post_title(p), "engagement": e,
                     "rate": (e / p["views"]) if p.get("views") else None})
    if not rows:
        return rows
    avg = sum(r["engagement"] for r in rows) / len(rows)
    keyf = {"engagement": lambda r: (r["engagement"], r["views"] or 0),
            "views": lambda r: (r["views"] if r["views"] is not None else -1, r["engagement"]),
            "rate": lambda r: (r["rate"] if r["rate"] is not None else -1, r["engagement"]),
            "newest": lambda r: r["created"]}[sort if sort in SORTS else "engagement"]
    rows.sort(key=keyf, reverse=True)
    n = len(rows)
    by_eng = sorted(rows, key=lambda r: (r["engagement"], r["views"] or 0), reverse=True)
    eng_rank = {r["post_id"]: i + 1 for i, r in enumerate(by_eng)}
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["eng_rank"] = eng_rank[r["post_id"]]
        r["vs_avg"] = (r["engagement"] / avg) if avg else None
        q = r["eng_rank"] / n
        r["tier"] = "top" if n >= 4 and q <= 0.25 else ("low" if n >= 4 and q > 0.75 else "mid")
    return rows


def takeaways(rows, locations=None):
    """A few plain-language observations for leadership, computed (never guessed)."""
    out = []
    if not rows:
        return out
    locs = [l for l in (locations or []) if l["count"] >= 2]
    loc_line_networks = set()
    if len(locs) >= 2:
        best_l = max(locs, key=lambda l: l["per_post"])
        worst_l = min(locs, key=lambda l: l["per_post"])
        if worst_l["per_post"] and best_l["per_post"] / worst_l["per_post"] >= 1.3:
            loc_line_networks = {best_l["network"], worst_l["network"]}
            out.append(f"{best_l['account_name']} ({best_l['network'].title()}) averaged {best_l['per_post']:,.0f} "
                       f"engagements per post, {best_l['per_post'] / worst_l['per_post']:.1f}× "
                       f"{worst_l['account_name']} ({worst_l['network'].title()}, {worst_l['per_post']:,.0f}).")
    best = max(rows, key=lambda r: (r["engagement"], r["views"] or 0))
    avg = sum(r["engagement"] for r in rows) / len(rows)
    if avg and len(rows) > 1:
        out.append(f"Best post: “{best['title']}” with {best['engagement']:,} engagements, "
                   f"{best['engagement'] / avg:.1f}× the average post.")
    by_net = {}
    for r in rows:
        by_net.setdefault(r["network"], []).append(r["engagement"])
    if len(by_net) == 2:
        (n1, v1), (n2, v2) = sorted(by_net.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))
        a1, a2 = sum(v1) / len(v1), sum(v2) / len(v2)
        if a2 and a1 / a2 >= 1.2 and len(loc_line_networks) < 2:   # skip if the location line already says it
            out.append(f"{n1.title()} posts averaged {a1:,.0f} engagements each, {a1 / a2:.1f}× {n2.title()} ({a2:,.0f}).")
    kinds = {}
    for r in rows:
        k = (r.get("media_type") or "").lower()
        k = "video / reel" if any(w in k for w in ("video", "reel")) else ("photo" if any(w in k for w in ("photo", "image", "feed", "carousel")) else None)
        if k:
            kinds.setdefault(k, []).append(r["engagement"])
    if len(kinds) == 2 and all(len(v) >= 2 for v in kinds.values()):
        (k1, v1), (k2, v2) = sorted(kinds.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))
        a1, a2 = sum(v1) / len(v1), sum(v2) / len(v2)
        if a2 and a1 / a2 >= 1.2:
            out.append(f"{k1.capitalize()} posts drew {a1 / a2:.1f}× the engagement of {k2} posts on average.")
    zero = [r for r in rows if r["engagement"] == 0]
    if zero and len(rows) >= 4:
        out.append(f"{len(zero)} of {len(rows)} posts got no likes, comments or shares.")
    return out


def report(practice, practices, master, period_key, sort):
    since, until = period_range(period_key)
    ids = accounts_for(practice, practices, master)
    whole_group = master and not own_accounts(practice)
    posts = label_posts(db.posts_for(ids, since, until), practices, whole_group) if ids else []
    rows = ranked(posts, sort)
    tot = summarize(posts)
    tot["engagement"] = sum(r["engagement"] for r in rows)
    tot["rate"] = (tot["engagement"] / tot["views"]) if tot.get("views") else None
    tot["avg_engagement"] = (tot["engagement"] / len(rows)) if rows else None
    names = {p["account_id"]: p["practice_name"] for p in posts if p.get("practice_name")} if whole_group else {}
    locations = by_location(posts)
    return {"linked": bool(ids), "since": since, "until": until, "period": period_key, "sort": sort,
            "rows": rows, "totals": tot, "takeaways": takeaways(rows, locations), "practice_of": names,
            "locations": locations, "multi": len(locations) > 1 or len(ids) > 1}
