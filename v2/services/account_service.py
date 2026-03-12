"""
Account Service — manages account status flow and v2-specific account operations.

The account status flow is:
    new → sequenced → revisit → noise

Rules for multi-signal accounts:
- When ANY prospect on an account gets enrolled → account_status = 'sequenced'
- When ALL sequences on an account complete with no reply → account_status = 'revisit'
- 'noise' is always a manual action
- Each signal is independent in the queue regardless of account status
"""
import logging
from typing import Optional, List

from v2.db import db_connection, insert_returning_id, row_to_dict, rows_to_dicts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Account Status Management
# ---------------------------------------------------------------------------

def get_account(account_id: int) -> Optional[dict]:
    """Get account by id with v2 fields."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, company_name, github_org, website, linkedin_url,
                   annual_revenue, company_size, industry, hq_location,
                   account_owner, account_status, current_tier, notes,
                   employee_count, funding_stage,
                   last_scanned_at, status_changed_at
            FROM monitored_accounts
            WHERE id = ? AND archived_at IS NULL
        ''', (account_id,))
        return row_to_dict(cursor.fetchone())


def update_account_status(account_id: int, new_status: str) -> bool:
    """Update account status. Valid values: new, sequenced, revisit, noise."""
    valid = ('new', 'sequenced', 'revisit', 'noise')
    if new_status not in valid:
        logger.warning("[ACCOUNT] Invalid status '%s' for account %d", new_status, account_id)
        return False

    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE monitored_accounts
            SET account_status = ?, status_changed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (new_status, account_id))
        conn.commit()
        logger.info("[ACCOUNT] Account %d status → %s", account_id, new_status)
        return True


def mark_account_sequenced(account_id: int) -> bool:
    """Mark account as sequenced (at least one prospect enrolled)."""
    return update_account_status(account_id, 'sequenced')


def mark_account_revisit(account_id: int) -> bool:
    """Mark account for revisit (all sequences complete, no reply)."""
    return update_account_status(account_id, 'revisit')


def mark_account_noise(account_id: int) -> bool:
    """Mark account as noise (false positive / not worth pursuing)."""
    return update_account_status(account_id, 'noise')


def check_all_sequences_complete(account_id: int) -> bool:
    """Check if ALL prospects for this account have completed sequences.

    Returns True only if there are enrolled prospects AND all are complete.
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT enrollment_status FROM prospects
            WHERE account_id = ? AND do_not_contact = 0
        ''', (account_id,))
        rows = cursor.fetchall()
        if not rows:
            return False
        enrolled_or_complete = [
            r['enrollment_status'] if isinstance(r, dict) else r[0]
            for r in rows
        ]
        # Must have at least one enrolled prospect, and all must be complete
        has_enrolled = any(s in ('enrolled', 'sequence_complete') for s in enrolled_or_complete)
        all_complete = all(s == 'sequence_complete' for s in enrolled_or_complete if s in ('enrolled', 'sequence_complete'))
        return has_enrolled and all_complete


# ---------------------------------------------------------------------------
# Account Owner Management
# ---------------------------------------------------------------------------

def set_account_owner(account_id: int, owner: str) -> bool:
    """Assign an owner to an account."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE monitored_accounts SET account_owner = ? WHERE id = ?
        ''', (owner, account_id))
        conn.commit()
        return True


def get_all_owners() -> List[str]:
    """Get distinct account owners."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT account_owner FROM monitored_accounts
            WHERE account_owner IS NOT NULL AND account_owner != ''
            ORDER BY account_owner
        ''')
        return [r['account_owner'] if isinstance(r, dict) else r[0]
                for r in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Account Lookup / Dedup
# ---------------------------------------------------------------------------

def find_account_by_name(company_name: str) -> Optional[dict]:
    """Find account by company name (case-insensitive)."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM monitored_accounts
            WHERE LOWER(company_name) = LOWER(?)
            AND archived_at IS NULL
            LIMIT 1
        ''', (company_name,))
        return row_to_dict(cursor.fetchone())


def find_or_create_account(
    company_name: str,
    website: Optional[str] = None,
    industry: Optional[str] = None,
    company_size: Optional[str] = None,
    annual_revenue: Optional[str] = None,
    account_owner: Optional[str] = None,
) -> int:
    """Find existing account by name or create a new one. Returns account_id."""
    existing = find_account_by_name(company_name)
    if existing:
        return existing['id']

    with db_connection() as conn:
        cursor = conn.cursor()
        account_id = insert_returning_id(cursor, '''
            INSERT INTO monitored_accounts (
                company_name, website, industry, company_size,
                annual_revenue, account_owner, account_status
            ) VALUES (?, ?, ?, ?, ?, ?, 'new')
        ''', (company_name, website, industry, company_size,
              annual_revenue, account_owner))
        conn.commit()
        logger.info("[ACCOUNT] Created new account %d: %s", account_id, company_name)
        return account_id


def get_account_domain(account_id: int) -> Optional[str]:
    """Extract domain from account website for Apollo search."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT website FROM monitored_accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        if not row:
            return None
        website = row['website'] if isinstance(row, dict) else row[0]
        if not website:
            return None
        # Strip protocol and path
        domain = website.lower().replace('https://', '').replace('http://', '').split('/')[0]
        return domain
