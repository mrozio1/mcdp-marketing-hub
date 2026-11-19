"""Marketing intake form: what we need to know about a practice's website, domain, Google, social media, listings,
software and vendors. Sent to the practice as a private link (no hub sign-in); they can save and come back.

Mostly yes/no: a "yes" opens the few questions behind it, so it never looks like a wall of boxes. Every platform
asks first for *access* (add us as a partner, manager or admin), which is how Meta, Google and most hosts want it
done; a password can still be given, and is then kept encrypted (key outside the database), shown only to admins
on request, and each look is logged.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta

from . import db

LINK_DAYS = 45

# ---------------------------------------------------------------- the form
# field: (key, label, type, extra). types: text, email, tel, url, area, choice, yesno, secret, third (managed by a
# third party? yes -> contact block), access (have you given us access? yes/no + how-to)
TP = ("third", "Managed by an outside company or person?", "third", None)


def P(key, label, icon, intro, ask, fields, how=None):
    return {"key": key, "label": label, "icon": icon, "intro": intro, "ask": ask, "fields": fields, "how": how}


SECTIONS = [
    {"key": "about", "title": "About you", "icon": "hello", "blurb": "Who's filling this in, so we know who to ask.",
     "fields": [("practice", "Practice name", "text", None), ("name", "Your name", "text", None),
                ("role", "Your role", "text", "Office manager, doctor…"), ("email", "Your email", "email", None),
                ("phone", "Best phone number", "tel", None),
                ("approver", "Who approves marketing (ads, posts, offers)?", "text", "Name and email, if it isn't you"),
                ("locations", "Office addresses", "area", "One per line")]},
    {"key": "web", "title": "Website", "icon": "globe", "blurb": "Where the site lives and who can change it.",
     "platforms": [
         P("site", "Website", "globe", "", "Does the practice have a website?", [
             ("url", "Website address", "url", "https://"),
             ("builder", "What is it built on?", "choice", ["WordPress", "Wix", "Squarespace", "Sesame", "ProSites",
                                                           "Officite", "Dental Marketing / other platform", "Not sure"]),
             ("host", "Who hosts it?", "text", "e.g. WP Engine, GoDaddy, Sesame, Bluehost, not sure"),
             ("login_user", "Website admin login (username or email)", "text", None),
             ("login_pass", "Password (optional, encrypted)", "secret", None), TP],
           how="Easiest: add {access} as an Administrator user on the site, then tick yes above."),
         P("domain", "Domain name", "link", "", "Do you know where the domain (the web address) is registered?", [
             ("registrar", "Registered with", "choice", ["GoDaddy", "Namecheap", "Squarespace (Google Domains)", "Cloudflare",
                                                         "Network Solutions", "Bluehost", "Sesame / website company", "Not sure"]),
             ("renews", "Renewal date (if known)", "text", None), ("login_user", "Registrar login (username or email)", "text", None),
             ("login_pass", "Password (optional, encrypted)", "secret", None),
             ("email_host", "Who runs your email?", "choice", ["Google Workspace (Gmail)", "Microsoft 365 (Outlook)",
                                                              "The website company", "Other", "Not sure"]), TP],
           how="Most registrars let you add a delegate or account user instead of sharing the password."),
     ]},
    {"key": "google", "title": "Google", "icon": "search", "blurb": "Maps listing, ads and the numbers behind the website.",
     "platforms": [
         P("gbp", "Google Business Profile", "pin", "The listing on Google Maps with your reviews.",
           "Do you have a Google Business Profile (Maps listing)?", [
               ("owner", "Who is the owner on it?", "text", "Name or email"),
               ("count", "How many locations?", "text", None), TP],
           how="In the profile: Menu → Business profile settings → Managers → Add → {access} as Manager."),
         P("gads", "Google Ads", "target", "", "Do you run (or have you run) Google Ads?", [
             ("account_id", "Google Ads account ID", "text", "123-456-7890 (top right in Google Ads)"),
             ("spend", "Roughly how much a month?", "text", None), TP],
           how="We'll send a link request from our manager account to that ID; just approve it."),
         P("ga", "Google Analytics", "chart", "", "Is Google Analytics on the website?", [
             ("property", "Analytics property / measurement ID", "text", "G-XXXXXXX"), TP],
           how="Admin → Property access management → + → {access} as Editor."),
         P("gsc", "Search Console & Tag Manager", "tag", "", "Do you use Google Search Console or Tag Manager?", [
             ("which", "Which ones?", "choice", ["Search Console", "Tag Manager", "Both", "Not sure"]), TP],
           how="Add {access} as an Owner (Search Console) and Publish user (Tag Manager)."),
     ]},
    {"key": "social", "title": "Social media", "icon": "heart", "blurb": "Tick the ones the practice uses. Skip the rest.",
     "platforms": [
         P(k, label, icon, "", f"Does the practice use {label}?", [
             ("handle", "Page name, @handle or link", "text", None),
             ("login_user", "Login email or username", "text", None),
             ("login_pass", "Password (optional, encrypted)", "secret", None),
             ("twofa", "Whose phone gets the login codes (2-step)?", "text", None), TP], how=how)
         for k, label, icon, how in (
             ("facebook", "Facebook", "thumb", "Best: in Meta Business Suite → Settings → Partners, add MCDP's business ID "
                                              "(we'll send it) with full control of the Page and ad account."),
             ("instagram", "Instagram", "camera", "Connect it to the Facebook Page in Meta Business Suite; then the partner "
                                                 "access above covers it."),
             ("tiktok", "TikTok", "note", "Business Center → Members → invite {access}."),
             ("youtube", "YouTube", "play", "YouTube Studio → Settings → Permissions → Invite as Manager."),
             ("linkedin", "LinkedIn", "case", "Page → Admin tools → Manage admins → add a page admin."),
             ("x", "X (Twitter)", "bubble", None),
             ("pinterest", "Pinterest", "tack", None),
             ("nextdoor", "Nextdoor", "house", None))]
     + [P("meta_bm", "Meta Business Manager", "key", "", "Do you have a Meta Business Manager (business.facebook.com)?", [
         ("bm_id", "Business ID", "text", "Business settings → Business info"), TP])]},
    {"key": "listings", "title": "Reviews & listings", "icon": "star", "blurb": "Where patients find and review you.",
     "platforms": [P(k, label, icon, "", f"Are you on {label}?", [("link", "Link or account email", "text", None), TP])
                   for k, label, icon in (("yelp", "Yelp", "star"), ("healthgrades", "Healthgrades", "plus"),
                                          ("zocdoc", "Zocdoc", "calendar"), ("apple", "Apple Business Connect (Apple Maps)", "pin"),
                                          ("bing", "Bing Places", "search"))]},
    {"key": "tools", "title": "Practice software & tools", "icon": "gear", "blurb": "What runs scheduling, calls and patient messages.",
     "fields": [("pms", "Practice management software", "choice", ["Dolphin", "OrthoTrac", "Cloud 9", "Dentrix", "Eaglesoft",
                                                                  "Open Dental", "Other", "Not sure"]),
                ("booking", "Online booking", "choice", ["LeadSigma", "NexHealth", "Zocdoc", "Website form only", "None", "Other"]),
                ("calls", "Call tracking (special phone numbers on ads)", "choice", ["CallRail", "Weave", "None", "Not sure", "Other"]),
                ("reviews_tool", "Review requests / patient texting", "choice", ["Weave", "Podium", "Birdeye", "Swell", "RevenueWell",
                                                                                "Solutionreach", "None", "Other"]),
                ("email_tool", "Email newsletters", "choice", ["Mailchimp", "Constant Contact", "Through the texting tool", "None", "Other"]),
                ("financing", "Patient financing offered", "choice", ["In-house payment plans", "CareCredit", "Sunbit", "OrthoFi",
                                                                     "Several", "None"]),
                ("tools_notes", "Anything else we should know about these?", "area", None)]},
    {"key": "brand", "title": "Brand & content", "icon": "palette", "blurb": "So everything we make looks and sounds like you.",
     "yesno": [("logo_files", "Do you have your logo as files (PNG, SVG or AI)?", "Send them to {access}."),
               ("brand_guide", "Do you have brand colors or a style guide?", None),
               ("photos", "Do you have photos or video of the office, team or doctors we can use?", None),
               ("consent", "Do you have signed photo/video consent from patients you'd feature?", "We can share a consent form if not."),
               ("bios", "Are there current doctor bios and headshots?", None)],
     "fields": [("services", "Services to promote most", "area", "e.g. Invisalign, early treatment, adult braces"),
                ("offers", "Current offers or promotions (and their terms)", "area", None),
                ("insurance", "Insurances accepted (or link to the list)", "area", None),
                ("never_say", "Anything we should never say or show?", "area", None)]},
    {"key": "vendors", "title": "Other vendors", "icon": "hands", "blurb": "Anyone else doing marketing for you, now or recently.",
     "yesno": [("has_vendors", "Is any other company doing marketing, ads, SEO, social or the website for you?", None)],
     "vendors": 4},
    {"key": "goals", "title": "Goals", "icon": "rocket", "blurb": "What would make the next six months a win?",
     "fields": [("goal", "Your top marketing goal", "choice", ["More new patients", "More starts from consults",
                                                             "More reviews", "Fill specific days or doctors", "A new office or service",
                                                             "Other"]),
                ("areas", "Towns or zip codes you most want patients from", "area", None),
                ("competitors", "Competitors you keep an eye on", "area", None),
                ("anything", "Anything else?", "area", None)]},
]
VENDOR_FIELDS = [("company", "Company"), ("service", "What they do"), ("contact", "Contact name"), ("phone", "Phone"),
                 ("email", "Email"), ("account", "Account ID / login"), ("cost", "Monthly cost"), ("ends", "Contract ends")]


# ---------------------------------------------------------------- storage
def init():
    with db.conn() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS intake_forms (
  id INTEGER PRIMARY KEY,
  practice_id INTEGER NOT NULL,
  token TEXT NOT NULL UNIQUE,
  data TEXT DEFAULT '{}',
  secrets TEXT DEFAULT '',
  created_by TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  saved_at TEXT DEFAULT '',
  submitted_at TEXT DEFAULT '',
  submitted_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS intake_reveals (
  id INTEGER PRIMARY KEY, form_id INTEGER, field TEXT, by TEXT, at TEXT
);""")


