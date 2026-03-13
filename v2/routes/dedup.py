"""
V2 Dedup Routes — find and resolve duplicate intent signals.

Blueprint: dedup_bp, prefix /v2/api/dedup
"""
import logging

from flask import Blueprint, request, jsonify

from validators import validate_positive_int

logger = logging.getLogger(__name__)

dedup_bp = Blueprint('v2_dedup', __name__, url_prefix='/v2/api/dedup')


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

@dedup_bp.route('/summary', methods=['GET'])
def summary():
    """High-level dedup stats: total duplicates, clusters, savings estimate."""
    try:
        from v2.services.dedup_service import get_dedup_summary
        return _success(**get_dedup_summary())
    except Exception:
        logger.exception("[DEDUP] Error fetching dedup summary")
        return _error('Internal server error', 500)


@dedup_bp.route('/exact', methods=['GET'])
def exact_duplicates():
    """List all exact duplicate clusters (same account + type + evidence)."""
    try:
        from v2.services.dedup_service import find_exact_duplicates
        clusters = find_exact_duplicates()
        return _success(
            clusters=clusters,
            total_clusters=len(clusters),
            total_duplicates=sum(c['count'] - 1 for c in clusters),
        )
    except Exception:
        logger.exception("[DEDUP] Error finding exact duplicates")
        return _error('Internal server error', 500)


@dedup_bp.route('/by-account', methods=['GET'])
def by_account():
    """Find same-type signal groups, optionally filtered to one account.

    Query params: account_id (optional)
    """
    try:
        account_id = request.args.get('account_id')
        if account_id:
            valid, account_id = validate_positive_int(account_id, 'account_id')
            if not valid:
                return _error(account_id)

        from v2.services.dedup_service import find_same_account_type_dupes
        groups = find_same_account_type_dupes(account_id)
        return _success(groups=groups, total_groups=len(groups))
    except Exception:
        logger.exception("[DEDUP] Error finding account duplicates")
        return _error('Internal server error', 500)


@dedup_bp.route('/archive', methods=['POST'])
def archive():
    """Archive specific duplicate signals, keeping one canonical signal.

    Body: { keep_signal_id: int, archive_signal_ids: [int, ...] }
    """
    try:
        data = request.get_json()
        if not data:
            return _error('Request body is required')

        keep_id = data.get('keep_signal_id')
        if not keep_id:
            return _error('keep_signal_id is required')
        valid, keep_id = validate_positive_int(keep_id, 'keep_signal_id')
        if not valid:
            return _error(keep_id)

        archive_ids = data.get('archive_signal_ids')
        if not archive_ids or not isinstance(archive_ids, list):
            return _error('archive_signal_ids must be a non-empty list')

        if len(archive_ids) > 200:
            return _error('Cannot archive more than 200 signals at once')

        cleaned_ids = []
        for sid in archive_ids:
            valid, cleaned = validate_positive_int(sid, 'signal_id')
            if not valid:
                return _error(f'Invalid signal_id in list: {cleaned}')
            cleaned_ids.append(cleaned)

        from v2.services.dedup_service import archive_duplicates
        result = archive_duplicates(cleaned_ids, keep_id)

        if result.get('status') == 'error':
            return _error(result.get('message', 'Archive failed'))

        return _success(**result)

    except Exception:
        logger.exception("[DEDUP] Error archiving duplicates")
        return _error('Internal server error', 500)


@dedup_bp.route('/auto-clean', methods=['POST'])
def auto_clean():
    """Automatically archive all exact duplicates, keeping the oldest per cluster."""
    try:
        from v2.services.dedup_service import auto_archive_exact_duplicates
        result = auto_archive_exact_duplicates()
        return _success(**result)
    except Exception:
        logger.exception("[DEDUP] Error in auto-clean")
        return _error('Internal server error', 500)
