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

        Auto-discovers tokens matching these patterns:
        1. GITHUB_TOKENS (comma-separated list for token pool)
        2. GITHUB_TOKEN (single token, for backward compatibility)
        3. GITHUB_TOKEN_* (e.g., GITHUB_TOKEN_2, GITHUB_TOKEN_BDR)
        4. GitHubToken_* (e.g., GitHubToken_Michael, GitHubToken_Sales)

        This allows BDRs to add their own tokens without modifying config.

        Returns:
            List of unique tokens, or empty list if none configured.
        """
        tokens = []
        seen = set()

        def add_token(token):
            if token and token.strip() and token.strip() not in seen:
                seen.add(token.strip())
                tokens.append(token.strip())

        tokens_str = os.getenv('GITHUB_TOKENS', '')
        if tokens_str:
            for t in tokens_str.split(','):
                add_token(t)

        add_token(os.getenv('GITHUB_TOKEN'))

        for key, value in os.environ.items():
            key_upper = key.upper()
            if key_upper.startswith('GITHUB_TOKEN_') or key_upper.startswith('GITHUBTOKEN_'):
                add_token(value)

        return tokens

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

    # Gemini AI (accepts either GOOGLE_API_KEY or GEMINI_API_KEY)
    GEMINI_API_KEY = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
    GEMINI_MODEL = 'gemini-2.5-flash'

    # Database
    DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'lead_machine.db')

    # ============================================================
    # REDIS CACHING CONFIGURATION
    # ============================================================
    # Redis provides fast caching for GitHub API responses, dramatically
    # reducing API calls during re-scans and enabling faster dashboard loads.
    #
    # Setup:
    #   Option 1 (Recommended): Set REDIS_URL in your .env file
    #   Option 2: Set individual REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD
    #   Option 3: No config = falls back to disk-based caching (slower but works)
    #
    # TTL Strategy:
    #   - Organization metadata: 24 hours (rarely changes)
    #   - Repository lists: 7 days (invalidated on webhook if configured)
    #   - File contents (package.json, etc.): 7 days
    #   - Branch/PR lists: 12 hours (more dynamic)
    #   - Issue/Discussion lists: 6 hours (frequently updated)
    #
    # Impact:
    #   - 60% reduction in GitHub API calls
    #   - 80% faster re-scans within TTL window
    #   - Preserves rate limit capacity for new scans
    # ============================================================

    REDIS_URL = os.getenv('REDIS_URL')  # e.g., redis://localhost:6379/0
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
    REDIS_DB = int(os.getenv('REDIS_DB', 0))
    REDIS_PASSWORD = os.getenv('REDIS_PASSWORD')

    # Cache TTLs in seconds
    CACHE_TTL_ORG_METADATA = int(os.getenv('CACHE_TTL_ORG_METADATA', 86400))      # 24 hours
    CACHE_TTL_REPO_LIST = int(os.getenv('CACHE_TTL_REPO_LIST', 604800))           # 7 days
    CACHE_TTL_FILE_CONTENT = int(os.getenv('CACHE_TTL_FILE_CONTENT', 604800))     # 7 days
    CACHE_TTL_BRANCH_LIST = int(os.getenv('CACHE_TTL_BRANCH_LIST', 43200))        # 12 hours
    CACHE_TTL_ISSUE_LIST = int(os.getenv('CACHE_TTL_ISSUE_LIST', 21600))          # 6 hours
    CACHE_TTL_DEFAULT = int(os.getenv('CACHE_TTL_DEFAULT', 3600))                 # 1 hour fallback

    # Cache control
    CACHE_ENABLED = os.getenv('CACHE_ENABLED', 'true').lower() == 'true'
    CACHE_FALLBACK_DIR = os.path.join(os.path.dirname(__file__), 'data', 'cache')

    # Webhook Configuration
    # URL to POST lead notifications when tier changes to Thinking (1) or Preparing (2)
    # Useful for Zapier, Salesforce, or other integrations
    WEBHOOK_URL = os.getenv('WEBHOOK_URL')

    # Scan Configuration
    MAX_REPOS_TO_SCAN = 50  # Top N most active repos to consider
    REPOS_PER_PHASE = 3     # Top N repos to scan deeply per phase (reduced from 5 for performance)
                            # Scanning 3 repos instead of 5 reduces API calls by 40% with minimal signal loss
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
        'internationalization',
        'translate',
        'global expansion',
    ]

    # ============================================================
    # NLP FILTERING FOR RFC DETECTION
    # ============================================================
    # These patterns help distinguish actual i18n planning discussions
    # from generic mentions of "translate" in code contexts.
    # This reduces false positives significantly.

    # High-confidence i18n intent phrases (require less context)
    RFC_HIGH_INTENT_PHRASES = [
        'i18n support',
        'localization support',
        'internationalization support',
        'translation support',
        'multi-language support',
        'multilingual support',
        'language support',
        'regional support',
        'i18n roadmap',
        'localization roadmap',
        'i18n initiative',
        'localization initiative',
        'i18n strategy',
        'localization strategy',
        'translation strategy',
        'i18n implementation',
        'localization implementation',
        'internationalization effort',
        'localization effort',
        'going global',
        'global expansion',
        'international markets',
        'international expansion',
        'support multiple languages',
        'support different languages',
        'multiple language',
        'different locales',
        'user language',
        'user locale',
        'locale detection',
        'language detection',
        'rtl support',
        'right-to-left',
        'bidirectional text',
    ]

    # False positive patterns - if these appear near "translate", it's likely NOT about i18n
    RFC_FALSE_POSITIVE_PATTERNS = [
        # Code/API translation (not human language)
        'translate coordinates',
        'translate position',
        'translate transform',
        'translate matrix',
        'translate x',
        'translate y',
        'translate z',
        'translate()',
        'translatex',
        'translatey',
        'translatez',
        'translate3d',
        'css translate',
        'svg translate',
        'canvas translate',
        'transform translate',
        # Compiler/parser translation
        'translate to bytecode',
        'translate to machine code',
        'translate to ir',
        'translate ast',
        'translate syntax',
        'translate code',
        'translate expression',
        'translate statement',
        'source to source',
        # Data format translation
        'translate json',
        'translate xml',
        'translate format',
        'translate schema',
        'translate data',
        'translate between formats',
        # Mathematical translation
        'translate vector',
        'translate point',
        'translate origin',
        'translate axis',
        'geometric translate',
        # Address/DNS translation
        'translate address',
        'translate domain',
        'nat translation',
        'address translation',
        'name translation',
        # Generic programming terms
        'translate method',
        'translate function',
        'translate call',
        'translate type',
        'translate value',
    ]

    # Context keywords that increase confidence of true i18n intent
    RFC_CONTEXT_BOOSTERS = [
        'language',
        'locale',
        'locales',
        'regional',
        'country',
        'countries',
        'international',
        'global',
        'worldwide',
        'users worldwide',
        'non-english',
        'foreign language',
        'native language',
        'mother tongue',
        'translation service',
        'translation platform',
        'translation management',
        'crowdin',
        'transifex',
        'lokalise',
        'phrase',
        'weblate',
        'gettext',
        'icu',
        'cldr',
        'unicode',
    ]

    # Minimum word context window for NLP filtering
    RFC_NLP_CONTEXT_WINDOW = 50  # words before and after keyword

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
        'pubspec.yaml',
        'Podfile',
        'build.gradle',
        'build.gradle.kts',
        'go.mod',
        'pom.xml',
        'pyproject.toml',
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
        'react-intl',                # React i18n framework - core library
        'i18next',                   # i18n framework core
        'formatjs',                  # ICU message formatting - building the foundation
        'vue-i18n',                  # Vue i18n framework - core library
        'next-i18next',              # Next.js i18n wrapper - core library
        'next-intl',                 # Modern Next.js i18n library
        '@lingui/core',              # LinguiJS core
        '@lingui/react',             # LinguiJS React bindings
        '@lingui/macro',             # LinguiJS Macro
        '@formatjs/intl',            # FormatJS Intl library
        'try-pseudo-localization',   # Pseudo-localization for UI layout testing
        'react-pseudo',              # Pseudo-localization for React
        'i18next-pseudo',            # Pseudo-localization for i18next
        'i18n-js',                   # JavaScript i18n library - general purpose
        'typesafe-i18n',             # TypeScript-first i18n library - type-safe translations

        # Backend / Other Languages (high-intent only)
        'django-babel', 'flask-babel', 'python-i18n',
        'rails-i18n', 'i18n-tasks',
        'go-i18n', 'messageformat',

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

    # =========================================================================
    # SMOKING GUN FORK DETECTION
    # =========================================================================
    # When a company FORKS these repositories (not just uses them as deps),
    # it's a strong signal they're customizing i18n infrastructure for their use case.
    # This is even stronger than dependency injection since they're modifying the source.
    SMOKING_GUN_FORK_REPOS = [
        'uppy',                      # File uploader with built-in i18n
        'react-intl',                # React i18n - forking = deep customization
        'i18next',                   # i18n framework - forking = extending
        'formatjs',                  # ICU message formatting
        'vue-i18n',                  # Vue i18n solution
        'next-i18next',              # Next.js i18n wrapper
        'react-i18next',             # React wrapper for i18next
        'lingui',                    # Lingui i18n framework  
        'typesafe-i18n',             # TypeScript-first i18n
        'polyglot.js',               # Airbnb's i18n library
        'ttag',                      # gettext-based i18n
        'rosetta',                   # i18n library
        'globalize',                 # jQuery Foundation i18n
        'messageformat',             # ICU MessageFormat
    ]

    # Keywords in package.json "scripts" that indicate i18n preparation
    BUILD_SCRIPT_I18N_KEYWORDS = [
        'locale',
        'i18n',
        'translation',
        'translations',
        'messages',
        'intl',
        'localize',
        'localization',
        'l10n',
        'extract-messages',
        'compile-messages',
    ]

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
        # Primary patterns (high confidence)
        'feature/i18n',
        'feature/l10n',
        'feature/localization',
        'feature/internationalization',
        'feature/translate',
        'feature/translation',
        'feature/translations',
        'feature/multi-language',
        'feature/multilingual',
        'chore/i18n',
        'chore/l10n',
        'chore/localization',
        'chore/translations',
        'add-translation-support',
        'refactor/extract-strings',
        'l10n-setup',
        'i18n-setup',
        # WIP/Work-in-progress patterns
        'wip/i18n',
        'wip/l10n',
        'wip/localization',
        'wip/translate',
        'wip/translations',
        'work/i18n',
        'work/localization',
        # Experimental/draft patterns
        'experimental/i18n',
        'experimental/localization',
        'draft/i18n',
        'draft/localization',
        'poc/i18n',
        'poc/localization',
        # Language-specific patterns
        'lang/',
        'language/',
        'locale/',
        'locales/',
        'translate/',
        'translation/',
        # Additional common patterns
        'i18n',
        'l10n',
        'localization',
        'internationalization',
        'translations',
        'intl',
        # Action-based patterns
        'add-i18n',
        'add-l10n',
        'add-localization',
        'setup-i18n',
        'setup-l10n',
        'enable-i18n',
        'enable-localization',
        'implement-i18n',
        'implement-l10n',
        # String extraction patterns
        'extract-strings',
        'string-extraction',
        'externalize-strings',
        'message-extraction',
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
        'next-intl': 'Next.js Internationalization',
        '@lingui/core': 'LinguiJS Implementation',
        '@lingui/react': 'LinguiJS React Bindings',
        '@lingui/macro': 'LinguiJS Macro',
        '@formatjs/intl': 'FormatJS Core',

        # Python
        'django-babel': 'Django Translation',
        'flask-babel': 'Flask Translation',
        'python-i18n': 'Python i18n',
        # Ruby
        'rails-i18n': 'Rails I18n',
        'i18n-tasks': 'Ruby i18n Tasks',
        # Go
        'go-i18n': 'Go Localization',
        'golang.org/x/text': 'Go Text Library',
        # Java/Kotlin
        'icu4j': 'Java ICU Library',
        'messageformat': 'Java MessageFormat',

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
        'next-intl': 'They are preparing their Next.js app for global markets. This is a modern, high-growth stack.',
        '@lingui/core': 'They are using LinguiJS, a powerful i18n library. They care about bundle size and performance.',
        '@lingui/react': 'They are integrating LinguiJS into their React components.',
        '@formatjs/intl': 'They are using the core FormatJS standards library. Highly technical implementation.',

        # Backend Libraries
        'django-babel': 'Found backend localization library. Infrastructure is active.',
        'flask-babel': 'Found backend localization library. Infrastructure is active.',
        'python-i18n': 'Found backend localization library. Infrastructure is active.',
        'babel': 'Found backend localization library. Infrastructure is active.',
        'rails-i18n': 'Found backend localization library. Infrastructure is active.',
        'i18n-tasks': 'Found backend localization library. Infrastructure is active.',
        'globalize': 'Found backend localization library. Infrastructure is active.',
        'go-i18n': 'Found backend localization library. Infrastructure is active.',
        'golang.org/x/text': 'Found backend localization library. Infrastructure is active.',
        'icu4j': 'Found backend localization library. Infrastructure is active.',
        'messageformat': 'Found backend localization library. Infrastructure is active.',

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

    # ============================================================
    # ENHANCED HEURISTICS - GLOBAL EXPANSION INTENT DETECTION
    # ============================================================
    # These additional heuristics identify companies with high intent
    # on global expansion through multiple signal dimensions.

    # ============================================================
    # HEURISTIC 1: JOB POSTING INTENT ANALYSIS
    # ============================================================
    # Detect job postings that indicate hiring for localization/i18n roles.
    # These signals indicate organizational commitment to global expansion.

    JOB_INTENT_KEYWORDS = [
        # Localization-specific roles
        'localization manager',
        'localization engineer',
        'localization specialist',
        'translation manager',
        'internationalization engineer',
        'i18n engineer',
        'globalization manager',
        'language program manager',
        'localization program manager',
        'translation coordinator',
        'localization coordinator',
        'localization lead',
        'i18n lead',
        'localization director',
        'head of localization',
        'vp of localization',
        # Regional expansion roles
        'regional marketing manager',
        'international marketing',
        'emea marketing',
        'apac marketing',
        'latam marketing',
        'international growth',
        'global growth manager',
        'international expansion',
        'regional sales manager',
        'country manager',
        'international operations',
        # Content localization
        'content localization',
        'multilingual content',
        'global content',
        'international content',
    ]

    # Job posting file patterns in repositories
    JOB_POSTING_FILES = [
        'JOBS.md',
        'CAREERS.md',
        'HIRING.md',
        'careers/',
        'jobs/',
        '.github/HIRING.md',
    ]

    # ============================================================
    # HEURISTIC 2: REGIONAL DOMAIN / ccTLD DETECTION
    # ============================================================
    # Detect country-code TLDs and regional domain patterns indicating
    # existing or planned international presence.

    REGIONAL_CCTLDS = {
        # Europe
        'de': 'Germany',
        'fr': 'France',
        'es': 'Spain',
        'it': 'Italy',
        'nl': 'Netherlands',
        'be': 'Belgium',
        'at': 'Austria',
        'ch': 'Switzerland',
        'pl': 'Poland',
        'se': 'Sweden',
        'no': 'Norway',
        'dk': 'Denmark',
        'fi': 'Finland',
        'pt': 'Portugal',
        'ie': 'Ireland',
        'uk': 'United Kingdom',
        'cz': 'Czech Republic',
        'hu': 'Hungary',
        'ro': 'Romania',
        'gr': 'Greece',
        # Asia-Pacific
        'jp': 'Japan',
        'cn': 'China',
        'kr': 'South Korea',
        'tw': 'Taiwan',
        'hk': 'Hong Kong',
        'sg': 'Singapore',
        'au': 'Australia',
        'nz': 'New Zealand',
        'in': 'India',
        'th': 'Thailand',
        'vn': 'Vietnam',
        'id': 'Indonesia',
        'my': 'Malaysia',
        'ph': 'Philippines',
        # Americas
        'ca': 'Canada',
        'mx': 'Mexico',
        'br': 'Brazil',
        'ar': 'Argentina',
        'cl': 'Chile',
        'co': 'Colombia',
        # Middle East / Africa
        'ae': 'UAE',
        'sa': 'Saudi Arabia',
        'il': 'Israel',
        'za': 'South Africa',
        'eg': 'Egypt',
        'tr': 'Turkey',
        # Russia & CIS
        'ru': 'Russia',
        'ua': 'Ukraine',
    }

    # Regional subdomain patterns
    REGIONAL_SUBDOMAIN_PATTERNS = [
        r'^(en|de|fr|es|it|pt|ja|zh|ko|ru|ar|nl|sv|no|da|fi|pl|tr|cs|hu|el|he)[-.]',
        r'[-.]?(emea|apac|latam|americas|europe|asia)[-.]?',
        r'^(eu|us|uk|au|jp|cn|kr|br|mx|in)[-.]',
    ]

    # ============================================================
    # HEURISTIC 3: HEADLESS CMS LOCALIZATION READINESS
    # ============================================================
    # Detect CMS platforms configured for multi-language content.

    HEADLESS_CMS_I18N_CONFIGS = {
        # Contentful
        'contentful': {
            'config_files': ['contentful.config.js', 'contentful.json', '.contentfulrc'],
            'i18n_indicators': ['locales', 'locale', 'fallbackLocale', 'defaultLocale', 'space'],
            'code_patterns': ['createClient', 'getEntry', 'getEntries', 'locale:'],
        },
        # Sanity
        'sanity': {
            'config_files': ['sanity.json', 'sanity.config.ts', 'sanity.config.js'],
            'i18n_indicators': ['i18n', 'languages', 'baseLanguage', 'document-internationalization'],
            'code_patterns': ['defineField', 'defineType', '@sanity/document-internationalization'],
        },
        # Strapi
        'strapi': {
            'config_files': ['config/plugins.js', 'config/plugins.ts', '.strapi'],
            'i18n_indicators': ['i18n', 'locales', 'defaultLocale'],
            'code_patterns': ['strapi-plugin-i18n', 'internationalization'],
        },
        # Prismic
        'prismic': {
            'config_files': ['prismicio.js', 'slicemachine.config.json'],
            'i18n_indicators': ['locales', 'defaultLocale', 'masterLocale'],
            'code_patterns': ['@prismicio/client', 'createClient'],
        },
        # Storyblok
        'storyblok': {
            'config_files': ['storyblok.config.js'],
            'i18n_indicators': ['languages', 'defaultLanguage', 'locales'],
            'code_patterns': ['@storyblok/js', '@storyblok/react'],
        },
        # DatoCMS
        'datocms': {
            'config_files': ['dato.config.js', 'datocms.json'],
            'i18n_indicators': ['locales', 'allLocales', 'fallbackLocales'],
            'code_patterns': ['datocms-client', 'buildClient'],
        },
    }

    # ============================================================
    # HEURISTIC 4: MULTI-CURRENCY PAYMENT INFRASTRUCTURE
    # ============================================================
    # Detect payment processing configured for multiple currencies/regions.

    PAYMENT_I18N_LIBRARIES = [
        # Stripe multi-currency
        'stripe',
        '@stripe/stripe-js',
        '@stripe/react-stripe-js',
        # PayPal international
        '@paypal/react-paypal-js',
        'paypal-rest-sdk',
        # Multi-currency specific
        'currency.js',
        'dinero.js',
        'money.js',
        'accounting.js',
        'currency-formatter',
        # Regional payment providers
        'razorpay',  # India
        'paytm',     # India
        'alipay',    # China
        'wechat-pay', # China
        'klarna',    # Europe
        'adyen',     # Global
        'mollie',    # Europe
        'mercadopago', # LATAM
        'payu',      # LATAM/EMEA
    ]

    PAYMENT_MULTI_CURRENCY_PATTERNS = [
        'currency', 'currencies', 'multi-currency', 'multicurrency',
        'exchange_rate', 'exchangeRate', 'fx_rate', 'fxRate',
        'price_in_', 'priceIn', 'local_currency', 'localCurrency',
        'currency_code', 'currencyCode', 'iso_currency', 'isoCurrency',
        'formatCurrency', 'convertCurrency', 'parseCurrency',
    ]

    # ============================================================
    # HEURISTIC 5: TIMEZONE & DATE FORMATTING LIBRARIES
    # ============================================================
    # Libraries that handle timezone and locale-aware date formatting
    # indicate preparation for global users.

    TIMEZONE_I18N_LIBRARIES = [
        # JavaScript timezone libraries
        'moment-timezone',
        'date-fns-tz',
        'luxon',
        'dayjs',
        '@date-io/luxon',
        '@date-io/dayjs',
        'spacetime',
        'timezone-support',
        'tz-offset',
        # Full ICU data (heavy i18n intent)
        'full-icu',
        'intl',
        '@formatjs/intl-datetimeformat',
        '@formatjs/intl-numberformat',
        '@formatjs/intl-relativetimeformat',
        '@formatjs/intl-pluralrules',
        '@formatjs/intl-listformat',
        '@formatjs/intl-displaynames',
        # Python
        'pytz',
        'python-dateutil',
        'babel',  # Also handles locale formatting
        'arrow',
        # Go
        'time',  # Standard library, but check for LoadLocation
    ]

    TIMEZONE_CODE_PATTERNS = [
        'timezone', 'timeZone', 'time_zone',
        'userTimezone', 'user_timezone',
        'localTimezone', 'local_timezone',
        'detectTimezone', 'detect_timezone',
        'Intl.DateTimeFormat',
        'toLocaleString', 'toLocaleDateString', 'toLocaleTimeString',
        'formatRelative', 'formatDistance',
        'LoadLocation',  # Go timezone
        'tz_localize', 'tz_convert',  # Python pandas
    ]

    # ============================================================
    # HEURISTIC 6: CI/CD LOCALIZATION PIPELINE DETECTION
    # ============================================================
    # GitHub Actions and CI configs that integrate with translation platforms.

    CI_LOCALIZATION_PATTERNS = {
        # GitHub Actions workflow indicators
        'github_actions': {
            'files': ['.github/workflows/*.yml', '.github/workflows/*.yaml'],
            'patterns': [
                'crowdin', 'lokalise', 'phrase', 'transifex', 'weblate',
                'poeditor', 'pontoon', 'smartling', 'memsource',
                'upload-translations', 'download-translations',
                'sync-translations', 'pull-translations', 'push-translations',
                'i18n-sync', 'l10n-sync', 'translation-sync',
                'crowdin/github-action', 'lokalise/lokalise-cli-action',
            ],
        },
        # Translation platform config files
        'platform_configs': [
            'crowdin.yml', 'crowdin.yaml', '.crowdin.yml',
            'lokalise.yml', 'lokalise.yaml', '.lokalise.yml',
            'phrase.yml', '.phrase.yml', '.phraseapp.yml',
            'transifex.yml', '.tx/config',
            'weblate.yaml', '.weblate',
        ],
        # CI integration patterns
        'ci_scripts': [
            'scripts/translations',
            'scripts/i18n',
            'scripts/l10n',
            'scripts/sync-translations',
            'scripts/pull-translations',
            'scripts/push-translations',
        ],
    }

    # ============================================================
    # HEURISTIC 7: LEGAL/COMPLIANCE DOCUMENTATION SIGNALS
    # ============================================================
    # Regional compliance documents often precede market expansion.

    COMPLIANCE_FILES = [
        # Privacy policies
        'PRIVACY.md', 'PRIVACY_POLICY.md', 'privacy-policy.md',
        'privacy/', 'legal/privacy',
        # GDPR specific
        'GDPR.md', 'gdpr.md', 'gdpr-compliance.md',
        'docs/gdpr', 'legal/gdpr',
        'data-processing-agreement.md', 'dpa.md',
        # Regional compliance
        'CCPA.md', 'ccpa.md',  # California
        'LGPD.md', 'lgpd.md',  # Brazil
        'PDPA.md', 'pdpa.md',  # Thailand/Singapore
        'POPIA.md', 'popia.md', # South Africa
        'APPI.md', 'appi.md',  # Japan
        # Terms of service variations
        'legal/', 'docs/legal/',
        'terms/', 'tos/',
    ]

    COMPLIANCE_KEYWORDS = [
        # GDPR (Europe)
        'gdpr', 'general data protection regulation',
        'data processing agreement', 'dpa',
        'data protection officer', 'dpo',
        'right to be forgotten', 'data portability',
        'privacy shield', 'standard contractual clauses', 'scc',
        # Regional regulations
        'ccpa', 'california consumer privacy',  # California
        'lgpd', 'lei geral de proteção de dados',  # Brazil
        'pdpa', 'personal data protection act',  # Singapore/Thailand
        'popia', 'protection of personal information',  # South Africa
        'appi', 'act on protection of personal information',  # Japan
        'pipl', 'personal information protection law',  # China
        # International indicators
        'international data transfer',
        'cross-border data',
        'data localization',
        'regional compliance',
        'multi-jurisdiction',
    ]

    # ============================================================
    # HEURISTIC 8: SOCIAL PROOF / MULTI-REGION META TAGS
    # ============================================================
    # OpenGraph and social meta tags configured for multiple regions.

    SOCIAL_MULTI_REGION_PATTERNS = {
        'meta_tags': [
            'og:locale',
            'og:locale:alternate',
            'twitter:site',  # Check for regional variants
            'al:android:app_name',  # App Links regional
            'al:ios:app_name',
        ],
        'structured_data': [
            'availableLanguage',
            'inLanguage',
            'contentLocation',
            'areaServed',
            '@graph',  # JSON-LD with multiple locales
        ],
        'regional_social_handles': [
            '_de', '_fr', '_es', '_it', '_pt', '_jp', '_cn', '_kr',
            '_brazil', '_mexico', '_uk', '_india', '_apac', '_emea', '_latam',
            'DE', 'FR', 'ES', 'IT', 'PT', 'JP', 'CN', 'KR',
        ],
    }

    # ============================================================
    # HEURISTIC 9: CONTENT FRESHNESS & UPDATE VELOCITY
    # ============================================================
    # Track commit patterns on locale-related files to identify
    # active localization work vs dormant infrastructure.

    LOCALE_FILE_PATTERNS = [
        r'locales?/.*\.(json|ya?ml|properties|po|pot|xliff|xlf)$',
        r'i18n/.*\.(json|ya?ml|properties|po|pot|xliff|xlf)$',
        r'translations?/.*\.(json|ya?ml|properties|po|pot|xliff|xlf)$',
        r'lang(uages)?/.*\.(json|ya?ml|properties|po|pot|xliff|xlf)$',
        r'l10n/.*\.(json|ya?ml|properties|po|pot|xliff|xlf)$',
        r'messages?/.*\.(json|ya?ml|properties|po|pot|xliff|xlf)$',
    ]

    # Velocity thresholds (commits in last 90 days)
    LOCALE_VELOCITY_THRESHOLDS = {
        'high_activity': 20,    # Very active localization work
        'medium_activity': 10,  # Moderate localization activity
        'low_activity': 3,      # Some localization activity
        'dormant': 0,           # No recent activity
    }

    # Lookback period for velocity calculation (days)
    LOCALE_VELOCITY_LOOKBACK_DAYS = 90

    # ============================================================
    # HEURISTIC 10: API INTERNATIONAL ENDPOINT DETECTION
    # ============================================================
    # Detect API patterns that indicate multi-region architecture.

    API_INTERNATIONAL_PATTERNS = {
        'endpoint_patterns': [
            r'/api/v\d+/(regions?|countries|locales?|languages?)',
            r'/api/v\d+/\w+/(region|country|locale|language)',
            r'\{(region|country|locale|lang(uage)?)\}',
            r'/(en|de|fr|es|it|pt|ja|zh|ko|ru)/',
            r'[?&](region|country|locale|lang(uage)?)=',
        ],
        'config_patterns': [
            'regions:', 'countries:', 'locales:', 'languages:',
            'availableRegions', 'supportedLocales', 'supportedLanguages',
            'allowedCountries', 'enabledLocales',
            'regionConfig', 'localeConfig', 'countryConfig',
        ],
        'code_patterns': [
            'getRegion', 'getLocale', 'getCountry', 'getLanguage',
            'setRegion', 'setLocale', 'setCountry', 'setLanguage',
            'detectRegion', 'detectLocale', 'detectCountry', 'detectLanguage',
            'switchRegion', 'switchLocale', 'switchCountry', 'switchLanguage',
            'regionMiddleware', 'localeMiddleware',
            'Accept-Language',  # HTTP header handling
            'Content-Language',
            'geo.country', 'cf.country',  # CDN geolocation
        ],
        'openapi_i18n_fields': [
            'x-region', 'x-locale', 'x-country', 'x-language',
            'Accept-Language',
            'Content-Language',
        ],
    }

    # OpenAPI/Swagger file patterns
    OPENAPI_FILES = [
        'openapi.yaml', 'openapi.yml', 'openapi.json',
        'swagger.yaml', 'swagger.yml', 'swagger.json',
        'api.yaml', 'api.yml', 'api.json',
        'docs/api/', 'api-docs/',
    ]

    # ============================================================
    # ENHANCED INTENT SCORE WEIGHTS
    # ============================================================
    # Updated weights including new heuristic signals

    ENHANCED_INTENT_WEIGHTS = {
        # Original signals
        'rfc_discussion_high': 30,
        'rfc_discussion_medium': 15,
        'dependency_injection': 40,
        'ghost_branch': 25,
        'documentation_intent_high': 20,
        'documentation_intent_medium': 10,
        # New enhanced signals
        'job_posting_intent': 35,          # High - organizational commitment
        'regional_domain_detection': 25,   # Medium-high - existing presence
        'headless_cms_i18n': 30,           # High - content infrastructure
        'payment_multi_currency': 30,      # High - commerce infrastructure
        'timezone_library': 15,            # Medium - UX preparation
        'ci_localization_pipeline': 35,    # High - active automation
        'compliance_documentation': 20,    # Medium - legal preparation
        'social_multi_region': 15,         # Medium - marketing preparation
        'locale_velocity_high': 25,        # High - active work
        'locale_velocity_medium': 15,      # Medium - some work
        'api_international': 25,           # Medium-high - backend preparation
    }
