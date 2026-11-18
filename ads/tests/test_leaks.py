"""Stopping the leaks: Audience Network out of Meta ad sets, campaigns LeadSigma says don't book, UTM tags on
Meta ads, and Google conversion actions that count clicks instead of bookings. Against stand-in accounts."""
import pytest


class FakeMeta:
    def __init__(self):
        self.objects = {
            "as1": {"name": "Parents 30-55", "campaign_id": "c1", "status": "ACTIVE",
                    "targeting": {"geo_locations": {"zips": [{"key": "US:48314"}]}, "age_min": 30}},
            "as2": {"name": "Adults", "campaign_id": "c1", "status": "ACTIVE",
                    "targeting": {"publisher_platforms": ["facebook", "instagram"]}},
            "as3": {"name": "Spring (ended)", "campaign_id": "c1", "status": "ACTIVE", "end_time": "2026-03-31T23:59:00-0400",
                    "targeting": {}},
            "ad1": {"name": "Price ad", "campaign_id": "c1", "status": "ACTIVE",
                    "creative": {"id": "cr1", "name": "Price", "url_tags": "ref=mc",
                                 "object_story_spec": {"page_id": "7", "link_data": {"link": "https://x.example"}}}},
            "ad2": {"name": "Boosted post", "campaign_id": "c1", "status": "ACTIVE",
                    "creative": {"id": "cr2", "object_story_id": "7_99"}},
            "ad3": {"name": "Dynamic", "campaign_id": "c1", "status": "ACTIVE",
                    "creative": {"id": "cr3", "asset_feed_spec": {"bodies": []}}},
        }
        self.creatives, self.n = {}, 0

    def paged(self, path, params, **kw):
        kind = path.split("/")[-1]
        pre = {"adsets": "as", "ads": "ad"}[kind]
        return [{"id": k, **v} for k, v in self.objects.items() if k.startswith(pre)]

    def get(self, oid, params):
        return {"id": oid, **self.objects[oid]}

    def write(self, path, params, method="POST"):
        if path.endswith("/adcreatives"):
            self.n += 1
            cid = f"new{self.n}"
            self.creatives[cid] = params
            return {"id": cid}
        o = self.objects[path]
        if "creative" in params:
            cid = params["creative"]["creative_id"]
            o["creative"] = {"id": cid, **self.creatives.get(cid, {})}
        else:
            o.update(params)
        return {"success": True}


@pytest.fixture
def meta(monkeypatch, tmp_path):
    monkeypatch.setenv("ADS_DB", str(tmp_path / "ads.db"))
    import importlib
    from app import db, leaks, connectors as cx
    importlib.reload(db)
    db.init()
    m = FakeMeta()
    monkeypatch.setattr(cx, "_meta_paged", m.paged)
    monkeypatch.setattr(cx, "_meta_get", m.get)
    monkeypatch.setattr(cx, "_meta_write", m.write)
    return m, leaks


CAMPS = [{"id": "c1", "name": "Price is right at Heights campaign 10-1-2026", "status": "ACTIVE"}]


def test_audience_network_out_and_back(meta):
    m, leaks = meta
    props = leaks.placement_proposals("1", CAMPS)
    assert [p["payload"]["adset"] for p in props] == ["Parents 30-55"]   # as2 already Facebook + Instagram; as3 has ended
    pl = props[0]["payload"]
    leaks.apply_placements(pl)
    t = m.objects["as1"]["targeting"]
    assert t["publisher_platforms"] == ["facebook", "instagram"] and t["age_min"] == 30   # the rest of the targeting kept
    assert leaks.placement_proposals("1", CAMPS) == []
    leaks.undo_placements(pl)
    assert "publisher_platforms" not in m.objects["as1"]["targeting"]
    t = leaks.without_audience_network({"publisher_platforms": ["facebook", "audience_network"],
                                        "audience_network_positions": ["classic"]})
    assert t == {"publisher_platforms": ["facebook"]}


def test_leadsigma_pause(meta, monkeypatch):
    m, leaks = meta
    monkeypatch.setattr(leaks, "leadsigma_dead", lambda today=None: [
        {"campaign": "Price is right at Heights campaign", "views": 3020, "bookings": 0, "sources": [("an", 2050), ("fb", 970)]}])
    p = leaks.leadsigma_pauses("1", CAMPS)
    assert p[0]["kind"] == "pause" and "3,020 visits" in p[0]["detail"] and "2,050 of them from Audience Network" in p[0]["detail"]
    assert leaks.leadsigma_pauses("1", CAMPS, already={"c1"}) == []
    assert not leaks.matches_campaign("Spring", "Spring Promo - Waterford")             # too short to trust


