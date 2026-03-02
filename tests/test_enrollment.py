"""Tests for enrollment pipeline: CRUD, dedup, batch processing, status transitions."""
import json
import pytest


pytestmark = pytest.mark.unit


class TestEnrollmentBatchCRUD:
    """Test enrollment_batches table operations."""

    def test_create_batch(self, test_db, sample_campaign):
        import database
        batch_id = database.create_enrollment_batch(sample_campaign, [10, 20, 30])
        assert batch_id is not None
        assert isinstance(batch_id, int)

    def test_get_batch(self, test_db, sample_campaign):
        import database
        batch_id = database.create_enrollment_batch(sample_campaign, [10, 20])
        batch = database.get_enrollment_batch(batch_id)
        assert batch is not None
        assert batch['campaign_id'] == sample_campaign
        assert batch['total_accounts'] == 2
        assert batch['status'] == 'pending'
        assert batch['account_ids'] == [10, 20]

    def test_get_nonexistent_batch(self, test_db):
        import database
        batch = database.get_enrollment_batch(99999)
        assert batch is None

    def test_update_batch_status(self, test_db, sample_batch):
        import database
        ok = database.update_enrollment_batch(sample_batch, status='running', current_phase='discovery')
        assert ok is True
        batch = database.get_enrollment_batch(sample_batch)
        assert batch['status'] == 'running'
        assert batch['current_phase'] == 'discovery'

    def test_update_batch_counters(self, test_db, sample_batch):
        import database
        database.update_enrollment_batch(sample_batch, discovered=5, generated=3, enrolled=2, failed=1)
        batch = database.get_enrollment_batch(sample_batch)
        assert batch['discovered'] == 5
        assert batch['generated'] == 3
        assert batch['enrolled'] == 2
        assert batch['failed'] == 1

    def test_update_batch_rejects_invalid_fields(self, test_db, sample_batch):
        import database
        ok = database.update_enrollment_batch(sample_batch, hacker_field='evil')
        assert ok is False

    def test_get_batches_for_campaign(self, test_db, sample_campaign):
        import database
        id1 = database.create_enrollment_batch(sample_campaign, [1])
        id2 = database.create_enrollment_batch(sample_campaign, [2])
        batches = database.get_enrollment_batches_for_campaign(sample_campaign)
        assert len(batches) == 2
        # Should return both batches (ordered by created_at DESC)
        batch_ids = {b['id'] for b in batches}
        assert id1 in batch_ids
        assert id2 in batch_ids

    def test_get_batches_for_empty_campaign(self, test_db, sample_campaign):
        import database
        batches = database.get_enrollment_batches_for_campaign(sample_campaign)
        assert batches == []


