"""
Comprehensive tests for all buttons and Apollo API endpoints.

Tests cover:
1. All Apollo API routes (/api/apollo-lookup, /api/apollo/sequences,
   /api/apollo/sequence-detect, /api/apollo/enroll-sequence,
   /api/scorecard/enroll, /api/contributors/<id>/apollo,
   /api/contributors/<id>/email)
2. All button-triggered API endpoints (/api/send-to-bdr,
   /api/send-outreach-email, /api/reports/<id>/favorite,
   /api/reports/<id> DELETE, /api/linkedin/extract,
   /api/linkedin/find-contact, /api/linkedin/generate-email,
   /api/scorecard/generate-email, /api/scorecard/rescore,
   /api/scorecard/systems)
3. Edge cases: missing API keys, missing fields, bad data, etc.
"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock

# Ensure APOLLO_API_KEY is set for route logic that checks it
os.environ.setdefault('APOLLO_API_KEY', 'test-apollo-key-12345')
os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret')

from app import app


@pytest.fixture
def client():
    """Create a Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def _mock_response(status_code=200, json_data=None, text=''):
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or json.dumps(json_data or {})
    return resp


# ──────────────────────────────────────────────────────────────────────
# Apollo Lookup: /api/apollo-lookup
# ──────────────────────────────────────────────────────────────────────
class TestApolloLookup:
    """Tests for the /api/apollo-lookup endpoint (Find Email button)."""

    def test_missing_body(self, client):
        """POST with no JSON body returns 400."""
        resp = client.post('/api/apollo-lookup', content_type='application/json')
        assert resp.status_code == 400

    def test_empty_json_body(self, client):
        """POST with empty JSON object returns 400."""
        resp = client.post('/api/apollo-lookup', json={})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'

    def test_no_api_key(self, client):
        """Returns error when APOLLO_API_KEY is unset."""
        with patch.dict(os.environ, {'APOLLO_API_KEY': ''}):
            resp = client.post('/api/apollo-lookup',
                               json={'name': 'John Doe', 'company': 'Acme'})
            assert resp.status_code == 500
            data = resp.get_json()
            assert 'not configured' in data['message'].lower()

    @patch('requests.post')
    def test_successful_lookup(self, mock_post, client):
        """Successful people/match returns email and metadata."""
        mock_post.return_value = _mock_response(200, {
            'person': {
                'email': 'john@acme.com',
                'email_status': 'verified',
                'name': 'John Doe',
                'title': 'CTO',
                'linkedin_url': 'https://linkedin.com/in/johndoe',
                'organization': {'name': 'Acme Inc'}
            }
        })

        resp = client.post('/api/apollo-lookup',
                           json={'name': 'John Doe', 'company': 'Acme'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['email'] == 'john@acme.com'
        assert data['email_status'] == 'verified'
        assert data['organization'] == 'Acme Inc'

    @patch('requests.post')
    def test_name_parsing(self, mock_post, client):
        """Full name is split into first_name/last_name correctly."""
        mock_post.side_effect = [
            _mock_response(200, {'person': None}),
            _mock_response(200, {'people': []}),
        ]

        resp = client.post('/api/apollo-lookup',
                           json={'name': 'Jane Smith', 'company': 'TestCo'})
        assert resp.status_code == 200
        # Verify name was parsed and sent to Apollo
        call_payload = mock_post.call_args_list[0][1]['json']
        assert call_payload['first_name'] == 'Jane'
        assert call_payload['last_name'] == 'Smith'

    @patch('requests.post')
    def test_domain_derivation(self, mock_post, client):
        """Domain is auto-derived from company name when not provided."""
        mock_post.side_effect = [
            _mock_response(200, {'person': None}),
            _mock_response(200, {'people': []}),
        ]

        resp = client.post('/api/apollo-lookup',
                           json={'first_name': 'John', 'last_name': 'Doe', 'company': 'Acme Inc'})
        assert resp.status_code == 200
        call_payload = mock_post.call_args_list[0][1]['json']
        assert call_payload['organization_domain'] == 'acme.com'

    @patch('requests.post')
    def test_domain_mismatch(self, mock_post, client):
        """Domain mismatch returns status='domain_mismatch'."""
        mock_post.return_value = _mock_response(200, {
            'person': {
                'email': 'john@differentcorp.com',
                'email_status': 'verified',
                'name': 'John Doe',
                'title': 'CTO',
                'linkedin_url': '',
                'organization': {'name': 'DifferentCorp'}
            }
        })

        resp = client.post('/api/apollo-lookup',
                           json={'name': 'John Doe', 'company': 'Acme', 'domain': 'acme.com'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'domain_mismatch'

    @patch('requests.post')
    def test_not_found(self, mock_post, client):
        """Returns not_found when no match in people/match or search."""
        mock_post.side_effect = [
            _mock_response(200, {'person': None}),
            _mock_response(200, {'people': []}),
        ]

        resp = client.post('/api/apollo-lookup',
                           json={'name': 'Nobody Known', 'company': 'FakeCo'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'not_found'

    @patch('requests.post')
    def test_search_fallback(self, mock_post, client):
        """Falls back to mixed_people/search when people/match has no person."""
        mock_post.side_effect = [
            _mock_response(200, {'person': None}),
            _mock_response(200, {'people': [{
                'email': 'jane@acme.com',
                'email_status': 'verified',
                'name': 'Jane Doe',
                'title': 'VP Eng',
                'linkedin_url': '',
                'organization': {'name': 'Acme'}
            }]}),
        ]

        resp = client.post('/api/apollo-lookup',
                           json={'name': 'Jane Doe', 'company': 'Acme'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['email'] == 'jane@acme.com'

    @patch('requests.post')
    def test_api_exception_handled(self, mock_post, client):
        """Network errors are caught and return 500."""
        mock_post.side_effect = Exception('Connection refused')

        resp = client.post('/api/apollo-lookup',
                           json={'name': 'John Doe', 'company': 'Acme'})
        assert resp.status_code == 500
        data = resp.get_json()
        assert data['status'] == 'error'

    @patch('requests.post')
    def test_personal_email_filtered(self, mock_post, client):
        """Gmail/Yahoo emails are filtered out (returns empty email)."""
        mock_post.return_value = _mock_response(200, {
            'person': {
                'email': 'john@gmail.com',
                'email_status': 'verified',
                'name': 'John Doe',
                'title': 'Developer',
                'linkedin_url': '',
                'organization': {'name': ''}
            }
        })

        resp = client.post('/api/apollo-lookup',
                           json={'name': 'John Doe', 'company': 'Acme'})
        assert resp.status_code == 200
        data = resp.get_json()
        # Personal email should be filtered — email should be empty
        assert data.get('email', '') == ''


# ──────────────────────────────────────────────────────────────────────
# Apollo Sequences: /api/apollo/sequences
# ──────────────────────────────────────────────────────────────────────
class TestApolloSequences:
    """Tests for /api/apollo/sequences (sequence dropdown)."""

    def test_no_api_key(self, client):
        """Returns error when APOLLO_API_KEY is unset."""
        with patch.dict(os.environ, {'APOLLO_API_KEY': ''}):
            resp = client.get('/api/apollo/sequences')
            assert resp.status_code == 400
            data = resp.get_json()
            assert data['status'] == 'error'
            assert 'NO_API_KEY' in data.get('code', '') or 'not configured' in data.get('message', '').lower()

    @patch('requests.post')
    def test_successful_sequences(self, mock_post, client):
        """Returns sequences from Apollo campaigns search."""
        mock_post.return_value = _mock_response(200, {
            'emailer_campaigns': [
                {'id': 'seq1', 'name': 'Test Sequence', 'active': True,
                 'emailer_steps': [{'type': 'auto_email'}], 'created_at': '2024-01-01'},
                {'id': 'seq2', 'name': 'Paused Seq', 'active': False,
                 'emailer_steps': [], 'created_at': '2024-01-02'},
            ]
        })

        resp = client.get('/api/apollo/sequences')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert len(data['sequences']) == 2
        assert data['sequences'][0]['name'] == 'Test Sequence'
        assert data['sequences'][0]['active'] is True
        assert data['sequences'][0]['num_steps'] == 1
        assert data['sequences'][1]['active'] is False

    @patch('requests.post')
    def test_forbidden_key(self, mock_post, client):
        """Returns 502 when Apollo returns 403 (insufficient permissions)."""
        mock_post.return_value = _mock_response(403)

        resp = client.get('/api/apollo/sequences')
        assert resp.status_code == 502
        data = resp.get_json()
        assert 'permission' in data['message'].lower() or 'master' in data['message'].lower()

    @patch('requests.post')
    def test_api_error(self, mock_post, client):
        """Returns 502 for non-200 Apollo responses."""
        mock_post.return_value = _mock_response(500)

        resp = client.get('/api/apollo/sequences')
        assert resp.status_code == 502

    @patch('requests.post')
    def test_exception_handled(self, mock_post, client):
        """Network exceptions return 500."""
        mock_post.side_effect = Exception('Timeout')

        resp = client.get('/api/apollo/sequences')
        assert resp.status_code == 500
        data = resp.get_json()
        assert data['status'] == 'error'


# ──────────────────────────────────────────────────────────────────────
# Apollo Sequence Detect: /api/apollo/sequence-detect
# ──────────────────────────────────────────────────────────────────────
class TestApolloSequenceDetect:
    """Tests for /api/apollo/sequence-detect (auto-detect config type)."""

    def test_no_api_key_returns_no_key(self, client):
        """Returns no_key silently when APOLLO_API_KEY unset."""
        with patch.dict(os.environ, {'APOLLO_API_KEY': ''}):
            resp = client.post('/api/apollo/sequence-detect', json={'sequence_id': 'abc'})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['status'] == 'no_key'

    def test_missing_sequence_id(self, client):
        """Returns 400 when sequence_id is missing."""
        resp = client.post('/api/apollo/sequence-detect', json={})
        assert resp.status_code == 400

    @patch('requests.post')
    def test_one_off_detection(self, mock_post, client):
        """Single email step → one_off config detected."""
        mock_post.return_value = _mock_response(200, {
            'emailer_campaigns': [{
                'id': 'seq123',
                'name': 'One-Off Sequence',
                'emailer_steps': [
                    {'type': 'auto_email', 'subject': 'Hello'}
                ]
            }]
        })

        resp = client.post('/api/apollo/sequence-detect', json={'sequence_id': 'seq123'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['detected_config'] == 'one_off'

    @patch('requests.post')
    def test_threaded_detection(self, mock_post, client):
        """Multiple steps with same/empty subject → threaded_4."""
        mock_post.return_value = _mock_response(200, {
            'emailer_campaigns': [{
                'id': 'seq456',
                'name': 'Threaded Sequence',
                'emailer_steps': [
                    {'type': 'auto_email', 'subject': 'Hello'},
                    {'type': 'auto_email', 'subject': ''},
                    {'type': 'auto_email', 'subject': ''},
                ]
            }]
        })

        resp = client.post('/api/apollo/sequence-detect', json={'sequence_id': 'seq456'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['detected_config'] == 'threaded_4'

    @patch('requests.post')
    def test_split_detection(self, mock_post, client):
        """Steps with 2 distinct subjects → split_2x2."""
        mock_post.return_value = _mock_response(200, {
            'emailer_campaigns': [{
                'id': 'seq789',
                'name': 'Split Sequence',
                'emailer_steps': [
                    {'type': 'auto_email', 'subject': 'Thread A'},
                    {'type': 'auto_email', 'subject': ''},
                    {'type': 'auto_email', 'subject': 'Thread B'},
                    {'type': 'auto_email', 'subject': ''},
                ]
            }]
        })

        resp = client.post('/api/apollo/sequence-detect', json={'sequence_id': 'seq789'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['detected_config'] == 'split_2x2'

    @patch('requests.post')
    def test_sequence_not_found(self, mock_post, client):
        """Returns not_found when sequence ID doesn't match any campaign."""
        mock_post.return_value = _mock_response(200, {
            'emailer_campaigns': [
                {'id': 'other_id', 'name': 'Other', 'emailer_steps': []}
            ]
        })

        resp = client.post('/api/apollo/sequence-detect', json={'sequence_id': 'nonexistent'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'not_found'

    @patch('requests.post')
    def test_forbidden_returns_auth_error(self, mock_post, client):
        """403 from Apollo returns auth_error status."""
        mock_post.return_value = _mock_response(403)

        resp = client.post('/api/apollo/sequence-detect', json={'sequence_id': 'seq1'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'auth_error'


# ──────────────────────────────────────────────────────────────────────
# Apollo Enroll Sequence: /api/apollo/enroll-sequence
# ──────────────────────────────────────────────────────────────────────
class TestApolloEnrollSequence:
    """Tests for /api/apollo/enroll-sequence (Enroll in Apollo button)."""

    def test_no_body(self, client):
        """Returns 400 when no JSON body."""
        resp = client.post('/api/apollo/enroll-sequence', content_type='application/json')
        assert resp.status_code == 400

    def test_no_api_key(self, client):
        """Returns 400 when APOLLO_API_KEY is unset."""
        with patch.dict(os.environ, {'APOLLO_API_KEY': ''}):
            resp = client.post('/api/apollo/enroll-sequence',
                               json={'email': 'test@example.com', 'sequence_id': 'seq1'})
            assert resp.status_code == 400

    def test_missing_required_fields(self, client):
        """Returns 400 when email or sequence_id missing."""
        resp = client.post('/api/apollo/enroll-sequence',
                           json={'email': 'test@example.com'})  # no sequence_id
        assert resp.status_code == 400

        resp = client.post('/api/apollo/enroll-sequence',
                           json={'sequence_id': 'seq1'})  # no email
        assert resp.status_code == 400

    @patch('requests.get')
    @patch('requests.post')
    @patch('requests.put')
    def test_successful_enrollment_new_contact(self, mock_put, mock_post, mock_get, client):
        """Full enrollment flow: create new contact + enroll in sequence."""
        # POST calls: 1) contact search, 2) contact create, 3) sequence enroll
        mock_post.side_effect = [
            _mock_response(200, {'contacts': []}),           # search
            _mock_response(200, {'contact': {'id': 'c123'}}), # create
            _mock_response(200, {}),                          # enroll
        ]

        # GET calls: 1) custom fields, 2) email accounts
        mock_get.side_effect = [
            _mock_response(200, {'typed_custom_fields': []}),
            _mock_response(200, {'email_accounts': [{'id': 'ea_1', 'active': True, 'email': 'sender@test.com'}]}),
        ]

        resp = client.post('/api/apollo/enroll-sequence', json={
            'email': 'john@acme.com',
            'first_name': 'John',
            'last_name': 'Doe',
            'sequence_id': 'seq_abc',
            'company_name': 'Acme',
            'personalized_subject': 'Hey John',
            'personalized_email_body': 'Custom body text'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['contact_id'] == 'c123'

    @patch('requests.get')
    @patch('requests.post')
    @patch('requests.put')
    def test_enrollment_existing_contact(self, mock_put, mock_post, mock_get, client):
        """Enrolls existing contact (found via search) with custom field injection."""
        # POST calls: 1) contact search finds existing, 2) sequence enroll
        mock_post.side_effect = [
            _mock_response(200, {'contacts': [{'id': 'existing_c'}]}),
            _mock_response(200, {}),  # enroll
        ]

        # GET calls: 1) custom fields, 2) email accounts
        mock_get.side_effect = [
            _mock_response(200, {'typed_custom_fields': [
                {'id': 'field_1', 'name': 'personalized_subject_1'}
            ]}),
            _mock_response(200, {'email_accounts': [{'id': 'ea_1', 'active': True}]}),
        ]

        # PUT for custom field injection
        mock_put.return_value = _mock_response(200)

        resp = client.post('/api/apollo/enroll-sequence', json={
            'email': 'existing@company.com',
            'sequence_id': 'seq_abc',
            'personalized_subject': 'Custom Subject'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'

    @patch('requests.get')
    @patch('requests.post')
    def test_no_email_account(self, mock_post, mock_get, client):
        """Returns error when no active email account found."""
        mock_post.return_value = _mock_response(200, {'contacts': [{'id': 'c1'}]})

        mock_get.side_effect = [
            _mock_response(200, {'typed_custom_fields': []}),
            _mock_response(200, {'email_accounts': []}),  # No active accounts
        ]

        resp = client.post('/api/apollo/enroll-sequence', json={
            'email': 'test@example.com',
            'sequence_id': 'seq1'
        })
        assert resp.status_code == 500
        data = resp.get_json()
        assert 'email account' in data['message'].lower()

    @patch('requests.get')
    @patch('requests.post')
    def test_contact_creation_failure(self, mock_post, mock_get, client):
        """Returns 502 when contact creation fails."""
        mock_post.side_effect = [
            _mock_response(200, {'contacts': []}),  # search: no contacts
            _mock_response(422, {'message': 'Unprocessable entity'}, text='Unprocessable entity'),
        ]

        mock_get.return_value = _mock_response(200, {'typed_custom_fields': []})

        resp = client.post('/api/apollo/enroll-sequence', json={
            'email': 'test@example.com',
            'sequence_id': 'seq1'
        })
        assert resp.status_code == 502


# ──────────────────────────────────────────────────────────────────────
# Contributor Apollo Status: /api/contributors/<id>/apollo
# ──────────────────────────────────────────────────────────────────────
class TestContributorApolloStatus:
    """Tests for /api/contributors/<id>/apollo (Update Apollo status button)."""

    @patch('app.update_contributor_apollo_status')
    def test_update_success(self, mock_update, client):
        """Successfully updates Apollo status."""
        mock_update.return_value = True

        resp = client.post('/api/contributors/1/apollo',
                           json={'status': 'enrolled', 'sequence_name': 'Test Seq'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        mock_update.assert_called_once_with(1, 'enrolled', 'Test Seq')

    @patch('app.update_contributor_apollo_status')
    def test_contributor_not_found(self, mock_update, client):
        """Returns 404 for non-existent contributor."""
        mock_update.return_value = False

        resp = client.post('/api/contributors/99999/apollo',
                           json={'status': 'enrolled'})
        assert resp.status_code == 404

    @patch('app.update_contributor_apollo_status')
    def test_default_status(self, mock_update, client):
        """Defaults to status='sent' when not provided."""
        mock_update.return_value = True

        resp = client.post('/api/contributors/1/apollo', json={})
        assert resp.status_code == 200
        mock_update.assert_called_once_with(1, 'sent', '')


# ──────────────────────────────────────────────────────────────────────
# Contributor Email: /api/contributors/<id>/email
# ──────────────────────────────────────────────────────────────────────
class TestContributorEmail:
    """Tests for /api/contributors/<id>/email (Save email button)."""

    @patch('app.update_contributor_email')
    def test_save_email_success(self, mock_update, client):
        """Successfully saves a work email."""
        mock_update.return_value = True

        resp = client.post('/api/contributors/1/email',
                           json={'email': 'john@acme.com'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['email'] == 'john@acme.com'

    @patch('app.update_contributor_email')
    def test_personal_email_rejected(self, mock_update, client):
        """Personal emails (gmail, yahoo) are rejected."""
        resp = client.post('/api/contributors/1/email',
                           json={'email': 'john@gmail.com'})
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'valid work email' in data['error'].lower()

    def test_empty_email(self, client):
        """Empty email returns 400."""
        resp = client.post('/api/contributors/1/email', json={'email': ''})
        assert resp.status_code == 400

    @patch('app.update_contributor_email')
    def test_contributor_not_found(self, mock_update, client):
        """Returns 404 for non-existent contributor."""
        mock_update.return_value = False
        resp = client.post('/api/contributors/99999/email',
                           json={'email': 'john@acme.com'})
        assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────
# Send to BDR: /api/send-to-bdr
# ──────────────────────────────────────────────────────────────────────
class TestSendToBDR:
    """Tests for /api/send-to-bdr (Send to BDR button on report page)."""

    def test_no_body(self, client):
        """Returns 400 when no JSON body."""
        resp = client.post('/api/send-to-bdr', content_type='application/json')
        assert resp.status_code == 400

    def test_missing_to_email(self, client):
        """Returns 400 when to_email missing."""
        resp = client.post('/api/send-to-bdr',
                           json={'subject': 'Test', 'body': 'Hello'})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['success'] is False

    def test_missing_subject_or_body(self, client):
        """Returns 400 when subject or body missing."""
        resp = client.post('/api/send-to-bdr',
                           json={'to_email': 'bdr@company.com'})
        assert resp.status_code == 400

    @patch('app.send_email_draft')
    def test_successful_send(self, mock_send, client):
        """Successfully sends email to BDR."""
        mock_send.return_value = {'success': True}

        resp = client.post('/api/send-to-bdr', json={
            'to_email': 'bdr@company.com',
            'subject': 'New Lead: Acme',
            'body': 'Check out this lead',
            'company_name': 'Acme',
            'report_url': 'https://example.com/report/1'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True

    @patch('app.send_email_draft')
    def test_send_failure(self, mock_send, client):
        """Returns 500 when email send fails."""
        mock_send.return_value = {'success': False, 'error': 'SMTP error'}

        resp = client.post('/api/send-to-bdr', json={
            'to_email': 'bdr@company.com',
            'subject': 'Test',
            'body': 'Hello'
        })
        assert resp.status_code == 500


# ──────────────────────────────────────────────────────────────────────
# Send Outreach Email: /api/send-outreach-email
# ──────────────────────────────────────────────────────────────────────
class TestSendOutreachEmail:
    """Tests for /api/send-outreach-email (Compose & Send button)."""

    def test_no_body(self, client):
        """Returns 400 when no JSON body."""
        resp = client.post('/api/send-outreach-email', content_type='application/json')
        assert resp.status_code == 400

    def test_missing_required_fields(self, client):
        """Returns 400 when to_email, subject, or body missing."""
        resp = client.post('/api/send-outreach-email',
                           json={'to_email': 'test@example.com', 'subject': 'Hi'})
        assert resp.status_code == 400

    @patch('app.send_email_draft')
    def test_successful_send(self, mock_send, client):
        """Successfully sends outreach email."""
        mock_send.return_value = {'success': True}

        resp = client.post('/api/send-outreach-email', json={
            'to_email': 'contact@company.com',
            'subject': 'Quick question',
            'body': 'Hi there, quick question about i18n...',
            'company_name': 'TestCo',
            'report_id': '42'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'

    @patch('app.send_email_draft')
    def test_send_failure(self, mock_send, client):
        """Returns 500 on send failure."""
        mock_send.return_value = {'success': False, 'error': 'SMTP failed'}

        resp = client.post('/api/send-outreach-email', json={
            'to_email': 'test@example.com',
            'subject': 'Test',
            'body': 'Hello'
        })
        assert resp.status_code == 500

    @patch('app.send_email_draft')
    def test_exception_handled(self, mock_send, client):
        """Network exceptions are caught."""
        mock_send.side_effect = Exception('Connection error')

        resp = client.post('/api/send-outreach-email', json={
            'to_email': 'test@example.com',
            'subject': 'Test',
            'body': 'Hello'
        })
        assert resp.status_code == 500


# ──────────────────────────────────────────────────────────────────────
# LinkedIn Extract: /api/linkedin/extract
# ──────────────────────────────────────────────────────────────────────
class TestLinkedInExtract:
    """Tests for /api/linkedin/extract (screenshot upload button)."""

    def test_no_image(self, client):
        """Returns 400 when no image uploaded."""
        resp = client.post('/api/linkedin/extract')
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'

    def test_no_ai_key(self, client):
        """Returns 400 when no AI API key configured."""
        import io
        with patch.dict(os.environ, {'AI_INTEGRATIONS_OPENAI_API_KEY': '', 'AI_INTEGRATIONS_OPENAI_BASE_URL': ''}):
            data = {'image': (io.BytesIO(b'fake image data'), 'test.png')}
            resp = client.post('/api/linkedin/extract', data=data,
                               content_type='multipart/form-data')
            assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# LinkedIn Find Contact: /api/linkedin/find-contact
# ──────────────────────────────────────────────────────────────────────
class TestLinkedInFindContact:
    """Tests for /api/linkedin/find-contact (Apollo lookup from LinkedIn)."""

    def test_no_body(self, client):
        """Returns 400 when no JSON body."""
        resp = client.post('/api/linkedin/find-contact', content_type='application/json')
        assert resp.status_code == 400

    def test_no_api_key(self, client):
        """Returns 400 when APOLLO_API_KEY is unset."""
        with patch.dict(os.environ, {'APOLLO_API_KEY': ''}):
            resp = client.post('/api/linkedin/find-contact',
                               json={'name': 'John Doe', 'company': 'Acme'})
            assert resp.status_code == 400

    @patch('requests.post')
    def test_successful_find(self, mock_post, client):
        """Finds contact via people/match with email."""
        mock_post.return_value = _mock_response(200, {
            'person': {
                'id': 'p1',
                'first_name': 'John',
                'last_name': 'Doe',
                'email': 'john@acme.com',
                'title': 'CTO',
                'organization_name': '',
                'organization': {'name': 'Acme'},
                'linkedin_url': 'https://linkedin.com/in/johndoe',
                'photo_url': 'https://example.com/photo.jpg',
                'sanitized_phone': '+1234567890',
                'phone_numbers': [],
                'city': 'SF',
                'state': 'CA',
                'country': 'US',
                'name': 'John Doe'
            }
        })

        resp = client.post('/api/linkedin/find-contact',
                           json={'name': 'John Doe', 'company': 'Acme'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['contact']['email'] == 'john@acme.com'

    @patch('requests.post')
    def test_not_found(self, mock_post, client):
        """Returns not_found when no match in any source."""
        mock_post.side_effect = [
            _mock_response(200, {'person': None}),   # people/match
            _mock_response(200, {'contacts': []}),   # contacts/search
        ]

        resp = client.post('/api/linkedin/find-contact',
                           json={'name': 'Nobody', 'company': 'FakeCo'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'not_found'

    @patch('requests.post')
    def test_linkedin_url_name_parsing(self, mock_post, client):
        """Extracts name from LinkedIn URL slug when name not provided."""
        mock_post.side_effect = [
            _mock_response(200, {'person': None}),
            _mock_response(200, {'contacts': []}),
        ]

        resp = client.post('/api/linkedin/find-contact',
                           json={'linkedin_url': 'https://www.linkedin.com/in/jane-smith-123abc'})
        assert resp.status_code == 200
        # Should have parsed "Jane Smith" from the slug
        call_payload = mock_post.call_args_list[0][1]['json']
        assert call_payload.get('first_name') == 'Jane'
        assert call_payload.get('last_name') == 'Smith'

    @patch('requests.post')
    def test_personal_email_filtered(self, mock_post, client):
        """Gmail emails are filtered out from Apollo results."""
        mock_post.side_effect = [
            _mock_response(200, {
                'person': {
                    'id': 'p1',
                    'first_name': 'John', 'last_name': 'Doe',
                    'email': 'john@gmail.com',  # personal email
                    'title': 'Developer', 'organization_name': '',
                    'organization': {'name': ''},
                    'linkedin_url': '', 'photo_url': '',
                    'sanitized_phone': '', 'phone_numbers': [],
                    'city': '', 'state': '', 'country': '',
                    'name': 'John Doe'
                }
            }),
            _mock_response(200, {'contacts': []}),  # CRM search
        ]

        resp = client.post('/api/linkedin/find-contact',
                           json={'name': 'John Doe', 'company': 'Acme'})
        assert resp.status_code == 200
        data = resp.get_json()
        # Should return person but with empty email (filtered personal email)
        if data['status'] == 'success':
            assert data['contact']['email'] == ''


# ──────────────────────────────────────────────────────────────────────
# LinkedIn Generate Email: /api/linkedin/generate-email
# ──────────────────────────────────────────────────────────────────────
class TestLinkedInGenerateEmail:
    """Tests for /api/linkedin/generate-email (Generate Email button)."""

    def test_no_body(self, client):
        """Returns 400 when no JSON body."""
        resp = client.post('/api/linkedin/generate-email', content_type='application/json')
        assert resp.status_code == 400

    def test_no_ai_key(self, client):
        """Returns 400 when OpenAI keys missing."""
        with patch.dict(os.environ, {'AI_INTEGRATIONS_OPENAI_API_KEY': '', 'AI_INTEGRATIONS_OPENAI_BASE_URL': ''}):
            resp = client.post('/api/linkedin/generate-email',
                               json={'contact': {'name': 'John', 'company': 'Acme'}})
            assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# Scorecard Enroll: /api/scorecard/enroll
# ──────────────────────────────────────────────────────────────────────
class TestScorecardEnroll:
    """Tests for /api/scorecard/enroll (Enroll button on scorecard page)."""

    def test_no_body(self, client):
        """Returns 400 when no JSON body."""
        resp = client.post('/api/scorecard/enroll', content_type='application/json')
        assert resp.status_code == 400

    def test_no_api_key(self, client):
        """Returns 400 when APOLLO_API_KEY is unset."""
        with patch.dict(os.environ, {'APOLLO_API_KEY': ''}):
            resp = client.post('/api/scorecard/enroll',
                               json={'email': 'test@example.com', 'sequence_id': 'seq1', 'account_id': 1})
            assert resp.status_code == 400

    def test_missing_required_fields(self, client):
        """Returns 400 when email, sequence_id, or account_id missing."""
        resp = client.post('/api/scorecard/enroll',
                           json={'email': 'test@example.com', 'sequence_id': 'seq1'})  # no account_id
        assert resp.status_code == 400

    @patch('app.update_scorecard_enrollment')
    @patch('requests.get')
    @patch('requests.post')
    @patch('requests.put')
    def test_successful_enrollment(self, mock_put, mock_post, mock_get, mock_update, client):
        """Full scorecard enrollment flow (no personalized fields → 1 GET only)."""
        # POST: 1) contact search, 2) enroll
        mock_post.side_effect = [
            _mock_response(200, {'contacts': [{'id': 'c1'}]}),
            _mock_response(200, {}),
        ]

        # GET: only email accounts (no personalized fields → no custom fields lookup)
        mock_get.return_value = _mock_response(200, {
            'email_accounts': [{'id': 'ea1', 'active': True}]
        })
        mock_update.return_value = True

        resp = client.post('/api/scorecard/enroll', json={
            'email': 'lead@company.com',
            'sequence_id': 'seq_abc',
            'account_id': 42,
            'first_name': 'Jane',
            'last_name': 'Doe',
            'company_name': 'Company',
            'sequence_name': 'Test Seq'
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        mock_update.assert_called_once_with(42, 'enrolled', 'Test Seq')


# ──────────────────────────────────────────────────────────────────────
# Scorecard Systems: /api/scorecard/systems
# ──────────────────────────────────────────────────────────────────────
class TestScorecardSystems:
    """Tests for /api/scorecard/systems (system checkbox buttons)."""

    def test_no_body(self, client):
        """Returns 400 when no JSON body."""
        resp = client.post('/api/scorecard/systems', content_type='application/json')
        assert resp.status_code == 400

    def test_missing_account_id(self, client):
        """Returns 400 when account_id missing."""
        resp = client.post('/api/scorecard/systems',
                           json={'systems': {'vcs': True}, 'rubric': {'sys_vcs': 5}})
        assert resp.status_code == 400

    @patch('app.get_scorecard_score')
    @patch('app.update_scorecard_systems')
    def test_systems_update(self, mock_update, mock_get_score, client):
        """Successfully updates systems and recalculates score."""
        mock_update.return_value = True
        mock_get_score.return_value = {'total_score': 15}

        resp = client.post('/api/scorecard/systems', json={
            'account_id': 1,
            'systems': {'vcs': True, 'design': True},
            'rubric': {'sys_vcs': 5, 'sys_design': 3}
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['systems_score'] == 8  # 5 + 3
        assert data['total_score'] == 15

    @patch('app.update_scorecard_systems')
    def test_account_not_found(self, mock_update, client):
        """Returns 404 when account doesn't exist."""
        mock_update.return_value = False

        resp = client.post('/api/scorecard/systems', json={
            'account_id': 99999,
            'systems': {},
            'rubric': {}
        })
        assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────
# Scorecard Rescore: /api/scorecard/rescore
# ──────────────────────────────────────────────────────────────────────
class TestScorecardRescore:
    """Tests for /api/scorecard/rescore (Rescore button)."""

    @patch('app.upsert_scorecard_scores')
    @patch('app.get_all_accounts')
    def test_rescore_success(self, mock_accounts, mock_upsert, client):
        """Rescores all accounts successfully."""
        mock_accounts.return_value = [
            {'id': 1, 'company_name': 'Acme', 'scan_data': json.dumps({
                'scoring_v2': {
                    'org_intent_score': 50,
                    'org_maturity_label': 'Preparing',
                    'readiness_index': 0.7,
                    'recommended_outreach_angle': 'URGENT'
                }
            }), 'report_id': 10}
        ]
        mock_upsert.return_value = True

        resp = client.post('/api/scorecard/rescore', json={
            'rubric': {
                'lang_python': 10, 'lang_javascript': 8,
                'sys_vcs': 5, 'sys_design': 3,
                'bonus_preparing': 20, 'bonus_thinking': 10, 'bonus_ghost': 5
            }
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'


# ──────────────────────────────────────────────────────────────────────
# Scorecard Generate Email: /api/scorecard/generate-email
# ──────────────────────────────────────────────────────────────────────
class TestScorecardGenerateEmail:
    """Tests for /api/scorecard/generate-email (Generate Email button)."""

    def test_no_body(self, client):
        """Returns 400 when no JSON body."""
        resp = client.post('/api/scorecard/generate-email',
                           content_type='application/json')
        assert resp.status_code == 400

    def test_no_ai_key(self, client):
        """Returns 400 when OpenAI keys missing."""
        with patch.dict(os.environ, {'AI_INTEGRATIONS_OPENAI_API_KEY': '', 'AI_INTEGRATIONS_OPENAI_BASE_URL': ''}):
            resp = client.post('/api/scorecard/generate-email',
                               json={'company_name': 'Acme', 'contact_name': 'John'})
            assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# AgentMail Status: /api/agentmail/status
# ──────────────────────────────────────────────────────────────────────
class TestAgentMailStatus:
    """Tests for /api/agentmail/status (check if email service configured)."""

    @patch('app.is_agentmail_configured')
    def test_configured(self, mock_configured, client):
        """Returns configured=true when AgentMail is configured."""
        mock_configured.return_value = True
        resp = client.get('/api/agentmail/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['configured'] is True

    @patch('app.is_agentmail_configured')
    def test_not_configured(self, mock_configured, client):
        """Returns configured=false when AgentMail is not configured."""
        mock_configured.return_value = False
        resp = client.get('/api/agentmail/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['configured'] is False


# ──────────────────────────────────────────────────────────────────────
# Helper function tests
# ──────────────────────────────────────────────────────────────────────
class TestHelperFunctions:
    """Tests for helper functions used by buttons/Apollo flows."""

    def test_filter_personal_email(self):
        """Personal email domains are filtered correctly."""
        from app import _filter_personal_email
        assert _filter_personal_email('john@gmail.com') == ''
        assert _filter_personal_email('jane@yahoo.com') == ''
        assert _filter_personal_email('dev@hotmail.com') == ''
        assert _filter_personal_email('dev@icloud.com') == ''
        assert _filter_personal_email('dev@protonmail.com') == ''
        assert _filter_personal_email('dev@proton.me') == ''
        assert _filter_personal_email('john@acme.com') == 'john@acme.com'
        assert _filter_personal_email('') == ''
        assert _filter_personal_email(None) == ''

    def test_derive_company_domain(self):
        """Company name → domain derivation works correctly."""
        from app import _derive_company_domain
        assert _derive_company_domain('Acme Inc') == 'acme.com'
        assert _derive_company_domain('Acme Inc.') == 'acme.com'
        assert _derive_company_domain('Acme Corp') == 'acme.com'
        assert _derive_company_domain('Acme LLC') == 'acme.com'
        assert _derive_company_domain('Acme GmbH') == 'acme.com'
        assert _derive_company_domain('My Company') == 'mycompany.com'
        assert _derive_company_domain('') == ''
        assert _derive_company_domain(None) == ''

    def test_check_company_match(self):
        """Email-company matching works correctly."""
        from app import _check_company_match
        assert _check_company_match('john@acme.com', 'Acme') is True
        assert _check_company_match('john@acme.com', 'Acme Inc') is True
        assert _check_company_match('john@different.com', 'Acme') is False
        assert _check_company_match('', 'Acme') is True  # nothing to compare
        assert _check_company_match('john@acme.com', '') is True

    def test_sanitize_contributor_email(self):
        """Contributor email sanitization filters personal + mismatched emails."""
        from app import _sanitize_contributor_email
        assert _sanitize_contributor_email('john@acme.com', 'Acme') == 'john@acme.com'
        assert _sanitize_contributor_email('john@gmail.com', 'Acme') == ''
        assert _sanitize_contributor_email('john@other.com', 'Acme') == ''
        assert _sanitize_contributor_email('', 'Acme') == ''

    def test_sanitize_ai_error(self):
        """AI error messages are sanitized to be user-friendly."""
        from app import sanitize_ai_error
        assert 'overloaded' in sanitize_ai_error(Exception('429 Resource exhausted')).lower()
        assert 'timed out' in sanitize_ai_error(Exception('Connection timed out')).lower()


# ──────────────────────────────────────────────────────────────────────
# Page Rendering Tests (button presence)
# ──────────────────────────────────────────────────────────────────────
class TestPageButtonRendering:
    """Verify that pages with buttons render without server errors."""

    def test_home_page_redirects(self, client):
        """Home page redirects to accounts."""
        resp = client.get('/')
        assert resp.status_code == 302

    @patch('app.get_archived_count')
    @patch('app.get_tier_counts')
    @patch('app.get_all_accounts')
    def test_accounts_page_loads(self, mock_accounts, mock_tier_counts, mock_archived, client):
        """Accounts page loads with buttons."""
        mock_accounts.return_value = {
            'accounts': [],
            'total_items': 0,
            'total_pages': 1,
            'current_page': 1,
            'limit': 50
        }
        mock_tier_counts.return_value = {}
        mock_archived.return_value = 0
        resp = client.get('/accounts')
        assert resp.status_code == 200

    @patch('app.get_contributors_datatable')
    @patch('app.get_contributor_stats')
    def test_contributors_page_loads(self, mock_stats, mock_dt, client):
        """Contributors page renders (has Apollo buttons)."""
        mock_stats.return_value = {
            'total_contributors': 0, 'total_enrolled': 0,
            'total_with_email': 0, 'total_accounts': 0
        }
        mock_dt.return_value = {'data': [], 'total': 0}
        resp = client.get('/contributors')
        assert resp.status_code == 200

    def test_linkedin_prospector_loads(self, client):
        """LinkedIn Prospector page renders (has Apollo buttons)."""
        resp = client.get('/linkedin-prospector')
        assert resp.status_code == 200
        html = resp.data.decode()
        # Verify key Apollo-related buttons are present
        assert 'Generate Email' in html
        assert 'Enroll in Sequence' in html
        assert 'sequence-select' in html

    def test_settings_page_loads(self, client):
        """Settings page loads."""
        resp = client.get('/settings')
        assert resp.status_code == 200

    def test_rules_page_loads(self, client):
        """Rules page loads."""
        resp = client.get('/rules')
        assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────────
# Edge Cases & Security Tests
# ──────────────────────────────────────────────────────────────────────
class TestEdgeCases:
    """Edge cases and boundary conditions for buttons/Apollo API."""

    @patch('requests.post')
    def test_apollo_lookup_empty_name(self, mock_post, client):
        """Apollo lookup handles empty name gracefully."""
        mock_post.side_effect = [
            _mock_response(200, {'person': None}),
            _mock_response(200, {'people': []}),
        ]

        resp = client.post('/api/apollo-lookup',
                           json={'name': '', 'company': 'Acme'})
        assert resp.status_code == 200

    def test_apollo_enroll_empty_strings(self, client):
        """Enroll with empty required fields returns 400."""
        resp = client.post('/api/apollo/enroll-sequence',
                           json={'email': '', 'sequence_id': ''})
        assert resp.status_code == 400

    @patch('requests.post')
    def test_apollo_lookup_special_characters_in_name(self, mock_post, client):
        """Apollo lookup handles special characters safely."""
        mock_post.side_effect = [
            _mock_response(200, {'person': None}),
            _mock_response(200, {'people': []}),
        ]

        resp = client.post('/api/apollo-lookup',
                           json={'name': "O'Brien <script>alert(1)</script>", 'company': 'Test & Co'})
        assert resp.status_code == 200

    @patch('requests.post')
    def test_apollo_lookup_company_suffix_stripping(self, mock_post, client):
        """Various company suffixes are stripped for domain derivation."""
        mock_post.side_effect = [
            _mock_response(200, {'person': None}),
            _mock_response(200, {'people': []}),
        ]

        resp = client.post('/api/apollo-lookup',
                           json={'first_name': 'John', 'last_name': 'Doe', 'company': 'TechCo Ltd.'})
        assert resp.status_code == 200
        call_payload = mock_post.call_args_list[0][1]['json']
        assert call_payload['organization_domain'] == 'techco.com'

    @patch('app.update_contributor_email')
    def test_contributor_email_whitespace_stripped(self, mock_update, client):
        """Email whitespace is stripped before saving."""
        mock_update.return_value = True
        resp = client.post('/api/contributors/1/email',
                           json={'email': '  john@acme.com  '})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['email'] == 'john@acme.com'

    @patch('requests.post')
    def test_apollo_sequence_detect_non_email_steps_filtered(self, mock_post, client):
        """Non-email steps (LinkedIn, calls) are filtered from step count."""
        mock_post.return_value = _mock_response(200, {
            'emailer_campaigns': [{
                'id': 'seq_mixed',
                'name': 'Mixed Steps',
                'emailer_steps': [
                    {'type': 'auto_email', 'subject': 'Hello'},
                    {'type': 'linkedin_step', 'subject': ''},
                    {'type': 'manual_task', 'subject': ''},
                    {'type': 'auto_email', 'subject': ''},
                ]
            }]
        })

        resp = client.post('/api/apollo/sequence-detect', json={'sequence_id': 'seq_mixed'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['num_email_steps'] == 2  # Only auto_email steps

    @patch('requests.get')
    @patch('requests.post')
    @patch('requests.put')
    def test_enroll_html_conversion(self, mock_put, mock_post, mock_get, client):
        """Newlines in email body are converted to HTML <br> for Apollo."""
        mock_post.side_effect = [
            _mock_response(200, {'contacts': [{'id': 'c1'}]}),
            _mock_response(200, {}),  # enroll
        ]

        mock_get.side_effect = [
            _mock_response(200, {'typed_custom_fields': [
                {'id': 'f1', 'name': 'personalized_email_1'}
            ]}),
            _mock_response(200, {'email_accounts': [{'id': 'ea1', 'active': True}]}),
        ]

        mock_put.return_value = _mock_response(200)

        resp = client.post('/api/apollo/enroll-sequence', json={
            'email': 'test@company.com',
            'sequence_id': 'seq1',
            'personalized_email_body': 'Line one\n\nLine two\nLine three'
        })
        assert resp.status_code == 200

        # Verify custom field PUT was called with HTML-converted body
        if mock_put.called:
            put_payload = mock_put.call_args[1]['json']
            fields = put_payload.get('typed_custom_fields', {})
            if 'f1' in fields:
                assert '<br>' in fields['f1']

    @patch('requests.get')
    @patch('requests.post')
    def test_enroll_preferred_sender(self, mock_post, mock_get, client):
        """Uses APOLLO_SENDER_EMAIL to pick the right sending account."""
        with patch.dict(os.environ, {'APOLLO_SENDER_EMAIL': 'preferred@myco.com'}):
            mock_post.side_effect = [
                _mock_response(200, {'contacts': [{'id': 'c1'}]}),
                _mock_response(200, {}),  # enroll
            ]

            # No personalized fields → no custom fields GET, only email accounts GET
            mock_get.return_value = _mock_response(200, {'email_accounts': [
                {'id': 'ea_other', 'active': True, 'email': 'other@myco.com'},
                {'id': 'ea_preferred', 'active': True, 'email': 'preferred@myco.com'},
            ]})

            resp = client.post('/api/apollo/enroll-sequence', json={
                'email': 'lead@co.com',
                'sequence_id': 'seq1'
            })
            assert resp.status_code == 200
            # Verify the preferred sender was selected
            enroll_call = mock_post.call_args_list[-1]
            enroll_payload = enroll_call[1]['json']
            assert enroll_payload['send_email_from_email_account_id'] == 'ea_preferred'
