"""
V2 API Routes — REST endpoints for the intent-signal-first frontend.

All endpoints return JSON with {"status": "success", ...} or {"status": "error", "message": "..."}.
Uses validators.py for all user input validation.
"""
import logging
import json

from flask import Blueprint, request, jsonify

from v2.services.signal_service import (
    list_signals, get_signal, get_signal_workspace,
    update_signal_status, update_signal_campaign,
    get_signal_counts_by_status, get_owners,
)
from v2.services.account_service import (
    get_account, update_account_status, get_account_domain,
    mark_account_noise, mark_account_sequenced, mark_account_revisit,
)
from v2.services.prospect_service import (
    get_prospects_for_signal, bulk_create_prospects, filter_actionable_prospects,
)
from v2.services.writing_prefs_service import (
    get_writing_preferences, update_writing_preferences,
    get_bdr_preferences, update_bdr_preference, delete_bdr_preference,
    get_merged_preferences,
)
from v2.db import db_connection, rows_to_dicts, row_to_dict
from validators import (
    validate_positive_int, validate_scope, validate_notes,
    validate_search_query, validate_company_name,
)

logger = logging.getLogger(__name__)

api_bp = Blueprint('v2_api', __name__, url_prefix='/v2/api')


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _error(message, status_code=400):
    return jsonify({'status': 'error', 'message': message}), status_code


def _success(**kwargs):
    return jsonify({'status': 'success', **kwargs})


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

@api_bp.route('/signals', methods=['GET'])
def api_list_signals():
    """List intent signals with optional filters."""
    try:
        status_filter = request.args.get('status', None)
        owner_filter = request.args.get('owner', None)
        signal_type_filter = request.args.get('signal_type', None)

        # Validate status if provided
        if status_filter:
            valid, result = validate_scope(status_filter, ('new', 'sequenced', 'revisit', 'noise'))
            if not valid:
                return _error(result)
            status_filter = result

        # Validate limit/offset
        limit = request.args.get('limit', '50')
        valid, limit = validate_positive_int(limit, 'limit', max_val=1000)
        if not valid:
            return _error(limit)

        offset = request.args.get('offset', '0')
        valid, offset = validate_positive_int(offset, 'offset', max_val=100000)
        if not valid:
            return _error(offset)

        # Validate owner if provided
        if owner_filter:
            valid, owner_filter = validate_search_query(owner_filter)
            if not valid:
                return _error(owner_filter)

        result = list_signals(
            status=status_filter,
            owner=owner_filter,
            signal_type=signal_type_filter,
            limit=limit,
            offset=offset,
        )

        # Serialize datetimes
        for sig in result.get('signals', []):
            for key in ('created_at', 'updated_at'):
                if sig.get(key) and hasattr(sig[key], 'isoformat'):
                    sig[key] = sig[key].isoformat()

        return _success(signals=result['signals'], total=result['total'])
    except Exception as e:
        logger.exception("[V2 API] Error listing signals")
        return _error('Internal server error', 500)


@api_bp.route('/signals/<int:signal_id>', methods=['GET'])
def api_get_signal(signal_id):
    """Get a single signal with full workspace context."""
    try:
        workspace = get_signal_workspace(signal_id)
        if not workspace:
            return _error('Signal not found', 404)

        # Serialize datetimes in the workspace
        _serialize_workspace_dates(workspace)

        return _success(**workspace)
    except Exception as e:
        logger.exception("[V2 API] Error getting signal %d", signal_id)
        return _error('Internal server error', 500)


@api_bp.route('/signals/counts', methods=['GET'])
def api_signal_counts():
    """Get signal counts by status."""
    try:
        counts = get_signal_counts_by_status()
        return _success(counts=counts)
    except Exception as e:
        logger.exception("[V2 API] Error getting signal counts")
        return _error('Internal server error', 500)


@api_bp.route('/signals/owners', methods=['GET'])
def api_signal_owners():
    """Get distinct owners."""
    try:
        owners = get_owners()
        return _success(owners=owners)
    except Exception as e:
        logger.exception("[V2 API] Error getting owners")
        return _error('Internal server error', 500)


