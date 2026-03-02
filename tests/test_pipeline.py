"""End-to-end pipeline tests with mocked external services."""
import json
import pytest
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.integration


class TestEnrollmentPipelineFlow:
    """Test full enrollment pipeline: batch create -> discover -> generate -> enroll."""

    def test_batch_lifecycle(self, test_db, sample_campaign):
        """Test the complete batch status lifecycle."""
        import database

        # 1. Create batch
        batch_id = database.create_enrollment_batch(sample_campaign, [1, 2, 3])
        batch = database.get_enrollment_batch(batch_id)
        assert batch['status'] == 'pending'

        # 2. Start processing
        database.update_enrollment_batch(batch_id, status='running', current_phase='discovery')
        batch = database.get_enrollment_batch(batch_id)
        assert batch['status'] == 'running'
        assert batch['current_phase'] == 'discovery'

        # 3. Add discovered contacts
        for i, name in enumerate(['Jane', 'John', 'Alice']):
            database.create_enrollment_contact(
                batch_id, f'Corp{i}',
                email=f'{name.lower()}@corp{i}.com',
                first_name=name,
                status='discovered'
            )
        database.update_enrollment_batch(batch_id, discovered=3, total_contacts=3)

        # 4. Move to generation phase
        database.update_enrollment_batch(batch_id, current_phase='generation')
        contacts = database.get_next_contacts_for_phase(batch_id, 'discovered', limit=10)
        assert len(contacts) == 3

        # 5. Generate emails for each contact
        for c in contacts:
            emails = json.dumps([{'subject': 'Test', 'body': f'Hey {c["first_name"]}'}])
            database.update_enrollment_contact(
                c['id'], status='generated', generated_emails_json=emails
            )
        database.update_enrollment_batch(batch_id, generated=3)

        # 6. Enrollment phase
        database.update_enrollment_batch(batch_id, current_phase='enrollment')
        generated = database.get_next_contacts_for_phase(batch_id, 'generated', limit=10)
        assert len(generated) == 3

        for c in generated:
            database.update_enrollment_contact(c['id'], status='enrolled', apollo_contact_id='ac_123')
        database.update_enrollment_batch(batch_id, enrolled=3)

        # 7. Complete
        database.update_enrollment_batch(batch_id, status='completed', current_phase='done')
        batch = database.get_enrollment_batch(batch_id)
        assert batch['status'] == 'completed'
        assert batch['enrolled'] == 3

        summary = database.get_enrollment_batch_summary(batch_id)
        assert summary['total'] == 3
        assert summary.get('enrolled', 0) == 3

    def test_partial_failure_batch(self, test_db, sample_campaign):
        """Test batch where some contacts fail during enrollment."""
        import database

        batch_id = database.create_enrollment_batch(sample_campaign, [1])

        # Add 5 contacts
        cids = []
        for i in range(5):
            cid = database.create_enrollment_contact(
                batch_id, f'Corp{i}',
                email=f'user{i}@corp{i}.com',
                status='discovered'
            )
            cids.append(cid)

        # Generate for all
        for cid in cids:
            database.update_enrollment_contact(cid, status='generated')

        # Enroll 3, fail 2
        for cid in cids[:3]:
            database.update_enrollment_contact(cid, status='enrolled')
        for cid in cids[3:]:
            database.update_enrollment_contact(cid, status='failed', error_message='Apollo API error')

        database.update_enrollment_batch(
            batch_id, enrolled=3, failed=2,
            status='completed_with_errors'
        )

        summary = database.get_enrollment_batch_summary(batch_id)
        assert summary.get('enrolled', 0) == 3
        assert summary.get('failed', 0) == 2
        assert summary['total'] == 5

    def test_pipeline_pause_resume(self, test_db, sample_campaign):
        """Test pausing and resuming a batch."""
        import database

        batch_id = database.create_enrollment_batch(sample_campaign, [1, 2])
        database.update_enrollment_batch(batch_id, status='running', current_phase='discovery')

        # Add some contacts
        database.create_enrollment_contact(batch_id, 'A', email='a@a.com', status='discovered')
        database.create_enrollment_contact(batch_id, 'B', email='b@b.com', status='discovered')

        # Pause
        database.update_enrollment_batch(batch_id, status='paused')
        batch = database.get_enrollment_batch(batch_id)
        assert batch['status'] == 'paused'

        # Resume
        database.update_enrollment_batch(batch_id, status='running')
        batch = database.get_enrollment_batch(batch_id)
        assert batch['status'] == 'running'

        # Contacts should still be there
        contacts = database.get_enrollment_contacts(batch_id)
        assert len(contacts) == 2


