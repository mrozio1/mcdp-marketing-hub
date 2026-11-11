"""Stock footage and music for the Studio video editor, searched inside the hub.

- Pixabay videos, through Pixabay's official API (free key). Searches are kept for 24 hours as Pixabay asks;
  a picked video is downloaded into the library (Pixabay doesn't allow hotlinking for good), with the credit.
- Freesound music and sounds, through Freesound's official API (free key), limited to Creative Commons 0 (free
  for any use, no credit needed). A picked track is saved to the shared Music folder.
- Pixabay's API has no music, so music from pixabay.com is added by dropping the downloaded file into the
  shared Music folder, where it's searchable like the rest.

The shared Music folder sits under MCDP in the library and is offered to every brand's videos.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db, studio

PIXABAY = "https://pixabay.com/api/videos/"
FREESOUND = "https://freesound.org/apiv2"
CACHE_S = 24 * 3600
MAX_DL = 400 * 1024 * 1024


class StockError(RuntimeError):
    def __init__(self, msg, blocked=False):
        super().__init__(msg)
        self.blocked = blocked          # the service's firewall turned the hub away: the browser can do it instead


def keys():
    return {"pixabay": bool(db.get_setting("pixabay_key")), "freesound": bool(db.get_setting("freesound_key"))}


def _tidy_key(k):
    """Keys pasted with spaces, quotes, or as part of a link ("...?key=123-abc&q=...")."""
    k = (k or "").strip().strip("'\"").strip()
    m = re.search(r"(?:key|token)=([^&\s]+)", k)
    return (m.group(1) if m else k).strip() or None


def save_keys(pixabay=None, freesound=None):
    if pixabay is not None:
        db.set_setting("pixabay_key", _tidy_key(pixabay))
    if freesound is not None:
        db.set_setting("freesound_key", _tidy_key(freesound))


UA = "Mozilla/5.0 (compatible; MCDP-Hub/1.0; +https://ads.mcdp.com)"
HEADERS = {"Accept": "application/json, */*;q=0.8", "Accept-Language": "en-US,en;q=0.9", "User-Agent": UA}


def _blocked(code, body):
    """A firewall page (Cloudflare and the like), not an answer from the service itself."""
    b = (body or "").lower()
    return code in (403, 503) and ("<html" in b or "cloudflare" in b or "error code: 10" in b or "attention required" in b or not b.strip())


def _curl(url, timeout, out_path=None):
    """The same request through curl, which firewalls that turn away Python's own client normally accept."""
    exe = shutil.which("curl")
    if not exe:
        return None
    args = [exe, "-sS", "-L", "--max-time", str(timeout), "-A", UA, "-H", "Accept: application/json, */*;q=0.8"]
    args += ["-o", out_path, "-w", "%{http_code}", url] if out_path else ["-w", "\n%{http_code}", url]
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout + 10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = r.stdout.decode(errors="replace")
    if out_path:
        return int(text.strip() or 0), ""
    body, _, code = text.rpartition("\n")
    return int(code.strip() or 0), body


def _explain(code, body, service):
    b = (body or "")[:300]
    if code == 429:
        return StockError("Too many searches in a minute; wait a moment and try again.")
    if code in (400, 401) or "invalid" in b.lower() and "key" in b.lower():
        return StockError(f"{service} says the key isn't right (HTTP {code}). Copy it again from your {service} account and paste it "
                          f"on Connections → Stock and music.")
    if _blocked(code, body):
        return StockError(f"{service}'s firewall turned the hub away (HTTP {code}), so this isn't the key.", blocked=True)
    return StockError(f"{service} didn't answer the search (HTTP {code}): {re.sub(r'<[^>]+>', ' ', b).strip()[:160]}")


def _get(url, timeout=30, service="The service"):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        raise StockError(f"Couldn't reach {service}: {e.reason}", blocked=True)
    if _blocked(code, body):
        got = _curl(url, timeout)
        if got:
            code, body = got
            if code == 200:
                try:
                    return json.loads(body)
                except ValueError:
                    pass
    raise _explain(code, body, service)


def _cache(key, fn):
    ck = "stock_cache_" + re.sub(r"[^a-z0-9]", "_", key.lower())[:120]
    try:
        hit = json.loads(db.get_setting(ck) or "null")
    except ValueError:
        hit = None
    if hit and hit.get("at", 0) > time.time() - CACHE_S:
        return hit["data"]
    data = fn()
    db.set_setting(ck, json.dumps({"at": time.time(), "data": data}))
    return data


