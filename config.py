"""
Configuration settings for 3-Signal Internationalization Intent Scanner.

Focused on detecting companies in the THINKING and PREPARING phases
of internationalization, BEFORE code is merged.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration for 3-Signal Intent Scanner."""

    # Flask
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'

    # GitHub API
    GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
    GITHUB_API_BASE = 'https://api.github.com'

    # Gemini AI
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GEMINI_MODEL = 'gemini-2.5-flash'

    # Database
    DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'lead_machine.db')

    # Scan Configuration
    MAX_REPOS_TO_SCAN = 15  # Top N most active repos

    # Priority keywords for repo selection
    PRIORITY_KEYWORDS = [
        'web', 'mobile', 'ios', 'android', 'frontend', 'docs',
        'app', 'client', 'site', 'platform', 'ui', 'portal'
    ]

    # ============================================================
    # 3-SIGNAL INTENT SYSTEM CONFIGURATION
    # ============================================================

    # ============================================================
    # SIGNAL 1: RFC & DISCUSSION (Thinking Phase)
    # ============================================================
    # Target: Issues and Discussions (Open & Closed)
    # Logic: Flag if title or body contains high-intent keywords
    # Priority: HIGH if title starts with 'RFC' or 'Proposal'

    RFC_LOOKBACK_DAYS = 180  # 6 months

    RFC_KEYWORDS = [
        'i18n strategy',
        'localization support',
        'handle timezones',
        'currency formatting',
        'RTL support',
        'translation workflow',
        'multi-currency',
    ]

    # ============================================================
    # SIGNAL 2: DEPENDENCY INJECTION (Preparing Phase)
    # ============================================================
    # Target: Dependency files
    # Logic: Flag if 'Smoking Gun' i18n libraries are present
    # Constraint: ONLY valid if /locales/ or /messages/ folder does NOT exist

    DEPENDENCY_INJECTION_FILES = [
        'package.json',
        'Gemfile',
        'requirements.txt',
        'composer.json',
        'mix.exs',
    ]

    # The 'Smoking Gun' Libraries - tools bought but not yet used
    SMOKING_GUN_LIBS = [
        # JS/React
        'react-intl',
        'i18next',
        'vue-i18n',
        'lingui',
        'formatjs',
        'next-intl',
        # Python
        'django-modeltranslation',
        'babel',
        # Ruby
        'globalize',
        'fast_gettext',
        # Elixir
        'gettext',
    ]

    # Folders that indicate i18n is already implemented (disqualifies Signal 2)
    LOCALE_FOLDERS = [
        'locales',
        'locale',
        'messages',
        'i18n',
        'translations',
        'lang',
    ]

    # ============================================================
    # SIGNAL 3: GHOST BRANCH (Active Phase)
    # ============================================================
    # Target: Branches and Pull Requests
    # Logic: Flag branches/PRs indicating WIP localization work

    GHOST_BRANCH_PATTERNS = [
        'feature/i18n',
        'chore/localization',
        'add-translation-support',
        'refactor/extract-strings',
        'l10n-setup',
        # Additional common patterns
        'i18n',
        'l10n',
        'localization',
        'internationalization',
        'translations',
        'intl',
    ]

    # ============================================================
    # INTENT SCORE WEIGHTS
    # ============================================================

    INTENT_SCORE_WEIGHTS = {
        'rfc_discussion_high': 30,    # HIGH priority RFC/Proposal
        'rfc_discussion_medium': 15,  # MEDIUM priority discussion
        'dependency_injection': 40,   # Smoking gun - highest value
        'ghost_branch': 25,           # WIP branch/PR
    }

    # ============================================================
    # LEGACY COMPATIBILITY (kept for AI summary module)
    # ============================================================

    # These are kept for backwards compatibility with ai_summary.py
    # but are not used in the new 3-Signal scanner

    I18N_FILE_PATTERNS = [
        'locales/', 'locale/', 'i18n/', 'translations/', 'lang/',
        'languages/', 'l10n/', '.lproj/', 'strings/', 'messages/'
    ]

    I18N_LIBRARIES = {
        'react-intl': 'React',
        'i18next': 'JS/React',
        'vue-i18n': 'Vue',
        'lingui': 'React',
        'formatjs': 'JS/React',
        'next-intl': 'Next.js',
        'django-modeltranslation': 'Django',
        'babel': 'Python',
        'globalize': 'Ruby',
        'fast_gettext': 'Ruby',
        'gettext': 'Elixir',
    }

    # Bot accounts to exclude
    BOT_ACCOUNTS = [
        'dependabot', 'dependabot[bot]', 'github-actions', 'github-actions[bot]',
        'renovate', 'renovate[bot]', 'semantic-release-bot', 'greenkeeper',
        'snyk-bot', 'codecov', 'codecov[bot]', 'vercel[bot]', 'netlify[bot]',
    ]

    # Legacy confidence weights (mapped to intent score)
    CONFIDENCE_WEIGHTS = {
        'rfc_discussion': 25,
        'dependency_injection': 40,
        'ghost_branch': 25,
    }
