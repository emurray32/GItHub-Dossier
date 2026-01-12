"""
SQLite database module for storing Lead Machine reports.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional
from config import Config


def get_db_connection() -> sqlite3.Connection:
    """Create a database connection with row factory."""
    os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(Config.DATABASE_PATH)
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

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_tier
        ON monitored_accounts(current_tier DESC)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_company
        ON monitored_accounts(company_name)
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

    conn.commit()
    conn.close()


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
    """Retrieve a report by ID, including associated signals."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM reports WHERE id = ?', (report_id,))
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
    TIER_TRACKING: {'name': 'Tracking', 'status': 'Cold', 'color': 'grey', 'emoji': '⚪'},
    TIER_THINKING: {'name': 'Thinking', 'status': 'Warm', 'color': 'yellow', 'emoji': '🟡'},
    TIER_PREPARING: {'name': 'Preparing', 'status': 'Hot Lead', 'color': 'green', 'emoji': '🟢'},
    TIER_LAUNCHED: {'name': 'Launched', 'status': 'Too Late', 'color': 'red', 'emoji': '🔴'},
    TIER_INVALID: {'name': 'Not Found', 'status': 'Disqualified', 'color': 'dark-grey', 'emoji': '🚫'},
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
    4. Tier 0 vs Tier 4 Split - Based on star count (>1000 = Greenfield, <1000 = Disqualified)

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
        return TIER_LAUNCHED, "🚫 Too Late: Translation files already exist in codebase."

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
            evidence = f"🔥 INFRASTRUCTURE READY: Installed {sales_name} but NO translations found."
        else:
            evidence = "🔥 INFRASTRUCTURE READY: i18n library installed but NO translations found."
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
                evidence_parts.append(f"💭 STRATEGY SIGNAL: {title}")
            else:
                evidence_parts.append(f"💭 STRATEGY SIGNAL: {rfc_count} i18n RFC/discussion(s)")

        if ghost_count > 0:
            ghost_hits = signal_summary.get('ghost_branch', {}).get('hits', [])
            if ghost_hits and isinstance(ghost_hits[0], dict):
                branch_name = ghost_hits[0].get('name', ghost_hits[0].get('ref', 'i18n branch'))[:40]
                evidence_parts.append(f"🛠️ ACTIVE BUILD: {branch_name}")
            else:
                evidence_parts.append(f"🛠️ ACTIVE BUILD: {ghost_count} i18n branch(es)")

        return TIER_THINKING, "; ".join(evidence_parts)

    # =========================================================================
    # TIER 0 vs TIER 4: No signals found - Split based on star count
    # =========================================================================
    if total_stars > 1000:
        # Major open source project with zero localization = Greenfield opportunity
        return TIER_TRACKING, f"⭐ GREENFIELD: Major Open Source Project ({total_stars:,} stars) with ZERO localization."
    else:
        # Low star count with no signals = Likely private codebase, disqualify
        return TIER_INVALID, "🚫 DISQUALIFIED: No public code signals found (Main codebase likely private)."


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

    # Calculate tier and evidence
    new_tier, evidence_summary = calculate_tier_from_scan(scan_data)

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if account exists
    cursor.execute('SELECT * FROM monitored_accounts WHERE company_name = ?', (company_name,))
    existing = cursor.fetchone()

    now = datetime.now().isoformat()
    # Set next scan due to 7 days from now
    next_scan = datetime.now()
    next_scan = next_scan.replace(day=next_scan.day + 7) if next_scan.day <= 24 else next_scan.replace(month=next_scan.month + 1, day=1)
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
                WHERE company_name = ?
            ''', (github_org, new_tier, now, now, evidence_summary, next_scan_iso, company_name))
        else:
            # Same tier - only update scan timestamp, NOT status_changed_at
            cursor.execute('''
                UPDATE monitored_accounts
                SET github_org = ?,
                    last_scanned_at = ?,
                    evidence_summary = ?,
                    next_scan_due = ?
                WHERE company_name = ?
            ''', (github_org, now, evidence_summary, next_scan_iso, company_name))

        account_id = existing['id']
    else:
        # New account - create record
        cursor.execute('''
            INSERT INTO monitored_accounts (
                company_name, github_org, current_tier, last_scanned_at,
                status_changed_at, evidence_summary, next_scan_due
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (company_name, github_org, new_tier, now, now, evidence_summary, next_scan_iso))
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
        'company_name': company_name,
        'tier': new_tier,
        'tier_name': tier_config['name'],
        'tier_status': tier_config['status'],
        'tier_changed': tier_changed,
        'evidence': evidence_summary,
        'report_id': report_id,
        'webhook_event': webhook_event
    }


