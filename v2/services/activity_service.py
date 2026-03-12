"""
Activity Service — audit trail for all key actions in the v2 pipeline.

Every meaningful action (signal creation, campaign assignment, draft approval,
enrollment, etc.) is logged here so Eric always has a timeline of what happened.
"""
import logging
from typing import Optional, List

from v2.db import db_connection, insert_returning_id, row_to_dict, rows_to_dicts, safe_json_dumps

logger = logging.getLogger(__name__)


def log_activity(
    event_type: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    details: Optional[dict] = None,
    created_by: Optional[str] = None,
) -> None:
    """Insert one row into activity_log.

    Args:
        event_type: one of the EventType enum values (e.g. 'signal_created',
            'csv_imported', 'draft_approved').
        entity_type: the kind of entity this event relates to
            (e.g. 'signal', 'account', 'prospect', 'draft').
        entity_id: the primary-key id of that entity.
        details: optional dict of extra context (stored as JSON text).
        created_by: who or what triggered this event.
    """
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            insert_returning_id(cursor, '''
                INSERT INTO activity_log (event_type, entity_type, entity_id, details, created_by)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                event_type,
                entity_type,
                entity_id,
                safe_json_dumps(details) if details else None,
                created_by,
            ))
            conn.commit()
    except Exception:
        # Activity logging must never crash the caller.
        logger.exception("[ACTIVITY] Failed to log event %s for %s/%s",
                         event_type, entity_type, entity_id)


def get_recent_activity(
    limit: int = 100,
    event_type: Optional[str] = None,
    entity_type: Optional[str] = None,
) -> List[dict]:
    """Return the most recent activity log entries, newest first.

    Args:
        limit: max rows to return.
        event_type: optional filter (e.g. 'signal_created').
        entity_type: optional filter (e.g. 'signal', 'account').
    """
    with db_connection() as conn:
        cursor = conn.cursor()

        where_clauses = []
        params: list = []

        if event_type:
            where_clauses.append("event_type = ?")
            params.append(event_type)

        if entity_type:
            where_clauses.append("entity_type = ?")
            params.append(entity_type)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        cursor.execute(f'''
            SELECT * FROM activity_log
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
        ''', tuple(params) + (limit,))

        return rows_to_dicts(cursor.fetchall())


def get_activity_for_account(account_id: int, limit: int = 50) -> List[dict]:
    """Return activity entries whose entity_type is 'account' and entity_id matches."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM activity_log
            WHERE entity_type = 'account' AND entity_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (account_id, limit))
        return rows_to_dicts(cursor.fetchall())


def get_activity_for_signal(signal_id: int, limit: int = 50) -> List[dict]:
    """Return activity entries whose entity_type is 'signal' and entity_id matches."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM activity_log
            WHERE entity_type = 'signal' AND entity_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (signal_id, limit))
        return rows_to_dicts(cursor.fetchall())
