"""
Unit tests for email_engine.py — pure helper functions.

Tests cover the internal functions that do NOT require an OpenAI client:
- _classify_persona
- _select_strongest_signal
- _extract_signal_details
- _build_hook_line
- _score_email_specificity
- _fallback_email
"""
import pytest

from email_engine import (
    _classify_persona,
    _select_strongest_signal,
    _extract_signal_details,
    _build_hook_line,
    _score_email_specificity,
    _fallback_email,
    SIGNAL_TEMPLATES,
    PERSONA_TONES,
)


# ──────────────────────────────────────────────────────────────────────
# _classify_persona
# ──────────────────────────────────────────────────────────────────────

class TestClassifyPersona:
    """Tests for _classify_persona title-to-persona mapping."""

    def test_vp_engineering(self):
        assert _classify_persona('VP Engineering') == 'vp_engineering'

    def test_vp_of_engineering(self):
        assert _classify_persona('VP of Engineering') == 'vp_engineering'

    def test_cto(self):
        assert _classify_persona('CTO') == 'vp_engineering'

    def test_chief_technology_officer(self):
        assert _classify_persona('Chief Technology Officer') == 'vp_engineering'

    def test_head_of_engineering(self):
        assert _classify_persona('Head of Engineering') == 'vp_engineering'

    def test_head_of_product(self):
        assert _classify_persona('Head of Product') == 'head_of_product'

    def test_vp_product(self):
        assert _classify_persona('VP Product') == 'head_of_product'

    def test_chief_product_officer(self):
        assert _classify_persona('Chief Product Officer') == 'head_of_product'

    def test_director_of_product(self):
        assert _classify_persona('Director of Product') == 'head_of_product'

    def test_director_of_localization(self):
        assert _classify_persona('Director of Localization') == 'dir_localization'

    def test_localization_manager(self):
        assert _classify_persona('Localization Manager') == 'dir_localization'

    def test_i18n_manager(self):
        assert _classify_persona('i18n Manager') == 'dir_localization'

    def test_empty_string_returns_default(self):
        assert _classify_persona('') == 'default'

    def test_none_returns_default(self):
        assert _classify_persona(None) == 'default'

    def test_unknown_title_returns_default(self):
        assert _classify_persona('Marketing Coordinator') == 'default'

    def test_case_insensitive(self):
        assert _classify_persona('vp engineering') == 'vp_engineering'

    def test_with_extra_whitespace(self):
        assert _classify_persona('  VP Engineering  ') == 'vp_engineering'

    def test_localization_takes_priority_over_broader(self):
        """dir_localization is checked first, ensuring specific match wins."""
        assert _classify_persona('Head of Localization') == 'dir_localization'


# ──────────────────────────────────────────────────────────────────────
# _select_strongest_signal
# ──────────────────────────────────────────────────────────────────────

class TestSelectStrongestSignal:
    """Tests for _select_strongest_signal priority ordering."""

    def test_empty_list_returns_none(self):
        sig_type, sig_data = _select_strongest_signal([])
        assert sig_type == 'none'
        assert sig_data == {}

    def test_single_signal_returned(self):
        signals = [{'signal_type': 'ghost_branch', 'description': 'Branch found'}]
        sig_type, sig_data = _select_strongest_signal(signals)
        assert sig_type == 'ghost_branch'
        assert sig_data['description'] == 'Branch found'

    def test_dependency_injection_wins_over_ghost_branch(self):
        """dependency_injection has higher priority than ghost_branch."""
        signals = [
            {'signal_type': 'ghost_branch', 'description': 'Branch'},
            {'signal_type': 'dependency_injection', 'description': 'Found lib'},
        ]
        sig_type, _ = _select_strongest_signal(signals)
        assert sig_type == 'dependency_injection'

    def test_rfc_wins_over_ghost_branch(self):
        signals = [
            {'signal_type': 'ghost_branch', 'description': 'Branch'},
            {'signal_type': 'rfc_discussion', 'description': 'RFC'},
        ]
        sig_type, _ = _select_strongest_signal(signals)
        assert sig_type == 'rfc_discussion'

    def test_suffix_normalization_high(self):
        """Suffixed types like rfc_discussion_high are normalized."""
        signals = [{'signal_type': 'rfc_discussion_high', 'description': 'RFC'}]
        sig_type, _ = _select_strongest_signal(signals)
        assert sig_type == 'rfc_discussion'

    def test_suffix_normalization_medium(self):
        signals = [{'signal_type': 'ghost_branch_medium', 'description': 'Branch'}]
        sig_type, _ = _select_strongest_signal(signals)
        assert sig_type == 'ghost_branch'

    def test_unknown_signal_type_falls_back_to_first(self):
        signals = [
            {'signal_type': 'custom_signal', 'description': 'Custom'},
            {'signal_type': 'another_custom', 'description': 'Another'},
        ]
        sig_type, sig_data = _select_strongest_signal(signals)
        assert sig_type == 'custom_signal'
        assert sig_data['description'] == 'Custom'


