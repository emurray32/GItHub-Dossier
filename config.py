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

    # ============================================================
    # GITHUB TOKEN POOL - Crowdsourced Rate Limit Evasion
    # ============================================================
    #
    # The Problem: GitHub's API limit is 5,000 requests/hour per token.
    # Scanning a "Mega-Corp" uses ~100-200 requests. You hit the wall
    # at ~25 companies per hour with a single token.
    #
    # The Solution: Token Pool with intelligent rotation.
    #
    # Setup:
    #   1. Ask every BDR on the team to generate a Personal Access Token
    #   2. Set GITHUB_TOKENS=token1,token2,token3,... in your .env file
    #   3. The system automatically rotates through tokens, selecting the
    #      one with the highest remaining rate limit.
    #
    # BDR Benefit:
    #   - 1 token  =  5,000 req/hr =  ~25 companies/hour
    #   - 5 tokens = 25,000 req/hr = ~125 companies/hour
    #   - 10 tokens = 50,000 req/hr = ~250 companies/hour
    #
    # Token Requirements (minimum permissions):
    #   - public_repo (read public repositories)
    #   - read:org (read organization info) - optional but recommended
    #
    # ============================================================

    GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
    GITHUB_API_BASE = 'https://api.github.com'

    # Token pool configuration
    TOKEN_POOL_LOW_THRESHOLD = 50     # Start considering other tokens when below this
    TOKEN_POOL_CRITICAL_THRESHOLD = 10  # Definitely switch tokens when below this

    @staticmethod
    def get_github_tokens() -> list:
        """
        Load GitHub tokens from environment variables.

        Priority:
        1. GITHUB_TOKENS (comma-separated list for token pool)
        2. GITHUB_TOKEN (single token, for backward compatibility)

        Example .env:
            GITHUB_TOKENS=ghp_abc123,ghp_def456,ghp_ghi789

        Returns:
            List of tokens, or empty list if none configured.
        """
        tokens_str = os.getenv('GITHUB_TOKENS', '')
        if tokens_str:
            # Parse comma-separated tokens, strip whitespace, filter empty
            tokens = [t.strip() for t in tokens_str.split(',') if t.strip()]
            if tokens:
                return tokens

        # Fall back to single token for backward compatibility
        single_token = os.getenv('GITHUB_TOKEN')
        if single_token:
            return [single_token]

        return []

    GITHUB_TOKENS = get_github_tokens.__func__()  # Initialize at class load time

    @staticmethod
    def get_token_pool_capacity() -> dict:
        """
        Calculate the theoretical capacity of the token pool.

        Returns:
            Dict with capacity metrics.
        """
        tokens = Config.get_github_tokens()
        token_count = len(tokens)
        hourly_capacity = token_count * 5000
        companies_per_hour = hourly_capacity // 200  # ~200 requests per company scan

        return {
            'token_count': token_count,
            'hourly_requests': hourly_capacity,
            'estimated_companies_per_hour': companies_per_hour,
        }

    # Gemini AI
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GEMINI_MODEL = 'gemini-2.5-flash'

    # Database
    DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'lead_machine.db')

    # Webhook Configuration
    # URL to POST lead notifications when tier changes to Thinking (1) or Preparing (2)
    # Useful for Zapier, Salesforce, or other integrations
    WEBHOOK_URL = os.getenv('WEBHOOK_URL')

    # Scan Configuration
    MAX_REPOS_TO_SCAN = 50  # Top N most active repos
    REPO_INACTIVITY_DAYS = 730  # Skip repos not pushed in this many days (2 years)
    REPO_INACTIVITY_FALLBACK = 10  # If all repos filtered, take top N anyway

    # Priority keywords for repo selection
    PRIORITY_KEYWORDS = [
        'web', 'mobile', 'ios', 'android', 'frontend', 'docs',
        'app', 'client', 'site', 'platform', 'ui', 'portal'
    ]

    # High-value repository patterns (core product indicators)
    # +1000 points if repo name contains any of these
    HIGH_VALUE_PATTERNS = [
        'web', 'app', 'frontend', 'mobile', 'ios', 'android',
        'server', 'api', 'ui', 'client', 'monorepo',
        'website', 'marketing', 'dashboard', 'console'
    ]

    # Low-value repository patterns (non-core repos to deprioritize)
    # -500 points if repo name contains any of these
    # Note: 'docs' and 'documentation' intentionally excluded - they can be
    # early indicators of i18n work (especially with Docusaurus/Astro)
    LOW_VALUE_PATTERNS = [
        'tool', 'script', 'demo', 'example', 'test', 'fork'
    ]

    # High-value programming languages for i18n scanning
    # +500 points if repo uses these languages
    HIGH_VALUE_LANGUAGES = ['TypeScript', 'JavaScript', 'Swift', 'Kotlin']

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
        'i18n-js',                   # JavaScript i18n library - general purpose
        'typesafe-i18n',             # TypeScript-first i18n library - type-safe translations
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

    # Framework configuration files that may contain i18n routing config
    FRAMEWORK_CONFIG_FILES = [
        'next.config.js',
        'next.config.mjs',
        'nuxt.config.js',
        'nuxt.config.ts',
        'remix.config.js',
        'angular.json',
        # Static site generators with i18n support
        'docusaurus.config.js',
        'docusaurus.config.ts',
        'astro.config.mjs',
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
    # SIGNAL 4: DOCUMENTATION INTENT (Thinking Phase)
    # ============================================================
    # Target: Documentation files that may mention planned i18n work
    # Logic: Flag if i18n keywords are found NEAR context words
    #        indicating future/in-progress work
    # This catches companies mentioning i18n in changelogs/roadmaps
    # BEFORE the code is fully live.

    DOCUMENTATION_FILES = [
        'CHANGELOG.md',
        'CONTRIBUTING.md',
        'README.md',
        'ROADMAP.md',
        'changelog.md',
        'contributing.md',
        'readme.md',
        'roadmap.md',
        'HISTORY.md',
        'history.md',
    ]

    # Intent keywords - these indicate i18n planning
    DOCUMENTATION_INTENT_KEYWORDS = [
        'i18n support',
        'localization support',
        'translation',
        'internationalization',
        'feat(i18n)',
        'chore(i18n)',
        'i18n:',
        'l10n support',
        'multi-language',
        'multilingual',
    ]

    # Context keywords - these indicate future/in-progress work
    # A match requires BOTH an intent keyword AND a context keyword nearby
    DOCUMENTATION_CONTEXT_KEYWORDS = [
        'beta',
        'roadmap',
        'upcoming',
        'help wanted',
        'best effort',
        'planned',
        'todo',
        'wip',
        'in progress',
        'in-progress',
        'unreleased',
        'experimental',
        'coming soon',
        'future',
        'proposal',
        'rfc',
        'draft',
        'milestone',
    ]

    # Negative indicators - if found near the keyword, it's likely already launched
    DOCUMENTATION_LAUNCHED_INDICATORS = [
        'available in',
        'supported languages',
        'translated to',
        'translations available',
        'localized for',
        'supports the following languages',
        'language support includes',
        'currently translated',
        'fully localized',
    ]

    # Proximity threshold - how close (in characters) context words must be
    DOCUMENTATION_PROXIMITY_CHARS = 200

    # File priority weights - CHANGELOG is higher signal than README
    DOCUMENTATION_FILE_WEIGHTS = {
        'changelog': 'HIGH',
        'roadmap': 'HIGH',
        'history': 'HIGH',
        'contributing': 'MEDIUM',
        'readme': 'MEDIUM',
    }

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
        'documentation_intent_high': 20,    # HIGH priority (CHANGELOG, ROADMAP)
        'documentation_intent_medium': 10,  # MEDIUM priority (README, CONTRIBUTING)
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
        'locale_folder_missing': 'GOLDILOCKS: They built the shelves, but have no books. Call now!',
        'locale_folder_source_only': 'GOLDILOCKS: They have a locale folder but ONLY source files. Infrastructure ready, waiting for translation!',
        'locale_folder_exists': 'BLOCKED: They already have translation files. We are too late.',
        'unknown': 'Generic Localization Software',
    }

    # Bot accounts to exclude
    BOT_ACCOUNTS = [
        'dependabot', 'dependabot[bot]', 'github-actions', 'github-actions[bot]',
        'renovate', 'renovate[bot]', 'semantic-release-bot', 'greenkeeper',
        'snyk-bot', 'codecov', 'codecov[bot]', 'vercel[bot]', 'netlify[bot]',
    ]

    # ============================================================
    # OPEN PROTOCOL / DECENTRALIZED PROJECT DISQUALIFIERS
    # ============================================================
    # These patterns identify open-source protocol projects and decentralized
    # community projects that are NOT commercial companies with buying intent.
    #
    # Examples: Status (decentralized messenger), Protocol Labs, various DAOs
    #
    # If ANY of these patterns match the org description (case-insensitive),
    # the account is disqualified as a false positive.

    OPEN_PROTOCOL_DISQUALIFIERS = [
        # Decentralized / Web3 indicators
        'decentralized',
        'decentralised',
        'open protocol',
        'open-protocol',
        'community project',
        'community-driven',
        'community owned',
        'community-owned',
        'powered by the community',
        'powered by its members',
        'powered by their members',
        'anyone can fork',
        'anyone can build',
        'anyone can contribute',

        # Blockchain / Crypto indicators (typically not commercial buyers)
        'blockchain protocol',
        'web3 protocol',
        'defi protocol',
        'dao ',  # Note: space to avoid matching "dao" in words like "shadow"
        ' dao',
        'decentralized autonomous',

        # Open source protocol indicators
        'protocol specification',
        'reference implementation',
        'open standard',
        'open-standard',
    ]

    # Legacy confidence weights (mapped to intent score)
    CONFIDENCE_WEIGHTS = {
        'rfc_discussion': 25,
        'dependency_injection': 40,
        'ghost_branch': 25,
        'documentation_intent': 15,
    }
