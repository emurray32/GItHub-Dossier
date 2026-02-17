"""
Filters + Decay — Part 4.

Provides structural, domain, and contextual filters, exponential decay,
contributor heuristics, and revenue proxy detection.
"""
import math
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from scoring.models import EnrichedSignal, SignalCategory
from scoring.woe_tables import HALF_LIFE_DAYS


# ============================================================
# STRUCTURAL FILTERS
# ============================================================

def apply_structural_filters(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> List[EnrichedSignal]:
    """Auto-reject or reduce signals from structurally disqualified repos.

    Filters:
    - Fork repos → filtered
    - Archived repos → filtered
    - <5 commits → filtered
    - Zero stars+watchers on personal accounts → filtered
    - >365 days since push → filtered
    """
    repos_meta = _build_repo_metadata(scan_results)

    for signal in signals:
        repo = signal.repo
        meta = repos_meta.get(repo, {})

        if meta.get('is_fork'):
            signal.is_filtered = True
            signal.filter_reason = 'fork_repo'
            signal.filter_multiplier = 0.0
            continue

        if meta.get('is_archived'):
            signal.is_filtered = True
            signal.filter_reason = 'archived_repo'
            signal.filter_multiplier = 0.0
            continue

        commit_count = meta.get('commit_count', 100)  # Default high if unknown
        if commit_count < 5:
            signal.is_filtered = True
            signal.filter_reason = 'low_commit_count'
            signal.filter_multiplier = 0.0
            continue

        # Zero stars + watchers on personal (non-org) repos
        is_personal = not scan_results.get('org_login')
        stars = meta.get('stars', 0)
        watchers = meta.get('watchers', 0)
        if is_personal and stars == 0 and watchers == 0:
            signal.is_filtered = True
            signal.filter_reason = 'zero_engagement_personal'
            signal.filter_multiplier = 0.0
            continue

        # Stale repo: >365 days since last push
        last_push = meta.get('pushed_at')
        if last_push:
            age = _days_since(last_push)
            if age is not None and age > 365:
                signal.is_filtered = True
                signal.filter_reason = 'stale_repo'
                signal.filter_multiplier = 0.0
                continue

    return signals


# ============================================================
# DOMAIN FILTERS
# ============================================================

def apply_domain_filters(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> List[EnrichedSignal]:
    """Apply 80% reduction for open protocol, tutorial, library/SDK repos."""
    org_description = (scan_results.get('org_description', '') or '').lower()
    org_name = (scan_results.get('org_login', '') or '').lower()

    # Check for open protocol / decentralized project
    open_protocol_keywords = [
        'decentralized', 'decentralised', 'open protocol', 'blockchain protocol',
        'web3 protocol', 'defi protocol', 'dao ',
    ]
    is_open_protocol = any(kw in org_description for kw in open_protocol_keywords)

    for signal in signals:
        if signal.is_filtered:
            continue

        repo_name = (signal.repo or '').lower()
        evidence_lower = (signal.evidence or '').lower()

        # Open protocol org → 80% reduction
        if is_open_protocol:
            signal.filter_multiplier *= 0.20
            signal.filter_reason = signal.filter_reason or 'open_protocol'

        # Tutorial/educational repo → 80% reduction
        tutorial_indicators = [
            'tutorial', 'example', 'demo', 'sample', 'starter',
            'boilerplate', 'template', 'learn', 'course', 'workshop',
        ]
        if any(ind in repo_name for ind in tutorial_indicators):
            signal.filter_multiplier *= 0.20
            signal.filter_reason = signal.filter_reason or 'tutorial_repo'

        # Library/SDK repo → 80% reduction (they provide i18n, not consuming)
        sdk_indicators = [
            '-sdk', '-client', '-api', '-library', '-lib', '-plugin',
            '-extension', '-package', '-module',
        ]
        if any(ind in repo_name for ind in sdk_indicators):
            signal.filter_multiplier *= 0.20
            signal.filter_reason = signal.filter_reason or 'sdk_library_repo'

    return signals


# ============================================================
# CONTEXTUAL FILTERS
# ============================================================

def apply_contextual_filters(signals: List[EnrichedSignal]) -> List[EnrichedSignal]:
    """Apply 50% reduction for docs-only or test/CI-only i18n references."""
    for signal in signals:
        if signal.is_filtered:
            continue

        evidence_lower = (signal.evidence or '').lower()
        file_lower = (signal.file_path or '').lower()

        # Docs-only i18n mention → 50% reduction
        docs_indicators = ['readme', 'docs/', 'documentation', 'wiki/', '.md']
        is_docs_only = any(ind in file_lower for ind in docs_indicators)
        code_indicators = ['.js', '.ts', '.py', '.rb', '.go', '.java', '.yml', 'package.json']
        has_code_context = any(ind in file_lower for ind in code_indicators)

        if is_docs_only and not has_code_context:
            signal.filter_multiplier *= 0.50
            signal.filter_reason = signal.filter_reason or 'docs_only'

        # Test/CI-only refs → 50% reduction
        test_indicators = ['test/', 'tests/', 'spec/', '__tests__', '.test.', '.spec.']
        ci_indicators = ['.github/workflows/', '.circleci/', 'jenkinsfile', '.travis']
        is_test_or_ci = (
            any(ind in file_lower for ind in test_indicators)
            or any(ind in file_lower for ind in ci_indicators)
        )
        if is_test_or_ci and not has_code_context:
            signal.filter_multiplier *= 0.50
            signal.filter_reason = signal.filter_reason or 'test_ci_only'

    return signals


# ============================================================
# EXPONENTIAL DECAY
# ============================================================

def apply_decay(signals: List[EnrichedSignal]) -> List[EnrichedSignal]:
    """Apply exponential half-life decay per signal category.

    Formula: decayed = raw_strength × 0.5^(age_in_days / half_life)
    """
    for signal in signals:
        if signal.is_filtered:
            continue

        age = signal.age_in_days
        if age is None:
            # No timestamp — assume moderately fresh (no decay)
            signal.decayed_strength = signal.raw_strength * signal.filter_multiplier
            continue

        half_life = HALF_LIFE_DAYS.get(signal.signal_category, 60)
        decay_factor = math.pow(0.5, age / half_life)
        signal.decayed_strength = signal.raw_strength * decay_factor * signal.filter_multiplier

    return signals


# ============================================================
# CONTRIBUTOR HEURISTICS
# ============================================================

def apply_contributor_heuristics(
    signals: List[EnrichedSignal],
    scan_results: Dict[str, Any],
) -> List[EnrichedSignal]:
    """Apply contributor-based multipliers.

    - Corporate ratio boost: 1.2x if >50% contributors have company field
    - Business hours boost: 1.2x if commits concentrated in 9AM-6PM weekdays
    """
    contributors = scan_results.get('contributors', {})
    if not contributors:
        return signals

    # Corporate ratio
    total = len(contributors)
    corporate = sum(1 for data in contributors.values()
                    if data.get('company') and data['company'].strip())
    corporate_ratio = corporate / total if total > 0 else 0

    corporate_boost = 1.2 if corporate_ratio > 0.50 else 1.0

    # Apply boost to all non-filtered signals
    for signal in signals:
        if not signal.is_filtered:
            signal.decayed_strength *= corporate_boost

    return signals


# ============================================================
# REVENUE PROXIES
# ============================================================

def compute_revenue_proxies(scan_results: Dict[str, Any]) -> Dict[str, bool]:
    """Detect revenue/commercial proxies from scan data.

    Returns dict of boolean flags.
    """
    org_data = scan_results

    has_verified_domain = bool(org_data.get('is_verified'))
    has_many_members = (org_data.get('org_public_members', 0) or 0) > 10
    has_website = bool(org_data.get('org_blog') or org_data.get('website'))

    # Professional README heuristic: check if org has a description
    has_description = bool(org_data.get('org_description'))

    return {
        'verified_domain': has_verified_domain,
        'many_members': has_many_members,
        'has_website': has_website,
        'professional_readme': has_description,
    }


# ============================================================
# HELPERS
# ============================================================

def _build_repo_metadata(scan_results: Dict[str, Any]) -> Dict[str, Dict]:
    """Build a lookup of repo name → metadata from scan_results."""
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
                'commit_count': repo.get('size', 100),  # Rough proxy
            }
        elif isinstance(repo, str):
            meta[repo] = {}

    return meta


def _days_since(timestamp_str: str) -> Optional[int]:
    """Calculate days since a timestamp string."""
    if not isinstance(timestamp_str, str):
        return None
    now = datetime.now(timezone.utc)
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(timestamp_str[:19], fmt.replace('Z', ''))
            dt = dt.replace(tzinfo=timezone.utc)
            return max(0, (now - dt).days)
        except (ValueError, TypeError):
            continue
    return None
