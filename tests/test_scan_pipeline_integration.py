"""
Integration tests for the full scan-to-tier classification pipeline.

Tests the complete flow:
  1. score_scan_results(scan_results) -> ScoringResult
  2. ScoringResult.apply_to_scan_results(scan_results) -> legacy fields
  3. calculate_tier_from_scan(scan_results) -> (tier, evidence)

Each test scenario builds a realistic scan_results dict, pushes it through
all three steps, and asserts on maturity level, legacy fields, and tier number.
"""
from __future__ import annotations

import copy
import pytest
from datetime import datetime, timezone, timedelta
from typing import Optional

from scoring import score_scan_results
from scoring.models import ScoringResult, MaturitySegment
from scoring.compat import _MATURITY_TO_TIER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recent_timestamp(days_ago: int = 5) -> str:
    """Return an ISO timestamp N days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _old_timestamp(days_ago: int = 200) -> str:
    """Return an ISO timestamp many days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _base_scan_results(
    org_login: str = 'testcorp',
    total_stars: int = 1000,
    org_public_repos: int = 10,
    org_public_members: int = 5,
) -> dict:
    """Return a minimal scan_results shell.  Callers add signals and repos."""
    return {
        'company_name': org_login.title(),
        'org_login': org_login,
        'org_name': org_login.title(),
        'org_url': f'https://github.com/{org_login}',
        'org_description': 'A software company',
        'org_public_repos': org_public_repos,
        'org_public_members': org_public_members,
        'total_stars': total_stars,
        'signals': [],
        'signal_summary': {
            'rfc_discussion': {'count': 0, 'hits': [], 'high_priority_count': 0},
            'dependency_injection': {'count': 0, 'hits': []},
            'ghost_branch': {'count': 0, 'hits': []},
            'documentation_intent': {'count': 0, 'hits': [], 'high_priority_count': 0},
            'smoking_gun_fork': {'count': 0, 'hits': []},
            'enhanced_heuristics': {'count': 0, 'by_type': {}},
        },
        'repos_scanned': [],
        'contributors': {},
    }


def _make_repo(
    name: str,
    *,
    fork: bool = False,
    archived: bool = False,
    stars: int = 200,
    watchers: int = 20,
    pushed_at: str | None = None,
    language: str = 'TypeScript',
    description: str = '',
) -> dict:
    """Build a repo metadata dict for repos_scanned."""
    return {
        'name': name,
        'fork': fork,
        'archived': archived,
        'stargazers_count': stars,
        'watchers_count': watchers,
        'pushed_at': pushed_at or _recent_timestamp(3),
        'language': language,
        'description': description or f'{name} application',
    }


def _run_full_pipeline(scan_results: dict):
    """Execute all three pipeline stages and return (result, tier, evidence).

    1. score_scan_results -> ScoringResult
    2. apply_to_scan_results -> sets legacy fields on scan_results
    3. calculate_tier_from_scan -> (tier, evidence)
    """
    result = score_scan_results(scan_results)

    # apply_to_scan_results stores legacy keys (intent_score, goldilocks_status,
    # lead_status) *and* we also need the structured scoring_v2 blob for
    # calculate_tier_from_scan to take the V2 path.
    result.apply_to_scan_results(scan_results)
    scan_results['scoring_v2'] = result.to_structured_output()

    # Import here to avoid circular import at module level; database.py has
    # heavy imports that rely on the project's config module.
    from database import calculate_tier_from_scan
    tier, evidence = calculate_tier_from_scan(scan_results)

    return result, tier, evidence


# ===================================================================
# TEST SCENARIOS
# ===================================================================

