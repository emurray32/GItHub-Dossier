"""
End-to-end test: RepoRadar → Scorecard → Apollo Lookup → Sequence Enrollment.

Simulates the full BDR workflow through the Flask test client.
External API calls (Apollo, OpenAI) are mocked; everything else runs for real
against a live SQLite test database.
"""
import json
import pytest
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_account(test_db, company='AcmeCorp', github_org='acmecorp', tier=2):
    """Insert a monitored account directly and return its ID."""
    import database
    conn = database.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO monitored_accounts
           (company_name, github_org, current_tier, scan_status, evidence_summary)
           VALUES (?, ?, ?, 'idle', 'Dependency injection detected')""",
        (company, github_org, tier),
    )
    conn.commit()
    account_id = cur.lastrowid
    conn.close()
    return account_id


def _seed_report(test_db, company='AcmeCorp', github_org='acmecorp'):
    """Insert a scan report and return its ID."""
    import database
    conn = database.get_db_connection()
    cur = conn.cursor()
    scan_data = json.dumps({
        'company_name': company,
        'org_login': github_org,
        'signals': [
            {'type': 'dependency_injection', 'Evidence': 'Found react-i18next'},
            {'type': 'ghost_branch', 'Evidence': 'Branch feature/i18n'},
        ],
        'signal_summary': {
            'dependency_injection': {'count': 1, 'hits': [{'goldilocks_status': 'preparing'}]},
            'ghost_branch': {'count': 1, 'hits': []},
        },
    })
    cur.execute(
        """INSERT INTO reports
           (company_name, github_org, scan_data, signals_found)
           VALUES (?, ?, ?, 2)""",
        (company, github_org, scan_data),
    )
    conn.commit()
    report_id = cur.lastrowid
    conn.close()
    return report_id


def _seed_scorecard(test_db, account_id, company='AcmeCorp'):
    """Insert a scorecard score for the account."""
    import database
    conn = database.get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO scorecard_scores
           (account_id, company_name, total_score, lang_score, systems_score,
            revenue_score, locale_count, cohort, apollo_status)
           VALUES (?, ?, 85, 30, 25, 30, 12, 'A', 'not_enrolled')""",
        (account_id, company),
    )
    conn.commit()
    conn.close()


def _seed_sequence_mapping(test_db):
    """Insert a sequence mapping and return the sequence_id."""
    import database
    database.upsert_sequence_mapping(
        sequence_id='seq_e2e_001',
        sequence_name='E2E Test Sequence - Preparing',
        sequence_config='threaded_4',
        num_steps=4,
        active=True,
        owner_name='eric@phrase.com',
    )
    return 'seq_e2e_001'


def _mock_apollo_response(status_code=200, json_data=None):
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = json.dumps(json_data or {})
    resp.ok = 200 <= status_code < 300
    return resp


# ---------------------------------------------------------------------------
# E2E Test: Full BDR Workflow
# ---------------------------------------------------------------------------