class TestEnrollmentContactCRUD:
    """Test enrollment_contacts table operations."""

    def test_create_contact(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(
            sample_batch, 'TestCorp',
            email='test@testcorp.com',
            first_name='Test',
            last_name='User',
            title='Engineer',
        )
        assert cid is not None
        assert isinstance(cid, int)

    def test_create_contact_minimal(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(sample_batch, 'TestCorp')
        assert cid is not None

    def test_get_contacts_by_batch(self, test_db, sample_batch):
        import database
        database.create_enrollment_contact(sample_batch, 'Corp A', email='a@corp.com')
        database.create_enrollment_contact(sample_batch, 'Corp B', email='b@corp.com')
        contacts = database.get_enrollment_contacts(sample_batch)
        assert len(contacts) == 2

    def test_get_contacts_by_status(self, test_db, sample_batch):
        import database
        database.create_enrollment_contact(sample_batch, 'Corp A', email='a@corp.com', status='discovered')
        database.create_enrollment_contact(sample_batch, 'Corp B', email='b@corp.com', status='generated')
        discovered = database.get_enrollment_contacts(sample_batch, status='discovered')
        assert len(discovered) == 1
        assert discovered[0]['company_name'] == 'Corp A'

    def test_update_contact_status(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(sample_batch, 'Corp A', email='a@corp.com', status='discovered')
        ok = database.update_enrollment_contact(cid, status='generated')
        assert ok is True
        contacts = database.get_enrollment_contacts(sample_batch, status='generated')
        assert len(contacts) == 1

    def test_update_contact_generated_emails(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(sample_batch, 'Corp A', email='a@corp.com')
        emails = json.dumps([{'subject': 'Test', 'body': 'Hello'}])
        database.update_enrollment_contact(cid, generated_emails_json=emails, status='generated')
        contacts = database.get_enrollment_contacts(sample_batch)
        assert contacts[0]['generated_emails_json'] == emails

    def test_update_contact_rejects_invalid_fields(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(sample_batch, 'Corp A')
        ok = database.update_enrollment_contact(cid, hacker_field='evil')
        assert ok is False

    def test_contact_ignores_unknown_kwargs(self, test_db, sample_batch):
        """create_enrollment_contact should silently ignore unknown fields."""
        import database
        cid = database.create_enrollment_contact(
            sample_batch, 'Corp A',
            email='a@corp.com',
            unknown_field='ignored'
        )
        assert cid is not None


class TestBulkCreateContacts:
    """Test batch contact insertion."""

    def test_bulk_create(self, test_db, sample_batch, sample_enrollment_contacts):
        import database
        # Fix batch_id to match our test batch
        for c in sample_enrollment_contacts:
            c['batch_id'] = sample_batch
        count = database.bulk_create_enrollment_contacts(sample_enrollment_contacts)
        assert count == 3
        contacts = database.get_enrollment_contacts(sample_batch)
        assert len(contacts) == 3

    def test_bulk_create_empty_list(self, test_db):
        import database
        count = database.bulk_create_enrollment_contacts([])
        assert count == 0

    def test_bulk_create_preserves_all_fields(self, test_db, sample_batch):
        import database
        contacts = [{
            'batch_id': sample_batch,
            'company_name': 'TestCorp',
            'company_domain': 'testcorp.com',
            'email': 'jane@testcorp.com',
            'first_name': 'Jane',
            'last_name': 'Smith',
            'title': 'VP Eng',
            'seniority': 'vp',
            'persona_name': 'Engineering',
            'sequence_id': 'seq_001',
            'sequence_name': 'Test Seq',
            'linkedin_url': 'https://linkedin.com/in/janesmith',
        }]
        database.bulk_create_enrollment_contacts(contacts)
        result = database.get_enrollment_contacts(sample_batch)
        assert len(result) == 1
        c = result[0]
        assert c['email'] == 'jane@testcorp.com'
        assert c['title'] == 'VP Eng'
        assert c['seniority'] == 'vp'
        assert c['linkedin_url'] == 'https://linkedin.com/in/janesmith'


class TestEnrollmentDedup:
    """Test deduplication: same email + same sequence = skip, different sequence = allow."""

    def test_same_email_same_sequence_detected(self, test_db, sample_batch):
        """Two contacts with the same email and sequence in same batch can be detected."""
        import database
        database.create_enrollment_contact(
            sample_batch, 'CorpA',
            email='dupe@corp.com', sequence_id='seq_001', status='enrolled'
        )
        database.create_enrollment_contact(
            sample_batch, 'CorpA',
            email='dupe@corp.com', sequence_id='seq_001', status='discovered'
        )
        # Application logic should check for duplicates by querying:
        contacts = database.get_enrollment_contacts(sample_batch)
        emails_in_seq = [(c['email'], c['sequence_id']) for c in contacts]
        dupes = [e for e in emails_in_seq if emails_in_seq.count(e) > 1]
        assert len(dupes) > 0, "Should detect duplicate email+sequence pair"

    def test_same_email_different_sequence_allowed(self, test_db, sample_batch):
        """Same email with different sequences should both be allowed."""
        import database
        database.create_enrollment_contact(
            sample_batch, 'CorpA',
            email='multi@corp.com', sequence_id='seq_001', status='enrolled'
        )
        database.create_enrollment_contact(
            sample_batch, 'CorpA',
            email='multi@corp.com', sequence_id='seq_002', status='discovered'
        )
        contacts = database.get_enrollment_contacts(sample_batch)
        assert len(contacts) == 2
        sequences = {c['sequence_id'] for c in contacts}
        assert sequences == {'seq_001', 'seq_002'}

    def test_cross_batch_dedup_detectable(self, test_db, sample_campaign):
        """Same email enrolled across batches can be detected via email index."""
        import database
        batch1 = database.create_enrollment_batch(sample_campaign, [1])
        batch2 = database.create_enrollment_batch(sample_campaign, [2])
        database.create_enrollment_contact(batch1, 'CorpA', email='same@corp.com', sequence_id='seq_001', status='enrolled')
        database.create_enrollment_contact(batch2, 'CorpA', email='same@corp.com', sequence_id='seq_001', status='discovered')
        # Query across batches
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM enrollment_contacts WHERE email = ? AND sequence_id = ?",
            ('same@corp.com', 'seq_001')
        )
        row = cursor.fetchone()
        conn.close()
        assert row['cnt'] == 2


class TestStatusTransitions:
    """Test enrollment contact status transitions."""

    def test_discovered_to_generated(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(sample_batch, 'Corp', status='discovered')
        database.update_enrollment_contact(cid, status='generated')
        contacts = database.get_enrollment_contacts(sample_batch, status='generated')
        assert len(contacts) == 1

    def test_generated_to_enrolled(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(sample_batch, 'Corp', status='generated')
        database.update_enrollment_contact(cid, status='enrolled', enrolled_at='2025-01-01T00:00:00')
        contacts = database.get_enrollment_contacts(sample_batch, status='enrolled')
        assert len(contacts) == 1
        assert contacts[0]['enrolled_at'] is not None

    def test_discovered_to_failed(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(sample_batch, 'Corp', status='discovered')
        database.update_enrollment_contact(cid, status='failed', error_message='Apollo API timeout')
        contacts = database.get_enrollment_contacts(sample_batch, status='failed')
        assert len(contacts) == 1
        assert contacts[0]['error_message'] == 'Apollo API timeout'

    def test_batch_summary_counts(self, test_db, sample_batch):
        import database
        database.create_enrollment_contact(sample_batch, 'A', status='discovered')
        database.create_enrollment_contact(sample_batch, 'B', status='discovered')
        database.create_enrollment_contact(sample_batch, 'C', status='generated')
        database.create_enrollment_contact(sample_batch, 'D', status='enrolled')
        database.create_enrollment_contact(sample_batch, 'E', status='failed')
        summary = database.get_enrollment_batch_summary(sample_batch)
        assert summary['total'] == 5
        assert summary.get('discovered', 0) == 2
        assert summary.get('generated', 0) == 1
        assert summary.get('enrolled', 0) == 1
        assert summary.get('failed', 0) == 1


class TestNextContactsForPhase:
    """Test the batch-processing contact fetcher."""

    def test_fetches_only_matching_status(self, test_db, sample_batch):
        import database
        database.create_enrollment_contact(sample_batch, 'A', status='discovered')
        database.create_enrollment_contact(sample_batch, 'B', status='discovered')
        database.create_enrollment_contact(sample_batch, 'C', status='generated')
        contacts = database.get_next_contacts_for_phase(sample_batch, 'discovered', limit=10)
        assert len(contacts) == 2
        for c in contacts:
            assert c['status'] == 'discovered'

    def test_respects_limit(self, test_db, sample_batch):
        import database
        for i in range(10):
            database.create_enrollment_contact(sample_batch, f'Corp{i}', status='discovered')
        contacts = database.get_next_contacts_for_phase(sample_batch, 'discovered', limit=3)
        assert len(contacts) == 3

    def test_orders_by_id(self, test_db, sample_batch):
        import database
        ids = []
        for i in range(5):
            cid = database.create_enrollment_contact(sample_batch, f'Corp{i}', status='discovered')
            ids.append(cid)
        contacts = database.get_next_contacts_for_phase(sample_batch, 'discovered', limit=5)
        returned_ids = [c['id'] for c in contacts]
        assert returned_ids == sorted(returned_ids)


class TestPersonalEmailFiltering:
    """Test that personal emails are properly handled in enrollment context."""

    def test_personal_email_stored_but_detectable(self, test_db, sample_batch):
        """Database stores what it's given; filtering is done at application layer."""
        import database
        cid = database.create_enrollment_contact(
            sample_batch, 'Corp',
            email='user@gmail.com',
            status='discovered'
        )
        contacts = database.get_enrollment_contacts(sample_batch)
        assert contacts[0]['email'] == 'user@gmail.com'
