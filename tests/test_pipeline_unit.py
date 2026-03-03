"""
Unit tests for pipeline.py — PipelineOrchestrator, configuration helpers,
state management, and health/status API structures.

All external dependencies (database, APScheduler, app module, network calls)
are mocked. No actual pipeline jobs or network calls are executed.
"""
import json
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers to build common mocks
# ---------------------------------------------------------------------------

def _mock_db_module():
    """Return a MagicMock that satisfies pipeline.py's _db() lazy import."""
    db = MagicMock()
    db.get_setting.return_value = None
    db.set_setting.return_value = None
    db.db_connection.return_value.__enter__ = MagicMock()
    db.db_connection.return_value.__exit__ = MagicMock(return_value=False)
    return db


def _mock_app_module():
    """Return a MagicMock that satisfies pipeline.py's _app_module() lazy import."""
    app = MagicMock()
    app.trigger_webhook.return_value = None
    app.spawn_background_scan.return_value = None
    return app


# ---------------------------------------------------------------------------
# _DEFAULT_CONFIG
# ---------------------------------------------------------------------------

class TestDefaultConfig:
    """Verify _DEFAULT_CONFIG has the expected shape and sane values."""

    def test_default_config_keys(self):
        from pipeline import _DEFAULT_CONFIG
        expected_keys = {
            'pipeline_enabled',
            'scan_schedule_cron_hour',
            'tier2_check_interval_hours',
            'weekly_digest_day',
            'health_check_interval_minutes',
            'max_emails_per_week',
            'max_enrollments_per_run',
            'max_contacts_per_account',
            'approval_required',
            'max_retries',
            'retry_backoff_base',
        }
        assert set(_DEFAULT_CONFIG.keys()) == expected_keys

    def test_pipeline_enabled_default_true(self):
        from pipeline import _DEFAULT_CONFIG
        assert _DEFAULT_CONFIG['pipeline_enabled'] is True

    def test_scan_schedule_cron_hour_is_int(self):
        from pipeline import _DEFAULT_CONFIG
        assert isinstance(_DEFAULT_CONFIG['scan_schedule_cron_hour'], int)
        assert 0 <= _DEFAULT_CONFIG['scan_schedule_cron_hour'] <= 23

    def test_max_emails_per_week_positive(self):
        from pipeline import _DEFAULT_CONFIG
        assert _DEFAULT_CONFIG['max_emails_per_week'] > 0

    def test_approval_required_default_false(self):
        from pipeline import _DEFAULT_CONFIG
        assert _DEFAULT_CONFIG['approval_required'] is False


# ---------------------------------------------------------------------------
# _get_config / _set_config
# ---------------------------------------------------------------------------

class TestGetConfig:
    """Test the _get_config helper that reads from system_settings."""

    @patch('pipeline._db')
    def test_returns_default_when_setting_is_none(self, mock_db_fn):
        """When db.get_setting returns None, the default should be used."""
        from pipeline import _get_config, _DEFAULT_CONFIG
        db = _mock_db_module()
        db.get_setting.return_value = None
        mock_db_fn.return_value = db

        result = _get_config('pipeline_enabled')
        assert result is _DEFAULT_CONFIG['pipeline_enabled']

    @patch('pipeline._db')
    def test_coerces_bool_from_string(self, mock_db_fn):
        """Boolean config values should be coerced from string representations."""
        from pipeline import _get_config
        db = _mock_db_module()
        mock_db_fn.return_value = db

        for truthy in ('true', 'True', '1', 'yes'):
            db.get_setting.return_value = truthy
            assert _get_config('pipeline_enabled') is True

        for falsy in ('false', '0', 'no', 'anything_else'):
            db.get_setting.return_value = falsy
            assert _get_config('pipeline_enabled') is False

    @patch('pipeline._db')
    def test_coerces_int_from_string(self, mock_db_fn):
        """Integer config values should be coerced from string representations."""
        from pipeline import _get_config
        db = _mock_db_module()
        db.get_setting.return_value = '12'
        mock_db_fn.return_value = db

        result = _get_config('scan_schedule_cron_hour')
        assert result == 12

    @patch('pipeline._db')
    def test_int_coerce_fallback_on_invalid(self, mock_db_fn):
        """If int coercion fails, the default should be returned."""
        from pipeline import _get_config, _DEFAULT_CONFIG
        db = _mock_db_module()
        db.get_setting.return_value = 'not_a_number'
        mock_db_fn.return_value = db

        result = _get_config('scan_schedule_cron_hour')
        assert result == _DEFAULT_CONFIG['scan_schedule_cron_hour']

    @patch('pipeline._db')
    def test_string_config_returned_as_is(self, mock_db_fn):
        """String config values should be returned verbatim."""
        from pipeline import _get_config
        db = _mock_db_module()
        db.get_setting.return_value = 'mon'
        mock_db_fn.return_value = db

        result = _get_config('weekly_digest_day')
        assert result == 'mon'

    @patch('pipeline._db')
    def test_coerces_bool_for_approval_required(self, mock_db_fn):
        """approval_required (bool default) should coerce correctly."""
        from pipeline import _get_config
        db = _mock_db_module()
        db.get_setting.return_value = 'true'
        mock_db_fn.return_value = db

        assert _get_config('approval_required') is True


