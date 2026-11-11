"""Studio video, version 2: CapCut-style layers, transitions, filters, motion and background removal.

Project (studio/designs/<id>.json):
  {"type": "video", "v": 2, "w": 1080, "h": 1920,
   "main":   [clip, ...]            the main track: plays in order; each clip can have a transition into it
   "layers": [layer, ...]           overlays on tracks 1..n (higher tracks on top): videos, pictures, text
   "captions", "lowers", "outro", "music", "style"   as in version 1}

clip:  {id, item, in, out, speed, volume, still?, fit: fill|fit, crop: {l,t,r,b}, zoom, dx, dy,
        filter: {preset, brightness, contrast, saturation, warmth, vignette}, motion, bg: {mode, cut, color, item},
        trans: {type, dur}}
layer: {id, kind: video|image|text, track, start, item?, in?, out?, speed?, volume?, dur?, x, y, w, h, rot, crop,
        opacity, filter, cut, anim: {in, out}, text?, font?, size?, color?, fill?, bold?, align?}

Everything here is shared by the hub's preview (studio-video.js does the same arithmetic) and the ffmpeg render.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import db, studio

FPS = 30
EPS = 2 / FPS                 # how much a piece runs on past a straight cut (hidden by a one-frame blend)
MAX_TOTAL = 180.0
MAX_MAIN = 40
MAX_LAYERS = 40
TRANSITIONS = (("none", "None"), ("fade", "Crossfade"), ("fadeblack", "Through black"), ("fadewhite", "Through white"),
               ("dissolve", "Dissolve"), ("wipeleft", "Wipe left"), ("wiperight", "Wipe right"), ("wipeup", "Wipe up"),
               ("wipedown", "Wipe down"), ("slideleft", "Slide left"), ("slideright", "Slide right"),
               ("slideup", "Slide up"), ("slidedown", "Slide down"), ("circleopen", "Circle open"),
               ("circleclose", "Circle close"), ("zoomin", "Zoom in"),
               ("pixelize", "Pixelate"), ("hblur", "Blur"), ("radial", "Clock wipe"), ("fadegrays", "Through grey"),
               ("horzopen", "Open across"), ("vertopen", "Open sideways"), ("squeezeh", "Squeeze"), ("diagtl", "Diagonal"),
               ("hlslice", "Slices"),
               ("glitchcut", "Color glitch"), ("shakecut", "Shake"), ("strobecut", "Strobe"), ("negcut", "Negative"),
               ("flashcut", "White flash"), ("punch", "Zoom punch"))
TRANS = dict(TRANSITIONS)
PRESETS = {
    "none": ("Original", {}),
    "warm": ("Warm", {"warmth": 0.5, "saturation": 0.1}),
    "cool": ("Cool", {"warmth": -0.5, "saturation": 0.05}),
    "vivid": ("Vivid", {"saturation": 0.35, "contrast": 0.12}),
    "bright": ("Bright", {"brightness": 0.12, "contrast": 0.05}),
    "soft": ("Soft", {"contrast": -0.15, "brightness": 0.05, "saturation": -0.1}),
    "film": ("Film", {"contrast": 0.1, "saturation": -0.15, "warmth": 0.2, "vignette": 0.35}),
    "vintage": ("Vintage", {"sepia": 0.45, "contrast": -0.08, "brightness": 0.04, "vignette": 0.4}),
    "bw": ("Black & white", {"saturation": -1, "contrast": 0.1}),
}
ADJUST = ("brightness", "contrast", "saturation", "warmth", "vignette")
MOTIONS = (("none", "None"), ("zoomin", "Slow zoom in"), ("zoomout", "Slow zoom out"), ("panleft", "Pan left"),
           ("panright", "Pan right"))
ANIM_IN = (("none", "None"), ("fade", "Fade"), ("pop", "Pop"), ("bounce", "Bounce"), ("zoomin", "Zoom in"), ("typewriter", "Typewriter"),
           ("wipe", "Wipe"), ("slideup", "Slide up"), ("slidedown", "Slide down"), ("slideleft", "Slide in"),
           ("slideright", "From the left"), ("drop", "Drop"), ("spin", "Spin"))
ANIM_OUT = (("none", "None"), ("fade", "Fade"), ("popout", "Shrink"), ("zoomout", "Grow"), ("typewriter", "Erase"), ("wipe", "Wipe"),
            ("slideup", "Slide up"), ("slidedown", "Slide down"), ("slideleft", "Slide left"), ("slideright", "Slide right"))
ANIM_LOOP = (("none", "None"), ("pulse", "Pulse"), ("bob", "Float"), ("wiggle", "Wiggle"), ("blink", "Blink"))
NO_FADE = ("none", "wipe", "typewriter")
BG_MODES = (("blur", "Blurred copy"), ("color", "Brand colour"), ("photo", "A photo"), ("black", "Black"))
SPEEDS = (0.5, 0.75, 1, 1.25, 1.5, 2)
# fonts for text layers (Google Fonts, free for any use), by kind; "single" ones have one weight only
FONT_GROUPS = (
    ("Clean", ("DejaVu Sans", "Montserrat", "Poppins", "Inter", "Public Sans", "Lato", "Roboto", "Open Sans", "Nunito", "Raleway",
               "Work Sans", "DM Sans", "Rubik", "Quicksand", "Josefin Sans", "Space Grotesk", "Comfortaa", "Fredoka")),
    ("Bold", ("Oswald", "Bebas Neue", "Anton", "Archivo Black", "Barlow Condensed", "Teko", "Alfa Slab One", "Righteous", "Bungee")),
    ("Classic", ("Playfair Display", "Fraunces", "Merriweather", "Cinzel", "Abril Fatface", "Lora", "Libre Baskerville")),
    ("Handwritten", ("Pacifico", "Caveat", "Dancing Script", "Great Vibes", "Satisfy", "Kalam", "Permanent Marker",
                     "Shadows Into Light", "Amatic SC", "Lobster", "Playball")),
    ("Fun", ("Monoton", "Press Start 2P", "Bangers", "Luckiest Guy", "Chewy")),
)
FONTS = tuple(f for _, fs in FONT_GROUPS for f in fs)
FONT_SINGLE = ("Bebas Neue", "Anton", "Archivo Black", "Alfa Slab One", "Righteous", "Bungee", "Abril Fatface", "Pacifico",
               "Great Vibes", "Satisfy", "Permanent Marker", "Shadows Into Light", "Lobster", "Playball", "Monoton",
               "Press Start 2P", "Bangers", "Luckiest Guy", "Chewy")
ANIM_T = 0.35


class V2Error(RuntimeError):
    pass


def _num(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(v):
        return default
    return max(lo, min(hi, v))


def _id(v):
    s = re.sub(r"[^a-zA-Z0-9_-]", "", str(v or ""))[:24]
    return s or os.urandom(4).hex()


def _color(v, default):
    v = str(v or "")
    return v if re.match(r"^#[0-9a-fA-F]{6}$", v) else default


# ------------------------------------------------------------------ cleaning (and moving version-1 projects over)
def _filter(f):
    f = f or {}
    out = {"preset": f.get("preset") if f.get("preset") in PRESETS else "none"}
    for k in ADJUST:
        out[k] = round(_num(f.get(k), -1 if k != "vignette" else 0, 1, 0), 2)
    return out


def _anim(a):
    """A layer's animations: how it comes in, goes out and moves in between, and how long in and out take."""
    return {"in": a.get("in") if a.get("in") in dict(ANIM_IN) else "none",
            "out": a.get("out") if a.get("out") in dict(ANIM_OUT) else "none",
            "loop": a.get("loop") if a.get("loop") in dict(ANIM_LOOP) else "none",
            "din": round(_num(a.get("din"), 0.15, 3, ANIM_T), 2), "dout": round(_num(a.get("dout"), 0.15, 3, ANIM_T), 2)}


