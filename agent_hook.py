"""LiteLLM hook for the Postiz Agent (runs inside the postiz-ai container).

The Agent asks for model "gpt-5.2". Before each of its requests goes to the AI provider, this hook:
  1. reads the channels picked in the Agent sidebar from the latest user message
     ("[--integrations--] Use the following social media platforms: [...] [--integrations--]"),
  2. asks the MCDP hub (ads container) for those practices' brand kits and the house rules,
  3. adds them to the system instructions.
If the hub can't be reached the request goes through unchanged, so the Agent never stalls on it.
"""
import json
import os
import re
import time

from litellm.integrations.custom_logger import CustomLogger

HUB = os.environ.get("HUB_URL", "http://ads:8000").rstrip("/")
KEY = os.environ.get("LITELLM_MASTER_KEY", "")
AGENT_MODELS = {"gpt-5.2"}
BLOCK = re.compile(r"\[--integrations--\]([\s\S]*?)\[--integrations--\]")
MARK = "MCDP house rules for this chat"
_cache = {}


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def channel_ids(items):
    """Channel ids from the newest user message that lists them."""
    for m in reversed(items or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        found = BLOCK.findall(_text(m.get("content")))
        if not found:
            continue
        raw = found[-1]
        start, end = raw.find("["), raw.rfind("]")
        try:
            return [str(c["id"]) for c in json.loads(raw[start:end + 1]) if isinstance(c, dict) and c.get("id")]
        except (ValueError, TypeError):
            return []
    return []


def add_instructions(data, text):
    """Adds text to the system instructions of a Chat Completions or Responses request."""
    if "messages" in data and isinstance(data["messages"], list):
        items = data["messages"]
    elif isinstance(data.get("input"), list):
        items = data["input"]
    else:                                       # Responses API with a plain-string input
        data["instructions"] = ((data.get("instructions") or "") + "\n\n" + text).strip()
        return data
    if any(MARK in _text(m.get("content")) for m in items if isinstance(m, dict) and m.get("role") in ("system", "developer")):
        return data                             # already added (e.g. a retry or fallback)
    for m in items:
        if isinstance(m, dict) and m.get("role") in ("system", "developer"):
            c = m.get("content")
            if isinstance(c, str):
                m["content"] = c + "\n\n" + text
            elif isinstance(c, list):
                c.append({"type": "input_text" if "input" in data else "text", "text": text})
            else:
                m["content"] = text
            return data
    items.insert(0, {"role": "system", "content": text})
    return data


async def fetch_context(ids):
    k = ",".join(ids)
    hit = _cache.get(k)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    import httpx
    async with httpx.AsyncClient(timeout=4) as c:
        r = await c.get(f"{HUB}/internal/agent-context", params={"ids": k},
                        headers={"Authorization": f"Bearer {KEY}"})
        r.raise_for_status()
        text = r.json().get("text") or ""
    _cache[k] = (time.time(), text)
    return text


class AgentBrand(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        try:
            if data.get("model") not in AGENT_MODELS:
                return data
            items = data.get("messages") if isinstance(data.get("messages"), list) else data.get("input")
            text = await fetch_context(channel_ids(items if isinstance(items, list) else []))
            if text:
                add_instructions(data, text)
        except Exception as e:                  # never block the Agent over brand context
            print(f"agent_hook: skipped brand context ({e})")
        return data


handler = AgentBrand()
