"""
End-to-end integration tests.

Feeds realistic scan_data through the full scoring pipeline and verifies:
1. Output is JSON-serializable (critical for SSE streaming)
2. Legacy fields always present and valid
3. scoring_v2 namespace has all required fields
"""
import json
import pytest
from scoring import score_scan_results
from scoring.models import MaturitySegment, OutreachAngle, RiskLevel


class TestFullPipeline:
    def test_zero_signals_rejected(self, empty_scan_results):
        """Zero signals → rejected at Stage 1."""
        result = score_scan_results(empty_scan_results)
        assert result.stage1_passed is False
        assert result.org_maturity_level == MaturitySegment.PRE_I18N

    def test_preparing_pipeline(self, preparing_scan_results):
        """Preparing repo → PREPARING segment, P(intent) > 0.5."""
        result = score_scan_results(preparing_scan_results)
        assert result.stage1_passed is True
        assert result.org_maturity_level in (
            MaturitySegment.PREPARING,
            MaturitySegment.ACTIVE_IMPLEMENTATION,
        )
        assert result.p_intent > 0.5

    def test_enterprise_pipeline(self, enterprise_scan_results):
        """Enterprise org with signal clustering → ENTERPRISE_SCALE."""
        result = score_scan_results(enterprise_scan_results)
        assert result.stage1_passed is True
        assert result.org_maturity_level == MaturitySegment.ENTERPRISE_SCALE

    def test_launched_pipeline(self, launched_scan_results):
        """Launched org → RECENTLY_LAUNCHED segment."""
        result = score_scan_results(launched_scan_results)
        assert result.org_maturity_level == MaturitySegment.RECENTLY_LAUNCHED

    def test_mixed_signal_proven_buyer(self, mixed_signal_results):
        """Mixed signals (1 mature + 3 preparing) → proven-buyer multiplier."""
        result = score_scan_results(mixed_signal_results)
        assert result.stage1_passed is True
        if result.org_score:
            assert result.org_score.proven_buyer_multiplier >= 1.0


class TestJSONSerializable:
    """Critical: all output must be JSON-serializable for SSE streaming."""

    def test_preparing_serializable(self, preparing_scan_results):
        result = score_scan_results(preparing_scan_results)
        output = result.to_structured_output()
        # Must not raise
        json_str = json.dumps(output)
        assert json_str
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_enterprise_serializable(self, enterprise_scan_results):
        result = score_scan_results(enterprise_scan_results)
        output = result.to_structured_output()
        json_str = json.dumps(output)
        assert json_str

    def test_empty_serializable(self, empty_scan_results):
        result = score_scan_results(empty_scan_results)
        output = result.to_structured_output()
        json_str = json.dumps(output)
        assert json_str


class TestLegacyFieldsPresent:
    """Verify legacy fields are always set correctly."""

    def test_preparing_legacy(self, preparing_scan_results):
        result = score_scan_results(preparing_scan_results)
        legacy = {}
        result.apply_to_scan_results(legacy)
        assert 'intent_score' in legacy
        assert 'goldilocks_status' in legacy
        assert 'lead_status' in legacy
        assert isinstance(legacy['intent_score'], int)
        assert legacy['goldilocks_status'] in ('preparing', 'launched', 'none')

    def test_empty_legacy(self, empty_scan_results):
        result = score_scan_results(empty_scan_results)
        legacy = {}
        result.apply_to_scan_results(legacy)
        assert legacy['goldilocks_status'] == 'none'
        assert legacy['intent_score'] == 0

    def test_launched_legacy(self, launched_scan_results):
        result = score_scan_results(launched_scan_results)
        legacy = {}
        result.apply_to_scan_results(legacy)
        assert legacy['goldilocks_status'] == 'launched'
        assert legacy['intent_score'] == 10


class TestScoringV2Namespace:
    """Verify scoring_v2 output has all required fields."""

    REQUIRED_FIELDS = [
        'org_intent_score',
        'org_maturity_level',
        'org_maturity_label',
        'org_maturity_color',
        'readiness_index',
        'readiness_components',
        'p_intent',
        'log_odds',
        'recommended_outreach_angle',
        'outreach_angle_label',
        'outreach_angle_description',
        'risk_level',
        'risk_level_label',
        'confidence_percent',
        'confidence_factors',
        'signal_clusters_detected',
        'primary_repo_of_concern',
        'recommended_sales_motion',
        'stage1_passed',
        'stage1_label',
        'enriched_signal_count',
        'enriched_signals',
    ]

    def test_preparing_has_all_fields(self, preparing_scan_results):
        result = score_scan_results(preparing_scan_results)
        output = result.to_structured_output()
        for field in self.REQUIRED_FIELDS:
            assert field in output, f"Missing field: {field}"

    def test_empty_has_all_fields(self, empty_scan_results):
        result = score_scan_results(empty_scan_results)
        output = result.to_structured_output()
        for field in self.REQUIRED_FIELDS:
            assert field in output, f"Missing field: {field}"


class TestOutreachAngleClassification:
    """Test all 7 outreach angles are reachable."""

    def test_greenfield(self, empty_scan_results):
        result = score_scan_results(empty_scan_results)
        # PRE_I18N with no signals → GREENFIELD_EDUCATOR
        assert result.recommended_outreach_angle == OutreachAngle.GREENFIELD_EDUCATOR

    def test_enterprise_strategic(self, enterprise_scan_results):
        result = score_scan_results(enterprise_scan_results)
        assert result.recommended_outreach_angle == OutreachAngle.ENTERPRISE_STRATEGIC

    def test_expansion_accelerator(self, launched_scan_results):
        result = score_scan_results(launched_scan_results)
        assert result.recommended_outreach_angle == OutreachAngle.EXPANSION_ACCELERATOR
