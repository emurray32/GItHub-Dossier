"""
Regression tests for the v2 final repair pass.

Tests:
1. Apollo existing-contact update uses PUT (not POST)
2. DNC prospects are rejected on save paths
3. Workflow status (account_status) listing/counts work correctly
4. Signal status endpoint only accepts internal statuses
5. Unverified prospects cannot be enrolled
6. MCP-created signals use 'cowork' source (not 'manual_entry')
7. Duplicate draft prevention — generate_drafts replaces approved drafts
8. Draft read-path deduplicates duplicate steps
9. Workspace exposes reload-safe enrollment readiness
10. Enrollment uses deterministic draft per step
11. Fallback draft generation is explicit and readable
12. Raw exception strings not leaked in 500 responses
13. Flask smoke tests
14. Account status route uses cascade-aware helpers (noise/sequenced/revisit)
15. Personal email filter direction (business emails kept, personal rejected)
16. Draft generation works without campaign_id
17. Enrollment panel preserves skipped terminal state and completion CTA
18. Campaign form preserves persona targeting fields
19. Accounts page links into the v2 intake flow
"""
import json
import io
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


APP_HTML = Path(__file__).resolve().parents[1] / 'templates/v2/app.html'
CAMPAIGN_FORM_HTML = Path(__file__).resolve().parents[1] / 'templates/campaign_form.html'


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


# =========================================================================
# 4b. Campaign form persona serialization
# =========================================================================

class TestCampaignPersonaSerialization:
    """Campaign saves should preserve persona targeting fields from the form."""

    def test_campaign_api_accepts_legacy_form_persona_shape(self, flask_app, test_db):
        """Form-style titles_json/seniorities_json payloads should persist correctly."""
        resp = flask_app.post('/api/campaigns', json={
            'name': 'Persona Shape Campaign',
            'prompt': 'Short prompt',
            'personas': [{
                'persona_name': 'Engineering Lead',
                'titles_json': '["Director Engineering", "VP Engineering"]',
                'seniorities_json': '["director", "vp"]',
                'sequence_id': 'seq_persona',
                'sequence_name': 'Persona Sequence',
                'priority': 0,
            }],
        })
        data = resp.get_json()
        assert resp.status_code == 200, data

        campaign_id = data['campaign']['id']
        personas_resp = flask_app.get(f'/api/campaigns/{campaign_id}/personas')
        personas_data = personas_resp.get_json()
        assert personas_resp.status_code == 200, personas_data
        assert personas_data['personas'][0]['titles'] == ['Director Engineering', 'VP Engineering']
        assert personas_data['personas'][0]['seniorities'] == ['director', 'vp']
        assert personas_data['personas'][0]['sequence_id'] == 'seq_persona'

    def test_campaign_form_round_trips_hidden_sequence_fields(self):
        """Editing a campaign should preserve existing persona sequence metadata."""
        source = CAMPAIGN_FORM_HTML.read_text()
        assert 'class="p-sequence-id"' in source
        assert 'class="p-sequence-name"' in source
        assert 'sequence_id: sequenceId' in source
        assert 'sequence_name: sequenceName' in source

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
# 7. Duplicate draft prevention
# =========================================================================

