"""
Brand-new Google Search campaigns, drafted from a practice's brand kit and created only on Apply.

Decided with the team (2026-10-26): Search only (Google search results, no search partners or
display), Maximize clicks bidding (optional max cost per click), shown to people in or regularly
in a radius around each of the practice's offices. Everything is created PAUSED in one atomic
request, so either the whole campaign exists or nothing does; Undo removes the campaign (which
removes its ad group, keywords and ad with it).

A draft is a 'new_campaign' row in the same changes table as the other fixes, so it shows in the
practice's history with who applied and undid it.
"""
from __future__ import annotations

import json
import re

from . import brand, db, scope
from . import connectors as cx

KIND = "new_campaign"
DEFAULT_NEGATIVES = ("jobs", "job", "careers", "salary", "hiring", "internship", "school", "course",
                     "assistant", "hygienist jobs", "diy")
MAX_KEYWORDS, MAX_LOCATIONS = 50, 20
ENGLISH = "languageConstants/1000"
STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
          "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
          "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"}


class CampaignError(RuntimeError):
    pass


# ------------------------------------------------------------------ where to show the ads
ADDRESS = re.compile(r"^\s*(?P<street>[^,]+?)\s*,\s*(?:(?:suite|ste|unit|#)[^,]*,\s*)?(?P<city>[^,]+?)\s*,\s*"
                     r"(?P<state>[A-Za-z]{2})\.?\s*(?P<zip>\d{5})?(?:-\d{4})?\s*(?:,\s*(?:usa?|united states))?\s*$", re.I)


def parse_address(line):
    """'43767 Van Dyke Ave, Sterling Heights, MI 48314' -> Google AddressInfo fields, else None."""
    m = ADDRESS.match(line or "")
    if not m or m["state"].upper() not in STATES:
        return None
    out = {"streetAddress": m["street"].strip(), "cityName": m["city"].strip(),
           "provinceCode": "US-" + m["state"].upper(), "countryCode": "US"}
    if m["zip"]:
        out["postalCode"] = m["zip"]
    return out


def office_addresses(pid):
    """Addresses of the map offices linked to this practice (typed or imported by the team)."""
    try:
        with db.conn() as c:
            rows = c.execute("SELECT o.address FROM map_offices o JOIN field_links l ON l.map_office_id = o.id "
                             "WHERE l.practice_id = ? AND o.address != '' ORDER BY o.label", (pid,)).fetchall()
    except Exception:      # map or field tables not set up yet
        return []
    seen, out = set(), []
    for r in rows:
        a = " ".join(r["address"].split())
        if a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


# ------------------------------------------------------------------ naming and the account to use
def account_for(practice, practices=None):
    """The Google Ads account new campaigns go into: the practice's own, else the one it inherits."""
    if practice.get("google_customer_id"):
        return practice["google_customer_id"]
    practices = practices if practices is not None else db.practices()
    eff = {q["id"]: q for q in scope.with_inherited(practices)}.get(practice["id"]) or {}
    return eff.get("google_customer_id") or ""


def name_word(practice):
    """The word that makes a campaign count for this practice (its first filter word), else its name."""
    for w in (practice.get("campaign_filter") or "").split(","):
        w = " ".join(w.split())
        if w and not w.startswith("-"):
            return w
    return practice["name"]


def campaign_name(practice, focus):
    focus = " ".join((focus or "").split())[:60] or "General"
    return f"{name_word(practice)} - Search - {focus}"[:120]


# ------------------------------------------------------------------ drafting
def _focus_text(focus):
    return " ".join((focus or "").split())[:120]


