from datetime import date
from pathlib import Path

import pytest

from app.analyze import analyze_campaigns, analyze_search_terms, pacing
from app.parse import ParseError, parse_export

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return parse_export((FIX / name).read_bytes(), name)


def test_google_campaigns_parsed_and_totals_skipped():
    p = load("google_campaigns.csv")
    assert p["platform"] == "google" and p["level"] == "campaign"
    assert len(p["rows"]) == 5 and p["skipped_total_rows"] == 2
    inv = p["rows"][0]
    assert inv["campaign"] == "Invisalign - Search"
    assert inv["spend"] == 1413.72 and inv["impressions"] == 12480 and inv["conversions"] == 39.0
    assert inv["clicks"] == 612            # Google calls clicks "Interactions" in this report
    assert p["date_start"] == date(2026, 9, 1) and p["date_end"] == date(2026, 9, 30)


def test_google_campaign_findings():
    p = load("google_campaigns.csv")
    camps, tot, findings = analyze_campaigns(p["rows"])
    assert round(tot["spend"], 2) == 3261.24 and tot["conversions"] == 105
    kinds = [(f["kind"], f.get("campaign")) for f in findings]
    assert ("zero-conv-campaign", "PMax - Smile Promo") in kinds
    assert ("high-cpa", "Display Retargeting") in kinds          # $243 vs ~$31 average
    assert all(f.get("campaign") != "Brand - Heights Ortho" for f in findings)
    assert findings[0]["severity"] == "high"


def test_search_terms():
    p = load("google_search_terms.csv")
    assert p["level"] == "search_term" and len(p["rows"]) == 6
    st = analyze_search_terms(p["rows"])
    names = [t["name"] for t in st["wasted_terms"]]
    assert names[0] == "free invisalign"                          # biggest waste first
    assert set(names) == {"free invisalign", "how to straighten teeth at home", "invisalign jobs", "braces colors"}
    assert st["wasted_total"] == round(155.40 + 112.20 + 60.45 + 30.80, 2)
    # equal conversions -> the cheaper term ranks first
    assert [t["name"] for t in st["winning_terms"]][:2] == ["orthodontist heights", "invisalign cost near me"]


def test_meta_export():
    p = load("meta_campaigns.csv")
    assert p["platform"] == "meta" and p["level"] == "campaign"
    r = {x["campaign"]: x for x in p["rows"]}
    assert r["Heights - Free Consult Leads"]["spend"] == 404.80
    assert r["Heights - Free Consult Leads"]["conversions"] == 22
    assert r["Heights - Smile Gallery Video"]["clicks"] == 401
    camps, tot, findings = analyze_campaigns(p["rows"])
    assert any(f["kind"] == "zero-conv-campaign" and f["campaign"] == "Heights - Smile Gallery Video" for f in findings)


def test_pacing_daily_rows():
    p = load("google_daily.csv")
    pc = pacing(p["rows"], monthly_budget=1500)
    assert pc["available"] and pc["days_elapsed"] == 12 and pc["days_in_month"] == 31
    assert pc["spend"] == 572.0
    assert pc["expected"] == round(1500 * 12 / 31, 2)
    assert pc["status"] == "on-track"
    pc2 = pacing(p["rows"], monthly_budget=1000)
    assert pc2["status"] == "over"


def test_pacing_full_month_range():
    p = load("google_campaigns.csv")
    pc = pacing(p["rows"], monthly_budget=4000)
    assert pc["available"] and pc["days_elapsed"] == 30 and pc["status"] == "under"


def test_no_conversions_flags_tracking_first():
    rows = [dict(r, conversions=0) for r in load("google_campaigns.csv")["rows"]]
    _, _, findings = analyze_campaigns(rows)
    assert findings[0]["kind"] == "tracking"
    assert not any(f["kind"] == "zero-conv-campaign" for f in findings)


def test_garbage_rejected():
    with pytest.raises(ParseError):
        parse_export(b"hello,world\n1,2\n", "x.csv")


def test_tab_separated_utf16_google_export():
    raw = (FIX / "google_campaigns.csv").read_text().replace(",", "\t")
    # commas inside quoted numbers become tabs above; rebuild a clean TSV instead
    tsv = "Campaign\tCost\tImpr.\tClicks\tConversions\nInvisalign\t$1,200.50\t9,000\t400\t20\n"
    p = parse_export(tsv.encode("utf-16"), "x.csv")
    assert p["rows"][0]["spend"] == 1200.5 and p["rows"][0]["impressions"] == 9000
