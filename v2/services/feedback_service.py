"""
Feedback Service — critique / regeneration history for email drafts.

Every time a draft is critiqued or regenerated, a feedback_log row is created
so we can track the iterative refinement process.
"""
import logging
from typing import Optional, List

from v2.db import db_connection, insert_returning_id, row_to_dict, rows_to_dicts
from v2.services import activity_service

logger = logging.getLogger(__name__)


def log_feedback(
    draft_id: int,
    critique: str,
    sequence_step: Optional[int] = None,
    prospect_id: Optional[int] = None,
    signal_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> int:
    """Record a feedback / critique entry for a draft.

    Also logs a 'draft_regenerated' activity event so the timeline
    captures the regeneration.

    Returns:
        The new feedback_log id.
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        feedback_id = insert_returning_id(cursor, '''
            INSERT INTO feedback_log
                (draft_id, prospect_id, signal_id, critique, sequence_step, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            draft_id,
            prospect_id,
            signal_id,
            critique,
            sequence_step,
            created_by,
        ))
        conn.commit()

    logger.info("[FEEDBACK] Logged feedback %d for draft %d", feedback_id, draft_id)

    # Record in the activity timeline
    activity_service.log_activity(
        event_type='draft_regenerated',
        entity_type='draft',
        entity_id=draft_id,
        details={
            'feedback_id': feedback_id,
            'signal_id': signal_id,
            'prospect_id': prospect_id,
            'critique_preview': critique[:120] if critique else None,
        },
        created_by=created_by,
    )

    return feedback_id


def get_feedback_for_draft(draft_id: int) -> List[dict]:
    """Return all feedback entries for a specific draft, newest first."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM feedback_log
            WHERE draft_id = ?
            ORDER BY created_at DESC
        ''', (draft_id,))
        return rows_to_dicts(cursor.fetchall())


def get_feedback_for_signal(signal_id: int) -> List[dict]:
    """Return all feedback entries tied to a signal, newest first."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM feedback_log
            WHERE signal_id = ?
            ORDER BY created_at DESC
        ''', (signal_id,))
        return rows_to_dicts(cursor.fetchall())


def get_recent_feedback(limit: int = 50) -> List[dict]:
    """Return the latest feedback entries across all drafts."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM feedback_log
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))
        return rows_to_dicts(cursor.fetchall())
