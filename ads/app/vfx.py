"""Video effects for the Studio editor: CapCut-style effects that sit on their own track over the video.

An effect is a segment of time: {"id", "type", "start", "dur", "k" (strength 0-1), "scope": "all"|"main", "track"}.
"all" changes everything under it (clips, pictures and text layers); "main" only the main track. Captions and
name tags always stay clean on top. Some transitions are also effects ("Color glitch", "Shake"…): the clips
crossfade and the effect plays over the cut.

Each effect is made twice, with the same arithmetic: here as an ffmpeg filter chain for the MP4, and in
static/studio-video.js for the live preview. The falling and floating overlays (snow, hearts, confetti…) are
pictures drawn here once (seeded, so they're always the same) and used by both.
"""
from __future__ import annotations

import math
import os
import random

from . import db

EFFECTS = (
    # key, label, group
    ("shake", "Shake", "Basic"), ("pulse", "Pulse", "Basic"), ("zoomlens", "Zoom lens", "Basic"), ("blur", "Blur", "Basic"),
    ("flash", "Flash", "Basic"), ("strobe", "Strobe", "Basic"), ("cinema", "Cinema bars", "Basic"),
    ("pixelate", "Pixelate", "Basic"), ("vignette", "Dark edges", "Basic"),
    ("vhs", "VHS", "Retro"), ("grain", "Film grain", "Retro"), ("oldfilm", "Old film", "Retro"),
    ("rgbsplit", "RGB split", "Glitch"), ("glitch", "Glitch", "Glitch"), ("negative", "Negative", "Glitch"),
    ("disco", "Color cycle", "Party"), ("mirror", "Mirror", "Party"), ("triple", "Triplet", "Party"),
    ("lightleak", "Light leak", "Light"), ("bokeh", "Bokeh", "Light"), ("sparkle", "Sparkles", "Light"),
    ("snow", "Snow", "Overlays"), ("rain", "Rain", "Overlays"), ("hearts", "Hearts", "Overlays"), ("confetti", "Confetti", "Overlays"),
)
EFFECT = {k: (label, group) for k, label, group in EFFECTS}
GROUPS = tuple(dict.fromkeys(g for _, _, g in EFFECTS))
SCOPES = (("all", "Everything under it"), ("main", "Only the main track"))
MAX_EFFECTS = 40

# transitions that are effects: the clips crossfade while this effect plays over the cut
TRANS_FX = {"glitchcut": "glitch", "shakecut": "shake", "strobecut": "strobe", "negcut": "negative", "flashcut": "flash",
            "punch": "pulse"}

# overlay pictures: name -> how they move (px a second at 1920 tall, "down"/"up"/"twinkle"/"drift")
SPRITES = {"snow_a": ("down", 140), "snow_b": ("down", 300), "rain": ("down", 2200), "confetti": ("down", 420),
           "hearts": ("up", 300), "bokeh": ("up", 70), "sparkle_a": ("twinkle", 0), "sparkle_b": ("twinkle", 0),
           "leak": ("drift", 0)}
SPRITE_FX = {"snow": ("snow_a", "snow_b"), "rain": ("rain",), "confetti": ("confetti",), "hearts": ("hearts",),
             "bokeh": ("bokeh",), "sparkle": ("sparkle_a", "sparkle_b"), "lightleak": ("leak",)}


def clean(effects, end):
    out = []
    for e in (effects or [])[:MAX_EFFECTS]:
        if not isinstance(e, dict) or e.get("type") not in EFFECT:
            continue
        try:
            start = max(0.0, min(float(e.get("start") or 0), 3600.0))
            dur = max(0.2, min(float(e.get("dur") or 2), 600.0))
            k = max(0.0, min(float(e.get("k") if e.get("k") is not None else 0.6), 1.0))
            track = max(1, min(int(e.get("track") or 1), 12))
        except (TypeError, ValueError):
            continue
        eid = "".join(ch for ch in str(e.get("id") or "") if ch.isalnum())[:16] or f"fx{len(out)}"
        out.append({"id": eid, "type": e["type"], "start": round(start, 3), "dur": round(dur, 3), "k": round(k, 3),
                    "scope": "main" if e.get("scope") == "main" else "all", "track": track})
    return [e for e in out if e["start"] < max(end, 0.1)]


