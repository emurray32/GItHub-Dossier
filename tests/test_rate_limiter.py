"""
Tests for rate_limiter.RateLimiter class.

Tests the in-memory sliding window rate limiter in isolation —
no Flask app, no HTTP requests. time.time is mocked throughout
so tests are deterministic and instant.
"""
import math
from collections import defaultdict
from threading import Thread
from unittest.mock import patch

import pytest

from rate_limiter import RateLimiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_limiter(now=1000.0):
    """Create a fresh RateLimiter with _last_cleanup pinned to *now*."""
    with patch('rate_limiter.time') as mock_time:
        mock_time.time.return_value = now
        lim = RateLimiter()
    return lim


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestInit:
    """Tests for RateLimiter.__init__."""

    def test_hits_is_empty_defaultdict(self):
        lim = _make_limiter()
        assert isinstance(lim._hits, defaultdict)
        assert len(lim._hits) == 0

    def test_default_api_limit(self):
        lim = _make_limiter()
        max_req, window = lim.default_api_limit
        assert max_req == 100
        assert window == 60

    def test_route_limits_empty(self):
        lim = _make_limiter()
        assert lim._route_limits == {}

    def test_exempt_prefixes(self):
        lim = _make_limiter()
        assert '/static/' in lim._exempt_prefixes
        assert '/favicon.ico' in lim._exempt_prefixes

    def test_cleanup_interval_default(self):
        lim = _make_limiter()
        assert lim._cleanup_interval == 60

    def test_last_cleanup_set_to_current_time(self):
        """_last_cleanup should be close to the time the object was created."""
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 5000.0
            lim = RateLimiter()
        assert lim._last_cleanup == 5000.0


# ---------------------------------------------------------------------------
# set_route_limit
# ---------------------------------------------------------------------------

class TestSetRouteLimit:
    """Tests for RateLimiter.set_route_limit."""

    def test_basic_set(self):
        lim = _make_limiter()
        lim.set_route_limit('/api/scan', 5, 30)
        assert lim._route_limits['/api/scan'] == (5, 30)

    def test_overwrite_existing(self):
        lim = _make_limiter()
        lim.set_route_limit('/api/scan', 5, 30)
        lim.set_route_limit('/api/scan', 20, 120)
        assert lim._route_limits['/api/scan'] == (20, 120)

    def test_multiple_routes(self):
        lim = _make_limiter()
        lim.set_route_limit('/api/scan', 5, 30)
        lim.set_route_limit('/login', 10, 60)
        lim.set_route_limit('/api/export', 2, 300)
        assert len(lim._route_limits) == 3
        assert lim._route_limits['/login'] == (10, 60)


# ---------------------------------------------------------------------------
# is_rate_limited — basic behaviour
# ---------------------------------------------------------------------------

