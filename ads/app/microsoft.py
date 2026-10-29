"""Microsoft 365 (Graph) for the Calendar: one sign-in, events in a shared mailbox's calendar, invites, replies.

An admin registers an app in Entra ID (delegated permissions: Calendars.ReadWrite, Calendars.ReadWrite.Shared,
User.Read, User.ReadBasic.All, offline_access, openid, profile, email) and signs in once on the Connections page.
The hub keeps the refresh token. Events are created in the "send from" mailbox (e.g. a shared mailbox the
signed-in person has Full Access and Send As on), so that mailbox is the organizer and receives the replies.
Outlook sends the invites; the hub reads the replies back.
"""
import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import connectors as cx, db

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ("offline_access openid profile email User.Read User.ReadBasic.All "
          "Calendars.ReadWrite Calendars.ReadWrite.Shared")
DEFAULT_TZ = "America/Detroit"     # the practices are in Michigan
_tok_lock = threading.Lock()
_token = {}


class MSError(RuntimeError):
    pass


# ------------------------------------------------------------------ settings
def settings():
    return {"tenant": db.get_setting("ms_tenant_id") or "", "client_id": db.get_setting("ms_client_id") or "",
            "secret": bool(db.get_setting("ms_client_secret")), "mailbox": mailbox(), "tz": timezone()}


def configured():
    return bool(db.get_setting("ms_tenant_id") and db.get_setting("ms_client_id") and db.get_setting("ms_client_secret"))


def connected():
    return bool(db.get_setting("ms_refresh_token"))


def mailbox():
    return (db.get_setting("ms_mailbox") or "").strip()


def timezone():
    return db.get_setting("ms_timezone") or DEFAULT_TZ


def user():
    try:
        return json.loads(db.get_setting("ms_user") or "{}")
    except ValueError:
        return {}


def save(tenant, client_id, secret, mbox, tz):
    db.set_setting("ms_tenant_id", tenant)
    db.set_setting("ms_client_id", client_id)
    if secret:
        db.set_setting("ms_client_secret", secret)
    db.set_setting("ms_mailbox", mbox or None)
    db.set_setting("ms_timezone", tz or None)
    _token.clear()


def disconnect():
    for k in ("ms_refresh_token", "ms_user", "ms_last_refresh", "ms_status"):
        db.set_setting(k, None)
    _token.clear()


# ------------------------------------------------------------------ sign-in
def _authority():
    return f"https://login.microsoftonline.com/{db.get_setting('ms_tenant_id')}/oauth2/v2.0"


def pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(redirect_uri, state, challenge):
    return _authority() + "/authorize?" + urllib.parse.urlencode({
        "client_id": db.get_setting("ms_client_id"), "response_type": "code", "redirect_uri": redirect_uri,
        "response_mode": "query", "scope": SCOPES, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "prompt": "select_account"})


