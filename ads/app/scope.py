"""Which campaigns belong to which practice, when several practices share one ad account.

A practice's filter is a comma-separated list of words matched against campaign names
(case-insensitive, "contains"). A word starting with "-" excludes campaigns containing it.
Campaigns whose names don't fit a pattern can also be added to a practice by exact name.
An added campaign belongs only to the practice(s) it was added to, even if another practice's
words would match it. A practice with neither sees every campaign: that is the master record.

Meta campaigns are also assigned by what they promote (see sync.build_owners): a boosted post or reel
belongs to the practice whose Facebook Page / Instagram account it came from, whatever it's called.
Other campaigns promoting a practice's Page fill in only when no practice's words match them. Moving a
campaign by hand (adding it to a practice) always wins.
"""
from __future__ import annotations

import json
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


def norm(name):
    return " ".join((name or "").split()).lower()


def added_campaigns(practice):
    """Campaign names added to this practice by hand (original spelling), in the order added."""
    raw = (practice or {}).get("campaign_list") or "[]"
    try:
        names = json.loads(raw) if isinstance(raw, str) else list(raw)
    except ValueError:
        return []
    return [n for n in names if isinstance(n, str) and n.strip()]


def has_filter(practice):
    inc, exc = parse_filter((practice or {}).get("campaign_filter"))
    return bool(inc or exc or added_campaigns(practice))


def claims_from(practices):
    """{normalized campaign name: {practice ids it was added to by hand}}"""
    out = {}
    for q in practices:
        for n in added_campaigns(q):
            out.setdefault(norm(n), set()).add(q["id"])
    return out


def _db_claims():
    from . import db
    return claims_from(db.practices())


def _db_owners():
    from . import db
    try:
        return json.loads(db.get_setting("campaign_owners") or "{}").get("meta", {})
    except ValueError:
        return {}


def boosted_names(owners=None):
    """Normalized names of Meta campaigns that boost an existing post or reel."""
    owners = _db_owners() if owners is None else owners
    return {k for k, e in owners.items() if e.get("boost")}


def _words_match(name, practice):
    inc, exc = parse_filter(practice.get("campaign_filter"))
    if not inc and not exc:
        return False
    return not any(e in name for e in exc) and (not inc or any(i in name for i in inc))


def matches(campaign, practice_or_filter, claims=None, owners=None, practices=None, platform=None):
    if not isinstance(practice_or_filter, dict):
        inc, exc = parse_filter(practice_or_filter)
        name = norm(campaign)
        return not any(e in name for e in exc) and (not inc or any(i in name for i in inc))
    p = practice_or_filter
    pid = p.get("id")
    name = norm(campaign)
    if pid is not None:
        if claims is None:
            claims = _db_claims()
        if owners is None:
            owners = _db_owners()
        if practices is None:
            from . import db
            practices = db.practices()
    own = (owners or {}).get(name) or {}
    boost_to = set(own.get("practices", [])) if own.get("boost") else set()
    if not has_filter(p):
        # Master / single-practice record: everything in its rows, except a Meta campaign that belongs
        # (moved by hand, or a boost of its posts) to a practice on a different ad account.
        if platform == "meta" and pid is not None:
            belongs = set((claims or {}).get(name, ())) or boost_to
            if belongs and pid not in belongs:
                mine = str(p.get("meta_account_id") or "")
                if not any(str(q.get("meta_account_id") or "") == mine and mine
                           for q in practices or [] if q["id"] in belongs):
                    return False
        return True
    if name in {norm(n) for n in added_campaigns(p)}:
        return True
    if claims and name in claims and pid not in claims[name]:
        return False                                  # moved by hand to another practice
    if boost_to:
        return pid in boost_to                        # a boost belongs to the practice whose post it is
    if _words_match(name, p):
        return True
    fallback = set(own.get("practices", []))
    if pid in fallback and not any(_words_match(name, q) for q in practices or [] if q["id"] != pid):
        return True                                   # promotes this practice's Page; no one's words claim it
    return False


def apply(rows, practice, claims=None, owners=None, platform=None):
    if not has_filter(practice) and platform != "meta":
        return rows
    practices = None
    if isinstance(practice, dict) and practice.get("id") is not None:
        from . import db
        practices = db.practices()
        claims = claims_from(practices) if claims is None else claims
        owners = _db_owners() if owners is None else owners
    cache = {}
    out = []
    for r in rows:
        c = r.get("campaign")
        if c not in cache:
            cache[c] = matches(c, practice, claims, owners, practices, platform)
        if cache[c]:
            out.append(r)
    return out


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
    claims = claims_from(practices)
    promo = _db_owners() if platform == "meta" else {}
    owners = {c["name"]: [q for q in others if matches(c["name"], q, claims, promo, practices, platform)] for c in camps}
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
