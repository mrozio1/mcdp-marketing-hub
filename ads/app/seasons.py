"""Seasonal look: a small cursor buddy, falling things and corner decorations, switched by an admin
(Automatic by date, one theme, or off). People can turn it off for themselves. The drawing is all in
static/seasonal.js; this module decides which theme shows today."""
import json
from datetime import date, timedelta

from . import db

# key, label, what it shows, default dates ("MM-DD", or "thanksgiving" = the fourth Thursday of November)
THEMES = [
    ("newyear", "New Year", "Party star buddy and confetti", "12-31", "01-06"),
    ("valentines", "Valentine's Day", "Heart buddy and hearts floating up", "02-07", "02-14"),
    ("stpatricks", "St. Patrick's Day", "Shamrock buddy and drifting clovers", "03-11", "03-17"),
    ("spring", "Spring", "Bunny buddy and cherry blossom petals", "03-20", "04-30"),
    ("summer", "Summer", "Sunny buddy and a few rising bubbles", "06-01", "08-31"),
    ("july4", "4th of July", "Star buddy, little fireworks and bunting", "06-29", "07-05"),
    ("school", "Back to School", "Smiling tooth with braces and falling pencils", "08-10", "09-05"),
    ("halloween", "Halloween", "Ghost buddy, spiderwebs and a dangling spider", "10-01", "10-31"),
    ("thanksgiving", "Thanksgiving", "Turkey buddy and a few falling leaves", "11-01", "thanksgiving"),
    ("christmas", "Christmas", "Snowman buddy, light snow and string lights", "12-01", "12-30"),
]
KEYS = [t[0] for t in THEMES]
ICON = {"newyear": "🎉", "valentines": "❤️", "stpatricks": "☘️", "spring": "🐰", "summer": "☀️", "july4": "🎆",
        "school": "🦷", "halloween": "👻", "thanksgiving": "🦃", "christmas": "⛄"}
LABEL = {t[0]: t[1] for t in THEMES}
# When dates overlap, the shorter, more specific one wins (4th of July over Summer, Back to School over Summer).
DEFAULTS = {"mode": "auto", "buddy": True, "falling": True, "corners": True, "amount": "light", "optout": True,
            "dates": {k: [s, e] for k, _, _, s, e in THEMES}, "auto": {k: True for k in KEYS}}


def thanksgiving(year):
    d = date(year, 11, 1)
    first_thu = d + timedelta(days=(3 - d.weekday()) % 7)
    return first_thu + timedelta(days=21)


def settings():
    try:
        s = json.loads(db.get_setting("seasonal") or "{}")
    except ValueError:
        s = {}
    out = {**DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS}}
    out["dates"] = {**DEFAULTS["dates"], **(s.get("dates") or {})}
    out["auto"] = {**DEFAULTS["auto"], **(s.get("auto") or {})}
    return out


def _md(v, year):
    if v == "thanksgiving":
        return thanksgiving(year)
    m, d = int(v[:2]), int(v[3:5])
    if m == 2 and d == 29:
        d = 28
    return date(year, m, d)


def window(key, today, dates):
    """The (start, end) of this theme's season that contains today, if any (handles Dec → Jan)."""
    s, e = dates.get(key) or DEFAULTS["dates"][key]
    for y in (today.year - 1, today.year):
        start = _md(s, y)
        end = _md(e, y)
        if end < start:
            end = _md(e, y + 1)
        if start <= today <= end:
            return start, end
    return None


def current(today=None, s=None):
    """(theme key, end date) showing today, or (None, None)."""
    today = today or date.today()
    s = s or settings()
    if s["mode"] == "off":
        return None, None
    if s["mode"] in KEYS:
        return s["mode"], None
    hits = []
    for k in KEYS:
        if s["auto"].get(k, True):
            w = window(k, today, s["dates"])
            if w:
                hits.append(((w[1] - w[0]).days, k, w[1]))
    if not hits:
        return None, None
    _, k, end = min(hits)
    return k, end


def for_page(user, preview=""):
    """What the page script needs, or None."""
    s = settings()
    if preview in KEYS:
        theme = preview
    else:
        if user and s["optout"] and not user.get("seasonal", 1):
            return None
        theme, _ = current(s=s)
    if not theme:
        return None
    return {"theme": theme, "buddy": s["buddy"], "falling": s["falling"], "corners": s["corners"], "amount": s["amount"]}


def valid_md(v):
    if v == "thanksgiving":
        return True
    try:
        _md(v, 2024)
        return len(v) == 5 and v[2] == "-"
    except (ValueError, IndexError):
        return False


def save(form):
    s = settings()
    mode = form.get("mode") or "auto"
    s["mode"] = mode if mode in ("auto", "off", *KEYS) else "auto"
    for k in ("buddy", "falling", "corners", "optout"):
        s[k] = form.get(k) == "1"
    s["amount"] = "normal" if form.get("amount") == "normal" else "light"
    bad = []
    for k in KEYS:
        st, en = (form.get(f"start_{k}") or "").strip(), (form.get(f"end_{k}") or "").strip()
        if st or en:
            if valid_md(st) and valid_md(en):
                s["dates"][k] = [st, en]
            else:
                bad.append(LABEL[k])
        s["auto"][k] = form.get(f"auto_{k}") == "1"
    db.set_setting("seasonal", json.dumps(s))
    return bad


def rows(today=None):
    """For the admin panel: each theme with its dates and whether it's on now."""
    today = today or date.today()
    s = settings()
    now, until = current(today, s)
    out = []
    for k, label, blurb, _, _ in THEMES:
        st, en = s["dates"][k]
        nxt = window(k, today, s["dates"])
        if not nxt:
            for y in (today.year, today.year + 1):
                a, b = _md(st, y), _md(en, y)
                if b < a:
                    b = _md(en, y + 1)
                if a > today:
                    nxt = (a, b)
                    break
        out.append({"key": k, "label": label, "blurb": blurb, "start": st, "end": en, "auto": s["auto"].get(k, True),
                    "now": k == now, "next": nxt})
    return out, s, now, until


def choices():
    return ([("auto", "Automatic", "Follows the dates below. Recommended.", "🗓️")]
            + [(k, label, blurb, ICON[k]) for k, label, blurb, _, _ in THEMES]
            + [("off", "Off", "Plain hub all year", "⊘")])
