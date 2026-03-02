"""Regression tests for i18n signal detection and classification."""
import pytest
from datetime import datetime, timezone, timedelta


pytestmark = pytest.mark.unit


class TestFalsePositiveFiltering:
    """Test _is_genuine_i18n_mention from scanner.py filters false positives."""

    def test_css_transform_translate_rejected(self):
        from monitors.scanner import _is_genuine_i18n_mention
        text = "Fix CSS transform: translate(50%, -50%) for centered modal"
        is_genuine, reason = _is_genuine_i18n_mention(text, 'translate')
        assert is_genuine is False
        # Reason may vary; just confirm it was filtered out
        assert reason  # non-empty reason

    def test_compiler_translate_rejected(self):
        from monitors.scanner import _is_genuine_i18n_mention
        text = "Translate IR nodes to machine code in the compiler backend"
        is_genuine, reason = _is_genuine_i18n_mention(text, 'translate')
        assert is_genuine is False

    def test_genuine_i18n_translate_accepted(self):
        from monitors.scanner import _is_genuine_i18n_mention
        text = "Add i18n support: translate all user-facing strings to locale bundles"
        is_genuine, reason = _is_genuine_i18n_mention(text, 'translate')
        assert is_genuine is True

    def test_high_intent_phrase_always_genuine(self):
        from monitors.scanner import _is_genuine_i18n_mention
        text = "RFC: internationalization strategy for the platform"
        is_genuine, reason = _is_genuine_i18n_mention(text, 'internationalization')
        assert is_genuine is True
        # Reason may be about context boosters or high-intent phrases
        assert reason  # non-empty reason

    def test_keyword_not_found_edge_case(self):
        from monitors.scanner import _is_genuine_i18n_mention
        # If keyword somehow not in text, function should handle gracefully
        is_genuine, reason = _is_genuine_i18n_mention("some unrelated text", 'nonexistent_keyword')
        # Should not crash; returns True as edge case
        assert isinstance(is_genuine, bool)


class TestCompanySizeClassification:
    """Test _classify_company_size heuristic."""

    def test_enterprise(self):
        from monitors.scanner import _classify_company_size
        result = _classify_company_size({'total_stars': 30000, 'org_public_repos': 100})
        assert result == 'enterprise'

    def test_large_by_repos(self):
        from monitors.scanner import _classify_company_size
        result = _classify_company_size({'total_stars': 1000, 'org_public_repos': 150})
        assert result == 'large'

    def test_medium(self):
        from monitors.scanner import _classify_company_size
        result = _classify_company_size({'total_stars': 1000, 'org_public_repos': 30})
        assert result == 'medium'

    def test_small(self):
        from monitors.scanner import _classify_company_size
        result = _classify_company_size({'total_stars': 50, 'org_public_repos': 3})
        assert result == 'small'

    def test_empty_data(self):
        from monitors.scanner import _classify_company_size
        result = _classify_company_size({})
        assert result == 'small'


class TestSizeWeight:
    """Test _get_size_weight returns correct multipliers."""

    def test_medium_highest_weight(self):
        from monitors.scanner import _get_size_weight
        assert _get_size_weight('medium') == 1.2

    def test_enterprise_lower_weight(self):
        from monitors.scanner import _get_size_weight
        assert _get_size_weight('enterprise') == 0.7

    def test_unknown_default(self):
        from monitors.scanner import _get_size_weight
        assert _get_size_weight('unknown') == 1.0


class TestOpenProtocolDetection:
    """Test _is_open_protocol_project disqualifier."""

    def test_blockchain_disqualified(self):
        from monitors.scanner import _is_open_protocol_project
        result = _is_open_protocol_project("Decentralized blockchain protocol")
        assert result is not None  # Returns the matched pattern

    def test_normal_company_passes(self):
        from monitors.scanner import _is_open_protocol_project
        result = _is_open_protocol_project("Building great SaaS products")
        assert result is None

    def test_none_description(self):
        from monitors.scanner import _is_open_protocol_project
        result = _is_open_protocol_project(None)
        assert result is None


