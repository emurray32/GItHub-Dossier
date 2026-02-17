"""Tests for output formatter module."""
import pytest
from scoring.output_formatter import (
    format_output,
    classify_outreach_angle,
    classify_risk_level,
)
from scoring.signal_enrichment import enrich_signals
from scoring.maturity import classify_maturity, calculate_confidence
from scoring.org_scorer import build_repo_scores, score_organization
from scoring.bayesian_pipeline import stage2_bayesian_scorer
from scoring.readiness import calculate_readiness_index
from scoring.filters import apply_decay
from scoring.models import (
    MaturitySegment, OutreachAngle, RiskLevel, EnrichedSignal, OrgScore,
)


class TestClassifyOutreachAngle:
    def test_enterprise(self):
        angle = classify_outreach_angle(
            MaturitySegment.ENTERPRISE_SCALE, [], {}
        )
        assert angle == OutreachAngle.ENTERPRISE_STRATEGIC

    def test_preparing_with_deps(self):
        signals = [
            EnrichedSignal(signal_type='dependency_injection', evidence='test'),
        ]
        angle = classify_outreach_angle(
            MaturitySegment.PREPARING, signals, {}
        )
        assert angle == OutreachAngle.IMPLEMENTATION_PARTNER

    def test_greenfield(self):
        angle = classify_outreach_angle(
            MaturitySegment.PRE_I18N, [], {}
        )
        assert angle == OutreachAngle.GREENFIELD_EDUCATOR

    def test_recently_launched(self):
        angle = classify_outreach_angle(
            MaturitySegment.RECENTLY_LAUNCHED, [], {}
        )
        assert angle == OutreachAngle.EXPANSION_ACCELERATOR

    def test_mature_midmarket(self):
        angle = classify_outreach_angle(
            MaturitySegment.MATURE_MIDMARKET, [], {}
        )
        assert angle == OutreachAngle.SCALE_OPTIMIZER

    def test_pain_driven(self):
        signals = [
            EnrichedSignal(signal_type='rfc_discussion', evidence='test'),
        ]
        angle = classify_outreach_angle(
            MaturitySegment.PRE_I18N, signals, {}
        )
        assert angle == OutreachAngle.PAIN_DRIVEN

    def test_migration_candidate(self):
        signals = [
            EnrichedSignal(
                signal_type='tms_config_file',
                evidence='TMS config detected: crowdin.yml',
            ),
        ]
        angle = classify_outreach_angle(
            MaturitySegment.PREPARING, signals, {}
        )
        assert angle == OutreachAngle.MIGRATION_CANDIDATE


class TestClassifyRiskLevel:
    def test_high_confidence_low_risk(self):
        signals = [
            EnrichedSignal(signal_type='dep', evidence='a', age_in_days=5),
            EnrichedSignal(signal_type='rfc', evidence='b', age_in_days=10),
            EnrichedSignal(signal_type='ghost', evidence='c', age_in_days=3),
        ]
        risk = classify_risk_level(0.8, signals, {})
        assert risk == RiskLevel.LOW

    def test_low_confidence_high_risk(self):
        signals = [EnrichedSignal(signal_type='dep', evidence='a')]
        risk = classify_risk_level(0.2, signals, {})
        assert risk == RiskLevel.HIGH


class TestFormatOutput:
    def test_full_pipeline_output(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        enriched = apply_decay(enriched)
        maturity = classify_maturity(enriched, preparing_scan_results)
        confidence = calculate_confidence(enriched, maturity)
        p_intent, log_odds = stage2_bayesian_scorer(enriched, maturity)
        repo_scores = build_repo_scores(enriched, preparing_scan_results)
        org_score = score_organization(repo_scores, enriched, preparing_scan_results)
        readiness, rc = calculate_readiness_index(enriched, preparing_scan_results)

        result = format_output(
            enriched_signals=enriched,
            maturity=maturity,
            p_intent=p_intent,
            log_odds=log_odds,
            org_score=org_score,
            readiness=readiness,
            readiness_components=rc,
            confidence=confidence,
            scan_results=preparing_scan_results,
            stage1_passed=True,
            stage1_label='passed',
        )

        assert result.org_intent_score > 0.0
        assert result.confidence_percent > 0.0
        assert isinstance(result.recommended_outreach_angle, OutreachAngle)
        assert isinstance(result.risk_level, RiskLevel)
        assert result.stage1_passed is True
