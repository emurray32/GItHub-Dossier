"""
SQLite database module for storing Lead Machine reports.
"""
import sqlite3
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional
from config import Config


def get_db_connection() -> sqlite3.Connection:
    """Create a database connection with row factory and timeout."""
    os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
    # Use a 30-second timeout to handle concurrent access gracefully
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the database schema."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            github_org TEXT,
            scan_data JSON,
            ai_analysis JSON,
            signals_found INTEGER DEFAULT 0,
            repos_scanned INTEGER DEFAULT 0,
            commits_analyzed INTEGER DEFAULT 0,
            prs_analyzed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            scan_duration_seconds REAL
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_reports_company
        ON reports(company_name)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_reports_created
        ON reports(created_at DESC)
    ''')

    # Monitored Accounts table for CRM-style tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS monitored_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL UNIQUE,
            github_org TEXT,
            annual_revenue TEXT,
            website TEXT,
            notes TEXT,
            current_tier INTEGER DEFAULT 0,
            last_scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            evidence_summary TEXT,
            next_scan_due TIMESTAMP,
            scan_status TEXT DEFAULT 'idle',
            scan_progress TEXT,
            scan_start_time TIMESTAMP
        )
    ''')

    # Migrate existing tables: add annual_revenue column if it doesn't exist
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN annual_revenue TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migrate existing tables: add notes column if it doesn't exist
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN notes TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migrate existing tables: add website column if it doesn't exist
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN website TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migrate existing tables: add scan_status columns if they don't exist
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN scan_status TEXT DEFAULT "idle"')
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN scan_progress TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN scan_start_time TIMESTAMP')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migrate existing tables: add last_scan_error column if it doesn't exist
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN last_scan_error TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_tier
        ON monitored_accounts(current_tier DESC)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_company
        ON monitored_accounts(company_name)
    ''')

    # Case-insensitive indices for faster JOINs and lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_company_lower
        ON monitored_accounts(LOWER(company_name))
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_org_lower
        ON monitored_accounts(LOWER(github_org))
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_reports_company_lower
        ON reports(LOWER(company_name))
    ''')

    # System Settings table - key-value store
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # System Stats table - daily usage tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_stats (
            date TEXT PRIMARY KEY,
            scans_run INTEGER DEFAULT 0,
            api_calls_estimated INTEGER DEFAULT 0,
            webhooks_fired INTEGER DEFAULT 0
        )
    ''')

    # Hourly API Stats table - tracks API calls per hour for rate limit display
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hourly_api_stats (
            hour_key TEXT PRIMARY KEY,
            api_calls INTEGER DEFAULT 0
        )
    ''')

    # Webhook Logs table - webhook delivery history
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS webhook_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            event_type TEXT NOT NULL,
            company TEXT,
            status TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_webhook_logs_timestamp
        ON webhook_logs(timestamp DESC)
    ''')

    # Import Batches table - persistent queue for bulk imports
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT DEFAULT 'pending',
            total_count INTEGER,
            processed_count INTEGER DEFAULT 0,
            companies_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_import_batches_status
        ON import_batches(status)
    ''')

    # Scan Signals table - stores individual signals detected during scans
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scan_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            company_name TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            description TEXT,
            file_path TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (report_id) REFERENCES reports(id)
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_scan_signals_report
        ON scan_signals(report_id)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_scan_signals_company
        ON scan_signals(company_name)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_scan_signals_timestamp
        ON scan_signals(timestamp DESC)
    ''')

    # Set default webhook_enabled to false (paused) if not already set
    cursor.execute('''
        INSERT OR IGNORE INTO system_settings (key, value)
        VALUES ('webhook_enabled', 'false')
    ''')

    conn.commit()
    conn.close()

    # Run cleanup tasks on initialization
    cleanup_duplicate_accounts()
    cleanup_quote_characters()


def cleanup_duplicate_accounts() -> dict:
    """
    Remove duplicate accounts based on both Company Name and GitHub Organization.
    Keeps the 'best' account: Highest Tier > Most Recent Scan > Newest ID.
    
    Returns:
        Dictionary with cleanup results: {deleted: int, kept: int, groups: list}
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    deleted_count = 0
    kept_count = 0
    groups_cleaned = []

    try:
        # Step 1: Find duplicates by Company Name (case-insensitive)
        cursor.execute('''
            SELECT LOWER(company_name) as normalized_name, COUNT(*) as count
            FROM monitored_accounts
            GROUP BY LOWER(company_name)
            HAVING count > 1
        ''')
        name_duplicates = cursor.fetchall()
        
        for row in name_duplicates:
            name = row['normalized_name']
            
            cursor.execute('''
                SELECT * FROM monitored_accounts 
                WHERE LOWER(company_name) = ?
                ORDER BY current_tier DESC, last_scanned_at DESC, id DESC
            ''', (name,))
            
            accounts = cursor.fetchall()
            if len(accounts) > 1:
                keep_id = accounts[0]['id']
                remove_ids = [acc['id'] for acc in accounts[1:]]
                
                placeholders = ','.join('?' * len(remove_ids))
                cursor.execute(f'DELETE FROM monitored_accounts WHERE id IN ({placeholders})', remove_ids)
                
                deleted_count += len(remove_ids)
                kept_count += 1
                groups_cleaned.append({'name': name, 'type': 'name', 'kept_id': keep_id, 'removed_count': len(remove_ids)})

        # Step 2: Find duplicates by GitHub Org (case-insensitive)
        cursor.execute('''
            SELECT github_org, COUNT(*) as count
            FROM monitored_accounts
            WHERE github_org IS NOT NULL AND github_org != ''
            GROUP BY LOWER(github_org)
            HAVING count > 1
        ''')
        org_duplicates = cursor.fetchall()
        
        for row in org_duplicates:
            org = row['github_org']
            
            cursor.execute('''
                SELECT * FROM monitored_accounts 
                WHERE LOWER(github_org) = LOWER(?)
                ORDER BY current_tier DESC, last_scanned_at DESC, id DESC
            ''', (org,))
            
            accounts = cursor.fetchall()
            if len(accounts) > 1:
                keep_id = accounts[0]['id']
                # Filter out accounts that might have already been deleted in Step 1
                # (though usually they would have been deleted already if they shared a name)
                remove_ids = [acc['id'] for acc in accounts[1:]]
                
                placeholders = ','.join('?' * len(remove_ids))
                cursor.execute(f'DELETE FROM monitored_accounts WHERE id IN ({placeholders})', remove_ids)
                
                deleted_count += len(remove_ids)
                kept_count += 1
                groups_cleaned.append({'org': org, 'type': 'org', 'kept_id': keep_id, 'removed_count': len(remove_ids)})
                
        conn.commit()
    except Exception as e:
        print(f"[CLEANUP] Error removing duplicates: {e}")
        conn.rollback()
    finally:
        conn.close()
        
    return {
        'deleted': deleted_count,
        'kept': kept_count,
        'groups': groups_cleaned
    }


def cleanup_quote_characters() -> int:
    """
    Remove leading/trailing quote characters from company names.
    This fixes data imported from CSV files where quotes weren't properly stripped.

    Returns:
        Number of records updated.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    updated_count = 0

    try:
        # Find company names with leading or trailing quotes
        cursor.execute('''
            SELECT id, company_name FROM monitored_accounts
            WHERE company_name LIKE '"%' OR company_name LIKE '%"'
        ''')
        rows = cursor.fetchall()

        for row in rows:
            old_name = row['company_name']
            # Strip leading and trailing quotes
            new_name = old_name.strip('"').strip()
            if new_name != old_name:
                cursor.execute('''
                    UPDATE monitored_accounts SET company_name = ? WHERE id = ?
                ''', (new_name, row['id']))
                updated_count += 1
                print(f"[CLEANUP] Fixed company name: '{old_name}' -> '{new_name}'")

        conn.commit()
    except Exception as e:
        print(f"[CLEANUP] Error cleaning quote characters: {e}")
        conn.rollback()
    finally:
        conn.close()

    return updated_count


def save_report(
    company_name: str,
    github_org: str,
    scan_data: dict,
    ai_analysis: dict,
    scan_duration: float
) -> int:
    """
    Save a completed report to the database.

    Returns:
        The ID of the newly created report.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Calculate summary stats
    signals_found = len(scan_data.get('signals', []))
    repos_scanned = len(scan_data.get('repos_scanned', []))
    commits_analyzed = scan_data.get('total_commits_analyzed', 0)
    prs_analyzed = scan_data.get('total_prs_analyzed', 0)

    cursor.execute('''
        INSERT INTO reports (
            company_name, github_org, scan_data, ai_analysis,
            signals_found, repos_scanned, commits_analyzed, prs_analyzed,
            scan_duration_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        company_name,
        github_org,
        json.dumps(scan_data),
        json.dumps(ai_analysis),
        signals_found,
        repos_scanned,
        commits_analyzed,
        prs_analyzed,
        scan_duration
    ))

    report_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return report_id


