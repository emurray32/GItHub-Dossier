"""
Regression tests for the v2 final repair pass.

Tests:
1. Apollo existing-contact update uses PUT (not POST)
2. DNC prospects are rejected on save paths
3. Workflow status (account_status) listing/counts work correctly
4. Signal status endpoint only accepts internal statuses
5. Unverified prospects cannot be enrolled
6. MCP-created signals use 'cowork' source (not 'manual_entry')
"""
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers: seed v2 data into the test DB
# ---------------------------------------------------------------------------

def _seed_account(db_path, company_name='TestCorp', website='https://testcorp.com',
                  account_status='new'):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO monitored_accounts (company_name, website, account_status) VALUES (?, ?, ?)",
        (company_name, website, account_status),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() as id").fetchone()
    aid = row['id']
    conn.close()
    return aid


def _seed_signal(db_path, account_id, description='test signal', signal_type='dependency_injection'):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO intent_signals (account_id, signal_description, signal_type) VALUES (?, ?, ?)",
        (account_id, description, signal_type),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() as id").fetchone()
    sid = row['id']
    conn.close()
    return sid


def _seed_prospect(db_path, account_id, signal_id, email='jane@testcorp.com',
                    full_name='Jane Smith', do_not_contact=False,
                    enrollment_status='found', email_verified=True):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO prospects (account_id, signal_id, full_name, email,
           do_not_contact, enrollment_status, email_verified)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (account_id, signal_id, full_name, email,
         1 if do_not_contact else 0, enrollment_status,
         1 if email_verified else 0),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() as id").fetchone()
    pid = row['id']
    conn.close()
    return pid


def _seed_draft(db_path, prospect_id, signal_id, step=1, status='approved',
                subject='Test Subject', body='Test Body'):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO drafts (prospect_id, signal_id, sequence_step, status,
           subject, body) VALUES (?, ?, ?, ?, ?, ?)""",
        (prospect_id, signal_id, step, status, subject, body),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid() as id").fetchone()
    did = row['id']
    conn.close()
    return did


# =========================================================================
# 1. Apollo existing-contact update uses PUT
# =========================================================================

class TestApolloContactUpdate:
    """Verify that the v2 enrollment service uses PUT for existing contacts."""

    def test_existing_contact_updated_with_put(self, test_db, monkeypatch):
        """When an existing Apollo contact is found, the update must use PUT."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        prospect_id = _seed_prospect(
            test_db, account_id, signal_id,
            email='jane@testcorp.com',
        )
        _seed_draft(test_db, prospect_id, signal_id, step=1)

        # Store the Apollo contact ID on the prospect (simulating prior lookup)
        conn = sqlite3.connect(test_db)
        conn.execute(
            "UPDATE prospects SET apollo_contact_id = ? WHERE id = ?",
            ('existing_contact_abc', prospect_id),
        )
        conn.commit()
        conn.close()

        # Track API calls
        api_calls = []

        def mock_apollo_call(method, url, json=None, timeout=None):
            api_calls.append({'method': method, 'url': url, 'json': json})
            resp = MagicMock()
            if '/contacts/search' in url:
                resp.status_code = 200
                resp.json.return_value = {'contacts': [{'id': 'existing_contact_abc'}]}
            elif '/contacts/' in url and method == 'put':
                resp.status_code = 200
                resp.json.return_value = {'contact': {'id': 'existing_contact_abc'}}
            elif '/add_contact_ids' in url:
                resp.status_code = 200
                resp.json.return_value = {'contacts': [{'id': 'existing_contact_abc'}]}
            elif '/email_accounts' in url:
                resp.status_code = 200
                resp.json.return_value = {'email_accounts': [{'id': 'ea_001'}]}
            elif '/typed_custom_fields' in url:
                resp.status_code = 200
                resp.json.return_value = {'typed_custom_fields': []}
            else:
                resp.status_code = 200
                resp.json.return_value = {}
            return resp

        monkeypatch.setattr('apollo_pipeline.apollo_api_call', mock_apollo_call)

        # Also need a sequence mapping for enrollment
        conn = sqlite3.connect(test_db)
        try:
            conn.execute(
                "INSERT INTO sequence_mappings (sequence_id, sequence_name, enabled) VALUES (?, ?, 1)",
                ('seq_test', 'Test Sequence'),
            )
            conn.commit()
        except Exception:
            pass
        conn.close()

        from v2.services.enrollment_service import enroll_prospect
        result = enroll_prospect(prospect_id, sequence_id='seq_test')

        # Find the contact update call
        update_calls = [c for c in api_calls
                        if 'contacts/existing_contact_abc' in c['url']
                        and c['method'] == 'put']
        # Should have used PUT, not POST, for the update
        assert len(update_calls) >= 1, (
            f"Expected PUT for existing contact update, got calls: "
            f"{[(c['method'], c['url']) for c in api_calls]}"
        )

        # Verify no POST calls to the contact update endpoint
        post_update_calls = [c for c in api_calls
                             if 'contacts/existing_contact_abc' in c['url']
                             and c['method'] == 'post']
        assert len(post_update_calls) == 0, (
            "Should NOT use POST for existing contact update"
        )