def anim_times(L, ln):
    """In and out lengths that fit the layer (they never overlap)."""
    a = L["anim"]
    din = a.get("din", ANIM_T) if a["in"] != "none" else 0
    dout = a.get("dout", ANIM_T) if a["out"] != "none" else 0
    if din + dout > ln * 0.9 and din + dout > 0:
        k = ln * 0.9 / (din + dout)
        din, dout = din * k, dout * k
    return max(din, 0.01) if a["in"] != "none" else 0, max(dout, 0.01) if a["out"] != "none" else 0


def anim_exprs(L, ln, W, H):
    """The animation as ffmpeg expressions of the layer's own time T (same arithmetic as anim() in studio-video.js):
    scale, extra turn (radians), x and y offsets; None where nothing moves."""
    a = L["anim"]
    din, dout = anim_times(L, ln)
    pi = f"clip(T/{din:.4f},0,1)" if din else "1"
    po = f"clip((T-{ln - dout:.4f})/{dout:.4f},0,1)" if dout else "0"
    E = lambda p: f"(1-pow(1-{p},3))"
    BK = lambda p: f"(1+2.70158*pow({p}-1,3)+1.70158*pow({p}-1,2))"
    Q = lambda p: f"pow({p},2)"
    sc, rot, dx, dy = [], [], [], []
    i, o, lp = a["in"], a["out"], a.get("loop", "none")
    if i == "pop": sc.append(f"(0.7+0.3*{E(pi)})")
    if i == "bounce": sc.append(f"max(0.02,{BK(pi)})")
    if i == "zoomin": sc.append(f"(1.6-0.6*{E(pi)})")
    if i == "spin": sc.append(f"(0.5+0.5*{E(pi)})"); rot.append(f"(-1.5708*(1-{E(pi)}))")
    if i == "slideup": dy.append(f"((1-{E(pi)})*{H * 0.12:.1f})")
    if i == "slidedown": dy.append(f"(-(1-{E(pi)})*{H * 0.12:.1f})")
    if i == "slideleft": dx.append(f"((1-{E(pi)})*{W * 0.25:.1f})")
    if i == "slideright": dx.append(f"(-(1-{E(pi)})*{W * 0.25:.1f})")
    if i == "drop": dy.append(f"(-(1-{BK(pi)})*{H * 0.2:.1f})")
    if o == "popout": sc.append(f"(1-0.7*{Q(po)})")
    if o == "zoomout": sc.append(f"(1+0.5*{Q(po)})")
    if o == "slideup": dy.append(f"(-{Q(po)}*{H * 0.12:.1f})")
    if o == "slidedown": dy.append(f"({Q(po)}*{H * 0.12:.1f})")
    if o == "slideleft": dx.append(f"(-{Q(po)}*{W * 0.25:.1f})")
    if o == "slideright": dx.append(f"({Q(po)}*{W * 0.25:.1f})")
    if lp == "pulse": sc.append("(1+0.06*sin(2*PI*T/1.2))")
    if lp == "bob": dy.append(f"({H * 0.008:.2f}*sin(2*PI*T/1.6))")
    if lp == "wiggle": rot.append("(0.07*sin(2*PI*T/0.8))")
    j = lambda xs: "*".join(xs) if xs else None
    k = lambda xs: "+".join(xs) if xs else None
    return {"scale": j(sc), "rot": k(rot), "dx": k(dx), "dy": k(dy), "din": din, "dout": dout}


