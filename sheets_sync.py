"""
Google Sheets Sync Scheduler for Lead Machine.

Handles the daily sync of accounts from Coefficient-synced Google Sheets
into the Lead Machine scanning pipeline.

Flow:
    1. Read unprocessed rows from Google Sheet (max 300/day)
    2. Deduplicate against existing monitored_accounts
    3. Resolve GitHub orgs for new companies
    4. Add to Tier 0 and queue for scanning
    5. Write status back to sheet (mark as 'imported')

The sync can be triggered:
    - Manually via /api/sheets-sync endpoint
    - Automatically via the daily cron (6:00 AM)
    - Via the Grow Pipeline UI (Google Sheets tab)
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from sheets_client import (
    is_sheets_configured,
    read_sheet_accounts,
    mark_rows_processed,
    get_sheet_info,
    COLUMN_MAPPINGS
)
from database import (
    get_account_by_company_case_insensitive,
    add_account_to_tier_0,
    get_setting,
    set_setting,
    increment_daily_stat,
    get_db_connection
)
from monitors.discovery import resolve_org_fast


# Default daily limit - how many accounts to pull per sync
DEFAULT_DAILY_LIMIT = 300

# Sync lock to prevent concurrent syncs
_sync_lock = threading.Lock()
_sync_in_progress = False

# Cron thread reference
_cron_thread = None
_cron_running = False


def get_sync_config() -> dict:
    """
    Get the current sync configuration from settings.

    Returns:
        Dict with sync settings.
    """
    return {
        'enabled': get_setting('sheets_sync_enabled') == 'true',
        'sheet_name': get_setting('sheets_sync_tab') or 'Sheet1',
        'daily_limit': int(get_setting('sheets_sync_daily_limit') or DEFAULT_DAILY_LIMIT),
        'auto_scan': get_setting('sheets_sync_auto_scan') != 'false',  # Default True
        'last_sync_at': get_setting('sheets_sync_last_run'),
        'last_sync_result': get_setting('sheets_sync_last_result'),
        'cron_hour': int(get_setting('sheets_sync_cron_hour') or 6),
        'cron_minute': int(get_setting('sheets_sync_cron_minute') or 0),
    }


def set_sync_config(config: dict) -> None:
    """
    Save sync configuration to settings.

    Args:
        config: Dict with settings to update (only provided keys are updated).
    """
    if 'enabled' in config:
        set_setting('sheets_sync_enabled', 'true' if config['enabled'] else 'false')
    if 'sheet_name' in config:
        set_setting('sheets_sync_tab', config['sheet_name'])
    if 'daily_limit' in config:
        set_setting('sheets_sync_daily_limit', str(config['daily_limit']))
    if 'auto_scan' in config:
        set_setting('sheets_sync_auto_scan', 'true' if config['auto_scan'] else 'false')
    if 'cron_hour' in config:
        set_setting('sheets_sync_cron_hour', str(config['cron_hour']))
    if 'cron_minute' in config:
        set_setting('sheets_sync_cron_minute', str(config['cron_minute']))


def run_sync(
    limit: Optional[int] = None,
    sheet_name: Optional[str] = None,
    auto_scan: bool = True,
    dry_run: bool = False
) -> dict:
    """
    Execute a Google Sheets sync.

    Reads unprocessed accounts from the sheet, deduplicates against the
    existing database, resolves GitHub orgs, and queues for scanning.

    Args:
        limit: Max accounts to import (overrides config).
        sheet_name: Sheet tab to read from (overrides config).
        auto_scan: Whether to auto-queue accounts for scanning.
        dry_run: If True, read and map but don't import or write back.

    Returns:
        Dict with sync results including counts and details.
    """
    global _sync_in_progress

    # Prevent concurrent syncs
    if not _sync_lock.acquire(blocking=False):
        return {
            'status': 'error',
            'error': 'Sync already in progress',
            'timestamp': datetime.now().isoformat()
        }

    _sync_in_progress = True

    try:
        result = _perform_sync(limit, sheet_name, auto_scan, dry_run)
        return result
    finally:
        _sync_in_progress = False
        _sync_lock.release()


def _perform_sync(
    limit: Optional[int],
    sheet_name: Optional[str],
    auto_scan: bool,
    dry_run: bool
) -> dict:
    """Internal sync implementation."""
    config = get_sync_config()
    limit = limit or config['daily_limit']
    sheet_name = sheet_name or config['sheet_name']
    auto_scan = auto_scan if auto_scan is not None else config['auto_scan']

    sync_result = {
        'status': 'success',
        'timestamp': datetime.now().isoformat(),
        'dry_run': dry_run,
        'sheet_name': sheet_name,
        'limit': limit,
        'total_rows_in_sheet': 0,
        'unprocessed_rows': 0,
        'accounts_read': 0,
        'added': [],
        'skipped_existing': [],
        'failed_resolve': [],
        'errors': [],
        'scan_queued': 0,
    }

    # Step 1: Check configuration
    if not is_sheets_configured():
        sync_result['status'] = 'error'
        sync_result['error'] = 'Google Sheets not configured'
        return sync_result

    print(f"[SHEETS-SYNC] Starting sync (limit={limit}, sheet={sheet_name}, dry_run={dry_run})")

    # Step 2: Read accounts from sheet
    sheet_data = read_sheet_accounts(
        sheet_name=sheet_name,
        limit=limit,
        only_unprocessed=True
    )

    if sheet_data['errors']:
        sync_result['status'] = 'error'
        sync_result['errors'] = sheet_data['errors']
        sync_result['error'] = sheet_data['errors'][0]
        return sync_result

    sync_result['total_rows_in_sheet'] = sheet_data['total_rows']
    sync_result['unprocessed_rows'] = sheet_data['unprocessed_rows']
    sync_result['accounts_read'] = len(sheet_data['accounts'])
    sync_result['headers_found'] = list(sheet_data['header_map'].keys())

    accounts = sheet_data['accounts']
    if not accounts:
        sync_result['status'] = 'success'
        sync_result['message'] = 'No unprocessed accounts found in sheet'
        _save_sync_result(sync_result)
        return sync_result

    print(f"[SHEETS-SYNC] Read {len(accounts)} unprocessed accounts from sheet")

    if dry_run:
        sync_result['preview'] = [
            {
                'company_name': a.get('company_name', ''),
                'domain': a.get('domain', ''),
                'industry': a.get('industry', ''),
                'row': a.get('_row_index'),
            }
            for a in accounts
        ]
        return sync_result

    # Step 3: Process each account
    added_rows = []
    added_companies = []

    for account in accounts:
        company_name = account['company_name']
        domain = account.get('domain', '')
        row_idx = account['_row_index']

        # Check if already exists in database
        existing = get_account_by_company_case_insensitive(company_name)
        if existing:
            sync_result['skipped_existing'].append({
                'company': company_name,
                'row': row_idx,
                'reason': 'already_in_database'
            })
            # Still mark as processed in sheet
            added_rows.append(row_idx)
            continue

        # Try to resolve GitHub org
        try:
            org = resolve_org_fast(company_name)
            if org:
                github_org = org.get('login', '')
                add_account_to_tier_0(company_name, github_org)

                # Store additional metadata (domain, industry) if available
                _store_account_metadata(company_name, account)

                sync_result['added'].append({
                    'company': company_name,
                    'github_org': github_org,
                    'domain': domain,
                    'row': row_idx
                })
                added_rows.append(row_idx)
                added_companies.append(company_name)
                print(f"[SHEETS-SYNC] Added: {company_name} -> {github_org}")
            else:
                sync_result['failed_resolve'].append({
                    'company': company_name,
                    'row': row_idx,
                    'reason': 'github_org_not_found'
                })
                # Mark as processed but with failure status
                added_rows.append(row_idx)
                print(f"[SHEETS-SYNC] Failed to resolve: {company_name}")

        except Exception as e:
            sync_result['failed_resolve'].append({
                'company': company_name,
                'row': row_idx,
                'reason': str(e)
            })
            print(f"[SHEETS-SYNC] Error processing {company_name}: {e}")

    # Step 4: Write status back to sheet
    if added_rows:
        try:
            # Mark successfully added rows as 'imported'
            imported_rows = [r for r in added_rows if any(
                a['row'] == r for a in sync_result['added']
            )]
            skipped_rows = [r for r in added_rows if any(
                s['row'] == r for s in sync_result['skipped_existing']
            )]
            failed_rows = [r for r in added_rows if any(
                f['row'] == r for f in sync_result['failed_resolve']
            )]

            if imported_rows:
                mark_rows_processed(imported_rows, 'imported', sheet_name)
            if skipped_rows:
                mark_rows_processed(skipped_rows, 'already_exists', sheet_name)
            if failed_rows:
                mark_rows_processed(failed_rows, 'no_github_found', sheet_name)

            print(f"[SHEETS-SYNC] Marked {len(added_rows)} rows as processed in sheet")
        except Exception as e:
            sync_result['errors'].append(f'Failed to write status to sheet: {e}')
            print(f"[SHEETS-SYNC] Error writing back to sheet: {e}")

    # Step 5: Queue for scanning if auto_scan is enabled
    if auto_scan and added_companies:
        try:
            # Import here to avoid circular imports
            from database import batch_set_scan_status_queued
            batch_set_scan_status_queued(added_companies)
            sync_result['scan_queued'] = len(added_companies)
            print(f"[SHEETS-SYNC] Queued {len(added_companies)} accounts for scanning")

            # Submit to executor
            from app import get_executor, perform_background_scan
            executor = get_executor()
            for company_name in added_companies:
                executor.submit(perform_background_scan, company_name)

        except Exception as e:
            sync_result['errors'].append(f'Failed to queue scans: {e}')
            print(f"[SHEETS-SYNC] Error queueing scans: {e}")

    # Step 6: Update stats
    try:
        increment_daily_stat('scans_run', len(sync_result['added']))
    except Exception:
        pass

    # Save sync result to settings
    _save_sync_result(sync_result)

    print(f"[SHEETS-SYNC] Sync complete: {len(sync_result['added'])} added, "
          f"{len(sync_result['skipped_existing'])} skipped, "
          f"{len(sync_result['failed_resolve'])} failed")

    return sync_result


def _store_account_metadata(company_name: str, account: dict) -> None:
    """
    Store additional account metadata (domain, industry, etc.) in the database.

    Uses the system_settings table with a prefixed key pattern.
    This keeps the schema simple and doesn't require ALTER TABLE.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Store metadata fields
        key_prefix = f'account_meta:{company_name.lower().strip()}'
        metadata_fields = ['domain', 'industry', 'employees', 'salesforce_id',
                           'city', 'state', 'country']

        for field in metadata_fields:
            value = account.get(field, '')
            if value:
                cursor.execute('''
                    INSERT OR REPLACE INTO system_settings (key, value)
                    VALUES (?, ?)
                ''', (f'{key_prefix}:{field}', value))

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[SHEETS-SYNC] Error storing metadata for {company_name}: {e}")