def add_account_to_tier_0(company_name: str, github_org: str) -> dict:
    """
    Add or update a company account to Tier 0 (Tracking) status.

    Used by the Grow pipeline for bulk imports. If account already exists,
    updates the github_org and timestamps.

    Args:
        company_name: The company name.
        github_org: The GitHub organization login.

    Returns:
        Dictionary with account creation/update result.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    # Set next scan due to 7 days from now
    next_scan = datetime.now()
    next_scan = next_scan.replace(day=next_scan.day + 7) if next_scan.day <= 24 else next_scan.replace(month=next_scan.month + 1, day=1)
    next_scan_iso = next_scan.isoformat()

    # Check if account exists
    cursor.execute('SELECT * FROM monitored_accounts WHERE company_name = ?', (company_name,))
    existing = cursor.fetchone()

    if existing:
        # Update existing account - don't change last_scanned_at
        cursor.execute('''
            UPDATE monitored_accounts
            SET github_org = ?,
                next_scan_due = ?
            WHERE company_name = ?
        ''', (github_org, next_scan_iso, company_name))
        account_id = existing['id']
    else:
        # Create new account at Tier 0
        # Note: last_scanned_at is NULL until a scan actually completes
        cursor.execute('''
            INSERT INTO monitored_accounts (
                company_name, github_org, current_tier, last_scanned_at,
                status_changed_at, evidence_summary, next_scan_due
            ) VALUES (?, ?, ?, NULL, ?, ?, ?)
        ''', (company_name, github_org, TIER_TRACKING, now,
              "Added via Grow pipeline", next_scan_iso))
        account_id = cursor.lastrowid

    conn.commit()
    conn.close()

    tier_config = TIER_CONFIG[TIER_TRACKING]

    return {
        'account_id': account_id,
        'company_name': company_name,
        'github_org': github_org,
        'tier': TIER_TRACKING,
        'tier_name': tier_config['name'],
        'tier_status': tier_config['status']
    }


def get_all_accounts(page: int = 1, limit: int = 50, tier_filter: Optional[int] = None) -> dict:
    """
    Get all monitored accounts with pagination, sorted by tier priority.

    Sort order: Tier 2 (Preparing) first, then Tier 1, Tier 0, Tier 3 (dimmed), and Tier 4 (invalid) last.

    Args:
        page: Page number (1-indexed, default 1)
        limit: Number of accounts per page (default 50)

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

    # Get total count
    count_query = 'SELECT COUNT(*) as total FROM monitored_accounts'
    count_params = []
    if tier_filter is not None:
        count_query += ' WHERE current_tier = ?'
        count_params.append(tier_filter)
    cursor.execute(count_query, count_params)
    total_items = cursor.fetchone()['total']

    # Calculate pagination
    total_pages = (total_items + limit - 1) // limit  # Ceiling division
    current_page = max(1, min(page, total_pages or 1))  # Clamp to valid range
    offset = (current_page - 1) * limit

    # Custom sort: Tier 2 first (priority 1), Tier 1 (priority 2), Tier 0 (priority 3), Tier 3 (priority 4), Tier 4 last (priority 5)
    select_query = '''
        SELECT
            ma.*,
            (SELECT r.id FROM reports r WHERE r.company_name = ma.company_name ORDER BY r.created_at DESC LIMIT 1) as latest_report_id
        FROM monitored_accounts ma
    '''
    select_params = []
    if tier_filter is not None:
        select_query += ' WHERE ma.current_tier = ?'
        select_params.append(tier_filter)
    select_query += '''
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
    select_params.extend([limit, offset])
    cursor.execute(select_query, select_params)

    rows = cursor.fetchall()
    conn.close()

    accounts = []
    for row in rows:
        account = dict(row)
        tier = account.get('current_tier', 0)
        account['tier_config'] = TIER_CONFIG.get(tier, TIER_CONFIG[TIER_TRACKING])
        accounts.append(account)

    return {
        'accounts': accounts,
        'total_items': total_items,
        'total_pages': total_pages,
        'current_page': current_page,
        'limit': limit
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
        tier = account.get('current_tier', 0)
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
        tier = account.get('current_tier', 0)
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
        tier = account.get('current_tier', 0)
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
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()

    # Check if account exists
    cursor.execute('SELECT * FROM monitored_accounts WHERE company_name = ?', (company_name,))
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
            WHERE company_name = ?
        ''', (TIER_INVALID, now, now, reason, company_name))
        account_id = existing['id']
    else:
        # Create new account at Tier 4
        cursor.execute('''
            INSERT INTO monitored_accounts (
                company_name, github_org, current_tier, last_scanned_at,
                status_changed_at, evidence_summary, next_scan_due
            ) VALUES (?, ?, ?, ?, ?, ?, NULL)
        ''', (company_name, '', TIER_INVALID, now, now, reason))
        account_id = cursor.lastrowid

    conn.commit()
    conn.close()

    tier_config = TIER_CONFIG[TIER_INVALID]

    return {
        'account_id': account_id,
        'company_name': company_name,
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
    cursor.execute('''
        SELECT
            ma.*,
            (SELECT r.id FROM reports r WHERE r.company_name = ma.company_name ORDER BY r.created_at DESC LIMIT 1) as latest_report_id
        FROM monitored_accounts ma
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
        tier = account.get('current_tier', 0)
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


def set_scan_status(company_name: str, status: str, progress: str = None) -> bool:
    """
    Update the scan status for a company.

    Args:
        company_name: The company name to update.
        status: One of 'idle', 'queued', 'processing'.
        progress: Optional progress message.

    Returns:
        True if the update was successful, False otherwise.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat() if status == SCAN_STATUS_PROCESSING else None

    if status == SCAN_STATUS_PROCESSING:
        cursor.execute('''
            UPDATE monitored_accounts
            SET scan_status = ?, scan_progress = ?, scan_start_time = ?
            WHERE company_name = ?
        ''', (status, progress, now, company_name))
    elif status == SCAN_STATUS_IDLE:
        cursor.execute('''
            UPDATE monitored_accounts
            SET scan_status = ?, scan_progress = NULL, scan_start_time = NULL
            WHERE company_name = ?
        ''', (status, company_name))
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
        Dictionary with scan_status, scan_progress, scan_start_time or None.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT scan_status, scan_progress, scan_start_time
        FROM monitored_accounts
        WHERE company_name = ?
    ''', (company_name,))

    row = cursor.fetchone()
    conn.close()

    if row:
        return {
            'scan_status': row['scan_status'] or SCAN_STATUS_IDLE,
            'scan_progress': row['scan_progress'],
            'scan_start_time': row['scan_start_time']
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


# Initialize database on module import
init_db()
