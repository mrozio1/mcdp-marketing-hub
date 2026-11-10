"""Canva: bring the team's existing designs into the Studio.

An admin creates an integration in Canva's developer portal (scopes: design:meta:read, design:content:read,
profile:read), pastes its client ID and secret on the Connections page and signs in once. The import then
reads every design the signed-in person can see, exports each one (PNG for one page, PDF for several, MP4 for
video-only designs), files it under the brand whose name it matches, and leaves the rest in a "needs a brand"
queue. Running it again picks up new designs and re-exports ones changed in Canva since.

Canva hands back flattened files; editing a design's layers still happens in Canva (the item links back).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db, studio

AUTHORIZE = "https://www.canva.com/api/oauth/authorize"
API = "https://api.canva.com/rest/v1"
SCOPES = "design:meta:read design:content:read profile:read"
EXPORT_GAP = 4.0               # seconds between export jobs (Canva limits how fast they can be started)
MAX_DOWNLOAD = 2 * 1024 ** 3
_tok_lock = threading.Lock()
_token = {}
_job_lock = threading.Lock()
STOP_WORDS = {"orthodontics", "orthodontic", "orthodontist", "ortho", "dental", "dentistry", "dentist", "the", "and",
              "of", "smiles", "smile", "pc", "llc", "family", "care", "center", "group", "pediatric", "kids", "for",
              "design", "post", "story", "copy", "untitled", "new", "a", "to", "in", "on", "at", "dr", "dds"}


class CanvaError(RuntimeError):
    pass


# ------------------------------------------------------------------ settings
def configured():
    return bool(db.get_setting("canva_client_id") and db.get_setting("canva_client_secret"))


def connected():
    return bool(db.get_setting("canva_refresh_token"))


def settings():
    return {"client_id": db.get_setting("canva_client_id") or "", "secret": bool(db.get_setting("canva_client_secret"))}


def user():
    try:
        return json.loads(db.get_setting("canva_user") or "{}")
    except ValueError:
        return {}


def save(client_id, secret):
    db.set_setting("canva_client_id", (client_id or "").strip() or None)
    if secret:
        db.set_setting("canva_client_secret", secret.strip())
    _token.clear()


def disconnect():
    for k in ("canva_refresh_token", "canva_user"):
        db.set_setting(k, None)
    _token.clear()


# ------------------------------------------------------------------ sign-in (OAuth with PKCE)
def pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(redirect_uri, state, challenge):
    return AUTHORIZE + "?" + urllib.parse.urlencode({
        "code_challenge": challenge, "code_challenge_method": "s256", "scope": SCOPES, "response_type": "code",
        "client_id": db.get_setting("canva_client_id"), "state": state, "redirect_uri": redirect_uri})


def _token_post(fields):
    cid, sec = db.get_setting("canva_client_id") or "", db.get_setting("canva_client_secret") or ""
    basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    req = urllib.request.Request(API + "/oauth/token", data=urllib.parse.urlencode(fields).encode(), headers={
        "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            j = json.loads(raw)
            msg = j.get("error_description") or j.get("message") or j.get("error") or raw[:200]
        except ValueError:
            msg = raw[:200]
        if e.code in (400, 401) and "invalid_grant" in raw:
            msg = "The Canva sign-in has expired. Sign in again on the Connections page."
        raise CanvaError(msg)
    except urllib.error.URLError as e:
        raise CanvaError(f"Couldn't reach Canva: {e.reason}")


def _store(tok):
    if tok.get("refresh_token"):              # Canva hands out a new refresh token each time; the old one stops working
        db.set_setting("canva_refresh_token", tok["refresh_token"])
    _token.update(access=tok["access_token"], until=time.time() + int(tok.get("expires_in", 14400)) - 120)


def exchange_code(code, redirect_uri, verifier):
    with _tok_lock:
        tok = _token_post({"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                           "redirect_uri": redirect_uri})
        _store(tok)
    try:
        me = call("GET", "/users/me/profile")
        name = (me.get("profile") or {}).get("display_name") or ""
    except CanvaError:
        name = ""
    db.set_setting("canva_user", json.dumps({"name": name}))
    return name


def _access(force=False):
    with _tok_lock:
        if not force and _token.get("access") and time.time() < _token.get("until", 0):
            return _token["access"]
        rt = db.get_setting("canva_refresh_token")
        if not rt:
            raise CanvaError("Canva isn't connected. An admin signs in on the Connections page.")
        tok = _token_post({"grant_type": "refresh_token", "refresh_token": rt})
        _store(tok)
        return tok["access_token"]


def call(method, path, body=None, params=None):
    for attempt in range(4):
        tok = _access(force=attempt == 1)
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {tok}", "Accept": "application/json",
            **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            if e.code == 401 and attempt == 0:
                continue
            if e.code == 429 and attempt < 3:
                time.sleep(int(e.headers.get("Retry-After") or 30))
                continue
            try:
                j = json.loads(raw)
                msg = j.get("message") or j.get("code") or raw[:200]
            except ValueError:
                msg = raw[:200]
            raise CanvaError(f"{msg} (HTTP {e.code})")
        except urllib.error.URLError as e:
            raise CanvaError(f"Couldn't reach Canva: {e.reason}")
    raise CanvaError("Canva is busy; try again in a few minutes.")


# ------------------------------------------------------------------ designs and exports
def designs(limit=5000):
    out, cont = [], None
    while len(out) < limit:
        params = {"ownership": "any", "sort_by": "modified_descending"}
        if cont:
            params["continuation"] = cont
        got = call("GET", "/designs", params=params)
        out += got.get("items", [])
        cont = got.get("continuation")
        if not cont:
            break
    return out[:limit]


def formats(design_id):
    try:
        got = call("GET", f"/designs/{design_id}/export-formats")
        return set((got.get("formats") or {}).keys())
    except CanvaError:
        return {"png", "pdf"}


def export(design_id, fmt, timeout=600, quality=None):
    """Run a Canva export job; returns the download links (one per page for images)."""
    body = {"design_id": design_id, "format": {"type": fmt}}
    if fmt == "mp4":
        body["format"]["quality"] = quality or "horizontal_1080p"
    job = call("POST", "/exports", body).get("job", {})
    end = time.time() + timeout
    wait = 2
    while job.get("status") == "in_progress" and time.time() < end:
        time.sleep(wait)
        wait = min(wait * 1.5, 10)
        job = call("GET", f"/exports/{job['id']}").get("job", {})
    if job.get("status") != "success":
        err = (job.get("error") or {}).get("message") or ("took too long" if job.get("status") == "in_progress" else job.get("status"))
        raise CanvaError(f"Canva couldn't export it as {fmt.upper()}: {err}")
    return job.get("urls") or []


def mp4_quality(w, h):
    """Canva wants a resolution for MP4s; match the design's shape."""
    return "vertical_1080p" if (h or 9) > (w or 16) * 1.1 else "horizontal_1080p"


