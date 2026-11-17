"""Fix ad tracking: unknown codes out, the account's UTM suffix in, {_campaign} on every campaign, undo, and new
campaigns tagged after a sync. Against a stand-in Google Ads account."""
import json

import pytest


class FakeGoogle:
    def __init__(self):
        self.customer = {"finalUrlSuffix": "utm_source=adwords&utm_medium={adname}&utm_campaign={campaignname}",
                         "trackingUrlTemplate": ""}
        self.campaigns = {
            "customers/1/campaigns/11": {"id": 11, "name": "Heights - Search", "status": "ENABLED", "finalUrlSuffix": "",
                                         "trackingUrlTemplate": "", "urlCustomParameters": []},
            "customers/1/campaigns/12": {"id": 12, "name": "Waterford – Invisalign (Brand)", "status": "ENABLED",
                                         "finalUrlSuffix": "utm_campaign={campaignname}&ref=mc", "trackingUrlTemplate": "",
                                         "urlCustomParameters": [{"key": "office", "value": "wf"}]},
        }
        self.groups = {"customers/1/adGroups/5": {"name": "Braces", "finalUrlSuffix": "",
                                                  "trackingUrlTemplate": "{lpurl}?src={adname}&calltrk=1"}}
        self.ads = {}
        self.refuse = set()

    def search(self, cid, q):
        if "FROM customer" in q:
            return [{"customer": dict(self.customer)}]
        if "FROM campaign" in q:
            return [{"campaign": {**c, "resourceName": rn}} for rn, c in self.campaigns.items()]
        if "FROM ad_group_ad" in q:
            return [{"adGroupAd": {"ad": {**a, "resourceName": rn}}} for rn, a in self.ads.items()]
        if "FROM ad_group" in q:
            return [{"adGroup": {**a, "resourceName": rn}} for rn, a in self.groups.items()]
        return []

    def mutate(self, cid, service, ops):
        from app import connectors as cx
        for op in ops:
            u = op["update"]
            store = {"campaigns": self.campaigns, "adGroups": self.groups, "ads": self.ads}[service]
            if u["resourceName"] in self.refuse:
                raise cx.ConnectorError("This campaign type doesn't support custom parameters.")
            for k, v in u.items():
                if k != "resourceName":
                    store[u["resourceName"]][k] = v
        return [{}]

    def mutate_customer(self, cid, update, mask):
        self.customer.update(update)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ADS_DB", str(tmp_path / "ads.db"))
    import importlib
    from app import db, tracking, actions
    importlib.reload(db)
    db.init()
    g = FakeGoogle()
    monkeypatch.setattr(actions, "_google", lambda: g)
    return g, tracking, db, actions


def test_codes_and_cleaning():
    from app import tracking as t
    assert t.bad_codes("utm_source=adwords&utm_medium={adname}&x={campaignid}&y={_campaign}&z={ifmobile:m}") == ["adname"]
    assert t.clean("{lpurl}?src={adname}&calltrk=1") == "{lpurl}?calltrk=1"
    assert t.clean("utm_campaign={campaignname}") == ""
    assert t.fixed_suffix("utm_campaign={campaignname}&ref=mc") == t.SUFFIX + "&ref=mc"
    assert t.fixed_suffix("utm_campaign={campaignname}") == ""
    assert t.slug("Waterford – Invisalign (Brand)") == "waterford-invisalign-brand"


def test_apply_undo_and_auto_tag(env):
    g, tracking, db, actions = env
    prop = tracking.proposal("1", {"id": 1})
    assert prop["kind"] == "tracking" and "{adname}" in prop["detail"] and "{campaignname}" in prop["detail"]
    pl = prop["payload"]
    assert {e["name"] for e in pl["edits"]} == {"Waterford – Invisalign (Brand)", "Braces"} and len(pl["tags"]) == 2
    done = tracking.apply(g, "1", json.loads(json.dumps(pl)))
    assert g.customer["finalUrlSuffix"] == tracking.SUFFIX
    wf = g.campaigns["customers/1/campaigns/12"]
    assert wf["finalUrlSuffix"] == tracking.SUFFIX + "&ref=mc"                        # keeps its own ref=mc
    assert {"key": "office", "value": "wf"} in wf["urlCustomParameters"]               # other custom parameters kept
    assert {"key": "campaign", "value": "waterford-invisalign-brand"} in wf["urlCustomParameters"]
    assert g.groups["customers/1/adGroups/5"]["trackingUrlTemplate"] == "{lpurl}?calltrk=1"
    assert tracking.plan(tracking.read(g, "1")) is None                                # nothing left to fix
    assert tracking.status()["on"] == ["1"]
    # a campaign added later gets its tag after the next sync
    g.campaigns["customers/1/campaigns/13"] = {"id": 13, "name": "Havens PMax", "status": "ENABLED", "finalUrlSuffix": "",
                                               "trackingUrlTemplate": "", "urlCustomParameters": []}
    assert tracking.tag_new_campaigns(g) == {"1": ["Havens PMax"]}
    assert g.campaigns["customers/1/campaigns/13"]["urlCustomParameters"] == [{"key": "campaign", "value": "havens-pmax"}]
    assert tracking.tag_new_campaigns(g) == {}                                         # nothing new the second time
    # undo puts everything back
    tracking.undo(g, "1", done)
    assert g.customer["finalUrlSuffix"].endswith("{campaignname}")
    assert g.campaigns["customers/1/campaigns/12"]["finalUrlSuffix"] == "utm_campaign={campaignname}&ref=mc"
    assert g.campaigns["customers/1/campaigns/11"]["urlCustomParameters"] == []
    assert tracking.status()["on"] == []


def test_refusals_and_stale(env):
    g, tracking, db, actions = env
    g.refuse.add("customers/1/campaigns/11")
    pl = tracking.proposal("1", {"id": 1})["payload"]
    done = tracking.apply(g, "1", pl)
    assert "didn't accept 1 change" in done["warning"] and g.customer["finalUrlSuffix"] == tracking.SUFFIX
    g.customer["finalUrlSuffix"] = "utm_medium={adname}"            # someone puts a bad suffix back
    pl2 = tracking.plan(tracking.read(g, "1"))
    g.customer["finalUrlSuffix"] = "changed=by-someone"               # ...and changes it again before Apply
    with pytest.raises(actions.ActionError):
        tracking.apply(g, "1", pl2)