def get_report(report_id: int) -> Optional[dict]:
    """Retrieve a report by ID, including associated signals and firmographics."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # JOIN with monitored_accounts to get firmographics (website, annual_revenue)
    cursor.execute('''
        SELECT r.*, ma.website, ma.annual_revenue
        FROM reports r
        LEFT JOIN monitored_accounts ma ON LOWER(ma.company_name) = LOWER(r.company_name)
        WHERE r.id = ?
    ''', (report_id,))
    row = cursor.fetchone()

    if row:
        report = _row_to_dict(row)

        # Fetch associated signals for this report
        cursor.execute('''
            SELECT id, signal_type, description, file_path, timestamp
            FROM scan_signals
            WHERE report_id = ?
            ORDER BY timestamp DESC
        ''', (report_id,))

        signal_rows = cursor.fetchall()
        report['signals'] = [dict(sig_row) for sig_row in signal_rows]

        conn.close()
        return report

    conn.close()
    return None


def save_signals(report_id: int, company_name: str, signals: list) -> int:
    """
    Save signals detected during a scan to the database.

    Args:
        report_id: The ID of the associated report.
        company_name: The company name being scanned.
        signals: List of signal dictionaries from scan results.

    Returns:
        The count of signals saved.
    """
    if not signals:
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()

    saved_count = 0

    for signal in signals:
        try:
            # Extract relevant fields from signal dict
            signal_type = signal.get('type', signal.get('Signal', 'unknown'))
            # Use 'Evidence' field as description, falling back to 'Signal' field
            description = signal.get('Evidence', signal.get('Signal', ''))
            # Use 'Link' or 'file' as file_path
            file_path = signal.get('Link', signal.get('file', signal.get('repo', '')))

            cursor.execute('''
                INSERT INTO scan_signals (
                    report_id, company_name, signal_type, description, file_path
                ) VALUES (?, ?, ?, ?, ?)
            ''', (report_id, company_name, signal_type, description, file_path))

            saved_count += 1
        except Exception as e:
            # Log error but continue processing other signals
            print(f"Error saving signal: {str(e)}")
            continue

    conn.commit()
    conn.close()

    return saved_count


def get_signals_for_report(report_id: int) -> list:
    """Retrieve all signals for a specific report."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, signal_type, description, file_path, timestamp
        FROM scan_signals
        WHERE report_id = ?
        ORDER BY timestamp DESC
    ''', (report_id,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_signals_by_company(company_name: str, limit: int = 100) -> list:
    """Retrieve recent signals for a company across all reports."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, report_id, signal_type, description, file_path, timestamp
        FROM scan_signals
        WHERE company_name = ?
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (company_name, limit))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_recent_reports(limit: int = 20) -> list:
    """Get the most recent reports."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, company_name, github_org, signals_found, repos_scanned,
               commits_analyzed, prs_analyzed, created_at, scan_duration_seconds
        FROM reports
        ORDER BY created_at DESC
        LIMIT ?
    ''', (limit,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def search_reports(query: str) -> list:
    """Search reports by company name."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, company_name, github_org, signals_found, repos_scanned,
               created_at
        FROM reports
        WHERE company_name LIKE ?
        ORDER BY created_at DESC
        LIMIT 50
    ''', (f'%{query}%',))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a database row to a dictionary with parsed JSON fields."""
    result = dict(row)

    # Parse JSON fields
    if result.get('scan_data'):
        result['scan_data'] = json.loads(result['scan_data'])
        if isinstance(result['scan_data'], dict):
            result['scan_data'].setdefault('compliance_assets', {'detected_files': []})
    if result.get('ai_analysis'):
        result['ai_analysis'] = json.loads(result['ai_analysis'])

    return result


# =============================================================================
# MONITORED ACCOUNTS - CRM Functions
# =============================================================================

# Tier Constants
TIER_TRACKING = 0    # Cold - No signals found
TIER_THINKING = 1    # Warm - RFC discussions found
TIER_PREPARING = 2   # Hot Lead (Goldilocks) - Dependencies without locale folders
TIER_LAUNCHED = 3    # Too Late - Already launched
TIER_INVALID = 4     # Disqualified - GitHub org not found or no public repos

TIER_CONFIG = {
    TIER_TRACKING: {'name': 'Tracking', 'status': 'Cold', 'color': 'grey', 'emoji': ''},
    TIER_THINKING: {'name': 'Thinking', 'status': 'Warm', 'color': 'yellow', 'emoji': ''},
    TIER_PREPARING: {'name': 'Preparing', 'status': 'Hot Lead', 'color': 'green', 'emoji': ''},
    TIER_LAUNCHED: {'name': 'Launched', 'status': 'Too Late', 'color': 'red', 'emoji': ''},
    TIER_INVALID: {'name': 'Not Found', 'status': 'Disqualified', 'color': 'dark-grey', 'emoji': ''},
}


def _convert_library_to_sales_name(lib_name: str) -> str:
    """
    Convert technical library names to sales-friendly title-case names.

    Examples:
        'react-i18next' -> 'React Translation Engine'
        'babel-plugin-react-intl' -> 'React String Extraction'
    """
    # Sales-friendly name mappings for common libraries
    SALES_NAMES = {
        'react-i18next': 'React Translation Engine',
        'babel-plugin-react-intl': 'React String Extraction',
        'formatjs': 'Message Formatting Library',
        'uppy': 'File Uploader i18n',
        'i18next': 'Translation Engine',
        'vue-i18n': 'Vue Translation Engine',
        'next-intl': 'Next.js Translation Engine',
        'lingui': 'React Localization Framework',
        'react-intl': 'React Internationalization',
    }

    if lib_name in SALES_NAMES:
        return SALES_NAMES[lib_name]

    # Fallback: Convert to title case (replace hyphens/underscores with spaces)
    return lib_name.replace('-', ' ').replace('_', ' ').title()


def calculate_tier_from_scan(scan_data: dict) -> tuple[int, str]:
    """
    Apply Sales-First tier logic based on scan results.

    Tier Priority Order:
    1. Tier 3 (Launched) - Locale folders exist = Too Late
    2. Tier 2 (Preparing) - i18n libraries WITHOUT locale folders = Hot Lead
    3. Tier 1 (Thinking) - RFCs or Ghost Branches = Warm Lead
    4. Tier 0 vs Tier 4 Split - Based on repos scanned:
       - repos_scanned > 0 → Tier 0 (Tracking) - Monitor for future changes
       - repos_scanned == 0 → Tier 4 (Disqualified) - Empty org or no access

    Returns:
        Tuple of (tier_number, evidence_summary)
    """
    signal_summary = scan_data.get('signal_summary', {})

    # Extract key signals
    rfc_count = signal_summary.get('rfc_discussion', {}).get('count', 0)
    dependency_count = signal_summary.get('dependency_injection', {}).get('count', 0)
    ghost_count = signal_summary.get('ghost_branch', {}).get('count', 0)

    # Get star count for Tier 0 vs Tier 4 decision
    total_stars = scan_data.get('total_stars', 0)

    # Check for locale folders in the scan
    locale_folders_found = False
    dependency_hits = signal_summary.get('dependency_injection', {}).get('hits', [])
    for hit in dependency_hits:
        if isinstance(hit, dict):
            # Check both possible keys - 'locale_folders_found' (list) or 'has_locale_folders' (bool)
            if hit.get('locale_folders_found') or hit.get('has_locale_folders'):
                locale_folders_found = True
                break
            # Also check if the signal type indicates already launched
            if hit.get('type') == 'already_launched':
                locale_folders_found = True
                break

    # Also check goldilocks_status for launched indicator
    goldilocks_status = scan_data.get('goldilocks_status', 'none')
    if goldilocks_status == 'launched':
        locale_folders_found = True

    # =========================================================================
    # TIER 3: LAUNCHED - Locale folders detected (Too Late)
    # =========================================================================
    if locale_folders_found:
        return TIER_LAUNCHED, "Too Late: Translation files already exist in codebase."

    # =========================================================================
    # TIER 2: PREPARING (GOLDILOCKS) - i18n libraries WITHOUT locale folders
    # =========================================================================
    if dependency_count > 0:
        dep_names = []
        for hit in dependency_hits:
            if isinstance(hit, dict):
                # The scanner uses 'libraries_found' as a list of library names
                libs = hit.get('libraries_found', [])
                if libs:
                    dep_names.extend(libs)
                else:
                    # Fallback to other possible keys
                    lib_name = hit.get('library', hit.get('name', ''))
                    if lib_name:
                        dep_names.append(lib_name)

        # Remove duplicates while preserving order
        seen = set()
        unique_deps = []
        for name in dep_names:
            if name not in seen:
                seen.add(name)
                unique_deps.append(name)
        dep_names = unique_deps

        if dep_names:
            # Convert first library to sales-friendly name
            sales_name = _convert_library_to_sales_name(dep_names[0])
            evidence = f"INFRASTRUCTURE READY: Installed {sales_name} but NO translations found."
        else:
            evidence = "INFRASTRUCTURE READY: i18n library installed but NO translations found."
        return TIER_PREPARING, evidence

    # =========================================================================
    # TIER 1: THINKING - RFC discussions OR Ghost Branches found
    # =========================================================================
    if rfc_count > 0 or ghost_count > 0:
        evidence_parts = []

        if rfc_count > 0:
            rfc_hits = signal_summary.get('rfc_discussion', {}).get('hits', [])
            if rfc_hits and isinstance(rfc_hits[0], dict):
                title = rfc_hits[0].get('title', 'i18n discussion')[:50]
                evidence_parts.append(f"STRATEGY SIGNAL: {title}")
            else:
                evidence_parts.append(f"STRATEGY SIGNAL: {rfc_count} i18n RFC/discussion(s)")

        if ghost_count > 0:
            ghost_hits = signal_summary.get('ghost_branch', {}).get('hits', [])
            if ghost_hits and isinstance(ghost_hits[0], dict):
                branch_name = ghost_hits[0].get('name', ghost_hits[0].get('ref', 'i18n branch'))[:40]
                evidence_parts.append(f"ACTIVE BUILD: {branch_name}")
            else:
                evidence_parts.append(f"ACTIVE BUILD: {ghost_count} i18n branch(es)")

        return TIER_THINKING, "; ".join(evidence_parts)

    # =========================================================================
    # TIER 0 vs TIER 4: No signals found - Split based on repos scanned and org status
    # =========================================================================
    # Get repos_scanned count and check if org was found
    repos_scanned = len(scan_data.get('repos_scanned', []))
    org_login = scan_data.get('org_login', '')
    org_public_repos = scan_data.get('org_public_repos', 0)

    if repos_scanned > 0:
        # We scanned valid repos but found no i18n signals - track for future changes
        return TIER_TRACKING, "No active signals detected. Monitoring for future changes."
    elif org_login:
        # Org was found but no repos were scanned - still track it
        # This could be due to: all private repos, all inactive repos, or repo fetch issues
        if org_public_repos > 0:
            return TIER_TRACKING, f"No active repositories to scan ({org_public_repos} public repos all inactive). Monitoring for future changes."
        else:
            return TIER_TRACKING, "Organization found but no public repositories. Monitoring for future changes."
    else:
        # No org was found - this shouldn't happen if scan completed, but handle it
        return TIER_INVALID, "DISQUALIFIED: Unable to complete scan (Organization not found or API error)."


def update_account_status(scan_data: dict, report_id: Optional[int] = None) -> dict:
    """
    Update or create a monitored account based on scan results.

    This function runs automatically after every successful scan.

    Args:
        scan_data: The complete scan result dictionary
        report_id: Optional ID of the saved report

    Returns:
        Dictionary with the account update status
    """
    company_name = scan_data.get('company_name', '')
    github_org = scan_data.get('org_login', '')

    if not company_name:
        return {'error': 'No company name in scan data'}

    # Normalize company name to lowercase to prevent duplicates
    company_name_normalized = company_name.lower().strip()

    # Calculate tier and evidence
    new_tier, evidence_summary = calculate_tier_from_scan(scan_data)

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if account exists (case-insensitive)
    cursor.execute(
        'SELECT * FROM monitored_accounts WHERE LOWER(company_name) = ?',
        (company_name_normalized,)
    )
    existing = cursor.fetchone()

    now = datetime.now().isoformat()
    # Set next scan due to 7 days from now
    next_scan = datetime.now() + timedelta(days=7)
    next_scan_iso = next_scan.isoformat()

    tier_changed = False

    if existing:
        existing_tier = existing['current_tier']
        tier_changed = existing_tier != new_tier

        if tier_changed:
            # Tier changed - update status_changed_at
            cursor.execute('''
                UPDATE monitored_accounts
                SET github_org = ?,
                    current_tier = ?,
                    last_scanned_at = ?,
                    status_changed_at = ?,
                    evidence_summary = ?,
                    next_scan_due = ?
                WHERE LOWER(company_name) = ?
            ''', (github_org, new_tier, now, now, evidence_summary, next_scan_iso, company_name_normalized))
        else:
            # Same tier - only update scan timestamp, NOT status_changed_at
            cursor.execute('''
                UPDATE monitored_accounts
                SET github_org = ?,
                    last_scanned_at = ?,
                    evidence_summary = ?,
                    next_scan_due = ?
                WHERE LOWER(company_name) = ?
            ''', (github_org, now, evidence_summary, next_scan_iso, company_name_normalized))

        account_id = existing['id']
    else:
        # New account - create record (use normalized name)
        cursor.execute('''
            INSERT INTO monitored_accounts (
                company_name, github_org, current_tier, last_scanned_at,
                status_changed_at, evidence_summary, next_scan_due
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (company_name_normalized, github_org, new_tier, now, now, evidence_summary, next_scan_iso))
        account_id = cursor.lastrowid
        tier_changed = True  # New account is considered a "change"

    conn.commit()
    conn.close()

    tier_config = TIER_CONFIG.get(new_tier, TIER_CONFIG[TIER_TRACKING])

    # Determine if a webhook event should be triggered
    # Webhook fires when tier changes to Thinking (1) or Preparing (2)
    webhook_event = tier_changed and new_tier in (TIER_THINKING, TIER_PREPARING)

    return {
        'account_id': account_id,
        'company_name': company_name_normalized,
        'tier': new_tier,
        'tier_name': tier_config['name'],
        'tier_status': tier_config['status'],
        'tier_changed': tier_changed,
        'evidence': evidence_summary,
        'report_id': report_id,
        'webhook_event': webhook_event,
        'revenue': existing['annual_revenue'] if existing else None
    }


def add_account_to_tier_0(company_name: str, github_org: str, annual_revenue: Optional[str] = None, website: Optional[str] = None) -> dict:
    """
    Add or update a company account to Tier 0 (Tracking) status.

    Used by the Grow pipeline for bulk imports. If account already exists,
    updates the github_org and timestamps.

    Args:
        company_name: The company name.
        github_org: The GitHub organization login.
        annual_revenue: Optional annual revenue string (e.g., "$50M", "$4.6B").
        website: Optional company website URL.

    Returns:
        Dictionary with account creation/update result.
    """
    # Normalize company name to lowercase to prevent duplicates
    company_name_normalized = company_name.lower().strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    # Set next scan due to 7 days from now
    next_scan = datetime.now() + timedelta(days=7)
    next_scan_iso = next_scan.isoformat()

    # Check if account exists by COMPANY NAME (case-insensitive)
    cursor.execute(
        'SELECT * FROM monitored_accounts WHERE LOWER(company_name) = ?',
        (company_name_normalized,)
    )
    existing_by_name = cursor.fetchone()

    # Check if account exists by GITHUB ORG (case-insensitive)
    existing_by_org = None
    if github_org:
        cursor.execute(
            'SELECT * FROM monitored_accounts WHERE LOWER(github_org) = ?',
            (github_org.lower(),)
        )
        existing_by_org = cursor.fetchone()

    # Determine which account to use (prioritize existing record)
    existing = existing_by_name or existing_by_org

    if existing:
        # Update existing account - don't change last_scanned_at
        # Only update annual_revenue/website if new values are provided
        if annual_revenue or website:
            # Build dynamic update based on what's provided
            update_fields = ['github_org = ?', 'next_scan_due = ?']
            update_params = [github_org, next_scan_iso]
            if annual_revenue:
                update_fields.append('annual_revenue = ?')
                update_params.append(annual_revenue)
            if website:
                update_fields.append('website = ?')
                update_params.append(website)
            update_params.append(existing['id'])
            cursor.execute(f'''
                UPDATE monitored_accounts
                SET {', '.join(update_fields)}
                WHERE id = ?
            ''', update_params)
        else:
            cursor.execute('''
                UPDATE monitored_accounts
                SET github_org = ?,
                    next_scan_due = ?
                WHERE id = ?
            ''', (github_org, next_scan_iso, existing['id']))
        account_id = existing['id']

        # If we matched by org but name is different, we might want to log it or update name,
        # but changing name could be risky if unique constraint on name validation fails.
        # We'll just update the org / timestamps on the existing record.
    else:
        # Create new account at Tier 0 (use normalized name)
        # Note: last_scanned_at is NULL until a scan actually completes
        cursor.execute('''
            INSERT INTO monitored_accounts (
                company_name, github_org, annual_revenue, website, current_tier, last_scanned_at,
                status_changed_at, evidence_summary, next_scan_due
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
        ''', (company_name_normalized, github_org, annual_revenue, website, TIER_TRACKING, now,
              "Added via Grow pipeline", next_scan_iso))
        account_id = cursor.lastrowid

    conn.commit()
    conn.close()

    tier_config = TIER_CONFIG[TIER_TRACKING]

    return {
        'account_id': account_id,
        'company_name': existing['company_name'] if existing else company_name_normalized,
        'github_org': github_org,
        'tier': existing['current_tier'] if existing else TIER_TRACKING,
        'tier_name': tier_config['name'],
        'tier_status': tier_config['status']
    }


def update_account_annual_revenue(company_name: str, annual_revenue: str) -> bool:
    """
    Update the annual_revenue field for an existing account.

    Used to enrich existing accounts with revenue data from CSV re-imports.

    Args:
        company_name: The company name to update.
        annual_revenue: The annual revenue string (e.g., "$50M", "$4.6B").

    Returns:
        True if the update was successful, False if account not found.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE monitored_accounts
        SET annual_revenue = ?
        WHERE LOWER(company_name) = LOWER(?)
    ''', (annual_revenue, company_name.strip()))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def update_account_website(company_name: str, website: str) -> bool:
    """
    Update the website field for an existing account.

    Used to enrich existing accounts with website data from CSV re-imports.

    Args:
        company_name: The company name to update.
        website: The company website URL.

    Returns:
        True if the update was successful, False if account not found.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE monitored_accounts
        SET website = ?
        WHERE LOWER(company_name) = LOWER(?)
    ''', (website, company_name.strip()))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def update_account_notes(account_id: int, notes: str) -> bool:
    """
    Update the notes field for an existing account.

    Used by BDRs to track outreach status, meetings, etc.

    Args:
        account_id: The account ID to update.
        notes: The notes text.

    Returns:
        True if the update was successful, False if account not found.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE monitored_accounts
        SET notes = ?
        WHERE id = ?
    ''', (notes, account_id))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def get_all_accounts(page: int = 1, limit: int = 50, tier_filter: Optional[list] = None, search_query: Optional[str] = None) -> dict:
    """
    Get all monitored accounts with pagination, sorted by tier priority.

    Sort order: Tier 2 (Preparing) first, then Tier 1, Tier 0, Tier 3 (dimmed), and Tier 4 (invalid) last.

    Args:
        page: Page number (1-indexed, default 1)
        limit: Number of accounts per page (default 50)
        tier_filter: List of tier integers to include (optional)
        search_query: Search string for company name (optional)

    Returns:
        Dictionary with:
            - accounts: List of account dictionaries
            - total_items: Total number of accounts
            - total_pages: Total number of pages
            - current_page: Current page number
            - limit: Items per page
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Build WHERE clause
    where_clauses = []
    params = []

    if tier_filter:
        placeholders = ','.join(['?'] * len(tier_filter))
        where_clauses.append(f'current_tier IN ({placeholders})')
        params.extend(tier_filter)
    
    if search_query:
        where_clauses.append('company_name LIKE ?')
        params.append(f'%{search_query}%')

    where_sql = ''
    if where_clauses:
        where_sql = ' WHERE ' + ' AND '.join(where_clauses)

    # Get total count
    count_query = f'SELECT COUNT(*) as total FROM monitored_accounts{where_sql}'
    cursor.execute(count_query, params)
    total_items = cursor.fetchone()['total']

    # Calculate pagination
    total_pages = (total_items + limit - 1) // limit  # Ceiling division
    current_page = max(1, min(page, total_pages or 1))  # Clamp to valid range
    offset = (current_page - 1) * limit

    # Custom sort: Tier 2 first (priority 1), Tier 1 (priority 2), Tier 0 (priority 3), Tier 3 (priority 4), Tier 4 last (priority 5)
    # Use LEFT JOIN with ROW_NUMBER() to efficiently fetch latest report per company
    select_query = f'''
        SELECT
            ma.*,
            lr.id as latest_report_id
        FROM monitored_accounts ma
        LEFT JOIN (
            SELECT id, company_name
            FROM (
                SELECT id, company_name,
                       ROW_NUMBER() OVER (PARTITION BY LOWER(company_name) ORDER BY created_at DESC) as rn
                FROM reports
            )
            WHERE rn = 1
        ) lr ON LOWER(lr.company_name) = LOWER(ma.company_name)
        {where_sql}
        ORDER BY
            CASE ma.current_tier
                WHEN 2 THEN 1
                WHEN 1 THEN 2
                WHEN 0 THEN 3
                WHEN 3 THEN 4
                WHEN 4 THEN 5
                ELSE 6
            END,
            ma.status_changed_at DESC
        LIMIT ? OFFSET ?
    '''
    # We need to duplicate params for the second query execution or just reuse the list + limit/offset
    # Since we execute a new query, we need to pass the params again + limit/offset
    select_params = list(params) + [limit, offset]
    
    cursor.execute(select_query, select_params)

    rows = cursor.fetchall()
    conn.close()

    accounts = []
    for row in rows:
        account = dict(row)
        tier = account.get('current_tier') or 0
        account['tier_config'] = TIER_CONFIG.get(tier, TIER_CONFIG[TIER_TRACKING])
        accounts.append(account)

    return {
        'accounts': accounts,
        'total_items': total_items,
        'total_pages': total_pages,
        'current_page': current_page,
        'limit': limit
    }


def get_tier_counts() -> dict:
    """
    Get the count of accounts in each tier.

    Returns:
        Dictionary with tier numbers as string keys and counts as values:
        {'0': count, '1': count, '2': count, '3': count, '4': count}
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT current_tier, COUNT(*) as count
        FROM monitored_accounts
        GROUP BY current_tier
    ''')

    rows = cursor.fetchall()
    conn.close()

    # Initialize with zeros for all tiers
    tier_counts = {'0': 0, '1': 0, '2': 0, '3': 0, '4': 0}

    # Populate with actual counts
    for row in rows:
        tier = str(row['current_tier'] or 0)  # Handle NULL as tier 0
        if tier in tier_counts:
            tier_counts[tier] = row['count']

    return tier_counts


def get_all_accounts_datatable(draw: int, start: int, length: int, search_value: str = '',
                               tier_filter: Optional[list] = None, order_column: int = 0,
                               order_dir: str = 'asc') -> dict:
    """
    Get accounts data in DataTables format for server-side processing.

    This function supports DataTables server-side processing with:
    - Pagination (start, length)
    - Global search (search_value)
    - Tier filtering
    - Sorting

    Args:
        draw: DataTables draw counter (for pagination)
        start: Start row index
        length: Number of rows to return
        search_value: Global search string (searches company_name and github_org)
        tier_filter: List of tier integers to include (optional)
        order_column: Column index for sorting (0=company, 1=org, 2=tier, etc.)
        order_dir: Sort direction ('asc' or 'desc')

    Returns:
        Dictionary with DataTables format:
        - draw: Same draw counter
        - recordsTotal: Total records in database
        - recordsFiltered: Total records after filtering
        - data: Array of account objects
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Column mapping for sorting (must match table column order)
    column_map = {
        0: 'company_name',
        1: 'annual_revenue',
        2: 'github_org',
        3: 'current_tier',
        4: 'last_scanned_at',
        5: 'evidence_summary',
    }

    # Get total count without filters
    cursor.execute('SELECT COUNT(*) as total FROM monitored_accounts')
    total_records = cursor.fetchone()['total']

    # Build WHERE clause for filtering
    where_clauses = []
    params = []

    # Tier filter
    if tier_filter:
        placeholders = ','.join(['?'] * len(tier_filter))
        where_clauses.append(f'current_tier IN ({placeholders})')
        params.extend(tier_filter)

    # Global search - searches both company_name and github_org
    if search_value:
        where_clauses.append('(LOWER(company_name) LIKE ? OR LOWER(github_org) LIKE ?)')
        search_param = f'%{search_value.lower()}%'
        params.extend([search_param, search_param])

    where_sql = ''
    if where_clauses:
        where_sql = ' WHERE ' + ' AND '.join(where_clauses)

    # Get filtered count
    count_query = f'SELECT COUNT(*) as total FROM monitored_accounts{where_sql}'
    cursor.execute(count_query, params)
    filtered_records = cursor.fetchone()['total']

    # Determine sort column
    sort_column = column_map.get(order_column, 'company_name')
    sort_order = 'DESC' if order_dir.lower() == 'desc' else 'ASC'

    # Validate sort order
    if sort_order not in ('ASC', 'DESC'):
        sort_order = 'ASC'

    # Get paginated and sorted data
    # Use LEFT JOIN with ROW_NUMBER() to efficiently fetch latest report per company
    select_query = f'''
        SELECT
            ma.*,
            lr.id as latest_report_id
        FROM monitored_accounts ma
        LEFT JOIN (
            SELECT id, company_name
            FROM (
                SELECT id, company_name,
                       ROW_NUMBER() OVER (PARTITION BY LOWER(company_name) ORDER BY created_at DESC) as rn
                FROM reports
            )
            WHERE rn = 1
        ) lr ON LOWER(lr.company_name) = LOWER(ma.company_name)
        {where_sql}
        ORDER BY ma.{sort_column} {sort_order}
        LIMIT ? OFFSET ?
    '''

    select_params = list(params) + [length, start]
    cursor.execute(select_query, select_params)
    rows = cursor.fetchall()
    conn.close()

    accounts = []
    for row in rows:
        account = dict(row)
        tier = account.get('current_tier') or 0
        account['tier_config'] = TIER_CONFIG.get(tier, TIER_CONFIG[TIER_TRACKING])
        accounts.append(account)

    return {
        'draw': draw,
        'recordsTotal': total_records,
        'recordsFiltered': filtered_records,
        'data': accounts
    }


def get_account(account_id: int) -> Optional[dict]:
    """Get a single account by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM monitored_accounts WHERE id = ?', (account_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        account = dict(row)
        tier = account.get('current_tier') or 0
        account['tier_config'] = TIER_CONFIG.get(tier, TIER_CONFIG[TIER_TRACKING])
        return account
    return None


def get_account_by_company(company_name: str) -> Optional[dict]:
    """Get a single account by company name."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM monitored_accounts WHERE company_name = ?', (company_name,))
    row = cursor.fetchone()
    conn.close()

    if row:
        account = dict(row)
        tier = account.get('current_tier') or 0
        account['tier_config'] = TIER_CONFIG.get(tier, TIER_CONFIG[TIER_TRACKING])
        return account
    return None


def get_account_by_company_case_insensitive(company_name: str) -> Optional[dict]:
    """Get a single account by company name, ignoring case."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        'SELECT * FROM monitored_accounts WHERE LOWER(company_name) = LOWER(?)',
        (company_name,)
    )
    row = cursor.fetchone()
    conn.close()

    if row:
        account = dict(row)
        tier = account.get('current_tier') or 0
        account['tier_config'] = TIER_CONFIG.get(tier, TIER_CONFIG[TIER_TRACKING])
        return account
    return None


