"""
Slack Bot Integration for GitHub Dossier (RepoRadar).

Flask Blueprint providing:
- Slash command handlers (/reporadar scan, pipeline, hot, stats)
- Interactive message callbacks (enrollment approval/reject)
- Real-time Tier 2 alerts
- Weekly digest summaries

Uses Slack Web API via requests (no SDK dependency).
All incoming requests are verified via Slack signing secret.
"""
import hashlib
import hmac
import json
import logging
import os
import time
import threading
from datetime import datetime, timedelta
from typing import Optional

import requests as http_requests
from flask import Blueprint, request, jsonify

from database import (
    get_tier_counts, get_all_accounts, get_account_by_company_case_insensitive,
    get_stats_last_n_days, log_webhook,
    get_enrollment_contacts, update_enrollment_contact,
    get_enrollment_batch, update_enrollment_batch,
    TIER_CONFIG, TIER_PREPARING,
)

slack_bot = Blueprint('slack_bot', __name__)

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _get_bot_token() -> Optional[str]:
    return os.environ.get('SLACK_BOT_TOKEN')


def _get_signing_secret() -> Optional[str]:
    return os.environ.get('SLACK_SIGNING_SECRET')


def _get_channel_id() -> Optional[str]:
    return os.environ.get('SLACK_CHANNEL_ID')


def _get_base_url() -> str:
    """Return the public base URL for report links."""
    try:
        from flask import request as _req, has_request_context
        if has_request_context():
            return _req.host_url.rstrip('/')
    except Exception:
        pass
    return os.environ.get('BASE_URL', 'http://localhost:5000')


# ---------------------------------------------------------------------------
# Slack request signature verification
# ---------------------------------------------------------------------------

def _verify_slack_signature(req) -> bool:
    """Verify that the incoming request is genuinely from Slack.

    Uses HMAC-SHA256 with the signing secret per Slack's verification spec.
    Returns False (and logs a warning) if verification fails.
    """
    signing_secret = _get_signing_secret()
    if not signing_secret:
        logging.warning('[SLACK-BOT] SLACK_SIGNING_SECRET not set, rejecting request')
        return False

    timestamp = req.headers.get('X-Slack-Request-Timestamp', '')
    signature = req.headers.get('X-Slack-Signature', '')

    if not timestamp or not signature:
        logging.warning('[SLACK-BOT] Missing Slack signature headers')
        return False

    # Reject requests older than 5 minutes to prevent replay attacks
    try:
        if abs(time.time() - int(timestamp)) > 300:
            logging.warning('[SLACK-BOT] Request timestamp too old (replay attack prevention)')
            return False
    except ValueError:
        return False

    body = req.get_data(as_text=True)
    sig_basestring = f'v0:{timestamp}:{body}'
    computed = 'v0=' + hmac.new(
        signing_secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed, signature):
        logging.warning('[SLACK-BOT] Slack signature verification failed')
        return False

    return True


# ---------------------------------------------------------------------------
# Slack Web API helpers
# ---------------------------------------------------------------------------

SLACK_API_BASE = 'https://slack.com/api'


def _slack_api(method: str, payload: dict) -> dict:
    """Call a Slack Web API method. Returns the parsed JSON response."""
    token = _get_bot_token()
    if not token:
        logging.error('[SLACK-BOT] SLACK_BOT_TOKEN not configured')
        return {'ok': False, 'error': 'not_configured'}

    resp = http_requests.post(
        f'{SLACK_API_BASE}/{method}',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; charset=utf-8',
        },
        json=payload,
        timeout=10,
    )
    data = resp.json()
    if not data.get('ok'):
        logging.error(f'[SLACK-BOT] API {method} failed: {data.get("error")}')
    return data


def _post_message(channel: str, text: str, blocks: list = None,
                  thread_ts: str = None) -> dict:
    """Post a message to a Slack channel."""
    payload = {'channel': channel, 'text': text}
    if blocks:
        payload['blocks'] = blocks
    if thread_ts:
        payload['thread_ts'] = thread_ts
    return _slack_api('chat.postMessage', payload)


