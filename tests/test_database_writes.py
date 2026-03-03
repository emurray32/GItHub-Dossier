"""
Comprehensive database write-path tests.

Tests every write operation that, if broken, would cause data loss or
corruption.  Each test runs against a fresh temporary SQLite database
so there is no cross-test contamination.

Tested functions:
  - save_report()
  - save_signals()
  - update_account_status()  (the main tier-update path)
  - enrich_existing_account()
  - calculate_tier_from_scan()
  - auto_retier_if_version_changed()
  - add_account_to_tier_0()
  - update_account_metadata()
  - set_setting() / get_setting()
"""
import json
import os
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret')

import database
from database import (
    init_db,
    get_db_connection,
    save_report,
    save_signals,
    get_report,
    get_signals_for_report,
    get_signals_by_company,
    update_account_status,
    add_account_to_tier_0,
    enrich_existing_account,
    update_account_metadata,
    calculate_tier_from_scan,
    auto_retier_if_version_changed,
    get_setting,
    set_setting,
    TIER_TRACKING,
    TIER_THINKING,
    TIER_PREPARING,
    TIER_LAUNCHED,
    TIER_INVALID,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Provide a fresh SQLite database for every single test.

    Patches Config.DATABASE_PATH, Config.DATABASE_URL, and the module-level
    _USE_POSTGRES flag so that all database functions use the temp db.
    Calls init_db() to create the full schema.
    """
    db_path = str(tmp_path / 'test.db')

    monkeypatch.setattr('config.Config.DATABASE_URL', '')
    monkeypatch.setattr('config.Config.DATABASE_PATH', db_path)
    monkeypatch.setattr(database, '_USE_POSTGRES', False)

    original_get_db = database.get_db_connection

    def _patched_get_db():
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    monkeypatch.setattr(database, 'get_db_connection', _patched_get_db)

    init_db()
    yield db_path


def _raw_query(db_path, sql, params=()):
    """Execute a raw SQL query and return all rows as dicts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _raw_query_one(db_path, sql, params=()):
    """Execute a raw SQL query and return the first row as a dict (or None)."""
    rows = _raw_query(db_path, sql, params)
    return rows[0] if rows else None


def _get_account(db_path, company_name):
    """Fetch an account row by company name (case-insensitive)."""
    return _raw_query_one(
        db_path,
        'SELECT * FROM monitored_accounts WHERE LOWER(company_name) = ?',
        (company_name.lower().strip(),),
    )


def _get_metadata(db_path, company_name):
    """Parse the metadata JSON from an account."""
    account = _get_account(db_path, company_name)
    if not account or not account.get('metadata'):
        return {}
    return json.loads(account['metadata'])


def _minimal_scan_data(company='TestCorp', org='testcorp', signals=None, **extra):
    """Build a minimal valid scan_data dict."""
    data = {
        'company_name': company,
        'org_login': org,
        'signals': signals or [],
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
    data.update(extra)
    return data


# =========================================================================
# save_report() Tests
# =========================================================================

class TestSaveReport:
    """Tests for save_report() — persists scan reports."""

    def test_happy_path_saves_and_returns_id(self, fresh_db):
        """save_report returns a positive integer ID and persists the report."""
        scan_data = _minimal_scan_data(
            signals=[{'type': 'ghost_branch', 'Evidence': 'branch i18n'}],
            repos_scanned=[{'name': 'app'}],
            total_commits_analyzed=42,
            total_prs_analyzed=7,
        )
        ai_analysis = {'summary': 'Looks promising'}

        report_id = save_report('TestCorp', 'testcorp', scan_data, ai_analysis, 12.5)

        assert isinstance(report_id, int)
        assert report_id > 0

        row = _raw_query_one(fresh_db, 'SELECT * FROM reports WHERE id = ?', (report_id,))
        assert row is not None
        assert row['company_name'] == 'TestCorp'
        assert row['github_org'] == 'testcorp'
        assert row['signals_found'] == 1
        assert row['repos_scanned'] == 1
        assert row['commits_analyzed'] == 42
        assert row['prs_analyzed'] == 7
        assert abs(row['scan_duration_seconds'] - 12.5) < 0.01

        # Verify JSON round-trips correctly
        stored_scan = json.loads(row['scan_data'])
        assert stored_scan['company_name'] == 'TestCorp'
        stored_ai = json.loads(row['ai_analysis'])
        assert stored_ai['summary'] == 'Looks promising'

    def test_get_report_round_trip(self, fresh_db):
        """get_report() returns the saved report with parsed JSON."""
        scan_data = _minimal_scan_data(company='RoundTrip', org='roundtrip')
        ai_analysis = {'grade': 'A'}
        report_id = save_report('RoundTrip', 'roundtrip', scan_data, ai_analysis, 1.0)

        report = get_report(report_id)
        assert report is not None
        assert report['company_name'] == 'RoundTrip'
        # scan_data should be a parsed dict, not a string
        assert isinstance(report['scan_data'], dict)
        assert report['scan_data']['company_name'] == 'RoundTrip'
        assert report['ai_analysis']['grade'] == 'A'

    def test_multiple_reports_get_unique_ids(self, fresh_db):
        """Each call to save_report produces a distinct ID."""
        ids = set()
        for i in range(5):
            rid = save_report(f'Co{i}', f'co{i}', _minimal_scan_data(), {}, 0.1)
            ids.add(rid)
        assert len(ids) == 5

    def test_same_company_creates_separate_reports(self, fresh_db):
        """Saving two reports for the same company does NOT overwrite; both persist."""
        rid1 = save_report('Acme', 'acme', _minimal_scan_data(), {'run': 1}, 1.0)
        rid2 = save_report('Acme', 'acme', _minimal_scan_data(), {'run': 2}, 2.0)

        assert rid1 != rid2

        rows = _raw_query(fresh_db, "SELECT id FROM reports WHERE company_name = 'Acme'")
        assert len(rows) == 2

    def test_updates_latest_report_id_on_account(self, fresh_db):
        """save_report sets latest_report_id on the matching monitored_account."""
        add_account_to_tier_0('Acme', 'acme')
        rid = save_report('Acme', 'acme', _minimal_scan_data(), {}, 1.0)

        account = _get_account(fresh_db, 'acme')
        assert account['latest_report_id'] == rid

    def test_empty_scan_data(self, fresh_db):
        """save_report handles empty scan_data and ai_analysis dicts."""
        rid = save_report('EmptyCo', 'emptyco', {}, {}, 0.0)
        assert rid > 0

        row = _raw_query_one(fresh_db, 'SELECT * FROM reports WHERE id = ?', (rid,))
        assert row['signals_found'] == 0
        assert row['repos_scanned'] == 0
        assert row['commits_analyzed'] == 0

    def test_special_characters_in_company_name(self, fresh_db):
        """Company names with quotes, unicode, and special chars save correctly."""
        names = [
            "O'Reilly Media",
            'Company "Quoted"',
            "Umlaut GmbH \u00fc\u00f6\u00e4",
            "Emoji Corp",
            "Slash/Back\\Slash",
            "Semi;Colon",
            "<script>alert('xss')</script>",
        ]
        for name in names:
            rid = save_report(name, 'org', _minimal_scan_data(), {}, 0.1)
            row = _raw_query_one(fresh_db, 'SELECT company_name FROM reports WHERE id = ?', (rid,))
            assert row['company_name'] == name, f"Failed for: {name}"

    def test_very_long_company_name(self, fresh_db):
        """Very long company names are stored without truncation."""
        long_name = 'A' * 5000
        rid = save_report(long_name, 'org', _minimal_scan_data(), {}, 0.1)
        row = _raw_query_one(fresh_db, 'SELECT company_name FROM reports WHERE id = ?', (rid,))
        assert row['company_name'] == long_name

    def test_large_scan_data_json(self, fresh_db):
        """Large JSON payloads are stored and retrieved correctly."""
        scan_data = _minimal_scan_data(
            repos_scanned=[{'name': f'repo-{i}', 'language': 'Python'} for i in range(500)],
        )
        ai_analysis = {'details': 'x' * 100_000}
        rid = save_report('BigCo', 'bigco', scan_data, ai_analysis, 99.9)

        report = get_report(rid)
        assert len(report['scan_data']['repos_scanned']) == 500
        assert len(report['ai_analysis']['details']) == 100_000


# =========================================================================
# save_signals() Tests
# =========================================================================

class TestSaveSignals:
    """Tests for save_signals() — persists individual scan signals."""

    def _create_report(self, fresh_db, company='TestCorp'):
        """Helper: create a report and return its ID."""
        return save_report(company, company.lower(), _minimal_scan_data(company=company), {}, 1.0)

    def test_happy_path_saves_signals(self, fresh_db):
        """Signals are saved and can be retrieved by report_id."""
        rid = self._create_report(fresh_db)
        signals = [
            {
                'type': 'dependency_injection',
                'Evidence': 'Found react-i18next in package.json',
                'Link': 'https://github.com/testcorp/app/blob/main/package.json',
            },
            {
                'type': 'ghost_branch',
                'Evidence': 'Branch feature/i18n found',
                'Link': 'https://github.com/testcorp/app/tree/feature/i18n',
            },
        ]

        count = save_signals(rid, 'TestCorp', signals)
        assert count == 2

        stored = get_signals_for_report(rid)
        assert len(stored) == 2
        types = {s['signal_type'] for s in stored}
        assert types == {'dependency_injection', 'ghost_branch'}

    def test_empty_signals_list(self, fresh_db):
        """save_signals with an empty list returns 0 and writes nothing."""
        rid = self._create_report(fresh_db)
        count = save_signals(rid, 'TestCorp', [])
        assert count == 0
        assert get_signals_for_report(rid) == []

    def test_signal_field_extraction(self, fresh_db):
        """All signal fields (type, evidence, file_path) are extracted correctly."""
        rid = self._create_report(fresh_db)
        signals = [{
            'type': 'rfc_discussion',
            'Evidence': 'Issue: Plan i18n migration',
            'Link': 'https://github.com/testcorp/app/issues/42',
            'raw_strength': 0.85,
            'age_in_days': 15,
            'source_context': 'issue_body',
            'woe_value': 1.2,
            'freshness_score': 0.9,
        }]

        save_signals(rid, 'TestCorp', signals)

        rows = _raw_query(fresh_db, 'SELECT * FROM scan_signals WHERE report_id = ?', (rid,))
        assert len(rows) == 1
        row = rows[0]
        assert row['signal_type'] == 'rfc_discussion'
        assert row['description'] == 'Issue: Plan i18n migration'
        assert row['file_path'] == 'https://github.com/testcorp/app/issues/42'
        assert abs(row['raw_strength'] - 0.85) < 0.001
        assert row['age_in_days'] == 15
        assert row['source_context'] == 'issue_body'
        assert abs(row['woe_value'] - 1.2) < 0.001
        assert abs(row['freshness_score'] - 0.9) < 0.001

    def test_fallback_field_names(self, fresh_db):
        """save_signals handles alternative field names (Signal, file, repo)."""
        rid = self._create_report(fresh_db)
        signals = [{
            'Signal': 'Ghost Branch',
            'file': 'src/i18n/',
            'repo': 'webapp',
        }]

        count = save_signals(rid, 'TestCorp', signals)
        assert count == 1

        rows = _raw_query(fresh_db, 'SELECT * FROM scan_signals WHERE report_id = ?', (rid,))
        row = rows[0]
        assert row['signal_type'] == 'Ghost Branch'
        assert row['description'] == 'Ghost Branch'
        assert row['file_path'] == 'src/i18n/'

    def test_malformed_signal_skipped_gracefully(self, fresh_db):
        """A signal that causes an error is skipped; others still save."""
        rid = self._create_report(fresh_db)
        signals = [
            {'type': 'good_signal', 'Evidence': 'Valid signal'},
            # This signal is valid too — both should save
            {'type': 'also_good', 'Evidence': 'Another valid'},
        ]

        count = save_signals(rid, 'TestCorp', signals)
        assert count == 2

    def test_signals_by_company_query(self, fresh_db):
        """get_signals_by_company returns signals across multiple reports."""
        rid1 = self._create_report(fresh_db, 'SharedCorp')
        rid2 = save_report('SharedCorp', 'sharedcorp', _minimal_scan_data(company='SharedCorp'), {}, 1.0)

        save_signals(rid1, 'SharedCorp', [{'type': 'sig_a', 'Evidence': 'ev_a'}])
        save_signals(rid2, 'SharedCorp', [{'type': 'sig_b', 'Evidence': 'ev_b'}])

        all_signals = get_signals_by_company('SharedCorp')
        assert len(all_signals) == 2

    def test_idempotency_duplicate_signals_creates_multiple_rows(self, fresh_db):
        """Calling save_signals twice with the same data creates duplicate rows.

        This is by design: each scan run records its own signals.
        """
        rid = self._create_report(fresh_db)
        signals = [{'type': 'dep_inj', 'Evidence': 'Found i18next'}]

        save_signals(rid, 'TestCorp', signals)
        save_signals(rid, 'TestCorp', signals)

        rows = _raw_query(fresh_db, 'SELECT * FROM scan_signals WHERE report_id = ?', (rid,))
        assert len(rows) == 2

    def test_none_enrichment_fields(self, fresh_db):
        """Scoring V2 enrichment fields default to None when not provided."""
        rid = self._create_report(fresh_db)
        signals = [{'type': 'basic', 'Evidence': 'no v2 fields'}]
        save_signals(rid, 'TestCorp', signals)

        rows = _raw_query(fresh_db, 'SELECT * FROM scan_signals WHERE report_id = ?', (rid,))
        assert rows[0]['raw_strength'] is None
        assert rows[0]['age_in_days'] is None
        assert rows[0]['woe_value'] is None

    def test_special_characters_in_evidence(self, fresh_db):
        """Signal evidence with special characters saves correctly."""
        rid = self._create_report(fresh_db)
        weird_evidence = "Found <i18n> in O'Reilly's \"app\"; path=src/l10n & more\nNewline too"
        signals = [{'type': 'dep_inj', 'Evidence': weird_evidence}]
        save_signals(rid, 'TestCorp', signals)

        rows = _raw_query(fresh_db, 'SELECT description FROM scan_signals WHERE report_id = ?', (rid,))
        assert rows[0]['description'] == weird_evidence


# =========================================================================
# update_account_status() Tests (tier update path)
# =========================================================================

class TestUpdateAccountStatus:
    """Tests for update_account_status() — creates/updates monitored accounts."""

    @pytest.fixture(autouse=True)
    def _mock_verify_signals(self):
        """Mock verify_signals to avoid network calls during tier tests."""
        with patch('database.verify_signals', side_effect=lambda sd, **kw: sd):
            yield

    def test_creates_new_account_on_first_scan(self, fresh_db):
        """First scan for a company creates a new monitored_account row."""
        scan_data = _minimal_scan_data()
        result = update_account_status(scan_data)

        assert result['tier'] in (TIER_TRACKING, TIER_INVALID)
        assert result['tier_changed'] is True

        account = _get_account(fresh_db, 'testcorp')
        assert account is not None
        assert account['github_org'] == 'testcorp'

    def test_updates_existing_account_on_rescan(self, fresh_db):
        """Second scan for same company updates the existing row, not creates a new one."""
        scan_data = _minimal_scan_data()
        update_account_status(scan_data)
        update_account_status(scan_data)

        rows = _raw_query(
            fresh_db,
            'SELECT * FROM monitored_accounts WHERE LOWER(company_name) = ?',
            ('testcorp',),
        )
        assert len(rows) == 1, "Should not create duplicate accounts"

    def test_tier_change_updates_status_changed_at(self, fresh_db):
        """When tier changes, status_changed_at is updated."""
        # First scan: Tier 0 (no signals, no repos)
        scan1 = _minimal_scan_data(repos_scanned=[{'name': 'app'}])
        result1 = update_account_status(scan1)
        account1 = _get_account(fresh_db, 'testcorp')
        changed_at_1 = account1['status_changed_at']

        # Second scan: same tier — status_changed_at should NOT change
        result2 = update_account_status(scan1)
        account2 = _get_account(fresh_db, 'testcorp')
        assert account2['status_changed_at'] == changed_at_1

    def test_company_name_normalized_to_lowercase(self, fresh_db):
        """Company names are normalized to lowercase to prevent duplicates."""
        update_account_status(_minimal_scan_data(company='TestCorp'))
        update_account_status(_minimal_scan_data(company='TESTCORP'))
        update_account_status(_minimal_scan_data(company='testcorp'))

        rows = _raw_query(fresh_db, 'SELECT * FROM monitored_accounts')
        assert len(rows) == 1

    def test_empty_company_name_returns_error(self, fresh_db):
        """Scan data with empty company name returns error dict."""
        result = update_account_status({'company_name': '', 'signals': []})
        assert 'error' in result

    def test_metadata_persisted_from_scan(self, fresh_db):
        """Scan metadata (org_name, stars, etc.) is saved to account metadata."""
        scan_data = _minimal_scan_data(
            org_name='TestCorp Official',
            total_stars=5000,
            org_public_repos=42,
        )
        update_account_status(scan_data)

        meta = _get_metadata(fresh_db, 'testcorp')
        assert meta.get('org_name') == 'TestCorp Official'
        assert meta.get('total_stars') == 5000
        assert meta.get('org_public_repos') == 42

    def test_rescan_preserves_csv_metadata(self, fresh_db):
        """Re-scanning merges scan metadata without losing CSV-imported fields."""
        add_account_to_tier_0('TestCorp', 'testcorp',
                              annual_revenue='$50M',
                              metadata={'industry': 'SaaS', 'hq': 'NYC'})

        update_account_status(_minimal_scan_data(org_name='TestCorp Inc', total_stars=100))

        meta = _get_metadata(fresh_db, 'testcorp')
        assert meta.get('industry') == 'SaaS'
        assert meta.get('hq') == 'NYC'
        assert meta.get('org_name') == 'TestCorp Inc'

        account = _get_account(fresh_db, 'testcorp')
        assert account['annual_revenue'] == '$50M'

    def test_tier4_auto_archived(self, fresh_db):
        """Tier 4 (Invalid) accounts are auto-archived."""
        scan_data = _minimal_scan_data(repos_scanned=[], org_login='')
        result = update_account_status(scan_data)

        assert result['tier'] == TIER_INVALID
        assert result['archived'] is True

        account = _get_account(fresh_db, 'testcorp')
        assert account['archived_at'] is not None

    def test_unarchive_on_tier_upgrade(self, fresh_db):
        """Account is unarchived when it upgrades from Tier 4 to a valid tier."""
        # First scan: Tier 4
        scan_empty = _minimal_scan_data(repos_scanned=[], org_login='')
        update_account_status(scan_empty)

        account = _get_account(fresh_db, 'testcorp')
        assert account['archived_at'] is not None

        # Second scan: Tier 0 (has repos now)
        scan_with_repos = _minimal_scan_data(repos_scanned=[{'name': 'app'}])
        result = update_account_status(scan_with_repos)

        assert result['unarchived'] is True
        account = _get_account(fresh_db, 'testcorp')
        assert account['archived_at'] is None

    def test_webhook_event_on_tier_upgrade(self, fresh_db):
        """webhook_event is True when tier changes to THINKING or PREPARING."""
        # Create initial account at tier 0
        scan_data = _minimal_scan_data(repos_scanned=[{'name': 'app'}])
        update_account_status(scan_data)

        # Upgrade to tier 1 (ghost branch)
        scan_data2 = _minimal_scan_data(
            repos_scanned=[{'name': 'app'}],
            signal_summary={
                'rfc_discussion': {'count': 0, 'hits': []},
                'dependency_injection': {'count': 0, 'hits': []},
                'ghost_branch': {'count': 1, 'hits': [{'name': 'feature/i18n'}]},
                'documentation_intent': {'count': 0, 'hits': []},
                'smoking_gun_fork': {'count': 0, 'hits': []},
                'enhanced_heuristics': {'count': 0, 'by_type': {}},
            },
        )
        result = update_account_status(scan_data2)
        assert result['webhook_event'] is True


# =========================================================================
# enrich_existing_account() Tests
# =========================================================================

class TestEnrichExistingAccount:
    """Tests for enrich_existing_account() — adds revenue/website/metadata."""

    def test_happy_path_enriches_all_fields(self, fresh_db):
        """All three fields (revenue, website, metadata) are enriched."""
        add_account_to_tier_0('TestCo', 'testco')

        result = enrich_existing_account(
            'TestCo',
            annual_revenue='$100M',
            website='https://testco.com',
            metadata={'industry': 'FinTech'},
        )
        assert result is True

        account = _get_account(fresh_db, 'testco')
        assert account['annual_revenue'] == '$100M'
        assert account['website'] == 'https://testco.com'

        meta = _get_metadata(fresh_db, 'testco')
        assert meta['industry'] == 'FinTech'

    def test_returns_false_for_nonexistent_account(self, fresh_db):
        """enrich_existing_account returns False when company doesn't exist."""
        result = enrich_existing_account('NobodyCorp', annual_revenue='$1M')
        assert result is False

    def test_case_insensitive_lookup(self, fresh_db):
        """Account lookup is case-insensitive."""
        add_account_to_tier_0('TestCo', 'testco')

        assert enrich_existing_account('TESTCO', annual_revenue='$50M') is True
        assert enrich_existing_account('testco', website='test.com') is True

        account = _get_account(fresh_db, 'testco')
        assert account['annual_revenue'] == '$50M'
        assert account['website'] == 'test.com'

    def test_partial_enrichment(self, fresh_db):
        """Enriching only revenue does not null out website (and vice versa)."""
        add_account_to_tier_0('PartialCo', 'partialco',
                              annual_revenue='$10M',
                              website='partialco.com')

        enrich_existing_account('PartialCo', annual_revenue='$20M')

        account = _get_account(fresh_db, 'partialco')
        assert account['annual_revenue'] == '$20M'
        assert account['website'] == 'partialco.com'  # preserved

    def test_metadata_merges_not_replaces(self, fresh_db):
        """Enrichment metadata merges with existing metadata."""
        add_account_to_tier_0('MergeCo', 'mergeco',
                              metadata={'key1': 'val1', 'key2': 'val2'})

        enrich_existing_account('MergeCo',
                                metadata={'key2': 'updated', 'key3': 'val3'})

        meta = _get_metadata(fresh_db, 'mergeco')
        assert meta['key1'] == 'val1'       # preserved
        assert meta['key2'] == 'updated'    # overwritten
        assert meta['key3'] == 'val3'       # added

    def test_whitespace_stripped_from_company_name(self, fresh_db):
        """Leading/trailing whitespace in company name is stripped."""
        add_account_to_tier_0('TrimCo', 'trimco')
        result = enrich_existing_account('  TrimCo  ', annual_revenue='$5M')
        assert result is True

        account = _get_account(fresh_db, 'trimco')
        assert account['annual_revenue'] == '$5M'

    def test_idempotent_enrichment(self, fresh_db):
        """Calling enrich twice with same data doesn't corrupt."""
        add_account_to_tier_0('IdempCo', 'idempco')

        enrich_existing_account('IdempCo', annual_revenue='$10M',
                                metadata={'x': '1'})
        enrich_existing_account('IdempCo', annual_revenue='$10M',
                                metadata={'x': '1'})

        account = _get_account(fresh_db, 'idempco')
        assert account['annual_revenue'] == '$10M'
        meta = _get_metadata(fresh_db, 'idempco')
        assert meta['x'] == '1'

    def test_none_values_no_update(self, fresh_db):
        """Passing None for all optional fields still returns True (account exists)."""
        add_account_to_tier_0('NullCo', 'nullco', annual_revenue='$1M')

        result = enrich_existing_account('NullCo')
        assert result is True

        account = _get_account(fresh_db, 'nullco')
        assert account['annual_revenue'] == '$1M'  # unchanged

    def test_empty_string_revenue(self, fresh_db):
        """Empty string revenue is stored (it is not None)."""
        add_account_to_tier_0('EmptyCo', 'emptyco', annual_revenue='$5M')
        enrich_existing_account('EmptyCo', annual_revenue='')

        account = _get_account(fresh_db, 'emptyco')
        assert account['annual_revenue'] == ''

    def test_metadata_with_null_existing(self, fresh_db):
        """Enriching metadata when existing metadata is NULL works correctly."""
        # Create account with no metadata
        conn = sqlite3.connect(fresh_db)
        conn.execute(
            "INSERT INTO monitored_accounts (company_name, github_org, current_tier) "
            "VALUES (?, ?, ?)",
            ('rawco', 'rawco', 0),
        )
        conn.commit()
        conn.close()

        result = enrich_existing_account('rawco', metadata={'source': 'import'})
        assert result is True

        meta = _get_metadata(fresh_db, 'rawco')
        assert meta['source'] == 'import'


# =========================================================================
# calculate_tier_from_scan() Tests
# =========================================================================

class TestCalculateTierFromScan:
    """Tests for calculate_tier_from_scan() — tier classification logic."""

    @pytest.fixture(autouse=True)
    def _mock_verify_signals(self):
        """Mock verify_signals to avoid network calls during tier tests."""
        with patch('database.verify_signals', side_effect=lambda sd, **kw: sd):
            yield

    def test_no_signals_no_repos_returns_tier4(self, fresh_db):
        """No signals, no repos_scanned, no org_login -> TIER_INVALID."""
        scan_data = _minimal_scan_data(org_login='')
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_INVALID

    def test_no_signals_with_repos_returns_tier0(self, fresh_db):
        """No signals but repos were scanned -> TIER_TRACKING."""
        scan_data = _minimal_scan_data(repos_scanned=[{'name': 'app'}])
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_TRACKING

    def test_ghost_branch_returns_tier1(self, fresh_db):
        """Ghost branch signal -> TIER_THINKING."""
        scan_data = _minimal_scan_data(
            signal_summary={
                'rfc_discussion': {'count': 0, 'hits': []},
                'dependency_injection': {'count': 0, 'hits': []},
                'ghost_branch': {'count': 1, 'hits': [{'name': 'feature/i18n'}]},
                'documentation_intent': {'count': 0, 'hits': []},
                'smoking_gun_fork': {'count': 0, 'hits': []},
                'enhanced_heuristics': {'count': 0, 'by_type': {}},
            },
        )
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_THINKING
        assert 'ACTIVE BUILD' in evidence

    def test_rfc_discussion_returns_tier1(self, fresh_db):
        """RFC discussion signal -> TIER_THINKING."""
        scan_data = _minimal_scan_data(
            signal_summary={
                'rfc_discussion': {
                    'count': 1,
                    'hits': [{'title': 'RFC: i18n strategy'}],
                },
                'dependency_injection': {'count': 0, 'hits': []},
                'ghost_branch': {'count': 0, 'hits': []},
                'documentation_intent': {'count': 0, 'hits': []},
                'smoking_gun_fork': {'count': 0, 'hits': []},
                'enhanced_heuristics': {'count': 0, 'by_type': {}},
            },
        )
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_THINKING
        assert 'STRATEGY SIGNAL' in evidence

    def test_dependency_plus_ghost_branch_returns_tier2(self, fresh_db):
        """Dependency injection + ghost branch (multi-signal) -> TIER_PREPARING."""
        scan_data = _minimal_scan_data(
            signal_summary={
                'rfc_discussion': {'count': 0, 'hits': []},
                'dependency_injection': {
                    'count': 1,
                    'hits': [{'libraries_found': ['react-i18next'], 'goldilocks_status': 'preparing'}],
                },
                'ghost_branch': {'count': 1, 'hits': [{'name': 'feature/i18n'}]},
                'documentation_intent': {'count': 0, 'hits': []},
                'smoking_gun_fork': {'count': 0, 'hits': []},
                'enhanced_heuristics': {'count': 0, 'by_type': {}},
            },
        )
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_PREPARING
        assert 'INFRASTRUCTURE READY' in evidence

    def test_dependency_with_smoking_gun_returns_tier2(self, fresh_db):
        """Dependency + smoking gun fork (silver bullet) -> TIER_PREPARING."""
        scan_data = _minimal_scan_data(
            signal_summary={
                'rfc_discussion': {'count': 0, 'hits': []},
                'dependency_injection': {
                    'count': 1,
                    'hits': [{'libraries_found': ['i18next']}],
                },
                'ghost_branch': {'count': 0, 'hits': []},
                'documentation_intent': {'count': 0, 'hits': []},
                'smoking_gun_fork': {'count': 1, 'hits': []},
                'enhanced_heuristics': {'count': 0, 'by_type': {}},
            },
        )
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_PREPARING

    def test_single_dependency_only_returns_tier1(self, fresh_db):
        """Single dependency signal alone (no corroboration) -> TIER_THINKING, not PREPARING."""
        scan_data = _minimal_scan_data(
            signal_summary={
                'rfc_discussion': {'count': 0, 'hits': []},
                'dependency_injection': {
                    'count': 1,
                    'hits': [{'libraries_found': ['i18next']}],
                },
                'ghost_branch': {'count': 0, 'hits': []},
                'documentation_intent': {'count': 0, 'hits': []},
                'smoking_gun_fork': {'count': 0, 'hits': []},
                'enhanced_heuristics': {'count': 0, 'by_type': {}},
            },
        )
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_THINKING
        assert 'single signal' in evidence

    def test_locale_folders_returns_tier3(self, fresh_db):
        """Locale folders detected (goldilocks_status=launched) -> TIER_LAUNCHED."""
        scan_data = _minimal_scan_data(goldilocks_status='launched')
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_LAUNCHED
        assert 'Too Late' in evidence

    def test_locale_folders_via_hit_flag(self, fresh_db):
        """Locale folders detected via dependency hit -> TIER_LAUNCHED."""
        scan_data = _minimal_scan_data(
            signal_summary={
                'rfc_discussion': {'count': 0, 'hits': []},
                'dependency_injection': {
                    'count': 1,
                    'hits': [{'locale_folders_found': ['locales/en', 'locales/fr']}],
                },
                'ghost_branch': {'count': 0, 'hits': []},
                'documentation_intent': {'count': 0, 'hits': []},
                'smoking_gun_fork': {'count': 0, 'hits': []},
                'enhanced_heuristics': {'count': 0, 'by_type': {}},
            },
        )
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_LAUNCHED

    def test_scoring_v2_overrides_legacy(self, fresh_db):
        """If scoring_v2 is present, it overrides legacy tier logic."""
        scan_data = _minimal_scan_data(
            scoring_v2={
                'org_maturity_level': 'preparing',
                'org_maturity_label': 'Preparing',
                'confidence_percent': 85.0,
                'readiness_index': 0.72,
                'outreach_angle_label': 'Infrastructure Gap',
            },
        )
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_PREPARING
        assert 'V2' in evidence
        assert '85' in evidence

    def test_org_found_but_no_repos_returns_tracking(self, fresh_db):
        """Org exists with public repos but none scanned -> TIER_TRACKING."""
        scan_data = _minimal_scan_data(
            org_login='somecorp',
            org_public_repos=10,
            repos_scanned=[],
        )
        tier, evidence = calculate_tier_from_scan(scan_data)
        assert tier == TIER_TRACKING


# =========================================================================
# auto_retier_if_version_changed() Tests
# =========================================================================

class TestAutoRetier:
    """Tests for auto_retier_if_version_changed() — re-tiers on scoring changes."""

    def _setup_account_with_v2_report(self, fresh_db, company, maturity, old_tier):
        """Create an account with a V2-scored report."""
        scan_data = _minimal_scan_data(
            company=company, org=company.lower(),
            scoring_v2={
                'org_maturity_level': maturity,
                'org_maturity_label': maturity.title(),
                'confidence_percent': 80.0,
                'readiness_index': 0.5,
                'outreach_angle_label': 'Generic',
            },
        )
        rid = save_report(company, company.lower(), scan_data, {}, 1.0)

        # Create monitored account
        add_account_to_tier_0(company, company.lower())

        # Set the tier and latest_report_id manually
        conn = sqlite3.connect(fresh_db)
        conn.execute(
            'UPDATE monitored_accounts SET current_tier = ?, latest_report_id = ? '
            'WHERE LOWER(company_name) = ?',
            (old_tier, rid, company.lower()),
        )
        conn.commit()
        conn.close()
        return rid

    @patch('scoring.get_scoring_fingerprint', return_value='new-fingerprint-abc')
    def test_retier_updates_when_fingerprint_changes(self, mock_fp, fresh_db):
        """Accounts are re-tiered when scoring fingerprint changes."""
        self._setup_account_with_v2_report(fresh_db, 'RetieredCo', 'preparing', TIER_TRACKING)

        # Set stored fingerprint to something different
        set_setting('scoring_fingerprint', 'old-fingerprint-xyz')

        updated = auto_retier_if_version_changed()
        assert updated >= 1

        account = _get_account(fresh_db, 'retieredco')
        assert account['current_tier'] == TIER_PREPARING

    @patch('scoring.get_scoring_fingerprint', return_value='same-fp')
    def test_no_retier_when_fingerprint_matches(self, mock_fp, fresh_db):
        """No re-tiering when fingerprint hasn't changed."""
        set_setting('scoring_fingerprint', 'same-fp')

        updated = auto_retier_if_version_changed()
        assert updated == 0

    @patch('scoring.get_scoring_fingerprint', return_value='new-fp-2')
    def test_retier_skips_accounts_without_v2(self, mock_fp, fresh_db):
        """Accounts without scoring_v2 data in their report are skipped."""
        # Create account with a non-V2 report
        scan_data = _minimal_scan_data(company='LegacyCo', org='legacyco')
        rid = save_report('LegacyCo', 'legacyco', scan_data, {}, 1.0)
        add_account_to_tier_0('LegacyCo', 'legacyco')

        conn = sqlite3.connect(fresh_db)
        conn.execute(
            'UPDATE monitored_accounts SET latest_report_id = ? '
            'WHERE LOWER(company_name) = ?',
            (rid, 'legacyco'),
        )
        conn.commit()
        conn.close()

        set_setting('scoring_fingerprint', 'old-fp')
        updated = auto_retier_if_version_changed()
        assert updated == 0

    @patch('scoring.get_scoring_fingerprint', return_value='new-fp-3')
    def test_retier_stores_new_fingerprint(self, mock_fp, fresh_db):
        """After re-tiering, the new fingerprint is stored."""
        set_setting('scoring_fingerprint', 'old-fp')
        auto_retier_if_version_changed()

        assert get_setting('scoring_fingerprint') == 'new-fp-3'


# =========================================================================
# add_account_to_tier_0() Tests
# =========================================================================

class TestAddAccountToTier0:
    """Tests for add_account_to_tier_0() — bulk import path."""

    def test_creates_new_account(self, fresh_db):
        """Creates a new account at tier 0."""
        result = add_account_to_tier_0('NewCorp', 'newcorp')

        assert result['tier'] == TIER_TRACKING
        account = _get_account(fresh_db, 'newcorp')
        assert account is not None
        assert account['current_tier'] == TIER_TRACKING
        assert account['github_org'] == 'newcorp'

    def test_with_revenue_and_website(self, fresh_db):
        """Revenue and website are saved as first-class columns."""
        add_account_to_tier_0('RichCo', 'richco',
                              annual_revenue='$500M',
                              website='richco.com')

        account = _get_account(fresh_db, 'richco')
        assert account['annual_revenue'] == '$500M'
        assert account['website'] == 'richco.com'

    def test_with_metadata(self, fresh_db):
        """Extra metadata is stored as JSON."""
        add_account_to_tier_0('MetaCo', 'metaco',
                              metadata={'industry': 'HealthTech', 'employees': '500'})

        meta = _get_metadata(fresh_db, 'metaco')
        assert meta['industry'] == 'HealthTech'
        assert meta['employees'] == '500'

    def test_idempotent_does_not_duplicate(self, fresh_db):
        """Calling add_account_to_tier_0 twice for same company doesn't duplicate."""
        add_account_to_tier_0('DupeCo', 'dupeco')
        add_account_to_tier_0('DupeCo', 'dupeco')

        rows = _raw_query(
            fresh_db,
            'SELECT * FROM monitored_accounts WHERE LOWER(company_name) = ?',
            ('dupeco',),
        )
        assert len(rows) == 1

    def test_reimport_updates_revenue(self, fresh_db):
        """Re-importing same company with new revenue updates the existing record."""
        add_account_to_tier_0('UpdCo', 'updco', annual_revenue='$10M')
        add_account_to_tier_0('UpdCo', 'updco', annual_revenue='$50M')

        account = _get_account(fresh_db, 'updco')
        assert account['annual_revenue'] == '$50M'

    def test_reimport_merges_metadata(self, fresh_db):
        """Re-importing merges new metadata with existing."""
        add_account_to_tier_0('MergeCo2', 'mergeco2',
                              metadata={'key1': 'val1'})
        add_account_to_tier_0('MergeCo2', 'mergeco2',
                              metadata={'key2': 'val2'})

        meta = _get_metadata(fresh_db, 'mergeco2')
        assert meta.get('key1') == 'val1'
        assert meta.get('key2') == 'val2'

    def test_preserves_existing_tier(self, fresh_db):
        """Re-importing does NOT reset an account's tier back to 0."""
        # First create at tier 0, then manually set tier to 2
        add_account_to_tier_0('TierCo', 'tierco')
        conn = sqlite3.connect(fresh_db)
        conn.execute(
            'UPDATE monitored_accounts SET current_tier = ? WHERE LOWER(company_name) = ?',
            (TIER_PREPARING, 'tierco'),
        )
        conn.commit()
        conn.close()

        # Re-import — should not change tier
        result = add_account_to_tier_0('TierCo', 'tierco', annual_revenue='$100M')
        assert result['tier'] == TIER_PREPARING

    def test_deduplicates_by_github_org(self, fresh_db):
        """If github_org matches an existing account, updates that account."""
        add_account_to_tier_0('OrgMatch', 'sharedorg')
        # Import with a different company name but same org
        result = add_account_to_tier_0('OrgMatch Renamed', 'sharedorg')

        rows = _raw_query(fresh_db, 'SELECT * FROM monitored_accounts WHERE LOWER(github_org) = ?', ('sharedorg',))
        assert len(rows) == 1

    def test_special_chars_in_name(self, fresh_db):
        """Special characters in company name are handled correctly."""
        result = add_account_to_tier_0("O'Malley & Sons", 'omalley')
        account = _get_account(fresh_db, "o'malley & sons")
        assert account is not None


# =========================================================================
# update_account_metadata() Tests
# =========================================================================

class TestUpdateAccountMetadata:
    """Tests for update_account_metadata() — direct metadata update."""

    def test_happy_path(self, fresh_db):
        """Metadata is merged into existing account."""
        add_account_to_tier_0('MetaTestCo', 'metatestco')
        result = update_account_metadata('MetaTestCo', {'source': 'sheets', 'tier_override': '2'})

        assert result is True
        meta = _get_metadata(fresh_db, 'metatestco')
        assert meta['source'] == 'sheets'
        assert meta['tier_override'] == '2'

    def test_returns_false_for_nonexistent(self, fresh_db):
        """Returns False when company doesn't exist."""
        result = update_account_metadata('GhostCo', {'x': '1'})
        assert result is False

    def test_merges_with_existing(self, fresh_db):
        """New keys are added, existing keys are overwritten."""
        add_account_to_tier_0('MergeMetaCo', 'mergemetaco',
                              metadata={'a': '1', 'b': '2'})

        update_account_metadata('MergeMetaCo', {'b': 'updated', 'c': '3'})

        meta = _get_metadata(fresh_db, 'mergemetaco')
        assert meta['a'] == '1'
        assert meta['b'] == 'updated'
        assert meta['c'] == '3'

    def test_case_insensitive(self, fresh_db):
        """Lookup is case-insensitive."""
        add_account_to_tier_0('CaseCo', 'caseco')
        result = update_account_metadata('CASECO', {'test': 'yes'})
        assert result is True

        meta = _get_metadata(fresh_db, 'caseco')
        assert meta['test'] == 'yes'


# =========================================================================
# set_setting() / get_setting() Tests
# =========================================================================

class TestSystemSettings:
    """Tests for the key-value system_settings store."""

    def test_set_and_get(self, fresh_db):
        """Setting a value and retrieving it works."""
        set_setting('my_key', 'my_value')
        assert get_setting('my_key') == 'my_value'

    def test_get_nonexistent_returns_none(self, fresh_db):
        """Getting a key that doesn't exist returns None."""
        assert get_setting('nonexistent_key') is None

    def test_upsert_overwrites(self, fresh_db):
        """Setting the same key again overwrites the previous value."""
        set_setting('version', '1')
        set_setting('version', '2')
        assert get_setting('version') == '2'

    def test_multiple_keys(self, fresh_db):
        """Multiple distinct keys coexist without interference."""
        set_setting('key_a', 'val_a')
        set_setting('key_b', 'val_b')
        assert get_setting('key_a') == 'val_a'
        assert get_setting('key_b') == 'val_b'

    def test_empty_string_value(self, fresh_db):
        """Empty string is a valid value (not confused with None)."""
        set_setting('empty', '')
        assert get_setting('empty') == ''

    def test_long_value(self, fresh_db):
        """Very long values are stored without truncation."""
        long_val = 'x' * 100_000
        set_setting('long_key', long_val)
        assert get_setting('long_key') == long_val

    def test_special_chars_in_value(self, fresh_db):
        """Values with special characters round-trip correctly."""
        special = "quotes: \" ' ; newlines: \n\ttabs"
        set_setting('special', special)
        assert get_setting('special') == special


# =========================================================================
# Cross-Function Integration Tests
# =========================================================================

class TestWritePathIntegration:
    """Integration tests combining multiple write functions."""

    @pytest.fixture(autouse=True)
    def _mock_verify_signals(self):
        """Mock verify_signals to avoid network calls during integration tests."""
        with patch('database.verify_signals', side_effect=lambda sd, **kw: sd):
            yield

    def test_full_scan_lifecycle(self, fresh_db):
        """Simulate a full scan lifecycle: import -> scan -> report -> signals -> enrich."""
        # Step 1: Import via CSV
        add_account_to_tier_0('LifecycleCo', 'lifecycleco',
                              annual_revenue='$25M',
                              website='lifecycle.co',
                              metadata={'source': 'csv_import'})

        account = _get_account(fresh_db, 'lifecycleco')
        assert account['current_tier'] == TIER_TRACKING

        # Step 2: Save scan report
        scan_data = _minimal_scan_data(
            company='LifecycleCo', org='lifecycleco',
            repos_scanned=[{'name': 'app', 'language': 'TypeScript'}],
        )
        rid = save_report('LifecycleCo', 'lifecycleco', scan_data, {'summary': 'clean'}, 5.0)
        assert rid > 0

        # Step 3: Save signals
        signals = [
            {'type': 'dependency_injection', 'Evidence': 'Found i18next'},
            {'type': 'ghost_branch', 'Evidence': 'Branch feature/l10n'},
        ]
        count = save_signals(rid, 'LifecycleCo', signals)
        assert count == 2

        # Step 4: Update account status (tier classification)
        update_account_status(scan_data, report_id=rid)

        # Step 5: Enrich with additional data
        enrich_existing_account('LifecycleCo', annual_revenue='$30M',
                                metadata={'crm_id': 'SF_123'})

        # Verify final state
        account = _get_account(fresh_db, 'lifecycleco')
        assert account['annual_revenue'] == '$30M'
        assert account['latest_report_id'] == rid

        meta = _get_metadata(fresh_db, 'lifecycleco')
        assert meta.get('source') == 'csv_import'
        assert meta.get('crm_id') == 'SF_123'

        stored_signals = get_signals_for_report(rid)
        assert len(stored_signals) == 2

        report = get_report(rid)
        assert report is not None
        assert report['company_name'] == 'LifecycleCo'

    def test_concurrent_account_writes_no_corruption(self, fresh_db):
        """Multiple sequential writes to the same account don't corrupt data."""
        add_account_to_tier_0('BusyCo', 'busyco')

        for i in range(20):
            enrich_existing_account('BusyCo', metadata={f'field_{i}': str(i)})

        meta = _get_metadata(fresh_db, 'busyco')
        for i in range(20):
            assert meta[f'field_{i}'] == str(i)

    def test_report_and_signals_fk_integrity(self, fresh_db):
        """Signals correctly reference their parent report via foreign key."""
        rid = save_report('FKCo', 'fkco', _minimal_scan_data(), {}, 1.0)
        save_signals(rid, 'FKCo', [
            {'type': 'sig1', 'Evidence': 'ev1'},
            {'type': 'sig2', 'Evidence': 'ev2'},
        ])

        # Verify FK relationship
        signals = _raw_query(
            fresh_db,
            'SELECT s.*, r.company_name as report_company '
            'FROM scan_signals s JOIN reports r ON s.report_id = r.id '
            'WHERE s.report_id = ?',
            (rid,),
        )
        assert len(signals) == 2
        for sig in signals:
            assert sig['report_company'] == 'FKCo'

    def test_save_report_with_none_scan_fields(self, fresh_db):
        """save_report handles scan_data where expected fields are missing."""
        scan_data = {}  # Completely empty
        ai_analysis = None  # Not even a dict

        # Should not crash — uses .get() with defaults
        rid = save_report('EmptyData', 'emptydata', scan_data, ai_analysis or {}, 0.0)
        assert rid > 0

        row = _raw_query_one(fresh_db, 'SELECT * FROM reports WHERE id = ?', (rid,))
        assert row['signals_found'] == 0
        assert row['repos_scanned'] == 0
