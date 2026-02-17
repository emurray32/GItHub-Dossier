"""Tests for filter and decay module."""
import pytest
import math
from scoring.filters import (
    apply_structural_filters,
    apply_domain_filters,
    apply_contextual_filters,
    apply_decay,
    apply_contributor_heuristics,
    compute_revenue_proxies,
)
from scoring.signal_enrichment import enrich_signals
from scoring.models import EnrichedSignal, SignalCategory


class TestStructuralFilters:
    def test_fork_filtered(self, fork_scan_results):
        enriched = enrich_signals(
            fork_scan_results['signals'], fork_scan_results
        )
        filtered = apply_structural_filters(enriched, fork_scan_results)
        fork_sigs = [s for s in filtered if s.repo == 'forked-app']
        for s in fork_sigs:
            assert s.is_filtered is True
            assert s.filter_reason == 'fork_repo'

    def test_non_fork_not_filtered(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        filtered = apply_structural_filters(enriched, preparing_scan_results)
        webapp_sigs = [s for s in filtered if s.repo == 'webapp']
        for s in webapp_sigs:
            assert s.is_filtered is False


class TestDomainFilters:
    def test_tutorial_repo_reduced(self):
        scan_results = {
            'org_login': 'testorg',
            'org_description': '',
            'signals': [],
            'repos_scanned': [],
        }
        signal = EnrichedSignal(
            signal_type='dependency_injection',
            evidence='Found i18next',
            repo='react-tutorial',
        )
        filtered = apply_domain_filters([signal], scan_results)
        assert filtered[0].filter_multiplier == pytest.approx(0.20, abs=0.01)

    def test_open_protocol_reduced(self):
        scan_results = {
            'org_login': 'testorg',
            'org_description': 'A decentralized protocol for the future',
            'signals': [],
            'repos_scanned': [],
        }
        signal = EnrichedSignal(
            signal_type='dependency_injection',
            evidence='Found i18next',
            repo='main-app',
        )
        filtered = apply_domain_filters([signal], scan_results)
        assert filtered[0].filter_multiplier == pytest.approx(0.20, abs=0.01)


class TestContextualFilters:
    def test_docs_only_reduced(self):
        signal = EnrichedSignal(
            signal_type='documentation_intent',
            evidence='i18n mentioned',
            file_path='docs/README.md',
        )
        filtered = apply_contextual_filters([signal])
        assert filtered[0].filter_multiplier == pytest.approx(0.50, abs=0.01)


class TestDecay:
    def test_no_age_no_decay(self):
        signal = EnrichedSignal(
            signal_type='dependency_injection',
            evidence='Found lib',
            raw_strength=2.0,
            signal_category=SignalCategory.LIBRARY_INSTALL,
        )
        decayed = apply_decay([signal])
        assert decayed[0].decayed_strength == pytest.approx(2.0, abs=0.01)

    def test_old_signal_decayed(self):
        signal = EnrichedSignal(
            signal_type='ghost_branch',
            evidence='Branch found',
            raw_strength=1.5,
            age_in_days=42,  # 2 half-lives for branch_commit (21d)
            signal_category=SignalCategory.BRANCH_COMMIT,
        )
        decayed = apply_decay([signal])
        # After 2 half-lives: 1.5 × 0.25 = 0.375
        assert decayed[0].decayed_strength == pytest.approx(0.375, abs=0.05)

    def test_half_life_formula(self):
        """Verify: decayed = raw × 0.5^(age/half_life)"""
        signal = EnrichedSignal(
            signal_type='rfc_discussion',
            evidence='RFC',
            raw_strength=1.2,
            age_in_days=30,  # PR_ISSUE half-life = 30
            signal_category=SignalCategory.PR_ISSUE,
        )
        decayed = apply_decay([signal])
        expected = 1.2 * math.pow(0.5, 30 / 30)  # = 0.6
        assert decayed[0].decayed_strength == pytest.approx(expected, abs=0.01)


class TestContributorHeuristics:
    def test_corporate_boost(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        # Apply decay first to set decayed_strength
        enriched = apply_decay(enriched)
        original_strengths = [s.decayed_strength for s in enriched if not s.is_filtered]

        boosted = apply_contributor_heuristics(enriched, preparing_scan_results)
        # All contributors have company → >50% corporate ratio → 1.2x boost
        for i, s in enumerate(boosted):
            if not s.is_filtered and i < len(original_strengths):
                assert s.decayed_strength >= original_strengths[i]


class TestRevenueProxies:
    def test_basic(self, preparing_scan_results):
        proxies = compute_revenue_proxies(preparing_scan_results)
        assert isinstance(proxies, dict)
        assert 'verified_domain' in proxies
        assert 'many_members' in proxies