def _update_message(channel: str, ts: str, text: str,
                    blocks: list = None) -> dict:
    """Update an existing Slack message."""
    payload = {'channel': channel, 'ts': ts, 'text': text}
    if blocks:
        payload['blocks'] = blocks
    return _slack_api('chat.update', payload)


# ---------------------------------------------------------------------------
# Routes — Slash commands
# ---------------------------------------------------------------------------

@slack_bot.route('/slack/commands', methods=['POST'])
def slack_commands():
    """Handle incoming /reporadar slash commands from Slack."""
    if not _verify_slack_signature(request):
        return jsonify({'error': 'invalid signature'}), 401

    command_text = (request.form.get('text') or '').strip()
    response_url = request.form.get('response_url', '')

    # Parse the sub-command
    parts = command_text.split(None, 1)
    sub_command = parts[0].lower() if parts else 'help'
    args = parts[1].strip() if len(parts) > 1 else ''

    handlers = {
        'scan': _cmd_scan,
        'pipeline': _cmd_pipeline,
        'hot': _cmd_hot,
        'stats': _cmd_stats,
        'help': _cmd_help,
    }

    handler = handlers.get(sub_command, _cmd_help)

    # Slash commands must respond within 3 seconds.
    # For scan (which is async), we ack immediately and post results later.
    if sub_command == 'scan':
        # Acknowledge immediately, process in background
        threading.Thread(
            target=_cmd_scan_async,
            args=(args, response_url),
            daemon=True,
        ).start()
        return jsonify({
            'response_type': 'ephemeral',
            'text': f'Queuing scan for *{args}*... Results will appear shortly.',
        })

    # Synchronous commands — return response directly
    result = handler(args)
    return jsonify(result)


def _cmd_help(_args: str = '') -> dict:
    """Return help text for /reporadar."""
    return {
        'response_type': 'ephemeral',
        'text': (
            '*RepoRadar Slash Commands*\n'
            '`/reporadar scan <company>` - Trigger a scan for a company\n'
            '`/reporadar pipeline` - Show pipeline status by tier\n'
            '`/reporadar hot` - List all Tier 2 (Goldilocks Zone) hot leads\n'
            '`/reporadar stats` - Weekly stats summary\n'
            '`/reporadar help` - Show this help message'
        ),
    }


def _cmd_scan(_args: str) -> dict:
    """Placeholder — scan is handled asynchronously via _cmd_scan_async."""
    return {'response_type': 'ephemeral', 'text': 'Scan queued.'}


def _cmd_scan_async(company_name: str, response_url: str):
    """Trigger a scan for a company (runs in background thread)."""
    if not company_name:
        _respond_to_slack(response_url, {
            'response_type': 'ephemeral',
            'text': 'Usage: `/reporadar scan <company name>`',
        })
        return

    # Import here to avoid circular imports (app.py -> slack_bot -> app.py)
    from app import spawn_background_scan, add_account_to_tier_0

    # Check if account already exists
    account = get_account_by_company_case_insensitive(company_name)

    if account:
        # Existing account — queue a rescan
        spawn_background_scan(account['company_name'])
        _respond_to_slack(response_url, {
            'response_type': 'in_channel',
            'text': (
                f'Rescan queued for *{account["company_name"]}* '
                f'(currently Tier {account.get("current_tier", "?")} - '
                f'{TIER_CONFIG.get(account.get("current_tier", 0), {}).get("name", "Unknown")})'
            ),
        })
    else:
        # New account — add to Tier 0 and scan
        add_account_to_tier_0(company_name, '')
        spawn_background_scan(company_name)
        _respond_to_slack(response_url, {
            'response_type': 'in_channel',
            'text': f'New account *{company_name}* added and scan queued.',
        })


