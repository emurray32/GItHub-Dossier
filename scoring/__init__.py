"""
Scoring V2 — Multi-stage Bayesian Scoring Engine.

Public API: score_scan_results(scan_results) -> ScoringResult
"""
from scoring.models import ScoringResult, MaturitySegment, OutreachAngle, RiskLevel


def score_scan_results(scan_results: dict) -> ScoringResult:
    """Score scan results using the full v2 pipeline.

    This is the single public entry point. Runs:
      1. Signal enrichment
      2. Stage 1: Fast filter (rule-based reject)
      3. Structural/domain/contextual filters + decay
      4. Maturity segmentation
      5. Stage 2: Bayesian scorer
      6. Org-level scoring
      7. Stage 3: Enterprise adjustments
      8. Readiness index
      9. Output formatting

    Args:
        scan_results: The full scan_results dict from the scanner.

    Returns:
        A ScoringResult with all scores, classifications, and metadata.
    """
    from scoring.signal_enrichment import enrich_signals
    from scoring.filters import (
        apply_structural_filters,
        apply_domain_filters,
        apply_contextual_filters,
        apply_decay,
        apply_contributor_heuristics,
    )
    from scoring.maturity import classify_maturity, calculate_confidence
    from scoring.org_scorer import score_organization, build_repo_scores
    from scoring.bayesian_pipeline import (
        stage1_fast_filter,
        stage2_bayesian_scorer,
        stage3_enterprise_adjuster,
    )
    from scoring.readiness import calculate_readiness_index
    from scoring.output_formatter import (
        format_output,
        classify_outreach_angle,
        classify_risk_level,
    )

    # --- Step 1: Enrich raw signals ---
    raw_signals = scan_results.get('signals', [])
    enriched = enrich_signals(raw_signals, scan_results)

    # --- Step 2: Stage 1 fast filter ---
    passed, label = stage1_fast_filter(enriched, scan_results)

    result = ScoringResult()
    result.stage1_passed = passed
    result.stage1_label = label

    if not passed:
        # Rejected at Stage 1 — minimal scoring
        result.org_maturity_level = MaturitySegment.PRE_I18N
        result.enriched_signals = enriched
        return result

    # --- Step 3: Apply filters + decay ---
    enriched = apply_structural_filters(enriched, scan_results)
    enriched = apply_domain_filters(enriched, scan_results)
    enriched = apply_contextual_filters(enriched)
    enriched = apply_decay(enriched)
    enriched = apply_contributor_heuristics(enriched, scan_results)

    # --- Step 4: Maturity segmentation ---
    maturity = classify_maturity(enriched, scan_results)
    confidence = calculate_confidence(enriched, maturity)

    # --- Step 5: Stage 2 Bayesian scorer ---
    p_intent, log_odds = stage2_bayesian_scorer(enriched, maturity)

    # --- Step 6: Org-level scoring ---
    repo_scores = build_repo_scores(enriched, scan_results)
    org_score = score_organization(repo_scores, enriched, scan_results)

    # --- Step 7: Stage 3 enterprise adjustments ---
    final_p = stage3_enterprise_adjuster(p_intent, org_score, scan_results)

    # --- Step 8: Readiness index ---
    readiness, readiness_components = calculate_readiness_index(enriched, scan_results)

    # --- Step 9: Format output ---
    result = format_output(
        enriched_signals=enriched,
        maturity=maturity,
        p_intent=final_p,
        log_odds=log_odds,
        org_score=org_score,
        readiness=readiness,
        readiness_components=readiness_components,
        confidence=confidence,
        scan_results=scan_results,
        stage1_passed=passed,
        stage1_label=label,
    )

    return result
