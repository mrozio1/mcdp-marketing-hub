#!/bin/sh
# Runs inside the "backup" container (postgres:17-alpine image).
# Backs up the Postiz database + uploaded media once a day, keeping the newest
# $KEEP of each. "sh backup-loop.sh once" runs a single backup and exits.
set -u
KEEP=${KEEP:-14}
OUT=${BACKUP_DIR:-/backups}
MEDIA=${UPLOAD_DIR:-/uploads}

prune() {
  for pattern in 'db-*.sql.gz' 'uploads-*.tgz' 'ads-*.tgz'; do
    # shellcheck disable=SC2012,SC2086
    ls -1t "$OUT"/$pattern 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r f; do rm -f "$f"; done
  done
}

backup() {
  ok=0
  stamp=$(date +%Y%m%d-%H%M%S)
  if pg_dump -h postiz-postgres -U postiz postiz > "$OUT/.db-$stamp.sql"; then
    gzip -c "$OUT/.db-$stamp.sql" > "$OUT/db-$stamp.sql.gz"
  else
    echo "$(date) ERROR: database dump failed"; ok=1
  fi
  rm -f "$OUT/.db-$stamp.sql"
  if ! tar czf "$OUT/uploads-$stamp.tgz" -C "$MEDIA" .; then
    rm -f "$OUT/uploads-$stamp.tgz"
    echo "$(date) ERROR: media backup failed"; ok=1
  fi
  if [ -f /ads-data/ads.db ]; then
    # Studio files (designs, photos, videos) are big and never change once saved, so they're kept as one
    # mirror instead of in each day's archive. Once SharePoint is linked they live there instead.
    tar czf "$OUT/ads-$stamp.tgz" --exclude=./studio/files --exclude=./studio/tmp -C /ads-data . \
      || { echo "$(date) ERROR: ads backup failed"; ok=1; }
    if [ -d /ads-data/studio/files ]; then
      mkdir -p "$OUT/studio-files"
      for f in /ads-data/studio/files/*; do
        [ -f "$f" ] || continue
        [ -f "$OUT/studio-files/${f##*/}" ] || cp "$f" "$OUT/studio-files/" || ok=1
      done
      for f in "$OUT"/studio-files/*; do      # gone from the hub (deleted, or moved to SharePoint): drop the copy
        [ -f "$f" ] || continue
        if [ ! -f "/ads-data/studio/files/${f##*/}" ]; then
          find "$f" -mtime +7 -exec rm -f {} \; 2>/dev/null || true
        fi
      done
    fi
  fi
  prune
  [ "$ok" = 0 ] && echo "$(date) Backup written: db-$stamp.sql.gz, uploads-$stamp.tgz"
  return "$ok"
}

if [ "${1:-}" = once ]; then
  backup
  exit $?
fi

sleep 60   # let the database settle after a restart
while :; do
  backup || true
  sleep 86400
done