def delete_account(account_id: int) -> bool:
    """Delete a monitored account."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM monitored_accounts WHERE id = ?', (account_id,))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def mark_account_as_invalid(company_name: str, reason: str) -> dict:
    """
    Mark a monitored account as invalid (Tier 4 - Disqualified).

    Used when a scan fails due to:
    - GitHub organization not found
    - No public repositories
    - Other scan errors

    This sets next_scan_due to NULL so the batch scanner skips it.

    Args:
        company_name: The company name to mark as invalid.
        reason: The failure reason (e.g., 'GitHub Org not found').

    Returns:
        Dictionary with the update result.
    """
    # Normalize company name to lowercase to prevent duplicates
    company_name_normalized = company_name.lower().strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()

    # Check if account exists (case-insensitive)
    cursor.execute(
        'SELECT * FROM monitored_accounts WHERE LOWER(company_name) = ?',
        (company_name_normalized,)
    )
    existing = cursor.fetchone()

    if existing:
        # Update existing account to Tier 4
        cursor.execute('''
            UPDATE monitored_accounts
            SET current_tier = ?,
                last_scanned_at = ?,
                status_changed_at = ?,
                evidence_summary = ?,
                next_scan_due = NULL
            WHERE LOWER(company_name) = ?
        ''', (TIER_INVALID, now, now, reason, company_name_normalized))
        account_id = existing['id']
    else:
        # Create new account at Tier 4 (use normalized name)
        cursor.execute('''
            INSERT INTO monitored_accounts (
                company_name, github_org, current_tier, last_scanned_at,
                status_changed_at, evidence_summary, next_scan_due
            ) VALUES (?, ?, ?, ?, ?, ?, NULL)
        ''', (company_name_normalized, '', TIER_INVALID, now, now, reason))
        account_id = cursor.lastrowid

    conn.commit()
    conn.close()

    tier_config = TIER_CONFIG[TIER_INVALID]

    return {
        'account_id': account_id,
        'company_name': company_name_normalized,
        'tier': TIER_INVALID,
        'tier_name': tier_config['name'],
        'tier_status': tier_config['status'],
        'reason': reason
    }


def get_refreshable_accounts() -> list:
    """
    Get accounts eligible for the weekly refresh pipeline.

    Selection criteria:
    - current_tier IN (0, 1, 2) - Tracking, Thinking, or Preparing
    - last_scanned_at < 7 days ago OR last_scanned_at IS NULL

    Excludes:
    - Tier 3 (Launched) - Already localized, no need to monitor
    - Tier 4 (Invalid) - GitHub org not found or no public repos

    Returns:
        List of account dictionaries eligible for refresh.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Select accounts in Tiers 0, 1, 2 that haven't been scanned in 7+ days
    # Use LEFT JOIN with ROW_NUMBER() to efficiently fetch latest report per company
    cursor.execute('''
        SELECT
            ma.*,
            lr.id as latest_report_id
        FROM monitored_accounts ma
        LEFT JOIN (
            SELECT id, company_name
            FROM (
                SELECT id, company_name,
                       ROW_NUMBER() OVER (PARTITION BY LOWER(company_name) ORDER BY created_at DESC) as rn
                FROM reports
            )
            WHERE rn = 1
        ) lr ON LOWER(lr.company_name) = LOWER(ma.company_name)
        WHERE ma.current_tier IN (0, 1, 2)
          AND (
              ma.last_scanned_at IS NULL
              OR ma.last_scanned_at < datetime('now', '-7 days')
          )
        ORDER BY
            CASE ma.current_tier
                WHEN 2 THEN 1  -- Hot leads first
                WHEN 1 THEN 2  -- Then warm
                WHEN 0 THEN 3  -- Then tracking
            END,
            ma.last_scanned_at ASC  -- Oldest scans first
    ''')

    rows = cursor.fetchall()
    conn.close()

    accounts = []
    for row in rows:
        account = dict(row)
        tier = account.get('current_tier') or 0
        account['tier_config'] = TIER_CONFIG.get(tier, TIER_CONFIG[TIER_TRACKING])
        accounts.append(account)

    return accounts