def _crop(c):
    c = c or {}
    out = {k: round(_num(c.get(k), 0, 0.9, 0), 4) for k in ("l", "t", "r", "b")}
    if out["l"] + out["r"] > 0.9:
        out["r"] = round(0.9 - out["l"], 4)
    if out["t"] + out["b"] > 0.9:
        out["b"] = round(0.9 - out["t"], 4)
    return out


def _source(item_id, kinds):
    it = studio.item(int(_num(item_id, 0, 1e12, 0)))
    return it if it and it["kind"] in kinds else None


def migrate(p):
    """A version-1 project (clips in a row) as version 2."""
    if p.get("v") == 2 or "main" in p or "layers" in p:
        return {**p, "v": 2, "main": p.get("main") or [], "layers": p.get("layers") or [], "effects": p.get("effects") or []}
    q = {k: p.get(k) for k in ("w", "h", "captions", "lowers", "outro", "music", "style")}
    q.update(type="video", v=2, layers=[], main=[], effects=[])
    for c in p.get("clips") or []:
        m = dict(c)
        m["id"] = _id(None)
        q["main"].append(m)
    return q


def clean(p, w, h):
    p = migrate(p or {})
    out = {"type": "video", "v": 2, "w": w, "h": h, "main": [], "layers": [], "captions": [], "lowers": []}
    for c in (p.get("main") or [])[:MAX_MAIN]:
        it = _source(c.get("item"), ("video", "image", "design"))
        if not it:
            continue
        still = it["kind"] != "video"
        m = {"id": _id(c.get("id")), "item": it["id"]}
        if still:
            m.update({"in": 0, "out": round(_num(c.get("out"), 0.5, 30, 3), 2), "speed": 1, "volume": 0, "still": True})
        else:
            a = _num(c.get("in"), 0, 36000, 0)
            m.update({"in": round(a, 3), "out": round(_num(c.get("out"), a + 0.2, a + 600, a + 3), 3),
                      "speed": min(SPEEDS, key=lambda s: abs(s - _num(c.get("speed"), 0.5, 2, 1))),
                      "volume": round(_num(c.get("volume"), 0, 2, 1), 2)})
        m["fit"] = c.get("fit") if c.get("fit") in ("fill", "fit") else ("fit" if still else "fill")
        m["crop"] = _crop(c.get("crop"))
        m["zoom"] = round(_num(c.get("zoom"), 0.3, 5, 1), 3)
        m["dx"] = round(_num(c.get("dx"), -1, 1, 0), 4)
        m["dy"] = round(_num(c.get("dy"), -1, 1, 0), 4)
        m["filter"] = _filter(c.get("filter"))
        m["motion"] = c.get("motion") if c.get("motion") in dict(MOTIONS) else "none"
        bg = c.get("bg") or {}
        bg_item = _source(bg.get("item"), ("image", "design")) if bg.get("mode") == "photo" else None
        m["bg"] = {"mode": bg.get("mode") if bg.get("mode") in dict(BG_MODES) else "blur",
                   "cut": bool(bg.get("cut")) and not still, "color": _color(bg.get("color"), "#0F7471"),
                   "item": bg_item["id"] if bg_item else None}
        if m["bg"]["mode"] == "photo" and not bg_item:
            m["bg"]["mode"] = "blur"
        tr = c.get("trans") or {}
        m["trans"] = {"type": tr.get("type") if tr.get("type") in TRANS else "none",
                      "dur": round(_num(tr.get("dur"), 0.2, 2, 0.5), 2)}
        out["main"].append(m)
    if out["main"]:
        out["main"][0]["trans"] = {"type": "none", "dur": 0.5}
    for c in (p.get("layers") or [])[:MAX_LAYERS]:
        kind = c.get("kind") if c.get("kind") in ("video", "image", "text") else None
        if not kind:
            continue
        L = {"id": _id(c.get("id")), "kind": kind, "track": int(_num(c.get("track"), 1, 12, 1)),
             "start": round(_num(c.get("start"), 0, MAX_TOTAL, 0), 3)}
        if kind == "video":
            it = _source(c.get("item"), ("video",))
            if not it:
                continue
            a = _num(c.get("in"), 0, 36000, 0)
            L.update({"item": it["id"], "in": round(a, 3), "out": round(_num(c.get("out"), a + 0.2, a + 600, a + 3), 3),
                      "speed": min(SPEEDS, key=lambda s: abs(s - _num(c.get("speed"), 0.5, 2, 1))),
                      "volume": round(_num(c.get("volume"), 0, 2, 0), 2), "cut": bool(c.get("cut"))})
        else:
            L["dur"] = round(_num(c.get("dur"), 0.3, MAX_TOTAL, 3), 3)
            if kind == "image":
                it = _source(c.get("item"), ("image", "design"))
                if not it:
                    continue
                L["item"] = it["id"]
            else:
                txt = str(c.get("text") or "").replace("\r", "")[:300].strip()
                if not txt:
                    continue
                L.update({"text": txt, "font": c.get("font") if c.get("font") in FONTS else "DejaVu Sans",
                          "size": round(_num(c.get("size"), 10, 400, 72)), "color": _color(c.get("color"), "#FFFFFF"),
                          "fill": _color(c.get("fill"), "") if c.get("fill") else "", "bold": bool(c.get("bold", True)),
                          "align": c.get("align") if c.get("align") in ("left", "center", "right") else "center",
                          "stroke": _color(c.get("stroke"), "") if c.get("stroke") else "",
                          "strokew": round(_num(c.get("strokew"), 0.01, 0.3, 0.08), 3), "shadow": bool(c.get("shadow")),
                          "caps": bool(c.get("caps"))})
        L.update({"x": round(_num(c.get("x"), -w, 2 * w, w * 0.25), 1), "y": round(_num(c.get("y"), -h, 2 * h, h * 0.25), 1),
                  "w": round(_num(c.get("w"), 8, 4 * w, w * 0.5), 1), "h": round(_num(c.get("h"), 8, 4 * h, h * 0.2), 1),
                  "rot": round(_num(c.get("rot"), -360, 360, 0), 2), "crop": _crop(c.get("crop")),
                  "opacity": round(_num(c.get("opacity"), 0, 1, 1), 2), "filter": _filter(c.get("filter")),
                  "anim": _anim(c.get("anim") or {})})
        out["layers"].append(L)
    for c in (p.get("captions") or [])[:400]:
        t = re.sub(r"\s+", " ", str(c.get("text") or "")).strip()[:160]
        a = _num(c.get("start"), 0, MAX_TOTAL + 30, 0)
        if t:
            out["captions"].append({"start": round(a, 3), "end": round(_num(c.get("end"), a + 0.2, a + 20, a + 2), 3), "text": t})
    for c in (p.get("lowers") or [])[:20]:
        t = re.sub(r"\s+", " ", str(c.get("text") or "")).strip()[:80]
        a = _num(c.get("start"), 0, MAX_TOTAL + 30, 0)
        if t:
            out["lowers"].append({"start": round(a, 3), "end": round(_num(c.get("end"), a + 0.5, a + 60, a + 4), 3), "text": t,
                                  "sub": re.sub(r"\s+", " ", str(c.get("sub") or "")).strip()[:80]})
    o = p.get("outro") or {}
    tr = o.get("trans") or {}
    out["outro"] = {"on": bool(o.get("on")), "dur": round(_num(o.get("dur"), 1, 8, 2.5), 2),
                    "text": re.sub(r"\s+", " ", str(o.get("text") or "")).strip()[:60],
                    "trans": {"type": tr.get("type") if tr.get("type") in TRANS else "fade", "dur": round(_num(tr.get("dur"), 0.2, 1.5, 0.4), 2)}}
    m = p.get("music") or None
    it = _source((m or {}).get("item"), ("audio", "video")) if m else None
    out["music"] = {"item": it["id"], "volume": round(_num(m.get("volume"), 0, 1.5, 0.25), 2)} if it else None
    st = p.get("style") or {}
    out["style"] = {"captions": "plain" if st.get("captions") == "plain" else "box",
                    "position": "middle" if st.get("position") == "middle" else "low"}
    lay = layout(out)
    end = lay["end"]
    if end > MAX_TOTAL:
        raise V2Error(f"Keep it under {int(MAX_TOTAL // 60)} minutes (it's {end:.0f} seconds now).")
    out["layers"] = [L for L in out["layers"] if L["start"] < max(end, 0.1)]
    from . import vfx
    out["effects"] = vfx.clean(p.get("effects"), end)
    for e in out["effects"]:
        e["dur"] = round(min(e["dur"], max(0.2, end - e["start"])), 3)
    for L in out["layers"]:                       # nothing runs past the end of the main track
        room = max(0.1, end - L["start"])
        if layer_len(L) > room:
            if L["kind"] == "video":
                L["out"] = round(L["in"] + room * L["speed"], 3)
            else:
                L["dur"] = round(room, 3)
    return out


