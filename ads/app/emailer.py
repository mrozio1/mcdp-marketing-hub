"""Email campaigns sent as each practice (Studio › Email campaigns).

Sending goes through Azure Communication Services (ACS) Email, not Exchange Online: Microsoft says Exchange Online
isn't for bulk mail and can restrict the whole tenant's mail when it sees it. Each practice gets one sending address
on its own verified domain (set up in Azure, DNS in Cloudflare); replies go to the practice's Microsoft 365 mailbox.

Every recipient gets their own message (never CC/BCC), with their own unsubscribe link. Wrong-practice safeguards:
a campaign's practice is fixed when it's made; the From address, lists, brand, images and footer all come from that
practice; a content check blocks other practices' names, websites, emails and phones and links off the practice's
domains; sending needs the practice's name typed in; and it starts 10 minutes later so it can be cancelled.
Counts and addresses only: no patient details beyond email and name.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import html
import io
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.utils import formatdate

from . import brand, db

API = "2023-03-31"
CANCEL_MINUTES = 10
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
OK_LINK_DOMAINS = ("facebook.com", "instagram.com", "youtube.com", "youtu.be", "tiktok.com", "linkedin.com", "google.com",
                   "g.page", "goo.gl", "maps.app.goo.gl", "yelp.com", "x.com", "twitter.com", "pinterest.com")
GENERIC = {"orthodontics", "orthodontic", "ortho", "dental", "dentistry", "pediatric", "of", "the", "and", "family",
           "oral", "surgery", "surgeons", "maxillofacial", "smile", "smiles", "specialists", "dr", "by", "group", "care"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS em_senders (
  practice_id INTEGER PRIMARY KEY,
  from_addr TEXT NOT NULL,
  from_name TEXT DEFAULT '',
  reply_to TEXT DEFAULT '',
  address TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  extra_domains TEXT DEFAULT '',
  daily_cap INTEGER DEFAULT 300,
  dns TEXT DEFAULT '{}',
  checked_at TEXT,
  tested_at TEXT,
  ready INTEGER DEFAULT 0,
  updated_at TEXT, updated_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS em_lists (
  id INTEGER PRIMARY KEY, practice_id INTEGER NOT NULL, name TEXT NOT NULL, created_at TEXT, created_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS em_contacts (
  id INTEGER PRIMARY KEY, list_id INTEGER NOT NULL REFERENCES em_lists(id) ON DELETE CASCADE,
  email TEXT NOT NULL, first_name TEXT DEFAULT '', last_name TEXT DEFAULT '', org TEXT DEFAULT '', tags TEXT DEFAULT '',
  added_at TEXT, source TEXT DEFAULT '', UNIQUE(list_id, email)
);
CREATE TABLE IF NOT EXISTS em_suppress (
  practice_id INTEGER NOT NULL, email TEXT NOT NULL, reason TEXT DEFAULT '', at TEXT, PRIMARY KEY (practice_id, email)
);
CREATE TABLE IF NOT EXISTS em_campaigns (
  id INTEGER PRIMARY KEY, practice_id INTEGER NOT NULL, name TEXT NOT NULL, subject TEXT DEFAULT '', preview TEXT DEFAULT '',
  blocks TEXT DEFAULT '[]', list_ids TEXT DEFAULT '[]', status TEXT DEFAULT 'draft', send_at TEXT, key TEXT,
  base_url TEXT DEFAULT '', links TEXT DEFAULT '[]', created_by TEXT DEFAULT '', created_at TEXT, updated_at TEXT,
  queued_by TEXT DEFAULT '', queued_at TEXT, sent_at TEXT, error TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS em_messages (
  id INTEGER PRIMARY KEY, campaign_id INTEGER NOT NULL, email TEXT NOT NULL, first_name TEXT DEFAULT '',
  token TEXT UNIQUE, status TEXT DEFAULT 'queued', acs_id TEXT, error TEXT DEFAULT '', sent_at TEXT,
  delivered TEXT, opened_at TEXT, clicked_at TEXT, UNIQUE(campaign_id, email)
);
CREATE INDEX IF NOT EXISTS em_messages_status ON em_messages(status, sent_at);
CREATE INDEX IF NOT EXISTS em_messages_acs ON em_messages(acs_id);
CREATE TABLE IF NOT EXISTS em_clicks (message_id INTEGER, link INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS em_templates (
  id INTEGER PRIMARY KEY, practice_id INTEGER, name TEXT NOT NULL, blocks TEXT NOT NULL, created_by TEXT DEFAULT '', created_at TEXT
);
"""


class EmailError(ValueError):
    pass


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


# ------------------------------------------------------------------ ACS connection (admin, Connections page)
def _conn_string():
    s = db.get_setting("acs_connection") or ""
    m = dict(re.findall(r"(endpoint|accesskey)=([^;]+)", s, re.I))
    m = {k.lower(): v for k, v in m.items()}
    return (m.get("endpoint", "").rstrip("/"), m.get("accesskey", ""))


def configured():
    e, k = _conn_string()
    return bool(e and k)


def save_connection(value):
    value = (value or "").strip()
    if value:
        if not re.search(r"endpoint=https://[^;]+;accesskey=[^;]+", value, re.I):
            raise EmailError("Paste the whole connection string from Azure: it starts with endpoint=https://… and has accesskey=…")
        db.set_setting("acs_connection", value)
    return configured()


def rates():
    return {"minute": int(db.get_setting("acs_per_minute") or 30), "hour": int(db.get_setting("acs_per_hour") or 100)}


def set_rates(minute, hour):
    db.set_setting("acs_per_minute", str(max(1, min(int(minute), 10000))))
    db.set_setting("acs_per_hour", str(max(1, min(int(hour), 100000))))


def _sign(method, url, body):
    endpoint, key = _conn_string()
    u = urllib.parse.urlparse(url)
    date = formatdate(usegmt=True)
    chash = base64.b64encode(hashlib.sha256(body).digest()).decode()
    to_sign = f"{method}\n{u.path}?{u.query}\n{date};{u.netloc};{chash}"
    sig = base64.b64encode(hmac.new(base64.b64decode(key), to_sign.encode(), hashlib.sha256).digest()).decode()
    return {"x-ms-date": date, "x-ms-content-sha256": chash, "Content-Type": "application/json",
            "Authorization": f"HMAC-SHA256 SignedHeaders=x-ms-date;host;x-ms-content-sha256&Signature={sig}"}