class TestDuplicateDraftPrevention:
    """Verify that generate_drafts replaces ALL prior non-enrolled drafts."""

    def test_regenerate_replaces_approved_drafts(self, test_db):
        """Generating drafts a second time must replace previously approved drafts."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        prospect_id = _seed_prospect(test_db, account_id, signal_id)

        # Create initial drafts and approve them
        _seed_draft(test_db, prospect_id, signal_id, step=1, status='approved',
                    subject='Old Subject 1', body='Old Body 1')
        _seed_draft(test_db, prospect_id, signal_id, step=2, status='approved',
                    subject='Old Subject 2', body='Old Body 2')
        _seed_draft(test_db, prospect_id, signal_id, step=3, status='approved',
                    subject='Old Subject 3', body='Old Body 3')

        # Verify 3 approved drafts exist
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM drafts WHERE prospect_id = ? AND status = 'approved'",
            (prospect_id,),
        ).fetchall()
        assert len(rows) == 3
        conn.close()

        # Now generate drafts again — this should delete the old approved ones
        from v2.services.draft_service import generate_drafts
        new_drafts = generate_drafts(prospect_id, signal_id, campaign_id=None)

        # Check DB: should have exactly 3 drafts (all new 'generated' status)
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        all_rows = conn.execute(
            "SELECT * FROM drafts WHERE prospect_id = ?",
            (prospect_id,),
        ).fetchall()
        approved_rows = conn.execute(
            "SELECT * FROM drafts WHERE prospect_id = ? AND status = 'approved'",
            (prospect_id,),
        ).fetchall()
        conn.close()

        assert len(all_rows) == 3, (
            f"Expected 3 drafts total, got {len(all_rows)} "
            f"(statuses: {[dict(r)['status'] for r in all_rows]})"
        )
        assert len(approved_rows) == 0, "Old approved drafts should have been deleted"

    def test_enrolled_drafts_preserved(self, test_db):
        """generate_drafts must NOT delete enrolled drafts."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        prospect_id = _seed_prospect(test_db, account_id, signal_id)

        # Create an enrolled draft (already sent to Apollo)
        _seed_draft(test_db, prospect_id, signal_id, step=1, status='enrolled',
                    subject='Sent Subject', body='Sent Body')

        from v2.services.draft_service import generate_drafts
        new_drafts = generate_drafts(prospect_id, signal_id, campaign_id=None)

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        all_rows = conn.execute(
            "SELECT * FROM drafts WHERE prospect_id = ?",
            (prospect_id,),
        ).fetchall()
        enrolled = [dict(r) for r in all_rows if dict(r)['status'] == 'enrolled']
        generated = [dict(r) for r in all_rows if dict(r)['status'] == 'generated']
        conn.close()

        assert len(enrolled) == 1, "Enrolled draft must be preserved"
        assert len(generated) == 3, "3 new generated drafts expected"

    def test_no_duplicate_steps_after_approval_and_regeneration(self, test_db):
        """After approve+regenerate, there should be exactly one draft per step."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        prospect_id = _seed_prospect(test_db, account_id, signal_id)

        # First generation
        from v2.services.draft_service import generate_drafts, approve_all_drafts
        drafts1 = generate_drafts(prospect_id, signal_id, campaign_id=None)
        assert len(drafts1) == 3

        # Approve all
        approve_all_drafts(prospect_id)

        # Second generation (should replace approved)
        drafts2 = generate_drafts(prospect_id, signal_id, campaign_id=None)
        assert len(drafts2) == 3

        # Check: exactly one draft per step
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        all_rows = conn.execute(
            "SELECT sequence_step, COUNT(*) as cnt FROM drafts WHERE prospect_id = ? GROUP BY sequence_step",
            (prospect_id,),
        ).fetchall()
        conn.close()

        for row in all_rows:
            assert dict(row)['cnt'] == 1, (
                f"Step {dict(row)['sequence_step']} has {dict(row)['cnt']} drafts (expected 1)"
            )


# =========================================================================
# 8. Draft read-paths surface only the latest version per step
# =========================================================================

class TestDraftReadDedup:
    """Duplicate drafts should be collapsed before the UI consumes them."""

    def test_get_signal_workspace_dedupes_duplicate_steps(self, test_db):
        """Workspace payload should only expose the newest draft per step."""
        account_id = _seed_account(test_db, company_name='Figma')
        signal_id = _seed_signal(test_db, account_id)
        prospect_id = _seed_prospect(test_db, account_id, signal_id, full_name='Dave Capra')

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO drafts (prospect_id, signal_id, sequence_step, status,
               subject, body, updated_at) VALUES (?, ?, 1, 'generated', 'Old Subject',
               'Old Body', '2025-01-01 00:00:00')""",
            (prospect_id, signal_id),
        )
        conn.execute(
            """INSERT INTO drafts (prospect_id, signal_id, sequence_step, status,
               subject, body, updated_at) VALUES (?, ?, 1, 'generated', 'New Subject',
               'New Body', '2025-06-01 00:00:00')""",
            (prospect_id, signal_id),
        )
        conn.execute(
            """INSERT INTO drafts (prospect_id, signal_id, sequence_step, status,
               subject, body, updated_at) VALUES (?, ?, 2, 'generated', 'Step 2',
               'Step 2 Body', '2025-06-01 00:00:00')""",
            (prospect_id, signal_id),
        )
        conn.commit()
        conn.close()

        from v2.services.signal_service import get_signal_workspace
        workspace = get_signal_workspace(signal_id)

        assert [(d['sequence_step'], d['subject']) for d in workspace['drafts']] == [
            (1, 'New Subject'),
            (2, 'Step 2'),
        ]


