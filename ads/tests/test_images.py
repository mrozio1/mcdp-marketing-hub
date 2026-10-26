"""Ad images: logo, photo library, AI images (Cloudflare adapter), picked for Google campaigns and Meta ads."""
import io

from PIL import Image

from tests import mock_platforms as mp
from tests.test_actions import hub  # noqa: F401
from tests.test_campaigns import ADDR, _fake


def _png(w, h, color=(10, 120, 130), alpha=False):
    buf = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", (w, h), color + ((0,) if alpha else ())).save(buf, "PNG")
    return buf.getvalue()


def _setup(monkeypatch, hub):
    c, db, pid, ai = hub
    from app import images
    monkeypatch.setattr(images, "CF_IMAGE_URL", "http://127.0.0.1:9977/v1")
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    monkeypatch.setenv("ADS_SYNC_JOBS", "1")
    return c, db, pid, ai, images


def test_library_logo_and_ai_images(hub, monkeypatch):
    c, db, pid, ai, images = _setup(monkeypatch, hub)
    r = c.post(f"/p/{pid}/brand/images/upload", data={"kind": "logo"},
               files=[("files", ("logo.png", _png(400, 160, alpha=True), "image/png"))])
    assert "Logo saved" in r.text and images.logo(pid)
    r = c.post(f"/p/{pid}/brand/images/upload", data={"kind": "photo"},
               files=[("files", ("office.jpg", _png(1600, 1200), "image/png")),
                      ("files", ("tiny.png", _png(300, 200), "image/png")),
                      ("files", ("notes.txt", b"hello", "text/plain"))])
    assert "Added 1 photo" in r.text and "tiny.png" in r.text
    r = c.post(f"/p/{pid}/brand/images/generate", data={"idea": "a bright modern office"})
    assert "Made 3 AI images" in r.text
    p = mp.STATE["cf_prompts"][0]
    assert "a bright modern office" in p and "No text" in p and "no before-and-after" in p.lower()
    assert len(images.pickable(pid)) == 4
    page = c.get(f"/p/{pid}").text
    assert "Ad images" in page and "AI images</strong> (3)" in page
    a = images.pickable(pid)[0]
    for v, size in (("google_square", (1200, 1200)), ("google_wide", (1200, 628)), ("meta_square", (1080, 1080))):
        img = Image.open(io.BytesIO(c.get(f"/brand-images/{a['id']}?v={v}").content))
        assert img.size == size
    meta = Image.open(io.BytesIO(c.get(f"/brand-images/{a['id']}?v=meta_square").content)).convert("RGB")
    plain = Image.open(io.BytesIO(c.get(f"/brand-images/{a['id']}?v=google_square").content)).convert("RGB")
    assert meta.getpixel((1000, 1010)) != plain.resize((1080, 1080)).getpixel((1000, 1010))   # logo corner
    c.post(f"/p/{pid}/brand/images/{a['id']}/delete")
    assert len(images.pickable(pid)) == 3


def test_google_campaign_gets_picked_images(hub, monkeypatch):
    c, db, pid, ai, images = _setup(monkeypatch, hub)
    monkeypatch.setattr(ai, "generate_json", _fake({}))
    c.post(f"/p/{pid}/brand/images/generate", data={"idea": "family outdoors"})
    c.post(f"/p/{pid}/campaign/new", data={"focus": "Invisalign", "daily_budget": "10", "radius": "10",
                                            "addresses": ADDR, "final_url": "https://heights.example/"})
    (ch,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "new_campaign"]
    ids = [str(a["id"]) for a in images.pickable(pid)][:2]
    r = c.post(f"/p/{pid}/drafts/{ch['id']}/images", data={"img": ids + ["99999"]})
    assert "2 images will be added" in r.text
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(ch["id"])], "back": "campaign"})
    assert "Applied 1 change" in r.text
    adds = [o for o in mp.STATE["g_assets"] if "assetOperation" in o]
    links = [o["campaignAssetOperation"]["create"] for o in mp.STATE["g_assets"] if "campaignAssetOperation" in o]
    assert len(adds) == 4 and len(links) == 4 and all(lk["fieldType"] == "AD_IMAGE" for lk in links)
    import base64
    sizes = sorted(Image.open(io.BytesIO(base64.b64decode(o["assetOperation"]["create"]["imageAsset"]["data"]))).size
                   for o in adds)
    assert sizes == [(1200, 628), (1200, 628), (1200, 1200), (1200, 1200)]
    (camp_rn,) = mp.STATE["g_new"]
    assert all(lk["campaign"] == camp_rn for lk in links)


def test_meta_ad_uses_picked_image_with_logo(hub, monkeypatch):
    c, db, pid, ai, images = _setup(monkeypatch, hub)
    monkeypatch.setattr(ai, "generate_json", lambda k, ctx: {
        "primary_text": "Meet our team for a free consult.", "headline": "Free Orthodontic Consult", "description": ""})
    c.post(f"/p/{pid}/brand/images/upload", data={"kind": "logo"},
           files=[("files", ("logo.png", _png(400, 160, (200, 30, 30), alpha=False), "image/png"))])
    c.post(f"/p/{pid}/brand/images/upload", data={"kind": "photo"},
           files=[("files", ("team.png", _png(1400, 1400, (20, 200, 90)), "image/png"))])
    c.post(f"/p/{pid}/fixes/draft", data={"target": "meta|Heights - Free Consult Leads"})
    (d,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "draft_ad"]
    assert "Keep the existing ad" in c.get(f"/p/{pid}").text
    photo = images.assets(pid, "photo")[0]
    r = c.post(f"/p/{pid}/drafts/{d['id']}/images", data={"img": str(photo["id"])})
    assert "logo in the corner" in r.text
    c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(d["id"])]})
    cr = [o for o in mp.STATE["m_created"].values() if o["type"] == "creative"][0]["spec"]
    h = cr["link_data"]["image_hash"]
    assert h != "abc123" and "picture" not in cr["link_data"]
    up = Image.open(io.BytesIO(mp.STATE["m_images"][h])).convert("RGB")
    assert up.size == (1080, 1080)
    assert up.getpixel((540, 540))[1] > 150                       # the photo
    assert up.getpixel((1080 - 60, 1080 - 60))[0] > 150           # red logo in the bottom-right corner
    # Picking "keep the existing image" goes back to the copied picture
    c.post(f"/p/{pid}/fixes/{d['id']}/undo")


def test_image_service_off_is_explained(hub, monkeypatch):
    c, db, pid, ai, images = _setup(monkeypatch, hub)
    monkeypatch.setattr(images, "CF_IMAGE_URL", "http://127.0.0.1:1/v1")
    r = c.post(f"/p/{pid}/brand/images/generate", data={"idea": "x"})
    assert "enable-cf-images.sh" in r.text
