"""
Tests for circuit_breaker.py — CircuitBreaker pattern implementation.

Tests cover:
- State transitions: CLOSED -> OPEN -> HALF_OPEN -> CLOSED
- Failure threshold tripping
- Cooldown-based recovery
- Manual force_reset / force_trip controls
- Decorator and context manager usage
- Registry singleton behavior
- Status dict correctness
- on_trip / on_reset callbacks
"""
import time
import pytest
from unittest.mock import MagicMock

from circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitState,
    circuit_breaker,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear the global CircuitBreaker registry between tests."""
    CircuitBreaker._registry.clear()
    yield
    CircuitBreaker._registry.clear()


# ──────────────────────────────────────────────────────────────────────
# Basic state transitions
# ──────────────────────────────────────────────────────────────────────

class TestCircuitBreakerStates:
    """Tests for state transitions and threshold behavior."""

    def test_initial_state_is_closed(self):
        """A new breaker starts in CLOSED state."""
        cb = CircuitBreaker('test-svc', failure_threshold=3, cooldown_seconds=10)
        assert cb.state == CircuitState.CLOSED
        assert cb.is_available() is True

    def test_stays_closed_below_threshold(self):
        """Breaker stays CLOSED when failures are below the threshold."""
        cb = CircuitBreaker('test-svc', failure_threshold=5, cooldown_seconds=10)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_available() is True

    def test_trips_at_threshold(self):
        """Breaker trips to OPEN after exactly failure_threshold consecutive failures."""
        cb = CircuitBreaker('test-svc', failure_threshold=3, cooldown_seconds=10)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.is_available() is False

    def test_open_rejects_via_guard(self):
        """guard() raises CircuitBreakerError when circuit is OPEN."""
        cb = CircuitBreaker('test-svc', failure_threshold=2, cooldown_seconds=600)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        with pytest.raises(CircuitBreakerError) as exc_info:
            cb.guard()
        assert exc_info.value.service == 'test-svc'
        assert exc_info.value.retry_after > 0

    def test_open_to_half_open_after_cooldown(self):
        """OPEN -> HALF_OPEN after cooldown_seconds elapse."""
        cb = CircuitBreaker('test-svc', failure_threshold=2, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Simulate cooldown expiry by backdating _tripped_at
        cb._tripped_at = time.time() - 2
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.is_available() is True

    def test_half_open_to_closed_on_success(self):
        """HALF_OPEN -> CLOSED on a successful call."""
        cb = CircuitBreaker('test-svc', failure_threshold=2, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        cb._tripped_at = time.time() - 2  # Expire cooldown
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._consecutive_failures == 0

    def test_half_open_to_open_on_failure(self):
        """HALF_OPEN -> OPEN on another failure."""
        cb = CircuitBreaker('test-svc', failure_threshold=2, cooldown_seconds=1)
        cb.record_failure()
        cb.record_failure()
        cb._tripped_at = time.time() - 2  # Expire cooldown
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_record_success_resets_failure_count(self):
        """record_success() resets consecutive failures while CLOSED."""
        cb = CircuitBreaker('test-svc', failure_threshold=5, cooldown_seconds=10)
        cb.record_failure()
        cb.record_failure()
        assert cb._consecutive_failures == 2
        cb.record_success()
        assert cb._consecutive_failures == 0
        assert cb.state == CircuitState.CLOSED


# ──────────────────────────────────────────────────────────────────────
# Manual controls
# ──────────────────────────────────────────────────────────────────────

class TestManualControls:
    """Tests for force_reset() and force_trip()."""

    def test_force_reset(self):
        """force_reset() returns breaker to CLOSED regardless of state."""
        cb = CircuitBreaker('test-svc', failure_threshold=2, cooldown_seconds=600)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb.force_reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._consecutive_failures == 0
        assert cb._tripped_at is None

    def test_force_trip(self):
        """force_trip() opens the breaker immediately."""
        cb = CircuitBreaker('test-svc', failure_threshold=100, cooldown_seconds=600)
        assert cb.state == CircuitState.CLOSED

        cb.force_trip()
        assert cb.state == CircuitState.OPEN
        assert cb._total_trips == 1


# ──────────────────────────────────────────────────────────────────────
# Decorator
# ──────────────────────────────────────────────────────────────────────

class TestDecorator:
    """Tests for the @circuit_breaker decorator."""

    def test_decorator_passes_through_on_success(self):
        """Decorated function returns normally when circuit is closed."""
        @circuit_breaker('decorator-test', failure_threshold=3, cooldown_seconds=10)
        def my_func(x):
            return x * 2

        assert my_func(5) == 10

    def test_decorator_trips_on_exceptions(self):
        """Decorated function trips breaker after threshold exceptions."""
        @circuit_breaker('decorator-exc', failure_threshold=2, cooldown_seconds=600)
        def failing_func():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            failing_func()
        with pytest.raises(ValueError):
            failing_func()

        # Breaker should be open now — next call raises CircuitBreakerError
        with pytest.raises(CircuitBreakerError):
            failing_func()

    def test_decorator_records_success(self):
        """Successful calls through decorator reset failure count."""
        @circuit_breaker('decorator-success', failure_threshold=3, cooldown_seconds=10)
        def sometimes_fails(should_fail):
            if should_fail:
                raise RuntimeError("fail")
            return "ok"

        with pytest.raises(RuntimeError):
            sometimes_fails(True)

        cb = CircuitBreaker.get('decorator-success')
        assert cb._consecutive_failures == 1

        assert sometimes_fails(False) == "ok"
        assert cb._consecutive_failures == 0


# ──────────────────────────────────────────────────────────────────────
# Context manager
# ──────────────────────────────────────────────────────────────────────

class TestContextManager:
    """Tests for the context manager protocol."""

    def test_context_manager_records_success(self):
        """Exiting context without exception records success."""
        cb = CircuitBreaker('ctx-svc', failure_threshold=3, cooldown_seconds=10)
        with cb:
            pass  # no exception
        assert cb._total_successes == 1
        assert cb._consecutive_failures == 0

    def test_context_manager_records_failure(self):
        """Exiting context with exception records failure."""
        cb = CircuitBreaker('ctx-fail-svc', failure_threshold=5, cooldown_seconds=10)
        with pytest.raises(RuntimeError):
            with cb:
                raise RuntimeError("test error")
        assert cb._total_failures == 1
        assert cb._consecutive_failures == 1

    def test_context_manager_guard_rejects_when_open(self):
        """Entering context manager raises when circuit is OPEN."""
        cb = CircuitBreaker('ctx-open-svc', failure_threshold=1, cooldown_seconds=600)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        with pytest.raises(CircuitBreakerError):
            with cb:
                pass


# ──────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────

class TestRegistry:
    """Tests for the global registry via CircuitBreaker.get()."""

    def test_get_returns_same_instance(self):
        """get() returns the same instance for the same service name."""
        cb1 = CircuitBreaker.get('reg-svc')
        cb2 = CircuitBreaker.get('reg-svc')
        assert cb1 is cb2

    def test_get_returns_different_instances_for_different_services(self):
        """get() returns different instances for different service names."""
        cb1 = CircuitBreaker.get('svc-a')
        cb2 = CircuitBreaker.get('svc-b')
        assert cb1 is not cb2

    def test_get_all_returns_snapshot(self):
        """get_all() returns all registered breakers."""
        CircuitBreaker.get('all-a')
        CircuitBreaker.get('all-b')
        all_breakers = CircuitBreaker.get_all()
        assert 'all-a' in all_breakers
        assert 'all-b' in all_breakers


# ──────────────────────────────────────────────────────────────────────
# Status dict
# ──────────────────────────────────────────────────────────────────────

class TestStatus:
    """Tests for the status() method."""

    def test_status_dict_fields(self):
        """status() returns all expected fields."""
        cb = CircuitBreaker('status-svc', failure_threshold=3, cooldown_seconds=60)
        s = cb.status()
        assert s['service'] == 'status-svc'
        assert s['state'] == 'closed'
        assert s['consecutive_failures'] == 0
        assert s['failure_threshold'] == 3
        assert s['cooldown_seconds'] == 60
        assert s['retry_after_seconds'] == 0.0
        assert s['tripped_at'] is None
        assert s['total_trips'] == 0
        assert s['total_successes'] == 0
        assert s['total_failures'] == 0

    def test_status_reflects_open_state(self):
        """status() reflects OPEN state with retry_after > 0."""
        cb = CircuitBreaker('status-open', failure_threshold=1, cooldown_seconds=60)
        cb.record_failure()
        s = cb.status()
        assert s['state'] == 'open'
        assert s['retry_after_seconds'] > 0
        assert s['total_trips'] == 1
        assert s['tripped_at'] is not None


# ──────────────────────────────────────────────────────────────────────
# Callbacks
# ──────────────────────────────────────────────────────────────────────

class TestCallbacks:
    """Tests for on_trip and on_reset callbacks."""

    def test_on_trip_fires_when_breaker_trips(self):
        """on_trip callback is called when the breaker trips."""
        trip_mock = MagicMock()
        cb = CircuitBreaker(
            'cb-trip',
            failure_threshold=2,
            cooldown_seconds=10,
            on_trip=trip_mock,
        )
        cb.record_failure()
        trip_mock.assert_not_called()
        cb.record_failure()
        trip_mock.assert_called_once_with('cb-trip')

    def test_on_reset_fires_when_half_open_succeeds(self):
        """on_reset callback is called when HALF_OPEN -> CLOSED transition occurs."""
        reset_mock = MagicMock()
        cb = CircuitBreaker(
            'cb-reset',
            failure_threshold=1,
            cooldown_seconds=1,
            on_reset=reset_mock,
        )
        cb.record_failure()  # Trips
        assert cb.state == CircuitState.OPEN

        # Expire cooldown -> HALF_OPEN
        cb._tripped_at = time.time() - 2
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        reset_mock.assert_called_once_with('cb-reset')
        assert cb.state == CircuitState.CLOSED

    def test_on_reset_not_fired_on_closed_success(self):
        """on_reset callback is NOT called for success in CLOSED state."""
        reset_mock = MagicMock()
        cb = CircuitBreaker(
            'cb-no-reset',
            failure_threshold=5,
            cooldown_seconds=10,
            on_reset=reset_mock,
        )
        cb.record_success()
        reset_mock.assert_not_called()

    def test_callback_exception_is_swallowed(self):
        """Exceptions in callbacks are swallowed and do not propagate."""
        def bad_callback(service):
            raise RuntimeError("callback error")

        cb = CircuitBreaker(
            'cb-bad',
            failure_threshold=1,
            cooldown_seconds=10,
            on_trip=bad_callback,
        )
        # Should not raise despite the bad callback
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
