"""Tests for email_routes.py — Email Engine API routes."""
import json

import pytest
from contextlib import contextmanager
from unittest.mock import patch, MagicMock


def _mock_db_conn(mock_conn):
    """Create a context-manager patch for email_routes.db_connection."""
    @contextmanager
    def _ctx():
        yield mock_conn
    return patch('email_routes.db_connection', _ctx)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_batch():
    """A sample enrollment batch dict as returned by get_enrollment_batch."""
    return {
        'id': 1,
        'campaign_id': 10,
        'account_ids': '[1, 2, 3]',
        'status': 'active',
        'created_at': '2025-12-01T00:00:00',
    }


@pytest.fixture
def mock_campaign():
    """A sample campaign dict as returned by get_campaign."""
    return {
        'id': 10,
        'name': 'Test Campaign',
        'prompt': 'You are a helpful sales assistant.',
        'status': 'active',
    }


@pytest.fixture
def mock_contacts():
    """Sample enrollment contacts in 'discovered' status."""
    return [
        {
            'id': 101,
            'batch_id': 1,
            'company_name': 'AlphaCorp',
            'first_name': 'Alice',
            'last_name': 'Smith',
            'email': 'alice@alphacorp.com',
            'title': 'VP Engineering',
            'status': 'discovered',
        },
        {
            'id': 102,
            'batch_id': 1,
            'company_name': 'BetaCorp',
            'first_name': 'Bob',
            'last_name': 'Jones',
            'email': 'bob@betacorp.com',
            'title': 'Director of Product',
            'status': 'discovered',
        },
    ]


@pytest.fixture
def mock_email_result():
    """A sample email generation result from generate_batch_emails."""
    return {
        'best_subject': 'Your i18n journey at AlphaCorp',
        'best_body': 'Hi Alice, I noticed your team added react-i18next...',
        'best_variant': 'A',
        'variants': {
            'A': {
                'subject': 'Your i18n journey at AlphaCorp',
                'body': 'Hi Alice, I noticed your team added react-i18next...',
                'score': 85,
            },
            'B': {
                'subject': 'Localization at AlphaCorp',
                'body': 'Hi Alice, your team seems to be preparing...',
                'score': 72,
            },
        },
        'signal_type': 'dependency_injection',
        'persona': 'engineering',
        'canspam_footer': 'Unsubscribe: ...',
    }


# ===========================================================================
# POST /api/pipeline/generate-emails
# ===========================================================================

