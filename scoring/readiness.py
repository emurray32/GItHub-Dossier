"""
Readiness Index — Part 6.

Continuous readiness score (0.0-1.0) based on:
  Preparation × 0.40 + Velocity × 0.30 + LaunchGap × 0.20 + PainIntensity × 0.10
"""
from typing import List, Dict, Any, Tuple

from scoring.models import EnrichedSignal
from scoring.woe_tables import READINESS_WEIGHTS


def calculate_readiness_index(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> Tuple[float, Dict[str, float]]:
    """Calculate the Readiness Index.

    Returns:
        (readiness_index: float, components: dict of component scores)
    """
    active = [s for s in signals if not s.is_filtered]

    prep = _calculate_preparation(active, scan_results)
    velocity = _calculate_velocity(active, scan_results)
    launch_gap = _calculate_launch_gap(active, scan_results)
    pain = _calculate_pain_intensity(active, scan_results)

    w = READINESS_WEIGHTS
    readiness = (
        prep * w['preparation']
        + velocity * w['velocity']
        + launch_gap * w['launch_gap']
        + pain * w['pain_intensity']
    )

    components = {
        'preparation': prep,
        'velocity': velocity,
        'launch_gap': launch_gap,
        'pain_intensity': pain,
    }

    return min(1.0, max(0.0, readiness)), components


def _calculate_preparation(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> float:
    """Preparation score: how much infrastructure is in place.

    0.0 = no i18n infrastructure
    1.0 = full infrastructure (libs, configs, branches, TMS)
    """
    infra_types = {
        'dependency_injection', 'smoking_gun_fork', 'tms_config_file',
        'ci_cd_i18n_workflow', 'monorepo_i18n_package', 'ci_localization_pipeline',
    }

    present = set()
    for s in signals:
        if s.signal_type in infra_types:
            present.add(s.signal_type)

    # Score: fraction of infrastructure signal types present
    score = len(present) / max(len(infra_types), 1)

    # Boost if multiple repos have infrastructure
    repos_with_infra = set()
    for s in signals:
        if s.signal_type in infra_types and s.repo:
            repos_with_infra.add(s.repo)

    if len(repos_with_infra) >= 2:
        score = min(1.0, score * 1.3)

    return min(1.0, score)


def _calculate_velocity(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> float:
    """Velocity score: how actively they are working on i18n.

    Based on signal recency and concentration.
    """
    if not signals:
        return 0.0

    # Count signals by recency buckets
    recent_7d = 0
    recent_30d = 0
    recent_90d = 0
    total = 0

    for s in signals:
        total += 1
        if s.age_in_days is not None:
            if s.age_in_days <= 7:
                recent_7d += 1
            if s.age_in_days <= 30:
                recent_30d += 1
            if s.age_in_days <= 90:
                recent_90d += 1

    if total == 0:
        return 0.0

    # Weighted velocity: more recent = higher weight
    velocity = (
        (recent_7d / total) * 0.50
        + (recent_30d / total) * 0.30
        + (recent_90d / total) * 0.20
    )

    # Boost for active ghost branches
    active_branches = [
        s for s in signals
        if s.signal_type in ('ghost_branch', 'ghost_branch_active')
        and s.age_in_days is not None and s.age_in_days <= 14
    ]
    if active_branches:
        velocity = min(1.0, velocity + 0.2)

    return min(1.0, velocity)


def _calculate_launch_gap(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> float:
    """Launch Gap score: how far from launching.

    High score = big gap (infrastructure ready, no translations = ideal)
    Low score = already launched or no infrastructure
    """
    has_infrastructure = any(
        s.signal_type in ('dependency_injection', 'smoking_gun_fork', 'tms_config_file')
        for s in signals
    )
    has_translations = any(s.signal_type == 'already_launched' for s in signals)

    if has_infrastructure and not has_translations:
        # Goldilocks zone — maximum gap
        return 1.0
    elif has_infrastructure and has_translations:
        # Some gap but also some completion
        return 0.3
    elif not has_infrastructure and not has_translations:
        # No infrastructure at all — no gap to measure
        return 0.0
    else:
        # Has translations but no detected infra — launched
        return 0.1


def _calculate_pain_intensity(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> float:
    """Pain Intensity score: how much pain they might be experiencing.

    Based on RFC/discussion signals, job postings, and frustration indicators.
    """
    pain_score = 0.0

    # RFC discussions about i18n = they're feeling the need
    rfc_signals = [s for s in signals if s.signal_type in ('rfc_discussion', 'rfc_discussion_high')]
    if rfc_signals:
        pain_score += min(0.4, len(rfc_signals) * 0.15)

    # Job postings for i18n roles = org-level pain
    job_signals = [s for s in signals if s.signal_type == 'job_posting_intent']
    if job_signals:
        pain_score += 0.3

    # Frustration signals from scan data
    frustration = scan_results.get('frustration_signals', [])
    if frustration:
        pain_score += min(0.3, len(frustration) * 0.1)

    return min(1.0, pain_score)
