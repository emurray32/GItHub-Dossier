"""Tests for org-level scoring module."""
import pytest
from scoring.org_scorer import (
    build_repo_scores, score_organization,
    calculate_cluster_bonus, detect_proven_buyer,
)
from scoring.signal_enrichment import enrich_signals


class TestBuildRepoScores:
    def test_empty(self, empty_scan_results):
        enriched = enrich_signals([], empty_scan_results)
        scores = build_repo_scores(enriched, empty_scan_results)
        assert scores == []

    def test_basic_scoring(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        scores = build_repo_scores(enriched, preparing_scan_results)
        assert len(scores) > 0
        # webapp should be Tier 1
        webapp = next((r for r in scores if r.repo_name == 'webapp'), None)
        if webapp:
            assert webapp.tier == 1
            assert webapp.tier_weight == 1.0

    def test_fork_excluded(self, enterprise_scan_results):
        enriched = enrich_signals(
            enterprise_scan_results['signals'], enterprise_scan_results
        )
        scores = build_repo_scores(enriched, enterprise_scan_results)
        fork_repo = next(
            (r for r in scores if 'fork' in r.repo_name.lower() and r.is_fork),
            None
        )
        if fork_repo:
            assert fork_repo.tier == 0
            assert fork_repo.tier_weight == 0.0


class TestScoreOrganization:
    def test_empty(self, empty_scan_results):
        org = score_organization([], [], empty_scan_results)
        assert org.composite == 0.0

    def test_preparing_org(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        repo_scores = build_repo_scores(enriched, preparing_scan_results)
        org = score_organization(repo_scores, enriched, preparing_scan_results)
        assert org.composite > 0.0
        assert org.peak_score > 0.0

    def test_enterprise_cluster_bonus(self, enterprise_scan_results):
        enriched = enrich_signals(
            enterprise_scan_results['signals'], enterprise_scan_results
        )
        repo_scores = build_repo_scores(enriched, enterprise_scan_results)
        org = score_organization(repo_scores, enriched, enterprise_scan_results)
        # Enterprise has multiple repos with signals
        assert org.composite > 0.0


class TestClusterBonus:
    def test_no_cluster(self):
        from scoring.models import RepoScore
        repos = [RepoScore(repo_name='a', signal_count=1), RepoScore(repo_name='b', signal_count=1)]
        assert calculate_cluster_bonus(repos) == 1.0

    def test_cluster_bonus_applied(self):
        from scoring.models import RepoScore
        repos = [
            RepoScore(repo_name='a', signal_count=2),
            RepoScore(repo_name='b', signal_count=1),
            RepoScore(repo_name='c', signal_count=3),
        ]
        bonus = calculate_cluster_bonus(repos)
        assert bonus > 1.0
        assert bonus == 1.0 + 0.2 * 3


class TestDetectProvenBuyer:
    def test_proven_buyer(self, mixed_signal_results):
        enriched = enrich_signals(
            mixed_signal_results['signals'], mixed_signal_results
        )
        multiplier = detect_proven_buyer(enriched, mixed_signal_results)
        assert multiplier == 1.3

    def test_no_proven_buyer(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        multiplier = detect_proven_buyer(enriched, preparing_scan_results)
        # No launched signal → no proven buyer
        assert multiplier == 1.0