def _download(url, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix, dir=studio._dir("tmp"))
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=300) as r, os.fdopen(fd, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise CanvaError("That export is over 2 GB.")
                f.write(chunk)
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return path


def _fetch_bytes(url, limit=20 * 1024 * 1024):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read(limit)
    except Exception:
        return None


# ------------------------------------------------------------------ filing by brand
def _words(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower())) - STOP_WORDS


def suggest_brand(title, plist):
    """The practice whose name best matches a design's title; None when it's unclear."""
    words = _words(title)
    if not words:
        return None
    scored = []
    for p in plist:
        names = [p["name"]] + [x for x in (p.get("campaign_filter") or "").split(",") if x.strip()]
        best = max((len(words & _words(n)) for n in names), default=0)
        if best:
            scored.append((best, p["id"]))
    if re.search(r"\bmcdp\b|michigan center", (title or "").lower()):
        scored.append((2, studio.MCDP))
    scored.sort(reverse=True)
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][1]


# ------------------------------------------------------------------ the import job
def status():
    try:
        return json.loads(db.get_setting("canva_import") or "{}")
    except ValueError:
        return {}


def _note(**kw):
    s = status()
    s.update(kw)
    db.set_setting("canva_import", json.dumps(s))


def running():
    s = status()
    return s.get("state") == "running" and time.time() - s.get("beat", 0) < 15 * 60


def _existing():
    with db.conn() as c:
        return {r["canva_id"]: dict(r) for r in c.execute(
            "SELECT id, canva_id, canva_updated, trashed_at FROM studio_items WHERE canva_id IS NOT NULL")}