def get_account_metadata(company_name: str) -> dict:
    """
    Retrieve stored metadata for an account.

    Returns:
        Dict with domain, industry, employees, etc.
    """
    metadata = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        key_prefix = f'account_meta:{company_name.lower().strip()}'
        cursor.execute(
            'SELECT key, value FROM system_settings WHERE key LIKE ?',
            (f'{key_prefix}:%',)
        )

        for row in cursor.fetchall():
            field = row['key'].split(':')[-1]
            metadata[field] = row['value']

        conn.close()
    except Exception as e:
        print(f"[SHEETS-SYNC] Error reading metadata for {company_name}: {e}")

    return metadata


def _save_sync_result(result: dict) -> None:
    """Save the latest sync result to settings."""
    try:
        set_setting('sheets_sync_last_run', datetime.now().isoformat())
        # Store a summary (full result may be too large for a single setting)
        summary = {
            'status': result.get('status'),
            'timestamp': result.get('timestamp'),
            'added': len(result.get('added', [])),
            'skipped': len(result.get('skipped_existing', [])),
            'failed': len(result.get('failed_resolve', [])),
            'scan_queued': result.get('scan_queued', 0),
        }
        import json
        set_setting('sheets_sync_last_result', json.dumps(summary))
    except Exception as e:
        print(f"[SHEETS-SYNC] Error saving sync result: {e}")


