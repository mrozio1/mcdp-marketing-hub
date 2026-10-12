#!/bin/sh
# shellcheck disable=SC2034  # DOCKER/PROFILE are used by the scripts that source this file
# Sourced by the other scripts: works out which optional services are switched on
# and which docker command to use. Sets $DOCKER and $PROFILE.
# (Uses if-blocks, not "a && b", so a missing setting never trips `set -e`.)
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then DOCKER="sudo docker"; fi
PROFILE=""
if grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+' .env 2>/dev/null; then PROFILE="$PROFILE --profile tunnel"; fi
if grep -qE '^AI_PROVIDER_KEY=.+' .env 2>/dev/null; then PROFILE="$PROFILE --profile ai"; fi
