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


# ---------------------------------------------------------------------------
# E2E: Sequence Sync & Dropdown Consistency
# ---------------------------------------------------------------------------

class TestSequenceSyncEndpoint:
    """Test that POST /api/sequence-mappings/sync pulls from Apollo and
    populates the sequence_mappings table."""

    def test_sync_populates_sequence_mappings(self, flask_app, test_db):
        """Calling POST /api/sequence-mappings/sync should upsert Apollo
        sequences into the sequence_mappings table."""
        apollo_response = {
            'emailer_campaigns': [
                {
                    'id': 'seq_sync_001',
                    'name': 'Ty - Direct - Upsell Disco',
                    'active': True,
                    'num_steps': 4,
                    'created_at': '2025-06-01',
                    'user': {'first_name': 'Ty', 'last_name': 'Smith'},
                },
                {
                    'id': 'seq_sync_002',
                    'name': 'ZH_CN_outbound_gaming',
                    'active': False,
                    'num_steps': 3,
                    'created_at': '2025-07-15',
                    'user': {'first_name': 'Jane', 'last_name': 'Doe'},
                },
            ],
            'pagination': {'total_entries': 2, 'total_pages': 1, 'page': 1},
        }

        with patch('requests.post', return_value=_mock_apollo_response(200, apollo_response)):
            resp = flask_app.post('/api/sequence-mappings/sync')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['synced'] == 2

        # Verify the mappings are in the database
        import database
        mappings = database.get_all_sequence_mappings()
        seq_ids = [m['sequence_id'] for m in mappings]
        assert 'seq_sync_001' in seq_ids
        assert 'seq_sync_002' in seq_ids

        # Verify active/inactive status is preserved
        sync_001 = next(m for m in mappings if m['sequence_id'] == 'seq_sync_001')
        sync_002 = next(m for m in mappings if m['sequence_id'] == 'seq_sync_002')
        assert sync_001['active'] == 1  # active
        assert sync_002['active'] == 0  # paused

    def test_sync_updates_existing_sequences(self, flask_app, test_db):
        """If a sequence already exists in the mapping table, sync should
        update its name and step count."""
        import database

        # Pre-seed a mapping
        database.upsert_sequence_mapping(
            sequence_id='seq_existing',
            sequence_name='Old Name',
            num_steps=2,
            active=True,
        )

        apollo_response = {
            'emailer_campaigns': [
                {
                    'id': 'seq_existing',
                    'name': 'Updated Name',
                    'active': False,
                    'num_steps': 5,
                },
            ],
            'pagination': {'total_entries': 1, 'total_pages': 1, 'page': 1},
        }

        with patch('requests.post', return_value=_mock_apollo_response(200, apollo_response)):
            resp = flask_app.post('/api/sequence-mappings/sync')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['synced'] == 1

        mappings = database.get_all_sequence_mappings()
        updated = next(m for m in mappings if m['sequence_id'] == 'seq_existing')
        assert updated['sequence_name'] == 'Updated Name'
        assert updated['num_steps'] == 5
        assert updated['active'] == 0  # now paused

    def test_sync_handles_pagination(self, flask_app, test_db):
        """Sync should paginate through all Apollo sequence pages."""
        page1_response = {
            'emailer_campaigns': [
                {'id': 'seq_page1', 'name': 'Page 1 Seq', 'active': True, 'num_steps': 2},
            ],
            'pagination': {'total_entries': 2, 'total_pages': 2, 'page': 1},
        }
        page2_response = {
            'emailer_campaigns': [
                {'id': 'seq_page2', 'name': 'Page 2 Seq', 'active': True, 'num_steps': 3},
            ],
            'pagination': {'total_entries': 2, 'total_pages': 2, 'page': 2},
        }

        call_count = [0]
        def mock_paginated_post(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_apollo_response(200, page1_response)
            return _mock_apollo_response(200, page2_response)

        with patch('requests.post', side_effect=mock_paginated_post):
            resp = flask_app.post('/api/sequence-mappings/sync')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['synced'] == 2

        import database
        mappings = database.get_all_sequence_mappings()
        seq_ids = [m['sequence_id'] for m in mappings]
        assert 'seq_page1' in seq_ids
        assert 'seq_page2' in seq_ids

    def test_sync_without_api_key_returns_error(self, flask_app, test_db, monkeypatch):
        """Sync should return an error if APOLLO_API_KEY is not set."""
        monkeypatch.delenv('APOLLO_API_KEY', raising=False)

        resp = flask_app.post('/api/sequence-mappings/sync')
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'not configured' in data['message']


class TestSequenceDropdownConsistency:
    """Test that the /api/apollo/sequences dropdown endpoint returns
    sequences from the sequence_mappings table (not Apollo directly),
    and that it matches what /api/sequence-mappings/enabled returns."""

    def test_dropdown_returns_enabled_mappings_only(self, flask_app, test_db):
        """GET /api/apollo/sequences should return only enabled sequences
        from the mapping table, not hit Apollo API at all."""
        import database

        # Create two mappings: one enabled, one disabled
        result_on = database.upsert_sequence_mapping(
            sequence_id='seq_drop_on',
            sequence_name='Enabled Seq',
            num_steps=3,
            active=True,
        )
        result_off = database.upsert_sequence_mapping(
            sequence_id='seq_drop_off',
            sequence_name='Disabled Seq',
            num_steps=2,
            active=True,
        )
        database.toggle_sequence_mapping_enabled(result_on['id'], True)
        # seq_drop_off stays disabled (enabled=0 by default)

        # Call the dropdown endpoint — should NOT hit Apollo API
        resp = flask_app.get('/api/apollo/sequences')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'

        seq_ids = [s['id'] for s in data['sequences']]
        assert 'seq_drop_on' in seq_ids, 'Enabled sequence should appear in dropdown'
        assert 'seq_drop_off' not in seq_ids, 'Disabled sequence should NOT appear in dropdown'

    def test_dropdown_matches_enabled_endpoint(self, flask_app, test_db):
        """GET /api/apollo/sequences and GET /api/sequence-mappings/enabled
        should return the same set of sequences."""
        import database

        # Create and enable two sequences
        for i, name in enumerate(['Alpha', 'Beta']):
            result = database.upsert_sequence_mapping(
                sequence_id=f'seq_match_{i}',
                sequence_name=f'{name} Sequence',
                num_steps=i + 2,
                active=True,
            )
            database.toggle_sequence_mapping_enabled(result['id'], True)

        # Fetch from both endpoints
        apollo_resp = flask_app.get('/api/apollo/sequences')
        mapping_resp = flask_app.get('/api/sequence-mappings/enabled')

        apollo_data = apollo_resp.get_json()
        mapping_data = mapping_resp.get_json()

        # Both should return the same sequence IDs
        apollo_ids = sorted(s['id'] for s in apollo_data['sequences'])
        mapping_ids = sorted(
            s.get('sequence_id', s.get('apollo_sequence_id', ''))
            for s in mapping_data['sequences']
        )

        assert apollo_ids == mapping_ids, (
            f'Dropdown and mapping endpoints return different sequences: '
            f'{apollo_ids} vs {mapping_ids}'
        )

    def test_dropdown_does_not_call_apollo_api(self, flask_app, test_db):
        """GET /api/apollo/sequences should NOT make any external HTTP calls.
        It should read from the local database only."""
        import database

        result = database.upsert_sequence_mapping(
            sequence_id='seq_local',
            sequence_name='Local Only',
            num_steps=2,
            active=True,
        )
        database.toggle_sequence_mapping_enabled(result['id'], True)

        with patch('requests.post') as mock_post, \
             patch('requests.get') as mock_get:
            resp = flask_app.get('/api/apollo/sequences')

            # No external HTTP calls should have been made
            mock_post.assert_not_called()
            mock_get.assert_not_called()

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'success'
        assert len(data['sequences']) >= 1

    def test_dropdown_shows_paused_sequences_with_status(self, flask_app, test_db):
        """Paused sequences (active=False) should appear in the dropdown
        with their active status set to False so the UI can mark them."""
        import database

        # Create an enabled but paused sequence
        result = database.upsert_sequence_mapping(
            sequence_id='seq_paused',
            sequence_name='Paused Outbound',
            num_steps=4,
            active=False,  # paused in Apollo
        )
        database.toggle_sequence_mapping_enabled(result['id'], True)

        resp = flask_app.get('/api/apollo/sequences')
        data = resp.get_json()
        paused_seq = next(
            (s for s in data['sequences'] if s['id'] == 'seq_paused'),
            None,
        )
        assert paused_seq is not None, 'Paused sequence should appear in dropdown'
        assert paused_seq['active'] is False, 'Paused sequence should have active=False'
        assert paused_seq['name'] == 'Paused Outbound'

    def test_dropdown_empty_when_no_enabled_sequences(self, flask_app, test_db):
        """When no sequences are enabled, the dropdown should return an
        empty list (not an error)."""
        import database

        # Create a mapping but don't enable it
        database.upsert_sequence_mapping(
            sequence_id='seq_not_enabled',
            sequence_name='Not Enabled',
            num_steps=2,
            active=True,
        )

        resp = flask_app.get('/api/apollo/sequences')
        data = resp.get_json()
        assert data['status'] == 'success'
        assert data['sequences'] == []
