"""
Input validation helpers for GitHub Dossier.

Provides validation functions for user-supplied data before
it reaches the database or external APIs. Each function returns
a (is_valid, cleaned_value_or_error) tuple.
"""
import re
from typing import Tuple, Union

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_COMPANY_NAME_LENGTH = 200
MAX_GITHUB_ORG_LENGTH = 39  # GitHub's limit
MAX_EMAIL_LENGTH = 254       # RFC 5321
MAX_NOTES_LENGTH = 5000
MAX_SEARCH_QUERY_LENGTH = 200
MAX_URL_LENGTH = 2048
MAX_APOLLO_ID_LENGTH = 100

# GitHub org/user name pattern: alphanumeric + hyphens, no leading/trailing hyphen
_GITHUB_ORG_RE = re.compile(r'^[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?$')

# Company name: letters, numbers, spaces, hyphens, dots, &, commas
_COMPANY_NAME_RE = re.compile(r'^[\w\s\.\,\-\&\(\)\'/\+\!\#]+$', re.UNICODE)

# Simple email validation (not exhaustive, but catches obvious junk)
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

# Apollo ID pattern: alphanumeric + hyphens + underscores
_APOLLO_ID_RE = re.compile(r'^[a-zA-Z0-9\-_]+$')

# URL pattern: basic check for http/https
_URL_RE = re.compile(r'^https?://[^\s<>"{}|\\^`\[\]]+$')

# SQL injection patterns to block
_SQL_INJECTION_RE = re.compile(
    r'(DROP\s+TABLE|UNION\s+SELECT|INSERT\s+INTO|DELETE\s+FROM|UPDATE\s+\w+\s+SET|'
    r'ALTER\s+TABLE|CREATE\s+TABLE|EXEC\s*\(|EXECUTE\s|xp_|sp_)',
    re.IGNORECASE
)

