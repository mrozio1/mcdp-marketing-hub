"""Field visit reports as branded PDFs: by field agent (or all of them as a group), by practice, by month.

Always in the report: date, the referral office, who was spoken to (and their role), what was brought,
the conversation notes and the follow-up interval. Optional: spend, time in each office and between
offices, a numbered map of each day's stops, and mileage.

Branding comes from the practice: its logo (Ad images on the practice page) and colours (taken from the
logo, or set by hand next to it). With every practice in the report, the master record's logo is used.
"""
import calendar
import colorsys
import hashlib
import io
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from . import db, field, images, places

DEFAULT_PRIMARY, DEFAULT_ACCENT = "#0F7471", "#D9A441"
ROAD_FACTOR = 1.25          # straight-line miles → rough driving miles when Google can't give a route
OPTIONS = (("spend", "Spend", "What was spent at each visit, and the month's total"),
           ("time", "Time in each office and time between", "From the time logged and how long they were there"),
           ("map", "Numbered map of each day's stops", "Numbers match the order of the visits that day"),
           ("miles", "Total mileage", "Driving miles between each day's stops"))


class ReportError(RuntimeError):
    pass


# ------------------------------------------------------------------ branding
def _hex(c):
    c = (c or "").strip()
    if len(c) == 4 and c.startswith("#"):
        c = "#" + "".join(ch * 2 for ch in c[1:])
    if len(c) == 7 and c.startswith("#"):
        try:
            int(c[1:], 16)
            return c.upper()
        except ValueError:
            return None
    return None


def saved_colors(pid):
    try:
        got = json.loads(db.get_setting(f"brand_colors_{pid}") or "{}")
    except ValueError:
        got = {}
    return _hex(got.get("primary")), _hex(got.get("accent"))


def set_colors(pid, primary, accent):
    db.set_setting(f"brand_colors_{pid}", json.dumps({"primary": _hex(primary) or "", "accent": _hex(accent) or ""}))


def logo_file(pid):
    a = images.logo(pid) if pid else None
    return images.path(a) if a else None


