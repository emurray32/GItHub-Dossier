"""
Shared test fixtures for scoring engine tests and enrollment pipeline tests.
"""
import json
import os
import sqlite3
import sys
import tempfile

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch


@pytest.fixture
def empty_scan_results():
    """Scan results with zero signals."""
    return {
        'company_name': 'EmptyCorp',
        'org_login': 'emptycorp',
        'org_name': 'EmptyCorp',
        'org_url': 'https://github.com/emptycorp',
        'org_description': '',
        'org_public_repos': 5,
        'org_public_members': 2,
        'total_stars': 100,
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


@pytest.fixture
def preparing_scan_results():
    """Scan results for a PREPARING company (Goldilocks Zone)."""
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=5)).strftime('%Y-%m-%dT%H:%M:%SZ')

    return {
        'company_name': 'PrepCorp',
        'org_login': 'prepcorp',
        'org_name': 'PrepCorp Inc.',
        'org_url': 'https://github.com/prepcorp',
        'org_description': 'Building great products',
        'org_public_repos': 15,
        'org_public_members': 8,
        'total_stars': 2000,
        'signals': [
            {
                'Company': 'prepcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found react-i18next in package.json (webapp). No locale folders detected.',
                'Link': 'https://github.com/prepcorp/webapp/blob/main/package.json',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'webapp',
                'file': 'package.json',
                'goldilocks_status': 'preparing',
                'gap_verified': True,
                'libraries_found': ['react-i18next'],
                'created_at': recent,
            },
            {
                'Company': 'prepcorp',
                'Signal': 'Ghost Branch',
                'Evidence': 'Branch feature/i18n found in webapp',
                'Link': 'https://github.com/prepcorp/webapp/tree/feature/i18n',
                'priority': 'HIGH',
                'type': 'ghost_branch',
                'repo': 'webapp',
                'pushed_at': recent,
                'created_at': recent,
            },
        ],
        'signal_summary': {
            'rfc_discussion': {'count': 0, 'hits': [], 'high_priority_count': 0},
            'dependency_injection': {
                'count': 1,
                'hits': [{
                    'goldilocks_status': 'preparing',
                    'gap_verified': True,
                    'libraries_found': ['react-i18next'],
                    'repo': 'webapp',
                }],
            },
            'ghost_branch': {'count': 1, 'hits': [{'name': 'feature/i18n', 'repo': 'webapp'}]},
            'documentation_intent': {'count': 0, 'hits': [], 'high_priority_count': 0},
            'smoking_gun_fork': {'count': 0, 'hits': []},
            'enhanced_heuristics': {'count': 0, 'by_type': {}},
        },
        'repos_scanned': [
            {
                'name': 'webapp',
                'fork': False,
                'archived': False,
                'stargazers_count': 500,
                'watchers_count': 50,
                'pushed_at': recent,
                'language': 'TypeScript',
                'description': 'Main web application',
            },
        ],
        'contributors': {
            'dev1': {'name': 'Dev One', 'contributions': 200, 'company': 'PrepCorp'},
            'dev2': {'name': 'Dev Two', 'contributions': 150, 'company': 'PrepCorp'},
        },
    }


