"""Per-practice brand kits: reading materials, AI suggestions, rules applied to new ads, editing drafts."""
import io
import zipfile

import pytest

from tests import mock_platforms as mp
from tests.test_actions import hub  # noqa: F401  (shared fixture: hub with a linked practice)


def _docx(paragraphs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


KIT = {"voice": "Warm, family-friendly, confident", "services": "Invisalign\nBraces for kids",
       "offers": "Free consultation\n$0 down payment plans", "must_say": "Heights Orthodontics",
       "never_say": "cheap\nsmile makeover", "locations": "Sterling Heights"}


def test_line_checks():
    from app import brand
    k = {**{x: "" for x in brand.KEYS}, **KIT}
    assert brand.check_line("Free Consult Today", "headline", k, 30) == []
    assert "never say" in brand.check_line("Cheap Braces Near You", "headline", k, 30)[0]
    assert "smile makeover" in brand.check_line("Get A Smile Makeover", "headline", k, 30)[0]
    assert "exclamation" in brand.check_line("Book Today!", "headline", k, 30)[0]
    assert brand.check_line("Book today!", "description", k, 90) == []        # allowed in descriptions
    assert "limit 30" in brand.check_line("A" * 10 + " way too long for a headline", "headline", k, 30)[0]
    assert "guarantee" in brand.check_line("Guaranteed Straight Teeth", "headline", k, 30)[0]
    assert "best" in brand.check_line("Best Orthodontist In Town", "headline", k, 30)[0].lower()
    assert "price $99" in brand.check_line("Consults From $99", "headline", k, 30)[0]
    assert brand.check_line("$0 Down Payment Plans", "headline", k, 30) == []  # price is an approved offer
    assert "phone" in brand.check_line("Call 586-555-1234", "headline", k, 30)[0]
    assert "capitals" in brand.check_line("BRACES For Kids", "headline", k, 30)[0]
    assert brand.check_line("FREE Consult", "headline", k, 30) == []
    assert "health" in brand.check_line("Are your teeth crooked? We help", "description", k, 90)[0]
    assert brand.missing_must_say(["Free consult", "Braces"], k) == ["Heights Orthodontics"]


def test_extracts_text_from_documents():
    from app import brand
    t = brand.extract_text(_docx(["Our voice is warm and friendly.", "Never say cheap."]), "Brand guide.docx")
    assert "warm and friendly" in t and "Never say cheap." in t
    assert "Hello" in brand.extract_text(b"Hello there, this is our brand guide text. " * 2, "g.txt")
    with pytest.raises(brand.BrandError):
        brand.extract_text(b"\x89PNG....", "logo.png")
    with pytest.raises(brand.BrandError):
        brand.extract_text(b"short", "g.txt")


def test_website_reader_refuses_local_addresses():
    from app import brand
    for url in ("http://127.0.0.1:9977/", "http://localhost/", "http://192.168.0.137/", "ftp://example.com"):
        with pytest.raises(brand.BrandError):
            brand.read_website(url)


def test_upload_fill_review_save(hub, monkeypatch):
    c, db, pid, ai = hub
    from app import brand
    r = c.post(f"/p/{pid}/brand/upload", files={"file": ("Heights brand guide.docx", _docx(
        ["Heights Orthodontics sounds warm and family-friendly.", "Offer: Free consultation.",
         "Never call us cheap."]))})
    assert "Read" in r.text and "Heights brand guide.docx" in r.text

    seen = {}

    def fake(kind, ctx):
        assert kind == "brand_fill"
        seen["ctx"] = ctx
        return {"voice": "Warm and family-friendly", "offers": ["Free consultation"], "never_say": "cheap"}
    monkeypatch.setattr(ai, "generate_json", fake)
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    r = c.post(f"/p/{pid}/brand/fill")
    assert "Never call us cheap" in seen["ctx"]
    assert "Suggested from your materials" in r.text
    assert not brand.has_kit(brand.kit(pid))                 # nothing is used until a person saves
    assert brand.kit(pid)["suggested"]["offers"] == "Free consultation"

    form = {k: "" for k in brand.KEYS}
    form.update(KIT)
    r = c.post(f"/p/{pid}/brand", data=form)
    assert "Brand kit saved" in r.text
    k = brand.kit(pid)
    assert k["never_say"] == "cheap\nsmile makeover" and k["suggested"] is None and k["updated_by"]


def test_new_ads_follow_and_are_checked_against_the_kit(hub, monkeypatch):
    c, db, pid, ai = hub
    from app import brand
    brand.save(pid, KIT, "Test")

    def fake(kind, ctx):
        assert "Brand kit for this practice" in ctx and "Never say: cheap; smile makeover" in ctx
        if kind == "google_ad":
            assert "Focus for this ad (from the team): Invisalign for adults" in ctx
            return {"headlines": ["Invisalign In Sterling Heights", "Free Invisalign Consult", "Cheap Clear Aligners",
                                  "Adult Invisalign Experts", "Best Aligners In Town", "Book Your Visit Today!"],
                    "descriptions": ["Clear aligners for busy adults. Book a free consultation today.",
                                     "$0 down payment plans. Evening and Saturday appointments.",
                                     "Get a smile makeover with clear aligners."]}
        return {"primary_text": "Clear aligners for adults at Heights Orthodontics. Book a free consultation.",
                "headline": "Free Invisalign Consult", "description": "Book online"}
    monkeypatch.setattr(ai, "generate_json", fake)
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    r = c.post(f"/p/{pid}/fixes/draft", data={"target": "google|Invisalign - Search", "focus": "Invisalign for adults",
                                              "count": "2"})
    assert "2 new ads written" in r.text and "with the brand kit" in r.text
    drafts = [ch for ch in db.changes(pid, ["proposed"]) if ch["kind"] == "draft_ad"]
    assert len(drafts) == 2
    g = drafts[0]
    assert g["payload"]["headlines"] == ["Invisalign In Sterling Heights", "Free Invisalign Consult",
                                         "Adult Invisalign Experts"]
    assert len(g["payload"]["descriptions"]) == 2
    whys = {d["text"]: d["why"] for d in g["payload"]["dropped"]}
    assert "never say" in whys["Cheap Clear Aligners"] and "exclamation" in whys["Book Your Visit Today!"]
    assert "best" in whys["Best Aligners In Town"].lower()
    assert "No line uses: “Heights Orthodontics”" in g["detail"]          # must-say flagged for the reviewer
    html = c.get(f"/p/{pid}").text
    assert "Lines left out (4)" in html and "Edit the text" in html and f'id="edit-{g["id"]}"' in html

    # A person edits it: bad lines are refused, good ones saved, then it's created as edited.
    bad = c.post(f"/p/{pid}/fixes/{g['id']}/edit", data={"headlines": "Cheap Braces\nA\nB", "descriptions": "x\ny"})
    assert "Not saved" in bad.text and "never say" in bad.text
    heads = "Heights Orthodontics\nFree Invisalign Consult\nAdult Invisalign Experts\nInvisalign In Sterling Heights"
    ok = c.post(f"/p/{pid}/fixes/{g['id']}/edit", data={
        "headlines": heads, "descriptions": "Clear aligners for busy adults.\r\nBook a free consultation today."})
    assert "Ad text saved" in ok.text
    ch = db.change(g["id"])
    assert ch["payload"]["headlines"][0] == "Heights Orthodontics" and ch["payload"]["edited"]
    c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(g["id"])]})
    (gad,) = mp.STATE["g_ads"].values()
    assert gad["status"] == "PAUSED"
    assert [h["text"] for h in gad["ad"]["responsiveSearchAd"]["headlines"]][0] == "Heights Orthodontics"

    # Meta: checked too, and editable
    r = c.post(f"/p/{pid}/fixes/draft", data={"target": "meta|Heights - Free Consult Leads"})
    m = next(ch for ch in db.changes(pid, ["proposed"]) if ch["kind"] == "draft_ad" and ch["platform"] == "meta")
    r = c.post(f"/p/{pid}/fixes/{m['id']}/edit", data={"primary_text": "Cheap aligners for adults.", "headline": "Hi"})
    assert "Not saved" in r.text
    r = c.post(f"/p/{pid}/fixes/{m['id']}/edit", data={"primary_text": "Aligners for adults at Heights Orthodontics.",
                                                       "headline": "Free Invisalign Consult", "description": ""})
    m = db.change(m["id"])
    assert m["payload"]["object_story_spec"]["link_data"]["message"].startswith("Aligners for adults")
    assert "description" not in m["payload"]["object_story_spec"]["link_data"]


def test_copy_kit_from_another_practice(hub):
    c, db, pid, ai = hub
    from app import brand, users
    other = db.add_practice("Kalamazoo Orthodontics", 0, 0)
    brand.save(pid, KIT, "Test")
    r = c.post(f"/p/{other}/brand/copy", data={"source": str(pid)})
    assert "Copied Heights Orthodontics" in r.text
    assert brand.kit(other)["offers"] == KIT["offers"]
    assert "Brand kit" in c.get(f"/p/{other}").text
