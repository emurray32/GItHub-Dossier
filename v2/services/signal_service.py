"""
Signal Service — CRUD and query operations for intent signals.

Intent signals are the root object of the v2 domain. Every workflow starts
from a signal in the queue.
"""
import logging
from typing import Optional, List

from v2.db import db_connection, insert_returning_id, row_to_dict, rows_to_dicts, safe_json_dumps

logger = logging.getLogger(__name__)


def create_signal(
    account_id: int,
    signal_description: str,
    signal_type: Optional[str] = None,
    evidence_type: str = 'manual',
    evidence_value: Optional[str] = None,
    signal_source: str = 'manual_entry',
    recommended_campaign_id: Optional[int] = None,
    recommended_campaign_reasoning: Optional[str] = None,
    created_by: Optional[str] = None,
    ingestion_batch_id: Optional[str] = None,
    raw_payload: Optional[str] = None,
    scan_signal_id: Optional[int] = None,
    outreach_angle: Optional[str] = None,
) -> int:
    """Create a new intent signal. Returns the signal id."""
    with db_connection() as conn:
        cursor = conn.cursor()
        signal_id = insert_returning_id(cursor, '''
            INSERT INTO intent_signals (
                account_id, signal_description, evidence_type, evidence_value,
                signal_type, signal_source, recommended_campaign_id,
                recommended_campaign_reasoning, created_by, ingestion_batch_id,
                raw_payload, scan_signal_id, outreach_angle
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            account_id, signal_description, evidence_type,
            safe_json_dumps(evidence_value) if isinstance(evidence_value, (dict, list)) else evidence_value,
            signal_type, signal_source, recommended_campaign_id,
            recommended_campaign_reasoning, created_by, ingestion_batch_id,
            safe_json_dumps(raw_payload) if isinstance(raw_payload, (dict, list)) else raw_payload,
            scan_signal_id, outreach_angle,
        ))
        conn.commit()
        logger.info("[SIGNAL] Created signal %d for account %d (type=%s, source=%s)",
                     signal_id, account_id, signal_type, signal_source)
        return signal_id


def get_signal(signal_id: int) -> Optional[dict]:
    """Get a single signal by id, enriched with account info."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT s.*, a.company_name, a.website, a.industry,
                   a.company_size, a.annual_revenue, a.account_status,
                   a.account_owner, a.github_org, a.linkedin_url, a.hq_location,
                   a.current_tier, a.evidence_summary, a.employee_count, a.funding_stage
            FROM intent_signals s
            JOIN monitored_accounts a ON s.account_id = a.id
            WHERE s.id = ?
        ''', (signal_id,))
        return row_to_dict(cursor.fetchone())