@pytest.fixture
def enterprise_scan_results():
    """Scan results for an enterprise org with signal clustering."""
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=10)).strftime('%Y-%m-%dT%H:%M:%SZ')

    return {
        'company_name': 'MegaCorp',
        'org_login': 'megacorp',
        'org_name': 'MegaCorp',
        'org_url': 'https://github.com/megacorp',
        'org_description': 'Enterprise software company',
        'org_public_repos': 500,
        'org_public_members': 200,
        'total_stars': 50000,
        'signals': [
            {
                'Company': 'megacorp',
                'Signal': 'Smoking Gun Fork',
                'Evidence': 'Forked react-i18next',
                'Link': 'https://github.com/megacorp/react-i18next',
                'priority': 'HIGH',
                'type': 'smoking_gun_fork',
                'repo': 'react-i18next-fork',
                'created_at': recent,
            },
            {
                'Company': 'megacorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in platform-app',
                'Link': 'https://github.com/megacorp/platform-app',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'platform-app',
                'goldilocks_status': 'preparing',
                'created_at': recent,
            },
            {
                'Company': 'megacorp',
                'Signal': 'Ghost Branch',
                'Evidence': 'Branch feature/localization in platform-app',
                'Link': '',
                'priority': 'HIGH',
                'type': 'ghost_branch',
                'repo': 'platform-app',
                'pushed_at': recent,
            },
            {
                'Company': 'megacorp',
                'Signal': 'RFC Discussion',
                'Evidence': 'Issue: i18n strategy RFC',
                'Link': 'https://github.com/megacorp/platform-app/issues/42',
                'priority': 'HIGH',
                'type': 'rfc_discussion',
                'repo': 'platform-app',
                'created_at': recent,
            },
        ],
        'signal_summary': {
            'rfc_discussion': {'count': 1, 'hits': [], 'high_priority_count': 1},
            'dependency_injection': {'count': 1, 'hits': [{'goldilocks_status': 'preparing'}]},
            'ghost_branch': {'count': 1, 'hits': []},
            'documentation_intent': {'count': 0, 'hits': [], 'high_priority_count': 0},
            'smoking_gun_fork': {'count': 1, 'hits': []},
            'enhanced_heuristics': {'count': 0, 'by_type': {}},
        },
        'repos_scanned': [
            {'name': 'platform-app', 'fork': False, 'archived': False, 'stargazers_count': 5000, 'watchers_count': 500, 'pushed_at': recent, 'language': 'TypeScript', 'description': 'Platform application'},
            {'name': 'react-i18next-fork', 'fork': True, 'archived': False, 'stargazers_count': 0, 'watchers_count': 0, 'pushed_at': recent, 'language': 'JavaScript', 'description': ''},
            {'name': 'docs', 'fork': False, 'archived': False, 'stargazers_count': 100, 'watchers_count': 20, 'pushed_at': recent, 'language': 'Markdown', 'description': 'Documentation'},
        ],
        'contributors': {},
    }


@pytest.fixture
def launched_scan_results():
    """Scan results for a LAUNCHED company (too late)."""
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=200)).strftime('%Y-%m-%dT%H:%M:%SZ')

    return {
        'company_name': 'LaunchedCorp',
        'org_login': 'launchedcorp',
        'org_name': 'LaunchedCorp',
        'org_url': 'https://github.com/launchedcorp',
        'org_description': '',
        'org_public_repos': 10,
        'org_public_members': 5,
        'total_stars': 1000,
        'signals': [
            {
                'Company': 'launchedcorp',
                'Signal': 'Already Launched',
                'Evidence': 'Locale folders found: locales/en, locales/fr, locales/de',
                'Link': '',
                'priority': 'LOW',
                'type': 'already_launched',
                'repo': 'main-app',
                'created_at': old,
            },
        ],
        'signal_summary': {
            'rfc_discussion': {'count': 0, 'hits': []},
            'dependency_injection': {'count': 0, 'hits': []},
            'ghost_branch': {'count': 0, 'hits': []},
            'documentation_intent': {'count': 0, 'hits': []},
            'smoking_gun_fork': {'count': 0, 'hits': []},
            'enhanced_heuristics': {'count': 0, 'by_type': {}},
        },
        'repos_scanned': [
            {'name': 'main-app', 'fork': False, 'archived': False, 'stargazers_count': 500, 'watchers_count': 50, 'pushed_at': old, 'language': 'Python'},
        ],
        'contributors': {},
    }


@pytest.fixture
def fork_scan_results():
    """Scan results with only fork-based signals."""
    return {
        'company_name': 'ForkOnly',
        'org_login': 'forkonly',
        'org_name': 'ForkOnly',
        'org_url': 'https://github.com/forkonly',
        'org_description': '',
        'org_public_repos': 3,
        'total_stars': 50,
        'signals': [
            {
                'Company': 'forkonly',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in package.json',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'forked-app',
                'fork': True,
            },
        ],
        'signal_summary': {
            'rfc_discussion': {'count': 0, 'hits': []},
            'dependency_injection': {'count': 1, 'hits': []},
            'ghost_branch': {'count': 0, 'hits': []},
        },
        'repos_scanned': [
            {'name': 'forked-app', 'fork': True, 'archived': False, 'stargazers_count': 0, 'watchers_count': 0},
        ],
        'contributors': {},
    }