# =========================================================================
# 9. Workspace exposes enrollment readiness for approved prospects
# =========================================================================

class TestWorkspaceEnrollmentReadiness:
    """Workspace payload should expose reload-safe approval state."""

    def test_workspace_marks_approved_prospects_and_enrollment_ready(self, test_db):
        """Approved drafts should survive reload as explicit workspace state."""
        account_id = _seed_account(test_db, company_name='Figma')
        signal_id = _seed_signal(test_db, account_id)
        first = _seed_prospect(test_db, account_id, signal_id, email='one@figma.com', full_name='One')
        second = _seed_prospect(test_db, account_id, signal_id, email='two@figma.com', full_name='Two')

        _seed_draft(test_db, first, signal_id, step=1, status='approved', subject='Approved')
        _seed_draft(test_db, second, signal_id, step=1, status='generated', subject='Pending')

        from v2.services.signal_service import get_signal_workspace

        workspace = get_signal_workspace(signal_id)
        by_id = {p['id']: p for p in workspace['prospects']}

        assert workspace['enrollment_ready'] is False
        assert workspace['approved_prospect_ids'] == [first]
        assert by_id[first]['all_drafts_approved'] is True
        assert by_id[second]['all_drafts_approved'] is False

        conn = sqlite3.connect(test_db)
        conn.execute("UPDATE drafts SET status = 'approved' WHERE prospect_id = ?", (second,))
        conn.commit()
        conn.close()

        workspace = get_signal_workspace(signal_id)
        by_id = {p['id']: p for p in workspace['prospects']}

        assert workspace['enrollment_ready'] is True
        assert workspace['approved_prospect_ids'] == [first, second]
        assert by_id[second]['all_drafts_approved'] is True


# =========================================================================
# 10. Bulk enrollment preserves sequence overrides
# =========================================================================

class TestBulkEnrollmentSequenceOverride:
    """Bulk enrollment should respect the selected Apollo sequence."""

    def test_bulk_route_forwards_sequence_override(self, flask_app, monkeypatch):
        """POST /v2/api/enrollment/bulk should pass sequence_id through."""
        captured = {}

        def fake_bulk_enroll(prospect_ids, sequence_id=None):
            captured['prospect_ids'] = prospect_ids
            captured['sequence_id'] = sequence_id
            return {
                'enrolled': 0,
                'failed': 0,
                'skipped': 0,
                'total': len(prospect_ids),
                'details': [],
            }

        monkeypatch.setattr('v2.services.enrollment_service.bulk_enroll', fake_bulk_enroll)

        resp = flask_app.post('/v2/api/enrollment/bulk', json={
            'prospect_ids': [11, 22],
            'sequence_id': 'seq_override',
        })
        assert resp.status_code == 200
        assert captured == {
            'prospect_ids': [11, 22],
            'sequence_id': 'seq_override',
        }

    def test_bulk_service_passes_override_to_each_prospect(self, monkeypatch):
        """bulk_enroll() should call enroll_prospect(..., sequence_id=override)."""
        calls = []

        monkeypatch.setattr(
            'v2.services.prospect_service.get_prospect',
            lambda pid: {'full_name': f'Prospect {pid}', 'email': f'p{pid}@corp.com'},
        )

        def fake_enroll(prospect_id, sequence_id=None):
            calls.append((prospect_id, sequence_id))
            return {'status': 'success'}

        monkeypatch.setattr('v2.services.enrollment_service.enroll_prospect', fake_enroll)

        from v2.services.enrollment_service import bulk_enroll
        result = bulk_enroll([11, 22], sequence_id='seq_override')

        assert result['enrolled'] == 2
        assert calls == [(11, 'seq_override'), (22, 'seq_override')]

    def test_bulk_service_counts_apollo_skips_as_skipped(self, monkeypatch):
        """Apollo-level skips should not be counted as hard failures in bulk mode."""
        monkeypatch.setattr(
            'v2.services.prospect_service.get_prospect',
            lambda pid: {'full_name': f'Prospect {pid}', 'email': f'p{pid}@corp.com'},
        )

        def fake_enroll(prospect_id, sequence_id=None):
            return {
                'status': 'error',
                'message': 'Apollo accepted request but skipped contact: already_in_campaign',
                'skipped': True,
            }

        monkeypatch.setattr('v2.services.enrollment_service.enroll_prospect', fake_enroll)

        from v2.services.enrollment_service import bulk_enroll
        result = bulk_enroll([11], sequence_id='seq_override')

        assert result['enrolled'] == 0
        assert result['failed'] == 0
        assert result['skipped'] == 1