# ------------------------------------------------------------------ timing
def clip_len(c):
    return c["out"] if c.get("still") else (c["out"] - c["in"]) / (c.get("speed") or 1)


def layer_len(L):
    return (L["out"] - L["in"]) / (L.get("speed") or 1) if L["kind"] == "video" else L["dur"]


def layout(p):
    """Where each main clip (and the end card) sits, with transitions overlapping neighbours."""
    segs, t, prev = [], 0.0, None
    items = [("clip", c) for c in p["main"]]
    if p.get("outro", {}).get("on") and p["main"]:
        items.append(("outro", {"out": p["outro"]["dur"], "still": True, "trans": p["outro"].get("trans") or {"type": "none"}}))
    for kind, c in items:
        ln = clip_len(c)
        d = 0.0
        tr = c.get("trans") or {}
        if prev is not None and tr.get("type", "none") != "none":
            d = min(tr.get("dur", 0.5), prev["len"] / 2, ln / 2)
        start = (prev["end"] - d) if prev else 0.0
        seg = {"kind": kind, "c": c, "start": round(start, 4), "len": ln, "end": round(start + ln, 4), "d": round(d, 4),
               "trans": tr.get("type", "none") if d else "none"}
        segs.append(seg)
        prev = seg
    return {"segs": segs, "end": segs[-1]["end"] if segs else 0.0}


