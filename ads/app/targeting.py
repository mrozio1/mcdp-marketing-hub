"""December plan fixes, each a proposal on the practice's fixes list (nothing changes until Apply; Undo puts it back).

- geo_zips (Google): a campaign's locations become the practice's assigned zips (zip_plan: each zip goes to one
  brand only), and it targets people *in* those zips (not "interested in" them), so practices don't overlap.
- meta_zips (Meta): the same for each running ad set.
- add_keyword: the standard phrase-match keywords for a search campaign that barely spends (narrow keywords are
  why), with the practice's city.
- draft_ad: a "use your benefits before they reset" search ad written from a template (no AI), created paused,
  with the practice's real Google rating.
"""
from __future__ import annotations

import copy
import re
from datetime import date

from . import connectors as cx
from . import db, field, zip_plan

LOW_DELIVERY = 0.2          # spends under 20% of its daily budget: keywords too narrow, not budget-limited
MAX_KEYWORDS = 8


def _err(msg):
    from . import actions
    return actions.ActionError(msg)


def _stale(what):
    return _err(f"{what} Nothing was changed; click “Prepare fixes” for an up-to-date list.")


# ------------------------------------------------------------------ Google: locations
LOC_Q = ("SELECT campaign.id, campaign_criterion.resource_name, campaign_criterion.type, campaign_criterion.negative, "
         "campaign_criterion.location.geo_target_constant, campaign_criterion.proximity.radius, "
         "campaign_criterion.proximity.radius_units, campaign_criterion.proximity.geo_point.latitude_in_micro_degrees, "
         "campaign_criterion.proximity.geo_point.longitude_in_micro_degrees FROM campaign_criterion "
         "WHERE campaign_criterion.type IN ('LOCATION', 'PROXIMITY') AND campaign_criterion.negative = FALSE "
         "AND campaign.id = {cid}")


def _criteria(g, cid, campaign_id):
    out = []
    for r in g.search(cid, LOC_Q.format(cid=int(campaign_id))):
        c = r.get("campaignCriterion") or {}
        item = {"resource": c.get("resourceName"), "type": c.get("type")}
        if c.get("type") == "LOCATION":
            item["geo"] = (c.get("location") or {}).get("geoTargetConstant")
        else:
            px = c.get("proximity") or {}
            item["proximity"] = {"radius": px.get("radius"), "radiusUnits": px.get("radiusUnits"),
                                 "geoPoint": px.get("geoPoint")}
        out.append(item)
    return out


def _geo_type(g, cid, campaign_id):
    r = g.search(cid, "SELECT campaign.geo_target_type_setting.positive_geo_target_type FROM campaign "
                      f"WHERE campaign.id = {int(campaign_id)}")
    return (((r[0] if r else {}).get("campaign") or {}).get("geoTargetTypeSetting") or {}).get("positiveGeoTargetType")


def google_geo_proposals(g, cid, practice, camps, notes):
    zips = zip_plan.practice_zips(practice["id"])
    if not zips:
        return []
    consts = g.zip_targets(zips)
    if not consts:
        notes.append("Google: the assigned zip codes couldn't be matched to Google locations.")
        return []
    want = sorted(set(consts.values()))
    out = []
    for c in camps:
        if c.get("status") != "ENABLED" or c.get("channel") not in ("SEARCH", "PERFORMANCE_MAX", "DISPLAY", "DEMAND_GEN",
                                                                      "VIDEO", "LOCAL"):
            continue
        cur = _criteria(g, cid, c["id"])
        have = sorted(x["geo"] for x in cur if x.get("geo"))
        gtype = _geo_type(g, cid, c["id"])
        if have == want and not any(x.get("proximity") for x in cur) and gtype == "PRESENCE":
            continue
        before = (f"{len(cur)} location{'s' if len(cur) != 1 else ''}" if cur else "no location targeting (all of the US)")
        out.append({"platform": "google", "account": cid, "kind": "geo_zips",
                    "title": f"Aim {c['name']} at {practice['name']}'s {len(want)} zip codes",
                    "detail": (f"Now: {before}"
                               + (", and it reaches people “interested in” the area, not just in it" if gtype != "PRESENCE" else "")
                               + f". After: only people in these zips (top two-thirds by household income near the "
                                 f"practice, none shared with another of our practices): {', '.join(sorted(consts))}."
                               + (f" Not found in Google: {', '.join(sorted(set(zips) - set(consts)))}." if set(zips) - set(consts) else "")),
                    "payload": {"campaign_id": c["id"], "campaign": c["name"], "campaign_rn": c["resource"],
                                "old": cur, "old_type": gtype, "new": want, "zips": sorted(consts)}})
    return out