class TestIsRateLimited:
    """Core tests for is_rate_limited (under / at / over limit)."""

    def test_first_request_allowed(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            assert lim.is_rate_limited('client1:/api', 5, 60) is False

    def test_under_limit_allowed(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(4):
                assert lim.is_rate_limited('client1:/api', 5, 60) is False

    def test_at_limit_blocked(self):
        """The request that would be the (max_requests + 1)th should be blocked."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(5):
                lim.is_rate_limited('client1:/api', 5, 60)
            # 6th request within the same window — should be blocked
            assert lim.is_rate_limited('client1:/api', 5, 60) is True

    def test_exactly_at_max_blocked(self):
        """When exactly max_requests hits exist, next call returns True."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(5):
                result = lim.is_rate_limited('k', 5, 60)
            # The 5th call was still allowed (recorded the 5th hit)
            assert result is False
            # The 6th should be blocked
            assert lim.is_rate_limited('k', 5, 60) is True

    def test_over_limit_stays_blocked(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(5):
                lim.is_rate_limited('k', 5, 60)
            # Repeated calls while blocked still return True
            for _ in range(10):
                assert lim.is_rate_limited('k', 5, 60) is True

    def test_no_hit_recorded_when_blocked(self):
        """When rate-limited, the call should NOT append another timestamp."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(5):
                lim.is_rate_limited('k', 5, 60)
            count_after_fill = len(lim._hits['k'])
            # Blocked call
            lim.is_rate_limited('k', 5, 60)
            assert len(lim._hits['k']) == count_after_fill

    def test_limit_of_one(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            assert lim.is_rate_limited('k', 1, 60) is False
            assert lim.is_rate_limited('k', 1, 60) is True


# ---------------------------------------------------------------------------
# Time window behaviour (mock time.time)
# ---------------------------------------------------------------------------

class TestTimeWindow:
    """Tests verifying that the sliding window expires old timestamps."""

    def test_window_expires_allows_new_requests(self):
        """After the window passes, previously blocked keys should be allowed again."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            # Fill the bucket
            for _ in range(5):
                lim.is_rate_limited('k', 5, 60)
            assert lim.is_rate_limited('k', 5, 60) is True

            # Advance past the 60-second window
            mock_time.time.return_value = 1061.0
            assert lim.is_rate_limited('k', 5, 60) is False

    def test_partial_window_expiry(self):
        """
        Requests made at t=0 and t=30 with a 60s window.
        At t=61 the t=0 entries expire but t=30 entries remain.
        """
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            # 3 hits at t=1000
            mock_time.time.return_value = 1000.0
            for _ in range(3):
                lim.is_rate_limited('k', 5, 60)

            # 2 hits at t=1030
            mock_time.time.return_value = 1030.0
            for _ in range(2):
                lim.is_rate_limited('k', 5, 60)

            # Now at limit (5 hits total within 60s)
            assert lim.is_rate_limited('k', 5, 60) is True

            # At t=1061 the first 3 hits expire — 2 hits remain
            mock_time.time.return_value = 1061.0
            assert lim.is_rate_limited('k', 5, 60) is False  # 3rd hit now
            assert lim.is_rate_limited('k', 5, 60) is False  # 4th
            assert lim.is_rate_limited('k', 5, 60) is False  # 5th — at capacity now
            assert lim.is_rate_limited('k', 5, 60) is True   # 6th — blocked

    def test_very_short_window(self):
        """A 1-second window should expire immediately after 1 second."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            assert lim.is_rate_limited('k', 1, 1) is False
            assert lim.is_rate_limited('k', 1, 1) is True

            mock_time.time.return_value = 1001.1
            assert lim.is_rate_limited('k', 1, 1) is False

    def test_large_window(self):
        """A 1-hour window should hold entries for a long time.

        Note: the cleanup routine has a hardcoded 300s (5 min) cutoff, so
        entries older than 5 minutes are purged regardless of the per-key
        window.  This test uses a time gap of 250s (inside the 300s cutoff)
        to verify that the rate-limit window itself is respected when cleanup
        does not interfere.
        """
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(10):
                lim.is_rate_limited('k', 10, 3600)
            assert lim.is_rate_limited('k', 10, 3600) is True

            # 250 seconds later — within both the 300s cleanup cutoff
            # and the 3600s rate-limit window, so still blocked
            mock_time.time.return_value = 1250.0
            assert lim.is_rate_limited('k', 10, 3600) is True

            # 61 minutes later — all expired (both cleanup and window)
            mock_time.time.return_value = 1000.0 + 3661
            assert lim.is_rate_limited('k', 10, 3600) is False


# ---------------------------------------------------------------------------
# Multiple routes / keys
# ---------------------------------------------------------------------------

class TestMultipleRoutesAndKeys:
    """Different keys and routes should be tracked independently."""

    def test_different_keys_independent(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            # Exhaust limit for client A
            for _ in range(3):
                lim.is_rate_limited('clientA:/api', 3, 60)
            assert lim.is_rate_limited('clientA:/api', 3, 60) is True

            # Client B should still be fine
            assert lim.is_rate_limited('clientB:/api', 3, 60) is False

    def test_same_client_different_routes(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(3):
                lim.is_rate_limited('c1:/api/scan', 3, 60)
            assert lim.is_rate_limited('c1:/api/scan', 3, 60) is True

            # Same client, different route key — should be independent
            assert lim.is_rate_limited('c1:/api/export', 3, 60) is False

    def test_different_limits_per_route(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            # Route A: tight limit (2 / 60s)
            assert lim.is_rate_limited('c1:/routeA', 2, 60) is False
            assert lim.is_rate_limited('c1:/routeA', 2, 60) is False
            assert lim.is_rate_limited('c1:/routeA', 2, 60) is True

            # Route B: generous limit (100 / 60s)
            for _ in range(50):
                assert lim.is_rate_limited('c1:/routeB', 100, 60) is False


# ---------------------------------------------------------------------------
# _hits dictionary clearing
# ---------------------------------------------------------------------------

class TestHitsClearing:
    """Tests for manual _hits.clear() and internal cleanup."""

    def test_manual_clear_resets_everything(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(5):
                lim.is_rate_limited('k', 5, 60)
            assert lim.is_rate_limited('k', 5, 60) is True

            lim._hits.clear()
            # After clearing, the same key should be allowed again
            assert lim.is_rate_limited('k', 5, 60) is False

    def test_clear_one_key_leaves_others(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(5):
                lim.is_rate_limited('a', 5, 60)
                lim.is_rate_limited('b', 5, 60)
            # Both blocked
            assert lim.is_rate_limited('a', 5, 60) is True
            assert lim.is_rate_limited('b', 5, 60) is True

            # Clear only key 'a'
            del lim._hits['a']
            assert lim.is_rate_limited('a', 5, 60) is False
            assert lim.is_rate_limited('b', 5, 60) is True


# ---------------------------------------------------------------------------
# _cleanup_old_entries
# ---------------------------------------------------------------------------

class TestCleanupOldEntries:
    """Tests for the periodic memory cleanup routine."""

    def test_cleanup_skipped_before_interval(self):
        """Cleanup should not run if less than _cleanup_interval has passed."""
        lim = _make_limiter(now=1000.0)
        # Insert a very old entry manually
        lim._hits['old_key'] = [500.0]

        with patch('rate_limiter.time') as mock_time:
            # Only 10 seconds since init — cleanup should NOT fire
            mock_time.time.return_value = 1010.0
            lim._cleanup_old_entries()
        assert 'old_key' in lim._hits

    def test_cleanup_runs_after_interval(self):
        """After _cleanup_interval seconds, old entries should be purged."""
        lim = _make_limiter(now=1000.0)
        # Entry older than 5 minutes (300s) from t=1061
        lim._hits['stale'] = [700.0]
        # Entry still fresh
        lim._hits['fresh'] = [1050.0]

        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1061.0  # 61s past init
            lim._cleanup_old_entries()

        # 'stale' had timestamps at 700, cutoff = 1061 - 300 = 761 -> removed
        assert 'stale' not in lim._hits
        assert 'fresh' in lim._hits
        assert lim._hits['fresh'] == [1050.0]

    def test_cleanup_removes_empty_keys(self):
        """Keys with no remaining timestamps should be deleted entirely."""
        lim = _make_limiter(now=1000.0)
        lim._hits['empty_soon'] = [600.0, 650.0]

        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1061.0
            lim._cleanup_old_entries()
        assert 'empty_soon' not in lim._hits

    def test_cleanup_preserves_recent_timestamps(self):
        """Timestamps within the 300s cutoff should survive cleanup."""
        lim = _make_limiter(now=1000.0)
        lim._hits['mixed'] = [500.0, 900.0, 1050.0]

        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1061.0
            lim._cleanup_old_entries()
        # cutoff = 1061 - 300 = 761 -> 500 and 900 are below?, 900 > 761 ✓
        # Actually: 500 < 761 gone, 900 > 761 kept, 1050 > 761 kept
        assert lim._hits['mixed'] == [900.0, 1050.0]

    def test_cleanup_updates_last_cleanup_time(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1061.0
            lim._cleanup_old_entries()
        assert lim._last_cleanup == 1061.0

    def test_cleanup_triggered_by_is_rate_limited(self):
        """is_rate_limited should trigger cleanup when interval has passed."""
        lim = _make_limiter(now=1000.0)
        lim._hits['ancient'] = [100.0]

        with patch('rate_limiter.time') as mock_time:
            # Jump well past cleanup_interval (60s) and cutoff (300s)
            mock_time.time.return_value = 2000.0
            lim.is_rate_limited('new_key', 10, 60)

        assert 'ancient' not in lim._hits
        assert 'new_key' in lim._hits


# ---------------------------------------------------------------------------
# _get_retry_after
# ---------------------------------------------------------------------------

class TestGetRetryAfter:
    """Tests for the Retry-After header calculation."""

    def test_empty_hits_returns_one(self):
        lim = _make_limiter(now=1000.0)
        result = lim._get_retry_after('nonexistent', 60)
        assert result == 1

    def test_retry_after_calculation(self):
        lim = _make_limiter(now=1000.0)
        lim._hits['k'] = [1000.0, 1005.0, 1010.0]
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1020.0
            # oldest=1000, window=60 -> oldest+window - now = 1060-1020 = 40
            result = lim._get_retry_after('k', 60)
        assert result == 40

    def test_retry_after_minimum_is_one(self):
        """Should never return less than 1."""
        lim = _make_limiter(now=1000.0)
        lim._hits['k'] = [1000.0]
        with patch('rate_limiter.time') as mock_time:
            # oldest + window - now = 1000 + 10 - 1020 = -10 -> clamped to 1
            mock_time.time.return_value = 1020.0
            result = lim._get_retry_after('k', 10)
        assert result == 1

    def test_retry_after_rounds_up(self):
        """Should use math.ceil so the client waits long enough."""
        lim = _make_limiter(now=1000.0)
        lim._hits['k'] = [1000.0]
        with patch('rate_limiter.time') as mock_time:
            # oldest + window - now = 1000 + 60 - 1020.3 = 39.7 -> ceil = 40
            mock_time.time.return_value = 1020.3
            result = lim._get_retry_after('k', 60)
        assert result == 40

    def test_retry_after_uses_oldest_timestamp(self):
        lim = _make_limiter(now=1000.0)
        lim._hits['k'] = [1010.0, 1020.0, 1005.0, 1015.0]
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1030.0
            # min = 1005, 1005 + 60 - 1030 = 35
            result = lim._get_retry_after('k', 60)
        assert result == 35


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_zero_max_requests_always_blocked(self):
        """A limit of 0 requests should block every call."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            assert lim.is_rate_limited('k', 0, 60) is True

    def test_very_large_limit(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            for _ in range(1000):
                assert lim.is_rate_limited('k', 1_000_000, 60) is False

    def test_zero_window_immediate_expiry(self):
        """A 0-second window means all prior timestamps are always expired."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            # With window=0, cutoff = now - 0 = now. Timestamps at exactly
            # `now` are NOT > cutoff (they are equal), so they get pruned.
            # Each call should be allowed because old hits are pruned first.
            for _ in range(10):
                assert lim.is_rate_limited('k', 1, 0) is False

    def test_empty_key(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            assert lim.is_rate_limited('', 5, 60) is False

    def test_unicode_key(self):
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            assert lim.is_rate_limited('192.168.1.1:/api/こんにちは', 5, 60) is False

    def test_negative_window_treated_as_zero(self):
        """Negative window should prune everything (cutoff > now)."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            # cutoff = 1000 - (-10) = 1010 > now, so all timestamps pruned
            for _ in range(5):
                assert lim.is_rate_limited('k', 1, -10) is False

    def test_rapid_successive_calls_same_timestamp(self):
        """Multiple calls at the exact same timestamp should be counted."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 1000.0
            results = [lim.is_rate_limited('k', 3, 60) for _ in range(5)]
        assert results == [False, False, False, True, True]

    def test_hits_defaultdict_auto_creates_key(self):
        """Accessing a new key in _hits should not raise."""
        lim = _make_limiter(now=1000.0)
        # Accessing a missing key should return an empty list (defaultdict)
        assert lim._hits['never_seen'] == []


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """Basic thread-safety smoke tests."""

    def test_concurrent_access_no_crash(self):
        """Multiple threads hammering the same key should not raise."""
        lim = _make_limiter(now=1000.0)
        errors = []

        def _hammer():
            try:
                with patch('rate_limiter.time') as mock_time:
                    mock_time.time.return_value = 1000.0
                    for _ in range(100):
                        lim.is_rate_limited('shared', 50, 60)
            except Exception as e:
                errors.append(e)

        threads = [Thread(target=_hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Threads raised errors: {errors}"

    def test_concurrent_different_keys(self):
        """Different threads using different keys should not interfere."""
        lim = _make_limiter(now=1000.0)
        results = {}

        def _fill(key, limit):
            with patch('rate_limiter.time') as mock_time:
                mock_time.time.return_value = 1000.0
                count = 0
                for _ in range(limit + 5):
                    if not lim.is_rate_limited(key, limit, 60):
                        count += 1
                results[key] = count

        threads = [
            Thread(target=_fill, args=('keyA', 10)),
            Thread(target=_fill, args=('keyB', 20)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results['keyA'] == 10
        assert results['keyB'] == 20


# ---------------------------------------------------------------------------
# Integration-style: full lifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """End-to-end lifecycle tests combining multiple features."""

    def test_fill_expire_refill(self):
        """Fill the bucket, let it expire, then refill."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            # Phase 1: fill
            mock_time.time.return_value = 1000.0
            for _ in range(5):
                lim.is_rate_limited('k', 5, 30)
            assert lim.is_rate_limited('k', 5, 30) is True

            # Phase 2: expire
            mock_time.time.return_value = 1031.0
            assert lim.is_rate_limited('k', 5, 30) is False

            # Phase 3: refill
            for _ in range(4):
                lim.is_rate_limited('k', 5, 30)
            assert lim.is_rate_limited('k', 5, 30) is True

    def test_rolling_window_drip(self):
        """One request per second with a 5/5s limit — should never block."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            for i in range(20):
                mock_time.time.return_value = 1000.0 + i
                assert lim.is_rate_limited('k', 5, 5) is False

    def test_burst_then_drip(self):
        """Burst to limit, then after window send one-at-a-time."""
        lim = _make_limiter(now=1000.0)
        with patch('rate_limiter.time') as mock_time:
            # Burst
            mock_time.time.return_value = 1000.0
            for _ in range(3):
                lim.is_rate_limited('k', 3, 10)
            assert lim.is_rate_limited('k', 3, 10) is True

            # Wait for window to clear, then drip
            for i in range(5):
                mock_time.time.return_value = 1011.0 + i * 11
                assert lim.is_rate_limited('k', 3, 10) is False

    def test_cleanup_during_active_use(self):
        """
        Cleanup fires mid-use without affecting the actively used key.

        Scenario: client uses key for 2 minutes with a 60s window.
        At ~61s cleanup fires, pruning old data but leaving current window intact.
        """
        lim = _make_limiter(now=1000.0)
        lim._cleanup_interval = 30  # more frequent cleanup

        with patch('rate_limiter.time') as mock_time:
            # At t=1000: 3 hits
            mock_time.time.return_value = 1000.0
            for _ in range(3):
                lim.is_rate_limited('k', 5, 60)

            # At t=1040: 2 more hits (total 5 in window)
            mock_time.time.return_value = 1040.0
            for _ in range(2):
                lim.is_rate_limited('k', 5, 60)
            assert lim.is_rate_limited('k', 5, 60) is True

            # At t=1061: first 3 hits expire, cleanup fires, 2 remain
            mock_time.time.return_value = 1061.0
            assert lim.is_rate_limited('k', 5, 60) is False
