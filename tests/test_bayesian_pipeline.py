"""Tests for the 3-stage Bayesian pipeline."""
import pytest
from scoring.bayesian_pipeline import (
    stage1_fast_filter,
    stage2_bayesian_scorer,
    stage3_enterprise_adjuster,
    sigmoid,
    prob_to_log_odds,
)
from scoring.signal_enrichment import enrich_signals
from scoring.maturity import classify_maturity
from scoring.org_scorer import build_repo_scores, score_organization
from scoring.models import MaturitySegment, OrgScore


class TestSigmoid:
    def test_zero(self):
        assert sigmoid(0) == pytest.approx(0.5, abs=0.001)

    def test_large_positive(self):
        assert sigmoid(100) == pytest.approx(1.0, abs=0.001)

    def test_large_negative(self):
        assert sigmoid(-100) == pytest.approx(0.0, abs=0.001)


class TestProbToLogOdds:
    def test_half(self):
        assert prob_to_log_odds(0.5) == pytest.approx(0.0, abs=0.001)

    def test_high(self):
        assert prob_to_log_odds(0.9) > 0

    def test_low(self):
        assert prob_to_log_odds(0.1) < 0


class TestStage1FastFilter:
    def test_no_signals_rejected(self, empty_scan_results):
        passed, label = stage1_fast_filter([], empty_scan_results)
        assert passed is False
        assert label == 'no_signals'

    def test_preparing_passes(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        passed, label = stage1_fast_filter(enriched, preparing_scan_results)
        assert passed is True
        assert label == 'passed'

    def test_all_filtered_rejected(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        for s in enriched:
            s.is_filtered = True
        passed, label = stage1_fast_filter(enriched, preparing_scan_results)
        assert passed is False
        assert label == 'all_filtered'


class TestStage2BayesianScorer:
    def test_preparing_high_intent(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        maturity = classify_maturity(enriched, preparing_scan_results)
        p_intent, log_odds = stage2_bayesian_scorer(enriched, maturity)
        assert p_intent > 0.5  # Preparing should have high intent
        assert log_odds > 0

    def test_pre_i18n_low_intent(self):
        p_intent, log_odds = stage2_bayesian_scorer([], MaturitySegment.PRE_I18N)
        assert p_intent < 0.1

    def test_enterprise_with_signals(self, enterprise_scan_results):
        enriched = enrich_signals(
            enterprise_scan_results['signals'], enterprise_scan_results
        )
        p_intent, log_odds = stage2_bayesian_scorer(
            enriched, MaturitySegment.ENTERPRISE_SCALE
        )
        assert p_intent > 0.4


class TestStage3EnterpriseAdjuster:
    def test_no_org_score(self):
        result = stage3_enterprise_adjuster(0.7, None, {})
        assert result == 0.7

    def test_with_org_score(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        repo_scores = build_repo_scores(enriched, preparing_scan_results)
        org_score = score_organization(repo_scores, enriched, preparing_scan_results)

        result = stage3_enterprise_adjuster(0.7, org_score, preparing_scan_results)
        assert 0.0 <= result <= 1.0

    def test_cluster_boost(self):
        org = OrgScore(composite=0.6, cluster_bonus=1.6)
        result = stage3_enterprise_adjuster(0.5, org, {})
        assert result > 0.5  # Cluster bonus should increase


class TestThresholdEdgeCases:
    """Test exact threshold boundaries."""

    def test_exactly_at_075(self, preparing_scan_results):
        """P(intent) at exactly 0.75 → hot_lead threshold."""
        from scoring.woe_tables import THRESHOLDS
        assert THRESHOLDS['hot_lead'] == 0.75

    def test_exactly_at_050(self):
        from scoring.woe_tables import THRESHOLDS
        assert THRESHOLDS['warm_lead'] == 0.50

    def test_exactly_at_030(self):
        from scoring.woe_tables import THRESHOLDS
        assert THRESHOLDS['monitor'] == 0.30

    def test_exactly_at_015(self):
        from scoring.woe_tables import THRESHOLDS
        assert THRESHOLDS['cold'] == 0.15
