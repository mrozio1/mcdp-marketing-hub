"""Stand-in for Meta Graph API, Google Ads API and Google OAuth, mimicking real response shapes."""
import json
import threading
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

META_TOKEN = "EAAG" + "Zx9mK2pQ" * 22  # real tokens are 150+ chars
GOOGLE_DEV = "dev-token-123"
REFRESH = "1//refresh-abc"
ACCESS = "ya29.access-xyz"
MCC = "1112223333"
SA_EMAIL = "mcdp-ads-reader@mcdp-ads.iam.gserviceaccount.com"
CLIENTS = {"4445556666": "Heights Orthodontics", "4445557777": "Havens Orthodontics"}
ACCESSIBLE = {MCC}   # accounts the signed-in user/robot was added to; tests may change this
LOG = []
_DENIED = [{"error": {"code": 403, "message": "The caller does not have permission", "details": [{"errors": [{
    "message": "User doesn't have permission to access customer. Note: If you're accessing a client customer, "
               "the manager's customer id must be set in the 'login-customer-id' header."}]}]}}]
_NOT_USER = [{"error": {"code": 401, "message": "Request is missing required authentication credential.", "details": [{
    "errors": [{"message": "The Google account (@gmail.com user) that generated the OAuth access tokens is not "
                           "associated with any Ads accounts."}]}]}}]