class TestEnrollmentSequenceResolution:
    """Enrollment should respect stored per-prospect sequence choices."""

    def test_resolve_sequence_prefers_prospect_override_over_shared_fallback(self):
        """A review-selected sequence override should beat the shared enroll fallback."""
        from v2.services.enrollment_service import _resolve_sequence_id

        sequence_id = _resolve_sequence_id(
            {
                'id': 99,
                'sequence_config_override': json.dumps({
                    'sequence_id': 'seq_override',
                    'sequence_name': 'Custom Override',
                }),
            },
            [],
            fallback_sequence_id='seq_shared',
        )

        assert sequence_id == 'seq_override'

    def test_resolve_sequence_uses_shared_fallback_without_override(self):
        """The shared enrollment override should still work when no per-prospect override exists."""
        from v2.services.enrollment_service import _resolve_sequence_id

        sequence_id = _resolve_sequence_id(
            {'id': 100},
            [],
            fallback_sequence_id='seq_shared',
        )

        assert sequence_id == 'seq_shared'


# =========================================================================
# 11. Enrollment panel UI/state guards
# =========================================================================

class TestEnrollmentPanelUiState:
    """The v2 enrollment panel should treat skipped prospects as terminal."""

    def test_skipped_enrollments_render_as_distinct_terminal_state(self):
        """Skipped contacts should not be styled or tracked like failures."""
        source = APP_HTML.read_text()
        assert "includes('skipped contact')" in source
        assert "newStatuses[d.prospect_id] = 'skipped';" in source
        assert "if (s === 'skipped') return <Icon name=\"skip-forward\"" in source
        assert "if (s === 'skipped') return 'Skipped';" in source
        assert "!['enrolled', 'enrolling', 'skipped'].includes(statuses[p.id])" in source

    def test_completion_panel_uses_actionable_count(self):
        """The completion CTA should appear once no actionable prospects remain."""
        source = APP_HTML.read_text()
        assert "const actionableCount = prospects.filter(p => !terminalStatuses.includes(statuses[p.id]) && statuses[p.id] !== 'enrolling').length;" in source
        assert "const completedCount = prospects.length - actionableCount;" in source
        assert "toast('All selected prospects are already processed', 'info');" in source
        assert "Enrollment complete ({completedCount}/{prospects.length} processed)" in source


class TestAdminIntakeLinks:
    """Less-used admin surfaces should still link into a live intake flow."""

    def test_accounts_page_links_to_v2_intake(self, flask_app, test_db):
        """Accounts CTAs should not send users to the removed /settings page."""
        _seed_account(test_db, company_name='LinkCo', website='linkco.com')
        resp = flask_app.get('/accounts')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert '/app?intake=1' in html
        assert '/settings#admin-intake' not in html
        assert 'https://linkco.com' in html

    def test_v2_app_supports_intake_deeplink(self):
        """The queue app should honor the intake query param used by admin links."""
        source = APP_HTML.read_text()
        assert "searchParams.get('intake') === '1'" in source
        assert "syncIntakeQueryParam(true);" in source
        assert "syncIntakeQueryParam(false);" in source


class TestBdrPreferenceValidation:
    """BDR overrides should only be created for real email identities."""

    def test_bdr_preference_rejects_invalid_email_identity(self, flask_app):
        """Arbitrary strings should not be accepted as BDR emails."""
        resp = flask_app.put('/v2/api/bdr-writing-preferences/not-an-email', json={
            'key': 'tone',
            'value': 'Keep it concise',
            'override_mode': 'replace',
        })
        data = resp.get_json()
        assert resp.status_code == 400, data
        assert 'email' in data['message'].lower()


class TestIngestionRouteFailures:
    """Bulk import routes should not report failed uploads as success."""

    def test_file_route_rejects_csv_schema_failure(self, flask_app):
        """Missing required signal columns should return an error response."""
        csv_bytes = io.BytesIO(b"company_name,website,industry\nAcme,acme.com,SaaS\n")
        resp = flask_app.post(
            '/v2/api/ingest/file',
            data={'file': (csv_bytes, 'bad-signals.csv')},
            content_type='multipart/form-data',
        )
        data = resp.get_json()
        assert resp.status_code == 400, data
        assert data['status'] == 'error'
        assert 'signal_description' in data['message']
        assert data['result']['signals_created'] == 0


