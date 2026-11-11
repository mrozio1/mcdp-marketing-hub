"""Studio phase 4: video projects, the edit's rules, rendering with ffmpeg, captions from speech."""
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg isn't installed here")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    tmp = tmp_path_factory.mktemp("db")
    os.environ.update({"ADS_DB": str(tmp / "ads.db"), "ADS_PASSWORD": "shared-pw", "ADS_DISABLE_SCHEDULER": "1"})
    from app import db, users, images, studio, studio_video
    for m in (db, users, images, studio, studio_video):
        importlib.reload(m)
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient

    def person(admin, name, email, role):
        r = admin.post("/team/add", data={"name": name, "email": email, "role": role, "social_on": "1"})
        tok = re.search(r"/setup/([\w-]+)", r.text).group(1)
        c = TestClient(main.app)
        c.post(f"/setup/{tok}", data={"password": "long-password-1", "confirm": "long-password-1"})
        return c

    def media(name, args):
        p = str(tmp / name)
        subprocess.run(["ffmpeg", "-y", "-v", "error", *args, p], check=True)
        return p

    with TestClient(main.app) as boss:
        boss.post("/login", data={"password": "shared-pw"})
        ed = person(boss, "Amber Fonnegra", "amber@example.com", "editor")
        pid = db.add_practice("Schulz Orthodontics", 0, 0)
        clip = media("talk.mp4", ["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=4", "-f", "lavfi",
                                  "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"])
        silent = media("broll.mp4", ["-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=24:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p"])
        song = media("song.mp3", ["-f", "lavfi", "-i", "sine=frequency=220:duration=2"])
        from PIL import Image
        Image.new("RGB", (1200, 900), (200, 100, 60)).save(tmp / "photo.png")
        ids = {}
        for key, path, mime in (("clip", clip, "video/mp4"), ("silent", silent, "video/mp4"), ("song", song, "audio/mpeg"),
                                ("photo", str(tmp / "photo.png"), "image/png")):
            cp = str(tmp / ("copy_" + os.path.basename(path)))
            shutil.copyfile(path, cp)
            ids[key] = studio.add_file(cp, os.path.basename(path), pid, None, by="Amber", mime=mime)
        yield {"main": main, "db": db, "studio": studio, "v": studio_video, "ed": ed, "pid": pid, "ids": ids}


def test_create_and_open(env):
    ed, v, studio, pid, ids = env["ed"], env["v"], env["studio"], env["pid"], env["ids"]
    assert "Reel or Story" in ed.get(f"/studio/new?b={pid}").text
    r = ed.post("/studio/videos", data={"pid": str(pid), "vsize": "story", "title": "Debond day reel"}, follow_redirects=False)
    iid = int(re.search(r"/studio/video/(\d+)", r.headers["location"]).group(1))
    it = studio.item(iid)
    assert it["kind"] == "video" and v.is_project(it) and (it["width"], it["height"]) == (1080, 1920)
    assert v.probe(studio.local_path(it))["duration"] == pytest.approx(1, abs=0.2)      # a placeholder until it's made
    page = ed.get(f"/studio/video/{iid}").text
    assert "studio-video.js" in page and "Export" in page and "Debond day reel" in page
    assert "Edit the video" in ed.get(f"/studio/i/{iid}").text
    j = ed.get(f"/studio/video/{iid}/project").json()
    assert j["project"]["main"] == [] and j["project"]["outro"]["on"] is True
    m = ed.get(f"/studio/video-media?pid={pid}").json()
    assert {x["id"] for x in m["videos"]} == {ids["clip"], ids["silent"]} and m["sounds"][0]["id"] == ids["song"]
    assert m["videos"][0]["src"].startswith("/studio/media/")
    assert iid not in {x["id"] for x in m["videos"]}                                    # projects aren't clips
    info = ed.get(f"/studio/video-info/{ids['clip']}").json()
    assert info["duration"] == pytest.approx(4, abs=0.1) and info["audio"] and info["w"] == 640
    env["iid"] = iid