# ------------------------------------------------------------------ colour: one matrix and a contrast curve
def grade(f):
    f = f or {}
    base = dict(PRESETS.get(f.get("preset") or "none", ("", {}))[1])
    vals = {k: base.get(k, 0) + (f.get(k) or 0) for k in ADJUST}
    vals["sepia"] = base.get("sepia", 0)
    vals = {k: max(-1.0, min(1.0, v)) for k, v in vals.items()}
    vals["vignette"] = max(0.0, vals["vignette"])
    I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    m = vals["sepia"]
    sep = [[.393, .769, .189], [.349, .686, .168], [.272, .534, .131]]
    S = [[(1 - m) * I[i][j] + m * sep[i][j] for j in range(3)] for i in range(3)]
    k = 1 + vals["saturation"]
    lw = (0.2126, 0.7152, 0.0722)
    Sat = [[(1 - k) * lw[j] + (k if i == j else 0) for j in range(3)] for i in range(3)]
    w = vals["warmth"]
    Wm = [[1 + 0.12 * w, 0, 0], [0, 1 + 0.02 * w, 0], [0, 0, 1 - 0.12 * w]]
    b = 1 + 0.5 * vals["brightness"]
    B = [[b, 0, 0], [0, b, 0], [0, 0, b]]
    M = mul(B, mul(Wm, mul(Sat, S)))
    return {"matrix": [[round(x, 5) for x in row] for row in M], "contrast": round(1 + vals["contrast"], 4),
            "vignette": round(vals["vignette"], 3), "neutral": M == I and vals["contrast"] == 0 and vals["vignette"] == 0}


def grade_filter(f):
    g = grade(f)
    if g["neutral"]:
        return ""
    M = g["matrix"]
    parts = [f"colorchannelmixer=rr={M[0][0]}:rg={M[0][1]}:rb={M[0][2]}:gr={M[1][0]}:gg={M[1][1]}:gb={M[1][2]}:"
             f"br={M[2][0]}:bg={M[2][1]}:bb={M[2][2]}"]
    if g["contrast"] != 1:
        k = g["contrast"]
        e = f"clip((val-128)*{k}+128\\,0\\,255)"
        parts.append(f"lutrgb=r='{e}':g='{e}':b='{e}'")
    if g["vignette"] > 0:
        parts.append(f"vignette=angle={0.2 + 0.6 * g['vignette']:.3f}")
    return ",".join(parts)


# ------------------------------------------------------------------ geometry
def even(v):
    return max(2, int(round(v / 2)) * 2)


def crop_box(sw, sh, crop):
    l, t, r, b = crop["l"], crop["t"], crop["r"], crop["b"]
    cw, ch = max(2, sw * (1 - l - r)), max(2, sh * (1 - t - b))
    return even(cw), even(ch), int(sw * l), int(sh * t)


def main_box(c, sw, sh, W, H):
    """Where a main clip's (cropped) picture sits on the frame."""
    cw, ch, _, _ = crop_box(sw, sh, c["crop"])
    s0 = (max if c["fit"] == "fill" else min)(W / cw, H / ch)
    s = s0 * c.get("zoom", 1)
    fw, fh = cw * s, ch * s
    cx, cy = W / 2 + c.get("dx", 0) * W, H / 2 + c.get("dy", 0) * H
    return {"x": cx - fw / 2, "y": cy - fh / 2, "w": fw, "h": fh}


def motion_expr(kind, L, W, H):
    """A per-frame zoom (scale + crop) for slow zooms and pans, as ffmpeg expressions; None for none."""
    if kind == "none":
        return None
    z = {"zoomin": f"(1+0.15*t/{L:.3f})", "zoomout": f"(1.15-0.15*t/{L:.3f})"}.get(kind, "1.12")
    px = {"panleft": f"(iw-{W})*(1-t/{L:.3f})", "panright": f"(iw-{W})*t/{L:.3f}"}.get(kind, f"(iw-{W})/2")
    return (f"scale=w='ceil({W}*{z}/2)*2':h='ceil({H}*{z}/2)*2':eval=frame,"
            f"crop=w={W}:h={H}:x='{px}':y='(ih-{H})/2'")


# ------------------------------------------------------------------ running ffmpeg
def run(args, timeout=3600):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        lines_ = [x for x in p.stderr.decode(errors="replace").strip().splitlines() if x.strip()]
        if os.environ.get("VIDEO_DEBUG"):
            print("FFMPEG FAILED:", " ".join(args)[:6000], "\n".join(lines_[-30:]))
        tail = lines_[-4:]
        raise V2Error("The video couldn't be made: " + " | ".join(tail)[:500])
    return p


def rotation(stream):
    """A phone video's turn (degrees) from its display matrix or old-style tag; ffmpeg applies it when decoding."""
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int((stream.get("tags") or {}).get("rotate") or 0) % 360
    except ValueError:
        return 0


def shown_size(stream):
    """Width and height as the video plays (a sideways-recorded phone video is tall)."""
    w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
    return (h, w) if rotation(stream) in (90, 270) else (w, h)


def probe_dims(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,codec_type:stream_side_data=rotation:stream_tags=rotate",
                        "-show_entries", "format=duration", "-of", "json", path],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    try:
        j = json.loads(p.stdout or b"{}")
        s = (j.get("streams") or [{}])[0]
        a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        w, h = shown_size(s)
        return w, h, bool(a.stdout.strip())
    except (ValueError, IndexError):
        return 0, 0, False