class TestEmptyOrg:
    """Tier 0 / PRE_I18N -- org with zero signals."""

    def test_maturity_is_pre_i18n(self):
        scan = _base_scan_results()
        scan['repos_scanned'] = [_make_repo('some-tool')]
        result = score_scan_results(scan)

        assert result.org_maturity_level == MaturitySegment.PRE_I18N

    def test_stage1_rejects_no_signals(self):
        scan = _base_scan_results()
        result = score_scan_results(scan)

        assert result.stage1_passed is False
        assert result.stage1_label == 'no_signals'

    def test_legacy_fields_cold(self):
        scan = _base_scan_results()
        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        assert scan['intent_score'] == 0
        assert scan['goldilocks_status'] == 'none'
        assert 'COLD' in scan['lead_status']

    def test_tier_is_zero(self):
        scan = _base_scan_results()
        scan['repos_scanned'] = [_make_repo('some-tool')]
        _, tier, _ = _run_full_pipeline(scan)

        assert tier == 0


class TestThinkingOrg:
    """Tier 1 / THINKING -- exploration signals only (rfc_discussion, ghost_branch)."""

    @staticmethod
    def _make_thinking_scan():
        recent = _recent_timestamp(7)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'RFC Discussion',
                'Evidence': 'Issue #42: Should we implement i18n?',
                'Link': 'https://github.com/testcorp/webapp/issues/42',
                'priority': 'MEDIUM',
                'type': 'rfc_discussion',
                'repo': 'webapp',
                'created_at': recent,
            },
            {
                'Company': 'testcorp',
                'Signal': 'Ghost Branch',
                'Evidence': 'Branch feature/i18n-exploration found',
                'Link': 'https://github.com/testcorp/webapp/tree/feature/i18n-exploration',
                'priority': 'MEDIUM',
                'type': 'ghost_branch',
                'repo': 'webapp',
                'pushed_at': recent,
                'created_at': recent,
            },
        ]
        scan['signal_summary']['rfc_discussion'] = {'count': 1, 'hits': [], 'high_priority_count': 0}
        scan['signal_summary']['ghost_branch'] = {'count': 1, 'hits': []}
        scan['repos_scanned'] = [_make_repo('webapp', stars=500, pushed_at=recent)]
        return scan

    def test_maturity_is_thinking(self):
        scan = self._make_thinking_scan()
        result = score_scan_results(scan)

        assert result.org_maturity_level == MaturitySegment.THINKING

    def test_stage1_passes(self):
        scan = self._make_thinking_scan()
        result = score_scan_results(scan)

        assert result.stage1_passed is True

    def test_legacy_fields_warm(self):
        scan = self._make_thinking_scan()
        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        assert scan['goldilocks_status'] == 'thinking'
        assert 'WARM' in scan['lead_status']
        assert 30 <= scan['intent_score'] <= 60

    def test_tier_is_one(self):
        scan = self._make_thinking_scan()
        _, tier, _ = _run_full_pipeline(scan)

        assert tier == 1


class TestPreparingOrg:
    """Tier 2 / PREPARING -- i18n library dependency without locale folders."""

    @staticmethod
    def _make_preparing_scan():
        recent = _recent_timestamp(5)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found react-i18next in package.json (webapp). No locale folders detected.',
                'Link': 'https://github.com/testcorp/webapp/blob/main/package.json',
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
                'Company': 'testcorp',
                'Signal': 'RFC Discussion',
                'Evidence': 'Issue: i18n integration plan',
                'Link': 'https://github.com/testcorp/webapp/issues/101',
                'priority': 'MEDIUM',
                'type': 'rfc_discussion',
                'repo': 'webapp',
                'created_at': recent,
            },
        ]
        scan['signal_summary']['dependency_injection'] = {
            'count': 1,
            'hits': [{'goldilocks_status': 'preparing', 'gap_verified': True, 'repo': 'webapp'}],
        }
        scan['signal_summary']['rfc_discussion'] = {'count': 1, 'hits': [], 'high_priority_count': 0}
        scan['repos_scanned'] = [_make_repo('webapp', stars=800, pushed_at=recent)]
        return scan

    def test_maturity_is_preparing(self):
        scan = self._make_preparing_scan()
        result = score_scan_results(scan)

        assert result.org_maturity_level == MaturitySegment.PREPARING

    def test_legacy_fields_hot(self):
        scan = self._make_preparing_scan()
        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        assert scan['goldilocks_status'] == 'preparing'
        assert 'HOT LEAD' in scan['lead_status']
        assert scan['intent_score'] >= 90

    def test_tier_is_two(self):
        scan = self._make_preparing_scan()
        _, tier, _ = _run_full_pipeline(scan)

        assert tier == 2


