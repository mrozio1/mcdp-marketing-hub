"""Boosted posts and reels: assigned to the practice whose Page / Instagram they promote, even when the
campaign name doesn't match its filter or the boost was charged to another ad account; movable by hand."""
import re
import pytest
from datetime import date

from tests.test_scope import _row, client  # noqa: F401  (pytest fixture)

TODAY = date(2026, 10, 22)


def shown(html, part):
    """The campaign appears as a row of the campaign table (not just in the 'Add campaign' list)."""
    from markupsafe import escape
    return bool(re.search(r"<td>[^<]*" + re.escape(str(escape(part))), html))

ACCOUNTS = {
    # shared account: Heights + Waterford split by name; one boost of a Waterford reel, one plain ad
    "111": [_row("Heights - Invisalign", 300, 3), _row("Waterford - Leads", 200, 4),
            _row("Post: \"Orthodontic treatment starts at $4,299\"", 19.40, 0),
            _row("Brand awareness Oct", 80, 0)],
    # a stray account nobody links: a boost of a Waterford Instagram reel was charged here
    "999": [_row("Instagram post: Halloween smiles 🎃", 25, 0), _row("Someone else", 500, 1)],
}
PROMOS = {
    "111": {"Heights - Invisalign": {"fb": ["PG_HEIGHTS"], "ig": [], "ig_media": [], "boost": False},
            "Waterford - Leads": {"fb": ["PG_WATERFORD"], "ig": [], "ig_media": [], "boost": False},
            "Post: \"Orthodontic treatment starts at $4,299\"": {"fb": ["PG_WATERFORD"], "ig": [], "ig_media": [], "boost": True},
            "Brand awareness Oct": {"fb": ["PG_WATERFORD"], "ig": [], "ig_media": [], "boost": False}},
    "999": {"Instagram post: Halloween smiles 🎃": {"fb": [], "ig": [], "ig_media": ["IGMEDIA1"], "boost": True},
            "Someone else": {"fb": ["PG_OTHER"], "ig": [], "ig_media": [], "boost": False}},
}


def _setup(db, sync, monkeypatch):
    cx = sync.cx
    monkeypatch.setattr(cx, "meta_configured", lambda: True)
    monkeypatch.setattr(cx, "meta_campaign_rows", lambda acct, since, until: [dict(r) for r in ACCOUNTS[acct]])
    monkeypatch.setattr(cx, "meta_campaign_promos", lambda acct: PROMOS[acct])
    monkeypatch.setattr(sync, "sync_social", lambda practices, today=None: {})
    db.set_setting("meta_accounts", '[{"id": "111", "name": "MCDP", "active": true}, {"id": "999", "name": "Stray", "active": true}]')
    master = db.add_practice("MCDP Ads", 0, 0, "")
    heights = db.add_practice("Heights", 0, 0, "heights")
    waterford = db.add_practice("Waterford", 0, 0, "waterford")
    for pid in (master, heights, waterford):
        db.set_practice_accounts(pid, "111", "")
    db.set_practice_social(heights, ["PG_HEIGHTS"], [])
    db.set_practice_social(waterford, ["PG_WATERFORD"], ["IG_WATERFORD"])
    with db.conn() as c:   # the Instagram reel, already pulled with Waterford's organic posts
        c.execute("INSERT INTO posts(post_id, network, account_id, created, synced_at) VALUES "
                  "('IGMEDIA1', 'instagram', 'IG_WATERFORD', '2026-10-20', '2026-10-21')")
    sync.sync_all(today=TODAY)
    return master, heights, waterford


def test_boosts_go_to_the_practice_whose_post_it_is(client, monkeypatch):
    c, db, sync, mp = client
    master, heights, waterford = _setup(db, sync, monkeypatch)
    w = c.get(f"/p/{waterford}").text
    assert shown(w, "Orthodontic treatment starts at $4,299") and "Boosted" in w      # name doesn't say Waterford
    assert shown(w, "Halloween smiles")                                              # charged to another account
    assert shown(w, "Waterford - Leads") and not shown(w, "Heights - Invisalign")
    assert shown(w, "Brand awareness Oct")     # promotes Waterford's Page and no practice's words match it
    h = c.get(f"/p/{heights}").text
    assert shown(h, "Heights - Invisalign") and not shown(h, "Orthodontic treatment") and not shown(h, "Halloween")
    m = c.get(f"/p/{master}").text
    assert shown(m, "Orthodontic treatment starts at $4,299")                        # master still shows everything
    assert not shown(m, "Halloween")            # not in the master's account: no double counting