# ------------------------------------------------------------------ Pixabay videos
def pixabay_search(q, orientation="", page=1):
    key = _tidy_key(db.get_setting("pixabay_key"))
    if not key:
        raise StockError("Stock video search needs a free Pixabay key. An admin adds it on Connections → Stock and music.")
    q = re.sub(r"\s+", " ", q or "").strip()[:100]
    page = max(1, min(10, int(page or 1)))

    def fetch():
        params = {"key": key, "q": q, "safesearch": "true", "per_page": 30, "page": page, "video_type": "film"}
        got = _get(PIXABAY + "?" + urllib.parse.urlencode(params), service="Pixabay")
        return {"hits": [x for x in map(pixabay_hit, got.get("hits", [])) if x], "total": got.get("totalHits", 0)}
    data = _cache(f"pxv_{q}_{page}", fetch)
    if orientation in ("tall", "wide"):
        data = {**data, "hits": [x for x in data["hits"] if (x["file"]["h"] or 0) > (x["file"]["w"] or 0)] if orientation == "tall"
                else [x for x in data["hits"] if (x["file"]["w"] or 0) >= (x["file"]["h"] or 0)]}
    return data


def _pixabay_url(u):
    """Only Pixabay's own addresses are fetched for a hit (a browser-sent hit can't point the hub elsewhere)."""
    try:
        pu = urllib.parse.urlparse(str(u or ""))
    except ValueError:
        return ""
    host = (pu.hostname or "").lower()
    return str(u) if pu.scheme == "https" and (host == "pixabay.com" or host.endswith(".pixabay.com")) else ""


def pixabay_hit(h):
    """One video from Pixabay's API answer, as the editor uses it: the largest file up to 1920 wide, a still and a preview."""
    if not isinstance(h, dict):
        return None
    vids = h.get("videos") or {}
    best = None
    for size in ("large", "medium", "small"):
        v = vids.get(size) or {}
        if _pixabay_url(v.get("url")) and 0 < int(v.get("width") or 0) <= 1920:
            best = {"url": v["url"], "w": int(v.get("width") or 0), "h": int(v.get("height") or 0), "size": v.get("size")}
            break
    if not best:
        return None
    tiny = vids.get("tiny") or vids.get("small") or {}
    try:
        hid = int(h.get("id"))
    except (TypeError, ValueError):
        return None
    return {"id": hid, "duration": h.get("duration"), "tags": str(h.get("tags", ""))[:300], "user": str(h.get("user", ""))[:100],
            "page": _pixabay_url(h.get("pageURL")), "thumb": _pixabay_url((vids.get("medium") or {}).get("thumbnail") or tiny.get("thumbnail")),
            "preview": _pixabay_url(tiny.get("url")), "file": best}