def _tempo(sp):
    out, left = [], sp
    while left > 2.0:
        out.append("atempo=2.0")
        left /= 2.0
    while left < 0.5:
        out.append("atempo=0.5")
        left /= 0.5
    out.append(f"atempo={left:.4f}")
    return ",".join(out)


def piece_main(src, c, W, H, out, length, has_audio, mask=None, bg_photo=None, sw=0, sh=0):
    """One main-track clip on its own: cropped, placed, background behind it, graded, moving; with its sound."""
    still = bool(c.get("still"))
    sp = c.get("speed") or 1
    args = ["ffmpeg", "-y", "-v", "error"]
    if still:
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{length:.3f}", "-i", src]
    else:
        args += ["-ss", f"{c['in']:.3f}", "-t", f"{c['out'] - c['in']:.3f}", "-i", src]
    n = 1
    mask_i = bg_i = None
    if mask:
        args += ["-ss", f"{c['in']:.3f}", "-t", f"{c['out'] - c['in']:.3f}", "-i", mask]
        mask_i, n = n, n + 1
    if bg_photo:
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{length:.3f}", "-i", bg_photo]
        bg_i, n = n, n + 1
    args += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    silence = n
    cw, ch, cx, cy = crop_box(sw, sh, c["crop"])
    box = main_box(c, sw, sh, W, H)
    fw, fh = even(box["w"]), even(box["h"])
    timing = "setpts=PTS-STARTPTS" if still else f"setpts=(PTS-STARTPTS)/{sp}"
    f = [f"[0:v]{timing},fps={FPS},crop={cw}:{ch}:{cx}:{cy},format=rgba,split=2[src][bgsrc]"]
    if mask_i is not None:
        f.append(f"[{mask_i}:v]setpts=(PTS-STARTPTS)/{sp},fps={FPS},scale={sw}:{sh},crop={cw}:{ch}:{cx}:{cy},format=gray[m]")
        f.append("[src][m]alphamerge[srca]")
        fg_in = "srca"
    else:
        fg_in = "src"
    f.append(f"[{fg_in}]scale={fw}:{fh}[fg]")
    mode = c["bg"]["mode"]
    if mode == "color":
        col = c["bg"]["color"].lstrip("#")
        f.append(f"color=c=0x{col}:s={W}x{H}:r={FPS}:d={length:.3f},format=rgba[bg]")
        f.append("[bgsrc]nullsink")
    elif mode == "black":
        f.append(f"color=c=black:s={W}x{H}:r={FPS}:d={length:.3f},format=rgba[bg]")
        f.append("[bgsrc]nullsink")
    elif mode == "photo" and bg_i is not None:
        f.append(f"[{bg_i}:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},format=rgba[bg]")
        f.append("[bgsrc]nullsink")
    else:
        f.append(f"[bgsrc]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:6,"
                 f"eq=brightness=-0.06,format=rgba[bg]")
    f.append(f"[bg][fg]overlay=x={int(round(box['x']))}:y={int(round(box['y']))}:shortest=1,format=rgb24[comp]")
    chain = []
    mo = motion_expr(c.get("motion", "none"), length, W, H)
    if mo:
        chain.append(mo)
    gf = grade_filter(c.get("filter"))
    if gf:
        chain.append(gf)
    chain += ["setsar=1", "format=yuv420p"]
    f.append(f"[comp]{','.join(chain)}[v]")
    vol = c.get("volume") or 0
    if has_audio and not still and vol > 0:
        f.append(f"[0:a]asetpts=PTS-STARTPTS,{_tempo(sp)},volume={vol},aresample=48000,aformat=channel_layouts=stereo[a0]")
        f.append(f"[a0][{silence}:a]amix=inputs=2:duration=longest:normalize=0[a]")
        amap = "[a]"
    else:
        amap = f"{silence}:a"
    args += ["-filter_complex", ";".join(f), "-map", "[v]", "-map", amap, "-t", f"{length:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k",
             "-ar", "48000", "-ac", "2", out]
    run(args)


def join_main(pieces, segs, out, total_len):
    """Pieces in order; straight cuts and transitions both done with xfade (a cut is a one-frame blend)."""
    if len(pieces) == 1:
        shutil.copyfile(pieces[0], out)
        return
    args = ["ffmpeg", "-y", "-v", "error"]
    for p_ in pieces:
        args += ["-i", p_]
    f, last = [], "0:v"
    for k in range(1, len(pieces)):
        s = segs[k]
        typ = s["trans"] if s["d"] > 0 else "fade"
        if typ in _trans_fx():                 # an effect transition: a crossfade, with the effect over the cut
            typ = "fade"
        dur = s["d"] if s["d"] > 0 else EPS
        lab = f"v{k}"
        f.append(f"[{last}][{k}:v]xfade=transition={typ}:duration={dur:.4f}:offset={s['start']:.4f}[{lab}]")
        last = lab
    amix = []
    for k, s in enumerate(segs):
        fades = []
        if s["d"] > 0:
            fades.append(f"afade=t=in:st=0:d={s['d']:.3f}")
        nxt = segs[k + 1] if k + 1 < len(segs) else None
        if nxt and nxt["d"] > 0:
            fades.append(f"afade=t=out:st={s['len'] - nxt['d']:.3f}:d={nxt['d']:.3f}")
        ms = int(round(s["start"] * 1000))
        chain = ",".join([f"atrim=0:{s['len']:.3f}"] + fades + [f"adelay={ms}|{ms}"])
        f.append(f"[{k}:a]{chain}[a{k}]")
        amix.append(f"[a{k}]")
    f.append("".join(amix) + f"amix=inputs={len(amix)}:duration=longest:normalize=0,atrim=0:{total_len:.3f}[a]")
    args += ["-filter_complex", ";".join(f), "-map", f"[{last}]", "-map", "[a]", "-t", f"{total_len:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", out]
    run(args)


