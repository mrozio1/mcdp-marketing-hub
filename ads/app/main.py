"""MCDP Ads: upload Google / Meta exports per practice, get exact findings + AI write-ups."""
from __future__ import annotations

import asyncio
import hmac
import html
import os
import re
import sqlite3
from pathlib import Path

import markdown as md
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import actions, ai, auth, db, emailfmt, organic, places, referrals, scope, sync
from . import connectors as cx
from .analyze import analyze_campaigns, analyze_search_terms, money, pacing, pct
from .parse import ParseError, parse_export

HERE = Path(__file__).parent
PASSWORD = os.environ.get("ADS_PASSWORD", "")
SECRET = os.environ.get("ADS_SECRET") or os.urandom(32).hex()
MAX_UPLOAD = 20 * 1024 * 1024
PLATFORMS = (("google", "Google Ads"), ("meta", "Meta Ads"))

class NotAllowed(Exception):
    def __init__(self, needed):
        self.needed = needed


def _role_gate(request: Request):
    """Runs before every route: checks the signed-in person's role against what the request needs
    (auth.needed_role). Not signed in is left to each route, which sends people to /login."""
    need = auth.needed_role(request.method, request.url.path)
    if need is None:
        return
    me = auth.current(request.session)
    request.state.me = me
    if me is not None and not auth.at_least(me["role"], need):
        raise NotAllowed(need)


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, dependencies=[Depends(_role_gate)])
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
    auth.init()
    places.init()
    places.purge_old()
    referrals.init()
    if os.environ.get("ADS_DISABLE_SCHEDULER") != "1":
        sync.start_scheduler()
        places.start_scheduler()
        referrals.start_scheduler()


def authed(request: Request):
    return auth.current(request.session) is not None


def me(request: Request):
    return getattr(request.state, "me", None) or auth.current(request.session)


@app.exception_handler(NotAllowed)
async def _not_allowed(request: Request, exc: NotAllowed):
    what = {"editor": "an Editor or Admin", "admin": "an Admin"}.get(exc.needed, "someone with more access")
    msg = f"Your sign-in can view and export, but this needs {what}. Ask an admin on the Team page if you need it."
    if request.method == "GET":
        who = me(request)
        return templates.TemplateResponse(request, "denied.html", {
            "msg": msg, "me": who, "flash": None, "role_labels": auth.ROLE_LABELS, "social_url": SOCIAL_URL,
            "can_edit": bool(who) and auth.at_least(who["role"], "editor"),
            "is_admin": bool(who) and who["role"] == "admin"}, status_code=403)
    if request.headers.get("content-type", "").startswith("application/json"):
        return Response('{"ok": false, "error": "view only"}', status_code=403, media_type="application/json")
    flash(request, msg, "error")
    back = request.headers.get("referer") or "/"
    return RedirectResponse(back, status_code=303)


def guard(request: Request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    return None


def flash(request, msg, kind="ok"):
    request.session["flash"] = {"msg": msg, "kind": kind}


def page(request, name, **ctx):
    who = me(request)
    ctx["me"] = who
    ctx["can_edit"] = bool(who) and auth.at_least(who["role"], "editor")
    ctx["is_admin"] = bool(who) and who["role"] == "admin"
    ctx["role_labels"] = auth.ROLE_LABELS
    ctx["flash"] = request.session.pop("flash", None)
    ctx["ai_on"] = ai.available()
    ctx["last_sync"] = sync.last_sync()
    ctx["any_connection"] = cx.meta_configured() or cx.google_configured()
    ctx.setdefault("social_url", SOCIAL_URL)
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
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if authed(request):
        return RedirectResponse("/", status_code=303)
    return page(request, "login.html", error=None, configured=bool(PASSWORD) or bool(auth.users()), email="")


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, email: str = Form(""), password: str = Form("")):
    sess = auth.login(email, password, PASSWORD)
    if sess:
        request.session.clear()
        request.session.update(sess)
        return RedirectResponse("/", status_code=303)
    await asyncio.sleep(1.5)  # slow down guessing
    err = "That email and password don't match." if email.strip() else "That password isn't right."
    return page(request, "login.html", error=err, configured=bool(PASSWORD) or bool(auth.users()), email=email)


@app.get("/setup/{token}", response_class=HTMLResponse)
def setup_form(request: Request, token: str):
    u = auth.by_setup_token(token)
    return page(request, "setup.html", u=u, token=token, error=None)


@app.post("/setup/{token}", response_class=HTMLResponse)
async def setup_save(request: Request, token: str, password: str = Form(""), confirm: str = Form("")):
    u = auth.by_setup_token(token)
    if not u:
        return page(request, "setup.html", u=None, token=token, error=None)
    problem = auth.password_problem(password) or (None if password == confirm else "The two passwords don't match.")
    if problem:
        return page(request, "setup.html", u=u, token=token, error=problem)
    auth.set_password(u["id"], password)
    request.session.clear()
    request.session.update(auth.login(u["email"], password, PASSWORD))
    flash(request, f"Welcome, {u['name']}. You're signed in as {auth.ROLE_LABELS[u['role']].lower()}.")
    return RedirectResponse("/", status_code=303)


