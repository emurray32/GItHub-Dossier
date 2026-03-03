"""Tests for Apollo API integration routes (mocked HTTP)."""
import json
import pytest
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.unit


def _mock_response(status_code=200, json_data=None, text=''):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or json.dumps(json_data or {})
    return resp


class TestApolloLookup:
    """Test /api/apollo-lookup route."""

    def test_lookup_success(self, flask_app, apollo_person_response):
        with patch('requests.post', return_value=_mock_response(200, apollo_person_response)):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane',
                'last_name': 'Smith',
                'domain': 'targetcorp.com',
                'company': 'TargetCorp',
            })
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['email'] == 'jane.smith@targetcorp.com'
        assert data['title'] == 'VP Engineering'

    def test_lookup_no_data(self, flask_app):
        resp = flask_app.post('/api/apollo-lookup', json=None,
                              content_type='application/json')
        assert resp.status_code == 400

    def test_lookup_no_api_key(self, flask_app, monkeypatch):
        monkeypatch.delenv('APOLLO_API_KEY', raising=False)
        # Also clear the os.environ cache inside the route
        with patch.dict('os.environ', {'APOLLO_API_KEY': ''}, clear=False):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane', 'last_name': 'Smith', 'domain': 'test.com'
            })
        assert resp.status_code == 500

    def test_lookup_domain_mismatch(self, flask_app):
        mismatch_response = {
            'person': {
                'id': 'p1',
                'email': 'jane@othercorp.com',
                'email_status': 'verified',
                'name': 'Jane Smith',
                'title': 'Engineer',
                'linkedin_url': '',
                'organization': {'name': 'OtherCorp'},
            }
        }
        with patch('requests.post', return_value=_mock_response(200, mismatch_response)):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane',
                'last_name': 'Smith',
                'domain': 'targetcorp.com',
                'company': 'TargetCorp',
            })
        data = resp.get_json()
        assert data['status'] == 'domain_mismatch'

    def test_lookup_fallback_to_search(self, flask_app, apollo_search_response):
        """When match API returns no person, falls back to people search."""
        no_person = _mock_response(200, {'person': None})
        search_hit = _mock_response(200, apollo_search_response)

        with patch('requests.post', side_effect=[no_person, search_hit]):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'John',
                'last_name': 'Doe',
                'domain': 'targetcorp.com',
                'company': 'TargetCorp',
            })
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['email'] == 'john.doe@targetcorp.com'

    def test_lookup_not_found(self, flask_app):
        no_person = _mock_response(200, {'person': None})
        no_search = _mock_response(200, {'people': []})
        with patch('requests.post', side_effect=[no_person, no_search]):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Nobody',
                'last_name': 'Exists',
                'domain': 'nowhere.com',
            })
        data = resp.get_json()
        assert data['status'] == 'not_found'

    def test_lookup_name_parsing(self, flask_app, apollo_person_response):
        """When only 'name' is provided, it should be split into first/last."""
        with patch('requests.post', return_value=_mock_response(200, apollo_person_response)) as mock_post:
            flask_app.post('/api/apollo-lookup', json={
                'name': 'Jane Smith',
                'domain': 'targetcorp.com',
            })
        # Verify the payload sent to Apollo
        call_args = mock_post.call_args
        payload = call_args[1].get('json', call_args[0][1] if len(call_args[0]) > 1 else {})
        if 'json' in call_args[1]:
            payload = call_args[1]['json']
        assert payload.get('first_name') == 'Jane'
        assert payload.get('last_name') == 'Smith'

    def test_lookup_derives_domain_from_company(self, flask_app, apollo_person_response):
        """When domain not provided, it should be derived from company name."""
        with patch('requests.post', return_value=_mock_response(200, apollo_person_response)):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane',
                'last_name': 'Smith',
                'company': 'TargetCorp Inc.',
            })
        data = resp.get_json()
        assert data['status'] == 'success'

    def test_lookup_personal_email_filtered(self, flask_app):
        """Gmail/personal emails should be filtered out."""
        personal_response = {
            'person': {
                'id': 'p1',
                'email': 'jane.smith@gmail.com',
                'email_status': 'verified',
                'name': 'Jane Smith',
                'title': 'Engineer',
                'linkedin_url': '',
                'organization': {'name': 'TargetCorp'},
            }
        }
        with patch('requests.post', return_value=_mock_response(200, personal_response)):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane', 'last_name': 'Smith',
                'domain': 'targetcorp.com', 'company': 'TargetCorp',
            })
        data = resp.get_json()
        # Personal email filtered -> empty string
        assert data.get('email', '') == ''

    def test_lookup_exception_handling(self, flask_app):
        with patch('requests.post', side_effect=Exception('Network error')):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane', 'last_name': 'Smith', 'domain': 'test.com'
            })
        assert resp.status_code == 500
        data = resp.get_json()
        assert data['status'] == 'error'