@api_bp.route('/signals/<int:signal_id>/status', methods=['PUT'])
def api_update_signal_status(signal_id):
    """Update a signal's internal bookkeeping status.

    NOTE: This is NOT the primary workflow status. The user-facing workflow
    is driven by account_status (new/sequenced/revisit/noise) via the
    /accounts/<id>/status endpoint. This endpoint only manages the
    signal-level archival state (new/actioned/archived) for internal
    bookkeeping. Normal UI flows should not call this directly — account
    status changes cascade to signals automatically.
    """
    try:
        data = request.get_json()
        if not data or 'status' not in data:
            return _error('status field is required')

        valid, status = validate_scope(data['status'], ('new', 'actioned', 'archived'))
        if not valid:
            return _error(status)

        ok = update_signal_status(signal_id, status)
        if not ok:
            return _error('Signal not found or update failed', 404)

        return _success(signal_id=signal_id, status=status)
    except Exception as e:
        logger.exception("[V2 API] Error updating signal status")
        return _error('Internal server error', 500)


@api_bp.route('/signals/<int:signal_id>/campaign', methods=['PUT'])
def api_update_signal_campaign(signal_id):
    """Update recommended campaign for a signal."""
    try:
        data = request.get_json()
        if not data or 'campaign_id' not in data:
            return _error('campaign_id is required')

        valid, campaign_id = validate_positive_int(data['campaign_id'], 'campaign_id')
        if not valid:
            return _error(campaign_id)

        reasoning = data.get('reasoning', '')
        if reasoning:
            valid, reasoning = validate_notes(reasoning)
            if not valid:
                return _error(reasoning)

        ok = update_signal_campaign(signal_id, campaign_id, reasoning=reasoning)
        if not ok:
            return _error('Signal not found or update failed', 404)

        return _success(signal_id=signal_id, campaign_id=campaign_id)
    except Exception as e:
        logger.exception("[V2 API] Error updating signal campaign")
        return _error('Internal server error', 500)


# ---------------------------------------------------------------------------
# Apollo People Search
# ---------------------------------------------------------------------------