class TestActiveImplementationOrg:
    """Tier 2 / ACTIVE_IMPLEMENTATION -- library + active branch work, no locale folders."""

    @staticmethod
    def _make_active_impl_scan():
        recent = _recent_timestamp(3)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next, react-i18next in package.json',
                'Link': 'https://github.com/testcorp/webapp/blob/main/package.json',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'webapp',
                'file': 'package.json',
                'goldilocks_status': 'preparing',
                'gap_verified': True,
                'libraries_found': ['i18next', 'react-i18next'],
                'created_at': recent,
            },
            {
                'Company': 'testcorp',
                'Signal': 'Ghost Branch',
                'Evidence': 'Branch feature/add-translations found in webapp',
                'Link': 'https://github.com/testcorp/webapp/tree/feature/add-translations',
                'priority': 'HIGH',
                'type': 'ghost_branch',
                'repo': 'webapp',
                'pushed_at': recent,
                'created_at': recent,
            },
        ]
        scan['signal_summary']['dependency_injection'] = {
            'count': 1,
            'hits': [{'goldilocks_status': 'preparing', 'repo': 'webapp'}],
        }
        scan['signal_summary']['ghost_branch'] = {'count': 1, 'hits': [{'name': 'feature/add-translations', 'repo': 'webapp'}]}
        scan['repos_scanned'] = [_make_repo('webapp', stars=600, pushed_at=recent)]
        return scan

    def test_maturity_is_active_implementation(self):
        scan = self._make_active_impl_scan()
        result = score_scan_results(scan)

        assert result.org_maturity_level == MaturitySegment.ACTIVE_IMPLEMENTATION

    def test_legacy_fields_hot(self):
        scan = self._make_active_impl_scan()
        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        assert scan['goldilocks_status'] == 'preparing'
        assert 'HOT LEAD' in scan['lead_status']
        assert scan['intent_score'] >= 90

    def test_tier_is_two(self):
        scan = self._make_active_impl_scan()
        _, tier, _ = _run_full_pipeline(scan)

        assert tier == 2

    def test_interaction_bonus_raises_intent(self):
        """dependency_injection + ghost_branch should trigger an interaction bonus."""
        scan = self._make_active_impl_scan()
        result = score_scan_results(scan)

        # The combined dep+branch interaction should yield a higher p_intent
        # than either signal alone would.  Just verify it is comfortably above
        # the prior for ACTIVE_IMPLEMENTATION (0.75).
        assert result.p_intent > 0.75


class TestRecentlyLaunchedOrg:
    """Tier 3 / RECENTLY_LAUNCHED -- locale folders present, no TMS config."""

    @staticmethod
    def _make_launched_scan():
        older = _recent_timestamp(60)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Already Launched',
                'Evidence': 'Locale folders found: locales/en.json, locales/fr.json, locales/de.json',
                'Link': 'https://github.com/testcorp/webapp/tree/main/locales',
                'priority': 'LOW',
                'type': 'already_launched',
                'repo': 'webapp',
                'created_at': older,
            },
        ]
        scan['repos_scanned'] = [_make_repo('webapp', stars=400, pushed_at=older)]
        return scan

    def test_maturity_is_recently_launched(self):
        scan = self._make_launched_scan()
        result = score_scan_results(scan)

        assert result.org_maturity_level == MaturitySegment.RECENTLY_LAUNCHED

    def test_legacy_fields_low_priority(self):
        scan = self._make_launched_scan()
        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        assert scan['goldilocks_status'] == 'launched'
        assert 'LOW PRIORITY' in scan['lead_status']
        assert scan['intent_score'] == 10

    def test_tier_is_three(self):
        scan = self._make_launched_scan()
        _, tier, _ = _run_full_pipeline(scan)

        assert tier == 3