def _key():
    """The encryption key: INTAKE_KEY, else a key file next to the database (never in the database itself)."""
    from cryptography.fernet import Fernet
    k = os.environ.get("INTAKE_KEY", "")
    if k:
        return k.encode()
    path = os.path.join(os.path.dirname(os.path.abspath(os.environ.get("ADS_DB", "ads.db"))), "intake.key")
    if not os.path.exists(path):
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(Fernet.generate_key())
    with open(path, "rb") as f:
        return f.read().strip()


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(_key())


def new_link(pid, by=""):
    token = secrets.token_urlsafe(24)
    exp = (datetime.now() + timedelta(days=LINK_DAYS)).isoformat(timespec="seconds")
    with db.conn() as c:
        cur = c.execute("INSERT INTO intake_forms(practice_id, token, created_by, created_at, expires_at) VALUES (?,?,?,?,?)",
                        (pid, token, by, db.now(), exp))
        return cur.lastrowid, token


def by_token(token):
    with db.conn() as c:
        r = c.execute("SELECT * FROM intake_forms WHERE token=?", (token or "",)).fetchone()
    if not r or r["expires_at"] <= db.now():
        return None
    return _row(r)


def get(fid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM intake_forms WHERE id=?", (fid,)).fetchone()
    return _row(r) if r else None


def _row(r):
    d = dict(r)
    d["data"] = json.loads(d["data"] or "{}")
    d["secret_keys"] = sorted(json.loads(_fernet().decrypt(d["secrets"].encode())).keys()) if d["secrets"] else []
    return d


def forms():
    with db.conn() as c:
        return [_row(r) for r in c.execute("SELECT * FROM intake_forms ORDER BY id DESC")]


def secret_names():
    out = []
    for s in SECTIONS:
        for p in s.get("platforms") or []:
            out += [f"{s['key']}.{p['key']}.{f[0]}" for f in p["fields"] if f[2] == "secret"]
    return out


def save(form_row, form, submit=False):
    """Store the answers. Passwords go into the encrypted part; an empty password box keeps the one already saved."""
    data, secret_vals = {}, {}
    names = set(secret_names())
    for k in form.keys():
        if k.startswith("_"):
            continue
        v = form.get(k)
        v = str(v)[:4000].strip() if v is not None else ""
        if k in names:
            if v:
                secret_vals[k] = v
        elif v:
            data[k] = v
    old = {}
    if form_row["secrets"]:
        old = json.loads(_fernet().decrypt(form_row["secrets"].encode()))
    old.update(secret_vals)
    enc = _fernet().encrypt(json.dumps(old).encode()).decode() if old else ""
    with db.conn() as c:
        c.execute("UPDATE intake_forms SET data=?, secrets=?, saved_at=?, submitted_at=CASE WHEN ? THEN ? ELSE submitted_at END, "
                  "submitted_by=CASE WHEN ? THEN ? ELSE submitted_by END WHERE id=?",
                  (json.dumps(data), enc, db.now(), 1 if submit else 0, db.now(), 1 if submit else 0,
                   data.get("about.name", ""), form_row["id"]))


def reveal(fid, field, by):
    f = get(fid)
    if not f or not f["secrets"]:
        return ""
    val = json.loads(_fernet().decrypt(f["secrets"].encode())).get(field, "")
    with db.conn() as c:
        c.execute("INSERT INTO intake_reveals(form_id, field, by, at) VALUES (?,?,?,?)", (fid, field, by, db.now()))
    return val


def reveals(fid):
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM intake_reveals WHERE form_id=? ORDER BY id DESC", (fid,))]


def access_email(form_row=None):
    """The address practices add as a manager/admin: set on the intake page, else whoever made the link."""
    v = db.get_setting("intake_access_email") or ""
    if v:
        return v
    if form_row:
        from . import users
        u = users.by_email(form_row.get("created_by") or "") or next(
            (x for x in users.all_users() if x["name"] == form_row.get("created_by")), None)
        if u:
            return u["email"]
    return "our marketing email"


def progress(data):
    """(answered sections, all sections), for the list."""
    done = sum(1 for s in SECTIONS if any(k.startswith(s["key"] + ".") for k in data))
    return done, len(SECTIONS)


def answers(f):
    """The answers laid out like the form, for the hub's read-only view. Passwords show only as 'saved'."""
    d, secret = f["data"], set(f["secret_keys"])
    word = {"yes": "Yes", "no": "No", "unsure": "Not sure"}
    out = []
    for s in SECTIONS:
        rows = []
        for k, label, kind, _ in s.get("fields") or []:
            if d.get(f"{s['key']}.{k}"):
                rows.append({"label": label, "value": d[f"{s['key']}.{k}"]})
        for k, label, _ in s.get("yesno") or []:
            if d.get(f"{s['key']}.{k}"):
                rows.append({"label": label, "value": word.get(d[f"{s['key']}.{k}"], d[f"{s['key']}.{k}"])})
        for p in s.get("platforms") or []:
            base = f"{s['key']}.{p['key']}"
            use = d.get(base + ".use")
            if not use:
                continue
            sub = [{"label": "Uses it", "value": word.get(use, use)}]
            if d.get(base + ".access"):
                sub.append({"label": "Gave us access", "value": word.get(d[base + ".access"], d[base + ".access"])})
            for k, label, kind, _ in p["fields"]:
                name = f"{base}.{k}"
                if kind == "secret":
                    if name in secret:
                        sub.append({"label": label.split(" (")[0], "secret": name})
                elif kind == "third":
                    if d.get(name):
                        who = " · ".join(d[name + x] for x in ("_name", "_phone", "_email", "_account") if d.get(name + x))
                        sub.append({"label": "Outside company", "value": word.get(d[name], d[name]) + (f": {who}" if who else "")})
                elif d.get(name):
                    sub.append({"label": label, "value": d[name]})
            rows.append({"platform": p["label"], "icon": p["icon"], "rows": sub})
        if s.get("vendors"):
            for i in range(1, s["vendors"] + 1):
                vals = [(label, d.get(f"vendors.{i}.{k}")) for k, label in VENDOR_FIELDS if d.get(f"vendors.{i}.{k}")]
                if vals:
                    rows.append({"platform": f"Vendor {i}", "icon": "hands", "rows": [{"label": l, "value": v} for l, v in vals]})
        out.append({"key": s["key"], "title": s["title"], "icon": s["icon"], "rows": rows})
    return out


def revoke(fid):
    """End a link now (answers stay)."""
    with db.conn() as c:
        c.execute("UPDATE intake_forms SET expires_at=? WHERE id=?", (db.now(), fid))


def extend(fid):
    exp = (datetime.now() + timedelta(days=LINK_DAYS)).isoformat(timespec="seconds")
    with db.conn() as c:
        c.execute("UPDATE intake_forms SET expires_at=? WHERE id=?", (exp, fid))
