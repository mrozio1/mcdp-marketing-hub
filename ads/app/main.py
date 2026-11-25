"""MCDP Ads: upload Google / Meta exports per practice, get exact findings + AI write-ups."""
from __future__ import annotations

import asyncio
import base64
import secrets
import hmac
import json
import html
import os
import re
import sqlite3
import time
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import markdown as md
from fastapi.responses import JSONResponse
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import p360
from . import dept, checklists
from . import accounts
from . import actions, agent, ai, brand, cal, campaigns, db, images, intake, wins, emailer, tools, emailfmt, field, organic, places, microsoft, referrals, reviews, salesforce, schools, scope, seasons, sync, users, visibility, field_report, studio, canva, studio_flow, social, studio_video, video2, bgremove, stock, vfx, proxy
from . import connectors as cx
from . import report as master_report
from . import dashboard as home_dash
from .analyze import analyze_campaigns, analyze_search_terms, money, pacing, pct
from .parse import ParseError, parse_export

HERE = Path(__file__).parent
PASSWORD = os.environ.get("ADS_PASSWORD", "")
SECRET = os.environ.get("ADS_SECRET") or os.urandom(32).hex()
MAX_UPLOAD = 20 * 1024 * 1024
PLATFORMS = (("google", "Google Ads"), ("meta", "Meta Ads"))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# ---------- who may do what ----------
# Looking and exporting (GET) needs any sign-in. Every change (POST) needs an editor, and these
# need an admin whatever the method: keys and connections, people, map setup, deleting practices.
PUBLIC = ("/login", "/logout", "/health", "/static/", "/setup/", "/internal/", "/s/", "/i/", "/e/", "/p360-in/", "/ls-in/")
ANY_SIGNED_IN_POST = ("/account", "/field/route-order", "/studio/template-favs", "/p360/", "/field/alerts/",
                      "/field/accounts/note")   # /p360: checked per department in the route
FIELD_WORK = re.compile(r"^/field/(log|visits|receipt)(/|$)(?!(\d+(/delete|/edit)?)$)")   # logging visits: field visits switched on
FIELD_DELETE = re.compile(r"^/field/visits/\d+(/delete|/edit)?$")          # own visit (field on) or any visit (editors); checked in the route
FIELD_SETTINGS = re.compile(r"^/field/settings(/|$)")             # budgets and practice links: editors
CAL_WORK = re.compile(r"^/calendar/(new|events|\d+)(/|$)")         # making and changing events: anyone signed in; the routes
                                                                   # let only editors and field agents add non-personal events
ADMIN_ONLY = (re.compile(r"^/(connections|google|salesforce|microsoft|canva|team)(/|$)"), re.compile(r"^/map/setup$"),
              re.compile(r"^/intake/(\d+/(reveal|revoke)|email)$"),
              re.compile(r"^/calendar/company(/|$)"),
              re.compile(r"^/p/\d+/(delete|hide|show)$"), re.compile(r"^/dashboard/budget$"))


def needed_role(method, path):
    if path.startswith(PUBLIC) or path == "/setup":
        return None
    if any(rx.match(path) for rx in ADMIN_ONLY):
        return "admin"
    if FIELD_SETTINGS.match(path):
        return "admin"
    if FIELD_DELETE.match(path):
        return "viewer"
    if FIELD_WORK.match(path):
        return "field"
    if CAL_WORK.match(path):
        return "viewer"
    if method in ("GET", "HEAD") or path.startswith(ANY_SIGNED_IN_POST):
        return "viewer"
    return "editor"


# What each person can open is set per person on the Team page (users.access): a tab, reports and
# exports, refreshing and syncing, and which practices.
TAB_PATHS = [("dashboard", re.compile(r"^/(dashboard/.*)?$")), ("practices", re.compile(r"^/(practices|p/\d+|tracking|brand-images|brand-kits|intake)(/|$)")),
             ("p360", re.compile(r"^/p360(/|$)")), ("reviews", re.compile(r"^/reviews")), ("search", re.compile(r"^/search")),
             ("studio", re.compile(r"^/studio(/|$)")), ("map", re.compile(r"^/map(/|$)")),
             ("field", re.compile(r"^/field(/|\.xlsx|$)")), ("calendar", re.compile(r"^/calendar(/|$)"))]
REPORT_PATHS = re.compile(r"^/(reports(/|$)|field/report|field\.xlsx|reviews\.xlsx|reviews/report|search\.xlsx"
                          r"|p/\d+/(report|posts\.csv|email)|map/(referrals|schools|zips)\.xlsx)")
REFRESH_PATHS = re.compile(r"^/(sync(/run)?$|sync/(?!status)|map/(refresh|schools/refresh|referrals/refresh|zips/refresh)$"
                           r"|reviews/(sync|locations/refresh)$|search/refresh$|studio/posts/sync$|tracking/fix-all$)")
PRACTICE_PATH = re.compile(r"^/p/(\d+)(/|$)")


def access_problem(u, method, path):
    """'' or why this person can't open the path (their tabs, reports, refreshing, practices)."""
    if not u or path.startswith(PUBLIC) or path == "/setup":
        return ""
    if path.startswith(("/account", "/static", "/sync/status", "/sync/done", "/calendar/lookup")):
        return ""
    a = users.access(u)
    if REPORT_PATHS.match(path) and not a["reports"]:
        return "Reports and exports aren't switched on for you. Ask an admin."
    if REFRESH_PATHS.match(path):
        return "" if a["refresh"] else "Refreshing and syncing aren't switched on for you. Ask an admin."
    if path == "/map/data.json" and (a["map"] or a["field"]):
        return ""
    if path == "/reports" or path.startswith("/reports/"):
        return ""
    for tab, rx in TAB_PATHS:
        if rx.match(path):
            if not a[tab]:
                return "" if tab == "dashboard" and method in ("GET", "HEAD") and path == "/" else \
                    f"{dict((k, l) for k, l, _ in users.TABS)[tab]} isn't switched on for you. Ask an admin."
            break
    m = PRACTICE_PATH.match(path)
    if m and not users.sees_practice(u, int(m.group(1))):
        return "That practice isn't one of yours. Ask an admin to add it to your practices."
    return ""


async def role_gate(request: Request, call_next):
    need = needed_role(request.method, request.url.path)
    request.state.user = current_user(request)
    u = request.state.user
    p = request.url.path
    if u is None and (p == "/p360" or p.startswith("/p360/")):
        # signed out (e.g. a 14-day session ran out on a phone): Practice 360 pages go to the sign-in page
        if "application/json" in request.headers.get("accept", ""):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "Please sign in again."}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    if u and request.url.path == "/" and request.method in ("GET", "HEAD") and not users.can(u, "dashboard"):
        return RedirectResponse(users.home(u), status_code=303)      # dashboard off: their first tab, or Account
    if REFRESH_PATHS.match(request.url.path) and u and users.can(u, "refresh"):
        need = "viewer" if need == "editor" else need                # refreshing switched on for them
    db.ONLY.set(users.practice_ids(u) if u else None)     # lists in this request keep to their practices
    why = access_problem(u, request.method, request.url.path)
    if why:
        if "application/json" in (request.headers.get("content-type", "") + request.headers.get("accept", "")) \
                or request.url.path.endswith(".json"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": why}, status_code=403)
        flash(request, why, "error")
        back = request.headers.get("referer") or ""
        if request.method in ("GET", "HEAD") or not back.startswith(str(request.base_url)):
            back = users.home(u)
        return RedirectResponse(back, status_code=303)
    refused = u and (not u.get("field") if need == "field" else
                     not (u.get("field") or users.rank(u["role"]) >= users.rank("editor")) if need == "calendar" else
                     users.rank(u["role"]) < users.rank(need))
    if need and refused:
        if need == "calendar":
            msg = "Adding calendar events needs editor access or field visits switched on. Ask an admin."
        elif need == "field":
            msg = "Field visits aren't switched on for you. Ask an admin to turn them on on the Team page."
        elif request.state.user["role"] == "viewer":
            msg = "You have view-only access, so you can look and export but not change things. Ask an editor."
        elif need == "admin":
            msg = "That needs admin access. Ask an admin."
        else:
            msg = "That needs editor access. Ask an editor or admin."
        if "application/json" in (request.headers.get("content-type", "") + request.headers.get("accept", "")):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": msg}, status_code=403)
        flash(request, msg, "error")
        back = request.headers.get("referer") or "/"
        if request.method in ("GET", "HEAD") or not back.startswith(str(request.base_url)):
            back = users.home(u)
        return RedirectResponse(back, status_code=303)
    return await call_next(request)


from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

app.add_middleware(BaseHTTPMiddleware, dispatch=role_gate)   # runs inside the session middleware below
app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="lax", https_only=False,
                   max_age=60 * 60 * 24 * 14, session_cookie="mcdp_ads")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["money"] = money
templates.env.filters["pct"] = pct
templates.env.filters["clock"] = lambda t: cal._clock(t) if t else ""
templates.env.filters["hm"] = lambda t: f"{int(t[:2]) % 12 or 12}:{t[3:5]}" if t else ""


def compact(n):
    """12345 -> 12.3k, for tight spaces."""
    if n is None:
        return "—"
    n = float(n)
    for div, suf in ((1e6, "M"), (1e3, "k")):
        if abs(n) >= div:
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + suf
    return f"{n:,.0f}"


templates.env.filters["compact"] = compact


LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+")


def _loosen_lists(text):
    """Models often start a list right under a line of text; Markdown needs a blank line first."""
    out, prev = [], ""
    for line in text.splitlines():
        starts_list = LIST_ITEM.match(line) and not LIST_ITEM.match(prev) and not prev.lstrip().startswith("|")
        starts_table = line.lstrip().startswith("|") and not prev.lstrip().startswith("|")
        if prev.strip() and (starts_list or starts_table):
            out.append("")
        out.append(line)
        prev = line
    return "\n".join(out)


def render_md(text):
    # Escape any raw HTML from the model, then render Markdown (tables supported).
    return md.markdown(_loosen_lists(html.escape(text, quote=False)), extensions=["tables", "sane_lists"])


templates.env.filters["markdown"] = render_md
templates.env.filters["poss"] = lambda n: (n or "") + ("'" if (n or "").endswith("s") else "'s")


PUBLIC_URL = os.environ.get("ADS_PUBLIC_URL", "").rstrip("/")


@app.on_event("startup")
def _startup():
    db.init()
    users.init()
    places.init()
    places.purge_old()
    referrals.init()
    schools.init()
    from . import zips
    zips.init()
    field.init()
    salesforce.init()
    accounts.init()
    cal.init()
    try:
        db.seed_archive()          # reports read the daily archive; fill it from what's already synced
    except Exception:
        pass
    reviews.init()
    visibility.init()
    brand.init()
    images.init()
    studio.init()
    studio_flow.init()
    home_dash.init()
    intake.init()
    emailer.init()
    p360.init()
    try:
        brand.sync_websites("Search page")
    except Exception:
        pass
    if os.environ.get("ADS_DISABLE_SCHEDULER") != "1":
        studio.start_scheduler()
        studio_flow.start_scheduler()
        sync.start_scheduler()
        reviews.start_scheduler()
        emailer.start_worker()
        visibility.start_scheduler()
        places.start_scheduler()
        referrals.start_scheduler()
        schools.start_scheduler()
        if zips.due():
            zips.refresh_in_background()        # new Census numbers come out each December
        salesforce.start_scheduler(PUBLIC_URL)
        cal.start_scheduler()
        field.start_reminders(PUBLIC_URL)


SHARED = {"id": 0, "name": "Team password", "email": "", "role": "admin"}


def current_user(request: Request):
    """The signed-in person (fresh from the database, so role changes and removals apply at once).
    The old shared team password signs in as a temporary admin only until someone has set up an
    admin account."""
    try:
        s = request.session
    except AssertionError:
        return None
    uid = s.get("uid")
    if uid == 0:
        return SHARED if s.get("ok") is True and PASSWORD and not users.any_admin() else None
    if uid:
        u = users.get(uid)
        if u and u["active"] and u["pw_hash"] and u["gen"] == s.get("gen"):
            return u
    return None


def me(request: Request):
    return getattr(request.state, "user", None) or current_user(request)


def authed(request: Request):
    return me(request) is not None


def actor(request: Request):
    u = me(request)
    return (u or {}).get("name") or ""


def guard(request: Request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    return None


def flash(request, msg, kind="ok"):
    request.session["flash"] = {"msg": msg, "kind": kind}


def _static_version():
    """Changes whenever style.css changes, so browsers fetch the new one after an update."""
    try:
        return str(int(os.path.getmtime(os.path.join(os.path.dirname(__file__), "static", "style.css"))))
    except OSError:
        return "1"


STATIC_V = _static_version()


def page(request, name, **ctx):
    ctx["static_v"] = STATIC_V
    ctx["flash"] = request.session.pop("flash", None) or getattr(request.state, "camp_flash", None)
    ctx["ai_on"] = ai.available()
    ctx["last_sync"] = sync.last_sync()
    ctx["sync_running"] = sync.running()
    ctx["any_connection"] = cx.meta_configured() or cx.google_configured()
    ctx.setdefault("social_url", SOCIAL_URL)
    u = me(request)
    ctx["me"] = u
    ctx["can_edit"] = bool(u) and users.rank(u["role"]) >= users.rank("editor")
    ctx["is_admin"] = bool(u) and u["role"] == "admin"
    ctx["can_log"] = bool(u) and bool(u.get("field"))
    ctx["show_social"] = bool(u) and bool(u.get("social", 1))
    ctx["acc"] = users.access(u)
    ctx["home_url"] = users.home(u) if u else "/"
    ctx["my_practices"] = users.practice_ids(u)
    ctx.setdefault("inbox_open", reviews.open_count() if u and ctx["acc"].get("reviews") else 0)
    try:
        ctx.setdefault("field_alerts", dept.attention_count(u) if u else 0)
    except Exception:
        ctx["field_alerts"] = 0
    try:
        ctx.setdefault("studio_waiting", studio_flow.waiting_count(u["id"]) if u else 0)
    except Exception:
        ctx["studio_waiting"] = 0
    preview = request.query_params.get("season_preview", "") if ctx["is_admin"] else ""
    try:
        ctx["season"] = seasons.for_page(u, preview) if u else None
    except Exception:          # decoration only: never break a page over it
        ctx["season"] = None
    return templates.TemplateResponse(request, name, ctx)


# ---------- analysis view-model ----------
def month_bounds(key, today=None):
    """'YYYY-MM' → (first day, last day or today if it's this month)."""
    import calendar as _cal
    from datetime import date as _date
    today = today or _date.today()
    y, m = int(key[:4]), int(key[5:7])
    end = _date(y, m, _cal.monthrange(y, m)[1])
    return _date(y, m, 1), min(end, today)


def platform_view(practice, key, label, all_practices=None, month=None):
    """month: 'YYYY-MM' to show that month (the newest upload covering it, else the daily archive);
    None for the newest upload whatever its dates."""
    budget = practice.get(f"{key}_budget") or 0
    up = (db.latest_upload(practice["id"], key, "campaign", month)
          or db.latest_upload(practice["id"], key, "ad", month))
    archived_rows = None
    if month and not up:
        since, until = month_bounds(month)
        archived_rows = db.archived(practice["id"], key, since, until)
        if archived_rows:
            up = {"id": None, "platform": key, "level": "campaign", "source": "archive", "filename": "Daily archive",
                  "date_start": since.isoformat(), "date_end": max(r["date"] for r in archived_rows).isoformat(),
                  "uploaded_at": "", "row_count": len(archived_rows)}
    view = {"key": key, "label": label, "upload": up, "budget": budget, "campaigns": [], "findings": [],
            "totals": None, "pacing": {"available": False}, "search_terms": None, "search_upload": None,
            "filtered": scope.has_filter(practice), "camp_total": 0, "breakdown": None, "all_names": [],
            "boosted": scope.boosted_names() if key == "meta" else set()}
    if up:
        all_rows = archived_rows if archived_rows is not None else db.upload_rows(up["id"])
        rows = scope.apply(all_rows, practice, platform=key)
        view["camp_total"] = len({r["campaign"] for r in all_rows})
        view["all_names"] = sorted({r["campaign"] for r in all_rows if r.get("campaign")}, key=str.lower)
        if rows:
            camps, tot, findings = analyze_campaigns(rows)
            view.update(campaigns=camps, totals=tot, findings=findings, pacing=pacing(rows, budget))
        if key == "google" and up.get("date_start"):     # Google listing actions: clicks, kept apart from conversions
            try:
                lst = scope.apply(db.archived(practice["id"], "google", date.fromisoformat(up["date_start"][:10]),
                                              date.fromisoformat(up["date_end"][:10])), practice, platform="google")
                view["listing"] = round(sum(r.get("listing") or 0 for r in lst))
            except (ValueError, TypeError):
                pass
        if not view["filtered"] and all_practices is not None:
            view["breakdown"] = scope.breakdown(practice, key, all_rows, all_practices)
    st_up = db.latest_upload(practice["id"], key, "search_term", month)
    if st_up:
        st_rows = scope.apply(db.upload_rows(st_up["id"]), practice, platform=key)
        if st_rows:
            view["search_upload"] = st_up
            view["search_terms"] = analyze_search_terms(st_rows)
            if not up:  # search terms alone still give a spend picture
                camps, tot, _ = analyze_campaigns(st_rows)
                view.update(campaigns=camps, totals=tot)
    return view


def practice_views(p, with_breakdown=False, month=None):
    allp = db.practices() if with_breakdown else None
    return [platform_view(p, k, l, allp, month) for k, l in PLATFORMS]


def period_for(views):
    """The ad data's date range (so paid and organic cover the same days), else the sync window."""
    from datetime import date as _date
    ups = [v["upload"] for v in views if v.get("upload") and v["upload"].get("date_start")]
    if ups:
        return (min(_date.fromisoformat(u["date_start"]) for u in ups),
                max(_date.fromisoformat(u["date_end"] or u["date_start"]) for u in ups))
    return sync.sync_window()


def organic_for(p, views, allp):
    """Organic posts for the same window the nightly sync uses (this month to date; the whole
    previous month in the first 3 days), independent of when ad data was uploaded."""
    since, until = sync.sync_window()
    return organic.organic_view(p, allp, is_master(p, allp), since, until)


def suggest_social(p, items):
    """Pre-tick the Pages / Instagram accounts whose names look like the practice (only when none are linked)."""
    words = [w for w in re.findall(r"[a-z0-9]+", (p.get("campaign_filter") or p["name"]).lower()) if len(w) > 2]
    return [it["id"] for it in items if words and words[0] in it["name"].lower()]


def is_master(p, practices):
    """No campaign filter while other practices have one: this record shows everything."""
    return (not scope.has_filter(p) and bool(p.get("meta_account_id") or p.get("google_customer_id"))
            and any(scope.has_filter(q) for q in practices if q["id"] != p["id"]))


# ---------- auth ----------
def _login_page(request, error=None, email=""):
    shared = bool(PASSWORD) and not users.any_admin()
    return page(request, "login.html", error=error, shared=shared, email=email,
                configured=shared or users.any_admin())


def _sign_in(request, u):
    request.session.clear()
    request.session["uid"] = u["id"]
    request.session["gen"] = u.get("gen")


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if authed(request):
        return RedirectResponse("/", status_code=303)
    return _login_page(request)


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, email: str = Form(""), password: str = Form("")):
    email = email.strip()
    if email:
        u = users.check_login(email, password)
        if u:
            _sign_in(request, u)
            return RedirectResponse("/", status_code=303)
    elif PASSWORD and not users.any_admin() and hmac.compare_digest(password.encode(), PASSWORD.encode()):
        request.session.clear()
        request.session["uid"] = 0
        request.session["ok"] = True
        flash(request, "Signed in with the shared team password. Set up your own admin account on the "
                       "Team page; the shared password stops working once you have.")
        return RedirectResponse("/team", status_code=303)
    await asyncio.sleep(1.5)  # slow down guessing
    return _login_page(request, "That email and password don't match." if email else "That password isn't right.", email)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- setting a password from a link ----------
@app.get("/setup/{token}", response_class=HTMLResponse)
def setup_form(request: Request, token: str):
    u = users.by_token(token)
    return page(request, "setup.html", person=u, token=token, error=None,
                min_len=users.MIN_PASSWORD)


@app.post("/setup/{token}", response_class=HTMLResponse)
async def setup_save(request: Request, token: str, password: str = Form(""), confirm: str = Form("")):
    u = users.by_token(token)
    problem = users.password_problem(password, confirm) if u else None
    if not u or problem:
        if not u:
            await asyncio.sleep(1)
        return page(request, "setup.html", person=u, token=token, error=problem,
                    min_len=users.MIN_PASSWORD)
    u = users.finish_setup(token, password)
    _sign_in(request, u)
    flash(request, f"Welcome, {u['name'].split(' ')[0]}. You're signed in.")
    return RedirectResponse("/", status_code=303)


# ---------- your own account ----------
@app.get("/account", response_class=HTMLResponse)
def account(request: Request):
    if (r := guard(request)):
        return r
    return page(request, "account.html", error=None, labels=users.ROLE_LABELS, help=users.ROLE_HELP,
                min_len=users.MIN_PASSWORD, season_cfg=seasons.settings(), season_now=seasons.current()[0],
                season_labels=seasons.LABEL)


@app.post("/account/seasonal")
def account_seasonal(request: Request, on: str = Form("1")):
    if (r := guard(request)):
        return r
    u = me(request)
    if u and u["id"]:
        users.set_switch(u["id"], "seasonal", on == "1")
        flash(request, "Seasonal decorations are on for you." if on == "1" else "Seasonal decorations are off for you.")
    return RedirectResponse("/account#seasonal", status_code=303)


@app.post("/account/password", response_class=HTMLResponse)
def account_password(request: Request, current: str = Form(""), password: str = Form(""), confirm: str = Form("")):
    if (r := guard(request)):
        return r
    u = me(request)
    if not u["id"]:
        return RedirectResponse("/team", status_code=303)
    full = users.get(u["id"])
    problem = None if users._check_pw(current, full["pw_hash"]) else "Your current password isn't right."
    problem = problem or users.password_problem(password, confirm)
    if problem:
        return page(request, "account.html", error=problem, labels=users.ROLE_LABELS, help=users.ROLE_HELP,
                    min_len=users.MIN_PASSWORD, season_cfg=seasons.settings(), season_now=seasons.current()[0],
                    season_labels=seasons.LABEL)
    _sign_in(request, users.set_password(u["id"], password))
    flash(request, "Password changed. Other devices have been signed out.")
    return RedirectResponse("/account", status_code=303)


# ---------- people (admins) ----------
def _setup_url(request, token):
    base = PUBLIC_URL
    if not base:   # behind the Cloudflare tunnel the app itself sees plain http
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        base = f"{proto}://{request.headers.get('host') or request.url.netloc}"
    return f"{base}/setup/{token}"


@app.get("/team", response_class=HTMLResponse)
def team(request: Request):
    if (r := guard(request)):
        return r
    link = request.session.pop("team_link", None)
    season_rows, season_cfg, season_now, season_until = seasons.rows()
    people = users.all_users()
    for t in people:
        t["acc"] = users.access(t)
        t["pids"] = users.chosen_practices(t)
        t["color"] = users.cal_color(t)
        t["on_tabs"] = [label for k, label, _ in users.TABS if t["acc"][k]]
    return page(request, "team.html", people=people, departments=dept.DEPARTMENTS, dept_labels=dept.LABEL, tabs=users.TABS, extras=users.EXTRAS, all_practices=db.practices(),
                depts=users.DEPT_FLAGS, dept_owner_ids=p360.owners(),
                cal_colors=users.CAL_COLORS, open_access=request.query_params.get("open", ""), roles=users.ROLES, labels=users.ROLE_LABELS,
                help=users.ROLE_HELP, link=link, link_days=users.LINK_DAYS, shared=me(request)["id"] == 0,
                now=db.now(), season_rows=season_rows, season_cfg=season_cfg, season_now=season_now, season_until=season_until,
                season_labels=seasons.LABEL, season_choices=seasons.choices())


@app.post("/team/seasonal")
async def team_seasonal(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    bad = seasons.save({k: form.get(k) for k in form.keys()})
    if bad:
        flash(request, "Saved, but these dates weren't understood, so they kept their old ones: " + ", ".join(bad)
              + ". Use month-day, like 10-01.", "error")
    else:
        now, until = seasons.current()
        flash(request, "Seasonal look saved. " + (f"Showing now: {seasons.LABEL[now]}." if now else "Nothing is showing right now."))
    return RedirectResponse("/team#seasonal", status_code=303)


@app.post("/team/add")
def team_add(request: Request, name: str = Form(""), email: str = Form(""), role: str = Form("viewer"),
             field_on: str = Form(""), social_on: str = Form("")):
    if (r := guard(request)):
        return r
    try:
        u, token = users.add(name, email, role, bool(field_on), bool(social_on))
    except ValueError as e:
        flash(request, str(e), "error")
        return RedirectResponse("/team#add", status_code=303)
    request.session["team_link"] = {"name": u["name"], "url": _setup_url(request, token)}
    return RedirectResponse("/team", status_code=303)


@app.post("/team/{uid}/link")
def team_link(request: Request, uid: int):
    if (r := guard(request)):
        return r
    u = users.get(uid)
    if u:
        request.session["team_link"] = {"name": u["name"], "url": _setup_url(request, users.new_link(uid))}
    return RedirectResponse("/team", status_code=303)


@app.post("/team/{uid}/role")
def team_role(request: Request, uid: int, role: str = Form("")):
    if (r := guard(request)):
        return r
    try:
        users.set_role(uid, role)
        flash(request, "Access updated.")
    except ValueError as e:
        flash(request, str(e), "error")
    return RedirectResponse("/team", status_code=303)


@app.post("/team/{uid}/department")
def team_department(request: Request, uid: int, department: str = Form(""), lead: str = Form("")):
    if (r := guard(request)):
        return r
    x = users.get(uid)
    if not x:
        return RedirectResponse("/team", status_code=303)
    try:
        if department != (x.get("department") or ""):
            lead = ""                        # a new department starts as a member, not its lead
        dept.set_department(uid, department, bool(lead))
        y = users.get(uid)
        flash(request, f"{x['name']}: " + (f"{dept.LABEL[y['department']]}{' lead' if y.get('dept_lead') else ''}."
                                          if y.get("department") else "no department."))
    except dept.DeptError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/team#access-{uid}", status_code=303)


@app.post("/team/{uid}/active")
def team_active(request: Request, uid: int, active: str = Form("")):
    if (r := guard(request)):
        return r
    if uid == me(request)["id"] and not active:
        flash(request, "You can't switch off your own access.", "error")
        return RedirectResponse("/team", status_code=303)
    try:
        users.set_active(uid, bool(active))
        flash(request, "Access switched back on." if active else "Access switched off; they've been signed out.")
    except ValueError as e:
        flash(request, str(e), "error")
    return RedirectResponse("/team", status_code=303)


@app.post("/team/{uid}/switch/{name}")
def team_switch(request: Request, uid: int, name: str, on: str = Form("")):
    if (r := guard(request)):
        return r
    u = users.get(uid)
    if u and name in users.SWITCHES:
        users.set_switch(uid, name, bool(on))
        flash(request, f"{users.SWITCHES[name]} {'on' if on else 'off'} for {u['name']}.")
    return RedirectResponse("/team", status_code=303)


@app.post("/team/{uid}/access")
async def team_access(request: Request, uid: int):
    """What one person can open: tabs, reports, refreshing, everyone's visits and calendar, admins' activity,
    which practices, and their calendar colour."""
    if (r := guard(request)):
        return r
    u = users.get(uid)
    if not u:
        return RedirectResponse("/team", status_code=303)
    form = await request.form()
    try:
        if form.get("reset"):
            users.reset_access(uid)
            flash(request, f"{u['name']} is back to the {users.ROLE_LABELS[u['role']]} defaults.")
        else:
            flags = {k: bool(form.get(k)) for k in users.FLAGS}
            pids = [int(x) for x in form.getlist("practice") if str(x).isdigit()]
            color = form.get("cal_color") if form.get("own_color") else ""
            users.set_access(uid, flags, pids, color)
            flash(request, f"Saved what {u['name']} can open.")
    except ValueError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/team?open={uid}#access-{uid}", status_code=303)


@app.post("/team/{uid}/remove")
def team_remove(request: Request, uid: int):
    if (r := guard(request)):
        return r
    if uid == me(request)["id"]:
        flash(request, "You can't remove yourself.", "error")
        return RedirectResponse("/team", status_code=303)
    try:
        users.remove(uid)
        flash(request, "Removed.")
    except ValueError as e:
        flash(request, str(e), "error")
    return RedirectResponse("/team", status_code=303)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/internal/agent-context")
def agent_context(request: Request, ids: str = ""):
    """For the AI translator only (key = the translator's master key): brand kits for the
    channels picked in the Postiz Agent."""
    key = os.environ.get("AI_API_KEY", "")
    sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not key or not hmac.compare_digest(sent.encode(), key.encode()):
        return JSONResponse({"error": "not allowed"}, status_code=403)
    return agent.context([i for i in ids.split(",") if i.strip()])


# ---------- welcome ----------
SOCIAL_URL = os.environ.get("SOCIAL_URL", "https://social.mcdp.com")


def _ad_totals(allp):
    """Spend and leads for the welcome page: the master record if there is one (it holds every
    campaign), otherwise every practice added up. Returns (spend, leads, since, until) or None."""
    master = next((p for p in allp if is_master(p, allp)), None)
    pool = [master] if master else allp
    spend = leads = 0.0
    seen = False
    ups = []
    for p in pool:
        for v in practice_views(p):
            if v["totals"]:
                seen = True
                spend += v["totals"].get("spend") or 0
                leads += v["totals"].get("conversions") or 0
                if v["upload"]:
                    ups.append(v["upload"])
    if not seen:
        return None
    since, until = period_for([{"upload": u} for u in ups])
    return {"spend": spend, "leads": leads, "since": since, "until": until}


def _worth_a_look(allp, role="admin"):
    items = []
    waiting = [(p, len(db.changes(p["id"], ["proposed"]))) for p in allp]
    waiting = [(p, n) for p, n in waiting if n]
    if waiting:
        total = sum(n for _, n in waiting)
        first = waiting[0][0]
        where = first["name"] if len(waiting) == 1 else f"{len(waiting)} practices"
        whose = "your" if users.rank(role) >= users.rank("editor") else "an editor's"
        items.append({"text": f"{total} fix{'es' if total != 1 else ''} waiting for {whose} approval ({where})",
                      "href": f"/p/{first['id']}#fixes"})
    st = places.status()
    if st.get("busy"):
        items.append({"text": "The map is refreshing right now", "href": "/map"})
    elif st and st.get("ok") is False:
        items.append({"text": "The last map refresh stopped; have a look", "href": "/map"})
    elif places.offices() and not db.get_setting("ref_last_refresh"):
        items.append({"text": "Build the referral and competition lists once", "href": "/map/list"})
    waiting = reviews.open_count()
    if waiting:
        items.insert(0, {"text": "1 review or comment waiting for a reply" if waiting == 1
                         else f"{waiting} reviews and comments waiting for a reply", "href": "/reviews"})
    last = sync.last_sync().get("at")
    if last:
        from datetime import datetime as _dt
        t = _dt.fromisoformat(last)
        day = "today" if t.date() == _dt.now().date() else t.strftime("%A")
        items.append({"text": f"Ads synced {day} at {t.strftime('%-I:%M %p')}", "href": None})
    elif cx.meta_configured() or cx.google_configured():
        items.append({"text": "Ads haven't synced yet", "href": "/connections" if role == "admin" else None})
    return items[:3]


@app.get("/", response_class=HTMLResponse)
def welcome(request: Request, kind: str = "week", key: str = ""):
    """The master dashboard: from ad spend to starts, for a week or a month."""
    if (r := guard(request)):
        return r
    kind = "month" if kind == "month" else "week"
    d = home_dash.build(kind, key or None)
    try:
        d["wins"] = wins.build(d["rep"], d["ls"], me(request))
    except Exception:
        import logging; logging.getLogger("ads").exception("home wins")
        d["wins"] = None
    opts = master_report.week_options() if kind == "week" else master_report.month_options()
    if d["period"]["key"] not in [k for k, _ in opts]:
        opts = [(d["period"]["key"], d["period"]["label"])] + opts
    allp = db.practices()
    mapped = sum(c.get("referrer", 0) for c in places.counts().values())
    return page(request, "welcome.html", d=d, kind=kind, options=opts, social_url=SOCIAL_URL, mapped=mapped,
                look=_worth_a_look(db.keep_mine(allp), me(request)["role"]), waiting=reviews.open_count(),
                money=master_report.money, num=master_report.num, pct=master_report.pct)


@app.post("/dashboard/leadsigma")
async def dashboard_leadsigma(request: Request):
    """Upload LeadSigma's Marketing Attribution CSV export; only counts are kept."""
    if (r := guard(request)):
        return r
    form = await request.form()
    up = form.get("file")
    if up is None or not getattr(up, "filename", ""):
        flash(request, "Pick the CSV you exported from LeadSigma.", "error")
        return RedirectResponse("/#leadsigma", status_code=303)
    data = await up.read()
    if len(data) > 20 * 1024 * 1024:
        flash(request, "That file is over 20 MB.", "error")
        return RedirectResponse("/#leadsigma", status_code=303)
    def _d(k):
        try:
            return date.fromisoformat(form.get(k) or "")
        except ValueError:
            return None
    try:
        msg = home_dash.import_csv(data, os.path.basename(up.filename), actor(request), _d("start"), _d("end"))
        flash(request, msg + " Names, emails and phone numbers in the file weren't kept.")
    except home_dash.DashError as e:
        flash(request, str(e), "error")
    return RedirectResponse("/", status_code=303)


# ---- LeadSigma scheduled run (until its API is connected): an admin gets a one-use key, then the collector running on
# LeadSigma's Marketing Attribution page sends one CSV per day to /ls-in/import. Only counts are kept.
LS_ORIGIN = "https://onlinebooking.leadsigma.com"


@app.post("/dashboard/leadsigma-key")
def leadsigma_key(request: Request):
    u = me(request)
    if not u or u["role"] != "admin":
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"key": p360.import_key("ls_import_key"), "minutes": p360.IMPORT_MINUTES}


@app.get("/ls-in/collect.js")
def leadsigma_collect_js():
    path = os.path.join(os.path.dirname(__file__), "static", "ls-collect.js")
    with open(path, encoding="utf-8") as f:
        return Response(f.read(), media_type="application/javascript",
                        headers={"Access-Control-Allow-Origin": LS_ORIGIN, "Cache-Control": "no-store"})


@app.options("/ls-in/import")
def leadsigma_import_options():
    return Response(status_code=204, headers={"Access-Control-Allow-Origin": LS_ORIGIN, "Access-Control-Allow-Methods": "POST",
                                              "Access-Control-Allow-Headers": "Content-Type"})


@app.post("/ls-in/import")
async def leadsigma_import(request: Request, k: str = ""):
    h = {"Access-Control-Allow-Origin": LS_ORIGIN}
    if not p360.take_key(k, "ls_import_key"):
        return JSONResponse({"error": "The import key is missing, used or expired."}, status_code=403, headers=h)
    body = await request.body()
    if len(body) > 40 * 1024 * 1024:
        return JSONResponse({"error": "Too big."}, status_code=413, headers=h)
    try:
        res = home_dash.import_days(json.loads(body).get("days"), "Scheduled run")
    except (ValueError, KeyError, TypeError, AttributeError, home_dash.DashError) as e:
        return JSONResponse({"error": f"Couldn't read it: {e}"}, status_code=422, headers=h)
    db.set_setting("p360_hub_at", "")   # Practice 360's marketing lane picks the new bookings up on its next view
    return JSONResponse({"ok": True, **res}, headers=h)