class TestMatureMidmarketOrg:
    """Tier 3 / MATURE_MIDMARKET -- already_launched + TMS config file."""

    @staticmethod
    def _make_mature_scan():
        older = _recent_timestamp(90)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Already Launched',
                'Evidence': 'Locale folders found: src/locales/en.json, src/locales/ja.json',
                'Link': 'https://github.com/testcorp/platform/tree/main/src/locales',
                'priority': 'LOW',
                'type': 'already_launched',
                'repo': 'platform',
                'created_at': older,
            },
            {
                'Company': 'testcorp',
                'Signal': 'TMS Config',
                'Evidence': 'Found crowdin.yml configuration file',
                'Link': 'https://github.com/testcorp/platform/blob/main/crowdin.yml',
                'priority': 'HIGH',
                'type': 'tms_config_file',
                'repo': 'platform',
                'file': 'crowdin.yml',
                'created_at': older,
            },
        ]
        scan['repos_scanned'] = [_make_repo('platform', stars=700, pushed_at=older)]
        return scan

    def test_maturity_is_mature_midmarket(self):
        scan = self._make_mature_scan()
        result = score_scan_results(scan)

        assert result.org_maturity_level == MaturitySegment.MATURE_MIDMARKET

    def test_legacy_fields_launched(self):
        scan = self._make_mature_scan()
        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        assert scan['goldilocks_status'] == 'launched'
        assert 'LOW PRIORITY' in scan['lead_status']

    def test_tier_is_three(self):
        scan = self._make_mature_scan()
        _, tier, _ = _run_full_pipeline(scan)

        assert tier == 3


class TestEnterpriseScaleOrg:
    """Tier 2 / ENTERPRISE_SCALE -- large org (>20k stars) with multiple signal types."""

    @staticmethod
    def _make_enterprise_scan():
        recent = _recent_timestamp(10)
        scan = _base_scan_results(
            org_login='megacorp',
            total_stars=50000,
            org_public_repos=500,
            org_public_members=200,
        )
        scan['signals'] = [
            {
                'Company': 'megacorp',
                'Signal': 'Smoking Gun Fork',
                'Evidence': 'Forked react-i18next into megacorp/react-i18next',
                'Link': 'https://github.com/megacorp/react-i18next',
                'priority': 'HIGH',
                'type': 'smoking_gun_fork',
                'repo': 'react-i18next-fork',
                'created_at': recent,
            },
            {
                'Company': 'megacorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in platform-app package.json',
                'Link': 'https://github.com/megacorp/platform-app/blob/main/package.json',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'platform-app',
                'file': 'package.json',
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
                'created_at': recent,
            },
            {
                'Company': 'megacorp',
                'Signal': 'RFC Discussion',
                'Evidence': 'Issue: i18n strategy RFC - enterprise rollout plan',
                'Link': 'https://github.com/megacorp/platform-app/issues/42',
                'priority': 'HIGH',
                'type': 'rfc_discussion',
                'repo': 'platform-app',
                'created_at': recent,
            },
        ]
        scan['signal_summary'] = {
            'rfc_discussion': {'count': 1, 'hits': [], 'high_priority_count': 1},
            'dependency_injection': {'count': 1, 'hits': [{'goldilocks_status': 'preparing'}]},
            'ghost_branch': {'count': 1, 'hits': []},
            'documentation_intent': {'count': 0, 'hits': [], 'high_priority_count': 0},
            'smoking_gun_fork': {'count': 1, 'hits': []},
            'enhanced_heuristics': {'count': 0, 'by_type': {}},
        }
        scan['repos_scanned'] = [
            _make_repo('platform-app', stars=5000, watchers=500, pushed_at=recent),
            _make_repo('react-i18next-fork', fork=True, stars=0, watchers=0, pushed_at=recent, language='JavaScript'),
            _make_repo('docs', stars=100, watchers=20, pushed_at=recent, language='Markdown'),
        ]
        return scan

    def test_maturity_is_enterprise_scale(self):
        scan = self._make_enterprise_scan()
        result = score_scan_results(scan)

        assert result.org_maturity_level == MaturitySegment.ENTERPRISE_SCALE

    def test_legacy_fields_enterprise(self):
        scan = self._make_enterprise_scan()
        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        # Enterprise with high intent maps to 'preparing' goldilocks
        # (per compat.py) unless p_intent < 0.60 which flips it.
        # With 4 strong signals, p_intent should be high.
        assert scan['goldilocks_status'] in ('preparing', 'launched')
        assert scan['intent_score'] >= 40

    def test_tier_is_two(self):
        """Enterprise scale maps to Tier 2 in _MATURITY_TO_TIER."""
        scan = self._make_enterprise_scan()
        _, tier, _ = _run_full_pipeline(scan)

        assert tier == 2

    def test_stage1_passes_despite_fork_signal(self):
        """The fork signal should be filtered by structural filters,
        but the non-fork signals keep stage1 passing."""
        scan = self._make_enterprise_scan()
        result = score_scan_results(scan)

        assert result.stage1_passed is True
        assert result.stage1_label == 'passed'