def _trans_fx():
    from . import vfx
    return vfx.TRANS_FX


def text_png(L, path):
    """Server-side stand-in for a text layer the browser didn't send (DejaVu, wrapped to the box)."""
    from PIL import Image, ImageDraw, ImageFont
    w, h = even(L["w"]), even(L["h"])
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font_file = os.path.join("/usr/share/fonts/truetype/dejavu", "DejaVuSans-Bold.ttf" if L.get("bold") else "DejaVuSans.ttf")
    try:
        font = ImageFont.truetype(font_file, int(L["size"]))
    except OSError:
        font = ImageFont.load_default()
    if L.get("fill"):
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(min(w, h) * 0.12), fill=L["fill"])
    lines = (L["text"].upper() if L.get("caps") else L["text"]).split("\n")
    lh = int(L["size"] * 1.2)
    y = (h - lh * len(lines)) / 2
    sw = max(1, int(L["size"] * L.get("strokew", 0.08))) if L.get("stroke") else 0
    for line in lines:
        tw = d.textlength(line, font=font)
        x = {"left": L["size"] * 0.3, "right": w - tw - L["size"] * 0.3}.get(L.get("align"), (w - tw) / 2)
        if L.get("shadow"):
            off = max(2, int(L["size"] * 0.06))
            d.text((x + off, y + off), line, font=font, fill=(0, 0, 0, 140), stroke_width=sw, stroke_fill=(0, 0, 0, 140))
        d.text((x, y), line, font=font, fill=L["color"], stroke_width=sw, stroke_fill=L.get("stroke") or None)
        y += lh
    img.save(path, "PNG")