class RateLimited(Exception):
    def __init__(self, wait):
        self.wait = wait


def acs_send(sender, to_addr, to_name, subject, html_body, text_body, unsub_url=None):
    """One message to one person. Returns ACS's operation id."""
    if not configured():
        raise EmailError("Email sending isn't connected yet (Connections › Email sending).")
    endpoint, _ = _conn_string()
    url = f"{endpoint}/emails:send?api-version={API}"
    msg = {"senderAddress": sender["from_addr"], "recipients": {"to": [{"address": to_addr, "displayName": to_name or ""}]},
           "content": {"subject": subject, "html": html_body, "plainText": text_body},
           "userEngagementTrackingDisabled": True}
    if sender.get("reply_to"):
        msg["replyTo"] = [{"address": sender["reply_to"], "displayName": sender.get("from_name") or ""}]
    if unsub_url:
        msg["headers"] = {"List-Unsubscribe": f"<{unsub_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    body = json.dumps(msg).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers=_sign("POST", url, body))
    req.add_header("Operation-Id", secrets.token_hex(16))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read() or b"{}")
            return data.get("id") or r.headers.get("Operation-Id") or ""
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        if e.code == 429:
            raise RateLimited(int(e.headers.get("Retry-After") or 60))
        try:
            m = json.loads(raw).get("error", {}).get("message") or raw
        except ValueError:
            m = raw
        raise EmailError(f"Azure refused the email ({e.code}): {m[:300]}")
    except urllib.error.URLError as e:
        raise EmailError(f"Couldn't reach Azure: {e.reason}")


# ------------------------------------------------------------------ sending addresses (one per practice)
def _domain(url_or_addr):
    s = (url_or_addr or "").strip().lower()
    if "@" in s:
        s = s.split("@", 1)[1]
    s = re.sub(r"^[a-z]+://", "", s).split("/")[0].split(":")[0]
    return s[4:] if s.startswith("www.") else s


def practice_domain(pid):
    kit = brand.kit(pid) or {}
    return _domain(kit.get("website") or "")


def sender(pid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM em_senders WHERE practice_id=?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["dns"] = json.loads(d["dns"] or "{}")
    d["domain"] = _domain(d["from_addr"])
    return d


def senders():
    with db.conn() as c:
        ids = [r[0] for r in c.execute("SELECT practice_id FROM em_senders")]
    return {pid: sender(pid) for pid in ids}


def save_sender(pid, form, by):
    addr = (form.get("from_addr") or "").strip().lower()
    reply = (form.get("reply_to") or "").strip().lower()
    if not EMAIL_RE.match(addr):
        raise EmailError("Type the sending address, e.g. news@havensorthodontics.com.")
    if reply and not EMAIL_RE.match(reply):
        raise EmailError("The reply-to address doesn't look like an email address.")
    site = practice_domain(pid)
    dom = _domain(addr)
    if site and not (dom == site or dom.endswith("." + site)):
        raise EmailError(f"The sending address has to be on the practice's own domain ({site}, from its brand kit's website). "
                         f"{dom} isn't.")
    for other, s in senders().items():
        if other != pid and s and s["domain"] == dom:
            raise EmailError(f"{dom} is already the sending domain of another practice. Each practice sends from its own domain.")
    if not (form.get("address") or "").strip():
        raise EmailError("Add the practice's mailing address: the law (CAN-SPAM) needs one in every marketing email.")
    try:
        cap = max(10, min(int(form.get("daily_cap") or 300), 50000))
    except ValueError:
        cap = 300
    old = sender(pid)
    changed = not old or old["from_addr"] != addr
    with db.conn() as c:
        c.execute("INSERT INTO em_senders(practice_id, from_addr, from_name, reply_to, address, phone, extra_domains, daily_cap, "
                  "updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(practice_id) DO UPDATE SET "
                  "from_addr=excluded.from_addr, from_name=excluded.from_name, reply_to=excluded.reply_to, address=excluded.address, "
                  "phone=excluded.phone, extra_domains=excluded.extra_domains, daily_cap=excluded.daily_cap, "
                  "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                  (pid, addr, (form.get("from_name") or "").strip()[:80], reply, (form.get("address") or "").strip()[:300],
                   (form.get("phone") or "").strip()[:40], " ".join(_domain(x) for x in re.split(r"[\s,]+", form.get("extra_domains") or "") if x)[:500],
                   cap, db.now(), by))
        if changed:       # a new address has to pass the checks and a test again
            c.execute("UPDATE em_senders SET ready=0, tested_at=NULL, dns='{}', checked_at=NULL WHERE practice_id=?", (pid,))


def remove_sender(pid):
    with db.conn() as c:
        c.execute("DELETE FROM em_senders WHERE practice_id=?", (pid,))


def _doh(name, rtype):
    """DNS over HTTPS (Cloudflare's public resolver): the record values, or [] if none."""
    url = "https://cloudflare-dns.com/dns-query?" + urllib.parse.urlencode({"name": name, "type": rtype})
    req = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    return [a.get("data", "").strip('"').replace('" "', "") for a in data.get("Answer") or []]


def check_dns(pid, lookup=None):
    """SPF, DKIM and DMARC for the sending domain, as Azure Communication Services needs them."""
    s = sender(pid)
    if not s:
        raise EmailError("Add the sending address first.")
    look = lookup or _doh
    d = s["domain"]
    out = {}
    try:
        spf = [t for t in look(d, "TXT") if t.lower().startswith("v=spf1")]
        out["spf"] = bool(spf and "include:spf.protection.outlook.com" in spf[0].lower())
        out["spf_note"] = spf[0] if spf else "no SPF record"
        dk = look(f"selector1-azurecomm-prod-net._domainkey.{d}", "CNAME")
        out["dkim"] = bool(dk)
        dm = [t for t in look(f"_dmarc.{d}", "TXT") if t.upper().startswith("V=DMARC1")]
        out["dmarc"] = bool(dm)
        out["dmarc_note"] = dm[0] if dm else "no DMARC record"
    except Exception as e:
        raise EmailError(f"Couldn't look up {d}'s DNS: {e}")
    with db.conn() as c:
        c.execute("UPDATE em_senders SET dns=?, checked_at=? WHERE practice_id=?", (json.dumps(out), db.now(), pid))
    _update_ready(pid)
    return out


