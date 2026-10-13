"""Which campaigns belong to which practice, when several practices share one ad account.

A practice's filter is a comma-separated list of words matched against campaign names
(case-insensitive, "contains"). A word starting with "-" excludes campaigns containing it.
A practice with no filter sees every campaign: that is the master record.
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict

from .analyze import _agg

SPLIT = re.compile(r"[,\n;]+")
GROUP_SEP = re.compile(r"\s+[-–—|:/]\s+|\s*[|:]\s*")


def parse_filter(text):
    inc, exc = [], []
    for t in SPLIT.split(text or ""):
        t = t.strip().lower()
        if t.startswith("-") and t[1:].strip():
            exc.append(t[1:].strip())
        elif t:
            inc.append(t)
    return inc, exc


def has_filter(practice):
    inc, exc = parse_filter((practice or {}).get("campaign_filter"))
    return bool(inc or exc)


def matches(campaign, practice_or_filter):
    f = practice_or_filter.get("campaign_filter") if isinstance(practice_or_filter, dict) else practice_or_filter
    inc, exc = parse_filter(f)
    name = (campaign or "").lower()
    if any(e in name for e in exc):
        return False
    return not inc or any(i in name for i in inc)


def apply(rows, practice):
    if not has_filter(practice):
        return rows
    return [r for r in rows if matches(r.get("campaign"), practice)]


def group_key(campaign):
    """Best guess at the practice part of a campaign name: 'Heights - Invisalign' -> 'Heights'."""
    name = (campaign or "").strip()
    head = GROUP_SEP.split(name, maxsplit=1)[0].strip()
    return head or name


def _words(name):
    return re.findall(r"[\w'&]+", (name or "").lower())


def suggest_practices(camps):
    """Group campaigns that look like the same practice: same first word of the name
    ('Havens Orthodontics' + 'Havens - Braces Promo'). The filter is the words they all start
    with; the suggested name is the practice-looking part of the biggest campaign's name."""
    groups = OrderedDict()
    for c in sorted(camps, key=lambda c: -c["spend"]):
        w = _words(c["name"])
        groups.setdefault(w[0] if w else c["name"].lower(), []).append(c)
    out = []
    for cs in groups.values():
        top = cs[0]["name"]
        names = [c["name"].lower() for c in cs]
        if len(cs) == 1:
            prefix = group_key(top).lower()
        else:
            prefix = os.path.commonprefix(names)
            # Cut back to a whole word in every name ('havens o' -> 'havens').
            if any(len(n) > len(prefix) and n[len(prefix)].isalnum() for n in names) and prefix[-1:].isalnum():
                prefix = prefix[:prefix.rfind(" ") + 1] if " " in prefix else ""
        prefix = prefix.strip(" -–—|:/").strip()
        flt = prefix or (_words(top)[0] if _words(top) else top.lower())
        out.append({"name": group_key(top), "filter": _cased(top, flt), "campaigns": cs, **_sum(cs)})
    return sorted(out, key=lambda s: -s["spend"])


def _cased(original, lowered):
    i = original.lower().find(lowered)
    return original[i:i + len(lowered)] if i >= 0 else lowered


def _sum(camps):
    spend = round(sum(c["spend"] for c in camps), 2)
    conv = round(sum(c["conversions"] for c in camps), 2)
    return {"spend": spend, "conversions": conv, "cpa": spend / conv if conv else None, "count": len(camps)}


def breakdown(master, platform, rows, practices):
    """For a master record: spend per practice, campaigns matched by more than one practice,
    and campaigns no practice claims yet (grouped into suggested new practices)."""
    camps = _agg(rows, "campaign")
    acct = master.get(f"{'google_customer_id' if platform == 'google' else 'meta_account_id'}")
    others = []
    for q in practices:
        if q["id"] == master["id"] or not has_filter(q):
            continue
        q_acct = q.get("google_customer_id" if platform == "google" else "meta_account_id")
        if acct and q_acct and q_acct != acct:
            continue  # linked to a different account; its campaigns aren't in these rows
        others.append(q)
    if not others and not camps:
        return None
    owners = {c["name"]: [q for q in others if matches(c["name"], q)] for c in camps}
    by_practice = []
    for q in others:
        mine = [c for c in camps if q in owners[c["name"]]]
        by_practice.append({"practice": q, **_sum(mine)})
    by_practice.sort(key=lambda x: -x["spend"])
    overlap = [c["name"] for c in camps if len(owners[c["name"]]) > 1]
    unassigned = [c for c in camps if not owners[c["name"]]]
    suggestions = suggest_practices(unassigned)
    return {"by_practice": by_practice, "overlap": overlap, "unassigned": _sum(unassigned),
            "suggestions": suggestions, "total": _sum(camps)}
