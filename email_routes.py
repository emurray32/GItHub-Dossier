"""
Email Engine API routes — Flask Blueprint for pipeline email generation and preview.

Routes:
    POST /api/pipeline/generate-emails — Generate emails for a batch of contacts
    GET  /api/pipeline/email-preview   — Preview generated email for a contact
"""
import json
from flask import Blueprint, request, jsonify
from validators import validate_positive_int, validate_company_name
from database import (
    get_enrollment_batch, get_campaign, get_next_contacts_for_phase,
    get_signals_by_company, get_account_by_company, get_scorecard_score,
    update_enrollment_contact, get_db_connection,
)
from email_engine import generate_batch_emails, preview_email, generate_email_sequence

email_bp = Blueprint('email_engine', __name__)


@email_bp.route('/api/pipeline/generate-emails', methods=['POST'])
def api_pipeline_generate_emails():
    """Generate personalized emails for a batch of enrollment contacts.

    Request JSON:
        batch_id: int — enrollment batch to generate for
        limit: int (optional, default 50) — max contacts per call
        campaign_prompt: str (optional) — override campaign prompt

    Returns JSON with generated count and results.
    """
    data = request.get_json()
    if not data or not data.get('batch_id'):
        return jsonify({'status': 'error', 'message': 'batch_id is required'}), 400

    batch_id = data['batch_id']
    is_valid, limit = validate_positive_int(data.get('limit', 50), name='limit', max_val=200)
    if not is_valid:
        return jsonify({'status': 'error', 'message': limit}), 400

    batch = get_enrollment_batch(batch_id)
    if not batch:
        return jsonify({'status': 'error', 'message': 'Batch not found'}), 404

    # Optionally pull campaign prompt
    campaign_prompt = data.get('campaign_prompt', '')
    if not campaign_prompt:
        campaign = get_campaign(batch['campaign_id'])
        if campaign:
            campaign_prompt = campaign.get('prompt', '')

    # Get contacts that need email generation (status = discovered)
    contacts = get_next_contacts_for_phase(batch_id, 'discovered', limit=limit)
    if not contacts:
        return jsonify({'status': 'success', 'message': 'No contacts pending email generation', 'generated': 0})

    # Gather signals and account data per company
    company_names = list({c['company_name'] for c in contacts})
    signals_by_company = {}
    account_data_by_company = {}
    for cn in company_names:
        signals_by_company[cn] = get_signals_by_company(cn, limit=50)
        acct = get_account_by_company(cn)
        if acct:
            score = get_scorecard_score(acct['id']) if acct.get('id') else None
            account_data_by_company[cn] = score or {}
            account_data_by_company[cn]['evidence_summary'] = acct.get('evidence_summary', '')

    results = generate_batch_emails(
        contacts=contacts,
        signals_by_company=signals_by_company,
        campaign_prompt=campaign_prompt,
        account_data_by_company=account_data_by_company,
    )

    # Persist results
    generated = 0
    failed = 0
    for contact_id, result in results:
        if result.get('error') and not result.get('best_subject'):
            update_enrollment_contact(contact_id,
                status='failed',
                error_message=f'Email generation failed: {result["error"][:300]}')
            failed += 1
        else:
            update_enrollment_contact(contact_id,
                generated_emails_json=json.dumps(result),
                status='email_generated')
            generated += 1

    return jsonify({
        'status': 'success',
        'generated': generated,
        'failed': failed,
        'total_processed': len(results),
    })


