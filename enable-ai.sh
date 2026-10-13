#!/bin/sh
# Turn on Postiz's built-in AI using a provider of your choice (default: Google Gemini).
# Usage:
#   sh enable-ai.sh YOUR_PROVIDER_KEY                         # Gemini (Google AI Studio key)
#   sh enable-ai.sh YOUR_KEY anthropic/claude-sonnet-4-5      # any LiteLLM provider/model
#   sh enable-ai.sh                                           # re-run with the saved key
#   sh enable-ai.sh off                                       # turn AI off again
set -eu
cd "$(dirname "$0")"
[ -f .env ] || { echo "ERROR: run install.sh first (.env not found)." >&2; exit 1; }

delvar() { sed -i.bak "/^$1=/d" .env && rm -f .env.bak; }

setvar() {
  case "$2" in *"|"*) echo "ERROR: value for $1 contains '|'." >&2; exit 1 ;; esac
  if grep -qE "^$1=" .env; then
    sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else
    echo "$1=$2" >> .env
  fi
}

saved_key=$(grep -E '^AI_PROVIDER_KEY=' .env | tail -1 | cut -d= -f2- || true)
# No key given (or a blank one): reuse the key already saved on this server.
if [ -z "${1:-}" ]; then
  if [ -z "$saved_key" ]; then
    echo "Usage: sh enable-ai.sh PROVIDER_KEY [provider/model]  |  sh enable-ai.sh off" >&2
    echo "No AI key is saved yet, so paste your key after the command." >&2
    exit 1
  fi
  echo "Using the AI key already saved on this server."
  set -- "$saved_key" "${2:-}" "${3:-}"
fi

if [ "$1" = off ]; then
  # Remove (not blank) these so Postiz falls back to its normal defaults.
  delvar AI_PROVIDER_KEY
  delvar OPENAI_API_KEY
  delvar OPENAI_BASE_URL
  . ./profiles.sh
  $DOCKER rm -f postiz-ai >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  $DOCKER compose $PROFILE up -d postiz
  echo "AI turned off."
  exit 0
fi

setvar AI_PROVIDER_KEY "$1"
# New text model chosen: drop the old backup so a matching one is picked below.
if [ -n "${2:-}" ]; then setvar AI_TEXT_MODEL "$2"; delvar AI_TEXT_FALLBACK_MODEL; fi
if [ -n "${3:-}" ]; then setvar AI_IMAGE_MODEL "$3"; fi

master=$(grep -E '^LITELLM_MASTER_KEY=' .env | tail -1 | cut -d= -f2- || true)
if [ -z "$master" ]; then
  master="sk-$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  setvar LITELLM_MASTER_KEY "$master"
fi
# Postiz talks "OpenAI" to the local translator, which holds the real provider key.
setvar OPENAI_API_KEY "$master"
setvar OPENAI_BASE_URL "http://litellm:4000/v1"

. ./profiles.sh

start_ai() {
  # shellcheck disable=SC2086
  $DOCKER compose $PROFILE up -d litellm postiz
}

# Ask the translator for a tiny reply. Prints the reply, "HTTPERR <code> <body>", or "WAIT".
test_ai() {
  $DOCKER exec postiz-ai python -c "
import json, urllib.request
req = urllib.request.Request('http://localhost:4000/v1/chat/completions',
  data=json.dumps({'model':'gpt-4.1','messages':[{'role':'user','content':'Reply with the single word: ready'}],'max_tokens':200}).encode(),
  headers={'Authorization':'Bearer $master','Content-Type':'application/json'})
try:
  msg = json.load(urllib.request.urlopen(req, timeout=90))['choices'][0]['message']
  print((msg.get('content') or 'ok').strip().splitlines()[0][:60])
except urllib.error.HTTPError as e:
  print('HTTPERR', e.code, e.read().decode()[:400].replace(chr(10), ' '))
except Exception as e:
  print('WAIT', e)
" 2>/dev/null || echo "WAIT"
}

