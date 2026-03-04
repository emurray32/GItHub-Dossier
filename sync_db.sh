#!/usr/bin/env bash
# sync_db.sh — Pull the latest production database from Replit
#
# Usage:
#   ./sync_db.sh                    # uses defaults from .env
#   REPLIT_URL=https://... ./sync_db.sh   # override URL
#
# Requires DOSSIER_SYNC_TOKEN and REPLIT_APP_URL in .env (or as env vars).
# Creates a timestamped backup of the local DB before overwriting.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_DIR="$SCRIPT_DIR/data"
DB_FILE="$DB_DIR/lead_machine.db"
BACKUP_DIR="$DB_DIR/backups"

# Load .env if present
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Configuration
SYNC_TOKEN="${DOSSIER_SYNC_TOKEN:-}"
APP_URL="${REPLIT_APP_URL:-}"

if [[ -z "$SYNC_TOKEN" ]]; then
    echo "ERROR: DOSSIER_SYNC_TOKEN is not set."
    echo "Add it to your .env file: DOSSIER_SYNC_TOKEN=your-secret-token"
    exit 1
fi

if [[ -z "$APP_URL" ]]; then
    echo "ERROR: REPLIT_APP_URL is not set."
    echo "Add it to your .env file: REPLIT_APP_URL=https://your-app.replit.app"
    exit 1
fi

# Strip trailing slash
APP_URL="${APP_URL%/}"

echo "Syncing database from $APP_URL ..."

# Create backup of existing local DB
if [[ -f "$DB_FILE" ]]; then
    mkdir -p "$BACKUP_DIR"
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_FILE="$BACKUP_DIR/lead_machine_${TIMESTAMP}.db"
    cp "$DB_FILE" "$BACKUP_FILE"
    echo "Backed up local DB to $BACKUP_FILE"

    # Keep only last 5 backups
    ls -1t "$BACKUP_DIR"/lead_machine_*.db 2>/dev/null | tail -n +6 | xargs -r rm --
fi

# Download from Replit
TMP_FILE=$(mktemp)
HTTP_CODE=$(curl -sS -w "%{http_code}" -o "$TMP_FILE" \
    -H "X-Sync-Token: $SYNC_TOKEN" \
    "$APP_URL/sync/export-db")

if [[ "$HTTP_CODE" != "200" ]]; then
    echo "ERROR: Download failed with HTTP $HTTP_CODE"
    if [[ -s "$TMP_FILE" ]]; then
        cat "$TMP_FILE"
    fi
    rm -f "$TMP_FILE"
    exit 1
fi

# Verify it's a valid SQLite file
FILE_TYPE=$(file -b "$TMP_FILE" 2>/dev/null || echo "unknown")
if [[ "$FILE_TYPE" != *"SQLite"* ]]; then
    echo "ERROR: Downloaded file is not a valid SQLite database"
    echo "File type: $FILE_TYPE"
    rm -f "$TMP_FILE"
    exit 1
fi

# Get file size for reporting
FILE_SIZE=$(wc -c < "$TMP_FILE" | tr -d ' ')
FILE_SIZE_MB=$(echo "scale=1; $FILE_SIZE / 1048576" | bc 2>/dev/null || echo "?")

# Replace local database
mv "$TMP_FILE" "$DB_FILE"

# Count accounts for confirmation
ACCOUNT_COUNT=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM monitored_accounts;" 2>/dev/null || echo "?")
REPORT_COUNT=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM reports;" 2>/dev/null || echo "?")

echo ""
echo "Sync complete!"
echo "  File size:  ${FILE_SIZE_MB} MB"
echo "  Accounts:   $ACCOUNT_COUNT"
echo "  Reports:    $REPORT_COUNT"
echo ""
echo "Restart the Flask server to pick up the new data."
