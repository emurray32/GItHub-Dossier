"""
V2 Ingestion Routes — Flask blueprint for importing signals via CSV,
Excel, Word documents, or manual entry.

Blueprint name: ingestion_bp
URL prefix:     /v2/api/ingest
"""
import logging

from flask import Blueprint, request, jsonify

from validators import validate_company_name, validate_positive_int, validate_notes
from v2.services import ingestion_service

logger = logging.getLogger(__name__)

ingestion_bp = Blueprint('v2_ingestion', __name__, url_prefix='/v2/api/ingest')

_ALLOWED_EXTENSIONS = {'.csv', '.xlsx', '.docx', '.txt', '.pdf'}


# ---------------------------------------------------------------------------
# POST /v2/api/ingest/file  — unified upload (CSV + Excel + DOCX)
# ---------------------------------------------------------------------------

@ingestion_bp.route('/file', methods=['POST'])
def ingest_file():
    """Upload a CSV, Excel, or Word file to create intent signals in bulk.

    Accepts multipart/form-data with:
        file          — CSV (.csv), Excel (.xlsx), or Word (.docx) file
        source_label  — optional label for tracking
        created_by    — optional user identifier
    """
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file provided'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'status': 'error', 'message': 'No file selected'}), 400

    filename_lower = file.filename.lower()
    ext = None
    for allowed in _ALLOWED_EXTENSIONS:
        if filename_lower.endswith(allowed):
            ext = allowed
            break

    if not ext:
        return jsonify({
            'status': 'error',
            'message': f'Unsupported file type. Accepted: {", ".join(sorted(_ALLOWED_EXTENSIONS))}'
        }), 400

    try:
        file_content = file.read()
    except Exception as exc:
        logger.exception("[INGEST ROUTE] Failed to read uploaded file")
        return jsonify({'status': 'error', 'message': 'Failed to read file'}), 400

    if not file_content:
        return jsonify({'status': 'error', 'message': 'File is empty'}), 400

    # 10 MB limit
    max_size = 10 * 1024 * 1024
    if len(file_content) > max_size:
        return jsonify({'status': 'error', 'message': f'File exceeds {max_size // (1024*1024)} MB limit'}), 400

    source_label = (request.form.get('source_label') or 'file_upload').strip()
    created_by = (request.form.get('created_by') or '').strip() or None
    clear_existing = (request.form.get('clear_existing') or '').lower() in ('true', '1', 'yes')

    try:
        if ext == '.xlsx' or ext not in {'.csv', '.docx', '.txt', '.pdf'}:
            result = ingestion_service.ingest_excel(
                file_content=file_content,
                source_label=source_label,
                created_by=created_by,
                clear_existing=clear_existing,
            )
        else:
            handlers = {
                '.csv': ingestion_service.ingest_csv,
                '.docx': ingestion_service.ingest_docx,
                '.txt': ingestion_service.ingest_text,
                '.pdf': ingestion_service.ingest_pdf,
            }
            # TODO: ingest_csv, ingest_docx, ingest_text, and ingest_pdf do not yet
            # accept clear_existing — their signatures in ingestion_service.py need
            # updating to match ingest_excel. Pass it once the service functions
            # support it; for now, only ingest_excel receives clear_existing.
            result = handlers[ext](
                file_content=file_content,
                source_label=source_label,
                created_by=created_by,
            )
        return jsonify({'status': 'success', 'result': result}), 200
    except Exception as exc:
        logger.exception("[INGEST ROUTE] File ingestion failed")
        return jsonify({'status': 'error', 'message': 'Ingestion failed'}), 500


# ---------------------------------------------------------------------------
# POST /v2/api/ingest/csv  — legacy endpoint, still works
# ---------------------------------------------------------------------------

@ingestion_bp.route('/csv', methods=['POST'])
def ingest_csv():
    """Upload a CSV file to create intent signals in bulk.

    Accepts multipart/form-data with:
        file          — the CSV file (required, must end in .csv)
        source_label  — optional label for tracking (default: 'csv_upload')
        created_by    — optional user identifier
    """
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file provided'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'status': 'error', 'message': 'No file selected'}), 400

    if not file.filename.lower().endswith('.csv'):
        return jsonify({'status': 'error', 'message': 'File must be a .csv file'}), 400

    try:
        file_content = file.read()
    except Exception as exc:
        logger.exception("[INGEST ROUTE] Failed to read uploaded file")
        return jsonify({'status': 'error', 'message': 'Failed to read file'}), 400

    if not file_content:
        return jsonify({'status': 'error', 'message': 'File is empty'}), 400

    if len(file_content) > 5 * 1024 * 1024:
        return jsonify({'status': 'error', 'message': 'File exceeds 5 MB limit'}), 400

    source_label = (request.form.get('source_label') or 'csv_upload').strip()
    created_by = (request.form.get('created_by') or '').strip() or None

    try:
        result = ingestion_service.ingest_csv(
            file_content=file_content,
            source_label=source_label,
            created_by=created_by,
        )
        return jsonify({'status': 'success', 'result': result}), 200
    except Exception as exc:
        logger.exception("[INGEST ROUTE] CSV ingestion failed")
        return jsonify({'status': 'error', 'message': 'Ingestion failed'}), 500


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
        return jsonify({'status': 'error', 'message': 'Ingestion failed'}), 500
