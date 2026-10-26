"""OpenAdKit-based tools: Score this ad, optimizers on live data, keyword ideas via Apply/Undo, research, social."""
import pytest

from tests import mock_platforms as mp
from tests.test_actions import hub  # noqa: F401
from tests.test_brand import KIT


@pytest.fixture
def ready(hub, monkeypatch):
    c, db, pid, ai = hub
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    monkeypatch.setenv("ADS_SYNC_JOBS", "1")
    from app import brand
    brand.save(pid, {**KIT, "website": "https://heights.example/"}, "Test")
    return hub


def test_score_a_draft(ready, monkeypatch):
    c, db, pid, ai = ready
    from app import tools
    monkeypatch.setattr(ai, "generate_json", lambda k, ctx: {
        "headlines": ["Invisalign In Sterling Heights", "Free Consultation", "Heights Orthodontics"],
        "descriptions": ["Clear aligners for adults. Book a free consultation today.", "Evening appointments."]})
    c.post(f"/p/{pid}/fixes/draft", data={"target": "google|Invisalign - Search"})
    (d,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "draft_ad"]
    seen = {}

    def fake_chat(messages, **kw):
        seen["ctx"] = messages[1]["content"]
        return ('{"scores": {"hook": {"score": 8, "reason": "Names the town"}, "specificity": {"score": 6, "reason": '
                '"Vague"}, "brand_fit": {"score": 7, "reason": "Warm"}, "call_to_action": {"score": 9, "reason": '
                '"Book"}, "care": {"score": 10, "reason": "Clean"}}, "fixes": [{"lever": "specificity", "change": '
                '"Free Consultation", "to": "Free Invisalign Consult"}], "verdict": "Solid."}')
    monkeypatch.setattr(ai, "_chat", fake_chat)
    r = c.post(f"/p/{pid}/drafts/{d['id']}/score")
    assert "Scored 7.9/10: Run it and test a variation" in r.text                 # 8*.25+6*.2+7*.2+9*.2+10*.15
    assert "Invisalign In Sterling Heights" in seen["ctx"] and "Brand kit for this practice" in seen["ctx"]
    page = c.get(f"/p/{pid}").text
    assert "Top fixes" in page and "Free Invisalign Consult" in page and "Score again" in page
    c.post(f"/p/{pid}/fixes/{d['id']}/edit", data={"headlines": "Invisalign In Sterling Heights\nFree Consult\nHeights Ortho",
                                                   "descriptions": "Book a free consultation today.\nEvening hours."})
    assert "score" not in db.change(d["id"])["payload"]                               # stale score cleared


def test_ctr_tool_uses_live_numbers_and_runs_in_background(ready, monkeypatch):
    c, db, pid, ai = ready
    seen = {}
    monkeypatch.setattr(ai, "_chat", lambda messages, **kw: seen.setdefault("ctx", messages[1]["content"]) and "## Weakest ads\nRewrite these.")
    r = c.post(f"/p/{pid}/tools/ctr")
    assert "Fix low click rates: done" in r.text and "Weakest ads" in r.text
    ctx = seen["ctx"]
    assert "Live Google ads, last 30 days" in ctx and "Invisalign in Heights" in ctx and "5,200" in ctx
    assert "Campaigns:" in ctx and "Never say: cheap" in ctx
    c.post(f"/p/{pid}/tools/ctr/delete")
    assert "Weakest ads" not in c.get(f"/p/{pid}").text