# ---------- team: who has access ----------
def _link(request, token):
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return f"{base}/setup/{token}"


@app.get("/team", response_class=HTMLResponse)
def team(request: Request):
    if (r := guard(request)):
        return r
    new = request.session.pop("new_link", None)
    return page(request, "team.html", people=auth.users(), roles=auth.ROLES, role_help=auth.ROLE_HELP,
                new_link=new, link_days=auth.LINK_DAYS)


@app.post("/team/add")
def team_add(request: Request, name: str = Form(""), email: str = Form(""), role: str = Form("viewer")):
    if (r := guard(request)):
        return r
    if not name.strip() or not auth.valid_email(email):
        flash(request, "Enter a name and a valid email address.", "error")
    elif auth.by_email(email):
        flash(request, "Someone with that email already has a sign-in. Use “New setup link” on their row.", "error")
    else:
        uid, token = auth.add_user(name, email, role)
        request.session["new_link"] = {"uid": uid, "name": name.strip(), "email": email.strip().lower(),
                                       "url": _link(request, token), "role": role}
    return RedirectResponse("/team", status_code=303)


def _team_target(request, uid):
    u = auth.user(uid)
    who = me(request)
    if not u:
        return None, "That person isn't on the list any more."
    if who and who.get("uid") == uid:
        return None, "You can't change your own access here; ask another admin."
    return u, None


@app.post("/team/{uid}/role")
def team_role(request: Request, uid: int, role: str = Form("viewer")):
    if (r := guard(request)):
        return r
    u, err = _team_target(request, uid)
    if u:
        auth.set_role(uid, role)
        flash(request, f"{u['name']} is now {auth.ROLE_LABELS.get(role, role).lower()}.")
    else:
        flash(request, err, "error")
    return RedirectResponse("/team", status_code=303)


@app.post("/team/{uid}/link")
def team_link(request: Request, uid: int):
    if (r := guard(request)):
        return r
    u = auth.user(uid)
    if u:
        token = auth.new_setup_link(uid)
        request.session["new_link"] = {"uid": uid, "name": u["name"], "email": u["email"],
                                       "url": _link(request, token), "role": u["role"], "reset": bool(u["pw_hash"])}
    return RedirectResponse("/team", status_code=303)


@app.post("/team/{uid}/access")
def team_access(request: Request, uid: int, action: str = Form("")):
    if (r := guard(request)):
        return r
    u, err = _team_target(request, uid)
    if not u:
        flash(request, err, "error")
    elif action == "disable":
        auth.set_disabled(uid, True)
        flash(request, f"{u['name']} can't sign in any more (their history is kept).")
    elif action == "enable":
        auth.set_disabled(uid, False)
        flash(request, f"{u['name']} can sign in again.")
    elif action == "delete":
        auth.delete_user(uid)
        flash(request, f"Removed {u['name']}.")
    return RedirectResponse("/team", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


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


def _worth_a_look(allp):
    items = []
    waiting = [(p, len(db.changes(p["id"], ["proposed"]))) for p in allp]
    waiting = [(p, n) for p, n in waiting if n]
    if waiting:
        total = sum(n for _, n in waiting)
        first = waiting[0][0]
        where = first["name"] if len(waiting) == 1 else f"{len(waiting)} practices"
        items.append({"text": f"{total} fix{'es' if total != 1 else ''} waiting for your approval ({where})",
                      "href": f"/p/{first['id']}#fixes"})
    st = places.status()
    if st.get("busy"):
        items.append({"text": "The map is refreshing right now", "href": "/map"})
    elif st and st.get("ok") is False:
        items.append({"text": "The last map refresh stopped; have a look", "href": "/map"})
    elif places.offices() and not db.get_setting("ref_last_refresh"):
        items.append({"text": "Build the referral and competition lists once", "href": "/map/list"})
    last = sync.last_sync().get("at")
    if last:
        from datetime import datetime as _dt
        t = _dt.fromisoformat(last)
        day = "today" if t.date() == _dt.now().date() else t.strftime("%A")
        items.append({"text": f"Ads synced {day} at {t.strftime('%-I:%M %p')}", "href": None})
    elif cx.meta_configured() or cx.google_configured():
        items.append({"text": "Ads haven't synced yet", "href": "/connections"})
    return items[:3]


@app.get("/", response_class=HTMLResponse)
def welcome(request: Request):
    if (r := guard(request)):
        return r
    allp = db.practices()
    mapped = sum(c.get("referrer", 0) for c in places.counts().values())
    return page(request, "welcome.html", ads=_ad_totals(allp), mapped=mapped, look=_worth_a_look(allp),
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
        ok, msg = await asyncio.to_thread(actions.apply_change, ch)
        if ok:
            done += 1
            db.update_change(cid, applied_by=(me(request) or {}).get("name", ""))
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
        ok, msg = await asyncio.to_thread(actions.undo_change, ch)
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
                window=sync.sync_window())


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
    status = await asyncio.to_thread(sync.sync_all)
    flash(request, *summarize_sync(status))
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
    return places.map_data()


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