# =============================================================================
# SYSTEM SETTINGS & STATS FUNCTIONS
# =============================================================================

def get_setting(key: str) -> Optional[str]:
    """Get a system setting value by key."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM system_settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    return row['value'] if row else None


def set_setting(key: str, value: str) -> None:
    """Set a system setting value."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO system_settings (key, value)
        VALUES (?, ?)
    ''', (key, value))
    conn.commit()
    conn.close()


def increment_daily_stat(stat_name: str, amount: int = 1) -> None:
    """
    Increment a daily statistic counter.
    
    Args:
        stat_name: One of 'scans_run', 'api_calls_estimated', 'webhooks_fired'
        amount: Amount to increment by (default 1)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Try to update existing row
    cursor.execute(f'''
        UPDATE system_stats
        SET {stat_name} = {stat_name} + ?
        WHERE date = ?
    ''', (amount, today))
    
    if cursor.rowcount == 0:
        # No row for today, create one
        cursor.execute('''
            INSERT INTO system_stats (date, scans_run, api_calls_estimated, webhooks_fired)
            VALUES (?, 0, 0, 0)
        ''', (today,))
        # Now update the stat
        cursor.execute(f'''
            UPDATE system_stats
            SET {stat_name} = {stat_name} + ?
            WHERE date = ?
        ''', (amount, today))
    
    conn.commit()
    conn.close()


def get_stats_last_n_days(days: int = 30) -> list:
    """
    Get system stats for the last N days.
    
    Returns:
        List of dicts with date, scans_run, api_calls_estimated, webhooks_fired
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT date, scans_run, api_calls_estimated, webhooks_fired
        FROM system_stats
        WHERE date >= date('now', ?)
        ORDER BY date ASC
    ''', (f'-{days} days',))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def increment_hourly_api_calls(amount: int = 1) -> None:
    """
    Increment the API calls counter for the current hour.

    The counter resets automatically at the top of each hour by using
    a unique hour_key (YYYY-MM-DD-HH format).

    Args:
        amount: Number of API calls to add (default 1)
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Generate hour key in format: YYYY-MM-DD-HH
    hour_key = datetime.now().strftime('%Y-%m-%d-%H')

    # Try to update existing row for this hour
    cursor.execute('''
        UPDATE hourly_api_stats
        SET api_calls = api_calls + ?
        WHERE hour_key = ?
    ''', (amount, hour_key))

    if cursor.rowcount == 0:
        # No row for this hour yet, create one
        cursor.execute('''
            INSERT INTO hourly_api_stats (hour_key, api_calls)
            VALUES (?, ?)
        ''', (hour_key, amount))

    conn.commit()
    conn.close()


def get_current_hour_api_calls() -> int:
    """
    Get the number of API calls made in the current hour.

    Returns:
        Number of API calls this hour (0 if no calls yet)
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Generate hour key for current hour
    hour_key = datetime.now().strftime('%Y-%m-%d-%H')

    cursor.execute('''
        SELECT api_calls FROM hourly_api_stats
        WHERE hour_key = ?
    ''', (hour_key,))

    row = cursor.fetchone()
    conn.close()

    return row['api_calls'] if row else 0