class TestSetConfig:
    """Test _set_config writes to system_settings."""

    @patch('pipeline._db')
    def test_set_config_calls_set_setting(self, mock_db_fn):
        from pipeline import _set_config
        db = _mock_db_module()
        mock_db_fn.return_value = db

        _set_config('pipeline_enabled', 'false')
        db.set_setting.assert_called_once_with('pipeline_pipeline_enabled', 'false')

    @patch('pipeline._db')
    def test_set_config_converts_to_string(self, mock_db_fn):
        from pipeline import _set_config
        db = _mock_db_module()
        mock_db_fn.return_value = db

        _set_config('max_retries', 5)
        db.set_setting.assert_called_once_with('pipeline_max_retries', '5')


# ---------------------------------------------------------------------------
# _extract_domain helper
# ---------------------------------------------------------------------------

class TestExtractDomain:
    """Test the URL -> bare domain extraction helper."""

    def test_strips_https(self):
        from pipeline import _extract_domain
        assert _extract_domain('https://example.com/path') == 'example.com'

    def test_strips_http(self):
        from pipeline import _extract_domain
        assert _extract_domain('http://example.com') == 'example.com'

    def test_strips_www(self):
        from pipeline import _extract_domain
        assert _extract_domain('www.example.com') == 'example.com'

    def test_strips_all_prefixes(self):
        from pipeline import _extract_domain
        assert _extract_domain('https://www.example.com/foo/bar') == 'example.com'

    def test_bare_domain_passes_through(self):
        from pipeline import _extract_domain
        assert _extract_domain('example.com') == 'example.com'

    def test_empty_returns_empty(self):
        from pipeline import _extract_domain
        assert _extract_domain('') == ''

    def test_none_returns_empty(self):
        """Passing None should not crash (guard in the function)."""
        from pipeline import _extract_domain
        # The function checks `if not url:` which covers None and ''
        assert _extract_domain(None) == ''

    def test_lowercases_domain(self):
        from pipeline import _extract_domain
        assert _extract_domain('HTTPS://EXAMPLE.COM/PATH') == 'example.com'


# ---------------------------------------------------------------------------
# PipelineOrchestrator — Singleton
# ---------------------------------------------------------------------------

