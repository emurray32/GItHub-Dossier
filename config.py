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
    MAX_REPOS_TO_SCAN = 50  # Top N most active repos

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

    I18N_SCRIPT_KEYWORDS = [
        'extract-i18n',
        'extract-intl',
        'compile-locales',
        'sync-translations',
        'update-strings',
        'i18next-scanner',
        'lingui extract',
    ]

    # ============================================================
    # GOLDILOCKS ZONE DETECTION
    # ============================================================
    # These are the ONLY libraries that indicate a company is
    # PREPARING for internationalization but NOT yet launched.
    # We want companies who bought the tools but haven't shipped.

    SMOKING_GUN_LIBS = [
        # TIER 1: Primary Targets (High-Intent Pre-Launch Signals)
        'babel-plugin-react-intl',   # React string extraction - infrastructure setup
        'react-i18next',             # React i18n framework - preparing for translations
        'formatjs',                  # ICU message formatting - building the foundation
        'try-pseudo-localization',   # Pseudo-localization for UI layout testing
        'react-pseudo',              # Pseudo-localization for React
        'i18next-pseudo',            # Pseudo-localization for i18next
        # Note: 'uppy' is checked separately for i18n/locale properties
    ]

    # Code cleaning/linting libraries for scrubbing hardcoded strings
    LINTER_LIBRARIES = [
        'eslint-plugin-i18n',
        'eslint-plugin-no-literal-string',
        'eslint-plugin-vue-i18n',
        'i18next-scanner',
        'babel-plugin-react-intl-auto',
        'rubocop-i18n',
    ]

    # CMS Internationalization libraries
    CMS_I18N_LIBS = [
        'gatsby-plugin-i18n',
        '@sanity/document-internationalization',
        'sanity-plugin-intl-input',
        'strapi-plugin-i18n',
        'contentful-resolve-response',
        '@storyblok/js',
        'netlify-cms-widget-i18n',
    ]

    # Uppy requires special handling - only counts if i18n/locale config is present
    UPPY_LIBRARY = 'uppy'
    UPPY_I18N_INDICATORS = ['locale', 'i18n', 'locales', 'strings']

    # Pseudo-localization config patterns
    PSEUDO_CONFIG_PATTERNS = [
        'pseudo: true',
        'pseudoLocale',
        'pseudoLocalize',
    ]

    # ============================================================
    # EXCLUSION FOLDERS - DISQUALIFIES "GOLDILOCKS ZONE" STATUS
    # ============================================================
    # If ANY of these folders exist, the company has ALREADY LAUNCHED.
    # They are "Too Late" for our ideal sales window.
    # Score drops to 10/100 (Low Priority - Already Launched)

    EXCLUSION_FOLDERS = [
        'locales',
        'locale',
        'i18n',
        'translations',
        'lang',
        'languages',
        'l10n',
        'messages',
    ]

    # Alias for backwards compatibility
    LOCALE_FOLDERS = EXCLUSION_FOLDERS

    # ============================================================
    # SOURCE LOCALE PATTERNS - Files that indicate "preparing" not "launched"
    # ============================================================
    # If a locale folder ONLY contains these source language files,
    # it means the infrastructure is ready but no translations exist yet.
    # This is still a GOLDILOCKS ZONE - don't disqualify!
    #
    # Pattern matching is case-insensitive.

    SOURCE_LOCALE_BASE_NAMES = [
        'en', 'en-us', 'en-gb', 'en_us', 'en_gb',  # English variants
        'base', 'source', 'default',                # Generic source names
    ]

    SOURCE_LOCALE_EXTENSIONS = ['.json', '.js', '.ts', '.yml', '.yaml']

    # Pre-computed list of all valid source-only filenames (lowercase)
    SOURCE_LOCALE_PATTERNS = []
    for base in SOURCE_LOCALE_BASE_NAMES:
        for ext in SOURCE_LOCALE_EXTENSIONS:
            SOURCE_LOCALE_PATTERNS.append(f"{base}{ext}")

    # ============================================================
    # MOBILE GOLDILOCKS ZONE DETECTION
    # ============================================================
    # Mobile apps (iOS/Android) have their own locale folder patterns.
    # These are excluded from web-style detection but checked separately.

    MOBILE_EXCLUSION_PATTERNS = ['*.lproj', 'values-*']

    # Indicators that mobile i18n infrastructure is set up
    MOBILE_INDICATORS = {
        'ios': {
            'type': 'folder',
            'path': 'Base.lproj',
            'description': 'iOS Base localization folder',
        },
        'android': {
            'type': 'file',
            'path': 'res/values/strings.xml',
            'description': 'Android default strings resource',
        },
    }

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
    # INTENT SCORE WEIGHTS - GOLDILOCKS ZONE SCORING
    # ============================================================
    # New scoring model focused on PRE-LAUNCH detection.
    # The "Gap" (Library found + No locale folders) = HIGHEST score.

    INTENT_SCORE_WEIGHTS = {
        'rfc_discussion_high': 30,    # HIGH priority RFC/Proposal
        'rfc_discussion_medium': 15,  # MEDIUM priority discussion
        'dependency_injection': 40,   # Smoking gun - highest value
        'ghost_branch': 25,           # WIP branch/PR
    }

    # ============================================================
    # GOLDILOCKS ZONE SCORING TIERS
    # ============================================================
    # These are the final intent scores based on the "Gap Requirement"

    GOLDILOCKS_SCORES = {
        # PREPARING: Found libraries + NO locale folders = GOLDILOCKS ZONE
        # This is our IDEAL customer - ready to buy, needs our help
        'preparing_min': 90,
        'preparing_max': 100,

        # THINKING: RFC/Discussions found, no code yet
        # Worth nurturing but not ready to buy
        'thinking': 40,

        # LAUNCHED: Locale folders exist - TOO LATE
        # They already have a solution, low priority
        'launched': 10,
    }

    # BDR-friendly status labels
    LEAD_STATUS_LABELS = {
        'preparing': 'HOT LEAD - Infrastructure Ready, No Translations',
        'thinking': 'WARM LEAD - Discussing Internationalization',
        'launched': 'LOW PRIORITY - Already Localized',
        'none': 'COLD - No Signals Detected',
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

    # BDR-Friendly Library Descriptions
    # Technical name -> "What this means for a sales rep"
    I18N_LIBRARIES = {
        'babel-plugin-react-intl': 'React String Extraction',
        'react-i18next': 'React i18n Framework',
        'formatjs': 'Message Formatting Library',
        'uppy': 'File Uploader with i18n Config',
        # Legacy mappings
        'react-intl': 'React',
        'i18next': 'JS/React',
        'vue-i18n': 'Vue',
        'lingui': 'React',
        'next-intl': 'Next.js',
        'django-modeltranslation': 'Django',
        'babel': 'Python',
        'globalize': 'Ruby',
        'fast_gettext': 'Ruby',
        'gettext': 'Elixir',
    }

    # BDR Layman Terms - Translate technical findings to sales language
    BDR_TRANSLATIONS = {
        'babel-plugin-react-intl': 'The team is currently TAGGING the app for translation. They are wrapping text strings so they can be translated later.',
        'react-i18next': 'The team has installed the TRANSLATION ENGINE but hasnt loaded any languages yet. The car is built, but theres no gas.',
        'formatjs': 'The team is setting up MESSAGE FORMATTING - how dates, numbers, and plurals will display in different languages.',
        'uppy': 'The file uploader is being prepared for MULTIPLE LANGUAGES. International users are expected.',
        'locale_folder_missing': '🔥 GOLDILOCKS: They built the shelves, but have no books. Call now!',
        'locale_folder_source_only': '🔥 GOLDILOCKS: They have a locale folder but ONLY source files. Infrastructure ready, waiting for translation!',
        'locale_folder_exists': '🚫 BLOCKED: They already have translation files. We are too late.',
        'unknown': 'Generic Localization Software',
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
