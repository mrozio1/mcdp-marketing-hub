"""Organic (unpaid) Facebook and Instagram post results, and the paid + organic combined view."""
from __future__ import annotations

from . import db

NETWORKS = (("facebook", "Facebook"), ("instagram", "Instagram"))


def accounts_for(practice, practices, master):
    """Page / Instagram ids whose posts belong on this practice's page. The master record
    shows every account linked to any practice (unless it has its own links)."""
    own = [practice.get("fb_page_id"), practice.get("ig_account_id")]
    if any(own) or not master:
        return [a for a in own if a]
    ids = []
    for q in practices:
        ids += [q.get("fb_page_id"), q.get("ig_account_id")]
    return sorted({a for a in ids if a})


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
    view = {"linked": bool(ids), "since": since, "until": until, "networks": [], "top": [], "totals": None}
    if not ids:
        return view
    posts = db.posts_for(ids, since, until)
    for key, label in NETWORKS:
        mine = [p for p in posts if p["network"] == key]
        linked_here = practice.get("fb_page_id") if key == "facebook" else practice.get("ig_account_id")
        if mine or linked_here:
            view["networks"].append({"key": key, "label": label, **summarize(mine)})
    view["totals"] = summarize(posts)
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
