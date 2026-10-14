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
READ_ONLY = {"google": False, "meta": False}   # simulate read-only access levels
PAGE_TOKENS = {"pg1": "PAGETOKEN-heights", "pg2": "PAGETOKEN-havens", "pg3": "PAGETOKEN-bandeen-postiz"}
BUSINESS = {"on": False}   # turn on to simulate a business portfolio with owned + client Pages / ad accounts
RETIRED_METRICS = {"post_impressions", "post_impressions_unique"}   # rejected like Meta does since Nov 2025
FB_POSTS = {
    "pg1": [
        {"id": "pg1_1", "message": "Free smile consults all October! Book online.", "created_time": "2026-10-08T15:00:00+0000",
         "permalink_url": "https://facebook.com/heights/posts/1", "status_type": "added_photos",
         "reactions": {"summary": {"total_count": 40}}, "comments": {"summary": {"total_count": 6}}, "shares": {"count": 4},
         "_m": {"post_media_view": 2400, "post_total_media_view_unique": 1800, "post_clicks": 55}},
        {"id": "pg1_2", "message": "Meet Dr. Lee", "created_time": "2026-10-03T13:00:00+0000",
         "permalink_url": "https://facebook.com/heights/posts/2", "status_type": "added_video",
         "reactions": {"summary": {"total_count": 12}}, "comments": {"summary": {"total_count": 1}},
         "_m": {"post_media_view": 900, "post_total_media_view_unique": 700, "post_clicks": 9}},
        {"id": "pg1_old", "message": "Summer hours", "created_time": "2026-08-20T13:00:00+0000",
         "permalink_url": "https://facebook.com/heights/posts/0", "status_type": "mobile_status_update",
         "reactions": {"summary": {"total_count": 3}}, "comments": {"summary": {"total_count": 0}},
         "_m": {"post_media_view": 100}},
    ],
    "pg3": [
        {"id": "pg3_1", "message": "Bandeen open house Saturday", "created_time": "2026-10-09T13:00:00+0000",
         "permalink_url": "https://facebook.com/bandeen/posts/1", "status_type": "added_photos",
         "reactions": {"summary": {"total_count": 33}}, "comments": {"summary": {"total_count": 5}}, "shares": {"count": 2},
         "_m": {"post_media_view": 1500, "post_total_media_view_unique": 1100, "post_clicks": 12}},
    ],
    "pg2": [
        {"id": "pg2_1", "message": "Havens is hiring!", "created_time": "2026-10-05T13:00:00+0000",
         "permalink_url": "https://facebook.com/havens/posts/1", "status_type": "added_photos",
         "reactions": {"summary": {"total_count": 20}}, "comments": {"summary": {"total_count": 2}}, "shares": {"count": 1},
         "_m": {"post_media_view": 1000, "post_total_media_view_unique": 800, "post_clicks": 4}},
    ],
}
IG_MEDIA = {
    "ig1": [
        {"id": "ig1_1", "caption": "Before your first adjustment: what to expect", "media_type": "VIDEO",
         "media_product_type": "REELS", "timestamp": "2026-10-10T16:00:00+0000", "permalink": "https://instagram.com/p/1",
         "like_count": 150, "comments_count": 12, "thumbnail_url": "https://cdn.example/1.jpg",
         "_m": {"views": 5200, "reach": 4100, "saved": 30, "shares": 18, "total_interactions": 210}},
        {"id": "ig1_2", "caption": "Team photo", "media_type": "IMAGE", "media_product_type": "FEED",
         "timestamp": "2026-10-02T16:00:00+0000", "permalink": "https://instagram.com/p/2", "like_count": 60,
         "comments_count": 3, "media_url": "https://cdn.example/2.jpg",
         "_m": {"views": 1300, "reach": 1000, "saved": 2, "shares": 1, "total_interactions": 66}},
    ],
}


