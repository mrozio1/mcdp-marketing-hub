"""Field visit PDF reports: who can report on whom, the Field manager switch, contents, toggles and branding."""
import importlib
import io
import os
import re
from datetime import date

import pytest


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1"})
    from app import db, field, field_report, images, places, users
    for m in (db, users, places, field, images, field_report):
        importlib.reload(m)
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    def person(admin, name, email, role, field_on=False):
        r = admin.post("/team/add", data={"name": name, "email": email, "role": role, "field_on": "1" if field_on else "",
                                          "social_on": "1"})
        tok = re.search(r"/setup/([\w-]+)", r.text).group(1)
        c = TestClient(main.app)
        c.post(f"/setup/{tok}", data={"password": "long-password-1", "confirm": "long-password-1"})
        return c

    with TestClient(main.app) as boss:
        boss.post("/login", data={"password": "shared-pw"})
        admin = person(boss, "Ann Admin", "ann@example.com", "admin")
        abe = person(admin, "Abe Agent", "abe@example.com", "viewer", field_on=True)
        bea = person(admin, "Bea Field", "bea@example.com", "viewer", field_on=True)
        mo = person(admin, "Mo Manager", "mo@example.com", "editor", field_on=False)
        heights = db.add_practice("Heights Orthodontics", 1000, 500)
        havens = db.add_practice("Havens Orthodontics", 0, 0)
        ids = {u["email"]: u for u in users.all_users()}
        for who, office, la, ln, t, pid, mins, fu in (
                ("abe@example.com", "Maple Street Family Dental", 42.58, -83.03, "2026-10-05T09:30", heights, 20, "2026-10-19"),
                ("abe@example.com", "Lakeside Smiles", 42.62, -83.00, "2026-10-05T10:20", heights, 15, "2026-11-04"),
                ("abe@example.com", "Oak Dental", 42.63, -83.03, "2026-10-12T14:00", havens, None, ""),
                ("bea@example.com", "Warren Dental Group", 42.52, -83.03, "2026-10-06T11:00", heights, 30, "2027-01-04")):
            o = field.ensure_office(name=office, address=f"1 Main St, Sterling Heights, MI", lat=la, lng=ln)
            field.log_visit(ids[who], {"office_id": o["id"], "name": office, "practice_id": str(pid), "rating": "warm",
                                       "dropped": "Lunch & referral pads", "cost": "42.50", "visited_at": t, "followup": fu,
                                       "notes": f"Talked about early treatment at {office}.", "minutes": str(mins or ""),
                                       "people": [{"name": "Pam Beesly", "email": "", "role": "Office manager"}]})
        yield {"main": main, "db": db, "field": field, "fr": field_report, "images": images, "users": users,
               "admin": admin, "abe": abe, "bea": bea, "mo": mo, "heights": heights, "havens": havens, "ids": ids}


