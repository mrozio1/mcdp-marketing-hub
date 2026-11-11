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
    pass


def keys():
    return {"pixabay": bool(db.get_setting("pixabay_key")), "freesound": bool(db.get_setting("freesound_key"))}


def save_keys(pixabay=None, freesound=None):
    if pixabay is not None:
        db.set_setting("pixabay_key", pixabay.strip() or None)
    if freesound is not None:
        db.set_setting("freesound_key", freesound.strip() or None)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "MCDP-Hub/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")[:200]
        if e.code in (400, 401, 403):
            raise StockError(f"The service didn't accept the key (HTTP {e.code}). Check it on Connections → Stock and music.")
        if e.code == 429:
            raise StockError("Too many searches in a minute; wait a moment and try again.")
        raise StockError(f"The search didn't work (HTTP {e.code}): {raw}")
    except urllib.error.URLError as e:
        raise StockError(f"Couldn't reach the service: {e.reason}")


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
    key = db.get_setting("pixabay_key")
    if not key:
        raise StockError("Stock video search needs a free Pixabay key. An admin adds it on Connections → Stock and music.")
    q = re.sub(r"\s+", " ", q or "").strip()[:100]
    page = max(1, min(10, int(page or 1)))

    def fetch():
        params = {"key": key, "q": q, "safesearch": "true", "per_page": 30, "page": page, "video_type": "film"}
        got = _get(PIXABAY + "?" + urllib.parse.urlencode(params))
        out = []
        for h in got.get("hits", []):
            vids = h.get("videos") or {}
            best = None
            for size in ("large", "medium", "small"):
                v = vids.get(size) or {}
                if v.get("url") and (v.get("width") or 0) <= 1920:
                    best = {"url": v["url"], "w": v.get("width"), "h": v.get("height"), "size": v.get("size")}
                    break
            if not best:
                continue
            tiny = vids.get("tiny") or vids.get("small") or {}
            out.append({"id": h.get("id"), "duration": h.get("duration"), "tags": h.get("tags", ""), "user": h.get("user", ""),
                        "page": h.get("pageURL", ""), "thumb": (vids.get("medium") or {}).get("thumbnail") or tiny.get("thumbnail", ""),
                        "preview": tiny.get("url", ""), "file": best})
        return {"hits": out, "total": got.get("totalHits", 0)}
    data = _cache(f"pxv_{q}_{page}", fetch)
    if orientation in ("tall", "wide"):
        data = {**data, "hits": [x for x in data["hits"] if (x["file"]["h"] or 0) > (x["file"]["w"] or 0)] if orientation == "tall"
                else [x for x in data["hits"] if (x["file"]["w"] or 0) >= (x["file"]["h"] or 0)]}
    return data


def _download(url, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix, dir=studio._dir("tmp"))
    total = 0
    req = urllib.request.Request(url, headers={"User-Agent": "MCDP-Hub/1.0"})
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
    except Exception as e:
        os.remove(path)
        raise StockError(f"Couldn't download it: {e}")
    return path


def pixabay_import(hit_id, q, page, pid, by=""):
    """Save a Pixabay video into the brand's 'Stock video' folder (once) and return its library id."""
    with db.conn() as c:
        r = c.execute("SELECT id FROM studio_items WHERE canva_id=? AND trashed_at IS NULL", (f"pixabay:{int(hit_id)}",)).fetchone()
    if r:
        return r[0]
    data = pixabay_search(q, "", page)
    hit = next((h for h in data["hits"] if str(h["id"]) == str(hit_id)), None)
    if not hit:
        raise StockError("That video is no longer in the search results; search again.")
    path = _download(hit["file"]["url"], ".mp4")
    title = (hit["tags"].split(",")[0].strip() or "Stock video").capitalize()
    iid = studio.add_file(path, f"{title}.mp4", pid, studio.add_folder(pid, "Stock video", "Pixabay"), by=by,
                          mime="video/mp4", title=f"{title} (Pixabay)", source="pixabay", width=hit["file"]["w"],
                          height=hit["file"]["h"], duration=hit["duration"], canva={"id": f"pixabay:{int(hit_id)}", "url": hit["page"]})
    studio.update(iid, by, notes=f"Video by {hit['user']} on Pixabay ({hit['page']}). Free to use under the Pixabay Content License.")
    return iid


# ------------------------------------------------------------------ music: Freesound (CC0) and the shared Music folder
def music_folder():
    return studio.add_folder(studio.MCDP, "Music", "Studio")


def freesound_search(q, page=1):
    key = db.get_setting("freesound_key")
    if not key:
        raise StockError("Music search needs a free Freesound key. An admin adds it on Connections → Stock and music.")
    q = re.sub(r"\s+", " ", q or "").strip()[:100] or "music"
    page = max(1, min(10, int(page or 1)))

    def fetch():
        params = {"query": q, "filter": 'license:"Creative Commons 0" duration:[15 TO 600]', "page": page, "page_size": 20,
                  "fields": "id,name,duration,username,tags,previews,url,avg_rating", "sort": "rating_desc", "token": key}
        got = _get(f"{FREESOUND}/search/text/?" + urllib.parse.urlencode(params))
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
             "source": it["source"], "src": f"/studio/f/{it['id']}"} for it in rows]
