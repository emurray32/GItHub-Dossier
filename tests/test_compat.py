"""Tests for backward compatibility module."""
import pytest
from scoring.compat import map_to_legacy, _score_to_intent
from scoring.models import ScoringResult, MaturitySegment


class TestMapToLegacy:
    def test_pre_i18n(self):
        result = ScoringResult(
            org_maturity_level=MaturitySegment.PRE_I18N,
            org_intent_score=0.0,
        )
        legacy = map_to_legacy(result)
        assert legacy['intent_score'] == 0
        assert legacy['goldilocks_status'] == 'none'
        assert 'COLD' in legacy['lead_status']

    def test_preparing(self):
        result = ScoringResult(
            org_maturity_level=MaturitySegment.PREPARING,
            org_intent_score=0.75,
        )
        legacy = map_to_legacy(result)
        assert 90 <= legacy['intent_score'] <= 100
        assert legacy['goldilocks_status'] == 'preparing'
        assert 'HOT' in legacy['lead_status']

    def test_active_implementation(self):
        result = ScoringResult(
            org_maturity_level=MaturitySegment.ACTIVE_IMPLEMENTATION,
            org_intent_score=0.80,
        )
        legacy = map_to_legacy(result)
        assert 90 <= legacy['intent_score'] <= 100
        assert legacy['goldilocks_status'] == 'preparing'

    def test_recently_launched(self):
        result = ScoringResult(
            org_maturity_level=MaturitySegment.RECENTLY_LAUNCHED,
            org_intent_score=0.30,
        )
        legacy = map_to_legacy(result)
        assert legacy['intent_score'] == 10
        assert legacy['goldilocks_status'] == 'launched'
        assert 'LOW PRIORITY' in legacy['lead_status']

    def test_mature_midmarket(self):
        result = ScoringResult(
            org_maturity_level=MaturitySegment.MATURE_MIDMARKET,
            org_intent_score=0.20,
        )
        legacy = map_to_legacy(result)
        assert legacy['intent_score'] == 10
        assert legacy['goldilocks_status'] == 'launched'

    def test_enterprise_high_intent(self):
        result = ScoringResult(
            org_maturity_level=MaturitySegment.ENTERPRISE_SCALE,
            org_intent_score=0.80,
        )
        legacy = map_to_legacy(result)
        assert legacy['intent_score'] >= 90
        assert legacy['goldilocks_status'] == 'preparing'

    def test_enterprise_low_intent(self):
        result = ScoringResult(
            org_maturity_level=MaturitySegment.ENTERPRISE_SCALE,
            org_intent_score=0.30,
        )
        legacy = map_to_legacy(result)
        assert legacy['goldilocks_status'] == 'launched'
        assert 'LOW PRIORITY' in legacy['lead_status']


class TestLegacyFieldsAlwaysPresent:
    """Verify legacy fields are always set regardless of maturity."""

    @pytest.mark.parametrize("maturity", list(MaturitySegment))
    def test_all_segments_produce_legacy_fields(self, maturity):
        result = ScoringResult(
            org_maturity_level=maturity,
            org_intent_score=0.5,
        )
        legacy = map_to_legacy(result)
        assert 'intent_score' in legacy
        assert 'goldilocks_status' in legacy
        assert 'lead_status' in legacy
        assert isinstance(legacy['intent_score'], int)
        assert legacy['goldilocks_status'] in ('none', 'preparing', 'launched')
