"""
Configuration settings for Lead Machine.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration."""

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
    COMMITS_PER_REPO = 100  # Last N commits to scan
    PR_LOOKBACK_DAYS = 90   # PRs from last N days
    COMMIT_LOOKBACK_DAYS = 90  # Commits from last N days

    # Priority keywords for repo selection
    PRIORITY_KEYWORDS = [
        'web', 'mobile', 'ios', 'android', 'frontend', 'docs',
        'app', 'client', 'site', 'platform', 'ui', 'portal'
    ]

    # I18n signal patterns
    I18N_FILE_PATTERNS = [
        'locales/', 'locale/', 'i18n/', 'translations/', 'lang/',
        'languages/', 'l10n/', '.lproj/', 'strings/', 'messages/'
    ]

    I18N_PR_KEYWORDS = [
        'localization', 'localisation', 'i18n', 'l10n', 'translation',
        'translate', 'language', 'locale', 'international', 'multilingual',
        'rtl', 'ltr', 'hreflang', 'intl'
    ]

    # Keywords that indicate high-value technical pain (The "Why")
    PAIN_KEYWORDS = [
        'refactor', 'migrate', 'rewrite', 'broken', 'fix', 
        'sync', 'missing', 'crash', 'fail', 'manual', 'script',
        'deprecated', 'legacy', 'debt', 'slow', 'timeout'
    ]

    # Noise filters to ignore low-value activity
    NOISE_PATTERNS = [
        'language version', 'kotlin version', 'java version',
        'bump version', 'update version', 'gradle version',
        'typo', 'grammar', 'spelling', 'readme', 'logo', 'icon',
        'formatting', 'prettier', 'lint', 'minor', 'style'
    ]

    # ============================================================
    # HIGH-INTENT SALES INTELLIGENCE CONFIGURATION
    # ============================================================

    # Dependency files to scan (top 5 repos only for speed)
    DEPENDENCY_FILES = [
        'package.json', 'Gemfile', 'requirements.txt', 'go.mod',
        'mix.exs', 'composer.json', 'pom.xml', 'build.gradle'
    ]

    # i18n libraries with framework mapping (for tech_stack_hook)
    I18N_LIBRARIES = {
        'react-intl': 'React',
        'i18next': 'JS/React',
        'react-i18next': 'React',
        'vue-i18n': 'Vue',
        'ngx-translate': 'Angular',
        'formatjs': 'JS/React',
        'lingui': 'React',
        '@lingui/core': 'React',
        'django-modeltranslation': 'Django',
        'django-parler': 'Django',
        'laravel-localization': 'Laravel',
        'go-i18n': 'Go',
        'ruby-i18n': 'Ruby',
        'i18n-js': 'Ruby/Rails',
        'next-intl': 'Next.js',
        'next-i18next': 'Next.js',
        'typesafe-i18n': 'TypeScript',
        'fluent': 'Mozilla Fluent',
        'messageformat': 'JS',
        'polyglot': 'JS',
        'ttag': 'JS',
        'gettext': 'Python/C',
    }

    # Competitor config files (HIGH INTENT - they're already using a TMS!)
    COMPETITOR_CONFIGS = [
        'lokalise.yaml', 'lokalise.yml', '.lokalise.yml',
        'crowdin.yml', 'crowdin.yaml', '.crowdin.yml',
        'smartling-config.json', '.smartling.json',
        '.transifexrc', 'transifex.yml', '.tx/config',
        'phraseapp.yml', '.phraseapp.yml', 'phrase.yml',
        'locize.json', '.locize',
        'applanga.yml', 'applanga.json',
    ]

    # TMS competitors (for detection in dependencies)
    TMS_COMPETITORS = [
        'transifex', 'smartling', 'phrase', 'lokalise', 'crowdin',
        'weblate', 'pontoon', 'locize', 'memsource', 'memoq',
        'applanga', 'poeditor', 'oneskyapp', 'loco', 'tolgee'
    ]

    # Frustration keywords regex (for mining pain points from commits)
    # Pattern: (action word) + up to 20 chars + (localization term)
    FRUSTRATION_REGEX = r'(fix|broken|missing|sync|conflict|manual|update|revert|hotfix|urgent).{0,20}(translation|locale|string|key|i18n|l10n|locali[sz]ation)'

    # Bot accounts to exclude from developer-as-translator metric
    BOT_ACCOUNTS = [
        'dependabot', 'dependabot[bot]', 'github-actions', 'github-actions[bot]',
        'renovate', 'renovate[bot]', 'semantic-release-bot', 'greenkeeper',
        'snyk-bot', 'codecov', 'codecov[bot]', 'vercel[bot]', 'netlify[bot]',
        'crowdin-bot', 'lokalise-bot', 'transifex-bot', 'phrase-bot',
        'weblate', 'l10n-bot', 'translation-bot', 'bot'
    ]

    # Common locale directory paths for inventory scan
    LOCALE_PATHS = [
        'locales', 'locale', 'i18n', 'translations', 'lang',
        'languages', 'l10n', 'src/locales', 'public/locales',
        'assets/locales', 'resources/lang', 'src/i18n', 'app/locales',
        'lib/locales', 'config/locales', 'static/locales'
    ]

    # I18n signal patterns (file paths)
    I18N_FILE_PATTERNS = [
        'locales/', 'locale/', 'i18n/', 'translations/', 'lang/',
        'languages/', 'l10n/', '.lproj/', 'strings/', 'messages/'
    ]

    # Locale code to region mapping for geo-spatial inference
    LOCALE_TO_REGION = {
        # LATAM
        'mx': 'Mexico (LATAM)', 'br': 'Brazil (LATAM)', 'ar': 'Argentina (LATAM)',
        'co': 'Colombia (LATAM)', 'cl': 'Chile (LATAM)', 'pe': 'Peru (LATAM)',
        # DACH
        'de': 'Germany (DACH)', 'at': 'Austria (DACH)', 'ch': 'Switzerland (DACH)',
        # Europe
        'fr': 'France', 'es': 'Spain', 'pt': 'Portugal', 'it': 'Italy',
        'nl': 'Netherlands', 'pl': 'Poland', 'se': 'Sweden', 'no': 'Norway',
        'dk': 'Denmark', 'fi': 'Finland',
        # APAC
        'jp': 'Japan (APAC)', 'kr': 'Korea (APAC)', 'cn': 'China (APAC)',
        'tw': 'Taiwan (APAC)', 'hk': 'Hong Kong (APAC)', 'sg': 'Singapore (APAC)',
        'in': 'India (APAC)', 'id': 'Indonesia (APAC)', 'th': 'Thailand (APAC)',
        'vn': 'Vietnam (APAC)', 'au': 'Australia (APAC)', 'nz': 'New Zealand (APAC)',
        # MENA
        'sa': 'Saudi Arabia (MENA)', 'ae': 'UAE (MENA)', 'eg': 'Egypt (MENA)',
        'il': 'Israel (MENA)', 'tr': 'Turkey (MENA)',
        # Other
        'ru': 'Russia', 'ua': 'Ukraine', 'za': 'South Africa', 'ng': 'Nigeria', 'ke': 'Kenya',
    }

    # Greenfield detection threshold
    GREENFIELD_STAR_THRESHOLD = 1000

    # Reviewer bottleneck threshold (percentage)
    REVIEWER_BOTTLENECK_THRESHOLD = 0.80  # 80%

    # ============================================================
    # SMART FILTERING - NOISE FILE EXTENSIONS
    # ============================================================
    # File extensions to IGNORE when scanning for localization signals
    # These generate false positives (e.g., README mentioning "translation")
    NOISE_FILE_EXTENSIONS = [
        '.md', '.txt', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.ico',
        '.lock', '.sum', '.resolved', '.log', '.map', '.min.js', '.min.css',
        '.d.ts', '.test.js', '.test.ts', '.spec.js', '.spec.ts',
        '.snap', '.fixture', '.mock'
    ]

    # Noise filenames to completely ignore
    NOISE_FILENAMES = [
        'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'Gemfile.lock',
        'poetry.lock', 'composer.lock', 'Cargo.lock', 'go.sum',
        'README.md', 'CHANGELOG.md', 'LICENSE', 'LICENSE.md', 'CONTRIBUTING.md'
    ]

    # ============================================================
    # SEO & MARKETING INTENT DETECTION
    # ============================================================
    # Files that indicate marketing/SEO international strategy
    SEO_CONFIG_FILES = [
        'next.config.js', 'next.config.mjs', 'next.config.ts',
        'nuxt.config.js', 'nuxt.config.ts',
        'sitemap.xml', 'robots.txt',
        'vercel.json', 'netlify.toml'
    ]

    # ============================================================
    # CONFIDENCE SCORE WEIGHTS
    # ============================================================
    CONFIDENCE_WEIGHTS = {
        'locale_folder': 10,           # +10 pts for locales/ folder
        'i18n_pr_recent': 20,          # +20 pts for i18n PR in last 30 days
        'new_language_added': 50,      # +50 pts for new language added
        'competitor_config': 40,       # +40 pts for competitor TMS config
        'tms_in_deps': 30,             # +30 pts for TMS in dependencies
        'frustration_signal': 15,      # +15 pts per frustration signal (max 3)
        'human_translator_pain': 25,   # +25 pts if >70% human translation edits
        'i18n_library': 10,            # +10 pts per i18n library detected
        'greenfield_opportunity': 35,  # +35 pts for greenfield (mature + no i18n)
        'market_expansion': 20,        # +20 pts for detected market expansion
        'reviewer_bottleneck': 20,     # +20 pts for reviewer bottleneck
        'seo_i18n_config': 30,         # +30 pts for SEO i18n config (next.config)
    }

    # ============================================================
    # COMPLIANCE & FORENSIC EXTERNAL SEARCH CONFIG
    # ============================================================

    # Compliance and Privacy patterns (Global vs Localized)
    COMPLIANCE_FILE_PATTERNS = [
        'PRIVACY', 'PRIVACY-POLICY', 'GDPR', 'LGPD', 'PIPL', 'CCPA',
        'TERMS-OF-SERVICE', 'TOS', 'LEGAL', 'COMPLIANCE', 'TRUST', 'SECURITY'
    ]

    # External Forensic Search (Stack Overflow queries)
    # {company} will be replaced during scan
    SO_SEARCH_QUERIES = [
        '"{company}" localization issue site:stackoverflow.com',
        '"{company}" i18n error site:stackoverflow.com',
        '"{company}" translation sync site:stackoverflow.com',
        '"{company}" react-intl error site:stackoverflow.com',
        '"{company}" i18next site:stackoverflow.com'
    ]