def _download(url, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix, dir=studio._dir("tmp"))
    total = 0
    req = urllib.request.Request(url, headers={**HEADERS, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r, os.fdopen(fd, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DL:
                    raise StockError("That file is too big to bring in.")
                f.write(chunk)
    except StockError:
        os.remove(path)
        raise
    except urllib.error.HTTPError as e:
        got = _curl(url, 300, out_path=path) if e.code in (403, 503) else None
        if got and got[0] == 200 and os.path.getsize(path) <= MAX_DL:
            return path
        os.remove(path)
        raise StockError(f"Couldn't download it (HTTP {e.code}).", blocked=e.code in (403, 503))
    except urllib.error.URLError as e:
        os.remove(path)
        raise StockError(f"Couldn't download it: {e.reason}", blocked=True)
    except Exception as e:
        os.remove(path)
        raise StockError(f"Couldn't download it: {e}")
    return path


def stock_folder(pid):
    return studio.add_folder(pid, "Stock video", "Pixabay")


def _have(hit_id):
    with db.conn() as c:
        r = c.execute("SELECT id FROM studio_items WHERE canva_id=? AND trashed_at IS NULL", (f"pixabay:{int(hit_id)}",)).fetchone()
    return r[0] if r else None


def _credit(iid, hit, by):
    title = (hit["tags"].split(",")[0].strip() or "Stock video").capitalize()
    studio.update(iid, by, title=f"{title} (Pixabay)",
                  notes=f"Video by {hit['user']} on Pixabay ({hit['page']}). Free to use under the Pixabay Content License.")
    with db.conn() as c:
        c.execute("UPDATE studio_items SET source='pixabay', canva_id=?, canva_url=? WHERE id=?", (f"pixabay:{hit['id']}", hit["page"], iid))


def pixabay_import(hit_id, q, page, pid, by="", raw=None):
    """Save a Pixabay video into the brand's 'Stock video' folder (once) and return its library id.
    raw: the hit as the browser got it from Pixabay, when the hub itself is turned away by Pixabay's firewall."""
    have = _have(hit_id)
    if have:
        return have
    hit = pixabay_hit(raw) if raw else None
    if not hit or str(hit["id"]) != str(hit_id):
        data = pixabay_search(q, "", page)
        hit = next((h for h in data["hits"] if str(h["id"]) == str(hit_id)), None)
    if not hit:
        raise StockError("That video is no longer in the search results; search again.")
    path = _download(hit["file"]["url"], ".mp4")
    title = (hit["tags"].split(",")[0].strip() or "Stock video").capitalize()
    iid = studio.add_file(path, f"{title}.mp4", pid, stock_folder(pid), by=by,
                          mime="video/mp4", title=f"{title} (Pixabay)", source="pixabay", width=hit["file"]["w"],
                          height=hit["file"]["h"], duration=hit["duration"], canva={"id": f"pixabay:{int(hit_id)}", "url": hit["page"]})
    _credit(iid, hit, by)
    return iid


def pixabay_attach(item_id, raw, by=""):
    """A Pixabay video the browser downloaded and uploaded itself: give it the credit and remember where it came from."""
    hit = pixabay_hit(raw)
    if not hit:
        raise StockError("That doesn't look like a Pixabay video.")
    have = _have(hit["id"])
    if have and have != item_id:
        studio.trash(item_id, by)
        return have
    _credit(item_id, hit, by)
    return item_id


# ------------------------------------------------------------------ music: Freesound (CC0) and the shared Music folder
def music_folder():
    return studio.add_folder(studio.MCDP, "Music", "Studio")


def freesound_search(q, page=1):
    key = _tidy_key(db.get_setting("freesound_key"))
    if not key:
        raise StockError("Music search needs a free Freesound key. An admin adds it on Connections → Stock and music.")
    q = re.sub(r"\s+", " ", q or "").strip()[:100] or "music"
    page = max(1, min(10, int(page or 1)))

    def fetch():
        params = {"query": q, "filter": 'license:"Creative Commons 0" duration:[15 TO 600]', "page": page, "page_size": 20,
                  "fields": "id,name,duration,username,tags,previews,url,avg_rating", "sort": "rating_desc", "token": key}
        got = _get(f"{FREESOUND}/search/text/?" + urllib.parse.urlencode(params), service="Freesound")
        out = []
        for r in got.get("results", []):
            pv = r.get("previews") or {}
            mp3 = pv.get("preview-hq-mp3") or pv.get("preview-lq-mp3")
            if not mp3:
                continue
            out.append({"id": r["id"], "name": r.get("name", "")[:100], "duration": r.get("duration"), "user": r.get("username", ""),
                        "tags": ", ".join((r.get("tags") or [])[:6]), "preview": mp3, "page": r.get("url", "")})
        return {"hits": out, "total": got.get("count", 0)}
    return _cache(f"fs_{q}_{page}", fetch)


def freesound_import(sound_id, q, page, by=""):
    with db.conn() as c:
        r = c.execute("SELECT id FROM studio_items WHERE canva_id=? AND trashed_at IS NULL", (f"freesound:{int(sound_id)}",)).fetchone()
    if r:
        return r[0]
    data = freesound_search(q, page)
    hit = next((h for h in data["hits"] if str(h["id"]) == str(sound_id)), None)
    if not hit:
        raise StockError("That track is no longer in the search results; search again.")
    key = db.get_setting("freesound_key") or ""
    url = hit["preview"] + ("&" if "?" in hit["preview"] else "?") + urllib.parse.urlencode({"token": key})
    path = _download(url, ".mp3")
    name = re.sub(r"\.(wav|mp3|flac|ogg|aiff?)$", "", hit["name"], flags=re.I).strip() or "Track"
    iid = studio.add_file(path, f"{name}.mp3", studio.MCDP, music_folder(), by=by, mime="audio/mpeg", title=name,
                          source="freesound", duration=hit["duration"], canva={"id": f"freesound:{int(sound_id)}", "url": hit["page"]})
    studio.update(iid, by, notes=f"“{hit['name']}” by {hit['user']} on Freesound ({hit['page']}). Creative Commons 0: free for any use. "
                                 f"Tags: {hit['tags']}")
    return iid


def music_library(q=""):
    """Every sound in the shared Music folder (and any brand's), newest first, filtered by words."""
    rows = [it for it in studio.items(all_brands=True, q=q, limit=500) if it["kind"] == "audio"]
    return [{"id": it["id"], "title": it["title"], "duration": it["duration"], "notes": it["notes"][:200],
             "source": it["source"], "src": f"/studio/media/{it['id']}"} for it in rows]