def apply_geo(g, cid, pl):
    cur = _criteria(g, cid, pl["campaign_id"])
    if sorted(x["resource"] for x in cur) != sorted(x["resource"] for x in pl["old"]):
        raise _stale(f"{pl['campaign']}'s locations were changed since.")
    ops = [{"remove": x["resource"]} for x in cur]
    ops += [{"create": {"campaign": pl["campaign_rn"], "location": {"geoTargetConstant": geo}}} for geo in pl["new"]]
    res = g.mutate(cid, "campaignCriteria", ops)
    made = [r.get("resourceName") for r in res[len(cur):]] if len(res) > len(cur) else []
    if pl.get("old_type") != "PRESENCE":
        g.mutate(cid, "campaigns", [{"update": {"resourceName": pl["campaign_rn"],
                                                "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE"}},
                                     "updateMask": "geo_target_type_setting.positive_geo_target_type"}])
    return {"made": made}


def undo_geo(g, cid, pl, res):
    cur = _criteria(g, cid, pl["campaign_id"])
    ops = [{"remove": x["resource"]} for x in cur]
    for x in pl["old"]:
        if x.get("geo"):
            ops.append({"create": {"campaign": pl["campaign_rn"], "location": {"geoTargetConstant": x["geo"]}}})
        elif x.get("proximity"):
            px = {k: v for k, v in x["proximity"].items() if v}
            ops.append({"create": {"campaign": pl["campaign_rn"], "proximity": px}})
    if ops:
        g.mutate(cid, "campaignCriteria", ops)
    if pl.get("old_type") and pl["old_type"] != "PRESENCE":
        g.mutate(cid, "campaigns", [{"update": {"resourceName": pl["campaign_rn"],
                                                "geoTargetTypeSetting": {"positiveGeoTargetType": pl["old_type"]}},
                                     "updateMask": "geo_target_type_setting.positive_geo_target_type"}])


# ------------------------------------------------------------------ Meta: locations
def meta_geo_proposals(acct, practice, camps):
    from . import leaks
    zips = zip_plan.practice_zips(practice["id"])
    if not zips:
        return []
    names = {c["id"]: c["name"] for c in camps if c.get("status") == "ACTIVE"}
    want = sorted(f"US:{z}" for z in zips)
    out = []
    for a in leaks._active_adsets(acct, set(names)):
        geo = (a.get("targeting") or {}).get("geo_locations") or {}
        have = sorted(z.get("key") for z in geo.get("zips", []))
        if have == want and set(geo) <= {"zips", "location_types"}:
            continue
        out.append({"platform": "meta", "account": acct, "kind": "meta_zips",
                    "title": f"Aim {names[a['campaign_id']]} › {a['name']} at {practice['name']}'s {len(zips)} zip codes",
                    "detail": ("Replaces its locations with the practice's assigned zips (top two-thirds by household "
                               "income near the practice, none shared with another of our practices), for people who "
                               f"live there or were recently there: {', '.join(zips)}."),
                    "payload": {"object_id": a["id"], "campaign": names[a["campaign_id"]], "adset": a["name"],
                                "old": geo, "new": {"zips": [{"key": k} for k in want], "location_types": ["home", "recent"]}}})
    return out


def apply_meta_geo(pl):
    cur = cx._meta_get(pl["object_id"], {"fields": "targeting"}).get("targeting") or {}
    if (cur.get("geo_locations") or {}) != pl["old"]:
        raise _stale(f"{pl['adset']}'s locations were changed since.")
    t = copy.deepcopy(cur)
    t["geo_locations"] = pl["new"]
    cx._meta_write(pl["object_id"], {"targeting": t})
    return {}


def undo_meta_geo(pl):
    cur = cx._meta_get(pl["object_id"], {"fields": "targeting"}).get("targeting") or {}
    t = copy.deepcopy(cur)
    t["geo_locations"] = pl["old"]
    cx._meta_write(pl["object_id"], {"targeting": t})


# ------------------------------------------------------------------ keywords for campaigns that barely run
CORE = {"ortho": ["orthodontist near me", "braces near me", "invisalign near me", "kids orthodontist",
                  "orthodontist {city}", "braces {city}", "invisalign {city}", "clear aligners {city}"],
        "dental": ["dentist near me", "family dentist near me", "pediatric dentist near me", "dentist {city}",
                   "family dentist {city}", "kids dentist {city}", "dental cleaning {city}", "new patient dentist {city}"],
        "surgery": ["oral surgeon near me", "wisdom teeth removal near me", "dental implants near me",
                    "oral surgeon {city}", "wisdom teeth removal {city}", "dental implants {city}"]}


def city_of(practice):
    for a in field.practice_locations(practice["id"]):
        parts = [x.strip() for x in a.split(",")]
        if len(parts) >= 2 and re.fullmatch(r"[A-Za-z .'-]{3,30}", parts[-2] if len(parts) > 2 else parts[-1].split()[0]):
            city = parts[-2] if len(parts) > 2 else parts[-1].split()[0]
            return city.title()
    return ""


