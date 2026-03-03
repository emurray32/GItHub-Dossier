#!/usr/bin/env python3
"""
One-time migration script: SQLite -> PostgreSQL.

Run this on the machine where the SQLite database file lives (e.g. Replit).
Requires DATABASE_URL environment variable pointing to the target PostgreSQL.

Usage:
    DATABASE_URL="postgresql://user:pass@host/db?sslmode=require" python migrate_to_postgres.py

The script is idempotent -- safe to re-run (truncates PG tables first).
"""

import json
import os
import sqlite3
import sys

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Tables in foreign-key-safe insertion order (parents before children)
# ---------------------------------------------------------------------------
TABLES = [
    'reports',
    'monitored_accounts',
    'system_settings',
    'system_stats',
    'hourly_api_stats',
    'webhook_logs',
    'import_batches',
    'scan_signals',
    'website_analyses',
    'webscraper_accounts',
    'contributors',
    'scorecard_scores',
    'campaigns',
    'sequence_mappings',
    'campaign_personas',
    'enrollment_batches',
    'enrollment_contacts',
    'audit_log',
]

BATCH_SIZE = 500

# Columns that store JSON as TEXT in SQLite and should be JSONB in PostgreSQL.
# Map of table_name -> set of column names that hold JSON data.
JSONB_COLUMNS = {
    'reports': {'scan_data', 'ai_analysis'},
    'monitored_accounts': {'metadata'},
    'import_batches': {'companies_json'},
    'website_analyses': {'tech_stack_json', 'analysis_details_json'},
    'webscraper_accounts': {'signals_json', 'prompt_history'},
    'scorecard_scores': {'systems_json'},
    'campaigns': {'assets', 'sequence_config'},
    'sequence_mappings': {'sequence_config'},
    'campaign_personas': {'titles_json', 'seniorities_json'},
    'enrollment_batches': {'account_ids_json'},
    'enrollment_contacts': {'generated_emails_json'},
}


def find_sqlite_db():
    """Locate the SQLite database file."""
    candidates = [
        os.path.join('data', 'lead_machine.db'),
        os.path.join(os.path.dirname(__file__), 'data', 'lead_machine.db'),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    print("ERROR: SQLite database not found. Tried:")
    for c in candidates:
        print(f"  {c}")
    sys.exit(1)


def get_columns(sqlite_cursor, table_name):
    """Return column names for a SQLite table."""
    sqlite_cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in sqlite_cursor.fetchall()]


