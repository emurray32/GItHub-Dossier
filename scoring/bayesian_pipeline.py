"""
3-Stage Bayesian Pipeline — Part 5.

Stage 1: Fast filter (rule-based reject)
Stage 2: Bayesian scorer (log-odds updating with WoE)
Stage 3: Enterprise adjuster (org-level corrections)
"""
import math
from typing import List, Dict, Any, Tuple

from scoring.models import EnrichedSignal, MaturitySegment, OrgScore
from scoring.woe_tables import (
    SEGMENT_PRIORS,
    INTERACTION_BONUSES,
    THRESHOLDS,
)


def sigmoid(x: float) -> float:
    """Logistic sigmoid function."""
    if x > 500:
        return 1.0
    if x < -500:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def prob_to_log_odds(p: float) -> float:
    """Convert probability to log-odds."""
    p = max(0.001, min(0.999, p))
    return math.log(p / (1.0 - p))


# ============================================================
# STAGE 1: FAST FILTER
# ============================================================

def stage1_fast_filter(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> Tuple[bool, str]:
    """Rule-based fast filter to reject clearly unqualified orgs.

    Rejects:
    - No signals at all
    - All signals from forks only
    - All signals from archived repos only
    - <5 commits total (when repo data available)
    - Inactive: no push in >365 days (all repos)

    Returns:
        (passed: bool, label: str)
    """
    if not signals:
        return False, "no_signals"

    # Check if ALL signals are filtered
    active = [s for s in signals if not s.is_filtered]
    if not active:
        return False, "all_filtered"

    # Check repos_scanned for basic org viability
    repos = scan_results.get('repos_scanned', [])
    if isinstance(repos, list) and len(repos) == 0:
        # No repos scanned but we have signals (from org-level data)
        return True, "org_level_signals"

    # Check if all signals come from forks
    all_forks = all(
        s.raw_signal.get('fork') or s.signal_type == 'fork_repo'
        for s in active
    )
    if all_forks:
        return False, "all_forks"

    return True, "passed"


# ============================================================
# STAGE 2: BAYESIAN SCORER
# ============================================================

def stage2_bayesian_scorer(
    signals: List[EnrichedSignal],
    segment: MaturitySegment,
) -> Tuple[float, float]:
    """Bayesian log-odds updating with WoE per signal.

    Starts with segment-specific prior, then updates with:
    - Decayed WoE per signal
    - Interaction bonuses for signal pairs
    - Disqualifier penalties

    Returns:
        (p_intent: float, log_odds: float)
    """
    # Start with segment prior
    prior_p = SEGMENT_PRIORS.get(segment, 0.10)
    log_odds = prob_to_log_odds(prior_p)

    active = [s for s in signals if not s.is_filtered]
    if not active:
        return sigmoid(log_odds), log_odds

    # Update with each signal's decayed WoE
    for signal in active:
        # Scale WoE by decay factor (decayed_strength / raw_strength)
        if signal.raw_strength > 0:
            decay_factor = signal.decayed_strength / signal.raw_strength
        else:
            decay_factor = 1.0

        woe_contribution = signal.woe_value * decay_factor
        log_odds += woe_contribution

    # Interaction bonuses: check for signal pairs
    present_types = set(s.signal_type for s in active)
    # Normalize sub-types for interaction matching
    normalized_types = set()
    for t in present_types:
        if t.startswith('dependency_injection'):
            normalized_types.add('dependency_injection')
        elif t.startswith('rfc_discussion'):
            normalized_types.add('rfc_discussion')
        elif t.startswith('ghost_branch'):
            normalized_types.add('ghost_branch')
        elif t.startswith('documentation_intent'):
            normalized_types.add('documentation_intent')
        else:
            normalized_types.add(t)

    for (type_a, type_b), bonus in INTERACTION_BONUSES.items():
        if type_a in normalized_types and type_b in normalized_types:
            log_odds += bonus

    # Disqualifier penalties
    for signal in active:
        if signal.woe_value < 0:
            # Negative WoE already applied above, no double-counting needed
            pass

    p_intent = sigmoid(log_odds)
    return p_intent, log_odds


# ============================================================
# STAGE 3: ENTERPRISE ADJUSTER
# ============================================================

def stage3_enterprise_adjuster(
    p_intent: float,
    org_score: OrgScore,
    scan_results: Dict[str, Any],
) -> float:
    """Apply org-level adjustments to the Bayesian P(intent).

    Blends repo-level P(intent) with org-level composite score.
    Applies cluster multiplier and proven-buyer multiplier.
    """
    if org_score is None:
        return p_intent

    # Blend: 70% Bayesian + 30% OrgScore composite
    blended = 0.70 * p_intent + 0.30 * org_score.composite

    # Apply cluster bonus (already factored into composite, but also adjust p)
    if org_score.cluster_bonus > 1.0:
        # Modest additional boost to p_intent
        cluster_adjust = (org_score.cluster_bonus - 1.0) * 0.1
        blended = min(1.0, blended + cluster_adjust)

    # Apply proven-buyer multiplier
    if org_score.proven_buyer_multiplier > 1.0:
        buyer_adjust = (org_score.proven_buyer_multiplier - 1.0) * 0.2
        blended = min(1.0, blended + buyer_adjust)

    return min(1.0, max(0.0, blended))