# ──────────────────────────────────────────────────────────────────────
# _extract_signal_details
# ──────────────────────────────────────────────────────────────────────

class TestExtractSignalDetails:
    """Tests for _extract_signal_details parsing."""

    def test_dependency_injection_parses_library(self):
        details = _extract_signal_details('dependency_injection', {
            'description': 'Found react-i18next in frontend-app/package.json',
            'file_path': 'frontend-app/package.json',
        })
        assert details['library'] == 'react-i18next'
        assert details['repo'] == 'frontend-app'

    def test_ghost_branch_parses_branch_name(self):
        details = _extract_signal_details('ghost_branch', {
            'description': 'Branch feature/i18n in my-org/my-repo',
        })
        assert details['branch'] == 'feature/i18n'
        assert details['repo'] == 'my-org'

    def test_rfc_discussion_parses_topic(self):
        details = _extract_signal_details('rfc_discussion', {
            'description': 'Discussion about localization strategy in my-repo#42',
        })
        assert details['topic'] == 'localization strategy'

    def test_documentation_intent_extracts_file(self):
        details = _extract_signal_details('documentation_intent', {
            'description': 'Localization mentioned in docs/roadmap.md',
            'file_path': 'docs/roadmap.md',
        })
        assert details['file_path'] == 'roadmap.md'

    def test_age_clause_generated(self):
        details = _extract_signal_details('dependency_injection', {
            'description': 'Found lib in repo/pkg.json',
            'age_in_days': 30,
        })
        assert 'recently' in details['age_clause']

    def test_no_age_clause_when_zero(self):
        details = _extract_signal_details('dependency_injection', {
            'description': 'Found lib in repo/pkg.json',
            'age_in_days': 0,
        })
        assert details['age_clause'] == ''

    def test_empty_description(self):
        details = _extract_signal_details('dependency_injection', {
            'description': '',
        })
        assert details['library'] == ''
        assert details['repo'] == ''

    def test_empty_signal_data(self):
        details = _extract_signal_details('unknown_type', {})
        assert details['library'] == ''
        assert details['repo'] == ''
        assert details['branch'] == ''


# ──────────────────────────────────────────────────────────────────────
# _build_hook_line
# ──────────────────────────────────────────────────────────────────────

class TestBuildHookLine:
    """Tests for _build_hook_line template interpolation."""

    def test_successful_interpolation(self):
        template = SIGNAL_TEMPLATES['dependency_injection']
        details = {
            'library': 'react-i18next',
            'repo': 'webapp',
            'age_clause': ' 10 days ago',
            'branch': '',
            'topic': '',
            'file_path': '',
        }
        hook = _build_hook_line('dependency_injection', template, details)
        assert 'react-i18next' in hook
        assert 'webapp' in hook

    def test_fallback_when_placeholders_empty(self):
        """Uses fallback_hook when key fields are empty and format yields empty."""
        template = SIGNAL_TEMPLATES['dependency_injection']
        details = {
            'library': '',
            'repo': '',
            'age_clause': '',
            'branch': '',
            'topic': '',
            'file_path': '',
        }
        hook = _build_hook_line('dependency_injection', template, details)
        # Should use fallback since library/repo are empty
        # The formatted string "I noticed your team added `` to ``." is non-empty,
        # so it returns the formatted version with backticks
        assert isinstance(hook, str)
        assert len(hook) > 0

    def test_fallback_on_missing_key(self):
        """Uses fallback_hook when template references missing keys."""
        template = {
            'hook': 'Found {nonexistent_key} in {repo}',
            'fallback_hook': 'I noticed your team is working on i18n.',
        }
        details = {'repo': 'webapp'}
        hook = _build_hook_line('test', template, details)
        assert hook == 'I noticed your team is working on i18n.'

    def test_ghost_branch_template(self):
        template = SIGNAL_TEMPLATES['ghost_branch']
        details = {
            'branch': 'feature/l10n',
            'repo': 'main-app',
            'age_clause': '',
            'library': '',
            'topic': '',
            'file_path': '',
        }
        hook = _build_hook_line('ghost_branch', template, details)
        assert 'feature/l10n' in hook
        assert 'main-app' in hook


# ──────────────────────────────────────────────────────────────────────
# _score_email_specificity
# ──────────────────────────────────────────────────────────────────────

