"""Stopping the leaks: fixes that keep ad money away from places that don't bring bookings, and make the numbers
honest. Each is a proposal on the practice's fixes list (nothing changes until Apply, and each can be undone).

- placements:      Meta ad sets that can show on Audience Network (ads inside other apps and games; clicks that
                   rarely book) are kept to Facebook and Instagram.
- pause (LeadSigma): a Meta campaign whose visits reached the booking page hundreds of times with no bookings,
                   per the latest LeadSigma export.
- meta_tags:       Meta ads without UTM tags get them (utm_source=facebook ...), so LeadSigma can say which ad
                   booked. Meta can't edit an ad's creative, so each ad gets a copy of its creative with the tags;
                   undo puts the old creative back. Meta reviews the ad again (usually minutes).
- conversion_goal: a Google Ads conversion action that counts something other than a booking or a lead (page
                   views, engagement, clicks) and is set as "primary" is made "secondary": it is still reported
                   under All conversions, but stops counting in Conversions and stops steering bidding.
"""
from __future__ import annotations

import copy
import re
from datetime import date, timedelta

from . import connectors as cx
from . import db

META_TAGS = ("utm_source=facebook&utm_medium=paid_social&utm_campaign={{campaign.name}}"
             "&utm_content={{ad.name}}&utm_term={{adset.name}}")
LS_MIN_VISITS = 500                      # booking-page visits with no bookings before a pause is suggested

# Conversion action categories that are a booking, lead or call: these stay primary.
GOOD_CATEGORIES = {"SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT", "PHONE_CALL_LEAD", "CONTACT", "IMPORTED_LEAD",
                   "QUALIFIED_LEAD", "CONVERTED_LEAD", "REQUEST_QUOTE", "SIGNUP", "PURCHASE", "GET_DIRECTIONS",
                   "STORE_VISIT"}
WEAK_CATEGORIES = {"PAGE_VIEW", "ENGAGEMENT", "OUTBOUND_CLICK", "DOWNLOAD", "ADD_TO_CART", "BEGIN_CHECKOUT",
                   "DEFAULT"}
WEAK_NAME = re.compile(r"page ?view|pageview|scroll|engag|time on|session|visit|all clicks|outbound|"
                       r"click|website traffic|button", re.I)
GOOD_NAME = re.compile(r"book|appoint|lead|form|submit|call|consult|schedul|contact|confirm", re.I)


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# ------------------------------------------------------------------ Meta: placements
def _active_adsets(acct, camp_ids):
    rows = cx._meta_paged(f"act_{acct}/adsets", {"fields": "id,name,campaign_id,status,effective_status,targeting",
                                                  "limit": 200})
    return [a for a in rows if a.get("campaign_id") in camp_ids and a.get("status") == "ACTIVE"]


def without_audience_network(targeting):
    """The targeting with Audience Network taken out ('' when there's nothing to change)."""
    t = copy.deepcopy(targeting or {})
    plats = t.get("publisher_platforms")
    if plats and "audience_network" not in plats:
        return None
    keep = [p for p in (plats or ["facebook", "instagram", "messenger", "audience_network"]) if p != "audience_network"]
    keep = [p for p in keep if p in ("facebook", "instagram")] or ["facebook", "instagram"]
    t["publisher_platforms"] = keep
    t.pop("audience_network_positions", None)
    t.pop("messenger_positions", None)
    return t


def placement_proposals(acct, camps):
    names = {c["id"]: c["name"] for c in camps if c.get("status") == "ACTIVE"}
    out = []
    for a in _active_adsets(acct, set(names)):
        new = without_audience_network(a.get("targeting"))
        if new is None:
            continue
        auto = not (a.get("targeting") or {}).get("publisher_platforms")
        out.append({"platform": "meta", "account": acct, "kind": "placements",
                    "title": f"Keep {names[a['campaign_id']]} › {a['name']} to Facebook and Instagram",
                    "detail": ("It can show on Meta's Audience Network (ads inside other apps and games), where clicks are "
                               "cheap but rarely book. " + ("It uses automatic placements; this keeps Facebook and "
                                                            "Instagram (feeds, Stories, Reels) and drops the rest."
                                                            if auto else "This drops Audience Network only.")),
                    "payload": {"object_id": a["id"], "campaign": names[a["campaign_id"]], "adset": a["name"],
                                "old": a.get("targeting") or {}, "new": new}})
    return out