def _update_ready(pid):
    s = sender(pid)
    ok = bool(s and all(s["dns"].get(k) for k in ("spf", "dkim", "dmarc")) and s["tested_at"])
    with db.conn() as c:
        c.execute("UPDATE em_senders SET ready=? WHERE practice_id=?", (1 if ok else 0, pid))
    return ok


def sender_test(pid, to_addr, practice_name):
    s = sender(pid)
    if not s:
        raise EmailError("Add the sending address first.")
    if not EMAIL_RE.match(to_addr or ""):
        raise EmailError("Which address should the test go to?")
    acs_send(s, to_addr, "", f"Test from {practice_name}",
             f"<p>This is a test from the MCDP Hub, sent as {html.escape(s['from_addr'])}. If it arrived in your inbox "
             f"(not spam), sending for {html.escape(practice_name)} works.</p>",
             f"This is a test from the MCDP Hub, sent as {s['from_addr']}.")
    with db.conn() as c:
        c.execute("UPDATE em_senders SET tested_at=? WHERE practice_id=?", (db.now(), pid))
    return _update_ready(pid)


def sent_today(pid):
    since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM em_messages m JOIN em_campaigns k ON k.id=m.campaign_id "
                         "WHERE k.practice_id=? AND m.sent_at >= ?", (pid, since)).fetchone()[0]


# ------------------------------------------------------------------ lists and contacts
def lists(pid):
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT l.*, (SELECT COUNT(*) FROM em_contacts x WHERE x.list_id=l.id AND lower(x.email) NOT IN "
            "(SELECT email FROM em_suppress WHERE practice_id=l.practice_id)) AS n, "
            "(SELECT COUNT(*) FROM em_contacts x WHERE x.list_id=l.id) AS total FROM em_lists l WHERE l.practice_id=? ORDER BY l.name", (pid,))]


def get_list(lid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM em_lists WHERE id=?", (lid,)).fetchone()
    return dict(r) if r else None


def new_list(pid, name, by):
    name = (name or "").strip()[:80]
    if not name:
        raise EmailError("Give the list a name, e.g. Referring dentists.")
    with db.conn() as c:
        return c.execute("INSERT INTO em_lists(practice_id, name, created_at, created_by) VALUES (?,?,?,?)",
                         (pid, name, db.now(), by)).lastrowid


def delete_list(lid):
    with db.conn() as c:
        c.execute("DELETE FROM em_contacts WHERE list_id=?", (lid,))
        c.execute("DELETE FROM em_lists WHERE id=?", (lid,))


def contacts(lid, q=""):
    l = get_list(lid)
    with db.conn() as c:
        sup = {r[0] for r in c.execute("SELECT email FROM em_suppress WHERE practice_id=?", (l["practice_id"],))} if l else set()
        rows = [dict(r) for r in c.execute("SELECT * FROM em_contacts WHERE list_id=? ORDER BY email", (lid,))]
    q = (q or "").lower().strip()
    if q:
        rows = [r for r in rows if q in " ".join([r["email"], r["first_name"], r["last_name"], r["org"]]).lower()]
    for r in rows:
        r["unsubscribed"] = r["email"].lower() in sup
    return rows


def add_contacts(lid, people, source=""):
    """people: [{email, first_name, last_name, org, tags}]. Returns (added, skipped)."""
    added = skipped = 0
    with db.conn() as c:
        for p in people:
            e = (p.get("email") or "").strip().lower()
            if not EMAIL_RE.match(e):
                skipped += 1
                continue
            cur = c.execute("INSERT OR IGNORE INTO em_contacts(list_id, email, first_name, last_name, org, tags, added_at, source) "
                            "VALUES (?,?,?,?,?,?,?,?)", (lid, e, (p.get("first_name") or "").strip()[:60],
                                                       (p.get("last_name") or "").strip()[:60], (p.get("org") or "").strip()[:120],
                                                       (p.get("tags") or "").strip()[:200], db.now(), source))
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
    return added, skipped


def remove_contact(lid, cid):
    with db.conn() as c:
        c.execute("DELETE FROM em_contacts WHERE id=? AND list_id=?", (cid, lid))


COLS = {"email": ("email", "e-mail", "email address", "e-mail address"), "first_name": ("first name", "first", "firstname", "given name"),
        "last_name": ("last name", "last", "lastname", "surname"), "name": ("name", "full name", "contact", "contact name"),
        "org": ("company", "practice", "office", "organization", "organisation", "account", "account name"), "tags": ("tags", "tag", "group")}


def parse_csv(data):
    text = data.decode("utf-8-sig", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise EmailError("That file is empty.")
    head = [h.strip().lower() for h in rows[0]]
    idx = {}
    for key, names in COLS.items():
        for i, h in enumerate(head):
            if h in names and key not in idx:
                idx[key] = i
    if "email" not in idx:
        hit = [i for i, v in enumerate(rows[1] if len(rows) > 1 else []) if EMAIL_RE.match(v.strip())]
        if not hit:
            raise EmailError("Couldn't find an email column. Name it \"Email\".")
        idx["email"] = hit[0]
    out = []
    for r in rows[1:]:
        g = lambda k: r[idx[k]].strip() if k in idx and idx[k] < len(r) else ""
        first, last = g("first_name"), g("last_name")
        if not first and g("name"):
            parts = g("name").split()
            first, last = parts[0], " ".join(parts[1:])
        out.append({"email": g("email"), "first_name": first, "last_name": last, "org": g("org"), "tags": g("tags")})
    return out


def field_people(pid):
    """People met on field visits for this practice who gave an email: referring offices' staff."""
    with db.conn() as c:
        try:
            rows = c.execute("SELECT p.name, p.email, o.name AS office FROM visit_people p JOIN visits v ON v.id=p.visit_id "
                             "JOIN field_offices o ON o.id=v.office_id WHERE v.practice_id=? AND p.email != ''", (pid,)).fetchall()
        except Exception:
            rows = []
    out = []
    for r in rows:
        parts = (r["name"] or "").split()
        out.append({"email": r["email"], "first_name": parts[0] if parts else "", "last_name": " ".join(parts[1:]),
                    "org": r["office"], "tags": "field visit"})
    return out


def unsubscribe(pid, email, reason="unsubscribed"):
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO em_suppress(practice_id, email, reason, at) VALUES (?,?,?,?)",
                  (pid, email.lower(), reason, db.now()))


def resubscribe(pid, email):
    with db.conn() as c:
        c.execute("DELETE FROM em_suppress WHERE practice_id=? AND email=? AND reason='added back'", (pid, email.lower()))


# ------------------------------------------------------------------ campaigns
TEMPLATES = {
    "newsletter": ("Newsletter", [
        {"type": "heading", "text": "[What's new this month]"},
        {"type": "text", "text": "[A short opening in the practice's voice.]"},
        {"type": "image", "item": None, "alt": ""},
        {"type": "text", "text": "[The main story: a new service, a team welcome, a community event.]"},
        {"type": "button", "label": "Book a visit", "url": ""},
        {"type": "divider"},
        {"type": "columns", "left": "**[Tip of the month]**\n[One practical tip.]", "right": "**[Office news]**\n[Hours, holidays, a new team member.]"}]),
    "referral": ("Referring offices update", [
        {"type": "heading", "text": "[A quick update for our referring offices]"},
        {"type": "text", "text": "Hi {first_name},\n\n[Thank you for trusting us with your patients. Here's what's new.]"},
        {"type": "columns", "left": "**[New]**\n[A new service, doctor or office.]", "right": "**[Easier referrals]**\n[How to refer: online form, phone, fax.]"},
        {"type": "button", "label": "Refer a patient", "url": ""},
        {"type": "text", "text": "[Doctor's name and a short sign-off.]"}]),
    "offer": ("Offer", [
        {"type": "image", "item": None, "alt": ""},
        {"type": "offer", "title": "[The offer, exactly as in the brand kit]", "text": "[Who it's for, and when it ends.]",
         "label": "Book a free consult", "url": ""},
        {"type": "text", "text": "[Terms in one or two lines.]"}]),
    "announcement": ("Announcement", [
        {"type": "heading", "text": "[The news in one line]"},
        {"type": "text", "text": "[Two or three sentences with the details.]"},
        {"type": "button", "label": "Learn more", "url": ""}]),
    "blank": ("Blank", [{"type": "text", "text": ""}]),
}
BLOCK_TYPES = [("heading", "Heading"), ("text", "Text"), ("image", "Image"), ("button", "Button"), ("columns", "Two columns"),
               ("offer", "Offer box"), ("divider", "Divider"), ("spacer", "Space")]
BLOCK_FIELDS = {"heading": ("text",), "text": ("text",), "image": ("item", "url", "alt", "link"), "button": ("label", "url"),
                "columns": ("left", "right"), "offer": ("title", "text", "label", "url"), "divider": (), "spacer": ()}


def templates(pid):
    with db.conn() as c:
        saved = [dict(r) for r in c.execute("SELECT id, name FROM em_templates WHERE practice_id=? ORDER BY name", (pid,))]
    return [(k, v[0]) for k, v in TEMPLATES.items()], saved


def save_template(pid, name, blocks, by):
    name = (name or "").strip()[:80] or "Saved template"
    with db.conn() as c:
        c.execute("INSERT INTO em_templates(practice_id, name, blocks, created_by, created_at) VALUES (?,?,?,?,?)",
                  (pid, name, json.dumps(blocks), by, db.now()))


def new_campaign(pid, name, list_ids, start, by):
    name = (name or "").strip()[:120] or "Untitled email"
    mine = {l["id"] for l in lists(pid)}
    ids = [i for i in list_ids if i in mine]
    if start.startswith("saved:"):
        with db.conn() as c:
            r = c.execute("SELECT blocks FROM em_templates WHERE id=? AND practice_id=?", (int(start[6:]), pid)).fetchone()
        blocks = json.loads(r["blocks"]) if r else TEMPLATES["blank"][1]
    else:
        blocks = TEMPLATES.get(start, TEMPLATES["newsletter"])[1]
    with db.conn() as c:
        return c.execute("INSERT INTO em_campaigns(practice_id, name, subject, blocks, list_ids, key, created_by, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (pid, name, "", json.dumps(blocks), json.dumps(ids),
                                                         secrets.token_urlsafe(12), by, db.now(), db.now())).lastrowid


