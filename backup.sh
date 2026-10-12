#!/bin/sh
# Run a backup right now. (Daily backups already run automatically in the
# "postiz-backup" container; files land in ./backups, newest 14 kept.)
set -eu
cd "$(dirname "$0")"
. ./profiles.sh
$DOCKER exec postiz-backup sh /backup-loop.sh once