class TestFullEnrollmentWorkflow:
    """
    Simulates the complete BDR journey:
      1. View accounts in RepoRadar
      2. Check scorecard for qualified leads
      3. Look up a contact via Apollo
      4. Enroll the contact in an Apollo sequence
    """

    def test_step1_accounts_page_loads(self, flask_app, test_db):
        """Step 1: BDR opens RepoRadar and sees the company."""
        account_id = _seed_account(test_db, 'AcmeCorp', 'acmecorp', tier=2)

        # Load the accounts page (HTML)
        resp = flask_app.get('/accounts')
        assert resp.status_code == 200
        assert b'RepoRadar' in resp.data or b'accounts' in resp.data.lower()

    def test_step2_accounts_api_returns_company(self, flask_app, test_db):
        """Step 2: The datatable API returns AcmeCorp in the list."""
        account_id = _seed_account(test_db, 'AcmeCorp', 'acmecorp', tier=2)

        resp = flask_app.get('/api/accounts/datatable?draw=1&start=0&length=25')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['recordsTotal'] >= 1

        company_names = [row['company_name'] for row in data['data']]
        assert 'AcmeCorp' in company_names

    def test_step3_scorecard_shows_qualified_lead(self, flask_app, test_db):
        """Step 3: Scorecard API shows AcmeCorp as a qualified lead."""
        account_id = _seed_account(test_db, 'AcmeCorp', 'acmecorp', tier=2)
        _seed_scorecard(test_db, account_id, 'AcmeCorp')

        resp = flask_app.get('/api/scorecard/datatable?draw=1&start=0&length=25')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['recordsTotal'] >= 1

        # Find AcmeCorp in the scorecard
        acme = next((r for r in data['data'] if r['company_name'] == 'AcmeCorp'), None)
        assert acme is not None, 'AcmeCorp not found in scorecard'
        assert acme['total_score'] == 85
        assert acme['cohort'] == 'A'
        assert acme['apollo_status'] == 'not_enrolled'

    def test_step4_apollo_lookup_finds_contact(self, flask_app, test_db):
        """Step 4: BDR looks up a contact at AcmeCorp via Apollo."""
        _seed_account(test_db, 'AcmeCorp', 'acmecorp', tier=2)

        apollo_match = {
            'person': {
                'id': 'person_e2e_001',
                'first_name': 'Jane',
                'last_name': 'Smith',
                'name': 'Jane Smith',
                'email': 'jane.smith@acmecorp.com',
                'email_status': 'verified',
                'title': 'VP Engineering',
                'linkedin_url': 'https://linkedin.com/in/janesmith',
                'organization': {'name': 'AcmeCorp', 'website_url': 'https://acmecorp.com'},
            }
        }

        with patch('requests.post', return_value=_mock_apollo_response(200, apollo_match)):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane',
                'last_name': 'Smith',
                'domain': 'acmecorp.com',
                'company': 'AcmeCorp',
            })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('email') == 'jane.smith@acmecorp.com'
        assert data.get('title') == 'VP Engineering'

    def test_step5_enroll_contact_in_sequence(self, flask_app, test_db):
        """Step 5: BDR enrolls the contact in an Apollo sequence."""
        account_id = _seed_account(test_db, 'AcmeCorp', 'acmecorp', tier=2)
        _seed_scorecard(test_db, account_id, 'AcmeCorp')
        seq_id = _seed_sequence_mapping(test_db)

        # Mock all Apollo API calls for enrollment
        def mock_apollo_calls(*args, **kwargs):
            url = args[0] if args else kwargs.get('url', '')
            json_body = kwargs.get('json', {})

            # Custom fields lookup
            if 'typed_custom_fields' in url:
                return _mock_apollo_response(200, {
                    'typed_custom_fields': [
                        {'id': 'cf_sub1', 'name': 'Personalized Subject 1'},
                        {'id': 'cf_email1', 'name': 'Personalized Email 1'},
                        {'id': 'cf_email2', 'name': 'Personalized Email 2'},
                        {'id': 'cf_email3', 'name': 'Personalized Email 3'},
                        {'id': 'cf_email4', 'name': 'Personalized Email 4'},
                    ]
                })
            # Contact search
            if 'contacts/search' in url:
                return _mock_apollo_response(200, {'contacts': []})
            # Contact create
            if '/contacts' in url and not ('search' in url or 'add_contact_ids' in url):
                return _mock_apollo_response(200, {
                    'contact': {
                        'id': 'contact_e2e_001',
                        'email': 'jane.smith@acmecorp.com',
                    }
                })
            # Email accounts
            if 'email_accounts' in url:
                return _mock_apollo_response(200, {
                    'email_accounts': [
                        {'id': 'ea_001', 'email': 'eric@phrase.com', 'active': True},
                    ]
                })
            # Sequence enrollment
            if 'add_contact_ids' in url:
                return _mock_apollo_response(200, {
                    'contacts': [{'id': 'contact_e2e_001'}]
                })
            return _mock_apollo_response(200, {})

        with patch('requests.post', side_effect=mock_apollo_calls), \
             patch('requests.get', side_effect=mock_apollo_calls), \
             patch('requests.put', side_effect=mock_apollo_calls):
            resp = flask_app.post('/api/scorecard/enroll', json={
                'account_id': account_id,
                'email': 'jane.smith@acmecorp.com',
                'first_name': 'Jane',
                'last_name': 'Smith',
                'sequence_id': seq_id,
                'sequence_name': 'E2E Test Sequence - Preparing',
                'company_name': 'AcmeCorp',
                'personalized_subject_1': 'Your i18n journey at AcmeCorp',
                'personalized_email_1': '<p>Hi Jane, I noticed react-i18next in your repo...</p>',
                'personalized_email_2': '<p>Following up on localization at AcmeCorp...</p>',
                'personalized_email_3': '<p>Quick check-in on i18n progress...</p>',
                'personalized_email_4': '<p>Last note about Phrase for AcmeCorp...</p>',
            })

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert 'contact_id' in data or 'enrolled' in data.get('message', '').lower()

    def test_step6_scorecard_updated_after_enrollment(self, flask_app, test_db):
        """Step 6: After enrollment, scorecard shows status as 'enrolled'."""
        account_id = _seed_account(test_db, 'AcmeCorp', 'acmecorp', tier=2)
        _seed_scorecard(test_db, account_id, 'AcmeCorp')
        seq_id = _seed_sequence_mapping(test_db)

        # Enroll (same as step 5)
        def mock_apollo_calls(*args, **kwargs):
            url = args[0] if args else kwargs.get('url', '')
            if 'typed_custom_fields' in url:
                return _mock_apollo_response(200, {'typed_custom_fields': [
                    {'id': 'cf_sub1', 'name': 'Personalized Subject 1'},
                    {'id': 'cf_email1', 'name': 'Personalized Email 1'},
                    {'id': 'cf_email2', 'name': 'Personalized Email 2'},
                    {'id': 'cf_email3', 'name': 'Personalized Email 3'},
                    {'id': 'cf_email4', 'name': 'Personalized Email 4'},
                ]})
            if 'contacts/search' in url:
                return _mock_apollo_response(200, {'contacts': []})
            if '/contacts' in url and not ('search' in url or 'add_contact_ids' in url):
                return _mock_apollo_response(200, {'contact': {'id': 'contact_e2e_001', 'email': 'jane@acme.com'}})
            if 'email_accounts' in url:
                return _mock_apollo_response(200, {'email_accounts': [{'id': 'ea1', 'email': 'eric@phrase.com', 'active': True}]})
            if 'add_contact_ids' in url:
                return _mock_apollo_response(200, {'contacts': [{'id': 'contact_e2e_001'}]})
            return _mock_apollo_response(200, {})

        with patch('requests.post', side_effect=mock_apollo_calls), \
             patch('requests.get', side_effect=mock_apollo_calls), \
             patch('requests.put', side_effect=mock_apollo_calls):
            flask_app.post('/api/scorecard/enroll', json={
                'account_id': account_id,
                'email': 'jane@acmecorp.com',
                'first_name': 'Jane',
                'last_name': 'Smith',
                'sequence_id': seq_id,
                'sequence_name': 'E2E Test Sequence - Preparing',
                'company_name': 'AcmeCorp',
                'personalized_subject_1': 'Subject',
                'personalized_email_1': 'Body 1',
            })

        # Verify scorecard reflects enrollment
        resp = flask_app.get('/api/scorecard/datatable?draw=1&start=0&length=25')
        data = resp.get_json()
        acme = next((r for r in data['data'] if r['company_name'] == 'AcmeCorp'), None)
        assert acme is not None
        assert acme['apollo_status'] == 'enrolled'
        assert acme['sequence_name'] == 'E2E Test Sequence - Preparing'

    def test_step7_audit_log_recorded(self, flask_app, test_db):
        """Step 7: Enrollment creates an audit log entry."""
        account_id = _seed_account(test_db, 'AuditCorp', 'auditcorp', tier=2)
        _seed_scorecard(test_db, account_id, 'AuditCorp')
        _seed_sequence_mapping(test_db)

        def mock_apollo_calls(*args, **kwargs):
            url = args[0] if args else kwargs.get('url', '')
            if 'typed_custom_fields' in url:
                return _mock_apollo_response(200, {'typed_custom_fields': [
                    {'id': 'cf1', 'name': 'Personalized Subject 1'},
                    {'id': 'cf2', 'name': 'Personalized Email 1'},
                ]})
            if 'contacts/search' in url:
                return _mock_apollo_response(200, {'contacts': []})
            if '/contacts' in url and 'add_contact_ids' not in url and 'search' not in url:
                return _mock_apollo_response(200, {'contact': {'id': 'c1', 'email': 'test@auditcorp.com'}})
            if 'email_accounts' in url:
                return _mock_apollo_response(200, {'email_accounts': [{'id': 'e1', 'email': 'x@phrase.com', 'active': True}]})
            if 'add_contact_ids' in url:
                return _mock_apollo_response(200, {'contacts': [{'id': 'c1'}]})
            return _mock_apollo_response(200, {})

        with patch('requests.post', side_effect=mock_apollo_calls), \
             patch('requests.get', side_effect=mock_apollo_calls), \
             patch('requests.put', side_effect=mock_apollo_calls):
            flask_app.post('/api/scorecard/enroll', json={
                'account_id': account_id,
                'email': 'test@auditcorp.com',
                'first_name': 'Test',
                'last_name': 'User',
                'sequence_id': 'seq_e2e_001',
                'sequence_name': 'E2E Test Sequence - Preparing',
                'company_name': 'AuditCorp',
                'personalized_subject_1': 'Subject',
                'personalized_email_1': 'Body',
            })

        # Check audit log
        import database
        conn = database.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM audit_log WHERE action = 'apollo_enrollment' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()

        assert row is not None, 'Expected an audit log entry for apollo_enrollment'
        assert 'test@auditcorp.com' in row['details']
        assert 'AuditCorp' in row['details']