def _cmd_pipeline(_args: str = '') -> dict:
    """Show pipeline status: account counts per tier and pending enrollments."""
    tier_counts = get_tier_counts()
    total = sum(tier_counts.values())

    lines = ['*Pipeline Status*\n']
    for tier_num in range(5):
        config = TIER_CONFIG.get(tier_num, {})
        name = config.get('name', f'Tier {tier_num}')
        status = config.get('status', '')
        count = tier_counts.get(str(tier_num), 0)
        bar = _progress_bar(count, total)
        lines.append(f'  *Tier {tier_num} — {name}* ({status}): {count}  {bar}')

    lines.append(f'\n*Total accounts:* {total}')

    return {
        'response_type': 'in_channel',
        'text': '\n'.join(lines),
    }


def _cmd_hot(_args: str = '') -> dict:
    """List all Tier 2 (Goldilocks/Preparing) accounts with signal summaries."""
    result = get_all_accounts(page=1, limit=25, tier_filter=[2])
    accounts = result.get('accounts', [])
    total = result.get('total_items', 0)

    if not accounts:
        return {
            'response_type': 'in_channel',
            'text': 'No Tier 2 (Goldilocks Zone) accounts found.',
        }

    base_url = _get_base_url()
    lines = [f'*Hot Leads (Tier 2 - Goldilocks Zone)* — {total} accounts\n']

    for acct in accounts[:15]:  # Show top 15 in Slack
        name = acct.get('company_name', 'Unknown')
        evidence = acct.get('evidence_summary', '') or acct.get('tier_change_reason', '')
        # Truncate long evidence
        if len(evidence) > 120:
            evidence = evidence[:117] + '...'
        revenue = acct.get('annual_revenue', '')
        revenue_str = f' | {revenue}' if revenue else ''

        report_link = ''
        report_id = acct.get('latest_report_id')
        if report_id:
            report_link = f' <{base_url}/report/{report_id}|View Report>'

        lines.append(f'  *{name}*{revenue_str}{report_link}')
        if evidence:
            lines.append(f'    _{evidence}_')

    if total > 15:
        lines.append(f'\n_...and {total - 15} more. View all at {base_url}_')

    return {
        'response_type': 'in_channel',
        'text': '\n'.join(lines),
    }


def _cmd_stats(_args: str = '') -> dict:
    """Weekly stats: scans run, webhooks fired, API calls."""
    stats = get_stats_last_n_days(7)

    totals = {'scans_run': 0, 'api_calls_estimated': 0, 'webhooks_fired': 0}
    for day in stats:
        totals['scans_run'] += day.get('scans_run', 0)
        totals['api_calls_estimated'] += day.get('api_calls_estimated', 0)
        totals['webhooks_fired'] += day.get('webhooks_fired', 0)

    tier_counts = get_tier_counts()

    lines = [
        '*Weekly Stats (last 7 days)*\n',
        f'  Scans run: *{totals["scans_run"]}*',
        f'  API calls: *{totals["api_calls_estimated"]}*',
        f'  Webhooks fired: *{totals["webhooks_fired"]}*',
        '',
        '*Current Pipeline*',
        f'  Tier 0 (Tracking): {tier_counts.get("0", 0)}',
        f'  Tier 1 (Thinking): {tier_counts.get("1", 0)}',
        f'  Tier 2 (Preparing): {tier_counts.get("2", 0)}',
        f'  Tier 3 (Launched): {tier_counts.get("3", 0)}',
        f'  Tier 4 (Invalid): {tier_counts.get("4", 0)}',
    ]

    return {
        'response_type': 'in_channel',
        'text': '\n'.join(lines),
    }


# ---------------------------------------------------------------------------
# Routes — Interactive message callbacks
# ---------------------------------------------------------------------------

@slack_bot.route('/slack/interactions', methods=['POST'])
def slack_interactions():
    """Handle interactive component callbacks (button clicks, etc.)."""
    if not _verify_slack_signature(request):
        return jsonify({'error': 'invalid signature'}), 401

    # Slack sends interaction payloads as a form-encoded 'payload' field
    raw_payload = request.form.get('payload', '{}')
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return jsonify({'error': 'invalid payload'}), 400

    action_type = payload.get('type')

    if action_type == 'block_actions':
        return _handle_block_actions(payload)

    return jsonify({'text': 'Unhandled interaction type'}), 200


