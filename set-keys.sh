#!/bin/sh
# Save social-network keys into .env and restart Postiz so it picks them up.
# Usage: sh set-keys.sh FACEBOOK_APP_ID=123 FACEBOOK_APP_SECRET=abc
set -eu
cd "$(dirname "$0")"
[ -f .env ] || { echo "ERROR: run install.sh first (.env not found)." >&2; exit 1; }
[ $# -gt 0 ] || { echo "Usage: sh set-keys.sh NAME=value [NAME=value ...]" >&2; exit 1; }

for pair in "$@"; do
  name=${pair%%=*}
  value=${pair#*=}
  case "$name" in
    ""|*[!A-Z0-9_]*) echo "ERROR: '$name' is not a valid setting name." >&2; exit 1 ;;
  esac
  [ "$name" = "$pair" ] && { echo "ERROR: '$pair' is missing '=value'." >&2; exit 1; }
  case "$value" in *"|"*|*"\\"*) echo "ERROR: value for $name contains unsupported characters." >&2; exit 1 ;; esac
  if grep -qE "^${name}=" .env; then
    sed -i.bak "s|^${name}=.*|${name}=${value}|" .env && rm -f .env.bak
  else
    echo "${name}=${value}" >> .env
  fi
  echo "Saved ${name}"
done

. ./profiles.sh
# shellcheck disable=SC2086
$DOCKER compose $PROFILE up -d postiz
echo "Postiz restarted with the new keys (give it about a minute)."
