#!/bin/sh
# Make Postiz's AI images (incl. the design editor's AI image tab) use
# Cloudflare Workers AI's free daily allowance instead of the text provider.
# Usage:
#   sh enable-cf-images.sh CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN
#   sh enable-cf-images.sh off        # go back to the text provider for images
set -eu
cd "$(dirname "$0")"
[ -f .env ] || { echo "ERROR: run install.sh first (.env not found)." >&2; exit 1; }
grep -qE '^AI_PROVIDER_KEY=.+' .env || { echo "Turn on AI first: sh enable-ai.sh YOUR_KEY" >&2; exit 1; }

getvar() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
delvar() { sed -i.bak "/^$1=/d" .env && rm -f .env.bak; }
setvar() {
  case "$2" in *"|"*) echo "ERROR: value for $1 contains '|'." >&2; exit 1 ;; esac
  if grep -qE "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else echo "$1=$2" >> .env; fi
}

if [ "${1:-}" = off ]; then
  before=$(getvar AI_IMAGE_MODEL_BEFORE_CF)
  delvar CF_ACCOUNT_ID; delvar CF_API_TOKEN; delvar AI_IMAGE_KEY; delvar AI_IMAGE_API_BASE; delvar AI_IMAGE_MODEL_BEFORE_CF
  if [ -n "$before" ]; then setvar AI_IMAGE_MODEL "$before"; fi
  . ./profiles.sh
  $DOCKER rm -f postiz-cf-image >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  $DOCKER compose $PROFILE up -d litellm
  echo "Cloudflare images turned off; images use the text provider again."
  exit 0
fi

[ $# -eq 2 ] && [ -n "$1" ] && [ -n "$2" ] || {
  echo "Usage: sh enable-cf-images.sh CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN" >&2; exit 1; }
case "$1" in *[!0-9a-fA-F]*) echo "ERROR: the Account ID should be 32 letters/numbers (0-9, a-f). Check you didn't swap the two values." >&2; exit 1 ;; esac

current=$(getvar AI_IMAGE_MODEL)
if [ -z "$(getvar AI_IMAGE_MODEL_BEFORE_CF)" ] && [ "$current" != "openai/flux-1-schnell" ]; then
  setvar AI_IMAGE_MODEL_BEFORE_CF "${current:-gemini/gemini-2.5-flash-image}"
fi
setvar CF_ACCOUNT_ID "$1"
setvar CF_API_TOKEN "$2"
# The translator sends image requests to the local cf-image adapter.
setvar AI_IMAGE_MODEL "openai/flux-1-schnell"
setvar AI_IMAGE_API_BASE "http://cf-image:8080/v1"
setvar AI_IMAGE_KEY "local-cf-image"

. ./profiles.sh
echo "Starting the Cloudflare image adapter and updating the AI translator..."
# shellcheck disable=SC2086
$DOCKER compose $PROFILE up -d cf-image litellm
echo "Waiting for the translator to restart..."
sleep 20
sh ./test-ai.sh
