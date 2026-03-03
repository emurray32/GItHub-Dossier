"""
Unit tests for scheduler.py — ScanScheduler, signal freshness scoring,
and module-level singleton management.

Tests mock APScheduler and database imports so nothing external runs.
APScheduler is not installed in the test environment, so we inject mock
classes into the scheduler module namespace before constructing ScanScheduler.
"""
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Signal Freshness Scoring (pure functions, no mocks needed)
# ---------------------------------------------------------------------------

from scheduler import (
    calculate_signal_freshness,
    enrich_signal_with_freshness,
    SIGNAL_DECAY_RATES,
    DEFAULT_DECAY_RATE,
)


class TestCalculateSignalFreshness:
    """Tests for the exponential-decay freshness scorer."""

    def test_brand_new_signal_full_strength(self):
        """A signal with age 0 should return raw_strength unchanged."""
        signal = {
            'signal_type': 'dependency_injection',
            'raw_strength': 1.0,
            'age_in_days': 0,
        }
        score = calculate_signal_freshness(signal)
        assert score == 1.0

    def test_decay_reduces_score(self):
        """Older signals should have a lower freshness score."""
        fresh = calculate_signal_freshness({
            'signal_type': 'rfc_discussion',
            'raw_strength': 1.0,
            'age_in_days': 0,
        })
        stale = calculate_signal_freshness({
            'signal_type': 'rfc_discussion',
            'raw_strength': 1.0,
            'age_in_days': 60,
        })
        assert stale < fresh

    def test_known_half_life_rfc_discussion(self):
        """rfc_discussion has lambda=0.020 -> half-life ~35 days."""
        lam = SIGNAL_DECAY_RATES['rfc_discussion']
        half_life_days = math.log(2) / lam  # ~34.7

        score = calculate_signal_freshness({
            'signal_type': 'rfc_discussion',
            'raw_strength': 1.0,
            'age_in_days': int(round(half_life_days)),
        })
        # At the half-life the score should be ~0.5
        assert 0.45 <= score <= 0.55

    def test_known_half_life_dependency_injection(self):
        """dependency_injection has lambda=0.005 -> half-life ~139 days."""
        lam = SIGNAL_DECAY_RATES['dependency_injection']
        half_life_days = math.log(2) / lam

        score = calculate_signal_freshness({
            'signal_type': 'dependency_injection',
            'raw_strength': 1.0,
            'age_in_days': int(round(half_life_days)),
        })
        assert 0.45 <= score <= 0.55

    def test_unknown_signal_type_uses_default_rate(self):
        """An unrecognized signal_type should fall back to DEFAULT_DECAY_RATE."""
        score = calculate_signal_freshness({
            'signal_type': 'totally_unknown_type',
            'raw_strength': 1.0,
            'age_in_days': 50,
        })
        expected = 1.0 * math.exp(-DEFAULT_DECAY_RATE * 50)
        assert score == round(expected, 4)

    def test_missing_raw_strength_defaults_by_priority(self):
        """When raw_strength is absent, HIGH priority = 1.0, else 0.7."""
        high = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'priority': 'HIGH',
            'age_in_days': 0,
        })
        assert high == 1.0

        medium = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'priority': 'MEDIUM',
            'age_in_days': 0,
        })
        assert medium == 0.7

    def test_missing_age_defaults_to_zero(self):
        """If both age_in_days and created_at are missing, treat as brand-new."""
        score = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'raw_strength': 0.8,
        })
        assert score == 0.8

    def test_age_computed_from_created_at_string(self):
        """age_in_days should be computed from an ISO created_at string."""
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        score = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'raw_strength': 1.0,
            'created_at': ten_days_ago,
        })
        lam = SIGNAL_DECAY_RATES['ghost_branch']
        expected = 1.0 * math.exp(-lam * 10)
        # Allow 1-day rounding tolerance
        assert abs(score - round(expected, 4)) <= 0.005

    def test_age_computed_from_created_at_with_z_suffix(self):
        """created_at with 'Z' suffix should parse correctly."""
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).strftime(
            '%Y-%m-%dT%H:%M:%SZ'
        )
        score = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'raw_strength': 1.0,
            'created_at': five_days_ago,
        })
        assert 0.0 < score <= 1.0

    def test_age_computed_from_timestamp_field(self):
        """Fallback to 'timestamp' field when 'created_at' is absent."""
        ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        score = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'raw_strength': 1.0,
            'timestamp': ts,
        })
        assert score < 1.0

    def test_age_computed_from_datetime_object(self):
        """created_at may be a datetime object, not just a string."""
        dt = datetime.now(timezone.utc) - timedelta(days=30)
        score = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'raw_strength': 1.0,
            'created_at': dt,
        })
        assert score < 1.0

    def test_naive_datetime_treated_as_utc(self):
        """A naive datetime (no tzinfo) should be treated as UTC."""
        dt = datetime.utcnow() - timedelta(days=15)
        score = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'raw_strength': 1.0,
            'created_at': dt,
        })
        assert 0.0 < score < 1.0

    def test_invalid_created_at_defaults_age_zero(self):
        """Unparseable created_at should silently default to age=0."""
        score = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'raw_strength': 1.0,
            'created_at': 'not-a-date',
        })
        assert score == 1.0

    def test_type_field_fallback(self):
        """'type' is used when 'signal_type' is missing."""
        score = calculate_signal_freshness({
            'type': 'job_posting_intent',
            'raw_strength': 1.0,
            'age_in_days': 10,
        })
        lam = SIGNAL_DECAY_RATES['job_posting_intent']
        expected = round(1.0 * math.exp(-lam * 10), 4)
        assert score == expected

    def test_score_never_negative(self):
        """Even for very old signals the score must be >= 0."""
        score = calculate_signal_freshness({
            'signal_type': 'rfc_discussion',
            'raw_strength': 1.0,
            'age_in_days': 100000,
        })
        assert score >= 0.0

    def test_score_rounded_to_four_decimals(self):
        """Scores should be rounded to 4 decimal places."""
        score = calculate_signal_freshness({
            'signal_type': 'ghost_branch',
            'raw_strength': 1.0,
            'age_in_days': 17,
        })
        assert score == round(score, 4)

    def test_all_known_signal_types_have_decay_rates(self):
        """Every key in SIGNAL_DECAY_RATES should produce valid scores."""
        for signal_type, lam in SIGNAL_DECAY_RATES.items():
            score = calculate_signal_freshness({
                'signal_type': signal_type,
                'raw_strength': 1.0,
                'age_in_days': 50,
            })
            assert 0.0 < score <= 1.0, f"Invalid score for {signal_type}: {score}"


