"""
Dedup Service — detect and resolve duplicate intent signals.

Finds clusters of signals that share the same account + signal_type + evidence,
flags near-duplicates with similar descriptions, and provides bulk archive
for cleaning up the queue.
"""
import logging
from typing import Optional, List

from v2.db import db_connection, rows_to_dicts, row_to_dict

logger = logging.getLogger(__name__)


def find_exact_duplicates() -> list:
    """Find signal clusters with identical account_id + signal_type + evidence_value.

    Returns groups where 2+ signals share the same fingerprint, sorted by
    cluster size descending. Each group includes the signals and a recommended
    action (keep the oldest, archive the rest).
    """
    with db_connection() as conn:
        cursor = conn.cursor()

        # Find fingerprints with more than one signal
        cursor.execute('''
            SELECT account_id, signal_type, evidence_value, COUNT(*) as cnt
            FROM intent_signals
            WHERE status != 'archived'
            GROUP BY account_id, signal_type, evidence_value
            HAVING cnt > 1
            ORDER BY cnt DESC
        ''')
        clusters = rows_to_dicts(cursor.fetchall())

        results = []
        for cluster in clusters:
            cursor.execute('''
                SELECT s.id, s.signal_description, s.signal_source, s.status,
                       s.created_at, a.company_name
                FROM intent_signals s
                JOIN monitored_accounts a ON s.account_id = a.id
                WHERE s.account_id = ? AND s.signal_type = ?
                  AND s.status != 'archived'
                  AND (s.evidence_value = ? OR (s.evidence_value IS NULL AND ? IS NULL))
                ORDER BY s.created_at ASC
            ''', (
                cluster['account_id'], cluster['signal_type'],
                cluster['evidence_value'], cluster['evidence_value'],
            ))
            signals = rows_to_dicts(cursor.fetchall())

            if len(signals) < 2:
                continue

            results.append({
                'company_name': signals[0].get('company_name'),
                'account_id': cluster['account_id'],
                'signal_type': cluster['signal_type'],
                'evidence_value': cluster['evidence_value'],
                'count': len(signals),
                'keep_signal_id': signals[0]['id'],
                'duplicate_signal_ids': [s['id'] for s in signals[1:]],
                'signals': signals,
            })

        return results


def find_same_account_type_dupes(account_id: Optional[int] = None) -> list:
    """Find signals on the same account with the same signal_type.

    These may not be exact evidence matches but could still represent
    redundant signals worth reviewing. Optionally filter to one account.
    """
    with db_connection() as conn:
        cursor = conn.cursor()

        where = "s.status != 'archived'"
        params = []
        if account_id:
            where += " AND s.account_id = ?"
            params.append(account_id)

        cursor.execute(f'''
            SELECT s.account_id, a.company_name, s.signal_type, COUNT(*) as cnt
            FROM intent_signals s
            JOIN monitored_accounts a ON s.account_id = a.id
            WHERE {where}
            GROUP BY s.account_id, s.signal_type
            HAVING cnt > 1
            ORDER BY cnt DESC
            LIMIT 50
        ''', tuple(params))

        groups = rows_to_dicts(cursor.fetchall())

        results = []
        for g in groups:
            cursor.execute('''
                SELECT id, signal_description, evidence_value, signal_source,
                       status, created_at
                FROM intent_signals
                WHERE account_id = ? AND signal_type = ? AND status != 'archived'
                ORDER BY created_at ASC
            ''', (g['account_id'], g['signal_type']))
            signals = rows_to_dicts(cursor.fetchall())

            # Check if evidence values differ — if so, they're distinct signals
            evidence_set = set(s.get('evidence_value') for s in signals)
            all_same_evidence = len(evidence_set) <= 1

            results.append({
                'company_name': g['company_name'],
                'account_id': g['account_id'],
                'signal_type': g['signal_type'],
                'count': len(signals),
                'all_same_evidence': all_same_evidence,
                'signals': signals,
            })

        return results