def test_move_a_campaign_and_back(client, monkeypatch):
    c, db, sync, mp = client
    master, heights, waterford = _setup(db, sync, monkeypatch)
    name = "Post: \"Orthodontic treatment starts at $4,299\""
    r = c.post(f"/p/{waterford}/campaigns/move", data={"name": name, "to": str(heights), "back": "meta"},
               follow_redirects=True)
    assert "Moved" in r.text and "Heights" in r.text
    assert shown(c.get(f"/p/{heights}").text, "Orthodontic treatment")
    assert not shown(c.get(f"/p/{waterford}").text, "Orthodontic treatment")
    assert "moved here" in c.get(f"/p/{heights}").text
    c.post(f"/p/{heights}/campaigns/move", data={"name": name, "to": "auto", "back": "meta"})
    assert shown(c.get(f"/p/{waterford}").text, "Orthodontic treatment")
    assert not shown(c.get(f"/p/{heights}").text, "Orthodontic treatment")


def test_owner_lookup_failure_keeps_the_numbers(client, monkeypatch):
    c, db, sync, mp = client

    def broken(acct):
        raise sync.cx.ConnectorError("(#100) Tried accessing nonexisting field")
    monkeypatch.setattr(sync.cx, "meta_configured", lambda: True)
    monkeypatch.setattr(sync.cx, "meta_campaign_rows", lambda acct, since, until: [dict(r) for r in ACCOUNTS[acct]])
    monkeypatch.setattr(sync.cx, "meta_campaign_promos", broken)
    monkeypatch.setattr(sync, "sync_social", lambda practices, today=None: {})
    pid = db.add_practice("Heights", 0, 0, "")
    db.set_practice_accounts(pid, "111", "")
    st = sync.sync_all(today=TODAY)
    assert st["results"][str(pid)]["meta"]["ok"]
    assert shown(c.get(f"/p/{pid}").text, "Heights - Invisalign")


def test_promo_lookup_reads_meta_fields(monkeypatch):
    from app import connectors as cx
    ads = [{"campaign": {"name": "Boost A"}, "creative": {"object_story_id": "PG1_55", "effective_object_story_id": "PG1_55"}},
           {"campaign": {"name": "Ad B"}, "creative": {"effective_object_story_id": "PG2_77", "instagram_user_id": "IG2"}},
           {"campaign": {"name": "Reel C"}, "creative": {"source_instagram_media_id": "M9", "instagram_user_id": "IG3"}}]
    calls = []

    def paged(path, params, limit_pages=50, token=None, stop=None):
        calls.append(params["fields"])
        if "instagram_user_id" in params["fields"]:
            raise cx.ConnectorError("(#100) Tried accessing nonexisting field (instagram_user_id)")
        return [{**a, "creative": {k.replace("instagram_user_id", "instagram_actor_id"): v for k, v in a["creative"].items()}}
                for a in ads]
    monkeypatch.setattr(cx, "_meta_paged", paged)
    got = cx.meta_campaign_promos("act_123")
    assert len(calls) == 2                                        # retried with the older field name
    assert got["Boost A"] == {"fb": ["PG1"], "ig": [], "ig_media": [], "boost": True}
    assert got["Ad B"]["fb"] == ["PG2"] and got["Ad B"]["ig"] == ["IG2"] and not got["Ad B"]["boost"]
    assert got["Reel C"]["boost"] and got["Reel C"]["ig_media"] == ["M9"] and got["Reel C"]["ig"] == ["IG3"]


def test_filter_only_practice_uses_the_master_accounts(client, monkeypatch):
    c, db, sync, mp = client
    master, heights, waterford = _setup(db, sync, monkeypatch)
    new = db.add_practice("Brand Office", 0, 0, "brand")          # words only, no ad account linked
    nomatch = db.add_practice("Wesley Orthodontics", 0, 900, "wesley")   # has a Meta budget
    bare = db.add_practice("Eric Groeneveld", 0, 0, "")             # nothing at all
    sync.sync_all(today=TODAY)
    assert shown(c.get(f"/p/{new}").text, "Brand awareness Oct")    # pulled through the master's account
    conn = c.get("/connections").text
    assert "(master&#39;s, by filter words)" in conn or "(master's, by filter words)" in conn
    dash = c.get("/practices").text
    assert "Filter matches no campaigns" in dash              # Wesley: words match nothing
    assert "Nothing linked yet" in dash
    assert db.practice(new)["meta_account_id"] in ("", None)        # nothing was written to the practice


def test_sync_errors_show_on_the_practices_list(client, monkeypatch):
    c, db, sync, mp = client
    master, heights, waterford = _setup(db, sync, monkeypatch)

    def gone(acct, since, until):
        raise sync.cx.ConnectorError("The customer account can't be accessed because it is not yet enabled")
    monkeypatch.setattr(sync.cx, "meta_campaign_rows", gone)
    sync.sync_all(today=TODAY)
    assert "Meta Ads sync failed" in c.get("/practices").text


def test_a_blank_practice_is_not_the_master(client, monkeypatch):
    c, db, sync, mp = client
    master, heights, waterford = _setup(db, sync, monkeypatch)
    blank = db.add_practice("Eric Groeneveld", 0, 0, "")
    assert "Master" not in c.get(f"/p/{blank}").text.split("<h1", 1)[1].split("</h1>", 1)[0]
    dash = c.get("/practices").text
    row = dash.split("Eric Groeneveld", 1)[1][:400]
    assert "tag-master" not in row