def _bring_in(d, plist, by, have):
    """Export one design and store (or refresh) it. Returns 'new', 'updated' or 'same'."""
    did, title = d["id"], (d.get("title") or "").strip() or "Untitled design"
    updated = str(d.get("updated_at") or "")
    old = have.get(did)
    if old and (old["trashed_at"] or old["canva_updated"] == updated):
        return "same"
    pages = d.get("page_count") or 1
    fm = formats(did)
    if pages > 1 and "pdf" in fm:
        fmt, ext = "pdf", ".pdf"
    elif "png" in fm:
        fmt, ext = "png", ".png"
    elif "mp4" in fm:
        fmt, ext = "mp4", ".mp4"
    else:
        fmt, ext = "pdf", ".pdf"
    t = d.get("thumbnail") or {}
    urls = export(did, fmt, quality=mp4_quality(t.get("width"), t.get("height")))
    if not urls:
        raise CanvaError("Canva sent no file.")
    path = _download(urls[0], ext)
    thumb = None
    if (d.get("thumbnail") or {}).get("url"):
        thumb = _fetch_bytes(d["thumbnail"]["url"])
    mime = {"pdf": "application/pdf", "png": "image/png", "mp4": "video/mp4"}[fmt]
    if old:
        studio.replace_file(old["id"], path, title + ext, mime, thumb, by="Canva import", note="Updated in Canva")
        with studio.db.conn() as c:
            c.execute("UPDATE studio_items SET canva_updated=?, pages=? WHERE id=?", (updated, pages, old["id"]))
        return "updated"
    pid = suggest_brand(title, plist)
    folder_id = studio.canva_folder(pid) if pid is not None else None
    studio.add_file(path, title + ext, pid, folder_id, by=by, mime=mime, title=title, source="canva",
                    width=t.get("width") if fmt == "png" else None, height=t.get("height") if fmt == "png" else None,
                    thumb_bytes=thumb, pages=pages,
                    canva={"id": did, "url": (d.get("urls") or {}).get("edit_url") or (d.get("urls") or {}).get("view_url"),
                           "updated": updated})
    return "new"


def run_import(by="", limit=5000):
    if not _job_lock.acquire(blocking=False):
        return
    try:
        _note(state="running", started=db.now(), finished=None, total=0, done=0, new=0, updated=0, failed=0,
              unbranded=0, error="", errors=[], beat=time.time(), by=by)
        try:
            ds = designs(limit)
        except CanvaError as e:
            _note(state="error", error=str(e), finished=db.now())
            return
        plist = db.practices()
        have = _existing()
        todo = [d for d in ds if not (have.get(d["id"]) and (have[d["id"]]["trashed_at"]
                                                               or have[d["id"]]["canva_updated"] == str(d.get("updated_at") or "")))]
        _note(total=len(todo), seen=len(ds))
        counts = {"new": 0, "updated": 0, "failed": 0}
        errors = []
        for i, d in enumerate(todo):
            if status().get("stop"):
                break
            try:
                got = _bring_in(d, plist, by, have)
                if got in counts:
                    counts[got] += 1
            except Exception as e:
                counts["failed"] += 1
                if len(errors) < 8:
                    errors.append(f"{(d.get('title') or 'Untitled')[:60]}: {str(e)[:160]}")
            _note(done=i + 1, beat=time.time(), errors=errors, **counts)
            time.sleep(EXPORT_GAP)
        _note(state="done", finished=db.now(), stop=False, unbranded=studio.counts()["unbranded"])
    finally:
        _job_lock.release()


def start_import(by=""):
    if running():
        return False
    _note(stop=False)
    threading.Thread(target=run_import, args=(by,), daemon=True).start()
    return True


def stop_import():
    _note(stop=True)


def export_more(iid, fmt, by=""):
    """Another format of an imported design (a PowerPoint, an MP4), as its own file next to it."""
    it = studio.item(iid)
    if not it or not it.get("canva_id"):
        raise CanvaError("That file didn't come from Canva.")
    ext, mime, label = {"pptx": (".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "PowerPoint"),
                        "mp4": (".mp4", "video/mp4", "video"), "pdf": (".pdf", "application/pdf", "PDF"),
                        "png": (".png", "image/png", "picture")}[fmt]
    if fmt not in formats(it["canva_id"]):
        raise CanvaError(f"Canva can't export this design as {label}.")
    urls = export(it["canva_id"], fmt, quality=mp4_quality(it.get("width"), it.get("height")))
    if not urls:
        raise CanvaError("Canva sent no file.")
    path = _download(urls[0], ext)
    thumb = None
    tp = studio.thumb_path(iid)
    if os.path.exists(tp):
        with open(tp, "rb") as f:
            thumb = f.read()
    return studio.add_file(path, it["title"] + ext, it["practice_id"], it["folder_id"], by=by, mime=mime,
                           title=f"{it['title']} ({label})", source="canva", thumb_bytes=thumb,
                           status=it["status"], canva={"url": it.get("canva_url")})
