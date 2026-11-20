"""Studio email campaigns sent as each practice: sending addresses, lists, the wrong-practice check, the typed
confirmation, the cancel window, one message per person, tracking, unsubscribes and bounces."""
import importlib
import json
import os
import re
from datetime import datetime, timedelta

import pytest


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    for k in [k for k in os.environ if k.startswith(("META_", "GOOGLE_"))]:
        os.environ.pop(k)
    os.environ.update({"ADS_DB": str(tmp_path_factory.mktemp("db") / "ads.db"), "ADS_PASSWORD": "shared-pw",
                       "ADS_DISABLE_SCHEDULER": "1", "ADS_PUBLIC_URL": "https://ads.example.com"})
    from app import brand, db, emailer, users
    for m in (db, users, emailer):
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

    with TestClient(main.app) as boss:
        boss.post("/login", data={"password": "shared-pw"})
        admin = person(boss, "Ann Admin", "ann@example.com", "admin")
        ed = person(admin, "Ed Editor", "ed@example.com", "editor")
        vi = person(admin, "Vi Viewer", "vi@example.com", "viewer")
        havens = db.add_practice("Havens Orthodontics", 0, 0, "havens")
        heights = db.add_practice("Heights Orthodontics", 0, 0, "heights")
        brand.save(havens, {"website": "havensorthodontics.com", "never_say": "cheap", "offers": "$1,000 off"})
        brand.save(heights, {"website": "heightsortho.com"})
        db.set_setting("acs_connection", "endpoint=https://acs.example.communication.azure.com/;accesskey=" + "a2V5" * 8)
        yield {"main": main, "db": db, "emailer": emailer, "admin": admin, "ed": ed, "vi": vi, "havens": havens,
               "heights": heights, "anon": TestClient(main.app)}
    os.environ.pop("ADS_PUBLIC_URL", None)


class fake_send:
    """Swap emailer.acs_send for a recorder."""
    def __init__(self, em, out):
        self.em, self.out = em, out

    def __enter__(self):
        self.old = self.em.acs_send
        self.em.acs_send = lambda s, to, name, subj, h, t, u=None: self.out.append((s["from_addr"], to, subj, h, u)) or f"op-{len(self.out)}"
        return self.out

    def __exit__(self, *a):
        self.em.acs_send = self.old


def _ready(env, pid, addr):
    em = env["emailer"]
    env["admin"].post(f"/connections/email/sender/{pid}", data={"from_addr": addr, "from_name": "X", "reply_to": "front@" + addr.split("@")[1],
                                                                "address": "1 Main St, Clawson MI", "phone": "(248) 555-0100", "daily_cap": "300"})
    em.check_dns(pid, lookup=lambda name, t: {"TXT": ["v=spf1 include:spf.protection.outlook.com -all"] if not name.startswith("_dmarc")
                                               else ["v=DMARC1; p=none"], "CNAME": ["x.azurecomm.net"]}[t])
    with fake_send(em, []):
        em.sender_test(pid, "ann@example.com", "Test")
    assert em.sender(pid)["ready"]


def test_sender_must_be_on_practice_domain(env):
    r = env["admin"].post(f"/connections/email/sender/{env['havens']}", data={"from_addr": "news@heightsortho.com", "address": "x"},
                          follow_redirects=True)
    assert "own domain" in r.text
    assert env["emailer"].sender(env["havens"]) is None
    assert env["ed"].get("/connections/email", follow_redirects=False).status_code in (303, 403)    # admins only


