"""
Tests for TokenPool and TokenStatus classes from utils.py.

Covers initialization, best-token selection, preemptive rotation,
rate-limit handling, cooldown/reset logic, and pool status reporting.
"""
import time
from unittest.mock import patch

import pytest

from utils import TokenPool, TokenStatus


# ---------------------------------------------------------------------------
# TokenStatus dataclass tests
# ---------------------------------------------------------------------------

class TestTokenStatus:
    """Tests for the TokenStatus dataclass and its computed properties."""

    def test_default_values(self):
        ts = TokenStatus(token="ghp_abcdefghij1234567890abcdefghij12")
        assert ts.remaining == 5000
        assert ts.limit == 5000
        assert ts.reset_time == 0
        assert ts.last_used == 0.0
        assert ts.request_count == 0
        assert ts.is_rate_limited is False

    def test_masked_token_long(self):
        ts = TokenStatus(token="ghp_abcdefghij1234567890abcdefghij12")
        assert ts.masked_token == "ghp_...ij12"
        assert "abcdefghij" not in ts.masked_token

    def test_masked_token_short(self):
        ts = TokenStatus(token="abcd1234")
        # Exactly 8 chars -- not > 8, so falls through to "****"
        assert ts.masked_token == "****"

    def test_masked_token_very_short(self):
        ts = TokenStatus(token="abc")
        assert ts.masked_token == "****"

    def test_masked_token_nine_chars(self):
        ts = TokenStatus(token="123456789")
        assert ts.masked_token == "1234...6789"

    def test_usage_percent_fresh(self):
        ts = TokenStatus(token="t", remaining=5000, limit=5000)
        assert ts.usage_percent == 0.0

    def test_usage_percent_half(self):
        ts = TokenStatus(token="t", remaining=2500, limit=5000)
        assert ts.usage_percent == 50.0

    def test_usage_percent_exhausted(self):
        ts = TokenStatus(token="t", remaining=0, limit=5000)
        assert ts.usage_percent == 100.0

    def test_usage_percent_zero_limit(self):
        ts = TokenStatus(token="t", remaining=0, limit=0)
        assert ts.usage_percent == 100.0


# ---------------------------------------------------------------------------
# Helper to build a TokenPool without touching Config
# ---------------------------------------------------------------------------

def _make_pool(tokens_list):
    """Create a TokenPool pre-loaded with the given token strings.

    Bypasses Config.GITHUB_TOKENS by patching _load_tokens, then
    manually injects TokenStatus objects.
    """
    with patch.object(TokenPool, '_load_tokens'):
        pool = TokenPool()
    for tok in tokens_list:
        pool._tokens[tok] = TokenStatus(token=tok)
    return pool


def _make_pool_with_statuses(statuses):
    """Create a TokenPool pre-loaded with custom TokenStatus objects.

    ``statuses`` is a dict mapping token string -> TokenStatus.
    """
    with patch.object(TokenPool, '_load_tokens'):
        pool = TokenPool()
    pool._tokens = dict(statuses)
    return pool


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestTokenPoolInit:
    """Tests for TokenPool initialization."""

    def test_init_with_single_token(self):
        with patch('utils.Config') as mock_cfg:
            mock_cfg.GITHUB_TOKENS = ["tok_a"]
            pool = TokenPool()
        assert pool.get_token_count() == 1
        assert "tok_a" in pool._tokens

    def test_init_with_multiple_tokens(self):
        with patch('utils.Config') as mock_cfg:
            mock_cfg.GITHUB_TOKENS = ["tok_a", "tok_b", "tok_c"]
            pool = TokenPool()
        assert pool.get_token_count() == 3

    def test_init_with_no_tokens(self):
        with patch('utils.Config') as mock_cfg:
            mock_cfg.GITHUB_TOKENS = []
            pool = TokenPool()
        assert pool.get_token_count() == 0

    def test_init_with_none_tokens(self):
        with patch('utils.Config') as mock_cfg:
            mock_cfg.GITHUB_TOKENS = None
            pool = TokenPool()
        assert pool.get_token_count() == 0

    def test_init_skips_empty_strings(self):
        with patch('utils.Config') as mock_cfg:
            mock_cfg.GITHUB_TOKENS = ["tok_a", "", "tok_b"]
            pool = TokenPool()
        assert pool.get_token_count() == 2

    def test_init_skips_duplicates(self):
        with patch('utils.Config') as mock_cfg:
            mock_cfg.GITHUB_TOKENS = ["tok_a", "tok_a", "tok_b"]
            pool = TokenPool()
        # The second "tok_a" is skipped by the `token not in self._tokens` guard
        assert pool.get_token_count() == 2


