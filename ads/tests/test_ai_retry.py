import io
import json
import urllib.error

import pytest

from app import ai


def _busy():
    body = json.dumps({"error": {"message": "litellm.ServiceUnavailableError: This model is currently "
                                            "experiencing high demand."}}).encode()
    return urllib.error.HTTPError("http://x", 503, "Service Unavailable", {}, io.BytesIO(body))


class _OK:
    def __enter__(self):
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "## Summary\nAll good."}}]}).encode())

    def __exit__(self, *a):
        return False


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(ai, "AI_API_KEY", "k")
    monkeypatch.setattr(ai, "RETRY_WAITS", (0, 0))


def test_retries_through_a_brief_overload(fast, monkeypatch):
    calls = []

    def fake(req, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise _busy()
        return _OK()
    monkeypatch.setattr(ai.urllib.request, "urlopen", fake)
    assert ai.generate("fixes", "ctx") == "## Summary\nAll good."
    assert len(calls) == 3


def test_plain_message_when_still_busy(fast, monkeypatch):
    def fake(req, timeout):
        raise _busy()
    monkeypatch.setattr(ai.urllib.request, "urlopen", fake)
    with pytest.raises(ai.AIError, match="busy right now") as e:
        ai.generate("fixes", "ctx")
    assert "litellm" not in str(e.value)