def test_edit_rules(env):
    ed, v, ids, iid = env["ed"], env["v"], env["ids"], env["iid"]
    proj = {"main": [{"id": "a", "item": ids["clip"], "in": 0.5, "out": 3.5, "speed": 1.4, "volume": 3,
                      "filter": {"preset": "warm", "brightness": 5}, "trans": {"type": "fade", "dur": 0.5}},
                     {"id": "b", "item": ids["photo"], "out": 2, "in": 9, "speed": 2, "trans": {"type": "slideleft", "dur": 9},
                      "motion": "zoomin", "crop": {"l": 0.8, "r": 0.5}},
                     {"item": 999999, "in": 0, "out": 2}],
            "layers": [{"id": "t1", "kind": "text", "text": "Free consults", "start": 1, "dur": 50, "x": 100, "y": 200,
                        "w": 600, "h": 120, "anim": {"in": "pop", "out": "nope"}, "font": "Comic Sans"},
                       {"id": "x", "kind": "text", "text": "   "}, {"kind": "image", "item": ids["photo"], "start": 99}],
            "captions": [{"start": 0.2, "end": 1.5, "text": "  Braces   off! "}],
            "outro": {"on": True, "dur": 20, "text": "Book a free consult"}, "music": {"item": ids["song"], "volume": 0.3},
            "style": {"captions": "plain", "position": "weird"}, "w": 99, "h": 99}
    r = ed.post(f"/studio/video/{iid}/project", json={"project": proj})
    assert r.status_code == 200, r.text
    p = r.json()["project"]
    assert (p["w"], p["h"], p["v"]) == (1080, 1920, 2)
    a, b = p["main"]
    assert a["speed"] == 1.5 and a["volume"] == 2.0 and a["filter"]["preset"] == "warm" and a["filter"]["brightness"] == 1
    assert a["trans"]["type"] == "none"                                   # nothing before the first clip
    assert b["still"] and b["out"] == 2.0 and b["motion"] == "zoomin" and b["trans"] == {"type": "slideleft", "dur": 2.0}
    assert b["crop"]["l"] == 0.8 and b["crop"]["l"] + b["crop"]["r"] <= 0.9
    lay = v.video2.layout(p)
    # clip a: 2 s; photo b: 2 s overlapping by 1 s (half the shorter); outro 8 s with a 0.4 s fade
    assert [s_["start"] for s_ in lay["segs"]] == [0, 1.0, 2.6] and lay["end"] == 10.6
    assert len(p["layers"]) == 1 and p["layers"][0]["font"] == "DejaVu Sans" and p["layers"][0]["anim"] == {"in": "pop", "out": "none"}
    assert p["layers"][0]["dur"] == pytest.approx(9.6)                    # can't run past the end
    assert r.json()["total"] == pytest.approx(10.6)
    long = {"main": [{"item": ids["photo"], "out": 30}] * 7}
    assert "under 3 minutes" in ed.post(f"/studio/video/{iid}/project", json={"project": long}).json()["error"]


def test_version1_projects_open(env):
    v, ids = env["v"], env["ids"]
    old = {"type": "video", "w": 1080, "h": 1920, "clips": [{"item": ids["clip"], "in": 0, "out": 2, "speed": 1, "volume": 1}],
           "captions": [], "lowers": [], "outro": {"on": False, "dur": 2, "text": ""}, "music": None, "style": {}}
    p = v.video2.clean(old, 1080, 1920)
    assert len(p["main"]) == 1 and p["main"][0]["item"] == ids["clip"] and p["main"][0]["fit"] == "fill"


