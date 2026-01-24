"""
WebScraper Utility Functions

Utility functions for the WebScraper module including tier calculation
based on scan results.
"""

from typing import Tuple, Dict, Any, Optional


def calculate_webscraper_tier(scan_results: Dict[str, Any]) -> Tuple[int, str]:
    """
    Calculate the WebScraper tier based on scan results.

    Tier System:
    - Tier 1 (Enterprise Ready): locale_count >= 5 AND (has_ci_pipeline OR enterprise_signals)
    - Tier 2 (Active Expansion): locale_count >= 2 AND recent_locale_activity
    - Tier 3 (Partial Coverage): has_any_i18n_signals
    - Tier 4 (Not Scanned): default / no signals

    Args:
        scan_results: Dictionary containing scan results with keys like:
            - locale_count: Number of locales detected
            - languages_detected: List of language codes
            - hreflang_tags: List of hreflang tags found
            - i18n_libraries: List of i18n libraries detected
            - has_ci_pipeline: Boolean indicating CI/CD presence
            - enterprise_signals: List of enterprise-level signals
            - recent_locale_activity: Boolean for recent i18n work

    Returns:
        Tuple of (tier_number, tier_label)
    """
    if not scan_results:
        return (4, 'Not Scanned')

    locale_count = scan_results.get('locale_count', 0) or 0
    languages = scan_results.get('languages_detected', []) or []
    hreflang_tags = scan_results.get('hreflang_tags', []) or []
    i18n_libraries = scan_results.get('i18n_libraries', []) or []
    has_ci_pipeline = scan_results.get('has_ci_pipeline', False)
    enterprise_signals = scan_results.get('enterprise_signals', []) or []
    recent_activity = scan_results.get('recent_locale_activity', False)

    # Count detected languages from various sources
    detected_language_count = max(
        locale_count,
        len(languages) if isinstance(languages, list) else 0,
        len(hreflang_tags) if isinstance(hreflang_tags, list) else 0
    )

    # Check for i18n signals
    has_i18n_signals = (
        detected_language_count > 0 or
        len(i18n_libraries) > 0 or
        len(hreflang_tags) > 0
    )

    # Check for enterprise indicators
    has_enterprise_indicators = (
        has_ci_pipeline or
        len(enterprise_signals) > 0 or
        detected_language_count >= 10
    )

    # Tier 1: Enterprise Ready
    # Large companies with mature localization (5+ locales AND enterprise signals)
    if detected_language_count >= 5 and has_enterprise_indicators:
        return (1, 'Enterprise Ready')

    # Tier 2: Active Expansion
    # Growing i18n infrastructure (2+ locales AND recent activity)
    if detected_language_count >= 2 and (recent_activity or len(i18n_libraries) > 0):
        return (2, 'Active Expansion')

    # Tier 3: Partial Coverage
    # Has any i18n signals but not comprehensive
    if has_i18n_signals:
        return (3, 'Partial Coverage')

    # Tier 4: Not Scanned
    # Default / no signals detected
    return (4, 'Not Scanned')


def calculate_localization_score(scan_results: Dict[str, Any]) -> int:
    """
    Calculate a localization coverage score (0-100) based on scan results.

    Scoring factors:
    - Locale count (up to 40 points)
    - Hreflang implementation (up to 20 points)
    - I18n library usage (up to 20 points)
    - Enterprise features (up to 20 points)

    Args:
        scan_results: Dictionary containing scan results

    Returns:
        Integer score from 0 to 100
    """
    if not scan_results:
        return 0

    score = 0

    # Locale count score (up to 40 points)
    locale_count = scan_results.get('locale_count', 0) or 0
    if locale_count >= 10:
        score += 40
    elif locale_count >= 5:
        score += 30
    elif locale_count >= 3:
        score += 20
    elif locale_count >= 1:
        score += 10

    # Hreflang implementation (up to 20 points)
    hreflang_tags = scan_results.get('hreflang_tags', []) or []
    if len(hreflang_tags) >= 5:
        score += 20
    elif len(hreflang_tags) >= 2:
        score += 15
    elif len(hreflang_tags) >= 1:
        score += 10

    # I18n library usage (up to 20 points)
    i18n_libraries = scan_results.get('i18n_libraries', []) or []
    if len(i18n_libraries) >= 2:
        score += 20
    elif len(i18n_libraries) >= 1:
        score += 15

    # Enterprise features (up to 20 points)
    if scan_results.get('has_ci_pipeline', False):
        score += 10
    if scan_results.get('enterprise_signals', []):
        score += 10

    return min(score, 100)


def calculate_quality_gap_score(scan_results: Dict[str, Any]) -> int:
    """
    Calculate a quality gap score (0-100) indicating improvement opportunities.

    Higher score = more opportunities for improvement.

    Factors:
    - Missing hreflang tags
    - Incomplete locale coverage
    - Missing i18n libraries
    - SEO gaps

    Args:
        scan_results: Dictionary containing scan results

    Returns:
        Integer score from 0 to 100 (higher = more gaps)
    """
    if not scan_results:
        return 100  # Maximum gap if not scanned

    gap_score = 0

    locale_count = scan_results.get('locale_count', 0) or 0
    hreflang_tags = scan_results.get('hreflang_tags', []) or []
    i18n_libraries = scan_results.get('i18n_libraries', []) or []

    # Check for locale vs hreflang mismatch (suggests missing SEO)
    if locale_count > len(hreflang_tags):
        gap_score += 25

    # Limited locales (opportunity to expand)
    if locale_count < 5:
        gap_score += 25
    elif locale_count < 10:
        gap_score += 15

    # No i18n framework detected
    if not i18n_libraries:
        gap_score += 25

    # No hreflang tags
    if not hreflang_tags:
        gap_score += 25

    return min(gap_score, 100)


def extract_tier_from_scan_results(scan_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract all tier-related information from scan results.

    Args:
        scan_results: Raw scan results dictionary

    Returns:
        Dictionary with tier info:
        {
            'tier': int,
            'tier_label': str,
            'localization_score': int,
            'quality_gap_score': int,
            'locale_count': int,
            'languages_detected': list,
            'hreflang_tags': list,
            'i18n_libraries': list
        }
    """
    tier, tier_label = calculate_webscraper_tier(scan_results)
    localization_score = calculate_localization_score(scan_results)
    quality_gap_score = calculate_quality_gap_score(scan_results)

    return {
        'tier': tier,
        'tier_label': tier_label,
        'localization_score': localization_score,
        'quality_gap_score': quality_gap_score,
        'locale_count': scan_results.get('locale_count', 0) or 0,
        'languages_detected': scan_results.get('languages_detected', []) or [],
        'hreflang_tags': scan_results.get('hreflang_tags', []) or [],
        'i18n_libraries': scan_results.get('i18n_libraries', []) or []
    }
