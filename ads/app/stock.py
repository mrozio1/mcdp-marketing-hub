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
    return {"pixabay": bool(db.get_setting("pixabay_key")), "freesound": bool(db.get_setting("freesound_key")),
            "pexels": bool(db.get_setting("pexels_key"))}


def provider():
    """Which photo and video library the hub searches: Pexels when it has a key (Pixabay's bot wall turns away
    requests from other sites and from servers), otherwise Pixabay."""
    return "pexels" if _tidy_key(db.get_setting("pexels_key")) else "pixabay"


def _tidy_key(k):
    """Keys pasted with spaces, quotes, or as part of a link ("...?key=123-abc&q=...")."""
    k = (k or "").strip().strip("'\"").strip()
    m = re.search(r"(?:key|token)=([^&\s]+)", k)
    return (m.group(1) if m else k).strip() or None


def save_keys(pixabay=None, freesound=None, pexels=None):
    if pexels is not None:
        db.set_setting("pexels_key", _tidy_key(pexels))
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


def _curl(url, timeout, out_path=None, headers=None):
    """The same request through curl, which firewalls that turn away Python's own client normally accept."""
    exe = shutil.which("curl")
    if not exe:
        return None
    args = [exe, "-sS", "-L", "--max-time", str(timeout), "-A", UA, "-H", "Accept: application/json, */*;q=0.8"]
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
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