def apply_placements(pl):
    cur = cx._meta_get(pl["object_id"], {"fields": "targeting"}).get("targeting") or {}
    if (cur.get("publisher_platforms") or None) != (pl["old"].get("publisher_platforms") or None):
        raise _stale(f"{pl['adset']}'s placements were changed since.")
    cx._meta_write(pl["object_id"], {"targeting": _merge_targeting(cur, pl["new"])})
    return {}


def _merge_targeting(cur, new):
    t = copy.deepcopy(cur)
    t["publisher_platforms"] = new["publisher_platforms"]
    t.pop("audience_network_positions", None)
    t.pop("messenger_positions", None)
    return t


def undo_placements(pl):
    cur = cx._meta_get(pl["object_id"], {"fields": "targeting"}).get("targeting") or {}
    if sorted(cur.get("publisher_platforms") or []) != sorted(pl["new"]["publisher_platforms"]):
        raise ActionError(f"{pl['adset']}'s placements were changed since, so they were left as they are.")
    t = copy.deepcopy(cur)
    for k in ("publisher_platforms", "audience_network_positions", "messenger_positions"):
        if k in pl["old"]:
            t[k] = pl["old"][k]
        else:
            t.pop(k, None)
    cx._meta_write(pl["object_id"], {"targeting": t})


# ------------------------------------------------------------------ Meta: campaigns LeadSigma says don't book
def leadsigma_dead(today=None):
    """[{'campaign', 'views', 'sources'}] from the latest LeadSigma export: campaigns with visits and no bookings."""
    from . import dashboard
    today = today or date.today()
    try:
        ls = dashboard.leadsigma(today - timedelta(days=45), today, db.practices())
    except Exception:
        return []
    return [d for d in (ls or {}).get("dead", []) if d["views"] >= LS_MIN_VISITS]


def matches_campaign(ls_name, camp_name):
    a, b = _norm(ls_name), _norm(camp_name)
    if len(a) < 8 or len(b) < 8:
        return a == b and bool(a)
    return a in b or b in a


def leadsigma_pauses(acct, camps, already=()):
    dead = leadsigma_dead()
    out = []
    for c in camps:
        if c.get("status") != "ACTIVE" or c["id"] in already:
            continue
        d = next((d for d in dead if matches_campaign(d["campaign"], c.get("name"))), None)
        if not d:
            continue
        an = sum(v for s, v in d.get("sources", []) if s == "an")
        out.append({"platform": "meta", "account": acct, "kind": "pause", "title": f"Pause {c['name']}",
                    "detail": (f"LeadSigma: {d['views']:,} visits to the booking page from this campaign and no bookings"
                               + (f" ({an:,} of them from Audience Network)" if an else "") + ". Pausing stops the spend; "
                               "undo turns it back on."),
                    "payload": {"object_id": c["id"], "campaign": c["name"], "from": "ACTIVE", "to": "PAUSED"}})
    return out


# ------------------------------------------------------------------ Meta: UTM tags on ads
def _active_ads(acct, camp_ids):
    rows = cx._meta_paged(f"act_{acct}/ads", {
        "fields": "id,name,campaign_id,status,creative{id,url_tags,object_story_id,asset_feed_spec}",
        "effective_status": '["ACTIVE"]', "limit": 25})          # small pages: Meta refuses big creative reads
    return [a for a in rows if a.get("campaign_id") in camp_ids and a.get("status") == "ACTIVE"]


def tag_proposal(acct, camps):
    names = {c["id"]: c["name"] for c in camps if c.get("status") == "ACTIVE"}
    if not names:
        return None
    todo, skipped = [], []
    for a in _active_ads(acct, set(names)):
        cr = a.get("creative") or {}
        if "utm_source" in (cr.get("url_tags") or ""):
            continue
        if cr.get("asset_feed_spec"):
            skipped.append(a["name"])
            continue
        todo.append({"ad_id": a["id"], "ad": a["name"], "campaign": names[a["campaign_id"]], "creative_id": cr["id"],
                     "url_tags": cr.get("url_tags") or ""})
    if not todo:
        return None
    return {"platform": "meta", "account": acct, "kind": "meta_tags",
            "title": f"Tag {len(todo)} Meta ad{'s' if len(todo) != 1 else ''} so LeadSigma can see which ones book",
            "detail": ("Adds utm_source=facebook, utm_medium=paid_social and the campaign, ad set and ad names to each ad's "
                       "link. Meta can't edit a live ad's link, so each ad gets a copy of its creative with the tags; Meta "
                       "reviews it again (usually minutes) and the ad keeps its results. "
                       + (f"Left alone (dynamic or catalogue ads, set their URL parameters in Ads Manager): "
                          f"{', '.join(skipped[:5])}." if skipped else "")),
            "payload": {"ads": todo, "tags": META_TAGS}}


