"""MCDP Ads: upload Google / Meta exports per practice, get exact findings + AI write-ups."""
from __future__ import annotations

import asyncio
import hmac
import html
import os
import re
import sqlite3
import urllib.parse
from datetime import date
from pathlib import Path

import markdown as md
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import actions, ai, db, emailfmt, field, organic, places, referrals, reviews, salesforce, scope, sync, users
from . import connectors as cx
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
PUBLIC = ("/login", "/logout", "/health", "/static/", "/setup/")
ANY_SIGNED_IN_POST = ("/account", "/field/route-order")
FIELD_WORK = re.compile(r"^/field/(log|visits|receipt)(/|$)(?!.*/delete$)")   # logging visits: field visits switched on
FIELD_DELETE = re.compile(r"^/field/visits/\d+/delete$")          # own visit (field on) or any visit (editors); checked in the route
FIELD_SETTINGS = re.compile(r"^/field/settings(/|$)")             # budgets and practice links: editors
ADMIN_ONLY = (re.compile(r"^/(connections|google|salesforce|team)(/|$)"), re.compile(r"^/map/setup$"),
              re.compile(r"^/p/\d+/delete$"))


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
    if method in ("GET", "HEAD") or path.startswith(ANY_SIGNED_IN_POST):
        return "viewer"
    return "editor"


async def role_gate(request: Request, call_next):
    need = needed_role(request.method, request.url.path)
    request.state.user = current_user(request)
    u = request.state.user
    refused = u and (not u.get("field") if need == "field" else users.rank(u["role"]) < users.rank(need))
    if need and refused:
        if need == "field":
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
            back = "/"
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


PUBLIC_URL = os.environ.get("ADS_PUBLIC_URL", "").rstrip("/")


@app.on_event("startup")
def _startup():
    db.init()
    users.init()
    places.init()
    places.purge_old()
    referrals.init()
    field.init()
    salesforce.init()
    reviews.init()
    if os.environ.get("ADS_DISABLE_SCHEDULER") != "1":
        sync.start_scheduler()
        reviews.start_scheduler()
        places.start_scheduler()
        referrals.start_scheduler()
        salesforce.start_scheduler(PUBLIC_URL)


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


def page(request, name, **ctx):
    ctx["flash"] = request.session.pop("flash", None)
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
    ctx.setdefault("inbox_open", reviews.open_count() if u else 0)
    return templates.TemplateResponse(request, name, ctx)


# ---------- analysis view-model ----------
def platform_view(practice, key, label, all_practices=None):
    budget = practice.get(f"{key}_budget") or 0
    up = db.latest_upload(practice["id"], key, "campaign") or db.latest_upload(practice["id"], key, "ad")
    view = {"key": key, "label": label, "upload": up, "budget": budget, "campaigns": [], "findings": [],
            "totals": None, "pacing": {"available": False}, "search_terms": None, "search_upload": None,
            "filtered": scope.has_filter(practice), "camp_total": 0, "breakdown": None, "all_names": []}
    if up:
        all_rows = db.upload_rows(up["id"])
        rows = scope.apply(all_rows, practice)
        view["camp_total"] = len({r["campaign"] for r in all_rows})
        view["all_names"] = sorted({r["campaign"] for r in all_rows if r.get("campaign")}, key=str.lower)
        if rows:
            camps, tot, findings = analyze_campaigns(rows)
            view.update(campaigns=camps, totals=tot, findings=findings, pacing=pacing(rows, budget))
        if not view["filtered"] and all_practices is not None:
            view["breakdown"] = scope.breakdown(practice, key, all_rows, all_practices)
    st_up = db.latest_upload(practice["id"], key, "search_term")
    if st_up:
        st_rows = scope.apply(db.upload_rows(st_up["id"]), practice)
        if st_rows:
            view["search_upload"] = st_up
            view["search_terms"] = analyze_search_terms(st_rows)
            if not up:  # search terms alone still give a spend picture
                camps, tot, _ = analyze_campaigns(st_rows)
                view.update(campaigns=camps, totals=tot)
    return view