class TestEnrichSignalWithFreshness:
    """Tests for enrich_signal_with_freshness() — non-mutating enrichment."""

    def test_adds_freshness_score(self):
        """Should add 'freshness_score' to the returned dict."""
        signal = {'signal_type': 'ghost_branch', 'raw_strength': 1.0, 'age_in_days': 0}
        enriched = enrich_signal_with_freshness(signal)
        assert 'freshness_score' in enriched
        assert enriched['freshness_score'] == 1.0

    def test_non_mutating(self):
        """Original signal dict should not be modified."""
        signal = {'signal_type': 'ghost_branch', 'raw_strength': 0.9, 'age_in_days': 10}
        original_keys = set(signal.keys())
        enrich_signal_with_freshness(signal)
        assert set(signal.keys()) == original_keys
        assert 'freshness_score' not in signal

    def test_adds_age_in_days_when_missing(self):
        """If 'age_in_days' is not in the signal, it should be computed and added."""
        ts = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        signal = {'signal_type': 'ghost_branch', 'raw_strength': 1.0, 'created_at': ts}
        enriched = enrich_signal_with_freshness(signal)
        assert 'age_in_days' in enriched
        assert enriched['age_in_days'] >= 6  # Allow some rounding

    def test_preserves_existing_age_in_days(self):
        """If age_in_days is already present, it should remain unchanged."""
        signal = {'signal_type': 'ghost_branch', 'raw_strength': 1.0, 'age_in_days': 42}
        enriched = enrich_signal_with_freshness(signal)
        assert enriched['age_in_days'] == 42

    def test_age_defaults_to_zero_no_timestamp(self):
        """With no timestamp fields at all, age_in_days should be 0."""
        signal = {'signal_type': 'ghost_branch', 'raw_strength': 1.0}
        enriched = enrich_signal_with_freshness(signal)
        assert enriched['age_in_days'] == 0

    def test_preserves_all_original_fields(self):
        """All original signal fields should be present in the enriched copy."""
        signal = {
            'signal_type': 'rfc_discussion',
            'raw_strength': 0.8,
            'age_in_days': 5,
            'extra_field': 'should_survive',
        }
        enriched = enrich_signal_with_freshness(signal)
        assert enriched['extra_field'] == 'should_survive'
        assert enriched['signal_type'] == 'rfc_discussion'
        assert enriched['raw_strength'] == 0.8


