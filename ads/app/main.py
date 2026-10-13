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
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import ai, db, sync
from . import connectors as cx
from .analyze import analyze_campaigns, analyze_search_terms, money, pacing, pct
from .parse import ParseError, parse_export

HERE = Path(__file__).parent
PASSWORD = os.environ.get("ADS_PASSWORD", "")
SECRET = os.environ.get("ADS_SECRET") or os.urandom(32).hex()
MAX_UPLOAD = 20 * 1024 * 1024
PLATFORMS = (("google", "Google Ads"), ("meta", "Meta Ads"))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
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
    if os.environ.get("ADS_DISABLE_SCHEDULER") != "1":
        sync.start_scheduler()


def authed(request: Request):
    return request.session.get("ok") is True


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
    ctx["any_connection"] = cx.meta_configured() or cx.google_configured()
    return templates.TemplateResponse(request, name, ctx)


# ---------- analysis view-model ----------
def platform_view(practice, key, label):
    budget = practice.get(f"{key}_budget") or 0
    up = db.latest_upload(practice["id"], key, "campaign") or db.latest_upload(practice["id"], key, "ad")
    view = {"key": key, "label": label, "upload": up, "budget": budget, "campaigns": [], "findings": [],
            "totals": None, "pacing": {"available": False}, "search_terms": None, "search_upload": None}
    if up:
        rows = db.upload_rows(up["id"])
        camps, tot, findings = analyze_campaigns(rows)
        view.update(campaigns=camps, totals=tot, findings=findings, pacing=pacing(rows, budget))
    st_up = db.latest_upload(practice["id"], key, "search_term")
    if st_up:
        view["search_upload"] = st_up
        view["search_terms"] = analyze_search_terms(db.upload_rows(st_up["id"]))
        if not up:  # search terms alone still give a spend picture
            rows = db.upload_rows(st_up["id"])
            camps, tot, _ = analyze_campaigns(rows)
            view.update(campaigns=camps, totals=tot)
    return view


def practice_views(p):
    return [platform_view(p, k, l) for k, l in PLATFORMS]


# ---------- auth ----------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if authed(request):
        return RedirectResponse("/", status_code=303)
    return page(request, "login.html", error=None, configured=bool(PASSWORD))


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form("")):
    if PASSWORD and hmac.compare_digest(password.encode(), PASSWORD.encode()):
        request.session.clear()
        request.session["ok"] = True
        return RedirectResponse("/", status_code=303)
    await asyncio.sleep(1.5)  # slow down guessing
    return page(request, "login.html", error="That password isn't right.", configured=bool(PASSWORD))


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/health")
def health():
    return {"ok": True}


# ---------- dashboard ----------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if (r := guard(request)):
        return r
    items = []
    for p in db.practices():
        views = practice_views(p)
        waste = sum(f["amount"] for v in views for f in v["findings"] if f["kind"] in ("zero-conv-campaign", "high-cpa"))
        waste += sum((v["search_terms"] or {}).get("wasted_total", 0) for v in views)
        items.append({"p": p, "views": views, "waste": round(waste, 2),
                      "issues": sum(1 for v in views for f in v["findings"] if f["severity"] in ("high", "medium"))})
    return page(request, "dashboard.html", items=items)


@app.post("/practices")
def create_practice(request: Request, name: str = Form(...), google_budget: float = Form(0),
                    meta_budget: float = Form(0)):
    if (r := guard(request)):
        return r
    if not name.strip():
        flash(request, "Give the practice a name.", "error")
        return RedirectResponse("/", status_code=303)
    try:
        pid = db.add_practice(name, max(google_budget, 0), max(meta_budget, 0))
    except sqlite3.IntegrityError:
        flash(request, f"“{name.strip()}” already exists.", "error")
        return RedirectResponse("/", status_code=303)
    flash(request, f"Added {name.strip()}. Upload its first export below.")
    return RedirectResponse(f"/p/{pid}", status_code=303)


# ---------- practice ----------
def get_practice_or_404(pid):
    return db.practice(pid)


