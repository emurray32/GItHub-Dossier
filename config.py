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

    # Noise filters
    NOISE_PATTERNS = [
        'language version', 'kotlin version', 'java version',
        'bump version', 'update version', 'gradle version'
    ]