def practice_views(p, with_breakdown=False):
    allp = db.practices() if with_breakdown else None
    return [platform_view(p, k, l, allp) for k, l in PLATFORMS]


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
    return not scope.has_filter(p) and any(scope.has_filter(q) for q in practices if q["id"] != p["id"])


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
                min_len=users.MIN_PASSWORD)


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
                    min_len=users.MIN_PASSWORD)
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
    return page(request, "team.html", people=users.all_users(), roles=users.ROLES, labels=users.ROLE_LABELS,
                help=users.ROLE_HELP, link=link, link_days=users.LINK_DAYS, shared=me(request)["id"] == 0,
                now=db.now())


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
def welcome(request: Request):
    if (r := guard(request)):
        return r
    allp = db.practices()
    mapped = sum(c.get("referrer", 0) for c in places.counts().values())
    return page(request, "welcome.html", ads=_ad_totals(allp), mapped=mapped, look=_worth_a_look(allp, me(request)["role"]),
                social_url=SOCIAL_URL, practice_count=len(allp))


# ---------- practices ----------
@app.get("/practices", response_class=HTMLResponse)
def dashboard(request: Request):
    if (r := guard(request)):
        return r
    items = []
    allp = db.practices()
    for p in allp:
        views = practice_views(p)
        waste = sum(f["amount"] for v in views for f in v["findings"] if f["kind"] in ("zero-conv-campaign", "high-cpa"))
        waste += sum((v["search_terms"] or {}).get("wasted_total", 0) for v in views)
        items.append({"p": p, "views": views, "waste": round(waste, 2), "master": is_master(p, allp),
                      "issues": sum(1 for v in views for f in v["findings"] if f["severity"] in ("high", "medium"))})
    items.sort(key=lambda it: (not it["master"], it["p"]["name"].lower()))
    return page(request, "dashboard.html", items=items)


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
    return page(request, "practice.html", p=p, views=views, uploads=db.uploads(pid),
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
                added=scope.added_campaigns(p),
                fixes_open=db.changes(pid, ["proposed", "failed", "stale"]),
                fixes_done=db.changes(pid, ["applied", "undone"], limit=40),
                kind_labels=actions.KIND_LABELS)


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
    try:
        text = await asyncio.to_thread(ai.generate, kind, ai.build_context(p, views, org))
    except ai.AIError as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/p/{pid}#ai-{kind}", status_code=303)
    db.save_ai(pid, kind, text)
    if kind == "report":
        return RedirectResponse(f"/p/{pid}/report", status_code=303)
    flash(request, {"fixes": "Fix list written.", "creative": "Ad ideas written."}[kind])
    return RedirectResponse(f"/p/{pid}#ai-{kind}", status_code=303)


# ---------- fixes: proposed in the page, applied in Google / Meta only on click ----------
def _fix_back(pid):
    return RedirectResponse(f"/p/{pid}#fixes", status_code=303)


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
    done, problems = 0, []
    for cid in ids:
        ch = db.change(cid)
        if not ch or ch["practice_id"] != pid:
            continue
        ok, msg = await asyncio.to_thread(actions.apply_change, ch, actor(request))
        if ok:
            done += 1
        else:
            problems.append(f"{ch['title']}: {msg}")
    msg = f"Applied {done} change{'s' if done != 1 else ''} in Google / Meta." if done else "Nothing was applied."
    if problems:
        msg += f" {len(problems)} didn't go through — " + problems[0] + (" (see the list for the rest)" if len(problems) > 1 else "")
    flash(request, msg, "error" if problems else "ok")
    return _fix_back(pid)


@app.post("/p/{pid}/fixes/{cid}/undo")
async def fixes_undo(request: Request, pid: int, cid: int):
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if ch and ch["practice_id"] == pid:
        ok, msg = await asyncio.to_thread(actions.undo_change, ch, actor(request))
        flash(request, f"Undone: {ch['title']}." if ok else f"Couldn't undo: {msg}", "ok" if ok else "error")
    return _fix_back(pid)


@app.post("/p/{pid}/fixes/{cid}/dismiss")
def fixes_dismiss(request: Request, pid: int, cid: int):
    if (r := guard(request)):
        return r
    ch = db.change(cid)
    if ch and ch["practice_id"] == pid and ch["status"] in ("proposed", "failed", "stale"):
        db.update_change(cid, status="dismissed")
    return _fix_back(pid)


