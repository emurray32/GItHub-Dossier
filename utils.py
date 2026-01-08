"""
Utility functions for GitHub Dossier High-Intent Sales Intelligence.

Provides locale parsing, geo-spatial inference, frustration detection,
and tech stack analysis capabilities.
"""
import re
from typing import Optional, Tuple
from config import Config


def parse_locale_code(filename: str) -> dict:
    """
    Extract language and region from locale filename.

    Handles common patterns:
        - en-US.json, en_US.json -> {'lang': 'en', 'region': 'US'}
        - es-MX.yml -> {'lang': 'es', 'region': 'MX'}
        - de.json -> {'lang': 'de', 'region': None}
        - zh-Hans.json -> {'lang': 'zh', 'region': None, 'script': 'Hans'}

    Args:
        filename: The filename to parse (e.g., 'en-US.json')

    Returns:
        Dict with 'lang', 'region', and optionally 'script' keys.
    """
    # Remove extension and path
    basename = filename.split('/')[-1]
    name = basename.rsplit('.', 1)[0] if '.' in basename else basename

    result = {'lang': None, 'region': None, 'script': None}

    # Pattern: lang-Region or lang_Region (e.g., en-US, es_MX)
    match = re.match(r'^([a-z]{2,3})[-_]([A-Z]{2})$', name, re.IGNORECASE)
    if match:
        result['lang'] = match.group(1).lower()
        result['region'] = match.group(2).upper()
        return result

    # Pattern: lang-Script (e.g., zh-Hans, zh-Hant)
    match = re.match(r'^([a-z]{2,3})[-_]([A-Z][a-z]{3})$', name, re.IGNORECASE)
    if match:
        result['lang'] = match.group(1).lower()
        result['script'] = match.group(2).title()
        return result

    # Pattern: lang-Script-Region (e.g., zh-Hans-CN)
    match = re.match(r'^([a-z]{2,3})[-_]([A-Z][a-z]{3})[-_]([A-Z]{2})$', name, re.IGNORECASE)
    if match:
        result['lang'] = match.group(1).lower()
        result['script'] = match.group(2).title()
        result['region'] = match.group(3).upper()
        return result

    # Pattern: just lang (e.g., de, fr, ja)
    match = re.match(r'^([a-z]{2,3})$', name, re.IGNORECASE)
    if match:
        result['lang'] = match.group(1).lower()
        return result

    return result


def get_region_insight(region_code: str) -> Optional[str]:
    """Map region code to strategic market insight."""
    if not region_code:
        return None
    return Config.LOCALE_TO_REGION.get(region_code.lower())


def infer_market_strategy(locales: list) -> dict:
    """
    Analyze detected locales to infer market expansion strategy.

    Args:
        locales: List of locale codes/filenames detected

    Returns:
        Dict with 'regions', 'markets', 'primary_market', and 'narrative' keys.
    """
    regions = set()
    markets = set()
    lang_count = 0

    for locale in locales:
        parsed = parse_locale_code(locale)

        if parsed['lang']:
            lang_count += 1

        if parsed['region']:
            region = parsed['region']
            insight = get_region_insight(region)
            if insight:
                regions.add(insight)
                # Extract market category (LATAM, APAC, etc.)
                if '(' in insight and ')' in insight:
                    market = insight.split('(')[1].split(')')[0]
                    markets.add(market)

    # Determine primary market for email subject
    primary_market = None
    if 'LATAM' in markets:
        primary_market = 'Latin America'
    elif 'APAC' in markets:
        primary_market = 'Asia-Pacific'
    elif 'DACH' in markets:
        primary_market = 'DACH region'
    elif 'MENA' in markets:
        primary_market = 'Middle East'
    elif regions:
        primary_market = list(regions)[0].split(' (')[0]

    # Generate narrative
    narrative = ""
    if regions:
        region_list = list(regions)[:5]
        if 'LATAM' in markets:
            narrative += "Strong focus on Latin American expansion. "
        if 'APAC' in markets:
            narrative += "Active Asia-Pacific market penetration. "
        if any('DACH' in r for r in region_list):
            narrative += "Targeting German-speaking European markets. "
        if 'MENA' in markets:
            narrative += "Expanding into Middle East/North Africa. "

        if not narrative:
            narrative = f"Targeting {', '.join(region_list[:3])}. "
    elif lang_count > 1:
        narrative = f"Multi-language support detected ({lang_count} languages). "
    else:
        narrative = "Limited localization footprint detected. "

    return {
        'regions': list(regions),
        'markets': list(markets),
        'primary_market': primary_market,
        'language_count': lang_count,
        'narrative': narrative.strip()
    }