def test_full_flow(env):
    em, ed, anon, db = env["emailer"], env["ed"], env["anon"], env["db"]
    _ready(env, env["havens"], "news@havensorthodontics.com")
    _ready(env, env["heights"], "hello@heightsortho.com")
    assert "Email campaigns" in ed.get("/studio").text
    r = ed.post("/studio/email/lists/new", data={"practice_id": env["havens"], "name": "Referring dentists"}, follow_redirects=True)
    lid = int(re.search(r"/studio/email/lists/(\d+)/import", r.text).group(1))
    csv = b"Email,First name,Company\nsue@dentist.com,Sue,Smile Dental\nbob@dentist.com,Bob,Bright Dental\nnot-an-email,X,Y\nsue@dentist.com,Sue,dupe\n"
    r = ed.post(f"/studio/email/lists/{lid}/import", files={"file": ("list.csv", csv, "text/csv")}, follow_redirects=True)
    assert "Added 2" in r.text
    hl = em.new_list(env["heights"], "Heights list", "x")
    em.add_contacts(hl, [{"email": "h@x.com"}])

    r = ed.post("/studio/email/new", data={"practice_id": env["havens"], "name": "December", "list_id": [str(lid), str(hl)],
                                           "start": "referral"}, follow_redirects=False)
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    k = em.campaign(cid)
    assert k["practice_id"] == env["havens"] and k["list_ids"] == [lid]       # another practice's list never sticks
    assert "Sending as <strong>Havens Orthodontics</strong>" in ed.get(f"/studio/email/{cid}").text

    blocks = [{"type": "heading", "text": "Hello {first_name}"},
              {"type": "text", "text": "We work with Heights Orthodontics too. Cheap! $500 off"},
              {"type": "button", "label": "Refer", "url": "https://heightsortho.com/refer"}]
    r = ed.post(f"/studio/email/{cid}", data={"name": "December", "subject": "News", "preview": "p", "list_id": str(lid),
                                              "blocks": json.dumps(blocks)}, headers={"x-requested-with": "fetch"})
    assert r.json()["ok"]
    p = db.practice(env["havens"])
    text = " ".join(c["detail"] for c in em.checks(em.campaign(cid), p) if not c["ok"])
    assert "Heights Orthodontics" in text and "heightsortho.com" in text and "cheap" in text and "$500" in text
    ed.post(f"/studio/email/{cid}/send", data={"typed": "Havens"})
    assert em.campaign(cid)["status"] == "draft"                                  # blocked

    blocks = [{"type": "heading", "text": "Hello {first_name}"}, {"type": "text", "text": "Thanks for the referrals. $1,000 off"},
              {"type": "button", "label": "Refer", "url": "https://havensorthodontics.com/refer"}]
    ed.post(f"/studio/email/{cid}", data={"name": "December", "subject": "News", "list_id": str(lid), "blocks": json.dumps(blocks)},
            headers={"x-requested-with": "fetch"})
    assert all(c["ok"] for c in em.checks(em.campaign(cid), p)), em.checks(em.campaign(cid), p)
    assert "Type <strong>Havens</strong>" in ed.get(f"/studio/email/{cid}/review").text
    ed.post(f"/studio/email/{cid}/send", data={"typed": "Heights"})
    assert em.campaign(cid)["status"] == "draft"                                  # wrong name typed
    ed.post(f"/studio/email/{cid}/send", data={"typed": "havens"})
    k = em.campaign(cid)
    assert k["status"] == "queued" and k["send_at"] > datetime.now().isoformat()  # waits the cancel window

    sent = []
    with fake_send(em, sent):
        assert em.work(send=em.acs_send) == 0                                     # not due yet
        assert em.work(send=em.acs_send, now=datetime.now() + timedelta(minutes=11)) == 2
    assert em.campaign(cid)["status"] == "sent"
    assert {x[1] for x in sent} == {"sue@dentist.com", "bob@dentist.com"}        # one message each, never together
    assert all(x[0] == "news@havensorthodontics.com" for x in sent)
    html = next(x[3] for x in sent if x[1] == "sue@dentist.com")
    assert "Hello Sue" in html and "1 Main St, Clawson MI" in html and "/e/u/" in html and "havensorthodontics.com/refer" not in html
    tok = re.search(r"/e/c/([\w-]+)/0", html).group(1)
    r = anon.get(f"/e/c/{tok}/0", follow_redirects=False)
    assert r.headers["location"].startswith("https://havensorthodontics.com/refer?utm_source=email")
    assert anon.get(f"/e/c/{tok}/9", follow_redirects=False).headers["location"] == "/"   # no open redirect
    anon.get(f"/e/o/{tok}.gif")
    st = em.stats(cid)
    assert st["sent"] == 2 and st["opened"] == 1 and st["clicked"] == 1
    assert "What people clicked" in ed.get(f"/studio/email/{cid}").text

    unsub = next(x[4] for x in sent if x[1] == "bob@dentist.com")
    utok = unsub.rsplit("/", 1)[1]
    assert "Unsubscribe from Havens Orthodontics?" in anon.get(f"/e/u/{utok}").text
    assert "unsubscribed" in anon.post(f"/e/u/{utok}").text
    people, skipped = em.recipients(em.campaign(em.copy_campaign(cid, "x")))
    assert [x["email"] for x in people] == ["sue@dentist.com"] and skipped == 1

    env["admin"].get("/connections/email")
    key = db.get_setting("em_events_key")
    v = anon.post(f"/e/events?k={key}", json=[{"eventType": "Microsoft.EventGrid.SubscriptionValidationEvent", "data": {"validationCode": "abc"}}])
    assert v.json() == {"validationResponse": "abc"}
    assert anon.post("/e/events?k=wrong", json=[]).status_code == 403
    sue_op = next(i + 1 for i, x in enumerate(sent) if x[1] == "sue@dentist.com")
    anon.post(f"/e/events?k={key}", json=[{"eventType": "Microsoft.Communication.EmailDeliveryReportReceived",
                                           "data": {"messageId": f"op-{sue_op}", "status": "Bounced"}}])
    assert em.stats(cid)["bounced"] == 1


