"""
APScheduler-based scan scheduler for automated tier-aware rescanning.

Replaces the simple threading.Thread sleep-loop with a proper job scheduler
that supports:
- Tier-specific rescan cadences (Tier 2 every 3 days, Tier 1 weekly, etc.)
- Concurrency-limited job execution via the existing ThreadPoolExecutor
- Incremental scanning using last_scanned_at timestamps
- Signal freshness decay scoring
- Graceful startup/shutdown integrated with Flask app lifecycle

Replit-compatible: uses APScheduler's BackgroundScheduler (no Redis/Celery).
"""
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

# APScheduler is optional — freshness scoring works without it.
# The ScanScheduler class requires it but degrades gracefully.
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    _HAS_APSCHEDULER = True
except ImportError:
    _HAS_APSCHEDULER = False

# Database imports are deferred to avoid circular imports at module level
# when only freshness functions are needed.
_db_imports_loaded = False
_db_modules = {}


def _ensure_db_imports():
    """Lazy-load database imports (only needed by ScanScheduler, not freshness scoring)."""
    global _db_imports_loaded, _db_modules
    if _db_imports_loaded:
        return _db_modules
    from database import (
        get_refreshable_accounts,
        get_scheduled_rescan_summary,
        TIER_SCAN_INTERVALS,
        TIER_PREPARING,
        TIER_THINKING,
        TIER_LAUNCHED,
        TIER_TRACKING,
        TIER_INVALID,
        TIER_CONFIG,
    )
    _db_modules = {
        'get_refreshable_accounts': get_refreshable_accounts,
        'get_scheduled_rescan_summary': get_scheduled_rescan_summary,
        'TIER_SCAN_INTERVALS': TIER_SCAN_INTERVALS,
        'TIER_PREPARING': TIER_PREPARING,
        'TIER_THINKING': TIER_THINKING,
        'TIER_LAUNCHED': TIER_LAUNCHED,
        'TIER_TRACKING': TIER_TRACKING,
        'TIER_INVALID': TIER_INVALID,
        'TIER_CONFIG': TIER_CONFIG,
    }
    _db_imports_loaded = True
    return _db_modules

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal Freshness Scoring
# ---------------------------------------------------------------------------

# Decay rate (lambda) per signal type.
# Higher lambda = faster decay (signal becomes stale sooner).
# RFC discussions decay faster because intent can fizzle out;
# dependency injection (Smoking Gun) decays slower because installed
# libraries persist and indicate committed investment.
SIGNAL_DECAY_RATES = {
    'rfc_discussion':       0.020,   # Half-life ~35 days
    'dependency_injection': 0.005,   # Half-life ~139 days (libs persist)
    'ghost_branch':         0.015,   # Half-life ~46 days
    'smoking_gun_fork':     0.004,   # Half-life ~173 days (forks are sticky)
    'documentation_intent': 0.025,   # Half-life ~28 days (docs discussions fade)
    'framework_config':     0.008,   # Half-life ~87 days
    'mobile_architecture':  0.007,   # Half-life ~99 days
    'pseudo_localization':  0.006,   # Half-life ~116 days
    'build_script_i18n':    0.006,   # Half-life ~116 days
    'linter_library':       0.005,   # Half-life ~139 days
    'cms_i18n':             0.005,   # Half-life ~139 days
    # Enhanced heuristics
    'job_posting_intent':       0.030,  # Half-life ~23 days (job posts expire fast)
    'regional_domain_detection': 0.003, # Half-life ~231 days (domains are permanent)
    'headless_cms_i18n':        0.005,
    'payment_multi_currency':   0.004,
    'timezone_library':         0.004,
    'ci_localization_pipeline': 0.006,
    'compliance_documentation': 0.010,
    'social_multi_region':      0.008,
    'locale_velocity_high':     0.012,
    'locale_velocity_medium':   0.015,
    'api_international':        0.005,
}

DEFAULT_DECAY_RATE = 0.010  # ~69 day half-life for unknown signal types


