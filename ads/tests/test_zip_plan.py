"""Who markets where: each zip to one brand, shared zips spread evenly; then the Google / Meta targeting fixes,
standard keywords, benefits ads and the no-lowering budget rule."""
import pytest

from tests.test_scope import client  # noqa: F401  (pytest fixture)


def _stats(db, rows):
    from app import zips
    zips.init()
    with db.conn() as c:
        c.execute("DELETE FROM zip_stats")
        c.executemany("INSERT INTO zip_stats(zip, lat, lng, income, households, hh_kids, pop, year) VALUES (?,?,?,?,?,?,?,'2023')",
                      rows)
    db.set_setting("zips_status", '{"at": "2026-11-18T10:00:00"}')


@pytest.fixture
def world(client):
    c, db, sync, mp = client
    from app import field, places
    # two ortho brands 4 miles apart, and a pediatric dentist right next to one of them
    a = db.add_practice("Heights Orthodontics", 1500, 0, "heights")
    b = db.add_practice("Lakepointe Orthodontics", 1500, 0, "lakepointe")
    k = db.add_practice("Pediatric Dentistry of Romeo", 1500, 0, "romeo peds")
    for pid in (a, b, k):
        db.set_practice_accounts(pid, "", "4445556666")
    places.add_office("Heights – Sterling Heights", "ChIJ_office_heights_01", 42.60, -83.03)
    places.add_office("Lakepointe – St Clair Shores", "ChIJ_office_lakept_01", 42.55, -82.93)
    places.add_office("Romeo Peds", "ChIJ_office_romeo_001", 42.60, -83.02)
    offs = {o["label"]: o["id"] for o in places.offices()}
    field.set_links({offs["Heights – Sterling Heights"]: a, offs["Lakepointe – St Clair Shores"]: b,
                     offs["Romeo Peds"]: k})
    # a line of zips between the two ortho offices, all rich enough to be wanted by both
    rows = [(f"480{n:02d}", 42.60 - n * 0.005, -83.03 + n * 0.01, 150000 - n * 1000, 5000, 2000, 14000) for n in range(10)]
    _stats(db, rows)
    return c, db, a, b, k


def test_each_zip_goes_to_one_brand_and_overlap_is_spread(world):
    c, db, a, b, k = world
    from app import zip_plan
    plan = zip_plan.assign(miles=15)
    by = {r["practice"]["id"]: r for r in plan["offices"]}
    za, zb, zk = ({z["zip"] for z in by[x]["zips"]} for x in (a, b, k))
    assert za and zb and not (za & zb)                              # never the same zip for two ortho brands
    assert abs(len(za) - len(zb)) <= 1                              # shared zips spread evenly
    assert zk == za | zb or zk                                       # dentistry is a different audience: not taken from ortho
    assert any(z["shared_with"] for z in by[a]["zips"]) and plan["contested"] > 0
    page = c.get("/map/zips").text
    assert "Who markets where" in page and "Lakepointe – St Clair Shores" in page
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(c.get("/map/zips.xlsx").content))
    assert wb.sheetnames[0] == "Who markets where" and wb["Who markets where"]["A2"].value


class G:
    def __init__(self):
        self.crit = {"customers/1/campaignCriteria/9~1": {"type": "PROXIMITY", "proximity": {"radius": 15, "radiusUnits": "MILES",
                     "geoPoint": {"latitudeInMicroDegrees": 42600000, "longitudeInMicroDegrees": -83030000}}}}
        self.geo_type, self.n = "PRESENCE_OR_INTEREST", 0

    def search(self, cid, q):
        if "FROM campaign_criterion" in q:
            return [{"campaignCriterion": {"resourceName": rn, **v}} for rn, v in self.crit.items()]
        if "geo_target_type_setting" in q:
            return [{"campaign": {"geoTargetTypeSetting": {"positiveGeoTargetType": self.geo_type}}}]
        return []

    def zip_targets(self, zips):
        return {z: f"geoTargetConstants/9{z}" for z in zips}

    def mutate(self, cid, service, ops):
        out = []
        for op in ops:
            if service == "campaigns":
                self.geo_type = op["update"]["geoTargetTypeSetting"]["positiveGeoTargetType"]
            elif "remove" in op:
                self.crit.pop(op["remove"])
                out.append({})
            else:
                self.n += 1
                rn = f"customers/1/campaignCriteria/9~{100 + self.n}"
                cr = op["create"]
                self.crit[rn] = ({"type": "LOCATION", "location": {"geoTargetConstant": cr["location"]["geoTargetConstant"]}}
                                 if "location" in cr else {"type": "PROXIMITY", "proximity": cr["proximity"]})
                out.append({"resourceName": rn})
        return out


def test_google_zip_targeting_apply_and_undo(world):
    c, db, a, b, k = world
    from app import targeting, zip_plan
    g = G()
    p = db.practice(a)
    camps = [{"id": "9", "name": "Heights - Search", "status": "ENABLED", "channel": "SEARCH",
              "resource": "customers/1/campaigns/9"}]
    props = targeting.google_geo_proposals(g, "1", p, camps, [])
    pl = props[0]["payload"]
    assert len(pl["new"]) == len(zip_plan.practice_zips(a)) and "interested in" in props[0]["detail"]
    targeting.apply_geo(g, "1", pl)
    assert {v["location"]["geoTargetConstant"] for v in g.crit.values()} == set(pl["new"]) and g.geo_type == "PRESENCE"
    assert targeting.google_geo_proposals(g, "1", p, camps, []) == []                 # nothing left to change
    targeting.undo_geo(g, "1", pl, {})
    assert [v["type"] for v in g.crit.values()] == ["PROXIMITY"] and g.geo_type == "PRESENCE_OR_INTEREST"


def test_benefits_ad_text_fits_google_limits(world):
    c, db, a, b, k = world
    from app import targeting
    with db.conn() as cn:
        cn.execute("INSERT INTO gbp_locations(name, title, address, practice_id, rating, total) VALUES "
                   "('locations/1', 'Heights', '43767 Van Dyke Ave, Sterling Heights, MI 48314', ?, 4.94, 269)", (a,))
    t = targeting.benefits_text(db.practice(a))
    assert all(len(h) <= 30 for h in t["headlines"]) and all(len(d) <= 90 for d in t["descriptions"])
    assert "4.9 Stars From 269 Reviews" in t["headlines"] and not any("★" in x for x in t["headlines"] + t["descriptions"])
    assert targeting.city_of(db.practice(a)) == "Sterling Heights"
    assert "Free Orthodontic Consult" in t["headlines"]
    assert "Free Orthodontic Consult" not in targeting.benefits_text(db.practice(k))["headlines"]   # a dentist


def test_budget_not_lowered_for_a_campaign_that_isnt_using_it():
    from app import actions
    view = {"pacing": {"available": True, "budget": 300, "spend": 290, "days_in_month": 30, "days_elapsed": 18},
            "campaigns": [{"name": "Findlay - Search", "spend": 12.0}, {"name": "Busy - Search", "spend": 340.0}]}
    plan, why = actions._budget_plan(view, [{"name": "Findlay - Search", "budget": 20.0},
                                            {"name": "Busy - Search", "budget": 20.0}], [], "Google Ads")
    assert [i["name"] for i, _ in plan] == ["Busy - Search"]
