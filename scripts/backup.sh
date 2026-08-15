#!/bin/bash
# Usage: ./scripts/backup.sh [output_dir]
# Creates a compressed pg_dump of the equitylens database.

DIR=${1:-./backups}
mkdir -p "$DIR"

FILE="$DIR/equitylens_$(date +%Y%m%d_%H%M%S).sql.gz"
docker compose exec -T postgres pg_dump -U equitylens equitylens | gzip > "$FILE"

echo "Database backed up to $FILE"