def test_grade_matches_presets(env):
    g = env["v"].video2.grade({"preset": "none"})
    assert g["neutral"] and env["v"].video2.grade_filter({}) == ""
    bw = env["v"].video2.grade({"preset": "bw"})
    row = bw["matrix"][0]
    assert bw["matrix"][0] == bw["matrix"][1] == bw["matrix"][2] and abs(sum(row) - 1) < 1e-3
    f = env["v"].video2.grade_filter({"preset": "film"})
    assert "colorchannelmixer" in f and "lutrgb" in f and "vignette" in f


def test_render(env):
    v, studio, ids, iid = env["v"], env["studio"], env["ids"], env["iid"]
    v.save_project(iid, {
        "main": [{"item": ids["clip"], "in": 0.5, "out": 3.5, "speed": 1.5, "volume": 1, "filter": {"preset": "vivid"}},
                 {"item": ids["silent"], "in": 0, "out": 2, "speed": 1, "volume": 1, "fit": "fit", "trans": {"type": "wipeleft", "dur": 0.5},
                  "bg": {"mode": "color", "color": "#224466"}},
                 {"item": ids["photo"], "out": 1.5, "motion": "zoomin", "crop": {"l": 0.1, "t": 0.1}, "trans": {"type": "none"}}],
        "layers": [{"kind": "text", "text": "Free consults\nall November", "start": 0.5, "dur": 3, "x": 140, "y": 300,
                    "w": 800, "h": 260, "size": 80, "fill": "#0F7471", "anim": {"in": "pop", "out": "fade"}, "rot": -4},
                   {"kind": "video", "item": ids["silent"], "in": 0, "out": 2, "speed": 1, "volume": 0, "start": 1, "track": 2,
                    "x": 600, "y": 1300, "w": 360, "h": 640, "anim": {"in": "slideleft"}, "opacity": 0.9, "filter": {"preset": "bw"}},
                   {"kind": "image", "item": ids["photo"], "dur": 2, "start": 3, "x": 60, "y": 60, "w": 300, "h": 225,
                    "crop": {"r": 0.2}, "anim": {"in": "slideup"}}],
        "captions": [{"start": 0.2, "end": 1.5, "text": "Braces off, smiles on"}],
        "lowers": [{"start": 0.5, "end": 2.5, "text": "Dr. Schulz", "sub": "Orthodontist"}],
        "outro": {"on": True, "dur": 2, "text": "Book a visit", "trans": {"type": "fadeblack", "dur": 0.4}},
        "music": {"item": ids["song"], "volume": 0.3}, "style": {"captions": "box", "position": "low"}})
    expect = v.video2.layout(v.project(iid))["end"]
    v._render(iid, "Amber", {})
    st = v.status(iid)
    assert st["state"] == "done", st
    it = studio.item(iid)
    pr = v.probe(studio.local_path(it))
    assert (pr["w"], pr["h"]) == (1080, 1920) and pr["audio"]
    assert pr["duration"] == pytest.approx(expect, abs=0.2) and expect == pytest.approx(2 + 2 - 0.5 + 1.5 + 2 - 0.4, abs=0.01)
    assert it["has_thumb"] and any(e["kind"] == "rendered" for e in studio.events(iid))
    env["rendered"] = studio.local_path(it)
    # the end card is a picture in the brand's colours
    png = v.outro_png(env["pid"], 540, 960, "Book a visit")
    from PIL import Image
    assert Image.open(io.BytesIO(png)).size == (540, 960)
    assert env["ed"].get(f"/studio/video/{iid}/outro.png?text=Hi").headers["content-type"] == "image/png"


def test_render_through_the_page_with_text_pictures(env):
    ed, v, iid = env["ed"], env["v"], env["iid"]
    from PIL import Image
    p = v.project(iid)
    tid = p["layers"][0]["id"]
    buf = io.BytesIO()
    Image.new("RGBA", (800, 260), (255, 0, 0, 200)).save(buf, "PNG")
    r = ed.post(f"/studio/video/{iid}/render", files={f"text_{tid}": ("t.png", buf.getvalue(), "image/png")})
    assert r.json()["started"] is True
    for _ in range(600):
        s = ed.get(f"/studio/video/{iid}/render").json()
        if s.get("state") in ("done", "error"):
            break
        time.sleep(0.1)
    assert s["state"] == "done", s


