"""
Maturity Segmentation — Part 2.

Classifies organizations into one of 6 maturity segments based on
signal patterns. Each segment has primary signals, secondary signals,
disqualifiers, and confidence logic.
"""
import math
from typing import List, Dict, Any, Optional

from scoring.models import EnrichedSignal, MaturitySegment, SignalCategory


def classify_maturity(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> MaturitySegment:
    """Classify an organization into a maturity segment.

    Segments are checked in priority order. The first match wins.

    Returns:
        MaturitySegment enum value.
    """
    # Filter to active (non-filtered) signals
    active = [s for s in signals if not s.is_filtered]

    if not active:
        return MaturitySegment.PRE_I18N

    # Check segments in order of specificity
    if _check_enterprise_scale(active, scan_results):
        return MaturitySegment.ENTERPRISE_SCALE

    if _check_mature_midmarket(active, scan_results):
        return MaturitySegment.MATURE_MIDMARKET

    if _check_recently_launched(active, scan_results):
        return MaturitySegment.RECENTLY_LAUNCHED

    if _check_active_implementation(active, scan_results):
        return MaturitySegment.ACTIVE_IMPLEMENTATION

    if _check_preparing(active, scan_results):
        return MaturitySegment.PREPARING

    # If we have any signals at all but no segment match, default to PREPARING
    # if there are library/fork signals, otherwise PRE_I18N
    has_library_signals = any(
        s.signal_type in ('dependency_injection', 'smoking_gun_fork')
        for s in active
    )
    if has_library_signals:
        return MaturitySegment.PREPARING

    return MaturitySegment.PRE_I18N


def calculate_confidence(
    signals: List[EnrichedSignal],
    segment: MaturitySegment,
) -> float:
    """Calculate confidence score for the maturity classification.

    confidence = signal_coverage × entropy_factor

    Returns:
        Float from 0.0 to 1.0.
    """
    active = [s for s in signals if not s.is_filtered]
    if not active:
        return 0.0

    # Signal coverage: fraction of expected signal types present
    expected_types = _expected_signal_types(segment)
    if not expected_types:
        return 0.5  # No expectations = moderate confidence

    present_types = set(s.signal_type for s in active)
    coverage = len(present_types & expected_types) / len(expected_types)

    # Entropy factor: lower entropy (more concentrated signals) = higher confidence
    type_counts = {}
    for s in active:
        type_counts[s.signal_type] = type_counts.get(s.signal_type, 0) + 1

    total = sum(type_counts.values())
    if total <= 1:
        entropy_factor = 1.0
    else:
        # Normalized entropy (0 = all same type, 1 = uniform distribution)
        n_types = len(type_counts)
        entropy = -sum(
            (c / total) * math.log2(c / total) for c in type_counts.values() if c > 0
        )
        max_entropy = math.log2(n_types) if n_types > 1 else 1.0
        normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0.0
        # We want moderate diversity (not too uniform, not too concentrated)
        # Peak confidence at ~0.5 entropy
        entropy_factor = 1.0 - abs(normalized_entropy - 0.5)

    confidence = min(1.0, coverage * entropy_factor * 1.5)  # Scale up slightly
    return round(confidence, 4)


# ============================================================
# SEGMENT CHECK FUNCTIONS
# ============================================================

def _check_enterprise_scale(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> bool:
    """Enterprise Scale: Large org with complex i18n needs.

    Primary: >20k stars OR >400 repos
    Secondary: multiple signal types OR signals from multiple repos
    """
    total_stars = scan_results.get('total_stars', 0)
    public_repos = scan_results.get('org_public_repos', 0)

    # Must be a large org
    if total_stars < 20000 and public_repos < 400:
        return False

    # Must have at least 2 distinct signal types
    signal_types = set(s.signal_type for s in signals)
    if len(signal_types) < 2:
        return False

    return True


def _check_mature_midmarket(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> bool:
    """Mature/Midmarket: Already has locale folders AND TMS config.

    Primary: already_launched signal + TMS config file
    Secondary: multiple locale folders, CI pipeline
    """
    has_launched = any(s.signal_type == 'already_launched' for s in signals)
    has_tms = any(s.signal_type == 'tms_config_file' for s in signals)
    has_ci_pipeline = any(s.signal_type in ('ci_cd_i18n_workflow', 'ci_localization_pipeline')
                         for s in signals)

    return has_launched and (has_tms or has_ci_pipeline)


def _check_recently_launched(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> bool:
    """Recently Launched: Has locale folders but they are new.

    Primary: already_launched signal
    Disqualifier: TMS config (→ mature instead)
    """
    has_launched = any(s.signal_type == 'already_launched' for s in signals)
    has_tms = any(s.signal_type == 'tms_config_file' for s in signals)

    return has_launched and not has_tms


def _check_active_implementation(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> bool:
    """Active Implementation: Library + active branch work.

    Primary: dependency_injection + ghost_branch (both present)
    Secondary: RFC discussions
    Disqualifier: locale folders exist
    """
    has_deps = any(s.signal_type == 'dependency_injection' for s in signals)
    has_branch = any(s.signal_type == 'ghost_branch' for s in signals)
    has_launched = any(s.signal_type == 'already_launched' for s in signals)

    return has_deps and has_branch and not has_launched


def _check_preparing(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> bool:
    """Preparing: Infrastructure ready, no translations.

    Primary: dependency_injection OR smoking_gun_fork
    Disqualifier: locale folders, already_launched
    """
    has_deps = any(
        s.signal_type in ('dependency_injection', 'smoking_gun_fork')
        for s in signals
    )
    has_launched = any(s.signal_type == 'already_launched' for s in signals)

    return has_deps and not has_launched


def _expected_signal_types(segment: MaturitySegment) -> set:
    """Return the expected signal type set for a segment (for confidence)."""
    expectations = {
        MaturitySegment.PRE_I18N: set(),
        MaturitySegment.PREPARING: {
            'dependency_injection', 'smoking_gun_fork', 'rfc_discussion',
            'ghost_branch', 'documentation_intent',
        },
        MaturitySegment.ACTIVE_IMPLEMENTATION: {
            'dependency_injection', 'ghost_branch', 'rfc_discussion',
            'tms_config_file', 'ci_cd_i18n_workflow',
        },
        MaturitySegment.RECENTLY_LAUNCHED: {
            'already_launched', 'dependency_injection', 'ghost_branch',
        },
        MaturitySegment.MATURE_MIDMARKET: {
            'already_launched', 'tms_config_file', 'ci_cd_i18n_workflow',
            'ci_localization_pipeline',
        },
        MaturitySegment.ENTERPRISE_SCALE: {
            'dependency_injection', 'smoking_gun_fork', 'tms_config_file',
            'ci_cd_i18n_workflow', 'rfc_discussion', 'ghost_branch',
        },
    }
    return expectations.get(segment, set())
