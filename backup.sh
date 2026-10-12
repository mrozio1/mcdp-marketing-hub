#!/bin/sh
# Run a backup right now. (Daily backups already run automatically in the
# "postiz-backup" container; files land in ./backups, newest 14 kept.)
set -eu
cd "$(dirname "$0")"
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
$DOCKER exec postiz-backup sh /backup-loop.sh once
