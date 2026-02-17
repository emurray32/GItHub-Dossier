"""
Org-Level Scoring — Part 3.

Enterprise org-level scoring with per-repo tiering, OrgScore composite formula,
cluster bonus, and proven-buyer detection.
"""
from typing import List, Dict, Any, Optional
from collections import defaultdict

from scoring.models import EnrichedSignal, RepoScore, OrgScore, MaturitySegment
from scoring.woe_tables import ORG_SCORE_WEIGHTS


def build_repo_scores(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> List[RepoScore]:
    """Score individual repos by grouping signals and assigning tiers.

    Tier classification:
    - Tier 1 (weight 1.0): Product repos (main app, frontend, etc.)
    - Tier 2 (weight 0.6): Support repos (docs, configs, tools)
    - Tier 3 (weight 0.2): Internal repos (CI, scripts, etc.)
    - Tier 0 (weight 0.0): Excluded (forks, archived)
    """
    repos_meta = _build_repo_meta(scan_results)

    # Group signals by repo
    repo_signals: Dict[str, List[EnrichedSignal]] = defaultdict(list)
    for signal in signals:
        repo = signal.repo or '_org_level_'
        repo_signals[repo].append(signal)

    repo_scores = []

    for repo_name, repo_sigs in repo_signals.items():
        meta = repos_meta.get(repo_name, {})

        rs = RepoScore(
            repo_name=repo_name,
            signal_count=len(repo_sigs),
            signals=repo_sigs,
            is_fork=meta.get('is_fork', False),
            is_archived=meta.get('is_archived', False),
            stars=meta.get('stars', 0),
            last_push=meta.get('pushed_at'),
        )

        # Assign tier
        if rs.is_fork or rs.is_archived:
            rs.tier = 0
            rs.tier_weight = 0.0
        else:
            rs.tier, rs.tier_weight = _classify_repo_tier(repo_name, meta)

        # Compute weighted score: sum of decayed signal strengths × tier weight
        active_sigs = [s for s in repo_sigs if not s.is_filtered]
        raw_score = sum(s.decayed_strength for s in active_sigs)
        rs.weighted_score = raw_score * rs.tier_weight

        repo_scores.append(rs)

    # Sort by weighted_score descending
    repo_scores.sort(key=lambda r: r.weighted_score, reverse=True)
    return repo_scores


def score_organization(
    repo_scores: List[RepoScore],
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> OrgScore:
    """Compute the organization-level composite score.

    OrgScore = Peak×0.30 + MeanTop3×0.25 + Breadth×0.20
               + HighValueConcentration×0.15 + Momentum×0.10

    Then apply cluster bonus and proven-buyer multiplier.
    """
    org = OrgScore()
    org.repo_scores = repo_scores

    if not repo_scores:
        return org

    # Get active repos only (tier > 0)
    active_repos = [r for r in repo_scores if r.tier > 0 and r.weighted_score > 0]
    if not active_repos:
        return org

    # Peak: highest single repo score (normalized)
    all_scores = [r.weighted_score for r in active_repos]
    max_possible = 10.0  # Approximate ceiling for normalization
    org.peak_score = min(1.0, max(all_scores) / max_possible) if all_scores else 0.0

    # MeanTop3: average of top 3 repo scores
    top3 = sorted(all_scores, reverse=True)[:3]
    org.mean_top3 = min(1.0, (sum(top3) / len(top3)) / max_possible) if top3 else 0.0

    # Breadth: fraction of repos with any signal (capped at 1.0)
    total_repos = len(scan_results.get('repos_scanned', [])) or 1
    repos_with_signals = len(active_repos)
    org.breadth = min(1.0, repos_with_signals / min(total_repos, 10))

    # High-value concentration: fraction of total score in top repo
    total_score = sum(all_scores)
    if total_score > 0:
        org.high_value_concentration = max(all_scores) / total_score
    else:
        org.high_value_concentration = 0.0

    # Momentum: recent signal activity (signals from last 30 days / total)
    active_signals = [s for s in signals if not s.is_filtered]
    recent = [s for s in active_signals if s.age_in_days is not None and s.age_in_days <= 30]
    org.momentum = len(recent) / max(len(active_signals), 1)

    # Composite weighted sum
    w = ORG_SCORE_WEIGHTS
    org.composite = (
        org.peak_score * w['peak']
        + org.mean_top3 * w['mean_top3']
        + org.breadth * w['breadth']
        + org.high_value_concentration * w['high_value_concentration']
        + org.momentum * w['momentum']
    )

    # Cluster bonus
    org.cluster_bonus = calculate_cluster_bonus(active_repos)
    org.composite *= org.cluster_bonus

    # Proven-buyer multiplier
    org.proven_buyer_multiplier = detect_proven_buyer(signals, scan_results)
    org.composite *= org.proven_buyer_multiplier

    org.composite = min(1.0, org.composite)

    return org


def calculate_cluster_bonus(active_repos: List[RepoScore]) -> float:
    """Calculate cluster bonus when multiple repos have signals.

    Formula: 1.0 + 0.2 × min(repos_with_signal, 10) when >= 3 repos
    """
    repos_with_signal = len([r for r in active_repos if r.signal_count > 0])
    if repos_with_signal >= 3:
        return 1.0 + 0.2 * min(repos_with_signal, 10)
    return 1.0


def detect_proven_buyer(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> float:
    """Detect proven-buyer pattern: mature repo + preparing repos coexist.

    Returns 1.3x multiplier if pattern detected, 1.0 otherwise.
    """
    has_launched = any(
        s.signal_type == 'already_launched' and not s.is_filtered
        for s in signals
    )
    has_preparing = any(
        s.signal_type in ('dependency_injection', 'smoking_gun_fork')
        and not s.is_filtered
        and s.raw_signal.get('goldilocks_status') == 'preparing'
        for s in signals
    )

    # Alternative: check for preparing signals without launched repos
    if not has_preparing:
        has_preparing = any(
            s.signal_type in ('dependency_injection', 'smoking_gun_fork')
            and not s.is_filtered
            for s in signals
        )

    if has_launched and has_preparing:
        return 1.3

    return 1.0


# ============================================================
# HELPERS
# ============================================================

def _build_repo_meta(scan_results: Dict[str, Any]) -> Dict[str, Dict]:
    """Build repo name → metadata lookup."""
    meta = {}
    repos = scan_results.get('repos_scanned', [])
    for repo in repos:
        if isinstance(repo, dict):
            name = repo.get('name', '')
            meta[name] = {
                'is_fork': repo.get('fork', False),
                'is_archived': repo.get('archived', False),
                'stars': repo.get('stargazers_count', 0),
                'watchers': repo.get('watchers_count', 0),
                'pushed_at': repo.get('pushed_at'),
                'language': repo.get('language', ''),
                'description': repo.get('description', ''),
            }
    return meta


def _classify_repo_tier(repo_name: str, meta: Dict) -> tuple:
    """Classify a repo into tier 1/2/3.

    Returns (tier, weight).
    """
    name_lower = repo_name.lower()
    desc_lower = (meta.get('description', '') or '').lower()
    language = (meta.get('language', '') or '').lower()
    stars = meta.get('stars', 0)

    # Tier 3: Internal/support repos
    internal_indicators = [
        'internal', '.github', 'config', 'infra', 'devops',
        'scripts', 'ops', 'ci-', 'cd-', 'deploy',
    ]
    if any(ind in name_lower for ind in internal_indicators):
        return 3, 0.2

    # Tier 2: Support/docs repos
    support_indicators = [
        'docs', 'documentation', 'wiki', 'blog', 'website',
        'landing', 'marketing', 'design', 'assets',
    ]
    if any(ind in name_lower for ind in support_indicators):
        return 2, 0.6

    # Tier 1: Product repos (default for non-matching)
    return 1, 1.0
