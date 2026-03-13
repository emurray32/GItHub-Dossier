"""
V2 Webhook Routes — external system callbacks for automated workflow transitions.

Blueprint: webhooks_bp, prefix /v2/api/webhooks
"""
import logging
import os

from flask import Blueprint, request, jsonify

from validators import validate_apollo_id, validate_email

logger = logging.getLogger(__name__)

webhooks_bp = Blueprint('v2_webhooks', __name__, url_prefix='/v2/api/webhooks')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error(message, status_code=400):
    return jsonify({'status': 'error', 'message': message}), status_code


def _success(**kwargs):
    return jsonify({'status': 'success', **kwargs})


def _check_webhook_auth():
    """Validate bearer token from APOLLO_WEBHOOK_SECRET env var.

    Returns None on success, or a Flask response tuple on failure.
    """
    secret = os.environ.get('APOLLO_WEBHOOK_SECRET')
    if not secret:
        logger.error("[WEBHOOK] APOLLO_WEBHOOK_SECRET not configured")
        return _error('Webhook not configured', 503)

    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return _error('Unauthorized', 401)

    token = auth_header[7:].strip()
    if token != secret:
        return _error('Unauthorized', 401)

    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@webhooks_bp.route('/apollo-sequence-complete', methods=['POST'])
def apollo_sequence_complete():
    """Handle Apollo sequence completion webhook.

    Called when an Apollo email sequence finishes for a contact.
    Looks up the prospect, marks their sequence complete, and checks
    whether the account should transition to 'revisit' status.

    Body: {
        apollo_contact_id?: str,   # preferred lookup key
        email?: str,               # fallback lookup key
        sequence_id?: str,         # optional context
        completed_at?: str         # optional timestamp
    }

    Auth: Bearer token via APOLLO_WEBHOOK_SECRET env var.
    """
    # Auth check
    auth_err = _check_webhook_auth()
    if auth_err:
        return auth_err

    try:
        data = request.get_json()
        if not data:
            return _error('Request body is required')

        apollo_contact_id = data.get('apollo_contact_id')
        email = data.get('email')

        if not apollo_contact_id and not email:
            return _error('Either apollo_contact_id or email is required')

        # Validate inputs
        if apollo_contact_id:
            valid, apollo_contact_id = validate_apollo_id(str(apollo_contact_id))
            if not valid:
                return _error(apollo_contact_id)

        if email:
            valid, email = validate_email(str(email))
            if not valid:
                return _error(email)

        # Look up the prospect
        prospect = _find_prospect(apollo_contact_id, email)
        if not prospect:
            return _error('Prospect not found', 404)

        prospect_id = prospect['id']
        account_id = prospect.get('account_id')

        # Idempotency: already complete
        if prospect.get('enrollment_status') == 'sequence_complete':
            return _success(
                prospect_id=prospect_id,
                already_complete=True,
                message='Sequence already marked complete',
            )

        # Mark sequence complete (handles prospect status + account rollup)
        from v2.services.enrollment_service import mark_sequence_complete
        result = mark_sequence_complete(prospect_id)
        if not result:
            return _error('Failed to mark sequence complete', 500)

        account_moved_to_revisit = result.get('account_moved_to_revisit', False)

        # Auto-create revisit signal when account transitions
        revisit_signal_id = None
        if account_moved_to_revisit and account_id:
            revisit_signal_id = _create_revisit_signal(account_id, prospect)

        # Log webhook activity
        try:
            from v2.services.activity_service import log_activity
            log_activity(
                event_type='webhook_sequence_completed',
                entity_type='prospect',
                entity_id=prospect_id,
                details={
                    'account_id': account_id,
                    'apollo_contact_id': apollo_contact_id,
                    'sequence_id': data.get('sequence_id'),
                    'completed_at': data.get('completed_at'),
                    'account_moved_to_revisit': account_moved_to_revisit,
                    'revisit_signal_id': revisit_signal_id,
                },
                created_by='apollo_webhook',
            )
        except Exception:
            logger.debug("[WEBHOOK] Could not log webhook activity")

        return _success(
            prospect_id=prospect_id,
            account_id=account_id,
            enrollment_status='sequence_complete',
            account_moved_to_revisit=account_moved_to_revisit,
            revisit_signal_id=revisit_signal_id,
        )

    except Exception as e:
        logger.exception("[WEBHOOK] Error processing apollo-sequence-complete")
        return _error('Internal server error', 500)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_prospect(apollo_contact_id=None, email=None):
    """Find a prospect by apollo_contact_id (preferred) or email.

    When looking up by email, prefers prospects with enrollment_status='enrolled'
    since those are most likely the ones whose sequence just completed.
    """
    from v2.db import db_connection, row_to_dict

    with db_connection() as conn:
        cursor = conn.cursor()

        # Try apollo_contact_id first (exact match)
        if apollo_contact_id:
            cursor.execute(
                'SELECT * FROM prospects WHERE apollo_contact_id = ? LIMIT 1',
                (apollo_contact_id,),
            )
            row = cursor.fetchone()
            if row:
                return row_to_dict(row)

        # Fall back to email — prefer enrolled prospects
        if email:
            cursor.execute('''
                SELECT * FROM prospects WHERE LOWER(email) = LOWER(?)
                ORDER BY CASE WHEN enrollment_status = 'enrolled' THEN 0 ELSE 1 END,
                         created_at DESC
                LIMIT 1
            ''', (email,))
            row = cursor.fetchone()
            if row:
                return row_to_dict(row)

    return None


def _create_revisit_signal(account_id, prospect):
    """Create a revisit intent signal when an account transitions to revisit."""
    try:
        from v2.services.signal_service import create_signal
        from v2.services.activity_service import log_activity

        company_name = prospect.get('company_name', 'Unknown')
        signal_id = create_signal(
            account_id=account_id,
            signal_description=f'All sequences completed for {company_name} — ready for revisit outreach',
            signal_type='revisit',
            evidence_type='webhook',
            evidence_value=f'Auto-created when all prospect sequences completed',
            signal_source='webhook',
            created_by='apollo_webhook',
        )

        log_activity(
            event_type='revisit_signal_created',
            entity_type='account',
            entity_id=account_id,
            details={'signal_id': signal_id, 'trigger': 'apollo_sequence_complete_webhook'},
            created_by='apollo_webhook',
        )

        logger.info("[WEBHOOK] Created revisit signal %d for account %d", signal_id, account_id)
        return signal_id

    except Exception:
        logger.exception("[WEBHOOK] Failed to create revisit signal for account %d", account_id)
        return None