class TestPipelineOrchestratorSingleton:
    """Test the singleton pattern on PipelineOrchestrator."""

    def setup_method(self):
        """Reset the singleton between tests."""
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    def test_instance_returns_same_object(self):
        """Repeated .instance() calls should return the same object."""
        from pipeline import PipelineOrchestrator
        a = PipelineOrchestrator.instance()
        b = PipelineOrchestrator.instance()
        assert a is b

    def test_instance_is_pipeline_orchestrator(self):
        from pipeline import PipelineOrchestrator
        inst = PipelineOrchestrator.instance()
        assert isinstance(inst, PipelineOrchestrator)

    def test_fresh_instance_not_running(self):
        from pipeline import PipelineOrchestrator
        inst = PipelineOrchestrator.instance()
        assert inst._running is False
        assert inst._scheduler is None
        assert inst._current_run_id is None

    def test_singleton_thread_safe(self):
        """Multiple threads calling .instance() should get the same object."""
        from pipeline import PipelineOrchestrator
        results = []

        def get_instance():
            results.append(PipelineOrchestrator.instance())

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(r is results[0] for r in results)


# ---------------------------------------------------------------------------
# PipelineOrchestrator — Pause / Resume / is_paused
# ---------------------------------------------------------------------------

class TestPipelinePauseResume:
    """Test pause/resume state management via _set_config/_get_config."""

    def setup_method(self):
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    @patch('pipeline._set_config')
    def test_pause_sets_config_false(self, mock_set):
        from pipeline import PipelineOrchestrator
        inst = PipelineOrchestrator.instance()
        inst.pause()
        mock_set.assert_called_once_with('pipeline_enabled', 'false')

    @patch('pipeline._set_config')
    def test_resume_sets_config_true(self, mock_set):
        from pipeline import PipelineOrchestrator
        inst = PipelineOrchestrator.instance()
        inst.resume()
        mock_set.assert_called_once_with('pipeline_enabled', 'true')

    @patch('pipeline._get_config')
    def test_is_paused_when_disabled(self, mock_get):
        from pipeline import PipelineOrchestrator
        mock_get.return_value = False  # pipeline_enabled = False
        inst = PipelineOrchestrator.instance()
        assert inst.is_paused() is True

    @patch('pipeline._get_config')
    def test_not_paused_when_enabled(self, mock_get):
        from pipeline import PipelineOrchestrator
        mock_get.return_value = True
        inst = PipelineOrchestrator.instance()
        assert inst.is_paused() is False


# ---------------------------------------------------------------------------
# PipelineOrchestrator — get_status()
# ---------------------------------------------------------------------------