def from_transitions(segs):
    """The effects that transitions bring with them, over each cut that has one."""
    out = []
    for s in segs:
        fx = TRANS_FX.get(s.get("trans") or "")
        if fx and s["d"] > 0:
            if fx == "flash":
                out.append({"id": "tr", "type": fx, "start": s["start"] + s["d"] * 0.35, "dur": max(0.3, s["d"]), "k": 0.85, "scope": "main"})
            else:
                out.append({"id": "tr", "type": fx, "start": s["start"], "dur": s["d"], "k": 0.75, "scope": "main"})
    return out


# ------------------------------------------------------------------ the overlay pictures
def _fx_dir():
    d = os.path.join(os.path.dirname(db.DB_PATH) or ".", "studio", "derived", "fx")
    os.makedirs(d, exist_ok=True)
    return d


def sprite_path(name, W, H):
    """The overlay picture for this size (drawn once, then kept)."""
    if name not in SPRITES:
        raise ValueError(name)
    W, H = int(W), int(H)
    path = os.path.join(_fx_dir(), f"{name}_{W}x{H}_v3.png")
    if not os.path.exists(path):
        img = _draw_sprite(name, W, H)
        tmp = path + ".part.png"
        img.save(tmp, "PNG")
        os.replace(tmp, path)
    return path


