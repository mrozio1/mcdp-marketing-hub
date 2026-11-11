"""Studio phase 4: short videos (Reels, Stories, posts) cut together in the hub and rendered with ffmpeg.

A video project is a library item (kind 'video', source 'studio') whose file is the latest render; the
edit itself lives in studio/designs/<id>.json:

  {"type": "video", "w": 1080, "h": 1920, "clips": [{"item": 12, "in": 0, "out": 6.5, "speed": 1, "volume": 1}, ...],
   "captions": [{"start": 0.4, "end": 2.1, "text": "..."}], "lowers": [{"start": 3, "end": 7, "text": "Dr. Schulz",
   "sub": "Orthodontist"}], "outro": {"on": true, "dur": 2.5, "text": "Book a visit"},
   "music": {"item": 40, "volume": 0.25} | null, "style": {"captions": "box"|"plain", "position": "low"|"middle"}}

Clips can be videos or pictures (shown for a few seconds). Rendering: each clip becomes a same-size,
same-codec piece (trimmed, sped up or slowed, cropped to fill the frame), the brand outro is added, the
pieces are joined, then captions and name tags (ASS subtitles) and music are put on top. Captions can be
written from the speech by Cloudflare's free Whisper model.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from . import db, images, studio, video2

SIZES = (("story", "Reel or Story", 1080, 1920), ("square", "Square post", 1080, 1080),
         ("portrait", "Portrait post", 1080, 1350), ("wide", "Wide (YouTube, website)", 1920, 1080))
SIZE = {k: {"key": k, "label": l, "w": w, "h": h} for k, l, w, h in SIZES}
SPEEDS = (0.5, 0.75, 1, 1.25, 1.5, 2)
MAX_TOTAL = 180.0            # seconds
MAX_CLIPS = 40
FPS = 30
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
FONT = "DejaVu Sans"
_jobs_lock = threading.Lock()
_running = set()


class VideoError(RuntimeError):
    pass


def ffmpeg_ok():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _need_ffmpeg():
    if not ffmpeg_ok():
        raise VideoError("The video tools aren't installed on the server yet. Run the hub update on the NAS.")


def _run(args, timeout=1800):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        tail = p.stderr.decode(errors="replace").strip().splitlines()[-3:]
        raise VideoError("The video couldn't be made: " + " ".join(tail)[:300])
    return p


# ------------------------------------------------------------------ files
def probe(path):
    _need_ffmpeg()
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,width,height:stream_side_data=rotation:stream_tags=rotate",
                        "-of", "json", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    try:
        j = json.loads(p.stdout or b"{}")
    except ValueError:
        j = {}
    streams = j.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    try:
        dur = float((j.get("format") or {}).get("duration") or 0)
    except ValueError:
        dur = 0
    w, h = video2.shown_size(v) if v else (None, None)
    return {"duration": dur, "w": w, "h": h,
            "audio": any(s.get("codec_type") == "audio" for s in streams)}


def source_path(it):
    """A local path for a library file (downloaded from SharePoint into tmp if needed). (path, is_temp)"""
    p = studio.local_path(it)
    if p:
        return p, False
    url = studio.download_url(it)
    if not url:
        raise VideoError(f"“{it['title']}” isn't reachable right now.")
    fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(it.get("filename") or "")[1], dir=studio._dir("tmp"))
    with urllib.request.urlopen(url, timeout=600) as r, os.fdopen(fd, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    return tmp, True


def media_info(iid):
    """Length, size and sound of a library video (measured once, then remembered)."""
    it = studio.item(iid)
    if not it or it["kind"] not in ("video", "image", "design", "audio"):
        raise VideoError("Pick a video, picture or sound from the library.")
    if it["kind"] in ("image", "design"):
        return {"id": iid, "kind": "image", "duration": None, "w": it["width"], "h": it["height"], "audio": False,
                "title": it["title"]}
    info_key = f"studio_probe_{iid}"
    cached = db.get_setting(info_key)
    if cached:
        try:
            got = json.loads(cached)
            if got.get("v") == f"2:{it.get('updated_at')}":
                return got["info"]
        except ValueError:
            pass
    path, temp = source_path(it)
    try:
        pr = probe(path)
    finally:
        if temp:
            os.remove(path)
    info = {"id": iid, "kind": it["kind"], "duration": round(pr["duration"], 3), "w": pr["w"], "h": pr["h"],
            "audio": pr["audio"], "title": it["title"]}
    with db.conn() as c:
        c.execute("UPDATE studio_items SET duration=COALESCE(duration, ?), width=COALESCE(width, ?), height=COALESCE(height, ?) "
                  "WHERE id=?", (info["duration"] or None, pr["w"], pr["h"], iid))
    db.set_setting(info_key, json.dumps({"v": f"2:{it.get('updated_at')}", "info": info}))
    return info


# ------------------------------------------------------------------ projects
def is_project(it):
    return bool(it) and it["kind"] == "video" and it.get("source") == "studio" and os.path.exists(studio.design_path(it["id"]))


def _placeholder(w, h, color="0x0F7471"):
    _need_ffmpeg()
    fd, path = tempfile.mkstemp(suffix=".mp4", dir=studio._dir("tmp"))
    os.close(fd)
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:d=1:r={FPS}",
          "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-shortest", path])
    return path


def create_project(pid, folder_id, size_key, title="", by="", clip_ids=()):
    size = SIZE.get(size_key)
    if not size:
        raise VideoError("Pick a size.")
    if pid != studio.MCDP and not db.practice(pid):
        raise VideoError("Pick a brand first.")
    title = (title or "").strip()[:200] or f"{size['label']} video"
    path = _placeholder(size["w"], size["h"])
    iid = studio.add_file(path, title + ".mp4", pid, folder_id, by=by, mime="video/mp4", title=title, source="studio",
                          width=size["w"], height=size["h"], duration=1)
    with db.conn() as c:
        c.execute("UPDATE studio_items SET size_key=? WHERE id=?", ("video_" + size_key, iid))
    proj = {"type": "video", "v": 2, "w": size["w"], "h": size["h"], "main": [], "layers": [], "captions": [], "lowers": [],
            "outro": {"on": True, "dur": 2.5, "text": "Book a visit", "trans": {"type": "fade", "dur": 0.4}}, "music": None,
            "style": {"captions": "box", "position": "low"}}
    for cid in list(clip_ids)[:MAX_CLIPS]:
        try:
            info = media_info(cid)
        except VideoError:
            continue
        proj["main"].append(_clip_from(info))
    save_project(iid, proj)
    return iid


def _clip_from(info):
    if info["kind"] == "video":
        return {"item": info["id"], "in": 0, "out": round(min(info["duration"] or 5, 60), 3), "speed": 1, "volume": 1, "fit": "fill"}
    return {"item": info["id"], "in": 0, "out": 3, "speed": 1, "volume": 0, "still": True, "fit": "fit"}


def project(iid):
    try:
        with open(studio.design_path(iid)) as f:
            p = json.load(f)
        if p.get("type") == "video":
            return video2.migrate(p)
    except (OSError, ValueError):
        pass
    raise VideoError("That video project is gone.")


def clean(p, old=None):
    old = old or {}
    w, h = int(old.get("w") or p.get("w") or 1080), int(old.get("h") or p.get("h") or 1920)
    try:
        return video2.clean(p, w, h)
    except video2.V2Error as e:
        raise VideoError(str(e))


def clip_len(c):
    return video2.clip_len(c)


def total(p):
    return video2.layout(video2.migrate(p))["end"]


def save_project(iid, p, by=""):
    it = studio.item(iid)
    if not it:
        raise VideoError("That video project is gone.")
    try:
        old = project(iid)
    except VideoError:
        old = {}
    p = clean(p, old)
    tmp = studio.design_path(iid) + ".new"
    with open(tmp, "w") as f:
        json.dump(p, f)
    os.replace(tmp, studio.design_path(iid))
    with db.conn() as c:
        c.execute("UPDATE studio_items SET updated_at=? WHERE id=?", (db.now(), iid))
    return p


def set_ratio(iid, size_key, by=""):
    """Change a project's shape (Reel, square, portrait, wide), keeping layers where they were on the screen."""
    size = SIZE.get(size_key)
    if not size:
        raise VideoError("Pick a size.")
    p = project(iid)
    W1, H1, W2, H2 = p["w"], p["h"], size["w"], size["h"]
    k = min(W2 / W1, H2 / H1)
    for L in p["layers"]:
        cx, cy = (L["x"] + L["w"] / 2) / W1 * W2, (L["y"] + L["h"] / 2) / H1 * H2
        L["w"], L["h"] = L["w"] * k, L["h"] * k
        if L.get("size"):
            L["size"] = max(10, round(L["size"] * k))
        L["x"], L["y"] = cx - L["w"] / 2, cy - L["h"] / 2
    p["w"], p["h"] = W2, H2
    try:
        p = video2.clean(p, W2, H2)
    except video2.V2Error as e:
        raise VideoError(str(e))
    tmp = studio.design_path(iid) + ".new"
    with open(tmp, "w") as f:
        json.dump(p, f)
    os.replace(tmp, studio.design_path(iid))
    with db.conn() as c:
        c.execute("UPDATE studio_items SET width=?, height=?, size_key=?, updated_at=? WHERE id=?",
                  (W2, H2, "video_" + size_key, db.now(), iid))
    return p


