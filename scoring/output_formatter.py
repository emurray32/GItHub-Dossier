"""
Output Formatter — Part 8.

Builds structured JSON output, classifies outreach angles,
determines risk level, and selects sales motion.
"""
from typing import List, Dict, Any, Optional

from scoring.models import (
    EnrichedSignal,
    MaturitySegment,
    OutreachAngle,
    RiskLevel,
    OrgScore,
    ScoringResult,
)
from scoring.woe_tables import THRESHOLDS


def format_output(
    enriched_signals: List[EnrichedSignal],
    maturity: MaturitySegment,
    p_intent: float,
    log_odds: float,
    org_score: OrgScore,
    readiness: float,
    readiness_components: Dict[str, float],
    confidence: float,
    scan_results: Dict[str, Any],
    stage1_passed: bool,
    stage1_label: str,
) -> ScoringResult:
    """Build the full ScoringResult with all classifications."""
    # Classify outreach angle
    outreach = classify_outreach_angle(maturity, enriched_signals, scan_results)

    # Classify risk level
    risk = classify_risk_level(confidence, enriched_signals, scan_results)

    # Detect signal clusters
    clusters = _detect_signal_clusters(enriched_signals)

    # Find primary repo of concern
    primary_repo = _find_primary_repo(enriched_signals, org_score)

    # Confidence factors breakdown
    confidence_factors = _compute_confidence_factors(enriched_signals, scan_results)

    # Sales motion recommendation
    sales_motion = _recommend_sales_motion(maturity, p_intent, outreach)

    result = ScoringResult(
        org_intent_score=p_intent,
        org_maturity_level=maturity,
        readiness_index=readiness,
        p_intent=p_intent,
        log_odds=log_odds,
        org_score=org_score,
        recommended_outreach_angle=outreach,
        risk_level=risk,
        confidence_percent=round(confidence * 100, 1),
        enriched_signals=enriched_signals,
        signal_clusters=clusters,
        stage1_passed=stage1_passed,
        stage1_label=stage1_label,
        primary_repo=primary_repo,
        confidence_factors=confidence_factors,
        recommended_sales_motion=sales_motion,
        readiness_components=readiness_components,
    )

    return result


