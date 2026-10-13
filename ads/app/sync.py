"""Nightly (and on-demand) pull of each practice's Meta + Google data into the normal analysis."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import connectors as cx
from . import db

_lock = threading.Lock()
# UTC hour for the nightly pull: 10:00 UTC is about 5 a.m. US Central, after Google and Meta
# have finished the previous day's numbers. (UTC avoids depending on time-zone data in the image.)
SYNC_HOUR_UTC = int(os.environ.get("ADS_SYNC_HOUR_UTC", "10"))


def sync_window(today=None):
    """Month to date, except in the first 3 days of a month: the whole previous month."""
    today = today or date.today()
    if today.day <= 3:
        last_month_end = today.replace(day=1) - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    return today.replace(day=1), today


def _parsed(rows, platform, level, since, until):
    return {"platform": platform, "level": level, "rows": rows,
            "date_start": since, "date_end": until, "warnings": []}


def _status(key):
    try:
        return json.loads(db.get_setting(key) or "{}")
    except ValueError:
        return {}


def last_sync():
    return _status("last_sync")


def refresh_account_lists():
    """Cache which ad accounts each connection can see (for the practice dropdowns)."""
    result = {}
    if cx.meta_configured():
        try:
            accts = cx.meta_accounts()
            db.set_setting("meta_accounts", json.dumps(accts))
            result["meta"] = {"ok": True, "count": len(accts)}
        except cx.ConnectorError as e:
            result["meta"] = {"ok": False, "error": str(e)}
    if cx.google_service_account() or (cx.google_oauth_configured() and db.get_setting("google_refresh_token")):
        try:
            accts = cx.GoogleClient(db.get_setting("google_refresh_token")).accounts()
            db.set_setting("google_accounts", json.dumps(accts))
            result["google"] = {"ok": True, "count": len(accts)}
        except cx.ConnectorError as e:
            result["google"] = {"ok": False, "error": str(e)}
    db.set_setting("accounts_checked", json.dumps({"at": db.now(), **result}))
    return result


def cached_accounts(platform):
    try:
        return json.loads(db.get_setting(f"{platform}_accounts") or "[]")
    except ValueError:
        return []


def _fetch(cache, key, fn):
    """Several practices can share one ad account (each with its own campaign filter);
    pull each account once per sync run. Errors are cached too, so they're reported per practice."""
    if cache is None:
        return fn()
    if key not in cache:
        try:
            cache[key] = ("ok", fn())
        except cx.ConnectorError as e:
            cache[key] = ("err", e)
    kind, val = cache[key]
    if kind == "err":
        raise val
    return val


def sync_practice(p, since, until, google=None, cache=None):
    """Returns {'meta': {...}, 'google': {...}} for one practice. Stores the whole account's rows;
    the practice's campaign filter is applied when the page reads them."""
    out = {}
    if p.get("meta_account_id") and cx.meta_configured():
        try:
            acct = p["meta_account_id"]
            rows = _fetch(cache, ("meta", acct), lambda: cx.meta_campaign_rows(acct, since, until))
            db.save_upload(p["id"], _parsed(rows, "meta", "campaign", since, until),
                           f"Synced from Meta ({len(rows)} campaign-days)", source="sync")
            db.prune_synced(p["id"], "meta", "campaign")
            out["meta"] = {"ok": True, "rows": len(rows)}
        except cx.ConnectorError as e:
            out["meta"] = {"ok": False, "error": str(e)}
    if p.get("google_customer_id") and google is not None:
        try:
            cid = p["google_customer_id"]
            rows = _fetch(cache, ("google", cid), lambda: google.campaign_rows(cid, since, until))
            db.save_upload(p["id"], _parsed(rows, "google", "campaign", since, until),
                           f"Synced from Google Ads ({len(rows)} campaign-days)", source="sync")
            db.prune_synced(p["id"], "google", "campaign")
            terms = _fetch(cache, ("google-terms", cid), lambda: google.search_term_rows(cid, since, until))
            if terms:
                db.save_upload(p["id"], _parsed(terms, "google", "search_term", since, until),
                               f"Synced from Google Ads ({len(terms)} search terms)", source="sync")
                db.prune_synced(p["id"], "google", "search_term")
            out["google"] = {"ok": True, "rows": len(rows), "terms": len(terms)}
        except cx.ConnectorError as e:
            out["google"] = {"ok": False, "error": str(e)}
    return out


def sync_all(today=None):
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    try:
        since, until = sync_window(today)
        google, google_error = None, None
        practices = db.practices()
        if cx.google_configured() and any(p.get("google_customer_id") for p in practices):
            try:
                google = cx.GoogleClient(db.get_setting("google_refresh_token"))
            except cx.ConnectorError as e:
                google_error = str(e)
        results, cache = {}, {}
        for p in practices:
            r = sync_practice(p, since, until, google, cache)
            if google_error and p.get("google_customer_id"):
                r["google"] = {"ok": False, "error": google_error}
            if r:
                results[str(p["id"])] = {"name": p["name"], **r}
        status = {"at": db.now(), "since": since.isoformat(), "until": until.isoformat(), "results": results}
        db.set_setting("last_sync", json.dumps(status))
        return status
    finally:
        _lock.release()


def _seconds_until(hour):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def start_scheduler():
    def loop():
        # Fill the account dropdowns soon after start-up (e.g. right after keys are saved).
        try:
            refresh_account_lists()
        except Exception:
            pass
        while True:
            time.sleep(_seconds_until(SYNC_HOUR_UTC))
            try:
                refresh_account_lists()
                sync_all()
            except Exception as e:  # never let the scheduler die
                db.set_setting("last_sync", json.dumps({"at": db.now(), "error": str(e)}))
    threading.Thread(target=loop, name="nightly-sync", daemon=True).start()
