#!/bin/sh
# MCDP Marketing Hub installer. Plain POSIX sh (works on NAS boxes without bash).
# Usage:   ./install.sh
#          PUBLIC_URL=https://social.example.com CLOUDFLARE_TUNNEL_TOKEN=xxx ./install.sh
# Safe to re-run: existing secrets in .env are kept.
set -eu
cd "$(dirname "$0")"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "Docker is not installed."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (docker compose ...)."

# Use sudo for docker if this user can't talk to the Docker daemon directly.
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
  else
    die "This user cannot reach Docker. Log in as an administrator (or root) and re-run."
  fi
fi

mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${mem_kb:-0}" -gt 0 ] && [ "$mem_kb" -lt 3500000 ]; then
  echo "WARNING: this machine has $((mem_kb / 1024)) MB RAM. Postiz + Temporal want ~4 GB."
fi

rand() {
  openssl rand -hex "$1" 2>/dev/null || head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'
}

getvar() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }

# setvar KEY VALUE [force] — only fills blank values unless "force" is given.
setvar() {
  if [ -z "$(getvar "$1")" ] || [ "${3:-}" = force ]; then
    if grep -qE "^$1=" .env; then
      sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
    else
      echo "$1=$2" >> .env
    fi
  fi
}

if [ ! -f .env ]; then
  say "Creating .env"
  cp .env.example .env
  chmod 600 .env
fi

setvar JWT_SECRET "$(rand 32)"
setvar POSTGRES_PASSWORD "$(rand 16)"
setvar TEMPORAL_DB_PASSWORD "$(rand 16)"
if [ -n "${PUBLIC_URL:-}" ]; then setvar PUBLIC_URL "${PUBLIC_URL%/}" force; fi
if [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then setvar CLOUDFLARE_TUNNEL_TOKEN "$CLOUDFLARE_TUNNEL_TOKEN" force; fi

port=$(getvar LOCAL_PORT); port=${port:-4007}

# No public domain yet: serve on the home network at http://<this-server-ip>:<port>
# so it can be tested from any computer on the LAN before the tunnel exists.
case "$(getvar PUBLIC_URL)" in
  ""|https://social.example.com)
    lan_ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src") print $(i+1)}' | head -1 || true)
    [ -n "$lan_ip" ] || lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
    [ -n "$lan_ip" ] || die "Could not detect this server's IP. Re-run with PUBLIC_URL=http://<server-ip>:$port ./install.sh"
    setvar PUBLIC_URL "http://${lan_ip}:${port}" force
    setvar LOCAL_BIND "0.0.0.0" force
    echo "No public domain given: using the home-network address http://${lan_ip}:${port}"
    ;;
esac

. ./profiles.sh

say "Pulling images (first run takes a few minutes)"
# shellcheck disable=SC2086
$DOCKER compose $PROFILE pull

say "Starting the stack"
# shellcheck disable=SC2086
$DOCKER compose $PROFILE up -d

say "Waiting for Postiz to answer (first start can take 2-3 minutes)"
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "http://127.0.0.1:${port}" 2>/dev/null; then
    say "Postiz is up: $(getvar PUBLIC_URL)"
    echo "Next: open it, create the first admin account, then run ./lock-signups.sh"
    exit 0
  fi
  sleep 5
done
die "Postiz did not respond after 5 minutes. Check: $DOCKER compose logs postiz --tail 100"
