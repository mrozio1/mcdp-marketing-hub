#!/usr/bin/env bash
# Nightly-friendly backup: Postiz database + uploaded media into ./backups,
# keeping the last 14. Add to cron:  0 3 * * * /path/to/mcdp-marketing-hub/backup.sh
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
stamp=$(date +%Y%m%d-%H%M%S)
docker exec postiz-postgres pg_dump -U postiz postiz | gzip > "backups/db-${stamp}.sql.gz"
docker run --rm -v mcdp-marketing-hub_postiz-uploads:/u:ro -v "$PWD/backups":/b alpine \
  tar czf "/b/uploads-${stamp}.tgz" -C /u .
ls -1t backups/db-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
ls -1t backups/uploads-*.tgz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "Backup written: backups/*-${stamp}*"
