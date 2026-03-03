"""
Tests for validators.py — input validation and sanitization helpers.

Tests cover every public function with valid input, invalid input, and edge cases:
- validate_company_name
- validate_github_org
- validate_email
- validate_url
- validate_apollo_id
- validate_search_query
- validate_notes
- validate_tier
- validate_positive_int
- sanitize_for_log
"""
import pytest

from validators import (
    validate_company_name,
    validate_github_org,
    validate_email,
    validate_url,
    validate_apollo_id,
    validate_search_query,
    validate_notes,
    validate_tier,
    validate_positive_int,
    sanitize_for_log,
    MAX_COMPANY_NAME_LENGTH,
    MAX_GITHUB_ORG_LENGTH,
    MAX_EMAIL_LENGTH,
    MAX_URL_LENGTH,
    MAX_SEARCH_QUERY_LENGTH,
    MAX_NOTES_LENGTH,
    MAX_APOLLO_ID_LENGTH,
)


# ──────────────────────────────────────────────────────────────────────
# validate_company_name
# ──────────────────────────────────────────────────────────────────────

class TestValidateCompanyName:
    """Tests for validate_company_name."""

    def test_valid_simple_name(self):
        ok, val = validate_company_name('Acme Corp')
        assert ok is True
        assert val == 'Acme Corp'

    def test_valid_name_with_apostrophe(self):
        ok, val = validate_company_name("O'Reilly Media")
        assert ok is True
        assert val == "O'Reilly Media"

    def test_valid_name_with_ampersand(self):
        ok, val = validate_company_name('Johnson & Johnson')
        assert ok is True
        assert val == 'Johnson & Johnson'

    def test_valid_name_with_parentheses(self):
        ok, val = validate_company_name('SAP (Deutschland)')
        assert ok is True

    def test_strips_whitespace(self):
        ok, val = validate_company_name('  Acme Corp  ')
        assert ok is True
        assert val == 'Acme Corp'

    def test_rejects_empty_string(self):
        ok, val = validate_company_name('')
        assert ok is False
        assert 'required' in val.lower()

    def test_rejects_whitespace_only(self):
        ok, val = validate_company_name('   ')
        assert ok is False
        assert 'empty' in val.lower()

    def test_rejects_none(self):
        ok, val = validate_company_name(None)
        assert ok is False

    def test_rejects_non_string(self):
        ok, val = validate_company_name(12345)
        assert ok is False

    def test_rejects_too_long(self):
        ok, val = validate_company_name('A' * (MAX_COMPANY_NAME_LENGTH + 1))
        assert ok is False
        assert 'too long' in val.lower()

    def test_rejects_script_tag(self):
        ok, val = validate_company_name('<script>alert(1)</script>')
        assert ok is False
        assert 'disallowed' in val.lower()

    def test_rejects_sql_injection_drop_table(self):
        ok, val = validate_company_name("Robert'; DROP TABLE companies;--")
        assert ok is False

    def test_rejects_sql_injection_union_select(self):
        ok, val = validate_company_name("x' UNION SELECT * FROM users--")
        assert ok is False

    def test_rejects_sql_injection_delete_from(self):
        ok, val = validate_company_name("x'; DELETE FROM reports;--")
        assert ok is False

    def test_rejects_invalid_chars(self):
        ok, val = validate_company_name('Acme Corp @$%^')
        assert ok is False
        assert 'invalid characters' in val.lower()

    def test_at_max_length(self):
        name = 'A' * MAX_COMPANY_NAME_LENGTH
        ok, val = validate_company_name(name)
        assert ok is True
        assert val == name


# ──────────────────────────────────────────────────────────────────────
# validate_github_org
# ──────────────────────────────────────────────────────────────────────