# ------------------------------------------------------------------ the brand outro
def _font(size, bold=True):
    from PIL import ImageFont
    for f in ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",):
        for d in (FONT_DIR, "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"):
            p = os.path.join(d, f)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def outro_png(pid, w, h, text):
    """The end card: brand colour, the logo, and a line of text."""
    from PIL import Image, ImageDraw
    import io
    from . import field_report
    primary, accent = (field_report.colors(pid) if pid else (field_report.DEFAULT_PRIMARY, field_report.DEFAULT_ACCENT))
    img = Image.new("RGB", (w, h), primary)
    d = ImageDraw.Draw(img)
    short = min(w, h)
    y = h * 0.5
    lg = images.logo(pid) if pid else None
    lp = images.path(lg) if lg else None
    if lp:
        try:
            with Image.open(lp) as L:
                L = L.convert("RGBA")
                card = Image.new("RGBA", (int(short * 0.5), int(short * 0.5)), (255, 255, 255, 0))
                L.thumbnail((int(short * 0.42), int(short * 0.3)))
                pad = int(short * 0.04)
                box = Image.new("RGBA", (L.width + pad * 2, L.height + pad * 2), (255, 255, 255, 255))
                box.paste(L, (pad, pad), L)
                x0, y0 = (w - box.width) // 2, int(h * 0.5 - box.height - short * 0.03)
                img.paste(box, (x0, y0), box)
                y = h * 0.5 + short * 0.03
                del card
        except Exception:
            pass
    else:
        name = (db.practice(pid) or {}).get("name") if pid else "MCDP"
        f = _font(int(short * 0.07))
        tw = d.textlength(name or "", font=f)
        d.text(((w - tw) / 2, h * 0.5 - short * 0.12), name or "", font=f, fill="white")
        y = h * 0.5 + short * 0.02
    if text:
        f = _font(int(short * 0.06))
        tw = d.textlength(text, font=f)
        pad_x, pad_y = short * 0.045, short * 0.025
        bh = f.size + pad_y * 2
        d.rounded_rectangle([(w - tw) / 2 - pad_x, y, (w + tw) / 2 + pad_x, y + bh], radius=bh / 2, fill=accent)
        d.text(((w - tw) / 2, y + pad_y - f.size * 0.08), text, font=f, fill="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ captions and name tags (ASS)
def _ass_time(t):
    t = max(0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_color(hexc, alpha=0):
    hexc = (hexc or "#000000").lstrip("#")
    r, g, b = hexc[0:2], hexc[2:4], hexc[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_text(t):
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def ass_file(p, pid):
    from . import field_report
    primary, accent = (field_report.colors(pid) if pid else (field_report.DEFAULT_PRIMARY, field_report.DEFAULT_ACCENT))
    w, h = p["w"], p["h"]
    short = min(w, h)
    box = p["style"]["captions"] == "box"
    cap_size = int(short * 0.058)
    margin_v = int(h * (0.42 if p["style"]["position"] == "middle" else (0.2 if h > w else 0.1)))
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
             "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
             "MarginR, MarginV, Encoding",
             f"Style: Cap,{FONT},{cap_size},&H00FFFFFF,&H00FFFFFF,{_ass_color(primary, 0x10) if box else '&H00000000'},"
             f"&H64000000,1,0,0,0,100,100,0,0,{3 if box else 1},{int(cap_size * 0.28) if box else int(cap_size * 0.09)},"
             f"{0 if box else 2},2,{int(w * 0.08)},{int(w * 0.08)},{margin_v},1",
             f"Style: Name,{FONT},{int(short * 0.05)},&H00FFFFFF,&H00FFFFFF,{_ass_color(accent)},&H00000000,1,0,0,0,100,100,0,0,3,"
             f"{int(short * 0.016)},0,1,{int(w * 0.07)},{int(w * 0.07)},{int(h * 0.36 if h > w else h * 0.24)},1",
             f"Style: Role,{FONT},{int(short * 0.034)},&H00FFFFFF,&H00FFFFFF,{_ass_color(primary)},&H00000000,0,0,0,0,100,100,0,0,3,"
             f"{int(short * 0.012)},0,1,{int(w * 0.07)},{int(w * 0.07)},{int(h * 0.36 if h > w else h * 0.24) - int(short * 0.075)},1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for c in p["captions"]:
        lines.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Cap,,0,0,0,,{_ass_text(c['text'])}")
    for c in p["lowers"]:
        fade = "{\\fad(250,250)}"
        lines.append(f"Dialogue: 1,{_ass_time(c['start'])},{_ass_time(c['end'])},Name,,0,0,0,,{fade}{_ass_text(c['text'])}")
        if c.get("sub"):
            lines.append(f"Dialogue: 1,{_ass_time(c['start'])},{_ass_time(c['end'])},Role,,0,0,0,,{fade}{_ass_text(c['sub'])}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ rendering
def status(iid):
    try:
        return json.loads(db.get_setting(f"studio_render_{iid}") or "{}")
    except ValueError:
        return {}


def _note(iid, **kw):
    s = status(iid)
    s.update(kw, at=time.time())
    db.set_setting(f"studio_render_{iid}", json.dumps(s))


def render(iid, by="", texts=None):
    """Make the MP4 (in the background). texts: {layer id: PNG bytes} drawn by the editor for text layers."""
    with _jobs_lock:
        if iid in _running:
            return False
        _running.add(iid)
    threading.Thread(target=_render, args=(iid, by, texts or {}), daemon=True).start()
    return True


def _dims(it, path):
    if it["kind"] in ("image", "design"):
        from PIL import Image
        with Image.open(path) as im:
            return im.size[0], im.size[1], False
    return video2.probe_dims(path)


def _render(iid, by, texts=None):
    from . import bgremove
    work = tempfile.mkdtemp(dir=studio._dir("tmp"))
    temps = []
    texts = texts or {}

    def local(item_id):
        it = studio.item(item_id)
        if not it:
            raise VideoError("A clip's file was deleted from the library.")
        path, temp = source_path(it)
        if temp:
            temps.append(path)
        return it, path
    try:
        _need_ffmpeg()
        it = studio.item(iid)
        p = project(iid)
        if not p["main"]:
            raise VideoError("Add at least one clip to the main track first.")
        pid = it["practice_id"] if it["practice_id"] not in (None, studio.MCDP) else None
        W, H = p["w"], p["h"]
        lay = video2.layout(p)
        segs, end = lay["segs"], lay["end"]
        steps = len(segs) + 3
        _note(iid, state="running", step=0, steps=steps, error="", started=time.time())
        pieces = []
        for k, sg in enumerate(segs):
            nxt = segs[k + 1] if k + 1 < len(segs) else None
            length = sg["len"] + (video2.EPS if nxt is not None and nxt["d"] == 0 else 0)
            out = os.path.join(work, f"p{k:03d}.mp4")
            if sg["kind"] == "outro":
                png = os.path.join(work, "outro.png")
                with open(png, "wb") as f:
                    f.write(outro_png(pid, W, H, p["outro"]["text"]))
                c = {"still": True, "out": length, "fit": "fill", "crop": video2._crop({}), "zoom": 1, "dx": 0, "dy": 0,
                     "filter": {}, "motion": "none", "bg": {"mode": "black"}}
                video2.piece_main(png, c, W, H, out, length, False, sw=W, sh=H)
            else:
                c = dict(sg["c"])
                src_it, src = local(c["item"])
                sw, sh, has_audio = _dims(src_it, src)
                if c.get("still"):
                    c["out"] = length
                elif length > sg["len"]:                       # run on a little past a straight cut
                    c["out"] = c["out"] + video2.EPS * (c.get("speed") or 1)
                mask = bgremove.mask_path(c["item"]) if c["bg"].get("cut") else None
                bg_photo = None
                if c["bg"]["mode"] == "photo" and c["bg"].get("item"):
                    _, bg_photo = local(c["bg"]["item"])
                if c["bg"]["mode"] == "color" and c["bg"]["color"] == "#0F7471" and pid:
                    from . import field_report
                    c["bg"]["color"] = field_report.colors(pid)[0]
                video2.piece_main(src, c, W, H, out, length, has_audio, mask=mask, bg_photo=bg_photo, sw=sw, sh=sh)
            pieces.append(out)
            _note(iid, step=k + 1)
        joined = os.path.join(work, "main.mp4")
        video2.join_main(pieces, segs, joined, end)
        from . import vfx
        fx_main = [e for e in p.get("effects", []) if e["scope"] == "main"] + vfx.from_transitions(segs)
        fx_all = [e for e in p.get("effects", []) if e["scope"] == "all"]
        if fx_main:                                   # effects on the main track only, under the layers
            joined_fx = os.path.join(work, "main_fx.mp4")
            vfx.apply(joined, fx_main, joined_fx, W, H, end, run=video2.run)
            joined = joined_fx
        _note(iid, step=len(segs) + 1)
        layer_src = {}
        for L in p["layers"]:
            if L["kind"] == "text":
                path = os.path.join(work, f"t_{L['id']}.png")
                data = texts.get(L["id"])
                if data and data.startswith(b"\x89PNG"):
                    with open(path, "wb") as f:
                        f.write(data)
                else:
                    video2.text_png(L, path)
                layer_src[L["id"]] = {"path": path, "w": int(L["w"]), "h": int(L["h"]), "audio": False}
            else:
                src_it, src = local(L["item"])
                sw, sh, aud = _dims(src_it, src)
                layer_src[L["id"]] = {"path": src, "w": sw, "h": sh, "audio": aud,
                                      "mask": bgremove.mask_path(L["item"]) if L.get("cut") and L["kind"] == "video" else None}
        ass = fontsdir = None
        if p["captions"] or p["lowers"]:
            ass = os.path.join(work, "subs.ass")
            with open(ass, "w") as f:
                f.write(ass_file(p, pid))
            fontsdir = FONT_DIR if os.path.isdir(FONT_DIR) else "/usr/share/fonts"
        music = None
        if p.get("music"):
            _, mpath = local(p["music"]["item"])
            music = {"path": mpath, "volume": p["music"]["volume"]}
        final = os.path.join(work, "final.mp4")
        if fx_all:                                    # effects over everything; captions stay clean on top
            staged = os.path.join(work, "stage.mp4")
            video2.overlay_stage(joined, p, layer_src, staged, end, music=music)
            vfx.apply(staged, fx_all, final, W, H, end, ass=ass, fontsdir=fontsdir, run=video2.run)
        else:
            video2.overlay_stage(joined, p, layer_src, final, end, ass=ass, fontsdir=fontsdir, music=music)
        _note(iid, step=len(segs) + 2)
        thumb = os.path.join(work, "thumb.jpg")
        _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, end / 3):.2f}", "-i", final, "-frames:v", "1",
              "-vf", "scale=640:-2", thumb])
        with open(thumb, "rb") as f:
            tb = f.read()
        size = os.path.getsize(final)
        studio.replace_file(iid, final, it["title"] + ".mp4", "video/mp4", tb, by=by, log=False)
        with db.conn() as c:
            c.execute("UPDATE studio_items SET duration=?, width=?, height=? WHERE id=?", (round(end, 2), W, H, iid))
        studio.event(iid, "rendered", by, comment=f"{end:.0f} s · {size / 1048576:.1f} MB")
        _note(iid, state="done", step=steps, error="", finished=time.time(), length=round(end, 1))
    except Exception as e:
        msg = str(e) if isinstance(e, (VideoError, video2.V2Error)) else f"Something went wrong making the video: {e}"
        _note(iid, state="error", error=msg[:500])
    finally:
        for t in temps:
            try:
                os.remove(t)
            except OSError:
                pass
        shutil.rmtree(work, ignore_errors=True)
        with _jobs_lock:
            _running.discard(iid)