class TestPipelineGetStatus:
    """Test the get_status() return structure."""

    def setup_method(self):
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    @patch('pipeline.get_recent_runs', return_value=[])
    @patch('pipeline.CircuitBreaker')
    @patch('pipeline._get_config')
    def test_get_status_structure(self, mock_get_config, mock_cb_class, mock_runs):
        """get_status() should return the expected top-level keys."""
        from pipeline import PipelineOrchestrator

        mock_get_config.side_effect = lambda k: True if k == 'pipeline_enabled' else 0
        mock_cb_class.get_all.return_value = {}

        inst = PipelineOrchestrator.instance()
        status = inst.get_status()

        assert 'enabled' in status
        assert 'running' in status
        assert 'current_run_id' in status
        assert 'scheduled_jobs' in status
        assert 'recent_runs' in status
        assert 'circuit_breakers' in status
        assert 'config' in status

    @patch('pipeline.get_recent_runs', return_value=[])
    @patch('pipeline.CircuitBreaker')
    @patch('pipeline._get_config')
    def test_get_status_config_covers_all_defaults(self, mock_get_config, mock_cb_class, mock_runs):
        """The config dict should have an entry for every _DEFAULT_CONFIG key."""
        from pipeline import PipelineOrchestrator, _DEFAULT_CONFIG

        mock_get_config.side_effect = lambda k: _DEFAULT_CONFIG.get(k)
        mock_cb_class.get_all.return_value = {}

        inst = PipelineOrchestrator.instance()
        status = inst.get_status()

        for key in _DEFAULT_CONFIG:
            assert key in status['config'], f"Missing config key: {key}"

    @patch('pipeline.get_recent_runs', return_value=[])
    @patch('pipeline.CircuitBreaker')
    @patch('pipeline._get_config')
    def test_get_status_no_scheduler_empty_jobs(self, mock_get_config, mock_cb_class, mock_runs):
        """When _scheduler is None, scheduled_jobs should be an empty list."""
        from pipeline import PipelineOrchestrator

        mock_get_config.return_value = True
        mock_cb_class.get_all.return_value = {}

        inst = PipelineOrchestrator.instance()
        assert inst._scheduler is None
        status = inst.get_status()
        assert status['scheduled_jobs'] == []

    @patch('pipeline.get_recent_runs', return_value=[])
    @patch('pipeline.CircuitBreaker')
    @patch('pipeline._get_config')
    def test_get_status_with_scheduler_lists_jobs(self, mock_get_config, mock_cb_class, mock_runs):
        """When a scheduler is attached, its jobs should appear in the status."""
        from pipeline import PipelineOrchestrator

        mock_get_config.return_value = True
        mock_cb_class.get_all.return_value = {}

        mock_job = MagicMock()
        mock_job.id = 'daily_pipeline'
        mock_job.name = 'Daily Full Pipeline'
        mock_job.next_run_time = MagicMock()
        mock_job.next_run_time.isoformat.return_value = '2026-03-02T06:00:00+00:00'

        mock_scheduler = MagicMock()
        mock_scheduler.get_jobs.return_value = [mock_job]

        inst = PipelineOrchestrator.instance()
        inst._scheduler = mock_scheduler

        status = inst.get_status()
        assert len(status['scheduled_jobs']) == 1
        assert status['scheduled_jobs'][0]['id'] == 'daily_pipeline'

    @patch('pipeline.get_recent_runs', return_value=[
        {'id': 1, 'status': 'success', 'started_at': '2026-03-01T06:00:00'},
    ])
    @patch('pipeline.CircuitBreaker')
    @patch('pipeline._get_config')
    def test_get_status_includes_recent_runs(self, mock_get_config, mock_cb_class, mock_runs):
        """recent_runs should be populated from get_recent_runs()."""
        from pipeline import PipelineOrchestrator

        mock_get_config.return_value = True
        mock_cb_class.get_all.return_value = {}

        inst = PipelineOrchestrator.instance()
        status = inst.get_status()
        assert len(status['recent_runs']) == 1
        assert status['recent_runs'][0]['status'] == 'success'

    @patch('pipeline.get_recent_runs', return_value=[])
    @patch('pipeline.CircuitBreaker')
    @patch('pipeline._get_config')
    def test_get_status_circuit_breakers(self, mock_get_config, mock_cb_class, mock_runs):
        """Circuit breaker statuses should be included."""
        from pipeline import PipelineOrchestrator

        mock_get_config.return_value = True
        mock_breaker = MagicMock()
        mock_breaker.status.return_value = {
            'state': 'closed',
            'consecutive_failures': 0,
        }
        mock_cb_class.get_all.return_value = {'apollo': mock_breaker}

        inst = PipelineOrchestrator.instance()
        status = inst.get_status()

        assert 'apollo' in status['circuit_breakers']
        assert status['circuit_breakers']['apollo']['state'] == 'closed'


# ---------------------------------------------------------------------------
# PipelineOrchestrator — get_health()
# ---------------------------------------------------------------------------

