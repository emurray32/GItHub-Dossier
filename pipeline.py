"""
Pipeline Orchestrator — End-to-end automation for the Dossier sales intelligence pipeline.

Wires together all pipeline stages into a scheduled, observable flow:

    Scan -> Detect Tier Changes -> Discover Contacts -> Generate Emails
         -> Enroll in Sequences -> Notify via Slack -> Track Metrics

Key design decisions:
    - Uses APScheduler (BackgroundScheduler) — Replit-compatible, no Celery/Redis.
    - Each pipeline step is independently retryable and circuit-breaker-protected.
    - Pipeline state is persisted in `pipeline_runs` / `pipeline_step_results` tables
      so it survives restarts and is observable via the status API.
    - Thread-safe: the scheduler runs jobs in its own thread pool; shared state is
      protected by locks.
    - Graceful shutdown: registers SIGTERM handler, finishes current step.
"""
import atexit
import json
import logging
import signal
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from circuit_breaker import CircuitBreaker, CircuitBreakerError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — avoid circular imports with app.py / database.py
# ---------------------------------------------------------------------------

def _db():
    """Lazy import of database module."""
    import database
    return database


def _app_module():
    """Lazy import of app module (for trigger_webhook, perform_background_scan, etc.)."""
    import app as _app
    return _app


# ---------------------------------------------------------------------------
# Pipeline configuration defaults (overridable via system_settings)
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    'pipeline_enabled': True,
    'scan_schedule_cron_hour': 6,       # 6 AM daily full pipeline
    'tier2_check_interval_hours': 3,    # Check for new Tier 2s every 3 hours
    'weekly_digest_day': 'fri',         # Weekly digest on Fridays
    'health_check_interval_minutes': 5,
    'max_emails_per_week': 500,
    'max_enrollments_per_run': 50,
    'max_contacts_per_account': 5,
    'approval_required': False,         # Slack approval before enrollment
    'max_retries': 3,
    'retry_backoff_base': 60,           # Base seconds for exponential backoff
}


def _get_config(key: str):
    """Read a pipeline config value from system_settings, falling back to defaults."""
    db = _db()
    raw = db.get_setting(f'pipeline_{key}')
    default = _DEFAULT_CONFIG.get(key)
    if raw is None:
        return default
    # Type-coerce based on default
    if isinstance(default, bool):
        return raw.lower() in ('true', '1', 'yes')
    if isinstance(default, int):
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default
    return raw


def _set_config(key: str, value):
    """Write a pipeline config value to system_settings."""
    db = _db()
    db.set_setting(f'pipeline_{key}', str(value))


# ---------------------------------------------------------------------------
# Pipeline state persistence
# ---------------------------------------------------------------------------

def _init_pipeline_tables():
    """Create pipeline_runs and pipeline_step_results tables if they don't exist."""
    db = _db()
    with db.db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute(db._adapt_ddl('''
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                status TEXT DEFAULT 'running',
                trigger_type TEXT DEFAULT 'scheduled',
                steps_completed INTEGER DEFAULT 0,
                steps_failed INTEGER DEFAULT 0,
                error_log TEXT,
                summary_json TEXT
            )
        '''))

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
            ON pipeline_runs(status)
        ''')

        cursor.execute(db._adapt_ddl('''
            CREATE TABLE IF NOT EXISTS pipeline_step_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                step_name TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                records_processed INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                detail_json TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
            )
        '''))

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_pipeline_steps_run
            ON pipeline_step_results(run_id)
        ''')

        conn.commit()
    logger.info("[PIPELINE] Pipeline tables initialized")


def _create_run(trigger_type: str = 'scheduled') -> int:
    """Create a new pipeline_runs row, return its id."""
    db = _db()
    with db.db_connection() as conn:
        cursor = conn.cursor()
        run_id = db._insert_returning_id(cursor, '''
            INSERT INTO pipeline_runs (trigger_type) VALUES (?)
        ''', (trigger_type,))
        conn.commit()
    return run_id


def _complete_run(run_id: int, status: str, steps_completed: int,
                  steps_failed: int, error_log: str = '',
                  summary: Optional[dict] = None):
    """Mark a pipeline run as completed."""
    db = _db()
    with db.db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE pipeline_runs SET completed_at = CURRENT_TIMESTAMP, '
            'status = ?, steps_completed = ?, steps_failed = ?, '
            'error_log = ?, summary_json = ? WHERE id = ?',
            (status, steps_completed, steps_failed, error_log,
             json.dumps(summary) if summary else None, run_id)
        )
        conn.commit()