# ---------------------------------------------------------------------------
# ScanScheduler — mocked APScheduler + database
#
# Since APScheduler is not installed in the test environment, we inject mock
# classes into the scheduler module before each test that needs ScanScheduler.
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_apscheduler(monkeypatch):
    """Inject mock APScheduler classes into the scheduler module and set
    _HAS_APSCHEDULER to True so ScanScheduler can be constructed."""
    import scheduler

    mock_bg_scheduler = MagicMock()
    mock_interval_trigger = MagicMock()
    mock_cron_trigger = MagicMock()

    monkeypatch.setattr(scheduler, '_HAS_APSCHEDULER', True)
    monkeypatch.setattr(scheduler, 'BackgroundScheduler', mock_bg_scheduler, raising=False)
    monkeypatch.setattr(scheduler, 'IntervalTrigger', mock_interval_trigger, raising=False)
    monkeypatch.setattr(scheduler, 'CronTrigger', mock_cron_trigger, raising=False)
    monkeypatch.setattr(scheduler, 'EVENT_JOB_EXECUTED', 1, raising=False)
    monkeypatch.setattr(scheduler, 'EVENT_JOB_ERROR', 2, raising=False)

    return mock_bg_scheduler


class TestScanSchedulerInit:
    """Test ScanScheduler construction and configuration."""

    def test_init_creates_scheduler(self, mock_apscheduler):
        """ScanScheduler() should instantiate a BackgroundScheduler."""
        from scheduler import ScanScheduler
        sched = ScanScheduler(app=None)
        mock_apscheduler.assert_called_once()
        assert sched._app is None
        assert sched._spawn_scan_fn is None

    def test_init_raises_without_apscheduler(self, monkeypatch):
        """Should raise ImportError if APScheduler is not installed."""
        import scheduler
        monkeypatch.setattr(scheduler, '_HAS_APSCHEDULER', False)
        with pytest.raises(ImportError, match="APScheduler is required"):
            scheduler.ScanScheduler(app=None)

    def test_configure_sets_spawn_fn(self, mock_apscheduler):
        """configure() should store the spawn function and limits."""
        from scheduler import ScanScheduler
        sched = ScanScheduler()
        dummy_fn = MagicMock()
        sched.configure(spawn_scan_fn=dummy_fn, max_workers=10,
                        check_interval_hours=12, max_per_cycle=50)

        assert sched._spawn_scan_fn is dummy_fn
        assert sched._max_workers == 10
        assert sched._state['check_interval_hours'] == 12
        assert sched._state['max_per_cycle'] == 50

    def test_default_state_values(self, mock_apscheduler):
        """Initial state should have sane defaults."""
        from scheduler import ScanScheduler
        sched = ScanScheduler()
        assert sched._state['enabled'] is True
        assert sched._state['started_at'] is None
        assert sched._state['last_cycle_at'] is None
        assert sched._state['last_queued_count'] == 0
        assert sched._state['total_queued_lifetime'] == 0
        assert sched._state['cycles_run'] == 0
        assert sched._state['check_interval_hours'] == 6
        assert sched._state['max_per_cycle'] == 100
        assert sched._state['errors'] == []


class TestScanSchedulerStart:
    """Test start/shutdown lifecycle with mocked scheduler."""

    def test_start_requires_configure(self, mock_apscheduler):
        """start() should raise RuntimeError if configure() was not called."""
        from scheduler import ScanScheduler
        sched = ScanScheduler()
        with pytest.raises(RuntimeError, match="configure.*must be called"):
            sched.start()

    @patch('scheduler._ensure_db_imports')
    def test_start_adds_jobs_and_starts(self, mock_db, mock_apscheduler):
        """start() should add two jobs and call scheduler.start()."""
        from scheduler import ScanScheduler
        mock_db.return_value = {'TIER_SCAN_INTERVALS': {1: 7, 2: 3}}

        sched = ScanScheduler()
        sched.configure(spawn_scan_fn=MagicMock())
        sched.start()

        # The internal _scheduler is the mock instance returned by BackgroundScheduler()
        mock_instance = mock_apscheduler.return_value
        # Should add at least 2 jobs (rescan + health check)
        assert mock_instance.add_job.call_count == 2
        mock_instance.start.assert_called_once()
        assert sched._state['started_at'] is not None

    def test_shutdown_stops_scheduler(self, mock_apscheduler):
        """shutdown() should call scheduler.shutdown()."""
        from scheduler import ScanScheduler
        mock_instance = mock_apscheduler.return_value
        mock_instance.running = True

        sched = ScanScheduler()
        sched.shutdown()

        mock_instance.shutdown.assert_called_once_with(wait=True)

    def test_shutdown_noop_when_not_running(self, mock_apscheduler):
        """shutdown() should be a no-op if the scheduler is not running."""
        from scheduler import ScanScheduler
        mock_instance = mock_apscheduler.return_value
        mock_instance.running = False

        sched = ScanScheduler()
        sched.shutdown()

        mock_instance.shutdown.assert_not_called()