class TestApolloSequences:
    """Test /api/apollo/sequences route.

    This endpoint now reads from the local sequence_mappings table
    (enabled sequences only) instead of hitting the Apollo API directly.
    """

    def test_list_sequences_from_mappings(self, flask_app, test_db):
        """Returns enabled sequences from the local mapping table."""
        import database
        r1 = database.upsert_sequence_mapping(
            sequence_id='seq_a1', sequence_name='Preparing - Technical',
            num_steps=4, active=True,
        )
        r2 = database.upsert_sequence_mapping(
            sequence_id='seq_a2', sequence_name='Ghost Branch',
            num_steps=2, active=True,
        )
        database.toggle_sequence_mapping_enabled(r1['id'], True)
        database.toggle_sequence_mapping_enabled(r2['id'], True)

        resp = flask_app.get('/api/apollo/sequences')
        data = resp.get_json()
        assert data['status'] == 'success'
        assert len(data['sequences']) == 2
        names = sorted(s['name'] for s in data['sequences'])
        assert 'Ghost Branch' in names
        assert 'Preparing - Technical' in names

    def test_sequences_without_api_key_still_works(self, flask_app, test_db, monkeypatch):
        """Endpoint reads from local DB, so no API key is needed."""
        monkeypatch.delenv('APOLLO_API_KEY', raising=False)
        import database
        r = database.upsert_sequence_mapping(
            sequence_id='seq_nokey', sequence_name='No Key Needed',
            num_steps=1, active=True,
        )
        database.toggle_sequence_mapping_enabled(r['id'], True)

        resp = flask_app.get('/api/apollo/sequences')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert len(data['sequences']) == 1

    def test_sequences_does_not_call_apollo(self, flask_app, test_db):
        """No external HTTP calls should be made."""
        with patch('requests.post') as mock_post:
            resp = flask_app.get('/api/apollo/sequences')
            mock_post.assert_not_called()
        assert resp.status_code == 200

    def test_sequences_only_returns_enabled(self, flask_app, test_db):
        """Disabled mappings should not appear in the dropdown."""
        import database
        r_on = database.upsert_sequence_mapping(
            sequence_id='seq_on', sequence_name='Enabled',
            num_steps=3, active=True,
        )
        database.upsert_sequence_mapping(
            sequence_id='seq_off', sequence_name='Disabled',
            num_steps=2, active=True,
        )
        database.toggle_sequence_mapping_enabled(r_on['id'], True)

        resp = flask_app.get('/api/apollo/sequences')
        data = resp.get_json()
        ids = [s['id'] for s in data['sequences']]
        assert 'seq_on' in ids
        assert 'seq_off' not in ids


class TestApolloSequenceDetect:
    """Test /api/apollo/sequence-detect route."""

    def test_detect_threaded(self, flask_app, apollo_sequences_response):
        with patch('requests.post', return_value=_mock_response(200, apollo_sequences_response)):
            resp = flask_app.post('/api/apollo/sequence-detect', json={'sequence_id': 'seq_001'})
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['detected_config'] == 'split_2x2'  # 2 distinct subjects

    def test_detect_no_sequence_id(self, flask_app):
        resp = flask_app.post('/api/apollo/sequence-detect', json={})
        assert resp.status_code == 400

    def test_detect_no_api_key(self, flask_app):
        with patch.dict('os.environ', {'APOLLO_API_KEY': ''}, clear=False):
            resp = flask_app.post('/api/apollo/sequence-detect', json={'sequence_id': 'seq_001'})
        data = resp.get_json()
        assert data['status'] == 'no_key'

    def test_detect_not_found(self, flask_app):
        empty_page = {
            'emailer_campaigns': [],
            'pagination': {'total_pages': 1, 'page': 1},
        }
        with patch('requests.post', return_value=_mock_response(200, empty_page)):
            resp = flask_app.post('/api/apollo/sequence-detect', json={'sequence_id': 'nonexistent'})
        data = resp.get_json()
        assert data['status'] == 'not_found'


