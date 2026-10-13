"""
Connectors for Meta Ads (Marketing API) and Google Ads (API searchStream).
Reads return rows in the same shape as parse.parse_export(), so synced data flows
through the exact same analysis as uploaded exports. Writes (actions.py) only happen
when someone clicks Apply on a proposed change.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

# Google Ads access now comes from the Google Cloud project (developer tokens were retired
# on 2026-09-09); a service account added as a read-only user in Google Ads is the simplest path.

META_VERSION = os.environ.get("META_API_VERSION", "v25.0")
META_BASE = os.environ.get("META_GRAPH_BASE", "https://graph.facebook.com").rstrip("/")
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
    pass


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
        raise ConnectorError(_explain(e.code, j, raw))
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
            if "not associated with any Ads accounts" in msg:
                return ("The key works, but this account hasn't been added in Google Ads yet. In Google Ads "
                        "(the manager account, or each practice account): Admin → Access and security → Users → +, "
                        "add the robot account's email with Standard access (Read only also works if you only want reports), then click Refresh account lists. "
                        f"(HTTP {code})")
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


def _meta_get(path, params):
    token = setting("META_ACCESS_TOKEN")
    if not token:
        raise ConnectorError("No Meta token saved yet.")
    q = urllib.parse.urlencode({**params, "access_token": token})
    return _http(f"{META_BASE}/{META_VERSION}/{path}?{q}")


def _meta_paged(path, params, limit_pages=50):
    data = _meta_get(path, params)
    out = list(data.get("data", []))
    pages = 1
    while data.get("paging", {}).get("next") and pages < limit_pages:
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
    """Ad accounts the token can read: [{'id': '1234', 'name': ..., 'currency': ...}]"""
    rows = _meta_paged("me/adaccounts", {"fields": "name,account_id,currency,account_status", "limit": 200})
    return sorted(({"id": r["account_id"], "name": r.get("name") or r["account_id"],
                    "currency": r.get("currency", ""), "active": r.get("account_status") == 1} for r in rows),
                  key=lambda a: a["name"].lower())


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
            raise ConnectorError(_write_hint(str(e), "google")) from None
        return res.get("results", [])

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
