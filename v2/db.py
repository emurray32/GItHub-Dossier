"""
V2 Database Helpers — thin wrapper on top of the existing database.py connection layer.

All v2 services import from here instead of using database.py directly.
This keeps the coupling clean and provides v2-specific convenience functions.
"""
import json
import logging
from contextlib import contextmanager
from database import db_connection as _db_connection, _insert_returning_id

logger = logging.getLogger(__name__)


def db_connection():
    """Return the standard database context manager from database.py."""
    return _db_connection()


def insert_returning_id(cursor, sql, params):
    """Insert a row and return the new id. Works on both PG and SQLite."""
    return _insert_returning_id(cursor, sql, params)


def row_to_dict(row):
    """Convert a database row to a plain dict.

    Handles both psycopg2 RealDictRow (already dict-like) and sqlite3.Row.
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    # sqlite3.Row or tuple — convert via keys() if available
    if hasattr(row, 'keys'):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def rows_to_dicts(rows):
    """Convert a list of database rows to a list of plain dicts."""
    return [row_to_dict(r) for r in rows] if rows else []


def safe_json_loads(value, default=None):
    """Parse a JSON string, returning default on failure."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value  # Already parsed (JSONB on PG)
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def safe_json_dumps(value):
    """Serialize a value to JSON string for storage."""
    if value is None:
        return None
    if isinstance(value, str):
        return value  # Already a string
    return json.dumps(value)