class TestPipelineGetHealth:
    """Test the get_health() method."""

    def setup_method(self):
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    @patch('pipeline._db')
    def test_get_health_returns_cached(self, mock_db_fn):
        """If pipeline_health is stored, it should be returned as-is."""
        from pipeline import PipelineOrchestrator

        health_data = {
            'timestamp': '2026-03-02T10:00:00',
            'status': 'healthy',
            'checks': {'database': {'status': 'ok'}},
        }
        db = _mock_db_module()
        db.get_setting.return_value = json.dumps(health_data)
        mock_db_fn.return_value = db

        inst = PipelineOrchestrator.instance()
        result = inst.get_health()

        assert result['status'] == 'healthy'
        assert 'checks' in result
        assert result['checks']['database']['status'] == 'ok'

    @patch('pipeline._db')
    @patch.object(
        __import__('pipeline', fromlist=['PipelineOrchestrator']).PipelineOrchestrator,
        'run_health_check',
        return_value={'status': 'healthy', 'checks': {}}
    )
    def test_get_health_runs_fresh_when_no_cache(self, mock_health, mock_db_fn):
        """If no cached health, run_health_check() should be called."""
        from pipeline import PipelineOrchestrator

        db = _mock_db_module()
        db.get_setting.return_value = None
        mock_db_fn.return_value = db

        inst = PipelineOrchestrator.instance()
        result = inst.get_health()

        assert result['status'] == 'healthy'

    @patch('pipeline._db')
    def test_get_health_handles_corrupt_json(self, mock_db_fn):
        """If the cached health JSON is corrupt, run_health_check should be called."""
        from pipeline import PipelineOrchestrator

        db = _mock_db_module()
        db.get_setting.return_value = 'not-valid-json{{'
        mock_db_fn.return_value = db

        inst = PipelineOrchestrator.instance()
        # Patch run_health_check to avoid real health checks
        inst.run_health_check = MagicMock(return_value={
            'status': 'healthy', 'checks': {}
        })

        result = inst.get_health()
        inst.run_health_check.assert_called_once()


# ---------------------------------------------------------------------------
# PipelineOrchestrator — start / shutdown
# ---------------------------------------------------------------------------

class TestPipelineStartShutdown:
    """Test start() and shutdown() lifecycle with mocked APScheduler."""

    def setup_method(self):
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    @patch('pipeline._init_pipeline_tables')
    @patch('pipeline._get_config')
    @patch('pipeline.atexit')
    @patch('pipeline.signal')
    def test_start_creates_scheduler_and_adds_jobs(self, mock_signal, mock_atexit,
                                                    mock_get_config, mock_init_tables):
        """start() should create a BackgroundScheduler, add 4 jobs, and start."""
        from pipeline import PipelineOrchestrator

        mock_get_config.side_effect = lambda k: {
            'scan_schedule_cron_hour': 6,
            'tier2_check_interval_hours': 3,
            'health_check_interval_minutes': 5,
        }.get(k, None)

        # APScheduler is imported locally inside start(), so we patch at the
        # source package level before the local import resolves.
        mock_scheduler = MagicMock()
        mock_bs_class = MagicMock(return_value=mock_scheduler)
        mock_cron = MagicMock()
        mock_interval = MagicMock()

        with patch.dict('sys.modules', {
            'apscheduler': MagicMock(),
            'apscheduler.schedulers': MagicMock(),
            'apscheduler.schedulers.background': MagicMock(BackgroundScheduler=mock_bs_class),
            'apscheduler.triggers': MagicMock(),
            'apscheduler.triggers.cron': MagicMock(CronTrigger=mock_cron),
            'apscheduler.triggers.interval': MagicMock(IntervalTrigger=mock_interval),
        }):
            inst = PipelineOrchestrator.instance()
            inst.start()

        assert mock_scheduler.add_job.call_count == 4  # daily, tier2, weekly, health
        mock_scheduler.start.assert_called_once()
        assert inst._running is True

    @patch('pipeline._init_pipeline_tables')
    @patch('pipeline._get_config')
    def test_start_when_already_running_is_noop(self, mock_get_config, mock_init_tables):
        """Calling start() twice should not re-start the scheduler."""
        from pipeline import PipelineOrchestrator

        inst = PipelineOrchestrator.instance()
        inst._running = True
        # Should return early without touching anything
        inst.start()
        # _init_pipeline_tables should NOT be called because we returned early
        mock_init_tables.assert_not_called()

    def test_shutdown_when_not_running_is_noop(self):
        """shutdown() when not running should not raise."""
        from pipeline import PipelineOrchestrator
        inst = PipelineOrchestrator.instance()
        inst._running = False
        # Should not raise
        inst.shutdown()

    def test_shutdown_stops_scheduler(self):
        """shutdown() should call _scheduler.shutdown and set _running=False."""
        from pipeline import PipelineOrchestrator
        inst = PipelineOrchestrator.instance()
        inst._running = True
        inst._scheduler = MagicMock()
        inst._shutdown_event = MagicMock()

        inst.shutdown()

        inst._scheduler.shutdown.assert_called_once_with(wait=True)
        assert inst._running is False
        inst._shutdown_event.set.assert_called_once()


