"""
Connectors for Meta Ads (Marketing API) and Google Ads (API searchStream).
Reads return rows in the same shape as parse.parse_export(), so synced data flows
through the exact same analysis as uploaded exports. Writes (actions.py) only happen
when someone clicks Apply on a proposed change.
"""
from __future__ import annotations

import json
import time
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

# Google Ads access now comes from the Google Cloud project (developer tokens were retired
# on 2026-09-09); a service account added as a read-only user in Google Ads is the simplest path.

META_VERSION = os.environ.get("META_API_VERSION", "v25.0")
META_BASE = os.environ.get("META_GRAPH_BASE", "https://graph.facebook.com").rstrip("/")
# Instagram accounts connected in Social with an Instagram login (not through a Facebook Page) have
# Instagram tokens ("IG…"), which only work on Instagram's own Graph API host.
IG_BASE = os.environ.get("IG_GRAPH_BASE", "https://graph.instagram.com").rstrip("/")


def _base(token):
    return IG_BASE if (token or "").startswith("IG") else META_BASE
GOOGLE_VERSION = os.environ.get("GOOGLE_ADS_API_VERSION", "v25")
GOOGLE_BASE = os.environ.get("GOOGLE_ADS_BASE", "https://googleads.googleapis.com").rstrip("/")
GOOGLE_TOKEN_URL = os.environ.get("GOOGLE_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token")
GOOGLE_AUTH_URL = os.environ.get("GOOGLE_OAUTH_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
GOOGLE_SCOPE = "https://www.googleapis.com/auth/adwords"

# Meta "results": lead-type actions counted as conversions. 'lead' already totals
# instant-form + pixel leads, so the specific types are only used when it's absent.
META_RESULT_ACTIONS = [a.strip() for a in os.environ.get(
    "META_RESULT_ACTIONS", "lead,schedule_total,contact_total,submit_application_total").split(",") if a.strip()]
META_LEAD_FALLBACK = ["onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"]


class ConnectorError(RuntimeError):
    def __init__(self, msg="", payload=None):
        super().__init__(msg)
        self.payload = payload      # the platform's error body, when there was one


def setting(name):
    """Keys saved on the Connections page win; environment variables are the fallback."""
    try:
        from . import db
        v = db.get_setting(name.lower())
        if v:
            return v
    except Exception:
        pass
    return os.environ.get(name, "")


def _http(url, data=None, headers=None, method=None, timeout=60):
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            j = json.loads(raw)
        except ValueError:
            j = None
        raise ConnectorError(_explain(e.code, j, raw), payload=j)
    except urllib.error.URLError as e:
        raise ConnectorError(f"Couldn't reach {urllib.parse.urlparse(url).netloc}: {e.reason}")


def _explain(code, j, raw):
    """Turn platform error payloads into one readable sentence."""
    if isinstance(j, list) and j:
        j = j[0]
    if isinstance(j, dict):
        err = j.get("error", j)
        if isinstance(err, dict):
            msg = err.get("error_user_msg") or err.get("message") or err.get("error_description") or ""
            # Google Ads puts the useful part in details[].errors[].message
            for d in err.get("details", []) or []:
                for e in (d.get("errors") or []):
                    if e.get("message"):
                        msg = e["message"]
                        break
            if j.get("error_description"):
                msg = j["error_description"]
            if "api access blocked" in msg.lower():
                codes = ", ".join(f"{k} {err[k]}" for k in ("code", "error_subcode") if err.get(k) is not None)
                return ("Meta is blocking the MCDP Social app (API access blocked"
                        + (f"; {codes}" if codes else "") + "). The token itself can be fine: check developers."
                        "facebook.com → MCDP Social → Alerts and Required actions (restriction, Data Use Checkup "
                        "or Business Verification), and generate the token with only the permissions listed under "
                        f"“Where do I get this?”. (HTTP {code})")
            if isinstance(err.get("code"), int) and msg and "graph" not in msg.lower() and err.get("type") == "OAuthException":
                extra = f"code {err['code']}" + (f", subcode {err['error_subcode']}" if err.get("error_subcode") else "")
                msg = f"{msg} [{extra}]"
            if "not associated with any Ads accounts" in msg:
                return ("The key works, but this account hasn't been added in Google Ads yet. In Google Ads "
                        "(the manager account, or each practice account): Admin → Access and security → Users → +, "
                        "add the robot account's email with Standard access (Read only also works if you only want reports), then click Refresh account lists. "
                        f"(HTTP {code})")
            if "not yet enabled or has been deactivated" in msg:
                return ("Google won't use this ad account: it was never finished (no billing set up) or it was "
                        "cancelled. Either finish or reactivate it in Google Ads (open the account → Billing to add "
                        "payment, or Admin → Account settings → Account status → Reactivate), or point this practice "
                        "at the account its campaigns really run in (Linked accounts → Google Ads account, e.g. the "
                        f"MCDP Master Account) and save. (HTTP {code})")
            if msg:
                return f"{msg} (HTTP {code})"
        elif isinstance(err, str):
            return f"{j.get('error_description') or err} (HTTP {code})"
    return f"HTTP {code}: {raw[:200]}"


def _blank_row(platform, level):
    return {"platform": platform, "level": level, "date": None, "date_start": None, "date_end": None,
            "campaign": "", "name": "", "spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0,
            "conv_value": 0.0, "status": "", "budget": 0.0, "match_type": ""}


# ------------------------------------------------------------------ Meta
def meta_configured():
    return bool(setting("META_ACCESS_TOKEN"))


def _meta_get(path, params, token=None):
    token = token or setting("META_ACCESS_TOKEN")
    if not token:
        raise ConnectorError("No Meta token saved yet.")
    q = urllib.parse.urlencode({**params, "access_token": token})
    return _http(f"{_base(token)}/{META_VERSION}/{path}?{q}")


def _meta_paged(path, params, limit_pages=50, token=None, stop=None):
    """stop(row) -> True ends paging early (e.g. posts older than the window)."""
    data = _meta_get(path, params, token)
    out = list(data.get("data", []))
    pages = 1
    while data.get("paging", {}).get("next") and pages < limit_pages:
        if stop and out and stop(out[-1]):
            break
        data = _http(data["paging"]["next"])
        out.extend(data.get("data", []))
        pages += 1
    return out


def _meta_write(path, params, method="POST"):
    token = setting("META_ACCESS_TOKEN")
    if not token:
        raise ConnectorError("No Meta token saved yet.")
    body = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                   for k, v in {**params, "access_token": token}.items()}).encode()
    try:
        return _http(f"{META_BASE}/{META_VERSION}/{path}", data=body, method=method,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    except ConnectorError as e:
        raise ConnectorError(_write_hint(str(e), "meta")) from None


def _write_hint(msg, platform):
    low = msg.lower()
    if platform == "meta" and ("ads_management" in low or "(#200)" in low or "permission" in low):
        return ("Meta refused the change: the system user token can only read. In Business Settings → System "
                "users → MCDP_Ads_Reader, give the ad accounts 'Manage campaigns' access, then Generate token "
                "again with ads_management ticked, and paste it on the Connections page. (" + msg + ")")
    if platform == "google" and ("permission" in low or "not permitted" in low or "read only" in low
                                 or "read-only" in low):
        return ("Google refused the change: the robot account has Read only access. In Google Ads → Admin → "
                "Access and security, change mcdp-ads-reader's access to Standard. (" + msg + ")")
    return msg


def meta_accounts():
    """Ad accounts: those assigned to the token, plus every ad account the business portfolios own
    or manage for clients. [{'id': '1234', 'name': ..., 'currency': ..., 'active': bool}]"""
    fields = {"fields": "name,account_id,currency,account_status", "limit": 200}
    rows = list(_meta_paged("me/adaccounts", fields))
    for b in _businesses():
        for edge in ("owned_ad_accounts", "client_ad_accounts"):
            try:
                rows += _meta_paged(f"{b['id']}/{edge}", fields)
            except ConnectorError:
                continue
    out = {}
    for r in rows:
        aid = r.get("account_id") or str(r.get("id", "")).removeprefix("act_")
        if aid and aid not in out:
            out[aid] = {"id": aid, "name": r.get("name") or aid, "currency": r.get("currency", ""),
                        "active": r.get("account_status") == 1}
    return sorted(out.values(), key=lambda a: a["name"].lower())


def _meta_results(actions):
    counts = {a.get("action_type"): float(a.get("value") or 0) for a in actions or []}
    total = sum(counts.get(t, 0.0) for t in META_RESULT_ACTIONS)
    if "lead" in META_RESULT_ACTIONS and "lead" not in counts:
        total += sum(counts.get(t, 0.0) for t in META_LEAD_FALLBACK)
    return total


def meta_campaign_rows(account_id, since: date, until: date):
    account_id = str(account_id).removeprefix("act_")
    data = _meta_paged(f"act_{account_id}/insights", {
        "level": "campaign",
        "time_increment": 1,
        "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
        "fields": "campaign_name,spend,impressions,inline_link_clicks,actions,date_start,date_stop",
        "limit": 500,
    })
    rows = []
    for r in data:
        d = date.fromisoformat(r["date_start"])
        row = _blank_row("meta", "campaign")
        row.update(date=d, date_start=d, date_end=date.fromisoformat(r.get("date_stop", r["date_start"])),
                   campaign=r.get("campaign_name") or "(no name)",
                   spend=round(float(r.get("spend") or 0), 2),
                   impressions=int(float(r.get("impressions") or 0)),
                   clicks=int(float(r.get("inline_link_clicks") or 0)),
                   conversions=round(_meta_results(r.get("actions")), 2))
        rows.append(row)
    return rows


def meta_campaign_promos(account_id):
    """Which Facebook Page / Instagram account each campaign's ads promote, and whether it's a boost
    (an ad built from an existing post or reel). {campaign name: {'fb': [page ids], 'ig': [ig user ids],
    'ig_media': [media ids], 'boost': bool}}"""
    account_id = str(account_id).removeprefix("act_")
    base = "object_story_id,effective_object_story_id,effective_instagram_media_id,source_instagram_media_id"
    data = None
    for extra in (",instagram_user_id", ",instagram_actor_id", ""):   # Meta has renamed the IG field over versions
        try:
            data = _meta_paged(f"act_{account_id}/ads", {"fields": f"campaign{{name}},creative{{{base}{extra}}}",
                                                         "limit": 200}, limit_pages=20)
            break
        except ConnectorError as e:
            if not extra or "field" not in str(e).lower():
                raise
    out = {}
    for ad in data or []:
        name = ((ad.get("campaign") or {}).get("name") or "").strip()
        cr = ad.get("creative") or {}
        if not name:
            continue
        o = out.setdefault(name, {"fb": [], "ig": [], "ig_media": [], "boost": False})
        story = cr.get("effective_object_story_id") or cr.get("object_story_id") or ""
        if "_" in story and story.split("_", 1)[0] not in o["fb"]:
            o["fb"].append(story.split("_", 1)[0])
        ig = cr.get("instagram_user_id") or cr.get("instagram_actor_id")
        if ig and str(ig) not in o["ig"]:
            o["ig"].append(str(ig))
        for m in (cr.get("effective_instagram_media_id"), cr.get("source_instagram_media_id")):
            if m and str(m) not in o["ig_media"]:
                o["ig_media"].append(str(m))
        if cr.get("object_story_id") or cr.get("source_instagram_media_id"):
            o["boost"] = True        # built from a post that was already published: a boost
    return out


# ------------------------------------------------------------------ Meta organic (Pages + Instagram)
# Meta retired post "impressions" in Nov 2025 in favour of "views" and keeps renaming metrics, so
# insights are requested newest-name-first and any metric Meta rejects is skipped (and remembered).
FB_POST_METRICS = ["post_media_view", "post_total_media_view_unique", "post_clicks",
                   "post_impressions", "post_impressions_unique"]
IG_MEDIA_METRICS = ["views", "reach", "saved", "shares", "total_interactions"]
_REJECTED_METRICS = set()


def _is_metric_error(msg):
    low = msg.lower()
    return "metric" in low and ("(#100)" in low or "valid" in low or "not supported" in low or "deprecated" in low)


def _insight_values(data):
    out = {}
    for item in data.get("data", []):
        vals = item.get("values") or []
        v = vals[-1].get("value") if vals else item.get("total_value", {}).get("value")
        if isinstance(v, dict):
            v = sum(x for x in v.values() if isinstance(x, (int, float)))
        if isinstance(v, (int, float)):
            out[item.get("name")] = v
    return out


def _insights(obj_id, metrics, token, extra=None):
    want = [m for m in metrics if m not in _REJECTED_METRICS]
    if not want:
        return {}
    params = {"metric": ",".join(want), **(extra or {})}
    try:
        return _insight_values(_meta_get(f"{obj_id}/insights", params, token))
    except ConnectorError as e:
        if not _is_metric_error(str(e)):
            raise
    out = {}
    for m in want:  # one bad name fails the whole request: retry one by one
        try:
            out.update(_insight_values(_meta_get(f"{obj_id}/insights", {**params, "metric": m}, token)))
        except ConnectorError as e:
            if _is_metric_error(str(e)):
                _REJECTED_METRICS.add(m)
            else:
                raise
    return out


PAGE_FIELDS = "id,name,access_token,instagram_business_account{id,username,name}"


def _businesses():
    """Business portfolios the token can see (needs business_management); [] if none."""
    try:
        return _meta_paged("me/businesses", {"fields": "id,name", "limit": 100})
    except ConnectorError:
        return []


def meta_pages():
    """Every Facebook Page and Instagram account reachable for organic stats, like Postiz finds them:
    Pages assigned to the system user, Pages each business portfolio owns or manages for clients,
    and channels connected in Postiz. Returns (pages, instagram):
      pages = [{'id','name','token','via'}]  (token None = listed but no access yet)
      instagram = [{'id','name','page_id','token','via'}]"""
    from . import postiz
    seen = {}

    def add(rows, via):
        for r in rows:
            pg = seen.setdefault(r["id"], {"id": r["id"], "name": r.get("name") or r["id"], "token": None,
                                           "via": via, "ig": None, "pz": None})
            if r.get("access_token") and not pg["token"]:
                pg["token"], pg["via"] = r["access_token"], via
            if r.get("instagram_business_account") and not pg["ig"]:
                pg["ig"] = r["instagram_business_account"]

    add(_meta_paged("me/accounts", {"fields": PAGE_FIELDS, "limit": 100}), "system user")
    for b in _businesses():
        for edge in ("owned_pages", "client_pages"):
            try:
                add(_meta_paged(f"{b['id']}/{edge}", {"fields": PAGE_FIELDS, "limit": 100}), b.get("name") or "portfolio")
            except ConnectorError:
                continue
    pz = postiz.channels()
    for pid, ch in pz["facebook"].items():
        pg = seen.setdefault(pid, {"id": pid, "name": ch["name"], "token": None, "via": "Postiz", "ig": None, "pz": None})
        pg["pz"] = ch["token"]
        if not pg["token"]:
            pg["token"], pg["via"] = ch["token"], "Postiz"
    pages, igs, ig_seen = [], [], set()
    for pg in seen.values():
        # "tokens": every way in, best first (used for comments, where one may have permissions the other lacks)
        pages.append({"id": pg["id"], "name": pg["name"], "token": pg["token"], "via": pg["via"],
                      "tokens": [t for t in dict.fromkeys([pg["token"], pg["pz"]]) if t]})
        ig = pg["ig"]
        if ig and ig.get("id"):
            # Postiz's *Facebook* connection has no Instagram permissions, so for Instagram use the
            # system user's Page token, else Postiz's own Instagram connection.
            own = pg["token"] if pg["via"] != "Postiz" else None
            pz_ig = (pz["instagram"].get(ig["id"]) or {}).get("token")
            tok = own or pz_ig
            igs.append({"id": ig["id"], "name": "@" + ig["username"] if ig.get("username") else ig.get("name") or ig["id"],
                        "page_id": pg["id"], "token": tok,
                        "via": pg["via"] if own else ("Postiz" if pz_ig else pg["via"]),
                        "tokens": [t for t in dict.fromkeys([own, pz_ig]) if t]})
            ig_seen.add(ig["id"])
    for iid, ch in pz["instagram"].items():
        if iid not in ig_seen:
            igs.append({"id": iid, "name": ch["name"], "page_id": None, "token": ch["token"], "via": "Postiz",
                        "tokens": [ch["token"]] if ch["token"] else []})
    pages.sort(key=lambda x: x["name"].lower())
    igs.sort(key=lambda x: x["name"].lower())
    return pages, igs


def _ts(s):
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00").replace("+0000", "+00:00"))


FB_FIELDS_FULL = ("id,message,created_time,permalink_url,full_picture,status_type,shares,"
                  "reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)")
FB_FIELDS_BASIC = "id,message,created_time,permalink_url,full_picture,status_type"


def _page_post_rows(page_id, page_token, since_ts):
    """The Page's own posts. Tries the published_posts edge first (includes posts scheduled from
    tools like Postiz), then posts; drops the like/comment counts only if the token can't read them."""
    last = None
    for edge, fields in (("published_posts", FB_FIELDS_FULL), ("posts", FB_FIELDS_FULL), ("posts", FB_FIELDS_BASIC)):
        # Busy Pages: Meta answers "Please reduce the amount of data you're asking for" when a page of posts
        # with their like/comment counts is too big, so ask for fewer at a time (more pages, same posts).
        for limit, pages in ((100, 10), (25, 40), (10, 100)):
            try:
                return _meta_paged(f"{page_id}/{edge}", {"fields": fields, "since": since_ts, "limit": limit},
                                   limit_pages=pages, token=page_token), fields == FB_FIELDS_BASIC
            except ConnectorError as e:
                last = e
                if _transient(e):
                    time.sleep(RETRY_PAUSE)     # Meta hiccup: wait a moment, then ask again in smaller pages
                elif not _too_much_data(e):
                    break
    raise last


RETRY_PAUSE = 3


def _too_much_data(e):
    m = str(e).lower()
    return "reduce the amount of data" in m or "(#1)" in m and "please reduce" in m


def _transient(e):
    """Meta's 'An unknown error occurred' / 'temporarily unavailable' (codes 1, 2, HTTP 500/503): usually a busy
    Page timing out on Meta's side. Worth another try, ideally with fewer posts per request."""
    err = (e.payload or {}).get("error", {}) if isinstance(getattr(e, "payload", None), dict) else {}
    if err.get("is_transient") or err.get("code") in (1, 2):
        return True
    m = str(e).lower()
    return "unknown error" in m or "temporarily unavailable" in m or "(http 500)" in m or "(http 503)" in m


def facebook_posts(page_id, page_token, since: date):
    """Returns (posts, note). A post whose stats Meta won't give yet (common for very new posts)
    is still kept, with its numbers left blank, instead of failing the whole Page."""
    from datetime import datetime, timezone
    since_ts = int(datetime(since.year, since.month, since.day, tzinfo=timezone.utc).timestamp())
    rows, basic = _page_post_rows(page_id, page_token, since_ts)
    out, failed, last_err = [], 0, ""
    for r in rows:
        created = _ts(r["created_time"])
        if created.date() < since:
            continue
        try:
            m = _insights(r["id"], FB_POST_METRICS, page_token, {"period": "lifetime"})
        except ConnectorError as e:
            m, failed, last_err = {}, failed + 1, str(e)
        likes = int((r.get("reactions") or {}).get("summary", {}).get("total_count") or 0)
        comments = int((r.get("comments") or {}).get("summary", {}).get("total_count") or 0)
        shares = int((r.get("shares") or {}).get("count") or 0)
        views = m.get("post_media_view", m.get("post_impressions"))
        reach = m.get("post_total_media_view_unique", m.get("post_impressions_unique"))
        out.append({"network": "facebook", "account_id": page_id, "post_id": r["id"],
                    "created": created.isoformat(timespec="seconds"), "text": (r.get("message") or "")[:500],
                    "permalink": r.get("permalink_url") or "", "media_type": r.get("status_type") or "",
                    "thumbnail": r.get("full_picture") or "",
                    "views": int(views) if views is not None else None,
                    "reach": int(reach) if reach is not None else None,
                    "likes": likes, "comments": comments, "shares": shares, "saves": None,
                    "clicks": int(m["post_clicks"]) if "post_clicks" in m else None,
                    "interactions": likes + comments + shares})
    notes = []
    if basic:
        notes.append("likes/comments/shares not readable with this access (needs pages_read_engagement)")
    if failed:
        notes.append(f"views not available yet for {failed} post{'s' if failed != 1 else ''} ({last_err[:160]})")
    return out, "; ".join(notes)


def instagram_posts(ig_id, token, since: date):
    rows = _meta_paged(f"{ig_id}/media", {
        "fields": "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count,"
                  "thumbnail_url,media_url", "limit": 50}, limit_pages=10, token=token,
        stop=lambda r: _ts(r["timestamp"]).date() < since)
    out, failed, last_err = [], 0, ""
    for r in rows:
        created = _ts(r["timestamp"])
        if created.date() < since:
            continue
        try:
            m = _insights(r["id"], IG_MEDIA_METRICS, token)
        except ConnectorError as e:
            m, failed, last_err = {}, failed + 1, str(e)
        likes, comments = int(r.get("like_count") or 0), int(r.get("comments_count") or 0)
        shares = int(m["shares"]) if "shares" in m else 0
        saves = int(m["saved"]) if "saved" in m else None
        inter = int(m["total_interactions"]) if "total_interactions" in m else likes + comments + shares + (saves or 0)
        kind = r.get("media_product_type") or r.get("media_type") or ""
        out.append({"network": "instagram", "account_id": ig_id, "post_id": r["id"],
                    "created": created.isoformat(timespec="seconds"), "text": (r.get("caption") or "")[:500],
                    "permalink": r.get("permalink") or "", "media_type": kind,
                    "thumbnail": r.get("thumbnail_url") or (r.get("media_url") if r.get("media_type") == "IMAGE" else "") or "",
                    "views": int(m["views"]) if "views" in m else None,
                    "reach": int(m["reach"]) if "reach" in m else None,
                    "likes": likes, "comments": comments, "shares": shares, "saves": saves, "clicks": None,
                    "interactions": inter})
    note = f"stats not available yet for {failed} post{'s' if failed != 1 else ''} ({last_err[:160]})" if failed else ""
    return out, note


# ------------------------------------------------------------------ comments (Reviews & comments inbox)
# Reading comments needs pages_read_engagement + pages_read_user_content (Facebook) or
# instagram_manage_comments (Instagram); replying, liking and hiding need pages_manage_engagement /
# instagram_manage_comments. Each Page can have two ways in (system user, Social), so callers pass a
# list of tokens and the first that works is used.
FB_COMMENT_FIELDS = "id,message,created_time,permalink_url,is_hidden,from{id,name},comments.limit(10){from{id}}"
FB_COMMENT_FIELDS_NO_FROM = "id,message,created_time,permalink_url,is_hidden,comments.limit(10){from{id}}"
IG_COMMENT_FIELDS = "id,text,timestamp,username,hidden,replies.limit(10){username}"


def _first_working(tokens, fn):
    """Try fn(token) with each token; return (result, token). Raises the last error if none works."""
    last = ConnectorError("No access to this account yet.")
    for t in [t for t in tokens if t]:
        try:
            return fn(t), t
        except ConnectorError as e:
            last = e
    raise last


def facebook_comments(page_id, tokens, since: date):
    """Top-level comments on the Page's recent posts (newest 25 per post).
    Returns (comments, token_used). Each comment: id, post_id, post_text, post_link, author, text,
    created, link, hidden, page_replied."""
    from datetime import datetime, timezone
    since_ts = int(datetime(since.year, since.month, since.day, tzinfo=timezone.utc).timestamp())

    def fetch(token):
        last = None
        for cf in (FB_COMMENT_FIELDS, FB_COMMENT_FIELDS_NO_FROM):   # "from" needs pages_read_user_content
            fields = (f"id,message,created_time,permalink_url,"
                      f"comments.filter(toplevel).order(reverse_chronological).limit(25){{{cf}}}")
            for limit, pages in ((25, 4), (10, 10), (5, 20)):
                try:
                    return _meta_paged(f"{page_id}/published_posts", {"fields": fields, "since": since_ts, "limit": limit},
                                       limit_pages=pages, token=token)
                except ConnectorError as e:
                    last = e
                    if not _too_much_data(e):
                        break
        raise last

    posts, token = _first_working(tokens, fetch)
    out = []
    for p in posts:
        for c in (p.get("comments") or {}).get("data", []):
            who = c.get("from") or {}
            if who.get("id") == page_id:
                continue                         # the Page's own comment
            replies = (c.get("comments") or {}).get("data", [])
            out.append({"id": c["id"], "post_id": p["id"], "post_text": (p.get("message") or "")[:300],
                        "post_link": p.get("permalink_url") or "", "author": who.get("name") or "Facebook user",
                        "text": c.get("message") or "", "created": _ts(c["created_time"]).isoformat(timespec="seconds"),
                        "link": c.get("permalink_url") or p.get("permalink_url") or "",
                        "hidden": bool(c.get("is_hidden")),
                        "page_replied": any((r.get("from") or {}).get("id") == page_id for r in replies)})
    return out, token


def instagram_comments(ig_id, username, tokens, since: date):
    """Comments on the account's recent posts. username: the account's own @handle (to spot its replies)."""
    own = (username or "").lstrip("@").lower()

    def fetch(token):
        return _meta_paged(f"{ig_id}/media", {
            "fields": f"id,caption,permalink,timestamp,comments.limit(25){{{IG_COMMENT_FIELDS}}}", "limit": 25},
            limit_pages=3, token=token, stop=lambda r: _ts(r["timestamp"]).date() < since)

    media, token = _first_working(tokens, fetch)
    out = []
    for m in media:
        if _ts(m["timestamp"]).date() < since:
            continue
        for c in (m.get("comments") or {}).get("data", []):
            if own and (c.get("username") or "").lower() == own:
                continue
            replies = (c.get("replies") or {}).get("data", [])
            out.append({"id": c["id"], "post_id": m["id"], "post_text": (m.get("caption") or "")[:300],
                        "post_link": m.get("permalink") or "", "author": "@" + (c.get("username") or "instagram user"),
                        "text": c.get("text") or "", "created": _ts(c["timestamp"]).isoformat(timespec="seconds"),
                        "link": m.get("permalink") or "", "hidden": bool(c.get("hidden")),
                        "page_replied": bool(own) and any((r.get("username") or "").lower() == own for r in replies)})
    return out, token


def _meta_post(path, params, token):
    body = urllib.parse.urlencode({**params, "access_token": token}).encode()
    return _http(f"{_base(token)}/{META_VERSION}/{path}", data=body, method="POST",
                 headers={"Content-Type": "application/x-www-form-urlencoded"})


def _comment_hint(msgs, network):
    """msgs: [(how, error)] for each way in that was tried."""
    low = " ".join(m for _, m in msgs).lower()
    detail = " · ".join(f"{how}: {m}" for how, m in msgs)
    if not any(w in low for w in ("permission", "(#200)", "(#10)", "(#3)", "does not exist", "unsupported post")):
        return detail
    if network == "instagram":
        return ("Meta refused the reply: the hub can read this Instagram account's comments but isn't allowed to "
                "answer them. Fix (once per account): Meta Business Settings → Users → System users → "
                "MCDP_Ads_Reader → Assign assets → Instagram accounts → this account → turn on “Community "
                "activity” (manage comments), then reply again. If the account is connected in Social through "
                "Instagram login instead, reconnect it there and allow comment management. (" + detail + ")")
    return ("Meta refused the reply: the hub can read this Page's comments but isn't allowed to answer them. Fix "
            "(once per Page): Business Settings → Users → System users → MCDP_Ads_Reader → Assign assets → Pages → "
            "this Page → turn on “Community activity” (or full control), make sure the token has "
            "pages_manage_engagement, then reply again. (" + detail + ")")


def _how(token):
    return "Instagram login (Social)" if (token or "").startswith("IG") else "Facebook access"


def comment_action(network, comment_id, action, tokens, message=""):
    """Reply to, like or hide one comment. network: facebook | instagram."""
    if action == "reply":
        edge, params = (f"{comment_id}/comments" if network == "facebook" else f"{comment_id}/replies"), {"message": message}
    elif action == "like" and network == "facebook":
        edge, params = f"{comment_id}/likes", {}
    elif action == "hide":
        edge, params = comment_id, ({"is_hidden": "true"} if network == "facebook" else {"hide": "true"})
    else:
        raise ConnectorError("That action isn't available for this comment.")
    tried = []
    for t in [t for t in tokens if t]:
        try:
            return _meta_post(edge, params, t)
        except ConnectorError as e:
            tried.append((_how(t), str(e)))
    if not tried:
        raise ConnectorError("No access to this account yet. Link it under the practice's Linked accounts or "
                             "connect it in Social.")
    raise ConnectorError(_comment_hint(tried, network))


# ------------------------------------------------------------------ Google Business Profile (reviews)
# Reviews and owner replies. Needs Google's approval for the Business Profile APIs and a person's
# sign-in (service accounts can't manage Business Profiles): an OAuth client in the Cloud project,
# signed in once by someone who manages the practices' profiles.
GBP_SCOPE = "https://www.googleapis.com/auth/business.manage"
GBP_ACCOUNTS = os.environ.get("GBP_ACCOUNTS_BASE", "https://mybusinessaccountmanagement.googleapis.com").rstrip("/")
GBP_INFO = os.environ.get("GBP_INFO_BASE", "https://mybusinessbusinessinformation.googleapis.com").rstrip("/")
GBP_V4 = os.environ.get("GBP_V4_BASE", "https://mybusiness.googleapis.com").rstrip("/")
STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}


