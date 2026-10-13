"""
Deterministic ad-account analysis. Every number here is calculated, never guessed;
the AI layer only explains and suggests based on these results.

Following the claude-ads rules: findings compare against the practice's own
account averages (not universal industry thresholds), measurement problems come
first, and negative-keyword candidates come only from a real search terms report.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date


def _safe_div(a, b):
    return a / b if b else None


def _agg(rows, key):
    groups = defaultdict(lambda: {"spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0, "conv_value": 0.0})
    for r in rows:
        g = groups[r[key] or "(blank)"]
        for m in ("spend", "impressions", "clicks", "conversions", "conv_value"):
            g[m] += r[m]
    out = []
    for name, g in groups.items():
        out.append({
            "name": name,
            "spend": round(g["spend"], 2),
            "impressions": g["impressions"],
            "clicks": g["clicks"],
            "conversions": round(g["conversions"], 2),
            "conv_value": round(g["conv_value"], 2),
            "ctr": _safe_div(g["clicks"], g["impressions"]),
            "cpc": _safe_div(g["spend"], g["clicks"]),
            "cpa": _safe_div(g["spend"], g["conversions"]),
            "conv_rate": _safe_div(g["conversions"], g["clicks"]),
        })
    return sorted(out, key=lambda x: -x["spend"])


def totals(rows):
    t = _agg([dict(r, _all="all") for r in rows], "_all")
    return t[0] if t else {"spend": 0, "impressions": 0, "clicks": 0, "conversions": 0, "conv_value": 0,
                           "ctr": None, "cpc": None, "cpa": None, "conv_rate": None}


def money(v):
    return "—" if v is None else f"${v:,.2f}"


def pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def analyze_campaigns(rows):
    """Findings from a campaign-level export. Returns (campaigns, totals, findings)."""
    camps = _agg(rows, "campaign")
    tot = totals(rows)
    findings = []
    spend = tot["spend"]
    if spend <= 0:
        return camps, tot, [{
            "severity": "info", "kind": "no-spend", "title": "No spend in this export",
            "detail": "The export shows no cost for the period, so there is nothing to evaluate yet.",
            "amount": 0}]

    # 1. Measurement first: no conversions recorded at all.
    if tot["conversions"] == 0:
        findings.append({
            "severity": "high", "kind": "tracking",
            "title": "No conversions recorded at all",
            "detail": (f"{money(spend)} was spent with zero recorded conversions across every campaign. "
                       "Before judging ad performance, confirm conversion tracking (calls, forms, bookings) "
                       "is set up and firing; otherwise every other result is unreliable."),
            "amount": spend})
    else:
        # 2. Campaigns spending meaningfully with zero conversions.
        threshold = max(50.0, 0.05 * spend)
        for c in camps:
            if c["conversions"] == 0 and c["spend"] >= threshold:
                findings.append({
                    "severity": "high" if c["spend"] >= 0.15 * spend else "medium",
                    "kind": "zero-conv-campaign", "campaign": c["name"],
                    "title": f"“{c['name']}” spent {money(c['spend'])} with no conversions",
                    "detail": (f"{pct(c['spend'] / spend)} of this period's spend, {c['clicks']:,} clicks, "
                               f"0 conversions. Check its targeting, landing page and conversion setup, or "
                               f"move budget to campaigns that convert."),
                    "amount": c["spend"]})
        # 3. Cost per conversion far above the practice's own average.
        acct_cpa = tot["cpa"]
        for c in camps:
            if c["conversions"] > 0 and acct_cpa and c["cpa"] >= 2 * acct_cpa and c["spend"] >= 0.05 * spend:
                extra = c["spend"] - c["conversions"] * acct_cpa
                findings.append({
                    "severity": "medium", "kind": "high-cpa", "campaign": c["name"],
                    "title": f"“{c['name']}” costs {c['cpa'] / acct_cpa:.1f}× the practice average per conversion",
                    "detail": (f"{money(c['cpa'])} per conversion vs. {money(acct_cpa)} account average "
                               f"({c['conversions']:g} conversion{'' if c['conversions'] == 1 else 's'} on {money(c['spend'])}). At the average rate "
                               f"those conversions would have cost about {money(extra)} less."),
                    "amount": round(extra, 2)})

    # 4. Click-through rate far below the practice's own average (enough data only).
    acct_ctr = tot["ctr"]
    for c in camps:
        if acct_ctr and c["impressions"] >= 1000 and c["ctr"] is not None and c["ctr"] < 0.5 * acct_ctr:
            findings.append({
                "severity": "low", "kind": "low-ctr", "campaign": c["name"],
                "title": f"“{c['name']}” gets clicked far less than your other campaigns",
                "detail": (f"Click-through rate {pct(c['ctr'])} vs. {pct(acct_ctr)} account average on "
                           f"{c['impressions']:,} impressions. The ads or audience may not match what people want."),
                "amount": 0})

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: (order[f["severity"]], -f.get("amount", 0)))
    return camps, tot, findings


def analyze_search_terms(rows, limit=25):
    """Search terms with spend and no conversions (candidates to review) and converting terms."""
    terms = _agg(rows, "name")
    wasted = [t for t in terms if t["conversions"] == 0 and t["spend"] > 0]
    winners = sorted([t for t in terms if t["conversions"] > 0], key=lambda t: (-t["conversions"], t["spend"]))
    return {
        "wasted_terms": wasted[:limit],
        "wasted_total": round(sum(t["spend"] for t in wasted), 2),
        "wasted_count": len(wasted),
        "winning_terms": winners[:limit],
        "term_count": len(terms),
        "total_spend": round(sum(t["spend"] for t in terms), 2),
    }


def pacing(rows, monthly_budget, today=None):
    """Month-to-date spend vs. budget for the most recent month in the data."""
    today = today or date.today()
    dated = [r for r in rows if r.get("date")]
    if dated:
        last = max(r["date"] for r in dated)
        month_rows = [r for r in dated if (r["date"].year, r["date"].month) == (last.year, last.month)]
        through = last
        basis = "daily rows"
    else:
        ends = [r["date_end"] for r in rows if r.get("date_end")]
        starts = [r["date_start"] for r in rows if r.get("date_start")]
        through = max(ends) if ends else today
        start = min(starts) if starts else through.replace(day=1)
        if (start.year, start.month) != (through.year, through.month):
            return {"available": False,
                    "reason": "This export covers more than one month without daily rows. "
                              "Export with a 'Day' column (or one month at a time) for pacing."}
        month_rows = rows
        basis = "export date range"
    spend = round(sum(r["spend"] for r in month_rows), 2)
    days_in_month = calendar.monthrange(through.year, through.month)[1]
    elapsed = through.day
    result = {
        "available": True, "month": through.strftime("%B %Y"), "through": through,
        "days_elapsed": elapsed, "days_in_month": days_in_month, "spend": spend, "basis": basis,
        "budget": monthly_budget or None,
    }
    projected = round(spend / elapsed * days_in_month, 2) if elapsed else spend
    result["projected"] = projected
    if monthly_budget:
        expected = round(monthly_budget * elapsed / days_in_month, 2)
        ratio = spend / expected if expected else None
        result.update({
            "expected": expected,
            "ratio": ratio,
            "remaining": round(monthly_budget - spend, 2),
            "daily_to_hit": round((monthly_budget - spend) / (days_in_month - elapsed), 2)
            if days_in_month > elapsed else 0.0,
        })
        if ratio is None:
            result["status"] = "unknown"
        elif ratio > 1.10:
            result["status"] = "over"
        elif ratio < 0.90:
            result["status"] = "under"
        else:
            result["status"] = "on-track"
    else:
        result["status"] = "no-budget"
    return result