class TestForkOnlyOrg:
    """Stage 1 rejection -- all signals come from fork repos."""

    def test_all_forks_rejected_at_stage1(self):
        scan = _base_scan_results(org_login='forkonly')
        scan['signals'] = [
            {
                'Company': 'forkonly',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'forked-app',
                'fork': True,
                'created_at': _recent_timestamp(5),
            },
        ]
        scan['repos_scanned'] = [_make_repo('forked-app', fork=True, stars=0, watchers=0)]
        result = score_scan_results(scan)

        assert result.stage1_passed is False
        assert result.stage1_label == 'all_forks'
        assert result.org_maturity_level == MaturitySegment.PRE_I18N

    def test_fork_only_tier_zero(self):
        scan = _base_scan_results(org_login='forkonly')
        scan['signals'] = [
            {
                'Company': 'forkonly',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'forked-app',
                'fork': True,
                'created_at': _recent_timestamp(5),
            },
        ]
        scan['repos_scanned'] = [_make_repo('forked-app', fork=True, stars=0, watchers=0)]
        _, tier, _ = _run_full_pipeline(scan)

        assert tier == 0


class TestStage1FilteredSignalsDoNotPromoteTier:
    """Edge case: signals that survive enrichment but get rejected at Stage 1
    should NOT promote the org to a higher tier."""

    def test_all_filtered_signals_stay_pre_i18n(self):
        """If every enriched signal is marked is_filtered, stage1 should reject."""
        scan = _base_scan_results()
        # A single signal from a fork -- stage1 will see all_forks
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Ghost Branch',
                'Evidence': 'Branch feature/i18n in forked-lib',
                'Link': '',
                'priority': 'MEDIUM',
                'type': 'ghost_branch',
                'repo': 'forked-lib',
                'fork': True,
                'created_at': _recent_timestamp(10),
            },
        ]
        scan['repos_scanned'] = [_make_repo('forked-lib', fork=True, stars=0)]

        result, tier, _ = _run_full_pipeline(scan)

        assert result.org_maturity_level == MaturitySegment.PRE_I18N
        assert tier == 0


class TestTierConsistencyWithMaturity:
    """Verify that _MATURITY_TO_TIER mapping is respected end-to-end."""

    EXPECTED_TIERS = {
        MaturitySegment.PRE_I18N: 0,
        MaturitySegment.THINKING: 1,
        MaturitySegment.PREPARING: 2,
        MaturitySegment.ACTIVE_IMPLEMENTATION: 2,
        MaturitySegment.RECENTLY_LAUNCHED: 3,
        MaturitySegment.MATURE_MIDMARKET: 3,
        MaturitySegment.ENTERPRISE_SCALE: 2,
    }

    def test_mapping_table_matches_expected(self):
        """Ensure the compat.py mapping matches our expectations exactly."""
        for segment, expected_tier in self.EXPECTED_TIERS.items():
            actual = _MATURITY_TO_TIER.get(segment)
            assert actual == expected_tier, (
                f"{segment.value} expected tier {expected_tier}, got {actual}"
            )