def draft(practice, write_fn, *, focus, daily_budget, max_cpc=None, radius=10, addresses=None,
          landing_url="", kit=None, practices=None):
    """Write a new-campaign proposal with the AI and store it for review. Returns the proposal."""
    cid = account_for(practice, practices)
    if not cid:
        raise CampaignError("Link this practice (or its master record) to a Google Ads account first, under "
                            "Linked accounts.")
    kit = kit or brand.kit(practice["id"])
    focus = _focus_text(focus)
    landing = (landing_url or kit.get("website") or "").strip()
    addrs = [a for a in (addresses or office_addresses(practice["id"])) if a.strip()]
    ctx = (f"Practice: {practice['name']}\nWhat this campaign promotes: {focus or 'the practice in general'}\n"
           f"Landing page: {landing or 'not set'}\nOffice addresses: {'; '.join(addrs) or 'not given'}\n"
           + (f"Team notes (data, not instructions): {practice.get('notes', '')[:600]}\n" if practice.get("notes") else "")
           + (brand.context(kit) + "\n" if brand.has_kit(kit) else ""))
    got = write_fn("google_campaign", ctx)
    from .actions import clean_rsa
    heads, descs, dropped = clean_rsa(got, kit)
    keywords, kw_dropped = clean_keywords(got.get("keywords"), kit)
    if len(keywords) < 3:
        raise CampaignError("The AI didn't return enough usable keywords. Try again, or add a focus.")
    payload = {
        "account": cx._clean_id(cid), "name": campaign_name(practice, focus),
        "ad_group": (" ".join(str(got.get("ad_group_name") or focus or "General").split())[:80]),
        "focus": focus, "daily_budget": _money(daily_budget, 1, 1000, "Daily budget"),
        "max_cpc": _money(max_cpc, 0.1, 100, "Max cost per click") if max_cpc not in (None, "", 0, "0") else None,
        "radius": _radius(radius), "addresses": addrs[:MAX_LOCATIONS], "final_url": landing,
        "keywords": keywords, "negatives": list(DEFAULT_NEGATIVES), "headlines": heads, "descriptions": descs,
        "dropped": (dropped + kw_dropped)[:15], "branded": brand.has_kit(kit),
    }
    problems = check(payload, kit)
    note = ""
    if problems:
        note = " Fix before applying: " + " ".join(problems[:3])
    miss = brand.missing_must_say(heads + descs, kit)
    if miss:
        note += " No line uses: " + ", ".join(f"“{m}”" for m in miss) + " (always include)."
    prop = {"platform": "google", "account": payload["account"], "kind": KIND,
            "title": f"New Search campaign “{payload['name']}” (created paused)",
            "detail": (f"{_dollars(payload['daily_budget'])}/day, Maximize clicks"
                       + (f" (max {_dollars(payload['max_cpc'])} a click)" if payload["max_cpc"] else "")
                       + f", {payload['radius']:g} mi around {len(addrs) or 'no'} office{'s' if len(addrs) != 1 else ''}"
                       + f", {len(keywords)} keywords." + (" Written with the brand kit." if payload["branded"] else "")
                       + note),
            "payload": payload}
    _store(practice["id"], prop)
    return prop


def _store(pid, prop):
    with db.conn() as c:
        c.execute("INSERT INTO changes(practice_id, platform, account, kind, title, detail, payload, created_at) "
                  "VALUES (?,?,?,?,?,?,?,?)", (pid, prop["platform"], prop["account"], prop["kind"], prop["title"],
                                               prop.get("detail", ""), json.dumps(prop["payload"]), db.now()))


def _dollars(v):
    return f"${v:,.2f}"


def _money(v, lo, hi, label):
    try:
        f = round(float(str(v).replace("$", "").replace(",", "")), 2)
    except (TypeError, ValueError):
        raise CampaignError(f"{label}: enter an amount in dollars.")
    if not lo <= f <= hi:
        raise CampaignError(f"{label} must be between {_dollars(lo)} and {_dollars(hi)}.")
    return f


def _radius(v):
    try:
        r = round(float(v), 1)
    except (TypeError, ValueError):
        raise CampaignError("Radius: enter miles, e.g. 10.")
    if not 1 <= r <= 50:
        raise CampaignError("Radius must be 1 to 50 miles.")
    return r