def _days(since, until):
    d = date.fromisoformat(since)
    while d <= date.fromisoformat(until):
        yield d
        d += timedelta(days=1)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    # ---------------- GET ----------------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        LOG.append(("GET", u.path, dict(self.headers)))
        if u.path.startswith("/v25.0/"):
            if q.get("access_token") != META_TOKEN:
                return self._json(400, {"error": {"message": "Invalid OAuth access token - Cannot parse access token",
                                                  "type": "OAuthException", "code": 190}})
            if u.path == "/v25.0/me/adaccounts":
                return self._json(200, {"data": [
                    {"account_id": "555001", "name": "Heights Orthodontics", "currency": "USD", "account_status": 1, "id": "act_555001"},
                    {"account_id": "555002", "name": "Havens Orthodontics", "currency": "USD", "account_status": 1, "id": "act_555002"},
                    {"account_id": "555009", "name": "Old Test Account", "currency": "USD", "account_status": 2, "id": "act_555009"}]})
            if u.path.endswith("/insights"):
                tr = json.loads(q["time_range"])
                rows = []
                for d in _days(tr["since"], tr["until"]):
                    rows.append({"campaign_name": "Heights - Free Consult Leads", "spend": "14.50", "impressions": "1200",
                                 "inline_link_clicks": "22", "date_start": d.isoformat(), "date_stop": d.isoformat(),
                                 "actions": [{"action_type": "lead", "value": "1"},
                                             {"action_type": "onsite_conversion.lead_grouped", "value": "1"},
                                             {"action_type": "link_click", "value": "22"}]})
                    rows.append({"campaign_name": "Heights - Smile Gallery Video", "spend": "11.20", "impressions": "3300",
                                 "inline_link_clicks": "14", "date_start": d.isoformat(), "date_stop": d.isoformat(),
                                 "actions": [{"action_type": "video_view", "value": "900"}]})
                # page 1 / page 2 like the real API
                page = int(q.get("page", "1"))
                half = len(rows) // 2
                if page == 1:
                    nxt = f"http://{self.headers['Host']}{u.path}?{urllib.parse.urlencode({**q, 'page': 2})}"
                    return self._json(200, {"data": rows[:half], "paging": {"next": nxt}})
                return self._json(200, {"data": rows[half:], "paging": {}})
        if u.path == "/o/oauth2/v2/auth":
            # Pretend the user approved: bounce back with a code
            back = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": "auth-code-1", "state": q["state"]})
            self.send_response(302)
            self.send_header("Location", back)
            self.end_headers()
            return
        if u.path == "/v25/customers:listAccessibleCustomers":
            dev = self.headers.get("developer-token")
            if dev is not None and dev != GOOGLE_DEV:
                return self._json(401, [{"error": {"code": 401, "message": "Unauthorized",
                                                   "details": [{"errors": [{"message": "The developer token is not approved."}]}]}}])
            return self._json(200, {"resourceNames": [f"customers/{c}" for c in sorted(ACCESSIBLE)]})
        self._json(404, {"error": {"message": "not found"}})

    # ---------------- POST ----------------
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        LOG.append(("POST", u.path, dict(self.headers), body))
        if u.path == "/token":
            f = dict(urllib.parse.parse_qsl(body))
            if f.get("grant_type") == "authorization_code" and f.get("code") == "auth-code-1":
                return self._json(200, {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3599})
            if f.get("grant_type") == "refresh_token" and f.get("refresh_token") == REFRESH:
                return self._json(200, {"access_token": ACCESS, "expires_in": 3599})
            if f.get("grant_type") == "urn:ietf:params:oauth:grant-type:jwt-bearer":
                import base64
                parts = f.get("assertion", "").split(".")
                if len(parts) == 3:
                    pad = lambda x: x + "=" * (-len(x) % 4)
                    hdr = json.loads(base64.urlsafe_b64decode(pad(parts[0])))
                    claims = json.loads(base64.urlsafe_b64decode(pad(parts[1])))
                    if hdr.get("alg") == "RS256" and claims.get("iss") == SA_EMAIL and "adwords" in claims.get("scope", ""):
                        return self._json(200, {"access_token": ACCESS, "expires_in": 3599, "token_type": "Bearer"})
                return self._json(400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."})
            return self._json(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        if u.path.endswith("googleAds:searchStream"):
            dev = self.headers.get("developer-token")
            if dev is not None and dev != GOOGLE_DEV:
                return self._json(401, [{"error": {"code": 401, "message": "Request is missing required authentication credential.",
                                                   "details": [{"errors": [{"message": "The developer token is not approved."}]}]}}])
            if self.headers.get("Authorization") != f"Bearer {ACCESS}":
                return self._json(401, [{"error": {"code": 401, "message": "Invalid credentials"}}])
            if not ACCESSIBLE:
                return self._json(401, _NOT_USER)
            target = u.path.split("/customers/")[1].split("/")[0]
            login = self.headers.get("login-customer-id") or target
            # Google's rule: you must be a user on the login account, and the target must be it or under it.
            if login not in ACCESSIBLE or not (target == login or (login == MCC and target in CLIENTS)):
                return self._json(403, _DENIED)
            q = json.loads(body)["query"]
            if "FROM customer_client" in q:
                return self._json(200, [{"results": [
                    {"customerClient": {"id": MCC, "descriptiveName": "MCDP Manager", "manager": True, "status": "ENABLED"}}] + [
                    {"customerClient": {"id": cid, "descriptiveName": n, "manager": False, "status": "ENABLED"}}
                    for cid, n in CLIENTS.items()]}])
            if q.rstrip().endswith("FROM customer"):
                return self._json(200, [{"results": [{"customer": {
                    "id": target, "descriptiveName": "MCDP Manager" if target == MCC else CLIENTS.get(target, target),
                    "manager": target == MCC, "status": "ENABLED"}}]}])
            since, until = q.split("BETWEEN '")[1].split("'")[0], q.split("AND '")[1].split("'")[0]
            if "FROM campaign" in q:
                res = []
                for d in _days(since, until):
                    res.append({"campaign": {"name": "Invisalign - Search", "status": "ENABLED"}, "segments": {"date": d.isoformat()},
                                "metrics": {"costMicros": "48250000", "impressions": "410", "clicks": "21", "conversions": 1.5,
                                            "conversionsValue": 0.0}})
                    res.append({"campaign": {"name": "PMax - Smile Promo", "status": "ENABLED"}, "segments": {"date": d.isoformat()},
                                "metrics": {"costMicros": "20000000", "impressions": "1630", "clicks": "14"}})
                # streamed in two batches like the real API
                return self._json(200, [{"results": res[: len(res) // 2]}, {"results": res[len(res) // 2:]}])
            if "FROM search_term_view" in q:
                return self._json(200, [{"results": [
                    {"searchTermView": {"searchTerm": "invisalign cost near me"}, "campaign": {"name": "Invisalign - Search"},
                     "metrics": {"costMicros": "235200000", "impressions": "1204", "clicks": "98", "conversions": 12.0}},
                    {"searchTermView": {"searchTerm": "invisalign jobs"}, "campaign": {"name": "Invisalign - Search"},
                     "metrics": {"costMicros": "60450000", "impressions": "402", "clicks": "31"}}]}])
        self._json(404, {"error": {"message": "not found"}})


def start(port=9944):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    import sys
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 9944), H)
    srv.serve_forever()