class TestScoringResultApplyToScanResults:
    """Verify that apply_to_scan_results sets all required legacy fields."""

    def test_legacy_keys_always_present(self):
        scan = _base_scan_results()
        # Even an empty result should produce these keys
        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        assert 'intent_score' in scan
        assert 'goldilocks_status' in scan
        assert 'lead_status' in scan

    def test_intent_score_is_int(self):
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found next-intl in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'app',
                'goldilocks_status': 'preparing',
                'created_at': _recent_timestamp(5),
            },
        ]
        scan['repos_scanned'] = [_make_repo('app')]

        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        assert isinstance(scan['intent_score'], int)

    def test_goldilocks_status_valid_values(self):
        """goldilocks_status must be one of the known values."""
        valid = {'none', 'thinking', 'preparing', 'launched'}
        scan = _base_scan_results()
        result = score_scan_results(scan)
        result.apply_to_scan_results(scan)

        assert scan['goldilocks_status'] in valid


class TestScoringV2ToCalculateTier:
    """Verify calculate_tier_from_scan takes the V2 code path when scoring_v2 is set."""

    def test_v2_path_used(self):
        """When scoring_v2 is populated, calculate_tier_from_scan should use it
        and the evidence string should start with 'V2:'."""
        recent = _recent_timestamp(5)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'webapp',
                'goldilocks_status': 'preparing',
                'created_at': recent,
            },
        ]
        scan['repos_scanned'] = [_make_repo('webapp', pushed_at=recent)]

        _, tier, evidence = _run_full_pipeline(scan)

        assert evidence.startswith('V2:')
        assert tier == 2


class TestSmokingGunForkProducesPreparingTier:
    """A smoking_gun_fork (forking an i18n library) is a strong PREPARING signal."""

    def test_smoking_gun_fork_tier_two(self):
        recent = _recent_timestamp(7)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Smoking Gun Fork',
                'Evidence': 'Forked formatjs into testcorp/formatjs',
                'Link': 'https://github.com/testcorp/formatjs',
                'priority': 'HIGH',
                'type': 'smoking_gun_fork',
                'repo': 'formatjs',
                'created_at': recent,
            },
        ]
        scan['signal_summary']['smoking_gun_fork'] = {'count': 1, 'hits': []}
        scan['repos_scanned'] = [_make_repo('formatjs', stars=50, pushed_at=recent)]

        result, tier, _ = _run_full_pipeline(scan)

        assert result.org_maturity_level == MaturitySegment.PREPARING
        assert tier == 2


class TestMultiRepoSignalSpread:
    """Signals spread across multiple repos should still produce a correct tier."""

    def test_multi_repo_preparing(self):
        recent = _recent_timestamp(5)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found react-i18next in frontend package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'frontend',
                'goldilocks_status': 'preparing',
                'created_at': recent,
            },
            {
                'Company': 'testcorp',
                'Signal': 'RFC Discussion',
                'Evidence': 'Issue: i18n requirements in backend',
                'Link': '',
                'priority': 'MEDIUM',
                'type': 'rfc_discussion',
                'repo': 'backend',
                'created_at': recent,
            },
        ]
        scan['repos_scanned'] = [
            _make_repo('frontend', stars=300, pushed_at=recent),
            _make_repo('backend', stars=250, pushed_at=recent, language='Python'),
        ]

        result, tier, _ = _run_full_pipeline(scan)

        assert result.org_maturity_level == MaturitySegment.PREPARING
        assert tier == 2