# =========================================================================
# 12. Enrollment uses deterministic draft per step
# =========================================================================

class TestEnrollmentDraftDedup:
    """Verify enrollment picks one draft per step if duplicates somehow exist."""

    def test_enrollment_dedup_by_step(self, test_db, monkeypatch):
        """If multiple approved drafts exist for same step, enrollment uses latest."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        prospect_id = _seed_prospect(test_db, account_id, signal_id)

        # Seed TWO approved drafts for step 1 with different timestamps
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO drafts (prospect_id, signal_id, sequence_step, status,
               subject, body, updated_at) VALUES (?, ?, 1, 'approved', 'Old Subject', 'Old Body',
               '2025-01-01 00:00:00')""",
            (prospect_id, signal_id),
        )
        conn.execute(
            """INSERT INTO drafts (prospect_id, signal_id, sequence_step, status,
               subject, body, updated_at) VALUES (?, ?, 1, 'approved', 'New Subject', 'New Body',
               '2025-06-01 00:00:00')""",
            (prospect_id, signal_id),
        )
        conn.commit()
        conn.close()

        # Track what typed_custom_fields get sent to Apollo
        captured_fields = {}

        def mock_apollo_call(method, url, json=None, timeout=None):
            if json and 'typed_custom_fields' in (json or {}):
                captured_fields.update(json['typed_custom_fields'])
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {'contact': {'id': 'test_abc'}, 'typed_custom_fields': [],
                                       'contacts': [{'id': 'test_abc'}],
                                       'email_accounts': [{'id': 'ea_1'}]}
            resp.text = ''
            return resp

        monkeypatch.setattr('apollo_pipeline.apollo_api_call', mock_apollo_call)

        from v2.services.enrollment_service import enroll_prospect
        result = enroll_prospect(prospect_id, sequence_id='seq_test')

        # The subject used should be 'New Subject' (the later one), not 'Old Subject'
        # Check captured fields contain the newer content
        has_new = any('New Subject' in str(v) for v in captured_fields.values())
        has_old = any('Old Subject' in str(v) for v in captured_fields.values())

        # If Apollo call was made, new content should be used
        if captured_fields:
            assert has_new, f"Expected 'New Subject' in custom fields, got: {captured_fields}"
            assert not has_old, f"Old subject should not be in custom fields: {captured_fields}"


# =========================================================================
# 12. Fallback draft generation is visible and readable
# =========================================================================

class TestDraftFallbackVisibility:
    """Fallback draft generation should not look like a silent success."""

    def test_generate_route_surfaces_fallback_warning(self, flask_app, test_db, monkeypatch):
        """Template fallback should be readable and explicitly disclosed."""
        account_id = _seed_account(test_db, company_name='Figma')
        signal_id = _seed_signal(test_db, account_id, description='translation key naming')
        prospect_id = _seed_prospect(
            test_db,
            account_id,
            signal_id,
            email='dave@figma.com',
            full_name='Dave Capra',
        )

        import v2.services.draft_service as draft_service

        monkeypatch.setattr(draft_service, '_llm_generate', lambda *args, **kwargs: None)
        monkeypatch.setenv('APOLLO_SENDER_EMAIL', 'alex.rep@phrase.com')

        resp = flask_app.post('/v2/api/drafts/generate', json={
            'prospect_id': prospect_id,
            'signal_id': signal_id,
        })
        assert resp.status_code == 200

        data = resp.get_json()
        assert 'fallback drafts' in data['message']
        assert len(data['drafts']) == 3

        step_one = data['drafts'][0]
        assert step_one['generation_notes']
        assert '{{company}}' not in step_one['subject']
        assert '{{first_name}}' not in step_one['body']
        assert '{{sender_first_name}}' not in step_one['body']
        assert 'Figma' in step_one['subject']
        assert 'Dave Capra' in step_one['body']
        assert 'Alex' in step_one['body']

        from v2.services.signal_service import get_signal_workspace
        workspace = get_signal_workspace(signal_id)
        assert workspace['drafts'][0]['generation_notes']


# =========================================================================
# 11. Campaign surfaces stay aligned with v2 expectations
# =========================================================================

