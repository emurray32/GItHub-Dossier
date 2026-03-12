"""
V2 Enrollment Routes — REST endpoints for prospect enrollment into Apollo sequences.

Blueprint: enrollment_bp, prefix /v2/api/enrollment
"""
import logging

from flask import Blueprint, request, jsonify

from validators import validate_positive_int

logger = logging.getLogger(__name__)

enrollment_bp = Blueprint('v2_enrollment', __name__, url_prefix='/v2/api/enrollment')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(message, status_code=400):
    return jsonify({'status': 'error', 'message': message}), status_code


def _success(**kwargs):
    return jsonify({'status': 'success', **kwargs})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@enrollment_bp.route('/enroll', methods=['POST'])
def enroll():
    """Enroll a single prospect into an Apollo sequence.

    Body: { prospect_id: int, sequence_id?: str }
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

        sequence_id = data.get('sequence_id')
        if sequence_id is not None:
            if not isinstance(sequence_id, str) or not sequence_id.strip():
                return _error('sequence_id must be a non-empty string')
            sequence_id = sequence_id.strip()

        from v2.services.enrollment_service import enroll_prospect
        result = enroll_prospect(prospect_id, sequence_id=sequence_id)

        if result.get('status') == 'error':
            return _error(result.get('message', 'Enrollment failed'), 400)

        return _success(**result)

    except Exception as e:
        logger.exception("[ENROLLMENT ROUTE] Error enrolling prospect")
        return _error(str(e), 500)


@enrollment_bp.route('/bulk', methods=['POST'])
def bulk():
    """Enroll multiple prospects.

    Body: { prospect_ids: [int, ...] }
    """
    try:
        data = request.get_json()
        if not data:
            return _error('Request body is required')

        prospect_ids = data.get('prospect_ids')
        if not prospect_ids or not isinstance(prospect_ids, list):
            return _error('prospect_ids must be a non-empty list')

        if len(prospect_ids) > 100:
            return _error('Cannot enroll more than 100 prospects at once')

        # Validate each ID
        cleaned_ids = []
        for pid in prospect_ids:
            valid, cleaned = validate_positive_int(pid, 'prospect_id')
            if not valid:
                return _error(f'Invalid prospect_id in list: {cleaned}')
            cleaned_ids.append(cleaned)

        from v2.services.enrollment_service import bulk_enroll
        result = bulk_enroll(cleaned_ids)
        return _success(**result)

    except Exception as e:
        logger.exception("[ENROLLMENT ROUTE] Error in bulk enrollment")
        return _error(str(e), 500)


@enrollment_bp.route('/complete', methods=['POST'])
def complete():
    """Mark a prospect's sequence as complete.

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

        from v2.services.enrollment_service import mark_sequence_complete
        result = mark_sequence_complete(prospect_id)
        if not result:
            return _error('Prospect not found', 404)

        return _success(**result)

    except Exception as e:
        logger.exception("[ENROLLMENT ROUTE] Error marking sequence complete")
        return _error(str(e), 500)