def classify_outreach_angle(
    maturity: MaturitySegment,
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> OutreachAngle:
    """Classify the recommended outreach angle based on maturity and signals."""
    active = [s for s in signals if not s.is_filtered]

    # Check for specific signal patterns
    has_tms = any(s.signal_type == 'tms_config_file' for s in active)
    has_competitor_tms = _has_competitor_tms(active)
    has_pain = any(
        s.signal_type in ('rfc_discussion', 'rfc_discussion_high', 'job_posting_intent')
        for s in active
    )
    has_deps = any(
        s.signal_type in ('dependency_injection', 'smoking_gun_fork')
        for s in active
    )
    has_launched = any(s.signal_type == 'already_launched' for s in active)

    # Enterprise strategic: large org with complex needs
    if maturity == MaturitySegment.ENTERPRISE_SCALE:
        return OutreachAngle.ENTERPRISE_STRATEGIC

    # Pain-driven: strong pain signals present
    if has_pain and not has_deps and not has_launched:
        return OutreachAngle.PAIN_DRIVEN

    # Migration candidate: has competitor TMS
    if has_competitor_tms:
        return OutreachAngle.MIGRATION_CANDIDATE

    # Scale optimizer: mature midmarket with existing setup
    if maturity == MaturitySegment.MATURE_MIDMARKET:
        return OutreachAngle.SCALE_OPTIMIZER

    # Expansion accelerator: recently launched, can expand
    if maturity == MaturitySegment.RECENTLY_LAUNCHED:
        return OutreachAngle.EXPANSION_ACCELERATOR

    # Implementation partner: actively building
    if maturity == MaturitySegment.ACTIVE_IMPLEMENTATION:
        return OutreachAngle.IMPLEMENTATION_PARTNER

    # Greenfield educator: preparing or pre-i18n
    if maturity in (MaturitySegment.PREPARING, MaturitySegment.PRE_I18N):
        if has_deps:
            return OutreachAngle.IMPLEMENTATION_PARTNER
        return OutreachAngle.GREENFIELD_EDUCATOR

    return OutreachAngle.GREENFIELD_EDUCATOR


def classify_risk_level(
    confidence: float,
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> RiskLevel:
    """Classify risk level based on confidence and signal quality."""
    active = [s for s in signals if not s.is_filtered]

    # Low risk: high confidence + multiple signal types + recent activity
    signal_types = set(s.signal_type for s in active)
    recent_signals = [s for s in active if s.age_in_days is not None and s.age_in_days <= 30]

    if confidence >= 0.7 and len(signal_types) >= 3 and len(recent_signals) >= 2:
        return RiskLevel.LOW

    if confidence >= 0.4 and len(signal_types) >= 2:
        return RiskLevel.MEDIUM

    return RiskLevel.HIGH


# ============================================================
# HELPERS
# ============================================================

def _has_competitor_tms(signals: List[EnrichedSignal]) -> bool:
    """Check if a non-Phrase TMS is detected."""
    competitor_indicators = [
        'crowdin', 'lokalise', 'transifex', 'weblate', 'pontoon',
        'smartling', 'memsource',
    ]
    for signal in signals:
        if signal.signal_type == 'tms_config_file':
            evidence_lower = signal.evidence.lower()
            if any(comp in evidence_lower for comp in competitor_indicators):
                return True
    return False


def _detect_signal_clusters(signals: List[EnrichedSignal]) -> List[str]:
    """Detect named signal clusters for the output."""
    active = [s for s in signals if not s.is_filtered]
    present_types = set(s.signal_type for s in active)

    clusters = []

    # Infrastructure cluster
    infra_types = {'dependency_injection', 'smoking_gun_fork', 'tms_config_file', 'ci_cd_i18n_workflow'}
    if len(present_types & infra_types) >= 2:
        clusters.append('infrastructure_cluster')

    # Active development cluster
    dev_types = {'ghost_branch', 'rfc_discussion', 'documentation_intent'}
    if len(present_types & dev_types) >= 2:
        clusters.append('active_development_cluster')

    # Expansion cluster
    expansion_types = {'regional_domain', 'payment_multi_currency', 'social_multi_region', 'job_posting_intent'}
    if len(present_types & expansion_types) >= 2:
        clusters.append('expansion_cluster')

    # CI/CD pipeline cluster
    ci_types = {'ci_cd_i18n_workflow', 'ci_localization_pipeline', 'tms_config_file'}
    if len(present_types & ci_types) >= 2:
        clusters.append('ci_pipeline_cluster')

    return clusters


def _find_primary_repo(
    signals: List[EnrichedSignal],
    org_score: Optional[OrgScore],
) -> str:
    """Find the primary repo of concern (highest signal concentration)."""
    if org_score and org_score.repo_scores:
        top = org_score.repo_scores[0]
        if top.weighted_score > 0:
            return top.repo_name

    # Fallback: repo with most signals
    from collections import Counter
    active = [s for s in signals if not s.is_filtered and s.repo]
    if not active:
        return ""
    repo_counts = Counter(s.repo for s in active)
    return repo_counts.most_common(1)[0][0] if repo_counts else ""


def _compute_confidence_factors(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> Dict[str, float]:
    """Break down confidence into individual factors."""
    active = [s for s in signals if not s.is_filtered]

    # Signal diversity: more types = higher confidence
    signal_types = set(s.signal_type for s in active)
    diversity = min(1.0, len(signal_types) / 5.0)

    # Signal recency: more recent = higher confidence
    recent = [s for s in active if s.age_in_days is not None and s.age_in_days <= 30]
    recency = len(recent) / max(len(active), 1)

    # Signal volume: more signals = higher confidence
    volume = min(1.0, len(active) / 10.0)

    # Repo spread: signals across repos = higher confidence
    repos = set(s.repo for s in active if s.repo)
    spread = min(1.0, len(repos) / 3.0)

    return {
        'signal_diversity': round(diversity, 4),
        'signal_recency': round(recency, 4),
        'signal_volume': round(volume, 4),
        'repo_spread': round(spread, 4),
    }


def _recommend_sales_motion(
    maturity: MaturitySegment,
    p_intent: float,
    outreach: OutreachAngle,
) -> str:
    """Recommend a sales motion based on maturity and intent."""
    if p_intent >= THRESHOLDS['hot_lead']:
        if maturity == MaturitySegment.ENTERPRISE_SCALE:
            return "Enterprise AE intro + custom demo"
        return "Immediate BDR outreach + personalized demo"

    if p_intent >= THRESHOLDS['warm_lead']:
        return "Nurture sequence + educational content"

    if p_intent >= THRESHOLDS['monitor']:
        return "Add to watch list + quarterly check-in"

    return "Low priority — monitor for signal changes"
