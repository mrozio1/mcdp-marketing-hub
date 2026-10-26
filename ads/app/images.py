"""
Ad images per practice: a logo, a library of the practice's own photos, and AI-generated scenes.

Decided with the team (2026-10-26):
- Sources: AI images from the free Cloudflare Workers AI route (the same cf-image adapter Social
  uses) plus each practice's real photo library.
- Placements: Google Search image assets on new campaigns (square 1200x1200 + wide 1200x628),
  and Meta image ads (square 1080x1080) instead of copying an existing ad's picture.
- Meta images get the practice logo, small, in a corner. Google images stay clean (Google
  rejects logos and text laid over images).

Healthcare care in prompts: no before/after, no close-ups of mouths or teeth, no medical
procedures, no text, logos or watermarks; people only as natural, non-clinical scenes.
Files live in /data/brand/<practice id>/ (the Ads data volume).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import urllib.error
import urllib.request

from . import brand, db

CF_IMAGE_URL = os.environ.get("CF_IMAGE_URL", "http://cf-image:8080/v1").rstrip("/")
MAX_UPLOAD = 15 * 1024 * 1024
LIBRARY_MAX_SIDE = 2400
KINDS = ("logo", "photo", "generated")
VARIANTS = {
    # name: (width, height, logo on it?)
    "google_square": (1200, 1200, False),
    "google_wide": (1200, 628, False),
    "meta_square": (1080, 1080, True),
}
NAME_RE = re.compile(r"^[0-9a-f]{20}\.(jpg|png)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS brand_assets (
  id INTEGER PRIMARY KEY,
  practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  filename TEXT NOT NULL,
  width INTEGER, height INTEGER,
  label TEXT DEFAULT '',
  prompt TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  created_by TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS brand_assets_practice ON brand_assets(practice_id, kind);
"""


class ImageError(RuntimeError):
    pass


def init():
    with db.conn() as c:
        c.executescript(SCHEMA)


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageOps
        return Image, ImageDraw, ImageOps
    except ImportError:
        raise ImageError("Image tools aren't installed on the server yet; run the hub update again.")


def asset_dir(pid):
    d = os.path.join(os.path.dirname(db.DB_PATH) or ".", "brand", str(int(pid)))
    os.makedirs(d, exist_ok=True)
    return d


# ------------------------------------------------------------------ the asset records
def assets(pid, kind=None):
    with db.conn() as c:
        if kind:
            rows = c.execute("SELECT * FROM brand_assets WHERE practice_id=? AND kind=? ORDER BY id DESC", (pid, kind))
        else:
            rows = c.execute("SELECT * FROM brand_assets WHERE practice_id=? ORDER BY id DESC", (pid,))
        return [dict(r) for r in rows]


