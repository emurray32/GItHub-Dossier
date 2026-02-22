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
from signal_verifier import verify_signals


def get_db_connection() -> sqlite3.Connection:
    """Create a database connection with row factory and timeout."""
    os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
    # Use a 30-second timeout to handle concurrent access gracefully
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
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

    # Migrate existing tables: add archived_at column for auto-archiving Tier 4 accounts
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN archived_at TIMESTAMP')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migrate existing tables: add metadata column for storing extra CSV fields as JSON
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN metadata TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migrate: add latest_report_id column for fast report lookups (eliminates expensive ROW_NUMBER JOIN)
    try:
        cursor.execute('ALTER TABLE monitored_accounts ADD COLUMN latest_report_id INTEGER')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migrate reports table: add is_favorite column if it doesn't exist
    try:
        cursor.execute('ALTER TABLE reports ADD COLUMN is_favorite INTEGER DEFAULT 0')
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

    # Index for archived accounts (efficient filtering by archive status)
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_archived
        ON monitored_accounts(archived_at)
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

    # Index for fast latest_report_id lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_accounts_latest_report
        ON monitored_accounts(latest_report_id)
    ''')

    # Backfill latest_report_id for existing accounts (runs once, fast due to index)
    cursor.execute('''
        UPDATE monitored_accounts
        SET latest_report_id = (
            SELECT r.id FROM reports r
            WHERE LOWER(r.company_name) = LOWER(monitored_accounts.company_name)
            ORDER BY r.created_at DESC LIMIT 1
        )
        WHERE latest_report_id IS NULL
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

    # Scoring V2: Add enrichment columns to scan_signals (idempotent)
    for col_def in [
        'raw_strength REAL',
        'age_in_days INTEGER',
        'source_context TEXT',
        'woe_value REAL',
    ]:
        col_name = col_def.split()[0]
        try:
            cursor.execute(f'ALTER TABLE scan_signals ADD COLUMN {col_def}')
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Website Analyses table - stores website quality and localization assessments
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS website_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            company_name TEXT NOT NULL,
            website_url TEXT NOT NULL,
            localization_score INTEGER,
            localization_grade TEXT,
            quality_score INTEGER,
            quality_grade TEXT,
            tech_stack_json TEXT,
            analysis_details_json TEXT,
            ai_analysis TEXT,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES monitored_accounts(id)
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_website_analyses_account
        ON website_analyses(account_id)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_website_analyses_company
        ON website_analyses(company_name)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_website_analyses_analyzed_at
        ON website_analyses(analyzed_at DESC)
    ''')

    # Set default webhook_enabled to false (paused) if not already set
    cursor.execute('''
        INSERT OR IGNORE INTO system_settings (key, value)
        VALUES ('webhook_enabled', 'false')
    ''')

    # WebScraper Accounts table - for website localization analysis at scale
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS webscraper_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            website_url TEXT,

            -- Tier system (1-4, default 4)
            current_tier INTEGER DEFAULT 4,
            tier_label TEXT DEFAULT 'Not Scanned',

            -- Scores (null until scanned)
            localization_coverage_score INTEGER,
            quality_gap_score INTEGER,
            enterprise_score INTEGER,

            -- Localization metrics
            locale_count INTEGER DEFAULT 0,
            languages_detected TEXT,
            hreflang_tags TEXT,
            i18n_libraries TEXT,

            -- Scan metadata
            last_scanned_at TIMESTAMP,
            scan_status TEXT DEFAULT 'not_scanned',
            scan_error TEXT,

            -- AI prompt results storage
            last_prompt TEXT,
            last_prompt_result TEXT,
            prompt_history TEXT,

            -- Evidence & signals
            signals_json TEXT,
            evidence_summary TEXT,

            -- Linked RepoRadar account (foreign key)
            monitored_account_id INTEGER,

            -- Metadata
            notes TEXT,
            archived_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (monitored_account_id) REFERENCES monitored_accounts(id)
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_webscraper_tier
        ON webscraper_accounts(current_tier)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_webscraper_company
        ON webscraper_accounts(company_name)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_webscraper_monitored_account
        ON webscraper_accounts(monitored_account_id)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_webscraper_archived
        ON webscraper_accounts(archived_at)
    ''')

    # Contributors table - tracks top GitHub contributors across scanned repos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contributors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            github_login TEXT NOT NULL,
            github_url TEXT,
            name TEXT,
            email TEXT,
            blog TEXT,
            company TEXT,
            company_size TEXT,
            annual_revenue TEXT,
            repo_source TEXT,
            github_org TEXT,
            contributions INTEGER DEFAULT 0,
            insight TEXT,
            apollo_status TEXT DEFAULT 'not_sent',
            emails_sent INTEGER DEFAULT 0,
            enrolled_in_sequence INTEGER DEFAULT 0,
            sequence_name TEXT,
            enrolled_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(github_login, github_org)
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_contributors_login
        ON contributors(github_login)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_contributors_org
        ON contributors(github_org)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_contributors_apollo
        ON contributors(apollo_status)
    ''')

    # Migrate contributors table: add org membership classification columns
    try:
        cursor.execute('ALTER TABLE contributors ADD COLUMN is_org_member INTEGER DEFAULT NULL')
    except sqlite3.OperationalError:
        pass  # Column already exists

    try:
        cursor.execute('ALTER TABLE contributors ADD COLUMN github_profile_company TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_contributors_org_member
        ON contributors(is_org_member)
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

    # Update latest_report_id in monitored_accounts for fast lookups
    cursor.execute('''
        UPDATE monitored_accounts
        SET latest_report_id = ?
        WHERE LOWER(company_name) = LOWER(?)
    ''', (report_id, company_name))

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

            # Scoring V2 enrichment fields (optional)
            raw_strength = signal.get('raw_strength')
            age_in_days = signal.get('age_in_days')
            source_context = signal.get('source_context')
            woe_value = signal.get('woe_value')

            cursor.execute('''
                INSERT INTO scan_signals (
                    report_id, company_name, signal_type, description, file_path,
                    raw_strength, age_in_days, source_context, woe_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (report_id, company_name, signal_type, description, file_path,
                  raw_strength, age_in_days, source_context, woe_value))

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
    """Get the most recent reports, deduplicated by company name.

    Only shows the latest report for each company to avoid duplicate entries.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Use ROW_NUMBER() to get only the most recent report per company
    cursor.execute('''
        SELECT id, company_name, github_org, signals_found, repos_scanned,
               commits_analyzed, prs_analyzed, created_at, scan_duration_seconds
        FROM (
            SELECT id, company_name, github_org, signals_found, repos_scanned,
                   commits_analyzed, prs_analyzed, created_at, scan_duration_seconds,
                   ROW_NUMBER() OVER (PARTITION BY LOWER(company_name) ORDER BY created_at DESC) as rn
            FROM reports
        )
        WHERE rn = 1
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
    # ============================================================
    # SCORING V2: Use new maturity level if available
    # ============================================================
    scoring_v2 = scan_data.get('scoring_v2')
    if scoring_v2 and isinstance(scoring_v2, dict):
        maturity = scoring_v2.get('org_maturity_level', '')
        maturity_label = scoring_v2.get('org_maturity_label', '')
        confidence = scoring_v2.get('confidence_percent', 0)
        readiness = scoring_v2.get('readiness_index', 0)

        _MATURITY_TO_TIER = {
            'pre_i18n': 0,
            'preparing': 2,
            'active_implementation': 2,
            'recently_launched': 3,
            'mature_midmarket': 3,
            'enterprise_scale': 2,
        }

        tier = _MATURITY_TO_TIER.get(maturity, 0)
        outreach = scoring_v2.get('outreach_angle_label', '')
        evidence = (
            f"V2: {maturity_label} (confidence: {confidence:.0f}%, "
            f"readiness: {readiness:.2f}, outreach: {outreach})"
        )
        return tier, evidence

    # ============================================================
    # SIGNAL VERIFICATION: Filter false positives before tiering
    # ============================================================
    try:
        scan_data = verify_signals(scan_data, use_llm=True)
        verification = scan_data.get('verification', {})
        
        # If verification flagged as definitive false positive, downgrade to Tier 0
        if verification.get('is_false_positive'):
            recommended_tier = verification.get('recommended_tier', 0)
            fp_reasons = verification.get('false_positive_reasons', ['Unknown reason'])
            evidence = f"VERIFIED FALSE POSITIVE: {fp_reasons[0]}"
            print(f"[TIER] Signal verification overrode tier: {evidence}")
            return recommended_tier, evidence
    except Exception as e:
        print(f"[TIER] Signal verification error (continuing with normal tiering): {e}")

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
    # Count total distinct signal types for multi-signal requirement
    total_signal_types = sum(1 for s in [rfc_count, dependency_count, ghost_count] if s > 0)
    
    # Check for silver bullet (smoking gun fork) - this alone qualifies for Tier 2
    smoking_gun_count = signal_summary.get('smoking_gun_fork', {}).get('count', 0)
    has_silver_bullet = smoking_gun_count > 0
    
    if dependency_count > 0 and (has_silver_bullet or total_signal_types >= 2):
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

    # Single dependency signal without silver bullet -> downgrade to TIER 1 (Thinking)
    # Rationale: One i18n library alone could be a dev doing things right, not org investment
    if dependency_count > 0 and not has_silver_bullet and total_signal_types < 2:
        dep_names = []
        dependency_hits = signal_summary.get('dependency_injection', {}).get('hits', [])
        for hit in dependency_hits:
            if isinstance(hit, dict):
                libs = hit.get('libraries_found', [])
                if libs:
                    dep_names.extend(libs)
                else:
                    lib_name = hit.get('library', hit.get('name', ''))
                    if lib_name:
                        dep_names.append(lib_name)
        lib_list = ', '.join(dep_names[:3]) if dep_names else 'i18n library'
        evidence = f"Found {lib_list} (single signal - needs corroborating evidence for Hot Lead)"
        return TIER_THINKING, evidence
    
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
    # Guard against None tier values to prevent comparison errors
    new_tier = new_tier if new_tier is not None else 0

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
        existing_tier = existing_tier if existing_tier is not None else 0
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

    # Auto-archive/unarchive based on tier
    was_archived = existing['archived_at'] if existing else None
    archived = False
    unarchived = False

    if new_tier == TIER_INVALID:
        # Auto-archive Tier 4 accounts
        if not was_archived:
            cursor.execute(
                'UPDATE monitored_accounts SET archived_at = ? WHERE id = ?',
                (now, account_id)
            )
            archived = True
    else:
        # Unarchive if account was archived but now has a valid tier
        if was_archived:
            cursor.execute(
                'UPDATE monitored_accounts SET archived_at = NULL WHERE id = ?',
                (account_id,)
            )
            unarchived = True

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
        'revenue': existing['annual_revenue'] if existing else None,
        'website': existing['website'] if existing else None,
        'archived': archived,
        'unarchived': unarchived
    }


def add_account_to_tier_0(company_name: str, github_org: str, annual_revenue: Optional[str] = None, website: Optional[str] = None, metadata: Optional[dict] = None) -> dict:
    """
    Add or update a company account to Tier 0 (Tracking) status.

    Used by the Grow pipeline for bulk imports. If account already exists,
    updates the github_org and timestamps.

    Args:
        company_name: The company name.
        github_org: The GitHub organization login.
        annual_revenue: Optional annual revenue string (e.g., "$50M", "$4.6B").
        website: Optional company website URL.
        metadata: Optional dict of extra CSV fields to store as JSON.

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
        # Only update annual_revenue/website/metadata if new values are provided
        if annual_revenue or website or metadata:
            # Build dynamic update based on what's provided
            update_fields = ['github_org = ?', 'next_scan_due = ?']
            update_params = [github_org, next_scan_iso]
            if annual_revenue:
                update_fields.append('annual_revenue = ?')
                update_params.append(annual_revenue)
            if website:
                update_fields.append('website = ?')
                update_params.append(website)
            if metadata:
                # Merge with existing metadata if present
                existing_metadata = {}
                if existing['metadata']:
                    try:
                        existing_metadata = json.loads(existing['metadata'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                existing_metadata.update(metadata)
                update_fields.append('metadata = ?')
                update_params.append(json.dumps(existing_metadata))
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
        metadata_json = json.dumps(metadata) if metadata else None
        cursor.execute('''
            INSERT INTO monitored_accounts (
                company_name, github_org, annual_revenue, website, current_tier, last_scanned_at,
                status_changed_at, evidence_summary, next_scan_due, metadata
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
        ''', (company_name.strip(), github_org, annual_revenue, website, TIER_TRACKING, now,
              "Added via Grow pipeline", next_scan_iso, metadata_json))
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


def update_account_metadata(company_name: str, metadata: dict) -> bool:
    """
    Update or merge the metadata field for an existing account.

    Merges new metadata with existing metadata (new values override existing keys).
    Used to enrich existing accounts with extra CSV fields.

    Args:
        company_name: The company name to update.
        metadata: Dictionary of extra fields to store.

    Returns:
        True if the update was successful, False if account not found.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # First, fetch existing metadata to merge
    cursor.execute('''
        SELECT metadata FROM monitored_accounts
        WHERE LOWER(company_name) = LOWER(?)
    ''', (company_name.strip(),))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return False

    # Merge existing metadata with new metadata
    existing_metadata = {}
    if row['metadata']:
        try:
            existing_metadata = json.loads(row['metadata'])
        except (json.JSONDecodeError, TypeError):
            pass

    existing_metadata.update(metadata)

    cursor.execute('''
        UPDATE monitored_accounts
        SET metadata = ?
        WHERE LOWER(company_name) = LOWER(?)
    ''', (json.dumps(existing_metadata), company_name.strip()))

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
    try:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE monitored_accounts
            SET notes = ?
            WHERE id = ?
        ''', (notes, account_id))
        updated = cursor.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        print(f"[DB] Error updating notes for account {account_id}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


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

    # Build WHERE clause - always exclude archived accounts
    where_clauses = ['ma.archived_at IS NULL']
    params = []

    if tier_filter:
        placeholders = ','.join(['?'] * len(tier_filter))
        where_clauses.append(f'ma.current_tier IN ({placeholders})')
        params.extend(tier_filter)

    if search_query:
        where_clauses.append('ma.company_name LIKE ?')
        params.append(f'%{search_query}%')

    where_sql = ' WHERE ' + ' AND '.join(where_clauses)

    # Get total count (use subquery to match main query's table alias)
    count_query = f'SELECT COUNT(*) as total FROM monitored_accounts ma{where_sql}'
    cursor.execute(count_query, params)
    total_items = cursor.fetchone()['total']

    # Calculate pagination
    total_pages = (total_items + limit - 1) // limit  # Ceiling division
    current_page = max(1, min(page, total_pages or 1))  # Clamp to valid range
    offset = (current_page - 1) * limit

    # Custom sort: Tier 2 first (priority 1), Tier 1 (priority 2), Tier 0 (priority 3), Tier 3 (priority 4), Tier 4 last (priority 5)
    # Use the cached latest_report_id column (no expensive JOIN needed)
    select_query = f'''
        SELECT ma.*
        FROM monitored_accounts ma
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
    Get the count of non-archived accounts in each tier.

    Returns:
        Dictionary with tier numbers as string keys and counts as values:
        {'0': count, '1': count, '2': count, '3': count, '4': count}
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Exclude archived accounts from tier counts
    cursor.execute('''
        SELECT current_tier, COUNT(*) as count
        FROM monitored_accounts
        WHERE archived_at IS NULL
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
                               order_dir: str = 'asc', last_scanned_filter: str = None,
                               revenue_min: int = None, revenue_max: int = None) -> dict:
    """
    Get accounts data in DataTables format for server-side processing.

    This function supports DataTables server-side processing with:
    - Pagination (start, length)
    - Global search (search_value)
    - Tier filtering
    - Last scanned filtering (never, 7d, 30d, 90d, older)
    - Revenue range filtering (in millions)
    - Sorting

    Args:
        draw: DataTables draw counter (for pagination)
        start: Start row index
        length: Number of rows to return
        search_value: Global search string (searches company_name and github_org)
        tier_filter: List of tier integers to include (optional)
        order_column: Column index for sorting (0=company, 1=org, 2=tier, etc.)
        order_dir: Sort direction ('asc' or 'desc')
        last_scanned_filter: Filter by last scanned time ('never', '7d', '30d', '90d', 'older')
        revenue_min: Minimum revenue in millions (optional)
        revenue_max: Maximum revenue in millions (optional)

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

    # Get total count without filters (excluding archived)
    cursor.execute('SELECT COUNT(*) as total FROM monitored_accounts WHERE archived_at IS NULL')
    total_records = cursor.fetchone()['total']

    # Build WHERE clause for filtering - always exclude archived accounts
    where_clauses = ['archived_at IS NULL']
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

    # Last scanned filter
    if last_scanned_filter:
        if last_scanned_filter == 'never':
            where_clauses.append('last_scanned_at IS NULL')
        elif last_scanned_filter == '7d':
            where_clauses.append("last_scanned_at >= datetime('now', '-7 days')")
        elif last_scanned_filter == '30d':
            where_clauses.append("last_scanned_at >= datetime('now', '-30 days')")
        elif last_scanned_filter == '90d':
            where_clauses.append("last_scanned_at >= datetime('now', '-90 days')")
        elif last_scanned_filter == 'older':
            where_clauses.append("last_scanned_at < datetime('now', '-90 days')")

    # Revenue range filter (in millions)
    # Revenue is stored as text, so we need to parse it for comparison
    # Common formats: "$100M", "100000000", "$1.5B", "1500000000", etc.
    if revenue_min is not None or revenue_max is not None:
        # Use a subquery to parse the revenue and filter
        # This handles formats like "$100M", "$1.5B", "100000000", etc.
        revenue_clause = """
            CASE
                WHEN annual_revenue IS NULL OR annual_revenue = '' THEN NULL
                WHEN annual_revenue LIKE '%B%' OR annual_revenue LIKE '%b%' THEN
                    CAST(REPLACE(REPLACE(REPLACE(REPLACE(annual_revenue, '$', ''), 'B', ''), 'b', ''), ',', '') AS REAL) * 1000
                WHEN annual_revenue LIKE '%M%' OR annual_revenue LIKE '%m%' THEN
                    CAST(REPLACE(REPLACE(REPLACE(REPLACE(annual_revenue, '$', ''), 'M', ''), 'm', ''), ',', '') AS REAL)
                WHEN annual_revenue LIKE '%K%' OR annual_revenue LIKE '%k%' THEN
                    CAST(REPLACE(REPLACE(REPLACE(REPLACE(annual_revenue, '$', ''), 'K', ''), 'k', ''), ',', '') AS REAL) / 1000
                ELSE
                    CAST(REPLACE(REPLACE(annual_revenue, '$', ''), ',', '') AS REAL) / 1000000
            END
        """
        if revenue_min is not None and revenue_max is not None:
            where_clauses.append(f'({revenue_clause}) >= ? AND ({revenue_clause}) <= ?')
            params.extend([revenue_min, revenue_max])
        elif revenue_min is not None:
            where_clauses.append(f'({revenue_clause}) >= ?')
            params.append(revenue_min)
        elif revenue_max is not None:
            where_clauses.append(f'({revenue_clause}) <= ?')
            params.append(revenue_max)

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
    # Use the cached latest_report_id column (no expensive JOIN needed)
    select_query = f'''
        SELECT ma.*
        FROM monitored_accounts ma
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


def find_potential_duplicates(company_name: str, github_org: str = None, website: str = None) -> list:
    """
    Find potential duplicate accounts using smart matching.

    This helps prevent importing the same company multiple times by checking:
    1. Exact company name match (case-insensitive)
    2. Fuzzy company name match (normalized - removes Inc, LLC, Corp, etc.)
    3. GitHub org match (case-insensitive)
    4. Website domain match (strips www, http, etc.)

    Args:
        company_name: The company name to check
        github_org: Optional GitHub organization to match
        website: Optional website URL to match

    Returns:
        List of potential duplicate accounts with match_reason
    """
    duplicates = []
    conn = get_db_connection()
    cursor = conn.cursor()

    # Normalize the input company name
    company_normalized = _normalize_company_name(company_name)

    # 1. Exact company name match (case-insensitive)
    cursor.execute(
        'SELECT * FROM monitored_accounts WHERE LOWER(company_name) = LOWER(?)',
        (company_name.strip(),)
    )
    for row in cursor.fetchall():
        account = dict(row)
        account['match_reason'] = 'exact_name'
        account['match_confidence'] = 100
        duplicates.append(account)

    # 2. Fuzzy company name match (normalized)
    cursor.execute('SELECT * FROM monitored_accounts')
    for row in cursor.fetchall():
        account = dict(row)
        existing_normalized = _normalize_company_name(account.get('company_name', ''))

        # Skip if already matched
        if any(d['id'] == account['id'] for d in duplicates):
            continue

        # Check normalized match
        if existing_normalized and company_normalized:
            if existing_normalized == company_normalized:
                account['match_reason'] = 'normalized_name'
                account['match_confidence'] = 80
                duplicates.append(account)
            # Check if one contains the other (for variations like "Acme" vs "Acme Inc")
            elif existing_normalized in company_normalized or company_normalized in existing_normalized:
                account['match_reason'] = 'partial_name'
                account['match_confidence'] = 50
                duplicates.append(account)

    # 3. GitHub org match (if provided)
    if github_org:
        cursor.execute(
            'SELECT * FROM monitored_accounts WHERE LOWER(github_org) = LOWER(?)',
            (github_org.strip(),)
        )
        for row in cursor.fetchall():
            account = dict(row)
            # Skip if already matched
            if any(d['id'] == account['id'] for d in duplicates):
                continue
            account['match_reason'] = 'github_org'
            account['match_confidence'] = 100
            duplicates.append(account)

    # 4. Website domain match (if provided)
    if website:
        website_domain = _extract_domain(website)
        if website_domain:
            cursor.execute('SELECT * FROM monitored_accounts WHERE website IS NOT NULL')
            for row in cursor.fetchall():
                account = dict(row)
                # Skip if already matched
                if any(d['id'] == account['id'] for d in duplicates):
                    continue
                existing_domain = _extract_domain(account.get('website', ''))
                if existing_domain and existing_domain == website_domain:
                    account['match_reason'] = 'website_domain'
                    account['match_confidence'] = 100
                    duplicates.append(account)

    conn.close()

    # Sort by confidence (highest first)
    duplicates.sort(key=lambda x: x.get('match_confidence', 0), reverse=True)

    return duplicates


def find_potential_duplicates_bulk(companies: list) -> dict:
    """
    Bulk version of find_potential_duplicates for performance.

    Instead of N queries for N companies, this does:
    1. Single query to fetch all existing accounts
    2. In-memory matching for all companies

    Args:
        companies: List of dicts with {'name': str, 'github_org': str, 'website': str}

    Returns:
        Dict mapping company names to their duplicate matches:
        {
            "Shopify": [
                {
                    "id": 123,
                    "company_name": "Shopify",
                    "match_reason": "exact_name",
                    "match_confidence": 100
                }
            ],
            ...
        }
    """
    if not companies:
        return {}

    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetch ALL existing accounts once
    cursor.execute('SELECT * FROM monitored_accounts')
    all_accounts = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # Build lookup indices for fast matching
    # Index by lowercase company name
    name_index = {}
    # Index by normalized company name
    normalized_index = {}
    # Index by lowercase github_org
    github_index = {}
    # Index by website domain
    domain_index = {}

    for account in all_accounts:
        account_id = account['id']

        # Index by company name
        name = account.get('company_name', '').lower().strip()
        if name:
            if name not in name_index:
                name_index[name] = []
            name_index[name].append(account)

        # Index by normalized company name
        normalized = _normalize_company_name(account.get('company_name', ''))
        if normalized:
            if normalized not in normalized_index:
                normalized_index[normalized] = []
            normalized_index[normalized].append(account)

        # Index by github_org
        github_org = account.get('github_org', '').lower().strip()
        if github_org:
            if github_org not in github_index:
                github_index[github_org] = []
            github_index[github_org].append(account)

        # Index by website domain
        website = account.get('website', '')
        if website:
            domain = _extract_domain(website)
            if domain:
                if domain not in domain_index:
                    domain_index[domain] = []
                domain_index[domain].append(account)

    # Now check each company against the indices
    results = {}

    for company_item in companies:
        if isinstance(company_item, dict):
            company_name = company_item.get('name', '').strip()
            github_org = company_item.get('github_org', '').strip() if company_item.get('github_org') else None
            website = company_item.get('website', '').strip() if company_item.get('website') else None
        else:
            company_name = str(company_item).strip()
            github_org = None
            website = None

        if not company_name:
            continue

        duplicates = []
        seen_ids = set()

        # 1. Exact company name match
        name_lower = company_name.lower().strip()
        if name_lower in name_index:
            for account in name_index[name_lower]:
                account_copy = account.copy()
                account_copy['match_reason'] = 'exact_name'
                account_copy['match_confidence'] = 100
                duplicates.append(account_copy)
                seen_ids.add(account['id'])

        # 2. Normalized company name match
        company_normalized = _normalize_company_name(company_name)
        if company_normalized:
            if company_normalized in normalized_index:
                for account in normalized_index[company_normalized]:
                    if account['id'] not in seen_ids:
                        account_copy = account.copy()
                        account_copy['match_reason'] = 'normalized_name'
                        account_copy['match_confidence'] = 80
                        duplicates.append(account_copy)
                        seen_ids.add(account['id'])

            # Partial match check (slower, but only for non-exact matches)
            if not duplicates:
                for existing_normalized, accounts in normalized_index.items():
                    if existing_normalized in company_normalized or company_normalized in existing_normalized:
                        for account in accounts:
                            if account['id'] not in seen_ids:
                                account_copy = account.copy()
                                account_copy['match_reason'] = 'partial_name'
                                account_copy['match_confidence'] = 50
                                duplicates.append(account_copy)
                                seen_ids.add(account['id'])

        # 3. GitHub org match
        if github_org:
            github_lower = github_org.lower().strip()
            if github_lower in github_index:
                for account in github_index[github_lower]:
                    if account['id'] not in seen_ids:
                        account_copy = account.copy()
                        account_copy['match_reason'] = 'github_org'
                        account_copy['match_confidence'] = 100
                        duplicates.append(account_copy)
                        seen_ids.add(account['id'])

        # 4. Website domain match
        if website:
            domain = _extract_domain(website)
            if domain and domain in domain_index:
                for account in domain_index[domain]:
                    if account['id'] not in seen_ids:
                        account_copy = account.copy()
                        account_copy['match_reason'] = 'website_domain'
                        account_copy['match_confidence'] = 100
                        duplicates.append(account_copy)
                        seen_ids.add(account['id'])

        results[company_name] = duplicates

    return results


def _normalize_company_name(name: str) -> str:
    """
    Normalize a company name for comparison.

    Removes common suffixes and normalizes spacing/casing.
    """
    if not name:
        return ''

    # Convert to lowercase and strip
    normalized = name.lower().strip()

    # Remove common company suffixes
    suffixes = [
        ', inc.', ', inc', ' inc.', ' inc',
        ', llc', ' llc',
        ', ltd.', ', ltd', ' ltd.', ' ltd',
        ', corp.', ', corp', ' corp.', ' corp',
        ', co.', ', co', ' co.', ' co',
        ', corporation', ' corporation',
        ', incorporated', ' incorporated',
        ', limited', ' limited',
        ', gmbh', ' gmbh',
        ', s.a.', ' s.a.',
        ', ag', ' ag',
        ', plc', ' plc',
    ]

    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]

    # Remove "the " prefix
    if normalized.startswith('the '):
        normalized = normalized[4:]

    # Normalize whitespace
    normalized = ' '.join(normalized.split())

    return normalized


def _extract_domain(url: str) -> str:
    """
    Extract the base domain from a URL.

    Strips protocol, www, and path to get just the domain.
    """
    if not url:
        return ''

    # Convert to lowercase
    url = url.lower().strip()

    # Remove protocol
    for prefix in ['https://', 'http://', '//']:
        if url.startswith(prefix):
            url = url[len(prefix):]

    # Remove www.
    if url.startswith('www.'):
        url = url[4:]

    # Remove path and query string
    url = url.split('/')[0]
    url = url.split('?')[0]
    url = url.split('#')[0]

    return url


def get_import_duplicates_summary(companies_list: list) -> dict:
    """
    Check a list of companies for potential duplicates before import.

    This is useful for providing a preview of duplicates during bulk import.

    Args:
        companies_list: List of company items (strings or dicts with name/github_org/website)

    Returns:
        Dictionary with:
        - 'total': Total companies in the list
        - 'duplicates': Number of companies that would be duplicates
        - 'new': Number of new companies
        - 'details': List of duplicate details for each company
    """
    results = {
        'total': len(companies_list),
        'duplicates': 0,
        'new': 0,
        'details': []
    }

    for company_item in companies_list:
        if isinstance(company_item, dict):
            company_name = company_item.get('name', '').strip()
            github_org = company_item.get('github_org', '').strip() if company_item.get('github_org') else None
            website = company_item.get('website', '').strip() if company_item.get('website') else None
        else:
            company_name = str(company_item).strip()
            github_org = None
            website = None

        if not company_name:
            continue

        potential_dups = find_potential_duplicates(company_name, github_org, website)

        # Only count as confirmed duplicate if any match is 100% confidence
        confirmed_dups = [d for d in potential_dups if d.get('match_confidence', 0) >= 100]
        if confirmed_dups:
            results['duplicates'] += 1
            results['details'].append({
                'company': company_name,
                'matches': [
                    {
                        'existing_name': d['company_name'],
                        'match_reason': d.get('match_reason'),
                        'match_confidence': d.get('match_confidence'),
                    }
                    for d in potential_dups
                ]
            })
        elif not potential_dups:
            results['new'] += 1
        else:
            # Has potential matches but none at 100% confidence - treat as new
            results['new'] += 1

    return results


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
        # Update existing account to Tier 4 (keep on main table, do NOT auto-archive)
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
        # Create new account at Tier 4 (keep on main table, do NOT auto-archive) (use normalized name)
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


def archive_account(account_id: int) -> bool:
    """
    Archive an account by setting its archived_at timestamp.

    Archived accounts are hidden from the main accounts list but retained
    in the database for periodic re-scanning.

    Args:
        account_id: The ID of the account to archive.

    Returns:
        True if account was archived, False if not found.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    cursor.execute(
        'UPDATE monitored_accounts SET archived_at = ? WHERE id = ? AND archived_at IS NULL',
        (now, account_id)
    )

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def unarchive_account(account_id: int) -> bool:
    """
    Unarchive an account by clearing its archived_at timestamp.

    Args:
        account_id: The ID of the account to unarchive.

    Returns:
        True if account was unarchived, False if not found or not archived.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        'UPDATE monitored_accounts SET archived_at = NULL WHERE id = ? AND archived_at IS NOT NULL',
        (account_id,)
    )

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def auto_archive_tier4_accounts() -> int:
    """
    Automatically archive all non-archived Tier 4 accounts.

    Called after scans complete to hide invalid/disqualified accounts
    from the main view while retaining them for periodic re-checks.

    Returns:
        Number of accounts archived.
    """
    # DISABLED: accounts should remain on main table
    return 0

    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    cursor.execute('''
        UPDATE monitored_accounts
        SET archived_at = ?
        WHERE current_tier = ? AND archived_at IS NULL
    ''', (now, TIER_INVALID))

    archived_count = cursor.rowcount
    conn.commit()
    conn.close()

    return archived_count


def get_archived_accounts(page: int = 1, limit: int = 50, search_query: Optional[str] = None) -> dict:
    """
    Get all archived accounts with pagination.

    Args:
        page: Page number (1-indexed, default 1)
        limit: Number of accounts per page (default 50)
        search_query: Search string for company name (optional)

    Returns:
        Dictionary with accounts, pagination info, and archive stats.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Build WHERE clause
    where_clauses = ['archived_at IS NOT NULL']
    params = []

    if search_query:
        where_clauses.append('company_name LIKE ?')
        params.append(f'%{search_query}%')

    where_sql = ' WHERE ' + ' AND '.join(where_clauses)

    # Get total count
    count_query = f'SELECT COUNT(*) as total FROM monitored_accounts{where_sql}'
    cursor.execute(count_query, params)
    total_items = cursor.fetchone()['total']

    # Calculate pagination
    total_pages = (total_items + limit - 1) // limit
    current_page = max(1, min(page, total_pages or 1))
    offset = (current_page - 1) * limit

    # Get archived accounts sorted by archive date (most recent first)
    select_query = f'''
        SELECT * FROM monitored_accounts
        {where_sql}
        ORDER BY archived_at DESC
        LIMIT ? OFFSET ?
    '''

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


def get_archived_accounts_for_rescan() -> list:
    """
    Get archived accounts that need re-scanning (every 4 weeks).

    Selection criteria:
    - archived_at IS NOT NULL (account is archived)
    - last_scanned_at < 28 days ago OR last_scanned_at IS NULL

    Returns:
        List of account dictionaries eligible for re-scan.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM monitored_accounts
        WHERE archived_at IS NOT NULL
          AND (
              last_scanned_at IS NULL
              OR last_scanned_at < datetime('now', '-28 days')
          )
        ORDER BY last_scanned_at ASC
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


def get_archived_count() -> int:
    """Get the count of archived accounts."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) as count FROM monitored_accounts WHERE archived_at IS NOT NULL')
    count = cursor.fetchone()['count']

    conn.close()
    return count


def get_refreshable_accounts() -> list:
    """
    Get accounts eligible for the weekly refresh pipeline.

    Selection criteria:
    - current_tier IN (0, 1, 2) - Tracking, Thinking, or Preparing
    - last_scanned_at < 7 days ago OR last_scanned_at IS NULL
    - archived_at IS NULL (not archived)

    Excludes:
    - Tier 3 (Launched) - Already localized, no need to monitor
    - Tier 4 (Invalid) - GitHub org not found or no public repos
    - Archived accounts (handled separately with 4-week rescan cycle)

    Returns:
        List of account dictionaries eligible for refresh.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Select non-archived accounts in Tiers 0, 1, 2 that haven't been scanned in 7+ days
    # Use the cached latest_report_id column (no expensive JOIN needed)
    cursor.execute('''
        SELECT ma.*
        FROM monitored_accounts ma
        WHERE ma.archived_at IS NULL
          AND ma.current_tier IN (0, 1, 2)
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
    Update the scan status for a company with race condition protection.

    Uses conditional WHERE clauses to prevent TOCTOU races where two workers
    could claim the same company from the queue.

    Args:
        company_name: The company name to update.
        status: One of 'idle', 'queued', 'processing'.
        progress: Optional progress message.
        error: Optional error message to store (clears previous error if None and status is not idle with error).

    Returns:
        True if the update was successful, False if another worker already
        claimed the row or the row was not found.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Track time for both 'processing' and 'queued' statuses for watchdog recovery
    now = datetime.now().isoformat() if status in (SCAN_STATUS_PROCESSING, SCAN_STATUS_QUEUED) else None

    if status == SCAN_STATUS_PROCESSING:
        # Only claim if currently queued OR already processing (progress update)
        cursor.execute('''
            UPDATE monitored_accounts
            SET scan_status = ?, scan_progress = ?, scan_start_time = ?, last_scan_error = NULL
            WHERE company_name = ? AND scan_status IN (?, ?)
        ''', (status, progress, now, company_name, SCAN_STATUS_QUEUED, SCAN_STATUS_PROCESSING))
        if cursor.rowcount == 0:
            conn.close()
            print(f"[SCAN_STATUS] Could not set {company_name} to processing (already claimed or not queued)")
            return False
    elif status == SCAN_STATUS_IDLE:
        # Always allow reset to idle (cleanup path)
        if error:
            cursor.execute('''
                UPDATE monitored_accounts
                SET scan_status = ?, scan_progress = NULL, scan_start_time = NULL, last_scan_error = ?
                WHERE company_name = ?
            ''', (status, error, company_name))
        else:
            cursor.execute('''
                UPDATE monitored_accounts
                SET scan_status = ?, scan_progress = NULL, scan_start_time = NULL, last_scan_error = NULL
                WHERE company_name = ?
            ''', (status, company_name))
    elif status == SCAN_STATUS_QUEUED:
        # Only queue if currently idle (prevents double-queuing)
        cursor.execute('''
            UPDATE monitored_accounts
            SET scan_status = ?, scan_progress = ?, scan_start_time = ?
            WHERE company_name = ? AND scan_status = ?
        ''', (status, progress, now, company_name, SCAN_STATUS_IDLE))
        if cursor.rowcount == 0:
            conn.close()
            print(f"[SCAN_STATUS] Could not queue {company_name} (already queued or processing)")
            return False
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


def get_queue_account_details() -> dict:
    """
    Get full account details for all queued and processing accounts.

    Returns:
        Dictionary with 'queued' and 'processing' lists containing full account details.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT *
        FROM monitored_accounts
        WHERE scan_status IN (?, ?)
        ORDER BY
            CASE
                WHEN scan_status = ? THEN 1
                WHEN scan_status = ? THEN 2
            END,
            scan_start_time ASC
    ''', (SCAN_STATUS_QUEUED, SCAN_STATUS_PROCESSING,
          SCAN_STATUS_PROCESSING, SCAN_STATUS_QUEUED))

    rows = cursor.fetchall()
    conn.close()

    result = {'queued': [], 'processing': []}
    for row in rows:
        account = dict(row)
        if account['scan_status'] == SCAN_STATUS_QUEUED:
            result['queued'].append(account)
        else:
            result['processing'].append(account)

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


# =============================================================================
# HISTORY TAB FUNCTIONS - Enhanced reports management
# =============================================================================

def get_paginated_reports(
    page: int = 1,
    limit: int = 10,
    search_query: str = None,
    date_from: str = None,
    date_to: str = None,
    min_signals: int = None,
    max_signals: int = None,
    sort_by: str = 'created_at',
    sort_order: str = 'desc',
    favorites_only: bool = False
) -> dict:
    """
    Get paginated reports with filtering, sorting, and search.

    Args:
        page: Page number (1-indexed)
        limit: Number of reports per page
        search_query: Search string for company name or github org
        date_from: Filter reports from this date (ISO format)
        date_to: Filter reports up to this date (ISO format)
        min_signals: Minimum signals count filter
        max_signals: Maximum signals count filter
        sort_by: Column to sort by (created_at, signals_found, company_name, etc.)
        sort_order: Sort order ('asc' or 'desc')
        favorites_only: If True, only return favorited reports

    Returns:
        Dictionary with:
            - reports: List of report dictionaries
            - total_items: Total number of reports matching filters
            - total_pages: Total number of pages
            - current_page: Current page number
            - limit: Items per page
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Build WHERE clause
    where_clauses = ["rn = 1"]  # Always filter to latest per company
    params = []

    if search_query:
        where_clauses.append('(LOWER(company_name) LIKE ? OR LOWER(github_org) LIKE ?)')
        search_param = f'%{search_query.lower()}%'
        params.extend([search_param, search_param])

    if date_from:
        where_clauses.append('created_at >= ?')
        params.append(date_from)

    if date_to:
        where_clauses.append('created_at <= ?')
        params.append(date_to + ' 23:59:59')

    if min_signals is not None:
        where_clauses.append('signals_found >= ?')
        params.append(min_signals)

    if max_signals is not None:
        where_clauses.append('signals_found <= ?')
        params.append(max_signals)

    if favorites_only:
        where_clauses.append('is_favorite = 1')

    where_sql = ' WHERE ' + ' AND '.join(where_clauses)

    # Validate sort column to prevent SQL injection
    valid_sort_columns = ['created_at', 'signals_found', 'company_name', 'github_org',
                          'repos_scanned', 'commits_analyzed', 'prs_analyzed',
                          'scan_duration_seconds', 'is_favorite']
    if sort_by not in valid_sort_columns:
        sort_by = 'created_at'

    sort_order = 'DESC' if sort_order.lower() == 'desc' else 'ASC'

    # Get total count with filters (using subquery for deduplication)
    count_query = f'''
        SELECT COUNT(*) as total FROM (
            SELECT id, company_name, github_org, signals_found, repos_scanned,
                   commits_analyzed, prs_analyzed, created_at, scan_duration_seconds,
                   COALESCE(is_favorite, 0) as is_favorite,
                   ROW_NUMBER() OVER (PARTITION BY LOWER(company_name) ORDER BY created_at DESC) as rn
            FROM reports
        )
        {where_sql}
    '''
    cursor.execute(count_query, params)
    total_items = cursor.fetchone()['total']

    # Calculate pagination
    total_pages = (total_items + limit - 1) // limit if total_items > 0 else 1
    current_page = max(1, min(page, total_pages))
    offset = (current_page - 1) * limit

    # Get paginated data
    select_query = f'''
        SELECT id, company_name, github_org, signals_found, repos_scanned,
               commits_analyzed, prs_analyzed, created_at, scan_duration_seconds,
               COALESCE(is_favorite, 0) as is_favorite
        FROM (
            SELECT id, company_name, github_org, signals_found, repos_scanned,
                   commits_analyzed, prs_analyzed, created_at, scan_duration_seconds,
                   COALESCE(is_favorite, 0) as is_favorite,
                   ROW_NUMBER() OVER (PARTITION BY LOWER(company_name) ORDER BY created_at DESC) as rn
            FROM reports
        )
        {where_sql}
        ORDER BY {sort_by} {sort_order}
        LIMIT ? OFFSET ?
    '''

    cursor.execute(select_query, params + [limit, offset])
    rows = cursor.fetchall()
    conn.close()

    return {
        'reports': [dict(row) for row in rows],
        'total_items': total_items,
        'total_pages': total_pages,
        'current_page': current_page,
        'limit': limit
    }


def toggle_report_favorite(report_id: int) -> dict:
    """
    Toggle the favorite status of a report.

    Args:
        report_id: The ID of the report to toggle

    Returns:
        Dictionary with 'success' and 'is_favorite' status
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get current favorite status
    cursor.execute('SELECT is_favorite FROM reports WHERE id = ?', (report_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return {'success': False, 'error': 'Report not found'}

    current_favorite = row['is_favorite'] or 0
    new_favorite = 0 if current_favorite else 1

    cursor.execute('UPDATE reports SET is_favorite = ? WHERE id = ?', (new_favorite, report_id))
    conn.commit()
    conn.close()

    return {'success': True, 'is_favorite': bool(new_favorite)}


def delete_report_by_id(report_id: int) -> dict:
    """
    Delete a report and its associated signals.

    Args:
        report_id: The ID of the report to delete

    Returns:
        Dictionary with 'success' status
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if report exists
    cursor.execute('SELECT id FROM reports WHERE id = ?', (report_id,))
    if not cursor.fetchone():
        conn.close()
        return {'success': False, 'error': 'Report not found'}

    # Delete associated signals first
    cursor.execute('DELETE FROM scan_signals WHERE report_id = ?', (report_id,))

    # Delete the report
    cursor.execute('DELETE FROM reports WHERE id = ?', (report_id,))

    conn.commit()
    conn.close()

    return {'success': True}


def get_report_preview(report_id: int) -> Optional[dict]:
    """
    Get a lightweight preview of a report for quick view.

    Args:
        report_id: The ID of the report

    Returns:
        Dictionary with preview data or None if not found
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT r.id, r.company_name, r.github_org, r.signals_found, r.repos_scanned,
               r.commits_analyzed, r.prs_analyzed, r.created_at, r.scan_duration_seconds,
               COALESCE(r.is_favorite, 0) as is_favorite, r.ai_analysis
        FROM reports r
        WHERE r.id = ?
    ''', (report_id,))

    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    report = dict(row)

    # Parse AI analysis for summary
    if report.get('ai_analysis'):
        try:
            ai_data = json.loads(report['ai_analysis'])
            report['ai_summary'] = ai_data.get('executive_summary', ai_data.get('summary', ''))
            report['ai_priority'] = ai_data.get('priority', ai_data.get('lead_priority', 'unknown'))
        except (json.JSONDecodeError, TypeError):
            report['ai_summary'] = ''
            report['ai_priority'] = 'unknown'
        del report['ai_analysis']  # Don't send full analysis in preview

    # Get top 5 signals for preview
    cursor.execute('''
        SELECT signal_type, description
        FROM scan_signals
        WHERE report_id = ?
        ORDER BY timestamp DESC
        LIMIT 5
    ''', (report_id,))

    signal_rows = cursor.fetchall()
    report['top_signals'] = [dict(s) for s in signal_rows]

    conn.close()
    return report


# =============================================================================
# WEBSITE ANALYSIS FUNCTIONS
# =============================================================================

def save_website_analysis(
    company_name: str,
    website_url: str,
    localization_score: dict,
    quality_metrics: dict,
    tech_stack: dict,
    ai_analysis: Optional[str] = None,
    account_id: Optional[int] = None
) -> int:
    """
    Save a website analysis to the database.

    Args:
        company_name: Company name
        website_url: Website URL analyzed
        localization_score: Localization score dictionary
        quality_metrics: Quality metrics dictionary
        tech_stack: Technical stack dictionary
        ai_analysis: Optional AI analysis text
        account_id: Optional monitored_accounts ID

    Returns:
        ID of the saved analysis
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Extract scores and grades
    loc_score = localization_score.get('score', 0)
    loc_grade = localization_score.get('grade', 'F')
    qual_score = quality_metrics.get('overall_score', 0)
    qual_grade = quality_metrics.get('overall_grade', 'F')

    # Store full details as JSON
    analysis_details = {
        'localization': localization_score,
        'quality': quality_metrics,
    }

    cursor.execute('''
        INSERT INTO website_analyses (
            account_id, company_name, website_url,
            localization_score, localization_grade,
            quality_score, quality_grade,
            tech_stack_json, analysis_details_json, ai_analysis
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        account_id, company_name, website_url,
        loc_score, loc_grade,
        qual_score, qual_grade,
        json.dumps(tech_stack), json.dumps(analysis_details), ai_analysis
    ))

    analysis_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return analysis_id


def get_website_analysis(analysis_id: int) -> Optional[dict]:
    """
    Get a website analysis by ID.

    Args:
        analysis_id: The analysis ID

    Returns:
        Dictionary with analysis data or None if not found
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM website_analyses
        WHERE id = ?
    ''', (analysis_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    analysis = dict(row)

    # Parse JSON fields
    if analysis.get('tech_stack_json'):
        try:
            analysis['tech_stack'] = json.loads(analysis['tech_stack_json'])
        except (json.JSONDecodeError, TypeError):
            analysis['tech_stack'] = {}

    if analysis.get('analysis_details_json'):
        try:
            analysis['analysis_details'] = json.loads(analysis['analysis_details_json'])
        except (json.JSONDecodeError, TypeError):
            analysis['analysis_details'] = {}

    return analysis


def get_latest_website_analysis(company_name: str) -> Optional[dict]:
    """
    Get the most recent website analysis for a company.

    Args:
        company_name: Company name

    Returns:
        Dictionary with analysis data or None if not found
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT * FROM website_analyses
        WHERE LOWER(company_name) = LOWER(?)
        ORDER BY analyzed_at DESC
        LIMIT 1
    ''', (company_name,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    analysis = dict(row)

    # Parse JSON fields
    if analysis.get('tech_stack_json'):
        try:
            analysis['tech_stack'] = json.loads(analysis['tech_stack_json'])
        except (json.JSONDecodeError, TypeError):
            analysis['tech_stack'] = {}

    if analysis.get('analysis_details_json'):
        try:
            analysis['analysis_details'] = json.loads(analysis['analysis_details_json'])
        except (json.JSONDecodeError, TypeError):
            analysis['analysis_details'] = {}

    return analysis


def get_all_website_analyses(limit: int = 100, offset: int = 0) -> list:
    """
    Get all website analyses with pagination.

    Args:
        limit: Maximum number of results
        offset: Number of results to skip

    Returns:
        List of analysis dictionaries
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT id, company_name, website_url,
               localization_score, localization_grade,
               quality_score, quality_grade,
               analyzed_at
        FROM website_analyses
        ORDER BY analyzed_at DESC
        LIMIT ? OFFSET ?
    ''', (limit, offset))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_accounts_with_websites(include_analyzed: bool = False) -> list:
    """
    Get all monitored accounts that have a website URL.

    Args:
        include_analyzed: If False, exclude accounts with existing analyses

    Returns:
        List of account dictionaries with website URLs
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if include_analyzed:
        # Get all accounts with websites
        cursor.execute('''
            SELECT id, company_name, website, github_org, annual_revenue
            FROM monitored_accounts
            WHERE website IS NOT NULL
              AND website != ''
              AND archived_at IS NULL
            ORDER BY company_name
        ''')
    else:
        # Exclude accounts that already have analyses
        cursor.execute('''
            SELECT ma.id, ma.company_name, ma.website, ma.github_org, ma.annual_revenue
            FROM monitored_accounts ma
            LEFT JOIN website_analyses wa ON ma.id = wa.account_id
            WHERE ma.website IS NOT NULL
              AND ma.website != ''
              AND ma.archived_at IS NULL
              AND wa.id IS NULL
            ORDER BY ma.company_name
        ''')

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def delete_website_analysis(analysis_id: int) -> bool:
    """
    Delete a website analysis.

    Args:
        analysis_id: The analysis ID to delete

    Returns:
        True if deleted, False if not found
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM website_analyses WHERE id = ?', (analysis_id,))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


# =============================================================================
# WEBSCRAPER ACCOUNTS - Website localization analysis at scale
# =============================================================================

# WebScraper Tier Configuration
WEBSCRAPER_TIER_CONFIG = {
    1: {'name': 'Global Leader', 'color': '#10b981', 'description': 'Mature global presence with 10+ locales'},
    2: {'name': 'Active Expansion', 'color': '#3b82f6', 'description': 'Already global, expanding to new markets'},
    3: {'name': 'Going Global', 'color': '#f59e0b', 'description': 'First-time global expansion'},
    4: {'name': 'Not Yet Global', 'color': '#6b7280', 'description': 'No localization signals - potential prospect'},
}


def populate_webscraper_from_reporadar() -> dict:
    """
    Populate webscraper_accounts from monitored_accounts (RepoRadar).

    For each RepoRadar account:
    - Creates a corresponding webscraper_accounts row if it doesn't exist
    - Copies company_name
    - Links via monitored_account_id
    - Extracts website_url from the monitored_accounts website field
    - Sets current_tier = 4, tier_label = 'Not Scanned', scan_status = 'not_scanned'

    Does NOT trigger any scans.

    Returns:
        Dictionary with migration results: {created: int, skipped: int, errors: int}
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    created_count = 0
    skipped_count = 0
    error_count = 0

    try:
        # Get all monitored accounts that have a website
        cursor.execute('''
            SELECT id, company_name, website
            FROM monitored_accounts
            WHERE archived_at IS NULL
            AND website IS NOT NULL
            AND TRIM(website) != ''
        ''')
        accounts = cursor.fetchall()

        for account in accounts:
            try:
                # Check if already exists in webscraper_accounts
                cursor.execute('''
                    SELECT id FROM webscraper_accounts
                    WHERE monitored_account_id = ?
                ''', (account['id'],))

                if cursor.fetchone():
                    skipped_count += 1
                    continue

                # Create new webscraper account
                cursor.execute('''
                    INSERT INTO webscraper_accounts (
                        company_name,
                        website_url,
                        current_tier,
                        tier_label,
                        scan_status,
                        monitored_account_id,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, 4, 'Not Scanned', 'not_scanned', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ''', (account['company_name'], account['website'], account['id']))

                created_count += 1

            except Exception as e:
                print(f"[WEBSCRAPER] Error creating account for {account['company_name']}: {e}")
                error_count += 1
                continue

        conn.commit()

    except Exception as e:
        print(f"[WEBSCRAPER] Migration error: {e}")
        conn.rollback()
    finally:
        conn.close()

    return {
        'created': created_count,
        'skipped': skipped_count,
        'errors': error_count
    }


def is_webscraper_accounts_empty() -> bool:
    """
    Check if the webscraper_accounts table has any records.

    Returns:
        True if the table is empty, False otherwise.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) as count FROM webscraper_accounts')
    row = cursor.fetchone()
    conn.close()

    return row['count'] == 0


def get_webscraper_tier_counts() -> dict:
    """
    Get the count of non-archived webscraper accounts in each tier.

    Returns:
        Dictionary with tier numbers as string keys and counts as values:
        {'1': count, '2': count, '3': count, '4': count, 'archived': count}
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get tier counts excluding archived
    cursor.execute('''
        SELECT current_tier, COUNT(*) as count
        FROM webscraper_accounts
        WHERE archived_at IS NULL
        GROUP BY current_tier
    ''')

    rows = cursor.fetchall()

    # Get archived count
    cursor.execute('''
        SELECT COUNT(*) as count
        FROM webscraper_accounts
        WHERE archived_at IS NOT NULL
    ''')
    archived_count = cursor.fetchone()['count']

    conn.close()

    # Initialize with zeros for all tiers
    tier_counts = {'1': 0, '2': 0, '3': 0, '4': 0, 'archived': archived_count}

    # Populate with actual counts
    for row in rows:
        tier = str(row['current_tier'] or 4)
        if tier in tier_counts:
            tier_counts[tier] = row['count']

    return tier_counts


def get_webscraper_accounts_datatable(
    draw: int,
    start: int,
    length: int,
    search_value: str = '',
    tier_filter: Optional[list] = None,
    order_column: int = 0,
    order_dir: str = 'asc'
) -> dict:
    """
    Get webscraper accounts data in DataTables format for server-side processing.

    Args:
        draw: DataTables draw counter
        start: Start row index
        length: Number of rows to return
        search_value: Global search string
        tier_filter: List of tier integers to include
        order_column: Column index for sorting
        order_dir: Sort direction ('asc' or 'desc')

    Returns:
        Dictionary with DataTables format
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Column mapping for sorting
    column_map = {
        0: 'company_name',
        1: 'website_url',
        2: 'current_tier',
        3: 'locale_count',
        4: 'localization_coverage_score',
        5: 'last_scanned_at',
        6: 'evidence_summary',
    }

    # Get total count without filters (excluding archived)
    cursor.execute('SELECT COUNT(*) as total FROM webscraper_accounts WHERE archived_at IS NULL')
    total_records = cursor.fetchone()['total']

    # Build WHERE clause - always exclude archived
    where_clauses = ['archived_at IS NULL']
    params = []

    # Tier filter
    if tier_filter:
        placeholders = ','.join(['?'] * len(tier_filter))
        where_clauses.append(f'current_tier IN ({placeholders})')
        params.extend(tier_filter)

    # Global search
    if search_value:
        where_clauses.append('(LOWER(company_name) LIKE ? OR LOWER(website_url) LIKE ?)')
        search_param = f'%{search_value.lower()}%'
        params.extend([search_param, search_param])

    where_sql = ' WHERE ' + ' AND '.join(where_clauses)

    # Get filtered count
    count_query = f'SELECT COUNT(*) as total FROM webscraper_accounts{where_sql}'
    cursor.execute(count_query, params)
    filtered_records = cursor.fetchone()['total']

    # Determine sort column
    sort_column = column_map.get(order_column, 'company_name')
    sort_order = 'DESC' if order_dir.lower() == 'desc' else 'ASC'

    # Get paginated and sorted data
    select_query = f'''
        SELECT
            id, company_name, website_url, current_tier, tier_label,
            localization_coverage_score, quality_gap_score, enterprise_score,
            locale_count, languages_detected, hreflang_tags, i18n_libraries,
            last_scanned_at, scan_status, scan_error,
            signals_json, evidence_summary, notes,
            monitored_account_id, created_at, updated_at
        FROM webscraper_accounts
        {where_sql}
        ORDER BY {sort_column} {sort_order}
        LIMIT ? OFFSET ?
    '''

    select_params = list(params) + [length, start]
    cursor.execute(select_query, select_params)
    rows = cursor.fetchall()
    conn.close()

    # Format data for DataTables
    data = []
    for row in rows:
        account = dict(row)
        account['tier_config'] = WEBSCRAPER_TIER_CONFIG.get(account['current_tier'], WEBSCRAPER_TIER_CONFIG[4])
        data.append(account)

    return {
        'draw': draw,
        'recordsTotal': total_records,
        'recordsFiltered': filtered_records,
        'data': data
    }


def update_webscraper_notes(account_id: int, notes: str) -> bool:
    """Update the notes field for a webscraper account."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE webscraper_accounts
        SET notes = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (notes, account_id))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def archive_webscraper_account(account_id: int) -> bool:
    """Archive a webscraper account."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE webscraper_accounts
        SET archived_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND archived_at IS NULL
    ''', (account_id,))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def unarchive_webscraper_account(account_id: int) -> bool:
    """Unarchive a webscraper account."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE webscraper_accounts
        SET archived_at = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND archived_at IS NOT NULL
    ''', (account_id,))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


def delete_webscraper_account(account_id: int) -> bool:
    """Delete a webscraper account."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('DELETE FROM webscraper_accounts WHERE id = ?', (account_id,))
    deleted = cursor.rowcount > 0

    conn.commit()
    conn.close()

    return deleted


def get_webscraper_archived_count() -> int:
    """Get the count of archived webscraper accounts."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) as count FROM webscraper_accounts WHERE archived_at IS NOT NULL')
    count = cursor.fetchone()['count']

    conn.close()
    return count


def webscraper_bulk_archive(account_ids: list) -> int:
    """Archive multiple webscraper accounts."""
    if not account_ids:
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()

    placeholders = ','.join(['?'] * len(account_ids))
    cursor.execute(f'''
        UPDATE webscraper_accounts
        SET archived_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id IN ({placeholders}) AND archived_at IS NULL
    ''', account_ids)

    updated = cursor.rowcount
    conn.commit()
    conn.close()

    return updated


def webscraper_bulk_delete(account_ids: list) -> int:
    """Delete multiple webscraper accounts."""
    if not account_ids:
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()

    placeholders = ','.join(['?'] * len(account_ids))
    cursor.execute(f'DELETE FROM webscraper_accounts WHERE id IN ({placeholders})', account_ids)

    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    return deleted


def webscraper_bulk_change_tier(account_ids: list, new_tier: int) -> int:
    """Change tier for multiple webscraper accounts."""
    if not account_ids or new_tier not in WEBSCRAPER_TIER_CONFIG:
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()

    tier_label = WEBSCRAPER_TIER_CONFIG[new_tier]['name']
    placeholders = ','.join(['?'] * len(account_ids))

    cursor.execute(f'''
        UPDATE webscraper_accounts
        SET current_tier = ?, tier_label = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id IN ({placeholders})
    ''', [new_tier, tier_label] + account_ids)

    updated = cursor.rowcount
    conn.commit()
    conn.close()

    return updated


def get_webscraper_account(account_id: int) -> Optional[dict]:
    """Get a single webscraper account by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM webscraper_accounts WHERE id = ?', (account_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def update_webscraper_scan_results(account_id: int, scan_results: dict) -> bool:
    """
    Update a webscraper account with scan results.

    Args:
        account_id: The account ID to update
        scan_results: Dictionary containing scan results with keys like:
            - tier: int (1-4)
            - tier_label: str
            - localization_coverage_score: int (0-100)
            - quality_gap_score: int (0-100)
            - enterprise_score: int (0-100)
            - locale_count: int
            - languages_detected: list or JSON string
            - hreflang_tags: list or JSON string
            - i18n_libraries: list or JSON string
            - signals_json: dict or JSON string (expansion signals)
            - evidence_summary: str
            - scan_error: str (if scan failed)

    Returns:
        True if update was successful
    """
    import json

    conn = get_db_connection()
    cursor = conn.cursor()

    # Convert lists to JSON strings if needed
    languages = scan_results.get('languages_detected', [])
    if isinstance(languages, list):
        languages = json.dumps(languages)

    hreflang = scan_results.get('hreflang_tags', [])
    if isinstance(hreflang, list):
        hreflang = json.dumps(hreflang)

    i18n_libs = scan_results.get('i18n_libraries', [])
    if isinstance(i18n_libs, list):
        i18n_libs = json.dumps(i18n_libs)

    signals = scan_results.get('signals_json', {})
    if isinstance(signals, dict):
        signals = json.dumps(signals)

    # Determine scan status
    scan_status = 'error' if scan_results.get('scan_error') else 'completed'

    cursor.execute('''
        UPDATE webscraper_accounts
        SET
            current_tier = ?,
            tier_label = ?,
            localization_coverage_score = ?,
            quality_gap_score = ?,
            enterprise_score = ?,
            locale_count = ?,
            languages_detected = ?,
            hreflang_tags = ?,
            i18n_libraries = ?,
            signals_json = ?,
            evidence_summary = ?,
            scan_status = ?,
            scan_error = ?,
            last_scanned_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (
        scan_results.get('tier', 4),
        scan_results.get('tier_label', 'Not Scanned'),
        scan_results.get('localization_coverage_score', 0),
        scan_results.get('quality_gap_score', 100),
        scan_results.get('enterprise_score', 0),
        scan_results.get('locale_count', 0),
        languages,
        hreflang,
        i18n_libs,
        signals,
        scan_results.get('evidence_summary', ''),
        scan_status,
        scan_results.get('scan_error'),
        account_id
    ))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()

    return updated


# =============================================================================
# CONTRIBUTORS - GitHub contributor tracking for BDR outreach
# =============================================================================

def save_contributor(contributor_data: dict) -> Optional[int]:
    """
    Save or update a contributor record.
    Uses UPSERT on (github_login, github_org) to avoid duplicates.

    Returns the contributor ID or None on failure.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('''
            INSERT INTO contributors (
                github_login, github_url, name, email, blog,
                company, company_size, annual_revenue, repo_source,
                github_org, contributions, insight,
                is_org_member, github_profile_company
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(github_login, github_org) DO UPDATE SET
                name = COALESCE(NULLIF(excluded.name, ''), contributors.name),
                email = COALESCE(NULLIF(excluded.email, ''), contributors.email),
                blog = COALESCE(NULLIF(excluded.blog, ''), contributors.blog),
                company = COALESCE(NULLIF(excluded.company, ''), contributors.company),
                company_size = COALESCE(NULLIF(excluded.company_size, ''), contributors.company_size),
                annual_revenue = COALESCE(NULLIF(excluded.annual_revenue, ''), contributors.annual_revenue),
                repo_source = COALESCE(NULLIF(excluded.repo_source, ''), contributors.repo_source),
                contributions = CASE WHEN excluded.contributions > 0 THEN excluded.contributions ELSE contributors.contributions END,
                insight = COALESCE(NULLIF(excluded.insight, ''), contributors.insight),
                is_org_member = excluded.is_org_member,
                github_profile_company = COALESCE(NULLIF(excluded.github_profile_company, ''), contributors.github_profile_company),
                updated_at = CURRENT_TIMESTAMP
        ''', (
            contributor_data.get('github_login', ''),
            contributor_data.get('github_url', ''),
            contributor_data.get('name', ''),
            contributor_data.get('email', ''),
            contributor_data.get('blog', ''),
            contributor_data.get('company', ''),
            contributor_data.get('company_size', ''),
            contributor_data.get('annual_revenue', ''),
            contributor_data.get('repo_source', ''),
            contributor_data.get('github_org', ''),
            contributor_data.get('contributions', 0),
            contributor_data.get('insight', ''),
            contributor_data.get('is_org_member'),
            contributor_data.get('github_profile_company', '')
        ))

        contributor_id = cursor.lastrowid
        conn.commit()
        return contributor_id
    except Exception as e:
        print(f"[CONTRIBUTORS] Error saving contributor: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def save_contributors_batch(contributors: list) -> int:
    """Save multiple contributors in a single transaction. Returns count saved."""
    conn = get_db_connection()
    cursor = conn.cursor()
    saved = 0

    # Bot patterns to filter out
    BOT_PATTERNS = ('[bot]', '-bot', 'github-actions', 'dependabot', 'renovate', 'greenkeeper', 'snyk-bot')

    try:
        for c in contributors:
            login = c.get('github_login', '').lower()
            if any(p in login for p in BOT_PATTERNS):
                continue

            cursor.execute('''
                INSERT INTO contributors (
                    github_login, github_url, name, email, blog,
                    company, company_size, annual_revenue, repo_source,
                    github_org, contributions, insight,
                    is_org_member, github_profile_company
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(github_login, github_org) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), contributors.name),
                    email = COALESCE(NULLIF(excluded.email, ''), contributors.email),
                    blog = COALESCE(NULLIF(excluded.blog, ''), contributors.blog),
                    company = COALESCE(NULLIF(excluded.company, ''), contributors.company),
                    company_size = COALESCE(NULLIF(excluded.company_size, ''), contributors.company_size),
                    annual_revenue = COALESCE(NULLIF(excluded.annual_revenue, ''), contributors.annual_revenue),
                    repo_source = COALESCE(NULLIF(excluded.repo_source, ''), contributors.repo_source),
                    contributions = CASE WHEN excluded.contributions > 0 THEN excluded.contributions ELSE contributors.contributions END,
                    insight = COALESCE(NULLIF(excluded.insight, ''), contributors.insight),
                    is_org_member = excluded.is_org_member,
                    github_profile_company = COALESCE(NULLIF(excluded.github_profile_company, ''), contributors.github_profile_company),
                    updated_at = CURRENT_TIMESTAMP
            ''', (
                c.get('github_login', ''),
                c.get('github_url', ''),
                c.get('name', ''),
                c.get('email', ''),
                c.get('blog', ''),
                c.get('company', ''),
                c.get('company_size', ''),
                c.get('annual_revenue', ''),
                c.get('repo_source', ''),
                c.get('github_org', ''),
                c.get('contributions', 0),
                c.get('insight', ''),
                c.get('is_org_member'),
                c.get('github_profile_company', '')
            ))
            saved += 1

        conn.commit()
    except Exception as e:
        print(f"[CONTRIBUTORS] Batch save error: {e}")
        conn.rollback()
    finally:
        conn.close()

    return saved


def get_contributors_datatable(draw=1, start=0, length=50, search_value='',
                                order_column=0, order_dir='asc',
                                apollo_filter=None,
                                has_email_filter=None,
                                warm_hot_filter=None,
                                i18n_filter=None,
                                not_contacted_filter=None) -> dict:
    """
    Server-side datatable processing for contributors.
    Returns paginated, sorted, and filtered contributor data.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Column mapping for sorting
    columns = ['github_login', 'name', 'company', 'company_size',
               'github_url', 'annual_revenue', 'contributions',
               'apollo_status', 'emails_sent', 'insight']

    sort_col = columns[order_column] if 0 <= order_column < len(columns) else 'contributions'
    sort_dir = 'DESC' if order_dir.lower() == 'desc' else 'ASC'

    # Build WHERE clause — default: hide confirmed external contributors
    conditions = ["(is_org_member IS NULL OR is_org_member = 1)"]
    params = []

    if search_value:
        conditions.append('''
            (LOWER(github_login) LIKE ? OR LOWER(name) LIKE ?
             OR LOWER(company) LIKE ? OR LOWER(email) LIKE ?
             OR LOWER(github_org) LIKE ? OR LOWER(repo_source) LIKE ?
             OR LOWER(insight) LIKE ?)
        ''')
        sv = f'%{search_value.lower()}%'
        params.extend([sv, sv, sv, sv, sv, sv, sv])

    if apollo_filter:
        conditions.append('apollo_status = ?')
        params.append(apollo_filter)

    if has_email_filter == '1':
        conditions.append("email IS NOT NULL AND email != ''")

    if warm_hot_filter == '1':
        # Filter to contributors whose company is in tier 1 or 2
        conditions.append("""LOWER(company) IN (
            SELECT LOWER(company_name) FROM monitored_accounts WHERE current_tier IN (1, 2)
        )""")

    if i18n_filter == '1':
        conditions.append("""(LOWER(insight) LIKE '%i18n%'
            OR LOWER(insight) LIKE '%internationalization%'
            OR LOWER(insight) LIKE '%locale%'
            OR LOWER(insight) LIKE '%translation%')""")

    if not_contacted_filter == '1':
        conditions.append("apollo_status = 'not_sent'")

    where_clause = ''
    if conditions:
        where_clause = 'WHERE ' + ' AND '.join(conditions)

    # Total records (excluding confirmed externals)
    cursor.execute('SELECT COUNT(*) as cnt FROM contributors WHERE (is_org_member IS NULL OR is_org_member = 1)')
    total_records = cursor.fetchone()['cnt']

    # Filtered records
    cursor.execute(f'SELECT COUNT(*) as cnt FROM contributors {where_clause}', params)
    filtered_records = cursor.fetchone()['cnt']

    # Fetch data
    query = f'''
        SELECT * FROM contributors
        {where_clause}
        ORDER BY {sort_col} {sort_dir}
        LIMIT ? OFFSET ?
    '''
    params.extend([length, start])
    cursor.execute(query, params)

    rows = cursor.fetchall()
    data = [dict(row) for row in rows]

    conn.close()

    return {
        'draw': draw,
        'recordsTotal': total_records,
        'recordsFiltered': filtered_records,
        'data': data
    }


def get_contributor_stats() -> dict:
    """Get aggregate stats for contributors: total, enrolled, emails sent."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT
            COUNT(*) as total_contributors,
            SUM(CASE WHEN apollo_status = 'sent' OR enrolled_in_sequence = 1 THEN 1 ELSE 0 END) as total_enrolled,
            SUM(emails_sent) as total_emails_sent
        FROM contributors
        WHERE (is_org_member IS NULL OR is_org_member = 1)
    ''')
    row = cursor.fetchone()
    conn.close()

    return {
        'total_contributors': row['total_contributors'] or 0,
        'total_enrolled': row['total_enrolled'] or 0,
        'total_emails_sent': row['total_emails_sent'] or 0
    }


def update_contributor_apollo_status(contributor_id: int, status: str, sequence_name: str = '') -> bool:
    """Update Apollo enrollment status for a contributor."""
    conn = get_db_connection()
    cursor = conn.cursor()

    enrolled = 1 if status == 'sent' else 0
    enrolled_at = datetime.now().isoformat() if status == 'sent' else None

    cursor.execute('''
        UPDATE contributors
        SET apollo_status = ?,
            enrolled_in_sequence = ?,
            sequence_name = ?,
            enrolled_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (status, enrolled, sequence_name, enrolled_at, contributor_id))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def increment_contributor_emails(contributor_id: int) -> bool:
    """Increment email count for a contributor."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        UPDATE contributors
        SET emails_sent = emails_sent + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (contributor_id,))

    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def update_contributor_email(contributor_id: int, email: str) -> bool:
    """Update email address for a contributor (persists Apollo lookup results)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE contributors
            SET email = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (email, contributor_id))
        updated = cursor.rowcount > 0
        conn.commit()
        return updated
    except Exception as e:
        print(f"[DB] Error updating email for contributor {contributor_id}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def get_contributor_by_id(contributor_id: int) -> Optional[dict]:
    """Get a single contributor by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM contributors WHERE id = ?', (contributor_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_contributor(contributor_id: int) -> bool:
    """Delete a contributor by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM contributors WHERE id = ?', (contributor_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