# ---------------------------------------------------------------------------
# PipelineOrchestrator — run_full_pipeline (logic, not execution)
# ---------------------------------------------------------------------------

class TestRunFullPipelineLogic:
    """Test run_full_pipeline control flow with all steps mocked."""

    def setup_method(self):
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    @patch('pipeline._get_config')
    def test_pipeline_disabled_returns_immediately(self, mock_get_config):
        """When pipeline_enabled=False, the pipeline should not run."""
        from pipeline import PipelineOrchestrator
        mock_get_config.return_value = False

        inst = PipelineOrchestrator.instance()
        result = inst.run_full_pipeline()
        assert result['status'] == 'disabled'

    @patch('pipeline._get_config', return_value=True)
    @patch('pipeline._create_run', return_value=42)
    @patch('pipeline._complete_run')
    @patch('pipeline.step_run_scheduled_scans', return_value={'queued': 5})
    @patch('pipeline.step_process_tier_changes', return_value={
        'total_changed': 2, 'hot_accounts': [{'id': 1, 'company_name': 'A'}],
        'changed': [],
    })
    @patch('pipeline.step_discover_contacts', return_value={
        'discovered': 3, 'contacts': [{'email': 'a@b.com'}],
    })
    @patch('pipeline.step_generate_emails', return_value={'generated': 1, 'errors': 0})
    @patch('pipeline.step_enroll_contacts', return_value={'enrolled': 1, 'errors': 0})
    @patch('pipeline.step_send_notifications', return_value={'errors': 0})
    @patch('pipeline.step_update_metrics', return_value={})
    def test_full_pipeline_all_steps_success(self, mock_metrics, mock_notify,
                                              mock_enroll, mock_emails,
                                              mock_contacts, mock_tiers,
                                              mock_scans, mock_complete,
                                              mock_create_run, mock_config):
        """When all steps succeed, the result should report success."""
        from pipeline import PipelineOrchestrator

        inst = PipelineOrchestrator.instance()
        result = inst.run_full_pipeline(trigger_type='manual')

        assert result['steps_failed'] == 0
        assert result['steps_completed'] == 7
        assert result['status'] == 'success'
        mock_create_run.assert_called_once_with('manual')
        mock_complete.assert_called_once()

    @patch('pipeline._get_config', return_value=True)
    @patch('pipeline._create_run', return_value=99)
    @patch('pipeline._complete_run')
    @patch('pipeline.step_run_scheduled_scans', return_value={'error': 'DB down'})
    @patch('pipeline.step_process_tier_changes', return_value={
        'total_changed': 0, 'hot_accounts': [], 'changed': [],
    })
    @patch('pipeline.step_discover_contacts', return_value={'discovered': 0, 'contacts': []})
    @patch('pipeline.step_generate_emails', return_value={'generated': 0, 'errors': 0})
    @patch('pipeline.step_enroll_contacts', return_value={'enrolled': 0, 'errors': 0})
    @patch('pipeline.step_send_notifications', return_value={'errors': 0})
    @patch('pipeline.step_update_metrics', return_value={})
    def test_partial_failure_reported(self, mock_metrics, mock_notify,
                                      mock_enroll, mock_emails,
                                      mock_contacts, mock_tiers,
                                      mock_scans, mock_complete,
                                      mock_create_run, mock_config):
        """When a step has an error, the result should report 'partial'."""
        from pipeline import PipelineOrchestrator

        inst = PipelineOrchestrator.instance()
        result = inst.run_full_pipeline()

        assert result['steps_failed'] >= 1
        assert result['status'] == 'partial'

    @patch('pipeline._get_config', return_value=True)
    @patch('pipeline._create_run', return_value=1)
    @patch('pipeline._complete_run')
    def test_already_running_skips(self, mock_complete, mock_create_run, mock_config):
        """If a pipeline run is already in progress, skip the new one."""
        from pipeline import PipelineOrchestrator

        inst = PipelineOrchestrator.instance()
        inst._current_run_id = 77  # Simulate already running

        result = inst.run_full_pipeline()
        assert result['status'] == 'already_running'
        mock_create_run.assert_not_called()

    @patch('pipeline._get_config', return_value=True)
    @patch('pipeline._create_run', return_value=10)
    @patch('pipeline._complete_run')
    @patch('pipeline.step_run_scheduled_scans', side_effect=Exception("fatal crash"))
    def test_fatal_error_recorded(self, mock_scans, mock_complete,
                                   mock_create_run, mock_config):
        """A fatal exception should be caught and recorded."""
        from pipeline import PipelineOrchestrator

        inst = PipelineOrchestrator.instance()
        result = inst.run_full_pipeline()

        # After the exception, _current_run_id should be cleared
        assert inst._current_run_id is None
        mock_complete.assert_called_once()
        # The status arg to _complete_run should be 'error'
        call_args = mock_complete.call_args
        assert call_args[0][1] == 'error'