@app.post("/p/{pid}/fixes/draft")
async def fixes_draft(request: Request, pid: int, target: str = Form("")):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    platform, _, campaign = target.partition("|")
    if not p or platform not in ("google", "meta") or not campaign:
        flash(request, "Pick a campaign for the new ad.", "error")
        return _fix_back(pid)
    try:
        await asyncio.to_thread(actions.draft_ad, p, platform, campaign, ai.generate_json)
        flash(request, f"New ad written for {campaign}. Review it below, then tick it and Apply to create it "
                       "(it's created paused).")
    except (actions.ActionError, ai.AIError, cx.ConnectorError) as e:
        flash(request, str(e), "error")
    return _fix_back(pid)


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
                checked=checked, redirect_uri=redirect_uri(request), practices=db.practices(),
                window=sync.sync_window(), sf_client=all(salesforce.client()), sf_on=salesforce.connected(),
                sf_user=salesforce.user(), sf_status=salesforce.status(), sf_redirect=_sf_redirect(request),
                sf_login=salesforce.login_url(), sf_waiting=len(salesforce.pending_ids(10000)) if salesforce.connected() else 0)


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
    return page(request, "map.html", offices=offs, counts=places.counts(), status=places.status(),
                browser_key=places.browser_key(), server_key_set=bool(places.server_key()),
                map_id=places.map_id(), map_id_custom=bool(cx.setting("MAPS_MAP_ID")),
                radius=places.radius(), brands=db.get_setting("map_brand_terms") or "",
                max_requests=places.max_requests(), per_office=places.per_office(), layers=places.LAYERS,
                last=db.get_setting("map_last_refresh"), prev=db.get_setting("map_prev_refresh"),
                preview=_import_preview())


@app.get("/map/data.json")
def market_map_data(request: Request):
    if not authed(request):
        return Response(status_code=401)
    places.purge_old()
    return {**places.map_data(), **field.map_overlay()}


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
    see_all = u["role"] == "admin"
    if not see_all:   # everyone except admins sees only their own visits, spend and follow-ups
        who = u["id"]
    start, end, label, prev_d, next_d = field.period_bounds(period, day)
    rows = field.visits_between(start, end, who, prac)
    m_start, m_end, *_ = field.period_bounds("month", day)
    month_rows = field.visits_between(m_start, m_end, None if see_all else u["id"])
    team = users.all_users()
    links = field.practice_links()
    budgets = field.budgets(m_start, month_rows)
    if not see_all:
        budgets = {"practices": [], "agents": [b for b in budgets["agents"] if b["id"] == u["id"]], "unlinked": 0}
    return page(request, "field.html", period=period, day=day, label=label, prev_d=prev_d, next_d=next_d,
                today=date.today(), start=start, end=end, rows=rows, days=field.by_day(rows), sums=field.summary(rows),
                budgets=budgets, see_all=see_all, month_label=m_start.strftime("%B %Y"),
                who=who, practice=prac, team=team, practices=db.practices(), ratings=field.RATING_LABELS,
                follow=field.followups(user_id=None if see_all else u["id"]), offices=places.offices(), links=links,
                agents=[t for t in team if t["active"] and t.get("field")], browser_key=places.browser_key(), map_id=places.map_id(),
                my_id=(u or {}).get("id"), sf_on=salesforce.connected(), sf_base=salesforce.instance_url())


@app.get("/field.xlsx")
def field_export(request: Request):
    if (r := guard(request)):
        return r
    period, day, who, prac = _field_args(request)
    u = me(request)
    if u["role"] != "admin":
        who = u["id"]
    start, end, label, *_ = field.period_bounds(period, day)
    rows = field.visits_between(start, end, who, prac)
    data = field.export_xlsx(rows, f"Field visits · {label}" + ("" if u["role"] == "admin" else f" · {u['name']}"))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="field-visits-{start.isoformat()}.xlsx"'})


@app.get("/field/log", response_class=HTMLResponse)
def field_log_form(request: Request, place: str = "", npi: str = "", office: str = "", error: str = ""):
    if (r := guard(request)):
        return r
    try:
        o = (field.office(int(office)) if office.isdigit() else None) or field.office_draft(place or None, npi or None)
    except field.FieldError as e:
        flash(request, str(e), "error")
        return RedirectResponse("/map", status_code=303)
    history = []
    if o.get("id"):
        with db.conn() as c:
            history = [dict(x) for x in c.execute("SELECT * FROM visits WHERE office_id=? ORDER BY visited_at DESC LIMIT 5",
                                                  (o["id"],))]
        history = field.with_people(history)
    return page(request, "field_log.html", o=o, history=history, ratings=field.RATING_LABELS, roles=field.MET_ROLES,
                max_people=field.MAX_PEOPLE, chips=field.DROP_CHIPS, browser_key=places.browser_key(), map_id=places.map_id(), error=error,
                ai_on=ai.available(), back=request.query_params.get("back", ""))


