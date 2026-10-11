#!/usr/bin/env bash
# Run once the MCDP admin account exists: stops anyone else from registering.
# Invite teammates from inside Postiz afterwards.
set -euo pipefail
cd "$(dirname "$0")"
sed -i.bak 's/^DISABLE_REGISTRATION=.*/DISABLE_REGISTRATION=true/' .env && rm -f .env.bak
profiles=(); grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+' .env && profiles=(--profile tunnel)
docker compose "${profiles[@]}" up -d postiz
echo "Public sign-ups are now disabled."