def logo_colors(path):
    """The two strongest colours in a logo (ignoring white, black, greys and transparency)."""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGBA")
        img.thumbnail((160, 160))
        counts = {}
        for r, g, b, a in img.getdata():
            if a < 128:
                continue
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            if s < 0.25 or l > 0.9 or l < 0.12:
                continue
            key = (r // 24, g // 24, b // 24)
            n, sr, sg, sb = counts.get(key, (0, 0, 0, 0))
            counts[key] = (n + 1, sr + r, sg + g, sb + b)
        picks = []
        for n, sr, sg, sb in sorted(counts.values(), reverse=True):
            rgb = (sr // n, sg // n, sb // n)
            hue = colorsys.rgb_to_hls(*(x / 255 for x in rgb))[0]
            if all(min(abs(hue - h2), 1 - abs(hue - h2)) > 0.06 for _, h2 in picks):
                picks.append((rgb, hue))
            if len(picks) == 2:
                break
        return ["#%02X%02X%02X" % p[0] for p in picks]
    except Exception:
        return []


def colors(pid):
    """(primary, accent) for a practice: set by hand, else from the logo, else the hub's colours."""
    p, a = saved_colors(pid) if pid else (None, None)
    if p and a:
        return p, a
    found = logo_colors(logo_file(pid)) if pid and logo_file(pid) else []
    p = p or (found[0] if found else DEFAULT_PRIMARY)
    a = a or (found[1] if len(found) > 1 else (DEFAULT_ACCENT if p != DEFAULT_ACCENT else DEFAULT_PRIMARY))
    return p, a


def _lum(hexc):
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# ------------------------------------------------------------------ the data
def month_bounds(month):
    y, m = int(month[:4]), int(month[5:7])
    start = date(y, m, 1)
    return start, start + timedelta(days=calendar.monthrange(y, m)[1])


def point(v):
    """Where a visit was: the phone's spot when it checked in at the office (the most reliable), else the
    office's saved location, else wherever the phone was."""
    order = (("phone_lat", "phone_lng"), ("office_lat", "office_lng")) if v.get("on_site") == 1 else \
        (("office_lat", "office_lng"), ("phone_lat", "phone_lng"))
    for la, ln in order:
        if v.get(la) is not None and v.get(ln) is not None:
            return float(v[la]), float(v[ln])
    return None


JUMP_MILES = 120     # a same-day hop longer than this is almost always an office saved in the wrong place


# ---- what Google said the last time it was asked (shown on the report page, so a refusal isn't silent)
def _google_note(kind, msg):
    try:
        got = json.loads(db.get_setting("field_report_google") or "{}")
    except ValueError:
        got = {}
    got[kind] = {"error": (msg or "")[:400], "at": db.now()}
    db.set_setting("field_report_google", json.dumps(got))


def google_status():
    try:
        return json.loads(db.get_setting("field_report_google") or "{}")
    except ValueError:
        return {}


def _http_error(e):
    try:
        raw = e.read().decode(errors="replace")
    except Exception:
        raw = ""
    try:
        j = json.loads(raw)
        return (j.get("error") or {}).get("message") or raw
    except ValueError:
        return " ".join(raw.split())[:300] or str(e)


def interval(visited_at, followup):
    """'In 2 weeks (Nov 17)' from the visit day to the follow-up date."""
    if not followup:
        return ""
    try:
        d0, d1 = date.fromisoformat(visited_at[:10]), date.fromisoformat(followup[:10])
    except ValueError:
        return followup
    days = (d1 - d0).days
    when = d1.strftime("%b %-d") + ("" if d1.year == d0.year else d1.strftime(", %Y"))
    if days <= 0:
        return f"Same day ({when})"
    named = {1: "Next day", 7: "In 1 week", 14: "In 2 weeks", 21: "In 3 weeks"}
    if days in named:
        label = named[days]
    elif days % 7 == 0 and days < 56:
        label = f"In {days // 7} weeks"
    elif 28 <= days <= 31:
        label = "In 1 month"
    elif 55 <= days <= 62:
        label = "In 2 months"
    elif 85 <= days <= 93:
        label = "In 3 months"
    elif 175 <= days <= 186:
        label = "In 6 months"
    else:
        label = f"In {days} days"
    return f"{label} ({when})"


def duration(minutes):
    if minutes is None:
        return "—"
    m = int(round(minutes))
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def _leg_key(a, b):
    return "%.4f,%.4f>%.4f,%.4f" % (a[0], a[1], b[0], b[1])


def _cached_legs(keys):
    with db.conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS field_legs (key TEXT PRIMARY KEY, miles REAL NOT NULL, at TEXT)")
        if not keys:
            return {}
        return {r["key"]: r["miles"] for r in c.execute(
            f"SELECT key, miles FROM field_legs WHERE key IN ({','.join('?' * len(keys))})", keys)}


def _store_legs(pairs):
    with db.conn() as c:
        c.executemany("INSERT OR REPLACE INTO field_legs(key, miles, at) VALUES (?,?,?)",
                      [(k, m, db.now()) for k, m in pairs])


def drive_miles(stops):
    """Driving miles for each leg between consecutive stops [(lat, lng)], and whether any is an estimate.
    Asks Google's Routes API once per day (results are kept); falls back to straight-line × 1.25."""
    legs = list(zip(stops, stops[1:]))
    if not legs:
        return [], False
    keys = [_leg_key(a, b) for a, b in legs]
    have = _cached_legs(keys)
    missing = [i for i, k in enumerate(keys) if k not in have]
    if missing:
        got = _routes(stops)
        if got and len(got) == len(legs):
            _store_legs([(keys[i], got[i]) for i in range(len(legs))])
            have.update({keys[i]: got[i] for i in range(len(legs))})
    out, estimated = [], False
    for (a, b), k in zip(legs, keys):
        if k in have:
            out.append(have[k])
        else:
            out.append(round(places.miles_between(a[0], a[1], b[0], b[1]) * ROAD_FACTOR, 1))
            estimated = True
    return out, estimated


ROUTES_URL = os.environ.get("ROUTES_URL", "https://routes.googleapis.com/directions/v2:computeRoutes")


def _routes(stops):
    key = places.server_key()
    if not key or len(stops) < 2 or len(stops) > 27:
        return None
    wp = lambda p: {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}
    body = {"origin": wp(stops[0]), "destination": wp(stops[-1]), "intermediates": [wp(p) for p in stops[1:-1]],
            "travelMode": "DRIVE", "routingPreference": "TRAFFIC_UNAWARE", "units": "IMPERIAL"}
    req = urllib.request.Request(ROUTES_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": "routes.legs.distanceMeters"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.load(r)
        legs = j["routes"][0]["legs"]
        _google_note("routes", "")
        return [round((l.get("distanceMeters") or 0) / 1609.344, 1) for l in legs]
    except urllib.error.HTTPError as e:
        _google_note("routes", f"Routes API (HTTP {e.code}): {_http_error(e)}")
    except (KeyError, IndexError):
        _google_note("routes", "Routes API found no driving route between these stops.")
    except Exception as e:
        _google_note("routes", f"Routes API couldn't be reached: {e}")
    return None


def gather(month, agent_id=None, practice_id=None, opts=None):
    """{'agents': [{'name', 'days': [{'day', 'visits': [...], 'miles', ...}], totals...}], totals...}."""
    opts = opts or {}
    start, end = month_bounds(month)
    rows = field.with_people(field.visits_between(start, end, agent_id, practice_id))
    rows.sort(key=lambda r: (r["user_name"] or "", r["visited_at"], r["id"]))
    agents, any_estimate = [], False
    for r in rows:
        if not agents or agents[-1]["key"] != (r["user_id"], r["user_name"]):
            agents.append({"key": (r["user_id"], r["user_name"]), "name": r["user_name"] or "Someone", "days": []})
        days = agents[-1]["days"]
        d = r["visited_at"][:10]
        if not days or days[-1]["day"] != d:
            days.append({"day": d, "visits": []})
        days[-1]["visits"].append(r)
    for a in agents:
        for d in a["days"]:
            vs = d["visits"]
            for i, v in enumerate(vs):
                v["n"] = i + 1
                v["at"] = datetime.fromisoformat(v["visited_at"][:16])
                v["gap"] = None
                if i:
                    prev = vs[i - 1]
                    left = prev["at"] + timedelta(minutes=prev["minutes"] or 0)
                    v["gap"] = max(0, (v["at"] - left).total_seconds() / 60)
                v["interval"] = interval(v["visited_at"], v.get("followup"))
                v["pt"] = point(v)
            d["spend"] = round(sum(v["cost"] or 0 for v in vs), 2)
            d["in_office"] = sum(v["minutes"] or 0 for v in vs)
            d["between"] = sum(v["gap"] or 0 for v in vs)
            d["miles"], d["legs"], d["estimated"] = None, [], False
            pts = [v["pt"] for v in vs if v["pt"]]
            d["jumps"] = []
            if opts.get("miles") and len(pts) > 1:
                d["legs"], d["estimated"] = drive_miles(pts)
                with_pt = [v for v in vs if v["pt"]]
                for i, leg in enumerate(d["legs"]):
                    if leg > JUMP_MILES:      # left out of the totals and named, so the office can be fixed
                        d["jumps"].append((with_pt[i]["office_name"], with_pt[i + 1]["office_name"], leg))
                d["miles"] = round(sum(l for l in d["legs"] if l <= JUMP_MILES), 1)
                any_estimate = any_estimate or d["estimated"]
            elif opts.get("miles"):
                d["miles"] = 0.0
        a["visits"] = sum(len(d["visits"]) for d in a["days"])
        a["spend"] = round(sum(d["spend"] for d in a["days"]), 2)
        a["miles"] = round(sum(d["miles"] or 0 for d in a["days"]), 1)
        a["in_office"] = sum(d["in_office"] for d in a["days"])
    # the same name at different offices is a different person (two Tammys); an email ties them together
    people = {(p["email"].lower(),) if p["email"] else (r["office_id"], p["name"].strip().lower())
              for r in rows for p in r["people"] if p["name"] or p["email"]}
    return {"agents": agents, "visits": len(rows), "offices": len({r["office_id"] for r in rows}), "people": len(people),
            "spend": round(sum(r["cost"] or 0 for r in rows), 2), "miles": round(sum(a["miles"] for a in agents), 1),
            "in_office": sum(a["in_office"] for a in agents), "timed": sum(1 for r in rows if r.get("minutes")),
            "estimated": any_estimate, "days": sum(len(a["days"]) for a in agents),
            "jumps": [(a["name"], d["day"], *j) for a in agents for d in a["days"] for j in d.get("jumps", [])]}


# ------------------------------------------------------------------ maps
STATIC_URL = os.environ.get("STATIC_MAPS_URL", "https://maps.googleapis.com/maps/api/staticmap")
LABELS = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def label(n):
    return LABELS[n - 1] if 0 < n <= len(LABELS) else ""


def _map_cache_dir():
    d = os.path.join(os.path.dirname(db.DB_PATH) or ".", "map-cache")
    os.makedirs(d, exist_ok=True)
    return d


def static_map(points, color):
    """A Google Static Maps PNG of numbered stops joined in order, or None (key missing or API off)."""
    key = places.server_key()
    if not key or not points:
        return None
    hexc = color.lstrip("#")
    params = [("size", "640x190"), ("scale", "2"), ("maptype", "roadmap"), ("key", key),
              ("style", "feature:poi|visibility:off"), ("style", "feature:transit|visibility:off")]
    if len(points) > 1:
        params.append(("path", f"color:0x{hexc}cc|weight:4|" + "|".join("%.5f,%.5f" % p for p in points)))
    for i, p in enumerate(points, 1):
        params.append(("markers", f"color:0x{hexc}|label:{label(i)}|%.5f,%.5f" % p))
    url = STATIC_URL + "?" + urllib.parse.urlencode(params)
    cache = os.path.join(_map_cache_dir(), hashlib.sha256(url.replace(key, "").encode()).hexdigest()[:32] + ".png")
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = r.read()
            if not data.startswith(b"\x89PNG"):
                _google_note("map", "Maps Static API sent something other than a map image.")
                return None
    except urllib.error.HTTPError as e:
        _google_note("map", f"Maps Static API (HTTP {e.code}): {_http_error(e)}")
        return None
    except Exception as e:
        _google_note("map", f"Maps Static API couldn't be reached: {e}")
        return None
    _google_note("map", "")
    with open(cache, "wb") as f:
        f.write(data)
    return data


# ------------------------------------------------------------------ the PDF
def build(month, agent_id=None, practice_id=None, opts=None, brand_pid=None, practice_name="", agent_name="",
          today=None):
    """PDF bytes. brand_pid: whose logo and colours to use (the practice, or the master record for all)."""
    from reportlab.lib import colors as rl
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String

    opts = opts or {}
    data = gather(month, agent_id, practice_id, opts)
    primary, accent = colors(brand_pid)
    P, A = rl.HexColor(primary), rl.HexColor(accent)
    on_p = rl.white if _lum(primary) < 0.6 else rl.HexColor("#1D1D1B")
    ink, soft, hair = rl.HexColor("#1D1D1B"), rl.HexColor("#5B5852"), rl.HexColor("#E3DED3")
    tint = rl.HexColor("#%02X%02X%02X" % tuple(int(255 - (255 - int(primary[i:i + 2], 16)) * 0.08) for i in (1, 3, 5)))
    month_label = date.fromisoformat(month + "-01").strftime("%B %Y")
    title = "Field visits"
    who = agent_name or "All field agents"
    whose = practice_name or "All practices"
    logo = logo_file(brand_pid)
    today = today or date.today()

    st = lambda name, **kw: ParagraphStyle(name, **{"fontName": "Helvetica", "fontSize": 8.6, "leading": 11,
                                                    "textColor": ink, **kw})
    body, small, bold = st("b"), st("s", fontSize=7.6, leading=9.6, textColor=soft), st("bb", fontName="Helvetica-Bold")
    h1 = st("h1", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=P)
    h2 = st("h2", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=ink, spaceBefore=4)
    cell_h = st("ch", fontName="Helvetica-Bold", fontSize=7.6, leading=9.6, textColor=on_p)
    num_style = st("n", fontName="Helvetica-Bold", fontSize=9, textColor=rl.white, alignment=1)
    kpi_v = st("kv", fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=P)
    kpi_l = st("kl", fontSize=7.6, leading=9.5, textColor=soft)
    right = st("r", alignment=TA_RIGHT, fontSize=7.6, textColor=soft)
    E = lambda s: escape(str(s or ""))

    W, H = landscape(letter)
    margin = 0.5 * inch
    band = 0.95 * inch

    def chrome(canv, doc):
        canv.saveState()
        canv.setFillColor(P)
        canv.rect(0, H - band, W, band, stroke=0, fill=1)
        canv.setFillColor(A)
        canv.rect(0, H - band - 4, W, 4, stroke=0, fill=1)
        x = margin
        if logo:
            try:
                from reportlab.lib.utils import ImageReader
                img = ImageReader(logo)
                iw, ih = img.getSize()
                bh, bw = band - 0.3 * inch, 2.4 * inch
                s = min((bw - 12) / iw, (bh - 12) / ih)
                canv.setFillColor(rl.white)
                canv.roundRect(x, H - band + 0.15 * inch, iw * s + 12, bh, 5, stroke=0, fill=1)
                canv.drawImage(img, x + 6, H - band + 0.15 * inch + (bh - ih * s) / 2, iw * s, ih * s, mask="auto")
                x += iw * s + 12 + 0.2 * inch
            except Exception:
                pass
        canv.setFillColor(on_p)
        canv.setFont("Helvetica-Bold", 16)
        canv.drawString(x, H - 0.47 * inch, f"{title} · {month_label}")
        canv.setFont("Helvetica", 9.5)
        canv.drawString(x, H - 0.7 * inch, f"{whose} · {who}")
        canv.setFillColor(soft)
        canv.setFont("Helvetica", 7.5)
        canv.drawString(margin, 0.32 * inch, f"Prepared {today:%B %-d, %Y} from the MCDP Hub")
        canv.drawRightString(W - margin, 0.32 * inch, f"Page {doc.page}")
        canv.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=(W, H), leftMargin=margin, rightMargin=margin,
                            topMargin=band + 0.3 * inch, bottomMargin=0.55 * inch, title=f"{title} · {month_label}",
                            author="MCDP Hub", subject=f"{whose} · {who}")
    out = []

    # ---- summary
    kpis = [("Visits", str(data["visits"])), ("Offices visited", str(data["offices"])), ("People spoken to", str(data["people"])),
            ("Days in the field", str(data["days"]))]
    if opts.get("spend"):
        kpis.append(("Spend", f"${data['spend']:,.2f}"))
    if opts.get("miles"):
        kpis.append(("Miles driven" + (" (about)" if data["estimated"] else ""), f"{data['miles']:,.1f}"))
    if opts.get("time"):
        kpis.append(("Time in offices", duration(data["in_office"]).replace(" min", "m").replace(" h", "h") if data["timed"] else "—"))
    cw = (W - 2 * margin) / len(kpis)
    kt = Table([[Paragraph(v, kpi_v) for _, v in kpis], [Paragraph(l, kpi_l) for l, _ in kpis]], colWidths=[cw] * len(kpis))
    kt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), tint), ("LINEABOVE", (0, 0), (-1, 0), 2, P),
                            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, 0), 8),
                            ("BOTTOMPADDING", (0, -1), (-1, -1), 8), ("LINEAFTER", (0, 0), (-2, -1), 0.5, rl.white)]))
    out += [kt, Spacer(1, 6)]
    if len(data["agents"]) > 1:
        rows = [["Field agent", "Visits", "Days"] + (["Spend"] if opts.get("spend") else []) +
                (["Miles"] if opts.get("miles") else []) + (["Time in offices"] if opts.get("time") else [])]
        for a in data["agents"]:
            rows.append([a["name"], str(a["visits"]), str(len(a["days"]))] + ([f"${a['spend']:,.2f}"] if opts.get("spend") else []) +
                        ([f"{a['miles']:,.1f}"] if opts.get("miles") else []) + ([duration(a["in_office"]) if a["in_office"] else "—"] if opts.get("time") else []))
        t = Table([[Paragraph(E(c), cell_h if i == 0 else body) for c in r] for i, r in enumerate(rows)],
                  colWidths=[2.6 * inch] + [1.1 * inch] * (len(rows[0]) - 1), hAlign="LEFT")
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), P), ("LINEBELOW", (0, 1), (-1, -1), 0.4, hair),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        out += [Spacer(1, 6), Paragraph("By field agent", h2), Spacer(1, 3), t]
    notes = []
    if opts.get("miles"):
        notes.append("Miles are driving distance between each day's stops, in order (not to the first stop or home)"
                     + (". Where Google couldn't give a route, straight-line distance × 1.25 is used, marked ≈." if data["estimated"] else "."))
    if opts.get("time"):
        notes.append("Time in an office is what was logged with the visit; time between runs from leaving one office "
                     "(arrival + time there) to arriving at the next.")
    for nm, dy, a_off, b_off, leg in data["jumps"]:
        notes.append(f"<font color='#A3401C'>Left out of the miles: {leg:,.0f} mi from {E(a_off)} to {E(b_off)} on "
                     f"{date.fromisoformat(dy):%b %-d} ({E(nm)}). One of those offices is probably saved in the wrong place; "
                     f"check its address on the Map.</font>")
    for n in notes:
        out += [Spacer(1, 3), Paragraph(n, small)]
    if not data["visits"]:
        out += [Spacer(1, 18), Paragraph(f"No field visits logged for {E(whose)} in {month_label}.", body)]

    # ---- each agent, each day
    numbered, timed = bool(opts.get("map")), bool(opts.get("time"))   # stop numbers go with the map, clock times with time
    cols = ([("#", 0.3)] if numbered else []) + ([("Time", 0.78)] if timed else []) + [
        ("Referral office", 1.8), ("Spoke with", 1.55), ("Brought", 1.3), ("Conversation", 2.5), ("Follow up", 1.1)]
    if opts.get("spend"):
        cols.append(("Spend", 0.72))
    if opts.get("time"):
        cols.append(("There · between", 0.95))
    scale = (W - 2 * margin) / (sum(w for _, w in cols) * inch)
    widths = [w * inch * scale for _, w in cols]
    for ai, a in enumerate(data["agents"]):
        if len(data["agents"]) > 1 or ai:
            out.append(PageBreak())
        else:
            out.append(Spacer(1, 14))
        out.append(Paragraph(E(a["name"]), h1))
        bits = [f"{a['visits']} visit{'s' if a['visits'] != 1 else ''} on {len(a['days'])} day{'s' if len(a['days']) != 1 else ''}"]
        if opts.get("spend"):
            bits.append(f"${a['spend']:,.2f} spent")
        if opts.get("miles"):
            bits.append(f"{a['miles']:,.1f} miles")
        if opts.get("time") and a["in_office"]:
            bits.append(f"{duration(a['in_office'])} in offices")
        out += [Paragraph(" · ".join(bits), small), Spacer(1, 8)]
        for d in a["days"]:
            day = date.fromisoformat(d["day"])
            head = [f"{day:%A, %B %-d}", f"{len(d['visits'])} visit{'s' if len(d['visits']) != 1 else ''}"]
            if opts.get("spend") and d["spend"]:
                head.append(f"${d['spend']:,.2f}")
            if opts.get("miles") and d["miles"] is not None:
                head.append(("≈ " if d["estimated"] else "") + f"{d['miles']:,.1f} mi")
            if opts.get("time") and d["in_office"]:
                head.append(f"{duration(d['in_office'])} in offices")
            block = [Paragraph(E(head[0]) + "<font color='#5B5852' size='8.6'>  ·  " + E("  ·  ".join(head[1:])) + "</font>", h2),
                     Spacer(1, 4)]
            if opts.get("map"):
                pts = [v["pt"] for v in d["visits"] if v["pt"]]
                png = static_map(pts, primary) if pts else None
                mw = W - 2 * margin
                if png:
                    block += [Image(io.BytesIO(png), width=mw, height=mw * 190 / 640), Spacer(1, 5)]
                elif pts:
                    block += [_sketch(pts, mw, 1.9 * inch, P, rl, Drawing, Rect, Line, Circle, String), Spacer(1, 5)]
            rows = [[Paragraph(c, cell_h) for c, _ in cols]]
            for i, v in enumerate(d["visits"]):
                spoke = "<br/>".join(E(p["name"] or p["email"]) + (f" <font color='#5B5852'>({E(p['role'])})</font>" if p["role"] else "")
                                     for p in v["people"]) or "<font color='#5B5852'>—</font>"
                office = f"<b>{E(v['office_name'])}</b>" + (f"<br/><font size='7.4' color='#5B5852'>{E(v['office_address'])}</font>" if v.get("office_address") else "")
                if opts.get("miles") and i and d["legs"] and i - 1 < len(d["legs"]) and d["legs"][i - 1] > JUMP_MILES:
                    office += "<br/><font size='7.4' color='#A3401C'>location looks wrong (left out of the miles)</font>"
                elif opts.get("miles") and i and d["legs"] and i - 1 < len(d["legs"]):
                    office += (f"<br/><font size='7.4' color='#5B5852'>{'≈ ' if d['estimated'] else ''}{d['legs'][i - 1]:.1f} mi "
                               + (f"from stop {label(i)}" if numbered else "from the last stop") + "</font>")
                row = ([Paragraph(label(v["n"]) or str(v["n"]), num_style)] if numbered else []) + \
                      ([Paragraph(v["at"].strftime("%-I:%M %p"), body)] if timed else []) + [
                       Paragraph(office, body), Paragraph(spoke, body), Paragraph(E(v["dropped"]) or "—", body),
                       Paragraph(E(v["notes"]).replace("\n", "<br/>") or "<font color='#5B5852'>—</font>", body),
                       Paragraph(E(v["interval"]) or "<font color='#5B5852'>None set</font>", body)]
                if opts.get("spend"):
                    row.append(Paragraph(f"${v['cost']:,.2f}" if v["cost"] else "—", body))
                if opts.get("time"):
                    row.append(Paragraph(duration(v["minutes"]) + "<br/><font size='7.4' color='#5B5852'>"
                                         + (duration(v["gap"]) + " since last" if v["gap"] is not None else "first stop") + "</font>", body))
                rows.append(row)
            t = Table(rows, colWidths=widths, repeatRows=1)
            style = [("BACKGROUND", (0, 0), (-1, 0), P), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                     ("LINEBELOW", (0, 1), (-1, -1), 0.4, hair), ("TOPPADDING", (0, 1), (-1, -1), 5),
                     ("BOTTOMPADDING", (0, 1), (-1, -1), 5)]
            for i in range(1, len(rows)):
                if numbered:
                    style.append(("BACKGROUND", (0, i), (0, i), A if _lum(accent) < 0.7 else P))
                if i % 2 == 0:
                    style.append(("BACKGROUND", (1 if numbered else 0, i), (-1, i), tint))
            t.setStyle(TableStyle(style))
            out.append(KeepTogether(block + [t]) if len(d["visits"]) <= 6 else KeepTogether(block[:2]))
            if len(d["visits"]) > 6:
                out += block[2:] + [t]
            out.append(Spacer(1, 12))
    doc.build(out, onFirstPage=chrome, onLaterPages=chrome)
    return buf.getvalue()


