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
            next_scan_due TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_tier
        ON monitored_accounts(current_tier DESC)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_company
        ON monitored_accounts(company_name)
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
    """Retrieve a report by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM reports WHERE id = ?', (report_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return _row_to_dict(row)
    return None


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


def get_all_accounts() -> list:
    """
    Get all monitored accounts, sorted by tier priority.

    Sort order: Tier 2 (Preparing) first, then Tier 1, Tier 0, Tier 3 (dimmed), and Tier 4 (invalid) last.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Custom sort: Tier 2 first (priority 1), Tier 1 (priority 2), Tier 0 (priority 3), Tier 3 (priority 4), Tier 4 last (priority 5)
    cursor.execute('''
        SELECT
            ma.*,
            (SELECT r.id FROM reports r WHERE r.company_name = ma.company_name ORDER BY r.created_at DESC LIMIT 1) as latest_report_id
        FROM monitored_accounts ma
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


# Initialize database on module import
init_db()
