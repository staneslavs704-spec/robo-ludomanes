#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
docker compose exec -T db pg_dump -U robo robo | gzip > "backups/robo_${stamp}.sql.gz"
echo "Backup saved: backups/robo_${stamp}.sql.gz"