@app.get("/p/{pid}", response_class=HTMLResponse)
def practice_page(request: Request, pid: int):
    if (r := guard(request)):
        return r
    p = get_practice_or_404(pid)
    if not p:
        return RedirectResponse("/", status_code=303)
    return page(request, "practice.html", p=p, views=practice_views(p), uploads=db.uploads(pid),
                ai_out={k: db.latest_ai(pid, k) for k in ai.TASKS},
                meta_accounts=sync.cached_accounts("meta"), google_accounts=sync.cached_accounts("google"),
                meta_on=cx.meta_configured(),
                google_on=cx.google_configured() and bool(db.get_setting("google_refresh_token")))


@app.post("/p/{pid}/settings")
def practice_settings(request: Request, pid: int, name: str = Form(...), google_budget: float = Form(0),
                      meta_budget: float = Form(0), notes: str = Form("")):
    if (r := guard(request)):
        return r
    try:
        db.update_practice(pid, name, max(google_budget, 0), max(meta_budget, 0), notes[:2000])
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
    return RedirectResponse("/", status_code=303)


@app.post("/p/{pid}/upload")
async def upload(request: Request, pid: int, file: UploadFile = File(...)):
    if (r := guard(request)):
        return r
    if not db.practice(pid):
        return RedirectResponse("/", status_code=303)
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
        return RedirectResponse("/", status_code=303)
    views = practice_views(p)
    if not any(v["upload"] or v["search_upload"] for v in views):
        flash(request, "Upload a Google Ads or Meta export first.", "error")
        return RedirectResponse(f"/p/{pid}", status_code=303)
    try:
        text = await asyncio.to_thread(ai.generate, kind, ai.build_context(p, views))
    except ai.AIError as e:
        flash(request, str(e), "error")
        return RedirectResponse(f"/p/{pid}#ai-{kind}", status_code=303)
    db.save_ai(pid, kind, text)
    if kind == "report":
        return RedirectResponse(f"/p/{pid}/report", status_code=303)
    flash(request, {"fixes": "Fix list written.", "creative": "Ad ideas written."}[kind])
    return RedirectResponse(f"/p/{pid}#ai-{kind}", status_code=303)


@app.get("/p/{pid}/report", response_class=HTMLResponse)
def report(request: Request, pid: int):
    if (r := guard(request)):
        return r
    p = db.practice(pid)
    if not p:
        return RedirectResponse("/", status_code=303)
    return page(request, "report.html", p=p, views=practice_views(p), report=db.latest_ai(pid, "report"))


# ---------- connections (phase 2: automatic pulls) ----------
def redirect_uri(request: Request):
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return base + "/google/callback"


def summarize_sync(status):
    if status.get("busy"):
        return "A sync is already running. Refresh in a minute.", "error"
    res = status.get("results", {})
    if not res:
        return "Nothing to sync yet: link a Meta or Google Ads account to a practice first.", "error"
    ok = sum(1 for r in res.values() for k in ("meta", "google") if r.get(k, {}).get("ok"))
    bad = [(r["name"], k, r[k]["error"]) for r in res.values() for k in ("meta", "google")
           if k in r and not r[k].get("ok")]
    msg = f"Synced {ok} account{'s' if ok != 1 else ''} ({status['since']} to {status['until']})."
    if bad:
        name, k, err = bad[0]
        msg += f" {len(bad)} failed — {name} ({'Meta' if k == 'meta' else 'Google'}): {err}"
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
    return page(request, "connections.html",
                meta_on=cx.meta_configured(), google_keys=cx.google_configured(),
                google_connected=bool(db.get_setting("google_refresh_token")),
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
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/", status_code=303)


@app.get("/google/connect")
def google_connect(request: Request):
    if (r := guard(request)):
        return r
    if not cx.google_configured():
        flash(request, "Save the Google Ads keys on the server first (sh connect-ads.sh google ...).", "error")
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
def practice_accounts(request: Request, pid: int, meta_account_id: str = Form(""),
                      google_customer_id: str = Form("")):
    if (r := guard(request)):
        return r
    db.set_practice_accounts(pid, meta_account_id.strip(), google_customer_id.strip())
    flash(request, "Linked accounts saved. Use “Sync now” to pull their numbers.")
    return RedirectResponse(f"/p/{pid}", status_code=303)