def campaign(cid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM em_campaigns WHERE id=?", (cid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["blocks"] = json.loads(d["blocks"] or "[]")
    d["list_ids"] = json.loads(d["list_ids"] or "[]")
    d["links"] = json.loads(d["links"] or "[]")
    return d


def campaigns(pids=None, status=""):
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM em_campaigns ORDER BY COALESCE(sent_at, send_at, updated_at) DESC")]
    if pids is not None:
        rows = [r for r in rows if r["practice_id"] in pids]
    if status:
        rows = [r for r in rows if r["status"] == status]
    for r in rows:
        r.update(stats(r["id"]))
    return rows


def update_campaign(cid, form):
    """Saves subject, preview, lists and blocks from the editor (never the practice)."""
    k = campaign(cid)
    if not k or k["status"] != "draft":
        raise EmailError("Only drafts can be changed.")
    mine = {l["id"] for l in lists(k["practice_id"])}
    ids = [int(x) for x in form.getlist("list_id") if str(x).isdigit() and int(x) in mine] if hasattr(form, "getlist") else k["list_ids"]
    try:
        raw = json.loads(form.get("blocks") or "[]")
    except ValueError:
        raise EmailError("The email's blocks couldn't be read; reload the page.")
    blocks = []
    for b in raw[:60]:
        t = b.get("type")
        if t not in BLOCK_FIELDS:
            continue
        nb = {"type": t}
        for f in BLOCK_FIELDS[t]:
            v = b.get(f)
            if f == "item":
                nb[f] = int(v) if str(v or "").isdigit() else None
            else:
                nb[f] = str(v or "")[:5000]
        blocks.append(nb)
    with db.conn() as c:
        c.execute("UPDATE em_campaigns SET name=?, subject=?, preview=?, blocks=?, list_ids=?, updated_at=? WHERE id=?",
                  ((form.get("name") or k["name"]).strip()[:120], (form.get("subject") or "").strip()[:200],
                   (form.get("preview") or "").strip()[:200], json.dumps(blocks), json.dumps(ids), db.now(), cid))


def delete_campaign(cid):
    k = campaign(cid)
    if k and k["status"] in ("draft", "cancelled"):
        with db.conn() as c:
            c.execute("DELETE FROM em_messages WHERE campaign_id=?", (cid,))
            c.execute("DELETE FROM em_campaigns WHERE id=?", (cid,))


def copy_campaign(cid, by):
    k = campaign(cid)
    with db.conn() as c:
        return c.execute("INSERT INTO em_campaigns(practice_id, name, subject, preview, blocks, list_ids, key, created_by, created_at, updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)", (k["practice_id"], ("Copy of " + k["name"])[:120], k["subject"], k["preview"],
                                                         json.dumps(k["blocks"]), json.dumps(k["list_ids"]), secrets.token_urlsafe(12),
                                                         by, db.now(), db.now())).lastrowid


def recipients(k):
    """Unique addresses on the campaign's lists, minus the practice's unsubscribes and bounces."""
    ids = [i for i in k["list_ids"] if i in {l["id"] for l in lists(k["practice_id"])}]
    if not ids:
        return [], 0
    q = ",".join("?" * len(ids))
    with db.conn() as c:
        rows = c.execute(f"SELECT email, first_name FROM em_contacts WHERE list_id IN ({q}) ORDER BY id", ids).fetchall()
        sup = {r[0] for r in c.execute("SELECT email FROM em_suppress WHERE practice_id=?", (k["practice_id"],))}
    seen, out, skipped = set(), [], 0
    for r in rows:
        e = r["email"].lower()
        if e in seen:
            continue
        seen.add(e)
        if e in sup:
            skipped += 1
            continue
        out.append({"email": e, "first_name": r["first_name"]})
    return out, skipped


# ------------------------------------------------------------------ rendering
def _inline(s, color):
    """Escaped text with **bold**, [label](https://…) links and line breaks."""
    s = html.escape(s or "")
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", lambda m: f'<a href="{m.group(2)}" style="color:{color}">{m.group(1)}</a>', s)
    return s.replace("\n", "<br>")


def _paras(s, color):
    parts = [p for p in re.split(r"\n\s*\n", s or "") if p.strip()]
    return "".join(f'<p style="margin:0 0 14px;font-size:16px;line-height:1.6;color:#2f3b44">{_inline(p, color)}</p>' for p in parts)


def look(pid):
    from . import field_report, images
    p, a = field_report.colors(pid)
    return {"primary": p, "accent": a, "logo": bool(images.logo(pid))}


def render(k, practice, s=None, person=None, urls=None):
    """The email's HTML. urls: functions for image(iid), logo(), link(url) and unsub; plain previews use the hub's own urls."""
    s = s or sender(practice["id"]) or {"address": "[Mailing address: add it under Connections › Email sending]", "phone": "",
                                         "from_name": practice["name"]}
    lk = look(practice["id"])
    pc = lk["primary"]
    u = urls or {}
    img = u.get("image") or (lambda iid: f"/studio/f/{iid}")
    logo = u.get("logo") or (lambda: f"/e/logo/{practice['id']}")
    link = u.get("link") or (lambda url: url)
    unsub = u.get("unsub") or "#unsubscribe"
    first = (person or {}).get("first_name") or ""
    fill = lambda t: (t or "").replace("{first_name}", first or "there")
    rows = []
    for b in k["blocks"]:
        t = b["type"]
        if t == "heading":
            rows.append(f'<tr><td style="padding:28px 32px 8px"><h1 style="margin:0;font-size:26px;line-height:1.25;color:#13232f;'
                        f'font-family:Arial,Helvetica,sans-serif">{_inline(fill(b.get("text")), pc)}</h1></td></tr>')
        elif t == "text":
            rows.append(f'<tr><td style="padding:12px 32px 4px">{_paras(fill(b.get("text")), pc)}</td></tr>')
        elif t == "image":
            src = img(b["item"]) if b.get("item") else (b.get("url") or "")
            if src:
                im = f'<img src="{html.escape(src)}" alt="{html.escape(b.get("alt") or "")}" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0">'
                if b.get("link"):
                    im = f'<a href="{html.escape(link(b["link"]))}">{im}</a>'
                rows.append(f'<tr><td style="padding:16px 0 0">{im}</td></tr>')
        elif t == "button":
            if b.get("label"):
                rows.append(f'<tr><td style="padding:16px 32px 20px"><a href="{html.escape(link(b.get("url") or "#"))}" style="display:inline-block;'
                            f'background:{pc};color:#ffffff;text-decoration:none;font-weight:bold;font-size:16px;padding:14px 28px;'
                            f'border-radius:30px;font-family:Arial,Helvetica,sans-serif">{html.escape(b["label"])}</a></td></tr>')
        elif t == "columns":
            rows.append('<tr><td style="padding:12px 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                        f'<td valign="top" width="50%" style="padding:0 8px">{_paras(fill(b.get("left")), pc)}</td>'
                        f'<td valign="top" width="50%" style="padding:0 8px">{_paras(fill(b.get("right")), pc)}</td></tr></table></td></tr>')
        elif t == "offer":
            btn = (f'<a href="{html.escape(link(b.get("url") or "#"))}" style="display:inline-block;background:{pc};color:#ffffff;'
                   f'text-decoration:none;font-weight:bold;font-size:15px;padding:12px 24px;border-radius:30px">{html.escape(b["label"])}</a>'
                   if b.get("label") else "")
            rows.append(f'<tr><td style="padding:16px 32px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                        f'style="border:2px solid {pc};border-radius:12px"><tr><td style="padding:22px 24px;text-align:center">'
                        f'<div style="font-size:22px;font-weight:bold;color:#13232f;margin-bottom:8px">{_inline(b.get("title"), pc)}</div>'
                        f'<div style="font-size:15px;line-height:1.5;color:#2f3b44;margin-bottom:14px">{_inline(fill(b.get("text")), pc)}</div>{btn}'
                        '</td></tr></table></td></tr>')
        elif t == "divider":
            rows.append('<tr><td style="padding:12px 32px"><hr style="border:0;border-top:1px solid #e4e7ea;margin:0"></td></tr>')
        elif t == "spacer":
            rows.append('<tr><td style="height:24px;line-height:24px">&nbsp;</td></tr>')
    head = (f'<img src="{html.escape(logo())}" alt="{html.escape(practice["name"])}" height="48" style="display:block;height:48px;width:auto;border:0">'
            if lk["logo"] else f'<span style="color:#ffffff;font-size:20px;font-weight:bold">{html.escape(practice["name"])}</span>')
    pre = html.escape(k.get("preview") or "")
    foot = (f'<strong style="color:#ffffff">{html.escape(practice["name"])}</strong><br>{html.escape(s.get("address") or "")}'
            + (f' · {html.escape(s["phone"])}' if s.get("phone") else "")
            + f'<br>You\'re getting this email from {html.escape(practice["name"])}. '
              f'<a href="{html.escape(unsub)}" style="color:#c7d3dc">Unsubscribe</a>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(k.get("subject") or k["name"])}</title></head>
<body style="margin:0;padding:0;background:#eef2f5">
<div style="display:none;max-height:0;overflow:hidden;color:transparent">{pre}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f5"><tr><td align="center" style="padding:24px 8px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;font-family:Arial,Helvetica,sans-serif">
<tr><td style="background:{pc};padding:20px 32px">{head}</td></tr>
{''.join(rows)}
<tr><td style="height:16px"></td></tr>
<tr><td style="background:#13232f;color:#c7d3dc;padding:22px 32px;font-size:12px;line-height:1.6">{foot}</td></tr>
</table></td></tr></table></body></html>"""


def plain_text(k, practice, s, person=None, unsub=""):
    first = (person or {}).get("first_name") or "there"
    out = []
    for b in k["blocks"]:
        for f in ("title", "text", "left", "right"):
            if b.get(f):
                out.append(re.sub(r"\*\*(.+?)\*\*", r"\1", b[f].replace("{first_name}", first)))
        if b["type"] in ("button", "offer") and b.get("label"):
            out.append(f"{b['label']}: {b.get('url') or ''}")
    out.append(f"--\n{practice['name']}\n{(s or {}).get('address', '')}\nUnsubscribe: {unsub}")
    return "\n\n".join(out)


def links_of(k):
    out = []
    for b in k["blocks"]:
        for f in ("url", "link"):
            if b.get(f) and b["type"] in ("button", "offer", "image"):
                out.append(b[f])
        for f in ("text", "left", "right", "title"):
            out += re.findall(r"\]\((https?://[^\s)]+)\)", b.get(f) or "")
    return out


# ------------------------------------------------------------------ the wrong-practice check
def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def _others(pid):
    """Other practices' names, distinctive words, domains, sending addresses and phones."""
    out = []
    ss = senders()
    for p in db.practices(include_hidden=True):
        if p["id"] == pid:
            continue
        words = [w for w in _norm(p["name"]).split() if w not in GENERIC and len(w) > 2]
        dom = practice_domain(p["id"])
        s = ss.get(p["id"])
        out.append({"name": p["name"], "norm": _norm(p["name"]), "words": words, "domains": {d for d in (dom, s and s["domain"]) if d},
                    "phone": re.sub(r"\D", "", (s or {}).get("phone") or "")[-10:]})
    return out


def checks(k, practice):
    """[{ok, text, detail}] — every one must pass to send."""
    pid = practice["id"]
    s = sender(pid)
    out = []
    site = practice_domain(pid)
    if not s:
        out.append({"ok": False, "text": "This practice has a sending address", "detail": "No sending address yet. An admin adds it under Connections › Email sending."})
    else:
        ok = bool(s["ready"]) and (not site or s["domain"] == site or s["domain"].endswith("." + site))
        out.append({"ok": ok, "text": f"Sends from {s['from_addr']}, on {poss(practice['name'])} own verified domain",
                    "detail": "" if ok else "The sending address isn't ready yet (DNS checks and a test email under Connections › Email sending)."})
    mine = {l["id"] for l in lists(pid)}
    people, skipped = recipients(k)
    ok = bool(k["list_ids"]) and all(i in mine for i in k["list_ids"]) and bool(people)
    out.append({"ok": ok, "text": f"Goes only to {poss(practice['name'])} lists ({len(people):,} people)",
                "detail": "" if ok else "Pick at least one of this practice's lists with people on it."})
    if s and s["ready"]:
        cap, today = s["daily_cap"], sent_today(pid)
        ok = len(people) + today <= cap
        out.append({"ok": ok, "text": f"Within the practice's daily limit ({cap:,} a day)",
                    "detail": "" if ok else f"{len(people):,} people plus {today:,} sent in the last 24 hours is over the limit. "
                                            "Send to fewer people, or raise the limit under Connections › Email sending."})
    text = " ".join(str(b.get(f) or "") for b in k["blocks"] for f in ("text", "title", "left", "right", "label", "alt", "url", "link"))
    text += " " + (k.get("subject") or "") + " " + (k.get("preview") or "")
    nt, digits = _norm(text), re.sub(r"\D", "", text)
    hits = []
    for o in _others(pid):
        if o["norm"] and f" {o['norm']} " in f" {nt} ":
            hits.append(f"\"{o['name']}\"")
        else:
            for w in o["words"]:
                if re.search(rf"\b{re.escape(w)}\s+(ortho|orthodontics|orthodontic|dental|dentistry|pediatric|oral)\b", nt):
                    hits.append(f"\"{w.title()} …\" ({o['name']})")
                    break
        for d in o["domains"]:
            if d and d in text.lower() and not (site and d.endswith(site)):
                hits.append(f"{d} ({o['name']})")
        if o["phone"] and len(o["phone"]) == 10 and o["phone"] in digits:
            hits.append(f"{o['name']}'s phone number")
    out.append({"ok": not hits, "text": "No other practice's name, website, email or phone",
                "detail": "" if not hits else "Found " + ", ".join(dict.fromkeys(hits)) + ". Sending is blocked until it's changed."})
    allowed = {d for d in (site, s and s["domain"]) if d} | set(((s or {}).get("extra_domains") or "").split()) | set(OK_LINK_DOMAINS)
    bad = []
    for url in links_of(k):
        d = _domain(url)
        if not url.startswith(("https://", "http://", "mailto:", "tel:")):
            bad.append(url or "(empty link)")
        elif url.startswith(("http://", "https://")) and not any(d == a or d.endswith("." + a) for a in allowed):
            bad.append(d)
    for b in k["blocks"]:
        if b["type"] in ("button", "offer") and b.get("label") and not (b.get("url") or "").strip():
            bad.append(f"the \"{b['label']}\" button has no link")
    out.append({"ok": not bad, "text": f"Every link goes to {site or 'the practice'} or an approved site",
                "detail": "" if not bad else "Fix: " + ", ".join(dict.fromkeys(bad)) + ". Approved extra sites are set per practice under Connections › Email sending."})
    from . import studio
    wrong = []
    for b in k["blocks"]:
        if b["type"] == "image":
            if b.get("item"):
                it = studio.item(b["item"])
                if not it or it.get("practice_id") != pid or it.get("trashed_at"):
                    wrong.append(it["title"] if it else f"file {b['item']}")
            elif b.get("url"):
                d = _domain(b["url"])
                if not (site and (d == site or d.endswith("." + site))):
                    wrong.append(d)
    out.append({"ok": not wrong, "text": f"Images are from {poss(practice['name'])} Studio",
                "detail": "" if not wrong else "Not this practice's: " + ", ".join(wrong) + "."})
    kit = brand.kit(pid) or {}
    never = [w.strip() for w in (kit.get("never_say") or "").splitlines() if w.strip()]
    said = [w for w in never if re.search(rf"\b{re.escape(w.lower())}\b", text.lower())]
    prices = set(re.findall(r"\$\s?\d[\d,]*", text))
    offers = (kit.get("offers") or "").replace(" ", "")
    unapproved = [p for p in prices if p.replace(" ", "") not in offers]
    ok = not said and not unapproved
    out.append({"ok": ok, "text": "Brand kit rules: no \"never say\" words, prices match approved offers",
                "detail": "" if ok else " ".join(x for x in ((f"Never say: {', '.join(said)}." if said else ""),
                                                           (f"Not in the brand kit's approved offers: {', '.join(sorted(unapproved))}." if unapproved else "")) if x)})
    out.append({"ok": bool((k.get("subject") or "").strip()), "text": "Has a subject line", "detail": "" if k.get("subject") else "Add a subject."})
    return out


def poss(name):
    return (name or "") + ("'" if (name or "").endswith("s") else "'s")


def short_name(practice):
    words = [w for w in practice["name"].split() if _norm(w) not in GENERIC]
    return words[0] if words else practice["name"]


# ------------------------------------------------------------------ sending
def queue(cid, practice, typed, by, base_url, send_at=None):
    k = campaign(cid)
    if not k or k["status"] != "draft":
        raise EmailError("This campaign isn't a draft any more.")
    if _norm(typed) != _norm(short_name(practice)) and _norm(typed) != _norm(practice["name"]):
        raise EmailError(f"Type {short_name(practice)} to confirm you're sending as {practice['name']}.")
    bad = [c for c in checks(k, practice) if not c["ok"]]
    if bad:
        raise EmailError("Can't send yet: " + bad[0]["text"].lower() + ". " + bad[0]["detail"])
    if not base_url.startswith("https://"):
        raise EmailError("The hub needs its public https address (ADS_PUBLIC_URL) for unsubscribe and tracking links.")
    people, _ = recipients(k)
    earliest = datetime.now() + timedelta(minutes=CANCEL_MINUTES)
    when = max(earliest, send_at) if send_at else earliest
    with db.conn() as c:
        for p in people:
            c.execute("INSERT OR IGNORE INTO em_messages(campaign_id, email, first_name, token) VALUES (?,?,?,?)",
                      (cid, p["email"], p["first_name"], secrets.token_urlsafe(16)))
        c.execute("UPDATE em_campaigns SET status='queued', send_at=?, base_url=?, links=?, queued_by=?, queued_at=? WHERE id=?",
                  (when.isoformat(timespec="seconds"), base_url.rstrip("/"), json.dumps(links_of(k)), by, db.now(), cid))
    return when


def cancel(cid):
    with db.conn() as c:
        k = c.execute("SELECT status FROM em_campaigns WHERE id=?", (cid,)).fetchone()
        if not k or k["status"] not in ("queued", "sending"):
            raise EmailError("It isn't waiting to send.")
        sent = c.execute("SELECT COUNT(*) FROM em_messages WHERE campaign_id=? AND status='sent'", (cid,)).fetchone()[0]
        c.execute("DELETE FROM em_messages WHERE campaign_id=? AND status='queued'", (cid,))
        c.execute("UPDATE em_campaigns SET status=? WHERE id=?", ("draft" if not sent else "cancelled", cid))
    return sent


def _urls(k, practice, m):
    base = k["base_url"]
    lks = k["links"]

    def link(url):
        try:
            return f"{base}/e/c/{m['token']}/{lks.index(url)}"
        except ValueError:
            return url
    return {"image": lambda iid: f"{base}/e/i/{k['key']}/{iid}", "logo": lambda: f"{base}/e/logo/{practice['id']}",
            "link": link, "unsub": f"{base}/e/u/{m['token']}"}


def message_html(k, practice, s, m):
    u = _urls(k, practice, m)
    body = render(k, practice, s, m, u)
    pixel = f'<img src="{k["base_url"]}/e/o/{m["token"]}.gif" width="1" height="1" alt="" style="display:block;border:0">'
    return body.replace("</body>", pixel + "</body>"), plain_text(k, practice, s, m, u["unsub"]), u["unsub"]


def _sent_since(seconds):
    since = (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM em_messages WHERE sent_at >= ?", (since,)).fetchone()[0]


_lock = threading.Lock()
_pause_until = [0.0]


def work(send=None, now=None, max_batch=None):
    """Send what's due, within Azure's rate limits. Returns how many went out."""
    send = send or acs_send
    if not _lock.acquire(blocking=False):
        return 0
    try:
        if time.time() < _pause_until[0]:
            return 0
        now = (now or datetime.now()).isoformat(timespec="seconds")
        r = rates()
        room = min(r["minute"] - _sent_since(60), r["hour"] - _sent_since(3600))
        if max_batch is not None:
            room = min(room, max_batch)
        done = 0
        with db.conn() as c:
            due = [x["id"] for x in c.execute("SELECT id FROM em_campaigns WHERE status IN ('queued', 'sending') AND send_at <= ? "
                                              "ORDER BY send_at", (now,))]
        for cid in due:
            k = campaign(cid)
            with db.conn() as c:
                c.execute("UPDATE em_campaigns SET status='sending' WHERE id=? AND status='queued'", (cid,))
            practice = db.practice(k["practice_id"])
            s = sender(k["practice_id"])
            if not practice or not s or not s["ready"]:
                with db.conn() as c:
                    c.execute("UPDATE em_campaigns SET status='paused', error=? WHERE id=?",
                              ("The practice's sending address isn't ready any more.", cid))
                continue
            while room > 0:
                with db.conn() as c:
                    m = c.execute("SELECT * FROM em_messages WHERE campaign_id=? AND status='queued' ORDER BY id LIMIT 1", (cid,)).fetchone()
                if not m:
                    break
                m = dict(m)
                with db.conn() as c:     # unsubscribed after it was queued
                    if c.execute("SELECT 1 FROM em_suppress WHERE practice_id=? AND email=?", (k["practice_id"], m["email"])).fetchone():
                        c.execute("UPDATE em_messages SET status='skipped' WHERE id=?", (m["id"],))
                        continue
                body, text, unsub = message_html(k, practice, s, m)
                try:
                    op = send(s, m["email"], m["first_name"], k["subject"], body, text, unsub)
                    with db.conn() as c:
                        c.execute("UPDATE em_messages SET status='sent', acs_id=?, sent_at=? WHERE id=?", (op, db.now(), m["id"]))
                    done += 1
                    room -= 1
                except RateLimited as e:
                    _pause_until[0] = time.time() + e.wait
                    return done
                except EmailError as e:
                    with db.conn() as c:
                        c.execute("UPDATE em_messages SET status='failed', error=?, sent_at=? WHERE id=?", (str(e)[:300], db.now(), m["id"]))
                    done += 1
                    room -= 1
            with db.conn() as c:
                left = c.execute("SELECT COUNT(*) FROM em_messages WHERE campaign_id=? AND status='queued'", (cid,)).fetchone()[0]
                if not left:
                    c.execute("UPDATE em_campaigns SET status='sent', sent_at=? WHERE id=?", (db.now(), cid))
            if room <= 0:
                break
        return done
    finally:
        _lock.release()


def start_worker():
    def loop():
        while True:
            try:
                work()
            except Exception:
                pass
            time.sleep(20)
    threading.Thread(target=loop, daemon=True, name="email-sender").start()


def test_send(k, practice, to_addr, base_url):
    s = sender(practice["id"])
    if not s:
        raise EmailError("This practice has no sending address yet.")
    if not EMAIL_RE.match(to_addr or ""):
        raise EmailError("Which address should the test go to?")
    tk = dict(k, base_url=base_url.rstrip("/"), links=links_of(k))
    m = {"token": "test", "first_name": "", "email": to_addr}
    body, text, unsub = message_html(tk, practice, s, m)
    acs_send(s, to_addr, "", "[Test] " + (k["subject"] or k["name"]), body, text, unsub)


# ------------------------------------------------------------------ tracking, unsubscribes, delivery reports
def by_token(token):
    with db.conn() as c:
        r = c.execute("SELECT m.*, k.practice_id, k.links FROM em_messages m JOIN em_campaigns k ON k.id=m.campaign_id "
                      "WHERE m.token=?", (token,)).fetchone()
    return dict(r) if r else None


def opened(token):
    with db.conn() as c:
        c.execute("UPDATE em_messages SET opened_at=COALESCE(opened_at, ?) WHERE token=?", (db.now(), token))


def clicked(token, n):
    m = by_token(token)
    if not m:
        return None
    links = json.loads(m["links"] or "[]")
    if not (0 <= n < len(links)):
        return None
    with db.conn() as c:
        c.execute("UPDATE em_messages SET clicked_at=COALESCE(clicked_at, ?), opened_at=COALESCE(opened_at, ?) WHERE id=?",
                  (db.now(), db.now(), m["id"]))
        c.execute("INSERT INTO em_clicks(message_id, link, at) VALUES (?,?,?)", (m["id"], n, db.now()))
    url = links[n]
    sep = "&" if "?" in url else "?"
    if url.startswith(("http://", "https://")) and "utm_source=" not in url:
        url += f"{sep}utm_source=email&utm_medium=email&utm_campaign=email-{m['campaign_id']}"
    return url


def delivery_report(data):
    """Azure Event Grid 'EmailDeliveryReportReceived': marks bounces and suppresses the address for that practice."""
    mid = (data.get("messageId") or "").strip()
    status = (data.get("status") or "").lower()
    if not mid:
        return
    with db.conn() as c:
        m = c.execute("SELECT m.id, m.email, k.practice_id FROM em_messages m JOIN em_campaigns k ON k.id=m.campaign_id "
                      "WHERE m.acs_id=?", (mid,)).fetchone()
        if not m:
            return
        c.execute("UPDATE em_messages SET delivered=? WHERE id=?", (status, m["id"]))
    if status in ("bounced", "suppressed", "failed", "quarantined", "filteredspam"):
        unsubscribe(m["practice_id"], m["email"], "bounced" if status == "bounced" else status)


def stats(cid):
    with db.conn() as c:
        r = c.execute("SELECT COUNT(*) AS n, SUM(status='sent') AS sent, SUM(status='failed') AS failed, SUM(status='queued') AS queued, "
                      "SUM(delivered='bounced' OR delivered='failed' OR delivered='suppressed') AS bounced, "
                      "SUM(opened_at IS NOT NULL) AS opened, SUM(clicked_at IS NOT NULL) AS clicked FROM em_messages WHERE campaign_id=?",
                      (cid,)).fetchone()
        k = c.execute("SELECT practice_id, sent_at, queued_at FROM em_campaigns WHERE id=?", (cid,)).fetchone()
        uns = c.execute("SELECT COUNT(*) FROM em_suppress s WHERE s.practice_id=? AND s.reason='unsubscribed' AND s.email IN "
                        "(SELECT email FROM em_messages WHERE campaign_id=?)", (k["practice_id"], cid)).fetchone()[0] if k else 0
    d = {x: (r[x] or 0) for x in ("n", "sent", "failed", "queued", "bounced", "opened", "clicked")}
    d["unsubscribed"] = uns
    base = d["sent"] - d["bounced"]
    d["open_rate"] = d["opened"] / base if base > 0 else None
    d["click_rate"] = d["clicked"] / base if base > 0 else None
    return d


def link_clicks(cid):
    k = campaign(cid)
    with db.conn() as c:
        rows = c.execute("SELECT link, COUNT(DISTINCT message_id) AS n FROM em_clicks WHERE message_id IN "
                         "(SELECT id FROM em_messages WHERE campaign_id=?) GROUP BY link ORDER BY n DESC", (cid,)).fetchall()
    return [{"url": k["links"][r["link"]] if r["link"] < len(k["links"]) else "?", "n": r["n"]} for r in rows]
