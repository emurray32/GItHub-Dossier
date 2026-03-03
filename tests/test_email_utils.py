"""
Comprehensive tests for email_utils module.

Tests cover the three exported helpers:
  - _filter_personal_email(email)
  - _derive_company_domain(company)
  - _check_company_match(email, target_company)
"""

import pytest

from email_utils import (
    _filter_personal_email,
    _derive_company_domain,
    _check_company_match,
    _PERSONAL_EMAIL_DOMAINS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# _filter_personal_email
# ═══════════════════════════════════════════════════════════════════════════════


class TestFilterPersonalEmail:
    """Tests for _filter_personal_email()."""

    # --- Normal corporate emails pass through ---

    def test_corporate_email_passthrough(self):
        assert _filter_personal_email('jane@acme.com') == 'jane@acme.com'

    def test_corporate_email_preserved_exactly(self):
        email = 'John.Doe@BigCorp.io'
        assert _filter_personal_email(email) == email

    def test_subdomain_corporate_email(self):
        assert _filter_personal_email('dev@eng.stripe.com') == 'dev@eng.stripe.com'

    # --- Every personal domain in the blocklist is filtered ---

    @pytest.mark.parametrize('domain', sorted(_PERSONAL_EMAIL_DOMAINS))
    def test_all_personal_domains_filtered(self, domain):
        email = f'user@{domain}'
        assert _filter_personal_email(email) == ''

    # --- Case insensitivity ---

    @pytest.mark.parametrize('email', [
        'User@Gmail.COM',
        'USER@GMAIL.COM',
        'user@Gmail.com',
        'test@YAHOO.COM',
        'hi@ProtonMail.Com',
    ])
    def test_case_insensitive_filtering(self, email):
        assert _filter_personal_email(email) == ''

    def test_case_insensitive_corporate_passthrough(self):
        """Corporate emails with mixed case should pass through unchanged."""
        email = 'Dev@MyCompany.COM'
        assert _filter_personal_email(email) == email

    # --- Edge cases: falsy / empty inputs ---

    def test_none_returns_empty(self):
        assert _filter_personal_email(None) == ''

    def test_empty_string_returns_empty(self):
        assert _filter_personal_email('') == ''

    def test_zero_returns_empty(self):
        assert _filter_personal_email(0) == ''

    def test_false_returns_empty(self):
        assert _filter_personal_email(False) == ''

    # --- Edge cases: malformed emails ---

    def test_no_at_sign_returns_as_is(self):
        """Without '@', the domain extraction yields '' which is not in the blocklist."""
        assert _filter_personal_email('nodomainemail') == 'nodomainemail'

    def test_multiple_at_signs(self):
        """split('@')[-1] takes everything after the last '@'."""
        assert _filter_personal_email('weird@@gmail.com') == ''

    def test_at_sign_only(self):
        """'@' alone -> domain is '', which is not personal -> passes through."""
        assert _filter_personal_email('@') == '@'

    def test_trailing_at_sign(self):
        assert _filter_personal_email('user@') == 'user@'

    def test_leading_at_sign_with_personal_domain(self):
        assert _filter_personal_email('@gmail.com') == ''

    def test_whitespace_string(self):
        """Whitespace is truthy so it enters the logic; no '@' -> passes through."""
        assert _filter_personal_email('  ') == '  '

    # --- Domains that look similar but are NOT personal ---

    def test_gmail_co_not_filtered(self):
        assert _filter_personal_email('user@gmail.co') == 'user@gmail.co'

    def test_hotmail_co_uk_not_filtered(self):
        assert _filter_personal_email('user@hotmail.co.uk') == 'user@hotmail.co.uk'

    def test_protonmail_io_not_filtered(self):
        assert _filter_personal_email('user@protonmail.io') == 'user@protonmail.io'

    def test_yahoo_co_jp_not_filtered(self):
        assert _filter_personal_email('user@yahoo.co.jp') == 'user@yahoo.co.jp'


# ═══════════════════════════════════════════════════════════════════════════════
# _derive_company_domain
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeriveCompanyDomain:
    """Tests for _derive_company_domain()."""

    # --- Simple one-word company names ---

    def test_simple_name(self):
        assert _derive_company_domain('Clay') == 'clay.com'

    def test_simple_name_lowercase(self):
        assert _derive_company_domain('stripe') == 'stripe.com'

    def test_simple_name_uppercase(self):
        assert _derive_company_domain('ACME') == 'acme.com'

    # --- Multi-word company names (spaces removed) ---

    def test_two_word_name(self):
        assert _derive_company_domain('Palo Alto') == 'paloalto.com'

    def test_three_word_name(self):
        assert _derive_company_domain('Big Red Corp') == 'bigred.com'

    def test_multi_word_with_mixed_case(self):
        assert _derive_company_domain('Elastic Search') == 'elasticsearch.com'

    # --- Suffix stripping (all 12 suffixes) ---

    @pytest.mark.parametrize('suffix', [
        ' Inc', ' Inc.', ' Corp', ' Corp.', ' Ltd', ' Ltd.',
        ' LLC', ' Co', ' Co.', ' GmbH', ' AG', ' SA',
    ])
    def test_suffix_stripped(self, suffix):
        result = _derive_company_domain(f'Acme{suffix}')
        assert result == 'acme.com', f'Failed for suffix "{suffix}"'

    def test_suffix_case_insensitive(self):
        assert _derive_company_domain('Acme INC') == 'acme.com'
        assert _derive_company_domain('Acme INC.') == 'acme.com'
        assert _derive_company_domain('Acme CORP') == 'acme.com'
        assert _derive_company_domain('Acme LLC') == 'acme.com'
        assert _derive_company_domain('Acme GMBH') == 'acme.com'

    def test_multi_word_with_suffix(self):
        assert _derive_company_domain('Palo Alto Networks Inc.') == 'paloaltonetworks.com'

    def test_suffix_only_stripped_at_end(self):
        """'Inc' in the middle of the name should not be stripped."""
        assert _derive_company_domain('Incorta') == 'incorta.com'

    def test_co_suffix_not_stripped_from_middle(self):
        """'Co' as suffix only matches ' co' at end, not inside a word."""
        assert _derive_company_domain('Coda') == 'coda.com'

    # --- Edge cases: falsy / empty inputs ---

    def test_none_returns_empty(self):
        assert _derive_company_domain(None) == ''

    def test_empty_string_returns_empty(self):
        assert _derive_company_domain('') == ''

    def test_zero_returns_empty(self):
        assert _derive_company_domain(0) == ''

    # --- Whitespace handling ---

    def test_leading_trailing_whitespace_stripped(self):
        assert _derive_company_domain('  Acme  ') == 'acme.com'

    def test_spaces_removed_from_result(self):
        assert _derive_company_domain('  Big  Red  ') == 'bigred.com'

    # --- Only suffix left after stripping ---

    def test_company_is_just_suffix_word(self):
        """If the company name is just 'Inc', the suffix ' inc' won't match
        because it requires a leading space. Result: 'inc.com'."""
        assert _derive_company_domain('Inc') == 'inc.com'

    def test_company_is_just_llc(self):
        assert _derive_company_domain('LLC') == 'llc.com'

    # --- German / European suffixes ---

    def test_gmbh_suffix(self):
        assert _derive_company_domain('SAP GmbH') == 'sap.com'

    def test_ag_suffix(self):
        assert _derive_company_domain('Siemens AG') == 'siemens.com'

    def test_sa_suffix(self):
        assert _derive_company_domain('Globant SA') == 'globant.com'


# ═══════════════════════════════════════════════════════════════════════════════
# _check_company_match
# ═══════════════════════════════════════════════════════════════════════════════


class TestCheckCompanyMatch:
    """Tests for _check_company_match()."""

    # --- Exact domain match via _derive_company_domain ---

    def test_exact_domain_match(self):
        assert _check_company_match('jane@acme.com', 'Acme') is True

    def test_exact_domain_match_with_suffix(self):
        assert _check_company_match('dev@stripe.com', 'Stripe Inc.') is True

    def test_exact_domain_multi_word(self):
        assert _check_company_match('dev@paloaltonetworks.com', 'Palo Alto Networks') is True

    # --- Fuzzy match: company name appears in email domain ---

    def test_company_name_in_domain(self):
        """'stripe' appears in 'stripe.io'."""
        assert _check_company_match('dev@stripe.io', 'Stripe') is True

    def test_company_name_in_subdomain(self):
        """'acme' appears in 'mail.acme.co.uk'."""
        assert _check_company_match('user@mail.acme.co.uk', 'Acme') is True

    def test_multi_word_company_in_domain(self):
        """'paloaltonetworks' (spaces removed) appears in domain."""
        assert _check_company_match('user@paloaltonetworks.io', 'Palo Alto Networks') is True

    # --- Fuzzy match: domain prefix appears in company name ---

    def test_domain_prefix_in_company_name(self):
        """email_domain.split('.')[0] = 'bigcorp' is in 'bigcorptech' (spaces removed)."""
        assert _check_company_match('user@bigcorp.com', 'BigCorp Tech') is True

    def test_short_domain_prefix_in_long_company(self):
        """'sap' is in 'sap' (exact, after lowering)."""
        assert _check_company_match('user@sap.com', 'SAP AG') is True

    # --- No match ---

    def test_no_match_different_company(self):
        assert _check_company_match('user@google.com', 'Microsoft') is False

    def test_no_match_partial_mismatch(self):
        assert _check_company_match('user@stripe.com', 'Acme Corp') is False

    def test_no_match_similar_but_different(self):
        assert _check_company_match('user@shoppy.com', 'Shopify') is False

    # --- Allow-through cases (missing data) ---

    def test_none_email_allows_through(self):
        assert _check_company_match(None, 'Acme') is True

    def test_empty_email_allows_through(self):
        assert _check_company_match('', 'Acme') is True

    def test_none_company_allows_through(self):
        assert _check_company_match('user@acme.com', None) is True

    def test_empty_company_allows_through(self):
        assert _check_company_match('user@acme.com', '') is True

    def test_both_none_allows_through(self):
        assert _check_company_match(None, None) is True

    def test_both_empty_allows_through(self):
        assert _check_company_match('', '') is True

    # --- No '@' in email allows through ---

    def test_no_at_sign_allows_through(self):
        assert _check_company_match('usernoatsign', 'Acme') is True

    def test_just_username_allows_through(self):
        assert _check_company_match('jane.smith', 'Acme') is True

    # --- Case insensitivity ---

    def test_case_insensitive_match(self):
        assert _check_company_match('USER@ACME.COM', 'acme') is True

    def test_mixed_case_match(self):
        assert _check_company_match('User@Stripe.Com', 'STRIPE INC.') is True

    # --- Company with suffix and domain match ---

    def test_ltd_suffix_stripped_for_match(self):
        assert _check_company_match('dev@globex.com', 'Globex Ltd.') is True

    def test_gmbh_suffix_stripped_for_match(self):
        assert _check_company_match('info@siemens.com', 'Siemens GmbH') is True

    def test_llc_suffix_stripped_for_match(self):
        assert _check_company_match('hello@basecamp.com', 'Basecamp LLC') is True

    # --- Edge: domain prefix is substring of company name (reverse fuzzy) ---

    def test_reverse_fuzzy_short_prefix(self):
        """'ms' is NOT a contiguous substring of 'microsoft' -> False."""
        assert _check_company_match('user@ms.com', 'Microsoft') is False

    def test_reverse_fuzzy_no_match(self):
        """'zoom' is NOT in 'microsoft' -> False."""
        assert _check_company_match('user@zoom.com', 'Microsoft') is False

    # --- Real-world examples ---

    def test_real_world_salesforce(self):
        assert _check_company_match('admin@salesforce.com', 'Salesforce Inc.') is True

    def test_real_world_hubspot(self):
        assert _check_company_match('contact@hubspot.com', 'HubSpot') is True

    def test_real_world_atlassian(self):
        assert _check_company_match('dev@atlassian.com', 'Atlassian Corp.') is True

    def test_real_world_mismatch(self):
        assert _check_company_match('dev@randomstartup.io', 'Salesforce') is False

    # --- Interaction with personal email domains ---

    def test_personal_email_does_not_match_company(self):
        """Gmail domain should not match a company named 'Acme'."""
        assert _check_company_match('user@gmail.com', 'Acme') is False

    def test_personal_email_matches_if_company_is_gmail(self):
        """Edge case: if the target company happened to be 'Gmail', it would match."""
        assert _check_company_match('user@gmail.com', 'Gmail') is True

    # --- Whitespace in company name ---

    def test_company_with_extra_spaces(self):
        assert _check_company_match('dev@acme.com', '  Acme  ') is True

    def test_company_with_internal_spaces(self):
        assert _check_company_match('dev@bigdata.com', 'Big Data') is True

    # --- Multiple '@' signs ---

    def test_multiple_at_signs_uses_last(self):
        """split('@')[-1] takes domain after the last '@'."""
        assert _check_company_match('weird@@acme.com', 'Acme') is True