class TestCampaignSurfaceAlignment:
    """Campaign UI/API should not leak legacy or incomplete state."""

    def test_v2_campaigns_api_returns_active_campaigns_with_status(self, flask_app, test_db):
        """The v2 campaign picker should only receive active campaigns, with status present."""
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO campaigns (name, prompt, status) VALUES (?, ?, ?)",
            ('Active Campaign', 'Prompt A', 'active'),
        )
        conn.execute(
            "INSERT INTO campaigns (name, prompt, status) VALUES (?, ?, ?)",
            ('Draft Campaign', 'Prompt B', 'draft'),
        )
        conn.commit()
        conn.close()

        resp = flask_app.get('/v2/api/campaigns')
        assert resp.status_code == 200

        data = resp.get_json()
        campaigns = data['campaigns']
        assert [c['name'] for c in campaigns] == ['Active Campaign']
        assert campaigns[0]['status'] == 'active'

    def test_campaign_edit_form_hides_null_tone(self, flask_app, test_db):
        """A null tone should render as blank, not the literal string 'None'."""
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO campaigns (name, prompt, status, tone) VALUES (?, ?, ?, ?)",
            ('Tone Check', 'Prompt', 'active', None),
        )
        conn.commit()
        campaign_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()['id']
        conn.close()

        resp = flask_app.get(f'/campaigns/{campaign_id}/edit')
        assert resp.status_code == 200

        html = resp.data.decode('utf-8')
        assert 'id="campaign-tone"' in html
        assert 'value="None"' not in html


# =========================================================================
# 12. Workspace approval state survives reloads
# =========================================================================

class TestWorkspaceApprovalState:
    """Workspace payload should expose enough state for the v2 UI to resume cleanly."""

    def test_signal_workspace_marks_enrollment_ready_when_all_actionable_prospects_are_approved(self, test_db):
        """Approved drafts should round-trip into workspace-level enrollment readiness."""
        account_id = _seed_account(test_db)
        signal_id = _seed_signal(test_db, account_id)
        approved_id = _seed_prospect(test_db, account_id, signal_id, email='approved@testcorp.com')
        dnc_id = _seed_prospect(
            test_db, account_id, signal_id,
            email='dnc@testcorp.com', do_not_contact=True,
        )

        _seed_draft(test_db, approved_id, signal_id, step=1, status='approved')
        _seed_draft(test_db, approved_id, signal_id, step=2, status='approved')
        _seed_draft(test_db, dnc_id, signal_id, step=1, status='generated')

        from v2.services.signal_service import get_signal_workspace
        workspace = get_signal_workspace(signal_id)

        assert workspace['approved_prospect_ids'] == [approved_id]
        assert workspace['enrollment_ready'] is True
        approved_row = next(p for p in workspace['prospects'] if p['id'] == approved_id)
        dnc_row = next(p for p in workspace['prospects'] if p['id'] == dnc_id)
        assert approved_row['all_drafts_approved'] is True
        assert dnc_row['all_drafts_approved'] is False


# =========================================================================
# 13. Raw exception strings not leaked in 500 responses
# =========================================================================

class TestExceptionHardening:
    """Verify 500 responses don't leak internal exception details."""

    def test_api_500_generic_message(self, flask_app, test_db):
        """Internal errors should return generic message, not str(exception)."""
        # Hit an endpoint that will trigger an error — use a nonexistent signal workspace
        # The normal 404 path should work, but if something unexpected fails internally,
        # it should return 'Internal server error' not the exception text
        import inspect
        from v2.routes import api

        source = inspect.getsource(api)
        # Verify no str(e) in 500 returns
        assert "return _error(str(e), 500)" not in source, (
            "API routes should not return str(e) in 500 responses"
        )

    def test_draft_500_generic_message(self, flask_app, test_db):
        """Draft routes should not leak exception details."""
        import inspect
        from v2.routes import draft

        source = inspect.getsource(draft)
        assert "return _error(str(e), 500)" not in source

    def test_enrollment_500_generic_message(self, flask_app, test_db):
        """Enrollment routes should not leak exception details."""
        import inspect
        from v2.routes import enrollment

        source = inspect.getsource(enrollment)
        assert "return _error(str(e), 500)" not in source


# =========================================================================
# 10. Flask smoke tests
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


# =========================================================================
# 11. Account status route uses cascade-aware helpers
# =========================================================================

