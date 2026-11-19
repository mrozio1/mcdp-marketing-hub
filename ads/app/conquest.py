"""Competitor-name campaigns: show our ads when people search for a competing orthodontist near one of our
practices. One Search campaign per practice, its own budget, aimed at the practice's assigned zips (zip_plan),
keywords = nearby competitors' practice names and doctors' names (phrase match), from the NPI Registry list on the
Map (public data, free to reuse). The ad text never names the competitor (Google's trademark rules); it's the
practice's own "benefits" text. Drafted as a 'new_campaign' (created PAUSED only on Apply).

Only orthodontic practices: for a dentist, nearby dental offices are our orthodontists' referral partners.
"""
from __future__ import annotations

import re

from . import brand, campaigns, db, field, leaks, places, referrals, targeting, zip_plan

PER_PRACTICE = 25
LEGAL = re.compile(r"\b(pllc|pc|p\.c\.|llc|inc|ltd|dds|dmd|ms|msd|pa|corp|co|the)\b\.?", re.I)


def _clean(name):
    n = re.sub(r"\+\s*\d+\s+more", " ", (name or "").lower())
    n = LEGAL.sub(" ", n)
    n = re.sub(r"[^a-z0-9&' ]+", " ", n)
    return " ".join(n.split())


def keywords_for(practice, offices):
    """Competitor names near these offices, nearest first."""
    seen, out = set(), []
    rows = []
    for o in offices:
        rows += referrals.listing(o["id"], include_omitted=False, kind="competitor")
    for r in sorted(rows, key=lambda r: r["miles"]):
        cands = [_clean(r["name"])]
        for d in r.get("dentists") or []:
            last = _clean(str(d).split(",")[0].split()[-1] if str(d).split() else "")
            if len(last) >= 4:
                cands.append(f"dr {last} orthodontist")
        for k in cands:
            if len(k) < 5 or k in seen or len(k.split()) > 7:
                continue
            if leaks._sister_brand(k, practice):
                continue                       # one of ours
            seen.add(k)
            out.append(k)
        if len(out) >= PER_PRACTICE:
            break
    return out[:PER_PRACTICE]


def draft(practice, google, daily_budget=10.0):
    if zip_plan.line_of(practice) != "ortho":
        raise campaigns.CampaignError("Competitor-name campaigns are for orthodontic practices only.")
    links = field.practice_links()
    offices = [o for o in places.offices() if links.get(o["id"]) == practice["id"] and o.get("lat") is not None]
    kws = keywords_for(practice, offices)
    if len(kws) < 3:
        raise campaigns.CampaignError(f"{practice['name']}: not enough competitors on the Map list near its offices.")
    zips = zip_plan.practice_zips(practice["id"])
    consts = google.zip_targets(zips) if zips else {}
    if not consts:
        raise campaigns.CampaignError(f"{practice['name']}: no assigned zip codes to aim at.")
    text = targeting.benefits_text(practice)
    heads = [h for h in text["headlines"] if "2026" not in h and "Dec 31" not in h]      # runs past December
    descs = [d for d in text["descriptions"] if "Dec 31" not in d] + \
        ["See why local families choose us. Book a free orthodontic consult online today."]
    from .offers import _landing
    kit = brand.kit(practice["id"])
    payload = {
        "account": campaigns.account_for(practice), "name": f"{campaigns.name_word(practice)} - Search - Competitor names"[:120],
        "ad_group": "Competitor names", "focus": "competitor names", "daily_budget": float(daily_budget), "max_cpc": None,
        "radius": 10, "addresses": [], "geo": sorted(set(consts.values())), "zips": sorted(consts),
        "final_url": _landing(practice), "keywords": kws,
        "negatives": list(dict.fromkeys(list(campaigns.DEFAULT_NEGATIVES) + ["medicaid", "reviews", "lawsuit", "complaints"])),
        "headlines": heads[:15], "descriptions": descs[:4], "dropped": [], "branded": brand.has_kit(kit),
        "conquest": True}
    probs = campaigns.check(payload, kit)
    prop = {"platform": "google", "account": payload["account"], "kind": campaigns.KIND,
            "title": f"New Search campaign “{payload['name']}” (created paused)",
            "detail": (f"${daily_budget:,.2f}/day, Maximize clicks, people in the practice's {len(consts)} zip codes searching "
                       f"for {len(kws)} nearby competitors: {', '.join(kws[:8])}{'…' if len(kws) > 8 else ''}. The ads never "
                       "name the competitor. Expect a higher cost per click than our own keywords."
                       + (" Fix before applying: " + " ".join(probs[:3]) if probs else "")),
            "payload": payload}
    with db.conn() as c:
        c.execute("DELETE FROM changes WHERE practice_id=? AND kind=? AND status='proposed' AND payload LIKE ?",
                  (practice["id"], campaigns.KIND, '%"conquest": true%'))
    campaigns._store(practice["id"], prop)
    return prop
