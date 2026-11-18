"""Who markets where: every target zip code goes to exactly one brand and location, so two of our practices
never chase the same families in the same zip.

How a zip is assigned:
  1. Each office's candidates are its Target and Also-good zips (the top two-thirds by income within the radius;
     see zips.py), among offices in the same line of work: orthodontics, dentistry (general and pediatric) and
     oral surgery are kept apart, since they're different audiences.
  2. A zip that only one office wants is that office's.
  3. A zip that several offices want goes to the nearest one, then contested zips are moved, one at a time, from
     the office with the most households to the office with the fewest, when that evens them out and the zip
     isn't more than SPREAD_MILES farther from its new office. So overlapping offices end up with similar reach.
Offices of the same brand share out zips the same way (Havens Clarkston and Havens Clawson don't both get one).
Priority practices (set on the page) go first: a shared zip goes to a priority practice's location when it's no
more than SPREAD_MILES farther than the nearest, and it's never moved away when spreading the rest.
"""
from __future__ import annotations

import re

from . import db, field, places, zips

SPREAD_MILES = 5.0


def priority_ids():
    import json
    try:
        return {int(x) for x in json.loads(db.get_setting("zip_priority") or "[]")}
    except (ValueError, TypeError):
        return set()


def set_priority(ids):
    import json
    db.set_setting("zip_priority", json.dumps(sorted({int(i) for i in ids})))
    _cache.clear()
LINES = {"ortho": "Orthodontics", "dental": "Dentistry", "surgery": "Oral surgery"}


def line_of(practice):
    n = f"{practice.get('name') or ''} {practice.get('campaign_filter') or ''}"
    if re.search(r"orthodont|\bortho\b|ossfw", n, re.I):
        return "ortho"
    if re.search(r"\boms\b|oral (and |& )?maxillo|oral surgery", n, re.I):
        return "surgery"
    return "dental"


def _offices():
    """[(office, practice)] for our offices that are linked to a shown practice with ads (not the master record)."""
    from . import report
    plist = db.practices()
    by_id = {p["id"]: p for p in plist}
    links = field.practice_links()
    out = []
    for o in places.offices():
        p = by_id.get(links.get(o["id"]))
        if not p or o.get("lat") is None or report._is_master(p, plist):
            continue
        if not (p.get("google_customer_id") or p.get("meta_account_id") or p.get("campaign_filter")):
            continue
        out.append((o, p))
    return out


_cache = {}


def assign(miles=None):
    """Cached until the Census numbers, the radius, the offices or their practices change."""
    miles = miles or zips.miles_setting()
    key = (zips.status().get("at"), miles, tuple(sorted(priority_ids())), tuple(sorted(field.practice_links().items())),
           tuple((o["id"], o.get("lat"), o.get("lng")) for o in places.offices()),
           tuple((p["id"], p["name"], bool(p.get("google_customer_id")), bool(p.get("meta_account_id")))
                 for p in db.practices()))
    if _cache.get("key") != key:
        _cache.update(key=key, plan=_assign(miles))
    return _cache["plan"]


def _assign(miles):
    """{'offices': [{office, practice, line, zips: [...], shared: n}], 'practices': {pid: {...}}, 'contested': n}."""
    stats = zips._all()
    entries = []
    for o, p in _offices():
        ranked = zips.tiers(zips.near(o["lat"], o["lng"], miles, stats))
        cands = {z["zip"]: z for z in ranked if z["tier"] in ("target", "good")}
        entries.append({"office": o, "practice": p, "line": line_of(p), "cands": cands, "zips": {}})
    # who wants each zip, per line of work
    wants = {}
    for i, e in enumerate(entries):
        for zc in e["cands"]:
            wants.setdefault((e["line"], zc), []).append(i)
    owner, locked = {}, set()
    prio = priority_ids()
    for key, idx in wants.items():
        near_i = min(idx, key=lambda i: entries[i]["cands"][key[1]]["miles"])
        owner[key] = near_i
        pri = [i for i in idx if entries[i]["practice"]["id"] in prio and
               entries[i]["cands"][key[1]]["miles"] <= entries[near_i]["cands"][key[1]]["miles"] + SPREAD_MILES]
        if pri:                                    # a priority practice keeps it
            owner[key] = min(pri, key=lambda i: entries[i]["cands"][key[1]]["miles"])
            locked.add(key)
    load = [0] * len(entries)
    for key, i in owner.items():
        load[i] += entries[i]["cands"][key[1]].get("households") or 0
    contested = {k: v for k, v in wants.items() if len(v) > 1}
    n_contested = len(contested)
    contested = {k: v for k, v in contested.items() if k not in locked}
    # even out: move a contested zip from a fuller office to an emptier one while it narrows the gap
    for _ in range(4 * len(contested) + 10):
        best = None
        for key, idx in contested.items():
            a = owner[key]
            hh = entries[a]["cands"][key[1]].get("households") or 0
            for b in idx:
                if b == a:
                    continue
                if entries[b]["cands"][key[1]]["miles"] > entries[a]["cands"][key[1]]["miles"] + SPREAD_MILES:
                    continue
                gap_now = abs(load[a] - load[b])
                gap_after = abs((load[a] - hh) - (load[b] + hh))
                gain = gap_now - gap_after
                if gain > 0 and (best is None or gain > best[0]):
                    best = (gain, key, a, b, hh)
        if not best:
            break
        _, key, a, b, hh = best
        owner[key] = b
        load[a] -= hh
        load[b] += hh
    for key, i in owner.items():
        z = dict(entries[i]["cands"][key[1]])
        z["shared_with"] = sorted({entries[j]["practice"]["name"] if entries[j]["practice"]["id"] != entries[i]["practice"]["id"]
                                   else entries[j]["office"]["label"] for j in wants[key] if j != i})
        entries[i]["zips"][key[1]] = z
    offices, practices = [], {}
    for e in entries:
        zs = sorted(e["zips"].values(), key=lambda z: (z["tier"] != "target", -(z["income"] or 0)))
        row = {"office": e["office"], "practice": e["practice"], "line": e["line"], "zips": zs,
               "households": sum(z.get("households") or 0 for z in zs), "families": sum(z.get("hh_kids") or 0 for z in zs),
               "shared": sum(1 for z in zs if z["shared_with"]),
               "given_away": sum(1 for zc in e["cands"] if zc not in e["zips"])}
        offices.append(row)
        p = practices.setdefault(e["practice"]["id"], {"practice": e["practice"], "line": e["line"], "offices": [], "zips": []})
        p["offices"].append(row)
        p["zips"] += [z["zip"] for z in zs]
    for p in practices.values():
        p["zips"] = sorted(set(p["zips"]))
    offices.sort(key=lambda r: (r["practice"]["name"].lower(), r["office"]["label"].lower()))
    return {"offices": offices, "practices": practices, "contested": n_contested, "miles": miles,
            "priority": sorted(prio)}


def practice_zips(pid, plan=None):
    plan = plan or assign()
    return (plan["practices"].get(pid) or {}).get("zips", [])


def export_rows(plan):
    """For Excel: one row per brand, location and zip."""
    out = []
    for r in plan["offices"]:
        for z in r["zips"]:
            out.append([r["practice"]["name"], r["office"]["label"], LINES[r["line"]], z["zip"],
                        "Target" if z["tier"] == "target" else "Also good", z["miles"], z["income"], z["households"],
                        z["hh_kids"], ", ".join(z["shared_with"])])
    return out