# ---------------------------------------------------------------------------
# E2E: Apollo Lookup → Filter Personal Emails → Enrollment
# ---------------------------------------------------------------------------

class TestLookupThenEnrollWorkflow:
    """Test the lookup-then-enroll pattern BDRs use on the scorecard."""

    def test_personal_email_blocked_from_enrollment(self, flask_app, test_db):
        """A personal email found via Apollo should not be enrollable."""
        _seed_account(test_db, 'PersonalTest', 'personaltest', tier=2)

        gmail_response = {
            'person': {
                'id': 'p1',
                'email': 'user@gmail.com',
                'email_status': 'verified',
                'name': 'Test User',
                'title': 'Engineer',
                'linkedin_url': '',
                'organization': {'name': 'PersonalTest'},
            }
        }

        with patch('requests.post', return_value=_mock_apollo_response(200, gmail_response)):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Test',
                'last_name': 'User',
                'domain': 'personaltest.com',
                'company': 'PersonalTest',
            })

        data = resp.get_json()
        # Personal email should be filtered out
        assert data.get('email', '') == ''

    def test_corporate_email_passes_through(self, flask_app, test_db):
        """A corporate email should pass through for enrollment."""
        _seed_account(test_db, 'CorpTest', 'corptest', tier=2)

        corp_response = {
            'person': {
                'id': 'p2',
                'first_name': 'Jane',
                'last_name': 'Doe',
                'email': 'jane@corptest.com',
                'email_status': 'verified',
                'name': 'Jane Doe',
                'title': 'CTO',
                'linkedin_url': 'https://linkedin.com/in/janedoe',
                'organization': {'name': 'CorpTest', 'website_url': 'https://corptest.com'},
            }
        }

        with patch('requests.post', return_value=_mock_apollo_response(200, corp_response)):
            resp = flask_app.post('/api/apollo-lookup', json={
                'first_name': 'Jane',
                'last_name': 'Doe',
                'domain': 'corptest.com',
                'company': 'CorpTest',
            })

        data = resp.get_json()
        assert data.get('email') == 'jane@corptest.com'
        assert data.get('title') == 'CTO'