@api_bp.route('/signals/<int:signal_id>/search', methods=['POST'])
def api_apollo_search(signal_id):
    """Trigger Apollo people search for this signal's account."""
    try:
        signal = get_signal(signal_id)
        if not signal:
            return _error('Signal not found', 404)

        account_id = signal['account_id']
        domain = get_account_domain(account_id)
        company_name = signal.get('company_name', '')
        if not domain and not company_name:
            return _error('Account has no website/domain or company name configured')

        data = request.get_json() or {}
        personas = data.get('personas', [])

        if not personas:
            return _error('At least one persona is required (e.g. {"title": "VP Engineering", "seniority": "vp"})')

        # Validate persona data
        for p in personas:
            if not isinstance(p, dict) or not p.get('title'):
                return _error('Each persona must have a "title" field')

        # Build Apollo people search request
        from apollo_client import apollo_api_call

        def _search_apollo(personas_list, org_filter):
            """Run Apollo search for a list of personas with given org filter."""
            candidates = []
            for persona in personas_list:
                all_titles = persona.get('allTitles') or [persona.get('title', '')]
                all_titles = [t for t in all_titles if t]
                if not all_titles:
                    continue
                seniority = persona.get('seniority', '')

                search_body = {
                    **org_filter,
                    'person_titles': all_titles,
                    'page': 1,
                    'per_page': 10,
                }
                if seniority:
                    search_body['person_seniorities'] = [seniority]

                try:
                    resp = apollo_api_call('post', 'https://api.apollo.io/v1/mixed_people/search', json=search_body)
                    if resp.status_code == 200:
                        result = resp.json()
                        people = result.get('people', [])
                        logger.info("[V2 API] Apollo search for %s: %d results",
                                    all_titles[0], len(people))
                        for person in people:
                            candidates.append({
                                'full_name': person.get('name', ''),
                                'first_name': person.get('first_name', ''),
                                'last_name': person.get('last_name', ''),
                                'title': person.get('title', ''),
                                'email': person.get('email', ''),
                                'email_verified': bool(person.get('email_status') == 'verified'),
                                'linkedin_url': person.get('linkedin_url', ''),
                                'apollo_person_id': person.get('id', ''),
                            })
                except RuntimeError as re_err:
                    raise re_err
                except Exception as e:
                    logger.warning("[V2 API] Apollo search error: %s", e)
            return candidates

        # Step 1: Search by domain first, then fall back to company name
        all_candidates = []
        try:
            if domain:
                all_candidates = _search_apollo(personas, {'q_organization_domains_list': [domain]})

            # Fallback: if domain search returned nothing, try company name
            if not all_candidates and company_name:
                logger.info("[V2 API] Domain search empty, retrying with company name: %s", company_name)
                all_candidates = _search_apollo(personas, {'q_organization_name': company_name})

            # Last resort: broaden search (drop seniority filter)
            if not all_candidates:
                broad_personas = [{'title': p.get('title', ''), 'allTitles': p.get('allTitles', [p.get('title', '')])} for p in personas]
                org_filter = {'q_organization_domains_list': [domain]} if domain else {'q_organization_name': company_name}
                all_candidates = _search_apollo(broad_personas, org_filter)
        except RuntimeError as re_err:
            return _error(str(re_err), 503)
        # Dedup by apollo_person_id
        seen_ids = set()
        deduped_candidates = []
        for p in all_candidates:
            pid = p.get('apollo_person_id', '')
            if pid and pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)
            deduped_candidates.append(p)

        # Step 2: Enrich top candidates to reveal emails via /people/match
        max_results = data.get('max_results', 3)
        verified_only = data.get('verified_only', True)
        enriched = []

        for candidate in deduped_candidates:
            if max_results and len(enriched) >= max_results:
                break

            # If search already returned a real verified email, use it directly
            candidate_email = candidate.get('email', '')
            if (candidate_email
                    and candidate_email != 'email_not_unlocked@domain.com'
                    and candidate.get('email_verified')):
                enriched.append(candidate)
                continue

            # Try enrichment to reveal email
            apollo_id = candidate.get('apollo_person_id')
            if not apollo_id:
                continue

            try:
                enrich_resp = apollo_api_call('post', 'https://api.apollo.io/v1/people/match', json={
                    'id': apollo_id,
                    'reveal_personal_emails': False,
                })
                if enrich_resp.status_code == 200:
                    person = enrich_resp.json().get('person', {})
                    email = person.get('email', '')
                    email_status = person.get('email_status', '')
                    is_verified = email_status == 'verified'

                    if email and email != 'email_not_unlocked@domain.com' and (not verified_only or is_verified):
                        enriched.append({
                            'full_name': person.get('name', '') or candidate['full_name'],
                            'first_name': person.get('first_name', '') or candidate['first_name'],
                            'last_name': person.get('last_name', '') or candidate['last_name'],
                            'title': person.get('title', '') or candidate['title'],
                            'email': email,
                            'email_verified': is_verified,
                            'linkedin_url': person.get('linkedin_url', '') or candidate['linkedin_url'],
                            'apollo_person_id': person.get('id', '') or apollo_id,
                        })
                        logger.info("[V2 API] Enriched %s -> %s (status=%s)",
                                    candidate['full_name'], email, email_status)
                    else:
                        logger.info("[V2 API] Enrichment for %s: email=%s status=%s (skipped)",
                                    candidate['full_name'], email or 'none', email_status)
                else:
                    logger.warning("[V2 API] Apollo enrich returned %d for %s",
                                   enrich_resp.status_code, candidate['full_name'])
            except RuntimeError:
                logger.warning("[V2 API] Apollo enrich failed for %s", candidate['full_name'])
                continue

        # Final dedup by email
        seen_emails = set()
        final = []
        for p in enriched:
            email = (p.get('email') or '').lower()
            if email and email in seen_emails:
                continue
            if email:
                seen_emails.add(email)
            final.append(p)

        # Step 3: Auto-save as prospects (merge search + save into one step)
        saved_count = 0
        if final:
            try:
                from v2.services.prospect_service import bulk_create_prospects
                # Inject signal_id and account_id into each prospect dict
                for p in final:
                    p['signal_id'] = signal_id
                    p['account_id'] = account_id
                saved_ids = bulk_create_prospects(final)
                saved_count = len(saved_ids) if isinstance(saved_ids, list) else 0
                logger.info("[V2 API] Auto-saved %d prospects for signal %d", saved_count, signal_id)
            except Exception as save_err:
                logger.warning("[V2 API] Auto-save prospects failed: %s", save_err)

        return _success(
            people=final,
            total=len(final),
            domain=domain,
            signal_id=signal_id,
            account_id=account_id,
            candidates_found=len(deduped_candidates),
            saved_count=saved_count,
        )
    except Exception as e:
        logger.exception("[V2 API] Error in Apollo search for signal %d", signal_id)
        return _error('Internal server error', 500)