def _join_tags(old, tags):
    old = "&".join(p for p in (old or "").split("&") if p and not p.startswith("utm_"))
    return f"{tags}&{old}" if old else tags


def apply_tags(acct, pl):
    done, problems = [], []
    for a in pl["ads"]:
        try:
            cur = cx._meta_get(a["ad_id"], {"fields": "creative{id,object_story_id,object_story_spec,url_tags,name}"})
            cr = cur.get("creative") or {}
            if cr.get("id") != a["creative_id"]:
                problems.append(f"{a['ad']}: its creative was changed since")
                continue
            new = {"name": f"{cr.get('name') or a['ad']} (tagged)"[:100], "url_tags": _join_tags(cr.get("url_tags"), pl["tags"])}
            if cr.get("object_story_id"):
                new["object_story_id"] = cr["object_story_id"]
            else:
                new["object_story_spec"] = cr["object_story_spec"]
            made = cx._meta_write(f"act_{acct}/adcreatives", new)
            cx._meta_write(a["ad_id"], {"creative": {"creative_id": made["id"]}})
            done.append({**a, "new_creative_id": made["id"]})
        except cx.ConnectorError as e:
            problems.append(f"{a['ad']}: {e}")
    if not done:
        raise ActionError("Meta didn't accept any of the tags. " + "; ".join(problems))
    res = {"ads": done}
    if problems:
        res["warning"] = f"Meta didn't accept {len(problems)} ad{'s' if len(problems) != 1 else ''}: " + "; ".join(problems)
    return res


def undo_tags(res):
    problems = []
    for a in res.get("ads", []):
        try:
            cur = cx._meta_get(a["ad_id"], {"fields": "creative{id}"}).get("creative") or {}
            if cur.get("id") != a["new_creative_id"]:
                problems.append(f"{a['ad']} (changed since)")
                continue
            cx._meta_write(a["ad_id"], {"creative": {"creative_id": a["creative_id"]}})
        except cx.ConnectorError as e:
            problems.append(f"{a['ad']} ({e})")
    if problems and len(problems) == len(res.get("ads", [])):
        raise ActionError("Couldn't put the old links back: " + "; ".join(problems))


# ------------------------------------------------------------------ Google: what counts as a conversion
def conversion_actions(g, cid):
    """Every enabled conversion action, with what it counted in the last 30 days."""
    acts = {}
    for r in g.search(cid, "SELECT conversion_action.resource_name, conversion_action.id, conversion_action.name, "
                           "conversion_action.category, conversion_action.type, conversion_action.primary_for_goal, "
                           "conversion_action.status FROM conversion_action WHERE conversion_action.status = 'ENABLED'"):
        a = r.get("conversionAction") or {}
        acts[a.get("resourceName")] = {"resource": a.get("resourceName"), "name": a.get("name") or "",
                                       "category": a.get("category") or "", "type": a.get("type") or "",
                                       "primary": bool(a.get("primaryForGoal")), "conv": 0.0}
    try:
        for r in g.search(cid, "SELECT segments.conversion_action, metrics.all_conversions FROM customer "
                               "WHERE segments.date DURING LAST_30_DAYS"):
            rn = (r.get("segments") or {}).get("conversionAction")
            if rn in acts:
                acts[rn]["conv"] += float((r.get("metrics") or {}).get("allConversions") or 0)
    except cx.ConnectorError:
        pass
    return list(acts.values())


def is_weak(a):
    if a["category"] in GOOD_CATEGORIES and not WEAK_NAME.search(a["name"]):
        return False
    if GOOD_NAME.search(a["name"]) and a["category"] not in ("PAGE_VIEW", "ENGAGEMENT"):
        return False
    return a["category"] in WEAK_CATEGORIES or bool(WEAK_NAME.search(a["name"]))