@app.post("/dashboard/budget")
async def dashboard_budget(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    try:
        home_dash.set_budget(form, actor(request))
        flash(request, "Budget saved.")
    except home_dash.DashError as e:
        flash(request, str(e), "error")
    return RedirectResponse("/#budget", status_code=303)


# ---------- practices ----------
def setup_problems(p, eff, views, result):
    """Reasons a practice shows no ad numbers, for the practices list: [{'short', 'long'}]."""
    out = []
    names = dict(PLATFORMS) | {"facebook": "Facebook posts", "instagram": "Instagram posts"}
    for k, r in (result or {}).items():
        if isinstance(r, dict) and r.get("ok") is False:
            out.append({"short": f"{names.get(k, k)} sync failed", "at": "accounts", "long": f"{names.get(k, k)}: {r.get('error', 'the last sync failed')}"})
    linked = any(eff.get(k) for k in ("meta_account_id", "google_customer_id"))
    has_any = any(v["upload"] for v in views)
    if not linked and not has_any and not p.get("fb_page_id") and not p.get("ig_account_id"):
        out.append({"short": "Nothing linked yet", "at": "accounts", "long": "Not linked to an ad account, Facebook Page or Instagram yet. Link them in Practice setup."})
    elif not linked and not has_any:
        out.append({"short": "No ad account", "at": "accounts", "long": "No ad account linked, so there are no ad numbers. Link one in Practice setup, "
                                                     "or give it campaign filter words to use the master record's accounts."})
    # only for a platform the practice has a budget for: most practices simply don't run ads on both
    none = [v for v in views if v["upload"] and v["filtered"] and not v["campaigns"] and v["camp_total"]
            and (p.get(f"{v['key']}_budget") or 0) > 0]
    if none:
        out.append({"short": "Filter matches no campaigns", "at": "settings",
                    "long": " ".join(f"{v['label']}: its filter words match none of the {v['camp_total']} campaigns in the account."
                                     for v in none) + " Check the words in Practice setup."})
    return out


def organic_summary(p, allp, month=None):
    """A month's Facebook / Instagram post numbers for the practices list (default this month)."""
    since, until = month_bounds(month) if month else sync.sync_window()
    try:
        org = organic.organic_view(p, allp, is_master(p, allp), since, until)
    except Exception:
        return []
    return [n for n in org["networks"] if n.get("count")]


@app.get("/reports", response_class=HTMLResponse)
def master_reports(request: Request, kind: str = "month", key: str = ""):
    """Master Reports: every practice for a month, or a weekly snapshot."""
    if (r := guard(request)):
        return r
    kind = "week" if kind == "week" else "month"
    look = master_report.brand()
    rep = master_report.build(kind, key or None, pal=look["pal"])
    opts = master_report.week_options() if kind == "week" else master_report.month_options()
    if rep["period"]["key"] not in [k for k, _ in opts]:
        opts = [(rep["period"]["key"], rep["period"]["label"])] + opts
    return page(request, "master_report.html", rep=rep, options=opts, money=master_report.money, num=master_report.num,
                pct=master_report.pct, meter=master_report.meter, colors=look["pal"], cats_order=master_report.CATS,
                look=look)


@app.get("/practices", response_class=HTMLResponse)
def dashboard(request: Request, m: str = ""):
    if (r := guard(request)):
        return r
    month_opts = reviews.months_back(13)[::-1]           # newest first; this month is the default
    month = m if m in month_opts else month_opts[0]
    items = []
    allp = db.practices()
    eff = {q["id"]: q for q in scope.with_inherited(allp)}
    results = sync.last_sync().get("results", {})
    for p in db.keep_mine(allp):                         # someone limited to some practices sees just those
        views = practice_views(p, month=month)
        problems = setup_problems(p, eff[p["id"]], views, results.get(str(p["id"]), {}))
        waste = sum(f["amount"] for v in views for f in v["findings"] if f["kind"] in ("zero-conv-campaign", "high-cpa"))
        waste += sum((v["search_terms"] or {}).get("wasted_total", 0) for v in views)
        items.append({"p": p, "views": views, "waste": round(waste, 2), "master": is_master(p, allp),
                      "issues": sum(1 for v in views for f in v["findings"] if f["severity"] in ("high", "medium")),
                      "problems": problems, "organic": organic_summary(p, allp, month)})
    items.sort(key=lambda it: (not it["master"], it["p"]["name"].lower()))
    from . import tracking
    return page(request, "dashboard.html", items=items, kits_waiting=brand.waiting_review(allp), hidden=db.hidden_practices(),
                tracking_run=tracking.run_state(), google_ok=actions.google_ready(),
                month=month, is_current=month == month_opts[0], month_label=reviews.month_label(month),
                month_opts=[(k, reviews.month_label(k)) for k in month_opts])


@app.post("/tracking/fix-all")
def tracking_fix_all(request: Request):
    """Check every Google Ads account and fix its tracking where needed (in the background)."""
    if (r := guard(request)):
        return r
    from . import tracking
    if not actions.google_ready():
        flash(request, "Connect Google Ads first (Connections page).", "error")
    elif tracking.run_state().get("running"):
        flash(request, "Already going through the accounts; this page shows the progress.")
    else:
        tracking.fix_all_in_background(actor(request))
        flash(request, "Checking and fixing every Google Ads account. Refresh in a minute to see each one.")
    return RedirectResponse("/practices#tracking", status_code=303)


@app.post("/practices")
def create_practice(request: Request, name: str = Form(...), google_budget: float = Form(0),
                    meta_budget: float = Form(0), campaign_filter: str = Form("")):
    if (r := guard(request)):
        return r
    if not name.strip():
        flash(request, "Give the practice a name.", "error")
        return RedirectResponse("/practices", status_code=303)
    try:
        pid = db.add_practice(name, max(google_budget, 0), max(meta_budget, 0), campaign_filter[:500])
    except sqlite3.IntegrityError:
        flash(request, f"“{name.strip()}” already exists.", "error")
        return RedirectResponse("/practices", status_code=303)
    flash(request, f"Added {name.strip()}. Upload its first export below.")
    return RedirectResponse(f"/p/{pid}", status_code=303)


# ---------- practice ----------
def get_practice_or_404(pid):
    return db.practice(pid)


@app.get("/p/{pid}", response_class=HTMLResponse)
def practice_page(request: Request, pid: int, show: str = "all"):
    if (r := guard(request)):
        return r
    p = get_practice_or_404(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    allp = db.practices()
    views = practice_views(p, with_breakdown=True)
    show = show if show in ("all", "paid", "organic") else "all"
    org = organic_for(p, views, allp)
    fb_pages = sync.cached_list("fb_pages")
    ig_accounts = sync.cached_list("ig_accounts")
    included = {c["name"] for v in views for c in v["campaigns"]}
    add_options = sorted({n for v in views for n in v["all_names"]} - included, key=str.lower)
    return page(request, "practice.html", p=p, eff=scope.effective(p, allp), views=views, uploads=db.uploads(pid),
                report_colors=field_report.colors(pid), report_colors_set=all(field_report.saved_colors(pid)),
                master=is_master(p, allp), add_options=add_options,
                show=show, org=org, combo=organic.combined(views, org),
                social_status=sync.cached_list("social_status") or {},
                fb_pages=fb_pages, ig_accounts=ig_accounts,
                fb_linked=db.social_ids(p, "fb"), ig_linked=db.social_ids(p, "ig"),
                fb_suggest=[] if db.social_ids(p, "fb") else suggest_social(p, fb_pages),
                ig_suggest=[] if db.social_ids(p, "ig") else suggest_social(p, ig_accounts),
                ai_out={k: db.latest_ai(pid, k) for k in ai.TASKS},
                meta_accounts=sync.cached_accounts("meta"), google_accounts=sync.cached_accounts("google"),
                meta_on=cx.meta_configured(), google_on=google_ready(),
                assign_targets=[q for q in allp if q["id"] != pid],
                added=scope.added_campaigns(p), all_practices=allp,
                added_norm={scope.norm(n) for n in scope.added_campaigns(p)},
                fixes_open=[c for c in db.changes(pid, ["proposed", "failed", "stale"]) if c["kind"] != campaigns.KIND],
                fixes_done=db.changes(pid, ["applied", "undone"], limit=40),
                kind_labels={k: v for k, v in actions.KIND_LABELS.items() if k != campaigns.KIND},
                camp_drafts=[c for c in db.changes(pid, ["proposed", "failed", "stale"]) if c["kind"] == campaigns.KIND],
                camp_made=[c for c in db.changes(pid, ["applied"], limit=60) if c["kind"] == campaigns.KIND],
                camp_account=campaigns.account_for(p, allp), camp_name_word=campaigns.name_word(p),
                camp_addresses=campaigns.office_addresses(pid), camp_job=_camp_job_view(request, pid),
                camp_waiting=_campaigns_without_numbers(pid, views),
                ad_job=_ad_job_view(request, pid), writeup_job=_writeup_job_view(request, pid),
                writeup_labels=WRITEUP_LABELS,
                brand_logo=images.logo(pid), brand_photos=images.assets(pid, "photo"),
                brand_generated=images.assets(pid, "generated"), pickable=images.pickable(pid),
                group_findings=campaigns.group_findings,
                tools=tools.TOOLS, tool_groups=tools.GROUPS, tool_job=_tool_job_view(request, pid),
                tool_out={k: db.latest_ai(pid, tools.OUTPUT_PREFIX + k) for k in tools.TOOLS},
                kit=brand.kit(pid), brand_fields=brand.FIELDS, brand_sources=brand.sources(pid),
                kit_sources=[q for q in allp if q["id"] != pid and brand.has_kit(brand.kit(q["id"]))])


@app.post("/p/{pid}/settings")
def practice_settings(request: Request, pid: int, name: str = Form(...), google_budget: float = Form(0),
                      meta_budget: float = Form(0), notes: str = Form(""), campaign_filter: str = Form("")):
    if (r := guard(request)):
        return r
    try:
        db.update_practice(pid, name, max(google_budget, 0), max(meta_budget, 0), notes[:2000],
                           campaign_filter[:500])
        flash(request, "Saved.")
    except sqlite3.IntegrityError:
        flash(request, "Another practice already has that name.", "error")
    return RedirectResponse(f"/p/{pid}", status_code=303)


@app.post("/p/{pid}/delete")
def practice_delete(request: Request, pid: int):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    db.delete_practice(pid)
    flash(request, f"Deleted {p['name'] if p else 'practice'} and its uploads.")
    return RedirectResponse("/practices", status_code=303)


@app.post("/p/{pid}/hide")
def practice_hide(request: Request, pid: int):
    """Take a practice off every page, list and report. Nothing is deleted; Show again brings it all back."""
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    back = request.headers.get("referer") or "/practices"
    back = back if back.startswith(str(request.base_url)) and f"/p/{pid}" not in back else "/practices"
    if not p:
        return RedirectResponse("/practices", status_code=303)
    allp = db.practices()
    if is_master(p, allp):
        flash(request, "The master record can't be hidden: the practices below it get their ad accounts from it.", "error")
        return RedirectResponse(back, status_code=303)
    db.set_hidden(pid, True, actor(request))
    flash(request, f"Hid {p['name']}. It's off every page and report; its data is kept. "
                   "Bring it back from Hidden practices at the bottom of the Practices page.")
    return RedirectResponse(back, status_code=303)


@app.post("/p/{pid}/show")
def practice_show(request: Request, pid: int):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if p:
        db.set_hidden(pid, False)
        flash(request, f"{p['name']} is back on every page and report.")
    return RedirectResponse(request.headers.get("referer") or "/practices", status_code=303)


@app.post("/p/{pid}/split")
def practice_split(request: Request, pid: int, name: str = Form(...), campaign_filter: str = Form(...)):
    """From the master record: make a practice for a group of campaigns, sharing the same ad accounts
    and starting from the master's latest data, so it shows numbers straight away."""
    if (r := guard(request)):
        return r
    master = db.practice(pid)
    if not master or not name.strip() or not campaign_filter.strip():
        return RedirectResponse("/practices", status_code=303)
    try:
        new_id = db.add_practice(name, 0, 0, campaign_filter[:500], master.get("meta_account_id"),
                                 master.get("google_customer_id"))
    except sqlite3.IntegrityError:
        flash(request, f"A practice called “{name.strip()}” already exists. Add “{campaign_filter.strip()}” "
                       "to its campaign filter instead.", "error")
        return RedirectResponse(f"/p/{pid}#by-practice", status_code=303)
    db.copy_latest_uploads(pid, new_id)
    flash(request, f"Created {name.strip()}, showing campaigns containing “{campaign_filter.strip()}”. "
                   "Set its monthly budgets so pacing works.")
    return RedirectResponse(f"/p/{new_id}", status_code=303)


# ---------- adding campaigns to practices by exact name ----------
def _adopt_master_data(master, target):
    """A practice receiving campaigns from the master gets the same ad accounts (and, if it has
    no data yet, the master's latest data) so the campaigns show up straight away."""
    meta = target.get("meta_account_id") or master.get("meta_account_id") or ""
    goog = target.get("google_customer_id") or master.get("google_customer_id") or ""
    if (meta, goog) != (target.get("meta_account_id") or "", target.get("google_customer_id") or ""):
        db.set_practice_accounts(target["id"], meta, goog)
    if not db.uploads(target["id"]):
        db.copy_latest_uploads(master["id"], target["id"])


@app.post("/p/{pid}/assign")
async def assign_campaigns(request: Request, pid: int):
    """From the master record: add ticked campaigns to an existing practice."""
    if (r := guard(request)):
        return r
    form = await request.form()
    names = [n for n in form.getlist("campaign") if str(n).strip()]
    target = db.practice(int(form.get("target") or 0)) if str(form.get("target") or "").isdigit() else None
    master = db.practice(pid)
    if not master or not target or target["id"] == pid:
        flash(request, "Pick the practice to add the campaigns to.", "error")
    elif not names:
        flash(request, "Tick at least one campaign to add.", "error")
    else:
        n = db.add_campaigns(target["id"], names)
        _adopt_master_data(master, target)
        flash(request, f"Added {n} campaign{'s' if n != 1 else ''} to {target['name']}."
              + ("" if n == len(names) else " (Some were already there.)"))
    return RedirectResponse(f"/p/{pid}#by-practice", status_code=303)


@app.post("/p/{pid}/campaigns/add")
def practice_campaign_add(request: Request, pid: int, name: str = Form("")):
    if (r := guard(request)):
        return r
    if name.strip() and db.practice(pid):
        n = db.add_campaigns(pid, [name])
        flash(request, f"Added “{name.strip()}”." if n else "That campaign was already added.")
    return RedirectResponse(f"/p/{pid}#campaigns", status_code=303)


@app.post("/p/{pid}/campaigns/move")
def practice_campaign_move(request: Request, pid: int, name: str = Form(""), to: str = Form(""), back: str = Form("")):
    """Move a campaign to another practice (it then belongs only there), or back to automatic assignment."""
    if (r := guard(request)):
        return r
    here = f"/p/{pid}#campaigns-{back}" if back in ("meta", "google") else f"/p/{pid}"
    name = " ".join(name.split())
    if not name or not to:
        return RedirectResponse(here, status_code=303)
    for q in db.practices():
        if scope.norm(name) in {scope.norm(n) for n in scope.added_campaigns(q)}:
            db.remove_campaign(q["id"], name)
    if to == "auto":
        flash(request, f"“{name}” is back to automatic assignment.")
        return RedirectResponse(here, status_code=303)
    target = db.practice(int(to)) if to.isdigit() else None
    if not target:
        return RedirectResponse(here, status_code=303)
    db.add_campaigns(target["id"], [name])
    have = False
    for plat in ("meta", "google"):
        up = db.latest_upload(target["id"], plat, "campaign")
        if up and any(scope.norm(r["campaign"]) == scope.norm(name) for r in db.upload_rows(up["id"])):
            have = True
    if have:
        flash(request, f"Moved “{name}” to {target['name']}.")
    else:
        started = sync.start_in_background()
        flash(request, f"Moved “{name}” to {target['name']}. It runs in an ad account {target['name']} isn't linked to, "
                       + ("so a sync has started to bring its numbers over (Meta campaigns only); it shows there in a minute or two."
                          if started else "so it shows there after the sync that's running now (Meta campaigns only)."))
    return RedirectResponse(here, status_code=303)


@app.post("/p/{pid}/campaigns/remove")
def practice_campaign_remove(request: Request, pid: int, name: str = Form("")):
    if (r := guard(request)):
        return r
    db.remove_campaign(pid, name)
    flash(request, f"Removed “{name.strip()}” from this practice.")
    return RedirectResponse(f"/p/{pid}#campaigns", status_code=303)


@app.post("/p/{pid}/upload")
async def upload(request: Request, pid: int, file: UploadFile = File(...)):
    if (r := guard(request)):
        return r
    if not db.practice(pid):
        return RedirectResponse("/practices", status_code=303)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        flash(request, "That file is over 20 MB. Export a shorter date range.", "error")
        return RedirectResponse(f"/p/{pid}", status_code=303)
    try:
        parsed = parse_export(data, file.filename or "")
    except ParseError as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/p/{pid}", status_code=303)
    db.save_upload(pid, parsed, file.filename)
    kind = {"campaign": "campaign", "search_term": "search terms", "ad": "ad-level"}[parsed["level"]]
    label = dict(PLATFORMS)[parsed["platform"]]
    msg = f"Read {len(parsed['rows'])} {kind} rows from a {label} export."
    if parsed["warnings"]:
        msg += " " + " ".join(parsed["warnings"])
    flash(request, msg)
    return RedirectResponse(f"/p/{pid}", status_code=303)


@app.post("/p/{pid}/uploads/{uid}/delete")
def upload_delete(request: Request, pid: int, uid: int):
    if (r := guard(request)):
        return r
    db.delete_upload(pid, uid)
    flash(request, "Upload removed.")
    return RedirectResponse(f"/p/{pid}#uploads", status_code=303)


@app.post("/p/{pid}/ai/{kind}")
async def ai_generate(request: Request, pid: int, kind: str):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p or kind not in ai.TASKS:
        return RedirectResponse("/practices", status_code=303)
    views = practice_views(p, with_breakdown=True)
    org = organic_for(p, views, db.practices())
    if not any(v["totals"] for v in views) and not (org.get("totals") or {}).get("count"):
        flash(request, "No numbers for this practice yet: upload an export or sync, and check its campaign filter.",
              "error")
        return RedirectResponse(f"/p/{pid}", status_code=303)
    # Write-ups can take longer than the Cloudflare tunnel waits (100 s), so they run in the background and
    # the page refreshes itself until the write-up is there.
    j = WRITEUP_JOBS.get(pid)
    if j and j["state"] == "running" and _time.time() - j["started"] < CAMP_JOB_MAX:
        flash(request, "A write-up is already being written for this practice; it appears here when ready.")
        return RedirectResponse(f"/p/{pid}#ai", status_code=303)
    if not ai.available():
        flash(request, "AI isn't switched on for the Ads page. Run: sh enable-ads.sh", "error")
        return RedirectResponse(f"/p/{pid}#ai", status_code=303)
    brand_text = brand.context(brand.kit(pid)) if kind == "creative" else ""
    context = ai.build_context(p, views, org, brand_text)
    WRITEUP_JOBS[pid] = {"state": "running", "kind": kind, "started": _time.time(), "msg": ""}
    if os.environ.get("ADS_SYNC_JOBS") == "1":
        _run_writeup(pid, kind, context)
    else:
        _threading.Thread(target=_run_writeup, args=(pid, kind, context), daemon=True).start()
    return RedirectResponse(f"/p/{pid}#ai", status_code=303)


WRITEUP_JOBS = {}       # practice id -> {"state", "kind", "msg", "started"}
WRITEUP_LABELS = {"fixes": "Fix list", "creative": "Ad ideas", "report": "Monthly report"}


def _run_writeup(pid, kind, context):
    try:
        text = ai.generate(kind, context)
        db.save_ai(pid, kind, text)
        msg = (f"{WRITEUP_LABELS[kind]} written." + (" Open it with “Open last report”." if kind == "report" else ""))
        WRITEUP_JOBS[pid] = {"state": "done", "kind": kind, "started": _time.time(), "msg": msg}
    except ai.AIError as e:
        WRITEUP_JOBS[pid] = {"state": "error", "kind": kind, "started": _time.time(), "msg": str(e)}
    except Exception as e:      # never leave the page stuck on "Writing…"
        WRITEUP_JOBS[pid] = {"state": "error", "kind": kind, "started": _time.time(),
                             "msg": f"Something went wrong writing the {WRITEUP_LABELS[kind].lower()}: {e}"}


def _writeup_job_view(request, pid):
    j = WRITEUP_JOBS.get(pid)
    if not j:
        return None
    if j["state"] == "running" and _time.time() - j["started"] > CAMP_JOB_MAX:
        j.update(state="error", msg="The write-up took too long and was stopped. Try again in a minute.")
    if j["state"] == "running":
        return j
    WRITEUP_JOBS.pop(pid, None)
    if not getattr(request.state, "camp_flash", None):
        request.state.camp_flash = {"msg": j["msg"], "kind": "ok" if j["state"] == "done" else "error"}
    return None



# ---------- fixes: proposed in the page, applied in Google / Meta only on click ----------
def _fix_back(pid, back=""):
    return RedirectResponse(f"/p/{pid}#{'new-campaign' if back == 'campaign' else 'fixes'}", status_code=303)


@app.post("/p/{pid}/fixes/prepare")
async def fixes_prepare(request: Request, pid: int):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    n, notes = await asyncio.to_thread(actions.prepare, p, practice_views(p))
    msg = (f"{n} change{'s' if n != 1 else ''} ready to review." if n
           else "Nothing to change right now: no wasted searches to block, campaigns to pause or budgets off track.")
    bad = [x for x in notes if "Google:" in x[:8] or "Meta:" in x[:6]]
    flash(request, " ".join([msg, *notes]), "error" if bad and not n else "ok")
    return _fix_back(pid)


@app.post("/p/{pid}/fixes/apply")
async def fixes_apply(request: Request, pid: int):
    if (r := guard(request)):
        return r
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    done, problems, warnings, turned_on, made_campaigns = 0, [], [], 0, 0
    live = form.get("live") == "1"
    for cid in ids:
        ch = db.change(cid)
        if not ch or ch["practice_id"] != pid:
            continue
        ok, msg = await asyncio.to_thread(actions.apply_change, ch, actor(request), live)
        if ok:
            done += 1
            if live and ch["kind"] in actions.LIVE_KINDS:
                turned_on += 1
            if ch["kind"] == campaigns.KIND:
                made_campaigns += 1
            if msg:
                warnings.append(msg)
        else:
            problems.append(f"{ch['title']}: {msg}")
    made_campaigns_synced = bool(made_campaigns) and os.environ.get("ADS_DISABLE_SCHEDULER") != "1" \
        and sync.start_in_background()
    msg = f"Applied {done} change{'s' if done != 1 else ''} in Google / Meta." if done else "Nothing was applied."
    if turned_on:
        it = "them" if turned_on != 1 else "it"
        msg += (f" Turned on {turned_on} new ad{'s' if turned_on != 1 else ''} or campaign{'s' if turned_on != 1 else ''}; "
                f"{it} start{'' if turned_on != 1 else 's'} once Google / Meta approve {it} (usually within a day).")
    if made_campaigns:
        msg += (" New campaigns count for this practice straight away; their numbers appear here once they start "
                "getting impressions" + (" (a sync has started)." if made_campaigns_synced else "."))
    if warnings:
        msg += " " + warnings[0]
    if problems:
        msg += f" {len(problems)} didn't go through — " + problems[0] + (" (see the list for the rest)" if len(problems) > 1 else "")
    flash(request, msg, "error" if problems else "ok")
    return _fix_back(pid, form.get("back", ""))


@app.post("/p/{pid}/fixes/{cid}/live")
async def fixes_live(request: Request, pid: int, cid: int, on: str = Form(""), back: str = Form("")):
    """Turn a new ad or campaign created here on or off, without going to Google Ads or Ads Manager."""
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if ch and ch["practice_id"] == pid:
        turn_on = on == "1"
        ok, msg = await asyncio.to_thread(actions.set_live, ch, turn_on, actor(request))
        if ok:
            flash(request, (f"Turned on: {ch['title'].replace(' (created paused)', '')}. It starts once "
                            f"{'Google' if ch['platform'] == 'google' else 'Meta'} approves it (usually within a day)."
                            if turn_on else f"Paused: {ch['title'].replace(' (created paused)', '')}."))
        else:
            flash(request, f"Couldn't {'turn it on' if turn_on else 'pause it'}: {msg}", "error")
    return _fix_back(pid, back)


@app.post("/p/{pid}/fixes/{cid}/undo")
async def fixes_undo(request: Request, pid: int, cid: int, back: str = Form("")):
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if ch and ch["practice_id"] == pid:
        ok, msg = await asyncio.to_thread(actions.undo_change, ch, actor(request))
        flash(request, f"Undone: {ch['title']}." if ok else f"Couldn't undo: {msg}", "ok" if ok else "error")
    return _fix_back(pid, back)


@app.post("/p/{pid}/fixes/{cid}/dismiss")
def fixes_dismiss(request: Request, pid: int, cid: int, back: str = Form("")):
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if ch and ch["practice_id"] == pid and ch["status"] in ("proposed", "failed", "stale"):
        db.update_change(cid, status="dismissed")
    return _fix_back(pid, back)


def _polish_note(pl):
    if not pl or not pl.get("score"):
        return "."
    applied = len((pl.get("fix_log") or {}).get("applied") or [])
    before = (pl.get("score_before") or {}).get("overall")
    sc = pl["score"]["overall"]
    if applied and before is not None:
        return f", scored and improved: {before:g} → {sc:g}/10 after {applied} fix{'es' if applied != 1 else ''}."
    return f" and scored {sc:g}/10."


AD_JOBS = {}            # practice id -> {"state", "msg", "started"}


def _ad_job_view(request, pid):
    j = AD_JOBS.get(pid)
    if not j:
        return None
    if j["state"] == "running" and _time.time() - j["started"] > CAMP_JOB_MAX:
        j.update(state="error", msg="Writing the ads took too long and was stopped. Try again.")
    if j["state"] == "running":
        return j
    AD_JOBS.pop(pid, None)
    if not getattr(request.state, "camp_flash", None):
        request.state.camp_flash = {"msg": j["msg"], "kind": "ok" if j["state"] == "done" else "error"}
    return None


def _run_ad_job(pid, platform, campaign, focus, count):
    p = db.practice(pid)
    kit = brand.kit(pid)
    made, err, notes = 0, "", []
    for _ in range(count):
        try:
            prop = actions.draft_ad(p, platform, campaign, ai.generate_json, kit, focus)
            made += 1
            AD_JOBS[pid]["msg"] = f"scoring {made}"
            pl = tools.polish(p, prop["id"])
            if pl and pl.get("score"):
                notes.append(f"{pl['score']['overall']:g}")
        except (actions.ActionError, ai.AIError, cx.ConnectorError) as e:
            err = str(e)
            break
        except Exception as e:
            err = f"Something went wrong: {e}"
            break
    if made:
        msg = (f"{made} new ads" if made > 1 else "New ad") + f" written for {campaign}" + \
              (" with the brand kit" if brand.has_kit(kit) else "") + \
              (f", scored and fixed automatically (now {', '.join(notes)}/10)" if notes else "") + \
              ". Review below (edit if needed), then tick and Apply to create " + ("them" if made > 1 else "it") + \
              " paused." + (f" One more didn't work: {err}" if err else "")
        AD_JOBS[pid] = {"state": "done", "started": _time.time(), "msg": msg}
    else:
        AD_JOBS[pid] = {"state": "error", "started": _time.time(), "msg": err or "No ad was written."}


@app.post("/p/{pid}/fixes/draft")
async def fixes_draft(request: Request, pid: int, target: str = Form(""), focus: str = Form(""),
                      count: int = Form(1)):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    platform, _, campaign = target.partition("|")
    if not p or platform not in ("google", "meta") or not campaign:
        flash(request, "Pick a campaign for the new ad.", "error")
        return _fix_back(pid)
    j = AD_JOBS.get(pid)
    if j and j["state"] == "running":
        flash(request, "Ads are already being written for this practice; they appear here when ready.")
        return _fix_back(pid)
    count = min(max(count, 1), 3)
    AD_JOBS[pid] = {"state": "running", "started": _time.time(), "msg": ""}
    if os.environ.get("ADS_SYNC_JOBS") == "1":
        _run_ad_job(pid, platform, campaign, focus, count)
    else:
        _threading.Thread(target=_run_ad_job, args=(pid, platform, campaign, focus, count), daemon=True).start()
    return _fix_back(pid)


@app.post("/p/{pid}/drafts/{cid}/apply-fixes")
def draft_apply_fixes(request: Request, pid: int, cid: int):
    """Put the score's suggested fixes into the draft (the draft only; nothing is sent to Google or Meta)."""
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if not ch or ch["practice_id"] != pid or ch["kind"] not in ("draft_ad", campaigns.KIND) \
            or ch["status"] not in ("proposed", "failed", "stale"):
        return _fix_back(pid)
    pl, applied, skipped = tools.apply_fixes(ch, brand.kit(pid))
    db.update_change(cid, payload=pl)
    anchor = f"camp-{cid}" if ch["kind"] == campaigns.KIND else f"fix-{cid}"
    if applied:
        flash(request, f"Applied {len(applied)} fix{'es' if len(applied) != 1 else ''} to the draft"
              + (f"; skipped {len(skipped)} (see the draft)" if skipped else "") + ". Score it again to check.")
    else:
        flash(request, "No fixes could be applied: " + "; ".join(f"“{s['change']}”: {s['why']}" for s in skipped[:3]), "error")
    return RedirectResponse(f"/p/{pid}#{anchor}", status_code=303)


EDIT_FIELDS = ("name", "daily_budget", "max_cpc", "radius", "addresses", "final_url", "ad_group", "keywords",
               "negatives", "headlines", "descriptions", "review", "primary_text", "headline", "description")


def _keep_unsaved(ch, form, err):
    """An edit that can't be saved yet: keep exactly what was typed, with the reason, on the draft itself, so
    the edit box reopens with the person's text and the problem right there (not lost at the top of the page)."""
    typed = {k: str(form.get(k) or "") for k in EDIT_FIELDS if k in form}
    db.update_change(ch["id"], payload={**ch["payload"], "unsaved": {"values": typed, "error": str(err)}})


@app.post("/p/{pid}/drafts/{cid}/discard-edit")
def discard_edit(request: Request, pid: int, cid: int):
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if ch and ch["practice_id"] == pid and ch["payload"].get("unsaved"):
        pl = dict(ch["payload"])
        pl.pop("unsaved", None)
        db.update_change(cid, payload=pl)
    anchor = f"camp-{cid}" if ch and ch["kind"] == campaigns.KIND else f"fix-{cid}"
    return RedirectResponse(f"/p/{pid}#{anchor}", status_code=303)


@app.post("/p/{pid}/fixes/{cid}/edit")
async def fixes_edit(request: Request, pid: int, cid: int):
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if not ch or ch["practice_id"] != pid:
        return _fix_back(pid)
    form = await request.form()
    split = lambda k: [x for x in str(form.get(k) or "").replace("\r", "").split("\n")]
    values = {"headlines": split("headlines"), "descriptions": split("descriptions"),
              "primary_text": form.get("primary_text", ""), "headline": form.get("headline", ""),
              "description": form.get("description", "")}
    try:
        payload = actions.edit_draft(ch, values, brand.kit(pid))
    except actions.ActionError as e:
        _keep_unsaved(ch, form, e)
        return RedirectResponse(f"/p/{pid}#fix-{cid}", status_code=303)
    payload.pop("unsaved", None)
    db.update_change(cid, payload=payload, status="proposed", error="")
    flash(request, "Ad text saved. Tick it and Apply to create it (paused).")
    return RedirectResponse(f"/p/{pid}#fix-{cid}", status_code=303)


# ---------- brand-new Google Search campaigns (drafted from the brand kit, created paused on Apply) ----------
# Writing a campaign can take longer than the Cloudflare tunnel waits (100 s), so it runs in the
# background; the page shows "Writing…" and refreshes itself until it's done.
import threading as _threading
import time as _time

CAMP_JOBS = {}          # practice id -> {"state": "running"|"done"|"error", "msg": str, "started": float}
CAMP_JOB_MAX = 6 * 60   # a job older than this is treated as lost (e.g. the app restarted)


def camp_job(pid):
    j = CAMP_JOBS.get(pid)
    if j and j["state"] == "running" and _time.time() - j["started"] > CAMP_JOB_MAX:
        j.update(state="error", msg="Writing the campaign took too long and was stopped. Try again.")
    return j


def _run_camp_job(pid, kwargs):
    p = db.practice(pid)
    try:
        prop = campaigns.draft(p, ai.generate_json, practices=db.practices(), **kwargs)
        CAMP_JOBS[pid]["msg"] = "scoring"
        pl = tools.polish(p, prop["id"])
        CAMP_JOBS[pid] = {"state": "done", "started": _time.time(), "msg": "Campaign drafted" + _polish_note(pl) +
                          " Check every part below (edit if needed), then tick it and click Create to make it, paused, "
                          "in Google Ads."}
    except (campaigns.CampaignError, actions.ActionError, ai.AIError, cx.ConnectorError) as e:
        CAMP_JOBS[pid] = {"state": "error", "started": _time.time(), "msg": str(e)}
    except Exception as e:      # never leave the page stuck on "Writing…"
        CAMP_JOBS[pid] = {"state": "error", "started": _time.time(), "msg": f"Something went wrong writing the campaign: {e}"}


@app.post("/p/{pid}/campaign/new")
async def campaign_new(request: Request, pid: int):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    back = RedirectResponse(f"/p/{pid}#new-campaign", status_code=303)
    j = camp_job(pid)
    if j and j["state"] == "running":
        flash(request, "A campaign is already being written for this practice; it appears here when ready.")
        return back
    f = await request.form()
    addrs = [x.strip() for x in str(f.get("addresses") or "").replace("\r", "").split("\n") if x.strip()]
    kwargs = dict(focus=f.get("focus", ""), daily_budget=f.get("daily_budget"), max_cpc=f.get("max_cpc") or None,
                  radius=f.get("radius") or 10, addresses=addrs, landing_url=f.get("final_url", ""))
    try:                                   # quick checks now, so mistakes show straight away
        campaigns._money(kwargs["daily_budget"], 1, 1000, "Daily budget")
        if kwargs["max_cpc"]:
            campaigns._money(kwargs["max_cpc"], 0.1, 100, "Max cost per click")
        campaigns._radius(kwargs["radius"])
        if not campaigns.account_for(p):
            raise campaigns.CampaignError("Link this practice (or its master record) to a Google Ads account first, "
                                          "under Linked accounts.")
        if not ai.available():
            raise campaigns.CampaignError("AI isn't switched on for the Ads page. Run: sh enable-ads.sh")
    except campaigns.CampaignError as e:
        flash(request, str(e), "error")
        return back
    CAMP_JOBS[pid] = {"state": "running", "started": _time.time(), "msg": ""}
    if os.environ.get("ADS_SYNC_JOBS") == "1":          # tests: run in line
        _run_camp_job(pid, kwargs)
    else:
        _threading.Thread(target=_run_camp_job, args=(pid, kwargs), daemon=True).start()
    return back


@app.post("/p/{pid}/campaign/{cid}/edit")
async def campaign_edit(request: Request, pid: int, cid: int):
    if (r := guard(request)):
        return r
    ch, p = db.change(cid), db.practice(pid)
    if not ch or not p or ch["practice_id"] != pid:
        return RedirectResponse(f"/p/{pid}#new-campaign", status_code=303)
    form = await request.form()
    try:
        payload = campaigns.edit(ch, form, brand.kit(pid), p)
    except campaigns.CampaignError as e:
        _keep_unsaved(ch, form, e)
        return RedirectResponse(f"/p/{pid}#camp-{cid}", status_code=303)
    payload.pop("unsaved", None)
    db.update_change(cid, payload=payload, status="proposed", error="",
                     title=f"New Search campaign “{payload['name']}” (created paused)",
                     detail=campaigns.summary_detail(payload))
    flash(request, "Campaign saved. Tick it and Apply to create it (paused).")
    return RedirectResponse(f"/p/{pid}#camp-{cid}", status_code=303)


def _campaigns_without_numbers(pid, views):
    """Campaigns created here that don't show in the synced numbers yet (no impressions so far)."""
    have = {scope.norm(c["name"]) for v in views if v["key"] == "google" for c in v["campaigns"]}
    out = []
    for ch in db.changes(pid, ["applied"], limit=60):
        if ch["kind"] == campaigns.KIND and scope.norm(ch["payload"]["name"]) not in have:
            out.append({"name": ch["payload"]["name"], "live": bool((ch["result"] or {}).get("live")),
                        "budget": ch["payload"].get("daily_budget")})
    return out


def _camp_job_view(request, pid):
    """Running -> shown with a self-refresh; finished -> its message shown once, as a flash."""
    j = camp_job(pid)
    if not j:
        return None
    if j["state"] == "running":
        return j
    CAMP_JOBS.pop(pid, None)
    request.state.camp_flash = {"msg": j["msg"], "kind": "ok" if j["state"] == "done" else "error"}
    return None


# ---------- ad images: logo, photo library, AI images, and picking them for drafts ----------
def _img_back(pid, anchor="brand"):
    return RedirectResponse(f"/p/{pid}#{anchor}", status_code=303)


@app.post("/p/{pid}/brand/images/upload")
async def brand_images_upload(request: Request, pid: int):
    if (r := guard(request)):
        return r
    if not db.practice(pid):
        return RedirectResponse("/practices", status_code=303)
    form = await request.form()
    kind = "logo" if form.get("kind") == "logo" else "photo"
    files = [f for f in form.getlist("files") if getattr(f, "filename", "")]
    added, errs = 0, []
    for f in files[:30]:
        data = await f.read(images.MAX_UPLOAD + 1)
        try:
            await asyncio.to_thread(images.add_upload, pid, data, f.filename, kind, actor(request))
            added += 1
        except images.ImageError as e:
            errs.append(f"{f.filename}: {e}")
    if kind == "logo":
        flash(request, "Logo saved. Meta ad images get it, small, in a corner." if added else (errs or ["Choose a file."])[0],
              "ok" if added else "error")
    else:
        msg = f"Added {added} photo{'s' if added != 1 else ''} to the library." if added else "No photos added."
        if errs:
            msg += " " + errs[0] + (f" (and {len(errs) - 1} more)" if len(errs) > 1 else "")
        flash(request, msg, "error" if errs and not added else "ok")
    return _img_back(pid, "brand-images")


@app.post("/p/{pid}/brand/images/generate")
async def brand_images_generate(request: Request, pid: int, idea: str = Form(""), back: str = Form(""),
                                focus: str = Form("")):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    try:
        ids = await asyncio.to_thread(images.generate, p, focus or idea, 3, None, actor(request), idea.strip()[:300])
        flash(request, f"Made {len(ids)} AI images. They're in the library below each draft and under Brand kit → "
                       "Ad images; pick the ones to use.")
    except images.ImageError as e:
        flash(request, str(e), "error")
    anchor = back if re.match(r"^(fix|camp)-\d+$", back or "") else "brand-images"
    return _img_back(pid, anchor)


@app.post("/p/{pid}/brand/images/{aid}/delete")
def brand_images_delete(request: Request, pid: int, aid: int):
    if (r := guard(request)):
        return r
    images.delete(pid, aid)
    flash(request, "Removed from the library. Ads already created keep their copy.")
    return _img_back(pid, "brand-images")


@app.get("/brand-images/{aid}")
def brand_image_file(request: Request, aid: int, v: str = ""):
    if (r := guard(request)):
        return r
    a = images.asset(aid)
    if not a or not images.path(a):
        return Response(status_code=404)
    if request.query_params.get("full") == "1":        # the Studio editor places the original
        from fastapi.responses import FileResponse
        return FileResponse(images.path(a), headers={"Cache-Control": "private, max-age=86400"})
    try:
        if v in images.VARIANTS:
            data = images.render(a, v, images.logo(a["practice_id"]))
        else:
            from PIL import Image
            import io as _io
            img = Image.open(images.path(a))
            img.thumbnail((480, 480))
            buf = _io.BytesIO()
            if img.mode in ("RGBA", "LA"):
                img.save(buf, "PNG")
                return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
            img.convert("RGB").save(buf, "JPEG", quality=82)
            data = buf.getvalue()
    except (images.ImageError, OSError):
        return Response(status_code=404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.post("/p/{pid}/drafts/{cid}/images")
async def draft_images(request: Request, pid: int, cid: int):
    """Pick the images for a drafted Google campaign (up to 4) or Meta ad (one)."""
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if not ch or ch["practice_id"] != pid or ch["status"] not in ("proposed", "failed", "stale"):
        return _fix_back(pid)
    form = await request.form()
    pl = dict(ch["payload"])
    picked = form.getlist("img")
    if ch["kind"] == campaigns.KIND:
        pl["images"] = images.clean_selection(pid, picked, images.GOOGLE_MAX_IMAGES)
        anchor, n = f"camp-{cid}", len(pl["images"])
        msg = f"{n} image{'s' if n != 1 else ''} will be added to the campaign." if n else "No images will be added."
    elif ch["kind"] == "draft_ad" and ch["platform"] == "meta":
        sel = images.clean_selection(pid, picked, 1)
        pl["image_id"] = sel[0] if sel else None
        anchor = f"fix-{cid}"
        msg = ("The new ad will use this image, with the logo in the corner." if sel else
               f"The new ad will reuse the image of “{pl.get('copied_from') or 'the existing ad'}”.")
    else:
        return _fix_back(pid)
    db.update_change(cid, payload=pl)
    flash(request, msg)
    return RedirectResponse(f"/p/{pid}#{anchor}", status_code=303)


@app.post("/p/{pid}/campaign/{cid}/review-and-create")
async def campaign_review_create(request: Request, pid: int, cid: int):
    """One click for the usual case: every flagged item can be appealed, so ask Google to review and create."""
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if not ch or ch["practice_id"] != pid or ch["kind"] != campaigns.KIND:
        return RedirectResponse(f"/p/{pid}#new-campaign", status_code=303)
    pl = dict(ch["payload"])
    if not pl.get("flagged") or not all(f["exemptible"] for f in pl["flagged"]):
        flash(request, "Some flagged items can't be appealed; remove them under Edit the campaign first.", "error")
        return RedirectResponse(f"/p/{pid}#camp-{cid}", status_code=303)
    pl["review"] = True
    db.update_change(cid, payload=pl, status="proposed")
    ok, msg = await asyncio.to_thread(actions.apply_change, db.change(cid), actor(request))
    if ok:
        flash(request, "Created, paused, with a review request for the flagged items. Google usually marks them "
                       "“Eligible (limited)” within a day or so; check the campaign in Google Ads before switching it on."
              + (f" {msg}" if msg else ""))
    else:
        flash(request, f"Didn't go through: {msg}", "error")
    return RedirectResponse(f"/p/{pid}#new-campaign", status_code=303)


# ---------- tools adapted from OpenAdKit: Score this ad, optimizers, research, social & leads ----------
@app.post("/p/{pid}/drafts/{cid}/score")
async def draft_score(request: Request, pid: int, cid: int):
    if (r := guard(request)):
        return r
    ch, p = db.change(cid), db.practice(pid)
    if not ch or not p or ch["practice_id"] != pid or ch["kind"] not in ("draft_ad", campaigns.KIND):
        return _fix_back(pid)
    anchor = f"camp-{cid}" if ch["kind"] == campaigns.KIND else f"fix-{cid}"
    try:
        result = await asyncio.to_thread(tools.score, p, ch)
        db.update_change(cid, payload={**ch["payload"], "score": result})
        flash(request, f"Scored {result['overall']:g}/10: {result['tier_label']}.")
    except ai.AIError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/p/{pid}#{anchor}", status_code=303)


TOOL_JOBS = {}          # practice id -> {"state", "kind", "msg", "started"}


def _tool_job(pid):
    j = TOOL_JOBS.get(pid)
    if j and j["state"] == "running" and _time.time() - j["started"] > CAMP_JOB_MAX:
        j.update(state="error", msg="The tool took too long and was stopped. Try again.")
    return j


def _tool_job_view(request, pid):
    j = _tool_job(pid)
    if not j:
        return None
    if j["state"] == "running":
        return j
    TOOL_JOBS.pop(pid, None)
    if not getattr(request.state, "camp_flash", None):
        request.state.camp_flash = {"msg": j["msg"], "kind": "ok" if j["state"] == "done" else "error"}
    return None


def _run_tool_job(pid, kind, form, by):
    p = db.practice(pid)
    try:
        tools.run(kind, p, practice_views(p), form, by)
        title = tools.TOOLS[kind]["title"]
        TOOL_JOBS[pid] = {"state": "done", "kind": kind, "started": _time.time(),
                          "msg": f"{title}: done. It's below under Tools." + (
                              " The keywords are waiting under Make the fixes." if kind == "keywords" else "")}
    except (ai.AIError, brand.BrandError, actions.ActionError, cx.ConnectorError) as e:
        TOOL_JOBS[pid] = {"state": "error", "kind": kind, "started": _time.time(), "msg": str(e)}
    except Exception as e:
        TOOL_JOBS[pid] = {"state": "error", "kind": kind, "started": _time.time(), "msg": f"Something went wrong: {e}"}


@app.post("/p/{pid}/tools/{kind}")
async def tool_run(request: Request, pid: int, kind: str):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p or kind not in tools.TOOLS:
        return RedirectResponse("/practices", status_code=303)
    back = RedirectResponse(f"/p/{pid}#tool-{kind}", status_code=303)
    j = _tool_job(pid)
    if j and j["state"] == "running":
        flash(request, "Another tool is still running for this practice; try again when it's done.", "error")
        return back
    if not ai.available():
        flash(request, "AI isn't switched on for the Ads page. Run: sh enable-ads.sh", "error")
        return back
    form = dict(await request.form())
    try:
        tools._clean_inputs(tools.TOOLS[kind], form)       # missing fields show straight away
    except ai.AIError as e:
        flash(request, str(e), "error")
        return back
    TOOL_JOBS[pid] = {"state": "running", "kind": kind, "started": _time.time(), "msg": ""}
    if os.environ.get("ADS_SYNC_JOBS") == "1":
        _run_tool_job(pid, kind, form, actor(request))
    else:
        _threading.Thread(target=_run_tool_job, args=(pid, kind, form, actor(request)), daemon=True).start()
    return back


@app.post("/p/{pid}/tools/{kind}/delete")
def tool_delete(request: Request, pid: int, kind: str):
    if (r := guard(request)):
        return r
    if kind in tools.TOOLS:
        db.delete_ai(pid, tools.OUTPUT_PREFIX + kind)
        flash(request, "Removed.")
    return RedirectResponse(f"/p/{pid}#tool-{kind}", status_code=303)


# ---------- brand kits (per practice): rules every new ad is written and checked against ----------
def _brand_back(pid):
    return RedirectResponse(f"/p/{pid}#brand", status_code=303)


@app.post("/p/{pid}/brand")
async def brand_save(request: Request, pid: int):
    if (r := guard(request)):
        return r
    if not db.practice(pid):
        return RedirectResponse("/practices", status_code=303)
    form = await request.form()
    brand.save(pid, {k: form.get(k, "") for k in brand.KEYS + brand.OTHER}, actor(request))
    brand.sync_websites(actor(request))
    flash(request, "Brand kit saved. New ads for this practice follow it.")
    return _brand_back(pid)


@app.post("/p/{pid}/brand/upload")
async def brand_upload(request: Request, pid: int, file: UploadFile = File(...)):
    if (r := guard(request)):
        return r
    if not db.practice(pid):
        return RedirectResponse("/practices", status_code=303)
    data = await file.read(brand.FILE_MAX + 1)
    try:
        text = await asyncio.to_thread(brand.extract_text, data, file.filename or "")
    except brand.BrandError as e:
        flash(request, str(e), "error")
        return _brand_back(pid)
    brand.add_source(pid, file.filename or "document", "document", text, actor(request))
    flash(request, f"Read {len(text):,} characters from {file.filename}. Click “Fill the kit from these” to "
                   "turn them into brand rules.")
    return _brand_back(pid)


@app.post("/p/{pid}/brand/website")
async def brand_website(request: Request, pid: int, url: str = Form("")):
    if (r := guard(request)):
        return r
    url = url.strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        n = await asyncio.to_thread(brand.add_website, pid, url, actor(request))
    except brand.BrandError as e:
        flash(request, str(e), "error")
        return _brand_back(pid)
    k = brand.kit(pid)
    if not k.get("website"):
        brand.save(pid, {**{x: k[x] for x in brand.KEYS}, "website": url}, k.get("updated_by") or actor(request))
    flash(request, f"Read {n} page{'s' if n != 1 else ''} of the website.")
    return _brand_back(pid)


@app.post("/p/{pid}/brand/fill")
async def brand_fill(request: Request, pid: int):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    try:
        await asyncio.to_thread(brand.fill_from_sources, p, ai.generate_json)
    except (brand.BrandError, ai.AIError) as e:
        flash(request, str(e), "error")
        return _brand_back(pid)
    flash(request, "Suggestions filled in below from your materials. Check each one, then Save the kit.")
    return _brand_back(pid)


@app.post("/brand-kits/import")
async def brand_import(request: Request, file: UploadFile = File(...)):
    if (r := guard(request)):
        return r
    import json as _json
    raw = await file.read(brand.FILE_MAX + 1)
    try:
        data = _json.loads(raw.decode("utf-8-sig"))
        rep = brand.import_kits(data, db.practices(), actor(request))
    except (ValueError, UnicodeDecodeError):
        flash(request, "That isn't a brand-kits file (it should be the .json file Claude made).", "error")
        return RedirectResponse("/practices#brand-import", status_code=303)
    except brand.BrandError as e:
        flash(request, str(e), "error")
        return RedirectResponse("/practices#brand-import", status_code=303)
    n = sum(len(v) for v in rep["matched"].values())
    msg = (f"Brand kits ready to review for {n} practice{'s' if n != 1 else ''} ({len(rep['matched'])} brands). "
           "Open each practice's Brand kit, check the suggestions and Save.")
    if rep["unmatched"]:
        msg += " No practice matched: " + ", ".join(rep["unmatched"]) + " (add the practice, or rename it to include the brand name, and import again)."
    flash(request, msg, "ok" if n else "error")
    return RedirectResponse("/practices#brand-import", status_code=303)


@app.post("/p/{pid}/brand/discard")
def brand_discard(request: Request, pid: int):
    if (r := guard(request)):
        return r
    brand.set_suggested(pid, None)
    flash(request, "Suggestions discarded; the saved kit is unchanged.")
    return _brand_back(pid)


@app.post("/p/{pid}/brand/copy")
def brand_copy(request: Request, pid: int, source: str = Form("")):
    if (r := guard(request)):
        return r
    src = db.practice(int(source)) if source.isdigit() else None
    if not src or src["id"] == pid:
        flash(request, "Pick the practice to copy from.", "error")
        return _brand_back(pid)
    try:
        brand.copy_kit(src["id"], pid, actor(request))
        flash(request, f"Copied {src['name']}'s brand kit. Change the practice name, locations and doctors to this practice's.")
    except brand.BrandError as e:
        flash(request, str(e), "error")
    return _brand_back(pid)


@app.post("/p/{pid}/brand/sources/{sid}/delete")
def brand_source_delete(request: Request, pid: int, sid: int):
    if (r := guard(request)):
        return r
    brand.delete_source(pid, sid)
    flash(request, "Removed. The saved kit is unchanged.")
    return _brand_back(pid)


@app.post("/p/{pid}/ai/{kind}/delete")
def ai_delete(request: Request, pid: int, kind: str, back: str = Form("")):
    if (r := guard(request)):
        return r
    if kind in ai.TASKS:
        db.delete_ai(pid, kind)
        flash(request, {"fixes": "Fix list removed.", "creative": "Ad ideas removed.",
                        "report": "Monthly report removed."}[kind])
    return RedirectResponse(f"/p/{pid}" + ("#ai" if back != "report" else ""), status_code=303)


# ---------- organic post performance report (for leadership) ----------
def _post_report(p, period, sort):
    allp = db.practices()
    period = period if period in organic.PERIODS else organic.default_period()
    sort = sort if sort in organic.SORTS else "engagement"
    return organic.report(p, allp, is_master(p, allp), period, sort)


@app.get("/p/{pid}/posts", response_class=HTMLResponse)
def post_report(request: Request, pid: int, period: str = "", sort: str = "engagement"):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    rep = _post_report(p, period, sort)
    return page(request, "posts.html", p=p, rep=rep, periods=organic.PERIODS, sorts=organic.SORTS)


@app.get("/p/{pid}/posts.csv")
def post_report_csv(request: Request, pid: int, period: str = "", sort: str = "engagement"):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    import csv
    import io
    rep = _post_report(p, period, sort)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Rank", "Practice", "Location (account)", "Date", "Network", "Post", "Link", "Views", "Reach",
                "Likes", "Comments", "Shares", "Saves", "Engagement", "Engagement rate", "Vs. average post"])
    for row in rep["rows"]:
        w.writerow([row["rank"], rep["practice_of"].get(row["account_id"], p["name"]), row.get("account_name", ""),
                    row["created"][:10], row["network"].title(), row["title"], row["permalink"],
                    "" if row["views"] is None else row["views"], "" if row["reach"] is None else row["reach"],
                    row["likes"], row["comments"], row["shares"], "" if row["saves"] is None else row["saves"],
                    row["engagement"], "" if row["rate"] is None else f"{row['rate'] * 100:.1f}%",
                    "" if row["vs_avg"] is None else f"{row['vs_avg']:.1f}x"])
    fname = re.sub(r"[^A-Za-z0-9]+", "-", f"{p['name']} posts {rep['since']} to {rep['until']}").strip("-")
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})