def _get(url, timeout=30, service="The service", headers=None):
    req = urllib.request.Request(url, headers={**HEADERS, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        raise StockError(f"Couldn't reach {service}: {e.reason}", blocked=True)
    if _blocked(code, body):
        got = _curl(url, timeout, headers=headers)
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


# ------------------------------------------------------------------ Pixabay photos and illustrations (the picture designer's templates)
PIXABAY_IMG = "https://pixabay.com/api/"
IMG_KINDS = {"photo": {"image_type": "photo"},
             "art": {"image_type": "illustration", "colors": "transparent"}}        # cut-out drawings: leaves, pumpkins…


def pixabay_image_hit(h):
    """One picture from Pixabay's API answer: a small preview, a web size and the large (up to 1280) file."""
    if not isinstance(h, dict):
        return None
    try:
        hid = int(h.get("id"))
    except (TypeError, ValueError):
        return None
    large = _pixabay_url(h.get("largeImageURL")) or _pixabay_url(h.get("webformatURL"))
    if not large:
        return None
    try:
        w, hh = int(h.get("imageWidth") or 0), int(h.get("imageHeight") or 0)
    except (TypeError, ValueError):
        w, hh = 0, 0
    return {"id": hid, "tags": str(h.get("tags", ""))[:300], "user": str(h.get("user", ""))[:100],
            "page": _pixabay_url(h.get("pageURL")), "thumb": _pixabay_url(h.get("previewURL")),
            "web": _pixabay_url(h.get("webformatURL")), "file": large, "w": w, "h": hh,
            "kind": "art" if str(h.get("type", "")) in ("illustration", "vector/svg", "vector") else "photo"}


def pixabay_images(q, kind="photo", orientation="", page=1):
    key = _tidy_key(db.get_setting("pixabay_key"))
    if not key:
        raise StockError("Stock photo search needs a free Pixabay key. An admin adds it on Connections → Stock and music.")
    q = re.sub(r"\s+", " ", q or "").strip()[:100]
    kind = kind if kind in IMG_KINDS else "photo"
    orientation = orientation if orientation in ("horizontal", "vertical") else "all"
    page = max(1, min(10, int(page or 1)))

    def fetch():
        params = {"key": key, "q": q, "safesearch": "true", "per_page": 30, "page": page, "orientation": orientation, **IMG_KINDS[kind]}
        got = _get(PIXABAY_IMG + "?" + urllib.parse.urlencode(params), service="Pixabay")
        return {"hits": [x for x in map(pixabay_image_hit, got.get("hits", [])) if x], "total": got.get("totalHits", 0)}
    return _cache(f"pxi_{kind}_{orientation}_{q}_{page}", fetch)


def photo_folder(pid):
    return studio.add_folder(pid, "Stock photos", "Pixabay")


def _have_image(hit_id, pid):
    with db.conn() as c:
        r = c.execute("SELECT id FROM studio_items WHERE canva_id=? AND practice_id IS ? AND trashed_at IS NULL",
                      (f"pixabay-img:{int(hit_id)}", pid)).fetchone()
    return r[0] if r else None


def _image_credit(iid, hit, by):
    what = "Illustration" if hit["kind"] == "art" else "Photo"
    title = (hit["tags"].split(",")[0].strip() or f"Stock {what.lower()}").capitalize()
    studio.update(iid, by, title=f"{title} (Pixabay)",
                  notes=f"{what} by {hit['user']} on Pixabay ({hit['page']}). Free to use under the Pixabay Content License.")
    with db.conn() as c:
        c.execute("UPDATE studio_items SET source='pixabay', canva_id=?, canva_url=? WHERE id=?", (f"pixabay-img:{hit['id']}", hit["page"], iid))


def image_import(raw, pid, by=""):
    """Save a Pixabay picture into the brand's 'Stock photos' folder (once per brand) and return its library id.
    raw: the hit as the editor got it (from the hub's search or straight from Pixabay); only Pixabay addresses are fetched."""
    hit = pixabay_image_hit(raw)
    if not hit:
        raise StockError("That doesn't look like a Pixabay picture.")
    have = _have_image(hit["id"], pid)
    if have:
        return have
    ext = ".png" if hit["file"].lower().split("?")[0].endswith(".png") else ".jpg"
    path = _download(hit["file"], ext)
    title = (hit["tags"].split(",")[0].strip() or "Stock photo").capitalize()
    iid = studio.add_file(path, f"{title}{ext}", pid, photo_folder(pid), by=by, mime="image/png" if ext == ".png" else "image/jpeg",
                          title=f"{title} (Pixabay)", source="pixabay")
    _image_credit(iid, hit, by)
    return iid


def image_attach(item_id, raw, pid, by=""):
    """A Pixabay picture the browser downloaded and uploaded itself: credit it, or swap in the copy the brand already has."""
    hit = pixabay_image_hit(raw)
    if not hit:
        raise StockError("That doesn't look like a Pixabay picture.")
    have = _have_image(hit["id"], pid)
    if have and have != item_id:
        studio.trash(item_id, by)
        return have
    with db.conn() as c:
        c.execute("UPDATE studio_items SET folder_id=? WHERE id=?", (photo_folder(pid), item_id))
    _image_credit(item_id, hit, by)
    return item_id


# ------------------------------------------------------------------ Pexels photos and videos (free for any use, no credit required)
PEXELS = "https://api.pexels.com"


def _pexels_url(u):
    try:
        pu = urllib.parse.urlparse(str(u or ""))
    except ValueError:
        return ""
    host = (pu.hostname or "").lower()
    return str(u) if pu.scheme == "https" and (host == "pexels.com" or host.endswith(".pexels.com")) else ""


def _pexels_key():
    key = _tidy_key(db.get_setting("pexels_key"))
    if not key:
        raise StockError("Stock search needs a free Pexels key. An admin adds it on Connections → Stock and music.")
    return key


def pexels_photo_hit(h):
    """One Pexels photo in the same shape as a Pixabay picture (see pixabay_image_hit)."""
    if not isinstance(h, dict):
        return None
    try:
        hid = int(h.get("id"))
    except (TypeError, ValueError):
        return None
    srcs = h.get("src") or {}
    big = _pexels_url(srcs.get("large2x")) or _pexels_url(srcs.get("large")) or _pexels_url(srcs.get("original"))
    if not big:
        return None
    return {"id": hid, "provider": "pexels", "tags": str(h.get("alt") or "")[:300], "user": str(h.get("photographer") or "")[:100],
            "page": _pexels_url(h.get("url")), "thumb": _pexels_url(srcs.get("small") or srcs.get("tiny")),
            "web": _pexels_url(srcs.get("large") or srcs.get("medium")), "file": big,
            "w": int(h.get("width") or 0), "h": int(h.get("height") or 0), "kind": "photo"}


def pexels_photos(q, kind="photo", orientation="", page=1):
    key = _pexels_key()
    q = re.sub(r"\s+", " ", q or "").strip()[:100]
    if kind == "art":
        q += " isolated on white background"      # Pexels has no cut-outs: objects on white, laid on the page with multiply
    o = {"horizontal": "landscape", "vertical": "portrait"}.get(orientation, "")
    page = max(1, min(10, int(page or 1)))

    def fetch():
        params = {"query": q, "per_page": 30, "page": page, **({"orientation": o} if o else {})}
        got = _get(PEXELS + "/v1/search?" + urllib.parse.urlencode(params), service="Pexels", headers={"Authorization": key})
        hits = [x for x in map(pexels_photo_hit, got.get("photos", [])) if x]
        for x in hits:
            x["kind"] = kind if kind in ("photo", "art") else "photo"
        return {"hits": hits, "total": got.get("total_results", 0), "provider": "pexels"}
    return _cache(f"pxl_{kind}_{o}_{q}_{page}", fetch)


def pexels_video_hit(h):
    """One Pexels video in the shape the editor uses (see pixabay_hit): the largest MP4 up to 1920, a still and a preview."""
    if not isinstance(h, dict):
        return None
    try:
        hid = int(h.get("id"))
    except (TypeError, ValueError):
        return None
    files = [f for f in (h.get("video_files") or []) if isinstance(f, dict) and _pexels_url(f.get("link"))
             and str(f.get("file_type", "")).endswith("mp4") and f.get("width") and f.get("height")]
    fit = sorted([f for f in files if max(int(f["width"]), int(f["height"])) <= 1920], key=lambda f: int(f["width"]) * int(f["height"]))
    if not fit:
        return None
    best, small = fit[-1], fit[0]
    return {"id": hid, "provider": "pexels", "duration": h.get("duration"), "tags": "", "user": str((h.get("user") or {}).get("name") or "")[:100],
            "page": _pexels_url(h.get("url")), "thumb": _pexels_url(h.get("image")), "preview": _pexels_url(small["link"]),
            "file": {"url": best["link"], "w": int(best["width"]), "h": int(best["height"])}}


def _video_title(hit):
    if hit.get("tags"):
        return hit["tags"].split(",")[0].strip().capitalize()
    slug = re.sub(r"^https://www\.pexels\.com/video/|/?$", "", hit.get("page") or "")
    words = re.sub(r"-?\d+$", "", slug).replace("-", " ").strip()
    return words.capitalize() or "Stock video"


def pexels_videos(q, orientation="", page=1):
    key = _pexels_key()
    q = re.sub(r"\s+", " ", q or "").strip()[:100]
    o = {"tall": "portrait", "wide": "landscape"}.get(orientation, "")
    page = max(1, min(10, int(page or 1)))

    def fetch():
        params = {"query": q, "per_page": 30, "page": page, **({"orientation": o} if o else {})}
        got = _get(PEXELS + "/videos/search?" + urllib.parse.urlencode(params), service="Pexels", headers={"Authorization": key})
        hits = [x for x in map(pexels_video_hit, got.get("videos", [])) if x]
        for x in hits:
            x["tags"] = _video_title(x)
        return {"hits": hits, "total": got.get("total_results", 0), "provider": "pexels"}
    return _cache(f"pxlv_{o}_{q}_{page}", fetch)


def _have_key(cid, pid=None, by_brand=False):
    with db.conn() as c:
        if by_brand:
            r = c.execute("SELECT id FROM studio_items WHERE canva_id=? AND practice_id IS ? AND trashed_at IS NULL", (cid, pid)).fetchone()
        else:
            r = c.execute("SELECT id FROM studio_items WHERE canva_id=? AND trashed_at IS NULL", (cid,)).fetchone()
    return r[0] if r else None


def _pexels_credit(iid, hit, what, by):
    studio.update(iid, by, title=f"{(hit.get('tags') or what).split(',')[0].strip()[:80].capitalize()} (Pexels)",
                  notes=f"{what} by {hit['user']} on Pexels ({hit['page']}). Free to use under the Pexels License.")
    with db.conn() as c:
        c.execute("UPDATE studio_items SET source='pexels', canva_id=?, canva_url=? WHERE id=?",
                  (f"pexels{'-img' if what == 'Photo' else ''}:{hit['id']}", hit["page"], iid))


def pexels_video_import(raw, pid, by=""):
    hit = raw if isinstance(raw, dict) and raw.get("provider") == "pexels" else None
    hit = _clean_pexels_video(hit)
    have = _have_key(f"pexels:{hit['id']}")
    if have:
        return have
    path = _download(hit["file"]["url"], ".mp4")
    title = _video_title(hit)
    iid = studio.add_file(path, f"{title}.mp4", pid, stock_folder(pid), by=by, mime="video/mp4", title=f"{title} (Pexels)",
                          source="pexels", width=hit["file"]["w"], height=hit["file"]["h"], duration=hit.get("duration"),
                          canva={"id": f"pexels:{hit['id']}", "url": hit["page"]})
    _pexels_credit(iid, {**hit, "tags": title}, "Video", by)
    return iid


def _clean_pexels_video(h):
    """A video hit sent back by the browser: only Pexels addresses, nothing else."""
    try:
        f = h["file"]
        clean = {"id": int(h["id"]), "provider": "pexels", "duration": h.get("duration"), "tags": str(h.get("tags") or "")[:300],
                 "user": str(h.get("user") or "")[:100], "page": _pexels_url(h.get("page")),
                 "file": {"url": _pexels_url(f.get("url")), "w": int(f.get("w") or 0), "h": int(f.get("h") or 0)}}
    except (TypeError, KeyError, ValueError):
        raise StockError("That doesn't look like a Pexels video.")
    if not clean["file"]["url"]:
        raise StockError("That doesn't look like a Pexels video.")
    return clean


def pexels_video_attach(item_id, raw, by=""):
    hit = _clean_pexels_video(raw if isinstance(raw, dict) else {})
    have = _have_key(f"pexels:{hit['id']}")
    if have and have != item_id:
        studio.trash(item_id, by)
        return have
    _pexels_credit(item_id, {**hit, "tags": _video_title(hit)}, "Video", by)
    return item_id


def _clean_pexels_photo(h):
    try:
        clean = {"id": int(h["id"]), "provider": "pexels", "tags": str(h.get("tags") or "")[:300], "user": str(h.get("user") or "")[:100],
                 "page": _pexels_url(h.get("page")), "file": _pexels_url(h.get("file")), "kind": "photo"}
    except (TypeError, KeyError, ValueError):
        raise StockError("That doesn't look like a Pexels photo.")
    if not clean["file"]:
        raise StockError("That doesn't look like a Pexels photo.")
    return clean


def pexels_photo_import(raw, pid, by=""):
    hit = _clean_pexels_photo(raw if isinstance(raw, dict) else {})
    have = _have_key(f"pexels-img:{hit['id']}", pid, by_brand=True)
    if have:
        return have
    path = _download(hit["file"], ".jpg")
    title = (hit["tags"].split(",")[0].strip() or "Stock photo")[:80].capitalize()
    iid = studio.add_file(path, f"{title}.jpg", pid, photo_folder(pid), by=by, mime="image/jpeg", title=f"{title} (Pexels)", source="pexels")
    _pexels_credit(iid, hit, "Photo", by)
    return iid


def pexels_photo_attach(item_id, raw, pid, by=""):
    hit = _clean_pexels_photo(raw if isinstance(raw, dict) else {})
    have = _have_key(f"pexels-img:{hit['id']}", pid, by_brand=True)
    if have and have != item_id:
        studio.trash(item_id, by)
        return have
    with db.conn() as c:
        c.execute("UPDATE studio_items SET folder_id=? WHERE id=?", (photo_folder(pid), item_id))
    _pexels_credit(item_id, hit, "Photo", by)
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