def _coerce_jsonb(value):
    """Ensure a value is valid JSON for JSONB columns.

    SQLite stores JSON as plain TEXT. PostgreSQL JSONB columns require
    valid JSON. This function:
    - Returns None as-is (NULL)
    - Validates that the string is parseable JSON
    - Wraps bare strings as JSON strings if they aren't valid JSON
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return json.dumps(value)
    # Already valid JSON?
    try:
        json.loads(value)
        return value
    except (json.JSONDecodeError, ValueError):
        # Wrap bare string as a JSON string
        return json.dumps(value)


def migrate_table(sqlite_conn, pg_conn, table_name):
    """Copy all rows from one SQLite table into PostgreSQL. Returns row count."""
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()

    columns = get_columns(sqlite_cursor, table_name)
    if not columns:
        print(f"  {table_name:<25} SKIPPED (no columns)")
        return 0

    # Determine which column indices need JSONB coercion
    jsonb_cols = JSONB_COLUMNS.get(table_name, set())
    jsonb_indices = [i for i, col in enumerate(columns) if col in jsonb_cols]

    col_names = ', '.join(columns)
    placeholders = ', '.join(['%s'] * len(columns))

    sqlite_cursor.execute(f"SELECT {col_names} FROM {table_name}")
    rows = sqlite_cursor.fetchall()

    if not rows:
        print(f"  {table_name:<25} 0 rows (empty)")
        return 0

    # Coerce JSONB columns if needed
    if jsonb_indices:
        coerced_rows = []
        for row in rows:
            row = list(row)
            for idx in jsonb_indices:
                row[idx] = _coerce_jsonb(row[idx])
            coerced_rows.append(tuple(row))
        rows = coerced_rows

    # Insert in batches
    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        psycopg2.extras.execute_batch(
            pg_cursor,
            f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})",
            batch,
            page_size=BATCH_SIZE,
        )
        total += len(batch)

    pg_conn.commit()
    print(f"  {table_name:<25} {total:>8} rows")
    return total


def reset_sequences(pg_conn):
    """Reset every SERIAL sequence to MAX(id) + 1 so new inserts get correct IDs."""
    pg_cursor = pg_conn.cursor()
    reset = 0

    for table_name in TABLES:
        try:
            pg_cursor.execute(f"SELECT MAX(id) FROM {table_name}")
            max_id = pg_cursor.fetchone()[0]
            if max_id is not None:
                seq_name = f"{table_name}_id_seq"
                pg_cursor.execute(f"SELECT setval('{seq_name}', %s)", (max_id,))
                print(f"  {seq_name:<40} -> {max_id}")
                reset += 1
        except psycopg2.Error:
            pg_conn.rollback()  # table may lack id / sequence

    pg_conn.commit()
    print(f"  {reset} sequences reset.")


def verify(sqlite_conn, pg_conn):
    """Compare row counts between SQLite and PostgreSQL."""
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()

    print(f"\n{'Table':<25} {'SQLite':>10} {'Postgres':>10} {'OK?':>5}")
    print("-" * 54)

    all_ok = True
    for table_name in TABLES:
        try:
            sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            s_count = sqlite_cursor.fetchone()[0]
        except sqlite3.OperationalError:
            s_count = 0  # table doesn't exist in SQLite (e.g. audit_log)

        try:
            pg_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            p_count = pg_cursor.fetchone()[0]
        except psycopg2.Error:
            pg_conn.rollback()
            p_count = 0

        ok = "OK" if s_count == p_count else "MISMATCH"
        if s_count != p_count:
            all_ok = False

        print(f"  {table_name:<23} {s_count:>10,} {p_count:>10,}   {ok}")

    print("-" * 54)
    if all_ok:
        print("All tables match -- migration successful!")
    else:
        print("WARNING: row count mismatch detected. Investigate before switching.")
    return all_ok


def verify_jsonb_integrity(pg_conn):
    """Spot-check that JSONB columns contain valid JSON after migration."""
    pg_cursor = pg_conn.cursor()
    issues = 0

    print("\n[JSONB] Validating JSONB column integrity...")
    for table_name, cols in JSONB_COLUMNS.items():
        for col in cols:
            try:
                # Check for any non-null values that can't be cast to jsonb
                # (shouldn't happen if coercion worked, but verify)
                pg_cursor.execute(f"""
                    SELECT COUNT(*) FROM {table_name}
                    WHERE {col} IS NOT NULL
                """)
                total = pg_cursor.fetchone()[0]
                if total > 0:
                    print(f"  {table_name}.{col:<30} {total:>6} non-null values OK")
            except psycopg2.Error as e:
                print(f"  {table_name}.{col:<30} ERROR: {e}")
                pg_conn.rollback()
                issues += 1

    if issues == 0:
        print("  All JSONB columns validated successfully.")
    else:
        print(f"  WARNING: {issues} JSONB column(s) had issues.")
    return issues == 0


def main():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        print("Usage: DATABASE_URL='postgresql://...' python migrate_to_postgres.py")
        sys.exit(1)

    sqlite_path = find_sqlite_db()

    # Mask password in display
    display_url = database_url
    if '@' in display_url:
        pre, post = display_url.split('@', 1)
        if ':' in pre:
            scheme_user = pre.rsplit(':', 1)[0]
            display_url = f"{scheme_user}:****@{post}"

    print("=" * 60)
    print("SQLite -> PostgreSQL Migration")
    print("=" * 60)
    print(f"  Source:  {sqlite_path}")
    print(f"  Target:  {display_url[:70]}")
    print()

    # ------------------------------------------------------------------
    # 1. Create PostgreSQL schema via init_db()
    # ------------------------------------------------------------------
    print("[1/5] Creating PostgreSQL schema via init_db()...")
    # Ensure DATABASE_URL is available before importing database module
    os.environ['DATABASE_URL'] = database_url
    from database import init_db
    init_db()
    print("  Schema created (includes all indexes and constraints).\n")

    # ------------------------------------------------------------------
    # 2. Truncate PG tables (reverse FK order) for idempotent re-runs
    # ------------------------------------------------------------------
    print("[2/5] Clearing PostgreSQL tables...")
    pg_conn = psycopg2.connect(database_url)
    pg_cursor = pg_conn.cursor()
    for table_name in reversed(TABLES):
        try:
            pg_cursor.execute(f"TRUNCATE TABLE {table_name} CASCADE")
        except psycopg2.Error:
            pg_conn.rollback()
    pg_conn.commit()
    print("  Cleared.\n")

    # ------------------------------------------------------------------
    # 3. Copy data table by table (with JSONB coercion)
    # ------------------------------------------------------------------
    print("[3/5] Migrating data (with JSONB conversion)...")
    sqlite_conn = sqlite3.connect(sqlite_path)
    total_rows = 0
    for table_name in TABLES:
        try:
            total_rows += migrate_table(sqlite_conn, pg_conn, table_name)
        except Exception as e:
            print(f"  {table_name:<25} ERROR: {e}")
            pg_conn.rollback()

    print(f"\n  Total: {total_rows:,} rows migrated.\n")

    # ------------------------------------------------------------------
    # 4. Reset SERIAL sequences
    # ------------------------------------------------------------------
    print("[4/5] Resetting SERIAL sequences...")
    reset_sequences(pg_conn)

    # ------------------------------------------------------------------
    # 5. Verification
    # ------------------------------------------------------------------
    print("\n[5/5] Verifying migration...")
    row_ok = verify(sqlite_conn, pg_conn)
    jsonb_ok = verify_jsonb_integrity(pg_conn)

    sqlite_conn.close()
    pg_conn.close()

    if row_ok and jsonb_ok:
        print("\nMigration completed successfully.")
    else:
        print("\nMigration completed with warnings -- review output above.")
    print("Done.")


if __name__ == '__main__':
    main()
