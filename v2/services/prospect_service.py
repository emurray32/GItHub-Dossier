"""
Prospect Service — manages prospects (people found via Apollo).

Prospects are tied to accounts and optionally to signals. They track
enrollment state through the pipeline: found → drafting → enrolled → sequence_complete.
"""
import logging
from typing import Optional, List

from v2.db import db_connection, insert_returning_id, row_to_dict, rows_to_dicts

logger = logging.getLogger(__name__)


def create_prospect(
    account_id: int,
    signal_id: Optional[int] = None,
    full_name: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    title: Optional[str] = None,
    email: Optional[str] = None,
    email_verified: bool = False,
    linkedin_url: Optional[str] = None,
    apollo_person_id: Optional[str] = None,
) -> int:
    """Create a new prospect. Returns the prospect id."""
    with db_connection() as conn:
        cursor = conn.cursor()
        prospect_id = insert_returning_id(cursor, '''
            INSERT INTO prospects (
                account_id, signal_id, full_name, first_name, last_name,
                title, email, email_verified, linkedin_url, apollo_person_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            account_id, signal_id, full_name, first_name, last_name,
            title, email, 1 if email_verified else 0, linkedin_url, apollo_person_id,
        ))
        conn.commit()
        logger.info("[PROSPECT] Created prospect %d: %s (%s) for account %d",
                     prospect_id, full_name, email, account_id)
        return prospect_id


def bulk_create_prospects(prospects: List[dict]) -> List[int]:
    """Create multiple prospects in one transaction. Returns list of ids.

    Skips prospects whose email already exists for the same signal_id
    to prevent duplicates on repeated imports.
    """
    ids = []
    with db_connection() as conn:
        cursor = conn.cursor()

        # Pre-load existing emails per signal_id to skip duplicates
        signal_ids = list({p.get('signal_id') for p in prospects if p.get('signal_id')})
        existing_emails = set()
        if signal_ids:
            placeholders = ', '.join(['?'] * len(signal_ids))
            cursor.execute(f'''
                SELECT signal_id, LOWER(email) as email_lower
                FROM prospects
                WHERE signal_id IN ({placeholders}) AND email IS NOT NULL
            ''', tuple(signal_ids))
            for row in cursor.fetchall():
                sid = row['signal_id'] if isinstance(row, dict) else row[0]
                em = row['email_lower'] if isinstance(row, dict) else row[1]
                existing_emails.add((sid, em))

        for p in prospects:
            email = p.get('email')
            sig_id = p.get('signal_id')
            # Skip if this email already exists for the same signal
            if email and sig_id and (sig_id, email.strip().lower()) in existing_emails:
                logger.debug("[PROSPECT] Skipping duplicate email %s for signal %d", email, sig_id)
                continue

            pid = insert_returning_id(cursor, '''
                INSERT INTO prospects (
                    account_id, signal_id, full_name, first_name, last_name,
                    title, email, email_verified, linkedin_url, apollo_person_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                p['account_id'], sig_id,
                p.get('full_name'), p.get('first_name'), p.get('last_name'),
                p.get('title'), email,
                1 if p.get('email_verified') else 0,
                p.get('linkedin_url'), p.get('apollo_person_id'),
            ))
            ids.append(pid)
            # Track newly inserted emails so later rows in the same batch are deduped too
            if email and sig_id:
                existing_emails.add((sig_id, email.strip().lower()))
        conn.commit()
    logger.info("[PROSPECT] Bulk created %d prospects", len(ids))
    return ids


def get_prospect(prospect_id: int) -> Optional[dict]:
    """Get a single prospect by id."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.*, a.company_name
            FROM prospects p
            JOIN monitored_accounts a ON p.account_id = a.id
            WHERE p.id = ?
        ''', (prospect_id,))
        return row_to_dict(cursor.fetchone())


def get_prospects_for_signal(signal_id: int) -> List[dict]:
    """Get all prospects tied to a signal."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.*, a.company_name
            FROM prospects p
            JOIN monitored_accounts a ON p.account_id = a.id
            WHERE p.signal_id = ?
            ORDER BY p.created_at DESC
        ''', (signal_id,))
        return rows_to_dicts(cursor.fetchall())


def get_prospects_for_account(account_id: int) -> List[dict]:
    """Get all prospects for an account."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM prospects
            WHERE account_id = ?
            ORDER BY created_at DESC
        ''', (account_id,))
        return rows_to_dicts(cursor.fetchall())


def update_prospect_status(prospect_id: int, enrollment_status: str) -> bool:
    """Update enrollment status of a prospect."""
    valid = ('found', 'drafting', 'enrolled', 'sequence_complete')
    if enrollment_status not in valid:
        return False
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE prospects
            SET enrollment_status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (enrollment_status, prospect_id))
        conn.commit()
        return cursor.rowcount > 0 if hasattr(cursor, 'rowcount') else True


def update_prospect_enrollment(
    prospect_id: int,
    enrollment_status: str,
    sequence_id: Optional[str] = None,
    sequence_name: Optional[str] = None,
) -> bool:
    """Update prospect's enrollment status and sequence info after enrollment."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE prospects
            SET enrollment_status = ?, sequence_id = ?, sequence_name = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (enrollment_status, sequence_id, sequence_name, prospect_id))
        conn.commit()
        return cursor.rowcount > 0 if hasattr(cursor, 'rowcount') else True


def update_apollo_contact_id(prospect_id: int, apollo_contact_id: str) -> bool:
    """Store the Apollo contact ID after a contact is created/found in Apollo."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE prospects SET apollo_contact_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (apollo_contact_id, prospect_id))
        conn.commit()
        return cursor.rowcount > 0 if hasattr(cursor, 'rowcount') else True


def mark_do_not_contact(prospect_id: int) -> bool:
    """Flag a prospect as do-not-contact."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE prospects SET do_not_contact = 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (prospect_id,))
        conn.commit()
        return cursor.rowcount > 0 if hasattr(cursor, 'rowcount') else True


def is_already_enrolled(email: str) -> bool:
    """Check if this email is already enrolled in any sequence."""
    if not email:
        return False
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM prospects
            WHERE LOWER(email) = LOWER(?) AND enrollment_status IN ('enrolled', 'sequence_complete')
            LIMIT 1
        ''', (email,))
        return cursor.fetchone() is not None


def is_do_not_contact(email: str) -> bool:
    """Check if this email is flagged do-not-contact in any existing prospect record."""
    if not email:
        return False
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM prospects
            WHERE LOWER(email) = LOWER(?) AND do_not_contact = 1
            LIMIT 1
        ''', (email,))
        return cursor.fetchone() is not None


def filter_actionable_prospects(signal_id: int) -> List[dict]:
    """Get prospects for a signal that are actionable (not DNC, not already enrolled)."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.*, a.company_name
            FROM prospects p
            JOIN monitored_accounts a ON p.account_id = a.id
            WHERE p.signal_id = ?
              AND p.do_not_contact = 0
              AND p.enrollment_status = 'found'
            ORDER BY p.email_verified DESC, p.created_at ASC
        ''', (signal_id,))
        return rows_to_dicts(cursor.fetchall())
