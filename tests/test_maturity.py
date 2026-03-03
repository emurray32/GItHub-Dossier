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


# ============================================================
# Helper to build minimal EnrichedSignal without enrich_signals
# ============================================================

def _make_signal(signal_type: str, evidence: str = 'test evidence') -> EnrichedSignal:
    """Create a minimal active EnrichedSignal for unit-testing classify_maturity."""
    return EnrichedSignal(
        signal_type=signal_type,
        evidence=evidence,
        company='testcorp',
        is_filtered=False,
    )


_EMPTY_SCAN = {
    'total_stars': 100,
    'org_public_repos': 5,
}


# ============================================================
# Tests for THINKING classification via _check_thinking()
# ============================================================

class TestThinkingClassification:
    """Verify that _check_thinking() gates THINKING correctly."""

    def test_rfc_discussion_only(self):
        """Single rfc_discussion signal with no deps/launched -> THINKING."""
        signals = [_make_signal('rfc_discussion')]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result == MaturitySegment.THINKING

    def test_ghost_branch_only(self):
        """Single ghost_branch signal with no deps/launched -> THINKING."""
        signals = [_make_signal('ghost_branch')]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result == MaturitySegment.THINKING

    def test_documentation_intent_only(self):
        """Single documentation_intent signal with no deps/launched -> THINKING."""
        signals = [_make_signal('documentation_intent')]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result == MaturitySegment.THINKING

    def test_multiple_exploration_signals(self):
        """All three exploration types present, no deps/launched -> THINKING."""
        signals = [
            _make_signal('rfc_discussion'),
            _make_signal('ghost_branch'),
            _make_signal('documentation_intent'),
        ]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result == MaturitySegment.THINKING

    def test_two_exploration_signals(self):
        """Two exploration signals (rfc + doc_intent), no deps -> THINKING."""
        signals = [
            _make_signal('rfc_discussion'),
            _make_signal('documentation_intent'),
        ]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result == MaturitySegment.THINKING


# ============================================================
# Tests for THINKING disqualifiers
# ============================================================

class TestThinkingDisqualifiers:
    """Exploration signals + disqualifiers should NOT classify as THINKING."""

    def test_exploration_plus_dependency_injection(self):
        """ghost_branch + dependency_injection -> PREPARING or higher, not THINKING."""
        signals = [
            _make_signal('ghost_branch'),
            _make_signal('dependency_injection'),
        ]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result != MaturitySegment.THINKING
        # With both deps and branch (no launched), should be ACTIVE_IMPLEMENTATION
        assert result == MaturitySegment.ACTIVE_IMPLEMENTATION

    def test_exploration_plus_smoking_gun_fork(self):
        """rfc_discussion + smoking_gun_fork -> PREPARING, not THINKING."""
        signals = [
            _make_signal('rfc_discussion'),
            _make_signal('smoking_gun_fork'),
        ]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result != MaturitySegment.THINKING
        assert result == MaturitySegment.PREPARING

    def test_exploration_plus_already_launched(self):
        """ghost_branch + already_launched -> RECENTLY_LAUNCHED, not THINKING."""
        signals = [
            _make_signal('ghost_branch'),
            _make_signal('already_launched'),
        ]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result != MaturitySegment.THINKING
        assert result == MaturitySegment.RECENTLY_LAUNCHED

    def test_all_exploration_plus_deps(self):
        """All exploration signals + dependency_injection -> not THINKING."""
        signals = [
            _make_signal('rfc_discussion'),
            _make_signal('ghost_branch'),
            _make_signal('documentation_intent'),
            _make_signal('dependency_injection'),
        ]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result != MaturitySegment.THINKING


# ============================================================
# Tests for fallback behavior (no segment match -> PRE_I18N)
# ============================================================

class TestFallbackBehavior:
    """Signals that match no segment check should fall through to PRE_I18N."""

    def test_unrecognized_signal_type_falls_to_pre_i18n(self):
        """A signal type not checked by any segment -> PRE_I18N."""
        signals = [_make_signal('ci_cd_i18n_workflow')]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result == MaturitySegment.PRE_I18N

    def test_tms_config_alone_falls_to_pre_i18n(self):
        """tms_config_file alone doesn't match any segment -> PRE_I18N."""
        signals = [_make_signal('tms_config_file')]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result == MaturitySegment.PRE_I18N

    def test_competitor_tms_alone_falls_to_pre_i18n(self):
        """competitor_tms alone doesn't match any segment -> PRE_I18N."""
        signals = [_make_signal('competitor_tms')]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result == MaturitySegment.PRE_I18N

    def test_fallback_safety_net_for_library_signals(self):
        """dependency_injection that somehow passes all checks still hits
        the safety-net fallback to PREPARING (not PRE_I18N).

        In practice _check_preparing catches it, so this validates the
        safety net is consistent.
        """
        signals = [_make_signal('dependency_injection')]
        result = classify_maturity(signals, _EMPTY_SCAN)
        assert result == MaturitySegment.PREPARING


# ============================================================
# Tests for calculate_confidence with THINKING segment
# ============================================================

class TestConfidenceThinking:
    """Verify confidence calculation for the THINKING segment."""

    def test_single_exploration_signal_confidence(self):
        """One exploration signal -> positive confidence in [0, 1]."""
        signals = [_make_signal('rfc_discussion')]
        conf = calculate_confidence(signals, MaturitySegment.THINKING)
        assert 0.0 < conf <= 1.0

    def test_all_expected_types_higher_confidence(self):
        """All three expected THINKING types -> higher confidence than one."""
        one_signal = [_make_signal('rfc_discussion')]
        all_signals = [
            _make_signal('rfc_discussion'),
            _make_signal('ghost_branch'),
            _make_signal('documentation_intent'),
        ]
        conf_one = calculate_confidence(one_signal, MaturitySegment.THINKING)
        conf_all = calculate_confidence(all_signals, MaturitySegment.THINKING)
        assert conf_all > conf_one

    def test_non_expected_signal_lower_coverage(self):
        """Signals outside THINKING's expected set contribute less coverage."""
        # Only non-expected signal types for THINKING
        non_expected = [_make_signal('ci_cd_i18n_workflow')]
        conf = calculate_confidence(non_expected, MaturitySegment.THINKING)
        # Coverage is 0/3 expected types, so confidence should be very low
        assert conf == 0.0

    def test_confidence_bounds(self):
        """Confidence is always clamped to [0.0, 1.0]."""
        signals = [
            _make_signal('rfc_discussion'),
            _make_signal('ghost_branch'),
            _make_signal('documentation_intent'),
            _make_signal('rfc_discussion'),  # duplicate type
            _make_signal('ghost_branch'),    # duplicate type
        ]
        conf = calculate_confidence(signals, MaturitySegment.THINKING)
        assert 0.0 <= conf <= 1.0
