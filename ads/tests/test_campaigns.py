"""Brand-new Google Search campaigns: drafted from the brand kit, reviewed, created paused in one request, undone."""
from tests import mock_platforms as mp
from tests.test_actions import hub  # noqa: F401
from tests.test_brand import KIT

ADDR = "43767 Van Dyke Ave, Sterling Heights, MI 48314"


def _fake(seen):
    def fake(kind, ctx):
        assert kind == "google_campaign"
        seen["ctx"] = ctx
        return {"ad_group_name": "Invisalign adults",
                "keywords": ["invisalign sterling heights", "Invisalign near me!", "adult braces sterling heights",
                             "cheap invisalign", "clear aligners for adults"],
                "headlines": ["Invisalign In Sterling Heights", "Heights Orthodontics", "Free Consultation",
                              "Best Aligners Around", "Clear Aligners For Adults"],
                "descriptions": ["Straighter teeth with clear aligners. Book a free consultation today.",
                                 "$0 down payment plans. Evening and Saturday appointments.",
                                 "Call 586-555-1234 now.", "Gentle care for adults and teens, close to home."]}
    return fake


def test_parse_address():
    from app import campaigns
    assert campaigns.parse_address(ADDR) == {"streetAddress": "43767 Van Dyke Ave", "cityName": "Sterling Heights",
                                             "provinceCode": "US-MI", "countryCode": "US", "postalCode": "48314"}
    assert campaigns.parse_address("1305 S Main St, Suite 4, Chelsea, MI 48118")["cityName"] == "Chelsea"
    assert campaigns.parse_address("Sterling Heights") is None
    assert campaigns.parse_address("1 Main St, Somewhere, ZZ 12345") is None


def test_draft_review_create_paused_and_undo(hub, monkeypatch):
    c, db, pid, ai = hub
    monkeypatch.setenv("ADS_SYNC_JOBS", "1")
    from app import brand, campaigns
    brand.save(pid, {**KIT, "website": "https://heights.example/"}, "Test")
    db.update_practice(pid, "Heights Orthodontics", 3000, 900, "", campaign_filter="Heights")
    seen = {}
    monkeypatch.setattr(ai, "generate_json", _fake(seen))
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    html = c.get(f"/p/{pid}").text
    assert "New Google campaign" in html and "Write the campaign" in html and 'value="https://heights.example/"' in html

    r = c.post(f"/p/{pid}/campaign/new", data={"focus": "Invisalign for adults", "daily_budget": "25",
                                                "radius": "8", "addresses": ADDR, "final_url": "https://heights.example/"})
    assert "Campaign drafted" in r.text
    assert "Brand kit for this practice" in seen["ctx"] and "Invisalign for adults" in seen["ctx"] and ADDR in seen["ctx"]
    (ch,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "new_campaign"]
    pl = ch["payload"]
    assert pl["name"] == "Heights - Search - Invisalign for adults"
    assert pl["keywords"] == ["invisalign sterling heights", "invisalign near me", "adult braces sterling heights",
                              "clear aligners for adults"]                        # "cheap" dropped (never say)
    assert "Best Aligners Around" not in pl["headlines"] and len(pl["descriptions"]) == 3   # superlative, phone dropped
    assert pl["daily_budget"] == 25 and pl["radius"] == 8 and pl["max_cpc"] is None
    assert mp.STATE["g_new"] == {}                                                # nothing sent yet
    assert "Invisalign adults" in c.get(f"/p/{pid}").text

    # Edits are checked: a name that wouldn't show on this practice's page, then a bad headline
    bad = {"name": "Other - Search", "daily_budget": "30", "radius": "10", "addresses": ADDR,
           "final_url": "https://heights.example/invisalign", "ad_group": "Invisalign adults",
           "keywords": "invisalign sterling heights\nclear aligners", "negatives": "jobs",
           "headlines": "Heights Orthodontics\nFree Consultation\nInvisalign In Sterling Hts",
           "descriptions": "Book a free consultation today.\nEvening and Saturday appointments."}
    assert "Not saved" in c.post(f"/p/{pid}/campaign/{ch['id']}/edit", data=bad).text
    good = {**bad, "name": "Heights - Search - Invisalign", "max_cpc": "4.50"}
    r = c.post(f"/p/{pid}/campaign/{ch['id']}/edit", data={**good, "headlines": good["headlines"] + "\nCheap Aligners"})
    assert "Not saved" in r.text and "never say" in r.text
    r = c.post(f"/p/{pid}/campaign/{ch['id']}/edit", data=good)
    assert "Campaign saved" in r.text
    ch = db.change(ch["id"])
    assert ch["payload"]["max_cpc"] == 4.5 and ch["payload"]["keywords"] == ["invisalign sterling heights", "clear aligners"]

    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(ch["id"])], "back": "campaign"})
    assert "Applied 1 change" in r.text
    (crn, ops), = mp.STATE["g_new"].items()
    kinds = [next(iter(o)) for o in ops]
    assert kinds[:2] == ["campaignBudgetOperation", "campaignOperation"]
    camp = ops[1]["campaignOperation"]["create"]
    assert camp["status"] == "PAUSED" and camp["advertisingChannelType"] == "SEARCH"
    assert camp["targetSpend"] == {"cpcBidCeilingMicros": "4500000"}
    assert camp["networkSettings"]["targetSearchNetwork"] is False and camp["networkSettings"]["targetContentNetwork"] is False
    assert camp["geoTargetTypeSetting"]["positiveGeoTargetType"] == "PRESENCE"
    assert ops[0]["campaignBudgetOperation"]["create"]["amountMicros"] == "30000000"
    prox = [o["campaignCriterionOperation"]["create"]["proximity"] for o in ops
            if "proximity" in o.get("campaignCriterionOperation", {}).get("create", {})]
    assert prox == [{"address": campaigns.parse_address(ADDR), "radius": 10.0, "radiusUnits": "MILES"}]
    kw = [o["adGroupCriterionOperation"]["create"]["keyword"] for o in ops if "adGroupCriterionOperation" in o]
    assert kw == [{"text": "invisalign sterling heights", "matchType": "PHRASE"}, {"text": "clear aligners", "matchType": "PHRASE"}]
    ad = ops[-1]["adGroupAdOperation"]["create"]["ad"]
    assert ad["finalUrls"] == ["https://heights.example/invisalign"] and ad["responsiveSearchAd"]["headlines"][0]["text"] == "Heights Orthodontics"
    assert "Campaigns created here (1)" in c.get(f"/p/{pid}").text

    # Same name again is refused; Undo removes it
    c.post(f"/p/{pid}/campaign/new", data={"focus": "Invisalign", "daily_budget": "25", "radius": "8",
                                            "addresses": ADDR, "final_url": "https://heights.example/"})
    (ch2,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "new_campaign"]
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(ch2["id"])]})
    assert "already exists" in r.text and len(mp.STATE["g_new"]) == 1
    r = c.post(f"/p/{pid}/fixes/{ch['id']}/undo", data={"back": "campaign"})
    assert "Undone" in r.text and mp.STATE["g_new"] == {}