def keyword_proposals(g, cid, practice, camps, view):
    """Standard keywords for Search campaigns spending under LOW_DELIVERY of their daily budget this month."""
    spend = {c["name"]: c["spend"] for c in view.get("campaigns", [])}
    pc = view.get("pacing") or {}
    days = max(pc.get("days_elapsed") or date.today().day, 1)
    line = zip_plan.line_of(practice)
    city = city_of(practice).lower()
    out = []
    for c in camps:
        if c.get("status") != "ENABLED" or c.get("channel") != "SEARCH" or not c.get("budget"):
            continue
        if spend.get(c["name"], 0) / days >= LOW_DELIVERY * c["budget"]:
            continue
        groups = g.search(cid, f"SELECT ad_group.resource_name, ad_group.name FROM ad_group WHERE campaign.id = "
                               f"{int(c['id'])} AND ad_group.status = 'ENABLED'")
        if not groups:
            continue
        ag = groups[0]["adGroup"]
        have = {((r.get("adGroupCriterion") or {}).get("keyword") or {}).get("text", "").lower() for r in g.search(
            cid, "SELECT ad_group_criterion.keyword.text FROM ad_group_criterion WHERE ad_group_criterion.type = 'KEYWORD' "
                 f"AND campaign.id = {int(c['id'])} AND ad_group_criterion.status != 'REMOVED'")}
        have |= {(ch["payload"].get("text") or "").lower() for ch in db.changes(practice["id"], ["proposed", "failed", "stale"])
                 if ch["kind"] == "add_keyword"}
        n = 0
        for k in CORE[line]:
            if "{city}" in k and not city:
                continue
            text = k.format(city=city)
            if text in have:
                continue
            out.append({"platform": "google", "account": cid, "kind": "add_keyword",
                        "title": f"Add “{text}” to {c['name']} › {ag.get('name', '')}",
                        "detail": (f"This campaign spent {spend.get(c['name'], 0) / days:,.2f}/day of its "
                                   f"${c['budget']:,.2f}/day budget this month: its keywords are too narrow to show "
                                   "often. Phrase match, aimed at the practice's zips; Undo removes it."),
                        "payload": {"campaign": c["name"], "campaign_id": c["id"], "ad_group_rn": ag["resourceName"],
                                    "ad_group": ag.get("name", ""), "text": text, "plan": True}})
            n += 1
            if n >= MAX_KEYWORDS:
                break
    return out


# ------------------------------------------------------------------ "use your benefits" ads (template, no AI)
def rating_of(practice):
    with db.conn() as c:
        rows = c.execute("SELECT rating, total FROM gbp_locations WHERE practice_id=? AND rating IS NOT NULL",
                         (practice["id"],)).fetchall()
    n = sum(r["total"] or 0 for r in rows)
    if not n:
        return None, 0
    return round(sum((r["rating"] or 0) * (r["total"] or 0) for r in rows) / n, 1), n


def benefits_text(practice, kind="google_ad", context=""):
    line = zip_plan.line_of(practice)
    city = city_of(practice)
    rating, n = rating_of(practice)
    name = practice["name"]
    heads = ["Use Your 2026 Benefits", "Benefits Reset Dec 31", "Book Online in Minutes", "Kids, Teens & Adults",
             "Smiles for Every Age" if line == "ortho" else "New Patients Welcome"]
    heads.insert(1, {"ortho": "Free Orthodontic Consult", "dental": "Book Your Family's Visit",
                     "surgery": "Expert Oral Surgery Care"}[line])
    if line == "ortho":
        heads.append("Braces & Invisalign")
    if rating and rating >= 4.5 and n >= 20:
        heads.append(f"{rating} Stars From {n:,} Reviews")       # Google doesn't allow symbols like ★ in ads
    if city and len(f"Orthodontist in {city}") <= 30 and line == "ortho":
        heads.append(f"Orthodontist in {city}")
    elif city and len(f"Dentist in {city}") <= 30 and line == "dental":
        heads.append(f"Dentist in {city}")
    if len(name) <= 30 and not (name.isupper() and len(name) > 3):     # Google refuses all-capitals headlines
        heads.append(name)
    what = {"ortho": "a free orthodontic consult", "dental": "your family's next visit", "surgery": "your consultation"}[line]
    descs = [f"Many dental benefits and FSAs reset Dec 31. Use yours: book {what}."[:90],
             "Book online in minutes and pick a time that works for your family."]
    if rating and rating >= 4.5 and n >= 20:
        descs.insert(1, f"Rated {rating} stars by {n:,} local patients on Google. See why families choose us."[:90])
    return {"headlines": heads, "descriptions": descs}


def benefits_ads(practice, camps):
    """One paused benefits ad per enabled Search campaign that doesn't have one yet."""
    from . import actions, brand
    have = {ch["payload"].get("campaign") for ch in db.changes(practice["id"], ["proposed", "applied"])
            if ch["kind"] == "draft_ad" and ch["payload"].get("focus") == "benefits"}
    out = []
    for c in camps:
        if c.get("status") != "ENABLED" or c.get("channel") != "SEARCH" or c["name"] in have:
            continue
        try:
            prop = actions._google_draft(practice, c["name"], lambda kind, ctx: benefits_text(practice, kind, ctx),
                                         brand.kit(practice["id"]), "benefits")
        except actions.ActionError:
            continue
        prop["title"] = f"“Use your benefits before they reset” ad in {c['name']} (created paused)"
        prop["detail"] += " Pause it again after Dec 31."
        prop["payload"]["plan"] = True
        out.append(prop)
    return out