# ---------------------------------------------------------------------------
# E2E: Sequence Mapping Workflow
# ---------------------------------------------------------------------------

class TestSequenceMappingWorkflow:
    """Test that sequence mappings are properly used during enrollment."""

    def test_mapping_sequences_page_loads(self, flask_app, test_db):
        """The mapping sequences page should load."""
        resp = flask_app.get('/mapping-sequences')
        assert resp.status_code == 200

    def test_sequences_api_returns_configured_sequences(self, flask_app, test_db):
        """API returns sequence mappings from the database."""
        _seed_sequence_mapping(test_db)

        # Use search endpoint (no enabled filter) to find our mapping
        resp = flask_app.get('/api/sequence-mappings/search?q=E2E')
        assert resp.status_code == 200
        data = resp.get_json()

        # Find our E2E test sequence
        mappings = data.get('results', [])
        assert len(mappings) >= 1
        e2e_seq = next((m for m in mappings if m.get('sequence_id') == 'seq_e2e_001'), None)
        assert e2e_seq is not None
        assert e2e_seq['sequence_name'] == 'E2E Test Sequence - Preparing'
        assert e2e_seq['num_steps'] == 4


# ---------------------------------------------------------------------------
# E2E: Full Scan → Tier → Scorecard Pipeline
# ---------------------------------------------------------------------------

class TestScanToScorecardPipeline:
    """Test that a scanned company flows through to the scorecard."""

    def test_account_with_report_shows_in_scorecard(self, flask_app, test_db):
        """An account with a report and scorecard score appears in the scorecard API."""
        account_id = _seed_account(test_db, 'PipelineCorp', 'pipelinecorp', tier=2)
        _seed_report(test_db, 'PipelineCorp', 'pipelinecorp')
        _seed_scorecard(test_db, account_id, 'PipelineCorp')

        # Verify it appears in both accounts and scorecard
        acct_resp = flask_app.get('/api/accounts/datatable?draw=1&start=0&length=50')
        acct_data = acct_resp.get_json()
        assert any(r['company_name'] == 'PipelineCorp' for r in acct_data['data'])

        score_resp = flask_app.get('/api/scorecard/datatable?draw=1&start=0&length=50')
        score_data = score_resp.get_json()
        pipeline = next((r for r in score_data['data'] if r['company_name'] == 'PipelineCorp'), None)
        assert pipeline is not None
        assert pipeline['total_score'] == 85

    def test_tier_filter_on_accounts(self, flask_app, test_db):
        """Filtering by tier 2 (Preparing) shows only tier 2 accounts."""
        _seed_account(test_db, 'Tier2Corp', 'tier2corp', tier=2)
        _seed_account(test_db, 'Tier0Corp', 'tier0corp', tier=0)

        resp = flask_app.get('/api/accounts/datatable?draw=1&start=0&length=50&tier=2')
        data = resp.get_json()

        names = [r['company_name'] for r in data['data']]
        assert 'Tier2Corp' in names
        assert 'Tier0Corp' not in names