# ------------------------------------------------------------------ captions from speech (Cloudflare Whisper)
WHISPER = "@cf/openai/whisper"


def _cf():
    acct = (os.environ.get("CF_ACCOUNT_ID") or db.get_setting("cf_account_id") or "").strip()
    tok = (os.environ.get("CF_API_TOKEN") or db.get_setting("cf_api_token") or "").strip()
    return acct, tok


def captions_ready():
    return all(_cf()) and ffmpeg_ok()


def _whisper(audio):
    acct, tok = _cf()
    if not (acct and tok):
        raise VideoError("Captions from speech use the hub's Cloudflare AI connection (the same one that makes AI "
                         "pictures), which isn't set up on this server yet.")
    req = urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{WHISPER}", data=audio,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            j = json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")[:300]
        raise VideoError(f"Cloudflare couldn't write the captions: {raw}")
    except urllib.error.URLError as e:
        raise VideoError(f"Couldn't reach Cloudflare: {e.reason}")
    return (j or {}).get("result") or {}


def group_words(words, max_words=6, max_len=2.8):
    """Word timings → short caption lines, broken at pauses and punctuation."""
    out, cur = [], []
    for wd in words:
        t = str(wd.get("word") or "").strip()
        if not t:
            continue
        try:
            a, b = float(wd.get("start")), float(wd.get("end"))
        except (TypeError, ValueError):
            continue
        if cur and (len(cur) >= max_words or b - cur[0][1] > max_len or a - cur[-1][2] > 0.6):
            out.append(cur)
            cur = []
        cur.append((t, a, b))
        if re.search(r"[.!?]$", t):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"start": round(g[0][1], 2), "end": round(max(g[-1][2], g[0][1] + 0.6), 2), "text": " ".join(x[0] for x in g)}
            for g in out]


