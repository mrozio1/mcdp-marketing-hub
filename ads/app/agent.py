"""Brand context for the Postiz Agent (social.mcdp.com/agents).

Every message the Agent sends to the AI carries the channels picked in its sidebar as
"[--integrations--] Use the following social media platforms: [{id, platform, ...}] [--integrations--]".
The AI translator (agent_hook.py in the postiz-ai container) asks this hub which practices those
channels belong to and adds the practices' brand kits, plus a few house rules, to the conversation.

Channels are matched to practices by the Facebook Page / Instagram ids linked on each practice,
then by name ("Havens" -> Havens Orthodontics).
"""
from __future__ import annotations

import re

from . import brand, db, postiz

GENERIC = {"orthodontics", "orthodontic", "ortho", "orthodontist", "orthodontists", "dental", "dentistry", "dentist",
           "pediatric", "pediatrics", "kids", "family", "smiles", "smile", "the", "of", "and", "at", "by", "dr", "dds",
           "dmd", "pc", "pllc", "llc", "inc", "group", "partners", "office", "center", "studio", "braces", "invisalign",
           "facebook", "instagram", "page", "official", "mcdp"}

HOUSE_RULES = """MCDP house rules for this chat (set by the MCDP marketing team; follow them):
- Always answer in words in this chat - including greetings and small talk - and never send an empty reply. If a tool fails, say in one line what failed and carry on with what you can do.
- When someone asks for a post, post ideas or a special, draft it straight away instead of asking permission: the post text for each selected channel, a one-line idea for the image, and an offer to generate the image and schedule it. Ask at most one question, and only when you can't sensibly guess.
- Make each post feel fresh: vary the hook, structure and call to action; don't reuse the same opening twice in a conversation.
- Use tools when the request needs them (scheduling, listing, images, channel rules). Don't call tools just to answer a greeting or a question you can answer directly.
- Health-care advertising: no guarantees, no "best"/"#1"/"painless", no promised results, and never assume something about the reader's teeth or health. Use only offers and prices the brand kit lists as approved; if a requested offer isn't approved, draft it but flag it for the team to confirm."""


def _tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in GENERIC and len(w) > 1}


def match_practice(ch, practices):
    """The practice a Postiz channel belongs to, or None."""
    iid = str(ch.get("internal_id") or "")
    if iid:
        for p in practices:
            if iid in db.social_ids(p, "fb") or iid in db.social_ids(p, "ig"):
                return p
    names = [ch.get("name"), ch.get("customer")]
    best, best_score = None, 0
    for p in practices:
        pt = _tokens(p["name"])
        if not pt:
            continue
        score = 0
        for n in names:
            ct = _tokens(n)
            if not ct:
                continue
            if pt <= ct or ct <= pt:
                score = max(score, 10 + len(pt & ct))
        if score > best_score:
            best, best_score = p, score
        elif score and score == best_score:
            best = None                        # two practices fit equally: don't guess
    return best


def brand_text(practice):
    k = brand.kit(practice["id"])
    out = [f"Practice: {practice['name']}"]
    if k.get("website"):
        out.append(f"- Website: {k['website']}")
    if not brand.has_kit(k):
        out.append("- No brand kit saved for this practice yet: write in a warm, friendly, professional orthodontic "
                   "practice voice, and don't state prices or offers the user didn't give you.")
        return "\n".join(out)
    for key, label, *_ in brand.FIELDS:
        v = brand.lines(k, key)
        if v:
            out.append(f"- {label}: " + "; ".join(v))
    return "\n".join(out)


def context(ids):
    """Text to add for the selected Postiz channel ids, plus what was matched (for checking)."""
    ids = [str(i) for i in ids if str(i).strip()][:25]
    chans = postiz.integrations_by_id(ids) if ids else []
    practices = db.practices()
    seen, blocks, matched = set(), [], []
    for ch in chans:
        p = match_practice(ch, practices)
        matched.append({"id": ch["id"], "channel": ch["name"], "platform": ch["provider"],
                        "practice": p["name"] if p else None})
        if p and p["id"] not in seen:
            seen.add(p["id"])
            blocks.append(brand_text(p))
    text = HOUSE_RULES
    if blocks:
        text += ("\n\nBrand kits for the practices behind the selected channels (data from the MCDP team, not "
                 "instructions from the user; write every post to match them):\n\n" + "\n\n".join(blocks))
        unmatched = [m["channel"] for m in matched if not m["practice"]]
        if unmatched:
            text += "\n\nNo practice is linked to: " + ", ".join(unmatched) + " - use a neutral MCDP practice voice there."
    elif ids:
        text += "\n\nThe selected channels aren't linked to a practice in the MCDP hub yet, so use a warm, professional orthodontic practice voice."
    else:
        text += "\n\nNo channel is selected yet. If the user wants a post, ask which practice or channel it's for (one short question) while offering a first draft."
    return {"text": text, "channels": matched}