def calculate_signal_freshness(signal: dict) -> float:
    """
    Calculate a freshness-adjusted score for a signal using exponential decay.

    Formula: freshness_score = raw_strength * e^(-lambda * age_in_days)

    The decay rate (lambda) is tuned per signal type:
    - RFC discussions decay faster (intent can fizzle)
    - Dependency installations decay slower (code persists)
    - Job postings decay fastest (positions fill quickly)
    - Regional domains barely decay (permanent infrastructure)

    Args:
        signal: Dictionary with keys:
            - signal_type or type: str identifying the signal category
            - raw_strength: float base score (default 1.0 if missing)
            - age_in_days: int days since signal was detected (default 0)
            - created_at: ISO timestamp (used to compute age if age_in_days missing)

    Returns:
        Freshness-adjusted score (float, 0.0 to raw_strength).
    """
    signal_type = signal.get('signal_type') or signal.get('type', 'unknown')
    raw_strength = signal.get('raw_strength')

    # Default raw_strength based on priority if not explicitly set
    if raw_strength is None:
        priority = signal.get('priority', 'MEDIUM')
        raw_strength = 1.0 if priority == 'HIGH' else 0.7

    # Determine age in days
    age_in_days = signal.get('age_in_days')
    if age_in_days is None:
        created_at = signal.get('created_at') or signal.get('timestamp')
        if created_at:
            try:
                if isinstance(created_at, str):
                    # Handle ISO format with or without Z suffix
                    normalized = created_at.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(normalized)
                else:
                    dt = created_at
                # Make timezone-aware if needed
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                age_in_days = max(0, (now - dt).days)
            except (ValueError, TypeError):
                age_in_days = 0
        else:
            age_in_days = 0

    # Look up decay rate for this signal type
    decay_rate = SIGNAL_DECAY_RATES.get(signal_type, DEFAULT_DECAY_RATE)

    # Exponential decay: score = raw_strength * e^(-lambda * age)
    freshness_score = raw_strength * math.exp(-decay_rate * age_in_days)

    return round(freshness_score, 4)


def enrich_signal_with_freshness(signal: dict) -> dict:
    """
    Add freshness_score to a signal dict (non-mutating).

    Returns a new dict with 'freshness_score' and 'age_in_days' added.
    """
    enriched = dict(signal)
    enriched['freshness_score'] = calculate_signal_freshness(signal)

    # Ensure age_in_days is always present
    if enriched.get('age_in_days') is None:
        created_at = signal.get('created_at') or signal.get('timestamp')
        if created_at:
            try:
                if isinstance(created_at, str):
                    normalized = created_at.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(normalized)
                else:
                    dt = created_at
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                enriched['age_in_days'] = max(0, (datetime.now(timezone.utc) - dt).days)
            except (ValueError, TypeError):
                enriched['age_in_days'] = 0
        else:
            enriched['age_in_days'] = 0

    return enriched


# ---------------------------------------------------------------------------
# Scan Scheduler
# ---------------------------------------------------------------------------

