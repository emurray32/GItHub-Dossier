"""
V2 Ingestion Routes — Flask blueprint for importing signals via CSV,
manual entry, or bulk scan-signal conversion.

Blueprint name: ingestion_bp
URL prefix:     /v2/api/ingest
"""
import logging

from flask import Blueprint, request, jsonify

from validators import validate_company_name, validate_positive_int, validate_notes
from v2.services import ingestion_service

logger = logging.getLogger(__name__)

ingestion_bp = Blueprint('v2_ingestion', __name__, url_prefix='/v2/api/ingest')


# ---------------------------------------------------------------------------
# POST /v2/api/ingest/csv
# ---------------------------------------------------------------------------

@ingestion_bp.route('/csv', methods=['POST'])
def ingest_csv():
    """Upload a CSV file to create intent signals in bulk.

    Accepts multipart/form-data with:
        file          — the CSV file (required, must end in .csv)
        source_label  — optional label for tracking (default: 'csv_upload')
        created_by    — optional user identifier
    """
    # --- Validate file ---
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file provided'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'status': 'error', 'message': 'No file selected'}), 400

    if not file.filename.lower().endswith('.csv'):
        return jsonify({'status': 'error', 'message': 'File must be a .csv file'}), 400

    # Read content
    try:
        file_content = file.read()
    except Exception as exc:
        logger.exception("[INGEST ROUTE] Failed to read uploaded file")
        return jsonify({'status': 'error', 'message': f'Failed to read file: {str(exc)[:100]}'}), 400

    if not file_content:
        return jsonify({'status': 'error', 'message': 'File is empty'}), 400

    # 5 MB limit
    if len(file_content) > 5 * 1024 * 1024:
        return jsonify({'status': 'error', 'message': 'File exceeds 5 MB limit'}), 400

    # Optional form fields
    source_label = (request.form.get('source_label') or 'csv_upload').strip()
    created_by = (request.form.get('created_by') or '').strip() or None

    # --- Run ingestion ---
    try:
        result = ingestion_service.ingest_csv(
            file_content=file_content,
            source_label=source_label,
            created_by=created_by,
        )
        return jsonify({'status': 'success', 'result': result}), 200
    except Exception as exc:
        logger.exception("[INGEST ROUTE] CSV ingestion failed")
        return jsonify({'status': 'error', 'message': f'Ingestion failed: {str(exc)[:200]}'}), 500


# ---------------------------------------------------------------------------
# POST /v2/api/ingest/manual
# ---------------------------------------------------------------------------

@ingestion_bp.route('/manual', methods=['POST'])
def ingest_manual():
    """Create a single intent signal for an existing account.

    JSON body:
        account_id          — int, required
        signal_description  — string, required
        signal_type         — string, optional
        evidence_value      — string, optional
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'status': 'error', 'message': 'Request body must be JSON'}), 400

    # --- Validate required fields ---
    raw_account_id = data.get('account_id')
    if raw_account_id is None:
        return jsonify({'status': 'error', 'message': 'account_id is required'}), 400

    valid, account_id_or_err = validate_positive_int(raw_account_id, name='account_id')
    if not valid:
        return jsonify({'status': 'error', 'message': account_id_or_err}), 400

    signal_description = (data.get('signal_description') or '').strip()
    if not signal_description:
        return jsonify({'status': 'error', 'message': 'signal_description is required'}), 400

    # Validate signal_description like notes (free text, max length, strip scripts)
    valid, desc_or_err = validate_notes(signal_description)
    if not valid:
        return jsonify({'status': 'error', 'message': desc_or_err}), 400
    signal_description = desc_or_err

    # Optional fields
    signal_type = (data.get('signal_type') or '').strip() or None
    evidence_value = (data.get('evidence_value') or '').strip() or None

    # --- Create signal ---
    try:
        signal_id = ingestion_service.ingest_manual(
            account_id=account_id_or_err,
            signal_description=signal_description,
            signal_type=signal_type,
            evidence_value=evidence_value,
        )
        return jsonify({'status': 'success', 'signal_id': signal_id}), 200
    except Exception as exc:
        logger.exception("[INGEST ROUTE] Manual ingestion failed")
        return jsonify({'status': 'error', 'message': f'Ingestion failed: {str(exc)[:200]}'}), 500


# ---------------------------------------------------------------------------
# POST /v2/api/ingest/from-scans
# ---------------------------------------------------------------------------

@ingestion_bp.route('/from-scans', methods=['POST'])
def ingest_from_scans():
    """Bulk-convert scan signals into intent signals.

    JSON body (optional):
        tier_filter — list of ints, e.g. [1, 2]
    """
    data = request.get_json(silent=True) or {}

    tier_filter = data.get('tier_filter')

    # Validate tier_filter if provided
    if tier_filter is not None:
        if not isinstance(tier_filter, list):
            return jsonify({'status': 'error', 'message': 'tier_filter must be a list of integers'}), 400

        validated_tiers = []
        for t in tier_filter:
            try:
                t_int = int(t)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid tier value: {t}'}), 400
            if t_int < 0 or t_int > 4:
                return jsonify({'status': 'error', 'message': f'Tier must be 0-4, got {t_int}'}), 400
            validated_tiers.append(t_int)

        tier_filter = validated_tiers if validated_tiers else None

    try:
        result = ingestion_service.batch_import_from_scans(tier_filter=tier_filter)
        return jsonify({'status': 'success', 'result': result}), 200
    except Exception as exc:
        logger.exception("[INGEST ROUTE] Scan import failed")
        return jsonify({'status': 'error', 'message': f'Scan import failed: {str(exc)[:200]}'}), 500