class TestScanSchedulerPauseResume:
    """Test pause/resume toggling."""

    def test_pause_disables_and_pauses_scheduler(self, mock_apscheduler):
        from scheduler import ScanScheduler
        mock_instance = mock_apscheduler.return_value

        sched = ScanScheduler()
        sched.pause()

        assert sched._state['enabled'] is False
        mock_instance.pause.assert_called_once()

    def test_resume_enables_and_resumes_scheduler(self, mock_apscheduler):
        from scheduler import ScanScheduler
        mock_instance = mock_apscheduler.return_value

        sched = ScanScheduler()
        sched.pause()
        sched.resume()

        assert sched._state['enabled'] is True
        mock_instance.resume.assert_called_once()


class TestScanSchedulerGetStatus:
    """Test get_status() return structure."""

    @patch('scheduler._ensure_db_imports')
    def test_get_status_structure(self, mock_db, mock_apscheduler):
        """get_status() should return scheduler/intervals/tiers/jobs keys."""
        from scheduler import ScanScheduler
        mock_instance = mock_apscheduler.return_value
        mock_instance.get_jobs.return_value = []

        mock_db.return_value = {
            'get_scheduled_rescan_summary': MagicMock(return_value={1: 5, 2: 3}),
            'TIER_SCAN_INTERVALS': {1: 7, 2: 3, 3: 14},
        }

        sched = ScanScheduler()
        status = sched.get_status()

        assert 'scheduler' in status
        assert 'intervals' in status
        assert 'tiers' in status
        assert 'jobs' in status
        assert isinstance(status['scheduler'], dict)
        assert isinstance(status['jobs'], list)

    @patch('scheduler._ensure_db_imports')
    def test_get_status_includes_job_details(self, mock_db, mock_apscheduler):
        """get_status() should include job id, name, next_run, pending."""
        from scheduler import ScanScheduler

        mock_job = MagicMock()
        mock_job.id = 'tier_rescan_cycle'
        mock_job.name = 'Tier-aware rescan cycle'
        mock_job.next_run_time = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)
        mock_job.pending = False

        mock_instance = mock_apscheduler.return_value
        mock_instance.get_jobs.return_value = [mock_job]

        mock_db.return_value = {
            'get_scheduled_rescan_summary': MagicMock(return_value={}),
            'TIER_SCAN_INTERVALS': {},
        }

        sched = ScanScheduler()
        status = sched.get_status()

        assert len(status['jobs']) == 1
        job_info = status['jobs'][0]
        assert job_info['id'] == 'tier_rescan_cycle'
        assert job_info['name'] == 'Tier-aware rescan cycle'
        assert job_info['next_run'] is not None
        assert job_info['pending'] is False

    @patch('scheduler._ensure_db_imports')
    def test_get_status_handles_db_error(self, mock_db, mock_apscheduler):
        """If the DB call fails, tiers should contain an error key."""
        from scheduler import ScanScheduler
        mock_instance = mock_apscheduler.return_value
        mock_instance.get_jobs.return_value = []

        mock_db.return_value = {
            'get_scheduled_rescan_summary': MagicMock(
                side_effect=Exception("DB connection failed")
            ),
            'TIER_SCAN_INTERVALS': {},
        }

        sched = ScanScheduler()
        status = sched.get_status()

        assert 'error' in status['tiers']


