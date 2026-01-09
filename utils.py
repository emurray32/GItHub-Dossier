"""
Utility functions for 3-Signal Internationalization Intent Scanner.

Provides helper functions for signal detection and analysis.
"""
import re
from typing import Optional
from config import Config


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
    priority = ['Next.js', 'React', 'Vue', 'Angular', 'Django', 'Laravel', 'Ruby', 'Elixir', 'Python']

    detected_frameworks = set()
    for lib in libraries:
        framework = Config.I18N_LIBRARIES.get(lib)
        if framework:
            detected_frameworks.add(framework)

    for pf in priority:
        if pf in detected_frameworks:
            return pf

    return list(detected_frameworks)[0] if detected_frameworks else None


def format_signal_for_output(signal: dict) -> dict:
    """
    Format a signal object for the standardized output format.

    Output Format:
    {
        "Company": "Name",
        "Signal": "Dependency Injection",
        "Evidence": "Found react-intl in package.json but no locales folder",
        "Link": "URL_TO_FILE"
    }

    Args:
        signal: Raw signal dict from scanner

    Returns:
        Formatted signal dict
    """
    return {
        'Company': signal.get('Company', 'Unknown'),
        'Signal': signal.get('Signal', signal.get('type', 'Unknown')),
        'Evidence': signal.get('Evidence', ''),
        'Link': signal.get('Link', signal.get('url', '')),
    }


def summarize_signals(signals: list) -> dict:
    """
    Create a summary of detected signals.

    Args:
        signals: List of signal objects

    Returns:
        Summary dict with counts and categorized signals
    """
    summary = {
        'total': len(signals),
        'by_type': {
            'rfc_discussion': [],
            'dependency_injection': [],
            'ghost_branch': [],
        },
        'high_priority_count': 0,
    }

    for signal in signals:
        signal_type = signal.get('type', 'unknown')

        if signal_type in summary['by_type']:
            summary['by_type'][signal_type].append(signal)

        if signal.get('priority') == 'HIGH':
            summary['high_priority_count'] += 1

    return summary


def get_phase_from_signal_type(signal_type: str) -> str:
    """
    Map signal type to internationalization phase.

    Args:
        signal_type: Type of signal (rfc_discussion, dependency_injection, ghost_branch)

    Returns:
        Phase name (Thinking, Preparing, Active)
    """
    phase_mapping = {
        'rfc_discussion': 'Thinking',
        'dependency_injection': 'Preparing',
        'ghost_branch': 'Active',
    }
    return phase_mapping.get(signal_type, 'Unknown')
