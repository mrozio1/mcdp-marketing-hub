#!/bin/sh
# Run once the MCDP admin account exists: stops anyone else from registering.
# Invite teammates from inside Postiz afterwards.
set -eu
cd "$(dirname "$0")"
sed -i.bak 's/^DISABLE_REGISTRATION=.*/DISABLE_REGISTRATION=true/' .env && rm -f .env.bak
. ./profiles.sh
# shellcheck disable=SC2086
$DOCKER compose $PROFILE up -d postiz
echo "Public sign-ups are now disabled."
