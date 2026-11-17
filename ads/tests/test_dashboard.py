"""Home dashboard: LeadSigma's Marketing Attribution export (sections: How did you hear / Referring dentist /
Source / campaign) read into counts, and the findings worked out from it."""
from datetime import date

from tests.test_scope import client  # noqa: F401  (pytest fixture)

EXPORT = """Report,Label,Account,Started views,Bookings,Confirmed,Cancelled,Share or rate
How did you hear,Google Search,,,4,,,40.0
How did you hear,Friend/Family Referral,,,3,,,30.0
How did you hear,Facebook,,,1,,,10.0
How did you hear,drive by,,,2,,,20.0
Referring dentist,BandeenWebsite (BandeenWebsite),Bandeen Orthodontics,40,6,6,0,15.0
Referring dentist,MCAD (MCAD),Heights Orthodontics,900,0,0,0,0.0
Referring dentist,Dr Example,Heights Orthodontics,0,1,1,0,0
Source / campaign,Direct / Unattributed / (none) / (none) / (none),,500,9,9,0,1.8
Source / campaign,Other / adwords / {adname} / {campaignname},,100,1,1,0,1.0
Source / campaign,Other / an / paid / Spring Promo Campaign,,700,0,0,0,0.0
Source / campaign,Meta / fb / paid / Spring Promo Campaign,,300,0,0,0,0.0
"""


def test_leadsigma_sections_and_findings(client):
    c, db, sync, mp = client
    from app import dashboard
    b = db.add_practice("Bandeen Orthodontics", 700, 0, "bandeen")
    h = db.add_practice("Heights Orthodontics", 700, 0, "heights")
    msg = dashboard.import_csv(EXPORT.encode(), "export.csv", "Tester", date(2026, 10, 19), date(2026, 11, 17))
    assert "Imported 10 bookings" in msg
    ls = dashboard.leadsigma(date(2026, 11, 9), date(2026, 11, 15), db.practices())
    assert ls["views"] == 1600 and ls["bookings"] == 10 and ls["confirmed"] == 10        # from Source / campaign only
    assert ls["direct"] == 9 and ls["unfilled"] == ["{adname}", "{campaignname}"]
    assert ls["answered"] == 10 and ls["heard"][0] == ("Google search", 4) and ls["ad_heard"] == 1
    assert ls["links"][0]["name"] == "Bandeen website" and ls["links"][0]["bookings"] == 6
    assert ls["dead"][0]["campaign"] == "Spring Promo Campaign" and ls["dead"][0]["views"] == 1000
    assert ls["dead_links"][0]["name"] == "MCAD"
    assert ls["by_practice"] == {b: 6, h: 1}
    # a second upload for the same period replaces the first rather than adding to it
    dashboard.import_csv(EXPORT.encode(), "export.csv", "Tester", date(2026, 10, 19), date(2026, 11, 17))
    assert dashboard.leadsigma(date(2026, 11, 9), date(2026, 11, 15), db.practices())["bookings"] == 10
    html = c.get("/").text
    assert "90% of online bookings have no source (9 of 10)" in html
    assert "“Spring Promo Campaign”: 1,000 visits, no bookings" in html and "Audience Network" in html
    assert "MCAD link: 900 visits, no bookings" in html
    assert "Oct 19–Nov 17" in html and "Bandeen website" in html
    assert dashboard.match_account("Orthodontics by Dr. Ken Lawrence", [{"id": 7, "name": "Lawrence Orthodontics"}]) == 7