def cleanup_old_hourly_stats(hours_to_keep: int = 24) -> int:
    """
    Clean up old hourly stats entries to prevent table from growing indefinitely.

    Args:
        hours_to_keep: Number of hours of history to retain (default 24)

    Returns:
        Number of rows deleted
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Calculate cutoff hour_key
    cutoff_time = datetime.now() - timedelta(hours=hours_to_keep)
    cutoff_key = cutoff_time.strftime('%Y-%m-%d-%H')

    cursor.execute('''
        DELETE FROM hourly_api_stats
        WHERE hour_key < ?
    ''', (cutoff_key,))

    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    return deleted


def log_webhook(event_type: str, company: str, status: str) -> int:
    """
    Log a webhook delivery attempt.
    
    Args:
        event_type: Type of event (e.g., 'tier_change', 'scan_complete')
        company: Company name
        status: 'success' or 'fail'
    
    Returns:
        ID of the log entry
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO webhook_logs (event_type, company, status)
        VALUES (?, ?, ?)
    ''', (event_type, company, status))
    
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return log_id


def get_recent_webhook_logs(limit: int = 50) -> list:
    """Get recent webhook logs."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, timestamp, event_type, company, status
        FROM webhook_logs
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


# =============================================================================
# SCAN STATUS FUNCTIONS - For tracking concurrent scan jobs
# =============================================================================

# Valid scan statuses
SCAN_STATUS_IDLE = 'idle'
SCAN_STATUS_QUEUED = 'queued'
SCAN_STATUS_PROCESSING = 'processing'


def set_scan_status(company_name: str, status: str, progress: str = None, error: str = None) -> bool:
    """
    Update the scan status for a company.

    Args:
        company_name: The company name to update.
        status: One of 'idle', 'queued', 'processing'.
        progress: Optional progress message.
        error: Optional error message to store (clears previous error if None and status is not idle with error).

    Returns:
        True if the update was successful, False otherwise.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Track time for both 'processing' and 'queued' statuses for watchdog recovery
    now = datetime.now().isoformat() if status in (SCAN_STATUS_PROCESSING, SCAN_STATUS_QUEUED) else None

    if status == SCAN_STATUS_PROCESSING:
        # Clear any previous error when starting a new scan
        cursor.execute('''
            UPDATE monitored_accounts
            SET scan_status = ?, scan_progress = ?, scan_start_time = ?, last_scan_error = NULL
            WHERE company_name = ?
        ''', (status, progress, now, company_name))
    elif status == SCAN_STATUS_IDLE:
        if error:
            # Store the error when setting to idle with an error
            cursor.execute('''
                UPDATE monitored_accounts
                SET scan_status = ?, scan_progress = NULL, scan_start_time = NULL, last_scan_error = ?
                WHERE company_name = ?
            ''', (status, error, company_name))
        else:
            # Clear error on successful completion
            cursor.execute('''
                UPDATE monitored_accounts
                SET scan_status = ?, scan_progress = NULL, scan_start_time = NULL, last_scan_error = NULL
                WHERE company_name = ?
            ''', (status, company_name))
    elif status == SCAN_STATUS_QUEUED:
        # Track queue time for watchdog to recover stale queued accounts
        cursor.execute('''
            UPDATE monitored_accounts
            SET scan_status = ?, scan_progress = ?, scan_start_time = ?
            WHERE company_name = ?
        ''', (status, progress, now, company_name))
    else:
        cursor.execute('''
            UPDATE monitored_accounts
            SET scan_status = ?, scan_progress = ?
            WHERE company_name = ?
        ''', (status, progress, company_name))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def get_scan_status(company_name: str) -> Optional[dict]:
    """
    Get the current scan status for a company.

    Args:
        company_name: The company name to check.

    Returns:
        Dictionary with scan_status, scan_progress, scan_start_time, last_scan_error or None.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT scan_status, scan_progress, scan_start_time, last_scan_error
        FROM monitored_accounts
        WHERE company_name = ?
    ''', (company_name,))

    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            'scan_status': row['scan_status'] or SCAN_STATUS_IDLE,
            'scan_progress': row['scan_progress'],
            'scan_start_time': row['scan_start_time'],
            'last_scan_error': row['last_scan_error']
        }
    return None