def _sketch(pts, w, h, color, rl, Drawing, Rect, Line, Circle, String):
    """A plain numbered sketch of the stops (no streets), when Google's map image isn't available."""
    d = Drawing(w, h)
    d.add(Rect(0, 0, w, h, fillColor=rl.HexColor("#F4F2EC"), strokeColor=rl.HexColor("#E3DED3")))
    lats, lngs = [p[0] for p in pts], [p[1] for p in pts]
    la0, la1, ln0, ln1 = min(lats), max(lats), min(lngs), max(lngs)
    span_la, span_ln = max(la1 - la0, 0.005), max((ln1 - ln0) * math.cos(math.radians((la0 + la1) / 2)), 0.005)
    s = min((w - 40) / span_ln, (h - 30) / span_la)
    cx, cy = w / 2, h / 2
    mid_la, mid_ln = (la0 + la1) / 2, (ln0 + ln1) / 2
    xy = [(cx + (p[1] - mid_ln) * math.cos(math.radians(mid_la)) * s, cy + (p[0] - mid_la) * s) for p in pts]
    for a, b in zip(xy, xy[1:]):
        d.add(Line(a[0], a[1], b[0], b[1], strokeColor=color, strokeWidth=2))
    for i, (x, y) in enumerate(xy, 1):
        d.add(Circle(x, y, 8, fillColor=color, strokeColor=rl.white, strokeWidth=1.5))
        d.add(String(x, y - 3, label(i), fontName="Helvetica-Bold", fontSize=8, fillColor=rl.white, textAnchor="middle"))
    why = (google_status().get("map") or {}).get("error") or "no Google Maps server key"
    d.add(String(6, 5, ("Sketch of the stops. Google didn't send a street map: " + why)[:190], fontName="Helvetica",
                 fontSize=6.5, fillColor=rl.HexColor("#5B5852")))
    return d