def _token_post(fields):
    body = urllib.parse.urlencode({"client_id": db.get_setting("ms_client_id"),
                                   "client_secret": db.get_setting("ms_client_secret"), "scope": SCOPES, **fields}).encode()
    req = urllib.request.Request(_authority() + "/token", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            j = json.loads(raw)
            msg = (j.get("error_description") or j.get("error") or raw).split("\r\n")[0]
        except ValueError:
            msg = raw[:200]
        raise MSError(_explain_signin(msg))
    except urllib.error.URLError as e:
        raise MSError(f"Couldn't reach Microsoft: {e.reason}")


def _explain_signin(msg):
    if "AADSTS7000215" in msg:
        return "The client secret is wrong or expired. Create a new one in Entra (Certificates & secrets) and save it here."
    if "AADSTS65001" in msg or "consent" in msg.lower():
        return ("Microsoft is asking for an admin to approve the app. In Entra → App registrations → MCDP Hub Calendar → "
                "API permissions, click “Grant admin consent”, then sign in again. (" + msg[:160] + ")")
    if "AADSTS50011" in msg:
        return "The redirect address doesn't match the app registration. Add the address shown on this page under Authentication."
    if "AADSTS700082" in msg or "AADSTS70008" in msg or "invalid_grant" in msg:
        return "The Microsoft sign-in has expired. Sign in again."
    return msg


def exchange_code(code, redirect_uri, verifier):
    tok = _token_post({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                       "code_verifier": verifier})
    if not tok.get("refresh_token"):
        raise MSError("Microsoft didn't return a long-term sign-in (offline_access). Check the app's API permissions.")
    _store(tok)
    me = graph("GET", "/me", params={"$select": "displayName,mail,userPrincipalName"})
    db.set_setting("ms_user", json.dumps({"name": me.get("displayName", ""),
                                          "email": me.get("mail") or me.get("userPrincipalName", "")}))
    return me


def _store(tok):
    with _tok_lock:
        if tok.get("refresh_token"):
            db.set_setting("ms_refresh_token", tok["refresh_token"])
        db.set_setting("ms_last_refresh", db.now())
        _token.update(access=tok["access_token"], until=time.time() + int(tok.get("expires_in", 3600)) - 120)


def _access(force=False):
    with _tok_lock:
        if not force and _token.get("access") and time.time() < _token.get("until", 0):
            return _token["access"]
        rt = db.get_setting("ms_refresh_token")
    if not rt:
        raise MSError("Microsoft 365 isn't connected.")
    tok = _token_post({"grant_type": "refresh_token", "refresh_token": rt})
    _store(tok)
    return tok["access_token"]


def keep_alive():
    """Refresh tokens last 90 days from last use; refresh daily so a quiet stretch doesn't disconnect."""
    from datetime import datetime, timedelta
    last = db.get_setting("ms_last_refresh")
    if connected() and (not last or datetime.now() - datetime.fromisoformat(last) > timedelta(days=1)):
        _access(force=True)


# ------------------------------------------------------------------ Graph
def graph(method, path, body=None, params=None, headers=None):
    for attempt in (0, 1):
        tok = _access(force=attempt == 1)
        url = (path if path.startswith("https://") else GRAPH + path) + ("?" + urllib.parse.urlencode(params) if params else "")
        h = {"Authorization": f"Bearer {tok}", "Accept": "application/json",
             "Prefer": f'outlook.timezone="{timezone()}"', **(headers or {})}
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            if e.code == 401 and attempt == 0:
                continue
            try:
                err = json.loads(raw).get("error", {})
                msg = f"{err.get('message') or err.get('code') or raw[:200]} (HTTP {e.code})"
            except ValueError:
                msg = f"HTTP {e.code}: {raw[:200]}"
            raise MSError(_explain_graph(msg, e.code))
        except urllib.error.URLError as e:
            raise MSError(f"Couldn't reach Microsoft Graph: {e.reason}")


def _explain_graph(msg, code):
    mb = mailbox()
    if code in (403, 404) and mb and ("ErrorAccessDenied" in msg or "Access is denied" in msg or "not found" in msg.lower()
                                      or "ErrorItemNotFound" in msg or "MailboxNotEnabled" in msg):
        return (f"Can't use the {mb} calendar as {user().get('email') or 'the signed-in account'}. In the Microsoft 365 admin "
                f"center → Shared mailboxes → {mb}, add that person as a member (Full Access and Send As), wait up to an "
                f"hour, then try again. ({msg})")
    return msg


def base():
    """Where events live: the 'send from' mailbox, or the signed-in person's own calendar."""
    mb = mailbox()
    me = (user().get("email") or "").lower()
    return f"/users/{urllib.parse.quote(mb)}" if mb and mb.lower() != me else "/me"


# ------------------------------------------------------------------ events
def event_body(ev, people, with_attendees=True):
    tz = timezone()
    if ev.get("all_day"):
        from datetime import date, timedelta
        d0 = date.fromisoformat(ev["starts"][:10])
        d1 = date.fromisoformat((ev.get("ends") or ev["starts"])[:10])
        d1 = max(d1, d0) + timedelta(days=1)
        start, end = {"dateTime": f"{d0}T00:00:00", "timeZone": tz}, {"dateTime": f"{d1}T00:00:00", "timeZone": tz}
    else:
        start = {"dateTime": ev["starts"][:16] + ":00", "timeZone": tz}
        end = {"dateTime": ev["ends"][:16] + ":00", "timeZone": tz}
    loc = ", ".join(x for x in (ev.get("place_label"), ev.get("place_address")) if x)
    body = {
        "subject": ev["subject"][:255],
        "body": {"contentType": "HTML", "content": invite_html(ev)},
        "start": start, "end": end, "isAllDay": bool(ev.get("all_day")),
        "location": {"displayName": loc[:255]} if loc else {"displayName": ""},
        "showAs": ev.get("show_as") or "busy",
        "isReminderOn": ev.get("reminder") is not None and ev.get("reminder") >= 0,
        "reminderMinutesBeforeStart": max(0, ev.get("reminder") or 0),
        "sensitivity": "private" if ev.get("private") else "normal",
        "responseRequested": True, "allowNewTimeProposals": True,
        "categories": [ev["category_label"]] if ev.get("category_label") else [],
    }
    if ev.get("teams"):
        body.update(isOnlineMeeting=True, onlineMeetingProvider="teamsForBusiness")
    rec = recurrence(ev)
    if rec:
        body["recurrence"] = rec
    if with_attendees:
        body["attendees"] = [{"emailAddress": {"address": p["email"], "name": p.get("name") or p["email"]},
                              "type": "required" if p.get("required", 1) else "optional"} for p in people if p.get("email")]
    return body


def recurrence(ev):
    from datetime import date
    rep = ev.get("repeat") or ""
    if not rep:
        return None
    d = date.fromisoformat(ev["starts"][:10])
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    pattern = ({"type": "weekly", "interval": 2 if rep == "biweekly" else 1, "daysOfWeek": [days[d.weekday()]]}
               if rep in ("weekly", "biweekly") else {"type": "absoluteMonthly", "interval": 1, "dayOfMonth": d.day})
    until = ev.get("repeat_until") or ""
    rng = ({"type": "endDate", "startDate": d.isoformat(), "endDate": until[:10]} if until
           else {"type": "noEnd", "startDate": d.isoformat()})
    return {"pattern": pattern, "range": rng}


def invite_html(ev):
    from html import escape
    parts = []
    if ev.get("extra"):
        parts.append(f"<p><strong>{escape(ev.get('extra_label') or 'Details')}:</strong> {escape(ev['extra'])}</p>")
    if ev.get("details"):
        parts.append("<p>" + escape(ev["details"]).replace("\n", "<br>") + "</p>")
    parts.append('<p style="color:#5B5852;font-size:12px">Scheduled from MCDP Hub</p>')
    return "\n".join(parts)


def create_event(ev, people, attachment=None):
    """Create in the send-from calendar; Outlook sends the invites. Returns (id, webLink).
    transactionId makes a retry after a timeout return the same event instead of a duplicate."""
    b = base()
    body = event_body(ev, people, with_attendees=not attachment)
    body["transactionId"] = ev["ms_txn"]
    got = graph("POST", f"{b}/events", body)
    if attachment:   # attach first, then add attendees, so the invite carries the file
        name, ctype, data = attachment
        graph("POST", f"{b}/events/{got['id']}/attachments",
              {"@odata.type": "#microsoft.graph.fileAttachment", "name": name, "contentType": ctype or "application/octet-stream",
               "contentBytes": base64.b64encode(data).decode()})
        graph("PATCH", f"{b}/events/{got['id']}", {"attendees": event_body(ev, people)["attendees"]})
    return got["id"], got.get("webLink", "")


def update_event(ms_id, ev, people):
    got = graph("PATCH", f"{base()}/events/{ms_id}", event_body(ev, people))
    return got.get("webLink", "")


def cancel_event(ms_id, comment=""):
    graph("POST", f"{base()}/events/{ms_id}/cancel", {"comment": comment[:500]})


def responses(ms_id):
    """{email: response} for an event: accepted, tentativelyAccepted, declined, none, notResponded, organizer."""
    got = graph("GET", f"{base()}/events/{ms_id}", params={"$select": "attendees,webLink,isCancelled"})
    return ({(a.get("emailAddress", {}).get("address") or "").lower(): a.get("status", {}).get("response", "none")
             for a in got.get("attendees", [])}, got.get("webLink", ""), bool(got.get("isCancelled")))


def free_busy(emails, start, end):
    """{email: 'free' | 'busy' | 'unknown'} for the window (local ISO times)."""
    tz = timezone()
    got = graph("POST", "/me/calendar/getSchedule", {
        "schedules": emails[:20], "startTime": {"dateTime": start[:16] + ":00", "timeZone": tz},
        "endTime": {"dateTime": end[:16] + ":00", "timeZone": tz}, "availabilityViewInterval": 15})
    out = {}
    for s in got.get("value", []):
        em = (s.get("scheduleId") or "").lower()
        if s.get("error"):
            out[em] = "unknown"
        else:
            busy = [i for i in s.get("scheduleItems", []) if i.get("status") in ("busy", "oof", "tentative")]
            out[em] = "busy" if busy else "free"
    return out


def directory(q):
    q = q.replace('"', "").strip()
    if len(q) < 2:
        return []
    got = graph("GET", "/users", params={"$search": f'"displayName:{q}" OR "mail:{q}"', "$top": "8",
                                         "$select": "displayName,mail,jobTitle"}, headers={"ConsistencyLevel": "eventual"})
    return [{"name": u.get("displayName") or "", "email": u["mail"], "role": u.get("jobTitle") or "", "source": "Microsoft 365"}
            for u in got.get("value", []) if u.get("mail")]


def check_mailbox():
    """Can the signed-in person write to the send-from calendar? Returns an error sentence or ''."""
    try:
        graph("GET", f"{base()}/calendar", params={"$select": "name,canEdit"})
        return ""
    except MSError as e:
        return str(e)
