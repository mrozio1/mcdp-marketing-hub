"""Light copies of library files for the video editor's live preview (what CapCut and Premiere call proxies).

Phones stutter when the preview plays full-size originals (often 4K, high bit-rate, streamed from SharePoint
through the NAS on every seek). So the first time a video is used in the editor, the NAS makes a small copy kept
next to the Studio files: at most 960 pixels on its long side, 30 frames a second, a keyframe every half second
(so seeking is instant), web-optimised. Photos get a copy at most 1920 pixels. The editor plays the copies; the
finished MP4 is still made from the originals.

One copy is made at a time, in the background, oldest request first.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import zlib

from . import db, studio

VIDEO_SIDE = 960
IMAGE_SIDE = 1920
VERSION = 1
_q: "queue.Queue[int]" = queue.Queue()
_pending: set = set()
_lock = threading.Lock()
_worker = None


def _dir():
    d = os.path.join(os.path.dirname(db.DB_PATH) or ".", "studio", "derived", "proxy")
    os.makedirs(d, exist_ok=True)
    return d


def _stamp(it):
    return f"{VERSION}:{it.get('updated_at') or ''}:{it.get('size') or ''}"


def path(it):
    """The ready copy of a library video, or None."""
    if not it or it["kind"] != "video":
        return None
    p = os.path.join(_dir(), f"v{int(it['id'])}.mp4")
    st = status(it["id"])
    return p if os.path.exists(p) and st.get("stamp") == _stamp(it) else None


def status(item_id):
    try:
        return json.loads(db.get_setting(f"studio_proxy_{int(item_id)}") or "{}")
    except ValueError:
        return {}


def state(it):
    """ready | queued | running | error | None (not asked for yet)."""
    if not it or it["kind"] != "video":
        return None
    if path(it):
        return "ready"
    with _lock:
        if it["id"] in _pending:
            return status(it["id"]).get("state") or "queued"
    st = status(it["id"])
    return "error" if st.get("state") == "error" and st.get("stamp") == _stamp(it) else None


def _note(item_id, **kw):
    s = status(item_id)
    s.update(kw, at=time.time())
    db.set_setting(f"studio_proxy_{int(item_id)}", json.dumps(s))


def ensure(item_id):
    """Ask for a video's copy (no-op if it's ready, queued, or failed for this version of the file)."""
    if not shutil.which("ffmpeg"):
        return None
    it = studio.item(item_id)
    st = state(it)
    if not it or st in ("ready", "queued", "running", "error"):
        return st
    with _lock:
        if it["id"] in _pending:
            return "queued"
        _pending.add(it["id"])
    _note(it["id"], state="queued", stamp=_stamp(it), error="")
    _q.put(it["id"])
    _start()
    return "queued"


def _start():
    global _worker
    with _lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_loop, daemon=True, name="studio-proxy")
        _worker.start()


def _loop():
    while True:
        try:
            iid = _q.get(timeout=30)
        except queue.Empty:
            return
        try:
            make(iid)
        finally:
            with _lock:
                _pending.discard(iid)


def make(iid):
    """Make one video's copy now (the worker calls this; tests call it directly)."""
    from . import studio_video
    it = studio.item(iid)
    if not it or it["kind"] != "video":
        return None
    out = os.path.join(_dir(), f"v{int(iid)}.mp4")
    part = out + ".part.mp4"
    tmp = None
    _note(iid, state="running", stamp=_stamp(it), error="")
    try:
        src, is_tmp = studio_video.source_path(it)
        tmp = src if is_tmp else None
        side = VIDEO_SIDE
        vf = (f"scale=w=if(gte(iw\\,ih)\\,min({side}\\,iw)\\,-2):h=if(gte(iw\\,ih)\\,-2\\,min({side}\\,ih)),"
              "format=yuv420p")
        args = ["ffmpeg", "-y", "-v", "error", "-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, "-fpsmax", "30",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
                "-profile:v", "high", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-movflags", "+faststart", part]
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
        if p.returncode != 0 or not os.path.exists(part) or os.path.getsize(part) < 1000:
            raise RuntimeError(p.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["ffmpeg failed"])
        os.replace(part, out)
        _note(iid, state="ready", stamp=_stamp(it), error="", bytes=os.path.getsize(out))
        return out
    except Exception as e:
        _note(iid, state="error", stamp=_stamp(it), error=str(e)[:300])
        try:
            os.remove(part)
        except OSError:
            pass
        return None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def image(it):
    """A photo at most 1920 pixels on its long side (made now, then kept); None if the original is already small."""
    from PIL import Image, ImageOps
    if not it or it["kind"] not in ("image", "design"):
        return None
    w, h = it.get("width") or 0, it.get("height") or 0
    if w and h and max(w, h) <= IMAGE_SIDE:
        return None
    out = os.path.join(_dir(), f"i{int(it['id'])}_{zlib.crc32(_stamp(it).encode()):08x}")
    for ext in (".jpg", ".png"):
        if os.path.exists(out + ext):
            return out + ext
    from . import studio_video
    src, is_tmp = studio_video.source_path(it)
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            if max(im.size) <= IMAGE_SIDE:
                return None
            im.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
            alpha = im.mode in ("RGBA", "LA", "P") and "transparency" in im.info or im.mode in ("RGBA", "LA")
            if alpha:
                im.convert("RGBA").save(out + ".png", "PNG")
                return out + ".png"
            im.convert("RGB").save(out + ".jpg", "JPEG", quality=86)
            return out + ".jpg"
    except Exception:
        return None
    finally:
        if is_tmp:
            try:
                os.remove(src)
            except OSError:
                pass
