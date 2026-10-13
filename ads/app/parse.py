"""
Parse ad-platform CSV exports as people actually download them from the UI:

* Google Ads: Campaigns report, Search terms report (title/date lines above the
  header, "Total:" rows at the bottom, "--" for blanks, "1,234.56" numbers).
* Meta Ads Manager: Campaigns / Ad sets / Ads export ("Amount spent (USD)",
  "Results", "Reporting starts", ...).

Everything is normalised to a list of dict rows with these keys:
    platform, level ('campaign' | 'search_term' | 'ad'), date (date|None),
    date_start, date_end, campaign, name, spend, impressions, clicks,
    conversions, conv_value, status, budget, match_type
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

# Header aliases (compared after normalisation: lowercase, no punctuation).
ALIASES = {
    "campaign": ["campaign", "campaign name", "campaign_name"],
    "name": ["search term", "search terms", "ad set name", "ad name", "ad group", "keyword"],
    "search_term": ["search term", "search terms"],
    "spend": ["cost", "spend", "amount spent", "amount spent usd", "cost usd", "spend usd"],
    "impressions": ["impr", "impressions", "impr."],
    "clicks": ["clicks", "link clicks", "clicks all", "interactions"],
    "conversions": ["conversions", "conv", "all conv", "results", "leads", "purchases", "conversions all"],
    "conv_value": ["conv value", "conversion value", "all conv value", "results value", "purchase conversion value"],
    "date": ["day", "date", "reporting starts", "date start"],
    "date_end": ["reporting ends", "date stop", "date end"],
    "status": ["campaign status", "status", "campaign delivery", "delivery"],
    "budget": ["budget", "daily budget", "campaign budget", "ad set budget"],
    "match_type": ["match type", "search term match type", "keyword match type"],
}

GOOGLE_HINTS = {"impr", "cost", "conv value", "search term", "campaign type", "avg cpc", "interaction rate"}
META_HINTS = {"amount spent", "reporting starts", "results", "result indicator", "cost per result", "reach", "frequency"}


class ParseError(ValueError):
    pass


def _norm(h: str) -> str:
    h = h.replace("﻿", "").strip().lower()
    h = re.sub(r"\(([a-z]{3})\)", r" \1", h)          # "Amount spent (USD)" -> "amount spent usd"
    h = re.sub(r"[^a-z0-9 ]+", " ", h)
    return re.sub(r"\s+", " ", h).strip()


def _num(v) -> float:
    if v is None:
        return 0.0
    s = str(v).strip()
    if s in ("", "--", "-", "—", " --", "n/a", "N/A"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^0-9.\-]", "", s)                     # drop $, %, commas, currency codes
    if s in ("", "-", ".", "-."):
        return 0.0
    try:
        n = float(s)
    except ValueError:
        return 0.0
    return -n if neg else n


DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%a, %b %d, %Y", "%d/%m/%Y", "%Y/%m/%d")


def _date(v):
    s = (v or "").strip()
    if not s or s == "--":
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _find_header(rows):
    """Google exports have title lines first; find the first row that looks like a header."""
    for i, row in enumerate(rows[:15]):
        cells = {_norm(c) for c in row if c and c.strip()}
        has_campaign = any(c in cells for c in ALIASES["campaign"] + ["search term", "ad set name", "ad name"])
        has_metric = any(c in cells for c in ALIASES["spend"] + ALIASES["impressions"] + ALIASES["clicks"])
        if has_campaign and has_metric and len(cells) >= 3:
            return i
    raise ParseError(
        "Couldn't find the column headers. Export a Campaigns or Search terms report "
        "as CSV from Google Ads, or a campaign export from Meta Ads Manager."
    )


# Words that mean a column is a ratio/derived metric, not the raw value we want.
DERIVED = {
    "spend": ("per", "conv", "avg", "rate"),
    "conversions": ("rate", "value", "cost", "per", "avg", "from"),
    "clicks": ("rate", "cost", "per", "ctr", "types"),
    "impressions": ("share", "rate", "per", "lost"),
    "conv_value": ("per", "cost"),
}


def _col(header_norm, key, exclude=()):
    for alias in ALIASES[key]:
        if alias in header_norm and alias not in exclude:
            return header_norm.index(alias)
    # prefix match for things like "amount spent usd" / "cost usd"
    bad = DERIVED.get(key, ())
    for alias in ALIASES[key]:
        for idx, h in enumerate(header_norm):
            if h.startswith(alias + " ") and h not in exclude and not any(w in h.split() for w in bad):
                return idx
    return None


def _date_range_from_title(lines):
    """Google exports put e.g. 'September 1, 2026 - September 30, 2026' above the header."""
    for line in lines:
        text = " ".join(line)
        m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*[-–]\s*([A-Z][a-z]+ \d{1,2}, \d{4})", text)
        if m:
            try:
                return (datetime.strptime(m.group(1), "%B %d, %Y").date(),
                        datetime.strptime(m.group(2), "%B %d, %Y").date())
            except ValueError:
                pass
    return None, None


def parse_export(data: bytes, filename: str = "") -> dict:
    """Return {'platform', 'level', 'rows', 'date_start', 'date_end', 'warnings'}."""
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = data.decode(enc)
            if "\x00" in text:
                continue
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ParseError("The file isn't readable text. Please export as CSV.")
    sample = text[:4096]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if len(rows) < 2:
        raise ParseError("The file is empty.")

    h = _find_header(rows)
    header = [_norm(c) for c in rows[h]]
    title_start, title_end = _date_range_from_title(rows[:h])
    cells = set(header)

    platform = "google" if len(cells & GOOGLE_HINTS) >= len(cells & META_HINTS) else "meta"
    if any(c.startswith("amount spent") for c in cells):
        platform = "meta"
    level = "search_term" if _col(header, "search_term") is not None else (
        "ad" if ("ad name" in cells or "ad set name" in cells) else "campaign")

    c = {k: _col(header, k) for k in ALIASES}
    if level == "search_term":
        c["name"] = _col(header, "search_term")
    elif "ad set name" in cells:
        c["name"] = header.index("ad set name")
    elif "ad name" in cells:
        c["name"] = header.index("ad name")
    else:
        c["name"] = None
    # "Conv. value" must not be read as conversions, nor "Cost / conv." as cost.
    conv_value_idx = c["conv_value"]
    if c["conversions"] is not None and c["conversions"] == conv_value_idx:
        c["conversions"] = None
    for alias in ALIASES["conversions"]:
        if alias in header and header.index(alias) != conv_value_idx:
            c["conversions"] = header.index(alias)
            break
    if c["spend"] is None:
        raise ParseError("Couldn't find a cost / amount spent column in this export.")
    if c["campaign"] is None and c["name"] is None:
        raise ParseError("Couldn't find a campaign (or search term) column in this export.")

    def get(row, key):
        idx = c.get(key)
        return row[idx] if idx is not None and idx < len(row) else ""

    out, skipped = [], 0
    for row in rows[h + 1:]:
        first = (row[0] if row else "").strip().lower()
        joined = " ".join(row).lower()
        if first.startswith("total") or joined.startswith("total:") or "total: " in joined[:40]:
            skipped += 1
            continue
        campaign = get(row, "campaign").strip()
        name = get(row, "name").strip()
        if not campaign and not name:
            continue
        d = _date(get(row, "date"))
        d_end = _date(get(row, "date_end"))
        out.append({
            "platform": platform,
            "level": level,
            "date": d if (d and (not d_end or d_end == d)) else None,
            "date_start": d or title_start,
            "date_end": d_end or d or title_end,
            "campaign": campaign or "(no campaign)",
            "name": name,
            "spend": round(_num(get(row, "spend")), 2),
            "impressions": int(_num(get(row, "impressions"))),
            "clicks": int(_num(get(row, "clicks"))),
            "conversions": round(_num(get(row, "conversions")), 2),
            "conv_value": round(_num(get(row, "conv_value")), 2),
            "status": get(row, "status").strip(),
            "budget": round(_num(get(row, "budget")), 2),
            "match_type": get(row, "match_type").strip(),
        })
    if not out:
        raise ParseError("No data rows found under the headers.")

    starts = [r["date_start"] for r in out if r["date_start"]]
    ends = [r["date_end"] for r in out if r["date_end"]]
    warnings = []
    if not starts:
        warnings.append("No dates found in the export; budget pacing will assume it covers this month to date.")
    return {
        "platform": platform,
        "level": level,
        "rows": out,
        "date_start": min(starts) if starts else None,
        "date_end": max(ends) if ends else None,
        "warnings": warnings,
        "skipped_total_rows": skipped,
    }
