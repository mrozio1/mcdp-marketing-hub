"""Google Ads tracking: UTM tags that LeadSigma (and any site analytics) can read.

The problem it fixes: codes Google Ads doesn't know, like {campaignname} or {adname}, are sent to the website as plain
text, so a booking from an ad can't be tied to its campaign. Google has no code for a campaign's name, so each
campaign gets a custom parameter, {_campaign}, holding a short version of its name.

The fix ("tracking"), proposed on the record that owns a Google Ads account and applied only on a click:
1. the account's final URL suffix becomes SUFFIX;
2. campaign, ad group and ad settings that use unknown codes have just those codes taken out; a campaign, ad group
   or ad suffix that would override the account's (and so drop the UTM tags) gets the UTM tags added in front;
3. every campaign gets its {_campaign} value.
Undo puts back every value it changed (where nobody has changed it since). Once applied, new campaigns found after
each sync are tagged automatically (setting tracking_auto).
"""
from __future__ import annotations

import json
import re

from . import connectors as cx
from . import db

SUFFIX = "utm_source=google&utm_medium=cpc&utm_campaign={_campaign}&utm_content={creative}&utm_term={keyword}"
KEY = "campaign"                    # referenced as {_campaign}; keys are letters and digits, 16 at most
# ValueTrack codes Google Ads fills in (support.google.com/google-ads/answer/6305348), plus the URL insertion ones
VALID = {"campaignid", "adgroupid", "creative", "keyword", "matchtype", "search_term_matchtype", "network", "device",
         "devicemodel", "gclid", "targetid", "placement", "target", "loc_physical_ms", "loc_interest_ms", "feeditemid",
         "extensionid", "param1", "param2", "random", "ignore", "adtype", "adposition", "sourceid", "assetgroupid",
         "assetid", "product_id", "product_country", "product_language", "product_channel", "product_partition_id",
         "store_code", "merchant_id", "hotel_id", "hotel_partner_id", "lpurl", "lpurl+2", "lpurl+3", "unescapedlpurl",
         "escapedlpurl", "escapedlpurl+2", "escapedlpurl+3", "gbraid", "wbraid"}
CODE = re.compile(r"\{([^{}]*)\}")


def is_valid(code):
    c = code.strip().lower()
    return c in VALID or c.startswith("_") or c.split(":", 1)[0] in ("ifmobile", "ifnotmobile", "ifsearch", "ifcontent")


def bad_codes(text):
    return [m for m in CODE.findall(text or "") if not is_valid(m)]


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s or "campaign")[:120]


def clean(text):
    """Take out query parameters whose value uses a code Google doesn't know (keeps everything else as it was)."""
    if not text or not bad_codes(text):
        return text or ""
    head, sep, query = text.partition("?") if "?" in text and not text.startswith("?") else ("", "", text)
    parts = [p for p in re.split(r"&", query) if p and not bad_codes(p)]
    out = "&".join(parts)
    return (head + (sep if out else "") + out) if head else out


def fixed_suffix(text):
    """A campaign / ad group / ad suffix overrides the account's: keep its own (good) parameters, with the UTM
    tags in front so tracking still works. Empty when nothing of its own is left (the account's then applies)."""
    rest = [p for p in clean(text).split("&") if p and not p.lower().startswith("utm_")]
    return "&".join([SUFFIX] + rest) if rest else ""


# ------------------------------------------------------------------ reading the account
def _q(g, cid, q):
    return g.search(cid, q)


def read(g, cid, full=True):
    """Everything the fix looks at, from the live account (full=False: the account and campaigns only)."""
    cust = (_q(g, cid, "SELECT customer.final_url_suffix, customer.tracking_url_template FROM customer LIMIT 1") or [{}])[0]
    c = cust.get("customer", {})
    camps = []
    for r in _q(g, cid, "SELECT campaign.id, campaign.name, campaign.resource_name, campaign.final_url_suffix, "
                        "campaign.tracking_url_template, campaign.url_custom_parameters, campaign.status "
                        "FROM campaign WHERE campaign.status != 'REMOVED'"):
        x = r.get("campaign", {})
        camps.append({"id": str(x.get("id")), "name": x.get("name") or "", "rn": x.get("resourceName"),
                      "suffix": x.get("finalUrlSuffix") or "", "template": x.get("trackingUrlTemplate") or "",
                      "params": x.get("urlCustomParameters") or []})
    groups, ads = [], []
    if not full:
        return {"suffix": c.get("finalUrlSuffix") or "", "template": c.get("trackingUrlTemplate") or "",
                "campaigns": camps, "groups": groups, "ads": ads}
    for r in _q(g, cid, "SELECT ad_group.resource_name, ad_group.name, ad_group.final_url_suffix, "
                        "ad_group.tracking_url_template FROM ad_group WHERE ad_group.status != 'REMOVED'"):
        x = r.get("adGroup", {})
        if x.get("finalUrlSuffix") or x.get("trackingUrlTemplate"):
            groups.append({"rn": x.get("resourceName"), "name": x.get("name") or "", "suffix": x.get("finalUrlSuffix") or "",
                           "template": x.get("trackingUrlTemplate") or ""})
    for r in _q(g, cid, "SELECT ad_group_ad.ad.resource_name, ad_group_ad.ad.id, ad_group_ad.ad.final_url_suffix, "
                        "ad_group_ad.ad.tracking_url_template FROM ad_group_ad WHERE ad_group_ad.status != 'REMOVED'"):
        x = (r.get("adGroupAd") or {}).get("ad") or {}
        if x.get("finalUrlSuffix") or x.get("trackingUrlTemplate"):
            ads.append({"rn": x.get("resourceName") or f"customers/{cx._clean_id(cid)}/ads/{x.get('id')}",
                        "name": f"ad {x.get('id')}", "suffix": x.get("finalUrlSuffix") or "",
                        "template": x.get("trackingUrlTemplate") or ""})
    return {"suffix": c.get("finalUrlSuffix") or "", "template": c.get("trackingUrlTemplate") or "",
            "campaigns": camps, "groups": groups, "ads": ads}


