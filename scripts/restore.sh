#!/bin/bash
# Usage: ./scripts/restore.sh backup_file.sql.gz
# Restores a compressed pg_dump to the equitylens database.

if [ -z "$1" ]; then
  echo "Usage: ./scripts/restore.sh backup_file.sql.gz"
  exit 1
fi

if [ ! -f "$1" ]; then
  echo "File not found: $1"
  exit 1
fi

echo "Restoring from $1..."
gunzip -c "$1" | docker compose exec -T postgres psql -U equitylens equitylens

echo "Database restored."
