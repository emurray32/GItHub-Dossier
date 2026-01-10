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

TIER_CONFIG = {
    TIER_TRACKING: {'name': 'Tracking', 'status': 'Cold', 'color': 'grey', 'emoji': '⚪'},
    TIER_THINKING: {'name': 'Thinking', 'status': 'Warm', 'color': 'yellow', 'emoji': '🟡'},
    TIER_PREPARING: {'name': 'Preparing', 'status': 'Hot Lead', 'color': 'green', 'emoji': '🟢'},
    TIER_LAUNCHED: {'name': 'Launched', 'status': 'Too Late', 'color': 'red', 'emoji': '🔴'},
}


def calculate_tier_from_scan(scan_data: dict) -> tuple[int, str]:
    """
    Apply strict tier logic based on scan results.

    Returns:
        Tuple of (tier_number, evidence_summary)
    """
    signal_summary = scan_data.get('signal_summary', {})

    # Extract key signals
    rfc_count = signal_summary.get('rfc_discussion', {}).get('count', 0)
    dependency_count = signal_summary.get('dependency_injection', {}).get('count', 0)
    ghost_count = signal_summary.get('ghost_branch', {}).get('count', 0)

    # Check for locale folders in the scan
    locale_folders_found = False
    dependency_hits = signal_summary.get('dependency_injection', {}).get('hits', [])
    for hit in dependency_hits:
        if isinstance(hit, dict) and hit.get('has_locale_folders'):
            locale_folders_found = True
            break

    # Also check goldilocks_status for launched indicator
    goldilocks_status = scan_data.get('goldilocks_status', 'none')
    if goldilocks_status == 'launched':
        locale_folders_found = True

    # Apply STRICT tier logic (order matters - most specific first)

    # Tier 3: Launched/Active - locale folders OR ghost branches
    if locale_folders_found or ghost_count > 0:
        evidence = []
        if locale_folders_found:
            evidence.append("Locale folders detected")
        if ghost_count > 0:
            evidence.append(f"{ghost_count} i18n branch(es) found")
        return TIER_LAUNCHED, "; ".join(evidence)

    # Tier 2: Preparing (GOLDILOCKS) - dependencies WITHOUT locale folders
    if dependency_count > 0 and not locale_folders_found:
        dep_names = []
        for hit in dependency_hits:
            if isinstance(hit, dict):
                dep_names.append(hit.get('library', hit.get('name', 'unknown')))
        evidence = f"i18n libraries installed: {', '.join(dep_names[:3])}"
        if len(dep_names) > 3:
            evidence += f" (+{len(dep_names) - 3} more)"
        return TIER_PREPARING, evidence

    # Tier 1: Thinking - RFC discussions found
    if rfc_count > 0:
        rfc_hits = signal_summary.get('rfc_discussion', {}).get('hits', [])
        if rfc_hits and isinstance(rfc_hits[0], dict):
            evidence = f"{rfc_count} RFC/discussion(s): {rfc_hits[0].get('title', 'i18n discussion')[:50]}"
        else:
            evidence = f"{rfc_count} i18n RFC/discussion(s) found"
        return TIER_THINKING, evidence

    # Tier 0: Tracking - No signals
    return TIER_TRACKING, "No localization signals detected"


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

    return {
        'account_id': account_id,
        'company_name': company_name,
        'tier': new_tier,
        'tier_name': tier_config['name'],
        'tier_status': tier_config['status'],
        'tier_changed': tier_changed,
        'evidence': evidence_summary,
        'report_id': report_id
    }


def get_all_accounts() -> list:
    """
    Get all monitored accounts, sorted by tier priority.

    Sort order: Tier 2 (Preparing) first, then Tier 1, Tier 0, and Tier 3 (dimmed) last.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Custom sort: Tier 2 first (priority 1), Tier 1 (priority 2), Tier 0 (priority 3), Tier 3 last (priority 4)
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
                ELSE 5
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


def delete_account(account_id: int) -> bool:
    """Delete a monitored account."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM monitored_accounts WHERE id = ?', (account_id,))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


# Initialize database on module import
init_db()