def get_queued_and_processing_accounts() -> dict:
    """
    Get all accounts that are currently queued or processing.

    Returns:
        Dictionary with 'queued' and 'processing' lists of company names.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT company_name, scan_status, scan_start_time
        FROM monitored_accounts
        WHERE scan_status IN (?, ?)
    ''', (SCAN_STATUS_QUEUED, SCAN_STATUS_PROCESSING))

    rows = cursor.fetchall()
    conn.close()

    result = {'queued': [], 'processing': []}
    for row in rows:
        if row['scan_status'] == SCAN_STATUS_QUEUED:
            result['queued'].append(row['company_name'])
        else:
            result['processing'].append({
                'company_name': row['company_name'],
                'scan_start_time': row['scan_start_time']
            })

    return result


def get_status_counts(stuck_timeout_minutes: int = 5) -> dict:
    """
    Get counts of accounts by scan status, including stuck accounts.

    Args:
        stuck_timeout_minutes: Minutes after which a processing scan is considered stuck.

    Returns:
        Dictionary with counts for each status: idle, queued, processing, stuck.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get counts for all statuses
    cursor.execute('''
        SELECT scan_status, COUNT(*) as count
        FROM monitored_accounts
        GROUP BY scan_status
    ''')

    rows = cursor.fetchall()

    # Initialize counts
    counts = {
        'idle': 0,
        'queued': 0,
        'processing': 0,
        'stuck': 0
    }

    # Populate basic counts
    for row in rows:
        status = row['scan_status'] or SCAN_STATUS_IDLE
        if status in counts:
            counts[status] = row['count']

    # Calculate stuck accounts (processing for too long)
    from datetime import datetime, timedelta
    stuck_threshold = (datetime.now() - timedelta(minutes=stuck_timeout_minutes)).isoformat()

    cursor.execute('''
        SELECT COUNT(*) as count
        FROM monitored_accounts
        WHERE scan_status = ?
        AND scan_start_time IS NOT NULL
        AND scan_start_time < ?
    ''', (SCAN_STATUS_PROCESSING, stuck_threshold))

    stuck_row = cursor.fetchone()
    if stuck_row:
        counts['stuck'] = stuck_row['count']
        # Subtract stuck from processing count
        counts['processing'] = max(0, counts['processing'] - counts['stuck'])

    conn.close()

    return counts