@pytest.fixture
def mixed_signal_results():
    """Org with 1 mature repo + 3 preparing repos (proven-buyer pattern)."""
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')

    signals = [
        # Mature repo - already launched
        {
            'Company': 'mixedcorp',
            'Signal': 'Already Launched',
            'Evidence': 'Locale folders found in legacy-app',
            'type': 'already_launched',
            'repo': 'legacy-app',
            'created_at': recent,
        },
        # Preparing repos
        {
            'Company': 'mixedcorp',
            'Signal': 'Dependency Injection',
            'Evidence': 'Found react-i18next in new-frontend',
            'type': 'dependency_injection',
            'repo': 'new-frontend',
            'goldilocks_status': 'preparing',
            'created_at': recent,
        },
        {
            'Company': 'mixedcorp',
            'Signal': 'Dependency Injection',
            'Evidence': 'Found next-intl in portal',
            'type': 'dependency_injection',
            'repo': 'portal',
            'goldilocks_status': 'preparing',
            'created_at': recent,
        },
        {
            'Company': 'mixedcorp',
            'Signal': 'Ghost Branch',
            'Evidence': 'Branch feature/i18n in mobile-app',
            'type': 'ghost_branch',
            'repo': 'mobile-app',
            'pushed_at': recent,
        },
    ]

    return {
        'company_name': 'MixedCorp',
        'org_login': 'mixedcorp',
        'org_name': 'MixedCorp',
        'org_url': 'https://github.com/mixedcorp',
        'org_description': 'Growing SaaS company',
        'org_public_repos': 20,
        'org_public_members': 15,
        'total_stars': 3000,
        'signals': signals,
        'signal_summary': {
            'rfc_discussion': {'count': 0, 'hits': []},
            'dependency_injection': {'count': 2, 'hits': [
                {'goldilocks_status': 'preparing', 'repo': 'new-frontend'},
                {'goldilocks_status': 'preparing', 'repo': 'portal'},
            ]},
            'ghost_branch': {'count': 1, 'hits': []},
            'documentation_intent': {'count': 0, 'hits': []},
            'smoking_gun_fork': {'count': 0, 'hits': []},
            'enhanced_heuristics': {'count': 0, 'by_type': {}},
        },
        'repos_scanned': [
            {'name': 'legacy-app', 'fork': False, 'archived': False, 'stargazers_count': 1000, 'pushed_at': recent},
            {'name': 'new-frontend', 'fork': False, 'archived': False, 'stargazers_count': 500, 'pushed_at': recent},
            {'name': 'portal', 'fork': False, 'archived': False, 'stargazers_count': 300, 'pushed_at': recent},
            {'name': 'mobile-app', 'fork': False, 'archived': False, 'stargazers_count': 200, 'pushed_at': recent},
        ],
        'contributors': {
            'eng1': {'name': 'Engineer 1', 'contributions': 300, 'company': 'MixedCorp'},
        },
    }