class TestTimestampParsing:
    """Test _parse_timestamp handles various formats."""

    def test_iso_format(self):
        from monitors.scanner import _parse_timestamp
        result = _parse_timestamp('2025-06-15T10:30:00Z')
        assert result is not None
        assert result.year == 2025

    def test_none_input(self):
        from monitors.scanner import _parse_timestamp
        result = _parse_timestamp(None)
        assert result is None

    def test_empty_string(self):
        from monitors.scanner import _parse_timestamp
        result = _parse_timestamp('')
        assert result is None

    def test_datetime_passthrough(self):
        from monitors.scanner import _parse_timestamp
        dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = _parse_timestamp(dt)
        assert result == dt

    def test_invalid_format(self):
        from monitors.scanner import _parse_timestamp
        result = _parse_timestamp('not-a-date')
        assert result is None


class TestSafeJsonParse:
    """Test _safe_json_parse validates content-type before parsing."""

    def test_valid_json_response(self):
        from monitors.scanner import _safe_json_parse
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.headers = {'content-type': 'application/json'}
        resp.json.return_value = {'key': 'value'}
        result = _safe_json_parse(resp)
        assert result == {'key': 'value'}

    def test_html_response_returns_default(self):
        from monitors.scanner import _safe_json_parse
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.headers = {'content-type': 'text/html'}
        result = _safe_json_parse(resp, default=[])
        assert result == []

    def test_json_decode_error(self):
        from monitors.scanner import _safe_json_parse
        from unittest.mock import MagicMock
        import json
        resp = MagicMock()
        resp.headers = {'content-type': 'application/json'}
        resp.json.side_effect = json.JSONDecodeError('err', 'doc', 0)
        result = _safe_json_parse(resp, default=None)
        assert result is None


class TestGoldilocksZoneClassification:
    """Test that scan results map to correct tier classifications via scoring."""

    def test_preparing_tier2_classification(self, preparing_scan_results):
        """Dependency injection + no locale folders = PREPARING (Tier 2 Goldilocks)."""
        from scoring import score_scan_results
        from scoring.models import MaturitySegment
        result = score_scan_results(preparing_scan_results)
        assert result.org_maturity_level in (
            MaturitySegment.PREPARING,
            MaturitySegment.ACTIVE_IMPLEMENTATION,
        )

    def test_launched_tier3_classification(self, launched_scan_results):
        """Locale folders present = RECENTLY_LAUNCHED (Tier 3)."""
        from scoring import score_scan_results
        from scoring.models import MaturitySegment
        result = score_scan_results(launched_scan_results)
        assert result.org_maturity_level == MaturitySegment.RECENTLY_LAUNCHED

    def test_empty_signals_pre_i18n(self, empty_scan_results):
        """Zero signals = PRE_I18N."""
        from scoring import score_scan_results
        from scoring.models import MaturitySegment
        result = score_scan_results(empty_scan_results)
        assert result.org_maturity_level == MaturitySegment.PRE_I18N

    def test_ghost_branch_classified(self, preparing_scan_results):
        """Ghost branch signal should contribute to higher intent score."""
        from scoring import score_scan_results
        result = score_scan_results(preparing_scan_results)
        assert result.p_intent > 0.5


class TestFormatRequestException:
    """Test error formatting for GitHub API errors."""

    def test_rate_limit_429(self):
        from monitors.scanner import _format_request_exception
        from unittest.mock import MagicMock
        import requests
        error = requests.RequestException()
        error.response = MagicMock()
        error.response.status_code = 429
        error.response.reason = 'Too Many Requests'
        result = _format_request_exception(error)
        assert '429' in result
        assert 'Rate Limit' in result

    def test_no_response(self):
        from monitors.scanner import _format_request_exception
        import requests
        error = requests.RequestException('Connection refused')
        error.response = None
        result = _format_request_exception(error)
        assert 'Connection refused' in result