def _handle_block_actions(payload: dict):
    """Route block_actions to the appropriate handler based on action_id."""
    actions = payload.get('actions', [])
    if not actions:
        return '', 200

    action = actions[0]
    action_id = action.get('action_id', '')

    if action_id.startswith('enrollment_approve_'):
        return _handle_enrollment_decision(payload, action, approved=True)
    elif action_id.startswith('enrollment_reject_'):
        return _handle_enrollment_decision(payload, action, approved=False)

    # Unknown action — acknowledge silently
    return '', 200


def _handle_enrollment_decision(payload: dict, action: dict, approved: bool):
    """Handle approve/reject button click for enrollment contacts."""
    action_id = action.get('action_id', '')
    # action_id format: enrollment_approve_<contact_id> or enrollment_reject_<contact_id>
    try:
        contact_id = int(action_id.split('_')[-1])
    except (ValueError, IndexError):
        return jsonify({'text': 'Invalid contact ID'}), 400

    user = payload.get('user', {})
    user_name = user.get('username', user.get('name', 'unknown'))
    channel = payload.get('channel', {}).get('id', '')
    message_ts = payload.get('message', {}).get('ts', '')

    if approved:
        # Mark contact as approved — the enrollment pipeline will pick it up
        update_enrollment_contact(contact_id, status='approved')
        status_text = f'Approved by @{user_name}'
        status_emoji = 'white_check_mark'
        logging.info(f'[SLACK-BOT] Enrollment contact {contact_id} approved by {user_name}')
    else:
        # Mark as skipped
        update_enrollment_contact(contact_id, status='skipped',
                                  error_message=f'Rejected by {user_name} via Slack')
        status_text = f'Rejected by @{user_name}'
        status_emoji = 'x'
        logging.info(f'[SLACK-BOT] Enrollment contact {contact_id} rejected by {user_name}')

    # Update the original message to show the decision (remove buttons)
    original_blocks = payload.get('message', {}).get('blocks', [])
    updated_blocks = _replace_actions_with_status(original_blocks, status_text, status_emoji)

    if channel and message_ts:
        _update_message(channel, message_ts,
                        text=f'Enrollment decision: {status_text}',
                        blocks=updated_blocks)

    try:
        log_webhook(f'enrollment_{"approve" if approved else "reject"}',
                    f'contact_{contact_id}', 'success')
    except Exception:
        pass

    return '', 200


def _replace_actions_with_status(blocks: list, status_text: str,
                                 status_emoji: str) -> list:
    """Replace action blocks with a status context block."""
    updated = []
    for block in blocks:
        if block.get('type') == 'actions':
            updated.append({
                'type': 'context',
                'elements': [{
                    'type': 'mrkdwn',
                    'text': f':{status_emoji}: *{status_text}* at {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}',
                }],
            })
        else:
            updated.append(block)
    return updated


# ---------------------------------------------------------------------------
# Proactive notifications — Tier 2 alert
# ---------------------------------------------------------------------------