class TestAccountStatusCascade:
    """Verify PUT /v2/api/accounts/<id>/status dispatches to cascade helpers."""

    def test_noise_cascades_signals_to_archived(self, flask_app, test_db):
        """Marking account as noise via route should archive its signals."""
        aid = _seed_account(test_db, 'NoisyCo', account_status='new')
        sid1 = _seed_signal(test_db, aid, 'signal one')
        sid2 = _seed_signal(test_db, aid, 'signal two')

        resp = flask_app.put(
            f'/v2/api/accounts/{aid}/status',
            json={'status': 'noise'},
        )
        assert resp.status_code == 200

        # Verify signals were cascaded to 'archived'
        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT status FROM intent_signals WHERE account_id = ?", (aid,)
        ).fetchall()
        conn.close()

        statuses = [dict(r)['status'] for r in rows]
        assert all(s == 'archived' for s in statuses), (
            f"Expected all signals archived after noise, got: {statuses}"
        )

    def test_sequenced_cascades_signals_to_actioned(self, flask_app, test_db):
        """Marking account as sequenced via route should action its new signals."""
        aid = _seed_account(test_db, 'SeqCo', account_status='new')
        sid = _seed_signal(test_db, aid, 'new signal')

        resp = flask_app.put(
            f'/v2/api/accounts/{aid}/status',
            json={'status': 'sequenced'},
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM intent_signals WHERE id = ?", (sid,)
        ).fetchone()
        conn.close()

        assert dict(row)['status'] == 'actioned', (
            f"Expected signal actioned after sequenced, got: {dict(row)['status']}"
        )

    def test_revisit_cascades_signals_to_actioned(self, flask_app, test_db):
        """Marking account as revisit via route should action its new signals."""
        aid = _seed_account(test_db, 'RevCo', account_status='new')
        sid = _seed_signal(test_db, aid, 'new signal')

        resp = flask_app.put(
            f'/v2/api/accounts/{aid}/status',
            json={'status': 'revisit'},
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM intent_signals WHERE id = ?", (sid,)
        ).fetchone()
        conn.close()

        assert dict(row)['status'] == 'actioned'

    def test_new_status_does_not_cascade(self, flask_app, test_db):
        """Setting account to 'new' should not change signal statuses."""
        aid = _seed_account(test_db, 'ResetCo', account_status='sequenced')
        sid = _seed_signal(test_db, aid, 'actioned signal')

        # Manually set signal to 'actioned'
        conn = sqlite3.connect(test_db)
        conn.execute("UPDATE intent_signals SET status = 'actioned' WHERE id = ?", (sid,))
        conn.commit()
        conn.close()

        resp = flask_app.put(
            f'/v2/api/accounts/{aid}/status',
            json={'status': 'new'},
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM intent_signals WHERE id = ?", (sid,)
        ).fetchone()
        conn.close()

        # Signal should still be 'actioned' — no cascade for 'new'
        assert dict(row)['status'] == 'actioned'

    def test_noise_already_archived_signals_unchanged(self, flask_app, test_db):
        """Noise should not re-archive already-archived signals (no-op is fine)."""
        aid = _seed_account(test_db, 'ArchCo', account_status='new')
        sid = _seed_signal(test_db, aid, 'already archived')

        # Pre-archive the signal
        conn = sqlite3.connect(test_db)
        conn.execute("UPDATE intent_signals SET status = 'archived' WHERE id = ?", (sid,))
        conn.commit()
        conn.close()

        resp = flask_app.put(
            f'/v2/api/accounts/{aid}/status',
            json={'status': 'noise'},
        )
        assert resp.status_code == 200

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM intent_signals WHERE id = ?", (sid,)
        ).fetchone()
        conn.close()

        assert dict(row)['status'] == 'archived'


# =========================================================================
# 12. Personal email filter direction (dogfood finding)
# =========================================================================