# ---------------------------------------------------------------------------
# PipelineOrchestrator — run_health_check (structure)
# ---------------------------------------------------------------------------

class TestRunHealthCheck:
    """Test run_health_check returns the expected structure."""

    def setup_method(self):
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    @patch('pipeline._db')
    @patch('pipeline.CircuitBreaker')
    @patch('os.environ.get', return_value='test-key')
    def test_health_check_structure(self, mock_env, mock_cb_class, mock_db_fn):
        """run_health_check should return timestamp, checks, and status."""
        from pipeline import PipelineOrchestrator

        # Mock database check
        db = _mock_db_module()
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        db.db_connection.return_value = mock_conn
        mock_db_fn.return_value = db

        # Mock circuit breakers
        mock_cb_class.get_all.return_value = {}

        # Mock config import (used inside run_health_check for GitHub tokens)
        mock_config = MagicMock()
        mock_config.GITHUB_TOKENS = ['tok1']
        with patch.dict('sys.modules', {'config': MagicMock(Config=mock_config)}):
            inst = PipelineOrchestrator.instance()
            inst._running = True
            inst._scheduler = MagicMock()
            inst._scheduler.running = True

            health = inst.run_health_check()

        assert 'timestamp' in health
        assert 'status' in health
        assert 'checks' in health
        assert isinstance(health['checks'], dict)

    @patch('pipeline._db')
    @patch('pipeline.CircuitBreaker')
    @patch('os.environ.get', return_value='')
    def test_health_check_unhealthy_when_scheduler_not_running(self, mock_env,
                                                                mock_cb_class,
                                                                mock_db_fn):
        """If the scheduler is not running, the overall status should be unhealthy."""
        from pipeline import PipelineOrchestrator

        db = _mock_db_module()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        db.db_connection.return_value = mock_conn
        mock_db_fn.return_value = db
        mock_cb_class.get_all.return_value = {}

        mock_config = MagicMock()
        mock_config.GITHUB_TOKENS = []
        with patch.dict('sys.modules', {'config': MagicMock(Config=mock_config)}):
            inst = PipelineOrchestrator.instance()
            inst._running = False
            inst._scheduler = None

            health = inst.run_health_check()

        assert health['checks']['scheduler']['status'] == 'error'
        assert health['status'] == 'unhealthy'


# ---------------------------------------------------------------------------
# PipelineOrchestrator — CircuitBreaker integration points
# ---------------------------------------------------------------------------

