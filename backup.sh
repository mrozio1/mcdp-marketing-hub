#!/bin/sh
# Nightly-friendly backup: Postiz database + uploaded media into ./backups,
# keeping the last 14. Add to cron:  0 3 * * * /path/to/mcdp-marketing-hub/backup.sh
set -eu
cd "$(dirname "$0")"
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
mkdir -p backups
stamp=$(date +%Y%m%d-%H%M%S)
$DOCKER exec postiz-postgres pg_dump -U postiz postiz | gzip > "backups/db-${stamp}.sql.gz"
$DOCKER run --rm -v mcdp-marketing-hub_postiz-uploads:/u:ro -v "$PWD/backups":/b alpine \
  tar czf "/b/uploads-${stamp}.tgz" -C /u .
# Keep the newest 14 of each kind.
for pattern in 'db-*.sql.gz' 'uploads-*.tgz'; do
  # shellcheck disable=SC2012
  ls -1t backups/$pattern 2>/dev/null | tail -n +15 | while read -r f; do rm -f "$f"; done
done
echo "Backup written: backups/*-${stamp}*"