# For Gemini: ask Google which models this key can use; print the newest
# general-purpose "flash" text model and image model as "text image".
pick_gemini_models() {
  $DOCKER exec -e GKEY="$(grep -E '^AI_PROVIDER_KEY=' .env | tail -1 | cut -d= -f2-)" postiz-ai python -c "
import json, os, re, urllib.request
url = 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&key=' + os.environ['GKEY']
models = json.load(urllib.request.urlopen(url, timeout=30)).get('models', [])
def ver(n):
    m = re.search(r'(\d+(?:\.\d+)?)', n); return float(m.group(1)) if m else 0
gen = [m['name'].split('/')[-1] for m in models if 'generateContent' in m.get('supportedGenerationMethods', [])]
skip = ('lite', 'image', 'tts', 'live', 'audio', 'embedding', 'exp', 'preview', 'thinking', 'robotics', 'computer')
text = sorted([n for n in gen if 'flash' in n and not any(s in n for s in skip)], key=ver, reverse=True)
if not text:
    text = sorted([n for n in gen if 'flash' in n and 'image' not in n and 'tts' not in n], key=ver, reverse=True)
img = sorted([n for n in gen if 'flash' in n and 'image' in n and 'preview' not in n], key=ver, reverse=True) \
   or sorted([n for n in gen if 'image' in n], key=ver, reverse=True)
print((text[0] if text else '') + ' ' + (img[0] if img else ''))
" 2>/dev/null || true
}

wait_for_ai() {
  printf "Checking the AI connection"
  for _ in $(seq 1 24); do
    out=$(test_ai)
    case "$out" in
      WAIT*) printf "."; sleep 5 ;;
      *) echo; return 0 ;;
    esac
  done
  echo; out="TIMEOUT"
}

echo "Starting the AI translator and restarting Postiz..."
start_ai
wait_for_ai

# Gemini model retired or renamed? Find the current one automatically and retry once.
case "$out" in
  HTTPERR\ 404*|*NotFound*|*"no longer available"*|*"not found"*)
    if grep -qE '^AI_TEXT_MODEL=gemini/' .env || ! grep -qE '^AI_TEXT_MODEL=.+' .env; then
      echo "That Gemini model isn't available to this key; asking Google which models are..."
      picked=$(pick_gemini_models)
      text_m=${picked%% *}; img_m=${picked#* }
      if [ -n "$text_m" ]; then
        echo "Switching to gemini/$text_m"
        setvar AI_TEXT_MODEL "gemini/$text_m"
        delvar AI_TEXT_FALLBACK_MODEL
        # Leave images alone if they run through Cloudflare (enable-cf-images.sh).
        if [ -n "$img_m" ] && [ "$img_m" != "$picked" ] && ! grep -qE '^CF_API_TOKEN=.+' .env; then setvar AI_IMAGE_MODEL "gemini/$img_m"; fi
        start_ai
        wait_for_ai
      fi
    fi
    ;;
esac

case "$out" in
  TIMEOUT) echo "The AI translator didn't come up in 2 minutes. Check: $DOCKER logs postiz-ai --tail 50"; exit 1 ;;
  HTTPERR*) echo "The AI provider refused the request:"; echo "  $out"; echo "Check the key (and model name), then re-run this script."; exit 1 ;;
  *)
    getvar() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
    . ./ai-fallback.sh
    if [ -z "$(getvar AI_TEXT_FALLBACK_MODEL)" ]; then
      set_ai_fallback
      # shellcheck disable=SC2086
      [ -z "$(getvar AI_TEXT_FALLBACK_MODEL)" ] || $DOCKER compose $PROFILE up -d litellm >/dev/null
    fi
    echo "AI is working using $(grep -E '^AI_TEXT_MODEL=' .env | tail -1 | cut -d= -f2-) (test reply: $out)."; echo "Refresh Postiz to use it."; exit 0 ;;
esac
