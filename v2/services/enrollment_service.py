"""
Enrollment Service — enrolls prospects into Apollo sequences.

After drafts are approved, this service handles the final step: pushing the
prospect into an Apollo email sequence. It also tracks completion and triggers
account-level status transitions.

Flow:
    drafts approved -> enroll_prospect -> Apollo API -> prospect.enrollment_status = 'enrolled'
    sequence finishes -> mark_sequence_complete -> check account rollup
"""
import logging
from typing import Optional, List

from v2.db import db_connection, row_to_dict, rows_to_dicts

logger = logging.getLogger(__name__)


def enroll_prospect(prospect_id: int, sequence_id: Optional[str] = None) -> dict:
    """Enroll a single prospect into an Apollo email sequence.

    Steps:
        1. Load prospect and validate (not DNC, not already enrolled)
        2. Load approved drafts
        3. Determine sequence_id from argument, campaign, or default
        4. Call Apollo API to add contact to sequence
        5. Update prospect enrollment status
        6. Update account status if needed
        7. Log activity

    Args:
        prospect_id: the prospect to enroll
        sequence_id: optional Apollo sequence/emailer_campaign id override

    Returns:
        Dict with status, prospect_id, sequence_id, apollo_response_ok
    """
    from v2.services.prospect_service import get_prospect, update_prospect_enrollment
    from v2.services.draft_service import get_drafts_for_prospect

    # 1. Load and validate prospect
    prospect = get_prospect(prospect_id)
    if not prospect:
        return {'status': 'error', 'message': f'Prospect {prospect_id} not found'}

    if prospect.get('do_not_contact'):
        return {'status': 'error', 'message': 'Prospect is flagged as do-not-contact'}

    if prospect.get('enrollment_status') in ('enrolled', 'sequence_complete'):
        return {
            'status': 'error',
            'message': f'Prospect already has status: {prospect["enrollment_status"]}',
        }

    if not prospect.get('apollo_person_id'):
        return {'status': 'error', 'message': 'Prospect has no Apollo person ID'}

    # 2. Check for approved drafts
    drafts = get_drafts_for_prospect(prospect_id)
    approved_drafts = [d for d in drafts if d.get('status') == 'approved']
    if not approved_drafts:
        return {
            'status': 'error',
            'message': 'No approved drafts found. Approve drafts before enrolling.',
        }

    # 3. Determine sequence_id
    if not sequence_id:
        sequence_id = _resolve_sequence_id(prospect, drafts)

    if not sequence_id:
        return {
            'status': 'error',
            'message': 'No sequence_id provided and no default could be determined',
        }

    # 4. Call Apollo API
    apollo_ok = False
    apollo_error = None
    try:
        from apollo_pipeline import apollo_api_call

        response = apollo_api_call(
            'post',
            f'https://api.apollo.io/api/v1/emailer_campaigns/{sequence_id}/add_contact_ids',
            json={
                'contact_ids': [prospect['apollo_person_id']],
                'emailer_campaign_id': sequence_id,
            },
            timeout=30,
        )
        apollo_ok = response.status_code in (200, 201)
        if not apollo_ok:
            apollo_error = response.text[:300]
            logger.warning(
                "[ENROLL] Apollo enrollment failed for prospect %d: %s",
                prospect_id, apollo_error,
            )
    except RuntimeError as e:
        apollo_error = str(e)
        logger.error("[ENROLL] Apollo API error enrolling prospect %d: %s",
                      prospect_id, e)
    except Exception as e:
        apollo_error = str(e)
        logger.error("[ENROLL] Unexpected error enrolling prospect %d: %s",
                      prospect_id, e)

    if not apollo_ok:
        return {
            'status': 'error',
            'message': f'Apollo enrollment failed: {apollo_error or "unknown error"}',
            'prospect_id': prospect_id,
            'sequence_id': sequence_id,
            'apollo_response_ok': False,
        }

    # 5. Update prospect enrollment status
    sequence_name = _lookup_sequence_name(sequence_id)
    update_prospect_enrollment(
        prospect_id,
        enrollment_status='enrolled',
        sequence_id=sequence_id,
        sequence_name=sequence_name,
    )

    # Mark drafts as enrolled
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE drafts SET status = 'enrolled', updated_at = CURRENT_TIMESTAMP
            WHERE prospect_id = ? AND status = 'approved'
        ''', (prospect_id,))
        conn.commit()

    # 6. Check if this makes the account 'sequenced'
    account_id = prospect.get('account_id')
    if account_id:
        try:
            from v2.services.account_service import mark_account_sequenced
            mark_account_sequenced(account_id)
        except Exception:
            logger.debug("[ENROLL] Could not update account status for account %d", account_id)

    # 7. Log activity
    try:
        from v2.services.activity_service import log_activity
        log_activity(
            event_type='prospect_enrolled',
            entity_type='prospect',
            entity_id=prospect_id,
            details={
                'sequence_id': sequence_id,
                'sequence_name': sequence_name,
                'account_id': account_id,
                'num_approved_drafts': len(approved_drafts),
            },
            created_by='enrollment_service',
        )
    except Exception:
        logger.debug("[ENROLL] Could not log activity for enrollment")

    return {
        'status': 'success',
        'prospect_id': prospect_id,
        'sequence_id': sequence_id,
        'sequence_name': sequence_name,
        'apollo_response_ok': True,
    }


def bulk_enroll(prospect_ids: List[int]) -> dict:
    """Enroll multiple prospects, collecting results.

    Args:
        prospect_ids: list of prospect ids to enroll

    Returns:
        Dict with enrolled count, failed count, and per-prospect results
    """
    enrolled = 0
    failed = 0
    results = []

    for pid in prospect_ids:
        try:
            result = enroll_prospect(pid)
            if result.get('status') == 'success':
                enrolled += 1
            else:
                failed += 1
            results.append(result)
        except Exception as e:
            failed += 1
            results.append({
                'status': 'error',
                'prospect_id': pid,
                'message': str(e)[:300],
            })
            logger.error("[ENROLL] Bulk enroll error for prospect %d: %s", pid, e)

    return {
        'enrolled': enrolled,
        'failed': failed,
        'total': len(prospect_ids),
        'results': results,
    }


def mark_sequence_complete(prospect_id: int) -> Optional[dict]:
    """Mark a prospect's sequence as complete and check account rollup.

    When ALL prospects for an account have completed sequences, the account
    moves to 'revisit' status.

    Args:
        prospect_id: the prospect whose sequence is complete

    Returns:
        Dict with status info, or None if prospect not found
    """
    from v2.services.prospect_service import get_prospect, update_prospect_status

    prospect = get_prospect(prospect_id)
    if not prospect:
        return None

    update_prospect_status(prospect_id, 'sequence_complete')

    # Check if ALL prospects for this account are now complete
    account_id = prospect.get('account_id')
    account_complete = False
    if account_id:
        try:
            from v2.services.account_service import (
                check_all_sequences_complete, mark_account_revisit,
            )
            if check_all_sequences_complete(account_id):
                mark_account_revisit(account_id)
                account_complete = True
        except Exception:
            logger.debug("[ENROLL] Could not check account completion for %d", account_id)

    # Log activity
    try:
        from v2.services.activity_service import log_activity
        log_activity(
            event_type='sequence_completed',
            entity_type='prospect',
            entity_id=prospect_id,
            details={
                'account_id': account_id,
                'account_moved_to_revisit': account_complete,
            },
            created_by='enrollment_service',
        )
    except Exception:
        logger.debug("[ENROLL] Could not log sequence completion activity")

    return {
        'status': 'success',
        'prospect_id': prospect_id,
        'account_id': account_id,
        'enrollment_status': 'sequence_complete',
        'account_moved_to_revisit': account_complete,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_sequence_id(prospect: dict, drafts: List[dict]) -> Optional[str]:
    """Try to determine the right Apollo sequence ID.

    Resolution order:
        1. Prospect already has a sequence_id assigned
        2. Campaign linked to the drafts has a sequence_id
        3. Default sequence from sequence_mappings table
    """
    # 1. Prospect already assigned
    if prospect.get('sequence_id'):
        return prospect['sequence_id']

    # 2. From campaign
    campaign_id = None
    for d in drafts:
        if d.get('campaign_id'):
            campaign_id = d['campaign_id']
            break

    if campaign_id:
        seq = _get_default_sequence_id(campaign_id)
        if seq:
            return seq

    # 3. Global default
    return _get_default_sequence_id()


def _get_default_sequence_id(campaign_id: Optional[int] = None) -> Optional[str]:
    """Look up a default sequence ID.

    If campaign_id is provided, check campaign_personas for a linked sequence.
    Otherwise fall back to the first enabled sequence_mapping.
    """
    with db_connection() as conn:
        cursor = conn.cursor()

        # Try campaign personas first
        if campaign_id:
            cursor.execute('''
                SELECT sequence_id FROM campaign_personas
                WHERE campaign_id = ?
                  AND sequence_id IS NOT NULL AND sequence_id != ''
                ORDER BY priority ASC
                LIMIT 1
            ''', (campaign_id,))
            row = cursor.fetchone()
            if row:
                val = row['sequence_id'] if isinstance(row, dict) else row[0]
                if val:
                    return val

        # Fall back to first enabled sequence mapping
        try:
            cursor.execute('''
                SELECT sequence_id FROM sequence_mappings
                WHERE enabled = 1
                ORDER BY sequence_name ASC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            if row:
                val = row['sequence_id'] if isinstance(row, dict) else row[0]
                if val:
                    return val
        except Exception:
            # sequence_mappings table may not exist
            pass

    return None


def _lookup_sequence_name(sequence_id: str) -> Optional[str]:
    """Look up a human-readable sequence name for an Apollo sequence ID."""
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sequence_name FROM sequence_mappings WHERE sequence_id = ?",
                (sequence_id,),
            )
            row = cursor.fetchone()
            if row:
                return row['sequence_name'] if isinstance(row, dict) else row[0]
    except Exception:
        pass
    return None