# ---------------------------------------------------------------------------
# Prospects
# ---------------------------------------------------------------------------

@api_bp.route('/prospects', methods=['POST'])
def api_save_prospects():
    """Save found prospects."""
    try:
        data = request.get_json()
        if not data:
            return _error('Request body is required')

        signal_id = data.get('signal_id')
        account_id = data.get('account_id')
        prospects = data.get('prospects', [])

        if not signal_id or not account_id:
            return _error('signal_id and account_id are required')

        valid, signal_id = validate_positive_int(signal_id, 'signal_id')
        if not valid:
            return _error(signal_id)

        valid, account_id = validate_positive_int(account_id, 'account_id')
        if not valid:
            return _error(account_id)

        if not prospects or not isinstance(prospects, list):
            return _error('prospects must be a non-empty list')

        # Server-side filtering: reject DNC, enrolled, unverified, personal email
        from v2.services.prospect_service import is_already_enrolled, is_do_not_contact
        from email_utils import _filter_personal_email

        records = []
        skipped_enrolled = 0
        skipped_personal = 0
        skipped_no_email = 0
        skipped_unverified = 0
        skipped_dnc = 0
        for p in prospects:
            email = (p.get('email') or '').strip().lower()

            # Skip prospects with no email
            if not email:
                skipped_no_email += 1
                continue

            # Skip unverified emails
            if not p.get('email_verified'):
                skipped_unverified += 1
                continue

            # Skip personal emails (gmail, yahoo, etc.)
            # _filter_personal_email returns '' for personal domains, the email for business
            if not _filter_personal_email(email):
                skipped_personal += 1
                continue

            # Skip do-not-contact (flagged in any prior prospect record)
            if is_do_not_contact(email):
                skipped_dnc += 1
                continue

            # Skip already-enrolled contacts (across all signals/accounts)
            if is_already_enrolled(email):
                skipped_enrolled += 1
                continue

            records.append({
                'account_id': account_id,
                'signal_id': signal_id,
                'full_name': p.get('full_name', ''),
                'first_name': p.get('first_name', ''),
                'last_name': p.get('last_name', ''),
                'title': p.get('title', ''),
                'email': email,
                'email_verified': p.get('email_verified', False),
                'linkedin_url': p.get('linkedin_url', ''),
                'apollo_person_id': p.get('apollo_person_id', ''),
            })

        if not records:
            return _error(
                f'No valid prospects to save (skipped: {skipped_enrolled} already enrolled, '
                f'{skipped_personal} personal email, {skipped_no_email} no email, '
                f'{skipped_unverified} unverified email, {skipped_dnc} do-not-contact)'
            )

        ids = bulk_create_prospects(records)
        return _success(
            prospect_ids=ids,
            count=len(ids),
            skipped_enrolled=skipped_enrolled,
            skipped_personal=skipped_personal,
            skipped_no_email=skipped_no_email,
            skipped_unverified=skipped_unverified,
            skipped_dnc=skipped_dnc,
        )
    except Exception as e:
        logger.exception("[V2 API] Error saving prospects")
        return _error('Internal server error', 500)


@api_bp.route('/prospects', methods=['GET'])
def api_get_prospects():
    """Get prospects for a signal."""
    try:
        signal_id = request.args.get('signal_id')
        if not signal_id:
            return _error('signal_id query parameter is required')

        valid, signal_id = validate_positive_int(signal_id, 'signal_id')
        if not valid:
            return _error(signal_id)

        prospects = get_prospects_for_signal(signal_id)

        # Serialize datetimes
        for p in prospects:
            for key in ('created_at', 'updated_at'):
                if p.get(key) and hasattr(p[key], 'isoformat'):
                    p[key] = p[key].isoformat()

        return _success(prospects=prospects)
    except Exception as e:
        logger.exception("[V2 API] Error getting prospects")
        return _error('Internal server error', 500)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@api_bp.route('/signals/export', methods=['GET'])