def _param(params, key=KEY):
    return next((p.get("value") for p in params or [] if p.get("key") == key), None)


def plan(state):
    """What the fix would change. None when the account already tracks properly."""
    edits = []          # {level, rn, name, field, old, new}
    for level, items in (("campaign", state["campaigns"]), ("ad group", state["groups"]), ("ad", state["ads"])):
        for it in items:
            if it["suffix"] and (bad_codes(it["suffix"]) or "utm_campaign={_campaign}" not in it["suffix"]):
                new = fixed_suffix(it["suffix"])
                if new != it["suffix"]:
                    edits.append({"level": level, "rn": it["rn"], "name": it["name"], "field": "final_url_suffix",
                                  "old": it["suffix"], "new": new})
            if it["template"] and bad_codes(it["template"]):
                edits.append({"level": level, "rn": it["rn"], "name": it["name"], "field": "tracking_url_template",
                              "old": it["template"], "new": clean(it["template"])})
    tags = [{"rn": c["rn"], "name": c["name"], "old": c["params"], "value": slug(c["name"])}
            for c in state["campaigns"] if _param(c["params"]) != slug(c["name"])]
    acct = None
    if state["suffix"] != SUFFIX:
        acct = {"field": "final_url_suffix", "old": state["suffix"], "new": SUFFIX}
    acct_tpl = None
    if state["template"] and bad_codes(state["template"]):
        acct_tpl = {"field": "tracking_url_template", "old": state["template"], "new": clean(state["template"])}
    if not (edits or tags or acct or acct_tpl):
        return None
    bad = sorted({b for t in [state["suffix"], state["template"]] + [x for e in edits for x in (e["old"],)]
                  for b in bad_codes(t)})
    return {"account": acct, "account_template": acct_tpl, "edits": edits, "tags": tags, "bad": ["{" + b + "}" for b in bad]}


def proposal(cid, p):
    """The fix as a change proposal for the practice that owns this account, or None."""
    from .actions import _google
    g = _google()
    pl = plan(read(g, cid))
    if not pl:
        return None
    bits = []
    if pl["bad"]:
        bits.append(f"Takes out {', '.join(pl['bad'][:4])}, which Google Ads doesn't fill in, so they reach the website as "
                    "plain text.")
    if pl["account"]:
        bits.append("Sets the account's tracking to " + SUFFIX + ".")
    if pl["edits"]:
        bits.append(f"Updates {len(pl['edits'])} campaign, ad group or ad setting{'s' if len(pl['edits']) != 1 else ''} "
                    "that would override it.")
    if pl["tags"]:
        bits.append(f"Gives {len(pl['tags'])} campaign{'s' if len(pl['tags']) != 1 else ''} a {{_campaign}} name "
                    "(e.g. " + ", ".join(t["value"] for t in pl["tags"][:2]) + ").")
    bits.append("New campaigns are tagged automatically after each sync. Undo puts everything back.")
    return {"platform": "google", "account": cid, "kind": "tracking",
            "title": "Fix ad tracking so bookings show which campaign they came from",
            "detail": " ".join(bits), "payload": pl}


# ------------------------------------------------------------------ applying
def _service(level):
    return {"campaign": "campaigns", "ad group": "adGroups", "ad": "ads"}[level]


def _mask(field):
    return {"final_url_suffix": "final_url_suffix", "tracking_url_template": "tracking_url_template"}[field]


def _camel(field):
    return {"final_url_suffix": "finalUrlSuffix", "tracking_url_template": "trackingUrlTemplate"}[field]