def test_draft_needs_addresses_and_a_google_account(hub, monkeypatch):
    c, db, pid, ai = hub
    monkeypatch.setenv("ADS_SYNC_JOBS", "1")
    monkeypatch.setattr(ai, "generate_json", _fake({}))
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    c.post(f"/p/{pid}/campaign/new", data={"focus": "Braces", "daily_budget": "20", "radius": "10",
                                            "addresses": "", "final_url": "https://heights.example/"})
    (ch,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "new_campaign"]
    assert "Fix before applying" in ch["detail"] and "office address" in ch["detail"]
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(ch["id"])]})
    assert "office address" in r.text and mp.STATE["g_new"] == {}
    assert "between" in c.post(f"/p/{pid}/campaign/new", data={"focus": "x", "daily_budget": "0", "radius": "10",
                                                               "addresses": ADDR}).text
    other = db.add_practice("Unlinked Ortho", 0, 0)
    r = c.post(f"/p/{other}/campaign/new", data={"focus": "x", "daily_budget": "20", "radius": "10", "addresses": ADDR})
    assert "Google Ads account first" in r.text


def test_lowercase_ai_text_is_recased(hub, monkeypatch):
    c, db, pid, ai = hub
    monkeypatch.setenv("ADS_SYNC_JOBS", "1")
    from app import brand
    brand.save(pid, {"must_say": "Eric Groeneveld DDS", "locations": "Richmond, MI"}, "Test")
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    monkeypatch.setattr(ai, "generate_json", lambda k, ctx: {
        "ad_group_name": "dental care", "keywords": ["dentist richmond mi", "family dentist richmond", "dentist near me"],
        "headlines": ["eric groeneveld dds", "richmond dental care", "book an appointment"],
        "descriptions": ["book an appointment with our team in richmond, mi today.", "we offer financing options. book today."]})
    c.post(f"/p/{pid}/campaign/new", data={"focus": "dental care", "daily_budget": "10", "radius": "10",
                                            "addresses": "68720 Main St, Richmond, MI 48062"})
    (ch,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "new_campaign"]
    assert ch["payload"]["headlines"] == ["Eric Groeneveld DDS", "Richmond Dental Care", "Book an Appointment"]
    assert ch["payload"]["descriptions"][0] == "Book an appointment with our team in Richmond, MI today."
    assert ch["payload"]["keywords"][0] == "dentist richmond mi"                         # keywords stay lowercase