def _record_step(run_id: int, step_name: str, status: str,
                 records_processed: int = 0, errors: int = 0,
                 duration_ms: int = 0, detail: Optional[dict] = None) -> int:
    """Record a completed pipeline step."""
    db = _db()
    with db.db_connection() as conn:
        cursor = conn.cursor()
        step_id = db._insert_returning_id(cursor, '''
            INSERT INTO pipeline_step_results
                (run_id, step_name, status, records_processed, errors,
                 duration_ms, detail_json, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (run_id, step_name, status, records_processed, errors,
              duration_ms, json.dumps(detail) if detail else None))
        conn.commit()
    return step_id


def get_recent_runs(limit: int = 20) -> list:
    """Return the most recent pipeline runs."""
    db = _db()
    with db.db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?',
            (limit,)
        )
        rows = [dict(r) for r in cursor.fetchall()]
    return rows


def get_run_steps(run_id: int) -> list:
    """Return step results for a given pipeline run."""
    db = _db()
    with db.db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM pipeline_step_results WHERE run_id = ? ORDER BY started_at ASC',
            (run_id,)
        )
        rows = [dict(r) for r in cursor.fetchall()]
    return rows


# ---------------------------------------------------------------------------
# Pipeline Steps — each step is a standalone function
# ---------------------------------------------------------------------------

def step_run_scheduled_scans(run_id: int) -> dict:
    """
    Step 1: Queue accounts that are due for rescan.

    Uses the existing get_refreshable_accounts() which respects tier-based
    scan intervals (Tier 2 = 3 days, Tier 1 = 7 days, etc.).

    Delegates actual scanning to the existing ThreadPoolExecutor via
    spawn_background_scan().
    """
    t0 = time.time()
    db = _db()
    app = _app_module()

    try:
        accounts = db.get_refreshable_accounts()
        max_per_cycle = _get_config('max_enrollments_per_run') or 100
        batch = accounts[:max_per_cycle]

        queued = 0
        errors = 0
        for account in batch:
            company = account.get('company_name')
            if not company:
                continue
            try:
                app.spawn_background_scan(company)
                queued += 1
                time.sleep(0.5)  # Small delay to avoid flooding
            except Exception as e:
                errors += 1
                logger.error(f"[PIPELINE] Failed to queue scan for {company}: {e}")

        duration_ms = int((time.time() - t0) * 1000)
        detail = {
            'total_due': len(accounts),
            'queued': queued,
            'remaining': len(accounts) - queued,
        }
        _record_step(run_id, 'scan', 'success', queued, errors, duration_ms, detail)
        db.increment_daily_stat('scans_run', queued)
        logger.info(f"[PIPELINE] Scan step: queued {queued}/{len(accounts)} accounts")
        return detail

    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        _record_step(run_id, 'scan', 'error', 0, 1, duration_ms, {'error': str(e)})
        logger.error(f"[PIPELINE] Scan step failed: {e}")
        return {'error': str(e)}


def step_process_tier_changes(run_id: int) -> dict:
    """
    Step 2: Detect accounts whose tier changed since last pipeline run.

    Checks monitored_accounts for recently changed tiers by comparing
    status_changed_at to the last pipeline run timestamp.

    Returns a list of accounts with tier changes for downstream steps.
    """
    t0 = time.time()
    db = _db()

    try:
        with db.db_connection() as conn:
            cursor = conn.cursor()

            # Find accounts whose tier changed in the last 24 hours
            dt_cutoff = db._adapt_datetime('-1 days')
            cursor.execute(f'''
                SELECT id, company_name, github_org, current_tier, website,
                       annual_revenue, status_changed_at
                FROM monitored_accounts
                WHERE status_changed_at >= {dt_cutoff}
                  AND archived_at IS NULL
                ORDER BY current_tier ASC
            ''')
            changed = [dict(r) for r in cursor.fetchall()]

        # Categorize
        tier_counts = {}
        hot_accounts = []
        for acct in changed:
            tier = acct.get('current_tier', 0)
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            if tier in (1, 2):
                hot_accounts.append(acct)

        duration_ms = int((time.time() - t0) * 1000)
        detail = {
            'total_changed': len(changed),
            'tier_breakdown': tier_counts,
            'hot_accounts': len(hot_accounts),
        }
        _record_step(run_id, 'tier_changes', 'success',
                     len(changed), 0, duration_ms, detail)
        logger.info(
            f"[PIPELINE] Tier changes: {len(changed)} total, "
            f"{len(hot_accounts)} hot (Tier 1/2)"
        )
        return {'changed': changed, 'hot_accounts': hot_accounts, **detail}

    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        _record_step(run_id, 'tier_changes', 'error', 0, 1, duration_ms,
                     {'error': str(e)})
        logger.error(f"[PIPELINE] Tier changes step failed: {e}")
        return {'error': str(e), 'changed': [], 'hot_accounts': []}


def step_discover_contacts(run_id: int, accounts: List[dict]) -> dict:
    """
    Step 3: Discover contacts for Tier 1/2 accounts via Apollo.

    This is a placeholder that defines the interface. The actual Apollo
    contact discovery is built by the Apollo agent (Task #1). This step
    will call into that module once integrated.

    Expected interface from Apollo module:
        apollo_client.search_contacts(domain, titles, seniorities) -> list[dict]

    Each returned contact dict should have:
        first_name, last_name, email, title, seniority, linkedin_url,
        apollo_person_id
    """
    t0 = time.time()
    cb = CircuitBreaker.get('apollo', failure_threshold=5, cooldown_seconds=900)

    if not accounts:
        _record_step(run_id, 'discover_contacts', 'skipped', 0, 0, 0,
                     {'reason': 'no hot accounts'})
        return {'discovered': 0, 'contacts': []}

    total_discovered = 0
    total_errors = 0
    all_contacts = []
    max_per_account = _get_config('max_contacts_per_account') or 5

    # Load dedup email set ONCE before the account loop (Issue #10 optimization)
    db = _db()
    with db.db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT email FROM enrollment_contacts WHERE email IS NOT NULL AND email != ?',
            ('',)
        )
        existing_emails = {r['email'].lower() for r in cursor.fetchall() if r['email']}

    for account in accounts:
        company = account.get('company_name', '')
        domain = _extract_domain(account.get('website', ''))

        if not domain:
            logger.warning(f"[PIPELINE] No domain for {company}, skipping contact discovery")
            continue

        try:
            cb.guard()
            # Apollo contact discovery via apollo_pipeline module
            from apollo_pipeline import auto_discover_contacts
            account_id = account.get('id')
            if account_id:
                result = auto_discover_contacts(account_id,
                                                existing_emails=existing_emails)
                contacts = result.get('contacts', [])
            else:
                contacts = []

            cb.record_success()
            total_discovered += len(contacts)
            for c in contacts:
                c['company_name'] = company
                c['company_domain'] = domain
            all_contacts.extend(contacts)

        except CircuitBreakerError:
            logger.warning(f"[PIPELINE] Apollo circuit breaker open, skipping {company}")
            total_errors += 1
            break  # Stop trying if Apollo is down
        except Exception as e:
            cb.record_failure(e)
            total_errors += 1
            logger.error(f"[PIPELINE] Contact discovery failed for {company}: {e}")

    duration_ms = int((time.time() - t0) * 1000)
    detail = {
        'accounts_processed': len(accounts),
        'contacts_discovered': total_discovered,
        'errors': total_errors,
    }
    status = 'success' if total_errors == 0 else 'partial'
    _record_step(run_id, 'discover_contacts', status,
                 total_discovered, total_errors, duration_ms, detail)
    logger.info(f"[PIPELINE] Contact discovery: {total_discovered} contacts from {len(accounts)} accounts")
    return {'discovered': total_discovered, 'contacts': all_contacts, **detail}


def step_generate_emails(run_id: int, contacts: List[dict]) -> dict:
    """
    Step 4: Generate personalized cold emails for discovered contacts.

    This is a placeholder that defines the interface. The actual email
    generation is built by the Email agent (Task #3). This step will call
    into the ai_summary module or a dedicated email generator once integrated.

    Expected interface:
        email_generator.generate_cold_email(contact, scan_data, campaign) -> dict
            Returns: {subject, body}

    Rate-limited by max_emails_per_week setting.
    """
    t0 = time.time()
    cb = CircuitBreaker.get('ai', failure_threshold=5, cooldown_seconds=900)

    if not contacts:
        _record_step(run_id, 'generate_emails', 'skipped', 0, 0, 0,
                     {'reason': 'no contacts'})
        return {'generated': 0}

    # Check weekly email budget
    max_weekly = _get_config('max_emails_per_week') or 500
    emails_this_week = _get_weekly_email_count()
    remaining_budget = max(max_weekly - emails_this_week, 0)

    if remaining_budget <= 0:
        logger.warning("[PIPELINE] Weekly email budget exhausted, skipping generation")
        _record_step(run_id, 'generate_emails', 'skipped', 0, 0, 0,
                     {'reason': 'weekly budget exhausted',
                      'budget': max_weekly, 'sent': emails_this_week})
        return {'generated': 0, 'reason': 'budget_exhausted'}

    batch = contacts[:remaining_budget]
    generated = 0
    errors = 0

    for contact in batch:
        try:
            cb.guard()
            # --- Placeholder: replace with actual email generation ---
            # email = email_generator.generate_cold_email(
            #     contact=contact,
            #     scan_data=_get_latest_scan_data(contact['company_name']),
            #     campaign=_get_active_campaign(contact['company_name']),
            # )
            # contact['generated_email'] = email
            # --- End placeholder ---
            cb.record_success()
            generated += 1
        except CircuitBreakerError:
            logger.warning("[PIPELINE] AI circuit breaker open, stopping email generation")
            break
        except Exception as e:
            cb.record_failure(e)
            errors += 1
            logger.error(f"[PIPELINE] Email generation failed for {contact.get('email', '?')}: {e}")

    duration_ms = int((time.time() - t0) * 1000)
    detail = {
        'batch_size': len(batch),
        'generated': generated,
        'errors': errors,
        'weekly_budget_remaining': remaining_budget - generated,
    }
    status = 'success' if errors == 0 else 'partial'
    _record_step(run_id, 'generate_emails', status,
                 generated, errors, duration_ms, detail)
    logger.info(f"[PIPELINE] Email generation: {generated}/{len(batch)} generated")
    return detail


def step_enroll_contacts(run_id: int, contacts: List[dict]) -> dict:
    """
    Step 5: Enroll approved contacts into Apollo sequences.

    This is a placeholder that defines the interface. The actual enrollment
    is built by the Apollo agent (Task #1). Uses the existing
    enrollment_batches / enrollment_contacts tables.

    Expected interface from Apollo module:
        apollo_client.add_to_sequence(sequence_id, contact_id, email_data) -> bool

    If approval_required is True, contacts are left in 'pending_approval'
    status for Slack-based approval.
    """
    t0 = time.time()
    cb = CircuitBreaker.get('apollo', failure_threshold=5, cooldown_seconds=900)

    if not contacts:
        _record_step(run_id, 'enroll_contacts', 'skipped', 0, 0, 0,
                     {'reason': 'no contacts to enroll'})
        return {'enrolled': 0}

    approval_required = _get_config('approval_required')
    max_per_run = _get_config('max_enrollments_per_run') or 50
    batch = contacts[:max_per_run]

    enrolled = 0
    errors = 0

    for contact in batch:
        try:
            cb.guard()

            if approval_required:
                # Mark as pending approval — Slack bot will approve/reject
                logger.info(f"[PIPELINE] Contact {contact.get('email')} queued for approval")
                continue

            # Apollo enrollment via apollo_pipeline module
            from apollo_pipeline import _enroll_single_contact, _resolve_email_account, _resolve_custom_field_ids, select_sequence
            email = (contact.get('email') or '').strip()
            sequence_id = contact.get('sequence_id', '')
            if not sequence_id:
                seq = select_sequence(tier=0, persona_name=contact.get('persona_name', ''))
                if seq:
                    sequence_id = seq['sequence_id']
            if email and sequence_id:
                ea_id = _resolve_email_account()
                field_map = _resolve_custom_field_ids()
                if ea_id:
                    result = _enroll_single_contact(
                        contact, contact.get('id', 0), email,
                        contact.get('company_name', ''),
                        sequence_id, ea_id, field_map
                    )
                    if result == 'enrolled':
                        enrolled += 1
                    else:
                        errors += 1
            cb.record_success()

        except CircuitBreakerError:
            logger.warning("[PIPELINE] Apollo circuit breaker open, stopping enrollment")
            break
        except Exception as e:
            cb.record_failure(e)
            errors += 1
            logger.error(f"[PIPELINE] Enrollment failed for {contact.get('email', '?')}: {e}")

    duration_ms = int((time.time() - t0) * 1000)
    detail = {
        'batch_size': len(batch),
        'enrolled': enrolled,
        'errors': errors,
        'approval_required': approval_required,
    }
    status = 'success' if errors == 0 else 'partial'
    _record_step(run_id, 'enroll_contacts', status,
                 enrolled, errors, duration_ms, detail)
    logger.info(f"[PIPELINE] Enrollment: {enrolled}/{len(batch)} enrolled")
    return detail


def step_send_notifications(run_id: int, tier_changes: dict,
                            enrollment_result: dict) -> dict:
    """
    Step 6: Send Slack notifications for tier changes and enrollments.

    Uses the existing trigger_webhook() from app.py for backward compatibility.
    The Slack agent (Task #4) may enhance this with richer Block Kit messages.
    """
    t0 = time.time()
    app = _app_module()
    db = _db()
    notifications_sent = 0
    errors = 0

    hot_accounts = tier_changes.get('hot_accounts', [])
    for account in hot_accounts:
        try:
            tier = account.get('current_tier', 0)
            tier_config = db.TIER_CONFIG.get(tier, db.TIER_CONFIG.get(0, {}))
            company_data = {
                'company': account.get('company_name', ''),
                'tier': tier,
                'tier_name': tier_config.get('name', 'Unknown'),
                'evidence': account.get('evidence_summary', ''),
                'github_org': account.get('github_org', ''),
                'revenue': account.get('annual_revenue', ''),
            }
            app.trigger_webhook('tier_change', company_data)
            notifications_sent += 1
        except Exception as e:
            errors += 1
            logger.error(f"[PIPELINE] Notification failed for {account.get('company_name')}: {e}")

    duration_ms = int((time.time() - t0) * 1000)
    detail = {
        'tier_change_notifications': notifications_sent,
        'enrollment_notifications': 0,  # Placeholder for enrollment notifications
        'errors': errors,
    }
    _record_step(run_id, 'notifications', 'success' if errors == 0 else 'partial',
                 notifications_sent, errors, duration_ms, detail)
    logger.info(f"[PIPELINE] Notifications: {notifications_sent} sent")
    return detail


def step_update_metrics(run_id: int, summary: dict) -> dict:
    """
    Step 7: Update system_stats with pipeline run results.
    """
    t0 = time.time()
    db = _db()

    try:
        # The individual steps already call increment_daily_stat where appropriate.
        # This step records the overall pipeline summary.
        db.set_setting('pipeline_last_run_at', datetime.now().isoformat())
        db.set_setting('pipeline_last_run_summary', json.dumps(summary))

        duration_ms = int((time.time() - t0) * 1000)
        _record_step(run_id, 'metrics', 'success', 1, 0, duration_ms, summary)
        return summary

    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        _record_step(run_id, 'metrics', 'error', 0, 1, duration_ms, {'error': str(e)})
        logger.error(f"[PIPELINE] Metrics step failed: {e}")
        return {'error': str(e)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_domain(url: str) -> str:
    """Extract bare domain from a URL or return as-is if already a domain."""
    if not url:
        return ''
    url = url.strip().lower()
    # Remove protocol
    for prefix in ('https://', 'http://', 'www.'):
        if url.startswith(prefix):
            url = url[len(prefix):]
    # Remove path
    url = url.split('/')[0]
    return url


def _get_weekly_email_count() -> int:
    """Count emails generated this week (Monday-Sunday)."""
    db = _db()
    with db.db_connection() as conn:
        cursor = conn.cursor()
        dt_week_start = db._adapt_datetime('-7 days')
        cursor.execute(f'''
            SELECT COUNT(*) as cnt FROM enrollment_contacts
            WHERE created_at >= {dt_week_start}
              AND generated_emails_json IS NOT NULL
        ''')
        row = cursor.fetchone()
    return row['cnt'] if row else 0


# ---------------------------------------------------------------------------
# Full Pipeline Orchestrator
# ---------------------------------------------------------------------------

class PipelineOrchestrator:
    """
    Manages the full automation pipeline with APScheduler.

    Singleton — use PipelineOrchestrator.instance() to get or create.
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._scheduler = None
        self._running = False
        self._current_run_id: Optional[int] = None
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()

    @classmethod
    def instance(cls) -> 'PipelineOrchestrator':
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # -----------------------------------------------------------------
    # Scheduler setup
    # -----------------------------------------------------------------

    def start(self, app=None):
        """Initialize APScheduler and register all pipeline jobs."""
        if self._running:
            logger.warning("[PIPELINE] Orchestrator already running")
            return

        # Initialize tables on first start
        _init_pipeline_tables()

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
        except ImportError:
            logger.error(
                "[PIPELINE] APScheduler not installed. "
                "Add 'apscheduler>=3.10.0' to requirements.txt"
            )
            return

        self._scheduler = BackgroundScheduler(
            job_defaults={
                'coalesce': True,        # Merge missed runs into one
                'max_instances': 1,      # No overlapping runs
                'misfire_grace_time': 3600,  # Allow up to 1h late
            },
            timezone='UTC',
        )

        # Job 1: Daily full pipeline (6 AM UTC by default)
        cron_hour = _get_config('scan_schedule_cron_hour') or 6
        self._scheduler.add_job(
            self.run_full_pipeline,
            CronTrigger(hour=cron_hour, minute=0),
            id='daily_pipeline',
            name='Daily Full Pipeline',
            kwargs={'trigger_type': 'scheduled_daily'},
        )

        # Job 2: Tier 2 hot-lead check every N hours
        tier2_interval = _get_config('tier2_check_interval_hours') or 3
        self._scheduler.add_job(
            self.run_tier2_check,
            IntervalTrigger(hours=tier2_interval),
            id='tier2_check',
            name='Tier 2 Hot Lead Check',
        )

        # Job 3: Weekly digest on Fridays at 9 AM UTC
        self._scheduler.add_job(
            self.run_weekly_digest,
            CronTrigger(day_of_week='fri', hour=9, minute=0),
            id='weekly_digest',
            name='Weekly Digest',
        )

        # Job 4: Health check every 5 minutes
        health_interval = _get_config('health_check_interval_minutes') or 5
        self._scheduler.add_job(
            self.run_health_check,
            IntervalTrigger(minutes=health_interval),
            id='health_check',
            name='Health Check',
        )

        self._scheduler.start()
        self._running = True

        # Register graceful shutdown
        atexit.register(self.shutdown)
        self._register_signal_handlers()

        logger.info(
            f"[PIPELINE] Orchestrator started — daily at {cron_hour}:00 UTC, "
            f"Tier 2 check every {tier2_interval}h, weekly digest Fri 09:00 UTC"
        )

    def shutdown(self):
        """Gracefully shut down the scheduler."""
        if not self._running:
            return
        self._shutdown_event.set()
        if self._scheduler:
            self._scheduler.shutdown(wait=True)
        self._running = False
        logger.info("[PIPELINE] Orchestrator shut down")

    def _register_signal_handlers(self):
        """Register SIGTERM handler for graceful shutdown."""
        def _handle_sigterm(signum, frame):
            logger.info("[PIPELINE] Received SIGTERM, initiating graceful shutdown...")
            self.shutdown()

        try:
            signal.signal(signal.SIGTERM, _handle_sigterm)
        except (ValueError, OSError):
            # Can't set signal handler from non-main thread — that's fine,
            # the atexit handler will still fire.
            pass

    # -----------------------------------------------------------------
    # Pipeline execution
    # -----------------------------------------------------------------

    def run_full_pipeline(self, trigger_type: str = 'scheduled') -> dict:
        """
        Execute the full pipeline: Scan -> Tier -> Contacts -> Emails ->
        Enroll -> Notify -> Metrics.

        Returns a summary dict. Safe to call from scheduler or manually.
        """
        if not _get_config('pipeline_enabled'):
            logger.info("[PIPELINE] Pipeline disabled, skipping run")
            return {'status': 'disabled'}

        with self._lock:
            if self._current_run_id is not None:
                logger.warning("[PIPELINE] Pipeline already running, skipping")
                return {'status': 'already_running'}

        run_id = _create_run(trigger_type)
        with self._lock:
            self._current_run_id = run_id

        logger.info(f"[PIPELINE] === Starting full pipeline run #{run_id} ({trigger_type}) ===")
        steps_completed = 0
        steps_failed = 0
        error_log_parts = []
        summary = {}

        try:
            # Step 1: Scan
            if self._shutdown_event.is_set():
                raise InterruptedError("Shutdown requested")
            scan_result = step_run_scheduled_scans(run_id)
            summary['scan'] = scan_result
            if 'error' not in scan_result:
                steps_completed += 1
            else:
                steps_failed += 1
                error_log_parts.append(f"scan: {scan_result['error']}")

            # Step 2: Detect tier changes
            if self._shutdown_event.is_set():
                raise InterruptedError("Shutdown requested")
            tier_result = step_process_tier_changes(run_id)
            summary['tier_changes'] = {
                k: v for k, v in tier_result.items() if k != 'changed'
            }
            if 'error' not in tier_result:
                steps_completed += 1
            else:
                steps_failed += 1
                error_log_parts.append(f"tier_changes: {tier_result['error']}")

            # Step 3: Discover contacts for hot accounts
            if self._shutdown_event.is_set():
                raise InterruptedError("Shutdown requested")
            hot_accounts = tier_result.get('hot_accounts', [])
            contact_result = step_discover_contacts(run_id, hot_accounts)
            summary['discover_contacts'] = {
                k: v for k, v in contact_result.items() if k != 'contacts'
            }
            if 'error' not in contact_result:
                steps_completed += 1
            else:
                steps_failed += 1
                error_log_parts.append(f"discover_contacts: {contact_result.get('error', '')}")

            # Step 4: Generate emails
            if self._shutdown_event.is_set():
                raise InterruptedError("Shutdown requested")
            contacts = contact_result.get('contacts', [])
            email_result = step_generate_emails(run_id, contacts)
            summary['generate_emails'] = email_result
            if email_result.get('errors', 0) == 0:
                steps_completed += 1
            else:
                steps_failed += 1

            # Step 5: Enroll
            if self._shutdown_event.is_set():
                raise InterruptedError("Shutdown requested")
            enroll_result = step_enroll_contacts(run_id, contacts)
            summary['enroll_contacts'] = enroll_result
            if enroll_result.get('errors', 0) == 0:
                steps_completed += 1
            else:
                steps_failed += 1

            # Step 6: Notify
            if self._shutdown_event.is_set():
                raise InterruptedError("Shutdown requested")
            notify_result = step_send_notifications(run_id, tier_result, enroll_result)
            summary['notifications'] = notify_result
            if notify_result.get('errors', 0) == 0:
                steps_completed += 1
            else:
                steps_failed += 1

            # Step 7: Metrics
            if self._shutdown_event.is_set():
                raise InterruptedError("Shutdown requested")
            step_update_metrics(run_id, summary)
            steps_completed += 1

            overall_status = 'success' if steps_failed == 0 else 'partial'
            _complete_run(run_id, overall_status, steps_completed, steps_failed,
                         '\n'.join(error_log_parts), summary)
            logger.info(
                f"[PIPELINE] === Run #{run_id} complete: {steps_completed} OK, "
                f"{steps_failed} failed ==="
            )

        except InterruptedError:
            _complete_run(run_id, 'interrupted', steps_completed, steps_failed,
                         'Shutdown requested', summary)
            logger.info(f"[PIPELINE] Run #{run_id} interrupted by shutdown")

        except Exception as e:
            steps_failed += 1
            error_log_parts.append(f"fatal: {e}")
            _complete_run(run_id, 'error', steps_completed, steps_failed,
                         '\n'.join(error_log_parts), summary)
            logger.error(f"[PIPELINE] Run #{run_id} failed: {e}")

        finally:
            with self._lock:
                self._current_run_id = None

        summary['status'] = 'success' if steps_failed == 0 else 'partial'
        summary['steps_completed'] = steps_completed
        summary['steps_failed'] = steps_failed
        return summary

    # -----------------------------------------------------------------
    # Lightweight scheduled jobs
    # -----------------------------------------------------------------

    def run_tier2_check(self):
        """
        Fast check for new Tier 2 (Preparing/Hot) accounts.

        Only triggers contact discovery and notifications for newly
        promoted Tier 2 accounts. Does not run a full pipeline.
        """
        if not _get_config('pipeline_enabled'):
            return

        db = _db()
        with db.db_connection() as conn:
            cursor = conn.cursor()

            # Find accounts promoted to Tier 2 in the last check interval
            interval_hours = _get_config('tier2_check_interval_hours') or 3
            dt_cutoff = db._adapt_datetime(f'-{interval_hours} hours')
            cursor.execute(f'''
                SELECT id, company_name, github_org, current_tier, website,
                       annual_revenue, evidence_summary
                FROM monitored_accounts
                WHERE current_tier = 2
                  AND status_changed_at >= {dt_cutoff}
                  AND archived_at IS NULL
            ''')
            new_tier2 = [dict(r) for r in cursor.fetchall()]

        if new_tier2:
            logger.info(f"[PIPELINE] Tier 2 check: {len(new_tier2)} new hot leads found")
            # Trigger notifications immediately for hot leads
            for account in new_tier2:
                try:
                    app = _app_module()
                    tier_config = db.TIER_CONFIG.get(2, {})
                    app.trigger_webhook('tier_change', {
                        'company': account.get('company_name', ''),
                        'tier': 2,
                        'tier_name': tier_config.get('name', 'Preparing'),
                        'evidence': account.get('evidence_summary', ''),
                        'github_org': account.get('github_org', ''),
                        'revenue': account.get('annual_revenue', ''),
                    })
                except Exception as e:
                    logger.error(f"[PIPELINE] Tier 2 notification failed: {e}")
        else:
            logger.debug("[PIPELINE] Tier 2 check: no new hot leads")

    def run_weekly_digest(self):
        """
        Generate and send a weekly summary digest.

        Collects stats from the past 7 days and sends a Slack notification.
        """
        if not _get_config('pipeline_enabled'):
            return

        db = _db()
        stats = db.get_stats_last_n_days(7)

        total_scans = sum(s.get('scans_run', 0) for s in stats)
        total_webhooks = sum(s.get('webhooks_fired', 0) for s in stats)
        total_api_calls = sum(s.get('api_calls_estimated', 0) for s in stats)

        # Count tier distribution
        with db.db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT current_tier, COUNT(*) as cnt
                FROM monitored_accounts
                WHERE archived_at IS NULL
                GROUP BY current_tier
            ''')
            tier_dist = {r['current_tier']: r['cnt'] for r in cursor.fetchall()}
            cursor.execute('SELECT COUNT(*) as cnt FROM monitored_accounts WHERE archived_at IS NULL')
            total_accounts = cursor.fetchone()['cnt']

        digest = {
            'period': 'weekly',
            'total_accounts': total_accounts,
            'scans_run': total_scans,
            'api_calls': total_api_calls,
            'webhooks_fired': total_webhooks,
            'tier_distribution': tier_dist,
        }

        # Store digest
        db.set_setting('pipeline_last_digest', json.dumps(digest))
        db.set_setting('pipeline_last_digest_at', datetime.now().isoformat())

        logger.info(
            f"[PIPELINE] Weekly digest: {total_accounts} accounts, "
            f"{total_scans} scans, T2={tier_dist.get(2, 0)} hot leads"
        )

        # Send digest as Slack notification
        try:
            app = _app_module()
            app.trigger_webhook('weekly_digest', digest)
        except Exception as e:
            logger.error(f"[PIPELINE] Weekly digest notification failed: {e}")

    def run_health_check(self):
        """
        Periodic health check — verifies DB connectivity, API keys, scheduler.

        Stores the result in system_settings for the /api/pipeline/health endpoint.
        """
        health = {
            'timestamp': datetime.now().isoformat(),
            'checks': {},
        }

        # Check 1: Database connectivity
        try:
            db = _db()
            with db.db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT 1')
            health['checks']['database'] = {'status': 'ok'}
        except Exception as e:
            health['checks']['database'] = {'status': 'error', 'message': str(e)}

        # Check 2: GitHub API key validity
        try:
            from config import Config
            if Config.GITHUB_TOKENS:
                health['checks']['github_api'] = {
                    'status': 'ok',
                    'tokens_configured': len(Config.GITHUB_TOKENS),
                }
            else:
                health['checks']['github_api'] = {
                    'status': 'warning',
                    'message': 'No GitHub tokens configured',
                }
        except Exception as e:
            health['checks']['github_api'] = {'status': 'error', 'message': str(e)}

        # Check 3: Apollo API key
        try:
            import os
            apollo_key = os.environ.get('APOLLO_API_KEY', '')
            health['checks']['apollo_api'] = {
                'status': 'ok' if apollo_key else 'warning',
                'configured': bool(apollo_key),
            }
        except Exception as e:
            health['checks']['apollo_api'] = {'status': 'error', 'message': str(e)}

        # Check 4: AI service
        try:
            import os
            ai_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
            health['checks']['ai_service'] = {
                'status': 'ok' if ai_key else 'warning',
                'configured': bool(ai_key),
            }
        except Exception as e:
            health['checks']['ai_service'] = {'status': 'error', 'message': str(e)}

        # Check 5: Scheduler status
        health['checks']['scheduler'] = {
            'status': 'ok' if self._running and self._scheduler and self._scheduler.running else 'error',
            'running': self._running,
        }

        # Check 6: Circuit breakers
        breakers = CircuitBreaker.get_all()
        breaker_statuses = {}
        for name, cb in breakers.items():
            status = cb.status()
            breaker_statuses[name] = {
                'state': status['state'],
                'consecutive_failures': status['consecutive_failures'],
            }
        health['checks']['circuit_breakers'] = breaker_statuses

        # Overall status
        has_errors = any(
            c.get('status') == 'error'
            for c in health['checks'].values()
            if isinstance(c, dict) and 'status' in c
        )
        health['status'] = 'unhealthy' if has_errors else 'healthy'

        # Persist
        try:
            db = _db()
            db.set_setting('pipeline_health', json.dumps(health))
        except Exception as e:
            logging.warning(f"[PIPELINE] Failed to persist pipeline_health setting: {e}")

        return health

    # -----------------------------------------------------------------
    # Control API helpers
    # -----------------------------------------------------------------

    def pause(self):
        """Pause the pipeline (stops scheduling new runs)."""
        _set_config('pipeline_enabled', 'false')
        logger.info("[PIPELINE] Pipeline paused")

    def resume(self):
        """Resume the pipeline."""
        _set_config('pipeline_enabled', 'true')
        logger.info("[PIPELINE] Pipeline resumed")

    def is_paused(self) -> bool:
        return not _get_config('pipeline_enabled')

    def get_status(self) -> dict:
        """Return full pipeline status for the API."""
        jobs = []
        if self._scheduler:
            for job in self._scheduler.get_jobs():
                next_run = job.next_run_time
                jobs.append({
                    'id': job.id,
                    'name': job.name,
                    'next_run': next_run.isoformat() if next_run else None,
                })

        # Get last run info
        recent_runs = get_recent_runs(5)

        # Get circuit breaker statuses
        breakers = {
            name: cb.status()
            for name, cb in CircuitBreaker.get_all().items()
        }

        return {
            'enabled': _get_config('pipeline_enabled'),
            'running': self._running,
            'current_run_id': self._current_run_id,
            'scheduled_jobs': jobs,
            'recent_runs': recent_runs,
            'circuit_breakers': breakers,
            'config': {
                key: _get_config(key)
                for key in _DEFAULT_CONFIG
            },
        }

    def get_health(self) -> dict:
        """Return the most recent health check result."""
        db = _db()
        raw = db.get_setting('pipeline_health')
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        # Run a fresh health check
        return self.run_health_check()
