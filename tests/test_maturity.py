"""Tests for maturity segmentation module."""
import pytest
from scoring.maturity import classify_maturity, calculate_confidence
from scoring.models import MaturitySegment, EnrichedSignal, SignalCategory


class TestClassifyMaturity:
    def test_no_signals(self, empty_scan_results):
        result = classify_maturity([], empty_scan_results)
        assert result == MaturitySegment.PRE_I18N

    def test_preparing(self, preparing_scan_results):
        from scoring.signal_enrichment import enrich_signals
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        result = classify_maturity(enriched, preparing_scan_results)
        # Should be PREPARING or ACTIVE_IMPLEMENTATION (has both deps and branch)
        assert result in (
            MaturitySegment.PREPARING,
            MaturitySegment.ACTIVE_IMPLEMENTATION,
        )

    def test_enterprise_scale(self, enterprise_scan_results):
        from scoring.signal_enrichment import enrich_signals
        enriched = enrich_signals(
            enterprise_scan_results['signals'], enterprise_scan_results
        )
        result = classify_maturity(enriched, enterprise_scan_results)
        assert result == MaturitySegment.ENTERPRISE_SCALE

    def test_launched(self, launched_scan_results):
        from scoring.signal_enrichment import enrich_signals
        enriched = enrich_signals(
            launched_scan_results['signals'], launched_scan_results
        )
        result = classify_maturity(enriched, launched_scan_results)
        assert result == MaturitySegment.RECENTLY_LAUNCHED

    def test_all_filtered_signals(self, preparing_scan_results):
        """All signals filtered → PRE_I18N."""
        from scoring.signal_enrichment import enrich_signals
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        for s in enriched:
            s.is_filtered = True
        result = classify_maturity(enriched, preparing_scan_results)
        assert result == MaturitySegment.PRE_I18N


class TestCalculateConfidence:
    def test_no_signals(self):
        conf = calculate_confidence([], MaturitySegment.PRE_I18N)
        assert conf == 0.0

    def test_some_coverage(self, preparing_scan_results):
        from scoring.signal_enrichment import enrich_signals
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        segment = MaturitySegment.PREPARING
        conf = calculate_confidence(enriched, segment)
        assert 0.0 <= conf <= 1.0
        assert conf > 0.0  # Should have some confidence

    def test_higher_coverage_higher_confidence(self, enterprise_scan_results):
        from scoring.signal_enrichment import enrich_signals
        enriched = enrich_signals(
            enterprise_scan_results['signals'], enterprise_scan_results
        )
        segment = MaturitySegment.ENTERPRISE_SCALE
        conf = calculate_confidence(enriched, segment)
        assert conf > 0.2  # Multiple signal types → decent confidence