def asset(aid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM brand_assets WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None


def logo(pid):
    got = assets(pid, "logo")
    return got[0] if got else None


def path(a):
    if not a or not NAME_RE.match(a["filename"]):
        return None
    p = os.path.join(asset_dir(a["practice_id"]), a["filename"])
    return p if os.path.exists(p) else None


def _save(pid, kind, img, label="", prompt="", by=""):
    """Store a PIL image; logos keep transparency as PNG, everything else is JPEG."""
    fmt = "png" if kind == "logo" else "jpg"
    name = secrets.token_hex(10) + "." + fmt
    full = os.path.join(asset_dir(pid), name)
    if fmt == "jpg":
        img.convert("RGB").save(full, "JPEG", quality=90, optimize=True)
    else:
        img.save(full, "PNG", optimize=True)
    with db.conn() as c:
        if kind == "logo":           # one logo per practice: replace the old one
            for r in c.execute("SELECT filename FROM brand_assets WHERE practice_id=? AND kind='logo'", (pid,)):
                try:
                    os.remove(os.path.join(asset_dir(pid), r["filename"]))
                except OSError:
                    pass
            c.execute("DELETE FROM brand_assets WHERE practice_id=? AND kind='logo'", (pid,))
        cur = c.execute("INSERT INTO brand_assets(practice_id, kind, filename, width, height, label, prompt, "
                        "created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                        (pid, kind, name, img.width, img.height, label[:120], prompt[:2000], db.now(), by))
        return cur.lastrowid


def delete(pid, aid):
    a = asset(aid)
    if not a or a["practice_id"] != pid:
        return
    p = path(a)
    if p:
        try:
            os.remove(p)
        except OSError:
            pass
    with db.conn() as c:
        c.execute("DELETE FROM brand_assets WHERE id=?", (aid,))


def _open(data):
    Image, _, ImageOps = _pil()
    if len(data) > MAX_UPLOAD:
        raise ImageError("That file is over 15 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ImageError("That isn't a picture the hub can read. Use JPEG, PNG or WebP (iPhone HEIC photos: "
                         "share or export them as JPEG first).")
    return ImageOps.exif_transpose(img)


def add_upload(pid, data, filename, kind, by=""):
    if kind not in ("logo", "photo"):
        raise ImageError("Unknown kind.")
    img = _open(data)
    if kind == "logo":
        if img.mode not in ("RGBA", "LA"):
            img = img.convert("RGBA")
        img.thumbnail((1200, 1200))
    else:
        if min(img.size) < 600:
            raise ImageError(f"That photo is small ({img.width}×{img.height}); ads need at least 600 pixels on the "
                             "short side. Use the original photo.")
        img = img.convert("RGB")
        img.thumbnail((LIBRARY_MAX_SIDE, LIBRARY_MAX_SIDE))
    label = os.path.splitext(os.path.basename(filename or ""))[0][:80]
    return _save(pid, kind, img, label=label, by=by)


# ------------------------------------------------------------------ AI images (Cloudflare Workers AI)
SAFE = ("Photorealistic photograph, natural light, warm and welcoming, high quality, uncluttered. "
        "No text, no letters, no words, no logos, no signs, no watermarks. No close-ups of mouths or teeth, "
        "no dental instruments in use, no medical procedures, no before-and-after, no x-rays. "
        "If people appear, they are relaxed in an everyday moment, shown naturally, not posing as patients.")


def prompt_for(practice, kit, focus, idea=""):
    k = kit or {}
    style = " ".join(brand.lines(k, "image_style")) if k.get("image_style") else ""
    place = brand.lines(k, "locations")[:1]
    kind = "orthodontic" if "ortho" in practice["name"].lower() else "dental"
    scene = idea.strip() or (f"a scene that suggests {focus.strip()}" if (focus or "").strip()
                             else f"a bright, modern, friendly {kind} practice")
    return (f"{scene}, for a family {kind} practice" + (f" in {place[0]}" if place else "") + ". "
            + (f"Style: {style}. " if style else "") + SAFE)[:1900]


IDEAS = ("a bright, modern {kind} office reception with plants and natural light, nobody in frame",
         "a smiling parent and teenager walking outside on a sunny day, seen from a little distance",
         "a cheerful adult laughing with friends at a cafe, candid, soft focus background",
         "a welcoming treatment room with large windows and calm colors, empty, tidy")


def generate(practice, focus="", n=3, kit=None, by="", idea=""):
    """Make n images and store them in the practice's library as 'generated'. Returns asset ids."""
    Image, _, _ = _pil()
    kit = kit or brand.kit(practice["id"])
    kind = "orthodontic" if "ortho" in practice["name"].lower() else "dental"
    ids = []
    for i in range(max(1, min(4, n))):
        scene = idea or ("" if (focus or "").strip() and i == 0 else IDEAS[i % len(IDEAS)].format(kind=kind))
        prompt = prompt_for(practice, kit, focus, scene)
        b64 = _cf_generate(prompt)
        try:
            img = Image.open(io.BytesIO(base64.b64decode(b64)))
            img.load()
        except Exception:
            raise ImageError("The image service returned something that isn't a picture. Try again.")
        ids.append(_save(practice["id"], "generated", img.convert("RGB"), label=(focus or "AI image")[:80],
                         prompt=prompt, by=by))
    return ids


def _cf_generate(prompt):
    req = urllib.request.Request(f"{CF_IMAGE_URL}/images/generations",
                                 data=json.dumps({"prompt": prompt, "n": 1}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise ImageError(f"The image service said: {detail}")
    except Exception:
        raise ImageError("Couldn't reach the free image service. It's switched on with "
                         "sh enable-cf-images.sh ACCOUNT_ID TOKEN on the server.")
    try:
        return data["data"][0]["b64_json"]
    except (KeyError, IndexError, TypeError):
        raise ImageError("The image service returned no picture. Try again.")


# ------------------------------------------------------------------ sizes and the logo corner
def _cover(img, w, h):
    """Scale and centre-crop to exactly w x h."""
    Image, _, ImageOps = _pil()
    return ImageOps.fit(img.convert("RGB"), (w, h), method=Image.LANCZOS, centering=(0.5, 0.45))


def _with_logo(img, logo_img):
    Image, ImageDraw, _ = _pil()
    lg = logo_img.convert("RGBA")
    target_w = int(img.width * 0.18)
    scale = min(target_w / lg.width, (img.height * 0.12) / lg.height)
    lg = lg.resize((max(1, int(lg.width * scale)), max(1, int(lg.height * scale))), Image.LANCZOS)
    margin = int(img.width * 0.035)
    pad = int(img.width * 0.014)
    x = img.width - lg.width - margin
    y = img.height - lg.height - margin
    base = img.convert("RGBA")
    # A soft white pill behind the logo so it reads on any photo.
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((x - pad, y - pad, x + lg.width + pad, y + lg.height + pad),
                                            radius=pad * 2, fill=(255, 255, 255, 225))
    base = Image.alpha_composite(base, layer)
    base.alpha_composite(lg, (x, y))
    return base.convert("RGB")


def render(a, variant, logo_asset=None):
    """JPEG bytes of an asset in one of VARIANTS."""
    Image, _, _ = _pil()
    if variant not in VARIANTS:
        raise ImageError("Unknown image size.")
    p = path(a)
    if not p:
        raise ImageError("That image is missing; pick another.")
    w, h, with_logo = VARIANTS[variant]
    img = _cover(Image.open(p), w, h)
    if with_logo and logo_asset and path(logo_asset):
        img = _with_logo(img, Image.open(path(logo_asset)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True)
    data = buf.getvalue()
    if len(data) > 5 * 1024 * 1024:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=75, optimize=True)
        data = buf.getvalue()
    return data


def pickable(pid):
    """Photos and AI images that can go on an ad (not the logo)."""
    return [a for a in assets(pid) if a["kind"] in ("photo", "generated") and path(a)]


def clean_selection(pid, ids, limit):
    ok = {a["id"] for a in pickable(pid)}
    out = []
    for i in ids:
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if i in ok and i not in out:
            out.append(i)
    return out[:limit]


# ------------------------------------------------------------------ sending to Google and Meta
GOOGLE_MAX_IMAGES = 4


def google_operations(pid, account, campaign_rn, image_ids, name_prefix):
    """Mutate operations adding each picked image (square + wide) to the campaign as image assets."""
    from . import connectors as cx
    cid = cx._clean_id(account)
    ops, temp = [], -100
    for n, aid in enumerate(image_ids[:GOOGLE_MAX_IMAGES], 1):
        a = asset(aid)
        if not a or a["practice_id"] != pid:
            continue
        for variant in ("google_square", "google_wide"):
            temp -= 1
            rn = f"customers/{cid}/assets/{temp}"
            ops.append({"assetOperation": {"create": {
                "resourceName": rn, "name": f"{name_prefix} image {n} {variant.split('_')[1]} {db.now()}"[:120],
                "type": "IMAGE", "imageAsset": {"data": base64.b64encode(render(a, variant)).decode()}}}})
            ops.append({"campaignAssetOperation": {"create": {"campaign": campaign_rn, "asset": rn,
                                                              "fieldType": "AD_IMAGE"}}})
    return ops


def meta_upload(pid, account, aid):
    """Upload the Meta version (logo in the corner) to the ad account. Returns the image hash."""
    from . import connectors as cx
    a = asset(aid)
    if not a or a["practice_id"] != pid:
        raise ImageError("That image is missing; pick another.")
    data = render(a, "meta_square", logo(pid))
    res = cx._meta_write(f"act_{str(account).removeprefix('act_')}/adimages",
                         {"bytes": base64.b64encode(data).decode(), "name": f"mcdp-{aid}.jpg"})
    imgs = (res or {}).get("images") or {}
    for v in imgs.values():
        if v.get("hash"):
            return v["hash"]
    raise ImageError("Meta didn't return the uploaded image.")
