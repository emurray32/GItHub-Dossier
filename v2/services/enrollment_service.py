"""
Enrollment Service — enrolls prospects into Apollo sequences.

After drafts are approved, this service handles the final step: pushing the
prospect into an Apollo email sequence. It also tracks completion and triggers
account-level status transitions.

Apollo enrollment follows the SAME proven pattern as the legacy pipeline
(apollo_pipeline.py):
    1. Search for / create an Apollo CONTACT (not just a person)
    2. Inject approved draft content into the contact's typed custom fields
    3. Resolve the sending email account
    4. Enroll the contact into the sequence
    5. Only mark prospect/drafts as enrolled after all steps succeed

Flow:
    drafts approved -> enroll_prospect -> Apollo API -> prospect.enrollment_status = 'enrolled'
    sequence finishes -> mark_sequence_complete -> check account rollup
"""
import json
import logging
from typing import Optional, List

from v2.db import db_connection, row_to_dict, rows_to_dicts

logger = logging.getLogger(__name__)


def enroll_prospect(prospect_id: int, sequence_id: Optional[str] = None) -> dict:
    """Enroll a single prospect into an Apollo email sequence.

    Follows the proven repo pattern (apollo_pipeline._enroll_single_contact):
        1. Validate prospect (not DNC, not already enrolled, has approved drafts)
        2. Search for existing Apollo contact by email
        3. Build typed_custom_fields from approved draft content
        4. Create or update the Apollo contact with draft content
        5. Resolve sender email account
        6. Enroll the Apollo contact into the sequence
        7. Update prospect/draft statuses and account rollup

    Args:
        prospect_id: the prospect to enroll
        sequence_id: optional Apollo sequence/emailer_campaign id override

    Returns:
        Dict with status, prospect_id, sequence_id, apollo_response_ok
    """
    from v2.services.prospect_service import (
        get_prospect, update_prospect_enrollment, update_apollo_contact_id,
    )
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

    email = (prospect.get('email') or '').strip().lower()
    if not email:
        return {'status': 'error', 'message': 'Prospect has no email address'}

    if not prospect.get('email_verified'):
        return {
            'status': 'error',
            'message': 'Prospect email is not verified. Only verified emails can be enrolled.',
        }

    # 2. Check for approved drafts — dedup by step (use most recent per step)
    drafts = get_drafts_for_prospect(prospect_id)
    all_approved = [d for d in drafts if d.get('status') == 'approved']
    if not all_approved:
        return {
            'status': 'error',
            'message': 'No approved drafts found. Approve drafts before enrolling.',
        }

    # If multiple approved drafts exist for the same step (should not happen
    # after the generate_drafts cleanup, but defensive), keep only the latest.
    best_by_step = {}
    for d in sorted(all_approved, key=lambda x: x.get('updated_at') or x.get('created_at') or '', reverse=True):
        step = d.get('sequence_step')
        if step not in best_by_step:
            best_by_step[step] = d
    approved_drafts = sorted(best_by_step.values(), key=lambda x: x.get('sequence_step', 0))

    # 3. Determine sequence_id
    if not sequence_id:
        sequence_id = _resolve_sequence_id(prospect, approved_drafts)

    if not sequence_id:
        return {
            'status': 'error',
            'message': 'No sequence_id provided and no default could be determined',
        }

    # 4. Apollo enrollment — follows proven v1 pattern
    try:
        # Import through the legacy compatibility seam so existing tests and
        # patch points still intercept Apollo calls.
        from apollo_pipeline import apollo_api_call

        # --- Step A: Resolve custom field IDs (name → Apollo field ID) ---
        field_id_map = _resolve_custom_field_ids_cached()

        # --- Step B: Build typed_custom_fields from approved drafts ---
        typed_custom_fields = _build_typed_custom_fields(
            approved_drafts, field_id_map,
        )

        # --- Step C: Search for existing Apollo contact ---
        apollo_contact_id = prospect.get('apollo_contact_id')
        if not apollo_contact_id:
            apollo_contact_id = _find_apollo_contact(email)

        # --- Step D: Create or update Apollo contact ---
        if not apollo_contact_id:
            # Create new contact with custom fields
            create_payload = {
                'first_name': prospect.get('first_name') or email.split('@')[0],
                'last_name': prospect.get('last_name', ''),
                'email': email,
                'organization_name': prospect.get('company_name', ''),
                'run_dedupe': True,
            }
            if typed_custom_fields:
                create_payload['typed_custom_fields'] = typed_custom_fields

            create_resp = apollo_api_call(
                'post', 'https://api.apollo.io/api/v1/contacts',
                json=create_payload,
            )
            if create_resp.status_code in (200, 201):
                apollo_contact_id = create_resp.json().get('contact', {}).get('id')
            else:
                err = create_resp.text[:300]
                logger.warning("[ENROLL] Apollo contact create failed for %s: %s", email, err)
                return {
                    'status': 'error',
                    'message': f'Apollo contact creation failed: {err}',
                    'prospect_id': prospect_id,
                    'apollo_response_ok': False,
                }
        elif typed_custom_fields:
            # Update existing contact with draft content.
            update_resp = apollo_api_call(
                'put', f'https://api.apollo.io/api/v1/contacts/{apollo_contact_id}',
                json={'typed_custom_fields': typed_custom_fields},
            )
            if update_resp.status_code not in (200, 201):
                err = update_resp.text[:300]
                logger.warning(
                    "[ENROLL] Apollo contact update failed for %s (contact %s): %s",
                    email, apollo_contact_id, err,
                )
                return {
                    'status': 'error',
                    'message': f'Apollo contact update failed: {err}',
                    'prospect_id': prospect_id,
                    'apollo_response_ok': False,
                }

        if not apollo_contact_id:
            return {
                'status': 'error',
                'message': 'Could not create or find Apollo contact',
                'prospect_id': prospect_id,
                'apollo_response_ok': False,
            }

        # Persist apollo_contact_id on the prospect
        update_apollo_contact_id(prospect_id, apollo_contact_id)

        # --- Step E: Resolve sender email account ---
        email_account_id = _resolve_sender_email_account(sequence_id)
        if not email_account_id:
            raise RuntimeError(
                'send_email_from_email_account_id is required but could not be resolved. '
                'Configure an email account in sequence_mappings or apollo_client.'
            )

        # --- Step F: Enroll contact in sequence ---
        enroll_payload = {
            'emailer_campaign_id': sequence_id,
            'contact_ids': [apollo_contact_id],
            'send_email_from_email_account_id': email_account_id,
        }

        enroll_resp = apollo_api_call(
            'post',
            f'https://api.apollo.io/api/v1/emailer_campaigns/{sequence_id}/add_contact_ids',
            json=enroll_payload,
            timeout=30,
        )
        apollo_ok = enroll_resp.status_code in (200, 201)
        if not apollo_ok:
            apollo_error = enroll_resp.text[:300]
            logger.warning(
                "[ENROLL] Apollo enrollment failed for prospect %d: %s",
                prospect_id, apollo_error,
            )
            return {
                'status': 'error',
                'message': f'Apollo enrollment failed: {apollo_error}',
                'prospect_id': prospect_id,
                'sequence_id': sequence_id,
                'apollo_response_ok': False,
            }

        # Check if Apollo skipped this contact (e.g. already in campaign, no email)
        enroll_data = enroll_resp.json() if enroll_resp.text else {}
        skipped_ids = enroll_data.get('skipped_contact_ids') or []
        if apollo_contact_id in skipped_ids:
            skip_reason = enroll_data.get('skip_reason', 'unknown (check already_in_campaign or no_email)')
            logger.warning(
                "[ENROLL] Apollo skipped contact %s for prospect %d: %s",
                apollo_contact_id, prospect_id, skip_reason,
            )
            return {
                'status': 'error',
                'message': f'Apollo accepted request but skipped contact: {skip_reason}',
                'prospect_id': prospect_id,
                'sequence_id': sequence_id,
                'apollo_contact_id': apollo_contact_id,
                'apollo_response_ok': True,
                'skipped': True,
                'skip_reason': skip_reason,
            }

    except RuntimeError as e:
        logger.error("[ENROLL] Apollo API error enrolling prospect %d: %s", prospect_id, e)
        return {
            'status': 'error',
            'message': f'Apollo API error: {e}',
            'prospect_id': prospect_id,
            'apollo_response_ok': False,
        }
    except Exception as e:
        logger.error("[ENROLL] Unexpected error enrolling prospect %d: %s", prospect_id, e)
        return {
            'status': 'error',
            'message': f'Enrollment error: {e}',
            'prospect_id': prospect_id,
            'apollo_response_ok': False,
        }

    # 5. Update prospect enrollment status (only after Apollo success)
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
        except Exception as e:
            logger.warning("[ENROLL] Could not update account status for account %d: %s", account_id, e)

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
                'apollo_contact_id': apollo_contact_id,
                'num_approved_drafts': len(approved_drafts),
            },
            created_by='enrollment_service',
        )
    except Exception as e:
        logger.warning("[ENROLL] Could not log activity for enrollment: %s", e)

    return {
        'status': 'success',
        'prospect_id': prospect_id,
        'sequence_id': sequence_id,
        'sequence_name': sequence_name,
        'apollo_contact_id': apollo_contact_id,
        'apollo_response_ok': True,
    }


