"""Stock search: telling a wrong key apart from a firewall, and tidying pasted keys."""
import io
import urllib.error

from app import stock


def _raise(code, body):
    def f(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body.encode()))
    return f


def test_firewall_is_not_called_a_bad_key(monkeypatch):
    monkeypatch.setattr(stock.urllib.request, "urlopen", _raise(403, "<html><title>Attention Required! | Cloudflare</title>error code: 1010</html>"))
    monkeypatch.setattr(stock, "_curl", lambda *a, **k: None)
    try:
        stock._get("https://pixabay.com/api/videos/?key=x", service="Pixabay")
    except stock.StockError as e:
        assert "firewall" in str(e) and "isn't the key" in str(e)
    else:
        raise AssertionError("no error")


def test_curl_is_tried_when_python_is_turned_away(monkeypatch):
    monkeypatch.setattr(stock.urllib.request, "urlopen", _raise(403, ""))
    monkeypatch.setattr(stock, "_curl", lambda url, timeout, out_path=None, headers=None: (200, '{"hits": [], "totalHits": 0}'))
    assert stock._get("https://pixabay.com/api/videos/?key=x", service="Pixabay") == {"hits": [], "totalHits": 0}


def test_wrong_key(monkeypatch):
    monkeypatch.setattr(stock.urllib.request, "urlopen", _raise(400, "[ERROR 400] Invalid or missing API key"))
    try:
        stock._get("https://pixabay.com/api/videos/?key=x", service="Pixabay")
    except stock.StockError as e:
        assert "key isn't right" in str(e)


def test_pasted_keys_are_tidied():
    assert stock._tidy_key("  '12345-abcdef'  ") == "12345-abcdef"
    assert stock._tidy_key("https://pixabay.com/api/?key=12345-abcdef&q=yellow+flowers") == "12345-abcdef"
    assert stock._tidy_key("   ") is None


def test_hits_from_the_browser_only_point_at_pixabay():
    raw = {"id": 42, "tags": "fireplace, cozy", "user": "ann", "pageURL": "https://pixabay.com/videos/fireplace-42/", "duration": 8,
           "videos": {"large": {"url": "https://cdn.pixabay.com/video/x_large.mp4", "width": 1920, "height": 1080},
                      "tiny": {"url": "https://cdn.pixabay.com/video/x_tiny.mp4", "width": 640, "height": 360, "thumbnail": "https://cdn.pixabay.com/t.jpg"}}}
    h = stock.pixabay_hit(raw)
    assert h["id"] == 42 and h["file"]["url"].startswith("https://cdn.pixabay.com/") and h["file"]["w"] == 1920
    evil = {**raw, "videos": {"large": {"url": "http://169.254.169.254/latest", "width": 100, "height": 100}}}
    assert stock.pixabay_hit(evil) is None
    evil2 = {**raw, "videos": {"large": {"url": "https://cdn.pixabay.com.evil.example/x.mp4", "width": 100, "height": 100}}}
    assert stock.pixabay_hit(evil2) is None
