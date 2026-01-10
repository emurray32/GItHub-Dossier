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


# Initialize database on module import
init_db()
