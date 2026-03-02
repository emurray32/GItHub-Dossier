"""Tests for cold email generation via ai_summary.py."""
import json
import pytest
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.unit


def _make_scan_data(company='TestCorp', org_login='testcorp', goldilocks='preparing',
                    libraries=None, signals=None):
    """Build minimal scan_data for email generation tests."""
    if libraries is None:
        libraries = ['react-i18next']
    if signals is None:
        signals = [
            {
                'Company': org_login,
                'Signal': 'Dependency Injection',
                'Evidence': f'Found {libraries[0]} in package.json',
                'type': 'dependency_injection',
                'repo': 'webapp',
                'libraries_found': libraries,
                'goldilocks_status': goldilocks,
            }
        ]
    return {
        'company_name': company,
        'org_login': org_login,
        'org_name': company,
        'goldilocks_status': goldilocks,
        'signals': signals,
        'signal_summary': {
            'dependency_injection': {'count': len(signals), 'hits': []},
            'ghost_branch': {'count': 0, 'hits': []},
            'rfc_discussion': {'count': 0, 'hits': []},
        },
        'contributors': {},
        'repos_scanned': [],
    }


def _mock_openai_response(subject='Test Subject', body='Hey {{first_name}}, test body.'):
    """Build a mock OpenAI response with cold email JSON."""
    email_json = json.dumps({'subject': subject, 'body': body})
    mock_choice = MagicMock()
    mock_choice.message.content = email_json
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


class TestColdEmailGeneration:
    """Test _generate_cold_email_with_openai function."""

    def test_generates_email_dict(self):
        from ai_summary import _generate_cold_email_with_openai
        mock_resp = _mock_openai_response(
            subject='react-i18next in {{company}}',
            body='Hey {{first_name}}, noticed react-i18next in your webapp.'
        )

        mock_openai_cls = MagicMock()
        mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp

        import ai_summary
        with patch.object(ai_summary, 'OPENAI_AVAILABLE', True), \
             patch.object(ai_summary, 'AI_INTEGRATIONS_OPENAI_API_KEY', 'test-key'), \
             patch.object(ai_summary, 'AI_INTEGRATIONS_OPENAI_BASE_URL', 'https://test.api'), \
             patch.dict('sys.modules', {'openai': MagicMock(OpenAI=mock_openai_cls)}):
            # Re-bind OpenAI in the module namespace
            ai_summary.OpenAI = mock_openai_cls
            try:
                result = _generate_cold_email_with_openai(
                    _make_scan_data(),
                    {'executive_summary': 'Preparing: TestCorp has i18n libs', 'key_findings': []}
                )
            finally:
                if hasattr(ai_summary, 'OpenAI'):
                    delattr(ai_summary, 'OpenAI')

        assert result is not None
        assert 'subject' in result
        assert 'body' in result
        assert '{{first_name}}' in result['body']
        assert '{{company}}' in result['subject']

    def test_returns_none_without_api_key(self):
        from ai_summary import _generate_cold_email_with_openai
        with patch('ai_summary.OPENAI_AVAILABLE', True), \
             patch('ai_summary.AI_INTEGRATIONS_OPENAI_API_KEY', None), \
             patch('ai_summary.AI_INTEGRATIONS_OPENAI_BASE_URL', None):
            result = _generate_cold_email_with_openai(
                _make_scan_data(), {'executive_summary': '', 'key_findings': []}
            )
        assert result is None

    def test_returns_none_when_openai_unavailable(self):
        from ai_summary import _generate_cold_email_with_openai
        with patch('ai_summary.OPENAI_AVAILABLE', False):
            result = _generate_cold_email_with_openai(
                _make_scan_data(), {'executive_summary': '', 'key_findings': []}
            )
        assert result is None

    def test_handles_api_error(self):
        from ai_summary import _generate_cold_email_with_openai
        import ai_summary

        mock_openai_cls = MagicMock()
        mock_openai_cls.return_value.chat.completions.create.side_effect = Exception('API down')

        with patch.object(ai_summary, 'OPENAI_AVAILABLE', True), \
             patch.object(ai_summary, 'AI_INTEGRATIONS_OPENAI_API_KEY', 'test-key'), \
             patch.object(ai_summary, 'AI_INTEGRATIONS_OPENAI_BASE_URL', 'https://test.api'):
            ai_summary.OpenAI = mock_openai_cls
            try:
                result = _generate_cold_email_with_openai(
                    _make_scan_data(), {'executive_summary': '', 'key_findings': []}
                )
            finally:
                if hasattr(ai_summary, 'OpenAI'):
                    delattr(ai_summary, 'OpenAI')
        assert result is None

    def test_handles_markdown_wrapped_json(self):
        """OpenAI sometimes wraps JSON in ```json``` blocks."""
        from ai_summary import _generate_cold_email_with_openai
        import ai_summary

        email_json = json.dumps({'subject': 'Test', 'body': 'Hey'})
        wrapped = f'```json\n{email_json}\n```'
        mock_choice = MagicMock()
        mock_choice.message.content = wrapped
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_openai_cls = MagicMock()
        mock_openai_cls.return_value.chat.completions.create.return_value = mock_resp

        with patch.object(ai_summary, 'OPENAI_AVAILABLE', True), \
             patch.object(ai_summary, 'AI_INTEGRATIONS_OPENAI_API_KEY', 'test-key'), \
             patch.object(ai_summary, 'AI_INTEGRATIONS_OPENAI_BASE_URL', 'https://test.api'):
            ai_summary.OpenAI = mock_openai_cls
            try:
                result = _generate_cold_email_with_openai(
                    _make_scan_data(), {'executive_summary': '', 'key_findings': []}
                )
            finally:
                if hasattr(ai_summary, 'OpenAI'):
                    delattr(ai_summary, 'OpenAI')
        assert result is not None
        assert result['subject'] == 'Test'


