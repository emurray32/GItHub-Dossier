"""
Configuration for GitHub Dossier — Lightweight BDR Sequencing Tool.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration."""

    # Flask
    _secret = os.getenv('FLASK_SECRET_KEY')
    SECRET_KEY = _secret if _secret else os.urandom(24).hex()
    if not _secret:
        import warnings
        warnings.warn(
            "FLASK_SECRET_KEY is not set. A random key will be generated on every restart, "
            "invalidating all sessions. Set FLASK_SECRET_KEY in your .env file.",
            RuntimeWarning,
            stacklevel=2,
        )
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'

    # API Key for endpoint authentication (opt-in: if not set, auth is disabled)
    API_KEY = os.getenv('DOSSIER_API_KEY', '')

    # Database
    DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'lead_machine.db')
    DATABASE_URL = os.getenv('DATABASE_URL')  # PostgreSQL connection string; overrides SQLite when set