def _fresh_state():
    return {
        "g_campaigns": {"901": {"name": "Invisalign - Search", "status": "ENABLED", "channel": "SEARCH", "budget": "b1"},
                        "902": {"name": "PMax - Smile Promo", "status": "ENABLED", "channel": "PERFORMANCE_MAX", "budget": "b2"}},
        "g_budgets": {"b1": {"micros": 50_000_000, "shared": False}, "b2": {"micros": 20_000_000, "shared": False}},
        "g_negatives": {},       # resource -> (campaign_id, text)
        "g_ads": {},             # resource -> payload
        "m_objects": {"c1": {"name": "Heights - Free Consult Leads", "status": "ACTIVE", "daily_budget": "2000"},
                      "c2": {"name": "Heights - Smile Gallery Video", "status": "ACTIVE"},
                      "as2": {"name": "Video viewers", "status": "ACTIVE", "daily_budget": "1500", "campaign_id": "c2"}},
        "m_created": {},
        "seq": 100,
    }


STATE = _fresh_state()


def reset():
    STATE.clear()
    STATE.update(_fresh_state())
    READ_ONLY.update(google=False, meta=False)
    BUSINESS["on"] = False


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
            if q.get("access_token") not in (META_TOKEN, *PAGE_TOKENS.values()):
                return self._json(400, {"error": {"message": "Invalid OAuth access token - Cannot parse access token",
                                                  "type": "OAuthException", "code": 190}})
            org = self._organic(u.path[len("/v25.0/"):], q)
            if org is not None:
                return org
            if u.path == "/v25.0/me/adaccounts":
                return self._json(200, {"data": [
                    {"account_id": "555001", "name": "Heights Orthodontics", "currency": "USD", "account_status": 1, "id": "act_555001"},
                    {"account_id": "555002", "name": "Havens Orthodontics", "currency": "USD", "account_status": 1, "id": "act_555002"},
                    {"account_id": "555009", "name": "Old Test Account", "currency": "USD", "account_status": 2, "id": "act_555009"}]})
            obj = u.path[len("/v25.0/"):]
            if obj == "act_555001/campaigns":
                return self._json(200, {"data": [dict(id=k, **{f: v for f, v in o.items()})
                                                 for k, o in STATE["m_objects"].items() if k.startswith("c")]})
            if obj == "act_555001/adsets":
                return self._json(200, {"data": [dict(id=k, **o) for k, o in STATE["m_objects"].items() if k.startswith("as")]})
            if obj == "c1/ads":
                return self._json(200, {"data": [{"id": "ad1", "name": "Free consult - smile photo", "adset_id": "as1",
                                                  "effective_status": "ACTIVE", "creative": {"id": "cr1", "object_story_spec": {
                                                      "page_id": "777", "instagram_user_id": "888", "link_data": {
                                                          "link": "https://heightsortho.example/consult", "image_hash": "abc123",
                                                          "message": "Book your free consult.", "name": "Free Consult",
                                                          "call_to_action": {"type": "BOOK_NOW"}}}}}]})
            if obj == "c2/ads":
                return self._json(200, {"data": [{"id": "ad2", "adset_id": "as2", "effective_status": "ACTIVE",
                                                  "creative": {"id": "cr2", "object_story_spec": {"page_id": "777",
                                                               "video_data": {"video_id": "v1"}}}}]})
            if obj in STATE["m_objects"]:
                o = STATE["m_objects"][obj]
                return self._json(200, {"id": obj, **{f: o.get(f) for f in q.get("fields", "").split(",") if f in o}})
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
    def _organic(self, obj, q):
        if obj == "me/accounts":
            return self._json(200, {"data": [
                {"id": "pg1", "name": "Heights Orthodontics", "access_token": PAGE_TOKENS["pg1"],
                 "instagram_business_account": {"id": "ig1", "username": "heightsortho"}},
                {"id": "pg2", "name": "Havens Orthodontics", "access_token": PAGE_TOKENS["pg2"]}]})
        if BUSINESS["on"]:
            if obj == "me/businesses":
                return self._json(200, {"data": [{"id": "biz1", "name": "Motor City Dental Partners"}]})
            if obj == "biz1/owned_pages":
                return self._json(200, {"data": [
                    {"id": "pg1", "name": "Heights Orthodontics", "access_token": PAGE_TOKENS["pg1"]},
                    {"id": "pg3", "name": "Bandeen Orthodontics",
                     "instagram_business_account": {"id": "ig3", "username": "bandeenortho"}}]})
            if obj == "biz1/client_pages":
                return self._json(200, {"data": [{"id": "pg4", "name": "Pinnacle Ortho"}]})
            if obj == "biz1/owned_ad_accounts":
                return self._json(200, {"data": [
                    {"account_id": "555001", "name": "Heights Orthodontics", "currency": "USD", "account_status": 1, "id": "act_555001"},
                    {"account_id": "555003", "name": "Bandeen Orthodontics", "currency": "USD", "account_status": 1, "id": "act_555003"}]})
            if obj == "biz1/client_ad_accounts":
                return self._json(200, {"data": []})
        if obj.endswith("/posts") and obj.split("/")[0] in FB_POSTS:
            page = obj.split("/")[0]
            if q.get("access_token") != PAGE_TOKENS[page]:
                return self._json(403, {"error": {"message": "(#10) This endpoint requires the 'pages_read_engagement' permission", "code": 10}})
            return self._json(200, {"data": [{k: v for k, v in r.items() if k != "_m"} for r in FB_POSTS[page]]})
        if obj.endswith("/media") and obj.split("/")[0] in IG_MEDIA:
            return self._json(200, {"data": [{k: v for k, v in r.items() if k != "_m"} for r in IG_MEDIA[obj.split("/")[0]]]})
        if obj.endswith("/insights"):
            oid = obj.split("/")[0]
            item = next((r for rows in (*FB_POSTS.values(), *IG_MEDIA.values()) for r in rows if r["id"] == oid), None)
            if item is None:
                return None
            metrics = q.get("metric", "").split(",")
            if any(m in RETIRED_METRICS for m in metrics):
                return self._json(400, {"error": {"message": "(#100) The value must be a valid insights metric", "code": 100}})
            return self._json(200, {"data": [{"name": m, "period": "lifetime", "values": [{"value": item["_m"][m]}]}
                                             for m in metrics if m in item["_m"]]})
        return None

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        f = {**q, **dict(urllib.parse.parse_qsl(body))}
        LOG.append(("DELETE", u.path, dict(self.headers), body))
        if f.get("access_token") != META_TOKEN:
            return self._json(400, {"error": {"message": "Invalid OAuth access token", "code": 190}})
        obj = u.path[len("/v25.0/"):]
        if STATE["m_created"].pop(obj, None) is not None:
            return self._json(200, {"success": True})
        return self._json(404, {"error": {"message": "not found", "code": 100}})

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
        if u.path.startswith("/v25.0/"):
            f = dict(urllib.parse.parse_qsl(body))
            if f.get("access_token") != META_TOKEN:
                return self._json(400, {"error": {"message": "Invalid OAuth access token", "code": 190}})
            if READ_ONLY["meta"]:
                return self._json(403, {"error": {"message": "(#200) Requires ads_management permission to manage the object", "code": 200}})
            obj = u.path[len("/v25.0/"):]
            if obj == "act_555001/adcreatives":
                STATE["seq"] += 1
                cid = f"cr{STATE['seq']}"
                STATE["m_created"][cid] = {"type": "creative", "spec": json.loads(f["object_story_spec"])}
                return self._json(200, {"id": cid})
            if obj == "act_555001/ads":
                STATE["seq"] += 1
                aid = f"ad{STATE['seq']}"
                STATE["m_created"][aid] = {"type": "ad", "status": f["status"], "adset_id": f["adset_id"],
                                           "creative": json.loads(f["creative"])}
                return self._json(200, {"id": aid})
            if obj in STATE["m_objects"]:
                for k in ("status", "daily_budget"):
                    if k in f:
                        STATE["m_objects"][obj][k] = f[k]
                return self._json(200, {"success": True})
            return self._json(404, {"error": {"message": "Unsupported post request.", "code": 100}})
        if u.path.endswith(":mutate"):
            if self.headers.get("Authorization") != f"Bearer {ACCESS}":
                return self._json(401, [{"error": {"code": 401, "message": "Invalid credentials"}}])
            if READ_ONLY["google"]:
                return self._json(403, {"error": {"code": 403, "message": "The caller does not have permission",
                                                  "details": [{"errors": [{"message": "The user is not permitted to perform this action."}]}]}})
            service = u.path.rsplit("/", 1)[1].split(":")[0]
            ops = json.loads(body)["operations"]
            results = []
            for op in ops:
                if "create" in op and service == "campaignCriteria":
                    c = op["create"]
                    camp = c["campaign"].rsplit("/", 1)[1]
                    STATE["seq"] += 1
                    rn = f"customers/4445556666/campaignCriteria/{camp}~{STATE['seq']}"
                    STATE["g_negatives"][rn] = (camp, c["keyword"]["text"].lower())
                    results.append({"resourceName": rn})
                elif "create" in op and service == "adGroupAds":
                    c = op["create"]
                    assert c["status"] == "PAUSED"
                    STATE["seq"] += 1
                    rn = f"{c['adGroup'].replace('adGroups', 'adGroupAds')}~{STATE['seq']}"
                    STATE["g_ads"][rn] = c
                    results.append({"resourceName": rn})
                elif "remove" in op:
                    STATE["g_negatives"].pop(op["remove"], None) or STATE["g_ads"].pop(op["remove"], None)
                    results.append({"resourceName": op["remove"]})
                elif "update" in op and service == "campaigns":
                    cid = op["update"]["resourceName"].rsplit("/", 1)[1]
                    assert op["updateMask"] == "status"
                    STATE["g_campaigns"][cid]["status"] = op["update"]["status"]
                    results.append({"resourceName": op["update"]["resourceName"]})
                elif "update" in op and service == "campaignBudgets":
                    bid = op["update"]["resourceName"].rsplit("/", 1)[1]
                    assert int(op["update"]["amountMicros"]) % 10000 == 0
                    STATE["g_budgets"][bid]["micros"] = int(op["update"]["amountMicros"])
                    results.append({"resourceName": op["update"]["resourceName"]})
                else:
                    return self._json(400, [{"error": {"code": 400, "message": f"unsupported op {op}"}}])
            return self._json(200, {"results": results})
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
            cust = "customers/4445556666"
            if "FROM campaign_budget" in q:
                bid = q.split("campaignBudgets/")[1].split("'")[0]
                return self._json(200, [{"results": [{"campaignBudget": {"amountMicros": str(STATE["g_budgets"][bid]["micros"])}}]}])
            if "FROM campaign_criterion" in q:
                return self._json(200, [{"results": [
                    {"campaign": {"id": c}, "campaignCriterion": {"resourceName": rn, "keyword": {"text": t}}}
                    for rn, (c, t) in STATE["g_negatives"].items()
                    if "campaign.id = " not in q or q.split("campaign.id = ")[1].split()[0] == c]}])
            if "FROM ad_group_ad" in q:
                if "901" not in q:
                    return self._json(200, [])
                return self._json(200, [{"results": [
                    {"adGroup": {"resourceName": f"{cust}/adGroups/71", "name": "Invisalign general"},
                     "adGroupAd": {"ad": {"finalUrls": ["https://heightsortho.example/invisalign"],
                                          "responsiveSearchAd": {"headlines": [{"text": "Invisalign in Heights"}]}}},
                     "metrics": {"costMicros": "300000000"}}]}])
            if "FROM campaign WHERE campaign.id = " in q:
                cid = q.split("campaign.id = ")[1].split()[0]
                return self._json(200, [{"results": [{"campaign": {"status": STATE["g_campaigns"][cid]["status"]}}]}])
            if "FROM campaign WHERE campaign.status != 'REMOVED'" in q:
                return self._json(200, [{"results": [
                    {"campaign": {"id": cid, "resourceName": f"{cust}/campaigns/{cid}", "name": c["name"], "status": c["status"],
                                  "advertisingChannelType": c["channel"]},
                     "campaignBudget": {"resourceName": f"{cust}/campaignBudgets/{c['budget']}",
                                        "amountMicros": str(STATE["g_budgets"][c["budget"]]["micros"]),
                                        "explicitlyShared": STATE["g_budgets"][c["budget"]]["shared"]}}
                    for cid, c in STATE["g_campaigns"].items()]}])
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