def get_dedup_summary() -> dict:
    """High-level dedup stats for the dashboard."""
    with db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('''
            SELECT COUNT(*) as cnt FROM intent_signals WHERE status != 'archived'
        ''')
        total_active = _val(cursor.fetchone())

        # Exact duplicate count (signals that share fingerprint with another)
        cursor.execute('''
            SELECT SUM(cnt - 1) as dupe_count FROM (
                SELECT COUNT(*) as cnt
                FROM intent_signals
                WHERE status != 'archived'
                GROUP BY account_id, signal_type, evidence_value
                HAVING cnt > 1
            )
        ''')
        exact_dupes = _val(cursor.fetchone())

        # Same-type clusters (accounts with 2+ signals of the same type)
        cursor.execute('''
            SELECT COUNT(*) as cnt FROM (
                SELECT account_id, signal_type
                FROM intent_signals
                WHERE status != 'archived'
                GROUP BY account_id, signal_type
                HAVING COUNT(*) > 1
            )
        ''')
        type_clusters = _val(cursor.fetchone())

        return {
            'total_active_signals': total_active,
            'exact_duplicates': exact_dupes,
            'same_type_clusters': type_clusters,
            'estimated_savings_pct': round(
                (exact_dupes / total_active) * 100, 1
            ) if total_active > 0 else 0,
        }


def archive_duplicates(signal_ids: List[int], keep_signal_id: int) -> dict:
    """Archive a list of duplicate signals, keeping one as the canonical signal.

    Args:
        signal_ids: IDs to archive (the duplicates)
        keep_signal_id: the signal to keep (must NOT be in signal_ids)

    Returns:
        Dict with archived count and the kept signal ID.
    """
    if keep_signal_id in signal_ids:
        return {'status': 'error', 'message': 'keep_signal_id must not be in the archive list'}

    if not signal_ids:
        return {'status': 'error', 'message': 'No signal IDs to archive'}

    with db_connection() as conn:
        cursor = conn.cursor()

        # Verify the keep signal exists
        cursor.execute('SELECT id FROM intent_signals WHERE id = ?', (keep_signal_id,))
        if not cursor.fetchone():
            return {'status': 'error', 'message': f'Keep signal {keep_signal_id} not found'}

        placeholders = ', '.join(['?'] * len(signal_ids))
        cursor.execute(f'''
            UPDATE intent_signals
            SET status = 'archived', updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders}) AND status != 'archived'
        ''', tuple(signal_ids))
        archived = cursor.rowcount if hasattr(cursor, 'rowcount') else len(signal_ids)
        conn.commit()

    # Log activity
    try:
        from v2.services.activity_service import log_activity
        log_activity(
            event_type='duplicates_archived',
            entity_type='signal',
            entity_id=keep_signal_id,
            details={
                'archived_ids': signal_ids,
                'archived_count': archived,
            },
            created_by='dedup_service',
        )
    except Exception:
        logger.debug("[DEDUP] Could not log dedup activity")

    logger.info("[DEDUP] Archived %d duplicate signals, kept signal %d", archived, keep_signal_id)

    return {
        'status': 'success',
        'archived': archived,
        'kept_signal_id': keep_signal_id,
    }


def auto_archive_exact_duplicates() -> dict:
    """Automatically archive all exact duplicates, keeping the oldest in each cluster.

    Returns summary of how many signals were archived.
    """
    clusters = find_exact_duplicates()
    total_archived = 0

    for cluster in clusters:
        result = archive_duplicates(
            signal_ids=cluster['duplicate_signal_ids'],
            keep_signal_id=cluster['keep_signal_id'],
        )
        if result.get('status') == 'success':
            total_archived += result.get('archived', 0)

    return {
        'clusters_processed': len(clusters),
        'signals_archived': total_archived,
    }


def _val(row):
    if row is None:
        return 0
    if isinstance(row, dict):
        return row.get('cnt', row.get('dupe_count', 0)) or 0
    return row[0] if row[0] is not None else 0
