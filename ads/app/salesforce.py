"""Field visits → Salesforce.

Each visit becomes a completed Task on the office's Account, linked to the first person met; everyone met
is a Contact on that Account. A follow-up date becomes an open Task on that day. Offices the hub creates
are tagged Account Type = "Referral office" so they can be filtered away from acquisition prospects.
Existing Accounts and Contacts are matched (by name and ZIP, by email) and never edited.

Sign-in is OAuth (web server flow with PKCE) through an External Client App in Salesforce; the hub keeps
the refresh token. Sending runs in the background after each visit is saved, and every 15 minutes for
anything that failed.
"""
import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse

from . import connectors as cx, db

API = "v62.0"
ACCOUNT_TYPE = "Referral office"
RETRY_MINUTES = 15
BATCH = 50
_lock = threading.Lock()
_tok_lock = threading.Lock()   # with refresh token rotation, two refreshes at once would spend the token twice
_token_cache = {}


class SFError(RuntimeError):
    pass


def init():
    with db.conn() as c:
        c.executescript("""
CREATE TABLE IF NOT EXISTS sf_links (
  kind TEXT NOT NULL,
  local_key TEXT NOT NULL,
  sf_id TEXT NOT NULL,
  PRIMARY KEY (kind, local_key)
);""")
        have = {r["name"] for r in c.execute("PRAGMA table_info(visits)")}
        for col, ddl in (("sf_task_id", "TEXT DEFAULT ''"), ("sf_followup_id", "TEXT DEFAULT ''"),
                         ("sf_status", "TEXT DEFAULT ''"), ("sf_error", "TEXT DEFAULT ''"), ("sf_at", "TEXT DEFAULT ''")):
            if col not in have:
                c.execute(f"ALTER TABLE visits ADD COLUMN {col} {ddl}")


# ------------------------------------------------------------------ connection
def login_url():
    return (cx.setting("SF_LOGIN_URL") or "https://login.salesforce.com").rstrip("/")


def client():
    return cx.setting("SF_CLIENT_ID"), cx.setting("SF_CLIENT_SECRET")


def connected():
    return bool(db.get_setting("sf_refresh_token"))


def status():
    try:
        return json.loads(db.get_setting("sf_status") or "{}")
    except ValueError:
        return {}


def pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(redirect_uri, state, challenge):
    cid, _ = client()
    return login_url() + "/services/oauth2/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": redirect_uri, "state": state,
        "scope": "api refresh_token id", "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "login consent"})


