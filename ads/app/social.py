"""Posting from the Studio through Social (Postiz), as the brand.

Social already holds every Facebook Page, Instagram account and Google profile connection and does the
scheduling and publishing, so the Studio hands it finished posts through its public API:
upload the file, then create one post per channel (now, at a time, or as a draft). Posts show up in
Social's calendar like any other, and their state (queued, published, failed, link) is read back.

The API key is read from Social's own database (Settings → Public API there); an admin can paste one on
Connections instead.
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db

DEFAULT_URL = "http://postiz:5000/api/public/v1"
PROVIDERS = {"facebook": "Facebook", "instagram": "Instagram", "instagram-standalone": "Instagram", "gmb": "Google profile"}
GMB_ACTIONS = (("NONE", "No button"), ("BOOK", "Book"), ("CALL", "Call now"), ("LEARN_MORE", "Learn more"),
               ("SIGN_UP", "Sign up"), ("GET_OFFER", "Get offer"))
_cache = {}


class SocialError(RuntimeError):
    pass


def base_url():
    return (db.get_setting("postiz_api_url") or os.environ.get("POSTIZ_API_URL") or DEFAULT_URL).rstrip("/")


def _db_key():
    url = os.environ.get("POSTIZ_DATABASE_URL", "")
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=5) as c:
            row = c.execute(
                'SELECT o."apiKey" FROM "Organization" o WHERE o."deletedAt" IS NULL AND o."apiKey" IS NOT NULL '
                'ORDER BY (SELECT COUNT(*) FROM "Integration" i WHERE i."organizationId" = o."id" '
                'AND i."deletedAt" IS NULL) DESC LIMIT 1').fetchone()
        return row[0] if row else None
    except Exception:
        return None


def api_key():
    return db.get_setting("postiz_api_key") or _db_key()


def key_source():
    if db.get_setting("postiz_api_key"):
        return "pasted"
    return "social" if _db_key() else None


def configured():
    return bool(api_key())


def save(key=None, url=None):
    if key is not None:
        db.set_setting("postiz_api_key", key.strip() or None)
    if url is not None:
        db.set_setting("postiz_api_url", url.strip().rstrip("/") or None)
    _cache.clear()


def _req(method, path, body=None, data=None, headers=None, timeout=120):
    key = api_key()
    if not key:
        raise SocialError("Posting isn't set up: Social's API key wasn't found. An admin can paste it on Connections → "
                          "Posting (in Social: Settings → Public API).")
    h = {"Authorization": key, "Accept": "application/json", **(headers or {})}
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(base_url() + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            j = json.loads(raw)
            msg = j.get("msg") or j.get("message") or j.get("error") or raw[:200]
            if isinstance(msg, list):
                msg = "; ".join(map(str, msg))
            if isinstance(j.get("error"), str) and j.get("provider"):
                msg = f"{j.get('name') or j['provider']}: {j['error']}"
        except ValueError:
            msg = raw[:200]
        if e.code in (401, 403):
            msg = "Social didn't accept the API key. Check it under Connections → Posting."
        elif e.code == 429:
            msg = "Social's posting limit for this hour was reached. Try again in a while."
        raise SocialError(f"{msg} (HTTP {e.code})" if e.code not in (401, 403, 429) else msg)
    except urllib.error.URLError as e:
        raise SocialError(f"Couldn't reach Social: {e.reason}")


def check():
    _req("GET", "/is-connected")
    return True


def integrations(fresh=False):
    """Social's channels the Studio can post to: [{id, name, provider, label, picture, internal_id, customer}]."""
    hit = _cache.get("integrations")
    if hit and not fresh and hit[1] > time.time():
        return hit[0]
    rows = _req("GET", "/integrations")
    out = []
    internal = {}
    try:
        from . import postiz
        internal = {r["id"]: r for r in postiz.integrations_by_id([r.get("id") for r in rows])}
    except Exception:
        pass
    for r in rows:
        prov = r.get("identifier") or ""
        if prov not in PROVIDERS or r.get("disabled"):
            continue
        meta = internal.get(r["id"], {})
        out.append({"id": r["id"], "name": r.get("name") or "", "provider": prov, "label": PROVIDERS[prov],
                    "picture": r.get("picture") or "", "internal_id": meta.get("internal_id", ""),
                    "customer": (r.get("customer") or {}).get("name") or meta.get("customer", "")})
    out.sort(key=lambda x: (x["label"], x["name"].lower()))
    _cache["integrations"] = (out, time.time() + 300)
    return out


def for_brand(pid, chans):
    """The channels that belong to a practice: its linked Page and Instagram, plus channels picked for it before."""
    if pid in (None, 0):
        picked = set(_picked(0))
        return [c for c in chans if c["id"] in picked]
    p = db.practice(pid) or {}
    fb = set(db.social_ids(p, "fb")) if p else set()
    ig = set(db.social_ids(p, "ig")) if p else set()
    picked = set(_picked(pid))
    out = []
    for c in chans:
        if c["id"] in picked or (c["provider"] == "facebook" and c["internal_id"] in fb) or \
                (c["provider"].startswith("instagram") and c["internal_id"] in ig):
            out.append(c)
    return out


def _picked(pid):
    try:
        return json.loads(db.get_setting(f"studio_channels_{pid}") or "[]")
    except ValueError:
        return []


def remember(pid, ids):
    db.set_setting(f"studio_channels_{pid if pid is not None else 0}", json.dumps(sorted(set(ids))[:50]))


def upload(path, filename, mime=""):
    boundary = "----mcdp" + secrets.token_hex(12)
    mime = mime or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        content = f.read()
    name = filename.replace('"', "")
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    got = _req("POST", "/upload", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
               timeout=600)
    if not got.get("id") or not got.get("path"):
        raise SocialError("Social didn't take the file.")
    return {"id": got["id"], "path": got["path"]}


def settings_for(provider, opts):
    if provider.startswith("instagram"):
        return {"__type": provider, "post_type": "story" if opts.get("ig_story") else "post", "collaborators": []}
    if provider == "facebook":
        return {"__type": "facebook"}
    if provider == "gmb":
        s = {"__type": "gmb", "topicType": "STANDARD"}
        cta = opts.get("gmb_cta") or "NONE"
        if cta != "NONE":
            s["callToActionType"] = cta
            if cta != "CALL":
                s["callToActionUrl"] = opts.get("gmb_url") or ""
        return s
    return {"__type": provider}


def create(kind, when_iso, items):
    """kind: 'now', 'schedule' or 'draft'. items: [{integration, provider, content, media: [{id, path}], opts}].
    Returns [{postId, integration}]."""
    body = {"type": kind, "date": when_iso, "shortLink": False, "tags": [], "posts": [
        {"integration": {"id": it["integration"]},
         "value": [{"content": it["content"], "image": it["media"]}],
         "settings": settings_for(it["provider"], it.get("opts") or {})} for it in items]}
    got = _req("POST", "/posts", body)
    return got if isinstance(got, list) else []


def posts_between(start_iso, end_iso):
    got = _req("GET", "/posts?" + urllib.parse.urlencode({"startDate": start_iso, "endDate": end_iso}))
    return got.get("posts", []) if isinstance(got, dict) else []


def delete(post_id):
    try:
        _req("DELETE", f"/posts/{urllib.parse.quote(post_id)}")
    except SocialError as e:
        if "HTTP 404" not in str(e):
            raise