def bulk_enroll(prospect_ids: List[int], sequence_id: Optional[str] = None) -> dict:
    """Enroll multiple prospects, collecting per-prospect results.

    Args:
        prospect_ids: list of prospect ids to enroll
        sequence_id: optional Apollo sequence/emailer_campaign id override

    Returns:
        Dict with enrolled/failed/skipped counts and a details list with
        per-prospect outcome: {prospect_id, full_name, email, success, error}
    """
    from v2.services.prospect_service import get_prospect as _get_prospect

    enrolled = 0
    failed = 0
    skipped = 0
    details = []

    for pid in prospect_ids:
        # Pre-fetch prospect metadata for the response
        meta = _get_prospect(pid)
        full_name = (meta or {}).get('full_name', '')
        email = (meta or {}).get('email', '')

        try:
            result = enroll_prospect(pid, sequence_id=sequence_id)
            if result.get('status') == 'success':
                enrolled += 1
                details.append({
                    'prospect_id': pid,
                    'full_name': full_name,
                    'email': email,
                    'success': True,
                    'error': None,
                })
            else:
                error_msg = result.get('message', 'Enrollment failed')
                # Distinguish skipped (already enrolled / DNC) from real failures
                if result.get('skipped') or 'already has status' in error_msg or 'do-not-contact' in error_msg:
                    skipped += 1
                else:
                    failed += 1
                details.append({
                    'prospect_id': pid,
                    'full_name': full_name,
                    'email': email,
                    'success': False,
                    'error': error_msg,
                })
        except Exception as e:
            failed += 1
            details.append({
                'prospect_id': pid,
                'full_name': full_name,
                'email': email,
                'success': False,
                'error': str(e)[:300],
            })
            logger.error("[ENROLL] Bulk enroll error for prospect %d: %s", pid, e)

    return {
        'enrolled': enrolled,
        'failed': failed,
        'skipped': skipped,
        'total': len(prospect_ids),
        'details': details,
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
        except Exception as e:
            logger.warning("[ENROLL] Could not check account completion for %d: %s", account_id, e)

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
    except Exception as e:
        logger.warning("[ENROLL] Could not log sequence completion activity: %s", e)

    return {
        'status': 'success',
        'prospect_id': prospect_id,
        'account_id': account_id,
        'enrollment_status': 'sequence_complete',
        'account_moved_to_revisit': account_complete,
    }


# ---------------------------------------------------------------------------
# Apollo helpers — reuse proven patterns from apollo_pipeline.py
# ---------------------------------------------------------------------------

_NOT_RESOLVED = object()
_CACHED_FIELD_IDS = _NOT_RESOLVED


def _resolve_custom_field_ids_cached() -> dict:
    """Fetch Apollo custom field ID mapping, cached for the process lifetime.

    Reuses the same API endpoint as apollo_pipeline._resolve_custom_field_ids().
    Only caches on success; failures leave the sentinel so the next call retries.
    """
    global _CACHED_FIELD_IDS
    if _CACHED_FIELD_IDS is not _NOT_RESOLVED:
        return _CACHED_FIELD_IDS

    try:
        from apollo_pipeline import resolve_custom_field_ids
        result = resolve_custom_field_ids()
        _CACHED_FIELD_IDS = result
        return _CACHED_FIELD_IDS
    except (ImportError, Exception) as e:
        logger.warning("[ENROLL] Could not resolve custom field IDs: %s", e)
        return {}


def _build_typed_custom_fields(
    approved_drafts: list,
    field_id_map: dict,
) -> dict:
    """Build typed_custom_fields dict from approved drafts.

    Maps draft content (subject/body per step) to Apollo custom field IDs.
    Skips any field whose ID cannot be resolved (human-readable names are
    silently ignored by Apollo, so we warn and omit them).
    """
    typed_custom_fields = {}

    for draft in sorted(approved_drafts, key=lambda d: d.get('sequence_step', 0)):
        step = draft.get('sequence_step', 1)

        subject = draft.get('subject', '')
        body = draft.get('body', '')

        subject_key = f'subject_step_{step}'
        body_key = f'body_step_{step}'

        if subject:
            fid = field_id_map.get(subject_key)
            if fid:
                typed_custom_fields[fid] = subject
            else:
                logger.warning("[ENROLL] No custom field ID found for '%s' — skipping field", subject_key)
        if body:
            fid = field_id_map.get(body_key)
            if fid:
                typed_custom_fields[fid] = body
            else:
                logger.warning("[ENROLL] No custom field ID found for '%s' — skipping field", body_key)

    # Also set top-level email_subject / email_body for step 1
    step1 = next((d for d in approved_drafts if d.get('sequence_step') == 1), None)
    if step1:
        subj = step1.get('subject', '')
        bod = step1.get('body', '')
        if subj:
            fid = field_id_map.get('email_subject')
            if fid:
                typed_custom_fields[fid] = subj
            else:
                logger.warning("[ENROLL] No custom field ID found for 'email_subject' — skipping field")
        if bod:
            fid = field_id_map.get('email_body')
            if fid:
                typed_custom_fields[fid] = bod
            else:
                logger.warning("[ENROLL] No custom field ID found for 'email_body' — skipping field")

    return typed_custom_fields


def _find_apollo_contact(email: str) -> Optional[str]:
    """Search for an existing Apollo contact by email.

    Reuses the same API endpoint as apollo_pipeline._enroll_single_contact().
    Returns the Apollo contact ID if found, else None.
    """
    try:
        from apollo_pipeline import apollo_api_call
        search_resp = apollo_api_call(
            'post', 'https://api.apollo.io/api/v1/contacts/search',
            json={'q_keywords': email, 'per_page': 1},
        )
        if search_resp.status_code == 200:
            found = search_resp.json().get('contacts', [])
            if found:
                return found[0].get('id')
    except Exception as e:
        logger.warning("[ENROLL] Apollo contact search failed for %s: %s", email, e)
    return None


def _resolve_sender_email_account(sequence_id: str) -> Optional[str]:
    """Resolve the sending email account for enrollment.

    Resolution order (matches apollo_pipeline.bulk_enroll_contacts):
        1. Per-sequence override from sequence_mappings.owner_email_account_id
        2. Global default via apollo_pipeline._resolve_email_account()
    """
    # 1. Try per-sequence override
    if sequence_id:
        try:
            with db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT owner_email_account_id FROM sequence_mappings WHERE sequence_id = ?',
                    (sequence_id,),
                )
                row = cursor.fetchone()
                if row:
                    val = row['owner_email_account_id'] if isinstance(row, dict) else row[0]
                    if val:
                        return val
        except Exception as e:
            logger.warning("[ENROLL] Failed to look up per-sequence email account for %s: %s", sequence_id, e)

    # 2. Global default
    try:
        from apollo_pipeline import resolve_email_account
        return resolve_email_account()
    except (ImportError, Exception) as e:
        logger.warning("[ENROLL] Could not resolve email account: %s", e)
    return None


# ---------------------------------------------------------------------------
# Sequence resolution helpers
# ---------------------------------------------------------------------------

def _resolve_sequence_id(prospect: dict, drafts: list) -> Optional[str]:
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
        except Exception as e:
            # sequence_mappings table may not exist
            logger.warning("[ENROLL] Failed to query sequence_mappings for default sequence: %s", e)

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
    except Exception as e:
        logger.warning("[ENROLL] Failed to look up sequence name for %s: %s", sequence_id, e)
    return None
