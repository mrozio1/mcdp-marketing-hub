#!/bin/sh
# Optional: the easy way is the Ads page itself (Connections: paste the Meta token,
# upload the Google service account key). This script stores keys on the server instead.
# Usage:
#   sh connect-ads.sh meta SYSTEM_USER_TOKEN
#   sh connect-ads.sh google-oauth OAUTH_CLIENT_ID OAUTH_CLIENT_SECRET [MANAGER_ACCOUNT_ID]
#   sh connect-ads.sh meta off | google-oauth off
set -eu
cd "$(dirname "$0")"
[ -f .env ] || { echo "ERROR: run install.sh first (.env not found)." >&2; exit 1; }
grep -qE '^ADS_PASSWORD=.+' .env || { echo "Turn on the Ads page first: sh enable-ads.sh" >&2; exit 1; }

getvar() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
delvar() { sed -i.bak "/^$1=/d" .env && rm -f .env.bak; }
setvar() {
  case "$2" in *"|"*|*"&"*|*" "*|*"\\"*) echo "ERROR: the value for $1 contains a character it shouldn't (space, |, & or \\). Check the copy-paste." >&2; exit 1 ;; esac
  if grep -qE "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else echo "$1=$2" >> .env; fi
}
usage() { sed -n '5,8p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1; }

# Public address of the Ads page (needed for Google sign-in): default to ads.<same domain as Postiz>.
if [ -z "$(getvar ADS_PUBLIC_URL)" ]; then
  pub=$(getvar PUBLIC_URL)
  case "$pub" in
    https://*.*.*) setvar ADS_PUBLIC_URL "https://ads.${pub#https://*.}" ;;
    https://*) setvar ADS_PUBLIC_URL "https://ads.${pub#https://}" ;;
  esac
fi

case "${1:-}" in
  meta)
    [ -n "${2:-}" ] || usage
    if [ "$2" = off ]; then delvar META_ACCESS_TOKEN; echo "Meta connection removed."
    else
      [ "${#2}" -ge 40 ] || { echo "ERROR: that doesn't look like a full Meta token (they're 150+ characters)." >&2; exit 1; }
      setvar META_ACCESS_TOKEN "$2"; echo "Saved the Meta token."
    fi ;;
  google-oauth)
    if [ "${2:-}" = off ]; then
      for k in GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET GOOGLE_ADS_LOGIN_CUSTOMER_ID GOOGLE_ADS_DEVELOPER_TOKEN; do delvar "$k"; done
      echo "Google sign-in keys removed."
    else
      [ -n "${2:-}" ] && [ -n "${3:-}" ] || usage
      case "$2" in *.apps.googleusercontent.com) ;; *) echo "ERROR: the OAuth client ID should end in .apps.googleusercontent.com. Order: client ID, then client secret." >&2; exit 1 ;; esac
      setvar GOOGLE_OAUTH_CLIENT_ID "$2"
      setvar GOOGLE_OAUTH_CLIENT_SECRET "$3"
      if [ -n "${4:-}" ]; then setvar GOOGLE_ADS_LOGIN_CUSTOMER_ID "$(echo "$4" | tr -cd '0-9')"; fi
      echo "Saved the Google sign-in keys."
    fi ;;
  *) usage ;;
esac

. ./profiles.sh
echo "Restarting the Ads page..."
# shellcheck disable=SC2086
$DOCKER compose $PROFILE up -d ads >/dev/null
url=$(getvar ADS_PUBLIC_URL)
echo "Done. Open ${url:-the Ads page}/connections to check the connection."
if [ "${1}" = google-oauth ] && [ "${2}" != off ]; then
  echo "Then click 'Connect Google'. The OAuth client must list this redirect URI:"
  echo "  ${url:-https://YOUR-ADS-ADDRESS}/google/callback"
fi