def test_tag_meta_ads_and_undo(meta):
    m, leaks = meta
    prop = leaks.tag_proposal("1", CAMPS)
    assert {a["ad"] for a in prop["payload"]["ads"]} == {"Price ad", "Boosted post"} and "Dynamic" in prop["detail"]
    res = leaks.apply_tags("1", prop["payload"])
    tags = m.objects["ad1"]["creative"]["url_tags"]
    assert tags.startswith("utm_source=facebook&utm_medium=paid_social") and tags.endswith("&ref=mc")
    assert m.objects["ad2"]["creative"]["object_story_id"] == "7_99"
    assert leaks.tag_proposal("1", CAMPS) is None                                  # nothing left to tag
    leaks.undo_tags(res)
    assert m.objects["ad1"]["creative"]["id"] == "cr1" and m.objects["ad2"]["creative"]["id"] == "cr2"


class FakeGoogle:
    def __init__(self, acts, goals=None, camp_goals=None):
        self.acts, self.goals, self.camp_goals = acts, goals or {}, camp_goals or {}

    def search(self, cid, q):
        if "FROM customer_conversion_goal" in q:
            return [{"customerConversionGoal": {"category": c, "origin": o, "biddable": b}} for (c, o), b in self.goals.items()]
        if "FROM campaign_conversion_goal" in q:
            return [{"campaignConversionGoal": {"resourceName": rn, "biddable": b}} for rn, b in self.camp_goals.items() if b]
        if "FROM conversion_action" in q:
            return [{"conversionAction": {"resourceName": rn, "name": a["name"], "category": a["category"],
                                          "origin": a.get("origin", "WEBSITE"), "primaryForGoal": a["primary"]}}
                    for rn, a in self.acts.items()]
        return [{"segments": {"conversionAction": rn}, "metrics": {"allConversions": a["n"]}} for rn, a in self.acts.items()]

    def mutate(self, cid, service, ops):
        for op in ops:
            u = op["update"]
            if service == "conversionActions":
                if self.acts[u["resourceName"]].get("origin") == "GOOGLE_HOSTED":
                    raise RuntimeError("Mutates are not allowed for the requested resource.")
                self.acts[u["resourceName"]]["primary"] = u["primaryForGoal"]
            elif service == "customerConversionGoals":
                cat, origin = u["resourceName"].split("/")[-1].split("~")
                self.goals[(cat, origin)] = u["biddable"]
            else:
                self.camp_goals[u["resourceName"]] = u["biddable"]
        return [{}]


def test_google_hosted_local_actions_switched_off_as_a_goal(meta):
    _, leaks = meta
    g = FakeGoogle({"a/1": {"name": "Local actions - Website visits", "category": "PAGE_VIEW", "origin": "GOOGLE_HOSTED",
                            "primary": True, "n": 121},
                    "a/2": {"name": "Local actions - Other engagements", "category": "ENGAGEMENT",
                            "origin": "GOOGLE_HOSTED", "primary": True, "n": 394},
                    "a/3": {"name": "Calls from ad", "category": "PHONE_CALL_LEAD", "origin": "CALL_FROM_ADS", "primary": True, "n": 6}},
                   goals={("PAGE_VIEW", "GOOGLE_HOSTED"): True, ("ENGAGEMENT", "GOOGLE_HOSTED"): True,
                          ("PHONE_CALL_LEAD", "CALL_FROM_ADS"): True},
                   camp_goals={"customers/1/campaignConversionGoals/9~PAGE_VIEW~GOOGLE_HOSTED": True})
    prop = leaks.conversion_proposal(g, "1")
    assert "515 in the last 30 days" in prop["detail"]
    res = leaks.apply_conversion_goal(g, "1", prop["payload"])
    assert g.goals[("PAGE_VIEW", "GOOGLE_HOSTED")] is False and g.goals[("ENGAGEMENT", "GOOGLE_HOSTED")] is False
    assert g.goals[("PHONE_CALL_LEAD", "CALL_FROM_ADS")] is True and res["campaign_goals"]
    assert leaks.conversion_proposal(g, "1") is None                    # no longer counting: not offered again
    leaks.undo_conversion_goal(g, "1", res)
    assert all(g.goals.values()) and all(g.camp_goals.values())


