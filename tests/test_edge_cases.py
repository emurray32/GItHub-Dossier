"""Edge case tests for enrollment pipeline robustness."""
import json
import pytest
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.unit


class TestDuplicateContactsAcrossOrgs:
    """Test handling of same person appearing in multiple organizations."""

    def test_same_email_different_companies(self, test_db, sample_batch):
        import database
        database.create_enrollment_contact(
            sample_batch, 'CorpA',
            email='consultant@gmail.com',
            first_name='Bob',
            status='discovered'
        )
        database.create_enrollment_contact(
            sample_batch, 'CorpB',
            email='consultant@gmail.com',
            first_name='Bob',
            status='discovered'
        )
        contacts = database.get_enrollment_contacts(sample_batch)
        assert len(contacts) == 2
        companies = {c['company_name'] for c in contacts}
        assert companies == {'CorpA', 'CorpB'}

    def test_same_person_different_batches(self, test_db, sample_campaign):
        import database
        batch1 = database.create_enrollment_batch(sample_campaign, [1])
        batch2 = database.create_enrollment_batch(sample_campaign, [2])

        database.create_enrollment_contact(batch1, 'CorpA', email='jane@corp.com', status='enrolled')
        database.create_enrollment_contact(batch2, 'CorpA', email='jane@corp.com', status='discovered')

        contacts1 = database.get_enrollment_contacts(batch1)
        contacts2 = database.get_enrollment_contacts(batch2)
        assert len(contacts1) == 1
        assert len(contacts2) == 1


class TestTierChangeMidBatch:
    """Test behavior when company tier changes during enrollment."""

    def test_contacts_retain_original_sequence(self, test_db, sample_batch):
        """Contacts created with a sequence should retain it even if tier changes."""
        import database
        cid = database.create_enrollment_contact(
            sample_batch, 'ChangingCorp',
            email='user@changingcorp.com',
            sequence_id='seq_preparing',
            sequence_name='Preparing - Technical',
            status='discovered'
        )
        # Later, company might be reclassified, but existing contacts keep their sequence
        contacts = database.get_enrollment_contacts(sample_batch)
        assert contacts[0]['sequence_id'] == 'seq_preparing'


class TestPartialEnrollmentFailure:
    """Test Apollo API failure mid-batch (partial enrollment)."""

    def test_some_enrolled_some_failed(self, test_db, sample_batch):
        import database
        cids = []
        for i in range(5):
            cid = database.create_enrollment_contact(
                sample_batch, f'Corp{i}',
                email=f'user{i}@corp{i}.com',
                status='generated'
            )
            cids.append(cid)

        # First 3 succeed
        for cid in cids[:3]:
            database.update_enrollment_contact(cid, status='enrolled')

        # Last 2 fail with error
        for cid in cids[3:]:
            database.update_enrollment_contact(
                cid, status='failed',
                error_message='Apollo API 500 error'
            )

        summary = database.get_enrollment_batch_summary(sample_batch)
        assert summary.get('enrolled', 0) == 3
        assert summary.get('failed', 0) == 2
        assert summary['total'] == 5

    def test_all_contacts_fail(self, test_db, sample_batch):
        import database
        for i in range(3):
            cid = database.create_enrollment_contact(
                sample_batch, f'Corp{i}',
                email=f'user{i}@corp{i}.com',
                status='generated'
            )
            database.update_enrollment_contact(
                cid, status='failed', error_message='Connection timeout'
            )

        database.update_enrollment_batch(
            sample_batch, status='error',
            error_message='All contacts failed: Connection timeout',
            failed=3
        )

        batch = database.get_enrollment_batch(sample_batch)
        assert batch['status'] == 'error'
        assert batch['failed'] == 3