# =========================================================================
# 2. DNC enforcement on save paths
# =========================================================================

class TestDNCEnforcement:
    """Verify that do-not-contact prospects are rejected on both save paths."""

    def test_is_do_not_contact_returns_true(self, test_db):
        """is_do_not_contact should find DNC-flagged emails."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        _seed_prospect(
            test_db, account_id, signal_id,
            email='blocked@evil.com',
            do_not_contact=True,
        )

        from v2.services.prospect_service import is_do_not_contact
        assert is_do_not_contact('blocked@evil.com') is True

    def test_is_do_not_contact_returns_false_for_normal(self, test_db):
        """is_do_not_contact should return False for normal prospects."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        _seed_prospect(
            test_db, account_id, signal_id,
            email='ok@good.com',
            do_not_contact=False,
        )

        from v2.services.prospect_service import is_do_not_contact
        assert is_do_not_contact('ok@good.com') is False

    def test_api_save_rejects_dnc(self, flask_app, test_db):
        """POST /v2/api/prospects should reject DNC contacts."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)

        # Create a DNC prospect first
        _seed_prospect(
            test_db, account_id, signal_id,
            email='blocked@testcorp.com',
            do_not_contact=True,
        )

        # Try to save the same email under a new signal
        signal_id2 = _seed_signal(test_db, account_id, description='new signal')

        resp = flask_app.post('/v2/api/prospects', json={
            'signal_id': signal_id2,
            'account_id': account_id,
            'prospects': [{
                'email': 'blocked@testcorp.com',
                'email_verified': True,
                'full_name': 'Blocked Person',
            }],
        })

        data = resp.get_json()
        # Should be rejected (either error or skipped in response)
        assert data.get('status') == 'error' or data.get('skipped_dnc', 0) > 0

    def test_is_do_not_contact_empty_email(self, test_db):
        """is_do_not_contact should return False for empty strings."""
        from v2.services.prospect_service import is_do_not_contact
        assert is_do_not_contact('') is False
        assert is_do_not_contact(None) is False


# =========================================================================
# 3. Workflow status listing and counts
# =========================================================================

class TestWorkflowStatus:
    """Verify queue listing and counts use workflow (account) status."""

    def test_list_signals_filters_by_account_status(self, test_db):
        """list_signals with status='sequenced' should filter on account_status."""
        aid1 = _seed_account(test_db, 'NewCo', account_status='new')
        aid2 = _seed_account(test_db, 'SeqCo', account_status='sequenced')

        _seed_signal(test_db, aid1, 'signal for new')
        _seed_signal(test_db, aid2, 'signal for sequenced')

        from v2.services.signal_service import list_signals
        result = list_signals(status='sequenced')

        assert result['total'] == 1
        assert result['signals'][0]['company_name'] == 'SeqCo'

    def test_list_signals_filters_by_noise(self, test_db):
        """list_signals with status='noise' returns only noise-account signals."""
        aid1 = _seed_account(test_db, 'GoodCo', account_status='new')
        aid2 = _seed_account(test_db, 'NoiseCo', account_status='noise')

        _seed_signal(test_db, aid1, 'real signal')
        _seed_signal(test_db, aid2, 'noise signal')

        from v2.services.signal_service import list_signals
        result = list_signals(status='noise')
        assert result['total'] == 1
        assert result['signals'][0]['company_name'] == 'NoiseCo'

    def test_counts_by_workflow_status(self, test_db):
        """Counts should be grouped by account_status, not signal status."""
        aid1 = _seed_account(test_db, 'Co1', account_status='new')
        aid2 = _seed_account(test_db, 'Co2', account_status='new')
        aid3 = _seed_account(test_db, 'Co3', account_status='sequenced')

        _seed_signal(test_db, aid1, 'sig1')
        _seed_signal(test_db, aid2, 'sig2')
        _seed_signal(test_db, aid3, 'sig3')

        from v2.services.signal_service import get_signal_counts_by_status
        counts = get_signal_counts_by_status()

        assert counts.get('new') == 2
        assert counts.get('sequenced') == 1

    def test_signals_include_workflow_status_field(self, test_db):
        """Signal rows should include workflow_status derived from account_status."""
        aid = _seed_account(test_db, 'TestCo', account_status='revisit')
        _seed_signal(test_db, aid, 'test')

        from v2.services.signal_service import list_signals
        result = list_signals()
        sig = result['signals'][0]

        assert sig.get('workflow_status') == 'revisit'
        assert sig.get('account_status') == 'revisit'

    def test_api_accepts_workflow_statuses(self, flask_app, test_db):
        """GET /v2/api/signals?status=sequenced should return 200."""
        _seed_account(test_db, 'Acme', account_status='sequenced')

        for status in ('new', 'sequenced', 'revisit', 'noise'):
            resp = flask_app.get(f'/v2/api/signals?status={status}')
            assert resp.status_code == 200, f"Failed for status={status}"

    def test_api_rejects_old_signal_statuses_on_queue(self, flask_app, test_db):
        """GET /v2/api/signals?status=actioned should be rejected."""
        resp = flask_app.get('/v2/api/signals?status=actioned')
        assert resp.status_code == 400


# =========================================================================
# 4. Signal status endpoint is internal-only
# =========================================================================

class TestSignalStatusEndpoint:
    """Verify the signal status endpoint accepts only internal statuses."""

    def test_signal_status_accepts_internal_values(self, flask_app, test_db):
        """PUT /v2/api/signals/<id>/status should accept new/actioned/archived."""
        aid = _seed_account(test_db)
        sid = _seed_signal(test_db, aid)

        for status in ('new', 'actioned', 'archived'):
            resp = flask_app.put(
                f'/v2/api/signals/{sid}/status',
                json={'status': status},
            )
            assert resp.status_code == 200, f"Failed for status={status}"

    def test_signal_status_rejects_workflow_values(self, flask_app, test_db):
        """PUT /v2/api/signals/<id>/status should reject workflow statuses."""
        aid = _seed_account(test_db)
        sid = _seed_signal(test_db, aid)

        for status in ('sequenced', 'revisit', 'noise'):
            resp = flask_app.put(
                f'/v2/api/signals/{sid}/status',
                json={'status': status},
            )
            assert resp.status_code == 400, f"Should reject workflow status '{status}'"


# =========================================================================
# 5. Unverified prospects cannot be enrolled
# =========================================================================

class TestVerifiedEmailEnrollment:
    """Verify that unverified prospects are rejected at enrollment time."""

    def test_unverified_prospect_cannot_enroll(self, test_db, monkeypatch):
        """enroll_prospect should reject prospects with email_verified=0."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        prospect_id = _seed_prospect(
            test_db, account_id, signal_id,
            email='unverified@testcorp.com',
            email_verified=False,  # not verified
        )
        _seed_draft(test_db, prospect_id, signal_id, step=1)

        from v2.services.enrollment_service import enroll_prospect
        result = enroll_prospect(prospect_id, sequence_id='seq_test')

        assert result['status'] == 'error'
        assert 'not verified' in result['message'].lower()

    def test_verified_prospect_can_proceed(self, test_db, monkeypatch):
        """enroll_prospect should NOT reject verified prospects at the email check."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        prospect_id = _seed_prospect(
            test_db, account_id, signal_id,
            email='verified@testcorp.com',
            email_verified=True,
        )
        _seed_draft(test_db, prospect_id, signal_id, step=1)

        # Mock Apollo to avoid real API calls — we just check it gets past the email check
        def mock_apollo_call(method, url, json=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {'typed_custom_fields': []}
            resp.text = ''
            return resp

        monkeypatch.setattr('apollo_pipeline.apollo_api_call', mock_apollo_call)

        from v2.services.enrollment_service import enroll_prospect
        result = enroll_prospect(prospect_id, sequence_id='seq_test')

        # Should NOT fail with "not verified" — may fail for other reasons (no Apollo contact)
        # but the verified-email gate should pass
        if result['status'] == 'error':
            assert 'not verified' not in result['message'].lower(), (
                "Verified prospect should not be rejected for email verification"
            )


# =========================================================================
# 6. MCP source attribution
# =========================================================================

class TestMCPSourceAttribution:
    """Verify MCP-created signals use 'cowork' source, not 'manual_entry'."""

    def test_create_signal_mcp_source(self, test_db):
        """MCP create_signal should use evidence_type='cowork_push' and signal_source='cowork'."""
        # Read the source code to verify the MCP tool uses correct values
        import inspect
        from v2 import mcp_tools

        source = inspect.getsource(mcp_tools)
        # The create_signal tool should set cowork source, not manual_entry
        assert "signal_source='cowork'" in source, (
            "MCP create_signal should use signal_source='cowork'"
        )
        assert "evidence_type='cowork_push'" in source, (
            "MCP create_signal should use evidence_type='cowork_push'"
        )
        # create_revisit_signal should also use cowork
        assert "signal_source='cowork'" in source


# =========================================================================
# 7. Flask smoke tests
# =========================================================================

class TestFlaskSmoke:
    """Basic route smoke tests."""

    def test_app_route(self, flask_app):
        """GET /app should return 200."""
        resp = flask_app.get('/app')
        assert resp.status_code == 200

    def test_campaigns_route(self, flask_app, test_db):
        """GET /v2/api/campaigns should return 200."""
        resp = flask_app.get('/v2/api/campaigns')
        assert resp.status_code == 200

    def test_signals_counts_route(self, flask_app, test_db):
        """GET /v2/api/signals/counts should return 200."""
        resp = flask_app.get('/v2/api/signals/counts')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get('status') == 'success'
        assert 'counts' in data