def test_google_policy_findings_are_shown_and_review_can_be_requested(hub, monkeypatch):
    c, db, pid, ai = hub
    monkeypatch.setenv("ADS_SYNC_JOBS", "1")
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    monkeypatch.setattr(ai, "generate_json", _fake({}))
    mp.STATE["g_policy"] = {"invisalign near me": True, "adult braces sterling heights": False}
    c.post(f"/p/{pid}/campaign/new", data={"focus": "Invisalign", "daily_budget": "10", "radius": "10",
                                            "addresses": ADDR, "final_url": "https://heights.example/"})
    (ch,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "new_campaign"]
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(ch["id"])], "back": "campaign"})
    assert "Google flagged 1 keyword for “Healthcare and medicines” (“invisalign near me”)" in r.text
    assert "1 keyword for “Healthcare and medicines” (“adult braces sterling heights”), which can" in r.text
    assert mp.STATE["g_new"] == {}
    page = c.get(f"/p/{pid}").text
    assert "Flagged by Google" in page and "Ask Google to review the flagged items" in page
    ch = db.change(ch["id"])
    pl = ch["payload"]
    form = {"name": pl["name"], "daily_budget": "10", "radius": "10", "addresses": ADDR, "final_url": pl["final_url"],
            "ad_group": pl["ad_group"], "negatives": "\n".join(pl["negatives"]),
            "keywords": "\n".join(k for k in pl["keywords"] if k != "adult braces sterling heights"),
            "headlines": "\n".join(pl["headlines"]), "descriptions": "\n".join(pl["descriptions"]), "review": "1"}
    assert "Campaign saved" in c.post(f"/p/{pid}/campaign/{ch['id']}/edit", data=form).text
    assert [f["text"] for f in db.change(ch["id"])["payload"]["flagged"]] == ["invisalign near me"]
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(ch["id"])], "back": "campaign"})
    assert "Applied 1 change" in r.text
    (ops,) = mp.STATE["g_new"].values()
    ex = [o["adGroupCriterionOperation"] for o in ops if "adGroupCriterionOperation" in o
          and o["adGroupCriterionOperation"]["create"]["keyword"]["text"] == "invisalign near me"][0]
    assert ex["exemptPolicyViolationKeys"] == [{"policyName": "HEALTH_TEST", "violatingText": "invisalign near me"}]


def test_writing_runs_in_the_background(hub, monkeypatch):
    c, db, pid, ai = hub
    import threading
    from app import main
    monkeypatch.delenv("ADS_SYNC_JOBS", raising=False)
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    gate = threading.Event()

    def slow(kind, ctx):
        gate.wait(5)
        return _fake({})(kind, ctx)
    monkeypatch.setattr(ai, "generate_json", slow)
    r = c.post(f"/p/{pid}/campaign/new", data={"focus": "Invisalign", "daily_budget": "10", "radius": "10",
                                                "addresses": ADDR, "final_url": "https://heights.example/"})
    assert "Writing the campaign" in r.text and "location.reload" in r.text          # came back straight away
    gate.set()
    for _ in range(50):
        if main.CAMP_JOBS.get(pid, {}).get("state") != "running":
            break
        import time
        time.sleep(0.1)
    assert "Campaign drafted" in c.get(f"/p/{pid}").text
    assert "Campaign drafted" not in c.get(f"/p/{pid}").text                          # message shown once


def test_one_click_review_for_health_keywords(hub, monkeypatch):
    c, db, pid, ai = hub
    monkeypatch.setenv("ADS_SYNC_JOBS", "1")
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    monkeypatch.setattr(ai, "generate_json", _fake({}))
    mp.STATE["g_policy"] = {"invisalign near me": True, "adult braces sterling heights": True}
    c.post(f"/p/{pid}/campaign/new", data={"focus": "Invisalign", "daily_budget": "10", "radius": "10",
                                            "addresses": ADDR, "final_url": "https://heights.example/"})
    (ch,) = [x for x in db.changes(pid, ["proposed"]) if x["kind"] == "new_campaign"]
    r = c.post(f"/p/{pid}/fixes/apply", data={"ids": [str(ch["id"])], "back": "campaign"})
    assert "Google flagged 2 keywords for “Healthcare and medicines”" in r.text
    page = c.get(f"/p/{pid}").text
    assert "keywords (2): “invisalign near me”, “adult braces sterling heights”" in page
    assert "Ask Google to review and create" in page
    r = c.post(f"/p/{pid}/campaign/{ch['id']}/review-and-create")
    assert "with a review request" in r.text and len(mp.STATE["g_new"]) == 1