def transcribe(iid):
    """Captions for the whole edit (main track), timed to it: trims, speed and transitions included."""
    _need_ffmpeg()
    p = project(iid)
    caps = []
    for sg in video2.layout(p)["segs"]:
        c = sg["c"]
        if sg["kind"] != "clip" or c.get("still") or (c.get("volume") or 0) <= 0:
            continue
        t0, ln = sg["start"], sg["len"]
        src_it = studio.item(c["item"])
        if not src_it:
            continue
        src, temp = source_path(src_it)
        try:
            if not probe(src)["audio"]:
                continue
            fd, mp3 = tempfile.mkstemp(suffix=".mp3", dir=studio._dir("tmp"))
            os.close(fd)
            try:
                _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{c['in']:.3f}", "-t", f"{c['out'] - c['in']:.3f}", "-i", src,
                      "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", mp3])
                with open(mp3, "rb") as f:
                    res = _whisper(f.read())
            finally:
                os.remove(mp3)
        finally:
            if temp:
                os.remove(src)
        sp = c.get("speed") or 1
        words = res.get("words") or []
        if not words and res.get("text"):
            words = [{"word": res["text"], "start": 0, "end": (c["out"] - c["in"])}]
        for g in group_words(words):
            a, b = t0 + g["start"] / sp, t0 + g["end"] / sp
            if a < t0 + ln:
                caps.append({"start": round(a, 2), "end": round(min(b, t0 + ln), 2), "text": g["text"]})
    return caps