class TestColdEmailInstructions:
    """Test the cold email instruction builder."""

    def test_default_instructions_include_apollo_vars(self):
        from ai_summary import _get_cold_email_instructions
        with patch('ai_summary._load_cold_outreach_skill', return_value=''):
            instructions = _get_cold_email_instructions()
        assert '{{first_name}}' in instructions
        assert '{{company}}' in instructions
        assert '{{sender_first_name}}' in instructions

    def test_default_instructions_word_limit(self):
        from ai_summary import _get_cold_email_instructions
        with patch('ai_summary._load_cold_outreach_skill', return_value=''):
            instructions = _get_cold_email_instructions()
        assert '100 words' in instructions

    def test_skill_file_overrides_defaults(self):
        from ai_summary import _get_cold_email_instructions
        custom_skill = 'Custom instruction: always mention GitHub Sync.'
        with patch('ai_summary._load_cold_outreach_skill', return_value=custom_skill):
            instructions = _get_cold_email_instructions()
        assert 'Custom instruction' in instructions
        assert 'GitHub Sync' in instructions

    def test_default_instructions_no_first_name_in_subject(self):
        from ai_summary import _get_cold_email_instructions
        with patch('ai_summary._load_cold_outreach_skill', return_value=''):
            instructions = _get_cold_email_instructions()
        assert 'NEVER use {{first_name}} in subject' in instructions


class TestSalesIntelligencePrompt:
    """Test the prompt builder for AI analysis."""

    def test_prompt_includes_company_name(self):
        from ai_summary import _build_sales_intelligence_prompt
        prompt = _build_sales_intelligence_prompt(_make_scan_data(company='SpecialCorp'))
        assert 'Specialcorp' in prompt  # .title() applied

    def test_prompt_includes_goldilocks_status(self):
        from ai_summary import _build_sales_intelligence_prompt
        prompt = _build_sales_intelligence_prompt(_make_scan_data(goldilocks='preparing'))
        assert 'PREPARING' in prompt

    def test_prompt_includes_libraries(self):
        from ai_summary import _build_sales_intelligence_prompt
        prompt = _build_sales_intelligence_prompt(
            _make_scan_data(libraries=['react-i18next', 'next-intl'])
        )
        assert 'react-i18next' in prompt
        assert 'next-intl' in prompt

    def test_prompt_includes_scoring_v2(self):
        from ai_summary import _build_sales_intelligence_prompt
        scan = _make_scan_data()
        scan['scoring_v2'] = {
            'org_maturity_label': 'PREPARING',
            'readiness_index': 0.85,
            'confidence_percent': 90,
            'outreach_angle_label': 'Infrastructure Guide',
            'outreach_angle_description': 'Help them connect i18n libs to TMS',
            'risk_level_label': 'Low',
            'signal_clusters_detected': ['dependency_cluster'],
            'recommended_sales_motion': 'Inbound demo',
            'primary_repo_of_concern': 'webapp',
            'enriched_signals': [],
        }
        prompt = _build_sales_intelligence_prompt(scan)
        assert 'PREPARING' in prompt
        assert '0.85' in prompt
        assert 'Infrastructure Guide' in prompt


class TestEmailPersonaVariations:
    """Test that different company contexts produce different prompt content."""

    def test_preparing_vs_launched(self):
        from ai_summary import _build_sales_intelligence_prompt
        preparing = _build_sales_intelligence_prompt(_make_scan_data(goldilocks='preparing'))
        launched = _build_sales_intelligence_prompt(_make_scan_data(goldilocks='launched'))
        # Both should reference the goldilocks status
        assert 'PREPARING' in preparing
        assert 'LAUNCHED' in launched

    def test_different_libraries_produce_different_prompts(self):
        from ai_summary import _build_sales_intelligence_prompt
        react = _build_sales_intelligence_prompt(_make_scan_data(libraries=['react-i18next']))
        next_intl = _build_sales_intelligence_prompt(_make_scan_data(libraries=['next-intl']))
        # Both prompts should include the relevant library in the dynamic data section
        assert 'react-i18next' in react
        assert 'next-intl' in next_intl
        # The prompts should differ in the dynamic signal data
        # (template examples also mention react-i18next, so we check the data section)
        assert react != next_intl