class TestLaunchedPlusDepProducesTier3:
    """If already_launched is present along with dependency_injection, the
    launched signal should take precedence -- the org is RECENTLY_LAUNCHED
    (tier 3), not PREPARING."""

    def test_launched_overrides_dep(self):
        recent = _recent_timestamp(30)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Already Launched',
                'Evidence': 'Locale folders found: locales/en.json, locales/es.json',
                'Link': '',
                'priority': 'LOW',
                'type': 'already_launched',
                'repo': 'main-app',
                'created_at': recent,
            },
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'main-app',
                'goldilocks_status': 'preparing',
                'created_at': recent,
            },
        ]
        scan['repos_scanned'] = [_make_repo('main-app', stars=500, pushed_at=recent)]

        result, tier, _ = _run_full_pipeline(scan)

        assert result.org_maturity_level == MaturitySegment.RECENTLY_LAUNCHED
        assert tier == 3


class TestDocumentationIntentAloneIsThinking:
    """A documentation_intent signal without library deps should be THINKING."""

    def test_doc_intent_thinking(self):
        recent = _recent_timestamp(10)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Documentation Intent',
                'Evidence': 'README mentions planned i18n support',
                'Link': 'https://github.com/testcorp/webapp/blob/main/README.md',
                'priority': 'MEDIUM',
                'type': 'documentation_intent',
                'repo': 'webapp',
                'file': 'README.md',
                'created_at': recent,
            },
        ]
        scan['repos_scanned'] = [_make_repo('webapp', pushed_at=recent)]

        result = score_scan_results(scan)

        assert result.org_maturity_level == MaturitySegment.THINKING
        assert result.stage1_passed is True


class TestEnrichedSignalCountMatchesInput:
    """The ScoringResult should contain enriched versions of all input signals
    plus any synthetic signals derived from scan_results."""

    def test_enriched_count_at_least_input(self):
        recent = _recent_timestamp(5)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'webapp',
                'created_at': recent,
            },
            {
                'Company': 'testcorp',
                'Signal': 'Ghost Branch',
                'Evidence': 'Branch feature/i18n in webapp',
                'Link': '',
                'priority': 'HIGH',
                'type': 'ghost_branch',
                'repo': 'webapp',
                'pushed_at': recent,
            },
        ]
        scan['repos_scanned'] = [_make_repo('webapp')]

        result = score_scan_results(scan)

        # At minimum, we should have the 2 input signals enriched
        assert len(result.enriched_signals) >= 2


class TestStructuredOutputCompleteness:
    """ScoringResult.to_structured_output() should produce all expected keys
    that calculate_tier_from_scan looks for."""

    def test_scoring_v2_has_required_keys(self):
        recent = _recent_timestamp(5)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found react-intl in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'webapp',
                'goldilocks_status': 'preparing',
                'created_at': recent,
            },
        ]
        scan['repos_scanned'] = [_make_repo('webapp')]

        result = score_scan_results(scan)
        output = result.to_structured_output()

        required_keys = [
            'org_maturity_level',
            'org_maturity_label',
            'confidence_percent',
            'readiness_index',
            'outreach_angle_label',
            'p_intent',
            'stage1_passed',
        ]
        for key in required_keys:
            assert key in output, f"Missing key '{key}' in structured output"


class TestDecayReducesOldSignalImpact:
    """Old signals should produce a lower p_intent than fresh signals with
    identical types, due to exponential decay."""

    def test_old_vs_fresh_intent_score(self):
        # Fresh scan
        fresh = _recent_timestamp(2)
        scan_fresh = _base_scan_results()
        scan_fresh['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'webapp',
                'goldilocks_status': 'preparing',
                'created_at': fresh,
            },
        ]
        scan_fresh['repos_scanned'] = [_make_repo('webapp', pushed_at=fresh)]

        # Old scan -- same signal but 300 days old
        old = _old_timestamp(300)
        scan_old = _base_scan_results(org_login='oldcorp')
        scan_old['signals'] = [
            {
                'Company': 'oldcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'webapp',
                'goldilocks_status': 'preparing',
                'created_at': old,
            },
        ]
        scan_old['repos_scanned'] = [_make_repo('webapp', pushed_at=old)]

        result_fresh = score_scan_results(scan_fresh)
        result_old = score_scan_results(scan_old)

        # Fresh signal should yield higher p_intent
        assert result_fresh.p_intent > result_old.p_intent