def conversion_proposal(g, cid, notes=None):
    acts = conversion_actions(g, cid)
    weak = [a for a in acts if a["primary"] and is_weak(a)]
    if not weak:
        return None
    keep = [a for a in acts if a["primary"] and not is_weak(a)]
    if not keep:
        if notes is not None:
            notes.append("Google: every primary conversion counts page views or clicks, not bookings ("
                         + ", ".join(a["name"] for a in weak[:4]) + "). Add a booking or call conversion first; "
                         "then Prepare fixes offers to make these secondary.")
        return None
    total = sum(a["conv"] for a in weak)
    return {"platform": "google", "account": cid, "kind": "conversion_goal",
            "title": f"Count bookings, not {'page views and clicks' if len(weak) > 1 else weak[0]['name']}",
            "detail": ("These conversion actions count something other than a booking or lead, and Google counts them as "
                       f"conversions ({total:,.0f} in the last 30 days), which makes cost per conversion look far better "
                       "than it is and steers bidding toward them: " + "; ".join(
                           f"{a['name']} ({a['category'].replace('_', ' ').lower() or 'other'}, {a['conv']:,.0f})"
                           for a in weak) + ". They become secondary: still reported under All conversions, no longer in "
                       "Conversions. These keep counting: " + ", ".join(a["name"] for a in keep) + "."),
            "payload": {"actions": [{"resource": a["resource"], "name": a["name"]} for a in weak]}}


def apply_conversion_goal(g, cid, pl):
    cur = {a["resource"]: a for a in conversion_actions(g, cid)}
    todo = [a for a in pl["actions"] if cur.get(a["resource"], {}).get("primary")]
    if not todo:
        raise _stale("Those conversion actions are already secondary.")
    g.mutate(cid, "conversionActions", [{"update": {"resourceName": a["resource"], "primaryForGoal": False},
                                         "updateMask": "primary_for_goal"} for a in todo])
    return {"actions": todo}


def undo_conversion_goal(g, cid, res):
    g.mutate(cid, "conversionActions", [{"update": {"resourceName": a["resource"], "primaryForGoal": True},
                                         "updateMask": "primary_for_goal"} for a in res.get("actions", [])])


# ------------------------------------------------------------------ Google: which searches are safe to block
CORE = re.compile(r"orthodont|\bortho\b|braces|invisalign|aligner|retainer|\bdentist|\bdental", re.I)
JUNK = re.compile(r"medicaid|molina|meridian|medicare|\bcheap|\bjobs?\b|salary|careers?|school|course|\bdiy\b|"
                  r"emergency|how to|what is|\bfree\b|cost of living|reddit", re.I)
GENERIC = {"the", "and", "orthodontics", "orthodontic", "orthodontist", "ortho", "dental", "dentistry", "family",
           "smiles", "smile", "llc", "pllc", "search", "care", "pediatric", "center", "group", "specialists", "dds", "dr"}
CORE_MIN_SPEND, CORE_MIN_CLICKS = 40.0, 5


def brand_words(practice, campaign=""):
    words = set()
    for s in (practice.get("name"), practice.get("campaign_filter"), (campaign or "").split(" - ")[0]):
        for part in (s or "").split(","):
            if part.strip().startswith("-"):
                continue
            words |= {w for w in re.findall(r"[a-z0-9]+", part.lower())
                      if len(w) > 3 and w not in GENERIC and not CORE.search(w)}   # 'Invisalign - Search' isn't a name
    return words


def safe_to_block(term, spend, clicks, practice, campaign=""):
    """A search worth blocking: never one with our own name in it; a core search ('invisalign near me',
    'orthodontist canton mi') only with real spend and clicks behind it, unless it's clearly the wrong person
    (jobs, Medicaid, cheap, DIY ...)."""
    if JUNK.search(term):
        return True                                   # "havens orthodontics jobs" is still the wrong person
    words = set(re.findall(r"[a-z0-9]+", term.lower()))
    if words & brand_words(practice, campaign):
        return False
    if CORE.search(term):
        return spend >= CORE_MIN_SPEND and clicks >= CORE_MIN_CLICKS
    return True


# ------------------------------------------------------------------ shared
def ActionError(msg):            # the fixes' own error, so Apply / Undo show it
    from . import actions
    return actions.ActionError(msg)


def _stale(what):
    from . import actions
    return actions.ActionError(f"{what} Nothing was changed; click “Prepare fixes” for an up-to-date list.")