class TestScanSchedulerUpdateConfig:
    """Test runtime config updates."""

    def test_update_max_per_cycle(self, mock_apscheduler):
        from scheduler import ScanScheduler
        sched = ScanScheduler()
        sched.update_config(max_per_cycle=200)
        assert sched._state['max_per_cycle'] == 200

    def test_update_max_per_cycle_clamps(self, mock_apscheduler):
        """max_per_cycle should be clamped between 1 and 500."""
        from scheduler import ScanScheduler
        sched = ScanScheduler()
        sched.update_config(max_per_cycle=0)
        assert sched._state['max_per_cycle'] == 1

        sched.update_config(max_per_cycle=9999)
        assert sched._state['max_per_cycle'] == 500

    def test_update_check_interval_clamps(self, mock_apscheduler):
        """check_interval_hours should be clamped between 1 and 48."""
        from scheduler import ScanScheduler
        sched = ScanScheduler()
        sched.update_config(check_interval_hours=0)
        assert sched._state['check_interval_hours'] == 1

        sched.update_config(check_interval_hours=100)
        assert sched._state['check_interval_hours'] == 48

    def test_update_interval_reschedules_job(self, mock_apscheduler):
        """Changing check_interval_hours while running should reschedule."""
        from scheduler import ScanScheduler
        mock_instance = mock_apscheduler.return_value
        mock_instance.running = True

        sched = ScanScheduler()
        sched.update_config(check_interval_hours=12)

        mock_instance.reschedule_job.assert_called_once()
        args = mock_instance.reschedule_job.call_args
        assert args[0][0] == 'tier_rescan_cycle'


class TestScanSchedulerRescanCycle:
    """Test the _rescan_cycle logic with mocked DB and spawn function."""

    @patch('scheduler._ensure_db_imports')
    @patch('scheduler.time.sleep', return_value=None)
    def test_rescan_cycle_queues_accounts(self, mock_sleep, mock_db, mock_apscheduler):
        """_rescan_cycle should call spawn for each due account."""
        from scheduler import ScanScheduler

        mock_db.return_value = {
            'get_refreshable_accounts': MagicMock(return_value=[
                {'company_name': 'AlphaCorp'},
                {'company_name': 'BetaCorp'},
            ]),
        }

        spawn_fn = MagicMock()
        sched = ScanScheduler()
        sched.configure(spawn_scan_fn=spawn_fn)

        result = sched._rescan_cycle()

        assert result['queued'] == 2
        assert result['remaining'] == 0
        assert result['errors'] == 0
        assert spawn_fn.call_count == 2
        assert sched._state['cycles_run'] == 1
        assert sched._state['total_queued_lifetime'] == 2

    @patch('scheduler._ensure_db_imports')
    @patch('scheduler.time.sleep', return_value=None)
    def test_rescan_cycle_skips_when_paused(self, mock_sleep, mock_db, mock_apscheduler):
        """_rescan_cycle should return early if enabled is False."""
        from scheduler import ScanScheduler

        sched = ScanScheduler()
        sched.configure(spawn_scan_fn=MagicMock())
        sched._state['enabled'] = False

        result = sched._rescan_cycle()
        assert result['skipped'] is True

    @patch('scheduler._ensure_db_imports')
    @patch('scheduler.time.sleep', return_value=None)
    def test_rescan_cycle_respects_max_per_cycle(self, mock_sleep, mock_db, mock_apscheduler):
        """Only max_per_cycle accounts should be queued."""
        from scheduler import ScanScheduler

        accounts = [{'company_name': f'Corp{i}'} for i in range(10)]
        mock_db.return_value = {
            'get_refreshable_accounts': MagicMock(return_value=accounts),
        }

        spawn_fn = MagicMock()
        sched = ScanScheduler()
        sched.configure(spawn_scan_fn=spawn_fn, max_per_cycle=3)

        result = sched._rescan_cycle()

        assert result['queued'] == 3
        assert result['remaining'] == 7
        assert spawn_fn.call_count == 3

    @patch('scheduler._ensure_db_imports')
    @patch('scheduler.time.sleep', return_value=None)
    def test_rescan_cycle_handles_spawn_failure(self, mock_sleep, mock_db, mock_apscheduler):
        """If spawn_scan_fn raises, the error should be recorded."""
        from scheduler import ScanScheduler

        mock_db.return_value = {
            'get_refreshable_accounts': MagicMock(return_value=[
                {'company_name': 'FailCorp'},
                {'company_name': 'OkCorp'},
            ]),
        }

        spawn_fn = MagicMock(side_effect=[Exception("boom"), None])
        sched = ScanScheduler()
        sched.configure(spawn_scan_fn=spawn_fn)

        result = sched._rescan_cycle()

        assert result['queued'] == 1
        assert result['errors'] == 1
        assert len(sched._state['errors']) >= 1

    @patch('scheduler._ensure_db_imports')
    @patch('scheduler.time.sleep', return_value=None)
    def test_rescan_cycle_skips_empty_company_name(self, mock_sleep, mock_db, mock_apscheduler):
        """Accounts without company_name should be skipped."""
        from scheduler import ScanScheduler

        mock_db.return_value = {
            'get_refreshable_accounts': MagicMock(return_value=[
                {'company_name': ''},
                {'company_name': None},
                {'company_name': 'GoodCorp'},
            ]),
        }

        spawn_fn = MagicMock()
        sched = ScanScheduler()
        sched.configure(spawn_scan_fn=spawn_fn)

        result = sched._rescan_cycle()
        assert result['queued'] == 1

    @patch('scheduler._ensure_db_imports')
    def test_rescan_cycle_handles_db_error(self, mock_db, mock_apscheduler):
        """If get_refreshable_accounts raises, the cycle should return an error."""
        from scheduler import ScanScheduler

        mock_db.return_value = {
            'get_refreshable_accounts': MagicMock(
                side_effect=Exception("DB down")
            ),
        }

        sched = ScanScheduler()
        sched.configure(spawn_scan_fn=MagicMock())

        result = sched._rescan_cycle()
        assert 'error' in result


