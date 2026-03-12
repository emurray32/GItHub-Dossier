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
    get_account, update_account_status, get_account_domain, mark_account_noise,
)
from v2.services.prospect_service import (
    get_prospects_for_signal, bulk_create_prospects, filter_actionable_prospects,
)
from v2.services.writing_prefs_service import (
    get_writing_preferences, update_writing_preferences,
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
            valid, result = validate_scope(status_filter, ('new', 'actioned', 'archived'))
            if not valid:
                return _error(result)
            status_filter = result

        # Validate limit/offset
        limit = request.args.get('limit', '50')
        valid, limit = validate_positive_int(limit, 'limit', max_val=200)
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
        return _error(str(e), 500)


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
        return _error(str(e), 500)


@api_bp.route('/signals/counts', methods=['GET'])
def api_signal_counts():
    """Get signal counts by status."""
    try:
        counts = get_signal_counts_by_status()
        return _success(counts=counts)
    except Exception as e:
        logger.exception("[V2 API] Error getting signal counts")
        return _error(str(e), 500)


@api_bp.route('/signals/owners', methods=['GET'])
def api_signal_owners():
    """Get distinct owners."""
    try:
        owners = get_owners()
        return _success(owners=owners)
    except Exception as e:
        logger.exception("[V2 API] Error getting owners")
        return _error(str(e), 500)


@api_bp.route('/signals/<int:signal_id>/status', methods=['PUT'])
def api_update_signal_status(signal_id):
    """Update signal status."""
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
        return _error(str(e), 500)


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
        return _error(str(e), 500)


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
        if not domain:
            return _error('Account has no website/domain configured')

        data = request.get_json() or {}
        personas = data.get('personas', [])

        if not personas:
            return _error('At least one persona is required (e.g. {"title": "VP Engineering", "seniority": "vp"})')

        # Validate persona data
        for p in personas:
            if not isinstance(p, dict) or not p.get('title'):
                return _error('Each persona must have a "title" field')

        # Build Apollo people search request
        from apollo_pipeline import apollo_api_call

        all_people = []
        for persona in personas:
            title = persona.get('title', '')
            seniority = persona.get('seniority', '')

            search_body = {
                'q_organization_domains': domain,
                'person_titles': [title],
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
                    for person in people:
                        all_people.append({
                            'full_name': person.get('name', ''),
                            'first_name': person.get('first_name', ''),
                            'last_name': person.get('last_name', ''),
                            'title': person.get('title', ''),
                            'email': person.get('email', ''),
                            'email_verified': bool(person.get('email_status') == 'verified'),
                            'linkedin_url': person.get('linkedin_url', ''),
                            'apollo_person_id': person.get('id', ''),
                        })
                else:
                    logger.warning("[V2 API] Apollo search returned %d for persona %s",
                                   resp.status_code, title)
            except RuntimeError as re_err:
                return _error(str(re_err), 503)

        # Dedup by email
        seen_emails = set()
        deduped = []
        for p in all_people:
            email = (p.get('email') or '').lower()
            if email and email in seen_emails:
                continue
            if email:
                seen_emails.add(email)
            deduped.append(p)

        return _success(
            people=deduped,
            total=len(deduped),
            domain=domain,
            signal_id=signal_id,
            account_id=account_id,
        )
    except Exception as e:
        logger.exception("[V2 API] Error in Apollo search for signal %d", signal_id)
        return _error(str(e), 500)


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

        # Prepare prospect records
        records = []
        for p in prospects:
            records.append({
                'account_id': account_id,
                'signal_id': signal_id,
                'full_name': p.get('full_name', ''),
                'first_name': p.get('first_name', ''),
                'last_name': p.get('last_name', ''),
                'title': p.get('title', ''),
                'email': p.get('email', ''),
                'email_verified': p.get('email_verified', False),
                'linkedin_url': p.get('linkedin_url', ''),
                'apollo_person_id': p.get('apollo_person_id', ''),
            })

        ids = bulk_create_prospects(records)
        return _success(prospect_ids=ids, count=len(ids))
    except Exception as e:
        logger.exception("[V2 API] Error saving prospects")
        return _error(str(e), 500)


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
        return _error(str(e), 500)


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
                SELECT id, campaign_name, sequence_config, campaign_type,
                       writing_guidelines
                FROM campaigns
                ORDER BY campaign_name
            ''')
            campaigns = rows_to_dicts(cursor.fetchall())

        return _success(campaigns=campaigns)
    except Exception as e:
        logger.exception("[V2 API] Error listing campaigns")
        return _error(str(e), 500)


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
        return _error(str(e), 500)


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
        return _error(str(e), 500)


# ---------------------------------------------------------------------------
# Account Status
# ---------------------------------------------------------------------------

@api_bp.route('/accounts/<int:account_id>/status', methods=['PUT'])
def api_update_account_status(account_id):
    """Update account status (noise/revisit/etc)."""
    try:
        data = request.get_json()
        if not data or 'status' not in data:
            return _error('status field is required')

        valid, new_status = validate_scope(data['status'], ('new', 'sequenced', 'revisit', 'noise'))
        if not valid:
            return _error(new_status)

        ok = update_account_status(account_id, new_status)
        if not ok:
            return _error('Account not found or invalid status', 404)

        return _success(account_id=account_id, status=new_status)
    except Exception as e:
        logger.exception("[V2 API] Error updating account status")
        return _error(str(e), 500)


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