class TestValidateGithubOrg:
    """Tests for validate_github_org."""

    def test_valid_org(self):
        ok, val = validate_github_org('facebook')
        assert ok is True
        assert val == 'facebook'

    def test_valid_org_with_hyphens(self):
        ok, val = validate_github_org('my-cool-org')
        assert ok is True

    def test_valid_single_char(self):
        ok, val = validate_github_org('x')
        assert ok is True

    def test_valid_numeric(self):
        ok, val = validate_github_org('org123')
        assert ok is True

    def test_rejects_empty(self):
        ok, val = validate_github_org('')
        assert ok is False

    def test_rejects_none(self):
        ok, val = validate_github_org(None)
        assert ok is False

    def test_rejects_too_long(self):
        ok, val = validate_github_org('a' * (MAX_GITHUB_ORG_LENGTH + 1))
        assert ok is False
        assert 'too long' in val.lower()

    def test_at_max_length(self):
        name = 'a' * MAX_GITHUB_ORG_LENGTH
        ok, val = validate_github_org(name)
        assert ok is True

    def test_rejects_leading_hyphen(self):
        ok, val = validate_github_org('-badorg')
        assert ok is False
        assert 'no leading/trailing hyphen' in val.lower()

    def test_rejects_trailing_hyphen(self):
        ok, val = validate_github_org('badorg-')
        assert ok is False

    def test_rejects_special_chars(self):
        ok, val = validate_github_org('bad org!')
        assert ok is False

    def test_rejects_dots(self):
        ok, val = validate_github_org('bad.org')
        assert ok is False

    def test_strips_whitespace(self):
        ok, val = validate_github_org('  facebook  ')
        assert ok is True
        assert val == 'facebook'


# ──────────────────────────────────────────────────────────────────────
# validate_email
# ──────────────────────────────────────────────────────────────────────

class TestValidateEmail:
    """Tests for validate_email."""

    def test_valid_email(self):
        ok, val = validate_email('user@example.com')
        assert ok is True
        assert val == 'user@example.com'

    def test_valid_email_with_dots(self):
        ok, val = validate_email('first.last@company.co.uk')
        assert ok is True

    def test_valid_email_with_plus(self):
        ok, val = validate_email('user+tag@example.com')
        assert ok is True

    def test_lowercases_email(self):
        ok, val = validate_email('User@EXAMPLE.COM')
        assert ok is True
        assert val == 'user@example.com'

    def test_strips_whitespace(self):
        ok, val = validate_email('  user@example.com  ')
        assert ok is True
        assert val == 'user@example.com'

    def test_rejects_empty(self):
        ok, val = validate_email('')
        assert ok is False

    def test_rejects_none(self):
        ok, val = validate_email(None)
        assert ok is False

    def test_rejects_missing_at(self):
        ok, val = validate_email('userexample.com')
        assert ok is False
        assert 'format' in val.lower()

    def test_rejects_missing_domain(self):
        ok, val = validate_email('user@')
        assert ok is False

    def test_rejects_too_long(self):
        ok, val = validate_email('a' * 243 + '@example.com')
        assert ok is False
        assert 'too long' in val.lower()

    def test_rejects_spaces_in_email(self):
        ok, val = validate_email('user @example.com')
        assert ok is False


# ──────────────────────────────────────────────────────────────────────
# validate_url
# ──────────────────────────────────────────────────────────────────────

class TestValidateUrl:
    """Tests for validate_url."""

    def test_valid_https_url(self):
        ok, val = validate_url('https://example.com/path')
        assert ok is True
        assert val == 'https://example.com/path'

    def test_valid_http_url(self):
        ok, val = validate_url('http://example.com')
        assert ok is True

    def test_valid_url_with_query(self):
        ok, val = validate_url('https://example.com/search?q=test&page=1')
        assert ok is True

    def test_rejects_empty(self):
        ok, val = validate_url('')
        assert ok is False

    def test_rejects_none(self):
        ok, val = validate_url(None)
        assert ok is False

    def test_rejects_javascript_protocol(self):
        ok, val = validate_url('javascript:alert(1)')
        assert ok is False
        assert 'http' in val.lower()

    def test_rejects_ftp_protocol(self):
        ok, val = validate_url('ftp://files.example.com')
        assert ok is False

    def test_rejects_too_long(self):
        ok, val = validate_url('https://example.com/' + 'a' * MAX_URL_LENGTH)
        assert ok is False
        assert 'too long' in val.lower()

    def test_strips_whitespace(self):
        ok, val = validate_url('  https://example.com  ')
        assert ok is True
        assert val == 'https://example.com'

    def test_rejects_bare_domain(self):
        ok, val = validate_url('example.com')
        assert ok is False