def gbp_client():
    return setting("GBP_CLIENT_ID"), setting("GBP_CLIENT_SECRET")


def gbp_auth_url(redirect_uri, state):
    cid, _ = gbp_client()
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "scope": GBP_SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state})


def gbp_exchange_code(code, redirect_uri):
    cid, secret = gbp_client()
    data = urllib.parse.urlencode({"code": code, "client_id": cid, "client_secret": secret,
                                   "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    tok = _http(GOOGLE_TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if not tok.get("refresh_token"):
        raise ConnectorError("Google didn't return a long-term sign-in. Remove the app's access at "
                             "myaccount.google.com/permissions and connect again.")
    return tok["refresh_token"]


class GBPClient:
    def __init__(self, refresh_token):
        cid, secret = gbp_client()
        if not (cid and secret and refresh_token):
            raise ConnectorError("Google Business Profile isn't connected yet.")
        data = urllib.parse.urlencode({"refresh_token": refresh_token, "client_id": cid, "client_secret": secret,
                                       "grant_type": "refresh_token"}).encode()
        self.token = _http(GOOGLE_TOKEN_URL, data=data,
                           headers={"Content-Type": "application/x-www-form-urlencoded"})["access_token"]

    def _get(self, url, params=None):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        try:
            return _http(url + q, headers={"Authorization": f"Bearer {self.token}"})
        except ConnectorError as e:
            raise ConnectorError(self._hint(str(e))) from None

    @staticmethod
    def _hint(msg):
        low = msg.lower()
        if "has not been used" in low or "is disabled" in low or "service_disabled" in low:
            return ("Google hasn't switched on the Business Profile APIs for this Cloud project yet. Apply for "
                    "access (Google's Business Profile API access form), then enable the 'My Business Account "
                    "Management', 'My Business Business Information' and 'Google My Business' APIs. (" + msg + ")")
        return msg

    def locations(self):
        """Every location the signed-in person manages: [{'name','title','address','account'}]."""
        out = []
        accounts = self._get(f"{GBP_ACCOUNTS}/v1/accounts", {"pageSize": 20}).get("accounts", [])
        for a in accounts:
            token = None
            for _ in range(20):
                params = {"readMask": "name,title,storefrontAddress", "pageSize": 100}
                if token:
                    params["pageToken"] = token
                j = self._get(f"{GBP_INFO}/v1/{a['name']}/locations", params)
                for loc in j.get("locations", []):
                    addr = loc.get("storefrontAddress") or {}
                    line = ", ".join(x for x in [" ".join(addr.get("addressLines") or []), addr.get("locality", ""),
                                                 addr.get("administrativeArea", "")] if x)
                    out.append({"name": f"{a['name']}/{loc['name']}", "title": loc.get("title") or loc["name"],
                                "address": line, "account": a.get("accountName") or a["name"]})
                token = j.get("nextPageToken")
                if not token:
                    break
        return out

    def reviews(self, location, all_pages=False):
        """(reviews, average rating, total count). Newest-updated first; one page unless all_pages."""
        out, token, avg, total = [], None, None, None
        for _ in range(200 if all_pages else 1):
            params = {"pageSize": 50, "orderBy": "updateTime desc"}
            if token:
                params["pageToken"] = token
            j = self._get(f"{GBP_V4}/v4/{location}/reviews", params)
            avg = j.get("averageRating", avg)
            total = j.get("totalReviewCount", total)
            for r in j.get("reviews", []):
                rev = r.get("reviewer") or {}
                reply = r.get("reviewReply") or {}
                out.append({"id": r["name"], "author": "A Google user" if rev.get("isAnonymous") else (rev.get("displayName") or "A Google user"),
                            "stars": STARS.get(r.get("starRating"), 0), "text": r.get("comment") or "",
                            "created": r.get("createTime", "")[:19], "updated": r.get("updateTime", "")[:19],
                            "reply": reply.get("comment") or "", "reply_at": (reply.get("updateTime") or "")[:19]})
            token = j.get("nextPageToken")
            if not token:
                break
        return out, avg, total

    def reply(self, review_name, comment):
        try:
            return _http(f"{GBP_V4}/v4/{review_name}/reply", data={"comment": comment}, method="PUT",
                         headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        except ConnectorError as e:
            raise ConnectorError(self._hint(str(e))) from None


# ------------------------------------------------------------------ Google
def google_service_account():
    raw = setting("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except ValueError:
        return None
    return info if info.get("client_email") and info.get("private_key") else None


def parse_service_account(raw):
    """Validate an uploaded key file; returns the cleaned JSON text."""
    try:
        info = json.loads(raw)
    except ValueError:
        raise ConnectorError("That file isn't a Google service account key (it isn't JSON).")
    if info.get("type") != "service_account" or not info.get("client_email") or not info.get("private_key"):
        raise ConnectorError("That JSON file isn't a service account key. In Google Cloud: IAM & Admin → "
                             "Service Accounts → your account → Keys → Add key → JSON.")
    return json.dumps({k: info[k] for k in ("type", "project_id", "private_key_id", "private_key",
                                             "client_email", "client_id", "token_uri") if k in info})


def google_oauth_configured():
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def google_configured():
    return bool(google_service_account()) or google_oauth_configured()


def _service_account_token(info):
    """OAuth 2.0 JWT-bearer flow for a service account (no extra HTTP library needed)."""
    import time as _t
    from google.auth import crypt, jwt
    try:
        signer = crypt.RSASigner.from_service_account_info(info)
    except Exception as e:
        raise ConnectorError(f"The service account key couldn't be read: {e}")
    token_uri = os.environ.get("GOOGLE_OAUTH_TOKEN_URL") or info.get("token_uri") or "https://oauth2.googleapis.com/token"
    now = int(_t.time())
    assertion = jwt.encode(signer, {"iss": info["client_email"], "scope": GOOGLE_SCOPE, "aud": token_uri,
                                    "iat": now, "exp": now + 3600})
    data = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": assertion.decode() if isinstance(assertion, bytes) else assertion}).encode()
    return _http(token_uri, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})["access_token"]


def google_auth_url(redirect_uri, state):
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"], "redirect_uri": redirect_uri,
        "response_type": "code", "scope": GOOGLE_SCOPE, "access_type": "offline",
        "prompt": "consent", "include_granted_scopes": "true", "state": state})


def google_exchange_code(code, redirect_uri):
    data = urllib.parse.urlencode({
        "code": code, "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    tok = _http(GOOGLE_TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if not tok.get("refresh_token"):
        raise ConnectorError("Google didn't return a long-term sign-in. Remove the app's access at "
                             "myaccount.google.com/permissions and connect again.")
    return tok["refresh_token"]


def _google_access_token(refresh_token):
    data = urllib.parse.urlencode({
        "refresh_token": refresh_token, "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"], "grant_type": "refresh_token"}).encode()
    return _http(GOOGLE_TOKEN_URL, data=data,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})["access_token"]


def _clean_id(cid):
    return "".join(ch for ch in str(cid) if ch.isdigit())


class GoogleClient:
    def __init__(self, refresh_token=None):
        sa = google_service_account()
        if sa:
            self.token = _service_account_token(sa)
        elif google_oauth_configured():
            if not refresh_token:
                raise ConnectorError("Google isn't connected yet. Use 'Connect Google' on the Connections page.")
            self.token = _google_access_token(refresh_token)
        else:
            raise ConnectorError("Google isn't set up yet. Upload a service account key on the Connections page.")
        self.login_cid = _clean_id(setting("GOOGLE_ADS_LOGIN_CUSTOMER_ID"))
        self._route = {}  # practice account id -> account to log in through (itself or its manager)
        self._discovered = False

    def _headers(self, login_cid=None):
        h = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        # Developer tokens were retired in Sept 2026 (access now comes from the Cloud project);
        # still sent if configured, since the API ignores it.
        if os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"):
            h["developer-token"] = os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"]
        lc = login_cid or self.login_cid
        if lc:
            h["login-customer-id"] = lc
        return h

    def query(self, customer_id, gaql, login_cid=None):
        url = f"{GOOGLE_BASE}/{GOOGLE_VERSION}/customers/{_clean_id(customer_id)}/googleAds:searchStream"
        batches = _http(url, data={"query": gaql}, headers=self._headers(login_cid), timeout=120)
        out = []
        for b in batches if isinstance(batches, list) else [batches]:
            out.extend(b.get("results", []))
        return out

    def mutate(self, customer_id, service, operations):
        """service e.g. 'campaigns', 'campaignBudgets', 'campaignCriteria', 'adGroupAds'."""
        cid = _clean_id(customer_id)
        url = f"{GOOGLE_BASE}/{GOOGLE_VERSION}/customers/{cid}/{service}:mutate"
        try:
            res = _http(url, data={"operations": operations}, headers=self._headers(self._login_for(cid)))
        except ConnectorError as e:
            raise ConnectorError(_write_hint(str(e), "google"), payload=e.payload) from None
        return res.get("results", [])

    def mutate_all(self, customer_id, mutate_operations):
        """GoogleAdsService.Mutate: several kinds of change in one all-or-nothing request (temporary
        negative IDs link the new objects together). Returns mutateOperationResponses."""
        cid = _clean_id(customer_id)
        url = f"{GOOGLE_BASE}/{GOOGLE_VERSION}/customers/{cid}/googleAds:mutate"
        try:
            res = _http(url, data={"mutateOperations": mutate_operations},
                        headers=self._headers(self._login_for(cid)), timeout=120)
        except ConnectorError as e:
            raise ConnectorError(_write_hint(str(e), "google"), payload=e.payload) from None
        return res.get("mutateOperationResponses", [])

    def search(self, customer_id, gaql):
        return self.query(customer_id, gaql, login_cid=self._login_for(customer_id))

    def accounts(self):
        """Every practice (non-manager) account the robot can read, found by asking Google which accounts
        it was added to and expanding any manager accounts. The typed manager ID is only a hint, so a
        wrong or missing one doesn't break anything. Also records which account to log in through."""
        found, errors, roots = {}, [], []
        if self.login_cid:
            roots.append(self.login_cid)
        try:
            listed = _http(f"{GOOGLE_BASE}/{GOOGLE_VERSION}/customers:listAccessibleCustomers",
                           headers={k: v for k, v in self._headers().items() if k != "login-customer-id"},
                           method="GET")
            roots += [rn.split("/")[-1] for rn in listed.get("resourceNames", [])]
        except ConnectorError as e:
            errors.append(str(e))
        seen = set()
        for cid in roots[:100]:
            if cid in seen:
                continue
            seen.add(cid)
            try:
                res = self.query(cid, "SELECT customer.id, customer.descriptive_name, customer.manager, "
                                 "customer.status FROM customer", login_cid=cid)
                c = res[0]["customer"] if res else {}
                if c.get("manager"):
                    for r in self.query(cid, "SELECT customer_client.id, customer_client.descriptive_name, "
                                        "customer_client.manager, customer_client.status FROM customer_client",
                                        login_cid=cid):
                        cc = r["customerClient"]
                        if cc.get("manager"):
                            continue
                        aid = str(cc["id"])
                        found.setdefault(aid, {"id": aid, "name": cc.get("descriptiveName") or aid,
                                               "active": cc.get("status", "ENABLED") == "ENABLED"})
                        self._route.setdefault(aid, cid)
                else:
                    found[cid] = {"id": cid, "name": c.get("descriptiveName") or cid,
                                  "active": c.get("status", "ENABLED") == "ENABLED"}
                    self._route[cid] = cid
            except ConnectorError as e:
                errors.append(str(e))
        self._discovered = True
        if not found:
            # A real problem (bad key, API not enabled) beats the generic "add the robot" advice.
            real = [e for e in errors if "permission" not in e.lower() and "not associated" not in e.lower()
                    and "hasn't been added" not in e]
            if real:
                raise ConnectorError(real[0])
            raise ConnectorError(
                "The robot account can't see any Google Ads accounts yet. In Google Ads (the manager "
                "account, or each practice account): Admin → Access and security → Users → +, add the robot "
                "account's email with Read only access. If it's listed there as pending, remove it and add it "
                "again. Then click Refresh account lists.")
        return sorted(found.values(), key=lambda a: a["name"].lower())

    def _login_for(self, customer_id):
        cid = _clean_id(customer_id)
        if cid not in self._route and not self._discovered:
            try:
                self.accounts()
            except ConnectorError:
                pass
        return self._route.get(cid) or self.login_cid or cid

    def campaign_rows(self, customer_id, since: date, until: date):
        res = self.query(customer_id, (
            "SELECT campaign.name, campaign.status, segments.date, metrics.cost_micros, metrics.impressions, "
            "metrics.clicks, metrics.conversions, metrics.conversions_value FROM campaign "
            f"WHERE segments.date BETWEEN '{since.isoformat()}' AND '{until.isoformat()}' "
            "AND metrics.impressions > 0"), login_cid=self._login_for(customer_id))
        rows = []
        for r in res:
            m, c = r.get("metrics", {}), r.get("campaign", {})
            d = date.fromisoformat(r["segments"]["date"])
            row = _blank_row("google", "campaign")
            row.update(date=d, date_start=d, date_end=d, campaign=c.get("name") or "(no name)",
                       status=(c.get("status") or "").title(),
                       spend=round(int(m.get("costMicros", 0)) / 1_000_000, 2),
                       impressions=int(m.get("impressions", 0)), clicks=int(m.get("clicks", 0)),
                       conversions=round(float(m.get("conversions", 0)), 2),
                       conv_value=round(float(m.get("conversionsValue", 0)), 2))
            rows.append(row)
        return rows

    def search_term_rows(self, customer_id, since: date, until: date):
        res = self.query(customer_id, (
            "SELECT search_term_view.search_term, campaign.name, metrics.cost_micros, metrics.impressions, "
            "metrics.clicks, metrics.conversions FROM search_term_view "
            f"WHERE segments.date BETWEEN '{since.isoformat()}' AND '{until.isoformat()}' "
            "AND metrics.impressions > 0"), login_cid=self._login_for(customer_id))
        rows = []
        for r in res:
            m = r.get("metrics", {})
            row = _blank_row("google", "search_term")
            row.update(date_start=since, date_end=until, campaign=r.get("campaign", {}).get("name") or "",
                       name=r.get("searchTermView", {}).get("searchTerm") or "",
                       spend=round(int(m.get("costMicros", 0)) / 1_000_000, 2),
                       impressions=int(m.get("impressions", 0)), clicks=int(m.get("clicks", 0)),
                       conversions=round(float(m.get("conversions", 0)), 2))
            if row["name"]:
                rows.append(row)
        return rows