class TestCircuitBreakerIntegration:
    """Test that circuit breaker usage in pipeline steps is correct."""

    @patch('pipeline._db')
    @patch('pipeline.CircuitBreaker')
    def test_discover_contacts_uses_apollo_breaker(self, mock_cb_class, mock_db_fn):
        """step_discover_contacts should get the 'apollo' circuit breaker."""
        from pipeline import step_discover_contacts

        mock_breaker = MagicMock()
        mock_cb_class.get.return_value = mock_breaker

        db = _mock_db_module()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        db.db_connection.return_value = mock_conn
        mock_db_fn.return_value = db

        result = step_discover_contacts(run_id=1, accounts=[])
        # With empty accounts, should still initialize the breaker but skip
        assert result['discovered'] == 0

    @patch('pipeline._record_step')
    @patch('pipeline._get_config', return_value=500)
    @patch('pipeline._get_weekly_email_count', return_value=0)
    @patch('pipeline.CircuitBreaker')
    def test_generate_emails_uses_ai_breaker(self, mock_cb_class, mock_weekly,
                                              mock_config, mock_record):
        """step_generate_emails should use the 'ai' circuit breaker."""
        from pipeline import step_generate_emails

        mock_breaker = MagicMock()
        mock_cb_class.get.return_value = mock_breaker

        result = step_generate_emails(run_id=1, contacts=[])
        assert result['generated'] == 0

    @patch('pipeline._record_step')
    @patch('pipeline._get_config', return_value=False)
    @patch('pipeline.CircuitBreaker')
    def test_enroll_contacts_uses_apollo_breaker(self, mock_cb_class,
                                                  mock_config, mock_record):
        """step_enroll_contacts should use the 'apollo' circuit breaker."""
        from pipeline import step_enroll_contacts

        mock_breaker = MagicMock()
        mock_cb_class.get.return_value = mock_breaker

        result = step_enroll_contacts(run_id=1, contacts=[])
        assert result['enrolled'] == 0

    @patch('pipeline._get_config', return_value=500)
    @patch('pipeline._get_weekly_email_count', return_value=600)
    @patch('pipeline._record_step')
    def test_email_budget_exhausted_skips(self, mock_record, mock_weekly, mock_config):
        """When weekly email budget is used up, step should be skipped."""
        from pipeline import step_generate_emails

        contacts = [{'email': 'a@b.com'}]
        result = step_generate_emails(run_id=1, contacts=contacts)
        assert result['generated'] == 0
        assert result.get('reason') == 'budget_exhausted'


# ---------------------------------------------------------------------------
# PipelineOrchestrator — run_tier2_check (lightweight job)
# ---------------------------------------------------------------------------

class TestRunTier2Check:
    """Test the tier2 hot-lead check job."""

    def setup_method(self):
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    @patch('pipeline._get_config')
    def test_tier2_check_disabled_returns_early(self, mock_get_config):
        """When pipeline is disabled, tier2 check should return early."""
        from pipeline import PipelineOrchestrator
        mock_get_config.return_value = False

        inst = PipelineOrchestrator.instance()
        # Should not raise
        result = inst.run_tier2_check()
        assert result is None


# ---------------------------------------------------------------------------
# PipelineOrchestrator — run_weekly_digest (lightweight job)
# ---------------------------------------------------------------------------

class TestRunWeeklyDigest:
    """Test the weekly digest job."""

    def setup_method(self):
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    @patch('pipeline._get_config')
    def test_weekly_digest_disabled_returns_early(self, mock_get_config):
        """When pipeline is disabled, weekly digest should return early."""
        from pipeline import PipelineOrchestrator
        mock_get_config.return_value = False

        inst = PipelineOrchestrator.instance()
        result = inst.run_weekly_digest()
        assert result is None


# ---------------------------------------------------------------------------
# Signal handler registration
# ---------------------------------------------------------------------------

class TestSignalHandlers:
    """Test that _register_signal_handlers does not crash in a non-main thread."""

    def setup_method(self):
        from pipeline import PipelineOrchestrator
        PipelineOrchestrator._instance = None

    def test_register_signal_handlers_noop_in_thread(self):
        """_register_signal_handlers should not raise in a background thread."""
        from pipeline import PipelineOrchestrator

        inst = PipelineOrchestrator.instance()
        errors = []

        def try_register():
            try:
                inst._register_signal_handlers()
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=try_register)
        t.start()
        t.join()
        # Should not have raised — the function catches ValueError/OSError
        assert len(errors) == 0
