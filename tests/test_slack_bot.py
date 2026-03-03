"""Tests for slack_bot.py — Slack Bot integration routes and helpers."""
import hashlib
import hmac
import json
import time

import pytest
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.unit

SIGNING_SECRET = 'test_slack_signing_secret_abc123'
BOT_TOKEN = 'xoxb-test-bot-token'
CHANNEL_ID = 'C1234567890'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_slack_signature(body: str, timestamp: str, secret: str = SIGNING_SECRET) -> str:
    """Compute the v0= HMAC-SHA256 signature Slack sends on every request."""
    sig_basestring = f'v0:{timestamp}:{body}'
    return 'v0=' + hmac.new(
        secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()


def _slack_env():
    """Return env dict that makes the bot fully configured."""
    return {
        'SLACK_SIGNING_SECRET': SIGNING_SECRET,
        'SLACK_BOT_TOKEN': BOT_TOKEN,
        'SLACK_CHANNEL_ID': CHANNEL_ID,
    }


def _post_command(client, text='help', response_url='https://hooks.slack.com/resp',
                  extra_form=None, env_override=None, skip_sig=True):
    """POST to /slack/commands with a valid Slack signature.

    By default, signature verification is bypassed (skip_sig=True) so tests
    can focus on handler logic.  Set skip_sig=False when explicitly testing
    signature verification.
    """
    form_data = {
        'text': text,
        'response_url': response_url,
        'user_id': 'U12345',
        'user_name': 'testuser',
        'channel_id': CHANNEL_ID,
        'command': '/reporadar',
    }
    if extra_form:
        form_data.update(extra_form)

    env = env_override or _slack_env()

    if skip_sig:
        with patch.dict('os.environ', env, clear=False), \
             patch('slack_bot._verify_slack_signature', return_value=True):
            return client.post('/slack/commands', data=form_data)
    else:
        # Compute signature over the raw body the Flask test client will send
        from urllib.parse import urlencode
        body = urlencode(form_data)
        timestamp = str(int(time.time()))
        signature = _make_slack_signature(body, timestamp)

        with patch.dict('os.environ', env, clear=False):
            return client.post(
                '/slack/commands',
                data=form_data,
                headers={
                    'X-Slack-Request-Timestamp': timestamp,
                    'X-Slack-Signature': signature,
                },
            )


def _post_interaction(client, payload_dict, env_override=None, skip_sig=True):
    """POST to /slack/interactions with a valid Slack signature.

    By default, signature verification is bypassed (skip_sig=True) so tests
    can focus on handler logic.  Set skip_sig=False when explicitly testing
    signature verification.
    """
    payload_str = json.dumps(payload_dict)
    env = env_override or _slack_env()

    if skip_sig:
        with patch.dict('os.environ', env, clear=False), \
             patch('slack_bot._verify_slack_signature', return_value=True):
            return client.post(
                '/slack/interactions',
                data={'payload': payload_str},
            )
    else:
        from urllib.parse import urlencode
        body = urlencode({'payload': payload_str})
        timestamp = str(int(time.time()))
        signature = _make_slack_signature(body, timestamp)

        with patch.dict('os.environ', env, clear=False):
            return client.post(
                '/slack/interactions',
                data={'payload': payload_str},
                headers={
                    'X-Slack-Request-Timestamp': timestamp,
                    'X-Slack-Signature': signature,
                },
            )


# ===========================================================================
# Signature Verification
# ===========================================================================

class TestSlackSignatureVerification:
    """Verify that _verify_slack_signature correctly accepts/rejects requests."""

    def test_valid_signature_accepted(self, flask_app):
        """A properly signed request returns 200, not 401."""
        from urllib.parse import urlencode
        form_data = {'text': 'stats'}
        body = urlencode(form_data)
        timestamp = str(int(time.time()))
        signature = _make_slack_signature(body, timestamp)

        env = _slack_env()
        with patch.dict('os.environ', env, clear=False), \
             patch('slack_bot.get_tier_counts', return_value={}), \
             patch('slack_bot.get_stats_last_n_days', return_value=[]):
            resp = flask_app.post(
                '/slack/commands',
                data=form_data,
                headers={
                    'X-Slack-Request-Timestamp': timestamp,
                    'X-Slack-Signature': signature,
                },
            )
        assert resp.status_code == 200

    def test_invalid_signature_rejected(self, flask_app):
        """A request with a tampered signature returns 401."""
        timestamp = str(int(time.time()))
        env = _slack_env()
        with patch.dict('os.environ', env, clear=False):
            resp = flask_app.post(
                '/slack/commands',
                data={'text': 'help'},
                headers={
                    'X-Slack-Request-Timestamp': timestamp,
                    'X-Slack-Signature': 'v0=badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbad',
                },
            )
        assert resp.status_code == 401
        assert resp.get_json()['error'] == 'invalid signature'

    def test_missing_signature_headers_rejected(self, flask_app):
        """A request with no signature headers returns 401."""
        env = _slack_env()
        with patch.dict('os.environ', env, clear=False):
            resp = flask_app.post('/slack/commands', data={'text': 'help'})
        assert resp.status_code == 401

    def test_missing_signing_secret_rejects(self, flask_app):
        """If SLACK_SIGNING_SECRET is not set, all requests are rejected."""
        timestamp = str(int(time.time()))
        body = 'text=help'
        signature = _make_slack_signature(body, timestamp)

        env = {'SLACK_BOT_TOKEN': BOT_TOKEN, 'SLACK_CHANNEL_ID': CHANNEL_ID}
        with patch.dict('os.environ', env, clear=False), \
             patch('os.environ.get') as mock_get:
            # Make SLACK_SIGNING_SECRET return None
            def side_effect(key, default=None):
                if key == 'SLACK_SIGNING_SECRET':
                    return None
                return env.get(key, default)
            mock_get.side_effect = side_effect

            resp = flask_app.post(
                '/slack/commands',
                data={'text': 'help'},
                headers={
                    'X-Slack-Request-Timestamp': timestamp,
                    'X-Slack-Signature': signature,
                },
            )
        assert resp.status_code == 401

    def test_expired_timestamp_rejected(self, flask_app):
        """A request older than 5 minutes is rejected (replay attack prevention)."""
        old_timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        body = 'text=help'
        signature = _make_slack_signature(body, old_timestamp)

        env = _slack_env()
        with patch.dict('os.environ', env, clear=False):
            resp = flask_app.post(
                '/slack/commands',
                data={'text': 'help'},
                headers={
                    'X-Slack-Request-Timestamp': old_timestamp,
                    'X-Slack-Signature': signature,
                },
            )
        assert resp.status_code == 401

    def test_non_numeric_timestamp_rejected(self, flask_app):
        """A non-numeric timestamp is rejected."""
        body = 'text=help'
        timestamp = 'not-a-number'
        signature = _make_slack_signature(body, timestamp)

        env = _slack_env()
        with patch.dict('os.environ', env, clear=False):
            resp = flask_app.post(
                '/slack/commands',
                data={'text': 'help'},
                headers={
                    'X-Slack-Request-Timestamp': timestamp,
                    'X-Slack-Signature': signature,
                },
            )
        assert resp.status_code == 401


# ===========================================================================
# Slash Command Handling
# ===========================================================================

class TestSlashCommandHelp:
    """Test /reporadar help (and default when no sub-command given)."""

    def test_help_command(self, flask_app):
        resp = _post_command(flask_app, text='help')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['response_type'] == 'ephemeral'
        assert '/reporadar scan' in data['text']

    def test_empty_text_defaults_to_help(self, flask_app):
        resp = _post_command(flask_app, text='')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['response_type'] == 'ephemeral'
        assert 'RepoRadar Slash Commands' in data['text']

    def test_unknown_subcommand_returns_help(self, flask_app):
        resp = _post_command(flask_app, text='foobar')
        data = resp.get_json()
        assert resp.status_code == 200
        assert 'RepoRadar Slash Commands' in data['text']


class TestSlashCommandPipeline:
    """Test /reporadar pipeline."""

    def test_pipeline_returns_tier_counts(self, flask_app):
        mock_counts = {'0': 10, '1': 5, '2': 3, '3': 1, '4': 2}
        with patch('slack_bot.get_tier_counts', return_value=mock_counts):
            resp = _post_command(flask_app, text='pipeline')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['response_type'] == 'in_channel'
        assert 'Pipeline Status' in data['text']
        # Total accounts = 21
        assert '21' in data['text']

    def test_pipeline_empty_tiers(self, flask_app):
        with patch('slack_bot.get_tier_counts', return_value={}):
            resp = _post_command(flask_app, text='pipeline')
        data = resp.get_json()
        assert resp.status_code == 200
        assert 'Total accounts' in data['text']


class TestSlashCommandHot:
    """Test /reporadar hot."""

    def test_hot_with_accounts(self, flask_app):
        mock_result = {
            'accounts': [
                {
                    'company_name': 'AcmeCorp',
                    'evidence_summary': 'Found react-i18next in webapp',
                    'annual_revenue': '$10M',
                    'latest_report_id': 42,
                },
            ],
            'total_items': 1,
        }
        with patch('slack_bot.get_all_accounts', return_value=mock_result):
            resp = _post_command(flask_app, text='hot')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['response_type'] == 'in_channel'
        assert 'AcmeCorp' in data['text']
        assert '$10M' in data['text']

    def test_hot_no_accounts(self, flask_app):
        mock_result = {'accounts': [], 'total_items': 0}
        with patch('slack_bot.get_all_accounts', return_value=mock_result):
            resp = _post_command(flask_app, text='hot')
        data = resp.get_json()
        assert resp.status_code == 200
        assert 'No Tier 2' in data['text']

    def test_hot_truncates_long_evidence(self, flask_app):
        long_evidence = 'A' * 200
        mock_result = {
            'accounts': [
                {
                    'company_name': 'LongCorp',
                    'evidence_summary': long_evidence,
                    'annual_revenue': '',
                    'latest_report_id': None,
                },
            ],
            'total_items': 1,
        }
        with patch('slack_bot.get_all_accounts', return_value=mock_result):
            resp = _post_command(flask_app, text='hot')
        data = resp.get_json()
        # Evidence should be truncated to 120 chars (117 + '...')
        assert '...' in data['text']

    def test_hot_more_than_fifteen(self, flask_app):
        accounts = [
            {'company_name': f'Corp{i}', 'evidence_summary': '', 'annual_revenue': '', 'latest_report_id': None}
            for i in range(20)
        ]
        mock_result = {'accounts': accounts, 'total_items': 20}
        with patch('slack_bot.get_all_accounts', return_value=mock_result):
            resp = _post_command(flask_app, text='hot')
        data = resp.get_json()
        assert '...and 5 more' in data['text']


class TestSlashCommandStats:
    """Test /reporadar stats."""

    def test_stats_aggregates_days(self, flask_app):
        mock_stats = [
            {'scans_run': 10, 'api_calls_estimated': 100, 'webhooks_fired': 5},
            {'scans_run': 20, 'api_calls_estimated': 200, 'webhooks_fired': 10},
        ]
        mock_counts = {'0': 5, '1': 3, '2': 2, '3': 1, '4': 0}
        with patch('slack_bot.get_stats_last_n_days', return_value=mock_stats), \
             patch('slack_bot.get_tier_counts', return_value=mock_counts):
            resp = _post_command(flask_app, text='stats')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['response_type'] == 'in_channel'
        assert '30' in data['text']   # total scans = 10 + 20
        assert '300' in data['text']  # total API calls
        assert '15' in data['text']   # total webhooks


class TestSlashCommandScan:
    """Test /reporadar scan <company>."""

    def test_scan_queues_background_thread(self, flask_app):
        """Scan returns immediate ack and spawns a background thread."""
        with patch('slack_bot.threading') as mock_threading:
            resp = _post_command(flask_app, text='scan TestCorp', skip_sig=True)
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['response_type'] == 'ephemeral'
        assert 'TestCorp' in data['text']
        # Verify a thread was started
        mock_threading.Thread.assert_called_once()
        mock_threading.Thread.return_value.start.assert_called_once()

    def test_scan_async_existing_account(self, flask_app):
        """_cmd_scan_async with an existing account triggers a rescan."""
        from slack_bot import _cmd_scan_async

        mock_account = {
            'company_name': 'ExistingCorp',
            'current_tier': 1,
        }
        with patch('slack_bot.get_account_by_company_case_insensitive', return_value=mock_account), \
             patch('slack_bot.validate_company_name', return_value=(True, 'ExistingCorp')), \
             patch('app.spawn_background_scan') as mock_scan, \
             patch('slack_bot._respond_to_slack') as mock_respond:
            _cmd_scan_async('ExistingCorp', 'https://hooks.slack.com/resp')

        mock_scan.assert_called_once_with('ExistingCorp')
        mock_respond.assert_called_once()
        payload = mock_respond.call_args[0][1]
        assert 'Rescan queued' in payload['text']

    def test_scan_async_new_account(self, flask_app):
        """_cmd_scan_async with a new company adds to Tier 0 and scans."""
        from slack_bot import _cmd_scan_async

        with patch('slack_bot.get_account_by_company_case_insensitive', return_value=None), \
             patch('slack_bot.validate_company_name', return_value=(True, 'NewCorp')), \
             patch('app.spawn_background_scan') as mock_scan, \
             patch('app.add_account_to_tier_0') as mock_add, \
             patch('slack_bot._respond_to_slack') as mock_respond:
            _cmd_scan_async('NewCorp', 'https://hooks.slack.com/resp')

        mock_add.assert_called_once_with('NewCorp', '')
        mock_scan.assert_called_once_with('NewCorp')
        payload = mock_respond.call_args[0][1]
        assert 'New account' in payload['text']

    def test_scan_async_empty_company_name(self, flask_app):
        """_cmd_scan_async with empty name sends usage message."""
        from slack_bot import _cmd_scan_async

        with patch('slack_bot._respond_to_slack') as mock_respond:
            _cmd_scan_async('', 'https://hooks.slack.com/resp')

        payload = mock_respond.call_args[0][1]
        assert 'Usage' in payload['text']

    def test_scan_async_invalid_company_name(self, flask_app):
        """_cmd_scan_async with invalid name (e.g. script injection) sends error."""
        from slack_bot import _cmd_scan_async

        with patch('slack_bot._respond_to_slack') as mock_respond:
            _cmd_scan_async('<script>alert(1)</script>', 'https://hooks.slack.com/resp')

        payload = mock_respond.call_args[0][1]
        assert 'Invalid company name' in payload['text']


# ===========================================================================
# Interactive Callbacks
# ===========================================================================

class TestSlackInteractions:
    """Test /slack/interactions — block_actions (enrollment approve/reject)."""

    def _make_enrollment_payload(self, action_id, contact_id):
        return {
            'type': 'block_actions',
            'user': {'username': 'testuser', 'name': 'Test User'},
            'channel': {'id': CHANNEL_ID},
            'message': {
                'ts': '1234567890.123456',
                'blocks': [
                    {'type': 'section', 'text': {'type': 'mrkdwn', 'text': 'Some text'}},
                    {'type': 'actions', 'elements': []},
                ],
            },
            'actions': [{
                'action_id': f'{action_id}_{contact_id}',
                'value': str(contact_id),
            }],
        }

    def test_approve_enrollment(self, flask_app):
        payload = self._make_enrollment_payload('enrollment_approve', 99)
        with patch('slack_bot.update_enrollment_contact') as mock_update, \
             patch('slack_bot._slack_api', return_value={'ok': True}), \
             patch('slack_bot.log_webhook'):
            resp = _post_interaction(flask_app, payload)
        assert resp.status_code == 200
        mock_update.assert_called_once_with(99, status='approved')

    def test_reject_enrollment(self, flask_app):
        payload = self._make_enrollment_payload('enrollment_reject', 42)
        with patch('slack_bot.update_enrollment_contact') as mock_update, \
             patch('slack_bot._slack_api', return_value={'ok': True}), \
             patch('slack_bot.log_webhook'):
            resp = _post_interaction(flask_app, payload)
        assert resp.status_code == 200
        mock_update.assert_called_once_with(
            42, status='skipped',
            error_message='Rejected by testuser via Slack',
        )

    def test_interaction_invalid_signature(self, flask_app):
        payload = {'type': 'block_actions', 'actions': []}
        env = _slack_env()
        with patch.dict('os.environ', env, clear=False):
            resp = flask_app.post(
                '/slack/interactions',
                data={'payload': json.dumps(payload)},
                headers={
                    'X-Slack-Request-Timestamp': str(int(time.time())),
                    'X-Slack-Signature': 'v0=invalid',
                },
            )
        assert resp.status_code == 401

    def test_interaction_invalid_json_payload(self, flask_app):
        """Malformed JSON in the payload field returns 400."""
        env = _slack_env()
        with patch.dict('os.environ', env, clear=False), \
             patch('slack_bot._verify_slack_signature', return_value=True):
            resp = flask_app.post(
                '/slack/interactions',
                data={'payload': 'not-valid-json'},
            )
        assert resp.status_code == 400

    def test_interaction_unhandled_type(self, flask_app):
        payload = {'type': 'view_submission', 'view': {}}
        resp = _post_interaction(flask_app, payload)
        assert resp.status_code == 200

    def test_interaction_no_actions(self, flask_app):
        payload = {'type': 'block_actions', 'actions': []}
        resp = _post_interaction(flask_app, payload)
        assert resp.status_code == 200

    def test_interaction_unknown_action_id(self, flask_app):
        payload = {
            'type': 'block_actions',
            'actions': [{'action_id': 'unknown_action_123', 'value': '1'}],
        }
        resp = _post_interaction(flask_app, payload)
        assert resp.status_code == 200

    def test_interaction_invalid_contact_id(self, flask_app):
        payload = {
            'type': 'block_actions',
            'user': {'username': 'testuser'},
            'channel': {'id': CHANNEL_ID},
            'message': {'ts': '123', 'blocks': []},
            'actions': [{'action_id': 'enrollment_approve_notanumber', 'value': 'bad'}],
        }
        resp = _post_interaction(flask_app, payload)
        assert resp.status_code == 400


# ===========================================================================
# send_tier2_alert
# ===========================================================================

class TestSendTier2Alert:
    """Test send_tier2_alert() proactive notification."""

    def test_sends_alert_with_full_account_data(self, flask_app):
        from slack_bot import send_tier2_alert

        account = {
            'company_name': 'AlertCorp',
            'evidence_summary': 'Found react-i18next in webapp',
            'github_org': 'alertcorp',
            'annual_revenue': '$5M',
            'latest_report_id': 7,
        }
        with patch.dict('os.environ', _slack_env(), clear=False), \
             patch('slack_bot._slack_api', return_value={'ok': True}) as mock_api:
            result = send_tier2_alert(account)

        assert result is True
        mock_api.assert_called_once()
        call_args = mock_api.call_args
        assert call_args[0][0] == 'chat.postMessage'
        payload = call_args[0][1]
        assert payload['channel'] == CHANNEL_ID
        assert 'AlertCorp' in payload['text']
        # Blocks should contain company info, evidence, and action buttons
        block_types = [b['type'] for b in payload['blocks']]
        assert 'header' in block_types
        assert 'actions' in block_types

    def test_sends_alert_minimal_data(self, flask_app):
        """Alert works with minimal account dict (no report_id, no github_org)."""
        from slack_bot import send_tier2_alert

        account = {'company_name': 'MinimalCorp'}
        with patch.dict('os.environ', _slack_env(), clear=False), \
             patch('slack_bot._slack_api', return_value={'ok': True}) as mock_api:
            result = send_tier2_alert(account)

        assert result is True
        payload = mock_api.call_args[0][1]
        assert 'MinimalCorp' in payload['text']

    def test_returns_false_when_channel_not_set(self, flask_app):
        from slack_bot import send_tier2_alert

        env = {'SLACK_BOT_TOKEN': BOT_TOKEN}
        with patch.dict('os.environ', env, clear=False), \
             patch('os.environ.get') as mock_get:
            mock_get.side_effect = lambda k, d=None: env.get(k, d)
            result = send_tier2_alert({'company_name': 'NoChanCorp'})

        assert result is False

    def test_returns_false_when_api_fails(self, flask_app):
        from slack_bot import send_tier2_alert

        account = {'company_name': 'FailCorp'}
        with patch.dict('os.environ', _slack_env(), clear=False), \
             patch('slack_bot._slack_api', return_value={'ok': False, 'error': 'channel_not_found'}):
            result = send_tier2_alert(account)

        assert result is False


# ===========================================================================
# send_enrollment_approval
# ===========================================================================

class TestSendEnrollmentApproval:
    """Test send_enrollment_approval() interactive approval messages."""

    def test_sends_approval_request(self, flask_app):
        from slack_bot import send_enrollment_approval

        contact = {
            'id': 55,
            'first_name': 'Jane',
            'last_name': 'Doe',
            'email': 'jane@example.com',
            'title': 'VP Engineering',
            'company_name': 'ApprovalCorp',
            'persona_name': 'Engineering',
            'sequence_name': 'Preparing - Technical',
            'linkedin_url': 'https://linkedin.com/in/janedoe',
        }
        with patch.dict('os.environ', _slack_env(), clear=False), \
             patch('slack_bot._slack_api', return_value={'ok': True}) as mock_api:
            result = send_enrollment_approval(contact)

        assert result is True
        payload = mock_api.call_args[0][1]
        assert 'Jane Doe' in payload['text']
        assert 'ApprovalCorp' in payload['text']
        # Should contain approve/reject buttons
        actions_block = [b for b in payload['blocks'] if b.get('type') == 'actions']
        assert len(actions_block) == 1
        buttons = actions_block[0]['elements']
        assert any('enrollment_approve_55' in b.get('action_id', '') for b in buttons)
        assert any('enrollment_reject_55' in b.get('action_id', '') for b in buttons)

    def test_no_channel_returns_false(self, flask_app):
        from slack_bot import send_enrollment_approval

        with patch.dict('os.environ', {'SLACK_BOT_TOKEN': BOT_TOKEN}, clear=False), \
             patch('os.environ.get') as mock_get:
            mock_get.side_effect = lambda k, d=None: {'SLACK_BOT_TOKEN': BOT_TOKEN}.get(k, d)
            result = send_enrollment_approval({'id': 1, 'company_name': 'X'})

        assert result is False


# ===========================================================================
# send_weekly_digest
# ===========================================================================

class TestSendWeeklyDigest:

    def test_sends_digest_with_hot_leads(self, flask_app):
        from slack_bot import send_weekly_digest

        mock_stats = [
            {'scans_run': 5, 'api_calls_estimated': 50, 'webhooks_fired': 2},
        ]
        mock_counts = {'0': 10, '1': 5, '2': 3, '3': 1, '4': 0}

        with patch.dict('os.environ', _slack_env(), clear=False), \
             patch('slack_bot.get_stats_last_n_days', return_value=mock_stats), \
             patch('slack_bot.get_tier_counts', return_value=mock_counts), \
             patch('slack_bot._slack_api', return_value={'ok': True}) as mock_api:
            result = send_weekly_digest()

        assert result is True
        payload = mock_api.call_args[0][1]
        assert 'Weekly Digest' in payload['text']
        # Should mention hot leads count
        blocks_text = json.dumps(payload['blocks'])
        assert 'Goldilocks Zone' in blocks_text

    def test_digest_no_channel_returns_false(self, flask_app):
        from slack_bot import send_weekly_digest

        with patch.dict('os.environ', {'SLACK_BOT_TOKEN': BOT_TOKEN}, clear=False), \
             patch('os.environ.get') as mock_get:
            mock_get.side_effect = lambda k, d=None: {'SLACK_BOT_TOKEN': BOT_TOKEN}.get(k, d)
            result = send_weekly_digest()

        assert result is False


# ===========================================================================
# Utility helpers
# ===========================================================================

class TestUtilityHelpers:

    def test_progress_bar_normal(self):
        from slack_bot import _progress_bar
        bar = _progress_bar(5, 10, width=10)
        assert bar == '`#####-----`'

    def test_progress_bar_zero_total(self):
        from slack_bot import _progress_bar
        bar = _progress_bar(5, 0)
        assert bar == ''

    def test_progress_bar_full(self):
        from slack_bot import _progress_bar
        bar = _progress_bar(10, 10, width=10)
        assert bar == '`##########`'

    def test_is_slack_bot_configured_all_set(self):
        from slack_bot import is_slack_bot_configured
        with patch.dict('os.environ', _slack_env(), clear=False):
            assert is_slack_bot_configured() is True

    def test_is_slack_bot_configured_missing_token(self):
        from slack_bot import is_slack_bot_configured
        env = {'SLACK_SIGNING_SECRET': SIGNING_SECRET, 'SLACK_CHANNEL_ID': CHANNEL_ID}
        with patch.dict('os.environ', env, clear=False), \
             patch('os.environ.get') as mock_get:
            mock_get.side_effect = lambda k, d=None: env.get(k, d)
            assert is_slack_bot_configured() is False

    def test_replace_actions_with_status(self):
        from slack_bot import _replace_actions_with_status
        blocks = [
            {'type': 'section', 'text': {'type': 'mrkdwn', 'text': 'Hello'}},
            {'type': 'actions', 'elements': []},
        ]
        result = _replace_actions_with_status(blocks, 'Approved by @testuser', 'white_check_mark')
        assert len(result) == 2
        assert result[0]['type'] == 'section'
        assert result[1]['type'] == 'context'
        assert 'Approved by @testuser' in result[1]['elements'][0]['text']


# ===========================================================================
# Slack API helper
# ===========================================================================

class TestSlackApiHelper:

    def test_slack_api_no_token(self, flask_app):
        from slack_bot import _slack_api
        with patch('os.environ.get', return_value=None):
            result = _slack_api('chat.postMessage', {'channel': 'C1', 'text': 'hi'})
        assert result['ok'] is False
        assert result['error'] == 'not_configured'

    def test_slack_api_calls_post(self, flask_app):
        from slack_bot import _slack_api

        mock_resp = MagicMock()
        mock_resp.json.return_value = {'ok': True, 'ts': '123'}

        with patch.dict('os.environ', _slack_env(), clear=False), \
             patch('slack_bot.http_requests.post', return_value=mock_resp) as mock_post:
            result = _slack_api('chat.postMessage', {'channel': 'C1', 'text': 'hi'})

        assert result['ok'] is True
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert call_url == 'https://slack.com/api/chat.postMessage'


class TestRespondToSlack:

    def test_respond_posts_to_url(self, flask_app):
        from slack_bot import _respond_to_slack

        with patch('slack_bot.http_requests.post') as mock_post:
            _respond_to_slack('https://hooks.slack.com/resp', {'text': 'done'})

        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == 'https://hooks.slack.com/resp'

    def test_respond_empty_url_noop(self, flask_app):
        from slack_bot import _respond_to_slack

        with patch('slack_bot.http_requests.post') as mock_post:
            _respond_to_slack('', {'text': 'done'})

        mock_post.assert_not_called()

    def test_respond_handles_exception(self, flask_app):
        from slack_bot import _respond_to_slack

        with patch('slack_bot.http_requests.post', side_effect=Exception('network error')):
            # Should not raise
            _respond_to_slack('https://hooks.slack.com/resp', {'text': 'done'})
