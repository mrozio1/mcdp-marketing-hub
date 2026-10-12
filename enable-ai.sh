#!/bin/sh
# Turn on Postiz's built-in AI using a provider of your choice (default: Google Gemini).
# Usage:
#   sh enable-ai.sh YOUR_PROVIDER_KEY                         # Gemini (Google AI Studio key)
#   sh enable-ai.sh YOUR_KEY anthropic/claude-sonnet-4-5      # any LiteLLM provider/model
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

[ $# -ge 1 ] || { echo "Usage: sh enable-ai.sh PROVIDER_KEY [provider/model]  |  sh enable-ai.sh off" >&2; exit 1; }

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
[ -n "${2:-}" ] && setvar AI_TEXT_MODEL "$2"
[ -n "${3:-}" ] && setvar AI_IMAGE_MODEL "$3"

master=$(grep -E '^LITELLM_MASTER_KEY=' .env | cut -d= -f2- || true)
if [ -z "$master" ]; then
  master="sk-$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  setvar LITELLM_MASTER_KEY "$master"
fi
# Postiz talks "OpenAI" to the local translator, which holds the real provider key.
setvar OPENAI_API_KEY "$master"
setvar OPENAI_BASE_URL "http://litellm:4000/v1"

. ./profiles.sh
echo "Starting the AI translator and restarting Postiz..."
# shellcheck disable=SC2086
$DOCKER compose $PROFILE up -d litellm postiz

printf "Checking the AI connection"
for _ in $(seq 1 24); do
  out=$($DOCKER exec postiz-ai python -c "
import json, urllib.request
req = urllib.request.Request('http://localhost:4000/v1/chat/completions',
  data=json.dumps({'model':'gpt-4.1','messages':[{'role':'user','content':'Reply with the single word: ready'}],'max_tokens':5}).encode(),
  headers={'Authorization':'Bearer $master','Content-Type':'application/json'})
try:
  print(json.load(urllib.request.urlopen(req, timeout=60))['choices'][0]['message']['content'].strip())
except urllib.error.HTTPError as e:
  print('HTTPERR', e.code, e.read().decode()[:300])
except Exception as e:
  print('WAIT', e)
" 2>/dev/null || echo "WAIT")
  case "$out" in
    WAIT*) printf "."; sleep 5 ;;
    HTTPERR*) echo; echo "The AI provider refused the request:"; echo "  $out"; echo "Check the key (and model name), then re-run this script."; exit 1 ;;
    *) echo; echo "AI is working (test reply: $out). Refresh Postiz to use it."; exit 0 ;;
  esac
done
echo; echo "The AI translator didn't come up in 2 minutes. Check: $DOCKER logs postiz-ai --tail 50"; exit 1
