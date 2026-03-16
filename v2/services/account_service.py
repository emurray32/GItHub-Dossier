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
import re
from typing import Optional, List
from urllib.parse import urlparse

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
    """Mark account as sequenced (at least one prospect enrolled).

    Cascades: all 'new' signals for this account move to 'actioned'.
    """
    ok = update_account_status(account_id, 'sequenced')
    if ok:
        _cascade_signal_status(account_id, new_signal_status='actioned',
                               only_from_statuses=('new',))
    return ok


def mark_account_revisit(account_id: int) -> bool:
    """Mark account for revisit (all sequences complete, no reply).

    Cascades: all 'new' signals for this account move to 'actioned'.
    """
    ok = update_account_status(account_id, 'revisit')
    if ok:
        _cascade_signal_status(account_id, new_signal_status='actioned',
                               only_from_statuses=('new',))
    return ok


def mark_account_noise(account_id: int) -> bool:
    """Mark account as noise (false positive / not worth pursuing).

    Cascades: all non-archived signals for this account move to 'archived'.
    """
    ok = update_account_status(account_id, 'noise')
    if ok:
        _cascade_signal_status(account_id, new_signal_status='archived',
                               only_from_statuses=('new', 'actioned'))
    return ok


def check_all_sequences_complete(account_id: int) -> bool:
    """Check if ALL non-DNC prospects for this account have completed sequences.

    Returns True only when:
    - There is at least one prospect
    - Every non-DNC prospect has enrollment_status = 'sequence_complete'
    - No prospects are still in 'found', 'drafting', or 'enrolled' states

    This prevents premature revisit transitions when some prospects haven't
    been fully processed yet.
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
        statuses = [
            r['enrollment_status'] if isinstance(r, dict) else r[0]
            for r in rows
        ]
        # ALL non-DNC prospects must be sequence_complete — no exceptions
        return all(s == 'sequence_complete' for s in statuses)


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

_COMPANY_SUFFIXES = re.compile(
    r'\b(inc\.?|llc\.?|ltd\.?|corp\.?|corporation|co\.?|gmbh|s\.?a\.?|b\.?v\.?|'
    r'pty\.?\s*ltd\.?|plc\.?|ag|sa|srl|limited)\s*$',
    re.IGNORECASE,
)


def _normalize_company_name(name: str) -> str:
    """Normalize a company name for dedup comparison."""
    name = name.strip().lower()
    name = _COMPANY_SUFFIXES.sub('', name)
    name = re.sub(r'[,.\-]+$', '', name)       # trailing punctuation
    name = re.sub(r'\s+', ' ', name).strip()    # collapse whitespace
    return name


def _extract_domain(url: str) -> Optional[str]:
    """Extract bare domain from a URL (strips www. prefix)."""
    if not url:
        return None
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    domain = urlparse(url).netloc.lower().split(':')[0]
    if domain.startswith('www.'):
        domain = domain[4:]
    return domain or None


def find_account_by_name(company_name: str) -> Optional[dict]:
    """Find account by company name (case-insensitive, suffix-normalized)."""
    normalized = _normalize_company_name(company_name)
    with db_connection() as conn:
        cursor = conn.cursor()
        # Try exact case-insensitive first (fast path)
        cursor.execute('''
            SELECT * FROM monitored_accounts
            WHERE LOWER(company_name) = LOWER(?)
            AND archived_at IS NULL
            LIMIT 1
        ''', (company_name,))
        row = row_to_dict(cursor.fetchone())
        if row:
            return row

        # Fallback: load candidates and compare normalized forms
        cursor.execute('''
            SELECT * FROM monitored_accounts WHERE archived_at IS NULL
        ''')
        for r in cursor.fetchall():
            r = row_to_dict(r) if not isinstance(r, dict) else r
            if _normalize_company_name(r.get('company_name', '')) == normalized:
                return r
    return None


def find_account_by_domain(domain: str) -> Optional[dict]:
    """Find account whose website matches the given domain."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM monitored_accounts
            WHERE website IS NOT NULL AND archived_at IS NULL
        ''')
        for r in cursor.fetchall():
            r = row_to_dict(r) if not isinstance(r, dict) else r
            acct_domain = _extract_domain(r.get('website', ''))
            if acct_domain and acct_domain == domain:
                return r
    return None


def find_or_create_account(
    company_name: str,
    website: Optional[str] = None,
    industry: Optional[str] = None,
    company_size: Optional[str] = None,
    annual_revenue: Optional[str] = None,
    account_owner: Optional[str] = None,
) -> int:
    """Find existing account by name (normalized) or domain, or create new. Returns account_id."""
    # Auto-capitalize: "verbling" -> "Verbling", preserve acronyms like "ABB"
    if company_name and company_name[0].islower():
        company_name = company_name[0].upper() + company_name[1:]

    existing = find_account_by_name(company_name)
    if existing:
        return existing['id']

    # Domain fallback: if website provided, check for domain match
    if website:
        domain = _extract_domain(website)
        if domain:
            existing = find_account_by_domain(domain)
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


def _cascade_signal_status(
    account_id: int,
    new_signal_status: str,
    only_from_statuses: tuple = ('new',),
) -> int:
    """Update signal statuses when account status changes.

    Args:
        account_id: the account whose signals to update
        new_signal_status: the status to set on matching signals
        only_from_statuses: only update signals currently in one of these statuses

    Returns:
        Number of signals updated
    """
    placeholders = ', '.join(['?'] * len(only_from_statuses))
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f'''
            UPDATE intent_signals
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE account_id = ? AND status IN ({placeholders})
        ''', (new_signal_status, account_id) + only_from_statuses)
        conn.commit()
        updated = cursor.rowcount if hasattr(cursor, 'rowcount') else 0
        if updated:
            logger.info(
                "[ACCOUNT] Cascaded %d signals for account %d → %s",
                updated, account_id, new_signal_status,
            )
        return updated


def update_account_enrichment(account_id: int, **fields) -> bool:
    """Update account with enrichment data. Only fills in fields that are currently empty.

    Accepts: website, industry, company_size, annual_revenue, linkedin_url,
             hq_location, employee_count, funding_stage.
    """
    acct = get_account(account_id)
    if not acct:
        return False

    updates = []
    params = []
    for field_name, value in fields.items():
        if not value:
            continue
        current = acct.get(field_name)
        if current and str(current).strip():
            continue  # Don't overwrite existing data
        updates.append(f"{field_name} = ?")
        params.append(str(value).strip())

    if not updates:
        return False

    params.append(account_id)
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE monitored_accounts SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        conn.commit()
        logger.info("[ACCOUNT] Enriched account %d with %d fields", account_id, len(updates))
    return True


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
