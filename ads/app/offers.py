"""Offer campaigns: a Search campaign for one offer at chosen locations only, aimed at those locations' assigned
zip codes (zip_plan), with an end date. Written from a template (no AI), stored as a 'new_campaign' draft and
created PAUSED only on Apply (campaigns.apply), so it can be reviewed and edited first.
"""
from __future__ import annotations

from datetime import date

from . import brand, campaigns, db, targeting, zip_plan

UNINSURED = {
    "key": "uninsured-1000",
    "focus": "$1,000 off without insurance",
    "headlines": ["Uninsured Patients Save $1,000", "$1,000 Off Without Insurance", "Offer Ends December 31",
                  "Free Orthodontic Consult", "New Patient Offer", "Braces & Invisalign", "Kids, Teens & Adults",
                  "Book Online in Minutes"],
    "descriptions": ["New patients without orthodontic insurance save $1,000 on braces or Invisalign.",
                     "Book a free consult and ask about $1,000 off for new patients. Offer ends Dec 31."],
    "keywords": ["braces without insurance", "invisalign without insurance", "orthodontist no insurance",
                 "braces cost", "invisalign cost", "affordable braces", "orthodontist near me", "braces near me",
                 "invisalign near me", "orthodontist {city}", "braces {city}", "invisalign {city}"],
    "negatives": ["medicaid", "free braces", "jobs", "careers", "salary", "school", "course", "diy"],
}


def _landing(practice):
    kit = brand.kit(practice["id"]) or {}
    url = (kit.get("website") or "").strip()
    if url:
        return url
    with db.conn() as c:
        r = c.execute("SELECT payload FROM changes WHERE practice_id=? AND kind='draft_ad' ORDER BY id DESC LIMIT 1",
                      (practice["id"],)).fetchone()
    if r:
        import json
        urls = json.loads(r["payload"]).get("final_urls") or []
        if urls:
            return urls[0]
    return ""


def draft(practice, office_ids, google, offer=UNINSURED, daily_budget=20.0, end=None, cities=""):
    """Draft the offer campaign for these of the practice's map offices. Returns the stored proposal."""
    end = end or date(date.today().year, 12, 31)
    plan = zip_plan.assign()
    rows = [r for r in plan["offices"] if r["office"]["id"] in office_ids]
    zips = sorted({z["zip"] for r in rows for z in r["zips"]})
    if not zips:
        raise campaigns.CampaignError("Those locations have no assigned zip codes yet (Map → Target zip codes).")
    consts = google.zip_targets(zips)
    if not consts:
        raise campaigns.CampaignError("Google didn't recognise those zip codes.")
    labels = [r["office"]["label"].split("–")[-1].strip() for r in rows]
    place = cities or " & ".join(labels)
    city_kw = [l.lower() for l in labels]
    heads = list(offer["headlines"])
    rating, n = targeting.rating_of(practice)
    if rating and rating >= 4.5 and n >= 20:
        heads.append(f"{rating} Stars From {n:,} Reviews")
    for h in (practice["name"], f"{place} Orthodontist", f"Orthodontist in {place}"):
        if len(h) <= 30 and h not in heads and not (h.split()[0].isupper() and len(h.split()[0]) > 3):
            heads.append(h)
            break
    kws = []
    for k in offer["keywords"]:
        for c in (city_kw if "{city}" in k else [None]):
            t = k.format(city=c) if c else k
            if t not in kws:
                kws.append(t)
    kit = brand.kit(practice["id"])
    payload = {
        "account": campaigns.account_for(practice), "name": f"{campaigns.name_word(practice)} - Search - "
                                                            f"{offer['focus']} - {place}"[:120],
        "ad_group": offer["focus"], "focus": offer["focus"], "daily_budget": float(daily_budget), "max_cpc": None,
        "radius": 10, "addresses": [], "geo": sorted(set(consts.values())), "zips": sorted(consts),
        "end_date": end.isoformat(), "final_url": _landing(practice), "keywords": kws[:campaigns.MAX_KEYWORDS],
        "negatives": list(dict.fromkeys(list(campaigns.DEFAULT_NEGATIVES) + offer["negatives"])),
        "headlines": heads[:15], "descriptions": offer["descriptions"][:4], "dropped": [], "branded": brand.has_kit(kit),
        "offer": offer["key"], "offices": sorted(office_ids)}
    probs = campaigns.check(payload, kit)
    prop = {"platform": "google", "account": payload["account"], "kind": campaigns.KIND,
            "title": f"New Search campaign “{payload['name']}” (created paused, ends {end:%b} {end.day})",
            "detail": (f"${daily_budget:,.2f}/day, Maximize clicks, only people in {place}'s {len(consts)} assigned zip codes "
                       f"({', '.join(sorted(consts))}), {len(payload['keywords'])} keywords, stops on {end:%B} {end.day}. "
                       "Check the landing page mentions the offer (Google expects it) and the offer terms are right."
                       + (" Fix before applying: " + " ".join(probs[:3]) if probs else "")),
            "payload": payload}
    with db.conn() as c:     # one draft per offer and practice: a fresh draft replaces an unapplied one
        c.execute("DELETE FROM changes WHERE practice_id=? AND kind=? AND status='proposed' AND payload LIKE ?",
                  (practice["id"], campaigns.KIND, f'%"offer": "{offer["key"]}"%'))
    campaigns._store(practice["id"], prop)
    return prop