def test_conversion_goal(meta):
    _, leaks = meta
    g = FakeGoogle({"a/1": {"name": "Page view - thank you", "category": "PAGE_VIEW", "primary": True, "n": 170},
                    "a/2": {"name": "LeadSigma booking", "category": "BOOK_APPOINTMENT", "primary": True, "n": 9},
                    "a/3": {"name": "Calls from ads", "category": "PHONE_CALL_LEAD", "primary": True, "n": 6},
                    "a/4": {"name": "Outbound click", "category": "OUTBOUND_CLICK", "primary": False, "n": 40}})
    prop = leaks.conversion_proposal(g, "1")
    assert [a["name"] for a in prop["payload"]["actions"]] == ["Page view - thank you"]
    assert "170 in the last 30 days" in prop["detail"] and "LeadSigma booking" in prop["detail"]
    res = leaks.apply_conversion_goal(g, "1", prop["payload"])
    assert g.acts["a/1"]["primary"] is False and g.acts["a/2"]["primary"] is True
    assert leaks.conversion_proposal(g, "1") is None
    leaks.undo_conversion_goal(g, "1", res)
    assert g.acts["a/1"]["primary"] is True
    # nothing that counts bookings: don't demote, say what's missing instead
    only_weak = FakeGoogle({"a/1": {"name": "Website visit", "category": "PAGE_VIEW", "primary": True, "n": 200}})
    notes = []
    assert leaks.conversion_proposal(only_weak, "1", notes) is None and "Add a booking or call conversion" in notes[0]


def test_brand_and_core_searches_are_not_blocked_on_one_click():
    from app import leaks
    havens = {"name": "Havens Orthodontics", "campaign_filter": "havens"}
    assert not leaks.safe_to_block("havens orthodontics near me", 7, 2, havens)         # our own name: never
    assert not leaks.safe_to_block("orthodontist in st clair shores", 9, 1, havens)      # core search, one click
    assert leaks.safe_to_block("orthodontist in st clair shores", 60, 7, havens)         # ...unless it keeps costing
    assert leaks.safe_to_block("orthodontists that accept medicaid", 8, 1, havens)       # the wrong person
    assert leaks.safe_to_block("cheap braces near me", 6, 1, havens)
    assert not leaks.safe_to_block("invisalign near me", 9, 1, {"name": "MCDP Ads"}, "Wheeler - Search - Toledo")
    assert leaks.safe_to_block("teeth whitening kit", 6, 1, havens)                      # not what we offer


def test_listing_actions_kept_apart_from_conversions(meta):
    _, leaks = meta
    from datetime import date
    from app import db, report
    pid = db.add_practice("Waterford Orthodontics", 1500, 0, "")
    d = date(2026, 11, 3)
    db.archive_rows(pid, "google", [{"date": d, "campaign": "Waterford Oct2026", "spend": 40, "impressions": 900,
                                     "clicks": 60, "conversions": 2, "listing": 31}], d, d)
    row = db.archived(pid, "google", d, d)[0]
    assert row["listing"] == 31 and row["conversions"] == 2
    paid = report._paid(db.practice(pid), d, d)
    assert paid["listing"] == 31 and paid["clicks"] == 60 and paid["conversions"] == 2


def test_dental_services_are_core_for_dentists_not_for_orthodontists():
    from app import leaks
    algonac = {"name": "Algonac Dental", "campaign_filter": "algonac"}
    for term in ("emergency dentist near me", "root canal near me", "teeth implants near me", "pain free dentist near me"):
        assert not leaks.safe_to_block(term, 10, 1, algonac), term
    havens = {"name": "Havens Orthodontics", "campaign_filter": "havens"}
    assert leaks.safe_to_block("emergency dentist near me", 9, 1, havens)
    assert leaks.safe_to_block("root canal near me", 9, 1, {"name": "MCDP Ads"}, "Wheeler - Search - Toledo Orthodontics")
    assert not leaks.safe_to_block("root canal near me", 9, 1, {"name": "MCDP Ads"}, "Algonac - Search - Algonac Dental Care")
    assert not leaks.safe_to_block("free braces consultation", 6, 1, havens)          # free consults are good leads


def test_misspelled_brand_and_aligner_brands_are_kept():
    from app import leaks
    charchut = {"name": "Charchut Orthodontics", "campaign_filter": "charchut"}
    assert not leaks.safe_to_block("dr charcut", 7, 1, charchut)
    assert not leaks.safe_to_block("clearcorrect near me", 7, 1, {"name": "Norwick Orthodontics", "campaign_filter": "norwick"})
    assert leaks.safe_to_block("dr hummon", 7, 1, charchut)                      # a competitor's name: fine to block


def test_qualified_leads_and_default_forms_keep_counting():
    from app import leaks
    assert not leaks.is_weak({"name": "MQL - Howell (GCLID)", "category": "DEFAULT"})
    assert not leaks.is_weak({"name": "chelseaorthodontics.com (web) contactus_formsubmit", "category": "DEFAULT"})
    assert leaks.is_weak({"name": "Local actions - Website visits", "category": "PAGE_VIEW"})