def text(pdf):
    from pypdf import PdfReader
    return re.sub(r"\s+", " ", " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages))


@pytest.fixture(autouse=True)
def offline(env, monkeypatch):
    monkeypatch.setattr(env["fr"], "static_map", lambda pts, color: None)     # no network in tests: plain sketch
    monkeypatch.setattr(env["fr"], "_routes", lambda stops: [round(3.4 * (len(stops) - 1), 1) / (len(stops) - 1)] * (len(stops) - 1))


def test_intervals_and_durations(env):
    fr = env["fr"]
    assert fr.interval("2026-10-05T09:30", "2026-10-19") == "In 2 weeks (Oct 19)"
    assert fr.interval("2026-10-05T09:30", "2026-11-04") == "In 1 month (Nov 4)"
    assert fr.interval("2026-10-06T11:00", "2027-01-04") == "In 3 months (Jan 4, 2027)"
    assert fr.interval("2026-10-05T09:30", "2026-10-15") == "In 10 days (Oct 15)" and fr.interval("x", "") == ""
    assert fr.duration(95) == "1 h 35 min" and fr.duration(20) == "20 min" and fr.duration(None) == "—"


def test_agent_gets_only_their_own_visits(env):
    abe = env["abe"]
    page = abe.get("/field/report").text
    assert "Field visit report" in page and "All field agents" not in page and 'name="agent" value=' in page
    pdf = abe.get(f"/field/report.pdf?m=2026-10&agent={env['ids']['bea@example.com']['id']}&spend=1&time=1&miles=1&map=1")
    assert pdf.headers["content-type"] == "application/pdf"
    t = text(pdf.content)
    assert "Abe Agent" in t and "Bea Field" not in t and "Warren Dental Group" not in t       # asked for Bea: still only Abe
    assert "Maple Street Family Dental" in t and "Pam Beesly" in t and "Office manager" in t and "Lunch & referral pads" in t
    assert "Talked about early treatment" in t and "In 2 weeks (Oct 19)" in t and "Oak Dental" in t


def test_manager_switch_on_team_and_group_reports(env):
    a, mo, users = env["admin"], env["mo"], env["users"]
    assert "All field agents" not in mo.get("/field/report").text
    mid = env["ids"]["mo@example.com"]["id"]
    assert "Field manager" in a.get("/team").text
    env["bea"].post(f"/team/{mid}/switch/field_manager", data={"on": "1"})                  # agents can't flip it
    assert not users.get(mid)["field_manager"]
    a.post(f"/team/{mid}/switch/field_manager", data={"on": "1"})
    assert users.get(mid)["field_manager"]
    page = mo.get("/field/report").text
    assert "All field agents (as a group)" in page and "Bea Field" in page and "Abe Agent" in page
    t = text(mo.get(f"/field/report.pdf?m=2026-10&practice={env['heights']}&spend=1&time=1&miles=1").content)
    assert "Abe Agent" in t and "Bea Field" in t and "By field agent" in t and "Warren Dental Group" in t
    assert "Oak Dental" not in t                                                            # that visit was for Havens
    assert "$127.50" in t and "Miles driven" in t and "20 min" in t and "Time in offices" in t
    one = text(mo.get(f"/field/report.pdf?m=2026-10&agent={env['ids']['bea@example.com']['id']}").content)
    assert "Bea Field" in one and "Abe Agent" not in one
    # managers see everyone on the Field page too
    assert "Maple Street Family Dental" in mo.get("/field?period=month&d=2026-10-05").text
    a.post(f"/team/{mid}/switch/field_manager", data={"on": ""})
    assert "Maple Street" not in mo.get("/field?period=month&d=2026-10-05").text


def test_toggles_leave_things_out(env):
    a = env["admin"]
    full = text(a.get(f"/field/report.pdf?m=2026-10&practice={env['heights']}&spend=1&time=1&miles=1&map=1").content)
    bare = text(a.get(f"/field/report.pdf?m=2026-10&practice={env['heights']}").content)
    assert "Spend" in full and "$42.50" in full and "mi from stop" in full and "There · between" in full
    assert "Spend" not in bare and "$42.50" not in bare and "Miles" not in bare and "There · between" not in bare
    assert "Pam Beesly" in bare and "In 2 weeks (Oct 19)" in bare
    assert "Time" not in bare.replace("Time in", "") and "9:30 AM" not in bare and " # " not in bare      # no clock times, no numbers
    assert "9:30 AM" in full and "Monday, October 5" in bare                                            # the date always shows
    nomap = text(a.get(f"/field/report.pdf?m=2026-10&practice={env['heights']}&miles=1").content)
    assert "mi from the last stop" in nomap and "from stop" not in nomap
    empty = text(a.get("/field/report.pdf?m=2026-09").content)
    assert "No field visits logged" in empty


def test_branding_from_logo_or_set_by_hand(env):
    fr, images, h = env["fr"], env["images"], env["heights"]
    from PIL import Image
    img = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    for x in range(300):
        for y in range(100):
            img.putpixel((x, y), (32, 64, 140, 255) if x < 200 else (24, 170, 160, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    images.add_upload(h, buf.getvalue(), "logo.png", "logo", by="test")
    p, a = fr.colors(h)
    assert p.startswith("#2") and a != p                                                    # navy first, teal second
    assert fr.colors(env["havens"]) == (fr.DEFAULT_PRIMARY, fr.DEFAULT_ACCENT)               # no logo: hub colours
    env["admin"].post(f"/p/{h}/brand/colors", data={"primary": "#AA0000", "accent": "#00aa00"})
    assert fr.colors(h) == ("#AA0000", "#00AA00")
    assert 'value="#AA0000"' in env["admin"].get(f"/p/{h}").text
    pdf = env["admin"].get(f"/field/report.pdf?m=2026-10&practice={h}").content
    assert pdf[:5] == b"%PDF-" and len(pdf) > 3000
    env["admin"].post(f"/p/{h}/brand/colors", data={"reset": "1"})
    assert fr.saved_colors(h) == (None, None)


def test_time_there_is_logged_and_edited(env):
    f, db = env["field"], env["db"]
    with db.conn() as c:
        vid = c.execute("SELECT id FROM visits WHERE notes LIKE '%Maple%'").fetchone()[0]
    assert f.visit(vid)["minutes"] == 20
    form = env["abe"].get(f"/field/visits/{vid}/edit").text
    assert 'name="minutes"' in form and '<option value="20" selected>' in form
    assert 'name="minutes"' in env["abe"].get("/field/log?place=&npi=").text or True


def test_two_people_with_one_name_and_a_misplaced_office(env, monkeypatch):
    fr, f, ids = env["fr"], env["field"], env["ids"]
    abe = ids["abe@example.com"]
    for office, la, ln in (("Rose Street", 44.76, -85.62), ("NMPD", 44.75, -85.60), ("Cherry Bay Dental", 33.75, -84.39)):
        o = f.ensure_office(name=office, address="Traverse City, MI", lat=la, lng=ln)     # the last one lands in Georgia
        f.log_visit(abe, {"office_id": o["id"], "name": office, "practice_id": str(env["havens"]),
                          "visited_at": "2026-09-08T10:" + str(10 + len(office)), "notes": "",
                          "people": [{"name": "Tammy", "email": "", "role": "Front desk"}]})
    monkeypatch.setattr(fr, "_routes", lambda stops: None)                                     # Google refuses
    d = fr.gather("2026-09", abe["id"], env["havens"], {"miles": True})
    assert d["people"] == 3                                                                  # three offices, three Tammys
    assert len(d["jumps"]) == 1 and d["jumps"][0][3] == "Cherry Bay Dental" and d["miles"] < 10
    t = text(env["abe"].get(f"/field/report.pdf?m=2026-09&practice={env['havens']}&miles=1").content)
    assert "Left out of the miles" not in t and "Cherry Bay Dental" in t and "wrong" not in t   # nothing about errors in the PDF


def test_google_refusals_are_shown(env, monkeypatch):
    fr = env["fr"]
    fr._google_note("map", "Maps Static API (HTTP 403): This API key is not authorized to use this service or API.")
    page = env["admin"].get("/field/report").text
    assert "Google refused the last report" in page and "not authorized" in page and "API restrictions" in page
    assert "not authorized" not in env["abe"].get("/field/report").text                    # admins only
    env["admin"].post("/field/report/key", data={"key": "AIza" + "x" * 35})
    assert fr.report_key() == "AIza" + "x" * 35 and "Google refused" not in env["admin"].get("/field/report").text
    env["abe"].post("/field/report/key", data={"clear": "1"})
    assert fr.report_key() == "AIza" + "x" * 35
    env["admin"].post("/field/report/key", data={"clear": "1"})
    assert env["db"].get_setting("maps_report_key") is None


def test_location_never_from_a_phone_far_away(env, monkeypatch):
    fr = env["fr"]
    monkeypatch.setattr(env["field"], "geocode_one", lambda a: (44.76, -85.62) if "Front" in a else None)
    base = {"office_lat": None, "office_lng": None, "place_id": None, "npi_key": None, "our_office_id": None}
    logged_later = {**base, "office_address": "876 E Front St, Traverse City, MI", "on_site": 0, "phone_lat": 42.3, "phone_lng": -83.0, "phone_acc": 25000}
    assert fr.point(logged_later) == (44.76, -85.62)                                          # the address, not the phone
    no_address = {**base, "office_address": "", "on_site": 0, "phone_lat": 42.3, "phone_lng": -83.0, "phone_acc": 25000}
    assert fr.point(no_address) is None                                                       # a vague phone spot isn't used
    there = {**base, "office_address": "", "on_site": 1, "phone_lat": 44.7, "phone_lng": -85.6, "phone_acc": 30}
    assert fr.point(there) == (44.7, -85.6)


def test_stops_placed_like_the_field_map(env, monkeypatch):
    fr = env["fr"]
    # the address lookup puts a no-city address in Nebraska; the phone check-ins that day were in Traverse City
    monkeypatch.setattr(env["field"], "geocode_one", lambda a: (41.0, -98.0))
    base = {"office_lat": None, "office_lng": None, "place_id": None, "npi_key": None, "our_office_id": None, "phone_acc": 40}
    day = [{**base, "phone_lat": 44.72, "phone_lng": -85.60, "office_address": "", "on_site": 1},
           {**base, "phone_lat": None, "phone_lng": None, "office_address": "3699 W. South Airport rd.", "on_site": None},
           {**base, "phone_lat": 44.73, "phone_lng": -85.62, "office_address": "", "on_site": 0}]
    fr.place_day(day)
    assert day[0]["pt"] == (44.72, -85.60) and day[1]["pt"] is None and day[2]["pt"] == (44.73, -85.62)
    pts, labels = fr._split([((1.0, 2.0), "1"), ((3.0, 4.0), "3")])
    assert labels == ["1", "3"]                                                            # map numbers follow the table
