#!/bin/sh
# Give the Postiz Agent (social.mcdp.com/agents) its own, stronger AI model.
# Everything else (post-editor helper, hub write-ups, images) keeps using the main AI model.
# If the Agent's model ever fails, it falls back to the main model automatically.
# Usage:
#   sh enable-agent.sh sk-ant-...            # Claude (Anthropic API key)
#   sh enable-agent.sh sk-...                # OpenAI API key
#   sh enable-agent.sh AIza...               # a (paid) Google AI Studio key: newest Gemini Pro
#   sh enable-agent.sh gemini-pro            # the same, with the Gemini key already saved here
#   sh enable-agent.sh KEY provider/model    # any LiteLLM provider/model
#   sh enable-agent.sh off                   # back to the main AI model
#   sh enable-agent.sh check                 # just test it
set -eu
cd "$(dirname "$0")"
[ -f .env ] || { echo "ERROR: run install.sh first (.env not found)." >&2; exit 1; }
getvar() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
delvar() { sed -i.bak "/^$1=/d" .env && rm -f .env.bak; }
setvar() {
  case "$2" in *"|"*|*" "*) echo "ERROR: value for $1 can't contain spaces or '|'." >&2; exit 1 ;; esac
  if grep -qE "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else echo "$1=$2" >> .env; fi
}
. ./profiles.sh
. ./agent-check.sh
[ -n "$(getvar AI_PROVIDER_KEY)" ] || { echo "Turn on AI first: sh enable-ai.sh YOUR_KEY" >&2; exit 1; }

case "${1:-}" in
  "") echo "Usage: sh enable-agent.sh KEY [provider/model] | off | check" >&2; exit 1 ;;
  check) agent_check; exit 0 ;;
  off) delvar AGENT_MODEL; delvar AGENT_KEY ;;
  *)
    key="$1"; model="${2:-}"
    if [ "$key" = gemini-pro ]; then key="$(getvar AI_PROVIDER_KEY)"; fi
    if [ -z "$model" ]; then
      case "$key" in
        sk-ant-*) model="anthropic/claude-sonnet-5-5" ;;
        sk-*) model="openai/gpt-5.2" ;;
        AIza*)
          model=$($DOCKER exec -e GKEY="$key" postiz-ai python -c "
import json, os, re, urllib.request
url = 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&key=' + os.environ['GKEY']
ms = [m['name'].split('/')[-1] for m in json.load(urllib.request.urlopen(url, timeout=30)).get('models', [])
      if 'generateContent' in m.get('supportedGenerationMethods', [])]
def ver(n):
    v = re.search(r'(\d+(?:\.\d+)?)', n); return float(v.group(1)) if v else 0
skip = ('image', 'tts', 'live', 'audio', 'embedding', 'exp', 'preview', 'thinking', 'robotics', 'computer')
pro = sorted([n for n in ms if '-pro' in n and not any(s in n for s in skip)], key=ver, reverse=True)
print('gemini/' + pro[0] if pro else 'gemini/gemini-2.5-pro')
" 2>/dev/null || echo gemini/gemini-2.5-pro) ;;
        *) echo "ERROR: can't tell which provider that key is for; add the model, e.g. sh enable-agent.sh KEY anthropic/claude-sonnet-5-5" >&2; exit 1 ;;
      esac
    fi
    setvar AGENT_KEY "$key"
    setvar AGENT_MODEL "$model"
    ;;
esac
# shellcheck disable=SC2086
$DOCKER compose $PROFILE up -d litellm >/dev/null
echo "Postiz Agent model: $(getvar AGENT_MODEL | grep . || echo "main AI model ($(getvar AI_TEXT_MODEL))")"
sleep 5
agent_check