def test_cancel_and_access(env):
    em, ed = env["emailer"], env["ed"]
    lid = em.new_list(env["heights"], "Cancel test", "x")
    em.add_contacts(lid, [{"email": "c@x.com"}])
    cid = em.new_campaign(env["heights"], "C", [lid], "announcement", "x")
    with env["db"].conn() as c:
        c.execute("UPDATE em_campaigns SET subject='S', blocks=? WHERE id=?", (json.dumps([{"type": "text", "text": "Hi"}]), cid))
    em.queue(cid, env["db"].practice(env["heights"]), "Heights", "x", "https://ads.example.com")
    assert "Cancel sending" in ed.get(f"/studio/email/{cid}").text
    ed.post(f"/studio/email/{cid}/cancel")
    assert em.campaign(cid)["status"] == "draft"
    assert env["vi"].get("/studio/email", follow_redirects=False).status_code == 303       # viewers can't send


def test_websites_sync_with_search_page(env):
    from app import brand, visibility
    db = env["db"]
    pid = db.add_practice("Wheeler Orthodontics", 0, 0, "wheeler")
    other = db.add_practice("Schulz Orthodontics", 0, 0, "schulz")
    brand.save(other, {"website": "https://schulzortho.com", "voice": "warm"})
    visibility.sites()
    with db.conn() as c:
        c.execute("INSERT INTO sc_sites(site, practice_id) VALUES ('sc-domain:wheelerorthodontics.com', ?)", (pid,))
        c.execute("INSERT INTO sc_sites(site, practice_id) VALUES ('https://www.schulzortho.com/', NULL)")
    brand.sync_websites()
    assert brand.kit(pid)["website"] == "https://wheelerorthodontics.com"            # Search page → brand kit
    assert env["emailer"].practice_domain(pid) == "wheelerorthodontics.com"
    assert {s["site"]: s["practice_id"] for s in visibility.sites()}["https://www.schulzortho.com/"] == other   # kit → Search page
    assert brand.kit(other)["voice"] == "warm"                                     # nothing else touched
