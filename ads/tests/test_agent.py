"""Postiz Agent brand context: channel -> practice matching, the key-protected endpoint, and the translator hook."""
import asyncio
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def hub(tmp_path, monkeypatch):
    for k, v in {"ADS_DB": str(tmp_path / "ads.db"), "ADS_PASSWORD": "test-password-1",
                 "ADS_DISABLE_SCHEDULER": "1", "AI_API_KEY": "master-key-1"}.items():
        monkeypatch.setenv(k, v)
    from app import agent, brand, db, main, postiz
    for m in (db, brand, postiz, agent, main):
        importlib.reload(m)
    db.init()
    brand.init()
    havens = db.add_practice("Havens Orthodontics")
    heights = db.add_practice("Heights Orthodontics")
    db.add_practice("Wesley Orthodontics")
    db.set_practice_social(heights, "pg-heights-1,pg-heights-2", "")
    brand.save(havens, {"voice": "Warm and upbeat", "offers": "Free consultation\n$500 off braces",
                        "never_say": "cheap", "website": "havensortho.com"})
    chans = {
        "c1": {"id": "c1", "provider": "facebook", "internal_id": "999", "name": "Havens", "customer": ""},
        "c2": {"id": "c2", "provider": "instagram", "internal_id": "pg-heights-2", "name": "heights_smiles", "customer": ""},
        "c3": {"id": "c3", "provider": "facebook", "internal_id": "1", "name": "Bandeen Family Dental", "customer": ""},
    }
    monkeypatch.setattr(postiz, "integrations_by_id", lambda ids: [chans[i] for i in ids if i in chans])
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c, agent, db


def test_channels_match_practices_by_page_id_then_name(hub):
    c, agent, db = hub
    out = agent.context(["c1", "c2", "c3"])
    assert [m["practice"] for m in out["channels"]] == ["Havens Orthodontics", "Heights Orthodontics", None]
    t = out["text"]
    assert "MCDP house rules" in t and "Practice: Havens Orthodontics" in t
    assert "Approved offers: Free consultation; $500 off braces" in t and "Never say: cheap" in t
    assert "Website: https://havensortho.com" in t
    assert "Practice: Heights Orthodontics" in t and "No brand kit saved" in t     # matched by its Instagram id
    assert "No practice is linked to: Bandeen Family Dental" in t
    assert "Wesley" not in t


def test_ambiguous_names_are_not_guessed():
    from app import agent
    ps = [{"id": 1, "name": "Smith Orthodontics Novi", "fb_page_id": "", "ig_account_id": ""},
          {"id": 2, "name": "Smith Orthodontics Troy", "fb_page_id": "", "ig_account_id": ""}]
    assert agent.match_practice({"name": "Smith Orthodontics"}, ps) is None
    assert agent.match_practice({"name": "Smith Ortho - Troy"}, ps)["id"] == 2
    assert agent.match_practice({"name": "Dental", "customer": ""}, ps) is None     # only generic words


def test_no_channels_selected(hub):
    _, agent, _ = hub
    assert "No channel is selected yet" in agent.context([])["text"]


def test_endpoint_needs_the_translator_key(hub):
    c, *_ = hub
    assert c.get("/internal/agent-context?ids=c1").status_code == 403
    assert c.get("/internal/agent-context?ids=c1", headers={"Authorization": "Bearer wrong"}).status_code == 403
    r = c.get("/internal/agent-context?ids=c1", headers={"Authorization": "Bearer master-key-1"})
    assert r.status_code == 200 and "Havens Orthodontics" in r.json()["text"]


def _load_hook(monkeypatch):
    fake = types.ModuleType("litellm.integrations.custom_logger")
    fake.CustomLogger = object
    for name in ("litellm", "litellm.integrations"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "litellm.integrations.custom_logger", fake)
    spec = importlib.util.spec_from_file_location("agent_hook", Path(__file__).parents[2] / "agent_hook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MSG = ('Make a free consult post\n[--integrations--]\nUse the following social media platforms: '
       + json.dumps([{"id": "c1", "platform": "facebook"}, {"id": "c2", "platform": "instagram"}]) + "\n[--integrations--]")


def test_hook_adds_brand_to_responses_and_chat_requests(monkeypatch):
    hook = _load_hook(monkeypatch)
    asked = []

    async def fake_fetch(ids):
        asked.append(ids)
        return "MCDP house rules for this chat\nPractice: Havens Orthodontics"
    monkeypatch.setattr(hook, "fetch_context", fake_fetch)
    run = lambda d: asyncio.run(hook.handler.async_pre_call_hook(None, None, d, "aresponses"))

    # Responses API (what the Postiz Agent uses): developer message gets the text appended
    d = run({"model": "gpt-5.2", "input": [{"role": "developer", "content": "You are an agent"},
                                           {"role": "user", "content": [{"type": "input_text", "text": MSG}]}]})
    assert asked[-1] == ["c1", "c2"]
    assert d["input"][0]["content"].startswith("You are an agent") and "Havens" in d["input"][0]["content"]
    # Chat Completions without a system message: one is added; a second pass doesn't add it twice
    d = run({"model": "gpt-5.2", "messages": [{"role": "user", "content": MSG}]})
    d = run(d)
    assert d["messages"][0]["role"] == "system" and sum("house rules" in str(m) for m in d["messages"]) == 1
    # Other models (the post editor's helper, the hub's own write-ups) are left alone
    d = run({"model": "gpt-4.1", "messages": [{"role": "user", "content": MSG}]})
    assert len(d["messages"]) == 1


def test_hook_never_blocks_the_agent(monkeypatch):
    hook = _load_hook(monkeypatch)

    async def down(ids):
        raise OSError("hub is down")
    monkeypatch.setattr(hook, "fetch_context", down)
    d = {"model": "gpt-5.2", "messages": [{"role": "user", "content": "hi"}]}
    assert asyncio.run(hook.handler.async_pre_call_hook(None, None, d, "acompletion")) == d
    assert hook.channel_ids([{"role": "user", "content": "[--integrations--] broken [ [--integrations--]"}]) == []