class TestProvenBuyerPattern:
    """Org with already_launched + preparing signals should trigger the
    proven-buyer multiplier in the org scorer."""

    def test_proven_buyer_detected(self):
        recent = _recent_timestamp(7)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Already Launched',
                'Evidence': 'Locale folders in legacy-app',
                'Link': '',
                'priority': 'LOW',
                'type': 'already_launched',
                'repo': 'legacy-app',
                'created_at': recent,
            },
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found react-i18next in new-frontend',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'new-frontend',
                'goldilocks_status': 'preparing',
                'created_at': recent,
            },
        ]
        scan['repos_scanned'] = [
            _make_repo('legacy-app', stars=600, pushed_at=recent),
            _make_repo('new-frontend', stars=300, pushed_at=recent),
        ]

        result = score_scan_results(scan)

        # With already_launched present, maturity should be RECENTLY_LAUNCHED
        # (since the launched check happens before preparing).
        assert result.org_maturity_level == MaturitySegment.RECENTLY_LAUNCHED

        # The org_score should have a proven_buyer_multiplier > 1.0
        assert result.org_score is not None
        assert result.org_score.proven_buyer_multiplier > 1.0


class TestIdempotency:
    """Running the pipeline twice on the same scan_results dict should
    produce the same result."""

    def test_double_run_same_result(self):
        recent = _recent_timestamp(5)
        scan = _base_scan_results()
        scan['signals'] = [
            {
                'Company': 'testcorp',
                'Signal': 'Dependency Injection',
                'Evidence': 'Found i18next in package.json',
                'Link': '',
                'priority': 'HIGH',
                'type': 'dependency_injection',
                'repo': 'webapp',
                'goldilocks_status': 'preparing',
                'created_at': recent,
            },
            {
                'Company': 'testcorp',
                'Signal': 'Ghost Branch',
                'Evidence': 'Branch feature/i18n',
                'Link': '',
                'priority': 'HIGH',
                'type': 'ghost_branch',
                'repo': 'webapp',
                'pushed_at': recent,
            },
        ]
        scan['repos_scanned'] = [_make_repo('webapp', pushed_at=recent)]

        # Run 1
        scan_copy1 = copy.deepcopy(scan)
        result1 = score_scan_results(scan_copy1)

        # Run 2
        scan_copy2 = copy.deepcopy(scan)
        result2 = score_scan_results(scan_copy2)

        assert result1.org_maturity_level == result2.org_maturity_level
        assert result1.stage1_passed == result2.stage1_passed
        assert result1.p_intent == pytest.approx(result2.p_intent, abs=1e-6)


class TestReadinessIndexRange:
    """Readiness index must always be between 0.0 and 1.0."""

    @pytest.mark.parametrize("scenario_fn", [
        # Empty
        lambda: _base_scan_results(),
        # Thinking
        lambda: (
            s := _base_scan_results(),
            s.__setitem__('signals', [
                {'Company': 'x', 'Signal': 'RFC', 'Evidence': 'i18n RFC',
                 'type': 'rfc_discussion', 'repo': 'app', 'created_at': _recent_timestamp(5)},
            ]),
            s.__setitem__('repos_scanned', [_make_repo('app')]),
            s,
        )[-1],
        # Preparing
        lambda: (
            s := _base_scan_results(),
            s.__setitem__('signals', [
                {'Company': 'x', 'Signal': 'Dep', 'Evidence': 'i18next',
                 'type': 'dependency_injection', 'repo': 'app',
                 'goldilocks_status': 'preparing', 'created_at': _recent_timestamp(3)},
            ]),
            s.__setitem__('repos_scanned', [_make_repo('app')]),
            s,
        )[-1],
    ])
    def test_readiness_in_bounds(self, scenario_fn):
        scan = scenario_fn()
        result = score_scan_results(scan)

        assert 0.0 <= result.readiness_index <= 1.0