def test_inactive_own_account_stands_in_with_the_master(client, monkeypatch):
    """A practice linked to an ad account the platform reports as inactive (e.g. a Google Ads account never
    set up with billing) uses the master's account until it's activated, for syncing and new work alike."""
    c, db, sync, mp = client
    master, heights, waterford = _setup(db, sync, monkeypatch)
    db.set_setting("meta_accounts", '[{"id": "111", "name": "MCDP", "active": true}, '
                                    '{"id": "555", "name": "[MCDP] Flushing", "active": false}]')
    flushing = db.add_practice("Flushing Orthodontics", 0, 900, "brand")
    db.set_practice_accounts(flushing, "555", "")
    asked = []
    real = sync.cx.meta_campaign_rows

    def rows(acct, since, until):
        asked.append(acct)
        if acct == "555":
            raise sync.cx.ConnectorError("Google won't use this ad account (HTTP 403)")
        return real(acct, since, until)
    monkeypatch.setattr(sync.cx, "meta_campaign_rows", rows)
    st = sync.sync_all(today=TODAY)
    assert "555" not in asked and st["results"][str(flushing)]["meta"]["ok"]
    assert shown(c.get(f"/p/{flushing}").text, "Brand awareness Oct")
    from app import scope, campaigns
    assert scope.effective(db.practice(flushing))["meta_account_id"] == "111"
    conn = c.get("/connections").text
    assert "is inactive; used until it" in conn
    assert "isn't active (never set up" not in c.get(f"/p/{flushing}").text   # that note is for Google accounts
    assert db.practice(flushing)["meta_account_id"] == "555"            # their link is left as it was
    # once the platform reports it active again, the practice's own account is used
    db.set_setting("meta_accounts", '[{"id": "111", "name": "MCDP", "active": true}, '
                                    '{"id": "555", "name": "[MCDP] Flushing", "active": true}]')
    assert scope.effective(db.practice(flushing))["meta_account_id"] == "555"
    # Google: new campaigns go into the stand-in account too
    db.set_setting("google_accounts", '[{"id": "3519750682", "name": "MCDP Master Account", "active": true}, '
                                      '{"id": "3533617786", "name": "[MCDP] Chelsea", "active": false}]')
    db.set_practice_accounts(master, "111", "3519750682")
    db.set_practice_accounts(flushing, "111", "3533617786")
    assert campaigns.account_for(db.practice(flushing)) == "3519750682"
    assert "isn't active (never set up" in c.get(f"/p/{flushing}").text


def test_busy_facebook_page_is_read_in_smaller_pieces(monkeypatch):
    from app import connectors as cx
    limits = []

    def paged(path, params, limit_pages=50, token=None, stop=None):
        limits.append(params["limit"])
        if params["limit"] > 25:
            raise cx.ConnectorError("Please reduce the amount of data you're asking for, then retry your request (HTTP 500)")
        return [{"id": "1_2", "created_time": "2026-10-20T10:00:00+0000"}]
    monkeypatch.setattr(cx, "_meta_paged", paged)
    monkeypatch.setattr(cx, "RETRY_PAUSE", 0)
    rows, basic = cx._page_post_rows("1", "tok", 0)
    assert limits == [100, 25] and len(rows) == 1 and not basic


def test_meta_unknown_error_is_retried(monkeypatch):
    """'An unknown error occurred (HTTP 500)' on a busy Page: pause and try again in smaller pages."""
    from app import connectors as cx
    calls = []

    def paged(path, params, limit_pages=50, token=None, stop=None):
        calls.append((path, params["limit"]))
        if len(calls) < 3:
            raise cx.ConnectorError("An unknown error occurred (HTTP 500)",
                                    payload={"error": {"code": 1, "message": "An unknown error occurred"}})
        return [{"id": "1_2", "created_time": "2026-10-20T10:00:00+0000"}]
    monkeypatch.setattr(cx, "_meta_paged", paged)
    monkeypatch.setattr(cx, "RETRY_PAUSE", 0)
    rows, basic = cx._page_post_rows("1", "tok", 0)
    assert calls == [("1/published_posts", 100), ("1/published_posts", 25), ("1/published_posts", 10)] and len(rows) == 1
    # a real error (bad permission) still fails at once
    calls.clear()
    def denied(path, params, limit_pages=50, token=None, stop=None):
        calls.append(1)
        raise cx.ConnectorError("(#10) This endpoint requires the 'pages_read_engagement' permission (HTTP 403)",
                                payload={"error": {"code": 10}})
    monkeypatch.setattr(cx, "_meta_paged", denied)
    with pytest.raises(cx.ConnectorError):
        cx._page_post_rows("1", "tok", 0)
    assert len(calls) == 3      # one try per edge/field set, no smaller-page retries