# ──────────────────────────────────────────────────────────────────────
# validate_apollo_id
# ──────────────────────────────────────────────────────────────────────

class TestValidateApolloId:
    """Tests for validate_apollo_id."""

    def test_valid_alphanumeric(self):
        ok, val = validate_apollo_id('abc123')
        assert ok is True
        assert val == 'abc123'

    def test_valid_with_hyphens(self):
        ok, val = validate_apollo_id('person-abc-123')
        assert ok is True

    def test_valid_with_underscores(self):
        ok, val = validate_apollo_id('person_abc_123')
        assert ok is True

    def test_rejects_empty(self):
        ok, val = validate_apollo_id('')
        assert ok is False

    def test_rejects_none(self):
        ok, val = validate_apollo_id(None)
        assert ok is False

    def test_rejects_special_chars(self):
        ok, val = validate_apollo_id('id@#$%')
        assert ok is False
        assert 'format' in val.lower()

    def test_rejects_too_long(self):
        ok, val = validate_apollo_id('a' * (MAX_APOLLO_ID_LENGTH + 1))
        assert ok is False
        assert 'too long' in val.lower()

    def test_strips_whitespace(self):
        ok, val = validate_apollo_id('  abc123  ')
        assert ok is True
        assert val == 'abc123'


# ──────────────────────────────────────────────────────────────────────
# validate_search_query
# ──────────────────────────────────────────────────────────────────────

class TestValidateSearchQuery:
    """Tests for validate_search_query."""

    def test_valid_query(self):
        ok, val = validate_search_query('react i18n')
        assert ok is True
        assert val == 'react i18n'

    def test_strips_angle_brackets(self):
        ok, val = validate_search_query('test <script> query')
        assert ok is True
        assert '<' not in val
        assert '>' not in val

    def test_rejects_empty(self):
        ok, val = validate_search_query('')
        assert ok is False

    def test_rejects_none(self):
        ok, val = validate_search_query(None)
        assert ok is False

    def test_rejects_too_long(self):
        ok, val = validate_search_query('a' * (MAX_SEARCH_QUERY_LENGTH + 1))
        assert ok is False
        assert 'too long' in val.lower()

    def test_at_max_length(self):
        query = 'a' * MAX_SEARCH_QUERY_LENGTH
        ok, val = validate_search_query(query)
        assert ok is True


# ──────────────────────────────────────────────────────────────────────
# validate_notes
# ──────────────────────────────────────────────────────────────────────

class TestValidateNotes:
    """Tests for validate_notes."""

    def test_valid_notes(self):
        ok, val = validate_notes('Some notes about the company.')
        assert ok is True
        assert val == 'Some notes about the company.'

    def test_empty_string_allowed(self):
        ok, val = validate_notes('')
        assert ok is True
        assert val == ''

    def test_strips_script_tags(self):
        ok, val = validate_notes('hello <script>alert(1)</script> world')
        assert ok is True
        assert '<script>' not in val
        assert '</script>' not in val
        assert 'hello' in val
        assert 'world' in val

    def test_rejects_non_string(self):
        ok, val = validate_notes(12345)
        assert ok is False
        assert 'string' in val.lower()

    def test_rejects_too_long(self):
        ok, val = validate_notes('a' * (MAX_NOTES_LENGTH + 1))
        assert ok is False
        assert 'too long' in val.lower()

    def test_at_max_length(self):
        notes = 'a' * MAX_NOTES_LENGTH
        ok, val = validate_notes(notes)
        assert ok is True

    def test_strips_whitespace(self):
        ok, val = validate_notes('  hello  ')
        assert ok is True
        assert val == 'hello'