def test_keyword_ideas_become_apply_undo_proposals(ready, monkeypatch):
    c, db, pid, ai = ready
    monkeypatch.setattr(ai, "_chat", lambda messages, **kw: (
        '{"campaign": "Invisalign - Search", "keywords": [{"text": "invisalign cost near me", "why": "converted 12 times"},'
        '{"text": "Invisalign Heights", "why": "already there"}, {"text": "cheap invisalign", "why": "x"},'
        '{"text": "adult invisalign sterling heights", "why": "long tail"}], "note": "From converting terms."}'))
    mp.STATE["g_policy"] = {"adult invisalign sterling heights": True}
    r = c.post(f"/p/{pid}/tools/keywords")
    assert "Keyword ideas: done" in r.text and "waiting under Make the fixes" in r.text
    props = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "add_keyword"]
    assert sorted(p["payload"]["text"] for p in props) == ["adult invisalign sterling heights", "invisalign cost near me"]
    assert "Add keywords" in c.get(f"/p/{pid}").text
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(p["id"]) for p in props]})
    assert "Applied 2 changes" in r.text and "asked for a review" in r.text
    added = {t: ex for _, t, ex in mp.STATE["g_keywords"].values()}
    assert "invisalign cost near me" in added and added["adult invisalign sterling heights"] == [
        {"policyName": "HEALTH_TEST", "violatingText": "adult invisalign sterling heights"}]
    # Running again doesn't propose them twice; Undo removes them
    c.post(f"/p/{pid}/tools/keywords")
    for p in db.changes(pid, ["applied"]):
        if p["kind"] == "add_keyword":
            assert "Undone" in c.post(f"/p/{pid}/fixes/{p['id']}/undo").text
    assert list(t for _, t, _ in mp.STATE["g_keywords"].values()) == ["invisalign heights"]


def test_research_and_social_tools(ready, monkeypatch):
    c, db, pid, ai = ready
    seen = {}

    def fake(messages, **kw):
        seen["ctx"] = messages[1]["content"]
        return "## Teardown\nTheir angle is price."
    monkeypatch.setattr(ai, "_chat", fake)
    assert "Fill in" in c.post(f"/p/{pid}/tools/teardown", data={"competitor": "Smile Co"}).text
    r = c.post(f"/p/{pid}/tools/teardown", data={"competitor": "Smile Co", "ads": "Braces from $99! Best in town.",
                                                 "platform": "Meta"})
    assert "Competitor ad teardown: done" in r.text
    assert "Braces from $99" in seen["ctx"] and "Live Google ads" not in seen["ctx"]
    out = db.latest_ai(pid, "tool:teardown")["content"]
    assert "facebook.com/ads/library/" in out and "q=Smile+Co" in out and "adstransparency.google.com" in out
    for kind, data in (("calendar", {"month": "December 2026"}), ("reels", {}), ("leadform", {"offer": "Free consult"}),
                       ("email", {"topic": "insurance reminder"})):
        r = c.post(f"/p/{pid}/tools/{kind}", data=data)
        assert ": done" in r.text, kind
        assert "Brand kit for this practice" in seen["ctx"]
    page = c.get(f"/p/{pid}").text
    for title in ("Content calendar", "Reel ideas", "Meta lead form", "Email subject lines", "Competitor ad teardown"):
        assert title in page


def test_landing_page_check_reads_the_page(ready, monkeypatch):
    c, db, pid, ai = ready
    from app import brand
    monkeypatch.setattr(brand, "read_website", lambda url: [(url, "Straight smiles in Sterling Heights. Book now.")])
    seen = {}
    monkeypatch.setattr(ai, "_chat", lambda messages, **kw: seen.setdefault("ctx", messages[1]["content"]) and "## Grade")
    c.post(f"/p/{pid}/tools/landing", data={"url": "https://heights.example/invisalign", "promise": "Free consult"})
    assert "Straight smiles in Sterling Heights" in seen["ctx"] and "Free consult" in seen["ctx"]


def test_viewers_can_see_but_not_run_tools(ready):
    c, db, pid, ai = ready
    from app import users
    page = c.get(f"/p/{pid}").text
    assert 'action="/p/%d/tools/ctr"' % pid in page


SCORE_JSON = ('{"scores": {"hook": {"score": 6, "reason": "r"}, "specificity": {"score": 5, "reason": "r"}, '
              '"brand_fit": {"score": 7, "reason": "r"}, "call_to_action": {"score": 6, "reason": "r"}, '
              '"care": {"score": 9, "reason": "r"}}, "fixes": [%s], "verdict": "v"}')


