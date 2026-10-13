#!/bin/sh
# Turn on the Ads page (per-practice Google/Meta export analysis + AI write-ups).
# Usage:
#   sh enable-ads.sh                 # first time: creates a team password and starts it
#   sh enable-ads.sh NEW_PASSWORD    # change the team password
#   sh enable-ads.sh off             # stop it (data is kept)
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
rand() { openssl rand -hex "$1" 2>/dev/null || head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }

if [ "${1:-}" = off ]; then
  delvar ADS_PASSWORD
  . ./profiles.sh
  $DOCKER rm -f postiz-ads >/dev/null 2>&1 || true
  echo "Ads page stopped. Its data is kept; run 'sh enable-ads.sh' to start it again."
  exit 0
fi

if [ -n "${1:-}" ]; then
  [ "${#1}" -ge 10 ] || { echo "ERROR: use a password of at least 10 characters." >&2; exit 1; }
  setvar ADS_PASSWORD "$1"
elif [ -z "$(getvar ADS_PASSWORD)" ]; then
  # Readable but strong: e.g. mcdp-3f9a1c-71c2d0-b8e04f
  pw="mcdp-$(rand 3)-$(rand 3)-$(rand 3)"
  setvar ADS_PASSWORD "$pw"
fi
[ -n "$(getvar ADS_SECRET)" ] || setvar ADS_SECRET "$(rand 32)"

. ./profiles.sh
echo "Building and starting the Ads page (first time takes a few minutes)..."
# shellcheck disable=SC2086
$DOCKER compose $PROFILE up -d --build

port=$(getvar ADS_PORT); port=${port:-4010}
printf "Waiting for it to answer"
for _ in $(seq 1 40); do
  if curl -fsS -o /dev/null "http://127.0.0.1:${port}/health" 2>/dev/null; then
    echo
    echo "The Ads page is running."
    echo "Team password: $(getvar ADS_PASSWORD)"
    if [ -z "$(getvar LITELLM_MASTER_KEY)" ]; then
      echo "Note: AI write-ups need AI turned on first: sh enable-ai.sh YOUR_KEY (then re-run this script)."
    fi
    exit 0
  fi
  printf "."; sleep 3
done
echo; echo "It didn't answer in 2 minutes. Check: $DOCKER logs postiz-ads --tail 50"; exit 1