def clear_stale_scan_statuses(timeout_minutes: int = 30) -> int:
    """
    Clear scan statuses that have been stuck in 'processing' or 'queued' state.

    This is a recovery mechanism for when scans fail without proper cleanup.

    Args:
        timeout_minutes: Minutes after which a scan is considered stale.

    Returns:
        Number of accounts reset.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Reset processing scans that have been running too long
    cursor.execute('''
        UPDATE monitored_accounts
        SET scan_status = ?, scan_progress = NULL, scan_start_time = NULL
        WHERE scan_status = ?
          AND scan_start_time < datetime('now', ?)
    ''', (SCAN_STATUS_IDLE, SCAN_STATUS_PROCESSING, f'-{timeout_minutes} minutes'))

    reset_count = cursor.rowcount

    conn.commit()
    conn.close()

    return reset_count


def reset_all_scan_statuses() -> int:
    """
    Reset all scan statuses to idle. Used on app startup.

    Returns:
        Number of accounts reset.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE monitored_accounts
        SET scan_status = ?, scan_progress = NULL, scan_start_time = NULL
        WHERE scan_status != ?
    ''', (SCAN_STATUS_IDLE, SCAN_STATUS_IDLE))

    reset_count = cursor.rowcount
    conn.commit()
    conn.close()

    return reset_count


def clear_misclassified_errors() -> int:
    """
    Clear last_scan_error values that contain tier evidence instead of actual errors.

    This fixes a bug where tier evidence messages (like "INFRASTRUCTURE READY: ...")
    were incorrectly stored in the last_scan_error column instead of evidence_summary.

    Returns:
        Number of accounts cleared.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Clear errors that look like tier evidence (not actual errors)
    # Actual errors have prefixes like "Failed to", "Tier classification failed:", etc.
    cursor.execute('''
        UPDATE monitored_accounts
        SET last_scan_error = NULL
        WHERE last_scan_error IS NOT NULL
          AND (
            last_scan_error LIKE 'INFRASTRUCTURE READY:%'
            OR last_scan_error LIKE 'STRATEGY SIGNAL:%'
            OR last_scan_error LIKE 'ACTIVE BUILD:%'
            OR last_scan_error LIKE 'Too Late:%'
            OR last_scan_error LIKE 'No active signals%'
            OR last_scan_error LIKE 'DISQUALIFIED:%'
            OR last_scan_error LIKE 'Organization found%'
          )
    ''')

    cleared_count = cursor.rowcount
    conn.commit()
    conn.close()

    return cleared_count


def batch_set_scan_status_queued(company_names: list) -> int:
    """
    Batch update multiple accounts to 'queued' status in a single transaction.

    This is much faster than calling set_scan_status individually for each account.

    Args:
        company_names: List of company names to queue.

    Returns:
        Number of accounts successfully queued.
    """
    if not company_names:
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()

    # Track queue time for watchdog recovery of stale queued accounts
    now = datetime.now().isoformat()

    # Use parameterized query with placeholders for all company names
    placeholders = ','.join(['?' for _ in company_names])
    cursor.execute(f'''
        UPDATE monitored_accounts
        SET scan_status = ?, scan_progress = NULL, scan_start_time = ?
        WHERE company_name IN ({placeholders})
    ''', [SCAN_STATUS_QUEUED, now] + list(company_names))

    updated_count = cursor.rowcount
    conn.commit()
    conn.close()

    return updated_count


# Duplicate cleanup check complete


# =============================================================================
# IMPORT BATCH FUNCTIONS - Persistent bulk import queue
# =============================================================================

def create_import_batch(companies_list: list) -> int:
    """
    Create a new import batch and persist it to the database.

    Args:
        companies_list: List of company items (strings or dicts with name/annual_revenue)

    Returns:
        The batch_id (integer) of the newly created batch
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    companies_json = json.dumps(companies_list)
    total_count = len(companies_list)

    cursor.execute('''
        INSERT INTO import_batches (status, total_count, processed_count, companies_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', ('pending', total_count, 0, companies_json, now, now))

    batch_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return batch_id


def get_pending_import_batches() -> list:
    """
    Get all import batches that are pending or processing.

    Used at startup to resume interrupted batches.

    Returns:
        List of batch dictionaries with id, status, total_count, processed_count, companies_json
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, status, total_count, processed_count, companies_json, created_at, updated_at
        FROM import_batches
        WHERE status IN ('pending', 'processing')
        ORDER BY created_at ASC
    ''')

    rows = cursor.fetchall()
    conn.close()

    batches = []
    for row in rows:
        batch = dict(row)
        # Parse the companies_json back to a list
        if batch.get('companies_json'):
            batch['companies'] = json.loads(batch['companies_json'])
        else:
            batch['companies'] = []
        batches.append(batch)

    return batches


def update_batch_progress(batch_id: int, processed_count: int, status: Optional[str] = None) -> bool:
    """
    Update the progress and optionally the status of an import batch.

    Args:
        batch_id: The ID of the batch to update
        processed_count: The new processed count
        status: Optional new status ('pending', 'processing', 'completed', 'failed')

    Returns:
        True if the update was successful, False otherwise
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()

    if status:
        cursor.execute('''
            UPDATE import_batches
            SET processed_count = ?, status = ?, updated_at = ?
            WHERE id = ?
        ''', (processed_count, status, now, batch_id))
    else:
        cursor.execute('''
            UPDATE import_batches
            SET processed_count = ?, updated_at = ?
            WHERE id = ?
        ''', (processed_count, now, batch_id))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def get_import_batch(batch_id: int) -> Optional[dict]:
    """
    Get a single import batch by ID.

    Args:
        batch_id: The ID of the batch to retrieve

    Returns:
        Batch dictionary or None if not found
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, status, total_count, processed_count, companies_json, created_at, updated_at
        FROM import_batches
        WHERE id = ?
    ''', (batch_id,))

    row = cursor.fetchone()
    conn.close()

    if row:
        batch = dict(row)
        if batch.get('companies_json'):
            batch['companies'] = json.loads(batch['companies_json'])
        else:
            batch['companies'] = []
        return batch

    return None


# Initialize database on module import
init_db()


def get_stale_queued_accounts(timeout_minutes: int = 30) -> list:
    """
    Get accounts that have been stuck in 'queued' status for too long.

    This retrieves accounts that were queued but never picked up by a worker,
    possibly due to executor saturation, deadlock, or app restart.

    Args:
        timeout_minutes: Minutes after which a queued account is considered 'stale'

    Returns:
        List of company names that are stale-queued
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Calculate cutoff time: now - timeout_minutes
    cutoff_time = (datetime.now() - timedelta(minutes=timeout_minutes)).isoformat()

    # Find accounts that have been 'queued' for too long
    cursor.execute('''
        SELECT company_name FROM monitored_accounts
        WHERE scan_status = 'queued'
          AND scan_start_time IS NOT NULL
          AND scan_start_time < ?
    ''', (cutoff_time,))

    accounts = [row[0] for row in cursor.fetchall()]
    conn.close()

    return accounts


def reset_stale_queued_accounts(timeout_minutes: int = 30) -> list:
    """
    Reset accounts that have been stuck in 'queued' status for too long.
    Returns the list of reset account names so they can be re-queued.

    Args:
        timeout_minutes: Minutes after which a queued account is considered 'stale'

    Returns:
        List of company names that were reset (for re-queueing)
    """
    # First get the list of accounts that will be reset
    accounts = get_stale_queued_accounts(timeout_minutes)

    if not accounts:
        return []

    conn = get_db_connection()
    cursor = conn.cursor()

    # Calculate cutoff time: now - timeout_minutes
    cutoff_time = (datetime.now() - timedelta(minutes=timeout_minutes)).isoformat()

    # Reset these accounts to 'idle' so they can be re-queued
    cursor.execute('''
        UPDATE monitored_accounts
        SET scan_status = 'idle',
            scan_progress = 'Re-queued (was stuck in queue)',
            scan_start_time = NULL
        WHERE scan_status = 'queued'
          AND scan_start_time IS NOT NULL
          AND scan_start_time < ?
    ''', (cutoff_time,))

    conn.commit()
    conn.close()

    return accounts


def get_all_queued_accounts() -> list:
    """
    Get all accounts currently in 'queued' status.
    Used for startup recovery of stuck accounts.

    Returns:
        List of company names that are currently queued
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT company_name FROM monitored_accounts
        WHERE scan_status = 'queued'
    ''')

    accounts = [row[0] for row in cursor.fetchall()]
    conn.close()

    return accounts


def reset_all_queued_to_idle() -> list:
    """
    Reset all queued accounts to idle status.
    Returns the list of accounts that were reset so they can be re-queued.

    This is used at startup to recover accounts stuck in queue from previous run.

    Returns:
        List of company names that were reset
    """
    accounts = get_all_queued_accounts()

    if not accounts:
        return []

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE monitored_accounts
        SET scan_status = 'idle',
            scan_progress = 'Reset at startup',
            scan_start_time = NULL
        WHERE scan_status = 'queued'
    ''')

    conn.commit()
    conn.close()

    return accounts