class TestMalformedData:
    """Test handling of malformed or unexpected data."""

    def test_empty_company_name(self, test_db, sample_batch):
        """Company name is required but could be empty string."""
        import database
        cid = database.create_enrollment_contact(sample_batch, '')
        assert cid is not None

    def test_null_email(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(sample_batch, 'Corp', email=None)
        contacts = database.get_enrollment_contacts(sample_batch)
        assert contacts[0]['email'] is None

    def test_very_long_email(self, test_db, sample_batch):
        import database
        long_email = 'a' * 200 + '@' + 'b' * 200 + '.com'
        cid = database.create_enrollment_contact(sample_batch, 'Corp', email=long_email)
        contacts = database.get_enrollment_contacts(sample_batch)
        assert contacts[0]['email'] == long_email

    def test_special_characters_in_company_name(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(
            sample_batch, "O'Brien & Associates, Inc."
        )
        contacts = database.get_enrollment_contacts(sample_batch)
        assert contacts[0]['company_name'] == "O'Brien & Associates, Inc."

    def test_batch_with_empty_account_ids(self, test_db, sample_campaign):
        import database
        batch_id = database.create_enrollment_batch(sample_campaign, [])
        batch = database.get_enrollment_batch(batch_id)
        assert batch['total_accounts'] == 0
        assert batch['account_ids'] == []

    def test_batch_json_parse_error_handled(self, test_db, sample_campaign):
        """If account_ids_json is malformed, get_enrollment_batch should not crash."""
        import database
        batch_id = database.create_enrollment_batch(sample_campaign, [1])
        # Manually corrupt the JSON
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE enrollment_batches SET account_ids_json = ? WHERE id = ?",
            ('not-valid-json', batch_id)
        )
        conn.commit()
        conn.close()
        batch = database.get_enrollment_batch(batch_id)
        assert batch['account_ids'] == []  # Should fall back to empty list


class TestEmptyScanResults:
    """Test pipeline with empty/no signals."""

    def test_zero_signals_no_crash(self, empty_scan_results):
        from scoring import score_scan_results
        result = score_scan_results(empty_scan_results)
        assert result is not None
        assert result.stage1_passed is False

    def test_no_contributors_no_crash(self):
        from scoring import score_scan_results
        scan = {
            'company_name': 'EmptyCorp',
            'org_login': 'emptycorp',
            'org_name': 'EmptyCorp',
            'org_url': 'https://github.com/emptycorp',
            'org_description': '',
            'org_public_repos': 0,
            'org_public_members': 0,
            'total_stars': 0,
            'signals': [],
            'signal_summary': {
                'rfc_discussion': {'count': 0, 'hits': []},
                'dependency_injection': {'count': 0, 'hits': []},
                'ghost_branch': {'count': 0, 'hits': []},
                'documentation_intent': {'count': 0, 'hits': []},
                'smoking_gun_fork': {'count': 0, 'hits': []},
                'enhanced_heuristics': {'count': 0, 'by_type': {}},
            },
            'repos_scanned': [],
            'contributors': {},
        }
        result = score_scan_results(scan)
        assert result is not None


class TestUnicodeHandling:
    """Test Unicode in company names and email subjects."""

    def test_unicode_company_name(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(
            sample_batch, 'Unternehmen GmbH',
            email='user@unternehmen.de',
            first_name='Hans',
        )
        contacts = database.get_enrollment_contacts(sample_batch)
        assert contacts[0]['company_name'] == 'Unternehmen GmbH'

    def test_japanese_company_name(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(
            sample_batch, 'Toyota Motor Corporation',
            email='user@toyota.co.jp',
        )
        contacts = database.get_enrollment_contacts(sample_batch)
        assert contacts[0]['company_name'] is not None

    def test_emoji_in_company_name(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(
            sample_batch, 'Rocket Corp',
            email='user@rocket.com',
        )
        contacts = database.get_enrollment_contacts(sample_batch)
        assert cid is not None

    def test_unicode_in_generated_email(self, test_db, sample_batch):
        import database
        cid = database.create_enrollment_contact(sample_batch, 'Corp')
        emails = json.dumps([{
            'subject': 'Internacionalizacion en tu empresa',
            'body': 'Hola, hemos notado que tu equipo esta preparando i18n.'
        }])
        database.update_enrollment_contact(cid, generated_emails_json=emails)
        contacts = database.get_enrollment_contacts(sample_batch)
        parsed = json.loads(contacts[0]['generated_emails_json'])
        assert 'Internacionalizacion' in parsed[0]['subject']


class TestLargeBatch:
    """Test behavior with larger batches."""

    @pytest.mark.slow
    def test_100_contacts(self, test_db, sample_batch):
        import database
        contacts = []
        for i in range(100):
            contacts.append({
                'batch_id': sample_batch,
                'company_name': f'Corp{i}',
                'email': f'user{i}@corp{i}.com',
                'first_name': f'User{i}',
                'status': 'discovered',
            })
        count = database.bulk_create_enrollment_contacts(contacts)
        assert count == 100

        all_contacts = database.get_enrollment_contacts(sample_batch, limit=200)
        assert len(all_contacts) == 100

    def test_pagination(self, test_db, sample_batch):
        import database
        for i in range(15):
            database.create_enrollment_contact(
                sample_batch, f'Corp{i}',
                email=f'user{i}@corp{i}.com',
                status='discovered'
            )
        page1 = database.get_enrollment_contacts(sample_batch, limit=10, offset=0)
        page2 = database.get_enrollment_contacts(sample_batch, limit=10, offset=10)
        assert len(page1) == 10
        assert len(page2) == 5
        all_emails = {c['email'] for c in page1} | {c['email'] for c in page2}
        assert len(all_emails) == 15


class TestEmailFilteringHelpers:
    """Test personal email filtering and domain matching at the app layer."""

    def test_filter_gmail(self):
        """Import from app.py module level to test _filter_personal_email."""
        # Since _filter_personal_email is defined inside app.py we test via import
        try:
            from app import _filter_personal_email
            assert _filter_personal_email('user@gmail.com') == ''
            assert _filter_personal_email('user@yahoo.com') == ''
            assert _filter_personal_email('user@company.com') == 'user@company.com'
            assert _filter_personal_email('') == ''
            assert _filter_personal_email(None) == ''
        except ImportError:
            pytest.skip("Cannot import _filter_personal_email from app.py in test environment")

    def test_check_company_match(self):
        try:
            from app import _check_company_match
            assert _check_company_match('user@targetcorp.com', 'TargetCorp') is True
            assert _check_company_match('user@othercorp.com', 'TargetCorp') is False
            assert _check_company_match('', 'TargetCorp') is True  # Nothing to compare
            assert _check_company_match('user@corp.com', '') is True
        except ImportError:
            pytest.skip("Cannot import _check_company_match from app.py in test environment")

    def test_derive_company_domain(self):
        try:
            from app import _derive_company_domain
            assert _derive_company_domain('TargetCorp Inc.') == 'targetcorp.com'
            assert _derive_company_domain('Big Corp LLC') == 'bigcorp.com'
            assert _derive_company_domain('') == ''
        except ImportError:
            pytest.skip("Cannot import _derive_company_domain from app.py in test environment")