def detect_dependencies_in_content(content: str, filename: str) -> dict:
    """
    Detect i18n libraries and TMS in dependency file content.

    Returns i18n libraries with their framework mapping for tech_stack_hook.

    Args:
        content: Raw content of dependency file
        filename: Name of the file (to determine parsing strategy)

    Returns:
        Dict with 'i18n_libraries', 'frameworks', 'tms_detected' lists.
    """
    content_lower = content.lower()

    i18n_found = []
    frameworks = set()
    tms_found = []

    # Check for i18n libraries (now a dict with framework mapping)
    for lib, framework in Config.I18N_LIBRARIES.items():
        if lib.lower() in content_lower:
            i18n_found.append(lib)
            frameworks.add(framework)

    # Check for TMS competitors
    for tms in Config.TMS_COMPETITORS:
        if tms.lower() in content_lower:
            tms_found.append(tms)

    return {
        'i18n_libraries': i18n_found,
        'frameworks': list(frameworks),
        'tms_detected': tms_found,
        'source_file': filename
    }


def detect_frustration_signal(message: str) -> Optional[dict]:
    """
    Check if a commit message indicates frustration/pain with localization.

    Uses FRUSTRATION_REGEX to detect patterns like:
    - "fix translation sync issue"
    - "missing locale key"
    - "conflict in strings file"

    Args:
        message: Commit message to analyze

    Returns:
        Dict with 'matched_text' and 'pain_indicator' if found, None otherwise.
    """
    if not message:
        return None

    match = re.search(Config.FRUSTRATION_REGEX, message.lower())
    if match:
        action = match.group(1)
        target = match.group(2)
        
        # Map to pain indicators
        pain_mapping = {
            'fix': 'bug_fixing',
            'broken': 'broken_workflow',
            'missing': 'missing_content',
            'sync': 'sync_issues',
            'conflict': 'merge_conflicts',
            'manual': 'manual_process',
            'update': 'maintenance',
            'revert': 'rollback',
            'hotfix': 'urgent_issue',
            'urgent': 'urgent_issue'
        }
        
        return {
            'matched_text': match.group(0),
            'action': action,
            'target': target,
            'pain_indicator': pain_mapping.get(action, 'general_issue')
        }
    
    return None


def is_bot_account(username: str) -> bool:
    """
    Check if a username is a known bot account.

    Args:
        username: GitHub username to check

    Returns:
        True if the username matches a known bot pattern.
    """
    if not username:
        return False
    
    username_lower = username.lower()
    
    # Check against known bot accounts
    if username_lower in [b.lower() for b in Config.BOT_ACCOUNTS]:
        return True
    
    # Check for common bot patterns
    bot_patterns = ['[bot]', '-bot', '_bot', 'bot-', 'bot_', 'automation']
    return any(pattern in username_lower for pattern in bot_patterns)


def calculate_developer_translator_ratio(file_signals: list) -> dict:
    """
    Calculate the percentage of translation file edits made by humans vs bots.

    High percentage = High Pain (developers doing translation work)

    Args:
        file_signals: List of file_change signals with author info

    Returns:
        Dict with 'human_edits', 'bot_edits', 'total', 'human_ratio', 'is_high_pain'.
    """
    human_edits = 0
    bot_edits = 0
    human_authors = set()

    for signal in file_signals:
        author = signal.get('author', '')
        if is_bot_account(author):
            bot_edits += 1
        else:
            human_edits += 1
            if author:
                human_authors.add(author)

    total = human_edits + bot_edits
    human_ratio = human_edits / total if total > 0 else 0

    return {
        'human_edits': human_edits,
        'bot_edits': bot_edits,
        'total': total,
        'human_ratio': human_ratio,
        'human_percentage': f"{human_ratio * 100:.0f}%",
        'human_authors': list(human_authors),
        'is_high_pain': human_ratio > 0.7  # 70%+ humans = high pain
    }


def get_framework_from_libraries(libraries: list) -> Optional[str]:
    """
    Get the primary framework from detected i18n libraries.

    Args:
        libraries: List of detected i18n library names

    Returns:
        Primary framework name (e.g., 'Next.js', 'React') or None.
    """
    if not libraries:
        return None

    # Priority order for frameworks (more specific first)
    priority = ['Next.js', 'React', 'Vue', 'Angular', 'Django', 'Laravel', 'Ruby/Rails', 'Go']
    
    detected_frameworks = set()
    for lib in libraries:
        framework = Config.I18N_LIBRARIES.get(lib)
        if framework:
            detected_frameworks.add(framework)
    
    for pf in priority:
        if pf in detected_frameworks:
            return pf
    
    return list(detected_frameworks)[0] if detected_frameworks else None
