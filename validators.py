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


def validate_sort_direction(direction: str) -> Tuple[bool, str]:
    """Validate a sort direction (must be 'asc' or 'desc').

    Returns:
        (True, cleaned_direction) on success, (False, error_message) on failure.
    """
    if not direction or not isinstance(direction, str):
        return True, 'asc'  # default

    direction = direction.strip().lower()
    if direction not in ('asc', 'desc'):
        return False, 'Sort direction must be asc or desc'

    return True, direction


def validate_scope(scope: str, allowed: tuple) -> Tuple[bool, str]:
    """Validate a scope/enum value against a whitelist.

    Returns:
        (True, cleaned_scope) on success, (False, error_message) on failure.
    """
    if not scope or not isinstance(scope, str):
        return False, 'Scope is required'

    scope = scope.strip().lower()
    if scope not in allowed:
        return False, f'Invalid scope. Allowed: {", ".join(allowed)}'

    return True, scope


def validate_csv_upload(file_storage, max_size_mb: int = 5) -> tuple:
    """Validate a CSV file upload for campaign account import.

    Args:
        file_storage: werkzeug FileStorage object from request.files
        max_size_mb: maximum file size in MB

    Returns:
        (is_valid, result) where result is error message string on failure,
        or dict {'valid_rows': [...], 'rejected_rows': [...]} on success.
    """
    import csv
    import io

    if not file_storage or not file_storage.filename:
        return False, 'No file provided'

    filename = file_storage.filename.lower()
    if not filename.endswith('.csv'):
        return False, 'File must be a .csv file'

    # Read file content
    try:
        content = file_storage.read()
        file_storage.seek(0)  # Reset for potential re-read
    except Exception as e:
        return False, f'Failed to read file: {str(e)[:100]}'

    if len(content) > max_size_mb * 1024 * 1024:
        return False, f'File exceeds {max_size_mb}MB limit'

    if len(content) == 0:
        return False, 'File is empty'

    # Parse CSV
    try:
        text = content.decode('utf-8-sig')  # Handle BOM
    except UnicodeDecodeError:
        try:
            text = content.decode('latin-1')
        except UnicodeDecodeError:
            return False, 'File encoding not supported (use UTF-8)'

    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    headers_lower = [h.lower().strip() for h in headers]

    # Check required columns
    has_company = any(h in ('company_name', 'company', 'name', 'account_name') for h in headers_lower)
    has_domain = any(h in ('website', 'domain', 'website_url', 'company_website', 'url') for h in headers_lower)

    if not has_company:
        return False, 'CSV must have a company_name column (also accepts: company, name, account_name)'
    if not has_domain:
        return False, 'CSV must have a website/domain column (also accepts: website_url, company_website, url)'

    # Map headers to canonical names
    company_col = next(h for h, hl in zip(headers, headers_lower) if hl in ('company_name', 'company', 'name', 'account_name'))
    domain_col = next(h for h, hl in zip(headers, headers_lower) if hl in ('website', 'domain', 'website_url', 'company_website', 'url'))

    valid_rows = []
    rejected_rows = []

    for i, row in enumerate(reader, start=2):  # start=2 because row 1 is header
        company_name = (row.get(company_col) or '').strip()
        domain = (row.get(domain_col) or '').strip()

        if not company_name:
            rejected_rows.append({'row': i, 'company_name': '', 'reason': 'Missing company name'})
            continue

        if not domain:
            rejected_rows.append({'row': i, 'company_name': company_name, 'reason': 'Missing website/domain'})
            continue

        # Clean domain: strip protocol and trailing slash
        clean_domain = domain.lower().replace('https://', '').replace('http://', '').rstrip('/')

        # Build row dict with all columns
        parsed = {
            'company_name': company_name,
            'website': clean_domain,
        }
        # Map remaining columns
        for header in headers:
            hl = header.lower().strip()
            if hl in ('company_name', 'company', 'name', 'account_name'):
                continue  # Already mapped
            if hl in ('website', 'domain', 'website_url', 'company_website', 'url'):
                continue  # Already mapped
            val = (row.get(header) or '').strip()
            if val:
                parsed[hl] = val

        valid_rows.append(parsed)

    return True, {'valid_rows': valid_rows, 'rejected_rows': rejected_rows, 'headers': headers}


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