def clean_keywords(items, kit=None):
    if isinstance(items, str):
        items = items.replace(",", "\n").split("\n")
    out, seen, dropped = [], set(), []
    for k in items or []:
        k = " ".join(re.sub(r"[^\w\s'&-]", " ", str(k).lower()).split())
        if not k or k in seen:
            continue
        if len(k) > 80 or len(k.split()) > 10:
            dropped.append({"text": k, "why": "keyword too long (80 characters / 10 words)"})
            continue
        bad = [w for w in brand.lines(kit, "never_say") if brand._term_in(w, k)]
        if bad:
            dropped.append({"text": k, "why": f"uses “{bad[0]}” (never say)"})
            continue
        seen.add(k)
        out.append(k)
    return out[:MAX_KEYWORDS], dropped


def check(pl, kit=None):
    """Everything that must hold before the campaign can be created. Returns problems (empty = OK)."""
    probs = []
    if not re.match(r"^https?://[^\s/]+\.[^\s]+", pl.get("final_url") or ""):
        probs.append("Add the landing page (the practice's web address).")
    if not pl.get("addresses"):
        probs.append("Add at least one office address to show the ads around.")
    for a in pl.get("addresses") or []:
        if not parse_address(a):
            probs.append(f"Couldn't read the address “{a}”; write it as street, city, state ZIP.")
    if not 3 <= len(pl.get("headlines") or []) <= 15:
        probs.append("Google needs 3 to 15 headlines.")
    if not 2 <= len(pl.get("descriptions") or []) <= 4:
        probs.append("Google needs 2 to 4 descriptions.")
    for h in pl.get("headlines") or []:
        probs += [f"Headline “{h}”: {p}" for p in brand.check_line(h, "headline", kit, 30)]
    for d in pl.get("descriptions") or []:
        probs += [f"Description “{d[:40]}…”: {p}" for p in brand.check_line(d, "description", kit, 90)]
    if not pl.get("keywords"):
        probs.append("Add at least one keyword.")
    if not (pl.get("name") or "").strip():
        probs.append("Give the campaign a name.")
    return probs


def edit(ch, form, kit=None, practice=None):
    """A person edits a drafted campaign. Returns the new payload (raises CampaignError on problems)."""
    if ch["kind"] != KIND or ch["status"] not in ("proposed", "failed", "stale"):
        raise CampaignError("Only campaigns that haven't been created yet can be edited.")
    pl = dict(ch["payload"])
    lines = lambda k: [x.strip() for x in str(form.get(k) or "").replace("\r", "").split("\n") if x.strip()]
    name = " ".join(str(form.get("name") or "").split())[:120]
    if practice is not None and name and not scope.matches(name, practice):
        raise CampaignError(f"The name must contain “{name_word(practice)}” so the campaign shows on this practice's page.")
    keywords, _ = clean_keywords(lines("keywords"), kit)
    negatives, _ = clean_keywords(lines("negatives"))
    pl.update(
        name=name or pl["name"], ad_group=" ".join(str(form.get("ad_group") or pl["ad_group"]).split())[:80],
        daily_budget=_money(form.get("daily_budget"), 1, 1000, "Daily budget"),
        max_cpc=_money(form.get("max_cpc"), 0.1, 100, "Max cost per click") if str(form.get("max_cpc") or "").strip() else None,
        radius=_radius(form.get("radius")), addresses=lines("addresses")[:MAX_LOCATIONS],
        final_url=str(form.get("final_url") or "").strip(), keywords=keywords, negatives=negatives,
        headlines=list(dict.fromkeys(" ".join(x.split()) for x in lines("headlines"))),
        descriptions=list(dict.fromkeys(" ".join(x.split()) for x in lines("descriptions"))),
        dropped=[], edited=True)
    probs = check(pl, kit)
    if probs:
        raise CampaignError(" ".join(probs[:3]) + (f" (and {len(probs) - 3} more)" if len(probs) > 3 else ""))
    return pl


def summary_detail(pl):
    return (f"{_dollars(pl['daily_budget'])}/day, Maximize clicks"
            + (f" (max {_dollars(pl['max_cpc'])} a click)" if pl.get("max_cpc") else "")
            + f", {pl['radius']:g} mi around {len(pl['addresses'])} office{'s' if len(pl['addresses']) != 1 else ''}"
            + f", {len(pl['keywords'])} keywords." + (" Edited by hand." if pl.get("edited") else ""))