# ---------------------------------------------------------------------------
# get_best_token — basic selection
# ---------------------------------------------------------------------------

class TestGetBestToken:
    """Tests for get_best_token() selection strategy."""

    def test_returns_none_when_empty(self):
        pool = _make_pool([])
        assert pool.get_best_token() is None

    @patch('utils.time.time', return_value=1000.0)
    def test_single_token_returned(self, _mock_time):
        pool = _make_pool(["tok_a"])
        assert pool.get_best_token() == "tok_a"

    @patch('utils.time.time', return_value=1000.0)
    def test_selects_highest_remaining(self, _mock_time):
        pool = _make_pool_with_statuses({
            "tok_a": TokenStatus(token="tok_a", remaining=1000),
            "tok_b": TokenStatus(token="tok_b", remaining=4000),
            "tok_c": TokenStatus(token="tok_c", remaining=2500),
        })
        assert pool.get_best_token() == "tok_b"

    @patch('utils.time.time', return_value=1000.0)
    def test_tie_broken_by_last_used_oldest_first(self, _mock_time):
        pool = _make_pool_with_statuses({
            "tok_a": TokenStatus(token="tok_a", remaining=3000, last_used=500.0),
            "tok_b": TokenStatus(token="tok_b", remaining=3000, last_used=100.0),
        })
        # Same remaining -- tok_b was used longer ago, so it wins
        assert pool.get_best_token() == "tok_b"

    @patch('utils.time.time', return_value=1000.0)
    def test_updates_last_used_and_request_count(self, _mock_time):
        pool = _make_pool(["tok_a"])
        pool.get_best_token()
        status = pool._tokens["tok_a"]
        assert status.last_used == 1000.0
        assert status.request_count == 1

    @patch('utils.time.time', return_value=2000.0)
    def test_request_count_increments(self, _mock_time):
        pool = _make_pool(["tok_a"])
        pool.get_best_token()
        pool.get_best_token()
        pool.get_best_token()
        assert pool._tokens["tok_a"].request_count == 3


# ---------------------------------------------------------------------------
# Preemptive rotation threshold
# ---------------------------------------------------------------------------

class TestPreemptiveRotation:
    """Tests for preemptive rotation when a token drops below the threshold."""

    def test_threshold_is_50(self):
        assert TokenPool.PREEMPTIVE_ROTATION_THRESHOLD == 50

    @patch('utils.time.time', return_value=1000.0)
    def test_low_token_deprioritized_when_fresh_exists(self, _mock_time):
        pool = _make_pool_with_statuses({
            "low": TokenStatus(token="low", remaining=30),    # below 50
            "fresh": TokenStatus(token="fresh", remaining=4000),
        })
        # "fresh" should be selected because "low" is below threshold
        assert pool.get_best_token() == "fresh"

    @patch('utils.time.time', return_value=1000.0)
    def test_low_token_used_when_only_option(self, _mock_time):
        pool = _make_pool_with_statuses({
            "low": TokenStatus(token="low", remaining=30),
        })
        # No fresh tokens -- low-quota token is the only choice
        assert pool.get_best_token() == "low"

    @patch('utils.time.time', return_value=1000.0)
    def test_best_low_token_selected_among_multiple_low(self, _mock_time):
        pool = _make_pool_with_statuses({
            "low_a": TokenStatus(token="low_a", remaining=10),
            "low_b": TokenStatus(token="low_b", remaining=40),
        })
        # Both below 50, but low_b has more remaining
        assert pool.get_best_token() == "low_b"

    @patch('utils.time.time', return_value=1000.0)
    def test_exactly_50_remaining_is_low_quota(self, _mock_time):
        """A token with remaining == 50 should be classified as low_quota
        (the condition is `remaining <= PREEMPTIVE_ROTATION_THRESHOLD`)."""
        pool = _make_pool_with_statuses({
            "threshold": TokenStatus(token="threshold", remaining=50),
            "fresh": TokenStatus(token="fresh", remaining=3000),
        })
        assert pool.get_best_token() == "fresh"

    @patch('utils.time.time', return_value=1000.0)
    def test_51_remaining_is_available(self, _mock_time):
        """A token with remaining == 51 should still be in the available bucket."""
        pool = _make_pool_with_statuses({
            "just_above": TokenStatus(token="just_above", remaining=51),
            "fresh": TokenStatus(token="fresh", remaining=3000),
        })
        # fresh has more remaining so it wins, but "just_above" is in available not low_quota
        assert pool.get_best_token() == "fresh"
        # Call again -- fresh was updated; verify just_above is still selectable as available
        pool._tokens["fresh"].remaining = 51
        pool._tokens["just_above"].last_used = 0.0
        pool._tokens["fresh"].last_used = 1000.0
        best = pool.get_best_token()
        # Both at 51 remaining, "just_above" last_used=0 wins by tie-break
        assert best == "just_above"


