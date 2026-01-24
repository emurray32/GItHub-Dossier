"""
WebScraper Utility Functions

Utility functions for the WebScraper module including tier calculation
based on scan results and global expansion signal detection.
"""

from typing import Tuple, Dict, Any, Optional, List
import re


# Tier Configuration - Updated for Global Expansion Detection
TIER_CONFIG = {
    1: {
        'name': 'Global Leader',
        'description': 'Mature global presence with 10+ locales and enterprise infrastructure',
        'color': '#10b981'
    },
    2: {
        'name': 'Active Expansion',
        'description': 'Already global, actively expanding to new markets',
        'color': '#3b82f6'
    },
    3: {
        'name': 'Going Global',
        'description': 'First-time global expansion - new to internationalization',
        'color': '#f59e0b'
    },
    4: {
        'name': 'Not Yet Global',
        'description': 'No localization signals detected - potential prospect',
        'color': '#6b7280'
    }
}


def detect_expansion_signals(website_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Detect global expansion signals from website data.

    Analyzes website content for indicators of global expansion intent including:
    - Language/locale infrastructure changes
    - International job postings
    - Press releases about expansion
    - Regional office mentions
    - International partnerships

    Args:
        website_data: Dictionary containing website analysis data

    Returns:
        Dictionary with expansion signals:
        {
            'is_first_time_global': bool,
            'is_actively_expanding': bool,
            'expansion_signals': list of signal descriptions,
            'expansion_score': int (0-100),
            'detected_intent': list of intent indicators,
            'new_markets': list of potential new markets,
            'evidence': list of evidence strings
        }
    """
    signals = {
        'is_first_time_global': False,
        'is_actively_expanding': False,
        'expansion_signals': [],
        'expansion_score': 0,
        'detected_intent': [],
        'new_markets': [],
        'evidence': []
    }

    if not website_data:
        return signals

    # Extract relevant data
    text_content = website_data.get('text_content', '').lower()
    links = website_data.get('links', [])
    hreflang_tags = website_data.get('hreflang_tags', [])
    localization_score = website_data.get('localization_score', {})
    locale_count = len(hreflang_tags) if hreflang_tags else 0

    # ========== FIRST-TIME GLOBAL INDICATORS ==========

    # Pattern 1: Very limited localization (1-2 locales) but has i18n infrastructure
    if 1 <= locale_count <= 2:
        i18n_libs = localization_score.get('details', {}).get('i18n_libraries', [])
        if i18n_libs:
            signals['is_first_time_global'] = True
            signals['expansion_signals'].append('Early-stage i18n infrastructure detected')
            signals['evidence'].append(f'Found {locale_count} locale(s) with i18n library: {", ".join(i18n_libs)}')
            signals['expansion_score'] += 25

    # Pattern 2: Language switcher exists but limited languages
    has_switcher = localization_score.get('details', {}).get('language_switcher', False)
    if has_switcher and locale_count <= 3:
        signals['is_first_time_global'] = True
        signals['expansion_signals'].append('Language switcher with limited languages')
        signals['evidence'].append('Language switcher present but only 1-3 languages available')
        signals['expansion_score'] += 20

    # Pattern 3: Detect expansion keywords in content
    expansion_keywords = [
        (r'expand(?:ing)?\s+(?:to|into)\s+(?:new\s+)?(?:international|global|overseas|foreign)\s+markets?', 'Market expansion announcement'),
        (r'launch(?:ing|ed)?\s+in\s+(?:new\s+)?(?:countries?|regions?|markets?)', 'New market launch'),
        (r'(?:entering|enter)\s+(?:the\s+)?(?:european|asian|latin\s+american|middle\s+east)', 'Regional expansion'),
        (r'(?:opening|opened?)\s+(?:new\s+)?(?:offices?|headquarters?|hq)\s+in', 'New office opening'),
        (r'international\s+(?:expansion|growth|presence)', 'International expansion mentioned'),
        (r'go(?:ing)?\s+global', 'Going global initiative'),
        (r'(?:localization|localisation)\s+(?:strategy|effort|initiative|project)', 'Localization project'),
        (r'(?:translate|translation|translating)\s+(?:our|the|into)', 'Translation initiative'),
        (r'(?:hiring|recruiting)\s+(?:for\s+)?(?:international|global|localization|translation)', 'International hiring'),
        (r'(?:new|launching|expanding)\s+(?:to\s+)?(?:\d+\s+)?(?:languages?|locales?|countries?)', 'Multi-language expansion'),
    ]

    for pattern, description in expansion_keywords:
        matches = re.findall(pattern, text_content)
        if matches:
            signals['detected_intent'].append(description)
            signals['evidence'].append(f'Found keyword pattern: "{matches[0]}"')
            signals['expansion_score'] += 15

    # ========== ACTIVE EXPANSION INDICATORS ==========

    # Pattern 4: Multiple locales with recent additions or gaps
    if locale_count >= 3:
        signals['is_actively_expanding'] = True

        # Check for regional patterns suggesting expansion
        regions = detect_target_regions(hreflang_tags)
        if regions:
            signals['new_markets'].extend(regions)
            signals['expansion_signals'].append(f'Multi-region presence: {", ".join(regions)}')
            signals['expansion_score'] += 20

    # Pattern 5: Career/Jobs page with international roles
    international_job_patterns = [
        r'(?:international|global|regional)\s+(?:sales|marketing|business\s+development)',
        r'(?:localization|translation)\s+(?:manager|specialist|coordinator)',
        r'(?:country|regional)\s+manager',
        r'(?:emea|apac|latam|amer)\s+(?:sales|marketing|manager)',
    ]

    for link in links[:50]:
        link_text = link.get('text', '').lower()
        link_href = link.get('href', '').lower()

        # Check if it's a careers/jobs link
        if any(word in link_href or word in link_text for word in ['career', 'job', 'hiring', 'join']):
            for pattern in international_job_patterns:
                if re.search(pattern, text_content):
                    signals['is_actively_expanding'] = True
                    signals['detected_intent'].append('International hiring activity')
                    signals['expansion_score'] += 15
                    break

    # Pattern 6: Partnership/Integration mentions
    partnership_patterns = [
        r'partner(?:ship|ing)?\s+with\s+(?:international|global)',
        r'(?:integration|integrated)\s+with\s+(?:local|regional)',
        r'(?:distributor|reseller|channel)\s+(?:in|for|across)',
    ]

    for pattern in partnership_patterns:
        if re.search(pattern, text_content):
            signals['is_actively_expanding'] = True
            signals['expansion_signals'].append('International partnership activity')
            signals['expansion_score'] += 10
            break

    # ========== LAUNCH INDICATORS ==========

    # Pattern 7: Product launch in new regions
    launch_patterns = [
        (r'(?:now\s+)?available\s+in\s+(?:\d+\s+)?(?:new\s+)?(?:countries?|regions?)', 'Product availability expansion'),
        (r'(?:proud|excited|thrilled)\s+to\s+announce.*(?:launch|expansion|available)', 'Launch announcement'),
        (r'beta\s+(?:launch|test|program)\s+in\s+(?:new\s+)?(?:markets?|regions?)', 'Beta expansion'),
        (r'coming\s+soon\s+to\s+(?:\w+\s+)?(?:europe|asia|america|africa|australia)', 'Upcoming regional launch'),
    ]

    for pattern, description in launch_patterns:
        if re.search(pattern, text_content):
            signals['expansion_signals'].append(description)
            signals['detected_intent'].append(f'Launch signal: {description}')
            signals['expansion_score'] += 20

    # Cap the expansion score at 100
    signals['expansion_score'] = min(signals['expansion_score'], 100)

    # Determine final classification
    if signals['expansion_score'] >= 40 and locale_count <= 2:
        signals['is_first_time_global'] = True
    elif signals['expansion_score'] >= 30 and locale_count >= 3:
        signals['is_actively_expanding'] = True

    return signals


def detect_target_regions(hreflang_tags: List[Dict]) -> List[str]:
    """
    Detect target regions from hreflang tags.

    Args:
        hreflang_tags: List of hreflang tag dictionaries

    Returns:
        List of detected region names
    """
    regions = set()

    region_mapping = {
        'de': 'Europe (DACH)',
        'fr': 'Europe (France)',
        'es': 'Europe (Spain) / Latin America',
        'pt': 'Europe (Portugal) / Latin America (Brazil)',
        'it': 'Europe (Italy)',
        'nl': 'Europe (Netherlands)',
        'pl': 'Europe (Poland)',
        'ja': 'Asia Pacific (Japan)',
        'zh': 'Asia Pacific (China)',
        'ko': 'Asia Pacific (Korea)',
        'ar': 'Middle East',
        'he': 'Middle East (Israel)',
        'ru': 'Eastern Europe / CIS',
        'tr': 'Middle East (Turkey)',
    }

    for tag in hreflang_tags:
        lang = tag.get('hreflang', '').lower()
        # Extract base language code
        base_lang = lang.split('-')[0] if lang else ''

        if base_lang in region_mapping:
            regions.add(region_mapping[base_lang])

    return list(regions)


def calculate_webscraper_tier(scan_results: Dict[str, Any]) -> Tuple[int, str]:
    """
    Calculate the WebScraper tier based on scan results with global expansion focus.

    Updated Tier System:
    - Tier 1 (Global Leader): 10+ locales with enterprise infrastructure
    - Tier 2 (Active Expansion): Already global (3-9 locales), actively expanding
    - Tier 3 (Going Global): First-time global expansion (1-2 locales with intent signals)
    - Tier 4 (Not Yet Global): No localization signals detected

    Args:
        scan_results: Dictionary containing scan results

    Returns:
        Tuple of (tier_number, tier_label)
    """
    if not scan_results:
        return (4, 'Not Yet Global')

    locale_count = scan_results.get('locale_count', 0) or 0
    languages = scan_results.get('languages_detected', []) or []
    hreflang_tags = scan_results.get('hreflang_tags', []) or []
    i18n_libraries = scan_results.get('i18n_libraries', []) or []
    expansion_signals = scan_results.get('expansion_signals', {})

    # Count detected languages from various sources
    detected_language_count = max(
        locale_count,
        len(languages) if isinstance(languages, list) else 0,
        len(hreflang_tags) if isinstance(hreflang_tags, list) else 0
    )

    # Check for enterprise indicators
    enterprise_signals = scan_results.get('enterprise_signals', []) or []
    has_enterprise_indicators = (
        scan_results.get('has_ci_pipeline', False) or
        len(enterprise_signals) > 0 or
        detected_language_count >= 10
    )

    # Check expansion signals
    is_first_time = expansion_signals.get('is_first_time_global', False) if isinstance(expansion_signals, dict) else False
    is_expanding = expansion_signals.get('is_actively_expanding', False) if isinstance(expansion_signals, dict) else False

    # ========== TIER CLASSIFICATION ==========

    # Tier 1: Global Leader
    # Large companies with mature localization (10+ locales)
    if detected_language_count >= 10:
        return (1, 'Global Leader')

    # Also Tier 1 if 5+ locales with enterprise signals
    if detected_language_count >= 5 and has_enterprise_indicators:
        return (1, 'Global Leader')

    # Tier 2: Active Expansion
    # Already global (3-9 locales) and showing expansion signals
    if detected_language_count >= 3:
        return (2, 'Active Expansion')

    # Also Tier 2 if 2 locales with strong expansion signals
    if detected_language_count >= 2 and (is_expanding or len(i18n_libraries) > 0):
        return (2, 'Active Expansion')

    # Tier 3: Going Global
    # First-time global expansion (1-2 locales with i18n setup)
    if detected_language_count >= 1 or is_first_time:
        if len(i18n_libraries) > 0 or is_first_time:
            return (3, 'Going Global')

    # Also Tier 3 if there are any i18n signals (even without hreflang)
    has_i18n_signals = (
        detected_language_count > 0 or
        len(i18n_libraries) > 0
    )
    if has_i18n_signals:
        return (3, 'Going Global')

    # Tier 4: Not Yet Global
    # No localization signals detected
    return (4, 'Not Yet Global')


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


def calculate_enterprise_score(scan_results: Dict[str, Any]) -> int:
    """
    Calculate an enterprise readiness score (0-100).

    Indicates how enterprise-ready the localization infrastructure is.

    Args:
        scan_results: Dictionary containing scan results

    Returns:
        Integer score from 0 to 100
    """
    if not scan_results:
        return 0

    score = 0

    # Multiple i18n libraries (professional setup)
    i18n_libs = scan_results.get('i18n_libraries', []) or []
    if len(i18n_libs) >= 2:
        score += 25
    elif len(i18n_libs) >= 1:
        score += 15

    # High locale count
    locale_count = scan_results.get('locale_count', 0) or 0
    if locale_count >= 10:
        score += 30
    elif locale_count >= 5:
        score += 20
    elif locale_count >= 3:
        score += 10

    # Proper hreflang implementation
    hreflang_tags = scan_results.get('hreflang_tags', []) or []
    if len(hreflang_tags) >= 10:
        score += 25
    elif len(hreflang_tags) >= 5:
        score += 20
    elif len(hreflang_tags) >= 2:
        score += 10

    # Enterprise signals
    if scan_results.get('has_ci_pipeline', False):
        score += 10
    if scan_results.get('enterprise_signals', []):
        score += 10

    return min(score, 100)


def extract_tier_from_scan_results(scan_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract all tier-related information from scan results.

    Args:
        scan_results: Raw scan results dictionary

    Returns:
        Dictionary with tier info
    """
    tier, tier_label = calculate_webscraper_tier(scan_results)
    localization_score = calculate_localization_score(scan_results)
    quality_gap_score = calculate_quality_gap_score(scan_results)
    enterprise_score = calculate_enterprise_score(scan_results)

    return {
        'tier': tier,
        'tier_label': tier_label,
        'localization_coverage_score': localization_score,
        'quality_gap_score': quality_gap_score,
        'enterprise_score': enterprise_score,
        'locale_count': scan_results.get('locale_count', 0) or 0,
        'languages_detected': scan_results.get('languages_detected', []) or [],
        'hreflang_tags': scan_results.get('hreflang_tags', []) or [],
        'i18n_libraries': scan_results.get('i18n_libraries', []) or []
    }


def generate_evidence_summary(scan_results: Dict[str, Any], expansion_signals: Dict[str, Any]) -> str:
    """
    Generate a human-readable evidence summary for the scan results.

    Args:
        scan_results: Scan results dictionary
        expansion_signals: Expansion signals dictionary

    Returns:
        String summary of key evidence
    """
    evidence = []

    locale_count = scan_results.get('locale_count', 0) or 0
    if locale_count > 0:
        evidence.append(f"{locale_count} locale(s) detected")

    hreflang_tags = scan_results.get('hreflang_tags', []) or []
    if hreflang_tags:
        evidence.append(f"{len(hreflang_tags)} hreflang tags")

    i18n_libs = scan_results.get('i18n_libraries', []) or []
    if i18n_libs:
        evidence.append(f"i18n: {', '.join(i18n_libs[:3])}")

    # Add expansion signals
    if expansion_signals:
        if expansion_signals.get('is_first_time_global'):
            evidence.append("First-time global expansion")
        if expansion_signals.get('is_actively_expanding'):
            evidence.append("Actively expanding")

        intent = expansion_signals.get('detected_intent', [])
        if intent:
            evidence.append(f"Intent: {intent[0]}")

        new_markets = expansion_signals.get('new_markets', [])
        if new_markets:
            evidence.append(f"Markets: {', '.join(new_markets[:2])}")

    return " | ".join(evidence) if evidence else "No signals detected"
