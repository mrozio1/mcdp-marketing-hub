"""
Tiny OpenAI-compatible image endpoint backed by Cloudflare Workers AI (free daily
allowance). LiteLLM forwards Postiz's image requests here:

    POST /v1/images/generations  {"prompt": "...", "size": "1024x1536", ...}
      -> {"created": ..., "data": [{"b64_json": "..."}]}

Settings (env): CF_ACCOUNT_ID, CF_API_TOKEN, CF_IMAGE_MODEL (default flux-1-schnell),
CF_IMAGE_STEPS (default 4, max 8). Standard library only.
Only reachable inside the Docker network; never exposed to the internet.
"""
import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "").strip()
TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
MODEL = os.environ.get("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell").strip()
STEPS = max(1, min(8, int(os.environ.get("CF_IMAGE_STEPS", "4") or 4)))
API = os.environ.get("CF_API_BASE", "https://api.cloudflare.com/client/v4").rstrip("/")


def generate(prompt):
    """Return (base64_image, None) or (None, (http_status, message))."""
    if not ACCOUNT or not TOKEN:
        return None, (500, "Cloudflare image settings are missing (CF_ACCOUNT_ID / CF_API_TOKEN).")
    body = json.dumps({"prompt": prompt[:2048], "steps": STEPS}).encode()
    req = urllib.request.Request(
        f"{API}/accounts/{ACCOUNT}/ai/run/{MODEL}",
        data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        try:
            errs = json.loads(detail).get("errors") or []
            detail = "; ".join(str(x.get("message", x)) for x in errs) or detail
        except ValueError:
            pass
        return None, (e.code, f"Cloudflare Workers AI: {detail}")
    except Exception as e:  # network trouble, timeouts
        return None, (502, f"Could not reach Cloudflare Workers AI: {e}")

    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    image = result.get("image") if isinstance(result, dict) else None
    if not image:
        return None, (502, "Cloudflare returned no image: " + json.dumps(payload)[:300])
    return image, None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("cf-image:", fmt % args, flush=True)

    def _send(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, message):
        self._send(status, {"error": {"message": message, "type": "cloudflare_error", "code": status}})

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/v1/health"):
            self._send(200, {"ok": True, "configured": bool(ACCOUNT and TOKEN), "model": MODEL})
        else:
            self._error(404, "not found")

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/images/generations"):
            return self._error(404, "Only /v1/images/generations is supported.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._error(400, "Request body must be JSON.")
        prompt = (req.get("prompt") or "").strip()
        if not prompt:
            return self._error(400, "A prompt is required.")
        n = max(1, min(4, int(req.get("n") or 1)))
        images = []
        for _ in range(n):
            image, err = generate(prompt)
            if err:
                return self._error(*err)
            images.append({"b64_json": image, "revised_prompt": prompt})
        self._send(200, {"created": int(time.time()), "data": images})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"cf-image: listening on :{port}, model {MODEL}, configured={bool(ACCOUNT and TOKEN)}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