def send_tier2_alert(account: dict) -> bool:
    """Send an immediate rich notification when an account moves to Tier 2.

    Args:
        account: Dictionary with company data (company_name, evidence_summary,
                 github_org, annual_revenue, latest_report_id, etc.)

    Returns:
        True if the message was sent successfully, False otherwise.
    """
    channel = _get_channel_id()
    if not channel:
        logging.warning('[SLACK-BOT] SLACK_CHANNEL_ID not set, skipping Tier 2 alert')
        return False

    base_url = _get_base_url()
    company = account.get('company_name', account.get('company', 'Unknown'))
    evidence = account.get('evidence_summary', account.get('evidence', ''))
    github_org = account.get('github_org', '')
    revenue = account.get('annual_revenue', account.get('revenue', ''))
    report_id = account.get('latest_report_id', account.get('report_id'))

    blocks = [
        {
            'type': 'header',
            'text': {'type': 'plain_text', 'text': 'New Goldilocks Lead Detected'},
        },
        {'type': 'divider'},
        {
            'type': 'section',
            'fields': [
                {'type': 'mrkdwn', 'text': f'*Company:*\n{company}'},
                {'type': 'mrkdwn', 'text': f'*Revenue:*\n{revenue or "N/A"}'},
            ],
        },
    ]

    if github_org:
        blocks.append({
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': f'*GitHub:* <https://github.com/{github_org}|{github_org}>',
            },
        })

    if evidence:
        blocks.append({
            'type': 'section',
            'text': {'type': 'mrkdwn', 'text': f'*Key Signals:*\n_{evidence}_'},
        })

    # Action buttons
    action_elements = []
    if report_id:
        action_elements.append({
            'type': 'button',
            'text': {'type': 'plain_text', 'text': 'View Full Report'},
            'url': f'{base_url}/report/{report_id}',
            'style': 'primary',
        })
    action_elements.append({
        'type': 'button',
        'text': {'type': 'plain_text', 'text': 'Start Enrollment'},
        'url': f'{base_url}/#scorecard',
        'style': 'danger',
    })

    if action_elements:
        blocks.append({'type': 'actions', 'elements': action_elements})

    blocks.append({
        'type': 'context',
        'elements': [{
            'type': 'mrkdwn',
            'text': f'RepoRadar Alert | {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}',
        }],
    })

    result = _post_message(
        channel,
        text=f'New Goldilocks Lead: {company}',
        blocks=blocks,
    )
    return result.get('ok', False)


# ---------------------------------------------------------------------------
# Enrollment approval request
# ---------------------------------------------------------------------------

def send_enrollment_approval(contact: dict, batch_info: dict = None) -> bool:
    """Send an enrollment approval request to Slack with Approve/Reject buttons.

    Args:
        contact: enrollment_contacts row dict (id, company_name, first_name,
                 last_name, email, title, persona_name, etc.)
        batch_info: Optional enrollment_batches row dict for extra context.

    Returns:
        True if message posted successfully.
    """
    channel = _get_channel_id()
    if not channel:
        logging.warning('[SLACK-BOT] SLACK_CHANNEL_ID not set, skipping enrollment approval')
        return False

    contact_id = contact.get('id')
    name = f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip() or 'Unknown'
    email = contact.get('email', 'N/A')
    title = contact.get('title', 'N/A')
    company = contact.get('company_name', 'Unknown')
    persona = contact.get('persona_name', '')
    sequence = contact.get('sequence_name', '')

    blocks = [
        {
            'type': 'header',
            'text': {'type': 'plain_text', 'text': 'Enrollment Approval Required'},
        },
        {'type': 'divider'},
        {
            'type': 'section',
            'fields': [
                {'type': 'mrkdwn', 'text': f'*Contact:*\n{name}'},
                {'type': 'mrkdwn', 'text': f'*Company:*\n{company}'},
                {'type': 'mrkdwn', 'text': f'*Title:*\n{title}'},
                {'type': 'mrkdwn', 'text': f'*Email:*\n{email}'},
            ],
        },
    ]

    if persona or sequence:
        detail_fields = []
        if persona:
            detail_fields.append({'type': 'mrkdwn', 'text': f'*Persona:*\n{persona}'})
        if sequence:
            detail_fields.append({'type': 'mrkdwn', 'text': f'*Sequence:*\n{sequence}'})
        blocks.append({'type': 'section', 'fields': detail_fields})

    linkedin = contact.get('linkedin_url')
    if linkedin:
        blocks.append({
            'type': 'section',
            'text': {'type': 'mrkdwn', 'text': f'*LinkedIn:* <{linkedin}|Profile>'},
        })

    # Approve / Reject buttons
    blocks.append({
        'type': 'actions',
        'elements': [
            {
                'type': 'button',
                'text': {'type': 'plain_text', 'text': 'Approve'},
                'style': 'primary',
                'action_id': f'enrollment_approve_{contact_id}',
                'value': str(contact_id),
            },
            {
                'type': 'button',
                'text': {'type': 'plain_text', 'text': 'Reject'},
                'style': 'danger',
                'action_id': f'enrollment_reject_{contact_id}',
                'value': str(contact_id),
            },
        ],
    })

    blocks.append({
        'type': 'context',
        'elements': [{
            'type': 'mrkdwn',
            'text': f'RepoRadar Enrollment | {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}',
        }],
    })

    result = _post_message(
        channel,
        text=f'Enrollment approval needed: {name} at {company}',
        blocks=blocks,
    )
    return result.get('ok', False)