class TestApolloEnrollSequence:
    """Test /api/apollo/enroll-sequence route."""

    def test_enroll_new_contact(self, flask_app, apollo_contact_create_response,
                                apollo_enroll_response, apollo_custom_fields_response,
                                apollo_email_accounts_response):
        """Full enrollment flow: search (not found) -> create -> enroll."""
        no_existing = _mock_response(200, {'contacts': []})
        create_ok = _mock_response(200, apollo_contact_create_response)
        enroll_ok = _mock_response(200, apollo_enroll_response)
        custom_fields = _mock_response(200, apollo_custom_fields_response)
        email_accounts = _mock_response(200, apollo_email_accounts_response)

        def mock_requests(method):
            def handler(url, **kwargs):
                if 'typed_custom_fields' in url:
                    return custom_fields
                if 'email_accounts' in url:
                    return email_accounts
                if 'contacts/search' in url:
                    return no_existing
                if '/v1/contacts' in url and method == 'post':
                    return create_ok
                if 'add_contact_ids' in url:
                    return enroll_ok
                return _mock_response(404)
            return handler

        with patch('requests.post', side_effect=mock_requests('post')), \
             patch('requests.get', side_effect=mock_requests('get')):
            resp = flask_app.post('/api/apollo/enroll-sequence', json={
                'email': 'jane@targetcorp.com',
                'first_name': 'Jane',
                'last_name': 'Smith',
                'sequence_id': 'seq_001',
                'company_name': 'TargetCorp',
                'personalized_subject': 'i18n in TargetCorp',
                'personalized_email_body': 'Hey Jane, noticed react-i18next in your webapp.',
            })
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['contact_id'] == 'contact_789'

    def test_enroll_missing_email(self, flask_app):
        resp = flask_app.post('/api/apollo/enroll-sequence', json={
            'sequence_id': 'seq_001',
        })
        assert resp.status_code == 400

    def test_enroll_missing_sequence_id(self, flask_app):
        resp = flask_app.post('/api/apollo/enroll-sequence', json={
            'email': 'test@test.com',
        })
        assert resp.status_code == 400

    def test_enroll_no_api_key(self, flask_app):
        with patch.dict('os.environ', {'APOLLO_API_KEY': ''}, clear=False):
            resp = flask_app.post('/api/apollo/enroll-sequence', json={
                'email': 'test@test.com', 'sequence_id': 'seq_001',
            })
        assert resp.status_code == 400

    def test_enroll_no_email_account(self, flask_app, apollo_contact_create_response,
                                     apollo_custom_fields_response):
        """When no active email account exists, enrollment should fail."""
        no_existing = _mock_response(200, {'contacts': []})
        create_ok = _mock_response(200, apollo_contact_create_response)
        custom_fields = _mock_response(200, apollo_custom_fields_response)
        no_accounts = _mock_response(200, {'email_accounts': []})

        def mock_requests(method):
            def handler(url, **kwargs):
                if 'typed_custom_fields' in url:
                    return custom_fields
                if 'email_accounts' in url:
                    return no_accounts
                if 'contacts/search' in url:
                    return no_existing
                if '/v1/contacts' in url:
                    return create_ok
                return _mock_response(404)
            return handler

        with patch('requests.post', side_effect=mock_requests('post')), \
             patch('requests.get', side_effect=mock_requests('get')):
            resp = flask_app.post('/api/apollo/enroll-sequence', json={
                'email': 'jane@targetcorp.com',
                'sequence_id': 'seq_001',
            })
        assert resp.status_code == 500
        data = resp.get_json()
        assert 'email account' in data['message'].lower()


class TestApolloRateLimiting:
    """Test handling of rate limit (429) responses."""

    def test_lookup_429(self, flask_app):
        """429 should be handled gracefully, not crash."""
        with patch('requests.post', return_value=_mock_response(429, text='Rate limited')):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane', 'last_name': 'Smith', 'domain': 'test.com'
            })
        # The current code will fall through to search fallback,
        # then also get 429 -> return not_found or error
        data = resp.get_json()
        assert data['status'] in ('not_found', 'error')


class TestApolloErrorResponses:
    """Test handling of various API error conditions."""

    def test_500_error(self, flask_app):
        with patch('requests.post', return_value=_mock_response(500, text='Internal Server Error')):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane', 'last_name': 'Smith', 'domain': 'test.com'
            })
        data = resp.get_json()
        assert data['status'] in ('not_found', 'error')

    def test_timeout_exception(self, flask_app):
        import requests as req
        with patch('requests.post', side_effect=req.exceptions.Timeout('Connection timed out')):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane', 'last_name': 'Smith', 'domain': 'test.com'
            })
        assert resp.status_code == 500