# Script tag pattern
_SCRIPT_TAG_RE = re.compile(r'<\s*/?script[^>]*>', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------

def validate_company_name(name: str) -> Tuple[bool, str]:
    """Validate and sanitize a company name.

    Blocks <script> tags and SQL injection patterns.

    Returns:
        (True, cleaned_name) on success, (False, error_message) on failure.
    """
    if not name or not isinstance(name, str):
        return False, 'Company name is required'

    name = name.strip()
    if not name:
        return False, 'Company name cannot be empty'

    if len(name) > MAX_COMPANY_NAME_LENGTH:
        return False, f'Company name too long (max {MAX_COMPANY_NAME_LENGTH} chars)'

    # Block script tags
    if _SCRIPT_TAG_RE.search(name):
        return False, 'Company name contains disallowed content'

    # Block SQL injection patterns
    if _SQL_INJECTION_RE.search(name):
        return False, 'Company name contains disallowed content'

    if not _COMPANY_NAME_RE.match(name):
        return False, 'Company name contains invalid characters'

    return True, name


def validate_github_org(org: str) -> Tuple[bool, str]:
    """Validate a GitHub organization or user login.

    GitHub pattern: letters, numbers, hyphens, max 39 chars,
    can't start/end with hyphen.

    Returns:
        (True, cleaned_org) on success, (False, error_message) on failure.
    """
    if not org or not isinstance(org, str):
        return False, 'GitHub org is required'

    org = org.strip()
    if not org:
        return False, 'GitHub org cannot be empty'

    if len(org) > MAX_GITHUB_ORG_LENGTH:
        return False, f'GitHub org too long (max {MAX_GITHUB_ORG_LENGTH} chars)'

    if not _GITHUB_ORG_RE.match(org):
        return False, 'Invalid GitHub org name (letters, numbers, hyphens only; no leading/trailing hyphen)'

    return True, org


def validate_email(email: str) -> Tuple[bool, str]:
    """Validate an email address (RFC-compliant format, max 254 chars).

    Returns:
        (True, cleaned_email) on success, (False, error_message) on failure.
    """
    if not email or not isinstance(email, str):
        return False, 'Email is required'

    email = email.strip().lower()
    if not email:
        return False, 'Email cannot be empty'

    if len(email) > MAX_EMAIL_LENGTH:
        return False, f'Email too long (max {MAX_EMAIL_LENGTH} chars)'

    if not _EMAIL_RE.match(email):
        return False, 'Invalid email address format'

    return True, email


def validate_apollo_id(apollo_id: str) -> Tuple[bool, str]:
    """Validate an Apollo person/contact ID.

    Alphanumeric + hyphens, max 100 chars.

    Returns:
        (True, cleaned_id) on success, (False, error_message) on failure.
    """
    if not apollo_id or not isinstance(apollo_id, str):
        return False, 'Apollo ID is required'

    apollo_id = apollo_id.strip()
    if len(apollo_id) > MAX_APOLLO_ID_LENGTH:
        return False, 'Apollo ID too long'

    if not _APOLLO_ID_RE.match(apollo_id):
        return False, 'Invalid Apollo ID format'

    return True, apollo_id


def validate_url(url: str) -> Tuple[bool, str]:
    """Validate a URL (must be http/https, reasonable length).

    Returns:
        (True, cleaned_url) on success, (False, error_message) on failure.
    """
    if not url or not isinstance(url, str):
        return False, 'URL is required'

    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        return False, f'URL too long (max {MAX_URL_LENGTH} chars)'

    if not _URL_RE.match(url):
        return False, 'Invalid URL (must start with http:// or https://)'

    return True, url


def validate_search_query(query: str) -> Tuple[bool, str]:
    """Validate a search/filter query string.

    Max 200 chars, strip dangerous chars.

    Returns:
        (True, cleaned_query) on success, (False, error_message) on failure.
    """
    if not query or not isinstance(query, str):
        return False, 'Search query is required'

    query = query.strip()
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        return False, f'Search query too long (max {MAX_SEARCH_QUERY_LENGTH} chars)'

    # Strip potentially dangerous characters
    query = re.sub(r'[<>]', '', query)

    return True, query


def validate_notes(notes: str) -> Tuple[bool, str]:
    """Validate free-text notes.

    Max 5000 chars, strip script tags.

    Returns:
        (True, cleaned_notes) on success, (False, error_message) on failure.
    """
    if not isinstance(notes, str):
        return False, 'Notes must be a string'

    notes = notes.strip()
    if len(notes) > MAX_NOTES_LENGTH:
        return False, f'Notes too long (max {MAX_NOTES_LENGTH} chars)'

    # Strip script tags
    notes = _SCRIPT_TAG_RE.sub('', notes)

    return True, notes


def validate_tier(tier) -> Tuple[bool, Union[int, str]]:
    """Validate a tier value (must be int 0-4).

    Returns:
        (True, tier_int) on success, (False, error_message) on failure.
    """
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        return False, 'Tier must be an integer'

    if tier not in (0, 1, 2, 3, 4):
        return False, 'Tier must be between 0 and 4'

    return True, tier


def validate_positive_int(value, name: str = 'value', max_val: int = None) -> Tuple[bool, Union[int, str]]:
    """Validate that a value is a positive integer.

    Returns:
        (True, int_value) on success, (False, error_message) on failure.
    """
    try:
        val = int(value)
    except (TypeError, ValueError):
        return False, f'{name} must be an integer'

    if val < 0:
        return False, f'{name} must be non-negative'

    if max_val is not None and val > max_val:
        return False, f'{name} exceeds maximum ({max_val})'

    return True, val


def sanitize_for_log(value: str, max_length: int = 200) -> str:
    """Sanitize a string for safe inclusion in log messages.

    Strips newlines and carriage returns to prevent log injection.
    Truncates to max_length.
    """
    if not isinstance(value, str):
        value = str(value)
    value = value.replace('\n', ' ').replace('\r', ' ')
    if len(value) > max_length:
        value = value[:max_length] + '...'
    return value