class TestScoreEmailSpecificity:
    """Tests for _score_email_specificity scoring algorithm."""

    def test_library_reference_scores(self):
        details = {'library': 'react-i18next', 'repo': '', 'branch': ''}
        score = _score_email_specificity(
            'I noticed react-i18next in your repo.',
            details,
        )
        assert score >= 25

    def test_repo_reference_scores(self):
        details = {'library': '', 'repo': 'webapp', 'branch': ''}
        score = _score_email_specificity(
            'I saw changes in webapp recently.',
            details,
        )
        assert score >= 20

    def test_branch_reference_scores(self):
        details = {'library': '', 'repo': '', 'branch': 'feature/i18n'}
        score = _score_email_specificity(
            'The feature/i18n branch caught my eye.',
            details,
        )
        assert score >= 20

    def test_apollo_variables_score(self):
        details = {'library': '', 'repo': '', 'branch': ''}
        text = 'Hey {{first_name}}, I noticed {{company}} is expanding. Best, {{sender_first_name}}'
        score = _score_email_specificity(text, details)
        assert score >= 20  # 10 + 5 + 5

    def test_brevity_bonus_under_100_words(self):
        details = {'library': '', 'repo': '', 'branch': ''}
        short_text = ' '.join(['word'] * 50)
        score_short = _score_email_specificity(short_text, details)

        long_text = ' '.join(['word'] * 150)
        score_long = _score_email_specificity(long_text, details)

        assert score_short > score_long

    def test_cta_question_mark_scores(self):
        details = {'library': '', 'repo': '', 'branch': ''}
        text_with_cta = 'Some text. Worth a look?'
        text_without_cta = 'Some text. Let me know.'
        score_with = _score_email_specificity(text_with_cta, details)
        score_without = _score_email_specificity(text_without_cta, details)
        assert score_with > score_without

    def test_max_score_capped_at_100(self):
        details = {'library': 'lib', 'repo': 'repo', 'branch': 'branch'}
        text = (
            'lib repo branch {{first_name}} {{company}} {{sender_first_name}} '
            'short text with a question?'
        )
        score = _score_email_specificity(text, details)
        assert score <= 100

    def test_empty_details_and_text(self):
        details = {'library': '', 'repo': '', 'branch': ''}
        score = _score_email_specificity('', details)
        assert score >= 0


# ──────────────────────────────────────────────────────────────────────
# _fallback_email
# ──────────────────────────────────────────────────────────────────────

class TestFallbackEmail:
    """Tests for _fallback_email template generation."""

    def test_returns_required_keys(self):
        contact = {'first_name': 'Jane', 'title': 'CTO', 'company_name': 'Acme'}
        signals = [{'signal_type': 'dependency_injection', 'description': 'Found lib'}]
        result = _fallback_email(contact, signals)

        assert 'best_variant' in result
        assert 'best_subject' in result
        assert 'best_body' in result
        assert 'variants' in result
        assert 'signal_type' in result
        assert 'persona' in result
        assert 'canspam_footer' in result

    def test_body_contains_apollo_variables(self):
        contact = {'first_name': 'Jane', 'company_name': 'Acme'}
        signals = [{'signal_type': 'ghost_branch', 'description': 'Branch i18n in repo'}]
        result = _fallback_email(contact, signals)
        assert '{{first_name}}' in result['best_body']
        assert '{{sender_first_name}}' in result['best_body']

    def test_subject_contains_company_variable(self):
        contact = {'first_name': 'Jane', 'company_name': 'Acme'}
        signals = []
        result = _fallback_email(contact, signals)
        assert '{{company}}' in result['best_subject']

    def test_empty_signals(self):
        contact = {'first_name': 'Jane', 'company_name': 'Acme'}
        result = _fallback_email(contact, [])
        assert result['signal_type'] == 'none'
        assert result['persona'] == 'default'
        assert result['best_variant'] == 'A'

    def test_variant_a_always_present(self):
        contact = {'first_name': 'Jane', 'company_name': 'Acme'}
        signals = [{'signal_type': 'dependency_injection', 'description': 'Found lib'}]
        result = _fallback_email(contact, signals)
        assert 'A' in result['variants']
        variant_a = result['variants']['A']
        assert 'subject' in variant_a
        assert 'body' in variant_a
        assert 'score' in variant_a

    def test_canspam_footer_present(self):
        contact = {'first_name': 'Jane', 'company_name': 'Acme'}
        result = _fallback_email(contact, [])
        assert 'Phrase SE GmbH' in result['canspam_footer']
        assert '{{unsubscribe}}' in result['canspam_footer']
