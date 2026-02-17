"""Tests for readiness index module."""
import pytest
from scoring.readiness import calculate_readiness_index
from scoring.signal_enrichment import enrich_signals
from scoring.filters import apply_decay


class TestReadinessIndex:
    def test_empty(self, empty_scan_results):
        readiness, components = calculate_readiness_index([], empty_scan_results)
        assert readiness == 0.0
        assert 'preparation' in components
        assert 'velocity' in components
        assert 'launch_gap' in components
        assert 'pain_intensity' in components

    def test_preparing_high_readiness(self, preparing_scan_results):
        enriched = enrich_signals(
            preparing_scan_results['signals'], preparing_scan_results
        )
        enriched = apply_decay(enriched)
        readiness, components = calculate_readiness_index(
            enriched, preparing_scan_results
        )
        assert readiness > 0.0
        # Should have high launch_gap (infra ready, no translations)
        assert components['launch_gap'] == 1.0

    def test_launched_low_gap(self, launched_scan_results):
        enriched = enrich_signals(
            launched_scan_results['signals'], launched_scan_results
        )
        enriched = apply_decay(enriched)
        readiness, components = calculate_readiness_index(
            enriched, launched_scan_results
        )
        # Launched has low gap
        assert components['launch_gap'] < 0.5

    def test_components_bounded(self, enterprise_scan_results):
        enriched = enrich_signals(
            enterprise_scan_results['signals'], enterprise_scan_results
        )
        enriched = apply_decay(enriched)
        readiness, components = calculate_readiness_index(
            enriched, enterprise_scan_results
        )
        assert 0.0 <= readiness <= 1.0
        for key, val in components.items():
            assert 0.0 <= val <= 1.0, f"{key} out of bounds: {val}"