def test_apply_these_fixes(ready, monkeypatch):
    c, db, pid, ai = ready
    monkeypatch.setattr(ai, "generate_json", lambda k, ctx: {
        "headlines": ["Invisalign In Sterling Heights", "Free Consultation", "Heights Orthodontics"],
        "descriptions": ["Clear aligners for adults. Book a free consultation today.", "Evening appointments."]})
    monkeypatch.setattr(ai, "_chat", lambda m, **k: (_ for _ in ()).throw(ai.AIError("busy")))   # no auto-polish
    c.post(f"/p/{pid}/fixes/draft", data={"target": "google|Invisalign - Search"})
    (d,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "draft_ad"]
    assert "Couldn’t score this automatically" in c.get(f"/p/{pid}").text
    fixes = ('{"lever": "specificity", "change": "Free Consultation", "to": "Free Invisalign Consult"},'
             '{"lever": "hook", "change": "Evening appointments.", "to": "Evening and Saturday visits available."},'
             '{"lever": "brand_fit", "change": "Not in the ad", "to": "Whatever"}')
    monkeypatch.setattr(ai, "_chat", lambda m, **k: SCORE_JSON % fixes)
    c.post(f"/p/{pid}/drafts/{d['id']}/score")
    assert "Apply these fixes" in c.get(f"/p/{pid}").text
    r = c.post(f"/p/{pid}/drafts/{d['id']}/apply-fixes")
    assert "Applied 2 fixes to the draft; skipped 1" in r.text
    pl = db.change(d["id"])["payload"]
    assert pl["headlines"] == ["Invisalign In Sterling Heights", "Free Invisalign Consult", "Heights Orthodontics"]
    assert pl["descriptions"][1] == "Evening and Saturday visits available."
    whys = {s["to"]: s["why"] for s in pl["fix_log"]["skipped"]}
    assert "isn't in the ad" in whys["Whatever"]
    assert "score" not in pl and pl["score_before"]["overall"] == 6.5
    page = c.get(f"/p/{pid}").text
    assert "Fixes applied (was 6.5/10)" in page and "“Free Consultation” → “Free Invisalign Consult”" in page


def test_new_drafts_are_scored_and_fixed_automatically(ready, monkeypatch):
    c, db, pid, ai = ready
    monkeypatch.setattr(ai, "generate_json", lambda k, ctx: {
        "primary_text": "Thinking about braces? Meet our team for a free consult.",
        "headline": "Free Orthodontic Consult", "description": "Book online"})
    calls = []

    def chat(m, **k):
        calls.append(1)
        if len(calls) == 1:
            return SCORE_JSON % '{"lever": "hook", "change": "Thinking about braces?", "to": "Braces at Heights Orthodontics."}'
        return SCORE_JSON.replace('"score": 6', '"score": 9') % ""
    monkeypatch.setattr(ai, "_chat", chat)
    r = c.post(f"/p/{pid}/fixes/draft", data={"target": "meta|Heights - Free Consult Leads"})
    assert "scored and fixed automatically (now" in r.text
    (d,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "draft_ad"]
    pl = d["payload"]
    assert pl["primary_text"].startswith("Braces at Heights Orthodontics.")
    assert pl["object_story_spec"]["link_data"]["message"].startswith("Braces at Heights Orthodontics.")
    assert pl["auto_polished"] and pl["score"]["overall"] > pl["score_before"]["overall"]
    assert "Fixed automatically: 6.5 →" in c.get(f"/p/{pid}").text
    assert len(calls) == 2                                                        # score, fix, score again


def test_fixes_that_break_rules_are_skipped():
    from app import tools
    ch = {"kind": "draft_ad", "platform": "google", "payload": {
        "headlines": ["Heights Orthodontics", "Free Consult"], "descriptions": ["Book today.", "Evening hours."],
        "score": {"overall": 6.0, "tier_label": "x", "fixes": []}}}
    kit = {"never_say": "cheap"}
    pl, applied, skipped = tools.apply_fixes(ch, kit, [
        {"change": "Heights Orthodontics", "to": "The Cheap Choice In Sterling Heights"},
        {"change": "Free Consult", "to": "Heights Orthodontics"},
        {"change": "Book today.", "to": "Book your visit today!"}])
    whys = [s["why"] for s in skipped]
    assert any("limit 30" in w for w in whys)                       # too long (and "cheap")
    assert any("repeat another line" in w for w in whys)            # duplicate headline
    assert applied == [{"change": "Book today.", "to": "Book your visit today!", "where": "description"}]
    assert pl["descriptions"][0] == "Book your visit today!"         # "!" is fine in descriptions