# ---------------------------------------------------------------------------
# update_token_status
# ---------------------------------------------------------------------------

class TestUpdateTokenStatus:
    """Tests for update_token_status()."""

    def test_updates_fields(self):
        pool = _make_pool(["tok_a"])
        pool.update_token_status("tok_a", remaining=2000, limit=5000, reset_time=9999)
        s = pool._tokens["tok_a"]
        assert s.remaining == 2000
        assert s.limit == 5000
        assert s.reset_time == 9999
        assert s.is_rate_limited is False

    def test_marks_rate_limited_when_zero(self):
        pool = _make_pool(["tok_a"])
        pool.update_token_status("tok_a", remaining=0, limit=5000, reset_time=9999)
        assert pool._tokens["tok_a"].is_rate_limited is True

    def test_ignores_unknown_token(self):
        pool = _make_pool(["tok_a"])
        # Should not raise or create a new entry
        pool.update_token_status("unknown", remaining=100, limit=5000, reset_time=0)
        assert "unknown" not in pool._tokens

    def test_successive_updates(self):
        pool = _make_pool(["tok_a"])
        pool.update_token_status("tok_a", remaining=3000, limit=5000, reset_time=1000)
        pool.update_token_status("tok_a", remaining=2999, limit=5000, reset_time=1000)
        assert pool._tokens["tok_a"].remaining == 2999


# ---------------------------------------------------------------------------
# mark_rate_limited
# ---------------------------------------------------------------------------

class TestMarkRateLimited:
    """Tests for mark_rate_limited()."""

    def test_marks_token(self):
        pool = _make_pool(["tok_a"])
        pool.mark_rate_limited("tok_a", reset_time=5000)
        s = pool._tokens["tok_a"]
        assert s.remaining == 0
        assert s.is_rate_limited is True
        assert s.reset_time == 5000

    def test_ignores_unknown_token(self):
        pool = _make_pool(["tok_a"])
        pool.mark_rate_limited("unknown", reset_time=5000)
        assert "unknown" not in pool._tokens


# ---------------------------------------------------------------------------
# Rate-limited token handling
# ---------------------------------------------------------------------------

class TestRateLimitedTokenHandling:
    """Tests for how get_best_token handles rate-limited tokens."""

    @patch('utils.time.time', return_value=1000.0)
    def test_skips_rate_limited_token(self, _mock_time):
        pool = _make_pool_with_statuses({
            "limited": TokenStatus(token="limited", remaining=0, is_rate_limited=True, reset_time=2000),
            "fresh": TokenStatus(token="fresh", remaining=4000),
        })
        assert pool.get_best_token() == "fresh"

    @patch('utils.time.time', return_value=1000.0)
    def test_skips_zero_remaining_token(self, _mock_time):
        """A token with remaining=0 but is_rate_limited=False is still skipped."""
        pool = _make_pool_with_statuses({
            "empty": TokenStatus(token="empty", remaining=0, is_rate_limited=False),
            "fresh": TokenStatus(token="fresh", remaining=3000),
        })
        assert pool.get_best_token() == "fresh"

    @patch('utils.time.time', return_value=1000.0)
    def test_rate_limited_negative_remaining(self, _mock_time):
        pool = _make_pool_with_statuses({
            "neg": TokenStatus(token="neg", remaining=-1, is_rate_limited=True, reset_time=2000),
            "ok": TokenStatus(token="ok", remaining=1000),
        })
        assert pool.get_best_token() == "ok"


# ---------------------------------------------------------------------------
# All tokens rate-limited — soonest reset wins
# ---------------------------------------------------------------------------