@email_bp.route('/api/pipeline/email-preview', methods=['GET'])
def api_pipeline_email_preview():
    """Preview a generated email for a contact, or generate one on the fly.

    Query params:
        contact_id: int — enrollment contact ID (returns stored email if available)
        company_name: str — company name (generates fresh preview from signals)
        variant: str (optional) — 'A', 'B', or 'C'
        title: str (optional) — contact title for persona detection
    """
    contact_id = request.args.get('contact_id', type=int)
    variant = request.args.get('variant')

    # If contact_id provided, try to return stored email first
    if contact_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM enrollment_contacts WHERE id = ?', (contact_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return jsonify({'status': 'error', 'message': 'Contact not found'}), 404

        contact = dict(row)
        stored = contact.get('generated_emails_json')
        if stored:
            try:
                email_data = json.loads(stored)
                # If the stored data has our variant structure, return it
                if 'variants' in email_data:
                    if variant and variant in email_data.get('variants', {}):
                        v = email_data['variants'][variant]
                        return jsonify({'status': 'success', 'email': {
                            'subject': v['subject'],
                            'body': v['body'],
                            'score': v.get('score', 0),
                            'variant': variant,
                            'signal_type': email_data.get('signal_type', ''),
                            'persona': email_data.get('persona', ''),
                        }})
                    return jsonify({'status': 'success', 'email': {
                        'subject': email_data.get('best_subject', ''),
                        'body': email_data.get('best_body', ''),
                        'variant': email_data.get('best_variant', 'A'),
                        'signal_type': email_data.get('signal_type', ''),
                        'persona': email_data.get('persona', ''),
                        'all_variants': email_data.get('variants', {}),
                    }})
                # Legacy format (subject_1, email_1, etc.) — return as-is
                return jsonify({'status': 'success', 'email': email_data, 'format': 'legacy'})
            except (json.JSONDecodeError, TypeError):
                pass

        # No stored email — generate fresh preview
        signals = get_signals_by_company(contact['company_name'], limit=50)
        account = get_account_by_company(contact['company_name'])
        account_data = None
        if account:
            score = get_scorecard_score(account['id']) if account.get('id') else None
            account_data = score or {}
            account_data['evidence_summary'] = account.get('evidence_summary', '')

        result = preview_email(contact=contact, signals=signals,
                               variant=variant, account_data=account_data)
        return jsonify({'status': 'success', 'email': result, 'generated_live': True})

    # No contact_id — generate from company_name
    raw_company = request.args.get('company_name', '')
    is_valid, company_name = validate_company_name(raw_company)
    if not is_valid:
        if not raw_company or not raw_company.strip():
            return jsonify({'status': 'error', 'message': 'contact_id or company_name is required'}), 400
        return jsonify({'status': 'error', 'message': company_name}), 400

    title = request.args.get('title', '')
    signals = get_signals_by_company(company_name, limit=50)
    account = get_account_by_company(company_name)
    account_data = None
    if account:
        score = get_scorecard_score(account['id']) if account.get('id') else None
        account_data = score or {}
        account_data['evidence_summary'] = account.get('evidence_summary', '')

    mock_contact = {
        'company_name': company_name,
        'first_name': '{{first_name}}',
        'title': title,
    }
    result = preview_email(contact=mock_contact, signals=signals,
                           variant=variant, account_data=account_data)
    return jsonify({'status': 'success', 'email': result, 'generated_live': True})


@email_bp.route('/api/pipeline/email-sequence', methods=['POST'])
def api_pipeline_email_sequence():
    """Generate a full 4-email sequence for a contact or company.

    Request JSON:
        contact_id: int (optional) — enrollment contact ID
        company_name: str (optional) — company name for fresh generation
        title: str (optional) — contact title for persona detection
        campaign_id: int (optional) — campaign for instructions/links
        campaign_prompt: str (optional) — override campaign prompt
        campaign_links: list (optional) — links for hyperlinking in email body

    Returns JSON with full email sequence (all 4 emails).
    """
    data = request.get_json() or {}
    contact_id = data.get('contact_id')
    campaign_links = data.get('campaign_links')

    # Get campaign context if campaign_id provided
    campaign_prompt = data.get('campaign_prompt', '')
    campaign_id = data.get('campaign_id')
    if campaign_id and not campaign_prompt:
        campaign = get_campaign(campaign_id)
        if campaign:
            campaign_prompt = campaign.get('prompt', '')
            if not campaign_links:
                try:
                    assets = json.loads(campaign.get('assets', '{}') or '{}')
                    campaign_links = assets.get('links', [])
                except (json.JSONDecodeError, TypeError):
                    pass

    # Build contact from enrollment_contacts if ID provided
    if contact_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM enrollment_contacts WHERE id = ?', (contact_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return jsonify({'status': 'error', 'message': 'Contact not found'}), 404
        contact = dict(row)
    else:
        raw_company = data.get('company_name', '')
        is_valid, company_name = validate_company_name(raw_company)
        if not is_valid:
            return jsonify({'status': 'error', 'message': company_name or 'company_name is required'}), 400
        contact = {
            'company_name': company_name,
            'first_name': '{{first_name}}',
            'title': data.get('title', ''),
        }

    company_name = contact.get('company_name', '')
    signals = get_signals_by_company(company_name, limit=50)
    account = get_account_by_company(company_name)
    account_data = None
    if account:
        score = get_scorecard_score(account['id']) if account.get('id') else None
        account_data = score or {}
        account_data['evidence_summary'] = account.get('evidence_summary', '')

    result = generate_email_sequence(
        contact=contact,
        signals=signals,
        campaign_prompt=campaign_prompt,
        account_data=account_data,
        campaign_links=campaign_links,
    )

    return jsonify({'status': 'success', 'sequence': result})