# ---------------------------------------------------------------------------
# Database / Enrollment Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Provide a fresh SQLite database for each test.

    Patches Config.DATABASE_PATH and Config.DATABASE_URL so that database.py
    uses this temp DB, then calls init_db() to create all tables.
    """
    db_path = str(tmp_path / 'test.db')

    # Ensure DATABASE_URL is empty so we use SQLite path
    monkeypatch.setattr('config.Config.DATABASE_URL', '')
    monkeypatch.setattr('config.Config.DATABASE_PATH', db_path)

    # Force database module to reload its dialect flag
    import database
    monkeypatch.setattr(database, '_USE_POSTGRES', False)

    # Re-bind get_db_connection to use patched path
    original_get_db = database.get_db_connection

    def _patched_get_db():
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    monkeypatch.setattr(database, 'get_db_connection', _patched_get_db)

    # Initialize schema
    database.init_db()

    yield db_path


@pytest.fixture
def flask_app(test_db, monkeypatch):
    """Create a Flask test client with a clean database."""
    # Set required env vars to avoid import-time errors
    monkeypatch.setenv('APOLLO_API_KEY', 'test-key-123')

    import app as flask_app_module
    flask_app_module.app.config['TESTING'] = True
    with flask_app_module.app.test_client() as client:
        with flask_app_module.app.app_context():
            yield client


@pytest.fixture
def sample_campaign(test_db):
    """Create a sample campaign and return its ID."""
    import database
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO campaigns (name, prompt, status) VALUES (?, ?, ?)",
        ('Test Campaign', 'Test prompt', 'active')
    )
    conn.commit()
    campaign_id = cursor.lastrowid
    conn.close()
    return campaign_id


@pytest.fixture
def sample_batch(test_db, sample_campaign):
    """Create a sample enrollment batch and return its ID."""
    import database
    batch_id = database.create_enrollment_batch(sample_campaign, [1, 2, 3])
    return batch_id


@pytest.fixture
def sample_sequence_mapping(test_db):
    """Create a sample sequence mapping and return it."""
    import database
    return database.upsert_sequence_mapping(
        sequence_id='seq_abc123',
        sequence_name='Test Sequence - Preparing',
        sequence_config='threaded_4',
        num_steps=4,
        active=True,
        owner_name='eric@phrase.com'
    )


# ---------------------------------------------------------------------------
# Apollo API Mock Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def apollo_person_response():
    """Mock Apollo People Match API response."""
    return {
        'person': {
            'id': 'person_123',
            'first_name': 'Jane',
            'last_name': 'Smith',
            'name': 'Jane Smith',
            'email': 'jane.smith@targetcorp.com',
            'email_status': 'verified',
            'title': 'VP Engineering',
            'linkedin_url': 'https://linkedin.com/in/janesmith',
            'organization': {
                'name': 'TargetCorp',
                'website_url': 'https://targetcorp.com',
            },
        }
    }


@pytest.fixture
def apollo_search_response():
    """Mock Apollo People Search API response."""
    return {
        'people': [
            {
                'id': 'person_456',
                'first_name': 'John',
                'last_name': 'Doe',
                'name': 'John Doe',
                'email': 'john.doe@targetcorp.com',
                'email_status': 'verified',
                'title': 'Engineering Manager',
                'linkedin_url': 'https://linkedin.com/in/johndoe',
                'organization': {'name': 'TargetCorp'},
            }
        ],
        'pagination': {'total_entries': 1, 'total_pages': 1, 'page': 1},
    }


@pytest.fixture
def apollo_sequences_response():
    """Mock Apollo Sequences search API response."""
    return {
        'emailer_campaigns': [
            {
                'id': 'seq_001',
                'name': 'Preparing - Technical',
                'active': True,
                'num_steps': 4,
                'created_at': '2025-01-01',
                'emailer_steps': [
                    {'type': 'auto_email', 'subject': 'i18n in {{company}}'},
                    {'type': 'auto_email', 'subject': ''},
                    {'type': 'auto_email', 'subject': ''},
                    {'type': 'auto_email', 'subject': 'Quick follow-up'},
                ],
            },
            {
                'id': 'seq_002',
                'name': 'Ghost Branch - Urgent',
                'active': True,
                'num_steps': 2,
                'created_at': '2025-02-01',
                'emailer_steps': [
                    {'type': 'auto_email', 'subject': 'Your i18n branch'},
                ],
            },
        ],
        'pagination': {'total_entries': 2, 'total_pages': 1, 'page': 1},
    }


@pytest.fixture
def apollo_contact_create_response():
    """Mock Apollo contact creation response."""
    return {
        'contact': {
            'id': 'contact_789',
            'first_name': 'Jane',
            'last_name': 'Smith',
            'email': 'jane.smith@targetcorp.com',
        }
    }


@pytest.fixture
def apollo_enroll_response():
    """Mock Apollo sequence enrollment response."""
    return {
        'contacts': [{'id': 'contact_789'}],
    }


@pytest.fixture
def apollo_custom_fields_response():
    """Mock Apollo typed custom fields response."""
    return {
        'typed_custom_fields': [
            {'id': 'cf_sub1', 'name': 'Personalized Subject 1'},
            {'id': 'cf_sub2', 'name': 'Personalized Subject 2'},
            {'id': 'cf_email1', 'name': 'Personalized Email 1'},
            {'id': 'cf_email2', 'name': 'Personalized Email 2'},
            {'id': 'cf_email3', 'name': 'Personalized Email 3'},
            {'id': 'cf_email4', 'name': 'Personalized Email 4'},
        ]
    }


@pytest.fixture
def apollo_email_accounts_response():
    """Mock Apollo email accounts response."""
    return {
        'email_accounts': [
            {'id': 'ea_001', 'email': 'eric@phrase.com', 'active': True},
            {'id': 'ea_002', 'email': 'sales@phrase.com', 'active': True},
        ]
    }


# ---------------------------------------------------------------------------
# Mock GitHub Responses
# ---------------------------------------------------------------------------

@pytest.fixture
def github_repos_response():
    """Mock GitHub repos API response for an org."""
    return [
        {
            'name': 'webapp',
            'full_name': 'targetcorp/webapp',
            'fork': False,
            'archived': False,
            'stargazers_count': 500,
            'watchers_count': 50,
            'pushed_at': '2025-12-01T00:00:00Z',
            'language': 'TypeScript',
            'description': 'Main web application',
            'default_branch': 'main',
        },
        {
            'name': 'api-server',
            'full_name': 'targetcorp/api-server',
            'fork': False,
            'archived': False,
            'stargazers_count': 200,
            'watchers_count': 20,
            'pushed_at': '2025-11-15T00:00:00Z',
            'language': 'Python',
            'description': 'REST API server',
            'default_branch': 'main',
        },
    ]


@pytest.fixture
def github_package_json_with_i18n():
    """Mock package.json content with i18n library."""
    return {
        'name': 'webapp',
        'dependencies': {
            'react': '^18.0.0',
            'react-i18next': '^13.0.0',
            'i18next': '^23.0.0',
        },
    }


@pytest.fixture
def github_package_json_without_i18n():
    """Mock package.json content without i18n library."""
    return {
        'name': 'webapp',
        'dependencies': {
            'react': '^18.0.0',
            'next': '^14.0.0',
        },
    }


@pytest.fixture
def sample_enrollment_contacts():
    """Sample enrollment contact data for batch operations."""
    return [
        {
            'batch_id': 1,
            'company_name': 'TargetCorp',
            'company_domain': 'targetcorp.com',
            'persona_name': 'Engineering',
            'sequence_id': 'seq_001',
            'sequence_name': 'Preparing - Technical',
            'first_name': 'Jane',
            'last_name': 'Smith',
            'email': 'jane.smith@targetcorp.com',
            'title': 'VP Engineering',
            'seniority': 'vp',
            'status': 'discovered',
        },
        {
            'batch_id': 1,
            'company_name': 'TargetCorp',
            'company_domain': 'targetcorp.com',
            'persona_name': 'Engineering',
            'sequence_id': 'seq_001',
            'sequence_name': 'Preparing - Technical',
            'first_name': 'John',
            'last_name': 'Doe',
            'email': 'john.doe@targetcorp.com',
            'title': 'Engineering Manager',
            'seniority': 'manager',
            'status': 'discovered',
        },
        {
            'batch_id': 1,
            'company_name': 'AnotherCorp',
            'company_domain': 'anothercorp.com',
            'persona_name': 'Product',
            'sequence_id': 'seq_002',
            'sequence_name': 'Ghost Branch - Urgent',
            'first_name': 'Alice',
            'last_name': 'Johnson',
            'email': 'alice@anothercorp.com',
            'title': 'Product Manager',
            'seniority': 'manager',
            'status': 'discovered',
        },
    ]