# ---------- email export (Outlook-friendly) ----------
def _email_parts(p, inc_param, period=""):
    from datetime import date as _date
    allp = db.practices()
    views = practice_views(p, with_breakdown=True)
    writeups = {}
    for kind in ("fixes", "creative", "report"):
        out = db.latest_ai(p["id"], kind)
        if out:
            writeups[kind] = emailfmt.inline_styles(render_md(out["content"]))
    month = _date.today().isoformat()[:7]
    changes = [c for c in db.changes(p["id"], ["applied"]) if (c.get("applied_at") or "").startswith(month)]
    master = is_master(p, allp)
    org = organic_for(p, views, allp)
    combo = organic.combined(views, org)
    rep = _post_report(p, period, "engagement")
    available = {"summary": any(v["totals"] for v in views),
                 "postrank": bool(rep["rows"]),
                 "organic": bool((org.get("totals") or {}).get("count")),
                 "practices": any(v["breakdown"] and v["breakdown"]["by_practice"] for v in views),
                 "attention": any(v["findings"] for v in views),
                 "search": any(v["search_terms"] for v in views),
                 "campaigns": any(v["campaigns"] for v in views),
                 "fixes": "fixes" in writeups, "creative": "creative" in writeups, "report": "report" in writeups,
                 "changes": bool(changes)}
    if inc_param is None:
        inc = {k for k, ok in available.items() if ok}
    else:
        inc = {k for k in inc_param.split(",") if available.get(k)}
    subject = emailfmt.subject_for(p, views)
    if inc and inc <= {"postrank", "organic"}:
        subject = (f"{p['name']} — social post performance, {organic.PERIODS[rep['period']].lower()} "
                   f"({rep['since'].strftime('%b %-d')}–{rep['until'].strftime('%b %-d, %Y')})")
    doc = templates.env.get_template("email_body.html").render(
        p=p, views=views, master=master, inc=inc, writeups=writeups, changes=changes, em=emailfmt, org=org, combo=combo,
        rep=rep,
        subject=subject, today=_date.today().strftime("%B %-d, %Y"))
    return {"doc": doc, "subject": subject, "inc": inc, "available": available}


@app.get("/p/{pid}/email", response_class=HTMLResponse)
def email_preview(request: Request, pid: int, inc: str | None = None, picked: str = "", period: str = ""):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    parts = _email_parts(p, inc if (inc is not None or picked) else None, period)
    body = parts["doc"].split("<body", 1)[1].split(">", 1)[1].rsplit("</body>", 1)[0]
    return page(request, "email.html", p=p, body=body, subject=parts["subject"], inc=parts["inc"],
                available=parts["available"], sections=emailfmt.SECTIONS, inc_param=",".join(sorted(parts["inc"])),
                period=period)


