"""Free background removal for Studio videos, on the NAS.

Uses Robust Video Matting (MobileNetV3, ONNX) on the CPU. For a library video it writes a grey "matte" video
the same length (white = the person, black = background), kept next to the Studio files. The editor and the
renderer use the matte to cut the person out: on a blurred copy, a brand colour, a photo, or over other clips.

The model (about 15 MB) is downloaded from the project's GitHub release the first time it's needed.
Runs in the background; a 30-second clip takes a few minutes on a NAS processor.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request

from . import db, studio

MODEL_URL = "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx"
MODEL_SIZE_MIN = 10_000_000
MAX_SECONDS = 180
WORK_SIDE = 768                 # the matte is worked out at this size, then scaled back up
FPS = 30
_lock = threading.Lock()
_running = set()


class BGError(RuntimeError):
    pass


def model_path():
    return os.path.join(os.path.dirname(db.DB_PATH) or ".", "models", "rvm_mobilenetv3_fp32.onnx")


def available():
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return bool(shutil.which("ffmpeg"))


def _derived():
    d = os.path.join(os.path.dirname(db.DB_PATH) or ".", "studio", "derived")
    os.makedirs(d, exist_ok=True)
    return d


def mask_path(item_id):
    p = os.path.join(_derived(), f"mask_{int(item_id)}.mp4")
    return p if os.path.exists(p) else None


def status(item_id):
    try:
        s = json.loads(db.get_setting(f"studio_mask_{int(item_id)}") or "{}")
    except ValueError:
        s = {}
    if mask_path(item_id) and s.get("state") != "running":
        s["state"] = "ready"
    return s


def _note(item_id, **kw):
    s = status(item_id)
    s.update(kw, at=time.time())
    db.set_setting(f"studio_mask_{int(item_id)}", json.dumps(s))


def _model():
    p = model_path()
    if os.path.exists(p) and os.path.getsize(p) > MODEL_SIZE_MIN:
        return p
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".part"
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=300) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1024 * 1024)
    except Exception as e:
        raise BGError(f"Couldn't download the background-removal model: {e}")
    if os.path.getsize(tmp) < MODEL_SIZE_MIN:
        os.remove(tmp)
        raise BGError("The background-removal model didn't download whole. Try again.")
    os.replace(tmp, p)
    return p


def start(item_id):
    it = studio.item(item_id)
    if not it or it["kind"] != "video":
        raise BGError("Background removal works on videos from the library.")
    if not available():
        raise BGError("Background removal needs the hub update on the NAS (it adds the tools it uses).")
    if mask_path(item_id):
        return False
    with _lock:
        if item_id in _running:
            return False
        _running.add(item_id)
    _note(item_id, state="running", pct=0, error="")
    threading.Thread(target=_run, args=(item_id,), daemon=True).start()
    return True


def _run(item_id):
    from . import studio_video
    tmp_src = None
    try:
        import numpy as np
        import onnxruntime as ort
        it = studio.item(item_id)
        src, temp = studio_video.source_path(it)
        if temp:
            tmp_src = src
        pr = studio_video.probe(src)
        dur = pr["duration"] or 0
        if dur > MAX_SECONDS:
            raise BGError(f"Background removal works on clips up to {MAX_SECONDS // 60} minutes; trim this one into a shorter copy first.")
        sw, sh = pr["w"] or 0, pr["h"] or 0
        if not sw or not sh:
            raise BGError("Couldn't read the video's size.")
        k = min(1.0, WORK_SIDE / max(sw, sh))
        w, h = max(2, int(sw * k) // 2 * 2), max(2, int(sh * k) // 2 * 2)
        sess = ort.InferenceSession(_model(), providers=["CPUExecutionProvider"])
        ratio = np.array([min(1.0, 384 / max(w, h))], dtype=np.float32)
        rec = [np.zeros((1, 1, 1, 1), dtype=np.float32)] * 4
        frames = max(1, int(dur * FPS))
        dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-vf", f"fps={FPS},scale={w}:{h}", "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out = os.path.join(_derived(), f"mask_{int(item_id)}.part.mp4")
        enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
                                "-r", str(FPS), "-i", "pipe:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                                "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        size = w * h * 3
        n = 0
        last = time.time()
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).astype(np.float32) / 255.0
            src_t = img.transpose(2, 0, 1)[None]
            fgr, pha, *rec = sess.run(None, {"src": src_t, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2], "r4i": rec[3],
                                             "downsample_ratio": ratio})
            enc.stdin.write((np.clip(pha[0, 0], 0, 1) * 255).astype(np.uint8).tobytes())
            n += 1
            if time.time() - last > 2:
                _note(item_id, pct=min(99, int(n * 100 / frames)))
                last = time.time()
        dec.wait()
        enc.stdin.close()
        enc.wait()
        if enc.returncode != 0 or n == 0:
            raise BGError("The background couldn't be worked out for this clip.")
        os.replace(out, os.path.join(_derived(), f"mask_{int(item_id)}.mp4"))
        _note(item_id, state="ready", pct=100, error="")
    except BGError as e:
        _note(item_id, state="error", error=str(e))
    except Exception as e:
        _note(item_id, state="error", error=f"Background removal didn't work: {e}"[:300])
    finally:
        if tmp_src:
            try:
                os.remove(tmp_src)
            except OSError:
                pass
        with _lock:
            _running.discard(item_id)


def forget(item_id):
    p = mask_path(item_id)
    if p:
        os.remove(p)
    db.set_setting(f"studio_mask_{int(item_id)}", None)
