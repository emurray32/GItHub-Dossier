"""Tests for signal enrichment module."""
import pytest
from scoring.signal_enrichment import enrich_signals, _resolve_signal_type, _compute_age_days
from scoring.models import SignalCategory


class TestEnrichSignals:
    def test_empty_signals(self, empty_scan_results):
        result = enrich_signals([], empty_scan_results)
        assert result == []

    def test_basic_enrichment(self, preparing_scan_results):
        signals = preparing_scan_results['signals']
        enriched = enrich_signals(signals, preparing_scan_results)
        assert len(enriched) >= len(signals)

        # Check first signal is enriched
        dep_signal = next(s for s in enriched if s.signal_type == 'dependency_injection')
        assert dep_signal.raw_strength > 0
        assert dep_signal.woe_value > 0
        assert dep_signal.company == 'prepcorp'

    def test_preparing_dep_gets_higher_woe(self, preparing_scan_results):
        signals = preparing_scan_results['signals']
        enriched = enrich_signals(signals, preparing_scan_results)

        dep_signal = next(s for s in enriched if 'dependency_injection' in s.signal_type)
        # Preparing dep should get higher WoE than base
        assert dep_signal.woe_value >= 1.8

    def test_signal_category_assigned(self, preparing_scan_results):
        signals = preparing_scan_results['signals']
        enriched = enrich_signals(signals, preparing_scan_results)

        for signal in enriched:
            assert isinstance(signal.signal_category, SignalCategory)

    def test_age_computed(self, preparing_scan_results):
        signals = preparing_scan_results['signals']
        enriched = enrich_signals(signals, preparing_scan_results)

        dep_signal = next(s for s in enriched if 'dependency_injection' in s.signal_type)
        assert dep_signal.age_in_days is not None
        assert dep_signal.age_in_days >= 0

    def test_from_legacy_roundtrip(self):
        from scoring.models import EnrichedSignal
        legacy = {
            'Company': 'test',
            'Signal': 'Test Signal',
            'Evidence': 'test evidence',
            'Link': 'http://example.com',
            'priority': 'HIGH',
            'type': 'dependency_injection',
            'repo': 'test-repo',
        }
        enriched = EnrichedSignal.from_legacy_dict(legacy)
        back = enriched.to_legacy_dict()
        assert back['Company'] == 'test'
        assert back['type'] == 'dependency_injection'
        assert back['priority'] == 'HIGH'


class TestResolveSignalType:
    def test_preparing_dependency(self):
        signal = {'type': 'dependency_injection', 'goldilocks_status': 'preparing'}
        assert _resolve_signal_type(signal) == 'dependency_injection_preparing'

    def test_high_priority_rfc(self):
        signal = {'type': 'rfc_discussion', 'priority': 'HIGH'}
        assert _resolve_signal_type(signal) == 'rfc_discussion_high'

    def test_high_priority_doc(self):
        signal = {'type': 'documentation_intent', 'priority': 'HIGH'}
        assert _resolve_signal_type(signal) == 'documentation_intent_high'

    def test_plain_type(self):
        signal = {'type': 'rfc_discussion', 'priority': 'MEDIUM'}
        assert _resolve_signal_type(signal) == 'rfc_discussion'


class TestComputeAge:
    def test_valid_timestamp(self):
        age = _compute_age_days({'created_at': '2024-01-01T00:00:00Z'})
        assert age is not None
        assert age > 300

    def test_no_timestamp(self):
        age = _compute_age_days({})
        assert age is None

    def test_invalid_timestamp(self):
        age = _compute_age_days({'created_at': 'not-a-date'})
        assert age is None