@app.post("/field/visits")
async def field_log(request: Request):
    if (r := guard(request)):
        return r
    form = await request.form()
    data = {k: form.get(k) for k in ("office_id", "place_id", "npi_key", "name", "address", "office_lat", "office_lng", "visited_at",
                                     "met_name", "met_role", "rating", "dropped", "cost", "followup", "notes",
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
    salesforce.send_in_background(PUBLIC_URL)
    v = field.visit(vid)
    where = " (on site)" if v["on_site"] == 1 else f" ({v['distance_mi']:g} mi from the office)" if v["on_site"] == 0 else ""
    flash(request, f"Visit to {v['office_name']} logged{where}.")
    back = form.get("back") or ""
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/field?period=day",
                            status_code=303)


@app.post("/field/visits/{vid}/delete")
def field_delete(request: Request, vid: int):
    if (r := guard(request)):
        return r
    v, u = field.visit(vid), me(request)
    if v and ((v["user_id"] == u["id"] and u.get("field")) or users.rank(u["role"]) >= users.rank("editor")):
        field.delete_visit(vid)
        flash(request, "Visit removed.")
    else:
        flash(request, "Only the person who logged a visit, or an editor, can remove it.", "error")
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
    status = "done" if q.get("status") == "done" else "open"
    filt = q.get("filter") if q.get("filter") in ("under", "quiet") else "all"
    items, counts = reviews.inbox(pid, src, status, plist)
    sel = _num(q.get("item"))
    cur = next((i for i in items if i["id"] == sel), None) or (items[0] if items else None)
    if cur:
        cur["posts_as"] = POSTS_AS[cur["source"]].format(acct=cur["account_name"], me=(me(request) or {}).get("name", "you").split(" ")[0])
        cur["no_reply_hint"] = reviews.no_reply_hint(cur)
    board = reviews.scoreboard(pid, plist=plist)
    rows = board["rows"]
    if filt == "under":
        rows = [x for x in rows if not x["met"]]
    elif filt == "quiet":
        rows = [x for x in rows if x["days"] is not None and x["days"] > 30]
    qs = {"practice": pid or "", "tab": tab, "src": src, "status": status}
    month_opts = reviews.months_back(13)[::-1][1:]
    with db.conn() as c:
        manual = {(m["practice_id"], m["month"]): m["count"] for m in c.execute("SELECT * FROM review_manual")}
    man_month = q.get("mm") if q.get("mm") in month_opts else month_opts[0]
    return page(request, "reviews.html", plist=plist, pid=pid, practice=next((p for p in plist if p["id"] == pid), None),
                tab=tab, src=src, status=status, filt=filt, items=items, counts=counts, cur=cur,
                stats=reviews.inbox_stats(pid, plist), board=board, rows=rows, qs=qs, sources=reviews.SOURCES,
                flag_text=reviews.FLAG_TEXT, duration=reviews.duration, inbox_status=reviews.last_status(),
                gbp_client=all(cx.gbp_client()), gbp_on=reviews.gbp_connected(), locations=reviews.locations(),
                gbp_redirect=_gbp_redirect(request), overdue=reviews.overdue_hours(), every=reviews.check_minutes(),
                month_opts=month_opts, man_month=man_month, manual=manual, month_label=reviews.month_label,
                meta_on=cx.meta_configured(), fb_linked=any(db.social_ids(p, "fb") or db.social_ids(p, "ig") for p in plist))


@app.get("/reviews.xlsx")
def reviews_export(request: Request):
    if (r := guard(request)):
        return r
    data = reviews.export_xlsx()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="google-reviews-{date.today().isoformat()}.xlsx"'})


DONE_MSG = {"reply": "Reply posted.", "like": "Liked. Marked done.", "hide": "Comment hidden. Marked done.",
            "done": "Marked done.", "reopen": "Moved back to Needs reply."}


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
            return {"draft": await asyncio.to_thread(reviews.draft_reply, iid)}
        except (reviews.ReviewsError, ai.AIError) as e:
            return JSONResponse({"error": str(e)}, status_code=422)
    back = _safe_back(form.get("back"))
    message = str(form.get("message") or "")
    if action not in DONE_MSG:
        return RedirectResponse(back, status_code=303)
    try:
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
            flash(request, f"Google reviews connected. {n} location{'s' if n != 1 else ''} found; check which practice each belongs to.")
        except cx.ConnectorError as e:
            flash(request, f"Google sign-in worked, but listing locations failed: {e}", "error")
    return RedirectResponse("/reviews#google-setup", status_code=303)


@app.post("/google/business/disconnect")
def gbp_disconnect(request: Request):
    if (r := guard(request)):
        return r
    db.set_setting("gbp_refresh_token", None)
    flash(request, "Google reviews disconnected. Reviews and counts already pulled are kept.")
    return RedirectResponse("/reviews#google-setup", status_code=303)
