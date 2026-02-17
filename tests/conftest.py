"""
Shared test fixtures for scoring engine tests.
"""
import pytest
from datetime import datetime, timezone, timedelta


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