def _set_params(g, cid, rn, params):
    g.mutate(cid, "campaigns", [{"update": {"resourceName": rn, "urlCustomParameters": params},
                                 "updateMask": "url_custom_parameters"}])


def _with_tag(params, value):
    return [p for p in params or [] if p.get("key") != KEY] + [{"key": KEY, "value": value}]


def apply(g, cid, pl):
    """Apply a plan. Returns what was done (for undo) and a warning for anything Google refused."""
    from .actions import ActionError
    live = read(g, cid)
    if pl.get("account") and live["suffix"] != pl["account"]["old"]:
        raise ActionError("The account's tracking was changed in Google Ads since this was prepared. Nothing was changed; "
                          "click “Prepare fixes” for an up-to-date list.")
    done = {"account": None, "account_template": None, "edits": [], "tags": [], "refused": []}
    # tags first: the account suffix refers to {_campaign}
    for t in pl.get("tags", []):
        cur = next((c for c in live["campaigns"] if c["rn"] == t["rn"]), None)
        if not cur:
            continue
        try:
            _set_params(g, cid, t["rn"], _with_tag(cur["params"], t["value"]))
            done["tags"].append({"rn": t["rn"], "name": t["name"], "old": cur["params"], "value": t["value"]})
        except cx.ConnectorError as e:
            done["refused"].append(f"{t['name']}: {str(e)[:140]}")
    for e in pl.get("edits", []):
        try:
            g.mutate(cid, _service(e["level"]), [{"update": {"resourceName": e["rn"], _camel(e["field"]): e["new"]},
                                                  "updateMask": _mask(e["field"])}])
            done["edits"].append(e)
        except cx.ConnectorError as err:
            done["refused"].append(f"{e['level']} {e['name']}: {str(err)[:140]}")
    for key in ("account", "account_template"):
        a = pl.get(key)
        if a:
            g.mutate_customer(cid, {_camel(a["field"]): a["new"]}, _mask(a["field"]))
            done[key] = a
    auto = _auto()
    auto[str(cx._clean_id(cid))] = True
    db.set_setting("tracking_auto", json.dumps(auto))
    if done["refused"]:
        done["warning"] = (f"Google Ads didn't accept {len(done['refused'])} change{'s' if len(done['refused']) != 1 else ''}: "
                           + "; ".join(done["refused"][:3]))
    return done


def undo(g, cid, res):
    """Put back what apply changed, where it's still what apply set."""
    from .actions import ActionError
    live = read(g, cid, full=False)
    skipped = []
    for key in ("account", "account_template"):
        a = res.get(key)
        if a:
            now = live["suffix"] if a["field"] == "final_url_suffix" else live["template"]
            if now == a["new"]:
                g.mutate_customer(cid, {_camel(a["field"]): a["old"]}, _mask(a["field"]))
            else:
                skipped.append("the account's tracking")
    for e in res.get("edits", []):
        g.mutate(cid, _service(e["level"]), [{"update": {"resourceName": e["rn"], _camel(e["field"]): e["old"]},
                                              "updateMask": _mask(e["field"])}])
    for t in res.get("tags", []):
        cur = next((c for c in live["campaigns"] if c["rn"] == t["rn"]), None)
        if cur and _param(cur["params"]) == t["value"]:
            _set_params(g, cid, t["rn"], t["old"])
    auto = _auto()
    auto.pop(str(cx._clean_id(cid)), None)
    db.set_setting("tracking_auto", json.dumps(auto) if auto else None)
    if skipped:
        raise ActionError("Put back everything except " + ", ".join(skipped) + ", which was changed in Google Ads since.")


# ------------------------------------------------------------------ keeping new campaigns tagged
def _auto():
    try:
        return json.loads(db.get_setting("tracking_auto") or "{}")
    except ValueError:
        return {}


def tag_new_campaigns(g):
    """After a sync: give every campaign that lacks it its {_campaign} value, in accounts where the fix was applied.
    Returns {account: [campaign names tagged]}."""
    out = {}
    for cid in _auto():
        try:
            camps = read(g, cid, full=False)["campaigns"]
        except cx.ConnectorError:
            continue
        for c in camps:
            if _param(c["params"]):
                continue
            try:
                _set_params(g, cid, c["rn"], _with_tag(c["params"], slug(c["name"])))
                out.setdefault(cid, []).append(c["name"])
            except cx.ConnectorError:
                continue
    if out:
        log = _log()
        log.insert(0, {"at": db.now(), "tagged": out})
        db.set_setting("tracking_log", json.dumps(log[:20]))
    return out


def _log():
    try:
        return json.loads(db.get_setting("tracking_log") or "[]")
    except ValueError:
        return []


def status():
    """For the dashboard: accounts with the fix on, and the last automatic tagging."""
    auto = _auto()
    log = _log()
    return {"on": sorted(auto), "last": log[0] if log else None}