class TestScanSchedulerIsRunning:
    """Test the is_running property."""

    def test_is_running_delegates_to_scheduler(self, mock_apscheduler):
        from scheduler import ScanScheduler
        mock_instance = mock_apscheduler.return_value
        mock_instance.running = True

        sched = ScanScheduler()
        assert sched.is_running is True

        mock_instance.running = False
        assert sched.is_running is False


class TestScanSchedulerTriggerNow:
    """Test trigger_now() which calls _rescan_cycle immediately."""

    @patch('scheduler._ensure_db_imports')
    @patch('scheduler.time.sleep', return_value=None)
    def test_trigger_now_runs_cycle(self, mock_sleep, mock_db, mock_apscheduler):
        from scheduler import ScanScheduler

        mock_db.return_value = {
            'get_refreshable_accounts': MagicMock(return_value=[]),
        }

        sched = ScanScheduler()
        sched.configure(spawn_scan_fn=MagicMock())

        result = sched.trigger_now()
        assert result['queued'] == 0


# ---------------------------------------------------------------------------
# Module-level singleton functions
# ---------------------------------------------------------------------------

class TestModuleSingleton:
    """Test get_scan_scheduler / init_scan_scheduler / shutdown_scan_scheduler."""

    def setup_method(self):
        """Reset the module-level singleton before each test."""
        import scheduler
        scheduler._scan_scheduler = None

    def test_get_scan_scheduler_returns_none_before_init(self):
        from scheduler import get_scan_scheduler
        assert get_scan_scheduler() is None

    @patch('scheduler._ensure_db_imports')
    def test_init_creates_and_starts_scheduler(self, mock_db, mock_apscheduler):
        from scheduler import init_scan_scheduler, get_scan_scheduler
        mock_db.return_value = {'TIER_SCAN_INTERVALS': {}}

        app_mock = MagicMock()
        spawn_mock = MagicMock()

        result = init_scan_scheduler(app_mock, spawn_mock, max_workers=5)
        assert result is not None
        assert get_scan_scheduler() is result

    def test_shutdown_clears_singleton(self, mock_apscheduler):
        import scheduler
        mock_instance = mock_apscheduler.return_value
        mock_instance.running = True

        instance = scheduler.ScanScheduler()
        scheduler._scan_scheduler = instance

        scheduler.shutdown_scan_scheduler()
        assert scheduler._scan_scheduler is None


class TestOnJobEvent:
    """Test the APScheduler event listener."""

    def test_on_job_event_logs_error(self, mock_apscheduler):
        """_on_job_event should log when a job fails."""
        from scheduler import ScanScheduler
        sched = ScanScheduler()
        event = MagicMock()
        event.exception = RuntimeError("job failed")
        event.job_id = 'test_job'

        # Should not raise
        sched._on_job_event(event)

    def test_on_job_event_no_error(self, mock_apscheduler):
        """_on_job_event should not log when the job succeeds."""
        from scheduler import ScanScheduler
        sched = ScanScheduler()
        event = MagicMock()
        event.exception = None
        event.job_id = 'test_job'

        # Should not raise
        sched._on_job_event(event)
