"""
Tests for account field persistence — verifying that ALL fields from
scan results, CSV imports, and Google Sheets sync are attached to
the account as metadata and never dropped.
"""
import json
import os
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret')

from database import (
    init_db, get_db_connection,
    update_account_status, add_account_to_tier_0,
    update_account_metadata, get_report,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Use a fresh in-memory-like temp database for each test."""
    db_path = str(tmp_path / 'test.db')
    with patch('config.Config.DATABASE_PATH', db_path):
        init_db()
        yield db_path


def _get_account(db_path, company_name):
    """Helper: fetch an account row by company name."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT * FROM monitored_accounts WHERE LOWER(company_name) = ?',
        (company_name.lower().strip(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _get_metadata(db_path, company_name):
    """Helper: parse the metadata JSON from an account."""
    account = _get_account(db_path, company_name)
    if not account or not account.get('metadata'):
        return {}
    return json.loads(account['metadata'])


# ──────────────────────────────────────────────────────────────────────
# Scan Results → Account Metadata
# ──────────────────────────────────────────────────────────────────────
class TestScanFieldPersistence:
    """Verify that scan results attach all GitHub API fields to the account."""

    def test_org_name_persisted(self, fresh_db):
        """org_name from scan is saved to account metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'TestCorp',
                'org_login': 'testcorp',
                'org_name': 'TestCorp Official',
                'signals': [],
            })
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('org_name') == 'TestCorp Official'

    def test_org_url_persisted(self, fresh_db):
        """org_url from scan is saved to account metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'TestCorp',
                'org_login': 'testcorp',
                'org_url': 'https://github.com/testcorp',
                'signals': [],
            })
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('org_url') == 'https://github.com/testcorp'

    def test_org_description_persisted(self, fresh_db):
        """org_description from scan is saved to account metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'TestCorp',
                'org_login': 'testcorp',
                'org_description': 'Building great software',
                'signals': [],
            })
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('org_description') == 'Building great software'

    def test_org_public_repos_persisted(self, fresh_db):
        """org_public_repos from scan is saved to account metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'TestCorp',
                'org_login': 'testcorp',
                'org_public_repos': 42,
                'signals': [],
            })
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('org_public_repos') == 42

    def test_total_stars_persisted(self, fresh_db):
        """total_stars from scan is saved to account metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'TestCorp',
                'org_login': 'testcorp',
                'total_stars': 5000,
                'signals': [],
            })
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('total_stars') == 5000

    def test_languages_extracted(self, fresh_db):
        """Language breakdown from repos_scanned is saved to metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'TestCorp',
                'org_login': 'testcorp',
                'signals': [],
                'repos_scanned': [
                    {'name': 'app', 'language': 'TypeScript'},
                    {'name': 'api', 'language': 'Python'},
                    {'name': 'web', 'language': 'TypeScript'},
                    {'name': 'docs', 'language': None},
                ],
            })
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('languages') == {'TypeScript': 2, 'Python': 1}
            assert meta.get('repos_scanned_count') == 4

    def test_contributor_count_persisted(self, fresh_db):
        """contributor_count from scan contributors dict is saved."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'TestCorp',
                'org_login': 'testcorp',
                'signals': [],
                'contributors': {
                    'dev1': {'name': 'Dev 1', 'contributions': 100},
                    'dev2': {'name': 'Dev 2', 'contributions': 50},
                },
            })
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('contributor_count') == 2

    def test_goldilocks_status_persisted(self, fresh_db):
        """goldilocks_status from scoring is saved to metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'TestCorp',
                'org_login': 'testcorp',
                'signals': [],
                'goldilocks_status': 'preparing',
                'intent_score': 75,
            })
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('goldilocks_status') == 'preparing'
            assert meta.get('intent_score') == 75

    def test_all_fields_in_single_scan(self, fresh_db):
        """All scan fields are persisted together in one scan."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            now = datetime.now(timezone.utc).isoformat()
            update_account_status({
                'company_name': 'MegaCorp',
                'org_login': 'megacorp',
                'org_name': 'MegaCorp Inc',
                'org_url': 'https://github.com/megacorp',
                'org_description': 'Enterprise software',
                'org_public_repos': 200,
                'total_stars': 50000,
                'scan_timestamp': now,
                'org_public_members': 150,
                'signals': [],
                'repos_scanned': [
                    {'name': 'platform', 'language': 'Java'},
                    {'name': 'frontend', 'language': 'TypeScript'},
                ],
                'contributors': {
                    'eng1': {'name': 'Engineer', 'contributions': 500},
                },
                'goldilocks_status': 'thinking',
                'intent_score': 40,
                'lead_status': 'qualified',
            })
            meta = _get_metadata(fresh_db, 'megacorp')
            assert meta['org_name'] == 'MegaCorp Inc'
            assert meta['org_url'] == 'https://github.com/megacorp'
            assert meta['org_description'] == 'Enterprise software'
            assert meta['org_public_repos'] == 200
            assert meta['total_stars'] == 50000
            assert meta['org_public_members'] == 150
            assert meta['scan_timestamp'] == now
            assert meta['languages'] == {'Java': 1, 'TypeScript': 1}
            assert meta['repos_scanned_count'] == 2
            assert meta['contributor_count'] == 1
            assert meta['goldilocks_status'] == 'thinking'
            assert meta['intent_score'] == 40
            assert meta['lead_status'] == 'qualified'

    def test_rescan_preserves_csv_metadata(self, fresh_db):
        """Re-scanning preserves CSV/import metadata on the account."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            # First: add via CSV import with metadata
            add_account_to_tier_0('TestCorp', 'testcorp',
                                  annual_revenue='$50M',
                                  website='testcorp.com',
                                  metadata={'industry': 'SaaS', 'employees': '200'})

            # Verify CSV metadata is set
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('industry') == 'SaaS'
            assert meta.get('employees') == '200'

            # Now: scan overwrites with GitHub data
            update_account_status({
                'company_name': 'TestCorp',
                'org_login': 'testcorp',
                'org_name': 'TestCorp Official',
                'total_stars': 1000,
                'signals': [],
            })

            # Verify both CSV and scan metadata are present
            meta = _get_metadata(fresh_db, 'testcorp')
            assert meta.get('industry') == 'SaaS'       # preserved from CSV
            assert meta.get('employees') == '200'        # preserved from CSV
            assert meta.get('org_name') == 'TestCorp Official'  # from scan
            assert meta.get('total_stars') == 1000       # from scan