class TestAllTokensRateLimited:
    """When every token is rate-limited, get_best_token returns the one
    that resets soonest (smallest reset_time)."""

    @patch('utils.time.time', return_value=1000.0)
    def test_returns_soonest_resetting(self, _mock_time):
        pool = _make_pool_with_statuses({
            "a": TokenStatus(token="a", remaining=0, is_rate_limited=True, reset_time=5000),
            "b": TokenStatus(token="b", remaining=0, is_rate_limited=True, reset_time=3000),
            "c": TokenStatus(token="c", remaining=0, is_rate_limited=True, reset_time=4000),
        })
        assert pool.get_best_token() == "b"

    @patch('utils.time.time', return_value=1000.0)
    def test_single_rate_limited_returned(self, _mock_time):
        pool = _make_pool_with_statuses({
            "only": TokenStatus(token="only", remaining=0, is_rate_limited=True, reset_time=9999),
        })
        assert pool.get_best_token() == "only"

    @patch('utils.time.time', return_value=1000.0)
    def test_does_not_update_last_used_for_rate_limited(self, _mock_time):
        """When all tokens are rate-limited, the returned token's last_used
        and request_count should NOT be bumped (the code just returns the
        token without touching the status)."""
        pool = _make_pool_with_statuses({
            "a": TokenStatus(token="a", remaining=0, is_rate_limited=True,
                             reset_time=2000, last_used=500.0, request_count=10),
        })
        pool.get_best_token()
        assert pool._tokens["a"].last_used == 500.0
        assert pool._tokens["a"].request_count == 10


# ---------------------------------------------------------------------------
# Cooldown / reset recovery
# ---------------------------------------------------------------------------

class TestCooldownRecovery:
    """Tests for automatic recovery when a rate-limited token's reset_time
    has passed."""

    @patch('utils.time.time', return_value=3000.0)
    def test_token_recovers_after_reset_time(self, _mock_time):
        """A rate-limited token whose reset_time has passed should be
        treated as available with limit restored."""
        pool = _make_pool_with_statuses({
            "was_limited": TokenStatus(
                token="was_limited",
                remaining=0,
                is_rate_limited=True,
                reset_time=2000,  # already passed (now=3000)
                limit=5000,
            ),
        })
        result = pool.get_best_token()
        assert result == "was_limited"
        s = pool._tokens["was_limited"]
        assert s.is_rate_limited is False
        assert s.remaining == 5000  # reset to full limit

    @patch('utils.time.time', return_value=3000.0)
    def test_recovered_token_competes_normally(self, _mock_time):
        pool = _make_pool_with_statuses({
            "recovered": TokenStatus(
                token="recovered", remaining=0, is_rate_limited=True,
                reset_time=2000, limit=5000,
            ),
            "partial": TokenStatus(token="partial", remaining=2000),
        })
        # After recovery, "recovered" has 5000 remaining vs "partial" at 2000
        assert pool.get_best_token() == "recovered"

    @patch('utils.time.time', return_value=1000.0)
    def test_token_does_not_recover_before_reset(self, _mock_time):
        pool = _make_pool_with_statuses({
            "still_limited": TokenStatus(
                token="still_limited", remaining=0, is_rate_limited=True,
                reset_time=2000,  # not yet (now=1000)
            ),
            "ok": TokenStatus(token="ok", remaining=3000),
        })
        assert pool.get_best_token() == "ok"
        assert pool._tokens["still_limited"].is_rate_limited is True

    @patch('utils.time.time', return_value=2000.0)
    def test_recovery_at_exact_reset_time(self, _mock_time):
        """Edge case: now == reset_time should trigger recovery
        (condition is `now >= reset_time`)."""
        pool = _make_pool_with_statuses({
            "exact": TokenStatus(
                token="exact", remaining=0, is_rate_limited=True,
                reset_time=2000, limit=5000,
            ),
        })
        assert pool.get_best_token() == "exact"
        assert pool._tokens["exact"].is_rate_limited is False


# ---------------------------------------------------------------------------
# has_available_tokens
# ---------------------------------------------------------------------------

class TestHasAvailableTokens:
    """Tests for has_available_tokens()."""

    def test_empty_pool(self):
        pool = _make_pool([])
        assert pool.has_available_tokens() is False

    @patch('utils.time.time', return_value=1000.0)
    def test_fresh_token_available(self, _mock_time):
        pool = _make_pool(["tok_a"])
        assert pool.has_available_tokens() is True

    @patch('utils.time.time', return_value=1000.0)
    def test_all_rate_limited_not_reset(self, _mock_time):
        pool = _make_pool_with_statuses({
            "a": TokenStatus(token="a", remaining=0, is_rate_limited=True, reset_time=5000),
        })
        assert pool.has_available_tokens() is False

    @patch('utils.time.time', return_value=6000.0)
    def test_rate_limited_but_reset_passed(self, _mock_time):
        pool = _make_pool_with_statuses({
            "a": TokenStatus(token="a", remaining=0, is_rate_limited=True, reset_time=5000),
        })
        # Reset time has passed -- should count as available
        assert pool.has_available_tokens() is True