def overlay_stage(main_mp4, p, layers_src, out, total_len, ass=None, fontsdir=None, music=None):
    """Layers on top of the main track (in track order), then captions and name tags, then all the sound."""
    W, H = p["w"], p["h"]
    args = ["ffmpeg", "-y", "-v", "error", "-i", main_mp4]
    f, n, base = [], 1, "0:v"
    audio = ["[0:a]"]
    order = sorted(p["layers"], key=lambda L: (L["track"], L["start"]))
    for idx, L in enumerate(order):
        src = layers_src.get(L["id"])
        if not src:
            continue
        ln = layer_len(L)
        st = L["start"]
        if L["kind"] == "video":
            args += ["-ss", f"{L['in']:.3f}", "-t", f"{L['out'] - L['in']:.3f}", "-i", src["path"]]
            vi = n
            n += 1
            timing = f"setpts=(PTS-STARTPTS)/{L['speed']}"
        else:
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{ln:.3f}", "-i", src["path"]]
            vi = n
            n += 1
            timing = "setpts=PTS-STARTPTS"
        chain = [timing, f"fps={FPS}", "format=rgba"]
        if L["kind"] != "text":
            cw, ch, cx, cy = crop_box(src["w"], src["h"], L["crop"])
            chain.append(f"crop={cw}:{ch}:{cx}:{cy}")
        lab_src = f"ls{idx}"
        f.append(f"[{vi}:v]{','.join(chain)}[{lab_src}]")
        if src.get("mask"):
            args += ["-ss", f"{L['in']:.3f}", "-t", f"{L['out'] - L['in']:.3f}", "-i", src["mask"]]
            mi = n
            n += 1
            cw, ch, cx, cy = crop_box(src["w"], src["h"], L["crop"])
            f.append(f"[{mi}:v]setpts=(PTS-STARTPTS)/{L['speed']},fps={FPS},scale={src['w']}:{src['h']},"
                     f"crop={cw}:{ch}:{cx}:{cy},format=gray[lm{idx}]")
            f.append(f"[{lab_src}][lm{idx}]alphamerge[lsa{idx}]")
            lab_src = f"lsa{idx}"
        lw, lh = even(L["w"]), even(L["h"])
        gf = grade_filter(L.get("filter")) if L["kind"] != "text" else ""
        ax = anim_exprs(L, ln, W, H)
        q = lambda e, tvar: "'" + e.replace("T", tvar).replace(",", "\\,") + "'"
        steps = [f"scale={lw}:{lh}"]
        if L["opacity"] < 1:
            steps.append(f"colorchannelmixer=aa={L['opacity']}")
        lab = f"l{idx}"
        if gf:
            # grade the colour, keep the transparency
            f.append(f"[{lab_src}]split[gc{idx}][ga{idx}];[ga{idx}]alphaextract[gx{idx}];"
                     f"[gc{idx}]format=rgb24,{gf},format=rgba[gg{idx}];[gg{idx}][gx{idx}]alphamerge[gm{idx}]")
            lab_src = f"gm{idx}"
        f.append(f"[{lab_src}]{','.join(steps)},fps={FPS},format=rgba,settb=1/{FPS}[lr{idx}]")
        cur = f"lr{idx}"
        # typewriter and wipe: revealed (or hidden) from the left, through a transparent picture
        n_chars = max(1, len((L.get("text") or "").replace(" ", "").replace("\n", ""))) if L["kind"] == "text" else 12
        for side in ("in", "out"):
            kind = L["anim"][side]
            d = ax["din"] if side == "in" else ax["dout"]
            if kind not in ("wipe", "typewriter") or not d:
                continue
            f.append(f"color=c=black@0:s={lw}x{lh}:r={FPS}:d={d + 0.1:.3f},format=rgba,settb=1/{FPS}[bl{side}{idx}]")
            prog = "(1-P)"
            if side == "in":
                cut = f"W*floor({prog}*{n_chars})/{n_chars}" if kind == "typewriter" else f"W*{prog}"
                expr = f"if(lt(X,{cut}),B,A)"
                f.append(f"[bl{side}{idx}][{cur}]xfade=transition=custom:duration={d:.3f}:offset=0:expr='{expr.replace(',', chr(92) + ',')}'[rv{side}{idx}]")
            else:
                if kind == "typewriter":
                    expr = f"if(lt(X,W*(1-floor({prog}*{n_chars})/{n_chars})),A,B)"
                else:
                    expr = f"if(lt(X,W*{prog}),B,A)"
                f.append(f"[{cur}][bl{side}{idx}]xfade=transition=custom:duration={d:.3f}:offset={max(0, ln - d):.3f}:"
                         f"expr='{expr.replace(',', chr(92) + ',')}'[rv{side}{idx}]")
            cur = f"rv{side}{idx}"
        steps = []
        if L["anim"]["in"] not in NO_FADE:
            steps.append(f"fade=t=in:st=0:d={ax['din']:.3f}:alpha=1")
        if L["anim"]["out"] not in NO_FADE:
            steps.append(f"fade=t=out:st={max(0, ln - ax['dout']):.3f}:d={ax['dout']:.3f}:alpha=1")
        if ax["scale"]:
            sw_ = q("max(2,ceil(iw*" + ax["scale"] + "/2)*2)", "t")
            sh_ = q("max(2,ceil(ih*" + ax["scale"] + "/2)*2)", "t")
            steps.append(f"scale=w={sw_}:h={sh_}:eval=frame")
        base_rot = math.radians(L["rot"])
        if ax["rot"]:
            ang = q(f"{base_rot:.5f}+{ax['rot']}", "t")
            steps.append(f"rotate=a={ang}:c=none:ow='hypot(iw\\,ih)':oh='hypot(iw\\,ih)'")
        elif abs(L["rot"]) > 0.01:
            ang = f"{base_rot:.5f}"
            steps.append(f"rotate=a={ang}:c=none:ow='rotw({ang})':oh='roth({ang})'")
        steps.append(f"setpts=PTS+{st:.3f}/TB")
        f.append(f"[{cur}]{','.join(steps)}[{lab}]")
        cx_, cy_ = L["x"] + L["w"] / 2, L["y"] + L["h"] / 2
        tl_ = f"(t-{st:.3f})"
        xe = f"{cx_:.1f}-w/2" + (f"+{ax['dx'].replace('T', tl_)}" if ax["dx"] else "")
        ye = f"{cy_:.1f}-h/2" + (f"+{ax['dy'].replace('T', tl_)}" if ax["dy"] else "")
        xe, ye = xe.replace(",", "\\,"), ye.replace(",", "\\,")
        on = f"between(t\\,{st:.3f}\\,{st + ln:.3f})"
        if L["anim"].get("loop") == "blink":
            on += f"*lt(mod(t-{st:.3f}\\,0.8)\\,0.55)"
        nb = f"b{idx}"
        f.append(f"[{base}][{lab}]overlay=x='{xe}':y='{ye}':enable='{on}':eof_action=pass[{nb}]")
        base = nb
        if L["kind"] == "video" and L.get("volume", 0) > 0 and src.get("audio"):
            ms = int(round(st * 1000))
            f.append(f"[{vi}:a]asetpts=PTS-STARTPTS,{_tempo(L['speed'])},volume={L['volume']},aresample=48000,"
                     f"aformat=channel_layouts=stereo,adelay={ms}|{ms}[la{idx}]")
            audio.append(f"[la{idx}]")
    if ass:
        f.append(f"[{base}]ass={ass}:fontsdir={fontsdir}[vs]")
        base = "vs"
    if music:
        args += ["-stream_loop", "-1", "-i", music["path"]]
        mi = n
        n += 1
        fade = max(0, total_len - 1.5)
        f.append(f"[{mi}:a]atrim=0:{total_len:.3f},volume={music['volume']},afade=t=out:st={fade:.3f}:d=1.5,"
                 f"aresample=48000,aformat=channel_layouts=stereo[mu]")
        audio.append("[mu]")
    if len(audio) > 1:
        f.append("".join(audio) + f"amix=inputs={len(audio)}:duration=first:normalize=0[aout]")
        amap = "[aout]"
    else:
        amap = "0:a"
    if base == "0:v":
        f.append("[0:v]null[vfinal]")
    else:
        f.append(f"[{base}]format=yuv420p[vfinal]")
    args += ["-filter_complex", ";".join(f), "-map", "[vfinal]", "-map", amap, "-t", f"{total_len:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
             "-movflags", "+faststart", out]
    run(args)
