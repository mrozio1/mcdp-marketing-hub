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

from . import db, images, studio

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
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
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
    return {"duration": dur, "w": v.get("width"), "h": v.get("height"),
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
            if got.get("v") == it.get("updated_at"):
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
    db.set_setting(info_key, json.dumps({"v": it.get("updated_at"), "info": info}))
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
    proj = {"type": "video", "w": size["w"], "h": size["h"], "clips": [], "captions": [], "lowers": [],
            "outro": {"on": True, "dur": 2.5, "text": "Book a visit"}, "music": None,
            "style": {"captions": "box", "position": "low"}}
    for cid in list(clip_ids)[:MAX_CLIPS]:
        try:
            info = media_info(cid)
        except VideoError:
            continue
        proj["clips"].append(_clip_from(info))
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
            return p
    except (OSError, ValueError):
        pass
    raise VideoError("That video project is gone.")


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def clean(p, old=None):
    """Keep only what the editor is allowed to set, within limits."""
    old = old or {}
    out = {"type": "video", "w": int(old.get("w") or p.get("w") or 1080), "h": int(old.get("h") or p.get("h") or 1920),
           "clips": [], "captions": [], "lowers": []}
    for c in (p.get("clips") or [])[:MAX_CLIPS]:
        it = studio.item(int(_num(c.get("item"), 0, 1e12, 0)))
        if not it or it["kind"] not in ("video", "image", "design"):
            continue
        if it["kind"] != "video":                      # a picture: shown for "out" seconds
            out["clips"].append({"item": it["id"], "in": 0, "out": round(_num(c.get("out"), 0.5, 30, 3), 2), "speed": 1,
                                 "volume": 0, "still": True, "fit": "fill" if c.get("fit") == "fill" else "fit"})
            continue
        a = _num(c.get("in"), 0, 36000, 0)
        b = _num(c.get("out"), a + 0.2, a + 600, a + 3)
        out["clips"].append({"item": it["id"], "in": round(a, 3), "out": round(b, 3),
                             "speed": min(SPEEDS, key=lambda s: abs(s - _num(c.get("speed"), 0.5, 2, 1))),
                             "volume": round(_num(c.get("volume"), 0, 2, 1), 2), "fit": "fit" if c.get("fit") == "fit" else "fill"})
    for c in (p.get("captions") or [])[:400]:
        t = re.sub(r"\s+", " ", str(c.get("text") or "")).strip()[:160]
        a = _num(c.get("start"), 0, MAX_TOTAL + 30, 0)
        b = _num(c.get("end"), a + 0.2, a + 20, a + 2)
        if t:
            out["captions"].append({"start": round(a, 3), "end": round(b, 3), "text": t})
    for c in (p.get("lowers") or [])[:20]:
        t = re.sub(r"\s+", " ", str(c.get("text") or "")).strip()[:80]
        a = _num(c.get("start"), 0, MAX_TOTAL + 30, 0)
        b = _num(c.get("end"), a + 0.5, a + 60, a + 4)
        if t:
            out["lowers"].append({"start": round(a, 3), "end": round(b, 3), "text": t,
                                  "sub": re.sub(r"\s+", " ", str(c.get("sub") or "")).strip()[:80]})
    o = p.get("outro") or {}
    out["outro"] = {"on": bool(o.get("on")), "dur": round(_num(o.get("dur"), 1, 8, 2.5), 2),
                    "text": re.sub(r"\s+", " ", str(o.get("text") or "")).strip()[:60]}
    m = p.get("music") or None
    if m and m.get("item"):
        it = studio.item(int(_num(m.get("item"), 0, 1e12, 0)))
        out["music"] = {"item": it["id"], "volume": round(_num(m.get("volume"), 0, 1.5, 0.25), 2)} \
            if it and it["kind"] in ("audio", "video") else None
    else:
        out["music"] = None
    st = p.get("style") or {}
    out["style"] = {"captions": "plain" if st.get("captions") == "plain" else "box",
                    "position": "middle" if st.get("position") == "middle" else "low"}
    if total(out) > MAX_TOTAL:
        raise VideoError(f"Keep it under {int(MAX_TOTAL // 60)} minutes (it's {total(out):.0f} seconds now).")
    return out


def clip_len(c):
    return (c["out"] if c.get("still") else (c["out"] - c["in"]) / (c.get("speed") or 1))


def total(p):
    return sum(clip_len(c) for c in p["clips"]) + (p["outro"]["dur"] if p.get("outro", {}).get("on") else 0)


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


def _piece(src, c, w, h, out, has_audio):
    """One clip, cropped to fill w×h at FPS, with sound (silence if none)."""
    tail = f"setsar=1,fps={FPS},format=yuv420p"
    if c.get("fit") == "fit":          # all of it, on a soft blurred copy of itself
        vf = (f"split[fa][fb];[fa]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=24:6,"
              f"eq=brightness=-0.06[bg];[fb]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}")
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},{tail}"
    dur = clip_len(c)
    if c.get("still"):
        args = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{dur:.3f}", "-i", src,
                "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
                "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-map", "1:a"]
    else:
        sp = c.get("speed") or 1
        tempo = []
        left = sp
        while left > 2.0:
            tempo.append("atempo=2.0")
            left /= 2.0
        while left < 0.5:
            tempo.append("atempo=0.5")
            left /= 0.5
        tempo.append(f"atempo={left:.4f}")
        args = ["ffmpeg", "-y", "-v", "error", "-ss", f"{c['in']:.3f}", "-t", f"{c['out'] - c['in']:.3f}", "-i", src,
                "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        fc = f"[0:v]setpts=(PTS-STARTPTS)/{sp},{vf}[v]"
        if has_audio and (c.get("volume") or 0) > 0:
            fc += (f";[0:a]asetpts=PTS-STARTPTS,{','.join(tempo)},volume={c['volume']},aresample=48000,"
                   f"aformat=channel_layouts=stereo[a0];[a0][1:a]amix=inputs=2:duration=longest:normalize=0[a]")
            args += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
        else:
            args += ["-filter_complex", fc, "-map", "[v]", "-map", "1:a"]
    args += ["-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS),
             "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", out]
    _run(args)


def render(iid, by=""):
    """Make the MP4 (in the background). The library file becomes the new video."""
    with _jobs_lock:
        if iid in _running:
            return False
        _running.add(iid)
    threading.Thread(target=_render, args=(iid, by), daemon=True).start()
    return True


def _render(iid, by):
    work = tempfile.mkdtemp(dir=studio._dir("tmp"))
    temps = []
    try:
        _need_ffmpeg()
        it = studio.item(iid)
        p = project(iid)
        if not p["clips"]:
            raise VideoError("Add at least one clip first.")
        pid = it["practice_id"] if it["practice_id"] not in (None, studio.MCDP) else None
        w, h = p["w"], p["h"]
        n = len(p["clips"]) + (1 if p["outro"]["on"] else 0) + 1
        _note(iid, state="running", step=0, steps=n, error="", started=time.time())
        pieces = []
        for i, c in enumerate(p["clips"]):
            src_it = studio.item(c["item"])
            if not src_it:
                raise VideoError("A clip's file was deleted from the library.")
            src, temp = source_path(src_it)
            if temp:
                temps.append(src)
            if src_it["kind"] == "design" or src_it["kind"] == "image":
                info = {"audio": False}
            else:
                info = probe(src)
            out = os.path.join(work, f"p{i:03d}.mp4")
            _piece(src, c, w, h, out, info["audio"])
            pieces.append(out)
            _note(iid, step=i + 1)
        if p["outro"]["on"]:
            png = os.path.join(work, "outro.png")
            with open(png, "wb") as f:
                f.write(outro_png(pid, w, h, p["outro"]["text"]))
            out = os.path.join(work, "outro.mp4")
            _piece(png, {"still": True, "out": p["outro"]["dur"], "fit": "fill"}, w, h, out, False)
            pieces.append(out)
            _note(iid, step=len(p["clips"]) + 1)
        lst = os.path.join(work, "list.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{x}'\n" for x in pieces)
        joined = os.path.join(work, "joined.mp4")
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
        final = os.path.join(work, "final.mp4")
        length = total(p)
        args = ["ffmpeg", "-y", "-v", "error", "-i", joined]
        vf = None
        if p["captions"] or p["lowers"]:
            ass = os.path.join(work, "subs.ass")
            with open(ass, "w") as f:
                f.write(ass_file(p, pid))
            fontsdir = FONT_DIR if os.path.isdir(FONT_DIR) else "/usr/share/fonts"
            vf = f"ass={ass}:fontsdir={fontsdir}"
        music = None
        if p.get("music"):
            m_it = studio.item(p["music"]["item"])
            if m_it:
                music, temp = source_path(m_it)
                if temp:
                    temps.append(music)
        if music:
            args += ["-stream_loop", "-1", "-i", music]
            fade = max(0, length - 1.5)
            fc = (f"[1:a]atrim=0:{length:.3f},volume={p['music']['volume']},afade=t=out:st={fade:.3f}:d=1.5,"
                  f"aresample=48000,aformat=channel_layouts=stereo[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]")
            if vf:
                fc = f"[0:v]{vf}[v];" + fc
                args += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
            else:
                args += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]"]
        elif vf:
            args += ["-vf", vf, "-map", "0:v", "-map", "0:a"]
        else:
            args += ["-map", "0:v", "-map", "0:a"]
        if vf or music:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]
        else:
            args += ["-c:v", "copy"]
        args += ["-c:a", "aac", "-b:a", "160k", "-t", f"{length:.3f}", "-movflags", "+faststart", final]
        _run(args, timeout=3600)
        thumb = os.path.join(work, "thumb.jpg")
        _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, length / 3):.2f}", "-i", final, "-frames:v", "1",
              "-vf", "scale=640:-2", thumb])
        with open(thumb, "rb") as f:
            tb = f.read()
        size = os.path.getsize(final)
        studio.replace_file(iid, final, it["title"] + ".mp4", "video/mp4", tb, by=by, log=False)
        with db.conn() as c:
            c.execute("UPDATE studio_items SET duration=?, width=?, height=? WHERE id=?", (round(length, 2), w, h, iid))
        studio.event(iid, "rendered", by, comment=f"{length:.0f} s · {size / 1048576:.1f} MB")
        _note(iid, state="done", step=n, error="", finished=time.time(), length=round(length, 1))
    except Exception as e:
        msg = str(e) if isinstance(e, VideoError) else f"Something went wrong making the video: {e}"
        _note(iid, state="error", error=msg[:400])
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
    """Captions for the whole cut, timed to the edit (trims and speed included)."""
    _need_ffmpeg()
    p = project(iid)
    caps, t0 = [], 0.0
    for c in p["clips"]:
        ln = clip_len(c)
        if c.get("still") or (c.get("volume") or 0) <= 0:
            t0 += ln
            continue
        src_it = studio.item(c["item"])
        src, temp = source_path(src_it)
        try:
            if not probe(src)["audio"]:
                t0 += ln
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
        t0 += ln
    return caps