class TestCampaignToEnrollmentFlow:
    """Test campaign -> personas -> batch -> contacts flow."""

    def test_full_campaign_setup(self, test_db, sample_campaign):
        import database

        # Create personas
        database.create_campaign_persona(
            sample_campaign, 'Engineering',
            titles=['VP Engineering', 'Director of Engineering'],
            seniorities=['vp', 'director'],
            sequence_id='seq_eng',
            sequence_name='Preparing - Technical',
            priority=0
        )
        database.create_campaign_persona(
            sample_campaign, 'Product',
            titles=['VP Product', 'Head of Product'],
            seniorities=['vp'],
            sequence_id='seq_prod',
            sequence_name='Preparing - Product',
            priority=1
        )

        # Create batch
        batch_id = database.create_enrollment_batch(sample_campaign, [1])

        # Simulate discovery: found contacts for each persona
        database.create_enrollment_contact(
            batch_id, 'TargetCorp',
            email='eng@targetcorp.com',
            persona_name='Engineering',
            sequence_id='seq_eng',
            status='discovered'
        )
        database.create_enrollment_contact(
            batch_id, 'TargetCorp',
            email='product@targetcorp.com',
            persona_name='Product',
            sequence_id='seq_prod',
            status='discovered'
        )

        # Verify personas linked correctly
        contacts = database.get_enrollment_contacts(batch_id)
        assert len(contacts) == 2
        personas = {c['persona_name'] for c in contacts}
        assert personas == {'Engineering', 'Product'}
        sequences = {c['sequence_id'] for c in contacts}
        assert sequences == {'seq_eng', 'seq_prod'}


class TestErrorRecovery:
    """Test error handling and recovery during pipeline execution."""

    def test_batch_error_message(self, test_db, sample_campaign):
        import database
        batch_id = database.create_enrollment_batch(sample_campaign, [1])
        database.update_enrollment_batch(
            batch_id,
            status='error',
            error_message='Apollo API rate limit exceeded after 50 contacts'
        )
        batch = database.get_enrollment_batch(batch_id)
        assert batch['status'] == 'error'
        assert 'rate limit' in batch['error_message'].lower()

    def test_contact_error_preserves_prior_status(self, test_db, sample_batch):
        """A failed contact should still have the error_message stored."""
        import database
        cid = database.create_enrollment_contact(
            sample_batch, 'Corp', email='test@corp.com', status='generated'
        )
        database.update_enrollment_contact(
            cid, status='failed',
            error_message='Timeout connecting to Apollo API'
        )
        contacts = database.get_enrollment_contacts(sample_batch, status='failed')
        assert len(contacts) == 1
        assert contacts[0]['error_message'] == 'Timeout connecting to Apollo API'

    def test_multiple_batches_independent(self, test_db, sample_campaign):
        """Failure in one batch should not affect another."""
        import database
        batch1 = database.create_enrollment_batch(sample_campaign, [1])
        batch2 = database.create_enrollment_batch(sample_campaign, [2])

        database.update_enrollment_batch(batch1, status='error', error_message='Failed')
        database.update_enrollment_batch(batch2, status='completed')

        b1 = database.get_enrollment_batch(batch1)
        b2 = database.get_enrollment_batch(batch2)
        assert b1['status'] == 'error'
        assert b2['status'] == 'completed'


class TestScoringToEnrollmentIntegration:
    """Test that scoring results can drive enrollment decisions."""

    def test_preparing_scan_creates_hot_lead(self, preparing_scan_results):
        """PREPARING scans should produce high p_intent suitable for enrollment."""
        from scoring import score_scan_results
        result = score_scan_results(preparing_scan_results)
        assert result.p_intent > 0.5
        assert result.stage1_passed is True

    def test_empty_scan_not_enrolled(self, empty_scan_results):
        """Empty scans should not pass stage 1."""
        from scoring import score_scan_results
        result = score_scan_results(empty_scan_results)
        assert result.stage1_passed is False