class TestGenerateEmails:
    """Test the generate-emails endpoint."""

    def test_missing_body_returns_400(self, flask_app):
        resp = flask_app.post('/api/pipeline/generate-emails',
                              content_type='application/json',
                              data='null')
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'batch_id' in data['message']

    def test_missing_batch_id_returns_400(self, flask_app):
        resp = flask_app.post('/api/pipeline/generate-emails',
                              json={'limit': 10})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'batch_id' in data['message']

    def test_invalid_limit_returns_400(self, flask_app):
        resp = flask_app.post('/api/pipeline/generate-emails',
                              json={'batch_id': 1, 'limit': 'abc'})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'limit' in data['message'].lower()

    def test_limit_exceeds_max_returns_400(self, flask_app):
        resp = flask_app.post('/api/pipeline/generate-emails',
                              json={'batch_id': 1, 'limit': 999})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'limit' in data['message'].lower()

    def test_batch_not_found_returns_404(self, flask_app):
        with patch('email_routes.get_enrollment_batch', return_value=None):
            resp = flask_app.post('/api/pipeline/generate-emails',
                                  json={'batch_id': 9999})
        assert resp.status_code == 404
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'Batch not found' in data['message']

    def test_no_pending_contacts(self, flask_app, mock_batch, mock_campaign):
        with patch('email_routes.get_enrollment_batch', return_value=mock_batch), \
             patch('email_routes.get_campaign', return_value=mock_campaign), \
             patch('email_routes.get_next_contacts_for_phase', return_value=[]):
            resp = flask_app.post('/api/pipeline/generate-emails',
                                  json={'batch_id': 1})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['generated'] == 0
        assert 'No contacts pending' in data['message']

    def test_successful_generation(self, flask_app, mock_batch, mock_campaign,
                                   mock_contacts, mock_email_result):
        """Happy path: contacts found, emails generated, results persisted."""
        # generate_batch_emails returns list of (contact_id, result_dict) tuples
        gen_results = [
            (101, mock_email_result),
            (102, mock_email_result),
        ]
        with patch('email_routes.get_enrollment_batch', return_value=mock_batch), \
             patch('email_routes.get_campaign', return_value=mock_campaign), \
             patch('email_routes.get_next_contacts_for_phase', return_value=mock_contacts), \
             patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value={'id': 1, 'evidence_summary': 'test'}), \
             patch('email_routes.get_scorecard_score', return_value={'score': 80}), \
             patch('email_routes.generate_batch_emails', return_value=gen_results) as mock_gen, \
             patch('email_routes.update_enrollment_contact') as mock_update:
            resp = flask_app.post('/api/pipeline/generate-emails',
                                  json={'batch_id': 1})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['generated'] == 2
        assert data['failed'] == 0
        assert data['total_processed'] == 2
        # update_enrollment_contact should be called for each result
        assert mock_update.call_count == 2

    def test_generation_with_failures(self, flask_app, mock_batch, mock_campaign,
                                      mock_contacts):
        """When generation returns errors, contacts are marked as failed."""
        gen_results = [
            (101, {'error': 'AI service unavailable', 'best_subject': ''}),
            (102, {'best_subject': 'Good email', 'best_body': 'Content', 'variants': {}}),
        ]
        with patch('email_routes.get_enrollment_batch', return_value=mock_batch), \
             patch('email_routes.get_campaign', return_value=mock_campaign), \
             patch('email_routes.get_next_contacts_for_phase', return_value=mock_contacts), \
             patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.generate_batch_emails', return_value=gen_results), \
             patch('email_routes.update_enrollment_contact') as mock_update:
            resp = flask_app.post('/api/pipeline/generate-emails',
                                  json={'batch_id': 1})

        data = resp.get_json()
        assert data['generated'] == 1
        assert data['failed'] == 1
        assert data['total_processed'] == 2

        # First call should be a failure (status='failed')
        first_call = mock_update.call_args_list[0]
        assert first_call[1]['status'] == 'failed'
        assert 'AI service unavailable' in first_call[1]['error_message']

        # Second call should be success (status='email_generated')
        second_call = mock_update.call_args_list[1]
        assert second_call[1]['status'] == 'email_generated'

    def test_custom_campaign_prompt(self, flask_app, mock_batch, mock_contacts):
        """When campaign_prompt is provided in the request, it is used directly."""
        gen_results = [(101, {'best_subject': 'S', 'best_body': 'B', 'variants': {}})]
        mock_contacts_single = [mock_contacts[0]]

        with patch('email_routes.get_enrollment_batch', return_value=mock_batch), \
             patch('email_routes.get_next_contacts_for_phase', return_value=mock_contacts_single), \
             patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.generate_batch_emails', return_value=gen_results) as mock_gen, \
             patch('email_routes.update_enrollment_contact'):
            resp = flask_app.post('/api/pipeline/generate-emails',
                                  json={
                                      'batch_id': 1,
                                      'campaign_prompt': 'Custom prompt override',
                                  })

        assert resp.status_code == 200
        # The custom prompt should be passed to generate_batch_emails
        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs['campaign_prompt'] == 'Custom prompt override'

    def test_no_campaign_prompt_falls_back_to_campaign(self, flask_app, mock_batch,
                                                        mock_campaign, mock_contacts):
        """When campaign_prompt not provided, it reads from the campaign record."""
        gen_results = [(101, {'best_subject': 'S', 'best_body': 'B', 'variants': {}})]
        mock_contacts_single = [mock_contacts[0]]

        with patch('email_routes.get_enrollment_batch', return_value=mock_batch), \
             patch('email_routes.get_campaign', return_value=mock_campaign), \
             patch('email_routes.get_next_contacts_for_phase', return_value=mock_contacts_single), \
             patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.generate_batch_emails', return_value=gen_results) as mock_gen, \
             patch('email_routes.update_enrollment_contact'):
            resp = flask_app.post('/api/pipeline/generate-emails',
                                  json={'batch_id': 1})

        call_kwargs = mock_gen.call_args[1]
        assert call_kwargs['campaign_prompt'] == 'You are a helpful sales assistant.'

    def test_default_limit_is_50(self, flask_app, mock_batch, mock_campaign):
        """When limit is not specified, default is 50."""
        with patch('email_routes.get_enrollment_batch', return_value=mock_batch), \
             patch('email_routes.get_campaign', return_value=mock_campaign), \
             patch('email_routes.get_next_contacts_for_phase', return_value=[]) as mock_get:
            resp = flask_app.post('/api/pipeline/generate-emails',
                                  json={'batch_id': 1})

        # The limit passed to get_next_contacts_for_phase should be 50
        mock_get.assert_called_once_with(1, 'discovered', limit=50)