def _soft(img, radius):
    """Blur a see-through picture without dark fringes (blur the colour weighted by its alpha, then un-weight)."""
    import numpy as np
    from PIL import Image, ImageFilter
    a = np.asarray(img, dtype=np.float32) / 255.0
    alpha = a[..., 3:4]
    pre = np.concatenate([a[..., :3] * alpha, alpha], axis=2)
    chans = [Image.fromarray((pre[..., c] * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(radius)) for c in range(4)]
    b = np.stack([np.asarray(c, dtype=np.float32) / 255.0 for c in chans], axis=2)
    al = b[..., 3:4]
    rgb = np.where(al > 1e-4, b[..., :3] / np.maximum(al, 1e-4), 0)
    out = np.concatenate([np.clip(rgb, 0, 1), al], axis=2)
    return Image.fromarray((out * 255).astype("uint8"), "RGBA")


def _draw_sprite(name, W, H):
    from PIL import Image, ImageDraw, ImageFilter
    rnd = random.Random(sum(map(ord, name)) * 7919)
    m = min(W, H) / 1080
    area = W * H / (1080 * 1920)
    w = W * 2 if name == "leak" else W
    img = Image.new("RGBA", (w, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if name in ("snow_a", "snow_b"):
        n, lo, hi = (int(170 * area), 2, 5) if name == "snow_a" else (int(55 * area), 6, 12)
        for _ in range(n):
            x, y, r = rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(lo, hi) * m
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rnd.randint(150, 240)))
        img = _soft(img, 1.2 * m if name == "snow_a" else 2.2 * m)
    elif name == "rain":
        for _ in range(int(260 * area)):
            x, y, ln = rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(40, 110) * m
            d.line([x, y, x - ln * 0.12, y + ln], fill=(220, 230, 255, rnd.randint(70, 140)), width=max(1, int(2 * m)))
    elif name == "confetti":
        cols = [(255, 92, 92), (255, 200, 60), (80, 200, 255), (130, 230, 120), (220, 120, 255), (255, 255, 255)]
        for _ in range(int(170 * area)):
            cw, ch = rnd.uniform(8, 16) * m, rnd.uniform(14, 28) * m
            piece = Image.new("RGBA", (int(cw) + 2, int(ch) + 2), (0, 0, 0, 0))
            ImageDraw.Draw(piece).rectangle([1, 1, cw, ch], fill=rnd.choice(cols) + (235,))
            piece = piece.rotate(rnd.uniform(0, 180), expand=True, resample=Image.BICUBIC)
            img.alpha_composite(piece, (int(rnd.uniform(0, W - piece.width)), int(rnd.uniform(0, H - piece.height))))
    elif name == "hearts":
        cols = [(255, 77, 109), (255, 128, 160), (230, 40, 80), (255, 180, 200)]
        for _ in range(int(30 * area)):
            s = rnd.uniform(30, 90) * m
            x, y = rnd.uniform(0, W - s), rnd.uniform(0, H - s)
            c = rnd.choice(cols) + (rnd.randint(170, 235),)
            d.ellipse([x, y, x + s * 0.55, y + s * 0.55], fill=c)
            d.ellipse([x + s * 0.45, y, x + s, y + s * 0.55], fill=c)
            d.polygon([(x + s * 0.03, y + s * 0.38), (x + s * 0.97, y + s * 0.38), (x + s * 0.5, y + s * 0.98)], fill=c)
    elif name == "bokeh":
        cols = [(255, 226, 160), (255, 190, 120), (255, 248, 220), (215, 232, 255)]
        for _ in range(int(34 * area)):
            r = rnd.uniform(35, 120) * m
            x, y = rnd.uniform(0, W), rnd.uniform(0, H)
            d.ellipse([x - r, y - r, x + r, y + r], fill=rnd.choice(cols) + (rnd.randint(110, 175),))
        img = _soft(img, 6 * m)
    elif name in ("sparkle_a", "sparkle_b"):
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        g = ImageDraw.Draw(glow)
        for _ in range(int(45 * area)):
            s = rnd.uniform(10, 34) * m
            x, y = rnd.uniform(s, W - s), rnd.uniform(s, H - s)
            col = rnd.choice([(255, 255, 255), (255, 236, 170), (255, 250, 220)])
            d.polygon([(x, y - s), (x + s * 0.18, y - s * 0.18), (x + s, y), (x + s * 0.18, y + s * 0.18), (x, y + s),
                       (x - s * 0.18, y + s * 0.18), (x - s, y), (x - s * 0.18, y - s * 0.18)], fill=col + (245,))
            g.ellipse([x - s * 0.7, y - s * 0.7, x + s * 0.7, y + s * 0.7], fill=col + (90,))
        img = Image.alpha_composite(_soft(glow, 5 * m), img)
    elif name == "leak":
        cols = [(255, 120, 40), (255, 190, 80), (255, 80, 60), (255, 220, 150)]
        for i in range(9):
            r = rnd.uniform(0.25, 0.55) * H
            x, y = rnd.uniform(0, w), rnd.uniform(-0.1 * H, 1.1 * H)
            d.ellipse([x - r, y - r * 0.7, x + r, y + r * 0.7], fill=rnd.choice(cols) + (rnd.randint(150, 215),))
        img = _soft(img, H * 0.08)
    return img


# ------------------------------------------------------------------ the filter chains for the MP4
def _q(expr):
    """An ffmpeg expression inside a filter graph: commas need a backslash."""
    return "'" + str(expr).replace(",", "\\,") + "'"


def _ccm(k):
    sep = [[.393, .769, .189], [.349, .686, .168], [.272, .534, .131]]
    m = [[(1 - k) * (1 if i == j else 0) + k * sep[i][j] for j in range(3)] for i in range(3)]
    names = ("rr", "rg", "rb", "gr", "gg", "gb", "br", "bg", "bb")
    vals = [m[i][j] for i in range(3) for j in range(3)]
    return "colorchannelmixer=" + ":".join(f"{n}={v:.4f}" for n, v in zip(names, vals))


def chain(e, W, H, L, i, inp, out, add_input):
    """The filter graph for one effect over its window: reads [inp], writes [out]; local time t starts at 0.
    add_input(path) adds a looped picture input for the window and returns its label."""
    k, typ = e["k"], e["type"]
    m = min(W, H) / 1080
    p = f"e{i}"
    if typ == "blur":
        r = max(1, round((3 + 22 * k) * m))
        return [f"[{inp}]boxblur=lr={r}:lp=2:cr={r}:cp=2[{out}]"]
    if typ == "shake":
        a = max(2, int(W * (0.008 + 0.03 * k)) // 2 * 2)
        return [f"[{inp}]crop=w=iw-{2 * a}:h=ih-{2 * a}:x={_q(f'{a}+{a}*sin(t*47)*cos(t*13)')}:y={_q(f'{a}+{a}*cos(t*41)*sin(t*19)')},"
                f"scale={W}:{H}[{out}]"]
    if typ in ("pulse", "zoomlens"):
        z = f"(1+{0.12 * k:.4f}*abs(sin(PI*t*2)))" if typ == "pulse" else f"(1+{0.35 * k:.4f}*t/{L:.3f})"
        return [f"[{inp}]scale=w={_q(f'ceil({W}*{z}/2)*2')}:h={_q(f'ceil({H}*{z}/2)*2')}:eval=frame,"
                f"crop=w={W}:h={H}:x='(iw-{W})/2':y='(ih-{H})/2'[{out}]"]
    if typ == "flash":
        return [f"[{inp}]split[{p}a][{p}b];[{p}b]fade=t=in:st=0:d=0.45:color=white[{p}w];"
                f"[{p}a][{p}w]blend=all_mode=normal:all_opacity={min(1, 0.95 * k + 0.05):.3f}[{out}]"]
    if typ == "strobe":
        return [f"[{inp}]eq=brightness={_q(f'{0.5 * k:.3f}*lt(mod(t,0.2),0.07)')}:eval=frame[{out}]"]
    if typ == "cinema":
        b = max(2, int(H * (0.05 + 0.09 * k)) // 2 * 2)
        return [f"[{inp}]drawbox=x=0:y=0:w=iw:h={b}:color=black:t=fill,drawbox=x=0:y=ih-{b}:w=iw:h={b}:color=black:t=fill[{out}]"]
    if typ == "pixelate":
        px = max(2, round((4 + 36 * k) * m))
        return [f"[{inp}]scale=w={max(2, W // px)}:h={max(2, H // px)}:flags=neighbor,scale={W}:{H}:flags=neighbor[{out}]"]
    if typ == "vignette":
        return [f"[{inp}]vignette=angle={0.35 + 0.6 * k:.3f}[{out}]"]
    if typ == "vhs":
        s = max(1, round((3 + 8 * k) * m))
        sl, th = max(3, round(5 * m)), max(1, round(1.5 * m))
        return [f"[{inp}]rgbashift=rh={s}:bh=-{s},eq=saturation={1 - 0.3 * k:.3f}:contrast=1.05,"
                f"noise=alls={round(8 + 18 * k)}:allf=t,drawgrid=w=iw:h={sl}:t={th}:c=black@{0.1 + 0.2 * k:.2f}[{out}]"]
    if typ == "grain":
        return [f"[{inp}]noise=alls={round(6 + 26 * k)}:allf=t+u[{out}]"]
    if typ == "oldfilm":
        return [f"[{inp}]{_ccm(0.4 + 0.6 * k)},noise=alls={round(12 + 16 * k)}:allf=t+u,vignette=angle=0.8,"
                f"eq=brightness={_q('0.04*sin(t*55)')}:eval=frame[{out}]"]
    if typ == "rgbsplit":
        s = max(2, round((4 + 18 * k) * m))
        return [f"[{inp}]rgbashift=rh=-{s}:bh={s}[{out}]"]
    if typ == "glitch":
        s = max(4, round((8 + 30 * k) * m))
        by = int(H * 0.42)
        return [f"[{inp}]split=3[{p}0][{p}1][{p}2];[{p}1]rgbashift=rh={s}:bh=-{s}:gv={s // 2}[{p}s];"
                f"[{p}0][{p}s]overlay=enable={_q(f'lt(mod(t*9.1,1),{0.25 + 0.3 * k:.2f})')}[{p}m];"
                f"[{p}2]crop=iw:{max(2, H // 6 // 2 * 2)}:0:{by},rgbashift=rh=-{s}:bh={s}[{p}b];"
                f"[{p}m][{p}b]overlay=x={3 * s}:y={by}:enable={_q('lt(mod(t*6.7,1),0.3)')}[{out}]"]
    if typ == "negative":
        per = 0.5 - 0.3 * k
        return [f"[{inp}]split[{p}a][{p}b];[{p}b]negate[{p}n];"
                f"[{p}a][{p}n]overlay=enable={_q(f'lt(mod(t,{per:.3f}),{per * 0.35:.3f})')}[{out}]"]
    if typ == "disco":
        return [f"[{inp}]hue=h={_q(f't*{90 + 270 * k:.1f}')}[{out}]"]
    if typ == "mirror":
        return [f"[{inp}]split[{p}a][{p}b];[{p}b]crop={W // 2}:{H}:0:0,hflip[{p}r];[{p}a][{p}r]overlay=x={W - W // 2}:y=0[{out}]"]
    if typ == "triple":
        h3 = max(2, H // 3 // 2 * 2)
        return [f"[{inp}]crop={W}:{h3}:0:{(H - h3) // 2},split=3[{p}0][{p}1][{p}2];[{p}0][{p}1][{p}2]vstack=inputs=3,scale={W}:{H}[{out}]"]
    if typ in SPRITE_FX:
        g, cur = [], inp
        names = SPRITE_FX[typ]
        for j, name in enumerate(names):
            how, v = SPRITES[name]
            lab = add_input(sprite_path(name, W, H))
            v = v * H / 1920
            s1, s2, o1, o2 = f"{p}s{j}a", f"{p}s{j}b", f"{p}o{j}a", f"{p}o{j}b"
            last = j == len(names) - 1
            dst = out if last else f"{p}x{j}"
            alpha = f"format=rgba,colorchannelmixer=aa={0.35 + 0.65 * k:.3f}"
            if how in ("down", "up"):
                yy = f"mod(t*{v:.1f},{H})" if how == "down" else f"-mod(t*{v:.1f},{H})"
                xx = f"20*sin(t*2.2)*{m:.3f}" if name == "confetti" else (f"30*sin(t*0.8)*{m:.3f}" if name == "bokeh" else "0")
                g.append(f"[{lab}]{alpha},split[{s1}][{s2}]")
                g.append(f"[{cur}][{s1}]overlay=x={_q(xx)}:y={_q(yy)}:shortest=1[{o1}]")
                yy2 = yy + (f"-{H}" if how == "down" else f"+{H}")
                g.append(f"[{o1}][{s2}]overlay=x={_q(xx)}:y={_q(yy2)}:shortest=1[{dst}]")
            elif how == "twinkle":
                on = "lt(mod(t,0.8),0.4)" if name.endswith("_a") else "gte(mod(t,0.8),0.4)"
                g.append(f"[{lab}]{alpha}[{s1}]")
                g.append(f"[{cur}][{s1}]overlay=enable={_q(on)}:shortest=1[{dst}]")
            else:                                           # the light leak drifts across
                g.append(f"[{lab}]{alpha}[{s1}]")
                g.append(f"[{cur}][{s1}]overlay=x={_q(f'-{W}*t/{L:.3f}')}:y=0:shortest=1[{dst}]")
            cur = dst
        return g
    return [f"[{inp}]null[{out}]"]


def apply(in_mp4, effects, out_mp4, W, H, total_len, ass=None, fontsdir=None, run=None, fps=30):
    """Every effect over its own stretch of the video, in track order; then the captions (if given) on top."""
    args = ["ffmpeg", "-y", "-v", "error", "-i", in_mp4]
    n = [1]
    f = []
    effects = sorted([e for e in effects if e["start"] < total_len], key=lambda e: (e.get("track", 1), e["start"]))
    base = "0:v"
    for i, e in enumerate(effects):
        s = e["start"]
        L = max(0.05, min(e["dur"], total_len - s))

        def add_input(path, L=L):
            args.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{L:.3f}", "-i", path])
            n[0] += 1
            return f"{n[0] - 1}:v"
        f.append(f"[{base}]split[ba{i}][bb{i}]")
        f.append(f"[bb{i}]trim=start={s:.3f}:end={s + L:.3f},setpts=PTS-STARTPTS,format=rgb24[w{i}]")
        f += chain(e, W, H, L, i, f"w{i}", f"wo{i}", add_input)
        f.append(f"[wo{i}]format=yuv420p,setpts=PTS+{s:.3f}/TB[fx{i}]")
        f.append(f"[ba{i}][fx{i}]overlay=enable={_q(f'between(t,{s:.3f},{s + L:.3f})')}:eof_action=pass[v{i}]")
        base = f"v{i}"
    if ass:
        f.append(f"[{base}]ass={ass}:fontsdir={fontsdir}[vs]")
        base = "vs"
    if base == "0:v":
        f.append("[0:v]null[vfinal]")
    else:
        f.append(f"[{base}]format=yuv420p[vfinal]")
    args += ["-filter_complex", ";".join(f), "-map", "[vfinal]", "-map", "0:a?", "-t", f"{total_len:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy",
             "-movflags", "+faststart", out_mp4]
    run(args)