# ---------------------------------------------------------------------------
# get_pool_status
# ---------------------------------------------------------------------------

class TestGetPoolStatus:
    """Tests for get_pool_status() reporting."""

    def test_empty_pool(self):
        pool = _make_pool([])
        status = pool.get_pool_status()
        assert status['pool_size'] == 0
        assert status['tokens_available'] == 0
        assert status['tokens_rate_limited'] == 0
        assert status['total_remaining'] == 0
        assert status['total_limit'] == 0
        assert status['token_details'] == []

    @patch('utils.time.time', return_value=1000.0)
    def test_single_fresh_token(self, _mock_time):
        pool = _make_pool(["tok_a"])
        status = pool.get_pool_status()
        assert status['pool_size'] == 1
        assert status['tokens_available'] == 1
        assert status['tokens_rate_limited'] == 0
        assert status['total_remaining'] == 5000
        assert status['total_limit'] == 5000
        assert status['effective_hourly_capacity'] == 5000

        detail = status['token_details'][0]
        assert detail['remaining'] == 5000
        assert detail['limit'] == 5000
        assert detail['usage_percent'] == 0.0
        assert detail['is_rate_limited'] is False
        assert detail['resets_in_seconds'] == 0

    @patch('utils.time.time', return_value=1000.0)
    def test_mixed_tokens(self, _mock_time):
        pool = _make_pool_with_statuses({
            "fresh": TokenStatus(token="fresh", remaining=4000, limit=5000),
            "limited": TokenStatus(
                token="limited", remaining=0, limit=5000,
                is_rate_limited=True, reset_time=1500,
            ),
        })
        status = pool.get_pool_status()
        assert status['pool_size'] == 2
        assert status['tokens_available'] == 1
        assert status['tokens_rate_limited'] == 1
        assert status['total_remaining'] == 4000
        assert status['total_limit'] == 10000
        assert status['effective_hourly_capacity'] == 10000

    @patch('utils.time.time', return_value=1000.0)
    def test_resets_in_seconds_calculation(self, _mock_time):
        pool = _make_pool_with_statuses({
            "limited": TokenStatus(
                token="limited", remaining=0, limit=5000,
                is_rate_limited=True, reset_time=1300,
            ),
        })
        status = pool.get_pool_status()
        detail = status['token_details'][0]
        assert detail['resets_in_seconds'] == 300  # 1300 - 1000

    @patch('utils.time.time', return_value=2000.0)
    def test_resets_in_seconds_never_negative(self, _mock_time):
        pool = _make_pool_with_statuses({
            "limited": TokenStatus(
                token="limited", remaining=0, limit=5000,
                is_rate_limited=True, reset_time=1000,
            ),
        })
        status = pool.get_pool_status()
        detail = status['token_details'][0]
        # reset_time already passed, max(0, 1000 - 2000) = 0
        assert detail['resets_in_seconds'] == 0

    @patch('utils.time.time', return_value=1000.0)
    def test_token_details_contain_masked_token(self, _mock_time):
        pool = _make_pool(["ghp_abcdefghij1234567890abcdefghij12"])
        status = pool.get_pool_status()
        detail = status['token_details'][0]
        assert detail['token'] == "ghp_...ij12"

    @patch('utils.time.time', return_value=1000.0)
    def test_request_count_reported(self, _mock_time):
        pool = _make_pool(["tok_a"])
        pool._tokens["tok_a"].request_count = 42
        status = pool.get_pool_status()
        assert status['token_details'][0]['request_count'] == 42

    @patch('utils.time.time', return_value=1000.0)
    def test_usage_percent_reported(self, _mock_time):
        pool = _make_pool_with_statuses({
            "tok": TokenStatus(token="tok", remaining=2500, limit=5000),
        })
        status = pool.get_pool_status()
        assert status['token_details'][0]['usage_percent'] == 50.0


# ---------------------------------------------------------------------------
# get_token_count
# ---------------------------------------------------------------------------

class TestGetTokenCount:

    def test_count_matches_loaded(self):
        pool = _make_pool(["a", "b", "c"])
        assert pool.get_token_count() == 3

    def test_count_zero_for_empty(self):
        pool = _make_pool([])
        assert pool.get_token_count() == 0