# ---------------------------------------------------------------------------
# Weekly digest
# ---------------------------------------------------------------------------

def send_weekly_digest() -> bool:
    """Send a weekly digest summarizing pipeline activity.

    Includes: scan stats, tier distribution, tier changes, enrollment counts.

    Returns:
        True if the message was sent successfully.
    """
    channel = _get_channel_id()
    if not channel:
        logging.warning('[SLACK-BOT] SLACK_CHANNEL_ID not set, skipping weekly digest')
        return False

    # Gather stats for the last 7 days
    stats = get_stats_last_n_days(7)
    totals = {'scans_run': 0, 'api_calls_estimated': 0, 'webhooks_fired': 0}
    for day in stats:
        totals['scans_run'] += day.get('scans_run', 0)
        totals['api_calls_estimated'] += day.get('api_calls_estimated', 0)
        totals['webhooks_fired'] += day.get('webhooks_fired', 0)

    tier_counts = get_tier_counts()
    total_accounts = sum(tier_counts.values())

    blocks = [
        {
            'type': 'header',
            'text': {'type': 'plain_text', 'text': 'RepoRadar Weekly Digest'},
        },
        {'type': 'divider'},
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': (
                    f'*Activity (last 7 days)*\n'
                    f'  Scans completed: *{totals["scans_run"]}*\n'
                    f'  API calls: *{totals["api_calls_estimated"]}*\n'
                    f'  Webhooks fired: *{totals["webhooks_fired"]}*'
                ),
            },
        },
        {'type': 'divider'},
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': (
                    f'*Pipeline Overview* ({total_accounts} total)\n'
                    f'  Tier 0 — Tracking: *{tier_counts.get("0", 0)}*\n'
                    f'  Tier 1 — Thinking (Warm): *{tier_counts.get("1", 0)}*\n'
                    f'  Tier 2 — Preparing (Hot): *{tier_counts.get("2", 0)}*\n'
                    f'  Tier 3 — Launched: *{tier_counts.get("3", 0)}*\n'
                    f'  Tier 4 — Invalid: *{tier_counts.get("4", 0)}*'
                ),
            },
        },
    ]

    # Highlight hot leads count
    hot_count = tier_counts.get('2', 0)
    if hot_count > 0:
        blocks.append({
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': f'*{hot_count} accounts in the Goldilocks Zone* — run `/reporadar hot` for details.',
            },
        })

    base_url = _get_base_url()
    blocks.append({
        'type': 'actions',
        'elements': [{
            'type': 'button',
            'text': {'type': 'plain_text', 'text': 'Open Dashboard'},
            'url': base_url,
        }],
    })

    blocks.append({
        'type': 'context',
        'elements': [{
            'type': 'mrkdwn',
            'text': f'RepoRadar Weekly Digest | {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}',
        }],
    })

    result = _post_message(
        channel,
        text=f'RepoRadar Weekly Digest — {hot_count} hot leads, {totals["scans_run"]} scans this week',
        blocks=blocks,
    )
    return result.get('ok', False)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _respond_to_slack(response_url: str, payload: dict):
    """Send a delayed response to Slack via the response_url."""
    if not response_url:
        return
    try:
        http_requests.post(
            response_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=10,
        )
    except Exception as e:
        logging.error(f'[SLACK-BOT] Failed to send delayed response: {e}')


def _progress_bar(value: int, total: int, width: int = 10) -> str:
    """Render a simple text progress bar for Slack."""
    if total <= 0:
        return ''
    filled = round(width * value / total)
    filled = min(filled, width)
    return '`' + '#' * filled + '-' * (width - filled) + '`'


def is_slack_bot_configured() -> bool:
    """Check if Slack bot integration is fully configured."""
    return bool(_get_bot_token() and _get_signing_secret() and _get_channel_id())