def export_signals_csv():
    """Export signals + account context as CSV.

    Query params: status, owner, signal_type (same filters as list_signals)
    """
    import csv
    import io
    from flask import Response

    try:
        status = request.args.get('status')
        owner = request.args.get('owner')
        signal_type = request.args.get('signal_type')

        result = list_signals(
            status=status, owner=owner, signal_type=signal_type,
            limit=10000, offset=0,
        )
        signals = result.get('signals', [])

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'company_name', 'signal_type', 'signal_description', 'evidence_value',
            'signal_source', 'status', 'account_status', 'account_owner',
            'industry', 'company_size', 'current_tier', 'website', 'created_at',
        ])
        for s in signals:
            writer.writerow([
                s.get('company_name', ''),
                s.get('signal_type', ''),
                s.get('signal_description', ''),
                s.get('evidence_value', ''),
                s.get('signal_source', ''),
                s.get('status', ''),
                s.get('account_status', ''),
                s.get('account_owner', ''),
                s.get('industry', ''),
                s.get('company_size', ''),
                s.get('current_tier', ''),
                s.get('website', ''),
                s.get('created_at', ''),
            ])

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=signals_export.csv'},
        )
    except Exception:
        logger.exception("[V2 API] Error exporting signals CSV")
        return _error('Internal server error', 500)


# ---------------------------------------------------------------------------
# Drafts — routed to v2.routes.draft blueprint at /v2/api/drafts/*
# Enrollment — routed to v2.routes.enrollment blueprint at /v2/api/enrollment/*
# The frontend calls those blueprints directly (no stubs needed here).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

@api_bp.route('/campaigns', methods=['GET'])
def api_list_campaigns():
    """List campaigns from the existing campaigns table."""
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, sequence_config, campaign_type,
                       writing_guidelines
                FROM campaigns
                ORDER BY name
            ''')
            campaigns = rows_to_dicts(cursor.fetchall())

        return _success(campaigns=campaigns)
    except Exception as e:
        logger.exception("[V2 API] Error listing campaigns")
        return _error('Internal server error', 500)


# ---------------------------------------------------------------------------
# Writing Preferences
# ---------------------------------------------------------------------------

@api_bp.route('/writing-preferences', methods=['GET'])
def api_get_writing_preferences():
    """Get org-wide writing preferences."""
    try:
        prefs = get_writing_preferences()
        return _success(preferences=prefs)
    except Exception as e:
        logger.exception("[V2 API] Error getting writing preferences")
        return _error('Internal server error', 500)


@api_bp.route('/writing-preferences', methods=['PUT'])
def api_update_writing_preferences():
    """Update writing preferences."""
    try:
        data = request.get_json()
        if not data or not isinstance(data, dict):
            return _error('Request body must be a JSON object of key-value pairs')

        # Validate each value
        cleaned = {}
        for key, value in data.items():
            if not isinstance(key, str) or not key.strip():
                return _error(f'Invalid preference key: {key}')
            if not isinstance(value, str):
                return _error(f'Preference value for "{key}" must be a string')
            valid, cleaned_val = validate_notes(value)
            if not valid:
                return _error(f'Invalid value for "{key}": {cleaned_val}')
            cleaned[key.strip()] = cleaned_val

        ok = update_writing_preferences(cleaned)
        if not ok:
            return _error('Failed to update writing preferences', 500)

        return _success(updated=list(cleaned.keys()))
    except Exception as e:
        logger.exception("[V2 API] Error updating writing preferences")
        return _error('Internal server error', 500)


# ---------------------------------------------------------------------------
# BDR Writing Preferences (per-user overrides)
# ---------------------------------------------------------------------------

@api_bp.route('/bdr-writing-preferences/<email>', methods=['GET'])
def api_get_bdr_preferences(email):
    """Get a BDR's personal writing preferences + merged view.

    Returns:
        personal: list of raw BDR overrides
        merged: org prefs with BDR overrides applied
    """
    try:
        valid, email = validate_search_query(email)
        if not valid:
            return _error(email)

        personal = get_bdr_preferences(email)
        merged = get_merged_preferences(email)

        return _success(
            user_email=email,
            personal=personal,
            merged=merged,
        )
    except Exception as e:
        logger.exception("[V2 API] Error getting BDR preferences for %s", email)
        return _error('Internal server error', 500)


@api_bp.route('/bdr-writing-preferences/<email>', methods=['PUT'])
def api_update_bdr_preference(email):
    """Create or update a BDR personal writing preference.

    Body: { "key": "banned_phrases", "value": "circle back, ping", "override_mode": "add" }
    """
    try:
        valid, email = validate_search_query(email)
        if not valid:
            return _error(email)

        data = request.get_json()
        if not data:
            return _error('Request body is required')

        key = data.get('key', '').strip()
        value = data.get('value', '').strip()
        override_mode = data.get('override_mode', 'add').strip()

        if not key:
            return _error('key is required')
        if not value:
            return _error('value is required')

        valid_mode, mode = validate_scope(override_mode, ('add', 'replace', 'remove'))
        if not valid_mode:
            return _error(f'Invalid override_mode: {mode}')

        valid_val, cleaned_value = validate_notes(value)
        if not valid_val:
            return _error(f'Invalid value: {cleaned_value}')

        update_bdr_preference(email, key, cleaned_value, mode)

        # Return the updated merged view
        merged = get_merged_preferences(email)
        return _success(
            user_email=email,
            key=key,
            override_mode=mode,
            merged=merged,
        )
    except Exception as e:
        logger.exception("[V2 API] Error updating BDR preference for %s", email)
        return _error('Internal server error', 500)


@api_bp.route('/bdr-writing-preferences/<email>/<key>', methods=['DELETE'])
def api_delete_bdr_preference(email, key):
    """Delete a BDR personal writing preference.

    Optional query param: override_mode (if omitted, deletes all modes for this key)
    """
    try:
        valid, email = validate_search_query(email)
        if not valid:
            return _error(email)

        override_mode = request.args.get('override_mode')
        if override_mode:
            valid_mode, mode = validate_scope(override_mode, ('add', 'replace', 'remove'))
            if not valid_mode:
                return _error(f'Invalid override_mode: {mode}')
            override_mode = mode

        delete_bdr_preference(email, key, override_mode)
        return _success(user_email=email, key=key, deleted=True)
    except Exception as e:
        logger.exception("[V2 API] Error deleting BDR preference for %s", email)
        return _error('Internal server error', 500)


@api_bp.route('/bdr-writing-preferences', methods=['GET'])
def api_list_all_bdr_preferences():
    """List all BDRs who have personal preferences (admin view)."""
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT user_email
                FROM bdr_writing_preferences
                ORDER BY user_email
            ''')
            rows = rows_to_dicts(cursor.fetchall())
            emails = [r['user_email'] for r in rows]

        return _success(bdr_emails=emails, count=len(emails))
    except Exception as e:
        logger.exception("[V2 API] Error listing BDR preferences")
        return _error('Internal server error', 500)