def _form_post(url, fields):
    try:
        return cx._http(url, data=urllib.parse.urlencode(fields).encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    except cx.ConnectorError as e:
        raise SFError(str(e))


def exchange_code(code, redirect_uri, verifier):
    cid, secret = client()
    tok = _form_post(login_url() + "/services/oauth2/token", {
        "grant_type": "authorization_code", "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect_uri, "code_verifier": verifier})
    if not tok.get("refresh_token"):
        raise SFError("Salesforce didn't return a long-term sign-in. In the External Client App, add the "
                      "“Perform requests at any time (refresh_token, offline_access)” scope, then connect again.")
    db.set_setting("sf_refresh_token", tok["refresh_token"])
    db.set_setting("sf_instance_url", tok["instance_url"])
    _token_cache.clear()
    _token_cache.update(access=tok["access_token"], url=tok["instance_url"], at=time.time())
    try:
        me = api("GET", tok["id"], absolute=True) if tok.get("id") else {}
    except SFError:   # the name is only for display
        me = {}
    db.set_setting("sf_user", json.dumps({"name": me.get("display_name", ""), "username": me.get("username", ""),
                                          "org": me.get("organization_id", "")}))
    return me


def disconnect():
    for k in ("sf_refresh_token", "sf_instance_url", "sf_user", "sf_status"):
        db.set_setting(k, None)
    _token_cache.clear()


def user():
    try:
        return json.loads(db.get_setting("sf_user") or "{}")
    except ValueError:
        return {}


def instance_url():
    return db.get_setting("sf_instance_url") or ""


def _access(force=False):
    seen = _token_cache.get("access")
    with _tok_lock:
        fresh = _token_cache.get("access") and time.time() - _token_cache.get("at", 0) < 1800
        if fresh and (not force or _token_cache["access"] != seen):   # someone else just refreshed: use theirs
            return _token_cache["access"], _token_cache["url"]
        return _refresh()


def _refresh():
    rt = db.get_setting("sf_refresh_token")
    if not rt:
        raise SFError("Salesforce isn't connected.")
    cid, secret = client()
    tok = _form_post(login_url() + "/services/oauth2/token", {
        "grant_type": "refresh_token", "refresh_token": rt, "client_id": cid, "client_secret": secret})
    url = tok.get("instance_url") or instance_url()
    if tok.get("refresh_token") and tok["refresh_token"] != rt:
        db.set_setting("sf_refresh_token", tok["refresh_token"])   # refresh token rotation: the old one is now spent
    db.set_setting("sf_last_refresh", db.now())
    _token_cache.update(access=tok["access_token"], url=url, at=time.time())
    return tok["access_token"], url


def keep_alive():
    """Salesforce expires a refresh token unused for 30 days; refresh once a day so a quiet month doesn't disconnect."""
    from datetime import datetime, timedelta
    last = db.get_setting("sf_last_refresh")
    if connected() and (not last or datetime.now() - datetime.fromisoformat(last) > timedelta(days=1)):
        _access(force=True)


def api(method, path, body=None, params=None, absolute=False):
    """One REST call. Retries once with a fresh token on 401."""
    for attempt in (0, 1):
        tok, base = _access(force=attempt == 1)
        url = path if absolute else f"{base}/services/data/{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            return cx._http(url, data=body, method=method,
                            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                                     "Accept": "application/json"}) or {}
        except cx.ConnectorError as e:
            if "HTTP 401" in str(e) and attempt == 0:
                continue
            raise SFError(str(e))
        except ValueError:   # 204 No Content
            return {}


def query(soql):
    return api("GET", "/query", params={"q": soql}).get("records", [])


# Fields that are nice to fill but not needed. If the org hides one (field-level security, or it doesn't exist),
# Salesforce answers "No such column 'X' on sobject of type Y": leave it out and try again rather than fail the visit.
OPTIONAL = {"Account": {"Description", "Type", "Phone", "Website", "BillingStreet", "BillingCity", "BillingState",
                        "BillingPostalCode", "BillingCountry"},
            "Contact": {"Description", "Title", "Salutation", "Email"},
            "Task": {"Description", "Priority", "WhoId", "OwnerId"}}
NO_COLUMN = re.compile(r"No such column '(\w+)' on sobject of type (\w+)")


def create(sobject, fields):
    body = {k: v for k, v in fields.items() if v not in (None, "")}
    while True:
        try:
            got = api("POST", f"/sobjects/{sobject}", body=body)
            break
        except SFError as e:
            m = NO_COLUMN.search(str(e))
            if not m or m.group(1) not in body or m.group(1) not in OPTIONAL.get(sobject, ()):
                raise
            body.pop(m.group(1))
    if not got.get("id"):
        raise SFError(f"Salesforce didn't create the {sobject}: {got}")
    return got["id"]


def _q(s):
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


# ------------------------------------------------------------------ matching
ADDR = re.compile(r"^(?P<street>.+?),\s*(?P<city>[^,]+?),\s*(?P<state>[A-Z]{2})\s*(?P<zip>\d{5}(?:-\d{4})?)?(?:,\s*(?:USA|United States))?\s*$")


def split_address(a):
    m = ADDR.match((a or "").strip())
    if not m:
        return {"BillingStreet": (a or "").strip()[:255]} if a else {}
    return {"BillingStreet": m["street"], "BillingCity": m["city"], "BillingState": m["state"],
            "BillingPostalCode": m["zip"] or ""}


def split_name(name, email=""):
    name = re.sub(r"\s+", " ", (name or "").strip())
    sal = ""
    m = re.match(r"^(dr\.?|doctor)\s+", name, re.I)
    if m:
        sal, name = "Dr.", name[m.end():]
    if not name:
        return {"LastName": (email.split("@")[0] if email else "Unknown")[:80], "Salutation": sal}
    parts = name.split(" ")
    return {"FirstName": " ".join(parts[:-1])[:40], "LastName": parts[-1][:80], "Salutation": sal}


def _link(kind, key):
    with db.conn() as c:
        r = c.execute("SELECT sf_id FROM sf_links WHERE kind=? AND local_key=?", (kind, str(key))).fetchone()
    return r["sf_id"] if r else None


def _remember(kind, key, sf_id):
    with db.conn() as c:
        c.execute("INSERT INTO sf_links(kind, local_key, sf_id) VALUES (?,?,?) "
                  "ON CONFLICT(kind, local_key) DO UPDATE SET sf_id=excluded.sf_id", (kind, str(key), sf_id))
    return sf_id


def account_for(v):
    """The Account for the visit's office: remembered, else matched by name (+ ZIP when several), else created."""
    if (got := _link("office", v["office_id"])):
        return got
    address = v.get("office_address") or ""
    if not address and (v.get("place_id") or v.get("npi_key")):
        from . import field
        n = field._npi_for_place(v["place_id"]) if v.get("place_id") else field._npi(v["npi_key"])
        address = field._npi_address(n) if n else ""
    addr = split_address(address)
    rows = query(f"SELECT Id, BillingPostalCode FROM Account WHERE Name = {_q(v['office_name'])} LIMIT 10")
    if len(rows) > 1 and addr.get("BillingPostalCode"):
        rows = [r for r in rows if (r.get("BillingPostalCode") or "")[:5] == addr["BillingPostalCode"][:5]] or rows
    if rows:
        return _remember("office", v["office_id"], rows[0]["Id"])
    acc = create("Account", {"Name": v["office_name"][:255], "Type": ACCOUNT_TYPE, **addr,
                             "Description": "Added from MCDP Hub field visits."})
    return _remember("office", v["office_id"], acc)


def contact_for(p, account_id):
    key = p["email"] or f"{account_id}|{p['name'].lower()}"
    if (got := _link("contact", key)):
        return got
    rows = (query(f"SELECT Id FROM Contact WHERE Email = {_q(p['email'])} ORDER BY CreatedDate LIMIT 1") if p["email"] else
            query(f"SELECT Id FROM Contact WHERE AccountId = {_q(account_id)} AND Name = {_q(p['name'])} LIMIT 1"))
    if rows:
        return _remember("contact", key, rows[0]["Id"])
    cid = create("Contact", {**split_name(p["name"], p["email"]), "Email": p["email"], "Title": p["role"][:128],
                             "AccountId": account_id, "Description": "Added from MCDP Hub field visits."})
    return _remember("contact", key, cid)


def owner_for(v):
    """The agent's Salesforce user, matched by the email they sign in to the hub with; else the connected user."""
    from . import users
    u = users.get(v["user_id"]) if v.get("user_id") else None
    email = (u or {}).get("email") or ""
    if not email:
        return None
    if (got := _link("user", email)) is not None:
        return got or None
    rows = query(f"SELECT Id FROM User WHERE Email = {_q(email)} AND IsActive = true LIMIT 1")
    return _remember("user", email, rows[0]["Id"] if rows else "") or None


def practice_name(v):
    """Which of our practices the visit was for (picked on the form)."""
    pid = v.get("practice_id")
    p = db.practice(pid) if pid else None
    return p["name"] if p else ""


def _subject(kind, v):
    pn = practice_name(v)
    return (f"{kind} for {pn}: {v['office_name']}" if pn else f"{kind}: {v['office_name']}")[:255]


def description(v, hub_url=""):
    from .field import RATING_LABELS
    lines = [f"Logged in MCDP Hub by {v.get('user_name') or 'a field agent'}."]
    if practice_name(v):
        lines.append(f"For practice: {practice_name(v)}")
    if v.get("people"):
        lines.append("Met: " + "; ".join(
            (p["name"] or p["email"]) + (f" ({p['role']})" if p["role"] else "") + (f" <{p['email']}>" if p["email"] and p["name"] else "")
            for p in v["people"]))
    if v.get("rating"):
        lines.append(f"Relationship: {RATING_LABELS.get(v['rating'], v['rating'])}")
    if v.get("dropped") or v.get("cost"):
        lines.append("Dropped off: " + (v.get("dropped") or "—") + (f" (${v['cost']:,.2f})" if v.get("cost") else ""))
    if v.get("on_site") is not None:
        lines.append("On site: yes" if v["on_site"] == 1 else f"On site: no ({v['distance_mi']:g} mi from the office)")
    if v.get("followup"):
        lines.append(f"Follow up on: {v['followup']}")
    if v.get("notes"):
        lines += ["", "Notes:", v["notes"]]
    if hub_url:
        lines += ["", f"In the hub: {hub_url}/field?period=day&day={v['visited_at'][:10]}"]
    return "\n".join(lines)[:32000]


# ------------------------------------------------------------------ sending
def send_visit(vid, hub_url=""):
    """Send one visit (idempotent: parts already created are skipped). Returns the Task id."""
    from . import field
    v = field.visit(vid)
    if not v:
        return None
    acc = account_for(v)
    contacts = [contact_for(p, acc) for p in v["people"]]
    owner = owner_for(v)
    who = contacts[0] if contacts else None
    task = v.get("sf_task_id") or ""
    if not task:
        task = create("Task", {"Subject": _subject("Field visit", v), "ActivityDate": v["visited_at"][:10],
                               "Status": "Completed", "Priority": "Normal", "WhatId": acc, "WhoId": who, "OwnerId": owner,
                               "Description": description(v, hub_url)})
        _set(vid, sf_task_id=task)
    if v.get("followup") and not v.get("sf_followup_id"):
        fu = create("Task", {"Subject": _subject("Follow up", v), "ActivityDate": v["followup"],
                             "Status": "Open", "Priority": "Normal", "WhatId": acc, "WhoId": who, "OwnerId": owner,
                             "Description": f"Follow-up from the {v['visited_at'][:10]} field visit by {v.get('user_name') or 'a field agent'}"
                                            + (f" for {practice_name(v)}." if practice_name(v) else ".")
                                            + (f"\n\nNotes from the visit:\n{v['notes']}" if v.get("notes") else "")})
        _set(vid, sf_followup_id=fu)
    _set(vid, sf_status="sent", sf_error="", sf_at=db.now())
    return task


def _set(vid, **cols):
    with db.conn() as c:
        c.execute(f"UPDATE visits SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), vid))


def pending_ids(limit=BATCH):
    with db.conn() as c:
        return [r["id"] for r in c.execute(
            "SELECT id FROM visits WHERE COALESCE(sf_status, '') != 'sent' ORDER BY visited_at LIMIT ?", (limit,))]


def send_pending(hub_url="", limit=BATCH):
    """Send visits not yet in Salesforce. Stops at the first sign-in problem (no point hammering)."""
    if not (connected() and all(client())):
        return {"sent": 0, "failed": 0, "skipped": True}
    sent = failed = 0
    last_error = ""
    with _lock:
        for vid in pending_ids(limit):
            try:
                send_visit(vid, hub_url)
                sent += 1
            except SFError as e:
                failed += 1
                last_error = str(e)
                _set(vid, sf_status="error", sf_error=last_error[:500], sf_at=db.now())
                if "invalid_grant" in last_error or "expired" in last_error.lower() or "isn't connected" in last_error:
                    break
    out = {"at": db.now(), "sent": sent, "failed": failed, "error": last_error, "waiting": len(pending_ids(10000))}
    db.set_setting("sf_status", json.dumps(out))
    return out


def send_in_background(hub_url="", limit=BATCH):
    if connected():
        threading.Thread(target=lambda: _safe(send_pending, hub_url, limit), name="salesforce-send", daemon=True).start()


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception as e:   # never let a background send crash anything
        db.set_setting("sf_status", json.dumps({"at": db.now(), "sent": 0, "failed": 1, "error": str(e)}))


def start_scheduler(hub_url=""):
    def loop():
        time.sleep(120)
        while True:
            if connected():
                _safe(send_pending, hub_url)
                _safe(keep_alive)
            time.sleep(RETRY_MINUTES * 60)
    threading.Thread(target=loop, name="salesforce-retry", daemon=True).start()


def record_url(sf_id):
    base = instance_url()
    return f"{base}/{sf_id}" if base and sf_id else ""