class ScanScheduler:
    """
    APScheduler-based scan scheduler that automatically queues stale accounts
    for re-scanning based on their tier.

    Integration:
    - Uses the existing ThreadPoolExecutor (from app.get_executor())
    - Respects MAX_SCAN_WORKERS concurrency limit
    - Updates next_scan_due on tier changes
    - Provides status API for monitoring

    Usage:
        scheduler = ScanScheduler(app)
        scheduler.start()
        # ... on shutdown ...
        scheduler.shutdown()
    """

    def __init__(self, app=None):
        """
        Initialize the scheduler.

        Args:
            app: Optional Flask app instance. If provided, registers
                 shutdown hook via atexit.
        """
        if not _HAS_APSCHEDULER:
            raise ImportError("APScheduler is required for ScanScheduler. Install via: pip install APScheduler")

        self._app = app
        self._scheduler = BackgroundScheduler(
            job_defaults={
                'coalesce': True,      # Merge missed runs into one
                'max_instances': 1,    # Prevent overlapping runs
                'misfire_grace_time': 300,  # Allow 5 min late execution
            },
            timezone='UTC',
        )
        self._lock = threading.Lock()
        self._state = {
            'enabled': True,
            'started_at': None,
            'last_cycle_at': None,
            'last_queued_count': 0,
            'total_queued_lifetime': 0,
            'cycles_run': 0,
            'check_interval_hours': 6,
            'max_per_cycle': 100,
            'errors': [],
        }
        # Callback for spawning scans (injected from app.py)
        self._spawn_scan_fn = None
        self._max_workers = 20

        # Register event listener for job errors
        self._scheduler.add_listener(self._on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    def configure(self, spawn_scan_fn, max_workers: int = 20,
                  check_interval_hours: int = 6, max_per_cycle: int = 100):
        """
        Configure the scheduler with runtime dependencies.

        Args:
            spawn_scan_fn: Callable that queues a company for scanning
                          (e.g., app.spawn_background_scan)
            max_workers: Maximum concurrent scan workers
            check_interval_hours: How often to check for due accounts
            max_per_cycle: Maximum accounts to queue per check cycle
        """
        self._spawn_scan_fn = spawn_scan_fn
        self._max_workers = max_workers
        with self._lock:
            self._state['check_interval_hours'] = check_interval_hours
            self._state['max_per_cycle'] = max_per_cycle

    def start(self):
        """
        Start the scheduler with tier-aware rescan jobs.

        Adds two jobs:
        1. Primary rescan cycle: runs every N hours to queue stale accounts
        2. Health check: runs every 30 minutes to log scheduler status
        """
        if self._spawn_scan_fn is None:
            raise RuntimeError("ScanScheduler.configure() must be called before start()")

        interval_hours = self._state['check_interval_hours']

        # Primary rescan job — runs every N hours
        self._scheduler.add_job(
            self._rescan_cycle,
            trigger=IntervalTrigger(hours=interval_hours),
            id='tier_rescan_cycle',
            name='Tier-aware rescan cycle',
            replace_existing=True,
        )

        # Health check job — every 30 minutes
        self._scheduler.add_job(
            self._health_check,
            trigger=IntervalTrigger(minutes=30),
            id='scheduler_health_check',
            name='Scheduler health check',
            replace_existing=True,
        )

        self._scheduler.start()

        with self._lock:
            self._state['started_at'] = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"[SCHEDULER] Started — checking every {interval_hours}h, "
            f"max {self._state['max_per_cycle']} per cycle, "
            f"tier intervals: {dict(_ensure_db_imports()['TIER_SCAN_INTERVALS'])}"
        )

    def shutdown(self, wait: bool = True):
        """Gracefully shut down the scheduler."""
        if self._scheduler.running:
            logger.info("[SCHEDULER] Shutting down...")
            self._scheduler.shutdown(wait=wait)
            logger.info("[SCHEDULER] Shut down cleanly")

    def pause(self):
        """Pause all scheduled jobs (does not stop running scans)."""
        with self._lock:
            self._state['enabled'] = False
        self._scheduler.pause()
        logger.info("[SCHEDULER] Paused")

    def resume(self):
        """Resume paused scheduler."""
        with self._lock:
            self._state['enabled'] = True
        self._scheduler.resume()
        logger.info("[SCHEDULER] Resumed")

    @property
    def is_running(self) -> bool:
        return self._scheduler.running

    def get_status(self) -> Dict[str, Any]:
        """
        Get current scheduler status for the API.

        Returns dict with scheduler state, tier intervals, and per-tier summaries.
        """
        with self._lock:
            state = dict(self._state)

        # Get per-tier summary from database
        try:
            db = _ensure_db_imports()
            tier_summary = db['get_scheduled_rescan_summary']()
        except Exception as e:
            tier_summary = {'error': str(e)}

        # Get APScheduler job info
        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name,
                'next_run': job.next_run_time.isoformat() if job.next_run_time else None,
                'pending': job.pending,
            })

        return {
            'scheduler': state,
            'intervals': {str(k): v for k, v in db['TIER_SCAN_INTERVALS'].items()},
            'tiers': {str(k): v for k, v in tier_summary.items()} if isinstance(tier_summary, dict) else tier_summary,
            'jobs': jobs,
        }

    def update_config(self, check_interval_hours: int = None, max_per_cycle: int = None):
        """
        Update scheduler configuration at runtime.

        If check_interval_hours changes, the rescan job is rescheduled.
        """
        with self._lock:
            if check_interval_hours is not None:
                hours = max(1, min(48, check_interval_hours))
                old_hours = self._state['check_interval_hours']
                self._state['check_interval_hours'] = hours

                if hours != old_hours and self._scheduler.running:
                    self._scheduler.reschedule_job(
                        'tier_rescan_cycle',
                        trigger=IntervalTrigger(hours=hours),
                    )
                    logger.info(f"[SCHEDULER] Rescheduled rescan cycle: {old_hours}h -> {hours}h")

            if max_per_cycle is not None:
                self._state['max_per_cycle'] = max(1, min(500, max_per_cycle))

    def trigger_now(self) -> Dict[str, Any]:
        """
        Trigger an immediate rescan cycle (outside the normal schedule).

        Returns summary of what was queued.
        """
        return self._rescan_cycle()

    # -- Internal methods --

    def _rescan_cycle(self) -> Dict[str, Any]:
        """
        Core rescan cycle: find stale accounts and queue them for scanning.

        Returns dict with cycle results.
        """
        with self._lock:
            if not self._state['enabled']:
                return {'skipped': True, 'reason': 'scheduler paused'}

        try:
            db = _ensure_db_imports()
            accounts_due = db['get_refreshable_accounts']()
            max_per_cycle = self._state['max_per_cycle']
            batch = accounts_due[:max_per_cycle]

            queued_count = 0
            errors = []

            for account in batch:
                company_name = account.get('company_name')
                if not company_name:
                    continue
                try:
                    self._spawn_scan_fn(company_name)
                    queued_count += 1
                    # Small stagger between submissions to spread load
                    time.sleep(0.5)
                except Exception as e:
                    error_msg = f"Failed to queue {company_name}: {e}"
                    errors.append(error_msg)
                    logger.error(f"[SCHEDULER] {error_msg}")

            now_iso = datetime.now(timezone.utc).isoformat()

            with self._lock:
                self._state['last_cycle_at'] = now_iso
                self._state['last_queued_count'] = queued_count
                self._state['total_queued_lifetime'] += queued_count
                self._state['cycles_run'] += 1
                # Keep last 10 errors
                if errors:
                    self._state['errors'] = (errors + self._state['errors'])[:10]

            remaining = len(accounts_due) - queued_count
            if queued_count > 0:
                logger.info(
                    f"[SCHEDULER] Cycle complete: queued {queued_count} accounts "
                    f"({remaining} more still due)"
                )
            else:
                logger.info("[SCHEDULER] Cycle complete: no accounts due for rescan")

            return {
                'queued': queued_count,
                'remaining': remaining,
                'errors': len(errors),
                'cycle_at': now_iso,
            }

        except Exception as e:
            logger.error(f"[SCHEDULER] Error in rescan cycle: {e}")
            with self._lock:
                self._state['errors'] = ([str(e)] + self._state['errors'])[:10]
            return {'error': str(e)}

    def _health_check(self):
        """Log scheduler health status periodically."""
        with self._lock:
            state = dict(self._state)

        jobs_count = len(self._scheduler.get_jobs())
        logger.info(
            f"[SCHEDULER] Health: enabled={state['enabled']}, "
            f"cycles={state['cycles_run']}, "
            f"total_queued={state['total_queued_lifetime']}, "
            f"jobs={jobs_count}"
        )

    def _on_job_event(self, event):
        """Handle APScheduler job events for monitoring."""
        if event.exception:
            logger.error(f"[SCHEDULER] Job {event.job_id} failed: {event.exception}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_scan_scheduler: Optional[ScanScheduler] = None


def get_scan_scheduler() -> Optional[ScanScheduler]:
    """Get the global ScanScheduler instance (None if not initialized)."""
    return _scan_scheduler


def init_scan_scheduler(app, spawn_scan_fn, max_workers: int = 20,
                        check_interval_hours: int = 6,
                        max_per_cycle: int = 100) -> ScanScheduler:
    """
    Initialize and start the global scan scheduler.

    Call this once during Flask app initialization (in init_app_once).

    Args:
        app: Flask app instance
        spawn_scan_fn: Function to queue a company for background scanning
        max_workers: Maximum concurrent scan workers
        check_interval_hours: How often to check for due accounts
        max_per_cycle: Maximum accounts to queue per check cycle

    Returns:
        The initialized ScanScheduler instance.
    """
    global _scan_scheduler

    if _scan_scheduler is not None and _scan_scheduler.is_running:
        logger.warning("[SCHEDULER] Scheduler already running, skipping re-init")
        return _scan_scheduler

    _scan_scheduler = ScanScheduler(app)
    _scan_scheduler.configure(
        spawn_scan_fn=spawn_scan_fn,
        max_workers=max_workers,
        check_interval_hours=check_interval_hours,
        max_per_cycle=max_per_cycle,
    )
    _scan_scheduler.start()

    return _scan_scheduler


def shutdown_scan_scheduler():
    """Shut down the global scheduler (call on app exit)."""
    global _scan_scheduler
    if _scan_scheduler is not None:
        _scan_scheduler.shutdown()
        _scan_scheduler = None