# ===========================================================================
# GET /api/pipeline/email-preview
# ===========================================================================

class TestEmailPreview:
    """Test the email-preview endpoint."""

    def test_no_params_returns_400(self, flask_app):
        """Neither contact_id nor company_name provided."""
        resp = flask_app.get('/api/pipeline/email-preview')
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'contact_id or company_name is required' in data['message']

    def test_empty_company_name_returns_400(self, flask_app):
        resp = flask_app.get('/api/pipeline/email-preview?company_name=')
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'

    def test_invalid_company_name_returns_400(self, flask_app):
        resp = flask_app.get('/api/pipeline/email-preview?company_name=%3Cscript%3Ealert(1)%3C/script%3E')
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'

    def test_contact_not_found_returns_404(self, flask_app):
        """contact_id provided but contact doesn't exist."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None

        with _mock_db_conn(mock_conn):
            resp = flask_app.get('/api/pipeline/email-preview?contact_id=9999')

        assert resp.status_code == 404
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'Contact not found' in data['message']

    def test_contact_with_stored_email_variants(self, flask_app, mock_email_result):
        """Contact has generated_emails_json with variant structure — returns stored data."""
        stored_json = json.dumps(mock_email_result)

        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: {
            'id': 101,
            'company_name': 'AlphaCorp',
            'generated_emails_json': stored_json,
        }[k]
        mock_row.keys.return_value = ['id', 'company_name', 'generated_emails_json']

        # Make dict(row) work
        def row_dict():
            return {'id': 101, 'company_name': 'AlphaCorp', 'generated_emails_json': stored_json}

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = mock_row

        with _mock_db_conn(mock_conn), \
             patch('builtins.dict', side_effect=lambda x: row_dict() if x is mock_row else dict.__call__(x)):
            # The simpler approach: mock the dict conversion
            pass

        # Use a simpler approach — patch at sqlite Row level
        mock_row_dict = {
            'id': 101,
            'company_name': 'AlphaCorp',
            'generated_emails_json': stored_json,
        }
        mock_cursor.fetchone.return_value = mock_row

        with _mock_db_conn(mock_conn):
            # Patch dict() around the row to return our mock dict
            original_dict = dict

            class PatchedRow:
                """Mimics a sqlite3.Row that dict() can convert."""
                def __init__(self):
                    self._data = mock_row_dict

                def keys(self):
                    return self._data.keys()

                def __getitem__(self, key):
                    return self._data[key]

                def __iter__(self):
                    return iter(self._data)

                def get(self, key, default=None):
                    return self._data.get(key, default)

            mock_cursor.fetchone.return_value = PatchedRow()

            resp = flask_app.get('/api/pipeline/email-preview?contact_id=101')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert 'email' in data
        assert data['email']['subject'] == 'Your i18n journey at AlphaCorp'
        assert 'all_variants' in data['email']

    def test_contact_with_stored_email_specific_variant(self, flask_app, mock_email_result):
        """Request a specific variant (B) from stored email data."""
        stored_json = json.dumps(mock_email_result)

        class PatchedRow:
            def __init__(self):
                self._data = {
                    'id': 101,
                    'company_name': 'AlphaCorp',
                    'generated_emails_json': stored_json,
                }

            def keys(self):
                return self._data.keys()

            def __getitem__(self, key):
                return self._data[key]

            def __iter__(self):
                return iter(self._data)

            def get(self, key, default=None):
                return self._data.get(key, default)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = PatchedRow()

        with _mock_db_conn(mock_conn):
            resp = flask_app.get('/api/pipeline/email-preview?contact_id=101&variant=B')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['email']['variant'] == 'B'
        assert data['email']['subject'] == 'Localization at AlphaCorp'

    def test_contact_no_stored_email_generates_live(self, flask_app):
        """Contact exists but has no generated_emails_json — generates live preview."""

        class PatchedRow:
            def __init__(self):
                self._data = {
                    'id': 101,
                    'company_name': 'AlphaCorp',
                    'first_name': 'Alice',
                    'title': 'VP Eng',
                    'generated_emails_json': None,
                }

            def keys(self):
                return self._data.keys()

            def __getitem__(self, key):
                return self._data[key]

            def __iter__(self):
                return iter(self._data)

            def get(self, key, default=None):
                return self._data.get(key, default)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = PatchedRow()

        mock_preview = {
            'subject': 'Generated on the fly',
            'body': 'Hi Alice...',
            'variant': 'A',
        }

        with _mock_db_conn(mock_conn), \
             patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.preview_email', return_value=mock_preview):
            resp = flask_app.get('/api/pipeline/email-preview?contact_id=101')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['generated_live'] is True
        assert data['email']['subject'] == 'Generated on the fly'

    def test_company_name_preview(self, flask_app):
        """Preview by company_name (no contact_id) — creates mock contact and generates."""
        mock_preview = {
            'subject': 'i18n at ValidCorp',
            'body': 'Hi {{first_name}}, ...',
            'variant': 'A',
        }

        with patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.preview_email', return_value=mock_preview) as mock_prev:
            resp = flask_app.get('/api/pipeline/email-preview?company_name=ValidCorp')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['generated_live'] is True
        assert data['email']['subject'] == 'i18n at ValidCorp'

        # Verify mock contact was constructed correctly
        call_kwargs = mock_prev.call_args[1]
        assert call_kwargs['contact']['company_name'] == 'ValidCorp'
        assert call_kwargs['contact']['first_name'] == '{{first_name}}'

    def test_company_name_with_title(self, flask_app):
        """Preview with company_name and title params."""
        mock_preview = {'subject': 'Test', 'body': 'Body'}

        with patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.preview_email', return_value=mock_preview) as mock_prev:
            resp = flask_app.get('/api/pipeline/email-preview?company_name=TestCo&title=CTO')

        call_kwargs = mock_prev.call_args[1]
        assert call_kwargs['contact']['title'] == 'CTO'

    def test_company_name_with_account_data(self, flask_app):
        """When an account exists for the company, scorecard data is passed through."""
        mock_account = {'id': 5, 'evidence_summary': 'Ghost branch detected'}
        mock_score = {'score': 90, 'details': 'high priority'}
        mock_preview = {'subject': 'Test', 'body': 'Body'}

        with patch('email_routes.get_signals_by_company', return_value=[{'type': 'ghost_branch'}]), \
             patch('email_routes.get_account_by_company', return_value=mock_account), \
             patch('email_routes.get_scorecard_score', return_value=mock_score), \
             patch('email_routes.preview_email', return_value=mock_preview) as mock_prev:
            resp = flask_app.get('/api/pipeline/email-preview?company_name=ScoreCorp')

        assert resp.status_code == 200
        call_kwargs = mock_prev.call_args[1]
        assert call_kwargs['account_data']['evidence_summary'] == 'Ghost branch detected'

    def test_company_name_with_variant_param(self, flask_app):
        """Variant parameter is passed through to preview_email."""
        mock_preview = {'subject': 'Variant B', 'body': 'Body B', 'variant': 'B'}

        with patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.preview_email', return_value=mock_preview) as mock_prev:
            resp = flask_app.get('/api/pipeline/email-preview?company_name=VarCorp&variant=B')

        call_kwargs = mock_prev.call_args[1]
        assert call_kwargs['variant'] == 'B'

    def test_contact_with_legacy_format(self, flask_app):
        """Contact has stored email in legacy format (no 'variants' key)."""
        legacy_data = {
            'subject_1': 'Legacy Subject',
            'email_1': 'Legacy Body',
        }

        class PatchedRow:
            def __init__(self):
                self._data = {
                    'id': 101,
                    'company_name': 'LegacyCorp',
                    'generated_emails_json': json.dumps(legacy_data),
                }

            def keys(self):
                return self._data.keys()

            def __getitem__(self, key):
                return self._data[key]

            def __iter__(self):
                return iter(self._data)

            def get(self, key, default=None):
                return self._data.get(key, default)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = PatchedRow()

        with _mock_db_conn(mock_conn):
            resp = flask_app.get('/api/pipeline/email-preview?contact_id=101')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['format'] == 'legacy'
        assert data['email']['subject_1'] == 'Legacy Subject'

    def test_contact_with_corrupt_json(self, flask_app):
        """Contact has corrupt JSON in generated_emails_json — falls back to live generation."""

        class PatchedRow:
            def __init__(self):
                self._data = {
                    'id': 101,
                    'company_name': 'CorruptCorp',
                    'first_name': 'Test',
                    'title': 'Eng',
                    'generated_emails_json': '{not valid json',
                }

            def keys(self):
                return self._data.keys()

            def __getitem__(self, key):
                return self._data[key]

            def __iter__(self):
                return iter(self._data)

            def get(self, key, default=None):
                return self._data.get(key, default)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = PatchedRow()

        mock_preview = {'subject': 'Fresh', 'body': 'Generated'}

        with _mock_db_conn(mock_conn), \
             patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.preview_email', return_value=mock_preview):
            resp = flask_app.get('/api/pipeline/email-preview?contact_id=101')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['generated_live'] is True


# ===========================================================================
# Input Validation
# ===========================================================================

class TestInputValidation:
    """Verify input validators are properly wired into the routes."""

    def test_generate_negative_limit(self, flask_app):
        resp = flask_app.post('/api/pipeline/generate-emails',
                              json={'batch_id': 1, 'limit': -5})
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'limit' in data['message'].lower()

    def test_generate_zero_batch_id(self, flask_app):
        """batch_id of 0 is falsy, should be treated as missing."""
        resp = flask_app.post('/api/pipeline/generate-emails',
                              json={'batch_id': 0})
        assert resp.status_code == 400

    def test_preview_sql_injection_company_name(self, flask_app):
        resp = flask_app.get(
            '/api/pipeline/email-preview?company_name=DROP%20TABLE%20accounts'
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'

    def test_preview_xss_company_name(self, flask_app):
        resp = flask_app.get(
            '/api/pipeline/email-preview?company_name=%3Cscript%3Ealert(1)%3C/script%3E'
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'

    def test_preview_very_long_company_name(self, flask_app):
        long_name = 'A' * 250
        resp = flask_app.get(f'/api/pipeline/email-preview?company_name={long_name}')
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'too long' in data['message'].lower()


# ===========================================================================
# JSON Response Structure
# ===========================================================================

class TestResponseStructure:
    """Verify JSON responses have the expected keys."""

    def test_generate_success_keys(self, flask_app, mock_batch, mock_campaign,
                                    mock_contacts, mock_email_result):
        gen_results = [(101, mock_email_result), (102, mock_email_result)]
        with patch('email_routes.get_enrollment_batch', return_value=mock_batch), \
             patch('email_routes.get_campaign', return_value=mock_campaign), \
             patch('email_routes.get_next_contacts_for_phase', return_value=mock_contacts), \
             patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.generate_batch_emails', return_value=gen_results), \
             patch('email_routes.update_enrollment_contact'):
            resp = flask_app.post('/api/pipeline/generate-emails',
                                  json={'batch_id': 1})

        data = resp.get_json()
        assert 'status' in data
        assert 'generated' in data
        assert 'failed' in data
        assert 'total_processed' in data
        assert isinstance(data['generated'], int)
        assert isinstance(data['failed'], int)

    def test_preview_success_keys(self, flask_app):
        mock_preview = {'subject': 'Test', 'body': 'Body', 'variant': 'A'}

        with patch('email_routes.get_signals_by_company', return_value=[]), \
             patch('email_routes.get_account_by_company', return_value=None), \
             patch('email_routes.preview_email', return_value=mock_preview):
            resp = flask_app.get('/api/pipeline/email-preview?company_name=StructCorp')

        data = resp.get_json()
        assert 'status' in data
        assert 'email' in data
        assert 'generated_live' in data

    def test_error_response_structure(self, flask_app):
        resp = flask_app.post('/api/pipeline/generate-emails',
                              json={})
        data = resp.get_json()
        assert 'status' in data
        assert data['status'] == 'error'
        assert 'message' in data
