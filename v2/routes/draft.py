"""
V2 Draft Routes — REST endpoints for email draft generation, editing, and approval.

Blueprint: draft_bp, prefix /v2/api/drafts
"""
import logging
from datetime import datetime

from flask import Blueprint, request, jsonify

from validators import validate_positive_int, validate_notes

logger = logging.getLogger(__name__)

draft_bp = Blueprint('v2_draft', __name__, url_prefix='/v2/api/drafts')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(message, status_code=400):
    return jsonify({'status': 'error', 'message': message}), status_code


def _success(**kwargs):
    return jsonify({'status': 'success', **kwargs})


def _serialize_dates(obj):
    """Recursively convert datetime objects to ISO strings in dicts and lists."""
    if isinstance(obj, dict):
        return {k: _serialize_dates(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_dates(item) for item in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif hasattr(obj, 'isoformat'):
        return obj.isoformat()
    return obj


def _serialize_list(items):
    """Serialize a list of dicts with datetime fields."""
    return [_serialize_dates(item) for item in items]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@draft_bp.route('/generate', methods=['POST'])
def generate():
    """Generate email drafts for a prospect.

    Body: { prospect_id: int, signal_id: int, campaign_id?: int }
    """
    try:
        data = request.get_json()
        if not data:
            return _error('Request body is required')

        # Validate required fields
        prospect_id = data.get('prospect_id')
        if not prospect_id:
            return _error('prospect_id is required')
        valid, prospect_id = validate_positive_int(prospect_id, 'prospect_id')
        if not valid:
            return _error(prospect_id)

        signal_id = data.get('signal_id')
        if not signal_id:
            return _error('signal_id is required')
        valid, signal_id = validate_positive_int(signal_id, 'signal_id')
        if not valid:
            return _error(signal_id)

        campaign_id = data.get('campaign_id')
        if campaign_id:
            valid, campaign_id = validate_positive_int(campaign_id, 'campaign_id')
            if not valid:
                return _error(campaign_id)
        else:
            campaign_id = None

        sequence_config = data.get('sequence_config')  # Optional dict: {num_steps, single_thread}

        from v2.services.draft_service import generate_drafts
        drafts = generate_drafts(prospect_id, signal_id, campaign_id, sequence_config_override=sequence_config)
        return _success(drafts=_serialize_list(drafts), count=len(drafts))

    except ValueError as e:
        return _error(str(e), 404)
    except Exception as e:
        logger.exception("[DRAFT ROUTE] Error generating drafts")
        return _error('Internal server error', 500)


@draft_bp.route('/', methods=['GET'])
def list_drafts():
    """Get drafts for a prospect.

    Query: ?prospect_id=X
    """
    try:
        prospect_id = request.args.get('prospect_id')
        if not prospect_id:
            return _error('prospect_id query parameter is required')

        valid, prospect_id = validate_positive_int(prospect_id, 'prospect_id')
        if not valid:
            return _error(prospect_id)

        from v2.services.draft_service import get_drafts_for_prospect
        drafts = get_drafts_for_prospect(prospect_id)
        return _success(drafts=_serialize_list(drafts))

    except Exception as e:
        logger.exception("[DRAFT ROUTE] Error listing drafts")
        return _error('Internal server error', 500)


@draft_bp.route('/<int:draft_id>', methods=['GET'])
def get_single_draft(draft_id):
    """Get a single draft by ID."""
    try:
        from v2.services.draft_service import get_draft
        draft = get_draft(draft_id)
        if not draft:
            return _error('Draft not found', 404)
        return _success(draft=_serialize_dates(draft))

    except Exception as e:
        logger.exception("[DRAFT ROUTE] Error getting draft %d", draft_id)
        return _error('Internal server error', 500)


@draft_bp.route('/<int:draft_id>', methods=['PUT'])
def update_single_draft(draft_id):
    """Update a draft's subject and/or body.

    Body: { subject?: str, body?: str }
    """
    try:
        data = request.get_json()
        if not data:
            return _error('Request body is required')

        subject = data.get('subject')
        body = data.get('body')

        if subject is None and body is None:
            return _error('At least one of subject or body is required')

        # Validate text fields
        if subject is not None:
            valid, subject = validate_notes(subject)
            if not valid:
                return _error(f'Invalid subject: {subject}')

        if body is not None:
            valid, body = validate_notes(body)
            if not valid:
                return _error(f'Invalid body: {body}')

        from v2.services.draft_service import update_draft
        draft = update_draft(draft_id, subject=subject, body=body)
        if not draft:
            return _error('Draft not found', 404)

        return _success(draft=_serialize_dates(draft))

    except Exception as e:
        logger.exception("[DRAFT ROUTE] Error updating draft %d", draft_id)
        return _error('Internal server error', 500)


@draft_bp.route('/<int:draft_id>/regenerate', methods=['POST'])
def regenerate(draft_id):
    """Regenerate a draft with critique feedback.

    Body: { critique: str }
    """
    try:
        data = request.get_json()
        if not data:
            return _error('Request body is required')

        critique = data.get('critique')
        if not critique:
            return _error('critique is required')

        valid, critique = validate_notes(critique)
        if not valid:
            return _error(f'Invalid critique: {critique}')

        from v2.services.draft_service import regenerate_draft
        draft = regenerate_draft(draft_id, critique)
        if not draft:
            return _error('Draft not found', 404)

        return _success(draft=_serialize_dates(draft))

    except Exception as e:
        logger.exception("[DRAFT ROUTE] Error regenerating draft %d", draft_id)
        return _error('Internal server error', 500)


@draft_bp.route('/<int:draft_id>/approve', methods=['POST'])
def approve(draft_id):
    """Mark a single draft as approved."""
    try:
        from v2.services.draft_service import approve_draft
        draft = approve_draft(draft_id)
        if not draft:
            return _error('Draft not found', 404)

        return _success(draft=_serialize_dates(draft))

    except Exception as e:
        logger.exception("[DRAFT ROUTE] Error approving draft %d", draft_id)
        return _error('Internal server error', 500)


@draft_bp.route('/approve-all', methods=['POST'])
def approve_all():
    """Approve all drafts for a prospect.

    Body: { prospect_id: int }
    """
    try:
        data = request.get_json()
        if not data:
            return _error('Request body is required')

        prospect_id = data.get('prospect_id')
        if not prospect_id:
            return _error('prospect_id is required')

        valid, prospect_id = validate_positive_int(prospect_id, 'prospect_id')
        if not valid:
            return _error(prospect_id)

        from v2.services.draft_service import approve_all_drafts
        drafts = approve_all_drafts(prospect_id)
        return _success(drafts=_serialize_list(drafts), count=len(drafts))

    except Exception as e:
        logger.exception("[DRAFT ROUTE] Error approving all drafts")
        return _error('Internal server error', 500)
