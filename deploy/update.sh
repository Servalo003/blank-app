#!/usr/bin/env bash
# Pull the latest code and restart. Usage, from the repository root: sudo deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
sed -i "s|^GIT_SHA=.*|GIT_SHA=$(git rev-parse --short HEAD)|" .env
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
docker image prune -f >/dev/null
echo "Updated to $(git rev-parse --short HEAD)."