# ---------------------------------------------------------------------------
# Account Status
# ---------------------------------------------------------------------------

@api_bp.route('/accounts/<int:account_id>/status', methods=['PUT'])
def api_update_account_status(account_id):
    """Update account status using workflow-aware helpers.

    Dispatches to the correct cascade helper so signal bookkeeping
    happens automatically:
      - noise    → mark_account_noise()    (archives all non-archived signals)
      - sequenced → mark_account_sequenced() (actions all new signals)
      - revisit  → mark_account_revisit()  (actions all new signals)
      - new      → plain update (reset, no cascade needed)
    """
    try:
        data = request.get_json()
        if not data or 'status' not in data:
            return _error('status field is required')

        valid, new_status = validate_scope(data['status'], ('new', 'sequenced', 'revisit', 'noise'))
        if not valid:
            return _error(new_status)

        # Dispatch to the correct cascade-aware helper
        dispatch = {
            'noise': mark_account_noise,
            'sequenced': mark_account_sequenced,
            'revisit': mark_account_revisit,
        }
        handler = dispatch.get(new_status)
        if handler:
            ok = handler(account_id)
        else:
            # 'new' is a reset — no signal cascade needed
            ok = update_account_status(account_id, new_status)

        if not ok:
            return _error('Account not found or invalid status', 404)

        return _success(account_id=account_id, status=new_status)
    except Exception as e:
        logger.exception("[V2 API] Error updating account status")
        return _error('Internal server error', 500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_workspace_dates(workspace):
    """Recursively serialize datetime objects in a workspace dict."""
    if isinstance(workspace, dict):
        for key, value in workspace.items():
            if hasattr(value, 'isoformat'):
                workspace[key] = value.isoformat()
            elif isinstance(value, dict):
                _serialize_workspace_dates(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _serialize_workspace_dates(item)
    elif isinstance(workspace, list):
        for item in workspace:
            if isinstance(item, dict):
                _serialize_workspace_dates(item)
