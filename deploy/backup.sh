#!/usr/bin/env bash
# Nightly database backup (installed by setup-oracle.sh as /usr/local/bin/dsl-backup).
# Keeps the last 14 dumps in ./backups. Run from the repository root.
set -euo pipefail
mkdir -p backups
stamp=$(date -u +%Y%m%d-%H%M%S)
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml exec -T db pg_dump -U dsl dsl | gzip > "backups/dsl-$stamp.sql.gz"
# keep the newest 14 (names are timestamps, so a reverse name sort is newest first)
find backups -maxdepth 1 -type f -name 'dsl-*.sql.gz' -print0 | sort -rz | tail -z -n +15 | xargs -0 -r rm --
echo "backups/dsl-$stamp.sql.gz"