# ──────────────────────────────────────────────────────────────────────
# validate_tier
# ──────────────────────────────────────────────────────────────────────

class TestValidateTier:
    """Tests for validate_tier."""

    def test_valid_tiers(self):
        for t in (0, 1, 2, 3, 4):
            ok, val = validate_tier(t)
            assert ok is True
            assert val == t

    def test_accepts_string_int(self):
        ok, val = validate_tier('3')
        assert ok is True
        assert val == 3

    def test_rejects_5(self):
        ok, val = validate_tier(5)
        assert ok is False
        assert 'between 0 and 4' in val.lower()

    def test_rejects_negative(self):
        ok, val = validate_tier(-1)
        assert ok is False

    def test_rejects_non_numeric(self):
        ok, val = validate_tier('abc')
        assert ok is False
        assert 'integer' in val.lower()

    def test_rejects_none(self):
        ok, val = validate_tier(None)
        assert ok is False

    def test_rejects_float_string(self):
        ok, val = validate_tier('2.5')
        assert ok is False


# ──────────────────────────────────────────────────────────────────────
# validate_positive_int
# ──────────────────────────────────────────────────────────────────────

class TestValidatePositiveInt:
    """Tests for validate_positive_int."""

    def test_valid_zero(self):
        ok, val = validate_positive_int(0)
        assert ok is True
        assert val == 0

    def test_valid_positive(self):
        ok, val = validate_positive_int(42)
        assert ok is True
        assert val == 42

    def test_accepts_string_int(self):
        ok, val = validate_positive_int('10')
        assert ok is True
        assert val == 10

    def test_rejects_negative(self):
        ok, val = validate_positive_int(-1)
        assert ok is False
        assert 'non-negative' in val.lower()

    def test_rejects_non_numeric(self):
        ok, val = validate_positive_int('abc')
        assert ok is False
        assert 'integer' in val.lower()

    def test_rejects_none(self):
        ok, val = validate_positive_int(None)
        assert ok is False

    def test_respects_max_val(self):
        ok, val = validate_positive_int(100, max_val=50)
        assert ok is False
        assert 'maximum' in val.lower()

    def test_at_max_val(self):
        ok, val = validate_positive_int(50, max_val=50)
        assert ok is True
        assert val == 50

    def test_custom_name_in_error(self):
        ok, val = validate_positive_int(-1, name='page_size')
        assert ok is False
        assert 'page_size' in val


# ──────────────────────────────────────────────────────────────────────
# sanitize_for_log
# ──────────────────────────────────────────────────────────────────────

class TestSanitizeForLog:
    """Tests for sanitize_for_log."""

    def test_normal_string(self):
        assert sanitize_for_log('hello world') == 'hello world'

    def test_strips_newlines(self):
        result = sanitize_for_log("line1\nline2\rline3")
        assert '\n' not in result
        assert '\r' not in result
        assert 'line1 line2 line3' == result

    def test_truncates_to_max_length(self):
        result = sanitize_for_log('a' * 300, max_length=200)
        assert len(result) == 203  # 200 + '...'
        assert result.endswith('...')

    def test_custom_max_length(self):
        result = sanitize_for_log('a' * 50, max_length=10)
        assert len(result) == 13  # 10 + '...'

    def test_non_string_coerced(self):
        result = sanitize_for_log(12345)
        assert result == '12345'

    def test_at_exact_max_length(self):
        result = sanitize_for_log('a' * 200, max_length=200)
        assert result == 'a' * 200  # No truncation

    def test_empty_string(self):
        assert sanitize_for_log('') == ''
