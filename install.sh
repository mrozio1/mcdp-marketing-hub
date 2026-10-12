#!/usr/bin/env bash
# MCDP Marketing Hub installer.
# Usage: PUBLIC_URL=https://social.mcdp.example CLOUDFLARE_TUNNEL_TOKEN=xxx ./install.sh
# Safe to re-run: existing secrets in .env are kept.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "Docker is not installed. See https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (docker compose ...)."

mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "$mem_kb" -gt 0 ] && [ "$mem_kb" -lt 3500000 ]; then
  echo "WARNING: this machine has $((mem_kb/1024)) MB RAM. Postiz + Temporal want ~4 GB."
fi

rand() { openssl rand -hex "${1:-24}" 2>/dev/null || head -c "${1:-24}" /dev/urandom | od -An -tx1 | tr -d ' \n'; }

if [ ! -f .env ]; then
  say "Creating .env"
  cp .env.example .env
  chmod 600 .env
fi

set_var() {  # set_var KEY VALUE  (only if currently blank, unless FORCE=1)
  local key=$1 val=$2 cur
  cur=$(grep -E "^${key}=" .env | head -1 | cut -d= -f2- || true)
  if [ -z "$cur" ] || [ "${FORCE:-0}" = 1 ]; then
    if grep -qE "^${key}=" .env; then
      sed -i.bak "s|^${key}=.*|${key}=${val}|" .env && rm -f .env.bak
    else
      echo "${key}=${val}" >> .env
    fi
  fi
}

set_var JWT_SECRET "$(rand 32)"
set_var POSTGRES_PASSWORD "$(rand 16)"
set_var TEMPORAL_DB_PASSWORD "$(rand 16)"
[ -n "${PUBLIC_URL:-}" ] && FORCE=1 set_var PUBLIC_URL "${PUBLIC_URL%/}"
[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ] && FORCE=1 set_var CLOUDFLARE_TUNNEL_TOKEN "$CLOUDFLARE_TUNNEL_TOKEN"

# No public domain yet: serve on the home network at http://<this-server-ip>:<port>
# so it can be tested from any computer on the LAN before the tunnel exists.
if grep -qE '^PUBLIC_URL=(https://social\.example\.com)?$' .env; then
  lan_ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src") print $(i+1)}' | head -1 || true)
  [ -z "$lan_ip" ] && lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
  [ -z "$lan_ip" ] && die "Could not detect this server's IP. Re-run with PUBLIC_URL=http://<server-ip>:4007 ./install.sh"
  port=$(grep -E '^LOCAL_PORT=' .env | cut -d= -f2); port=${port:-4007}
  FORCE=1 set_var PUBLIC_URL "http://${lan_ip}:${port}"
  FORCE=1 set_var LOCAL_BIND "0.0.0.0"
  echo "No public domain given: using the home-network address http://${lan_ip}:${port}"
fi

profiles=()
if grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+' .env; then
  profiles=(--profile tunnel)
fi

say "Pulling images (first run takes a few minutes)"
docker compose "${profiles[@]}" pull

say "Starting the stack"
docker compose "${profiles[@]}" up -d

say "Waiting for Postiz to answer"
port=$(grep -E '^LOCAL_PORT=' .env | cut -d= -f2); port=${port:-4007}
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "http://127.0.0.1:${port}" 2>/dev/null; then
    say "Postiz is up: $(grep -E '^PUBLIC_URL=' .env | cut -d= -f2)"
    echo "Next: open it, create the first admin account, then run ./lock-signups.sh"
    exit 0
  fi
  sleep 5
done
die "Postiz did not respond after 5 minutes. Check: docker compose logs postiz --tail 100"