# =============================================================================
# CRON SCHEDULER - Daily automatic sync
# =============================================================================

def _cron_worker():
    """
    Background thread that triggers the daily sync at the configured time.

    Runs every 60 seconds checking if it's time for the daily sync.
    Only runs if sheets_sync_enabled is 'true' in settings.
    """
    global _cron_running
    print("[SHEETS-CRON] Cron scheduler started")

    while _cron_running:
        try:
            config = get_sync_config()

            if config['enabled']:
                now = datetime.now()
                target_hour = config['cron_hour']
                target_minute = config['cron_minute']

                # Check if we should run now
                if now.hour == target_hour and now.minute == target_minute:
                    # Check if we already ran today
                    last_run = config['last_sync_at']
                    already_ran_today = False

                    if last_run:
                        try:
                            last_run_dt = datetime.fromisoformat(last_run)
                            if last_run_dt.date() == now.date():
                                already_ran_today = True
                        except ValueError:
                            pass

                    if not already_ran_today:
                        print(f"[SHEETS-CRON] Triggering daily sync at {now.strftime('%H:%M')}")
                        try:
                            result = run_sync()
                            print(f"[SHEETS-CRON] Daily sync complete: "
                                  f"{len(result.get('added', []))} added")
                        except Exception as e:
                            print(f"[SHEETS-CRON] Daily sync failed: {e}")

        except Exception as e:
            print(f"[SHEETS-CRON] Error in cron worker: {e}")

        # Sleep for 60 seconds before checking again
        time.sleep(60)

    print("[SHEETS-CRON] Cron scheduler stopped")


def start_cron_scheduler():
    """Start the daily cron scheduler in a background thread."""
    global _cron_thread, _cron_running

    if _cron_thread and _cron_thread.is_alive():
        print("[SHEETS-CRON] Cron scheduler already running")
        return

    _cron_running = True
    _cron_thread = threading.Thread(
        target=_cron_worker,
        daemon=True,
        name="SheetsCronScheduler"
    )
    _cron_thread.start()
    print("[SHEETS-CRON] Started cron scheduler thread")


def stop_cron_scheduler():
    """Stop the daily cron scheduler."""
    global _cron_running
    _cron_running = False
    print("[SHEETS-CRON] Stopping cron scheduler...")


def is_sync_in_progress() -> bool:
    """Check if a sync is currently running."""
    return _sync_in_progress