def list_signals(
    status: Optional[str] = None,
    owner: Optional[str] = None,
    signal_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List intent signals with optional filters. Returns {signals, total}."""
    with db_connection() as conn:
        cursor = conn.cursor()

        where_clauses = []
        params = []

        if status:
            where_clauses.append("a.account_status = ?")
            params.append(status)

        if owner:
            where_clauses.append("a.account_owner = ?")
            params.append(owner)

        if signal_type:
            where_clauses.append("s.signal_type = ?")
            params.append(signal_type)

        # Don't hide archived signals when explicitly filtering for noise,
        # since mark_account_noise cascades signal status to 'archived'.
        if status != 'noise':
            where_clauses.append("s.status != 'archived'")
        where_sql = " AND ".join(where_clauses)

        # Count
        cursor.execute(f'''
            SELECT COUNT(*) as cnt
            FROM intent_signals s
            JOIN monitored_accounts a ON s.account_id = a.id
            WHERE {where_sql}
        ''', tuple(params))
        row = cursor.fetchone()
        total = row['cnt'] if isinstance(row, dict) else row[0]

        # Fetch — account_status is exposed as workflow_status for the public API
        cursor.execute(f'''
            SELECT s.*, a.company_name, a.website, a.industry,
                   a.company_size, a.annual_revenue, a.account_status,
                   a.account_status AS workflow_status,
                   a.account_owner
            FROM intent_signals s
            JOIN monitored_accounts a ON s.account_id = a.id
            WHERE {where_sql}
            ORDER BY a.current_tier ASC, s.created_at DESC
            LIMIT ? OFFSET ?
        ''', tuple(params) + (limit, offset))

        return {
            'signals': rows_to_dicts(cursor.fetchall()),
            'total': total,
        }


def update_signal_status(signal_id: int, status: str) -> bool:
    """Update a signal's status. Returns True if updated."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE intent_signals SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (status, signal_id))
        conn.commit()
        return cursor.rowcount > 0 if hasattr(cursor, 'rowcount') else True


def update_signal_campaign(
    signal_id: int,
    campaign_id: int,
    reasoning: Optional[str] = None,
) -> bool:
    """Update the recommended campaign for a signal."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE intent_signals
            SET recommended_campaign_id = ?, recommended_campaign_reasoning = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (campaign_id, reasoning, signal_id))
        conn.commit()
        return True


def archive_signal(signal_id: int) -> bool:
    """Archive a signal (soft delete)."""
    return update_signal_status(signal_id, 'archived')


def get_signal_workspace(signal_id: int) -> Optional[dict]:
    """Get the full workspace context for a signal.

    Returns signal + account + recommended campaign + personas + existing prospects.
    Used by both the web UI and MCP tools.
    """
    signal = get_signal(signal_id)
    if not signal:
        return None

    with db_connection() as conn:
        cursor = conn.cursor()

        # Campaign recommendation
        campaign = None
        personas = []
        if signal.get('recommended_campaign_id'):
            cursor.execute('''
                SELECT * FROM campaigns WHERE id = ?
            ''', (signal['recommended_campaign_id'],))
            campaign = row_to_dict(cursor.fetchone())

            if campaign:
                cursor.execute('''
                    SELECT * FROM campaign_personas WHERE campaign_id = ?
                    ORDER BY priority ASC
                ''', (campaign['id'],))
                personas = rows_to_dicts(cursor.fetchall())

        # Existing prospects for this signal
        cursor.execute('''
            SELECT * FROM prospects WHERE signal_id = ?
            ORDER BY created_at DESC
        ''', (signal_id,))
        prospects = rows_to_dicts(cursor.fetchall())

        # Drafts for these prospects
        prospect_ids = [p['id'] for p in prospects]
        drafts = []
        if prospect_ids:
            placeholders = ', '.join(['?'] * len(prospect_ids))
            cursor.execute(f'''
                SELECT * FROM drafts WHERE prospect_id IN ({placeholders})
                ORDER BY prospect_id, sequence_step
            ''', tuple(prospect_ids))
            drafts = rows_to_dicts(cursor.fetchall())

        # Writing preferences
        cursor.execute("SELECT preference_key, preference_value FROM writing_preferences")
        prefs = {r['preference_key']: r['preference_value'] for r in rows_to_dicts(cursor.fetchall())}

        # Scorecard from latest scan report
        scorecard = None
        cursor.execute('''
            SELECT r.scan_data FROM reports r
            JOIN monitored_accounts ma ON ma.latest_report_id = r.id
            WHERE ma.id = ?
        ''', (signal['account_id'],))
        report_row = cursor.fetchone()
        if report_row:
            import json
            scan_data = report_row['scan_data'] if isinstance(report_row, dict) else report_row[0]
            if isinstance(scan_data, str):
                try:
                    scan_data = json.loads(scan_data)
                except (json.JSONDecodeError, TypeError):
                    scan_data = {}
            scoring = scan_data.get('scoring_v2', {}) if isinstance(scan_data, dict) else {}
            if scoring:
                scorecard = {
                    'maturity_level': scoring.get('org_maturity_level'),
                    'maturity_label': scoring.get('org_maturity_label'),
                    'intent_score': scoring.get('org_intent_score'),
                    'readiness_index': scoring.get('readiness_index'),
                    'confidence_percent': scoring.get('confidence_percent'),
                    'outreach_angle': scoring.get('outreach_angle_label'),
                    'risk_level': scoring.get('risk_level'),
                }

    return {
        'signal': signal,
        'account': {
            'id': signal.get('account_id'),
            'company_name': signal.get('company_name'),
            'website': signal.get('website'),
            'industry': signal.get('industry'),
            'company_size': signal.get('company_size'),
            'annual_revenue': signal.get('annual_revenue'),
            'account_status': signal.get('account_status'),
            'account_owner': signal.get('account_owner'),
            'github_org': signal.get('github_org'),
            'linkedin_url': signal.get('linkedin_url'),
            'hq_location': signal.get('hq_location'),
            'current_tier': signal.get('current_tier'),
            'evidence_summary': signal.get('evidence_summary'),
            'employee_count': signal.get('employee_count'),
            'funding_stage': signal.get('funding_stage'),
        },
        'scorecard': scorecard,
        'campaign': campaign,
        'personas': personas,
        'prospects': prospects,
        'drafts': drafts,
        'writing_preferences': prefs,
    }


def check_duplicate_signal(
    account_id: int,
    signal_type: str,
    signal_source: str = None,
    evidence_value: Optional[str] = None,
) -> bool:
    """Check if a signal with the same account + type already exists.

    Only one signal per company per signal_type — BDRs don't need 5 separate
    'timezone_library' signals for the same company. Additional evidence is
    captured in the first signal's description, not as separate queue items.
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM intent_signals
            WHERE account_id = ? AND signal_type = ? AND status != 'archived'
            LIMIT 1
        ''', (account_id, signal_type))
        return cursor.fetchone() is not None


def update_signal_bdr_evaluation(
    signal_id: int,
    quality_score: int,
    positioning: str,
) -> bool:
    """Update BDR evaluation for a signal (quality score + positioning angle)."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE intent_signals
            SET bdr_quality_score = ?, bdr_positioning = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (quality_score, positioning, signal_id))
        conn.commit()
        return True


def get_signal_counts_by_status() -> dict:
    """Get signal counts grouped by workflow status (account_status).

    Excludes archived signals for non-noise statuses (matching list_signals
    behavior) so tab counts reflect what users actually see in the list.
    Noise signals ARE included even though they have s.status='archived'
    (due to cascade), so the noise tab count stays accurate.
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        # Count non-archived signals grouped by account_status
        cursor.execute('''
            SELECT a.account_status AS workflow_status, COUNT(*) as cnt
            FROM intent_signals s
            JOIN monitored_accounts a ON s.account_id = a.id
            WHERE s.status != 'archived'
            GROUP BY a.account_status
        ''')
        counts = {r['workflow_status']: r['cnt'] for r in rows_to_dicts(cursor.fetchall())}

        # Noise signals have s.status='archived' (cascade), so count them separately
        cursor.execute('''
            SELECT COUNT(*) as cnt
            FROM intent_signals s
            JOIN monitored_accounts a ON s.account_id = a.id
            WHERE a.account_status = 'noise'
        ''')
        row = cursor.fetchone()
        noise_count = row['cnt'] if isinstance(row, dict) else row[0]
        if noise_count > 0:
            counts['noise'] = noise_count

        return counts


def get_owners() -> List[str]:
    """Get distinct account owners that have signals."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT a.account_owner
            FROM intent_signals s
            JOIN monitored_accounts a ON s.account_id = a.id
            WHERE a.account_owner IS NOT NULL AND a.account_owner != ''
            ORDER BY a.account_owner
        ''')
        return [r['account_owner'] for r in rows_to_dicts(cursor.fetchall())]