def test_ass_and_words(env):
    v = env["v"]
    p = {"w": 1080, "h": 1920, "captions": [{"start": 1.5, "end": 3.25, "text": "Hello {world}"}],
         "lowers": [{"start": 0, "end": 2, "text": "Dr. A", "sub": "Orthodontist"}], "style": {"captions": "box", "position": "low"}}
    a = v.ass_file(p, None)
    assert "Dialogue: 0,0:00:01.50,0:00:03.25,Cap,,0,0,0,,Hello (world)" in a and "PlayResY: 1920" in a
    assert ",Name,," in a and ",Role,," in a
    g = v.group_words([{"word": "Braces", "start": 0, "end": 0.4}, {"word": "off.", "start": 0.4, "end": 0.8},
                       {"word": "Smiles", "start": 2.0, "end": 2.4}, {"word": "on", "start": 2.4, "end": 2.6}])
    assert g == [{"start": 0.0, "end": 0.8, "text": "Braces off."}, {"start": 2.0, "end": 2.6, "text": "Smiles on"}]


def test_captions_from_speech(env, monkeypatch):
    ed, v, ids, iid = env["ed"], env["v"], env["ids"], env["iid"]
    v.save_project(iid, {"main": [{"item": ids["photo"], "out": 2},
                                  {"item": ids["clip"], "in": 1, "out": 3, "speed": 2, "volume": 1}],
                         "outro": {"on": False, "dur": 2, "text": ""}})
    sent = []

    def fake(audio):
        sent.append(len(audio))
        return {"text": "Hi there friends", "words": [{"word": "Hi", "start": 0, "end": 0.5},
                                                      {"word": "there", "start": 0.5, "end": 1.0},
                                                      {"word": "friends", "start": 1.0, "end": 1.6}]}
    monkeypatch.setattr(v, "_whisper", fake)
    caps = ed.post(f"/studio/video/{iid}/captions").json()["captions"]
    assert len(sent) == 1 and sent[0] > 0                         # only the clip with sound
    assert caps == [{"start": 2.0, "end": 2.8, "text": "Hi there friends"}]   # after the 2 s photo, at 2× speed
    monkeypatch.setattr(v, "_cf", lambda: ("", ""))
    monkeypatch.undo()
    monkeypatch.setattr(v, "_cf", lambda: ("", ""))
    r = ed.post(f"/studio/video/{iid}/captions")
    assert r.status_code == 422 and "Cloudflare" in r.json()["error"]


def test_ratio_change_keeps_layers_in_place(env):
    v, ed, ids = env["v"], env["ed"], env["ids"]
    iid = v.create_project(env["pid"], None, "story", "Ratio", "Amber", [ids["clip"]])
    p = v.project(iid)
    p["layers"] = [{"id": "t1", "kind": "text", "track": 1, "start": 0, "dur": 2, "text": "Hi", "x": 140, "y": 860, "w": 800, "h": 200,
                    "size": 80, "rot": 0, "opacity": 1}]
    v.save_project(iid, p)
    r = ed.post(f"/studio/video/{iid}/ratio", json={"size": "square"})
    assert r.status_code == 200, r.text
    q = v.project(iid)
    assert (q["w"], q["h"]) == (1080, 1080)
    L = q["layers"][0]
    assert abs(L["x"] + L["w"] / 2 - 540) < 2 and abs(L["y"] + L["h"] / 2 - 540) < 2
    assert L["w"] < 800 and L["size"] < 80
    assert env["studio"].item(iid)["height"] == 1080
    assert ed.post(f"/studio/video/{iid}/ratio", json={"size": "huge"}).status_code == 422