# ──────────────────────────────────────────────────────────────────────
# CSV Import → Account Metadata
# ──────────────────────────────────────────────────────────────────────
class TestCSVImportFieldPersistence:
    """Verify CSV import attaches all fields to the account."""

    def test_basic_metadata_saved(self, fresh_db):
        """Extra CSV columns are saved to account metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            add_account_to_tier_0('Acme', 'acme',
                                  metadata={'industry': 'Manufacturing', 'hq': 'NYC'})
            meta = _get_metadata(fresh_db, 'acme')
            assert meta['industry'] == 'Manufacturing'
            assert meta['hq'] == 'NYC'

    def test_revenue_and_website_saved(self, fresh_db):
        """annual_revenue and website are saved as first-class columns."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            add_account_to_tier_0('Acme', 'acme',
                                  annual_revenue='$100M',
                                  website='acme.com')
            account = _get_account(fresh_db, 'acme')
            assert account['annual_revenue'] == '$100M'
            assert account['website'] == 'acme.com'

    def test_metadata_merge_on_reimport(self, fresh_db):
        """Re-importing merges new metadata with existing."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            add_account_to_tier_0('Acme', 'acme',
                                  metadata={'industry': 'Manufacturing'})
            add_account_to_tier_0('Acme', 'acme',
                                  metadata={'employees': '500', 'city': 'NYC'})
            meta = _get_metadata(fresh_db, 'acme')
            assert meta['industry'] == 'Manufacturing'  # from first import
            assert meta['employees'] == '500'            # from second import
            assert meta['city'] == 'NYC'                 # from second import

    def test_all_csv_extra_columns_preserved(self, fresh_db):
        """Arbitrary CSV columns are stored as metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            add_account_to_tier_0('BigCo', '',
                                  annual_revenue='$5B',
                                  website='bigco.com',
                                  metadata={
                                      'salesforce_id': 'SF001',
                                      'industry': 'Tech',
                                      'employees': '10000',
                                      'city': 'San Francisco',
                                      'state': 'CA',
                                      'country': 'US',
                                      'custom_field_1': 'value1',
                                      'custom_field_2': 'value2',
                                  })
            meta = _get_metadata(fresh_db, 'bigco')
            assert meta['salesforce_id'] == 'SF001'
            assert meta['industry'] == 'Tech'
            assert meta['employees'] == '10000'
            assert meta['city'] == 'San Francisco'
            assert meta['state'] == 'CA'
            assert meta['country'] == 'US'
            assert meta['custom_field_1'] == 'value1'
            assert meta['custom_field_2'] == 'value2'

    def test_update_account_metadata_function(self, fresh_db):
        """update_account_metadata merges correctly."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            add_account_to_tier_0('TestCo', '', metadata={'a': '1'})
            update_account_metadata('TestCo', {'b': '2', 'c': '3'})
            meta = _get_metadata(fresh_db, 'testco')
            assert meta['a'] == '1'
            assert meta['b'] == '2'
            assert meta['c'] == '3'


# ──────────────────────────────────────────────────────────────────────
# Google Sheets Sync → Account Metadata
# ──────────────────────────────────────────────────────────────────────
class TestSheetsSyncFieldPersistence:
    """Verify Google Sheets sync saves fields to account metadata."""

    def test_store_account_metadata_saves_to_account(self, fresh_db):
        """_store_account_metadata writes to account metadata column."""
        with patch('config.Config.DATABASE_PATH', fresh_db), \
             patch('sheets_sync.get_db_connection') as mock_conn_fn:
            # Create account first
            add_account_to_tier_0('SheetCo', 'sheetco')

            # Set up real DB connection for the system_settings write
            conn = sqlite3.connect(fresh_db)
            conn.row_factory = sqlite3.Row
            mock_conn_fn.return_value = conn

            from sheets_sync import _store_account_metadata
            _store_account_metadata('SheetCo', {
                'domain': 'sheetco.com',
                'industry': 'FinTech',
                'employees': '300',
                'salesforce_id': 'SF_123',
                'city': 'Austin',
                'state': 'TX',
                'country': 'US',
            })

            # Verify metadata was written to the account
            meta = _get_metadata(fresh_db, 'sheetco')
            assert meta.get('domain') == 'sheetco.com'
            assert meta.get('industry') == 'FinTech'
            assert meta.get('employees') == '300'
            assert meta.get('salesforce_id') == 'SF_123'
            assert meta.get('city') == 'Austin'
            assert meta.get('state') == 'TX'
            assert meta.get('country') == 'US'
            assert meta.get('source') == 'google_sheets'


# ──────────────────────────────────────────────────────────────────────
# Edge Cases
# ──────────────────────────────────────────────────────────────────────
class TestFieldPersistenceEdgeCases:
    """Edge cases for metadata persistence."""

    def test_empty_scan_no_metadata(self, fresh_db):
        """Scan with minimal fields still creates account."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'MinimalCo',
                'org_login': 'minimalco',
                'signals': [],
            })
            account = _get_account(fresh_db, 'minimalco')
            assert account is not None
            assert account['github_org'] == 'minimalco'

    def test_none_values_not_stored(self, fresh_db):
        """None values from scan data are not stored in metadata."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            update_account_status({
                'company_name': 'TestCo',
                'org_login': 'testco',
                'org_name': None,
                'org_description': None,
                'signals': [],
            })
            meta = _get_metadata(fresh_db, 'testco')
            assert 'org_name' not in meta
            assert 'org_description' not in meta

    def test_scan_updates_preserve_metadata(self, fresh_db):
        """Second scan updates metadata without losing fields."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            # First scan
            update_account_status({
                'company_name': 'EvolCo',
                'org_login': 'evolco',
                'org_name': 'EvolCo v1',
                'total_stars': 100,
                'signals': [],
            })

            # Second scan - org_name changes, total_stars changes
            update_account_status({
                'company_name': 'EvolCo',
                'org_login': 'evolco',
                'org_name': 'EvolCo v2',
                'total_stars': 200,
                'org_description': 'Now with description',
                'signals': [],
            })

            meta = _get_metadata(fresh_db, 'evolco')
            assert meta['org_name'] == 'EvolCo v2'    # updated
            assert meta['total_stars'] == 200           # updated
            assert meta['org_description'] == 'Now with description'  # added

    def test_no_metadata_column_still_works(self, fresh_db):
        """Account with NULL metadata doesn't crash on scan update."""
        with patch('config.Config.DATABASE_PATH', fresh_db):
            # Manually insert with no metadata
            conn = sqlite3.connect(fresh_db)
            conn.execute(
                'INSERT INTO monitored_accounts (company_name, github_org, current_tier) VALUES (?, ?, ?)',
                ('OldCo', 'oldco', 0))
            conn.commit()
            conn.close()

            # Scan should work and add metadata
            update_account_status({
                'company_name': 'OldCo',
                'org_login': 'oldco',
                'org_name': 'OldCo Inc',
                'total_stars': 500,
                'signals': [],
            })
            meta = _get_metadata(fresh_db, 'oldco')
            assert meta['org_name'] == 'OldCo Inc'
            assert meta['total_stars'] == 500