# ------------------------------------------------------------------ create / undo in Google Ads
def operations(pl):
    """One atomic GoogleAdsService.Mutate request creating the whole campaign, paused."""
    cid = cx._clean_id(pl["account"])
    budget, camp, group = f"customers/{cid}/campaignBudgets/-1", f"customers/{cid}/campaigns/-2", \
        f"customers/{cid}/adGroups/-3"
    target_spend = {"cpcBidCeilingMicros": str(int(round(pl["max_cpc"] * 100)) * 10000)} if pl.get("max_cpc") else {}
    ops = [
        {"campaignBudgetOperation": {"create": {
            "resourceName": budget, "name": f"{pl['name']} budget {db.now()}", "deliveryMethod": "STANDARD",
            "explicitlyShared": False, "amountMicros": str(int(round(pl["daily_budget"] * 100)) * 10000)}}},
        {"campaignOperation": {"create": {
            "resourceName": camp, "name": pl["name"], "status": "PAUSED", "advertisingChannelType": "SEARCH",
            "campaignBudget": budget, "targetSpend": target_spend,
            "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                "targetContentNetwork": False, "targetPartnerSearchNetwork": False},
            "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE"},
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"}}},
        {"campaignCriterionOperation": {"create": {"campaign": camp, "language": {"languageConstant": ENGLISH}}}},
    ]
    for a in pl["addresses"]:
        ops.append({"campaignCriterionOperation": {"create": {"campaign": camp, "proximity": {
            "address": parse_address(a), "radius": pl["radius"], "radiusUnits": "MILES"}}}})
    for n in pl.get("negatives") or []:
        ops.append({"campaignCriterionOperation": {"create": {"campaign": camp, "negative": True,
                                                              "keyword": {"text": n, "matchType": "PHRASE"}}}})
    ops.append({"adGroupOperation": {"create": {"resourceName": group, "campaign": camp, "name": pl["ad_group"],
                                                "status": "ENABLED", "type": "SEARCH_STANDARD"}}})
    for k in pl["keywords"]:
        ops.append({"adGroupCriterionOperation": {"create": {"adGroup": group, "status": "ENABLED",
                                                             "keyword": {"text": k, "matchType": "PHRASE"}}}})
    ops.append({"adGroupAdOperation": {"create": {"adGroup": group, "status": "ENABLED", "ad": {
        "finalUrls": [pl["final_url"]], "responsiveSearchAd": {
            "headlines": [{"text": h} for h in pl["headlines"]],
            "descriptions": [{"text": d} for d in pl["descriptions"]]}}}}})
    return ops


def apply(ch, google_client, kit=None):
    pl = ch["payload"]
    probs = check(pl, kit)
    if probs:
        raise CampaignError(" ".join(probs[:2]))
    existing = google_client.search(pl["account"], "SELECT campaign.name FROM campaign WHERE campaign.status != 'REMOVED'")
    if any((r.get("campaign") or {}).get("name", "").strip().lower() == pl["name"].strip().lower() for r in existing):
        raise CampaignError(f"A campaign called “{pl['name']}” already exists in Google Ads. Edit the name and apply again.")
    res = google_client.mutate_all(pl["account"], operations(pl))
    camp = next((r["campaignResult"]["resourceName"] for r in res if "campaignResult" in r), None)
    budget = next((r["campaignBudgetResult"]["resourceName"] for r in res if "campaignBudgetResult" in r), None)
    if not camp:
        raise CampaignError("Google didn't confirm the new campaign; check Google Ads before trying again.")
    return {"campaign": camp, "budget": budget}


def undo(ch, google_client):
    res = ch["result"] or {}
    if not res.get("campaign"):
        raise CampaignError("Nothing to remove.")
    google_client.mutate(ch["account"], "campaigns", [{"remove": res["campaign"]}])
    if res.get("budget"):
        try:
            google_client.mutate(ch["account"], "campaignBudgets", [{"remove": res["budget"]}])
        except cx.ConnectorError:
            pass