# ---------------------------------------------------------------------------
# reload_tokens
# ---------------------------------------------------------------------------

class TestReloadTokens:
    """Tests for reload_tokens() adding new tokens at runtime."""

    def test_adds_new_tokens(self):
        pool = _make_pool(["tok_a"])
        with patch('utils.Config') as mock_cfg:
            mock_cfg.get_github_tokens.return_value = ["tok_a", "tok_b"]
            pool.reload_tokens()
        assert pool.get_token_count() == 2
        assert "tok_b" in pool._tokens

    def test_preserves_existing_status(self):
        pool = _make_pool(["tok_a"])
        pool._tokens["tok_a"].remaining = 1234
        pool._tokens["tok_a"].request_count = 99
        with patch('utils.Config') as mock_cfg:
            mock_cfg.get_github_tokens.return_value = ["tok_a", "tok_b"]
            pool.reload_tokens()
        # tok_a stats should be preserved
        assert pool._tokens["tok_a"].remaining == 1234
        assert pool._tokens["tok_a"].request_count == 99
        # tok_b is new with defaults
        assert pool._tokens["tok_b"].remaining == 5000
        assert pool._tokens["tok_b"].request_count == 0


# ---------------------------------------------------------------------------
# Complex / integration-style scenarios
# ---------------------------------------------------------------------------

class TestComplexScenarios:
    """Multi-step scenarios exercising several features together."""

    @patch('utils.time.time', return_value=1000.0)
    def test_full_lifecycle(self, _mock_time):
        """Simulate: fresh -> usage -> rate-limited -> recovery."""
        pool = _make_pool(["tok_a", "tok_b"])

        # Both fresh -- tok_a or tok_b selected (both at 5000)
        first = pool.get_best_token()
        assert first in ("tok_a", "tok_b")

        # Simulate API response: tok first picked has 2000 remaining
        pool.update_token_status(first, remaining=2000, limit=5000, reset_time=2000)

        # Next call should prefer the untouched token (5000 remaining)
        second = pool.get_best_token()
        other = "tok_b" if first == "tok_a" else "tok_a"
        assert second == other

        # Drain the second token
        pool.update_token_status(second, remaining=0, limit=5000, reset_time=3000)
        assert pool._tokens[second].is_rate_limited is True

        # Now first (2000 remaining) should be selected
        assert pool.get_best_token() == first

        # Drain first too -- mark both rate-limited
        pool.mark_rate_limited(first, reset_time=2000)

        # All limited: soonest reset wins -- first resets at 2000, second at 3000
        assert pool.get_best_token() == first

    @patch('utils.time.time', return_value=1000.0)
    def test_gradual_depletion_across_pool(self, _mock_time):
        """Tokens are gradually depleted; pool correctly rotates through them."""
        pool = _make_pool_with_statuses({
            "a": TokenStatus(token="a", remaining=100),
            "b": TokenStatus(token="b", remaining=200),
            "c": TokenStatus(token="c", remaining=300),
        })

        # c has highest remaining
        assert pool.get_best_token() == "c"
        pool.update_token_status("c", remaining=200, limit=5000, reset_time=9999)

        # Now b and c are tied at 200, but a is at 100
        # b was last_used=0 vs c was last_used=1000 -- b wins on tie-break
        assert pool.get_best_token() == "b"

    @patch('utils.time.time', return_value=1000.0)
    def test_three_tiers(self, _mock_time):
        """Pool with tokens in all three buckets: available, low, rate-limited."""
        pool = _make_pool_with_statuses({
            "available": TokenStatus(token="available", remaining=3000),
            "low": TokenStatus(token="low", remaining=30),
            "limited": TokenStatus(
                token="limited", remaining=0,
                is_rate_limited=True, reset_time=5000,
            ),
        })
        # Available bucket wins
        assert pool.get_best_token() == "available"

        # Drain available into low territory
        pool.update_token_status("available", remaining=20, limit=5000, reset_time=5000)

        # Now both "available" (20) and "low" (30) are in low_quota bucket
        # "low" has more remaining (30 > 20)
        assert pool.get_best_token() == "low"

        # Rate-limit both low-quota tokens
        pool.mark_rate_limited("available", reset_time=6000)
        pool.mark_rate_limited("low", reset_time=7000)

        # All rate-limited: "limited" resets at 5000 (soonest)
        assert pool.get_best_token() == "limited"