@app.get("/p/{pid}/email.eml")
def email_download(request: Request, pid: int, inc: str | None = None, period: str = ""):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    parts = _email_parts(p, inc, period)
    fname = re.sub(r"[^A-Za-z0-9 _-]+", "", parts["subject"]).strip().replace(" ", "-")[:80] or "ad-results"
    return Response(emailfmt.eml(parts["subject"], parts["doc"]), media_type="message/rfc822",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.eml"'})


@app.get("/p/{pid}/report", response_class=HTMLResponse)
def report(request: Request, pid: int):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/practices", status_code=303)
    return page(request, "report.html", p=p, views=practice_views(p), report=db.latest_ai(pid, "report"))


# ---------- connections (phase 2: automatic pulls) ----------
def google_ready():
    return bool(cx.google_service_account()) or (cx.google_oauth_configured()
                                                and bool(db.get_setting("google_refresh_token")))

def redirect_uri(request: Request):
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return base + "/google/callback"


def summarize_sync(status):
    if status.get("busy"):
        return "A sync is already running. Refresh in a minute.", "error"
    res = status.get("results", {})
    if not res:
        return "Nothing to sync yet: link an ad account, Facebook Page or Instagram account to a practice first.", "error"
    kinds = {"meta": "Meta Ads", "google": "Google Ads", "facebook": "Facebook posts", "instagram": "Instagram posts"}
    ok = sum(1 for r in res.values() for k in kinds if r.get(k, {}).get("ok"))
    bad = [(r["name"], k, r[k]["error"]) for r in res.values() for k in kinds if k in r and not r[k].get("ok")]
    msg = f"Synced {ok} account{'s' if ok != 1 else ''} ({status['since']} to {status['until']})."
    if bad:
        name, k, err = bad[0]
        msg += f" {len(bad)} failed — {name} ({kinds[k]}): {err}"
    return msg, ("error" if bad else "ok")


@app.get("/connections/google/conversions", response_class=HTMLResponse)
def google_conversions(request: Request, cid: str = ""):
    """Read-only: what each Google Ads conversion action counted in the last 30 days, and in which campaigns."""
    if (r := guard(request)):
        return r
    from . import leaks
    ids = sorted({str(p["google_customer_id"]) for p in db.practices(include_hidden=True) if p.get("google_customer_id")})
    cid = cid if cid in ids else (ids[0] if ids else "")
    rep, err = None, ""
    if cid and actions.google_ready():
        try:
            rep = leaks.conversion_report(actions._google(), cid)
        except cx.ConnectorError as e:
            err = str(e)
    return page(request, "google_conversions.html", rep=rep, err=err, cid=cid, ids=ids)


@app.get("/connections", response_class=HTMLResponse)
def connections(request: Request):
    if (r := guard(request)):
        return r
    import json as _json
    try:
        checked = _json.loads(db.get_setting("accounts_checked") or "{}")
    except ValueError:
        checked = {}
    sa = cx.google_service_account()
    return page(request, "connections.html",
                meta_on=cx.meta_configured(), google_keys=cx.google_configured(),
                google_sa=sa["client_email"] if sa else None, google_oauth=cx.google_oauth_configured(),
                google_connected=google_ready(), manager_id=cx.setting("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
                meta_from_page=bool(db.get_setting("meta_access_token")),
                meta_accounts=sync.cached_accounts("meta"), google_accounts=sync.cached_accounts("google"),
                checked=checked, redirect_uri=redirect_uri(request), practices=scope.with_inherited(db.practices()),
                window=sync.sync_window(), sf_client=all(salesforce.client()), sf_on=salesforce.connected(),
                sf_user=salesforce.user(), sf_status=salesforce.status(), sf_redirect=_sf_redirect(request),
                sf_login=salesforce.login_url(), sf_waiting=len(salesforce.pending_ids(10000)) if salesforce.connected() else 0,
                ms=microsoft.settings(), ms_on=microsoft.connected(), ms_user=microsoft.user(), ms_redirect=_ms_redirect(request),
                ms_studio=microsoft.studio_on(), sp_site=studio.sp_site(), sp_name=db.get_setting("studio_sp_site_name") or "",
                sp_root=studio.sp_root(), sp_drive=bool(studio.sp_drive()), sp_status=studio.sp_status(),
                studio_counts=studio.counts(), canva_cfg=canva.settings(), canva_on=canva.connected(),
                canva_user=canva.user(), canva_redirect=_canva_redirect(request),
                post_key=social.key_source(), post_url=social.base_url(), post_default_url=social.DEFAULT_URL,
                stock_keys=stock.keys())


@app.post("/connections/refresh")
async def connections_refresh(request: Request):
    if (r := guard(request)):
        return r
    res = await asyncio.to_thread(sync.refresh_account_lists)
    errs = [f"{'Meta' if k == 'meta' else 'Google'}: {v['error']}" for k, v in res.items() if not v.get("ok")]
    if errs:
        flash(request, " ".join(errs), "error")
    elif res:
        flash(request, "Account lists refreshed: " + ", ".join(
            f"{v['count']} {'Meta' if k == 'meta' else 'Google'} account{'s' if v['count'] != 1 else ''}"
            for k, v in res.items()) + ".")
    else:
        flash(request, "No connections are set up yet.", "error")
    return RedirectResponse("/connections", status_code=303)


@app.post("/sync")
async def sync_now(request: Request, back: str = Form("/")):
    if (r := guard(request)):
        return r
    back = back if back.startswith("/") and not back.startswith("//") else "/practices"
    if sync.start_in_background():
        flash(request, "Sync started. It runs in the background and can take a few minutes; this page updates when it's done.")
    else:
        flash(request, "A sync is already running; this page updates when it's done.")
    return RedirectResponse(back, status_code=303)


@app.get("/sync/status")
def sync_status(request: Request):
    if not authed(request):
        return Response(status_code=401)
    return {"running": sync.running(), "at": sync.last_sync().get("at")}


@app.get("/sync/done")
def sync_done(request: Request, back: str = "/practices"):
    if (r := guard(request)):
        return r
    st = sync.last_sync()
    if st.get("error"):
        flash(request, f"The sync stopped: {st['error']}", "error")
    elif st:
        flash(request, *summarize_sync(st))
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/practices", status_code=303)


@app.get("/google/connect")
def google_connect(request: Request):
    if (r := guard(request)):
        return r
    if not cx.google_oauth_configured():
        flash(request, "Google sign-in isn't set up on the server. Upload a service account key instead.", "error")
        return RedirectResponse("/connections", status_code=303)
    state = os.urandom(16).hex()
    request.session["google_state"] = state
    return RedirectResponse(cx.google_auth_url(redirect_uri(request), state), status_code=303)


@app.get("/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if (r := guard(request)):
        return r
    expected = request.session.pop("google_state", None)
    if error:
        flash(request, f"Google sign-in was cancelled ({error}).", "error")
    elif not expected or not hmac.compare_digest(state, expected):
        flash(request, "That Google sign-in link expired. Try Connect Google again.", "error")
    else:
        try:
            token = await asyncio.to_thread(cx.google_exchange_code, code, redirect_uri(request))
            db.set_setting("google_refresh_token", token)
            res = await asyncio.to_thread(sync.refresh_account_lists)
            g = res.get("google", {})
            if g.get("ok"):
                flash(request, f"Google connected. {g['count']} Google Ads account{'s' if g['count'] != 1 else ''} found.")
            else:
                flash(request, f"Google signed in, but listing accounts failed: {g.get('error', 'unknown error')}", "error")
        except cx.ConnectorError as e:
            flash(request, f"Google sign-in failed: {e}", "error")
    return RedirectResponse("/connections", status_code=303)


@app.post("/google/disconnect")
def google_disconnect(request: Request):
    if (r := guard(request)):
        return r
    db.set_setting("google_refresh_token", None)
    db.set_setting("google_accounts", None)
    flash(request, "Google disconnected. Synced data already pulled is kept.")
    return RedirectResponse("/connections", status_code=303)


@app.post("/p/{pid}/accounts")
async def practice_accounts(request: Request, pid: int):
    if (r := guard(request)):
        return r
    form = await request.form()
    db.set_practice_accounts(pid, str(form.get("meta_account_id", "")).strip(),
                             str(form.get("google_customer_id", "")).strip())
    # Several Pages / Instagram accounts (locations) per practice; "social" marks that the lists were on the form.
    if form.get("social") == "1" or "fb_page_id" in form or "ig_account_id" in form:
        db.set_practice_social(pid, [v for v in form.getlist("fb_page_id") if v],
                               [v for v in form.getlist("ig_account_id") if v])
    flash(request, "Linked accounts saved. Use “Sync now” to pull their numbers.")
    return RedirectResponse(f"/p/{pid}", status_code=303)


def _check_and_report(request, platform_key, label):
    res = sync.refresh_account_lists().get(platform_key)
    if res and res.get("ok"):
        flash(request, f"{label} connected. {res['count']} account{'s' if res['count'] != 1 else ''} found.")
    elif res:
        flash(request, f"{label} saved, but checking it failed: {res['error']}", "error")


@app.post("/connections/meta")
async def save_meta(request: Request, token: str = Form("")):
    if (r := guard(request)):
        return r
    token = token.strip()
    if len(token) < 40 or any(ch.isspace() for ch in token):
        flash(request, "That doesn't look like a full Meta token. Copy the whole token (usually 150+ characters).", "error")
        return RedirectResponse("/connections", status_code=303)
    db.set_setting("meta_access_token", token)
    await asyncio.to_thread(_check_and_report, request, "meta", "Meta")
    return RedirectResponse("/connections", status_code=303)


@app.post("/connections/meta/remove")
def remove_meta(request: Request):
    if (r := guard(request)):
        return r
    db.set_setting("meta_access_token", None)
    db.set_setting("meta_accounts", None)
    flash(request, "Meta token removed. Synced numbers already pulled are kept.")
    return RedirectResponse("/connections", status_code=303)


@app.post("/connections/google")
async def save_google(request: Request, keyfile: UploadFile = File(None), manager_id: str = Form("")):
    if (r := guard(request)):
        return r
    if keyfile is not None and keyfile.filename:
        raw = (await keyfile.read(64 * 1024)).decode(errors="replace")
        try:
            db.set_setting("google_service_account_json", cx.parse_service_account(raw))
        except cx.ConnectorError as e:
            flash(request, str(e), "error")
            return RedirectResponse("/connections", status_code=303)
    mid = "".join(ch for ch in manager_id if ch.isdigit())
    if manager_id.strip() and len(mid) != 10:
        flash(request, "The manager account ID should be 10 digits, like 123-456-7890.", "error")
        return RedirectResponse("/connections", status_code=303)
    db.set_setting("google_ads_login_customer_id", mid or None)
    if not cx.google_service_account():
        flash(request, "Upload the service account key file (.json) too.", "error")
        return RedirectResponse("/connections", status_code=303)
    await asyncio.to_thread(_check_and_report, request, "google", "Google")
    return RedirectResponse("/connections", status_code=303)


@app.post("/connections/google/remove")
def remove_google(request: Request):
    if (r := guard(request)):
        return r
    for k in ("google_service_account_json", "google_refresh_token", "google_accounts"):
        db.set_setting(k, None)
    flash(request, "Google connection removed. Synced numbers already pulled are kept.")
    return RedirectResponse("/connections", status_code=303)


# ---------- market map: our offices, nearby dentists (referrers) and other orthodontists ----------
@app.get("/map", response_class=HTMLResponse)
def market_map(request: Request):
    if (r := guard(request)):
        return r
    places.purge_old()
    offs = places.offices()
    only = users.practice_ids(me(request))
    if only is not None:
        links = field.practice_links()
        offs = [o for o in offs if links.get(o["id"]) in only]
    return page(request, "map.html", offices=offs, counts=places.counts(), status=places.status(),
                browser_key=places.browser_key(), server_key_set=bool(places.server_key()),
                map_id=places.map_id(), map_id_custom=bool(cx.setting("MAPS_MAP_ID")),
                radius=places.radius(), brands=db.get_setting("map_brand_terms") or "",
                max_requests=places.max_requests(), per_office=places.per_office(), layers=places.LAYERS,
                last=db.get_setting("map_last_refresh"), prev=db.get_setting("map_prev_refresh"),
                peds_at=db.get_setting("map_peds_at"),
                preview=_import_preview(), school_status=schools.status(), school_kinds=schools.KINDS)


@app.post("/map/schools/refresh")
def map_schools_refresh(request: Request):
    if (r := guard(request)):
        return r
    if schools.status().get("busy"):
        flash(request, "Schools are already being fetched; refresh the page in a minute.")
    elif not [o for o in places.offices() if o.get("lat") is not None]:
        flash(request, "Add our offices to the Map first.", "error")
    else:
        schools.refresh_in_background()
        flash(request, "Fetching private and charter schools from NCES in the background. Refresh the page in a minute or two.")
    return RedirectResponse("/map#schools", status_code=303)


def _zip_rows(request):
    offs = places.offices()
    only = users.practice_ids(me(request))
    if only is not None:
        links = field.practice_links()
        offs = [o for o in offs if links.get(o["id"]) in only]
    from . import zips
    return zips.for_offices(offs)


@app.get("/map/zips", response_class=HTMLResponse)
def map_zips(request: Request):
    """Target zip codes around each office: income and families, from the Census."""
    if (r := guard(request)):
        return r
    from . import zips, zip_plan
    rows = _zip_rows(request)
    plan = zip_plan.assign() if any(r["zips"] for r in rows) else None
    only = users.practice_ids(me(request))
    if plan and only is not None:
        plan = {**plan, "offices": [r for r in plan["offices"] if r["practice"]["id"] in only]}
    plan_practices = sorted({r["practice"]["id"]: r["practice"] for r in (plan or {}).get("offices", [])}.values(),
                            key=lambda p: p["name"].lower())
    return page(request, "map_zips.html", rows=rows, plan=plan, lines=zip_plan.LINES, plan_practices=plan_practices,
                priority=zip_plan.priority_ids(), status=zips.status(), miles=zips.miles_setting(), has_key=zips.has_key(),
                any_data=any(r["zips"] for r in rows), min_hh=zips.MIN_HOUSEHOLDS)


@app.post("/map/zips/refresh")
def map_zips_refresh(request: Request):
    if (r := guard(request)):
        return r
    from . import zips
    if zips.status().get("busy"):
        flash(request, "Already fetching the Census numbers; refresh the page in a minute.")
    else:
        zips.refresh_in_background()
        flash(request, "Fetching income and family numbers for every US zip code from the Census Bureau. Refresh the page in a minute.")
    return RedirectResponse("/map/zips", status_code=303)


@app.post("/map/zips/key")
def map_zips_key(request: Request, key: str = Form("")):
    """The free Census API key (admins). Saved in the hub's settings; never shown again."""
    if (r := guard(request)):
        return r
    key = key.strip()
    if (me(request) or {}).get("role") != "admin":
        flash(request, "That needs admin access. Ask an admin.", "error")
    elif re.fullmatch(r"[0-9a-fA-F]{40}", key):
        db.set_setting("census_key", key)
        from . import zips
        zips.refresh_in_background()
        flash(request, "Key saved. Fetching the Census numbers now; refresh the page in a minute.")
    else:
        flash(request, "That doesn't look like a Census API key (40 letters and numbers, from their email).", "error")
    return RedirectResponse("/map/zips", status_code=303)


@app.post("/map/zips/priority")
async def map_zips_priority(request: Request):
    """Practices that get first claim on shared zip codes (admins)."""
    if (r := guard(request)):
        return r
    if (me(request) or {}).get("role") != "admin":
        flash(request, "That needs admin access. Ask an admin.", "error")
        return RedirectResponse("/map/zips#who", status_code=303)
    form = await request.form()
    from . import zip_plan
    zip_plan.set_priority([int(x) for x in form.getlist("pid") if str(x).isdigit()])
    flash(request, "Saved which practices come first for shared zip codes.")
    return RedirectResponse("/map/zips#who", status_code=303)


@app.post("/p/{pid}/offers/uninsured")
async def offer_uninsured(request: Request, pid: int):
    """Draft the "$1,000 off without insurance" campaign for some of the practice's locations (created paused)."""
    if (r := guard(request)):
        return r
    from . import campaigns, offers
    form = await request.form()
    p = db.practice(pid)
    ids = {int(x) for x in form.getlist("office") if str(x).isdigit()}
    try:
        budget = float(form.get("daily_budget") or 20)
        prop = offers.draft(p, ids, actions._google(), daily_budget=budget, cities=str(form.get("cities") or "")[:40])
        flash(request, "Drafted " + prop["title"] + ". Review it under New campaigns, then Apply.")
    except (campaigns.CampaignError, cx.ConnectorError, ValueError) as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/p/{pid}#fixes", status_code=303)


@app.post("/practices/conquest")
async def practices_conquest(request: Request):
    """Draft a competitor-name campaign (created paused) for every orthodontic practice with assigned zips."""
    if (r := guard(request)):
        return r
    from . import campaigns, conquest, zip_plan
    form = await request.form()
    try:
        budget = float(form.get("daily_budget") or 10)
    except ValueError:
        budget = 10.0
    g = actions._google()
    done, skipped = [], []
    for pid in zip_plan.assign()["practices"]:
        p = db.practice(pid)
        if zip_plan.line_of(p) != "ortho":
            continue
        try:
            conquest.draft(p, g, budget)
            done.append(p["name"])
        except (campaigns.CampaignError, cx.ConnectorError) as e:
            skipped.append(str(e))
    flash(request, f"Drafted competitor-name campaigns for {len(done)} practices (created paused on Apply)."
          + (" Skipped: " + " ".join(skipped[:5]) if skipped else ""))
    return RedirectResponse("/practices", status_code=303)


@app.post("/map/zips/miles")
def map_zips_miles(request: Request, miles: str = Form("")):
    if (r := guard(request)):
        return r
    if miles.isdigit() and 3 <= int(miles) <= 40:
        db.set_setting("zip_miles", miles)
        flash(request, f"Zip codes within {miles} miles of each office.")
    else:
        flash(request, "Pick between 3 and 40 miles.", "error")
    return RedirectResponse("/map/zips", status_code=303)


@app.get("/map/zips.xlsx")
def map_zips_xlsx(request: Request):
    if (r := guard(request)):
        return r
    from . import zips, zip_plan
    plan = zip_plan.assign()
    only = users.practice_ids(me(request))
    if only is not None:
        plan = {**plan, "offices": [r for r in plan["offices"] if r["practice"]["id"] in only]}
    return Response(zips.export_xlsx(_zip_rows(request), zip_plan.export_rows(plan)),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="target-zip-codes.xlsx"'})


@app.get("/map/schools.xlsx")
def map_schools_xlsx(request: Request, office: str = "", kind: str = ""):
    if (r := guard(request)):
        return r
    data, n = schools.export_xlsx(int(office) if office.isdigit() else None, kind if kind in schools.KINDS else None)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="schools-near-our-offices.xlsx"'})


@app.get("/map/data.json")
def market_map_data(request: Request):
    if not authed(request):
        return Response(status_code=401)
    places.purge_old()
    u = me(request)
    data = {**places.map_data(), **field.map_overlay(None if users.sees_all_field(u) else u["id"], users.hidden_people(u)),
            **schools.map_data()}
    try:
        only_p = users.practice_ids(u)
        hid_p = db.hidden_ids()
        data["sf_accounts"] = [x for x in accounts.map_data(only_p) if x["pid"] not in hid_p]
    except Exception:
        data["sf_accounts"] = []
    hid, only = db.hidden_ids(), users.practice_ids(u)
    if hid or only is not None:   # hidden practices' offices (and the dental offices around them) drop off; someone
        links = field.practice_links()      # limited to some practices sees only those practices' offices
        gone = {o["id"] for o in data.get("offices", []) if links.get(o["id"]) in hid
                or (only is not None and links.get(o["id"]) not in only)}
        data["offices"] = [o for o in data.get("offices", []) if o["id"] not in gone]
        data["places"] = [p for p in data.get("places", []) if p.get("office") not in gone]
        if only is not None and "schools" in data and isinstance(data["schools"], list):
            data["schools"] = [x for x in data["schools"] if x.get("office") not in gone]
    return data


def _map_back(anchor=""):
    return RedirectResponse("/map" + anchor, status_code=303)


@app.post("/map/setup")
def market_map_setup(request: Request, browser_key: str = Form(""), server_key: str = Form(""),
                     map_id: str = Form(""), radius: str = Form(""), brands: str = Form(""),
                     max_requests: str = Form(""), per_office: str = Form("")):
    if (r := guard(request)):
        return r
    def clean(v):
        return re.sub(r"[^A-Za-z0-9_-]", "", v)[:120]
    if browser_key.strip():
        db.set_setting("maps_browser_key", clean(browser_key))
    if server_key.strip():
        db.set_setting("maps_server_key", clean(server_key))
    db.set_setting("maps_map_id", clean(map_id) or None)
    try:
        db.set_setting("map_radius_miles", str(min(max(float(radius), 1), 50)) if radius.strip() else None)
        db.set_setting("map_max_requests", str(max(int(max_requests), 10)) if max_requests.strip() else None)
        db.set_setting("map_per_office", str(max(int(per_office), 0)) if per_office.strip() else None)
    except ValueError:
        flash(request, "Radius and request limit must be numbers.", "error")
        return _map_back("#setup")
    db.set_setting("map_brand_terms", brands.strip()[:1000] or None)
    flash(request, "Map setup saved. Refresh the map to apply a new radius or brand names.")
    return _map_back("#setup")


@app.post("/map/offices")
def market_map_add_office(request: Request, label: str = Form(""), place_id: str = Form(""),
                          lat: str = Form(""), lng: str = Form("")):
    if (r := guard(request)):
        return r
    try:
        places.add_office(label, place_id, lat, lng)
        flash(request, f"Added {label.strip()}. Click Refresh map to find the practices around it.")
    except places.MapError as e:
        flash(request, str(e), "error")
    return _map_back("#offices")


@app.post("/map/offices/{oid}/rename")
def market_map_rename_office(request: Request, oid: int, label: str = Form("")):
    if (r := guard(request)):
        return r
    places.rename_office(oid, label)
    return _map_back("#offices")


@app.post("/map/offices/{oid}/delete")
def market_map_delete_office(request: Request, oid: int):
    if (r := guard(request)):
        return r
    places.remove_office(oid)
    flash(request, "Office removed from the map.")
    return _map_back("#offices")


@app.post("/map/refresh")
def market_map_refresh(request: Request):
    if (r := guard(request)):
        return r
    if not places.server_key() or not places.offices():
        flash(request, "Add the server API key and at least one office first.", "error")
        return _map_back("#setup")
    if places.status().get("busy"):
        flash(request, "A refresh is already running. Reload this page in a minute or two.", "error")
        return _map_back()
    places.refresh_in_background()
    flash(request, "Refreshing: searching Google around each office. This takes a minute or two per office; "
                   "reload the page to see progress.")
    return _map_back()


# ---------- importing our own office list (CSV / Excel / My Maps KML) ----------
OFFICE_GROUP_HINT = re.compile(r"\b(our|office|offices|location|locations|practice|practices|mcdp)\b", re.I)


def _import_preview():
    import json as _json
    try:
        rows = _json.loads(db.get_setting("map_import_preview") or "[]")
    except ValueError:
        rows = []
    if not rows:
        return None
    brands = places.brand_terms()
    groups = sorted({r.get("group") or "" for r in rows})
    hinted = {g for g in groups if g and (OFFICE_GROUP_HINT.search(g) or any(b in g.lower() for b in brands))}
    for i, r in enumerate(rows):
        r["idx"] = i
        r["checked"] = (r.get("group") or "") in hinted if hinted else True
    return {"rows": rows, "groups": groups}


@app.post("/map/import")
async def market_map_import(request: Request, file: UploadFile = File(...)):
    if (r := guard(request)):
        return r
    import json as _json
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        flash(request, "That file is over 20 MB.", "error")
        return _map_back("#offices")
    try:
        rows = places.parse_office_file(data, file.filename or "")
    except places.MapError as e:
        flash(request, str(e), "error")
        return _map_back("#offices")
    db.set_setting("map_import_preview", _json.dumps(rows))
    flash(request, f"Read {len(rows)} place{'s' if len(rows) != 1 else ''}. Tick the ones that are our offices, "
                   "then click Add.")
    return _map_back("#import")


@app.post("/map/import/confirm")
async def market_map_import_confirm(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    pv = _import_preview()
    if not pv:
        return _map_back("#offices")
    picked = [pv["rows"][int(i)] for i in form.getlist("row") if str(i).isdigit() and int(i) < len(pv["rows"])]
    if not picked:
        flash(request, "Tick at least one office to add.", "error")
        return _map_back("#import")
    if not places.server_key():
        flash(request, "Add the server API key in Map setup first; it's used to find each office on Google.", "error")
        return _map_back("#setup")
    added, problems = await asyncio.to_thread(places.import_offices, picked)
    db.set_setting("map_import_preview", None)
    msg = f"Added {added} office{'s' if added != 1 else ''}."
    if problems:
        msg += f" {len(problems)} couldn't be matched: " + "; ".join(problems[:3]) + (" …" if len(problems) > 3 else "")
    elif added:
        msg += " Click Refresh map to find the practices around them."
    flash(request, msg, "error" if problems and not added else "ok")
    return _map_back("#offices")


@app.post("/map/import/cancel")
def market_map_import_cancel(request: Request):
    if (r := guard(request)):
        return r
    db.set_setting("map_import_preview", None)
    return _map_back("#offices")


# ---------- map lists (NPI Registry): referral / competition, omit / star / note, export ----------
def _office_param(office):
    return int(office) if str(office).isdigit() else None


def _kind_param(kind):
    return kind if kind in referrals.KINDS else "referrer"


@app.get("/map/list", response_class=HTMLResponse)
def map_list(request: Request, office: str = "", kind: str = "referrer"):
    if (r := guard(request)):
        return r
    offs = [o for o in places.offices() if o["lat"] is not None]
    oid = _office_param(office)
    if office != "all" and oid not in {o["id"] for o in offs}:
        oid = offs[0]["id"] if offs else None          # our practice is the starting point
    return page(request, "map_list.html", offices=offs, office_id=oid, kind=_kind_param(kind), kinds=referrals.KINDS,
                radius=places.radius(), ref_status=referrals.status(), ref_last=db.get_setting("ref_last_refresh"))


@app.get("/map/referrals.json")
def referral_list(request: Request, office: str = "", kind: str = "referrer"):
    if not authed(request):
        return Response(status_code=401)
    keep = ("key", "name", "dentists", "specialties", "address", "city", "state", "zip", "phone", "lat", "lng", "geo",
            "has_ortho", "place_id", "omitted", "starred", "note", "office_id", "office", "miles", "maps_url")
    return {"radius": places.radius(), "status": referrals.status(), "kind": _kind_param(kind),
            "rows": [{k: r[k] for k in keep} for r in referrals.listing(_office_param(office), kind=_kind_param(kind))]}


@app.post("/map/referrals/mark")
async def referral_mark(request: Request):
    if not authed(request):
        return Response(status_code=401)
    try:
        body = await request.json()
    except ValueError:
        return Response(status_code=400)
    ok = referrals.mark(str(body.get("key", ""))[:32], omitted=body.get("omitted"), starred=body.get("starred"),
                        note=body.get("note") if isinstance(body.get("note"), str) else None)
    return {"ok": ok}


@app.post("/map/referrals/refresh")
def referral_refresh(request: Request, back: str = Form("/map/list")):
    if (r := guard(request)):
        return r
    back = back if back.startswith("/map/list") else "/map/list"
    if referrals.status().get("busy"):
        flash(request, "The lists are already being updated. Reload in a few minutes.", "error")
    elif not [o for o in places.offices() if o["lat"] is not None]:
        flash(request, "Add our offices on the Map page first, so the lists know where to look.", "error")
    else:
        referrals.refresh_in_background()
        flash(request, "Building the lists from the NPI Registry. This takes a few minutes; reload to see progress.")
    return RedirectResponse(back, status_code=303)


@app.get("/map/referrals.xlsx")
def referral_export(request: Request, office: str = "", starred: str = "", kind: str = "referrer"):
    if (r := guard(request)):
        return r
    oid, kind = _office_param(office), _kind_param(kind)
    data, n = referrals.export_xlsx(oid, starred_only=starred == "1", kind=kind)
    label = next((o["label"] for o in places.offices() if o["id"] == oid), "all offices")
    from datetime import date as _date
    what = "Referral practices" if kind == "referrer" else "Competing practices"
    fname = re.sub(r"[^A-Za-z0-9]+", "-", f"{what} {label} {_date.today()}").strip("-")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'})


# ---------- field visits ----------
def _field_args(request):
    q = request.query_params
    period = q.get("period") if q.get("period") in ("day", "week", "month") else "week"
    try:
        day = date.fromisoformat(q.get("d", "")[:10])
    except ValueError:
        day = date.today()
    who = int(q["who"]) if (q.get("who") or "").isdigit() else None
    prac = int(q["practice"]) if (q.get("practice") or "").isdigit() else None
    return period, day, who, prac


@app.get("/field", response_class=HTMLResponse)
def field_page(request: Request):
    if (r := guard(request)):
        return r
    period, day, who, prac = _field_args(request)
    u = me(request)
    see_all = users.sees_all_field(u)
    if not see_all:   # everyone except admins and field managers sees only their own visits, spend and follow-ups
        who = u["id"]
    start, end, label, prev_d, next_d = field.period_bounds(period, day)
    rows = field.visits_between(start, end, who, prac)
    dfilter = request.query_params.get("dept", "")
    dfilter = dfilter if dfilter in dept.LABEL or dfilter == "none" else ""
    if dfilter:
        rows = [r for r in rows if (r.get("department") or "none") == dfilter]
    m_start, m_end, *_ = field.period_bounds("month", day)
    month_rows = field.visits_between(m_start, m_end, None if see_all else u["id"])
    hide = users.hidden_people(u)
    if hide:                                     # admins' visits hidden from this person
        rows = [r for r in rows if r["user_id"] not in hide]
        month_rows = [r for r in month_rows if r["user_id"] not in hide]
    team = [t for t in users.all_users() if t["id"] not in hide]
    links = field.practice_links()
    budgets = field.budgets(m_start, month_rows)
    mine_ids = users.practice_ids(u)
    if mine_ids is not None:
        budgets = {**budgets, "practices": [b for b in budgets["practices"] if b.get("id") in mine_ids], "unlinked": 0}
    if not see_all:
        budgets = {"practices": [], "agents": [b for b in budgets["agents"] if b["id"] == u["id"]], "unlinked": 0}
    offer = request.session.pop("fu_offer", None)
    offer_v = field.visit(offer) if offer else None
    vids = [r["id"] for r in rows] + ([offer] if offer else [])
    fu_events = cal.followups_for(vids)
    if offer_v and (offer in fu_events or offer_v["user_id"] != u["id"]):
        offer_v = None
    vperm = {r["id"]: {"change": dept.can_change(u, r), "approve": dept.can_approve(u, r)} for r in rows}
    vforms = {k: f for k, f in dept.forms_for([r["id"] for r in rows]).items()
              if dept.sees_forms(u, checklists.DEPT)}
    return page(request, "field.html", unlogged=cal.unlogged(u) if u.get("field") else [], fu_offer=offer_v,
                vperm=vperm, vforms=vforms, dfilter=dfilter, acct_labels=accounts.labels_for_offices({r["office_id"] for r in rows}), departments=dept.DEPARTMENTS, dept_labels=dept.LABEL,
                my_alerts=dept.alerts(u["id"]), my_pending=(mp := dept.pending_for(u)), pending_rows=mp[:15], is_lead=dept.is_lead(u),
                my_dept=dept.of(u), ck_forms=checklists.FORMS,
                fu_events=fu_events, visit_events=cal.events_for_visits([r["id"] for r in rows]), period=period, day=day, label=label, prev_d=prev_d, next_d=next_d,
                today=date.today(), start=start, end=end, rows=rows, days=field.by_day(rows), sums=field.summary(rows),
                budgets=budgets, see_all=see_all, month_label=m_start.strftime("%B %Y"),
                who=who, practice=prac, team=team, practices=db.keep_mine(db.practices()), ratings=field.RATING_LABELS,
                follow=field.followups(user_id=u["id"]), offices=places.offices(), links=links,
                manage_fu=_manage_followups(u, hide) if users.rank(u["role"]) >= users.rank("editor") else None,
                fu_people=[t for t in users.all_users() if t["active"] and t["id"] not in hide],
                reminder_days=field.REMINDER_DAYS,
                agents=[t for t in team if t["active"] and t.get("field")], browser_key=places.browser_key(), map_id=places.map_id(),
                my_id=(u or {}).get("id"), sf_on=salesforce.connected(), sf_base=salesforce.instance_url())


def _manage_followups(u, hide):
    only = users.practice_ids(u)
    links = field.practice_links()
    out = []
    everyone = users.sees_all_field(u)
    for f in field.open_followups():
        if f["owner_id"] in hide or (not everyone and f["owner_id"] != u["id"] and f["user_id"] != u["id"]):
            continue                                  # without "everyone's field visits": only their own
        if only is not None:
            v = field.visit(f["visit_id"]) or {}
            if (v.get("practice_id") or links.get(v.get("our_office_id"))) not in only:
                continue
        out.append(f)
    return out


@app.post("/field/followups/{vid}/{action}")
async def field_followup_manage(request: Request, vid: int, action: str):
    """Editors and admins: change a follow-up's owner, remove it, or restart its 120-day reminder clock."""
    if (r := guard(request)):
        return r
    u = me(request)
    if users.rank(u["role"]) < users.rank("editor"):
        flash(request, "Managing follow-ups needs editor access.", "error")
        return RedirectResponse("/field#manage-fu", status_code=303)
    v = field.visit(vid)
    if not v:
        flash(request, "That visit isn't there any more.", "error")
        return RedirectResponse("/field#manage-fu", status_code=303)
    form = await request.form()
    if action == "owner" and str(form.get("owner") or "").isdigit() and users.get(int(form.get("owner"))):
        field.set_followup_owner(vid, int(form.get("owner")))
        flash(request, f"{v['office_name']}: the follow-up now belongs to {users.get(int(form.get('owner')))['name']}.")
    elif action == "remove":
        field.remove_followup(vid)
        flash(request, f"{v['office_name']}: follow-up and reminder removed.")
    elif action == "reset":
        field.reset_reminder(vid)
        flash(request, f"{v['office_name']}: the {field.REMINDER_DAYS}-day reminder starts again from today.")
    return RedirectResponse("/field#manage-fu", status_code=303)


@app.get("/field.xlsx")
def field_export(request: Request):
    if (r := guard(request)):
        return r
    period, day, who, prac = _field_args(request)
    u = me(request)
    if not users.sees_all_field(u):
        who = u["id"]
    start, end, label, *_ = field.period_bounds(period, day)
    rows = field.visits_between(start, end, who, prac)
    hide = users.hidden_people(u)
    rows = [r for r in rows if r["user_id"] not in hide]
    data = field.export_xlsx(rows, f"Field visits · {label}" + ("" if users.sees_all_field(u) else f" · {u['name']}"))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="field-visits-{start.isoformat()}.xlsx"'})


# ---------- Field visit PDF reports ----------
def _report_scope(request):
    """(user, may see everyone, agents to choose from, practices to choose from)."""
    u = me(request)
    allp = db.practices()
    choices = sorted([q for q in db.keep_mine(allp) if not master_report._is_master(q, allp)], key=lambda q: q["name"].lower())
    everyone = users.sees_all_field(u)
    hide = users.hidden_people(u)
    agents = sorted([t for t in users.all_users() if (t.get("field") or t["id"] == u["id"]) and t["id"] not in hide],
                    key=lambda t: t["name"].lower()) if everyone else [u]
    return u, everyone, agents, choices, allp


@app.get("/field/report", response_class=HTMLResponse)
def field_report_form(request: Request):
    if (r := guard(request)):
        return r
    u, everyone, agents, choices, allp = _report_scope(request)
    opts = reviews.months_back(13)[::-1]
    return page(request, "field_report.html", everyone=everyone, agents=agents, practice_choices=choices,
                month_opts=[(k, date.fromisoformat(k + "-01").strftime("%B %Y")) for k in opts],
                month=request.query_params.get("m") if request.query_params.get("m") in opts else opts[0],
                options=field_report.OPTIONS, me_id=u["id"],
                maps_key=bool(field_report.report_key()), google=field_report.google_status(),
                report_key_own=bool(db.get_setting("maps_report_key")))


@app.post("/field/report/key")
def field_report_key(request: Request, key: str = Form(""), clear: str = Form("")):
    if (r := guard(request)):
        return r
    if (me(request) or {}).get("role") != "admin":
        flash(request, "That needs admin access. Ask an admin.", "error")
    elif clear:
        db.set_setting("maps_report_key", None)
        flash(request, "Reports now use the Map's server key.")
    elif re.fullmatch(r"AIza[\w-]{30,}", key.strip()):
        db.set_setting("maps_report_key", key.strip())
        db.set_setting("field_report_google", None)
        flash(request, "Saved. The next report uses this key for maps and miles.")
    else:
        flash(request, "That doesn't look like a Google API key (they start with AIza).", "error")
    return RedirectResponse("/field/report#report-google", status_code=303)


@app.get("/field/report.pdf")
def field_report_pdf(request: Request, m: str = "", agent: str = "", practice: str = ""):
    if (r := guard(request)):
        return r
    u, everyone, agents, choices, allp = _report_scope(request)
    month = reviews.valid_month(m) or date.today().isoformat()[:7]
    agent_id = int(agent) if agent.isdigit() else None
    if not everyone or (agent_id and agent_id not in {a["id"] for a in agents}):
        agent_id = u["id"]                     # people without manager access only ever get their own visits
    pid = int(practice) if practice.isdigit() and int(practice) in {q["id"] for q in choices} else None
    q = request.query_params
    opts = {k: q.get(k) == "1" for k, *_ in field_report.OPTIONS}
    master = next((p for p in allp if master_report._is_master(p, allp)), None)
    brand_pid = pid or (master["id"] if master else None)
    pname = next((p["name"] for p in choices if p["id"] == pid), "")
    aname = next((a["name"] for a in agents if a["id"] == agent_id), "") if agent_id else ""
    try:
        data = field_report.build(month, agent_id, pid, opts, brand_pid=brand_pid, practice_name=pname, agent_name=aname)
    except ImportError:
        flash(request, "PDF reports need the server updated (the PDF library isn't installed yet). Run the update command.", "error")
        return RedirectResponse("/field/report", status_code=303)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{pname or 'all-practices'}-{aname or 'all-agents'}".lower()).strip("-")
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="field-visits-{month}-{slug}.pdf"'})


@app.post("/p/{pid}/brand/colors")
def brand_colors(request: Request, pid: int, primary: str = Form(""), accent: str = Form(""), reset: str = Form("")):
    if (r := guard(request)):
        return r
    if reset:
        db.set_setting(f"brand_colors_{pid}", None)
        flash(request, "Report colours reset; they come from the logo again.")
    else:
        field_report.set_colors(pid, primary, accent)
        flash(request, "Brand colours saved.")
    return RedirectResponse(f"/p/{pid}#brand-images", status_code=303)


# ---- Referral accounts: parent (brand) → location (Salesforce location account) → child accounts
def _acct_brands(u):
    return accounts.brands(users.practice_ids(u))


@app.get("/field/accounts", response_class=HTMLResponse)
def field_accounts(request: Request, p: int = 0, loc: str = "", a: str = "", f: str = "all", q: str = "", act: str = "all",
                   more: int = 0):
    if (r := guard(request)):
        return r
    u = me(request)
    accounts.sync_if_stale()
    blist = _acct_brands(u)
    brand = next((b for b in blist if b["id"] == p), None) or (blist[0] if blist else None)
    ctx = {"brands": blist, "brand": brand, "sync": accounts.status(), "sf_on": salesforce.connected(),
           "filters": accounts.FILTERS, "f": f if f in dict(accounts.FILTERS) else "all", "q": q.strip(), "act": act,
           "is_admin": u["role"] == "admin", "can_refresh": users.can(u, "refresh") or u["role"] in ("admin", "editor"),
           "sf_base": salesforce.instance_url(), "more": bool(more)}
    if not brand:
        if u["role"] == "admin":
            ctx["all_locations"] = _acct_all_locations()
        return page(request, "field_accounts.html", **ctx)
    pid = brand["id"]
    locs = accounts.locations(pid)
    loc_row = next((l for l in locs if l["sf_id"] == loc), None)
    rows_all = accounts.accounts(pid)
    rows = [x for x in rows_all if not loc_row or x["loc"] == loc_row["sf_id"]]
    see_all = users.sees_all_field(u)
    per_loc = {}
    for x in rows_all:
        if x["source"] != "npi":
            d = per_loc.setdefault(x["loc"], {"n": 0, "v": 0})
            d["n"] += 1
            d["v"] += x["visits"]
    sel = next((x for x in rows_all if x["key"] == a), None) if a else None
    if sel is None and a.startswith("n:"):
        sel = accounts.find(pid, a)
    shown = accounts.sort_accounts(accounts.filter_accounts(rows, ctx["f"]))
    kinds = ("visit",) if act == "visits" else ("note",) if act == "notes" else ("visit", "note")
    if sel:
        oids = [sel["office_id"]] if sel["office_id"] else []
        feed = accounts.activity(pid, rows_all, oids, limit=60, kinds=kinds, keys=[sel["key"]])
    elif loc_row:
        oids = [x["office_id"] for x in rows if x["office_id"]]
        feed = accounts.activity(pid, rows_all, oids, limit=40, kinds=kinds, keys=[x["key"] for x in rows])
    else:
        feed = accounts.activity(pid, rows_all, None, limit=40, kinds=kinds)
    if not see_all:                       # people who see only their own visits see only their own here too
        feed = [e for e in feed if e["kind"] != "visit" or e["who"] == u["name"]]
    ctx.update(pid=pid, locs=locs, loc=loc_row, per_loc=per_loc, rows=shown if more else shown[:60], total=len(shown),
               counts=accounts.counts(rows), sel=sel, feed=feed, results=accounts.search(pid, q) if q.strip() else None,
               people=accounts.people_met(sel["office_id"]) if sel else [], size=accounts.office_size(sel["office_id"]) if sel else None,
               all_rows=rows_all)
    if u["role"] == "admin":
        ctx["all_locations"] = _acct_all_locations()
        ctx["practices"] = db.practices()
    return page(request, "field_accounts.html", **ctx)


def _acct_all_locations():
    with db.conn() as c:
        return [dict(x) for x in c.execute("SELECT * FROM acct_locations WHERE gone_at='' ORDER BY name")]


@app.get("/field/accounts/search.json")
def field_accounts_search(request: Request, p: int = 0, q: str = ""):
    if not authed(request):
        return Response(status_code=401)
    u = me(request)
    if p not in {b["id"] for b in _acct_brands(u)}:
        return {"accounts": [], "people": [], "notes": []}
    res = accounts.search(p, q)
    if not users.sees_all_field(u):
        res["notes"] = [n for n in res["notes"] if n["kind"] != "visit" or n["who"] == u["name"]]
    slim = lambda x: {k: x.get(k) for k in ("key", "name", "loc", "loc_name", "address", "source", "refs", "last", "role", "account",
                                             "who", "at", "where", "text", "kind")}
    return {k: [slim(x) for x in v] for k, v in res.items() if isinstance(v, list)} | {"total": res.get("total", 0)}


@app.post("/field/accounts/note")
async def field_accounts_note(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    u = me(request)
    pid = int(form.get("p") or 0)
    back = form.get("back") or "/field/accounts"
    back = back if back.startswith("/field/accounts") else "/field/accounts"
    if pid not in {b["id"] for b in _acct_brands(u)}:
        return RedirectResponse("/field/accounts", status_code=303)
    try:
        accounts.add_note(pid, form.get("key") or "", form.get("text") or "", u, PUBLIC_URL)
        flash(request, "Note saved" + (" (copied to Salesforce in a moment)." if str(form.get("key", "")).startswith("sf:") else "."))
    except accounts.AcctError as e:
        flash(request, str(e), "error")
    return RedirectResponse(back, status_code=303)


@app.get("/field/accounts/log")
def field_accounts_log(request: Request, p: int = 0, key: str = ""):
    if (r := guard(request)):
        return r
    u = me(request)
    if not u.get("field"):
        flash(request, "Field visits aren't switched on for you. Ask an admin to turn them on on the Team page.", "error")
        return RedirectResponse("/field/accounts", status_code=303)
    try:
        oid = accounts.office_for(key, u["name"])
    except (accounts.AcctError, field.FieldError) as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/field/accounts?p={p}", status_code=303)
    back = f"/field/accounts?p={p}&a={urllib.parse.quote(key)}"
    return RedirectResponse("/field/log?" + urllib.parse.urlencode({"office": oid, "practice": p, "back": back}), status_code=303)


@app.post("/field/accounts/sync")
def field_accounts_sync(request: Request):
    if (r := guard(request)):
        return r
    u = me(request)
    if not (users.can(u, "refresh") or u["role"] in ("admin", "editor")):
        return RedirectResponse("/field/accounts", status_code=303)
    accounts.sync_in_background(u["name"])
    flash(request, "Reading accounts from Salesforce now; this page fills in within a few minutes.")
    return RedirectResponse(request.headers.get("referer") or "/field/accounts", status_code=303)


@app.post("/field/accounts/settings")
async def field_accounts_settings(request: Request):
    if (r := guard(request)):
        return r
    u = me(request)
    if u["role"] != "admin":
        return RedirectResponse("/field/accounts", status_code=303)
    form = await request.form()
    try:
        for k in form.keys():
            if k.startswith("loc_"):
                v = str(form.get(k) or "")
                accounts.set_location_practice(k[4:], int(v) if v.isdigit() else None)
            elif k.startswith("parent_") and k[7:].isdigit():
                accounts.set_parent(int(k[7:]), form.get(k))
        flash(request, "Saved how Salesforce locations match practices.")
    except accounts.AcctError as e:
        flash(request, str(e), "error")
    return RedirectResponse(request.headers.get("referer") or "/field/accounts", status_code=303)


@app.get("/field/log", response_class=HTMLResponse)
def field_log_form(request: Request, place: str = "", npi: str = "", office: str = "", error: str = ""):
    if (r := guard(request)):
        return r
    ev_id = request.query_params.get("event", "")
    from_ev = cal.get(int(ev_id)) if ev_id.isdigit() else None
    if from_ev and not (office or place or npi):      # picked from the calendar: the event says which office
        office = str(from_ev["office_id"] or "")
        npi, place = from_ev["npi_key"] or "", from_ev["place_id"] or ""
    try:
        o = (field.office(int(office)) if office.isdigit() else None) or field.office_draft(place or None, npi or None)
    except field.FieldError as e:
        flash(request, str(e), "error")
        return RedirectResponse("/map", status_code=303)
    if from_ev and not o.get("name"):
        o = {**o, "name": from_ev["place_label"] or "", "address": from_ev["place_address"] or o.get("address", "")}
    history = []
    if o.get("id"):
        with db.conn() as c:
            history = [dict(x) for x in c.execute("SELECT * FROM visits WHERE office_id=? ORDER BY visited_at DESC LIMIT 5",
                                                  (o["id"],))]
        history = field.with_people(history)
        names = {q['id']: q['name'] for q in db.practices()}
        for h in history:
            h['practice_name'] = names.get(h.get('practice_id'), '')
    allp = db.practices()
    choices = sorted([q for q in db.keep_mine(allp) if not master_report._is_master(q, allp)], key=lambda q: q["name"].lower())
    chosen = request.query_params.get("practice", "")
    pre = None
    if (ev := from_ev):
        team = {(x.get("email") or "").lower() for x in users.all_users()}
        team.add(microsoft.mailbox().lower())
        pre = {"event_id": ev["id"], "dropped": ev["extra"] if ev["category"] in ("referral", "office") else "",
               "visited_at": ev["starts"][:16] if not ev["all_day"] else "",
               "virtual": bool(ev["teams"] or ev["where_kind"] == "online"), "subject": ev["subject"],
               "cost": ev["cost"], "people": [{"name": p["name"], "email": p["email"], "role": ""} for p in ev["people"]
                                              if p["email"] not in team and p.get("source") != "team"]}
        chosen = chosen or str(ev["practice_id"] or "")
    suggested = (int(chosen) if chosen.isdigit() else None) or next(
        (h["practice_id"] for h in history if h.get("practice_id")), None) or field.suggested_practice(o, (me(request) or {}).get("id"))
    return page(request, "field_log.html", o=o, history=history, ratings=field.RATING_LABELS, roles=field.MET_ROLES,
                max_people=field.MAX_PEOPLE, chips=field.DROP_CHIPS, staff_ranges=field.STAFF_RANGES, followup_choices=field.FOLLOWUP_DAYS, minute_choices=field.MINUTE_CHOICES, browser_key=places.browser_key(), map_id=places.map_id(), error=error,
                ai_on=ai.available(), back=request.query_params.get("back", ""), practice_choices=choices,
                practice_options=field.practice_options(choices), suggested_practice=suggested, pre=pre,
                cal_choices=cal.visit_candidates(me(request)) if not pre else [], **_form_ctx(me(request)),
                acct_label=accounts.labels_for_offices({o["id"]}).get(o["id"]) if o.get("id") else None)


@app.post("/field/visits")
async def field_log(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    data = {k: form.get(k) for k in ("office_id", "place_id", "npi_key", "name", "address", "office_lat", "office_lng", "visited_at",
                                     "met_name", "met_role", "rating", "dropped", "cost", "followup", "followup_days", "notes", "minutes",
                                     "virtual", "practice_id", "practice_required", "doctors", "staff",
                                     "phone_lat", "phone_lng", "phone_acc")}
    names = form.getlist("person_name")
    if names:
        emails, roles = form.getlist("person_email"), form.getlist("person_role")
        data["people"] = [{"name": n, "email": emails[i] if i < len(emails) else "", "role": roles[i] if i < len(roles) else ""}
                          for i, n in enumerate(names)]
    up = form.get("receipt")
    blob = await up.read() if up is not None and getattr(up, "filename", "") else None
    try:
        vid = await asyncio.to_thread(field.log_visit, me(request), data, blob)
    except field.FieldError as e:
        q = urllib.parse.urlencode({k: v for k, v in (("office", data["office_id"]), ("place", data["place_id"]),
                                                       ("npi", data["npi_key"]), ("error", str(e))) if v})
        return RedirectResponse(f"/field/log?{q}", status_code=303)
    u = me(request)
    if dept.uses_forms(u) and (form.get("ck_kind") or "") in checklists.FORMS:
        dept.save_form(vid, form.get("ck_kind"), form, u)
    await asyncio.to_thread(dept.on_logged, vid, u, PUBLIC_URL)
    salesforce.send_in_background(PUBLIC_URL)
    if str(form.get("event_id") or "").isdigit():
        cal.set_status(int(form.get("event_id")), visit_id=vid)
    v = field.visit(vid)
    where = " (on site)" if v["on_site"] == 1 else f" ({v['distance_mi']:g} mi from the office)" if v["on_site"] == 0 else ""
    flash(request, f"Virtual visit with {v['office_name']} logged." if v.get("virtual") else f"Visit to {v['office_name']} logged{where}.")
    if v.get("followup") and v["followup"] >= date.today().isoformat():
        request.session["fu_offer"] = vid
    back = form.get("back") or ""
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/field?period=day",
                            status_code=303)


def _can_change_visit(v, u):
    return dept.can_change(u, v)


@app.get("/field/visits/{vid}/edit", response_class=HTMLResponse)
def field_edit_form(request: Request, vid: int, error: str = ""):
    if (r := guard(request)):
        return r
    v, u = field.visit(vid), me(request)
    if not _can_change_visit(v, u):
        flash(request, "Only the person who logged a visit, their department's lead, Management or an admin can change it.", "error")
        return RedirectResponse("/field", status_code=303)
    allp = db.practices()
    choices = sorted([q for q in allp if not master_report._is_master(q, allp)], key=lambda q: q["name"].lower())
    opts = field.practice_options(choices)
    sel = next((o["value"] for o in opts if o["pid"] == v["practice_id"] and o["address"] == (v["practice_location"] or "")), None) \
        or next((o["value"] for o in opts if o["pid"] == v["practice_id"]), "")
    o = field.office(v["office_id"]) or {"id": v["office_id"], "name": v["office_name"], "address": v["office_address"]}
    back = request.query_params.get("back", "")
    return page(request, "field_log.html", o=o, ev=v, history=[], ratings=field.RATING_LABELS, roles=field.MET_ROLES,
                max_people=field.MAX_PEOPLE, chips=field.DROP_CHIPS, staff_ranges=field.STAFF_RANGES, followup_choices=field.FOLLOWUP_DAYS, minute_choices=field.MINUTE_CHOICES, browser_key="", map_id=places.map_id(), error=error,
                ai_on=ai.available(), back=back if back.startswith("/") and not back.startswith("//") else "/field",
                practice_choices=choices, practice_options=opts, suggested_practice=v["practice_id"],
                selected_value=sel, pre=None, **_form_ctx(u, v))


def _form_ctx(u, v=None):
    """The Operations checklists for the log/edit form, when this person uses them."""
    show = dept.uses_forms(u) and (v is None or dept.of(u) == checklists.DEPT or (v.get("department") or "") == checklists.DEPT
                                   or dept.form_for(v["id"]) is not None or dept.is_admin(u) or dept.is_management(u))
    f = dept.form_for(v["id"]) if (v and show) else None
    return {"ck_show": show, "ck_forms": checklists.FORMS, "ck_saved": f, "ck_item_id": checklists.item_id,
            "ops_visit": dept.of(u) == checklists.DEPT and (v is None or v["user_id"] == u["id"])}


@app.post("/field/visits/{vid}")
async def field_edit(request: Request, vid: int):
    if (r := guard(request)):
        return r
    v, u = field.visit(vid), me(request)
    if not _can_change_visit(v, u):
        flash(request, "Only the person who logged a visit, their department's lead, Management or an admin can change it.", "error")
        return RedirectResponse("/field", status_code=303)
    form = await request.form()
    data = {k: form.get(k) for k in ("visited_at", "rating", "dropped", "cost", "followup", "followup_days", "notes", "minutes", "virtual", "practice_id",
                                     "practice_required", "remove_receipt", "doctors", "staff")}
    names = form.getlist("person_name")
    emails, roles = form.getlist("person_email"), form.getlist("person_role")
    data["people"] = [{"name": n, "email": emails[i] if i < len(emails) else "", "role": roles[i] if i < len(roles) else ""}
                      for i, n in enumerate(names)]
    up = form.get("receipt")
    blob = await up.read() if up is not None and getattr(up, "filename", "") else None
    back = form.get("back") or ""
    back = back if back.startswith("/") and not back.startswith("//") else "/field"
    try:
        nv = await asyncio.to_thread(field.update_visit, vid, data, blob, "", u.get("name", ""))
    except field.FieldError as e:
        return RedirectResponse(f"/field/visits/{vid}/edit?" + urllib.parse.urlencode({"error": str(e), "back": back}), status_code=303)
    if dept.uses_forms(u) and "ck_kind" in form:
        k = form.get("ck_kind") or ""
        dept.save_form(vid, k if k in checklists.FORMS else "", form, u)
    await asyncio.to_thread(dept.on_edited, vid, u, PUBLIC_URL)
    if nv.get("sf_task_id"):
        salesforce.send_in_background(PUBLIC_URL)
    flash(request, f"Visit to {nv['office_name']} updated{' (Salesforce is updated in a moment)' if nv.get('sf_task_id') else ''}.")
    return RedirectResponse(back, status_code=303)


@app.post("/field/visits/{vid}/approve")
def field_approve(request: Request, vid: int, note: str = Form("")):
    if (r := guard(request)):
        return r
    try:
        dept.approve(vid, me(request), note)
        flash(request, "Visit approved.")
    except dept.DeptError as e:
        flash(request, str(e), "error")
    return RedirectResponse(request.headers.get("referer") or "/field", status_code=303)


@app.post("/field/visits/{vid}/return")
async def field_send_back(request: Request, vid: int, note: str = Form("")):
    if (r := guard(request)):
        return r
    try:
        await asyncio.to_thread(dept.send_back, vid, me(request), note, PUBLIC_URL)
        flash(request, "Sent back with your note; they've been told.")
    except dept.DeptError as e:
        flash(request, str(e), "error")
    return RedirectResponse(request.headers.get("referer") or "/field", status_code=303)


@app.post("/field/alerts/seen")
def field_alerts_seen(request: Request):
    if (r := guard(request)):
        return r
    dept.mark_seen(me(request)["id"])
    return RedirectResponse(request.headers.get("referer") or "/field", status_code=303)


@app.post("/field/visits/{vid}/delete")
def field_delete(request: Request, vid: int):
    if (r := guard(request)):
        return r
    v, u = field.visit(vid), me(request)
    if _can_change_visit(v, u):
        field.delete_visit(vid)
        flash(request, "Visit removed.")
    else:
        flash(request, "Only the person who logged a visit, their department's lead, Management or an admin can remove it.", "error")
    return RedirectResponse(request.headers.get("referer") or "/field", status_code=303)


@app.post("/field/receipt/read")
async def field_receipt_read(request: Request):
    if not authed(request):
        return Response(status_code=401)
    from fastapi.responses import JSONResponse
    form = await request.form()
    up = form.get("receipt")
    if up is None:
        return JSONResponse({"error": "No photo."}, status_code=400)
    data = await up.read()
    try:
        got = await asyncio.to_thread(field.read_receipt, data)
    except (field.FieldError, ai.AIError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return got


@app.get("/field/receipts/{name}")
def field_receipt(request: Request, name: str):
    if not authed(request):
        return Response(status_code=401)
    p = field.receipt_path(name)
    if not p:
        return Response(status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})


@app.post("/field/route-order")
async def field_route_order(request: Request):
    if not authed(request):
        return Response(status_code=401)
    body = await request.json()
    start = body.get("start") or {}
    origin_text, start_xy = "", None
    if start.get("kind") == "home":
        u = users.get(me(request)["id"]) if me(request)["id"] else None
        if u and u.get("home_lat") is not None:
            start_xy, origin_text = (u["home_lat"], u["home_lng"]), f"{u['home_lat']},{u['home_lng']}"
        else:
            return {"error": "Add your home address on Your account first."}
    elif start.get("kind") == "office":
        o = next((x for x in places.offices() if str(x["id"]) == str(start.get("id")) and x["lat"] is not None), None)
        if o:
            start_xy, origin_text = (o["lat"], o["lng"]), f"{o['lat']},{o['lng']}"
    elif start.get("lat") is not None:
        try:
            start_xy = (float(start["lat"]), float(start["lng"]))
            origin_text = ""   # Google Maps starts from where the phone is
        except (TypeError, ValueError):
            pass
    stops = [s for s in (body.get("stops") or [])[:60] if isinstance(s, dict)]
    ordered = field.order_stops(start_xy, stops)
    return {"stops": ordered, "links": field.maps_links(origin_text, ordered)}


@app.post("/field/settings/budgets")
async def field_budgets(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    try:
        for k, v in form.items():
            if k.startswith("pb_") and k[3:].isdigit():
                field.set_practice_budget(int(k[3:]), field.money_in(v))
            elif k.startswith("ab_") and k[3:].isdigit():
                field.set_agent_budget(int(k[3:]), field.money_in(v))
        flash(request, "Field budgets saved.")
    except field.FieldError as e:
        flash(request, str(e), "error")
    return RedirectResponse("/field?period=month#budgets", status_code=303)


@app.post("/field/settings/links")
async def field_links(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    pairs = {int(k[2:]): (int(v) if str(v).isdigit() else None) for k, v in form.items()
             if k.startswith("o_") and k[2:].isdigit()}
    field.set_links(pairs)
    flash(request, "Offices linked to practices.")
    return RedirectResponse("/field?period=month#links", status_code=303)


@app.post("/account/home")
async def account_home(request: Request, address: str = Form("")):
    if (r := guard(request)):
        return r
    u = me(request)
    if not u["id"]:
        return RedirectResponse("/account", status_code=303)
    address = " ".join(address.split())[:200]
    lat = lng = None
    if address:
        got = await asyncio.to_thread(field.geocode_one, address)
        if not got:
            flash(request, "Couldn't find that address. Try the full street, city, state and ZIP.", "error")
            return RedirectResponse("/account#home", status_code=303)
        lat, lng = got
    with db.conn() as c:
        c.execute("UPDATE users SET home_address=?, home_lat=?, home_lng=? WHERE id=?", (address, lat, lng, u["id"]))
    flash(request, "Home address saved for routes." if address else "Home address removed.")
    return RedirectResponse("/account#home", status_code=303)


# ---------- reviews & comments ----------
def _num(v):
    v = str(v or "").strip()
    return int(v) if v.isdigit() else None


def _safe_back(v, default="/reviews"):
    v = str(v or "")
    return v if v.startswith("/") and not v.startswith("//") else default


POSTS_AS = {
    "google": "Posts on Google as {acct}, labeled “Response from the owner.” No personal name shows publicly; the hub records that {me} sent it.",
    "facebook": "Replies as the {acct} Facebook Page, not as you personally. The hub records that {me} sent it.",
    "instagram": "Replies as {acct} on Instagram. The hub records that {me} sent it.",
}


@app.get("/reviews", response_class=HTMLResponse)
def reviews_page(request: Request):
    if (r := guard(request)):
        return r
    q = request.query_params
    plist = reviews.practices()
    pid = _num(q.get("practice"))
    if pid not in {p["id"] for p in plist}:
        pid = None
    tab = "score" if q.get("tab") == "score" else "inbox"
    src = q.get("src") if q.get("src") in ("google", "facebook", "instagram") else "all"
    status = q.get("status") if q.get("status") in ("done", "reported") else "open"
    filt = q.get("filter") if q.get("filter") in ("under", "quiet") else "all"
    hm_opts = reviews.months_back(13)[::-1]                    # "Handled the month of", newest first
    hm_pick = q.get("hm") if q.get("hm") in hm_opts else ""
    hm = hm_pick or hm_opts[0]
    items, counts = reviews.inbox(pid, src, status, plist, month=hm_pick or None)
    sel = _num(q.get("item"))
    cur = next((i for i in items if i["id"] == sel), None) or (items[0] if items else None)
    if cur:
        cur["posts_as"] = POSTS_AS[cur["source"]].format(acct=cur["account_name"], me=(me(request) or {}).get("name", "you").split(" ")[0])
        cur["no_reply_hint"] = reviews.no_reply_hint(cur)
    sb_month = reviews.valid_month(q.get("m") or "") or None
    board = reviews.scoreboard(pid, plist=plist, month=sb_month)
    sb_months = [(k, reviews.month_label(k)) for k in reviews.months_back(24)[::-1]]
    rows = board["rows"]
    if filt == "under":
        rows = [x for x in rows if not x["met"]]
    elif filt == "quiet":
        rows = [x for x in rows if x["days"] is not None and x["days"] > 30]
    qs = {"practice": pid or "", "tab": tab, "src": src, "status": status, "m": sb_month or "", "hm": hm_pick}
    month_opts = reviews.months_back(13)[::-1][1:]
    with db.conn() as c:
        manual = {(m["practice_id"], m["month"]): m["count"] for m in c.execute("SELECT * FROM review_manual")}
    man_month = q.get("mm") if q.get("mm") in month_opts else month_opts[0]
    return page(request, "reviews.html", plist=plist, pid=pid, practice=next((p for p in plist if p["id"] == pid), None),
                tab=tab, src=src, status=status, filt=filt, items=items, counts=counts, cur=cur,
                stats=reviews.inbox_stats(pid, plist), board=board,
                hstats=reviews.month_handled(hm, pid, plist), hm=hm, hm_pick=hm_pick,
                hm_opts=[(k, reviews.month_label(k)) for k in hm_opts], rows=rows, qs=qs, sources=reviews.SOURCES,
                flag_text=reviews.FLAG_TEXT, duration=reviews.duration, inbox_status=reviews.last_status(),
                gbp_client=all(cx.gbp_client()), gbp_on=reviews.gbp_connected(), locations=reviews.locations(),
                gbp_redirect=_gbp_redirect(request), overdue=reviews.overdue_hours(), every=reviews.check_minutes(),
                month_opts=month_opts, man_month=man_month, manual=manual, month_label=reviews.month_label,
                meta_on=cx.meta_configured(), fb_linked=any(db.social_ids(p, "fb") or db.social_ids(p, "ig") for p in plist),
                reply_style=reviews.reply_style(pid) if pid else "",
                style_examples=len(reviews.past_replies(pid)) if pid else 0,
                reported_n=reviews.reported_count(pid, plist), report_reasons=reviews.REPORT_REASONS,
                report_tool=reviews.REPORT_TOOL, followup_days=reviews.REPORT_FOLLOWUP_DAYS,
                sb_month=sb_month or "", sb_months=sb_months)


@app.get("/reviews.xlsx")
def reviews_export(request: Request, m: str = ""):
    if (r := guard(request)):
        return r
    month = reviews.valid_month(m) or reviews.months_back(1)[0]
    data = reviews.export_xlsx(month=month)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="google-reviews-{month}.xlsx"'})


@app.get("/reviews/report", response_class=HTMLResponse)
def reviews_month_report(request: Request, m: str = "", practice: str = ""):
    if (r := guard(request)):
        return r
    plist = reviews.practices()
    pid = int(practice) if practice.isdigit() and int(practice) in {p["id"] for p in plist} else None
    month = reviews.valid_month(m) or reviews.months_back(1)[0]
    rep = reviews.month_report(month, pid, plist)
    return page(request, "reviews_report.html", rep=rep, board=rep["board"], pid=pid,
                practice=next((p for p in plist if p["id"] == pid), None), month=month,
                months=[(k, reviews.month_label(k)) for k in reviews.months_back(24)[::-1]])


DONE_MSG = {"reply": "Reply posted.", "like": "Liked. Marked done.", "hide": "Comment hidden. Marked done.",
            "done": "Marked done.", "reopen": "Moved back to Needs reply.",
            "report": "Marked as reported. Finish the report in Google's form (it opened in a new tab); "
                      "the hub checks every half hour and marks it Removed by Google when it's gone.",
            "unreport": "Report undone."}


@app.post("/reviews/items/{iid}/{action}")
async def reviews_act(request: Request, iid: int, action: str):
    if (r := guard(request)):
        return r
    form = await request.form()
    from fastapi.responses import JSONResponse
    if action == "draft":
        if not ai.available():
            return JSONResponse({"error": "AI isn't switched on."}, status_code=422)
        try:
            return {"draft": await asyncio.to_thread(reviews.draft_reply, iid, str(form.get("tone") or "") or None,
                                                     str(form.get("current") or ""))}
        except (reviews.ReviewsError, ai.AIError) as e:
            return JSONResponse({"error": str(e)}, status_code=422)
    back = _safe_back(form.get("back"))
    message = str(form.get("message") or "")
    if action not in DONE_MSG:
        return RedirectResponse(back, status_code=303)
    try:
        if action == "report":
            reviews.report(iid, str(form.get("reason") or ""), str(form.get("note") or ""), actor(request))
        elif action == "unreport":
            reviews.unreport(iid)
        else:
            await asyncio.to_thread(reviews.act, iid, action, actor(request), message)
        flash(request, DONE_MSG[action])
    except (reviews.ReviewsError, cx.ConnectorError) as e:
        if action == "reply" and message.strip():
            reviews.save_draft(iid, message)          # keep what they wrote
        flash(request, str(e), "error")
        sep = "&" if "?" in back else "?"
        back = f"{back}{sep}item={iid}" if "item=" not in back else back
    if action == "reopen":
        back = back.replace("status=done", "status=open")
        back = f"{back}{'&' if '?' in back else '?'}item={iid}"
    return RedirectResponse(back, status_code=303)


@app.post("/reviews/sync")
async def reviews_sync(request: Request, back: str = Form("/reviews")):
    if (r := guard(request)):
        return r
    st = await asyncio.to_thread(reviews.sync_inbox)
    if st.get("busy"):
        flash(request, "A check is already running. Refresh in a minute.", "error")
    elif not st.get("accounts"):
        flash(request, "Nothing to check yet: link a Google location, Facebook Page or Instagram account to a practice first.",
              "error")
    else:
        msg = f"Checked {st['accounts']} account{'s' if st['accounts'] != 1 else ''}: {st['new']} new."
        if st.get("error_count"):
            msg += f" {st['error_count']} couldn't be read — {st['errors'][0]}"
        flash(request, msg, "error" if st.get("error_count") else "ok")
    return RedirectResponse(_safe_back(back), status_code=303)


@app.post("/reviews/goals")
async def reviews_goals(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    plist = reviews.practices()
    try:
        if form.get("apply") == "all":
            n = _num(form.get("all_goal"))
            if n is None:
                raise reviews.ReviewsError("Type a goal first.")
            reviews.set_goals({0: n, **{p["id"]: n for p in plist}})
            flash(request, f"Every practice's goal is now {n} a month, from {reviews.month_label(reviews.month_key(date.today()))} on.")
        else:
            cur = reviews.month_key(date.today())
            changes = {}
            for p in plist:
                v = form.get(f"g_{p['id']}")
                if v is None or str(v).strip() == "":
                    continue
                n = _num(v)
                if n is None:
                    raise reviews.ReviewsError(f"The goal for {p['name']} isn't a whole number.")
                if n != reviews.goal_for(p["id"], cur):
                    changes[p["id"]] = n
            reviews.set_goals(changes)
            flash(request, f"{len(changes)} goal{'s' if len(changes) != 1 else ''} changed." if changes else "No goals changed.")
    except reviews.ReviewsError as e:
        flash(request, str(e), "error")
    return RedirectResponse("/reviews?tab=score#goals", status_code=303)


@app.post("/reviews/manual")
async def reviews_manual(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    month = str(form.get("month") or "")
    counts = {}
    try:
        for p in reviews.practices():
            v = form.get(f"m_{p['id']}")
            if v is None:
                continue
            v = str(v).strip()
            if v and not v.isdigit():
                raise reviews.ReviewsError(f"The count for {p['name']} isn't a whole number.")
            counts[p["id"]] = int(v) if v else None
        reviews.set_manual(month, counts)
        flash(request, f"Counts for {reviews.month_label(month)} saved.")
    except reviews.ReviewsError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/reviews?tab=score&mm={month}#manual", status_code=303)


@app.post("/reviews/locations")
async def reviews_locations(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    pairs = {k[5:]: _num(v) for k, v in form.items() if k.startswith("loc::")}
    reviews.link_locations(pairs)
    flash(request, "Google locations linked to practices. Their reviews come in on the next check.")
    return RedirectResponse("/reviews#google-setup", status_code=303)


@app.post("/reviews/locations/refresh")
async def reviews_locations_refresh(request: Request):
    if (r := guard(request)):
        return r
    try:
        n = await asyncio.to_thread(reviews.refresh_locations)
        flash(request, f"Found {n} Google location{'s' if n != 1 else ''}. Check the practice next to each, then save.")
    except cx.ConnectorError as e:
        flash(request, f"Couldn't list Google locations: {e}", "error")
    return RedirectResponse("/reviews#google-setup", status_code=303)


@app.post("/reviews/style")
async def reviews_style(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    pid = _num(form.get("practice"))
    if pid in {p["id"] for p in reviews.practices()}:
        reviews.set_reply_style(pid, str(form.get("style") or ""))
        flash(request, "Reply style saved. New drafts for this practice follow it.")
    return RedirectResponse(f"/reviews?practice={pid or ''}#reply-style", status_code=303)


@app.post("/reviews/settings")
async def reviews_settings(request: Request, overdue: str = Form(""), every: str = Form("")):
    if (r := guard(request)):
        return r
    o, e = _num(overdue), _num(every)
    if o is None or not 1 <= o <= 720 or e is None or not 10 <= e <= 1440:
        flash(request, "Overdue time should be 1–720 hours and the check every 10–1440 minutes.", "error")
    else:
        db.set_setting("inbox_overdue_hours", str(o))
        db.set_setting("inbox_check_minutes", str(e))
        flash(request, "Inbox settings saved.")
    return RedirectResponse("/reviews#inbox-setup", status_code=303)


# ---------- Salesforce (field visits) ----------
def _sf_redirect(request: Request):
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return base + "/salesforce/callback"


@app.post("/connections/salesforce")
async def save_salesforce(request: Request, client_id: str = Form(""), client_secret: str = Form(""),
                          login_url: str = Form("")):
    if (r := guard(request)):
        return r
    client_id, client_secret, login_url = client_id.strip(), client_secret.strip(), login_url.strip().rstrip("/")
    if len(client_id) < 20 or len(client_secret) < 10:
        flash(request, "That doesn't look like a Salesforce consumer key and secret. Copy both from the External Client App.", "error")
    elif login_url and not re.match(r"^https://[a-z0-9.-]+\.(salesforce|force)\.com$", login_url):
        flash(request, "The sign-in address should look like https://login.salesforce.com or https://yourcompany.my.salesforce.com.", "error")
    else:
        db.set_setting("sf_client_id", client_id)
        db.set_setting("sf_client_secret", client_secret)
        db.set_setting("sf_login_url", login_url or None)
        flash(request, "Saved. Now click “Sign in to Salesforce”.")
    return RedirectResponse("/connections#salesforce", status_code=303)


@app.get("/salesforce/connect")
def salesforce_connect(request: Request):
    if (r := guard(request)):
        return r
    if not all(salesforce.client()):
        flash(request, "Save the Salesforce consumer key and secret first.", "error")
        return RedirectResponse("/connections#salesforce", status_code=303)
    state = os.urandom(16).hex()
    verifier, challenge = salesforce.pkce_pair()
    request.session["sf_state"], request.session["sf_verifier"] = state, verifier
    return RedirectResponse(salesforce.auth_url(_sf_redirect(request), state, challenge), status_code=303)


@app.get("/salesforce/callback")
async def salesforce_callback(request: Request, code: str = "", state: str = "", error: str = "",
                              error_description: str = ""):
    if (r := guard(request)):
        return r
    expected, verifier = request.session.pop("sf_state", None), request.session.pop("sf_verifier", None)
    if error:
        flash(request, f"Salesforce sign-in didn't finish: {error_description or error}.", "error")
    elif not expected or not hmac.compare_digest(state, expected):
        flash(request, "That Salesforce sign-in link expired. Try again.", "error")
    else:
        try:
            who = await asyncio.to_thread(salesforce.exchange_code, code, _sf_redirect(request), verifier)
            n = len(salesforce.pending_ids(10000))
            flash(request, f"Salesforce connected as {who.get('display_name') or who.get('username') or 'you'}. "
                           f"{n} visit{'s' if n != 1 else ''} waiting; click “Send visits now” or they go within 15 minutes.")
        except salesforce.SFError as e:
            flash(request, f"Salesforce sign-in failed: {e}", "error")
    return RedirectResponse("/connections#salesforce", status_code=303)


@app.post("/salesforce/disconnect")
def salesforce_disconnect(request: Request):
    if (r := guard(request)):
        return r
    salesforce.disconnect()
    flash(request, "Salesforce disconnected. Visits already sent stay in Salesforce.")
    return RedirectResponse("/connections#salesforce", status_code=303)


@app.post("/salesforce/send")
async def salesforce_send(request: Request):
    if (r := guard(request)):
        return r
    if not salesforce.connected():
        flash(request, "Connect Salesforce first.", "error")
    else:
        n = len(salesforce.pending_ids(10000))
        salesforce.send_in_background(PUBLIC_URL, 500)
        flash(request, f"Sending {n} visit{'s' if n != 1 else ''} to Salesforce in the background. "
                       "Refresh in a minute to see the result here." if n else "Nothing waiting; every visit is already in Salesforce.")
    back = (await request.form()).get("back") or "/connections#salesforce"
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/connections", status_code=303)


def _gbp_redirect(request: Request):
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return base + "/google/business/callback"


@app.post("/connections/gbp")
async def save_gbp(request: Request, client_id: str = Form(""), client_secret: str = Form("")):
    if (r := guard(request)):
        return r
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com") or len(client_secret) < 10:
        flash(request, "That doesn't look like a Google OAuth client. The client ID ends in .apps.googleusercontent.com.", "error")
    else:
        db.set_setting("gbp_client_id", client_id)
        db.set_setting("gbp_client_secret", client_secret)
        flash(request, "Saved. Now click “Sign in with Google” with an account that manages the practices' profiles.")
    return RedirectResponse("/reviews#google-setup", status_code=303)


@app.get("/google/business/connect")
def gbp_connect(request: Request):
    if (r := guard(request)):
        return r
    if not all(cx.gbp_client()):
        flash(request, "Save the Google OAuth client first.", "error")
        return RedirectResponse("/reviews#google-setup", status_code=303)
    state = os.urandom(16).hex()
    request.session["gbp_state"] = state
    back = request.query_params.get("back", "")
    request.session["gbp_back"] = back if back.startswith("/") and not back.startswith("//") else ""
    return RedirectResponse(cx.gbp_auth_url(_gbp_redirect(request), state), status_code=303)


@app.get("/google/business/callback")
async def gbp_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if (r := guard(request)):
        return r
    expected = request.session.pop("gbp_state", None)
    if error:
        flash(request, f"Google sign-in was cancelled ({error}).", "error")
    elif not expected or not hmac.compare_digest(state, expected):
        flash(request, "That Google sign-in link expired. Try again.", "error")
    else:
        try:
            db.set_setting("gbp_refresh_token", await asyncio.to_thread(cx.gbp_exchange_code, code, _gbp_redirect(request)))
            n = await asyncio.to_thread(reviews.refresh_locations)
            flash(request, f"Google connected. {n} location{'s' if n != 1 else ''} found; check which practice each belongs to. "
                           "Search & Maps numbers are being pulled now.")
            visibility.refresh_in_background()
        except cx.ConnectorError as e:
            flash(request, f"Google sign-in worked, but listing locations failed: {e}", "error")
    back = request.session.pop("gbp_back", "") or "/reviews#google-setup"
    return RedirectResponse(back, status_code=303)


@app.post("/google/business/disconnect")
def gbp_disconnect(request: Request):
    if (r := guard(request)):
        return r
    db.set_setting("gbp_refresh_token", None)
    flash(request, "Google reviews disconnected. Reviews and counts already pulled are kept.")
    return RedirectResponse("/reviews#google-setup", status_code=303)


# ---------- Search & Maps (Search Console + Business Profile performance) ----------
@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request):
    if (r := guard(request)):
        return r
    q = request.query_params
    plist = reviews.practices()
    pid = _num(q.get("practice"))
    if pid not in {p["id"] for p in plist}:
        pid = None
    opts = visibility.months_back(13)[::-1]
    month = q.get("m") if q.get("m") in opts else opts[0]
    view = q.get("view") if q.get("view") in ("up", "down", "new") else "top"
    data = visibility.detail(pid, month) if pid else visibility.overview(month, plist)
    return page(request, "search.html", plist=plist, pid=pid, practice=next((p for p in plist if p["id"] == pid), None),
                month=month, month_opts=[(k, reviews.month_label(k)) for k in opts], d=data, view=view,
                connected=visibility.connected(), gbp_client=all(cx.gbp_client()), status=visibility.status(),
                running=visibility.running(), sites=visibility.sites(), domain=visibility.domain,
                locations=reviews.locations(), any_data=visibility.any_data(), all_practices=plist,
                back_url="/search?" + urllib.parse.urlencode({"practice": pid or "", "m": month}))


@app.post("/search/refresh")
def search_refresh(request: Request, back: str = Form("/search")):
    if (r := guard(request)):
        return r
    if not visibility.connected():
        flash(request, "Connect Google first (Reviews → Google setup).", "error")
    elif visibility.refresh_in_background():
        flash(request, "Pulling the latest Search Console and Google profile numbers. The first run takes a few minutes; refresh this page in a bit.")
    else:
        flash(request, "A refresh is already running.")
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/search", status_code=303)


@app.post("/search/sites")
async def search_sites(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    known = {s["site"] for s in visibility.sites()}
    valid = {str(p["id"]) for p in reviews.practices()}
    n = 0
    for k, v in form.items():
        if k.startswith("site:") and k[5:] in known:
            visibility.set_site_practice(k[5:], int(v) if v in valid else None)
            n += 1
    if n:
        brand.sync_websites(actor(request))
    flash(request, "Saved which website belongs to which practice." if n else "Nothing to save.")
    return RedirectResponse("/search#setup", status_code=303)


@app.get("/search.xlsx")
def search_xlsx(request: Request, m: str = ""):
    if (r := guard(request)):
        return r
    month = reviews.valid_month(m) or date.today().isoformat()[:7]
    return Response(visibility.export_xlsx(month, reviews.practices()), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="search-and-maps-{month}.xlsx"'})


# ---------- Calendar (Outlook invites through Microsoft 365) ----------
def _cal_practices():
    allp = db.practices()
    return sorted([q for q in db.keep_mine(allp) if not master_report._is_master(q, allp)], key=lambda q: q["name"].lower())


def _can_schedule(u):
    return bool(u) and (bool(u.get("field")) or users.rank(u["role"]) >= users.rank("editor"))


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, view: str = "month", d: str = "", practice: str = "", category: str = "",
                  mine: str = "", fu: str = "1", event: str = "", day: str = ""):
    if (r := guard(request)):
        return r
    u = me(request)
    today = date.today()
    try:
        focus = date.fromisoformat(d) if d else today
    except ValueError:
        focus = today
    view = view if view in ("month", "week", "list") else "month"
    pid = int(practice) if practice.isdigit() else None
    category = category if category in cal.CAT else ""
    if view == "month":
        start, end, weeks = cal.month_grid(focus.year, focus.month)
        prev_d = (focus.replace(day=1) - timedelta(days=1)).replace(day=1)
        next_d = (focus.replace(day=28) + timedelta(days=4)).replace(day=1)
        title = focus.strftime("%B %Y")
    elif view == "week":
        start = focus - timedelta(days=(focus.weekday() + 1) % 7)
        end = start + timedelta(days=6)
        weeks = [[start + timedelta(days=i) for i in range(7)]]
        prev_d, next_d = start - timedelta(days=7), start + timedelta(days=7)
        title = f"Week of {start.strftime('%b')} {start.day}" + (f"–{end.day}" if end.month == start.month else f" – {end.strftime('%b')} {end.day}")
    else:
        start, end, weeks = focus, focus + timedelta(days=59), []
        prev_d, next_d = focus - timedelta(days=60), focus + timedelta(days=60)
        title = f"From {focus.strftime('%b')} {focus.day}, {focus.year}"
    items = cal.items_between(start, end, u, pid, category, mine == "1", fu == "1")
    marks = cal.marks_between(start, end)
    by_day = {}
    for i in items:
        by_day.setdefault(i["day"], []).append(i)
    try:
        picked_day = date.fromisoformat(day) if day else (today if start <= today <= end else start)
    except ValueError:
        picked_day = today
    sel = cal.get(int(event)) if event.isdigit() else None
    if sel and not cal.listed(sel, u):
        sel = None                                   # not on their calendar
    if sel:
        sel["practice_name"] = (db.practice(sel["practice_id"]) or {}).get("name", "")
        sel["can_change"] = cal.can_change(sel, u)
        sel["mine"] = cal.owns(sel, u)
        if not sel["mine"]:
            sel["my_notes"] = ""           # personal notes never leave the person who wrote them
        sel["show"] = cal.visible(sel, u)
        if sel["status"] == "sent":
            cal.refresh_soon(sel["id"])
    qs = {"view": view, "d": focus.isoformat(), "practice": practice, "category": category, "mine": mine, "fu": fu}
    by_ids = {i.get("by") for i in items}
    people_key = [(t["name"], t["cal_color"]) for t in users.all_users() if t.get("cal_color") and t["id"] in by_ids]
    return page(request, "calendar.html", people_key=people_key, view=view, focus=focus, title=title, weeks=weeks, by_day=by_day, items=items,
                start=start, end=end, today=today, prev_d=prev_d, next_d=next_d, practices=_cal_practices(), pid=pid,
                category=category, mine=mine == "1", fu=fu == "1", cats=cal.CATEGORIES, sel=sel, picked_day=picked_day,
                upcoming=cal.upcoming(u, 6, pid, mine == "1"), qs=qs, can_schedule=_can_schedule(u),
                ms_on=microsoft.connected(), ms_mailbox=microsoft.mailbox(), followup_color=cal.FOLLOWUP_COLOR,
                marks=marks, next_marks=cal.next_marks(today))


def _cal_cats(u):
    """What someone can add: everything for editors and field agents, just personal time for everyone else."""
    return cal.CATEGORIES if _can_schedule(u) else [c for c in cal.CATEGORIES if c["key"] == cal.PERSONAL]


def _cal_form(request, ev=None, error="", form=None):
    u = me(request)
    pr = _cal_practices()
    qp = request.query_params
    cats = _cal_cats(u)
    if ev is None:
        start_d = qp.get("date") or date.today().isoformat()
        cat0 = qp.get("category") if qp.get("category") in {c["key"] for c in cats} else cats[0]["key"]
        kind0 = qp.get("kind") if qp.get("kind") in {k for k, _, _ in cal.types_for(cat0)} else ""
        pto = cat0 == cal.PERSONAL and kind0 == "pto"
        ev = {"id": None, "practice_id": int(qp["practice"]) if qp.get("practice", "").isdigit() else None,
              "category": cat0, "kind": kind0, "my_notes": "",
              "subject": "", "subject_custom": 0, "where_kind": "", "place_label": "", "place_address": "",
              "office_id": None, "npi_key": "", "place_id": "", "starts": f"{start_d}T10:00", "ends": f"{start_d}T10:45",
              "all_day": int(pto), "repeat": "", "repeat_until": "", "reminder": 60, "show_as": "oof" if pto else "busy", "teams": 0, "extra": "",
              "cost": None, "details": "", "private": 0, "log_reminder": 1, "people": [], "status": "new", "ms_id": ""}
        if u and u.get("email"):
            ev["people"] = [{"name": u["name"], "email": u["email"].lower(), "required": 1, "source": "team"}]
    if ev.get("id") and not cal.owns(ev, u):
        ev = dict(ev, my_notes="")
    return page(request, "calendar_form.html", ev=ev, practices=pr, cats=cats, cat_json=cal.form_json(),
                notes_mine=not ev.get("id") or cal.owns(ev, u),
                where_opts=cal.WHERE, repeats=cal.REPEATS, reminders=cal.REMINDERS, show_as=cal.SHOW_AS, error=error,
                ms_on=microsoft.connected(), ms_mailbox=microsoft.mailbox(), ms_user=microsoft.user(),
                tz=microsoft.timezone(), max_attach_mb=cal.MAX_ATTACHMENT // (1024 * 1024))


@app.get("/calendar/company", response_class=HTMLResponse)
def calendar_company(request: Request, year: str = ""):
    if (r := guard(request)):
        return r
    today = date.today()
    y = int(year) if year.isdigit() and 2000 < int(year) < 2100 else (today.year + 1 if today.month >= 11 else today.year)
    rows = cal.marks()
    have = {(m["day"], m["kind"]) for m in rows}
    hol = [{"key": k, "name": n, "day": d, "on": on, "have": (d.isoformat(), "holiday") in have} for k, n, d, on in cal.us_holidays(y)]
    upcoming = [m for m in rows if m["repeat"] or m["day"] >= today.isoformat()]
    past = [m for m in rows if not m["repeat"] and m["day"] < today.isoformat()]
    return page(request, "calendar_company.html", upcoming=upcoming, past=past, kinds=cal.MARK_KINDS,
                repeats=cal.MARK_REPEATS, hol=hol, year=y, today=today)


@app.post("/calendar/company")
async def calendar_company_add(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    try:
        m = cal.parse_mark(form)
    except cal.CalError as e:
        flash(request, str(e), "error")
        return RedirectResponse("/calendar/company", status_code=303)
    cal.add_mark(m, me(request))
    flash(request, f"Added. Everyone sees it on the Calendar.")
    return RedirectResponse(f"/calendar/company", status_code=303)


@app.post("/calendar/company/holidays")
async def calendar_company_holidays(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    y = form.get("year") or ""
    if not str(y).isdigit():
        return RedirectResponse("/calendar/company", status_code=303)
    n = cal.add_us_holidays(int(y), set(form.getlist("h")), me(request))
    flash(request, f"Added {n} holiday{'' if n == 1 else 's'} for {y}." if n else "Nothing new to add; those are already on the calendar.")
    return RedirectResponse(f"/calendar/company?year={y}", status_code=303)


@app.post("/calendar/company/{mid}/delete")
def calendar_company_delete(request: Request, mid: int):
    if (r := guard(request)):
        return r
    cal.delete_mark(mid)
    flash(request, "Removed from the calendar.")
    return RedirectResponse("/calendar/company", status_code=303)


@app.get("/calendar/new", response_class=HTMLResponse)
def calendar_new(request: Request):
    if (r := guard(request)):
        return r
    vid = request.query_params.get("from_visit", "")
    v = field.visit(int(vid)) if vid.isdigit() else None
    u = me(request)
    if v and (v["user_id"] == u["id"] or users.sees_all_field(u)):
        return _cal_form(request, followup_event(v, u))
    return _cal_form(request)


def followup_event(v, u):
    """A new calendar event for a visit's follow-up: same practice and office, the people met, on the follow-up date
    (a Teams meeting if the visit was virtual)."""
    d = v.get("followup") or date.today().isoformat()
    virtual = bool(v.get("virtual"))
    people = [{"name": u["name"], "email": u["email"].lower(), "required": 1, "source": "team"}] if u.get("email") else []
    people += [{"name": p["name"], "email": p["email"].lower(), "required": 1, "source": "contact"}
               for p in v["people"] if p.get("email") and p["email"].lower() not in {x["email"] for x in people}]
    met = ", ".join((p["name"] or p["email"]) + (f" ({p['role']})" if p["role"] else "") for p in v["people"])
    return {"id": None, "practice_id": v.get("practice_id"), "category": "referral", "kind": "followup", "subject": "",
            "subject_custom": 0, "where_kind": "online" if virtual else "referral",
            "place_label": "" if virtual else v["office_name"], "place_address": "" if virtual else (v.get("office_address") or ""),
            "office_id": None if virtual else v["office_id"], "npi_key": "" if virtual else (v.get("npi_key") or ""),
            "place_id": "" if virtual else (v.get("place_id") or ""), "starts": f"{d}T10:00", "ends": f"{d}T10:30",
            "all_day": 0, "repeat": "", "repeat_until": "", "reminder": 60, "show_as": "busy", "teams": int(virtual), "extra": "",
            "cost": None, "private": 0, "log_reminder": 1, "people": people, "status": "new", "ms_id": "",
            "from_visit": v["id"],
            "details": (f"Follow-up from the {v['visited_at'][:10]} visit to {v['office_name']}"
                        + (f" (met {met})" if met else "") + "." + (f"\n\nNotes from that visit: {v['notes']}" if v.get("notes") else ""))[:4000]}


@app.post("/calendar/{eid}/nolog")
def calendar_nolog(request: Request, eid: int):
    if (r := guard(request)):
        return r
    ev = cal.get(eid)
    if ev and cal.can_change(ev, me(request)) or (ev and any(p["email"] == (me(request) or {}).get("email", "").lower() for p in ev["people"])):
        cal.no_log(eid)
        flash(request, "Okay, it won't be listed as a visit to log.")
    return RedirectResponse("/field", status_code=303)


@app.get("/calendar/{eid}/edit", response_class=HTMLResponse)
def calendar_edit(request: Request, eid: int):
    if (r := guard(request)):
        return r
    ev = cal.get(eid)
    if not ev or not cal.can_change(ev, me(request)):
        flash(request, "That event can't be changed here.", "error")
        return RedirectResponse("/calendar", status_code=303)
    return _cal_form(request, ev)


async def _cal_form_data(request):
    form = await request.form()
    data = {k: form.get(k) for k in form.keys() if not k.startswith("person_")}
    for k in ("person_name", "person_email", "person_required", "person_source"):
        data[k] = form.getlist(k)
    up = form.get("attachment")
    attach = None
    if up is not None and getattr(up, "filename", ""):
        blob = await up.read()
        if len(blob) > cal.MAX_ATTACHMENT:
            raise cal.CalError(f"Attachments can be up to {cal.MAX_ATTACHMENT // (1024 * 1024)} MB.")
        if blob:
            attach = (os.path.basename(up.filename)[:120], up.content_type or "", blob)
    return data, attach


async def _cal_store(request, eid=None):
    u = me(request)
    old = cal.get(eid) if eid else None
    if eid and (not old or not cal.can_change(old, u)):
        flash(request, "That event can't be changed here.", "error")
        return RedirectResponse("/calendar", status_code=303)
    try:
        data, attach = await _cal_form_data(request)
        ev, people = cal.parse(data, _cal_practices(), (old or {}).get("created_name") or (u or {}).get("name", ""))
        if ev["category"] != cal.PERSONAL and not _can_schedule(u):
            raise cal.CalError("You can add personal time (lunch, focus time, PTO). Other events need editor access "
                               "or field visits switched on. Ask an admin.")
        if old and not cal.owns(old, u):
            ev["my_notes"] = old.get("my_notes") or ""       # someone else's notes stay as they were
    except cal.CalError as e:
        draft = cal.draft(data if "data" in locals() else {}, old)
        return _cal_form(request, draft, error=str(e))
    eid = cal.save(ev, people, u, eid)
    send = data.get("action") != "save" and (people or (old and old.get("ms_id")))
    if send:
        err = await asyncio.to_thread(cal.send, eid, attach)
        if err:
            flash(request, f"Saved in the hub, but Outlook didn't get it: {err}", "error")
        else:
            n = len(people)
            flash(request, (f"Invite sent to {n} {'person' if n == 1 else 'people'} from {microsoft.mailbox() or 'Outlook'}."
                            if not old or not old.get("ms_id") else "Updated; Outlook sent the change to everyone invited."))
    else:
        flash(request, "Saved to the calendar." if ev["category"] == cal.PERSONAL else "Saved to the calendar. No invite was sent.")
    d = ev["starts"][:10]
    return RedirectResponse(f"/calendar?d={d}&event={eid}&day={d}", status_code=303)


@app.post("/calendar/events")
async def calendar_create(request: Request):
    if (r := guard(request)):
        return r
    return await _cal_store(request)


@app.post("/calendar/{eid}")
async def calendar_update(request: Request, eid: int):
    if (r := guard(request)):
        return r
    return await _cal_store(request, eid)


@app.post("/calendar/{eid}/retry")
async def calendar_retry(request: Request, eid: int):
    if (r := guard(request)):
        return r
    ev = cal.get(eid)
    if not ev or not cal.can_change(ev, me(request)):
        return RedirectResponse("/calendar", status_code=303)
    err = await asyncio.to_thread(cal.send, eid)
    flash(request, f"Outlook still didn't take it: {err}" if err else "Sent.", "error" if err else "ok")
    return RedirectResponse(f"/calendar?d={ev['starts'][:10]}&event={eid}", status_code=303)


@app.post("/calendar/{eid}/cancel")
async def calendar_cancel(request: Request, eid: int):
    if (r := guard(request)):
        return r
    ev = cal.get(eid)
    if not ev or not cal.can_change(ev, me(request)):
        return RedirectResponse("/calendar", status_code=303)
    form = await request.form()
    try:
        await asyncio.to_thread(cal.cancel, eid, form.get("comment") or "")
        flash(request, "Cancelled. Outlook told everyone invited." if ev["ms_id"] else "Cancelled.")
    except microsoft.MSError as e:
        flash(request, f"Couldn't cancel it in Outlook: {e}", "error")
    return RedirectResponse(f"/calendar?d={ev['starts'][:10]}&event={eid}", status_code=303)


@app.post("/calendar/{eid}/delete")
def calendar_delete(request: Request, eid: int):
    if (r := guard(request)):
        return r
    ev = cal.get(eid)
    if ev and cal.can_change(ev, me(request)) and (not ev["ms_id"] or ev["status"] == "cancelled"):
        cal.delete(eid)
        flash(request, "Removed from the calendar.")
    else:
        flash(request, "Cancel it first so the people invited are told.", "error")
    return RedirectResponse("/calendar" + (f"?d={ev['starts'][:10]}" if ev else ""), status_code=303)


@app.get("/calendar/lookup/people")
async def calendar_people(request: Request, q: str = "", office_id: str = "", npi: str = "", place: str = ""):
    if (r := guard(request)):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    if office_id or npi or place:
        return JSONResponse({"people": cal.office_contacts(int(office_id) if office_id.isdigit() else None, npi, place)})
    out = cal.people_search(q)
    if microsoft.connected() and len(q.strip()) >= 2:
        try:
            seen = {p["email"] for p in out}
            out += [p for p in await asyncio.to_thread(microsoft.directory, q) if p["email"].lower() not in seen]
        except microsoft.MSError:
            pass
    return JSONResponse({"people": out[:15]})


@app.get("/calendar/lookup/offices")
def calendar_offices(request: Request, q: str = ""):
    if (r := guard(request)):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    return JSONResponse({"offices": cal.office_search(q)})


@app.get("/calendar/lookup/locations")
def calendar_locations(request: Request, practice: str = ""):
    if (r := guard(request)):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    return JSONResponse({"locations": cal.our_locations(int(practice)) if practice.isdigit() else []})


@app.get("/calendar/lookup/freebusy")
async def calendar_freebusy(request: Request, emails: str = "", start: str = "", end: str = ""):
    if (r := guard(request)):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    if not microsoft.connected():
        return JSONResponse({"ok": False, "error": "Microsoft 365 isn't connected."})
    ems = [e.strip().lower() for e in emails.split(",") if cal.EMAIL.match(e.strip())][:20]
    try:
        datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:
        return JSONResponse({"ok": False, "error": "Pick a time first."})
    if not ems:
        return JSONResponse({"ok": True, "people": {}})
    try:
        return JSONResponse({"ok": True, "people": await asyncio.to_thread(microsoft.free_busy, ems, start, end)})
    except microsoft.MSError as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ---------- Microsoft 365 connection ----------
def _ms_redirect(request: Request):
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return base + "/microsoft/callback"


GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@app.post("/connections/microsoft")
async def save_microsoft(request: Request, tenant_id: str = Form(""), client_id: str = Form(""), client_secret: str = Form(""),
                         mailbox: str = Form(""), timezone_name: str = Form("")):
    if (r := guard(request)):
        return r
    tenant_id, client_id, client_secret, mailbox = tenant_id.strip(), client_id.strip(), client_secret.strip(), mailbox.strip().lower()
    timezone_name = timezone_name.strip()
    if not GUID.match(tenant_id) or not GUID.match(client_id):
        flash(request, "The Directory (tenant) ID and Application (client) ID are both long IDs like 1a2b3c4d-…; copy them from the app's Overview in Entra.", "error")
    elif not client_secret and not db.get_setting("ms_client_secret"):
        flash(request, "Paste the client secret's Value (not its Secret ID).", "error")
    elif client_secret and GUID.match(client_secret):
        flash(request, "That looks like the Secret ID. Paste the secret's Value instead (it's shown once, right after you create it).", "error")
    elif mailbox and not cal.EMAIL.match(mailbox):
        flash(request, "The send-from mailbox should be an email address.", "error")
    else:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(timezone_name or microsoft.DEFAULT_TZ)
        except Exception:
            flash(request, "The time zone should look like America/Detroit or America/Chicago.", "error")
            return RedirectResponse("/connections#microsoft", status_code=303)
        microsoft.save(tenant_id, client_id, client_secret, mailbox, timezone_name)
        flash(request, "Saved. Now click “Sign in with Microsoft”.")
    return RedirectResponse("/connections#microsoft", status_code=303)


@app.get("/microsoft/connect")
def microsoft_connect(request: Request):
    if (r := guard(request)):
        return r
    if not microsoft.configured():
        flash(request, "Save the tenant ID, client ID and secret first.", "error")
        return RedirectResponse("/connections#microsoft", status_code=303)
    state = os.urandom(16).hex()
    verifier, challenge = microsoft.pkce_pair()
    studio_scopes = request.query_params.get("studio") == "1" or microsoft.studio_on()
    request.session["ms_state"], request.session["ms_verifier"] = state, verifier
    request.session["ms_studio"] = studio_scopes
    return RedirectResponse(microsoft.auth_url(_ms_redirect(request), state, challenge, studio_scopes), status_code=303)


@app.get("/microsoft/callback")
async def microsoft_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if (r := guard(request)):
        return r
    expected, verifier = request.session.pop("ms_state", None), request.session.pop("ms_verifier", None)
    studio_scopes = bool(request.session.pop("ms_studio", False))
    if error:
        flash(request, f"Microsoft sign-in didn't finish: {microsoft._explain_signin(error_description or error)}", "error")
    elif not expected or not hmac.compare_digest(state, expected):
        flash(request, "That Microsoft sign-in link expired. Try again.", "error")
    else:
        try:
            who = await asyncio.to_thread(microsoft.exchange_code, code, _ms_redirect(request), verifier, studio_scopes)
            problem = await asyncio.to_thread(microsoft.check_mailbox)
            name = who.get("displayName") or who.get("mail") or "you"
            if problem:
                flash(request, f"Signed in as {name}, but the send-from calendar isn't reachable yet: {problem}", "error")
            elif studio_scopes and not microsoft.studio_on():
                flash(request, f"Signed in as {name}, but Microsoft didn't grant the SharePoint and email permissions. In Entra → "
                               "App registrations → the hub's app → API permissions, add Files.ReadWrite.All, Sites.ReadWrite.All, "
                               "Mail.Send and Mail.Send.Shared (Microsoft Graph, delegated), click “Grant admin consent”, then "
                               "turn them on again here.", "error")
            else:
                flash(request, f"Microsoft 365 connected as {name}. Invites will come from {microsoft.mailbox() or who.get('mail')}."
                      + (" SharePoint and email for the Studio are on." if microsoft.studio_on() else ""))
                studio.kick_push()
        except microsoft.MSError as e:
            flash(request, f"Microsoft sign-in failed: {e}", "error")
    return RedirectResponse("/connections#" + ("studio" if studio_scopes else "microsoft"), status_code=303)


@app.post("/microsoft/check")
async def microsoft_check(request: Request):
    if (r := guard(request)):
        return r
    problem = await asyncio.to_thread(microsoft.check_mailbox) if microsoft.connected() else "Not connected."
    flash(request, problem or f"All good: the hub can write to the {microsoft.mailbox() or 'signed-in'} calendar.",
          "error" if problem else "ok")
    return RedirectResponse("/connections#microsoft", status_code=303)


@app.post("/microsoft/disconnect")
def microsoft_disconnect(request: Request):
    if (r := guard(request)):
        return r
    microsoft.disconnect()
    flash(request, "Microsoft 365 disconnected. Events already sent stay in Outlook; new ones are saved in the hub only.")
    return RedirectResponse("/connections#microsoft", status_code=303)


# ---------- Studio: designs, photos and videos by brand ----------
STUDIO_FMTS = {"pptx", "mp4", "pdf", "png"}


def _brand_in(v):
    """Form value → brand id (0 = MCDP); None if it isn't one."""
    try:
        pid = int(v)
    except (TypeError, ValueError):
        return None
    if not db.practice(pid) and pid != studio.MCDP:
        return None
    only = db.only_ids()
    if only is not None and pid not in only:
        return None                       # not one of their practices (MCDP's own brand is for everyone-practices people)
    return pid


def _studio_default_brand():
    only = db.only_ids()
    if only is None:
        return studio.MCDP
    mine = db.keep_mine(db.practices())
    return mine[0]["id"] if mine else None


def _studio_back(request, fallback="/studio"):
    back = request.headers.get("referer") or ""
    return back if back.startswith(str(request.base_url)) else fallback


def _brand_look(pid):
    """Colours and logo for a brand's share page."""
    if pid in (None, studio.MCDP):
        return {"primary": field_report.DEFAULT_PRIMARY, "accent": field_report.DEFAULT_ACCENT, "logo": False}
    p, a = field_report.colors(pid)
    return {"primary": p, "accent": a, "logo": bool(images.logo(pid))}


@app.get("/studio", response_class=HTMLResponse)
def studio_page(request: Request, b: str = "", f: str = "", s: str = "", q: str = ""):
    if (r := guard(request)):
        return r
    plist = db.practices()
    view, pid, fid = "brand", None, None
    if b in ("none", "trash", "feedback", "all", "mine"):
        view = b
    else:
        pid = _brand_in(b)
        if pid is None:
            pid = request.session.get("studio_brand")
            pid = pid if pid is not None and _brand_in(pid) is not None else _studio_default_brand()
        request.session["studio_brand"] = pid
        if pid is None:
            view = "all"
    try:
        fid = int(f) if f and f != "none" else ("none" if f == "none" else None)
    except ValueError:
        fid = None
    fold = studio.folder(fid) if isinstance(fid, int) else None
    if fold and fold["practice_id"] != pid:
        fold, fid = None, None
    status = s if s in studio.STATUS_LABEL else ""
    if view == "brand":
        got = studio.items(pid, fid, status or None, q)
        counts = studio.status_counts(pid, fid, q)
    elif view == "all":
        got = studio.items(None, None, status or None, q, all_brands=True)
        counts = studio.status_counts(None, None, q, all_brands=True)
    elif view == "none":
        got, counts = studio.items(unbranded=True, q=q), None
    elif view == "trash":
        got, counts = studio.items(trash=True, q=q), None
    elif view == "mine":
        got, counts = [studio._row(x) for x in studio_flow.waiting_for((me(request) or {}).get("id"))], None
    else:
        got, counts = [], None
    names = {p["id"]: p["name"] for p in plist}
    names[studio.MCDP] = "MCDP"
    assets = []
    if view == "brand" and pid and not fid and not q and not status:
        assets = images.assets(pid)
    look = _brand_look(pid) if view == "brand" else None
    return page(request, "studio.html", view=view, pid=pid, fid=fid, folder=fold, status=status, q=q, items=got,
                counts=counts, brands=[x for x in studio.brands(db.keep_mine(plist))
                                       if db.only_ids() is None or x.get("id") != studio.MCDP], folders=studio.folders(pid) if view == "brand" else [],
                statuses=studio.STATUSES, names=names, totals=studio.counts(), assets=assets, look=look,
                brand_name=studio.brand_name(pid) if view == "brand" else "",
                feedback=studio.outside_feedback() if view == "feedback" else [],
                canva_on=canva.connected(), canva_status=canva.status(), canva_running=canva.running(),
                sp_ready=studio.sp_ready(), chunk=8 * 1024 * 1024, max_file=studio.MAX_FILE,
                folder_shares=studio.shares_for(folder_id=fold["id"]) if fold else [],
                new_share=request.session.pop("studio_folder_share", None), mail_on=microsoft.studio_on(),
                base=PUBLIC_URL or str(request.base_url).rstrip("/"),
                all_folders={p: studio.folders(p) for p in ([studio.MCDP] + [x["id"] for x in plist])} if view in ("none", "all") else {})


@app.post("/studio/folders")
def studio_folder_add(request: Request, pid: str = Form(...), name: str = Form("")):
    if (r := guard(request)):
        return r
    b = _brand_in(pid)
    if b is None:
        flash(request, "Pick a brand first.", "error")
        return RedirectResponse("/studio", status_code=303)
    try:
        fid = studio.add_folder(b, name, actor(request))
    except studio.StudioError as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/studio?b={b}", status_code=303)
    return RedirectResponse(f"/studio?b={b}&f={fid}", status_code=303)


@app.post("/studio/folders/{fid}/rename")
def studio_folder_rename(request: Request, fid: int, name: str = Form("")):
    if (r := guard(request)):
        return r
    f = studio.folder(fid)
    try:
        studio.rename_folder(fid, name)
    except studio.StudioError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/studio?b={f['practice_id'] if f else ''}&f={fid}", status_code=303)


@app.post("/studio/folders/{fid}/delete")
def studio_folder_delete(request: Request, fid: int):
    if (r := guard(request)):
        return r
    f = studio.folder(fid)
    studio.delete_folder(fid)
    flash(request, "Folder removed. Its files are still in the brand, under All files.")
    return RedirectResponse(f"/studio?b={f['practice_id'] if f else ''}", status_code=303)


@app.post("/studio/upload/start")
async def studio_upload_start(request: Request):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    try:
        j = await request.json()
    except ValueError:
        return JSONResponse({"error": "Bad request."}, status_code=400)
    pid = _brand_in(j.get("pid"))
    fid = j.get("folder")
    fid = int(fid) if isinstance(fid, (int, str)) and str(fid).isdigit() else None
    try:
        uid = studio.start_upload(j.get("name"), j.get("size"), j.get("mime"), pid if pid is not None else -1, fid,
                                  actor(request))
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"id": uid}


@app.post("/studio/upload/{uid}/chunk")
async def studio_upload_chunk(request: Request, uid: str, offset: int = 0):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    data = await request.body()
    if len(data) > studio.CHUNK_MAX:
        return JSONResponse({"error": "Piece too large."}, status_code=413)
    try:
        have = await asyncio.to_thread(studio.put_chunk, uid, offset, data, actor(request))
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"have": have}


@app.post("/studio/upload/{uid}/done")
async def studio_upload_done(request: Request, uid: str):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    form = await request.form()

    def num(k):
        try:
            v = float(form.get(k) or "")
            return v if v > 0 else None
        except ValueError:
            return None
    thumb = form.get("thumb")
    tb = await thumb.read(5 * 1024 * 1024) if getattr(thumb, "filename", None) is not None and hasattr(thumb, "read") else None
    try:
        iid = await asyncio.to_thread(studio.finish_upload, uid, actor(request),
                                      int(num("width") or 0) or None, int(num("height") or 0) or None, num("duration"), tb)
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    it = studio.item(iid)
    return {"id": iid, "title": it["title"], "kind": it["kind"]}


@app.post("/studio/bulk")
async def studio_bulk(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    ids = [int(x) for x in form.getlist("ids") if str(x).isdigit()][:500]
    action = form.get("action") or ""
    by = actor(request)
    n, errs = 0, []
    for iid in ids:
        try:
            if action == "trash":
                studio.trash(iid, by)
            elif action == "restore":
                studio.restore(iid, by)
            elif action in studio.STATUS_LABEL:
                studio.update(iid, by, status=action)
            elif action == "brand":
                b = _brand_in(form.get("pid"))
                if b is None:
                    raise studio.StudioError("Pick a brand.")
                fid = form.get("folder")
                kw = {"practice_id": b}
                if fid and str(fid).isdigit():
                    kw["folder_id"] = int(fid)
                studio.update(iid, by, **kw)
            elif action == "folder":
                it = studio.item(iid)
                fid = form.get("folder")
                studio.update(iid, by, folder_id=int(fid) if fid and str(fid).isdigit() else None,
                              practice_id=it["practice_id"])
            else:
                raise studio.StudioError("Pick what to do.")
            n += 1
        except studio.StudioError as e:
            errs.append(str(e))
    if n:
        flash(request, f"Done for {n} file{'s' if n != 1 else ''}." + (f" {errs[0]}" if errs else ""))
    else:
        flash(request, errs[0] if errs else "Tick the files first.", "error")
    return RedirectResponse(_studio_back(request), status_code=303)


@app.get("/studio/i/{iid}", response_class=HTMLResponse)
def studio_item_page(request: Request, iid: int):
    if (r := guard(request)):
        return r
    it = studio.item(iid)
    if it and db.only_ids() is not None and it.get("practice_id") not in db.only_ids():
        it = None                                     # another practice's file
    if not it:
        flash(request, "That file is gone.", "error")
        return RedirectResponse("/studio", status_code=303)
    plist = db.practices()
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    shares = studio.shares_for(item_id=iid)
    new_share = request.session.pop("studio_new_share", None)
    u = me(request) or {}
    chain = studio_flow.chain(iid)
    my_step = next((st for st in chain if st["status"] == "pending" and st["kind"] == "person"
                    and (st["user_id"] == u.get("id"))), None)
    chans, brand_chans, social_error = [], [], ""
    if it["kind"] in ("image", "design", "video") and not it["trashed_at"]:
        try:
            chans = social.integrations()
            brand_chans = social.for_brand(it["practice_id"], chans)
        except social.SocialError as e:
            social_error = str(e)
    tz = studio_flow.local_tz()
    from datetime import datetime as _dt, timedelta as _td
    soon = (_dt.now(tz) + _td(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    posts = studio_flow.posts(iid)
    for p_ in posts:
        try:
            p_["local"] = _dt.fromisoformat(p_["when_utc"].replace("Z", "+00:00")).astimezone(tz).strftime("%a %b %-d, %-I:%M %p")
        except (ValueError, AttributeError):
            p_["local"] = ""
    return page(request, "studio_item.html", it=it, brand_name=studio.brand_name(it["practice_id"]),
                is_video_project=studio_video.is_project(it),
                chain=chain, my_step=my_step, open_chain=any(st["status"] in ("pending", "waiting") for st in chain),
                team=[x for x in users.all_users() if x["active"] and x["pw_hash"]],
                comments=studio_flow.comments(iid), posts=posts, chans=chans, brand_chans=[c["id"] for c in brand_chans],
                social_error=social_error, gmb_actions=social.GMB_ACTIONS, soon_day=soon.strftime("%Y-%m-%d"),
                soon_time=soon.strftime("%H:%M"), tz_name=str(tz), ai_on=ai.available(),
                share_by_id={sh["id"]: sh for sh in shares},
                folder=studio.folder(it["folder_id"]), brands=studio.brands(plist, counts=False),
                folders=studio.folders(it["practice_id"]) if it["practice_id"] is not None else [],
                all_folders={p: studio.folders(p) for p in ([studio.MCDP] + [x["id"] for x in plist])},
                statuses=studio.STATUSES, shares=shares, base=base, new_share=new_share,
                events=studio.events(iid), mail_on=microsoft.studio_on(), canva_on=canva.connected(),
                share_fmts=STUDIO_FMTS)


@app.post("/studio/i/{iid}")
async def studio_item_save(request: Request, iid: int):
    if (r := guard(request)):
        return r
    form = await request.form()
    fields = {}
    for k in ("title", "notes", "status"):
        if k in form:
            fields[k] = form.get(k)
    if "brand" in form:
        b = _brand_in(form.get("brand"))
        if b is None:
            flash(request, "Pick a brand.", "error")
            return RedirectResponse(f"/studio/i/{iid}", status_code=303)
        fields["practice_id"] = b
    if "folder" in form:
        fv = form.get("folder")
        fields["folder_id"] = int(fv) if fv and str(fv).isdigit() else None
    try:
        studio.update(iid, actor(request), **fields)
        flash(request, "Saved.")
    except studio.StudioError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/studio/i/{iid}", status_code=303)


@app.post("/studio/i/{iid}/trash")
def studio_item_trash(request: Request, iid: int):
    if (r := guard(request)):
        return r
    it = studio.item(iid)
    studio.trash(iid, actor(request))
    flash(request, f"Moved to the trash. It's emptied after {studio.TRASH_DAYS} days.")
    return RedirectResponse(f"/studio?b={it['practice_id'] if it and it['practice_id'] is not None else 'none'}", status_code=303)


@app.post("/studio/i/{iid}/restore")
def studio_item_restore(request: Request, iid: int):
    if (r := guard(request)):
        return r
    studio.restore(iid, actor(request))
    flash(request, "Restored.")
    return RedirectResponse(f"/studio/i/{iid}", status_code=303)


@app.post("/studio/i/{iid}/delete")
def studio_item_delete(request: Request, iid: int):
    if (r := guard(request)):
        return r
    it = studio.item(iid)
    if it and not it["trashed_at"]:
        flash(request, "Move it to the trash first.", "error")
        return RedirectResponse(f"/studio/i/{iid}", status_code=303)
    studio.delete_forever(iid)
    flash(request, "Deleted for good.")
    return RedirectResponse("/studio?b=trash", status_code=303)


@app.post("/studio/i/{iid}/thumb")
async def studio_item_thumb(request: Request, iid: int, thumb: UploadFile = File(...)):
    """The browser grabs a frame from a video that has no picture yet."""
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    data = await thumb.read(5 * 1024 * 1024)
    await asyncio.to_thread(studio.set_thumb, iid, data)
    return {"ok": True}


@app.get("/studio/f/{iid}")
async def studio_file(request: Request, iid: int, dl: int = 0):
    if not authed(request):
        return Response(status_code=401)
    return await _send_studio_file(studio.item(iid), dl)


async def _send_studio_file(it, dl):
    from fastapi.responses import FileResponse
    if not it:
        return Response(status_code=404)
    p = studio.local_path(it)
    if p:
        return FileResponse(p, media_type=it["mime"] or None, filename=studio.download_name(it) if dl else None,
                            content_disposition_type="attachment" if dl else "inline",
                            headers={"Cache-Control": "private, max-age=3600"})
    if it.get("sp_item"):
        try:
            url = await asyncio.to_thread(studio.download_url, it)
        except Exception:
            url = None
        if url:
            return RedirectResponse(url, status_code=302)
    return Response("That file isn't reachable right now (SharePoint didn't answer). Try again in a minute.",
                    status_code=503)


def _thumb_response(it):
    from fastapi.responses import FileResponse
    if it and it.get("has_thumb") and os.path.exists(studio.thumb_path(it["id"])):
        return FileResponse(studio.thumb_path(it["id"]), media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=600"})
    kind = (it or {}).get("kind", "file")
    label = {"design": "DESIGN", "video": "VIDEO", "pdf": "PDF", "slides": "SLIDES", "doc": "DOC", "audio": "AUDIO", "image": "IMAGE"}.get(kind, "FILE")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 320 240">'
           f'<rect width="320" height="240" fill="#EEEBE5"/><text x="160" y="128" text-anchor="middle" '
           f'font-family="Public Sans, Arial, sans-serif" font-size="18" font-weight="600" letter-spacing="3" fill="#8A857C">{label}</text></svg>')
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=60"})


@app.get("/studio/t/{iid}")
def studio_thumb(request: Request, iid: int):
    if not authed(request):
        return Response(status_code=401)
    return _thumb_response(studio.item(iid))


# ---- sharing
def _share_form(form):
    def on(k):
        return form.get(k) in ("1", "on", "true")
    try:
        days = int(form.get("days") or 30)
    except ValueError:
        days = 30
    return dict(title=(form.get("title") or "").strip(), message=(form.get("message") or "").strip(),
                password=form.get("password") or "", days=days, allow_download=on("allow_download"),
                allow_review=on("allow_review"))


async def _send_share(request, s, to_raw, note):
    to = [a.strip() for a in re.split(r"[,;\s]+", to_raw or "") if a.strip()]
    bad = [a for a in to if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", a)]
    if bad:
        raise studio.StudioError(f"That isn't an email address: {bad[0]}")
    if not to:
        return 0
    if len(to) > 25:
        raise studio.StudioError("Send to 25 people or fewer at a time.")
    if not microsoft.studio_on():
        raise studio.StudioError("Sending by email needs SharePoint and email turned on for Microsoft 365 (Connections). "
                                 "Copy the link instead for now.")
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    link = f"{base}/s/{s['token']}"
    look = _brand_look(s["practice_id"])
    u = me(request) or {}
    body = studio.share_email_html(s, link, note, u.get("name") or "The MCDP marketing team", look,
                                   studio.brand_name(s["practice_id"]) if s["practice_id"] is not None else "MCDP")
    await asyncio.to_thread(microsoft.send_mail, to, f"{s['title']} · shared with you", body, u.get("email") or None)
    studio.log_share(s, "sent", s["item_id"], u.get("name") or "", ", ".join(to), note)
    return len(to)


async def _make_share(request, item_id=None, folder_id=None):
    form = await request.form()
    u = me(request) or {}
    try:
        s = studio.create_share(actor(request), u.get("email") or "", item_id=item_id, folder_id=folder_id, **_share_form(form))
    except studio.StudioError as e:
        flash(request, str(e), "error")
        return None
    request.session["studio_new_share"] = s["id"]
    try:
        n = await _send_share(request, s, form.get("to"), form.get("note") or s["message"])
        flash(request, f"Link made and emailed to {n} {'person' if n == 1 else 'people'}." if n else
              "Link made. Copy it below and send it however you like.")
    except (studio.StudioError, microsoft.MSError) as e:
        flash(request, f"Link made, but the email didn't go: {e}", "error")
    return s


@app.post("/studio/i/{iid}/share")
async def studio_item_share(request: Request, iid: int):
    if (r := guard(request)):
        return r
    await _make_share(request, item_id=iid)
    return RedirectResponse(f"/studio/i/{iid}#share", status_code=303)


@app.post("/studio/folders/{fid}/share")
async def studio_folder_share(request: Request, fid: int):
    if (r := guard(request)):
        return r
    f = studio.folder(fid)
    s = await _make_share(request, folder_id=fid)
    if s:
        request.session["studio_folder_share"] = s["id"]
    return RedirectResponse(f"/studio?b={f['practice_id'] if f else ''}&f={fid}#share", status_code=303)


@app.post("/studio/shares/{sid}/revoke")
def studio_share_revoke(request: Request, sid: int):
    if (r := guard(request)):
        return r
    studio.revoke_share(sid, actor(request))
    flash(request, "Link turned off. Anyone opening it now sees that it's no longer available.")
    return RedirectResponse(_studio_back(request), status_code=303)


@app.post("/studio/shares/{sid}/send")
async def studio_share_send(request: Request, sid: int, to: str = Form(""), note: str = Form("")):
    if (r := guard(request)):
        return r
    s = studio.share(sid)
    if not s or not s["live"]:
        flash(request, "That link is off or expired. Make a new one.", "error")
    else:
        try:
            n = await _send_share(request, s, to, note)
            flash(request, f"Emailed to {n} {'person' if n == 1 else 'people'}." if n else "Add an email address.",
                  "ok" if n else "error")
        except (studio.StudioError, microsoft.MSError) as e:
            flash(request, str(e), "error")
    return RedirectResponse(_studio_back(request), status_code=303)


# ---- Canva
def _canva_redirect(request: Request):
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return base + "/canva/callback"


@app.post("/studio/canva/import")
def studio_canva_import(request: Request):
    if (r := guard(request)):
        return r
    if not canva.connected():
        flash(request, "Canva isn't connected yet. An admin signs in on the Connections page.", "error")
    elif canva.start_import(actor(request)):
        flash(request, "Importing from Canva. Designs appear here as they come in; you can leave this page.")
    else:
        flash(request, "An import is already running.")
    return RedirectResponse(_studio_back(request), status_code=303)


@app.post("/studio/canva/stop")
def studio_canva_stop(request: Request):
    if (r := guard(request)):
        return r
    canva.stop_import()
    flash(request, "Stopping after the design it's on. Run it again any time to carry on.")
    return RedirectResponse(_studio_back(request), status_code=303)


@app.get("/studio/canva/status")
def studio_canva_status(request: Request):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    return {**canva.status(), "running": canva.running(), "unbranded": studio.counts()["unbranded"]}


@app.post("/studio/i/{iid}/canva/{fmt}")
async def studio_canva_more(request: Request, iid: int, fmt: str):
    if (r := guard(request)):
        return r
    if fmt not in STUDIO_FMTS:
        return RedirectResponse(f"/studio/i/{iid}", status_code=303)
    try:
        nid = await asyncio.to_thread(canva.export_more, iid, fmt, actor(request))
        flash(request, "Added next to the original.")
        return RedirectResponse(f"/studio/i/{nid}", status_code=303)
    except (canva.CanvaError, studio.StudioError) as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/studio/i/{iid}", status_code=303)


@app.post("/connections/canva")
def connections_canva(request: Request, client_id: str = Form(""), client_secret: str = Form("")):
    if (r := guard(request)):
        return r
    if not client_id.strip():
        flash(request, "Paste the client ID.", "error")
    else:
        canva.save(client_id, client_secret)
        flash(request, "Saved. Now sign in with Canva.")
    return RedirectResponse("/connections#canva", status_code=303)


@app.get("/canva/connect")
def canva_connect(request: Request):
    if (r := guard(request)):
        return r
    if not canva.configured():
        flash(request, "Save the Canva client ID and secret first.", "error")
        return RedirectResponse("/connections#canva", status_code=303)
    state = os.urandom(16).hex()
    verifier, challenge = canva.pkce_pair()
    request.session["canva_state"], request.session["canva_verifier"] = state, verifier
    return RedirectResponse(canva.auth_url(_canva_redirect(request), state, challenge), status_code=303)


@app.get("/canva/callback")
async def canva_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if (r := guard(request)):
        return r
    expected, verifier = request.session.pop("canva_state", None), request.session.pop("canva_verifier", None)
    if error:
        flash(request, f"Canva sign-in didn't finish: {error_description or error}", "error")
    elif not expected or not hmac.compare_digest(state, expected):
        flash(request, "That Canva sign-in link expired. Try again.", "error")
    else:
        try:
            name = await asyncio.to_thread(canva.exchange_code, code, _canva_redirect(request), verifier)
            flash(request, f"Canva connected{' as ' + name if name else ''}. Start the import from the Studio.")
        except canva.CanvaError as e:
            flash(request, f"Canva sign-in failed: {e}", "error")
    return RedirectResponse("/connections#canva", status_code=303)


@app.post("/canva/disconnect")
def canva_disconnect(request: Request):
    if (r := guard(request)):
        return r
    canva.disconnect()
    flash(request, "Canva disconnected. Designs already imported stay in the Studio.")
    return RedirectResponse("/connections#canva", status_code=303)


# ---- SharePoint for the Studio (admins)
@app.post("/connections/studio-sharepoint")
async def connections_studio_sp(request: Request, site: str = Form(""), root: str = Form("")):
    if (r := guard(request)):
        return r
    try:
        name = await asyncio.to_thread(studio.connect_sharepoint, site, root)
        n = studio.counts()["local"]
        flash(request, f"Studio files now go to {name} on SharePoint." +
              (f" The {n} file{'s' if n != 1 else ''} on the NAS are moving there in the background." if n else ""))
    except (studio.StudioError, microsoft.MSError) as e:
        flash(request, f"Couldn't link the site: {e}", "error")
    return RedirectResponse("/connections#studio", status_code=303)


@app.post("/connections/studio-sharepoint/push")
def connections_studio_push(request: Request):
    if (r := guard(request)):
        return r
    studio.kick_push()
    flash(request, "Moving NAS files to SharePoint in the background.")
    return RedirectResponse("/connections#studio", status_code=303)


@app.post("/connections/studio-sharepoint/disconnect")
def connections_studio_sp_off(request: Request):
    if (r := guard(request)):
        return r
    studio.disconnect_sharepoint()
    flash(request, "Unlinked. New Studio files stay on the NAS; files already on SharePoint stay there and won't open "
                   "in the hub until a site is linked again.")
    return RedirectResponse("/connections#studio", status_code=303)


# ---- public share pages: /s/<token> (no sign-in)
def _share_open(request, s):
    return not s["pw_hash"] or request.session.get(f"sh{s['id']}") is True


def _share_gone(request, why):
    return templates.TemplateResponse(request, "share.html", {"gone": why, "s": None, "static_v": STATIC_V},
                                      status_code=404 if why == "missing" else 410)


@app.get("/s/{token}", response_class=HTMLResponse)
def share_page(request: Request, token: str, error: str = ""):
    s = studio.share_by_token(token)
    if not s:
        return _share_gone(request, "missing")
    if not s["live"]:
        return _share_gone(request, "expired" if s["expired"] else "revoked")
    look = _brand_look(s["practice_id"])
    brand = studio.brand_name(s["practice_id"]) if s["practice_id"] is not None else "MCDP"
    ctx = {"s": s, "look": look, "brand": brand, "static_v": STATIC_V, "gone": None,
           "locked": not _share_open(request, s), "error": error, "done": request.session.pop(f"shd{s['id']}", None)}
    if not ctx["locked"]:
        ctx["items"] = studio.share_items(s)
        seen_key = f"shv{s['id']}"
        if request.session.get(seen_key, 0) < time.time() - 3600:      # one view per visitor per hour
            request.session[seen_key] = time.time()
            studio.log_share(s, "view", s["item_id"])
        ctx["reviews"] = {e["item_id"]: e for e in reversed(studio.events(share_id=s["id"], kinds=("approved", "changes")))}
    return templates.TemplateResponse(request, "share.html", ctx)


@app.post("/s/{token}/unlock")
def share_unlock(request: Request, token: str, password: str = Form("")):
    s = studio.share_by_token(token)
    if not s or not s["live"]:
        return RedirectResponse(f"/s/{token}", status_code=303)
    key = f"shtry{s['id']}"
    tries = [t for t in request.session.get(key, []) if t > time.time() - 900]
    if len(tries) >= 8:
        return RedirectResponse(f"/s/{token}?error=wait", status_code=303)
    if studio.check_password(s, password):
        request.session[f"sh{s['id']}"] = True
        request.session.pop(key, None)
        return RedirectResponse(f"/s/{token}", status_code=303)
    request.session[key] = tries + [time.time()]
    return RedirectResponse(f"/s/{token}?error=password", status_code=303)


def _share_item(request, token, iid):
    s = studio.share_by_token(token)
    if not s or not s["live"] or not _share_open(request, s) or not studio.share_has(s, iid):
        return None, None
    return s, studio.item(iid)


@app.get("/s/{token}/f/{iid}")
async def share_file(request: Request, token: str, iid: int, dl: int = 0):
    s, it = _share_item(request, token, iid)
    if not it:
        return Response(status_code=404)
    if dl:
        if not s["allow_download"]:
            return Response("Downloads are turned off for this link.", status_code=403)
        studio.log_share(s, "download", iid)
    return await _send_studio_file(it, dl)


@app.get("/s/{token}/t/{iid}")
def share_thumb(request: Request, token: str, iid: int):
    s, it = _share_item(request, token, iid)
    return _thumb_response(it) if it else Response(status_code=404)


@app.get("/s/{token}/logo")
def share_logo(request: Request, token: str):
    from fastapi.responses import FileResponse
    s = studio.share_by_token(token)
    a = images.logo(s["practice_id"]) if s and s["live"] and s["practice_id"] else None
    p = images.path(a) if a else None
    if not p:
        return RedirectResponse("/static/favicon-192.png", status_code=302)
    return FileResponse(p, headers={"Cache-Control": "public, max-age=3600"})


@app.post("/s/{token}/review")
def share_review(request: Request, token: str, item_id: int = Form(...), decision: str = Form(""),
                 name: str = Form(""), email: str = Form(""), comment: str = Form("")):
    s, it = _share_item(request, token, item_id)
    if not it:
        return RedirectResponse(f"/s/{token}", status_code=303)
    try:
        st = studio_flow.step_for_share(s["id"], item_id)
        studio.review_from_share(s, item_id, decision, name, email, comment, apply_status=not st)
        request.session[f"shd{s['id']}"] = {"item": item_id, "decision": decision}
        request.session["share_name"] = name.strip()[:120]
        if st:                                   # part of an approval chain: it moves on (and tells people) itself
            studio_flow.decide(st["id"], decision, comment, f"{name.strip()} (outside link)", _base(request))
        else:
            _notify_share_owner(request, s, it, decision, name, comment)
    except (studio.StudioError, studio_flow.FlowError) as e:
        return RedirectResponse(f"/s/{token}?error=" + urllib.parse.quote(str(e)) + f"#i{item_id}", status_code=303)
    return RedirectResponse(f"/s/{token}#i{item_id}", status_code=303)


def _notify_share_owner(request, s, it, decision, who, comment):
    """Email whoever made the link, from the marketing mailbox, when someone outside answers."""
    if not (s.get("created_by_email") and microsoft.studio_on()):
        return
    import html as _h
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    verb = "approved" if decision == "approved" else "asked for changes to"
    body = (f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;line-height:1.5">'
            f'<p><strong>{_h.escape(who)}</strong> {verb} <strong>{_h.escape(it["title"])}</strong> '
            f'({_h.escape(studio.brand_name(it["practice_id"]))}).</p>'
            + (f'<blockquote style="margin:0 0 14px;padding:8px 12px;border-left:3px solid #0F7471;background:#F4F7F6">'
               f'{_h.escape(comment)}</blockquote>' if comment.strip() else "")
            + f'<p><a href="{base}/studio/i/{it["id"]}">Open it in the Studio</a></p></div>')

    def go():
        try:
            microsoft.send_mail([s["created_by_email"]], f"{who} {verb} {it['title']}", body)
        except Exception:
            pass
    import threading as _t
    _t.Thread(target=go, daemon=True).start()


# ---------- Studio editor (phase 2): designs made in the hub ----------
async def _form_text(form, key):
    """A big text part (the editor sends the design as a file part, since plain fields stop at 1 MB)."""
    v = form.get(key)
    if hasattr(v, "read"):
        return (await v.read(studio.MAX_DOC + 1)).decode("utf-8", errors="replace")
    return v or ""


def _design(iid):
    it = studio.item(iid)
    return it if it and it["kind"] == "design" else None


def _media_for(pid):
    """Pictures the editor can place: the brand kit's logo, photos and AI images, and Studio pictures."""
    out = {"logo": [], "photos": [], "ai": [], "library": []}
    if pid:
        for a in images.assets(pid):
            key = {"logo": "logo", "photo": "photos", "generated": "ai"}.get(a["kind"])
            if key:
                out[key].append({"src": f"/brand-images/{a['id']}?full=1", "thumb": f"/brand-images/{a['id']}",
                                 "w": a["width"], "h": a["height"], "name": a["label"] or a["kind"]})
    names = {f["id"]: f["name"] for f in studio.folders(pid if pid is not None else studio.MCDP)}
    for it in studio.items(pid if pid is not None else studio.MCDP, limit=120):
        if it["kind"] == "image":
            out["library"].append({"src": f"/studio/img/{it['id']}", "thumb": f"/studio/t/{it['id']}", "w": it["width"],
                                   "h": it["height"], "name": it["title"], "id": it["id"],
                                   "folder": names.get(it["folder_id"], "")})
    return out


@app.get("/studio/new", response_class=HTMLResponse)
def studio_new(request: Request, b: str = "", f: str = ""):
    if (r := guard(request)):
        return r
    pid = _brand_in(b)
    if pid is None:
        pid = request.session.get("studio_brand", studio.MCDP)
    fold = studio.folder(int(f)) if f.isdigit() else None
    if fold and fold["practice_id"] != pid:
        fold = None
    plist = db.practices()
    return page(request, "studio_new.html", pid=pid, folder=fold, brands=studio.brands(plist, counts=False),
                brand_name=studio.brand_name(pid), sizes=studio.SIZES, groups=["Social", "Google", "Print"],
                video_sizes=studio_video.SIZES,
                templates=studio.templates(pid), all_folders={p: studio.folders(p) for p in ([studio.MCDP] + [x["id"] for x in plist])})


@app.post("/studio/designs")
async def studio_design_create(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    pid = _brand_in(form.get("pid"))
    fv = form.get("folder")
    fid = int(fv) if fv and str(fv).isdigit() else None
    try:
        if pid is None:
            raise studio.StudioError("Pick a brand.")
        size = studio.size_for(form.get("size"), form.get("w"), form.get("h"))
        doc = None
        tv = form.get("template")
        if tv and str(tv).isdigit():
            t = studio.template(int(tv))
            if t:
                doc = t["doc"]
                if (t["w"], t["h"]) != (size["w"], size["h"]):
                    doc["resize_from"] = {"w": t["w"], "h": t["h"]}
                doc["w"], doc["h"] = size["w"], size["h"]
        iid = studio.create_design(pid, fid, size, form.get("title") or "", actor(request), doc)
    except studio.StudioError as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/studio/new?b={pid if pid is not None else ''}", status_code=303)
    return RedirectResponse(f"/studio/edit/{iid}" + ("" if doc else "?fresh=1"), status_code=303)


@app.get("/studio/edit/{iid}", response_class=HTMLResponse)
def studio_edit(request: Request, iid: int, fresh: int = 0):
    if (r := guard(request)):
        return r
    it = _design(iid)
    if not it:
        flash(request, "That design is gone.", "error")
        return RedirectResponse("/studio", status_code=303)
    pid = it["practice_id"]
    look = _brand_look(pid)
    kit_colors = []
    for c in (look["primary"], look["accent"]):
        if c and c not in kit_colors:
            kit_colors.append(c)
    return page(request, "studio_edit.html", it=it, brand_name=studio.brand_name(pid), folder=studio.folder(it["folder_id"]),
                look=look, colors=kit_colors, fonts=studio.brand_fonts(pid if pid is not None else studio.MCDP),
                font_list=studio.FONTS, sizes=studio.SIZES, fresh=fresh, statuses=studio.STATUSES,
                templates=studio.templates(pid), ai_on=ai.available(), is_practice=bool(pid),
                logo=(images.logo(pid) or {}).get("id") if pid else None, tpl_favs=_tpl_favs(request),
                photo_source=stock.image_provider())


def _tpl_favs_key(request: Request):
    return f"studio_tpl_favs_{int((me(request) or {}).get('id') or 0)}"


def _tpl_favs(request: Request):
    """Each person's starred templates ("b:<built-in key>" or "t:<team template id>"), newest first."""
    try:
        v = json.loads(db.get_setting(_tpl_favs_key(request)) or "[]")
        return [k for k in v if isinstance(k, str)][:200]
    except ValueError:
        return []


@app.post("/studio/template-favs")
async def studio_template_favs(request: Request):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    form = await request.form()
    key = str(form.get("key") or "").strip()
    if not re.fullmatch(r"(b:[a-z0-9_]{1,40}|t:\d{1,12})", key):
        return JSONResponse({"error": "That isn't a template."}, status_code=422)
    on = str(form.get("on") or "1") not in ("0", "false", "")
    favs = [k for k in _tpl_favs(request) if k != key]
    if on:
        favs.insert(0, key)
    db.set_setting(_tpl_favs_key(request), json.dumps(favs[:200]))
    return JSONResponse({"ok": True, "favs": favs[:200]})


@app.get("/studio/designs/{iid}/doc")
def studio_design_doc(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    if not _design(iid):
        return JSONResponse({"error": "That design is gone."}, status_code=404)
    return JSONResponse(studio.design_doc(iid), headers={"Cache-Control": "no-store"})


@app.post("/studio/designs/{iid}/save")
async def studio_design_save(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    form = await request.form()
    png = form.get("png")
    data = await png.read() if hasattr(png, "read") else None
    try:
        it = await asyncio.to_thread(studio.save_design, iid, await _form_text(form, "doc"), data, actor(request))
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    if form.get("title"):
        it = studio.update(iid, actor(request), title=form.get("title"))
    return {"ok": True, "saved": it["updated_at"], "title": it["title"], "rendered": bool(data)}


@app.post("/studio/designs/{iid}/copy")
async def studio_design_copy(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    try:
        size = studio.size_for(j.get("size"), j.get("w"), j.get("h"))
        nid = studio.copy_design(iid, size, actor(request))
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"id": nid, "url": f"/studio/edit/{nid}"}


@app.post("/studio/designs/{iid}/upload")
async def studio_design_upload(request: Request, iid: int, file: UploadFile = File(...)):
    """A picture added from the computer while designing: it's kept in the brand's 'Design uploads' folder."""
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    it = _design(iid)
    if not it:
        return JSONResponse({"error": "That design is gone."}, status_code=404)
    data = await file.read(40 * 1024 * 1024 + 1)
    if len(data) > 40 * 1024 * 1024:
        return JSONResponse({"error": "Pictures up to 40 MB."}, status_code=422)
    mime = file.content_type or ""
    if not mime.startswith("image/") or "heic" in mime or "heif" in mime:
        return JSONResponse({"error": "Use a JPEG, PNG, WebP or SVG picture (export iPhone photos as JPEG)."}, status_code=422)
    import tempfile
    fd, path = tempfile.mkstemp(dir=studio._dir("tmp"))
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    pid = it["practice_id"] if it["practice_id"] is not None else studio.MCDP
    try:
        nid = await asyncio.to_thread(studio.add_file, path, file.filename or "picture", pid, studio.media_folder(pid),
                                      actor(request), mime)
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    n = studio.item(nid)
    return {"id": nid, "src": f"/studio/img/{nid}", "thumb": f"/studio/t/{nid}", "w": n["width"], "h": n["height"],
            "name": n["title"]}


@app.get("/studio/img/{iid}")
async def studio_img(request: Request, iid: int):
    """A Studio picture from the hub's own address (the editor can't export a design holding pictures from elsewhere)."""
    from fastapi.responses import FileResponse
    if not authed(request):
        return Response(status_code=401)
    it = studio.item(iid)
    if not it or it["kind"] not in ("image", "design"):
        return Response(status_code=404)
    p = studio.local_path(it)
    if p:
        return FileResponse(p, media_type=it["mime"] or None, headers={"Cache-Control": "private, max-age=86400"})
    try:
        url = await asyncio.to_thread(studio.download_url, it)
        import urllib.request as _u

        def fetch():
            with _u.urlopen(url, timeout=60) as r:
                return r.read(60 * 1024 * 1024)
        data = await asyncio.to_thread(fetch)
    except Exception:
        return Response(status_code=503)
    return Response(data, media_type=it["mime"] or "application/octet-stream", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/studio/media")
def studio_media(request: Request, pid: str = ""):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    b = _brand_in(pid)
    return _media_for(b)


@app.post("/studio/brand/{pid}/fonts")
async def studio_brand_fonts(request: Request, pid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    if _brand_in(pid) is None:
        return JSONResponse({"error": "Pick a brand."}, status_code=422)
    try:
        studio.set_brand_fonts(pid, j.get("heading"), j.get("body"))
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"ok": True, **studio.brand_fonts(pid)}


@app.post("/studio/templates")
async def studio_template_save(request: Request):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    form = await request.form()
    png = form.get("png")
    data = await png.read() if hasattr(png, "read") else None
    scope = _brand_in(form.get("pid")) if form.get("scope") == "brand" else None
    try:
        tid = studio.save_template(form.get("name"), await _form_text(form, "doc"), data, scope, actor(request))
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"id": tid}


@app.get("/studio/templates/{tid}/doc")
def studio_template_doc(request: Request, tid: int):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    t = studio.template(tid)
    return JSONResponse(t["doc"]) if t else JSONResponse({"error": "gone"}, status_code=404)


@app.get("/studio/templates/{tid}/thumb")
def studio_template_thumb(request: Request, tid: int):
    from fastapi.responses import FileResponse
    if not authed(request):
        return Response(status_code=401)
    p = studio.template_thumb(tid)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=600"}) if os.path.exists(p) else _thumb_response(None)


@app.post("/studio/templates/{tid}/delete")
def studio_template_delete(request: Request, tid: int):
    if (r := guard(request)):
        return r
    studio.delete_template(tid)
    if "application/json" in request.headers.get("accept", ""):
        return {"ok": True}
    flash(request, "Template removed. Designs made from it are unchanged.")
    return RedirectResponse(_studio_back(request), status_code=303)


@app.post("/studio/ai/copy")
async def studio_ai_copy(request: Request):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    pid = _brand_in(j.get("pid"))
    try:
        got = await asyncio.to_thread(studio.ai_copy, pid or 0, j.get("brief"), j.get("size") or "")
    except (studio.StudioError, ai.AIError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"options": got}


@app.post("/studio/ai/image")
async def studio_ai_image(request: Request):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    pid = _brand_in(j.get("pid"))
    p = db.practice(pid) if pid else None
    if not p:
        return JSONResponse({"error": "AI pictures are made for a practice; open a practice's design."}, status_code=422)
    idea = (j.get("idea") or "").strip()[:300]
    if not idea:
        return JSONResponse({"error": "Describe the picture."}, status_code=422)
    tag = re.sub(r"\s+", " ", str(j.get("tag") or "")).strip()[:80]
    if tag:                                   # a template's photo idea: reuse the picture already made for it
        made = [a for a in images.assets(p["id"], "generated") if (a.get("label") or "") == tag]
        n = int(j.get("n") or 0)
        if made and n < len(made):
            a = made[n]
            return {"src": f"/brand-images/{a['id']}?full=1", "thumb": f"/brand-images/{a['id']}", "w": a["width"], "h": a["height"], "name": tag}
    try:
        ids = await asyncio.to_thread(images.generate, p, tag or idea, 1, None, actor(request), idea, bool(j.get("plain")))
    except images.ImageError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    a = images.asset(ids[0])
    return {"src": f"/brand-images/{a['id']}?full=1", "thumb": f"/brand-images/{a['id']}", "w": a["width"], "h": a["height"],
            "name": idea[:60]}


@app.post("/studio/designs/{iid}/check")
async def studio_design_check(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    it = _design(iid)
    j = await request.json()
    texts = [str(t)[:2000] for t in (j.get("texts") or [])][:60]
    return {"problems": studio.check_texts(it["practice_id"] if it else None, texts)}


# ---------- Studio phase 3: approvals, comments, posting as the brand ----------
def _base(request):
    return PUBLIC_URL or str(request.base_url).rstrip("/")


def _flow_back(iid, anchor):
    return RedirectResponse(f"/studio/i/{iid}#{anchor}", status_code=303)


@app.post("/studio/i/{iid}/approval")
async def studio_ask_approval(request: Request, iid: int):
    if (r := guard(request)):
        return r
    form = await request.form()
    kinds, uids, names, emails = (form.getlist(k) for k in ("step_kind", "step_user", "step_name", "step_email"))
    steps = []
    for i, k in enumerate(kinds):
        if k == "person" and i < len(uids) and str(uids[i]).isdigit():
            steps.append({"kind": "person", "user_id": int(uids[i])})
        elif k == "outside" and i < len(names) and (names[i] or "").strip():
            steps.append({"kind": "outside", "name": names[i], "email": emails[i] if i < len(emails) else ""})
    u = me(request) or {}
    try:
        first = await asyncio.to_thread(studio_flow.request_approval, iid, steps, form.get("note") or "", actor(request),
                                        u.get("email") or "", _base(request))
        if first and first["kind"] == "outside":
            request.session["studio_new_share"] = first.get("share_id")
            flash(request, f"Sent for approval. {first['name']} is first" +
                  (": the link was emailed." if first["email"] and microsoft.studio_on() else
                   ": copy their link below and send it to them."))
        else:
            flash(request, f"Sent for approval. {first['name'] if first else 'Everyone'} is first" +
                  (" and got an email." if microsoft.studio_on() else "; it's waiting under Studio → Waiting for me."))
    except (studio_flow.FlowError, studio.StudioError) as e:
        flash(request, str(e), "error")
    return _flow_back(iid, "approve")


@app.post("/studio/steps/{sid}")
async def studio_decide(request: Request, sid: int, decision: str = Form(""), comment: str = Form("")):
    if (r := guard(request)):
        return r
    st = studio_flow.step(sid)
    u = me(request) or {}
    if not st:
        return RedirectResponse("/studio", status_code=303)
    if st["kind"] != "person" or (st["user_id"] != u.get("id") and u.get("role") != "admin"):
        flash(request, "That approval is someone else's.", "error")
        return _flow_back(st["item_id"], "approve")
    who = actor(request) + ("" if st["user_id"] == u.get("id") else f" (for {st['name']})")
    try:
        nxt = await asyncio.to_thread(studio_flow.decide, sid, decision, comment, who, _base(request))
        if decision == "changes":
            flash(request, "Sent back with your changes. It's a draft again.")
        else:
            flash(request, f"Approved. Next: {nxt['name']}." if nxt else "Approved. That was the last approval, so it's ready to post.")
    except studio_flow.FlowError as e:
        flash(request, str(e), "error")
    return _flow_back(st["item_id"], "approve")


@app.post("/studio/i/{iid}/approval/stop")
def studio_stop_approval(request: Request, iid: int):
    if (r := guard(request)):
        return r
    studio_flow.cancel_chain(iid, actor(request))
    flash(request, "Approval stopped. It's a draft again.")
    return _flow_back(iid, "approve")


@app.post("/studio/i/{iid}/comments")
async def studio_comment(request: Request, iid: int, body: str = Form(""), x: str = Form(""), y: str = Form("")):
    if (r := guard(request)):
        return r
    u = me(request) or {}
    try:
        studio_flow.add_comment(iid, u.get("id"), actor(request), body, x, y, base_url=_base(request))
    except studio_flow.FlowError as e:
        flash(request, str(e), "error")
    return _flow_back(iid, "comments")


@app.post("/studio/comments/{cid}/resolve")
def studio_comment_resolve(request: Request, cid: int, undo: str = Form(""), item: int = Form(0)):
    if (r := guard(request)):
        return r
    studio_flow.resolve_comment(cid, actor(request), undo == "1")
    return _flow_back(item, "comments")


@app.post("/studio/comments/{cid}/delete")
def studio_comment_delete(request: Request, cid: int, item: int = Form(0)):
    if (r := guard(request)):
        return r
    u = me(request) or {}
    if not studio_flow.delete_comment(cid, u.get("id"), u.get("role") == "admin"):
        flash(request, "Only the person who wrote a comment can delete it.", "error")
    return _flow_back(item, "comments")


@app.post("/studio/i/{iid}/post")
async def studio_post(request: Request, iid: int):
    if (r := guard(request)):
        return r
    form = await request.form()
    kind = form.get("when") or "schedule"
    opts = {"ig_story": form.get("ig_story") == "1", "gmb_cta": form.get("gmb_cta") or "NONE",
            "gmb_url": (form.get("gmb_url") or "").strip()}
    u = me(request) or {}
    try:
        when = studio_flow.parse_when(form.get("day"), form.get("time")) if kind == "schedule" else None
        n = await asyncio.to_thread(studio_flow.publish, iid, form.getlist("channels"), form.get("caption") or "", kind,
                                    when, opts, actor(request), form.get("override") == "1" and u.get("role") == "admin")
        if kind == "draft":
            flash(request, f"Saved as a draft in Social for {n} channel{'s' if n != 1 else ''}. Finish it there, or schedule it here.")
        elif kind == "now":
            flash(request, f"Sent to Social to post now on {n} channel{'s' if n != 1 else ''}. The links show here once it's live.")
        else:
            local = when.astimezone(studio_flow.local_tz())
            flash(request, f"Scheduled on {n} channel{'s' if n != 1 else ''} for {local.strftime('%a %b %-d at %-I:%M %p')}. "
                           "It's in Social's calendar too.")
    except (studio_flow.FlowError, social.SocialError, studio.StudioError) as e:
        flash(request, str(e), "error")
    return _flow_back(iid, "post")


@app.post("/studio/posts/{rid}/cancel")
async def studio_post_cancel(request: Request, rid: int):
    if (r := guard(request)):
        return r
    row = studio_flow.post_row(rid)
    try:
        await asyncio.to_thread(studio_flow.cancel_post, rid, actor(request))
        flash(request, "Cancelled; it won't post.")
    except (studio_flow.FlowError, social.SocialError) as e:
        flash(request, str(e), "error")
    return _flow_back(row["item_id"] if row else 0, "post")


@app.post("/studio/posts/sync")
async def studio_posts_sync(request: Request):
    if (r := guard(request)):
        return r
    n = await asyncio.to_thread(studio_flow.sync_posts)
    flash(request, f"Checked with Social: {n} update{'s' if n != 1 else ''}." if n else "Checked with Social: nothing new yet.")
    return RedirectResponse(_studio_back(request), status_code=303)


@app.post("/studio/i/{iid}/caption")
async def studio_caption(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    it = studio.item(iid)
    if not it:
        return JSONResponse({"error": "That file is gone."}, status_code=404)
    j = await request.json()
    try:
        caps = await asyncio.to_thread(studio_flow.ai_caption, it, (j.get("brief") or "").strip())
    except (studio_flow.FlowError, ai.AIError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"captions": caps}


@app.post("/connections/posting")
async def connections_posting(request: Request, api_key: str = Form(""), api_url: str = Form(""), clear: str = Form("")):
    if (r := guard(request)):
        return r
    if clear == "1":
        social.save(key="")
    else:
        social.save(key=api_key if api_key.strip() else None, url=api_url)
    try:
        await asyncio.to_thread(social.check)
        n = len(await asyncio.to_thread(social.integrations, True))
        flash(request, f"Social answered: {n} Facebook, Instagram and Google channel{'s' if n != 1 else ''} to post to.")
    except social.SocialError as e:
        flash(request, f"Saved, but Social didn't answer yet: {e}", "error")
    return RedirectResponse("/connections#posting", status_code=303)


# ---------- Studio phase 4: video ----------
def _video(iid):
    it = studio.item(iid)
    return it if studio_video.is_project(it) else None


@app.post("/studio/videos")
async def studio_video_create(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    pid = _brand_in(form.get("pid"))
    fv = form.get("folder")
    try:
        if pid is None:
            raise studio_video.VideoError("Pick a brand.")
        clips = [int(x) for x in form.getlist("clip") if str(x).isdigit()]
        iid = await asyncio.to_thread(studio_video.create_project, pid, int(fv) if fv and str(fv).isdigit() else None,
                                      form.get("vsize") or "story", form.get("title") or "", actor(request), clips)
    except (studio_video.VideoError, studio.StudioError) as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/studio/new?b={pid if pid is not None else ''}#video", status_code=303)
    return RedirectResponse(f"/studio/video/{iid}", status_code=303)


@app.get("/studio/video/{iid}", response_class=HTMLResponse)
def studio_video_page(request: Request, iid: int):
    if (r := guard(request)):
        return r
    it = _video(iid)
    if not it:
        flash(request, "That video project is gone.", "error")
        return RedirectResponse("/studio", status_code=303)
    pid = it["practice_id"] if it["practice_id"] is not None else studio.MCDP
    look = _brand_look(it["practice_id"])
    return page(request, "studio_video.html", it=it, brand_name=studio.brand_name(it["practice_id"]),
                folder=studio.folder(it["folder_id"]), look=look, speeds=studio_video.SPEEDS,
                ffmpeg_ok=studio_video.ffmpeg_ok(), captions_ok=studio_video.captions_ready(),
                uploads_folder=studio.add_folder(pid, "Video uploads", "Studio video") if can_edit_now(request) else None,
                pid=pid, render=studio_video.status(iid), max_total=studio_video.MAX_TOTAL,
                transitions=video2.TRANSITIONS, presets=[(k, v[0], v[1]) for k, v in video2.PRESETS.items()],
                motions=video2.MOTIONS, anim_in=video2.ANIM_IN, anim_out=video2.ANIM_OUT, anim_loop=video2.ANIM_LOOP,
                font_groups=video2.FONT_GROUPS, font_single=video2.FONT_SINGLE, frame_anims=sorted(video2.FRAME_ANIMS), loop_period=video2.LOOP_PERIOD, bg_modes=video2.BG_MODES,
                fonts=video2.FONTS, bg_ok=bgremove.available(), stock_keys=stock.keys(),
                ratios=[(k, l, w, h) for k, l, w, h in studio_video.SIZES],
                effects=list(vfx.EFFECTS), fx_groups=list(vfx.GROUPS), fx_scopes=list(vfx.SCOPES), trans_fx=vfx.TRANS_FX,
                sprites={k: list(v) for k, v in vfx.SPRITES.items()}, sprite_fx={k: list(v) for k, v in vfx.SPRITE_FX.items()})


def can_edit_now(request):
    u = me(request)
    return bool(u) and users.rank(u["role"]) >= users.rank("editor")


@app.get("/studio/video/{iid}/project")
def studio_video_project(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    try:
        p = studio_video.project(iid)
    except studio_video.VideoError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    ids = {c["item"] for c in p["main"]} | {L["item"] for L in p["layers"] if L.get("item")}
    ids |= {c["bg"]["item"] for c in p["main"] if (c.get("bg") or {}).get("item")}
    if p.get("music"):
        ids.add(p["music"]["item"])
    for i in ids:                                  # start the light preview copies for this project's videos
        proxy.ensure(i)
    return JSONResponse({"project": p, "items": {i: _media_row(studio.item(i)) for i in ids if studio.item(i)}},
                        headers={"Cache-Control": "no-store"})


def _media_row(it):
    """What the video editor needs to know about a library file."""
    row = {"id": it["id"], "title": it["title"], "kind": "image" if it["kind"] in ("image", "design") else it["kind"],
           "duration": it["duration"], "w": it["width"], "h": it["height"], "thumb": f"/studio/t/{it['id']}",
           "src": f"/studio/media/{it['id']}", "source": it.get("source")}
    if it["kind"] == "video":
        bs = bgremove.status(it["id"])
        row["mask"] = f"/studio/mask/{it['id']}" if bs.get("state") == "ready" else None
        row["mask_state"] = bs.get("state")
        row["proxy"] = proxy.state(it)
    return row


@app.post("/studio/video/{iid}/project")
async def studio_video_save(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    if not _video(iid):
        return JSONResponse({"error": "That video project is gone."}, status_code=404)
    j = await request.json()
    try:
        p = await asyncio.to_thread(studio_video.save_project, iid, j.get("project") or {}, actor(request))
        if j.get("title"):
            studio.update(iid, actor(request), title=j["title"])
    except (studio_video.VideoError, studio.StudioError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"ok": True, "project": p, "total": round(studio_video.total(p), 2)}


@app.post("/studio/video/{iid}/ratio")
async def studio_video_ratio(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    if not _video(iid):
        return JSONResponse({"error": "That video project is gone."}, status_code=404)
    j = await request.json()
    try:
        p = await asyncio.to_thread(studio_video.set_ratio, iid, j.get("size") or "", actor(request))
    except studio_video.VideoError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"ok": True, "project": p}


@app.get("/studio/fx/{name}.png")
def studio_fx_sprite(request: Request, name: str, w: int = 1080, h: int = 1920):
    """The overlay pictures the snow, hearts, confetti… effects use (the same ones the MP4 gets)."""
    from fastapi.responses import FileResponse
    if not authed(request):
        return Response(status_code=401)
    if name not in vfx.SPRITES or not (16 <= w <= 4096 and 16 <= h <= 4096):
        return Response(status_code=404)
    return FileResponse(vfx.sprite_path(name, w, h), media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/studio/video-media")
def studio_video_media(request: Request, pid: str = ""):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    b = _brand_in(pid)
    out = {"videos": [], "pictures": [], "sounds": []}
    for it in studio.items(b if b is not None else studio.MCDP, limit=300):
        if it["kind"] == "video" and not studio_video.is_project(it):
            out["videos"].append(_media_row(it))
        elif it["kind"] in ("image", "design"):
            out["pictures"].append(_media_row(it))
    out["sounds"] = stock.music_library()
    return out


@app.get("/studio/video-info/{item_id}")
async def studio_video_info(request: Request, item_id: int):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    try:
        info = await asyncio.to_thread(studio_video.media_info, item_id)
        if info.get("kind") == "video":
            proxy.ensure(item_id)
        return info
    except studio_video.VideoError as e:
        return JSONResponse({"error": str(e)}, status_code=422)


@app.post("/studio/video/{iid}/render")
async def studio_video_render(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    if not _video(iid):
        return JSONResponse({"error": "That video project is gone."}, status_code=404)
    if not studio_video.ffmpeg_ok():
        return JSONResponse({"error": "The video tools aren't installed on the server yet. Run the hub update on the NAS."}, status_code=422)
    texts, frames = {}, {}
    if "multipart/form-data" in request.headers.get("content-type", ""):
        form = await request.form(max_files=4000, max_fields=4000)
        for k, v in form.multi_items():
            if not hasattr(v, "read"):
                continue
            if k.startswith("text_"):
                texts[re.sub(r"[^a-zA-Z0-9_-]", "", k[5:])[:24]] = await v.read(20 * 1024 * 1024)
            elif k.startswith("tf_"):                  # tf_<layer>_<in|mid|out>_<n>: frames of an animated text layer
                m = re.fullmatch(r"tf_([A-Za-z0-9]{1,24})_(in|mid|out)_(\d{1,4})", k)
                if m:
                    frames.setdefault(m[1], {}).setdefault(m[2], []).append((int(m[3]), await v.read(8 * 1024 * 1024)))
        texts["__frames__"] = {lid: {seg: [b for _, b in sorted(fs)] for seg, fs in segs.items()} for lid, segs in frames.items()}
    started = studio_video.render(iid, actor(request), texts)
    return {**studio_video.status(iid), "started": started}


@app.get("/studio/video/{iid}/render")
def studio_video_render_status(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    it = studio.item(iid)
    return {**studio_video.status(iid), "updated": (it or {}).get("updated_at")}


@app.post("/studio/video/{iid}/captions")
async def studio_video_captions(request: Request, iid: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    try:
        caps = await asyncio.to_thread(studio_video.transcribe, iid)
    except studio_video.VideoError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"captions": caps}


@app.get("/studio/video/{iid}/outro.png")
def studio_video_outro(request: Request, iid: int, text: str = ""):
    if not authed(request):
        return Response(status_code=401)
    it = _video(iid)
    if not it:
        return Response(status_code=404)
    p = studio_video.project(iid)
    w, h = p["w"], p["h"]
    k = 540 / max(w, h)
    data = studio_video.outro_png(it["practice_id"] if it["practice_id"] not in (None, studio.MCDP) else None,
                                  int(w * k), int(h * k), text[:60])
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=60"})


# ---------- Studio video v2: media for the editor, background removal, stock video and music ----------
@app.get("/studio/media/{iid}")
async def studio_media_stream(request: Request, iid: int):
    """A library file from the hub's own address (so the editor can draw and colour it), with seeking."""
    from fastapi.responses import FileResponse, StreamingResponse
    if not authed(request):
        return Response(status_code=401)
    it = studio.item(iid)
    if not it:
        return Response(status_code=404)
    # the editor plays light copies (proxies): small, quick to seek, no trip to SharePoint
    if it["kind"] == "video" and not request.query_params.get("full"):
        pp = proxy.path(it)
        if pp:
            return FileResponse(pp, media_type="video/mp4", headers={"Cache-Control": "private, max-age=86400"})
        proxy.ensure(iid)
    if it["kind"] in ("image", "design"):
        ip = await asyncio.to_thread(proxy.image, it)
        if ip:
            return FileResponse(ip, media_type="image/png" if ip.endswith(".png") else "image/jpeg",
                                headers={"Cache-Control": "private, max-age=86400"})
    p = studio.local_path(it)
    if p:
        return FileResponse(p, media_type=it["mime"] or None, headers={"Cache-Control": "private, max-age=3600"})
    try:
        url = await asyncio.to_thread(studio.download_url, it)
    except Exception:
        url = None
    if not url:
        return Response(status_code=503)
    import urllib.request as _u
    hdr = {"Range": request.headers["range"]} if request.headers.get("range") else {}

    def opener():
        return _u.urlopen(_u.Request(url, headers=hdr), timeout=60)
    try:
        upstream = await asyncio.to_thread(opener)
    except Exception:
        return Response(status_code=503)

    def body():
        try:
            while True:
                chunk = upstream.read(256 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()
    keep = {k: v for k, v in upstream.headers.items() if k.lower() in ("content-length", "content-range", "accept-ranges")}
    keep.setdefault("Accept-Ranges", "bytes")
    return StreamingResponse(body(), status_code=upstream.status, media_type=it["mime"] or "application/octet-stream",
                             headers={**keep, "Cache-Control": "private, max-age=3600"})


@app.get("/studio/proxy")
def studio_proxy_states(request: Request, ids: str = ""):
    """Which of these videos have their light preview copy ready."""
    if not authed(request):
        return JSONResponse({}, status_code=401)
    out = {}
    for x in ids.split(",")[:60]:
        if x.strip().isdigit():
            out[x.strip()] = proxy.state(studio.item(int(x)))
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.get("/studio/mask/{item_id}")
def studio_mask_file(request: Request, item_id: int):
    from fastapi.responses import FileResponse
    if not authed(request):
        return Response(status_code=401)
    p = bgremove.mask_path(item_id)
    return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"}) if p else Response(status_code=404)


@app.post("/studio/bg/{item_id}")
def studio_bg_start(request: Request, item_id: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    try:
        bgremove.start(item_id)
    except bgremove.BGError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {**bgremove.status(item_id), "mask": f"/studio/mask/{item_id}" if bgremove.mask_path(item_id) else None}


@app.get("/studio/bg/{item_id}")
def studio_bg_status(request: Request, item_id: int):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    return {**bgremove.status(item_id), "mask": f"/studio/mask/{item_id}" if bgremove.mask_path(item_id) else None,
            "available": bgremove.available()}


@app.get("/studio/stock/videos")
async def studio_stock_videos(request: Request, q: str = "", page: int = 1, o: str = ""):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    try:
        if stock.provider() == "pexels":
            return await asyncio.to_thread(stock.pexels_videos, q, o, page)
        return {**(await asyncio.to_thread(stock.pixabay_search, q, o, page)), "provider": "pixabay"}
    except stock.StockError as e:
        return JSONResponse({"error": str(e), "blocked": e.blocked}, status_code=422)


@app.get("/studio/stock/images")
async def studio_stock_images(request: Request, q: str = "", kind: str = "photo", o: str = "", page: int = 1):
    """Photos (and drawings) for the picture designer from Pexels, Unsplash or Pixabay, whichever has a key (cached a day)."""
    if not authed(request):
        return JSONResponse({}, status_code=401)
    try:
        prov = stock.image_provider()
        if prov == "pexels":
            return await asyncio.to_thread(stock.pexels_photos, q, kind, o, page)
        if prov == "unsplash":
            return await asyncio.to_thread(stock.unsplash_photos, q, kind, o, page)
        return {**(await asyncio.to_thread(stock.pixabay_images, q, kind, o, page)), "provider": "pixabay"}
    except stock.StockError as e:
        return JSONResponse({"error": str(e), "blocked": e.blocked}, status_code=422)


@app.post("/studio/stock/unsplash/used")
async def studio_stock_unsplash_used(request: Request):
    """A photo from Unsplash went into a design: tell Unsplash (its rules ask for this)."""
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    try:
        await asyncio.to_thread(stock.unsplash_used, j.get("dl"))
    except stock.StockError as e:
        return JSONResponse({"error": str(e), "blocked": e.blocked}, status_code=422)
    return {"ok": True}


def _img_row(iid):
    n = studio.item(iid)
    return {"id": iid, "src": f"/studio/img/{iid}", "thumb": f"/studio/t/{iid}", "w": n["width"], "h": n["height"], "name": n["title"]}


@app.post("/studio/stock/images/import")
async def studio_stock_image_import(request: Request):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    pid = _brand_in(j.get("pid"))
    pid = pid if pid is not None else studio.MCDP
    try:
        pexels = isinstance(j.get("hit"), dict) and j["hit"].get("provider") == "pexels"
        iid = await asyncio.to_thread(stock.pexels_photo_import if pexels else stock.image_import, j.get("hit"), pid, actor(request))
    except stock.StockError as e:
        return JSONResponse({"error": str(e), "blocked": e.blocked}, status_code=422)
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return _img_row(iid)


@app.post("/studio/stock/images/attach")
async def studio_stock_image_attach(request: Request):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    pid = _brand_in(j.get("pid"))
    pid = pid if pid is not None else studio.MCDP
    try:
        it = studio.item(int(j.get("item") or 0))
        if not it or it["practice_id"] != pid or it["kind"] != "image":
            raise stock.StockError("That picture isn't in this brand's library.")
        pexels = isinstance(j.get("hit"), dict) and j["hit"].get("provider") == "pexels"
        iid = await asyncio.to_thread(stock.pexels_photo_attach if pexels else stock.image_attach, it["id"], j.get("hit"), pid, actor(request))
    except (stock.StockError, studio.StudioError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return _img_row(iid)


@app.get("/studio/stock/browser-key")
def studio_stock_browser_key(request: Request):
    """When Pixabay's firewall turns the NAS away, the editor searches from the person's browser with the team's key."""
    if not authed(request) or not can_edit_now(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    return JSONResponse({"pixabay": stock._tidy_key(db.get_setting("pixabay_key")) or "", "pexels": stock._tidy_key(db.get_setting("pexels_key")) or "",
                         "unsplash": stock._tidy_key(db.get_setting("unsplash_key")) or "",
                         "provider": stock.provider(), "images": stock.image_provider()}, headers={"Cache-Control": "no-store"})


@app.post("/studio/stock/videos/{hit_id}/attach")
async def studio_stock_video_attach(request: Request, hit_id: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    try:
        pexels = isinstance(j.get("hit"), dict) and j["hit"].get("provider") == "pexels"
        iid = await asyncio.to_thread(stock.pexels_video_attach if pexels else stock.pixabay_attach, int(j.get("item") or 0), j.get("hit"), actor(request))
    except (stock.StockError, studio.StudioError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return _media_row(studio.item(iid))


@app.post("/studio/stock/videos/{hit_id}")
async def studio_stock_video_add(request: Request, hit_id: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    pid = _brand_in(j.get("pid"))
    try:
        if isinstance(j.get("hit"), dict) and j["hit"].get("provider") == "pexels":
            iid = await asyncio.to_thread(stock.pexels_video_import, j["hit"], pid if pid is not None else studio.MCDP, actor(request))
        else:
            iid = await asyncio.to_thread(stock.pixabay_import, hit_id, j.get("q") or "", j.get("page") or 1,
                                          pid if pid is not None else studio.MCDP, actor(request), j.get("hit"))
    except stock.StockError as e:
        return JSONResponse({"error": str(e), "blocked": e.blocked,
                             "folder": stock.stock_folder(pid if pid is not None else studio.MCDP) if e.blocked else None}, status_code=422)
    except studio.StudioError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return _media_row(studio.item(iid))


@app.get("/studio/stock/music")
async def studio_stock_music(request: Request, q: str = "", page: int = 1):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    try:
        return await asyncio.to_thread(stock.freesound_search, q, page)
    except stock.StockError as e:
        return JSONResponse({"error": str(e)}, status_code=422)


@app.post("/studio/stock/music/{sound_id}")
async def studio_stock_music_add(request: Request, sound_id: int):
    if not authed(request):
        return JSONResponse({"error": "Sign in again."}, status_code=401)
    j = await request.json()
    try:
        iid = await asyncio.to_thread(stock.freesound_import, sound_id, j.get("q") or "", j.get("page") or 1, actor(request))
    except (stock.StockError, studio.StudioError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    it = studio.item(iid)
    return {"id": iid, "title": it["title"], "duration": it["duration"], "src": f"/studio/media/{iid}", "notes": it["notes"]}


@app.get("/studio/music")
def studio_music_list(request: Request, q: str = ""):
    if not authed(request):
        return JSONResponse({}, status_code=401)
    return {"tracks": stock.music_library(q), "folder": stock.music_folder()}


@app.post("/connections/stock")
def connections_stock(request: Request, pixabay_key: str = Form(""), freesound_key: str = Form(""), pexels_key: str = Form(""),
                      unsplash_key: str = Form(""), clear: str = Form("")):
    if (r := guard(request)):
        return r
    if clear in ("pixabay", "freesound", "pexels", "unsplash"):
        stock.save_keys(**{clear: ""})
        flash(request, "Key removed.")
    else:
        stock.save_keys(pixabay=pixabay_key if pixabay_key.strip() else None,
                        freesound=freesound_key if freesound_key.strip() else None,
                        pexels=pexels_key if pexels_key.strip() else None,
                        unsplash=unsplash_key if unsplash_key.strip() else None)
        flash(request, "Saved. Stock photos, video and music search use these in the Studio.")
    return RedirectResponse("/connections#stock", status_code=303)


# ---- marketing intake: a private link per practice (no sign-in) with a yes/no form; answers in the hub
def _intake_editor(request):
    if (r := guard(request)):
        return r
    u = me(request)
    if users.rank(u["role"]) < users.rank("editor"):
        flash(request, "Only editors and admins can open intake forms.", "error")
        return RedirectResponse("/practices", status_code=303)
    return None


def _intake_link(request, token):
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return f"{base}/i/{token}"


@app.get("/intake", response_class=HTMLResponse)
def intake_list(request: Request):
    if (r := _intake_editor(request)):
        return r
    u = me(request)
    mine = [p for p in db.practices() if users.sees_practice(u, p["id"])]
    names = {p["id"]: p["name"] for p in db.practices(include_hidden=True)}
    rows = []
    for f in intake.forms():
        if not users.sees_practice(u, f["practice_id"]):
            continue
        done, total = intake.progress(f["data"])
        rows.append({**f, "practice": names.get(f["practice_id"], "(removed)"), "done": done, "total": total,
                     "live": f["expires_at"] > db.now(), "link": _intake_link(request, f["token"])})
    new_link = request.session.pop("intake_new", None)
    return page(request, "intake_list.html", rows=rows, practices=mine, new_link=new_link,
                access=db.get_setting("intake_access_email") or "", link_days=intake.LINK_DAYS)


@app.post("/intake/new")
def intake_new(request: Request, practice_id: int = Form(...)):
    if (r := _intake_editor(request)):
        return r
    if not users.sees_practice(me(request), practice_id) or not db.practice(practice_id):
        flash(request, "Pick one of your practices.", "error")
        return RedirectResponse("/intake", status_code=303)
    fid, token = intake.new_link(practice_id, (me(request) or {}).get("email") or actor(request))
    request.session["intake_new"] = {"id": fid, "link": _intake_link(request, token)}
    return RedirectResponse("/intake", status_code=303)


@app.post("/intake/email")
def intake_email(request: Request, email: str = Form("")):
    email = email.strip()
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        flash(request, "That doesn't look like an email address.", "error")
    else:
        db.set_setting("intake_access_email", email)
        flash(request, "Saved. The form now asks practices to give access to " + (email or "whoever made the link") + ".")
    return RedirectResponse("/intake", status_code=303)


def _intake_form(request, fid):
    f = intake.get(fid)
    if not f or not users.sees_practice(me(request), f["practice_id"]):
        return None
    return f


@app.get("/intake/{fid}", response_class=HTMLResponse)
def intake_view(request: Request, fid: int):
    if (r := _intake_editor(request)):
        return r
    f = _intake_form(request, fid)
    if not f:
        return RedirectResponse("/intake", status_code=303)
    shown = request.session.pop("intake_shown", None)
    shown = shown if shown and shown.get("id") == fid else None
    return page(request, "intake_view.html", f=f, practice=db.practice(f["practice_id"]), sections=intake.answers(f),
                link=_intake_link(request, f["token"]), live=f["expires_at"] > db.now(), shown=shown,
                reveals=intake.reveals(fid) if me(request)["role"] == "admin" else [])


@app.post("/intake/{fid}/reveal")
def intake_reveal(request: Request, fid: int, field: str = Form("")):
    f = _intake_form(request, fid)
    if not f or field not in f["secret_keys"]:
        return RedirectResponse(f"/intake/{fid}", status_code=303)
    val = intake.reveal(fid, field, actor(request))
    request.session["intake_shown"] = {"id": fid, "field": field, "value": val}
    return RedirectResponse(f"/intake/{fid}#pw", status_code=303)


@app.post("/intake/{fid}/revoke")
def intake_revoke(request: Request, fid: int):
    if _intake_form(request, fid):
        intake.revoke(fid)
        flash(request, "Link switched off. The answers are kept.")
    return RedirectResponse(f"/intake/{fid}", status_code=303)


@app.post("/intake/{fid}/extend")
def intake_extend(request: Request, fid: int):
    if (r := _intake_editor(request)):
        return r
    if _intake_form(request, fid):
        intake.extend(fid)
        flash(request, f"Link works for another {intake.LINK_DAYS} days.")
    return RedirectResponse(f"/intake/{fid}", status_code=303)


def _intake_public(request, f, done=False, status=200):
    p = db.practice(f["practice_id"]) or {"name": "Your practice"}
    f = dict(f, data=dict(f["data"]))
    f["data"].setdefault("about.practice", p["name"])
    return templates.TemplateResponse(request, "intake_form.html", {
        "practice": p, "f": f, "sections": intake.SECTIONS, "vendor_fields": intake.VENDOR_FIELDS,
        "access": intake.access_email(f), "done": done, "saved": request.query_params.get("saved") == "1" and not done,
        "static_v": STATIC_V}, status_code=status,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"})


def _intake_gone(request):
    return HTMLResponse("<!doctype html><meta name=viewport content='width=device-width'><title>Link ended</title>"
                        "<body style='font:17px/1.5 system-ui;max-width:36rem;margin:15vh auto;padding:0 1rem'>"
                        "<h1>This link has ended</h1><p>Ask your MCDP contact for a new one; anything you already sent "
                        "is safe with us.</p>", status_code=410, headers={"X-Robots-Tag": "noindex"})


@app.get("/i/{token}", response_class=HTMLResponse)
def intake_public(request: Request, token: str):
    f = intake.by_token(token)
    if not f:
        return _intake_gone(request)
    return _intake_public(request, f, done=request.session.pop(f"intake_done{f['id']}", False))


@app.post("/i/{token}")
async def intake_public_save(request: Request, token: str):
    f = intake.by_token(token)
    if not f:
        return _intake_gone(request)
    form = await request.form()
    submit = bool(form.get("_submit"))
    intake.save(f, form, submit=submit)
    if submit:
        request.session[f"intake_done{f['id']}"] = True
        try:
            to = [x for x in {f.get("created_by") or "", db.get_setting("intake_access_email") or ""} if "@" in x]
            if to:
                p = db.practice(f["practice_id"]) or {"name": "A practice"}
                from html import escape as _esc
                url = f"{PUBLIC_URL or str(request.base_url).rstrip('/')}/intake/{f['id']}"
                def _mail(*a):
                    try:
                        microsoft.send_mail(*a)
                    except Exception:
                        pass
                _threading.Thread(target=_mail, daemon=True, args=(
                    to, f"Marketing intake sent: {p['name']}",
                    f"<p>{_esc(p['name'])} sent their marketing intake form.</p><p><a href='{url}'>Open the answers</a></p>")).start()
        except Exception:
            pass
    return RedirectResponse(f"/i/{token}" + ("" if submit else "?saved=1") + "#top", status_code=303)


# ---- email campaigns (Studio › Email campaigns), sent as each practice through Azure Communication Services
def _email_gate(request):
    if (r := guard(request)):
        return r
    u = me(request)
    if users.rank(u["role"]) < users.rank("editor") or not users.can(u, "email"):
        flash(request, "Sending email isn't switched on for you. Ask an admin (Team page › Send email).", "error")
        return RedirectResponse("/studio", status_code=303)
    return None


def _email_practices(request):
    u = me(request)
    return [p for p in db.practices() if users.sees_practice(u, p["id"])]


def _email_campaign(request, cid):
    k = emailer.campaign(cid)
    if not k or not users.sees_practice(me(request), k["practice_id"]):
        return None, None
    return k, db.practice(k["practice_id"])


def _email_base(request):
    return PUBLIC_URL or str(request.base_url).rstrip("/")


@app.get("/studio/email", response_class=HTMLResponse)
def email_home(request: Request, status: str = "", b: str = ""):
    if (r := _email_gate(request)):
        return r
    plist = _email_practices(request)
    ids = {p["id"] for p in plist}
    pick = int(b) if b.isdigit() and int(b) in ids else None
    rows = emailer.campaigns({pick} if pick else ids, status)
    names = {p["id"]: p for p in plist}
    ss = emailer.senders()
    for r_ in rows:
        r_["practice"] = names.get(r_["practice_id"], {}).get("name", "?")
        r_["from_addr"] = (ss.get(r_["practice_id"]) or {}).get("from_addr", "")
        r_["color"] = emailer.look(r_["practice_id"])["primary"]
    counts = {s_: len([x for x in emailer.campaigns(ids) if x["status"] == s_]) for s_ in ("draft", "queued", "sending", "sent")}
    return page(request, "email_list.html", rows=rows, status=status, plist=plist, pick=pick, counts=counts,
                configured=emailer.configured())


@app.get("/studio/email/new", response_class=HTMLResponse)
def email_new_page(request: Request, b: str = ""):
    if (r := _email_gate(request)):
        return r
    plist = _email_practices(request)
    ss = emailer.senders()
    cards = []
    for p in plist:
        s = ss.get(p["id"])
        cards.append({"p": p, "s": s, "lists": emailer.lists(p["id"]), "color": emailer.look(p["id"])["primary"],
                      "ready": bool(s and s["ready"]), "short": emailer.short_name(p)})
    cards.sort(key=lambda c: (not c["ready"], c["p"]["name"].lower()))
    tmpl = {c["p"]["id"]: emailer.templates(c["p"]["id"])[1] for c in cards}
    pick = int(b) if b.isdigit() else None
    return page(request, "email_new.html", cards=cards, builtins=emailer.templates(0)[0], saved=tmpl, pick=pick)


@app.post("/studio/email/new")
async def email_new(request: Request):
    if (r := _email_gate(request)):
        return r
    form = await request.form()
    try:
        pid = int(form.get("practice_id") or 0)
    except ValueError:
        pid = 0
    if pid not in {p["id"] for p in _email_practices(request)}:
        flash(request, "Pick one of your practices.", "error")
        return RedirectResponse("/studio/email/new", status_code=303)
    ids = [int(x) for x in form.getlist("list_id") if str(x).isdigit()]
    cid = emailer.new_campaign(pid, form.get("name"), ids, form.get("start") or "newsletter", actor(request))
    return RedirectResponse(f"/studio/email/{cid}", status_code=303)


def _email_media(pid):
    out = []
    for it in studio.items(pid=pid, limit=300):
        if it["kind"] in ("image", "design") and (it.get("mime") or "").startswith("image/"):
            out.append({"id": it["id"], "title": it["title"]})
    return out


@app.get("/studio/email/{cid}", response_class=HTMLResponse)
def email_edit(request: Request, cid: int):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if not k:
        return RedirectResponse("/studio/email", status_code=303)
    if k["status"] != "draft":
        return page(request, "email_report.html", k=k, practice=p, st=emailer.stats(cid), clicks=emailer.link_clicks(cid),
                    s=emailer.sender(p["id"]))
    lk = emailer.look(p["id"])
    revs, rating = emailer.practice_reviews(p["id"])
    picked = {x for b in k["blocks"] if b["type"] == "reviews" for x in b.get("ids") or []}
    allrev = emailer.practice_reviews(p["id"], 200)[0]
    revs += [r_ for r_ in allrev if r_["id"] in picked and r_ not in revs]
    data = {"blocks": k["blocks"], "rev": k.get("rev") or 0, "types": emailer.BLOCK_TYPES, "fields": emailer.BLOCK_FIELDS,
            "opts": emailer.OPTS, "social": emailer.SOCIAL, "socialDefaults": emailer.social_defaults(p["id"]),
            "media": _email_media(p["id"]), "practice": p["name"], "site": emailer.practice_domain(p["id"]) or "",
            "reviews": [{"id": r_["id"], "who": r_["who"], "stars": r_["stars"], "text": r_["text"], "date": (r_["created"] or "")[:10]}
                        for r_ in revs],
            "rating": rating, "colors": {"primary": lk["primary"], "accent": lk["accent"]},
            "checks": emailer.checks(k, p), "myEmail": (me(request) or {}).get("email") or ""}
    return page(request, "email_edit.html", k=k, practice=p, s=emailer.sender(p["id"]), lists=emailer.lists(p["id"]),
                color=lk["primary"], accent=lk["accent"], data=data, short=emailer.short_name(p))


@app.post("/studio/email/{cid}")
async def email_save(request: Request, cid: int):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    fetch_ = request.headers.get("x-requested-with") == "fetch"
    if not k:
        return JSONResponse({"error": "Not found."}, status_code=404)
    form = await request.form()
    try:
        rev = emailer.update_campaign(cid, form, (me(request) or {}).get("name") or actor(request))
    except emailer.EmailError as e:
        if fetch_:
            return JSONResponse({"error": str(e), "conflict": isinstance(e, emailer.Conflict)},
                                status_code=409 if isinstance(e, emailer.Conflict) else 422)
        flash(request, str(e), "error")
        return RedirectResponse(f"/studio/email/{cid}", status_code=303)
    if fetch_:
        return {"ok": True, "saved": db.now(), "rev": rev, "checks": emailer.checks(emailer.campaign(cid), p)}
    nxt = form.get("next") or ""
    return RedirectResponse(f"/studio/email/{cid}/review" if nxt == "review" else f"/studio/email/{cid}", status_code=303)


_PREVIEW_HEADERS = {"Content-Security-Policy": "script-src 'none'", "Cache-Control": "no-store"}


@app.get("/studio/email/{cid}/preview", response_class=HTMLResponse)
def email_preview(request: Request, cid: int, edit: int = 0):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if not k:
        return HTMLResponse("", status_code=404)
    return HTMLResponse(emailer.render(k, p, person={"first_name": "Alex"}, editor=bool(edit) and k["status"] == "draft"),
                        headers=_PREVIEW_HEADERS)


@app.post("/studio/email/{cid}/draw")
async def email_draw(request: Request, cid: int):
    """The editor's live canvas: renders the unsaved blocks (nothing is saved or sent)."""
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if not k:
        return HTMLResponse("", status_code=404)
    d = emailer.draft_from(k, await request.form())
    return HTMLResponse(emailer.render(d, p, person={"first_name": "Alex"}, editor=True), headers=_PREVIEW_HEADERS)


@app.get("/studio/email/{cid}/review", response_class=HTMLResponse)
def email_review(request: Request, cid: int):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if not k:
        return RedirectResponse("/studio/email", status_code=303)
    if k["status"] != "draft":
        return RedirectResponse(f"/studio/email/{cid}", status_code=303)
    people, skipped = emailer.recipients(k)
    lists_ = [l for l in emailer.lists(p["id"]) if l["id"] in k["list_ids"]]
    return page(request, "email_review.html", k=k, practice=p, s=emailer.sender(p["id"]), checks=emailer.checks(k, p),
                n=len(people), skipped=skipped, lists=lists_, short=emailer.short_name(p),
                color=emailer.look(p["id"])["primary"], cancel_minutes=emailer.CANCEL_MINUTES,
                my_email=(me(request) or {}).get("email") or "")


@app.post("/studio/email/{cid}/test")
def email_test(request: Request, cid: int, to: str = Form("")):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if not k:
        return RedirectResponse("/studio/email", status_code=303)
    try:
        emailer.test_send(k, p, to.strip(), _email_base(request))
        msg, ok = f"Test sent to {to.strip()}. Check it arrived in the inbox, not spam.", True
    except emailer.EmailError as e:
        msg, ok = str(e), False
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 422)
    flash(request, msg, "ok" if ok else "error")
    return RedirectResponse(f"/studio/email/{cid}/review", status_code=303)


@app.post("/studio/email/{cid}/send")
def email_send(request: Request, cid: int, typed: str = Form(""), when: str = Form("")):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if not k:
        return RedirectResponse("/studio/email", status_code=303)
    at = None
    if when.strip():
        try:
            at = datetime.fromisoformat(when.strip())
        except ValueError:
            flash(request, "That send time couldn't be read.", "error")
            return RedirectResponse(f"/studio/email/{cid}/review", status_code=303)
    try:
        t = emailer.queue(cid, p, typed, actor(request), _email_base(request), at)
        flash(request, f"Sending as {p['name']} starts {t.strftime('%b %-d at %-I:%M %p')}. You can cancel until then.")
    except emailer.EmailError as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/studio/email/{cid}/review", status_code=303)
    return RedirectResponse(f"/studio/email/{cid}", status_code=303)


@app.post("/studio/email/{cid}/cancel")
def email_cancel(request: Request, cid: int):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if k:
        try:
            sent = emailer.cancel(cid)
            flash(request, "Cancelled. " + (f"{sent:,} had already gone out." if sent else "Nothing was sent; it's a draft again."))
        except emailer.EmailError as e:
            flash(request, str(e), "error")
    return RedirectResponse(f"/studio/email/{cid}", status_code=303)


@app.post("/studio/email/{cid}/copy")
def email_copy(request: Request, cid: int):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if not k:
        return RedirectResponse("/studio/email", status_code=303)
    return RedirectResponse(f"/studio/email/{emailer.copy_campaign(cid, actor(request))}", status_code=303)


@app.post("/studio/email/{cid}/delete")
def email_delete(request: Request, cid: int):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if k:
        emailer.delete_campaign(cid)
        flash(request, "Draft deleted.")
    return RedirectResponse("/studio/email", status_code=303)


@app.post("/studio/email/{cid}/template")
def email_template(request: Request, cid: int, name: str = Form("")):
    if (r := _email_gate(request)):
        return r
    k, p = _email_campaign(request, cid)
    if k:
        emailer.save_template(p["id"], name, k["blocks"], actor(request))
        flash(request, f"Saved as a {p['name']} template.")
    return RedirectResponse(f"/studio/email/{cid}", status_code=303)


# lists
@app.get("/studio/email/lists", response_class=HTMLResponse)
def email_lists(request: Request, b: str = ""):
    if (r := _email_gate(request)):
        return r
    plist = _email_practices(request)
    ids = {p["id"] for p in plist}
    pick = int(b) if b.isdigit() and int(b) in ids else (plist[0]["id"] if plist else None)
    return page(request, "email_lists.html", plist=plist, pick=pick, practice=db.practice(pick) if pick else None,
                lists=emailer.lists(pick) if pick else [], field_n=len(emailer.field_people(pick)) if pick else 0)


@app.post("/studio/email/lists/new")
def email_list_new(request: Request, practice_id: int = Form(...), name: str = Form("")):
    if (r := _email_gate(request)):
        return r
    if practice_id not in {p["id"] for p in _email_practices(request)}:
        return RedirectResponse("/studio/email/lists", status_code=303)
    try:
        lid = emailer.new_list(practice_id, name, actor(request))
    except emailer.EmailError as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/studio/email/lists?b={practice_id}", status_code=303)
    return RedirectResponse(f"/studio/email/lists/{lid}", status_code=303)


def _email_list(request, lid):
    l = emailer.get_list(lid)
    if not l or not users.sees_practice(me(request), l["practice_id"]):
        return None
    return l


@app.get("/studio/email/lists/{lid}", response_class=HTMLResponse)
def email_list_page(request: Request, lid: int, q: str = ""):
    if (r := _email_gate(request)):
        return r
    l = _email_list(request, lid)
    if not l:
        return RedirectResponse("/studio/email/lists", status_code=303)
    rows = emailer.contacts(lid, q)
    return page(request, "email_list_one.html", l=l, practice=db.practice(l["practice_id"]), rows=rows[:500], total=len(rows),
                q=q, field_n=len(emailer.field_people(l["practice_id"])))


@app.post("/studio/email/lists/{lid}/import")
async def email_list_import(request: Request, lid: int):
    if (r := _email_gate(request)):
        return r
    l = _email_list(request, lid)
    if not l:
        return RedirectResponse("/studio/email/lists", status_code=303)
    form = await request.form()
    try:
        if form.get("source") == "field":
            people, src = emailer.field_people(l["practice_id"]), "field visits"
        elif form.get("source") == "one":
            people, src = [{"email": form.get("email"), "first_name": form.get("first_name"), "last_name": form.get("last_name"),
                            "org": form.get("org")}], "typed in"
        else:
            up = form.get("file")
            if up is None or not getattr(up, "filename", ""):
                raise emailer.EmailError("Pick a CSV file (save the spreadsheet as CSV first).")
            data = await up.read()
            if len(data) > 10 * 1024 * 1024:
                raise emailer.EmailError("That file is over 10 MB.")
            people, src = emailer.parse_csv(data), "csv: " + os.path.basename(up.filename)[:80]
        added, skipped = emailer.add_contacts(lid, people, src)
        flash(request, f"Added {added:,}." + (f" Skipped {skipped:,} (already on the list or not an email address)." if skipped else ""))
    except emailer.EmailError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/studio/email/lists/{lid}", status_code=303)


@app.post("/studio/email/lists/{lid}/remove")
def email_list_remove(request: Request, lid: int, contact_id: int = Form(...)):
    if (r := _email_gate(request)):
        return r
    if _email_list(request, lid):
        emailer.remove_contact(lid, contact_id)
    return RedirectResponse(f"/studio/email/lists/{lid}", status_code=303)


@app.post("/studio/email/lists/{lid}/delete")
def email_list_delete(request: Request, lid: int):
    if (r := _email_gate(request)):
        return r
    l = _email_list(request, lid)
    if l:
        emailer.delete_list(lid)
        flash(request, f"Deleted the list “{l['name']}”.")
        return RedirectResponse(f"/studio/email/lists?b={l['practice_id']}", status_code=303)
    return RedirectResponse("/studio/email/lists", status_code=303)


# admin: the connection and each practice's sending address
@app.get("/connections/email", response_class=HTMLResponse)
def email_connections(request: Request):
    if (r := guard(request)):
        return r
    ss = emailer.senders()
    key = db.get_setting("em_events_key")
    if not key:
        key = secrets.token_urlsafe(18)
        db.set_setting("em_events_key", key)
    rows = [{"p": p, "s": ss.get(p["id"]), "sites": sorted(emailer.practice_domains(p["id"]))} for p in db.practices()]
    for r_ in rows:
        r_["site"] = emailer.practice_domain(r_["p"]["id"]) or (r_["sites"][0] if r_["sites"] else "")
    rows.sort(key=lambda x: (x["s"] is None, x["p"]["name"].lower()))
    return page(request, "email_senders.html", rows=rows, configured=emailer.configured(), rates=emailer.rates(),
                events_url=f"{_email_base(request)}/e/events?k={key}", my_email=(me(request) or {}).get("email") or "")


@app.post("/connections/email/connection")
def email_connection(request: Request, connection: str = Form(""), per_minute: str = Form(""), per_hour: str = Form("")):
    try:
        if connection.strip():
            emailer.save_connection(connection)
        if per_minute.strip() and per_hour.strip():
            emailer.set_rates(int(per_minute), int(per_hour))
        flash(request, "Saved.")
    except (emailer.EmailError, ValueError) as e:
        flash(request, str(e) if isinstance(e, emailer.EmailError) else "Rates must be whole numbers.", "error")
    return RedirectResponse("/connections/email", status_code=303)


@app.post("/connections/email/sender/{pid}")
async def email_sender_save(request: Request, pid: int):
    form = await request.form()
    try:
        if not db.practice(pid):
            raise emailer.EmailError("That practice is gone.")
        emailer.save_sender(pid, form, actor(request))
        flash(request, "Saved. Next: Check DNS, then send a test.")
    except emailer.EmailError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/connections/email#p{pid}", status_code=303)


@app.post("/connections/email/sender/{pid}/check")
def email_sender_check(request: Request, pid: int):
    try:
        d = emailer.check_dns(pid)
        missing = [x.upper() for x in ("spf", "dkim", "dmarc") if not d.get(x)]
        flash(request, "SPF, DKIM and DMARC all found." if not missing else "Still missing: " + ", ".join(missing) + ".",
              "ok" if not missing else "error")
    except emailer.EmailError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/connections/email#p{pid}", status_code=303)


@app.post("/connections/email/sender/{pid}/test")
def email_sender_test(request: Request, pid: int, to: str = Form("")):
    try:
        ready = emailer.sender_test(pid, to.strip(), db.practice(pid)["name"])
        flash(request, f"Test sent to {to.strip()}." + (" This practice is ready to send." if ready else " Fix the DNS checks to finish."))
    except emailer.EmailError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/connections/email#p{pid}", status_code=303)


@app.post("/connections/email/sender/{pid}/remove")
def email_sender_remove(request: Request, pid: int):
    emailer.remove_sender(pid)
    flash(request, "Sending address removed. That practice can't send until one is added again.")
    return RedirectResponse("/connections/email", status_code=303)


# public: tracking, images, unsubscribe, Azure delivery reports (no sign-in)
_GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


@app.get("/e/o/{token}.gif")
def email_open(token: str):
    emailer.opened(token)
    return Response(_GIF, media_type="image/gif", headers={"Cache-Control": "no-store"})


@app.get("/e/c/{token}/{n}")
def email_click(token: str, n: int):
    url = emailer.clicked(token, n)
    return RedirectResponse(url or "/", status_code=302)


@app.get("/e/i/{key}/{iid}")
async def email_image(key: str, iid: int):
    with db.conn() as c:
        k = c.execute("SELECT id FROM em_campaigns WHERE key=?", (key,)).fetchone()
    k = emailer.campaign(k["id"]) if k else None
    it = studio.item(iid)
    if not k or not it or it.get("practice_id") != k["practice_id"] or iid not in [b.get("item") for b in k["blocks"]]:
        return Response(status_code=404)
    return await _send_studio_file(it, 0)


@app.get("/e/logo/{pid}")
def email_logo(pid: int):
    from fastapi.responses import FileResponse
    a = images.logo(pid) if emailer.sender(pid) else None
    p_ = images.path(a) if a else None
    if not p_:
        return Response(status_code=404)
    return FileResponse(p_, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/e/u/{token}", response_class=HTMLResponse)
def email_unsub_page(request: Request, token: str):
    m = emailer.by_token(token)
    p = db.practice(m["practice_id"]) if m else None
    return templates.TemplateResponse(request, "email_unsub.html", {"m": m, "practice": p, "done": False, "static_v": STATIC_V},
                                      headers={"X-Robots-Tag": "noindex"})


@app.post("/e/u/{token}", response_class=HTMLResponse)
def email_unsub(request: Request, token: str):
    m = emailer.by_token(token)
    p = db.practice(m["practice_id"]) if m else None
    if m:
        emailer.unsubscribe(m["practice_id"], m["email"])
    return templates.TemplateResponse(request, "email_unsub.html", {"m": m, "practice": p, "done": bool(m), "static_v": STATIC_V},
                                      headers={"X-Robots-Tag": "noindex"})


@app.post("/e/events")
async def email_events(request: Request, k: str = ""):
    key = db.get_setting("em_events_key") or ""
    if not key or not hmac.compare_digest(k, key):
        return Response(status_code=403)
    try:
        events = await request.json()
    except Exception:
        return Response(status_code=400)
    for ev in events if isinstance(events, list) else [events]:
        t = ev.get("eventType") or ev.get("type") or ""
        data = ev.get("data") or {}
        if t == "Microsoft.EventGrid.SubscriptionValidationEvent":
            return {"validationResponse": data.get("validationCode")}
        if t == "Microsoft.Communication.EmailDeliveryReportReceived":
            emailer.delivery_report(data)
    return {"ok": True}


# ------------------------------------------------------------------ Practice 360 (p360.py)
def _p360_practices(request):
    """Practices in MCDP Reports (matched to a location) that this person can open."""
    u = me(request)
    keep = p360.drapp_practice_ids()
    return [p for p in db.practices() if p["id"] in keep and users.sees_practice(u, p["id"])]


def _p360_practice(request, pid):
    p = db.practice(pid)
    return p if p and pid in p360.drapp_practice_ids() and users.sees_practice(me(request), pid) else None


async def _p360_shots(form):
    out = []
    for f in form.getlist("shot"):
        if getattr(f, "filename", None) is None or not hasattr(f, "read"):
            continue
        data = await f.read(p360.MAX_SHOT + 1)
        if data:
            out.append((data, (f.content_type or "").split(";")[0], ""))
    return out[:6]


@app.get("/p360/settings", response_class=HTMLResponse)
def p360_settings(request: Request):
    u = me(request)
    if not u or u["role"] != "admin":
        flash(request, "Only admins set department owners.", "error")
        return RedirectResponse("/p360", status_code=303)
    people = [x for x in users.all_users() if x.get("active", 1)]
    return page(request, "p360_settings.html", depts=p360.DEPTS, owners=p360.owners(), people=people)


@app.post("/p360/settings")
async def p360_settings_save(request: Request):
    u = me(request)
    if not u or u["role"] != "admin":
        return RedirectResponse("/p360", status_code=303)
    form = await request.form()
    for k, label in p360.DEPTS:
        p360.set_owners(k, [int(x) for x in form.getlist("owner_" + k) if str(x).isdigit()])
    flash(request, "Saved the department owners.")
    return RedirectResponse("/p360/settings", status_code=303)


# ---- Practice 360 HR numbers: typed in or uploaded each month (counts only) until a payroll (ADP) feed exists
def _hr_period(v):
    from datetime import date as _d
    if re.match(r"^\d{4}-\d{2}$", v or ""):
        return v
    t = _d.today().replace(day=1) - timedelta(days=1)          # last complete month
    return t.strftime("%Y-%m")


@app.get("/p360/hr", response_class=HTMLResponse)
def p360_hr(request: Request, m: str = ""):
    u = me(request)
    if not p360.can_write(u, "hr"):
        flash(request, "Entering HR numbers needs HR access on the Team page. Ask an admin.", "error")
        return RedirectResponse("/p360", status_code=303)
    period = _hr_period(m)
    plist = _p360_practices(request)
    vals = p360.hr_month([p["id"] for p in plist], period)
    y, mo = int(period[:4]), int(period[5:])
    prev = f"{y - (mo == 1):04d}-{(mo - 2) % 12 + 1:02d}"
    nxt = f"{y + (mo == 12):04d}-{mo % 12 + 1:02d}"
    label = datetime.strptime(period + "-01", "%Y-%m-%d").strftime("%B %Y")
    return page(request, "p360_hr.html", plist=plist, vals=vals, period=period, label=label, prev=prev, nxt=nxt,
                inputs=p360.HR_INPUTS, decimal=p360.DECIMAL)


@app.post("/p360/hr")
async def p360_hr_save(request: Request):
    u = me(request)
    if not p360.can_write(u, "hr"):
        return RedirectResponse("/p360", status_code=303)
    form = await request.form()
    period = str(form.get("period") or "")
    allowed = {p["id"] for p in _p360_practices(request)}
    values = {}
    for k in form.keys():
        mm = re.match(r"^v_(\d+)_(\d+)$", k)
        if mm and int(mm.group(1)) in allowed and int(mm.group(2)) < len(p360.HR_INPUTS):
            values.setdefault(int(mm.group(1)), {})[p360.HR_INPUTS[int(mm.group(2))][0]] = form.get(k)
    try:
        n = p360.save_hr(period, values, actor(request))
        flash(request, f"Saved {n:,} HR number{'' if n == 1 else 's'} for {period}.")
    except p360.P360Error as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/p360/hr?m={period}", status_code=303)


@app.post("/p360/hr/upload")
async def p360_hr_upload(request: Request):
    u = me(request)
    if not p360.can_write(u, "hr"):
        return RedirectResponse("/p360", status_code=303)
    form = await request.form()
    up = form.get("file")
    if up is None or not getattr(up, "filename", ""):
        flash(request, "Pick the CSV to upload.", "error")
        return RedirectResponse("/p360/hr", status_code=303)
    data = await up.read()
    if len(data) > 5 * 1024 * 1024:
        flash(request, "That file is over 5 MB.", "error")
        return RedirectResponse("/p360/hr", status_code=303)
    plist = _p360_practices(request)
    allowed = {p["id"] for p in plist}
    try:
        got, missing = p360.hr_from_csv(data, plist)
        n = sum(p360.save_hr(per, {pid: v for pid, v in vals.items() if pid in allowed}, actor(request)) for per, vals in got.items())
        msg = f"Imported {n:,} HR numbers for {', '.join(sorted(got))}. Only the counts were kept."
        if missing:
            msg += f" Not matched to a practice: {', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}."
        flash(request, msg)
        return RedirectResponse(f"/p360/hr?m={max(got)}", status_code=303)
    except p360.P360Error as e:
        flash(request, str(e), "error")
        return RedirectResponse("/p360/hr", status_code=303)


@app.get("/p360", response_class=HTMLResponse)
def p360_home(request: Request, pod: int = 0):
    u = me(request)
    plist = _p360_practices(request)
    keys = [k for k, _ in p360.depts_for(u)]
    pods = p360.pods()
    seen = {p["id"] for p in plist}
    pods = [dict(x, n=len([i for i in x["practice_ids"] if i in seen])) for x in pods]
    pods = [x for x in pods if x["n"]]
    cur = next((x for x in pods if x["id"] == pod), None)
    if cur:
        plist = [p for p in plist if p["id"] in cur["practice_ids"]]
    pod_of = {}
    for x in pods:
        for i in x["practice_ids"]:
            pod_of.setdefault(i, []).append(x["name"])
    rows = [{"p": p, "open": p360.open_count(p["id"], keys), "pods": pod_of.get(p["id"], [])} for p in plist]
    return page(request, "p360_pick.html", rows=rows, depts=p360.depts_for(u), pods=pods, cur=cur)


@app.post("/p360/hub-refresh")
def p360_hub_refresh(request: Request):
    """Re-reads the Hub's own marketing numbers (ads, LeadSigma, reviews, field visits) for Practice 360 now."""
    u = me(request)
    if not u or u["role"] not in ("admin", "editor"):
        return RedirectResponse("/p360", status_code=303)
    n = p360.hub_marketing()
    flash(request, f"Updated the Hub's marketing numbers ({n:,} figures).")
    return RedirectResponse(request.headers.get("referer") or "/p360", status_code=303)


@app.get("/p360/pods", response_class=HTMLResponse)
def p360_pods(request: Request):
    u = me(request)
    if not u or u["role"] != "admin":
        flash(request, "Only admins match pods to practices.", "error")
        return RedirectResponse("/p360", status_code=303)
    return page(request, "p360_pods.html", pods=p360.pods(), plist=db.practices(include_hidden=True))


@app.post("/p360/pods")
async def p360_pods_save(request: Request):
    u = me(request)
    if not u or u["role"] != "admin":
        return RedirectResponse("/p360", status_code=303)
    form = await request.form()
    for k, v in form.items():
        if k.startswith("loc_") and k[4:].isdigit():
            p360.set_location_practice(int(k[4:]), int(v) if str(v).isdigit() else None)
    p360.recompute(u["name"])
    flash(request, "Saved which practice each MCDP Reports location belongs to; the numbers follow the new matches.")
    return RedirectResponse("/p360/pods", status_code=303)


@app.get("/p360/pod/{pod_id}", response_class=HTMLResponse)
def p360_pod(request: Request, pod_id: int, tf: str = "t3m"):
    u = me(request)
    pd, lanes = p360.pod_lanes(pod_id, u, users.practice_ids(u))
    if not pd:
        return RedirectResponse("/p360", status_code=303)
    pnames = {p["id"]: p for p in _p360_practices(request)}
    members = [pnames[i] for i in pd["practice_ids"] if i in pnames]
    if not members:
        return RedirectResponse("/p360", status_code=303)
    return page(request, "p360_pod.html", pd=pd, lanes=lanes, members=members, v=p360.view(tf, pod_id=pod_id),
                timeframes=p360.TIMEFRAMES,
                pods=[x for x in p360.pods() if any(i in pnames for i in x["practice_ids"])])


@app.post("/p360/{pid}/ai")
def p360_ai(request: Request, pid: int):
    p = _p360_practice(request, pid)
    u = me(request)
    if not p or u["role"] not in ("admin", "editor"):
        return RedirectResponse("/p360", status_code=303)
    try:
        p360.ai_summary(pid, u["name"])
        flash(request, "The AI summary is up to date.")
    except (p360.P360Error, ai.AIError) as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/p360/{pid}#ai", status_code=303)


@app.post("/p360/{pid}/ai/feedback")
async def p360_ai_feedback(request: Request, pid: int):
    p = _p360_practice(request, pid)
    if not p:
        return RedirectResponse("/p360", status_code=303)
    form = await request.form()
    p360.ai_feedback(pid, form.get("point"), form.get("reason"), me(request)["name"])
    flash(request, "Thanks. That goes to the department's owner, and the next summary will be written fresh.")
    return RedirectResponse(f"/p360/{pid}#ai", status_code=303)


@app.get("/p360/{pid}", response_class=HTMLResponse)
def p360_practice(request: Request, pid: int, tf: str = "t3m"):
    p = _p360_practice(request, pid)
    if not p:
        return RedirectResponse("/p360", status_code=303)
    u = me(request)
    keys = [k for k, _ in p360.depts_for(u)]
    if os.environ.get("ADS_DISABLE_SCHEDULER") != "1":
        p360.hub_refresh_if_stale()
    return page(request, "p360_practice.html", p=p, lanes=p360.lanes(pid, u), open_n=p360.open_count(pid, keys),
                plist=_p360_practices(request), p_pods=p360.pods_of_practice(pid), v=p360.view(tf, pid=pid),
                timeframes=p360.TIMEFRAMES, aisum=p360.ai_saved(pid), ai_on=ai.available(), network=_p360_network(pid, u))


def _p360_network(pid, u):
    """Practice 360's Referral network section (for people who can open Field)."""
    if not users.can(u, "field"):
        return None
    try:
        return accounts.p360_summary(pid)
    except Exception:
        return None


@app.get("/p360/{pid}/ledger", response_class=HTMLResponse)
def p360_ledger(request: Request, pid: int, kind: str = "", only: str = "", dept: str = "", e: int = 0, new: str = "", d: str = ""):
    p = _p360_practice(request, pid)
    if not p:
        return RedirectResponse("/p360", status_code=303)
    u = me(request)
    depts = p360.depts_for(u)
    keys = [k for k, _ in depts]
    rows = p360.entries(pid, keys, kind, only, u["id"], dept if dept in keys else "")
    sel = next((r for r in rows if r["id"] == e), None) or (rows[0] if rows and not new else None)
    if e and not sel:
        x = p360.entry(e)
        sel = x if x and x["practice_id"] == pid and x["dept"] in keys else None
    names = {x["id"]: x["name"] for x in users.all_users()}
    detail = None
    if sel:
        detail = {"e": sel, "events": p360.events(sel["id"]), "pin": p360.first_pin(sel["id"]),
                  "now": p360.pin_for(pid, (p360.first_pin(sel["id"]) or {}).get("metric", "")),
                  "owner": names.get(sel.get("owner_id"), ""), "can_manage": p360.can_manage(u, sel),
                  "can_follow": p360.can_write(u, sel["dept"]) or u["id"] == sel.get("owner_id")}
    people = [x for x in users.all_users() if x.get("active", 1)]
    writable = [(k, l) for k, l in depts if p360.can_write(u, k)]
    return page(request, "p360_ledger.html", p=p, rows=rows, sel=sel, d=detail, kind=kind, only=only, dept=dept, depts=depts,
                writable=writable, metrics=p360.METRICS, people=people, names=names, new=bool(new) or not rows,
                open_n=p360.open_count(pid, keys), statuses=[(s_, p360.STATUS_LABEL[s_]) for s_ in p360.STATUSES],
                owners=p360.owners(), plist=_p360_practices(request), pick_dept=d, p_pods=p360.pods_of_practice(pid))


@app.post("/p360/{pid}/ledger")
async def p360_add(request: Request, pid: int):
    p = _p360_practice(request, pid)
    if not p:
        return RedirectResponse("/p360", status_code=303)
    form = await request.form()
    u = me(request)
    try:
        owner = int(form.get("owner_id")) if str(form.get("owner_id") or "").isdigit() else None
        eid = p360.add_entry(pid, form.get("dept") or "", form.get("kind") or "note", form.get("title"), form.get("text"), u,
                             metric=form.get("metric") or "", owner_id=owner, due=form.get("due") or "",
                             review=bool(form.get("review")), shots=await _p360_shots(form))
    except (p360.P360Error, ValueError) as ex:
        flash(request, str(ex), "error")
        return RedirectResponse(f"/p360/{pid}/ledger?new=1", status_code=303)
    flash(request, "Added to the ledger.")
    return RedirectResponse(f"/p360/{pid}/ledger?e={eid}", status_code=303)


def _p360_entry_for(request, eid):
    e = p360.entry(eid)
    u = me(request)
    if not e or not users.sees_practice(u, e["practice_id"]) or not p360.sees_dept(u, e["dept"]):
        return None
    return e


@app.post("/p360/entry/{eid}/follow")
async def p360_follow(request: Request, eid: int):
    e = _p360_entry_for(request, eid)
    if not e:
        return RedirectResponse("/p360", status_code=303)
    form = await request.form()
    try:
        p360.follow_up(eid, form.get("text"), me(request), form.get("status") or "", bool(form.get("pin_now")),
                       shots=await _p360_shots(form))
        flash(request, "Follow-up added.")
    except p360.P360Error as ex:
        flash(request, str(ex), "error")
    return RedirectResponse(f"/p360/{e['practice_id']}/ledger?e={eid}#history", status_code=303)


@app.post("/p360/entry/{eid}/plan")
async def p360_plan(request: Request, eid: int):
    e = _p360_entry_for(request, eid)
    if not e:
        return RedirectResponse("/p360", status_code=303)
    form = await request.form()
    try:
        owner = int(form.get("owner_id")) if str(form.get("owner_id") or "").isdigit() else 0
        p360.set_plan(eid, me(request), owner_id=owner, due=(form.get("due") or "").strip())
        flash(request, "Saved.")
    except (p360.P360Error, ValueError) as ex:
        flash(request, str(ex) if isinstance(ex, p360.P360Error) else "The due date isn't a date.", "error")
    return RedirectResponse(f"/p360/{e['practice_id']}/ledger?e={eid}", status_code=303)


@app.get("/p360/shot/{sid}")
def p360_shot(request: Request, sid: int):
    from fastapi.responses import FileResponse
    with db.conn() as c:
        s = c.execute("SELECT * FROM p360_shots WHERE id=?", (sid,)).fetchone()
    if not s or not _p360_entry_for(request, s["entry_id"]):
        return Response(status_code=404)
    path = os.path.join(p360.shot_dir(), os.path.basename(s["file"]))
    if not os.path.exists(path):
        return Response(status_code=404)
    return FileResponse(path, media_type=s["mime"], headers={"Cache-Control": "private, max-age=3600"})



# ---- MCDP Reports import: the weekly run gets a one-use key here (signed in as an admin), then the collector
# running on the MCDP Reports page sends the numbers to /p360-in/import with it.
DRAPP_ORIGIN = "https://drapp.mcdentalpartners.com"


@app.post("/p360/import-key")
def p360_import_key(request: Request):
    u = me(request)
    if not u or u["role"] != "admin":
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"key": p360.import_key(), "minutes": p360.IMPORT_MINUTES}


@app.get("/p360-in/collect.js")
def p360_collect_js():
    path = os.path.join(os.path.dirname(__file__), "static", "p360-collect.js")
    with open(path, encoding="utf-8") as f:
        return Response(f.read(), media_type="application/javascript",
                        headers={"Access-Control-Allow-Origin": DRAPP_ORIGIN, "Cache-Control": "no-store"})


@app.options("/p360-in/import")
def p360_import_options():
    return Response(status_code=204, headers={"Access-Control-Allow-Origin": DRAPP_ORIGIN, "Access-Control-Allow-Methods": "POST",
                                              "Access-Control-Allow-Headers": "Content-Type"})


@app.post("/p360-in/import")
async def p360_import(request: Request, k: str = ""):
    h = {"Access-Control-Allow-Origin": DRAPP_ORIGIN}
    if not p360.take_key(k):
        return JSONResponse({"error": "The import key is missing, used or expired."}, status_code=403, headers=h)
    body = await request.body()
    if len(body) > 8 * 1024 * 1024:
        return JSONResponse({"error": "Too big."}, status_code=413, headers=h)
    try:
        res = p360.import_reports(json.loads(body))
    except (ValueError, KeyError, TypeError, p360.P360Error) as e:
        return JSONResponse({"error": f"Couldn't read it: {e}"}, status_code=422, headers=h)
    return JSONResponse({"ok": True, **res}, headers=h)