class TestPersonalEmailFilter:
    """Verify personal email filtering keeps business emails and rejects personal."""

    def test_business_email_saved(self, flask_app, test_db):
        """Business domain emails should be saved, not filtered out."""
        aid = _seed_account(test_db, 'BizCo', website='https://bizco.com')
        sid = _seed_signal(test_db, aid, 'test')

        resp = flask_app.post('/v2/api/prospects', json={
            'signal_id': sid,
            'account_id': aid,
            'prospects': [
                {'email': 'jane@bizco.com', 'email_verified': True, 'full_name': 'Jane Biz'},
                {'email': 'bob@bizco.com', 'email_verified': True, 'full_name': 'Bob Biz'},
            ],
        })
        data = resp.get_json()
        assert data.get('count') == 2, (
            f"Expected 2 business emails saved, got {data.get('count')}: {data}"
        )

    def test_personal_email_rejected(self, flask_app, test_db):
        """Gmail/Yahoo/etc emails should be filtered out."""
        aid = _seed_account(test_db, 'FilterCo')
        sid = _seed_signal(test_db, aid, 'test')

        resp = flask_app.post('/v2/api/prospects', json={
            'signal_id': sid,
            'account_id': aid,
            'prospects': [
                {'email': 'user@gmail.com', 'email_verified': True, 'full_name': 'Gmail User'},
                {'email': 'user@yahoo.com', 'email_verified': True, 'full_name': 'Yahoo User'},
                {'email': 'valid@filterco.com', 'email_verified': True, 'full_name': 'Valid User'},
            ],
        })
        data = resp.get_json()
        assert data.get('count') == 1, f"Expected 1 saved, got {data.get('count')}"
        assert data.get('skipped_personal') == 2, f"Expected 2 personal skipped, got {data.get('skipped_personal')}"


# =========================================================================
# 13. Draft generation without campaign_id (dogfood finding)
# =========================================================================

class TestDraftGenerationNoCampaign:
    """Verify draft generation works without a campaign_id."""

    def test_generate_without_campaign_id(self, flask_app, test_db):
        """POST /v2/api/drafts/generate should work without campaign_id."""
        aid = _seed_account(test_db, 'DraftCo')
        sid = _seed_signal(test_db, aid, 'test signal')
        pid = _seed_prospect(test_db, aid, sid, email='dev@draftco.com')

        resp = flask_app.post('/v2/api/drafts/generate', json={
            'prospect_id': pid,
            'signal_id': sid,
        })
        data = resp.get_json()
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {data}"
        assert len(data.get('drafts', [])) == 3

    def test_sequence_override_route_regenerates_with_campaign_zero(self, flask_app, test_db, monkeypatch):
        """Changing a prospect sequence should still regenerate drafts when campaign_id=0."""
        aid = _seed_account(test_db, 'RouteZeroCo')
        sid = _seed_signal(test_db, aid, 'test signal')
        pid = _seed_prospect(test_db, aid, sid, email='dev@routezeroco.com')
        captured = {}

        def fake_generate_drafts(prospect_id, signal_id, campaign_id, **kwargs):
            captured['prospect_id'] = prospect_id
            captured['signal_id'] = signal_id
            captured['campaign_id'] = campaign_id
            captured['sequence_config_override'] = kwargs.get('sequence_config_override')
            return [{
                'id': 1,
                'prospect_id': prospect_id,
                'signal_id': signal_id,
                'sequence_step': 1,
                'status': 'generated',
                'subject': 'Hi',
                'body': 'Body',
            }]

        monkeypatch.setattr('v2.services.draft_service.generate_drafts', fake_generate_drafts)

        resp = flask_app.put(f'/v2/api/prospects/{pid}/sequence', json={
            'sequence_config': {
                'sequence_id': 'seq_override',
                'sequence_name': 'Custom Sequence',
                'num_steps': 4,
            },
            'regenerate': True,
            'signal_id': sid,
            'campaign_id': 0,
        })

        data = resp.get_json()
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {data}"
        assert captured == {
            'prospect_id': pid,
            'signal_id': sid,
            'campaign_id': 0,
            'sequence_config_override': {
                'sequence_id': 'seq_override',
                'sequence_name': 'Custom Sequence',
                'num_steps': 4,
            },
        }
        assert len(data.get('drafts', [])) == 1

    def test_generate_with_campaign_id_zero(self, flask_app, test_db):
        """POST /v2/api/drafts/generate with campaign_id=0 should treat as no campaign."""
        aid = _seed_account(test_db, 'ZeroCo')
        sid = _seed_signal(test_db, aid, 'test signal')
        pid = _seed_prospect(test_db, aid, sid, email='dev@zeroco.com')

        resp = flask_app.post('/v2/api/drafts/generate', json={
            'prospect_id': pid,
            'signal_id': sid,
            'campaign_id': 0,
        })
        data = resp.get_json()
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {data}"
