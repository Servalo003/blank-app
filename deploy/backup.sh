#!/usr/bin/env bash
# Nightly database backup (installed by setup-oracle.sh as /usr/local/bin/dsl-backup).
# Keeps the last 14 dumps in ./backups. Run from the repository root.
set -euo pipefail
mkdir -p backups
stamp=$(date -u +%Y%m%d-%H%M%S)
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml exec -T db pg_dump -U dsl dsl | gzip > "backups/dsl-$stamp.sql.gz"
ls -1t backups/dsl-*.sql.gz | tail -n +15 | xargs -r rm --
echo "backups/dsl-$stamp.sql.gz"
