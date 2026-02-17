"""
Backward Compatibility Layer.

Maps new ScoringResult fields to legacy scan_results fields so the Flask app,
SSE streaming, templates, AI summary, and PDF generator continue working.
"""
from typing import Dict, Any

from scoring.models import ScoringResult, MaturitySegment


# Maturity → legacy goldilocks_status mapping
_MATURITY_TO_GOLDILOCKS = {
    MaturitySegment.PRE_I18N: 'none',
    MaturitySegment.PREPARING: 'preparing',
    MaturitySegment.ACTIVE_IMPLEMENTATION: 'preparing',
    MaturitySegment.RECENTLY_LAUNCHED: 'launched',
    MaturitySegment.MATURE_MIDMARKET: 'launched',
    MaturitySegment.ENTERPRISE_SCALE: 'preparing',  # hot enterprise = preparing
}

# Maturity → legacy lead_status mapping
_MATURITY_TO_LEAD_STATUS = {
    MaturitySegment.PRE_I18N: 'COLD - No Signals Detected',
    MaturitySegment.PREPARING: 'HOT LEAD - Infrastructure Ready, No Translations',
    MaturitySegment.ACTIVE_IMPLEMENTATION: 'HOT LEAD - Infrastructure Ready, No Translations',
    MaturitySegment.RECENTLY_LAUNCHED: 'LOW PRIORITY - Already Localized',
    MaturitySegment.MATURE_MIDMARKET: 'LOW PRIORITY - Already Localized',
    MaturitySegment.ENTERPRISE_SCALE: 'HOT LEAD - Infrastructure Ready, No Translations',
}

# Maturity → database tier mapping
_MATURITY_TO_TIER = {
    MaturitySegment.PRE_I18N: 0,
    MaturitySegment.PREPARING: 2,
    MaturitySegment.ACTIVE_IMPLEMENTATION: 2,
    MaturitySegment.RECENTLY_LAUNCHED: 3,
    MaturitySegment.MATURE_MIDMARKET: 3,
    MaturitySegment.ENTERPRISE_SCALE: 2,
}


def _score_to_intent(result: ScoringResult) -> int:
    """Convert org_intent_score (0.0-1.0) to legacy intent_score (0-100).

    Maps maturity segments into legacy score ranges to preserve
    the existing Goldilocks Zone semantics.
    """
    maturity = result.org_maturity_level
    p = result.org_intent_score

    if maturity == MaturitySegment.PRE_I18N:
        return 0

    if maturity in (MaturitySegment.PREPARING, MaturitySegment.ACTIVE_IMPLEMENTATION):
        # Map to 90-100 range (Goldilocks Zone)
        return int(90 + p * 10)

    if maturity == MaturitySegment.ENTERPRISE_SCALE:
        # Enterprise with high intent → Goldilocks, otherwise lower
        if p >= 0.60:
            return int(90 + p * 10)
        return int(40 + p * 50)

    if maturity in (MaturitySegment.RECENTLY_LAUNCHED, MaturitySegment.MATURE_MIDMARKET):
        # Launched = 10
        return 10

    # Fallback based on raw probability
    return int(p * 100)


def _classify_company_size(scan_results: Dict[str, Any]) -> str:
    """Replicate legacy company size classification."""
    total_stars = scan_results.get('total_stars', 0)
    public_repos = scan_results.get('org_public_repos', 0)

    if total_stars > 20000 or public_repos > 400:
        return 'enterprise'
    if total_stars > 5000 or public_repos > 100:
        return 'large'
    if total_stars > 500 or public_repos > 20:
        return 'medium'
    return 'small'


def _get_size_weight(company_size: str) -> float:
    """Replicate legacy size weighting."""
    weights = {
        'small': 1.0,
        'medium': 0.95,
        'large': 0.9,
        'enterprise': 0.85,
    }
    return weights.get(company_size, 1.0)


def map_to_legacy(result: ScoringResult) -> Dict[str, Any]:
    """Map a ScoringResult to the legacy fields expected by the Flask app.

    Returns a dict with all the fields that _calculate_intent_score()
    used to set on scan_results.
    """
    maturity = result.org_maturity_level

    goldilocks = _MATURITY_TO_GOLDILOCKS.get(maturity, 'none')

    # For enterprise: if intent is low, map to 'launched' instead of 'preparing'
    if maturity == MaturitySegment.ENTERPRISE_SCALE and result.org_intent_score < 0.60:
        goldilocks = 'launched'

    lead_status = _MATURITY_TO_LEAD_STATUS.get(maturity, 'COLD - No Signals Detected')
    if maturity == MaturitySegment.ENTERPRISE_SCALE and result.org_intent_score < 0.60:
        lead_status = 'LOW PRIORITY - Already Localized'

    intent_score = _score_to_intent(result)

    return {
        'intent_score': intent_score,
        'goldilocks_status': goldilocks,
        'lead_status': lead_status,
    }
