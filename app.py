"""
Lead Machine - Deep-Dive Research Engine

A Flask application for analyzing GitHub organizations for localization signals.
"""
import json
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import requests
from datetime import datetime
from flask import Flask, render_template, Response, request, jsonify, redirect, url_for, stream_with_context, send_file
from config import Config
from database import (
    save_report, get_report, get_recent_reports, search_reports,
    update_account_status, get_all_accounts, get_all_accounts_datatable, get_tier_counts, add_account_to_tier_0, TIER_CONFIG,
    get_account_by_company, get_account_by_company_case_insensitive,
    mark_account_as_invalid, get_refreshable_accounts, delete_account,
    get_db_connection, get_setting, set_setting, increment_daily_stat,
    get_stats_last_n_days, log_webhook, get_recent_webhook_logs,
    set_scan_status, get_scan_status, get_queued_and_processing_accounts,
    clear_stale_scan_statuses, reset_all_scan_statuses, batch_set_scan_status_queued,
    reset_stale_queued_accounts, reset_all_queued_to_idle, clear_misclassified_errors,
    SCAN_STATUS_IDLE, SCAN_STATUS_QUEUED, SCAN_STATUS_PROCESSING,
    save_signals, cleanup_duplicate_accounts, update_account_annual_revenue,
    update_account_website, update_account_notes,
    create_import_batch, get_pending_import_batches, update_batch_progress, get_import_batch,
    increment_hourly_api_calls, get_current_hour_api_calls, cleanup_old_hourly_stats
)
from monitors.scanner import deep_scan_generator
from monitors.discovery import search_github_orgs, resolve_org_fast, discover_companies_via_ai
from ai_summary import generate_analysis
from pdf_generator import generate_report_pdf
from agentmail_client import is_agentmail_configured, send_email_draft


app = Flask(__name__)
app.config.from_object(Config)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_top_contributors(org_login: str, repo_name: str, limit: int = 5) -> list:
    """
    Fetch top contributors for a repository with resolved real names.

    Uses the GitHub Contributors API to get the top contributors,
    then makes a second API call to /users/{username} for each to fetch
    their real name, public email, and website.

    Args:
        org_login: GitHub organization login name
        repo_name: Repository name
        limit: Maximum number of contributors to return (default 5)

    Returns:
        List of contributor dicts with: login, name, email, blog, github_url
    """
    from utils import make_github_request

    try:
        url = f"{Config.GITHUB_API_BASE}/repos/{org_login}/{repo_name}/contributors"
        response = make_github_request(url, params={'per_page': limit + 5}, timeout=10)
        if response.status_code == 200:
            contributors = []
            for c in response.json():
                # Filter out bots
                if c['type'] != 'Bot' and '[bot]' not in c['login']:
                    login = c['login']

                    # Fetch full user profile to get real name, email, blog
                    user_data = {
                        'login': login,
                        'github_url': c['html_url'],
                        'name': login,  # Fallback
                        'email': '',
                        'blog': ''
                    }

                    try:
                        user_url = f"{Config.GITHUB_API_BASE}/users/{login}"
                        user_response = make_github_request(user_url, timeout=10)
                        if user_response.status_code == 200:
                            user_info = user_response.json()
                            user_data['name'] = user_info.get('name') or login
                            user_data['email'] = user_info.get('email') or ''
                            user_data['blog'] = user_info.get('blog') or ''
                    except Exception as e:
                        print(f"[ZAPIER] Failed to fetch user profile for {login}: {e}")

                    contributors.append(user_data)

                    if len(contributors) >= limit:
                        break

            return contributors
    except Exception as e:
        print(f"[ZAPIER] Contributor fetch failed for {org_login}/{repo_name}: {e}")
    return []


# Custom Jinja2 filter to normalize URLs
@app.template_filter('normalize_url')
def normalize_url_filter(url):
    """Ensure URL has a protocol prefix (https://) for proper linking."""
    if not url:
        return ''
    url = url.strip()
    if not url.lower().startswith(('http://', 'https://')):
        return 'https://' + url
    return url


# =============================================================================
# WEBHOOK NOTIFICATIONS - Push leads to Slack/Zapier/Salesforce
# =============================================================================

def format_slack_message(event_type: str, company_data: dict) -> dict:
    """
    Format webhook data as a Slack-ready message payload.

    Creates a rich Block Kit message with:
    - Company name and GitHub link
    - Tier status with color
    - Why they're a good lead (evidence)
    - Link to the full report
    - Key signal details

    Args:
        event_type: Type of event (e.g., 'tier_change')
        company_data: Dictionary with company info, tier, signals, etc.

    Returns:
        Dictionary formatted for Slack's incoming webhook API
    """
    company = company_data.get('company', 'Unknown')
    tier = company_data.get('tier', 0)
    tier_name = company_data.get('tier_name', 'Unknown')
    evidence = company_data.get('evidence', company_data.get('signal', ''))
    github_org = company_data.get('github_org', '')
    report_id = company_data.get('report_id')
    signals_summary = company_data.get('signals_summary', [])
    revenue = company_data.get('revenue')

    # Get base URL from Flask request context or environment/config
    try:
        from flask import request, has_request_context
        if has_request_context():
            base_url = request.host_url.rstrip('/')
        else:
            # Fallback for background threads without request context
            base_url = os.environ.get('BASE_URL', 'http://localhost:5000')
    except Exception:
        base_url = os.environ.get('BASE_URL', 'http://localhost:5000')

    # Determine color and emoji based on tier
    tier_colors = {
        0: '#808080',  # grey - Tracking
        1: '#FFD700',  # gold - Thinking/Warm Lead
        2: '#28A745',  # green - Preparing/Hot Lead
        3: '#DC3545',  # red - Launched/Too Late
        4: '#404040',  # dark grey - Invalid
    }

    tier_emojis = {
        0: '👀',
        1: '🔍',
        2: '🎯',
        3: '❌',
        4: '⚠️',
    }

    color = tier_colors.get(tier, '#808080')
    emoji = tier_emojis.get(tier, '')

    # Build header blocks
    header_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji} *New {tier_name} Lead Detected*"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Company:*\n{company}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Revenue:*\n{revenue if revenue else 'N/A'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Status:*\n{tier_name}"
                }
            ]
        }
    ]

    # Add GitHub org link if available
    if github_org:
        header_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*GitHub Org:* <https://github.com/{github_org}|{github_org}>"
            }
        })

    # Add evidence section (why they're a good lead)
    evidence_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Why This Lead?*\n_{evidence}_"
            }
        }
    ]

    # Add signals summary if available
    signals_blocks = []
    if signals_summary:
        signal_text = "*Key Signals Detected:*\n"
        for i, signal in enumerate(signals_summary[:5], 1):  # Show top 5 signals
            if isinstance(signal, dict):
                signal_type = signal.get('type', signal.get('signal_type', 'Unknown'))
                description = signal.get('description', signal.get('Evidence', ''))
                signal_text += f"{i}. *{signal_type}:* {description}\n"
            else:
                signal_text += f"{i}. {signal}\n"

        signals_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": signal_text
            }
        })

    # Add report link if available
    action_blocks = []
    if report_id:
        report_url = f"{base_url}/report/{report_id}"
        action_blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View Full Report"
                    },
                    "url": report_url,
                    "style": "primary"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Download PDF"
                    },
                    "url": f"{report_url}/pdf"
                }
            ]
        })

    # Add footer with timestamp
    footer_blocks = [
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"🤖 GitHub Dossier • {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                }
            ]
        }
    ]

    # Combine all blocks
    blocks = header_blocks + evidence_blocks + signals_blocks + action_blocks + footer_blocks

    # Return Slack incoming webhook format
    return {
        "blocks": blocks,
        "attachments": [
            {
                "fallback": f"{tier_name} Lead: {company}",
                "color": color,
                "text": evidence
            }
        ]
    }


def enrich_webhook_data(company_data: dict, report_id: Optional[int] = None) -> dict:
    """
    Enrich webhook data with report details and signals.

    Fetches the most recent report for a company and includes:
    - Report ID (for report link)
    - GitHub organization
    - Top signals detected
    - Scan details

    Args:
        company_data: Base company data dictionary
        report_id: Optional report ID (if not provided, fetches most recent)

    Returns:
        Enriched company_data dictionary with additional fields
    """
    enriched = company_data.copy()

    try:
        company_name = company_data.get('company', company_data.get('company_name', ''))

        # If report_id not provided, fetch most recent report
        if report_id is None:
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, github_org FROM reports
                    WHERE company_name = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                ''', (company_name,))
                row = cursor.fetchone()
                if row:
                    report_id = row['id']
                    if not enriched.get('github_org'):
                        enriched['github_org'] = row['github_org']
            finally:
                conn.close()

        # If we have a report_id, fetch signals
        if report_id:
            enriched['report_id'] = report_id

            # Fetch signals for this report
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT signal_type, description, file_path
                    FROM scan_signals
                    WHERE report_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 10
                ''', (report_id,))
                signal_rows = cursor.fetchall()

                if signal_rows:
                    signals_summary = []
                    for sig_row in signal_rows:
                        signals_summary.append({
                            'type': sig_row['signal_type'],
                            'description': sig_row['description'],
                            'file_path': sig_row['file_path']
                        })
                    enriched['signals_summary'] = signals_summary
            finally:
                conn.close()
    except Exception as e:
        print(f"[WEBHOOK] Error enriching webhook data: {str(e)}")
        # Continue with non-enriched data if error occurs

    return enriched


def trigger_webhook(event_type: str, company_data: dict) -> None:
    """
    Send a webhook notification asynchronously.

    This function runs in a background thread to avoid blocking the UI.
    Fetches the webhook URL from system_settings and sends a POST request.

    Supports both Slack webhooks (detects hooks.slack.com) and generic webhooks.
    - Slack: Formats as rich Block Kit message with buttons and colors
    - Generic: Sends standard JSON payload for Zapier, etc.

    Args:
        event_type: Type of event (e.g., 'tier_change', 'scan_complete')
        company_data: Dictionary containing company information to send
    """
    # Check if webhooks are enabled
    webhook_enabled = get_setting('webhook_enabled')
    if webhook_enabled != 'true':
        print("[WEBHOOK] Webhooks are paused, skipping notification")
        return

    webhook_url = get_setting('webhook_url')
    if not webhook_url:
        print("[WEBHOOK] No webhook_url configured in settings, skipping notification")
        return

    def send_webhook():
        company_name = company_data.get('company', company_data.get('company_name', 'Unknown'))

        # Detect if this is a Slack webhook
        is_slack_webhook = 'hooks.slack.com' in webhook_url

        # Prepare payload based on webhook type
        if is_slack_webhook:
            # Format as Slack Block Kit message
            try:
                payload = format_slack_message(event_type, company_data)
            except Exception as e:
                print(f"[WEBHOOK] Error formatting Slack message: {str(e)}, falling back to simple Slack payload")
                # Slack requires 'text' field as fallback
                tier_name = company_data.get('tier_name', 'Unknown')
                evidence = company_data.get('evidence', '')
                payload = {
                    'text': f"New {tier_name} Lead: {company_name}\n{evidence}",
                    'event_type': event_type,
                    'timestamp': datetime.now().isoformat(),
                    **company_data
                }
        else:
            # Use generic JSON payload for Zapier, custom endpoints, etc.
            payload = {
                'event_type': event_type,
                'timestamp': datetime.now().isoformat(),
                **company_data
            }

        try:
            response = requests.post(
                webhook_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            if response.status_code >= 200 and response.status_code < 300:
                webhook_type = "Slack" if is_slack_webhook else "Generic"
                print(f"[WEBHOOK] Success ({webhook_type}): {company_name} -> {webhook_url} (status: {response.status_code})")
                try:
                    log_webhook(event_type, company_name, 'success')
                    increment_daily_stat('webhooks_fired')
                except Exception as db_err:
                    print(f"[WEBHOOK] DB logging error: {db_err}")
            else:
                print(f"[WEBHOOK] Failed: {company_name} -> {webhook_url} (status: {response.status_code})")
                try:
                    log_webhook(event_type, company_name, 'fail')
                except Exception as db_err:
                    print(f"[WEBHOOK] DB logging error: {db_err}")
        except requests.exceptions.Timeout:
            print(f"[WEBHOOK] Timeout: {company_name} -> {webhook_url}")
            try:
                log_webhook(event_type, company_name, 'fail')
            except Exception:
                pass
        except requests.exceptions.RequestException as e:
            print(f"[WEBHOOK] Error: {company_name} -> {str(e)}")
            try:
                log_webhook(event_type, company_name, 'fail')
            except Exception:
                pass

    # Run in background thread to avoid blocking
    webhook_thread = threading.Thread(target=send_webhook, daemon=True, name="WebhookSender")
    webhook_thread.start()


# =============================================================================
# THREAD POOL EXECUTOR - Concurrent scan processing with DB-backed state
# =============================================================================

# Configurable number of workers (default 5)
MAX_SCAN_WORKERS = int(os.environ.get('SCAN_WORKERS', 5))

# Thread pool executor for concurrent scans
_executor = None
_executor_lock = threading.Lock()


def get_executor() -> ThreadPoolExecutor:
    """
    Get or create the thread pool executor.

    The executor is created lazily and reused across requests.
    """
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=MAX_SCAN_WORKERS,
                thread_name_prefix="ScanWorker"
            )
            print(f"[EXECUTOR] Created ThreadPoolExecutor with {MAX_SCAN_WORKERS} workers")
        return _executor


def perform_background_scan(company_name: str):
    """
    Execute a background scan for a company.

    CRITICAL: This function creates its own database connection for thread safety.
    SQLite requires each thread to have its own connection.

    Updates the database scan_status at start ('processing') and end ('idle').
    Handles all exceptions to ensure status is always reset.
    Stores any errors in last_scan_error for user visibility.
    """
    print(f"[WORKER] Starting background scan for: {company_name}")
    scan_error = None  # Track errors for user visibility

    try:
        # Update database status to 'processing' (clears any previous error)
        set_scan_status(company_name, SCAN_STATUS_PROCESSING, 'Starting scan...')

        start_time = time.time()
        scan_data = None
        analysis_data = None

        # Phase 1: Run the deep scan (silent)
        account = get_account_by_company_case_insensitive(company_name)
        last_scanned_at = account.get('last_scanned_at') if account else None
        # CRITICAL: Pass the pre-linked github_org so scanner uses it directly
        # instead of trying to discover from company name (which can fail)
        github_org = account.get('github_org') if account else None
        for message in deep_scan_generator(company_name, last_scanned_at, github_org):
            # Capture LOG messages for real-time progress feedback in UI
            if 'data: LOG:' in message:
                log_msg = message.split('data: LOG:', 1)[1].strip()
                # Clean up the message and check for progress keywords
                clean_msg = log_msg.replace('\n', '').strip()
                
                # Report key scanning steps to the database for UI display
                if any(kw in clean_msg for kw in ['PHASE', 'Scanning', 'step', 'MEGA-CORP', 'Searching for']):
                    set_scan_status(company_name, SCAN_STATUS_PROCESSING, clean_msg)

            # Check for scan errors - mark as invalid and exit
            if 'data: ERROR:' in message:
                error_msg = message.split('data: ERROR:', 1)[1].strip()
                error_msg = error_msg.replace('\n', '').strip()
                print(f"[WORKER] Scan error for {company_name}: {error_msg}")
                mark_account_as_invalid(company_name, error_msg)
                return

            if 'SCAN_COMPLETE:' in message:
                json_str = message.split('SCAN_COMPLETE:', 1)[1].strip()
                if json_str.startswith('data: '):
                    json_str = json_str[6:]
                scan_data = json.loads(json_str)

        if not scan_data:
            print(f"[WORKER] No scan data generated for: {company_name}")
            mark_account_as_invalid(company_name, 'No scan data generated')
            return

        print(f"[WORKER] Scan complete for {company_name}, generating AI analysis...")
        set_scan_status(company_name, SCAN_STATUS_PROCESSING, 'Generating AI analysis...')

        # Phase 2: Generate AI analysis (silent)
        try:
            for message in generate_analysis(scan_data):
                if 'ANALYSIS_COMPLETE:' in message:
                    json_str = message.split('ANALYSIS_COMPLETE:', 1)[1].strip()
                    if json_str.startswith('data: '):
                        json_str = json_str[6:]
                    analysis_data = json.loads(json_str)
        except Exception as e:
            print(f"[WORKER] AI analysis failed for {company_name}: {str(e)}")
            analysis_data = {'error': 'Analysis failed'}

        # Phase 3: Save report to database (uses fresh connection internally)
        set_scan_status(company_name, SCAN_STATUS_PROCESSING, 'Saving report...')
        duration = time.time() - start_time
        try:
            report_id = save_report(
                company_name=company_name,
                github_org=scan_data.get('org_login', ''),
                scan_data=scan_data,
                ai_analysis=analysis_data or {},
                scan_duration=duration
            )
            print(f"[WORKER] Report saved for {company_name} (ID: {report_id})")
        except Exception as e:
            scan_error = f"Failed to save report: {str(e)}"
            print(f"[WORKER] {scan_error}")
            return

        # Phase 3b: Save signals detected during the scan
        try:
            signals = scan_data.get('signals', [])
            if signals:
                signals_count = save_signals(report_id, company_name, signals)
                print(f"[WORKER] Saved {signals_count} signals for {company_name}")
        except Exception as e:
            # Non-fatal error - continue but record for visibility
            scan_error = f"Warning: Failed to save signals: {str(e)}"
            print(f"[WORKER] {scan_error}")

        # Phase 4: Update monitored account status and tier
        try:
            result = update_account_status(scan_data, report_id)
            tier_name = result.get('tier_name', 'Unknown')
            print(f"[WORKER] Account status updated for {company_name}: Tier {result.get('tier')} ({tier_name})")

            # Phase 5: Trigger webhook if tier changed to Thinking or Preparing
            if result.get('webhook_event'):
                webhook_data = {
                    'company': company_name,
                    'tier': result.get('tier'),
                    'tier_name': tier_name,
                    'evidence': result.get('evidence', ''),
                    'github_org': scan_data.get('org_login', ''),
                    'revenue': result.get('revenue')
                }
                # Enrich with report details and signals
                webhook_data = enrich_webhook_data(webhook_data, report_id)
                trigger_webhook('tier_change', webhook_data)
                print(f"[WORKER] Webhook triggered for {company_name} (Tier {result.get('tier')})")
        except Exception as e:
            # Store the error for user visibility instead of silently failing
            scan_error = f"Tier classification failed: {str(e)}"
            print(f"[WORKER] {scan_error}")

        print(f"[WORKER] Completed scan for {company_name} in {duration:.1f}s")

    except Exception as e:
        scan_error = f"Scan failed: {str(e)}"
        print(f"[WORKER] Background scan failed for {company_name}: {str(e)}")
    finally:
        # ALWAYS reset scan status to idle when done (success or failure)
        # Pass any error that occurred so it's stored for user visibility
        set_scan_status(company_name, SCAN_STATUS_IDLE, error=scan_error)


def spawn_background_scan(company_name: str):
    """
    Submit a company for background scanning using the thread pool.

    The scan will run asynchronously without blocking the API response.
    The account tier is automatically updated with the scan results.
    Scan status is tracked in the database, not in memory.
    """
    # Mark as queued in database before submitting to executor
    set_scan_status(company_name, SCAN_STATUS_QUEUED)

    # Submit to thread pool
    executor = get_executor()
    future = executor.submit(perform_background_scan, company_name)
    print(f"[EXECUTOR] Submitted scan for {company_name}")


def validate_revenue_value(value):
    """
    Validate that a value looks like a revenue number.

    Returns the value if it matches revenue patterns (e.g., "$50M", "4.6B", "50000000"),
    or None if it appears to be plain text (e.g., "Inc.", "Company Name").
    """
    import re
    if not value or not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None

    # Pattern matches revenue formats like: $50M, $4.6B, 50000000, $50,000,000, 500K, €50M
    # Must contain at least one digit to be considered a revenue value
    revenue_pattern = r'^[$€£¥]?\s*[\d,.]+\s*[KkMmBbTt]?$'

    if re.match(revenue_pattern, trimmed):
        return trimmed

    return None


def process_import_batch_worker(batch_id: int):
    """
    Process an import batch from the database.

    This worker function:
    1. Fetches the batch data from DB
    2. Updates status to 'processing'
    3. Loops through companies, processing each item
    4. For each item: check if exists (idempotency), add to tier 0
    5. Updates progress every 10 items
    6. Marks batch as 'completed' when done

    Note: Companies are added without GitHub org resolution. Users can
    manually link GitHub orgs via the /api/update-org endpoint.

    This is resilient to restarts - progress is persisted and can be resumed.
    """
    print(f"[BATCH-WORKER] Starting batch {batch_id}")

    try:
        # Fetch batch data from DB
        batch = get_import_batch(batch_id)
        if not batch:
            print(f"[BATCH-WORKER] Batch {batch_id} not found")
            return

        companies = batch.get('companies', [])
        processed_count = batch.get('processed_count', 0)
        total_count = batch.get('total_count', len(companies))

        print(f"[BATCH-WORKER] Batch {batch_id}: {total_count} companies, resuming from {processed_count}")

        # Update status to 'processing'
        update_batch_progress(batch_id, processed_count, status='processing')

        # Track results for logging
        added = []
        skipped = []

        # Process each company starting from where we left off
        for i, company_item in enumerate(companies[processed_count:], start=processed_count):
            # Support both string format and object format with annual_revenue/website
            if isinstance(company_item, dict):
                company_name = company_item.get('name', '').strip()
                # Validate revenue - ignore text that doesn't look like a number
                annual_revenue = validate_revenue_value(company_item.get('annual_revenue'))
                website = company_item.get('website', '').strip() if company_item.get('website') else None
            else:
                company_name = str(company_item).strip()
                annual_revenue = None
                website = None

            if not company_name:
                processed_count = i + 1
                continue

            try:
                # Check if account already exists (idempotency)
                existing = get_account_by_company_case_insensitive(company_name)
                if existing:
                    # If annual_revenue or website provided and account exists, enrich it
                    if annual_revenue:
                        update_account_annual_revenue(company_name, annual_revenue)
                    if website:
                        update_account_website(company_name, website)
                    skipped.append(company_name)
                    processed_count = i + 1
                    # Update progress every 10 items
                    if processed_count % 10 == 0:
                        update_batch_progress(batch_id, processed_count)
                        print(f"[BATCH-WORKER] Batch {batch_id}: processed {processed_count}/{total_count}")
                    continue

                # Add to monitored_accounts at Tier 0 without GitHub org
                # Users can manually link GitHub orgs via /api/update-org
                add_account_to_tier_0(company_name, '', annual_revenue, website)
                added.append(company_name)

            except Exception as e:
                print(f"[BATCH-WORKER] Error processing {company_name}: {str(e)}")

            processed_count = i + 1

            # Update progress every 10 items
            if processed_count % 10 == 0:
                update_batch_progress(batch_id, processed_count)
                print(f"[BATCH-WORKER] Batch {batch_id}: processed {processed_count}/{total_count}")

        # Mark batch as completed
        update_batch_progress(batch_id, processed_count, status='completed')
        print(f"[BATCH-WORKER] Batch {batch_id} completed: {len(added)} added, {len(skipped)} skipped")

    except Exception as e:
        print(f"[BATCH-WORKER] Batch {batch_id} failed with error: {str(e)}")
        # Mark as failed but preserve progress
        try:
            update_batch_progress(batch_id, processed_count, status='failed')
        except Exception:
            pass


@app.route('/favicon.ico')
def favicon():
    """Return empty response for favicon to prevent 404 errors.

    The actual favicon is defined as an inline SVG data URI in base.html,
    but browsers also request /favicon.ico automatically.
    """
    return Response(status=204)


@app.route('/')
def index():
    """Redirect to accounts page."""
    return redirect(url_for('accounts'))


@app.route('/scan/<company>')
def scan_page(company: str):
    """Render the live console page for scanning."""
    return render_template('console.html', company=company)


@app.route('/stream_scan/<company>')
def stream_scan(company: str):
    """
    Stream the deep scan results using Server-Sent Events.

    This endpoint keeps the connection alive while the scan runs,
    preventing browser timeouts during long operations.
    """
    def generate():
        start_time = time.time()
        scan_data = None
        analysis_data = None

        # Phase 1: Run the deep scan
        try:
            account = get_account_by_company_case_insensitive(company)
            last_scanned_at = account.get('last_scanned_at') if account else None
            # CRITICAL: Pass the pre-linked github_org so scanner uses it directly
            github_org = account.get('github_org') if account else None
            for message in deep_scan_generator(company, last_scanned_at, github_org):
                yield message

                # Check if this is the scan complete message
                if 'SCAN_COMPLETE:' in message:
                    json_str = message.split('SCAN_COMPLETE:', 1)[1].strip()
                    # Remove the SSE data prefix formatting
                    if json_str.startswith('data: '):
                        json_str = json_str[6:]
                    scan_data = json.loads(json_str)

        except Exception as e:
            yield f"data: ERROR:Scan failed: {str(e)}\n\n"
            return

        if not scan_data:
            yield f"data: ERROR:No scan data generated\n\n"
            return

        # Phase 2: Generate AI analysis
        try:
            for message in generate_analysis(scan_data):
                yield message

                # Check if this is the analysis complete message
                if 'ANALYSIS_COMPLETE:' in message:
                    json_str = message.split('ANALYSIS_COMPLETE:', 1)[1].strip()
                    if json_str.startswith('data: '):
                        json_str = json_str[6:]
                    analysis_data = json.loads(json_str)

        except Exception as e:
            yield f"data: LOG:AI analysis error: {str(e)}\n\n"
            analysis_data = {'error': str(e), 'executive_summary': 'Analysis failed'}

        # Phase 3: Save report to database
        duration = time.time() - start_time
        yield f"data: LOG:Saving report to database...\n\n"

        try:
            report_id = save_report(
                company_name=company,
                github_org=scan_data.get('org_login', ''),
                scan_data=scan_data,
                ai_analysis=analysis_data or {},
                scan_duration=duration
            )
            yield f"data: LOG:Report saved (ID: {report_id})\n\n"

        except Exception as e:
            yield f"data: LOG:Warning: Could not save report: {str(e)}\n\n"
            report_id = None

        # Phase 3b: Save signals detected during the scan
        if report_id:
            try:
                signals = scan_data.get('signals', [])
                if signals:
                    signals_count = save_signals(report_id, company, signals)
                    yield f"data: LOG:Saved {signals_count} signals to database\n\n"
            except Exception as e:
                yield f"data: LOG:Warning: Could not save signals: {str(e)}\n\n"

        # Phase 3.5: Update monitored account status
        try:
            account_result = update_account_status(scan_data, report_id)
            tier_name = account_result.get('tier_name', 'Unknown')
            tier_status = account_result.get('tier_status', '')
            yield f"data: LOG:Account status updated: {tier_name} ({tier_status})\n\n"

            if account_result.get('tier_changed'):
                yield f"data: LOG:Tier changed! Evidence: {account_result.get('evidence', 'N/A')}\n\n"

            # Phase 3.6: Trigger webhook if tier changed to Thinking or Preparing
            if account_result.get('webhook_event'):
                webhook_data = {
                    'company': company,
                    'tier': account_result.get('tier'),
                    'tier_name': tier_name,
                    'evidence': account_result.get('evidence', ''),
                    'github_org': scan_data.get('org_login', ''),
                    'revenue': account_result.get('revenue')
                }
                # Enrich with report details and signals
                webhook_data = enrich_webhook_data(webhook_data, report_id)
                trigger_webhook('tier_change', webhook_data)
                yield f"data: LOG:Webhook notification sent for {tier_name} lead\n\n"

        except Exception as e:
            yield f"data: LOG:Warning: Could not update account status: {str(e)}\n\n"

        # Phase 4: Send final result
        final_result = {
            'report_id': report_id,
            'scan_data': scan_data,
            'analysis': analysis_data,
            'duration_seconds': duration
        }

        yield f"data: COMPLETE:{json.dumps(final_result)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',  # Disable nginx buffering
            'Access-Control-Allow-Origin': '*'
        }
    )


@app.route('/report/<int:report_id>/pdf')
def download_pdf(report_id: int):
    """Generate and download a PDF report."""
    report = get_report(report_id)
    if not report:
        return render_template('error.html', message='Report not found'), 404
        
    # Create temp directory for PDFs if it doesn't exist
    pdf_dir = os.path.join(app.root_path, 'static', 'pdfs')
    os.makedirs(pdf_dir, exist_ok=True)
    
    filename = f"LeadMachine_Report_{report['github_org']}_{report_id}.pdf"
    filepath = os.path.join(pdf_dir, filename)
    
    try:
        generate_report_pdf(report, filepath)
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )
    except Exception as e:
        return render_template('error.html', message=f'PDF Generation Failed: {str(e)}'), 500


@app.route('/report/<int:report_id>')
def view_report(report_id: int):
    """View a saved report."""
    report = get_report(report_id)
    if not report:
        return render_template('error.html', message='Report not found'), 404
    return render_template('report.html', report=report)


@app.route('/search')
def search():
    """Search for a company - redirects to scan page."""
    company = request.args.get('q', '').strip()
    # Sanitize input: remove dangerous characters, keep alphanumeric, dots, dashes, underscores
    company = "".join(c for c in company if c.isalnum() or c in ".-_").strip()
    
    if not company:
        return redirect(url_for('index'))
    return redirect(url_for('scan_page', company=company))


@app.route('/api/reports')
def api_reports():
    """API endpoint to get recent reports."""
    limit = request.args.get('limit', 20, type=int)
    reports = get_recent_reports(limit=limit)
    return jsonify(reports)


@app.route('/api/reports/search')
def api_search_reports():
    """API endpoint to search reports."""
    query = request.args.get('q', '')
    reports = search_reports(query)
    return jsonify(reports)


@app.route('/api/reports/paginated')
def api_reports_paginated():
    """API endpoint for paginated reports with filtering and sorting."""
    from database import get_paginated_reports

    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    search = request.args.get('search', '', type=str)
    date_from = request.args.get('date_from', None, type=str)
    date_to = request.args.get('date_to', None, type=str)
    min_signals = request.args.get('min_signals', None, type=int)
    max_signals = request.args.get('max_signals', None, type=int)
    sort_by = request.args.get('sort_by', 'created_at', type=str)
    sort_order = request.args.get('sort_order', 'desc', type=str)
    favorites_only = request.args.get('favorites_only', 'false', type=str).lower() == 'true'

    result = get_paginated_reports(
        page=page,
        limit=limit,
        search_query=search if search else None,
        date_from=date_from,
        date_to=date_to,
        min_signals=min_signals,
        max_signals=max_signals,
        sort_by=sort_by,
        sort_order=sort_order,
        favorites_only=favorites_only
    )

    return jsonify(result)


@app.route('/api/reports/<int:report_id>/favorite', methods=['POST'])
def api_toggle_favorite(report_id):
    """API endpoint to toggle report favorite status."""
    from database import toggle_report_favorite

    result = toggle_report_favorite(report_id)
    if result.get('success'):
        return jsonify(result)
    return jsonify(result), 404


@app.route('/api/reports/<int:report_id>', methods=['DELETE'])
def api_delete_report(report_id):
    """API endpoint to delete a report."""
    from database import delete_report_by_id

    result = delete_report_by_id(report_id)
    if result.get('success'):
        return jsonify(result)
    return jsonify(result), 404


@app.route('/api/reports/<int:report_id>/preview')
def api_report_preview(report_id):
    """API endpoint to get a report preview."""
    from database import get_report_preview

    preview = get_report_preview(report_id)
    if preview:
        return jsonify(preview)
    return jsonify({'error': 'Report not found'}), 404


@app.route('/api/agentmail/status')
def api_agentmail_status():
    """Check if AgentMail is configured."""
    return jsonify({'configured': is_agentmail_configured()})


@app.route('/api/send-to-bdr', methods=['POST'])
def api_send_to_bdr():
    """Send email draft to BDR via AgentMail."""
    data = request.get_json()
    
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400
    
    to_email = data.get('to_email')
    subject = data.get('subject')
    body = data.get('body')
    company_name = data.get('company_name')
    report_url = data.get('report_url')
    
    if not to_email:
        return jsonify({'success': False, 'error': 'BDR email address required'}), 400
    
    if not subject or not body:
        return jsonify({'success': False, 'error': 'Email subject and body required'}), 400
    
    result = send_email_draft(
        to_email=to_email,
        subject=subject,
        body=body,
        company_name=company_name,
        report_url=report_url
    )
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify(result), 500


@app.route('/api/report/<int:report_id>/deep-dive', methods=['POST'])
def api_deep_dive(report_id: int):
    """
    Generate a Deep Dive analysis for a report using Gemini AI.

    Only available for Tier 1-3 accounts (Thinking, Preparing, Launched).
    Not available for Tier 0 (Tracking) or Tier 4 (Invalid).

    Returns:
        JSON with timeline_events, code_insights, and outreach_narrative
    """
    from ai_summary import generate_deep_dive

    # Get the report
    report = get_report(report_id)
    if not report:
        return jsonify({'error': 'Report not found'}), 404

    scan_data = report.get('scan_data', {})
    ai_analysis = report.get('ai_analysis', {})

    # Determine the tier from goldilocks_status
    goldilocks_status = scan_data.get('goldilocks_status', ai_analysis.get('goldilocks_status', 'none'))

    # Map goldilocks_status to tier
    # preparing = Tier 2, thinking = Tier 1, launched = Tier 3, none = Tier 0
    tier_map = {
        'preparing': 2,
        'thinking': 1,
        'launched': 3,
        'none': 0,
        'unknown': 0
    }
    tier = tier_map.get(goldilocks_status, 0)

    # Check if Deep Dive is allowed for this tier (only Tier 1-3)
    if tier not in [1, 2, 3]:
        return jsonify({
            'error': 'Deep Dive is only available for Tier 1-3 accounts (Thinking, Preparing, or Launched)',
            'current_tier': tier,
            'goldilocks_status': goldilocks_status
        }), 403

    # Generate the Deep Dive analysis
    try:
        deep_dive_result = generate_deep_dive(scan_data, ai_analysis)
        return jsonify({
            'status': 'success',
            'deep_dive': deep_dive_result,
            'company_name': report.get('company_name', ''),
            'tier': tier,
            'goldilocks_status': goldilocks_status
        })
    except Exception as e:
        print(f"[API] Deep Dive error for report {report_id}: {str(e)}")
        return jsonify({'error': f'Failed to generate Deep Dive: {str(e)}'}), 500


@app.route('/history')
def history():
    """View scan history."""
    reports = get_recent_reports(limit=50)
    return render_template('history.html', reports=reports)


@app.route('/accounts')
def accounts():
    """View monitored accounts dashboard."""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)
    
    # Handle multi-select tier filter and search
    tiers = request.args.getlist('tier', type=int)
    if not tiers:
        tiers = None
        
    search_query = request.args.get('q', '').strip()
    if not search_query:
        search_query = None

    # Get paginated accounts
    result = get_all_accounts(page=page, limit=limit, tier_filter=tiers, search_query=search_query)

    # Get tier counts for ALL accounts (not just current page)
    tier_counts = get_tier_counts()

    return render_template(
        'accounts.html',
        accounts=result['accounts'],
        total_items=result['total_items'],
        total_pages=result['total_pages'],
        current_page=result['current_page'],
        limit=result['limit'],
        current_tier_filter=tiers,
        current_search=search_query or '',
        tier_config=TIER_CONFIG,
        tier_counts=tier_counts
    )


@app.route('/api/accounts')
def api_accounts():
    """API endpoint to get all monitored accounts with live scan status and pagination."""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)
    
    # Handle multi-select tier filter and search
    tiers = request.args.getlist('tier', type=int)
    if not tiers:
        tiers = None
        
    search_query = request.args.get('q', '').strip()
    if not search_query:
        search_query = None

    # Get paginated accounts
    result = get_all_accounts(page=page, limit=limit, tier_filter=tiers, search_query=search_query)

    # Scan status is now stored in the database (scan_status, scan_start_time columns)
    # The get_all_accounts() query already includes these fields
    # Just ensure default values are set for display
    for account in result['accounts']:
        if not account.get('scan_status'):
            account['scan_status'] = SCAN_STATUS_IDLE
        # Map scan_start_time to scan_started_at for API compatibility
        if account.get('scan_start_time'):
            account['scan_started_at'] = account['scan_start_time']

    return jsonify({
        'accounts': result['accounts'],
        'total_items': result['total_items'],
        'total_pages': result['total_pages'],
        'current_page': result['current_page'],
        'limit': result['limit']
    })


@app.route('/api/accounts/datatable', methods=['GET', 'POST'])
def api_accounts_datatable():
    """
    DataTables server-side processing endpoint.

    Handles parameters from DataTables JavaScript library for efficient
    server-side pagination, searching, and sorting.
    """
    # DataTables parameters
    draw = request.args.get('draw', 1, type=int)
    start = request.args.get('start', 0, type=int)
    length = request.args.get('length', 50, type=int)
    search_value = request.args.get('search[value]', '').strip()

    # Get tier filter if provided
    tiers = request.args.getlist('tier', type=int)
    tier_filter = tiers if tiers else None

    # Get ordering parameters
    order_column = request.args.get('order[0][column]', 0, type=int)
    order_dir = request.args.get('order[0][dir]', 'asc').lower()

    # Validate parameters
    length = max(1, min(length, 10000))  # Limit to max 10000 rows per request
    start = max(0, start)

    # Get data from database
    result = get_all_accounts_datatable(
        draw=draw,
        start=start,
        length=length,
        search_value=search_value,
        tier_filter=tier_filter,
        order_column=order_column,
        order_dir=order_dir
    )

    # Ensure scan status is set
    for account in result['data']:
        if not account.get('scan_status'):
            account['scan_status'] = SCAN_STATUS_IDLE
        if account.get('scan_start_time'):
            account['scan_started_at'] = account['scan_start_time']

    return jsonify(result)


@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def api_delete_account(account_id: int):
    """Delete a monitored account by ID."""
    deleted = delete_account(account_id)
    if not deleted:
        return jsonify({'error': 'Account not found'}), 404
    return jsonify({'status': 'success'})


@app.route('/api/accounts/<int:account_id>/notes', methods=['PUT'])
def api_update_account_notes(account_id: int):
    """Update the notes field for an account."""
    data = request.get_json() or {}
    notes = data.get('notes', '')

    updated = update_account_notes(account_id, notes)
    if not updated:
        return jsonify({'error': 'Account not found'}), 404
    return jsonify({'status': 'success', 'notes': notes})


@app.route('/grow')
def grow():
    """Render the Grow pipeline dashboard."""
    return render_template('grow.html')


@app.route('/api/discover')
def api_discover():
    """
    Search for GitHub organizations by keyword.

    Query parameters:
        q: Search keyword (required)
        limit: Maximum results (default 20)

    Returns JSON list of organization candidates not already in monitored_accounts.
    """
    keyword = request.args.get('q', '').strip()
    limit = request.args.get('limit', 20, type=int)

    if not keyword:
        return jsonify({'error': 'Missing query parameter: q'}), 400

    # Search for orgs matching the keyword
    results = search_github_orgs(keyword, limit=limit)

    # Get existing accounts to filter them out (use large limit to get all)
    existing_accounts_result = get_all_accounts(page=1, limit=10000)
    existing_logins = {acc['github_org'].lower() for acc in existing_accounts_result['accounts'] if acc.get('github_org')}

    # Filter out orgs already being monitored
    fresh_candidates = [
        org for org in results
        if org['login'].lower() not in existing_logins
    ]

    return jsonify(fresh_candidates)


@app.route('/api/ai-discover')
def api_ai_discover():
    """
    AI-powered Universal Discovery Engine for any industry.

    Uses AI to find companies that:
    - Have an internal engineering team (likely to use GitHub)
    - Are growing companies (Series B+ or >$10M Revenue)
    - Have a need for Internationalization (global customer base)

    Query parameters:
        q: Industry/sector keyword (e.g., "Fintech", "DTC Retail", "HealthTech")
        limit: Maximum results (default 15)

    Returns JSON list of AI-discovered companies with validated GitHub orgs.
    Each result includes:
    - name: Company name
    - revenue: Estimated revenue
    - industry: Specific niche
    - description: Tech/product summary
    - suggested_github_org: GitHub handle
    - github_validated: Boolean (True = confirmed GitHub org)
    - github_data: GitHub org details if validated
    """
    keyword = request.args.get('q', '').strip()
    limit = request.args.get('limit', 15, type=int)

    if not keyword:
        return jsonify({'error': 'Missing query parameter: q'}), 400

    # Use AI to discover companies in this sector
    companies = discover_companies_via_ai(keyword, limit=limit)

    # Get existing accounts to filter them out (use large limit to get all)
    existing_accounts_result = get_all_accounts(page=1, limit=10000)
    existing_logins = {acc['github_org'].lower() for acc in existing_accounts_result['accounts'] if acc.get('github_org')}

    # Filter out companies already being monitored
    fresh_candidates = []
    for company in companies:
        github_login = company.get('github_data', {}).get('login', '')
        if github_login and github_login.lower() not in existing_logins:
            fresh_candidates.append(company)

    return jsonify(fresh_candidates)


@app.route('/api/lead-stream')
def api_lead_stream():
    """
    Rapid lead discovery stream for Technology and SaaS companies with GitHub repos.

    Serves a continuous stream of leads filtered to the Goldilocks Zone ICP:
    - Technology and SaaS industries ONLY
    - Must have verified GitHub organization
    - Prioritizes companies not yet in monitoring pipeline

    Query parameters:
        offset: Starting position (default 0, for pagination)
        limit: Number of results per request (default 10, max 30)

    Returns JSON list of pre-filtered leads ready for rapid approval/tracking.
    """
    offset = request.args.get('offset', 0, type=int)
    limit = request.args.get('limit', 10, type=int)

    # Enforce max limit
    if limit > 30:
        limit = 30
    if offset < 0:
        offset = 0

    try:
        # Discover Technology companies
        tech_companies = discover_companies_via_ai("Technology Software Development", limit=15)

        # Discover SaaS companies
        saas_companies = discover_companies_via_ai("SaaS B2B Software", limit=15)

        # Combine and deduplicate by GitHub login
        all_companies = tech_companies + saas_companies
        seen = set()
        unique_companies = []

        for company in all_companies:
            github_login = company.get('github_data', {}).get('login', '')
            if github_login and github_login.lower() not in seen:
                seen.add(github_login.lower())
                unique_companies.append(company)

        # Filter out companies already being monitored
        existing_accounts_result = get_all_accounts(page=1, limit=10000)
        existing_logins = {acc['github_org'].lower() for acc in existing_accounts_result['accounts'] if acc.get('github_org')}

        # Only return companies with validated GitHub and not already tracked
        fresh_leads = []
        for company in unique_companies:
            github_login = company.get('github_data', {}).get('login', '')
            if company.get('github_validated') and github_login and github_login.lower() not in existing_logins:
                fresh_leads.append(company)

        # Apply pagination
        paginated_leads = fresh_leads[offset:offset + limit]

        return jsonify({
            'leads': paginated_leads,
            'total': len(fresh_leads),
            'offset': offset,
            'limit': limit,
            'has_more': offset + limit < len(fresh_leads)
        })

    except Exception as e:
        return jsonify({'error': f'Failed to generate lead stream: {str(e)}'}), 500


@app.route('/api/import', methods=['POST'])
@app.route('/api/accounts/import', methods=['POST'])
def api_import():
    """
    Bulk import companies by resolving them to GitHub organizations.

    This endpoint is resilient to server restarts:
    1. Saves the import data to a persistent database batch
    2. Submits the batch to the thread pool for background processing
    3. Returns immediately with the batch_id

    The batch worker processes each company, checking for duplicates,
    resolving GitHub orgs, and spawning scans. Progress is persisted
    so interrupted batches can be resumed on restart.

    Expects JSON payload: {"companies": ["Shopify", "Stripe", ...]}
    Or with annual_revenue: {"companies": [{"name": "Shopify", "annual_revenue": "$4.6B"}, ...]}

    Returns:
        JSON with: {
            "batch_id": <int>,
            "total_count": <int>,
            "status": "queued",
            "message": "Import batch created and queued for processing"
        }
    """
    data = request.get_json() or {}
    companies = data.get('companies', [])

    if not isinstance(companies, list) or not companies:
        return jsonify({'error': 'Invalid payload: expected {"companies": [...]}'}), 400

    # Create persistent batch in database
    batch_id = create_import_batch(companies)
    print(f"[IMPORT] Created batch {batch_id} with {len(companies)} companies")

    # Submit batch to thread pool for background processing
    executor = get_executor()
    executor.submit(process_import_batch_worker, batch_id)
    print(f"[EXECUTOR] Submitted batch {batch_id} for processing")

    return jsonify({
        'batch_id': batch_id,
        'total_count': len(companies),
        'status': 'queued',
        'message': 'Import batch created and queued for processing'
    })


@app.route('/api/import-batch/<int:batch_id>', methods=['GET'])
def api_import_batch_status(batch_id):
    """
    Get the status and progress of an import batch.

    Returns:
        JSON with: {
            "batch_id": <int>,
            "status": "pending" | "processing" | "completed" | "failed",
            "total_count": <int>,
            "processed_count": <int>,
            "progress_percent": <int>
        }
    """
    batch = get_import_batch(batch_id)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404

    total = batch.get('total_count', 0)
    processed = batch.get('processed_count', 0)
    progress_percent = int((processed / total * 100) if total > 0 else 0)

    return jsonify({
        'batch_id': batch_id,
        'status': batch.get('status', 'unknown'),
        'total_count': total,
        'processed_count': processed,
        'progress_percent': progress_percent
    })


@app.route('/api/track', methods=['POST'])
def api_track():
    """
    Add a specific discovered organization to Tier 0 monitoring.

    Expects JSON payload: {"org_login": "shopify", "company_name": "Shopify"}

    Returns:
        JSON with account creation result.

    After adding the organization to the database, spawns a background scan
    thread so the organization data is analyzed automatically.
    """
    data = request.get_json() or {}
    org_login = data.get('org_login', '').strip()
    company_name = data.get('company_name', '').strip()

    if not org_login or not company_name:
        return jsonify({'error': 'Missing required fields: org_login, company_name'}), 400

    try:
        existing = get_account_by_company_case_insensitive(company_name)
        if existing:
            return jsonify({
                'error': 'Account already indexed',
                'company_name': existing.get('company_name'),
                'github_org': existing.get('github_org')
            }), 409

        result = add_account_to_tier_0(company_name, org_login)

        # Spawn background scan immediately after adding to DB
        spawn_background_scan(company_name)

        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'Failed to track organization: {str(e)}'}), 500


@app.route('/api/update-org', methods=['POST'])
def api_update_org():
    """
    Update the GitHub organization for a monitored account.

    Expects JSON payload: {"company_name": "Company", "github_org": "org-name"}

    Returns:
        JSON with update result.
    """
    from database import get_db_connection

    data = request.get_json() or {}
    company_name = data.get('company_name', '').strip()
    github_org = data.get('github_org', '').strip()

    if not company_name or not github_org:
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Update the github_org for this account
        cursor.execute('''
            UPDATE monitored_accounts
            SET github_org = ?, current_tier = 0, evidence_summary = 'GitHub org updated manually'
            WHERE company_name = ?
        ''', (github_org, company_name))

        if cursor.rowcount == 0:
            return jsonify({'status': 'error', 'message': 'Account not found'}), 404

        conn.commit()

        return jsonify({
            'status': 'success',
            'company_name': company_name,
            'github_org': github_org
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/rescan/<company_name>', methods=['POST'])
def api_rescan(company_name: str):
    """
    Queue a rescan for a company.

    Instead of running synchronously (which blocks and can cause database locks),
    this submits the scan job to the thread pool and returns immediately.

    Rate limiting: If the account was scanned less than 5 minutes ago,
    returns a 'recent' status without triggering a new scan.

    Returns:
        JSON with queued status. The UI should refresh to see updated results.
    """
    # Check if already scanning
    current_status = get_scan_status(company_name)
    if current_status and current_status.get('scan_status') in (SCAN_STATUS_QUEUED, SCAN_STATUS_PROCESSING):
        return jsonify({
            'status': current_status.get('scan_status'),
            'company': company_name,
            'message': f'Scan already {current_status.get("scan_status")}'
        })

    # Rate limiting: Check if scanned within the last 5 minutes
    account = get_account_by_company_case_insensitive(company_name)
    if account and account.get('last_scanned_at'):
        try:
            last_scanned = datetime.fromisoformat(account['last_scanned_at'].replace('Z', '+00:00'))
            # Handle naive datetime (no timezone info)
            if last_scanned.tzinfo is None:
                time_since_scan = datetime.now() - last_scanned
            else:
                time_since_scan = datetime.now(last_scanned.tzinfo) - last_scanned

            if time_since_scan.total_seconds() < 300:  # 5 minutes = 300 seconds
                return jsonify({
                    'status': 'recent',
                    'company': company_name,
                    'message': 'Scan already recent',
                    'last_scanned_at': account['last_scanned_at']
                })
        except (ValueError, TypeError):
            # If we can't parse the timestamp, proceed with the scan
            pass

    # Submit to thread pool (this also sets status to 'queued' in DB)
    spawn_background_scan(company_name)
    print(f"[EXECUTOR] Rescan submitted for {company_name}")

    # Get current account info for response
    account = get_account_by_company(company_name)
    active_jobs = get_queued_and_processing_accounts()
    queue_size = len(active_jobs.get('queued', [])) + len(active_jobs.get('processing', []))

    if account:
        tier = account.get('current_tier') or 0
        tier_config = TIER_CONFIG.get(tier, TIER_CONFIG[0])
        return jsonify({
            'status': 'queued',
            'company': company_name,
            'message': 'Scan queued successfully',
            'active_jobs': queue_size,
            'current_tier': tier,
            'tier_name': tier_config['name'],
            'tier_emoji': tier_config['emoji']
        })

    return jsonify({
        'status': 'queued',
        'company': company_name,
        'message': 'Scan queued successfully',
        'active_jobs': queue_size
    })


@app.route('/api/scan-pending', methods=['POST'])
def api_scan_pending():
    """
    Queue scans for all accounts that have never been scanned.

    Logic: Query monitored_accounts for all records where last_scanned_at
    is NULL or equal to created_at (meaning never scanned).

    Action: Submit them all to the thread pool.

    Returns:
        JSON with count of scans queued.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Find accounts that have never been scanned (last_scanned_at is NULL)
        # and are not currently being scanned
        cursor.execute('''
            SELECT company_name FROM monitored_accounts
            WHERE last_scanned_at IS NULL
              AND (scan_status IS NULL OR scan_status = ?)
        ''', (SCAN_STATUS_IDLE,))

        pending_accounts = [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()

    # Submit scans for each pending account
    queued_count = 0
    for company_name in pending_accounts:
        spawn_background_scan(company_name)
        queued_count += 1

    print(f"[EXECUTOR] Submitted {queued_count} pending accounts for scanning")

    # Get current active job count
    active_jobs = get_queued_and_processing_accounts()
    total_active = len(active_jobs.get('queued', [])) + len(active_jobs.get('processing', []))

    return jsonify({
        'status': 'success',
        'queued': queued_count,
        'accounts': pending_accounts,
        'active_jobs': total_active,
        'max_workers': MAX_SCAN_WORKERS
    })


@app.route('/api/refresh-pipeline', methods=['POST'])
def api_refresh_pipeline():
    """
    Queue all accounts eligible for weekly refresh.

    Selects accounts where:
    - current_tier IN (0, 1, 2) - Tracking, Thinking, or Preparing
    - last_scanned_at < 7 days ago OR IS NULL

    Excludes Tier 3 (Launched) and Tier 4 (Invalid) accounts.
    Also excludes accounts currently being scanned.

    Returns:
        JSON with count of accounts queued for refresh.
    """
    # Get all refreshable accounts
    refreshable = get_refreshable_accounts()

    # Submit each account for scanning (skip if already scanning)
    queued_count = 0
    queued_companies = []
    for account in refreshable:
        company_name = account.get('company_name')
        scan_status = account.get('scan_status')

        # Skip if already queued or processing
        if scan_status in (SCAN_STATUS_QUEUED, SCAN_STATUS_PROCESSING):
            continue

        if company_name:
            spawn_background_scan(company_name)
            queued_count += 1
            queued_companies.append(company_name)

    print(f"[EXECUTOR] Submitted {queued_count} accounts for refresh")

    # Get current active job count
    active_jobs = get_queued_and_processing_accounts()
    total_active = len(active_jobs.get('queued', [])) + len(active_jobs.get('processing', []))

    return jsonify({
        'status': 'success',
        'queued': queued_count,
        'accounts': queued_companies,
        'active_jobs': total_active,
        'max_workers': MAX_SCAN_WORKERS
    })


@app.route('/api/queue-status')
def api_queue_status():
    """
    Get the current status of the scan queue.

    Returns:
        JSON with active job counts and executor status.
    """
    active_jobs = get_queued_and_processing_accounts()
    queued = active_jobs.get('queued', [])
    processing = active_jobs.get('processing', [])

    return jsonify({
        'queued_count': len(queued),
        'processing_count': len(processing),
        'queued_companies': queued,
        'processing_companies': [p['company_name'] for p in processing],
        'max_workers': MAX_SCAN_WORKERS,
        'executor_active': _executor is not None
    })


@app.route('/api/status-counts')
def api_status_counts():
    """
    Get counts of accounts by scan status.

    Returns:
        JSON with counts for idle, queued, processing, and stuck accounts.
    """
    from database import get_status_counts
    counts = get_status_counts(stuck_timeout_minutes=5)

    return jsonify({
        'idle': counts['idle'],
        'queued': counts['queued'],
        'processing': counts['processing'],
        'stuck': counts['stuck'],
        'total': sum(counts.values())
    })


@app.route('/api/worker-restart', methods=['POST'])
def api_worker_restart():
    """
    Clear stale scan statuses and ensure the executor is ready.

    Use this if scans are stuck in processing/queued state.
    Clears any jobs stuck for more than 30 minutes.

    Returns:
        JSON with status after cleanup.
    """
    # Clear stale scan statuses (jobs stuck for > 30 minutes)
    stale_cleared = clear_stale_scan_statuses(timeout_minutes=30)

    # Ensure executor is created
    get_executor()

    # Get current status
    active_jobs = get_queued_and_processing_accounts()
    total_active = len(active_jobs.get('queued', [])) + len(active_jobs.get('processing', []))

    return jsonify({
        'status': 'success',
        'stale_jobs_cleared': stale_cleared,
        'active_jobs': total_active,
        'max_workers': MAX_SCAN_WORKERS,
        'message': f'Cleared {stale_cleared} stale jobs, executor ready with {MAX_SCAN_WORKERS} workers'
    })


@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors."""
    return render_template('error.html', message='Page not found'), 404


@app.errorhandler(500)
def internal_error(e):
    """Handle 500 errors."""
    # Extract the original exception message if available
    error_msg = str(e.original_exception) if hasattr(e, 'original_exception') else str(e)
    return render_template('error.html', message=f'Internal server error: {error_msg}'), 500


# =============================================================================
# App Startup - Initialize executor and reset stale statuses
# =============================================================================

# Use a flag to track if we've initialized on first request
_app_initialized = False

@app.before_request
def initialize_on_first_request():
    """
    Initialize the thread pool executor and reset stale scan statuses on first request.

    This provides resilience against app restarts:
    - Recovers accounts stuck in 'queued' status and re-queues them
    - Resets any scan statuses that were stuck in 'processing'
    - Resumes interrupted import batches
    - Auto-scans any accounts that were imported but never scanned
    """
    global _app_initialized
    if not _app_initialized:
        print("[APP] First request - initializing executor and cleaning up...")

        # Initialize the executor FIRST
        get_executor()

        # IMPORTANT: Recover stuck queued accounts BEFORE reset_all_scan_statuses
        _recover_stuck_queued_accounts()

        # Reset any remaining stale scan statuses (processing accounts)
        reset_count = reset_all_scan_statuses()
        if reset_count > 0:
            print(f"[APP] Reset {reset_count} stale processing statuses from previous run")

        # Clear any misclassified errors (tier evidence stored as errors)
        cleared_errors = clear_misclassified_errors()
        if cleared_errors > 0:
            print(f"[APP] Cleared {cleared_errors} misclassified error messages")

        # Resume any interrupted import batches
        _resume_interrupted_import_batches()

        # Auto-scan any accounts that were imported but never scanned
        _auto_scan_pending_accounts()

        _app_initialized = True


def _recover_stuck_queued_accounts():
    """
    Recover accounts that were stuck in 'queued' status from a previous run.

    This handles cases where accounts were queued but the app was restarted
    before they could be processed. The accounts are reset to idle and then
    re-queued for scanning.
    """
    try:
        # Get all accounts stuck in queued status and reset them
        stuck_accounts = reset_all_queued_to_idle()

        if stuck_accounts:
            print(f"[APP] Found {len(stuck_accounts)} accounts stuck in queue from previous run")

            # Re-queue them using batch method
            batch_set_scan_status_queued(stuck_accounts)

            # Submit all to executor for background scanning
            executor = get_executor()
            for company_name in stuck_accounts:
                executor.submit(perform_background_scan, company_name)

            print(f"[APP] Re-queued {len(stuck_accounts)} stuck accounts for scanning")
        else:
            print("[APP] No stuck queued accounts to recover")

    except Exception as e:
        print(f"[APP] Error recovering stuck queued accounts: {str(e)}")


def _resume_interrupted_import_batches():
    """
    Resume any import batches that were interrupted by a server restart.

    This checks for batches with status 'pending' or 'processing' and
    submits them to the executor to continue processing from where they left off.
    """
    try:
        pending_batches = get_pending_import_batches()

        if pending_batches:
            print(f"[APP] Found {len(pending_batches)} interrupted import batches to resume")

            executor = get_executor()
            for batch in pending_batches:
                batch_id = batch.get('id')
                processed = batch.get('processed_count', 0)
                total = batch.get('total_count', 0)
                status = batch.get('status', 'unknown')

                print(f"[APP] Resuming batch {batch_id}: {processed}/{total} processed (was {status})")
                executor.submit(process_import_batch_worker, batch_id)

            print(f"[APP] Submitted {len(pending_batches)} batches for resumption")
        else:
            print("[APP] No interrupted import batches to resume")

    except Exception as e:
        print(f"[APP] Error resuming interrupted import batches: {str(e)}")


def _auto_scan_pending_accounts():
    """
    Automatically queue scans for accounts that were imported but never scanned.

    This is called on app startup to ensure imported accounts get scanned
    even if the app was restarted before their initial scan completed.

    Uses batch queueing for reliability - sets all statuses to 'queued' FIRST,
    then submits to executor. This ensures status is visible immediately even
    if executor submission is slow.
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Find accounts that have never been scanned (regardless of current status)
        cursor.execute('''
            SELECT company_name FROM monitored_accounts
            WHERE last_scanned_at IS NULL
        ''')

        pending_accounts = [row[0] for row in cursor.fetchall()]
        conn.close()
        conn = None  # Mark as closed

        if pending_accounts:
            print(f"[APP] Found {len(pending_accounts)} accounts pending initial scan")

            # Step 1: Batch set ALL pending accounts to 'queued' status immediately
            # This makes the queue visible right away in the UI
            batch_set_scan_status_queued(pending_accounts)
            print(f"[APP] Batch queued {len(pending_accounts)} pending accounts")

            # Step 2: Submit all to executor for background scanning
            executor = get_executor()
            for company_name in pending_accounts:
                executor.submit(perform_background_scan, company_name)
            print(f"[APP] Auto-submitted {len(pending_accounts)} pending accounts for scan")
        else:
            print("[APP] No pending accounts to scan")

    except Exception as e:
        print(f"[APP] Error auto-scanning pending accounts: {str(e)}")
    finally:
        if conn is not None:
            conn.close()


# =============================================================================
# SETTINGS & STATS API ROUTES
# =============================================================================

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    """
    Get or update system settings.

    GET: Returns current settings (webhook_url, webhook_enabled, etc.)
    POST: Updates settings from JSON payload {"webhook_url": "...", "webhook_enabled": true}
    """
    if request.method == 'GET':
        webhook_enabled = get_setting('webhook_enabled')
        return jsonify({
            'webhook_url': get_setting('webhook_url') or '',
            'webhook_enabled': webhook_enabled == 'true'
        })

    # POST - update settings
    data = request.get_json() or {}

    # Update webhook URL if provided
    if 'webhook_url' in data:
        webhook_url = data['webhook_url'].strip()
        # Validate URL format (must be empty or start with http:// or https://)
        if webhook_url and not (webhook_url.startswith('http://') or webhook_url.startswith('https://')):
            return jsonify({
                'status': 'error',
                'message': 'Webhook URL must start with http:// or https://'
            }), 400
        set_setting('webhook_url', webhook_url)

    # Update webhook enabled if provided
    if 'webhook_enabled' in data:
        webhook_enabled = data['webhook_enabled']
        set_setting('webhook_enabled', 'true' if webhook_enabled else 'false')

    webhook_enabled = get_setting('webhook_enabled')
    return jsonify({
        'status': 'success',
        'webhook_url': get_setting('webhook_url') or '',
        'webhook_enabled': webhook_enabled == 'true'
    })


@app.route('/api/settings/zapier', methods=['POST'])
def api_settings_zapier():
    """
    Save the Zapier webhook URL for Smart Enrollment.

    POST payload: {"zapier_url": "https://hooks.zapier.com/..."}

    Returns:
        JSON with status and saved URL.
    """
    data = request.get_json() or {}
    zapier_url = data.get('zapier_url', '').strip()

    if not zapier_url:
        return jsonify({
            'status': 'error',
            'message': 'Missing zapier_url field'
        }), 400

    # Validate URL format
    if not (zapier_url.startswith('http://') or zapier_url.startswith('https://')):
        return jsonify({
            'status': 'error',
            'message': 'Zapier URL must start with http:// or https://'
        }), 400

    set_setting('zapier_webhook_url', zapier_url)

    return jsonify({
        'status': 'success',
        'zapier_url': zapier_url
    })


@app.route('/api/integrations/zapier/trigger', methods=['POST'])
def api_zapier_trigger():
    """
    Trigger a Zapier webhook to enroll a lead into Apollo/outreach sequence.

    This endpoint:
    1. Loads report data by report_id or account data by company_name
    2. Finds the "best" repository (highest stars) from scan_data
    3. Fetches top contributors for that repository
    4. Sends enriched payload to Zapier webhook

    POST payload: {"report_id": 123} or {"company_name": "Shopify"}

    Returns:
        JSON with trigger status and payload summary.
    """
    # Check for Zapier webhook URL
    zapier_url = get_setting('zapier_webhook_url')
    if not zapier_url:
        return jsonify({
            'status': 'error',
            'code': 'MISSING_URL',
            'message': 'Zapier webhook URL not configured. Please set it first.'
        }), 400

    data = request.get_json() or {}
    report_id = data.get('report_id')
    company_name = data.get('company_name')

    # Load report or account data
    report = None
    account = None

    if report_id:
        report = get_report(report_id)
        if not report:
            return jsonify({
                'status': 'error',
                'message': f'Report {report_id} not found'
            }), 404
        company_name = report.get('company_name')
    elif company_name:
        account = get_account_by_company_case_insensitive(company_name)
        if not account:
            return jsonify({
                'status': 'error',
                'message': f'Account "{company_name}" not found'
            }), 404
        # Try to get the most recent report for this company
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id FROM reports
                WHERE company_name = ?
                ORDER BY created_at DESC
                LIMIT 1
            ''', (company_name,))
            row = cursor.fetchone()
            if row:
                report = get_report(row['id'])
        finally:
            conn.close()
    else:
        return jsonify({
            'status': 'error',
            'message': 'Either report_id or company_name is required'
        }), 400

    # Extract data from report or account
    scan_data = report.get('scan_data', {}) if report else {}
    ai_analysis = report.get('ai_analysis', {}) if report else {}
    github_org = report.get('github_org') if report else (account.get('github_org') if account else '')
    tier = account.get('current_tier') or 0 if account else 0
    tier_name = TIER_CONFIG.get(tier, TIER_CONFIG[0])['name']
    revenue = account.get('annual_revenue') if account else None

    # Get company website
    website = ''
    if account and account.get('website'):
        website = account.get('website', '')

    # Extract domain from website for Apollo API searches
    domain = ''
    if website:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(website if website.startswith('http') else f'https://{website}')
            domain = parsed.netloc or parsed.path.split('/')[0]
            # Remove www. prefix if present
            if domain.startswith('www.'):
                domain = domain[4:]
        except Exception:
            # Fallback: try to extract domain directly
            domain = website.replace('https://', '').replace('http://', '').replace('www.', '').split('/')[0]

    # Find best repo (highest stars) from scan_data
    best_repo = None
    repos_scanned = scan_data.get('repos_scanned', [])
    if repos_scanned:
        # Sort by stars descending and get the first one
        sorted_repos = sorted(repos_scanned, key=lambda r: r.get('stars', 0), reverse=True)
        if sorted_repos:
            best_repo = sorted_repos[0]

    # Fetch top contributors for the best repo
    contributors = []
    if best_repo and github_org:
        repo_name = best_repo.get('name', '')
        if repo_name:
            contributors = get_top_contributors(github_org, repo_name, limit=5)

    # Build report URL
    try:
        from flask import request as flask_request, has_request_context
        if has_request_context():
            base_url = flask_request.host_url.rstrip('/')
        else:
            base_url = os.environ.get('BASE_URL', 'http://localhost:5000')
    except Exception:
        base_url = os.environ.get('BASE_URL', 'http://localhost:5000')

    report_url = f"{base_url}/report/{report_id}" if report_id else ''

    # Construct payload for Zapier
    # Format contributors to ensure consistent structure for Zapier workflow
    formatted_contributors = []
    for c in contributors:
        # Parse name into first/last if possible
        full_name = c.get('name', '') or c.get('login', '')
        name_parts = full_name.split(' ', 1) if full_name else ['', '']
        first_name = name_parts[0] if name_parts else ''
        last_name = name_parts[1] if len(name_parts) > 1 else ''

        formatted_contributors.append({
            'login': c.get('login', ''),
            'name': full_name,
            'first_name': first_name,
            'last_name': last_name,
            'email': c.get('email', ''),
            'blog': c.get('blog', ''),
            'github_url': c.get('github_url', ''),
            'organization_name': company_name,  # Include company name for Zapier mapping
            # Include a flag to help Zapier's Path A filter identify real persons
            'has_real_name': bool(c.get('name') and c.get('name') != c.get('login'))
        })

    payload = {
        'event': 'enroll_lead',
        'company': company_name,
        'website': website,
        'domain': domain,  # Required for Apollo API search in Path B
        'github_org': github_org,
        'contributors': formatted_contributors,
        'fallback_role': 'Engineering Manager',
        'report_url': report_url,
        'timestamp': datetime.now().isoformat()
    }

    # Send to Zapier
    try:
        response = requests.post(
            zapier_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=15
        )

        if response.status_code >= 200 and response.status_code < 300:
            print(f"[ZAPIER] Successfully triggered enrollment for {company_name}")
            return jsonify({
                'status': 'success',
                'message': f'Successfully enrolled {company_name}',
                'contributors_count': len(contributors),
                'zapier_response_status': response.status_code
            })
        else:
            print(f"[ZAPIER] Failed to trigger for {company_name}: HTTP {response.status_code}")
            return jsonify({
                'status': 'error',
                'message': f'Zapier webhook returned status {response.status_code}',
                'zapier_response': response.text[:500] if response.text else ''
            }), 500

    except requests.exceptions.Timeout:
        return jsonify({
            'status': 'error',
            'message': 'Zapier webhook timed out'
        }), 504
    except requests.exceptions.RequestException as e:
        return jsonify({
            'status': 'error',
            'message': f'Failed to call Zapier webhook: {str(e)}'
        }), 500


@app.route('/api/stats')
def api_stats():
    """
    Get system usage statistics for the last 30 days.

    Returns:
        JSON with daily stats for graphing:
        {
            "stats": [
                {"date": "2026-01-01", "scans_run": 5, "api_calls_estimated": 200, "webhooks_fired": 2},
                ...
            ]
        }
    """
    days = request.args.get('days', 30, type=int)
    stats = get_stats_last_n_days(days)

    return jsonify({
        'stats': stats,
        'days': days
    })


@app.route('/api/hourly-api-stats')
def api_hourly_stats():
    """
    Get API call statistics for the current hour.

    Returns:
        JSON with hourly API usage:
        {
            "api_calls_this_hour": 1234,
            "hourly_limit": 10000,
            "token_count": 2,
            "rate_per_token": 5000
        }

    The hourly_limit is calculated as: token_count * 5000 (GitHub's per-token limit)
    This allows the frontend to display: "1,234 / 10,000 per hour"
    """
    from utils import get_token_pool

    # Get current hour's API calls
    api_calls = get_current_hour_api_calls()

    # Get token count from the pool
    token_pool = get_token_pool()
    token_count = token_pool.get_token_count()

    # Calculate hourly limit (5,000 per token)
    rate_per_token = 5000
    hourly_limit = token_count * rate_per_token

    # Clean up old hourly stats periodically (keep 24 hours)
    cleanup_old_hourly_stats(hours_to_keep=24)

    return jsonify({
        'api_calls_this_hour': api_calls,
        'hourly_limit': hourly_limit,
        'token_count': token_count,
        'rate_per_token': rate_per_token
    })


@app.route('/api/token-pool')
def api_token_pool():
    """
    Get the current status of the GitHub token pool.

    The token pool allows crowdsourced rate limit expansion:
    - Each BDR contributes their Personal Access Token
    - System rotates through tokens, selecting the one with highest remaining limit
    - 10 BDRs = 50,000 requests/hour = 250+ company scans without pausing

    Returns:
        JSON with pool status:
        {
            "pool_size": 5,
            "tokens_available": 4,
            "tokens_rate_limited": 1,
            "total_remaining": 20000,
            "total_limit": 25000,
            "effective_hourly_capacity": 25000,
            "estimated_companies_per_hour": 125,
            "token_details": [
                {
                    "token": "ghp_...wxyz",
                    "remaining": 4500,
                    "limit": 5000,
                    "usage_percent": 10.0,
                    "request_count": 50,
                    "is_rate_limited": false,
                    "resets_in_seconds": 0
                },
                ...
            ]
        }
    """
    from utils import get_token_pool_status
    from config import Config

    pool_status = get_token_pool_status()

    # Add capacity estimates
    capacity = Config.get_token_pool_capacity()
    pool_status['estimated_companies_per_hour'] = capacity['estimated_companies_per_hour']

    return jsonify(pool_status)


@app.route('/api/cache')
def api_cache_stats():
    """
    Get cache statistics and status.

    The caching layer reduces GitHub API calls by storing responses:
    - Organization metadata: 24 hours TTL
    - Repository lists: 7 days TTL
    - File contents: 7 days TTL
    - Branch/PR lists: 12 hours TTL
    - Issue lists: 6 hours TTL

    Returns:
        JSON with cache status and statistics:
        {
            "backend": "redis" | "diskcache" | "disabled" | "none",
            "enabled": true,
            "hits": 150,
            "misses": 50,
            "hit_rate_percent": 75.0,
            "bytes_saved_approx": 1048576,
            "ttl_config": {
                "org_metadata": 86400,
                "repo_list": 604800,
                ...
            }
        }
    """
    from cache import get_cache_stats
    return jsonify(get_cache_stats())


@app.route('/api/cache/clear', methods=['POST'])
def api_cache_clear():
    """
    Clear all cached entries.

    Use this when you need fresh data for all organizations.
    Typically not needed as cache auto-expires based on TTL.

    Returns:
        JSON with number of entries cleared.
    """
    from cache import clear_cache
    deleted = clear_cache()
    return jsonify({
        'success': True,
        'entries_cleared': deleted,
        'message': f'Cleared {deleted} cache entries'
    })


@app.route('/api/cache/invalidate/<org_login>', methods=['POST'])
def api_cache_invalidate_org(org_login):
    """
    Invalidate cache for a specific organization.

    Use this when:
    - You receive a webhook that a repo was pushed
    - You know an org's data has changed
    - You want fresh data for a specific company scan

    Args:
        org_login: GitHub organization login name

    Returns:
        JSON with number of entries invalidated.
    """
    from cache import invalidate_org_cache
    deleted = invalidate_org_cache(org_login)
    return jsonify({
        'success': True,
        'org': org_login,
        'entries_invalidated': deleted,
        'message': f'Invalidated {deleted} cache entries for {org_login}'
    })


@app.route('/api/webhook-logs')
def api_webhook_logs():
    """
    Get recent webhook delivery logs.

    Returns:
        JSON with recent webhook logs for display.
    """
    limit = request.args.get('limit', 50, type=int)
    logs = get_recent_webhook_logs(limit)

    return jsonify({
        'logs': logs
    })


# =============================================================================
# RULES PANEL API - Scanning Rules Visibility for Team
# =============================================================================

def _get_rules_last_updated():
    """Get the timestamp when rules were last updated."""
    from database import get_setting
    timestamp = get_setting('rules_last_updated')
    if timestamp:
        return timestamp
    # Default to now if never set
    now = datetime.now().isoformat()
    set_setting('rules_last_updated', now)
    return now


def _update_rules_timestamp():
    """Update the rules last-updated timestamp to now."""
    now = datetime.now().isoformat()
    set_setting('rules_last_updated', now)
    return now


@app.route('/api/rules')
def api_rules():
    """
    Get all scanning rules and heuristics for the Rules Panel.

    This endpoint extracts rules from the Config class and formats them
    for display to the engineering/sales team, providing visibility into
    what the scanner looks for.

    Returns:
        JSON with categorized rules and last-updated timestamp.
    """
    rules = {
        'last_updated': _get_rules_last_updated(),
        'categories': [
            {
                'name': 'Signal 1: RFC & Discussion Keywords',
                'description': 'Keywords that indicate internationalization planning in GitHub Issues/Discussions (Thinking Phase)',
                'phase': 'THINKING',
                'items': Config.RFC_KEYWORDS,
                'lookback_days': Config.RFC_LOOKBACK_DAYS
            },
            {
                'name': 'Signal 2: Smoking Gun Libraries',
                'description': 'i18n libraries that indicate infrastructure setup without launched translations (Preparing Phase)',
                'phase': 'PREPARING',
                'items': Config.SMOKING_GUN_LIBS
            },
            {
                'name': 'Signal 2b: Smoking Gun Fork Repos',
                'description': 'When companies fork these repos, they are customizing i18n infrastructure (HIGH intent)',
                'phase': 'PREPARING',
                'items': Config.SMOKING_GUN_FORK_REPOS
            },
            {
                'name': 'Signal 2c: Linter Libraries',
                'description': 'Code linting/cleaning libraries for scrubbing hardcoded strings',
                'phase': 'PREPARING',
                'items': Config.LINTER_LIBRARIES
            },
            {
                'name': 'Signal 2d: CMS i18n Libraries',
                'description': 'CMS-specific internationalization plugins and libraries',
                'phase': 'PREPARING',
                'items': Config.CMS_I18N_LIBS
            },
            {
                'name': 'Signal 3: Ghost Branch Patterns',
                'description': 'Branch/PR naming patterns that indicate WIP localization work (Active Phase)',
                'phase': 'ACTIVE',
                'items': Config.GHOST_BRANCH_PATTERNS
            },
            {
                'name': 'Signal 4: Documentation Intent Keywords',
                'description': 'Keywords in docs (README, CHANGELOG) that indicate planned i18n work',
                'phase': 'THINKING',
                'items': Config.DOCUMENTATION_INTENT_KEYWORDS
            },
            {
                'name': 'Documentation Context Keywords',
                'description': 'Context words that must appear near intent keywords (indicates future/WIP)',
                'phase': 'THINKING',
                'items': Config.DOCUMENTATION_CONTEXT_KEYWORDS
            },
            {
                'name': 'Exclusion Folders (Disqualifiers)',
                'description': 'If these folders exist with translations, company has ALREADY LAUNCHED (Too Late)',
                'phase': 'LAUNCHED',
                'items': Config.EXCLUSION_FOLDERS
            },
            {
                'name': 'Source Locale Patterns (Goldilocks Exception)',
                'description': 'If locale folder contains ONLY these source files, still in Goldilocks Zone',
                'phase': 'PREPARING',
                'items': Config.SOURCE_LOCALE_PATTERNS
            },
            {
                'name': 'Dependency Files Scanned',
                'description': 'Package manager files checked for i18n library dependencies',
                'phase': 'PREPARING',
                'items': Config.DEPENDENCY_INJECTION_FILES
            },
            {
                'name': 'Framework Config Files',
                'description': 'Framework configuration files checked for i18n routing setup',
                'phase': 'PREPARING',
                'items': Config.FRAMEWORK_CONFIG_FILES
            },
            {
                'name': 'Documentation Files Scanned',
                'description': 'Documentation files checked for i18n intent signals',
                'phase': 'THINKING',
                'items': Config.DOCUMENTATION_FILES
            },
            {
                'name': 'i18n Script Keywords',
                'description': 'Keywords in package.json scripts that indicate i18n preparation',
                'phase': 'PREPARING',
                'items': Config.I18N_SCRIPT_KEYWORDS
            },
            {
                'name': 'Build Script i18n Keywords',
                'description': 'Keywords in build scripts indicating locale/translation work',
                'phase': 'PREPARING',
                'items': Config.BUILD_SCRIPT_I18N_KEYWORDS
            },
            {
                'name': 'Open Protocol Disqualifiers',
                'description': 'Patterns that identify non-commercial open source/decentralized projects',
                'phase': 'DISQUALIFIED',
                'items': Config.OPEN_PROTOCOL_DISQUALIFIERS
            },
            {
                'name': 'High-Value Repo Patterns',
                'description': 'Repo name patterns that indicate core product (prioritized for scanning)',
                'phase': 'SCORING',
                'items': Config.HIGH_VALUE_PATTERNS
            },
            {
                'name': 'Low-Value Repo Patterns',
                'description': 'Repo name patterns that indicate non-core repos (deprioritized)',
                'phase': 'SCORING',
                'items': Config.LOW_VALUE_PATTERNS
            },
            {
                'name': 'High-Value Languages',
                'description': 'Programming languages that get bonus points for i18n scanning',
                'phase': 'SCORING',
                'items': Config.HIGH_VALUE_LANGUAGES
            },
            {
                'name': 'Launched Indicators (Negative)',
                'description': 'Keywords in docs that indicate i18n is already live (disqualifies Goldilocks)',
                'phase': 'LAUNCHED',
                'items': Config.DOCUMENTATION_LAUNCHED_INDICATORS
            }
        ],
        'scoring': {
            'weights': Config.INTENT_SCORE_WEIGHTS,
            'goldilocks_scores': Config.GOLDILOCKS_SCORES,
            'lead_status_labels': Config.LEAD_STATUS_LABELS
        },
        'scan_config': {
            'max_repos_to_scan': Config.MAX_REPOS_TO_SCAN,
            'repo_inactivity_days': Config.REPO_INACTIVITY_DAYS,
            'rfc_lookback_days': Config.RFC_LOOKBACK_DAYS,
            'documentation_proximity_chars': Config.DOCUMENTATION_PROXIMITY_CHARS
        }
    }

    return jsonify(rules)


@app.route('/api/rules/refresh', methods=['POST'])
def api_rules_refresh():
    """
    Manually trigger a rules timestamp update.

    This endpoint allows admins to mark that rules have been reviewed/updated.
    The automatic 7am EST update happens via scheduler, but this allows
    manual refresh when rules are modified.

    Returns:
        JSON with new timestamp.
    """
    new_timestamp = _update_rules_timestamp()
    return jsonify({
        'success': True,
        'last_updated': new_timestamp
    })


@app.route('/api/rules/download')
def api_rules_download():
    """
    Download all scanning rules as a formatted text file.

    This is useful for sharing with LLMs or for documentation purposes.
    Returns a plain text file with all rules organized by category.
    """
    rules_text = _generate_rules_document()

    response = Response(rules_text, mimetype='text/plain')
    response.headers['Content-Disposition'] = 'attachment; filename=scanning_rules.txt'
    return response


def _generate_rules_document():
    """Generate a formatted text document of all scanning rules."""
    lines = []
    lines.append("=" * 80)
    lines.append("LEAD MACHINE - SCANNING RULES & HEURISTICS")
    lines.append("3-Signal Internationalization Intent Scanner")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # Intent Score Weights
    lines.append("-" * 80)
    lines.append("INTENT SCORE WEIGHTS")
    lines.append("-" * 80)
    for key, value in Config.INTENT_SCORE_WEIGHTS.items():
        label = key.replace('_', ' ').title()
        lines.append(f"  {label}: {value} points")
    lines.append("")

    # Goldilocks Scores
    lines.append("-" * 80)
    lines.append("GOLDILOCKS ZONE SCORES")
    lines.append("-" * 80)
    for key, value in Config.GOLDILOCKS_SCORES.items():
        label = key.replace('_', ' ').title()
        lines.append(f"  {label}: {value}")
    lines.append("")

    # Lead Status Labels
    lines.append("-" * 80)
    lines.append("LEAD STATUS LABELS")
    lines.append("-" * 80)
    for key, value in Config.LEAD_STATUS_LABELS.items():
        lines.append(f"  {key.upper()}: {value}")
    lines.append("")

    # Scan Configuration
    lines.append("-" * 80)
    lines.append("SCAN CONFIGURATION")
    lines.append("-" * 80)
    lines.append(f"  Max Repos to Scan: {Config.MAX_REPOS_TO_SCAN}")
    lines.append(f"  Repo Inactivity Days: {Config.REPO_INACTIVITY_DAYS}")
    lines.append(f"  RFC Lookback Days: {Config.RFC_LOOKBACK_DAYS}")
    lines.append(f"  Documentation Proximity Chars: {Config.DOCUMENTATION_PROXIMITY_CHARS}")
    lines.append("")

    # Rule Categories
    categories = [
        ("SIGNAL 1: RFC & DISCUSSION KEYWORDS (Thinking Phase)", Config.RFC_KEYWORDS,
         "Keywords that trigger detection when found in GitHub Issues/Discussions"),
        ("SIGNAL 2: SMOKING GUN LIBRARIES (Preparing Phase)", Config.SMOKING_GUN_LIBS,
         "i18n libraries that indicate infrastructure setup without translations"),
        ("SIGNAL 2b: SMOKING GUN FORK REPOS", Config.SMOKING_GUN_FORK_REPOS,
         "When companies fork these repos, they're customizing i18n infrastructure"),
        ("SIGNAL 2c: LINTER LIBRARIES", Config.LINTER_LIBRARIES,
         "Code linting libraries for finding/scrubbing hardcoded strings"),
        ("SIGNAL 2d: CMS i18n LIBRARIES", Config.CMS_I18N_LIBS,
         "CMS-specific internationalization plugins"),
        ("SIGNAL 3: GHOST BRANCH PATTERNS (Active Phase)", Config.GHOST_BRANCH_PATTERNS,
         "Branch/PR naming patterns indicating WIP localization work"),
        ("SIGNAL 4: DOCUMENTATION INTENT KEYWORDS", Config.DOCUMENTATION_INTENT_KEYWORDS,
         "Keywords in docs that indicate planned i18n work"),
        ("DOCUMENTATION CONTEXT KEYWORDS", Config.DOCUMENTATION_CONTEXT_KEYWORDS,
         "Must appear near intent keywords to indicate future/WIP work"),
        ("EXCLUSION FOLDERS (Disqualifiers)", Config.EXCLUSION_FOLDERS,
         "If these exist with translations, company has ALREADY LAUNCHED"),
        ("SOURCE LOCALE PATTERNS (Goldilocks Exception)", Config.SOURCE_LOCALE_PATTERNS,
         "If locale folder contains ONLY these, still in Goldilocks Zone"),
        ("DEPENDENCY FILES SCANNED", Config.DEPENDENCY_INJECTION_FILES,
         "Package manager files checked for i18n library dependencies"),
        ("FRAMEWORK CONFIG FILES", Config.FRAMEWORK_CONFIG_FILES,
         "Framework configuration files checked for i18n routing"),
        ("DOCUMENTATION FILES SCANNED", Config.DOCUMENTATION_FILES,
         "Documentation files checked for i18n intent signals"),
        ("i18n SCRIPT KEYWORDS", Config.I18N_SCRIPT_KEYWORDS,
         "Keywords in package.json scripts indicating i18n preparation"),
        ("BUILD SCRIPT i18n KEYWORDS", Config.BUILD_SCRIPT_I18N_KEYWORDS,
         "Keywords in build scripts for locale/translation work"),
        ("OPEN PROTOCOL DISQUALIFIERS", Config.OPEN_PROTOCOL_DISQUALIFIERS,
         "Patterns that identify non-commercial open source projects"),
        ("HIGH-VALUE REPO PATTERNS (+1000 points)", Config.HIGH_VALUE_PATTERNS,
         "Repo name patterns indicating core product"),
        ("LOW-VALUE REPO PATTERNS (-500 points)", Config.LOW_VALUE_PATTERNS,
         "Repo name patterns indicating non-core repos"),
        ("HIGH-VALUE LANGUAGES (+500 points)", Config.HIGH_VALUE_LANGUAGES,
         "Programming languages that get bonus points"),
        ("LAUNCHED INDICATORS (Negative)", Config.DOCUMENTATION_LAUNCHED_INDICATORS,
         "Keywords indicating i18n is already live"),
    ]

    for title, items, description in categories:
        lines.append("-" * 80)
        lines.append(title)
        lines.append("-" * 80)
        lines.append(f"Description: {description}")
        lines.append("")
        for item in items:
            lines.append(f"  - {item}")
        lines.append("")

    lines.append("=" * 80)
    lines.append("END OF RULES DOCUMENT")
    lines.append("=" * 80)

    return "\n".join(lines)


def _get_cached_rule_explanation(rule_name: str) -> str:
    """Get a cached AI explanation for a rule, or None if not cached."""
    cache_key = f"rule_explanation:{rule_name}"
    cached = get_setting(cache_key)
    return cached


def _cache_rule_explanation(rule_name: str, explanation: str) -> None:
    """Cache an AI-generated explanation for a rule."""
    cache_key = f"rule_explanation:{rule_name}"
    set_setting(cache_key, explanation)


@app.route('/api/rules/explain', methods=['POST'])
def api_rules_explain():
    """
    Get AI-generated simple explanations for rules.

    Uses Gemini AI to generate layman-friendly explanations and caches them.

    Request body:
        {"rules": ["rule_name_1", "rule_name_2", ...]}

    Returns:
        JSON with explanations for each rule.
    """
    if not Config.GEMINI_API_KEY:
        return jsonify({'error': 'Gemini API key not configured'}), 500

    data = request.get_json() or {}
    rule_names = data.get('rules', [])

    if not rule_names:
        return jsonify({'error': 'No rules specified'}), 400

    explanations = {}
    rules_to_generate = []

    # Check cache first
    for rule_name in rule_names:
        cached = _get_cached_rule_explanation(rule_name)
        if cached:
            explanations[rule_name] = cached
        else:
            rules_to_generate.append(rule_name)

    # Generate explanations for uncached rules
    if rules_to_generate:
        try:
            new_explanations = _generate_rule_explanations(rules_to_generate)
            for rule_name, explanation in new_explanations.items():
                _cache_rule_explanation(rule_name, explanation)
                explanations[rule_name] = explanation
        except Exception as e:
            print(f"[RULES] Error generating explanations: {e}")
            # Return what we have from cache, mark others as error
            for rule_name in rules_to_generate:
                if rule_name not in explanations:
                    explanations[rule_name] = "Unable to generate explanation at this time."

    return jsonify({'explanations': explanations})


def _generate_rule_explanations(rule_names: list) -> dict:
    """
    Generate AI explanations for a batch of rules using Gemini.

    Returns a dict mapping rule names to their explanations.
    """
    from google import genai

    # Build context about what each rule does
    rule_context = _get_rule_context_for_ai(rule_names)

    prompt = f"""You are explaining technical software scanning rules to a non-technical business person.

For each rule below, provide a simple 1-2 sentence explanation that:
- Uses everyday language (no developer jargon)
- Explains WHY this matters for finding potential customers
- Is conversational and friendly

Rules to explain:
{rule_context}

Format your response as JSON with rule names as keys and explanations as values.
Example: {{"rule_name": "Simple explanation here"}}

IMPORTANT: Return ONLY valid JSON, no markdown formatting or code blocks."""

    client = genai.Client(api_key=Config.GEMINI_API_KEY)

    response = client.models.generate_content(
        model=Config.GEMINI_MODEL,
        contents=prompt
    )

    response_text = response.text.strip()

    # Clean up response - remove markdown code blocks if present
    if response_text.startswith('```'):
        lines = response_text.split('\n')
        lines = [l for l in lines if not l.startswith('```')]
        response_text = '\n'.join(lines)

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        print(f"[RULES] Failed to parse AI response: {response_text[:200]}")
        return {}


def _get_rule_context_for_ai(rule_names: list) -> str:
    """Build context string for AI to understand what each rule does."""
    context_map = {
        'rfc_keywords': f"RFC & Discussion Keywords: {', '.join(Config.RFC_KEYWORDS[:5])}... These are searched in GitHub issues to find companies discussing internationalization plans.",
        'smoking_gun_libs': f"Smoking Gun Libraries: {', '.join(Config.SMOKING_GUN_LIBS[:5])}... These are code libraries that companies install when preparing for translation.",
        'smoking_gun_fork_repos': f"Forked Repositories: {', '.join(Config.SMOKING_GUN_FORK_REPOS[:5])}... When companies copy and modify these projects, they're building translation systems.",
        'linter_libraries': f"Linter Libraries: {', '.join(Config.LINTER_LIBRARIES[:3])}... Tools that help find text that needs translation.",
        'cms_i18n_libs': f"CMS i18n Libraries: {', '.join(Config.CMS_I18N_LIBS[:3])}... Plugins for content management systems to support multiple languages.",
        'ghost_branch_patterns': f"Branch Patterns: {', '.join(Config.GHOST_BRANCH_PATTERNS[:5])}... Names developers use for work-in-progress translation features.",
        'documentation_intent_keywords': f"Documentation Keywords: {', '.join(Config.DOCUMENTATION_INTENT_KEYWORDS[:5])}... Phrases found in project docs indicating translation plans.",
        'documentation_context_keywords': f"Context Keywords: {', '.join(Config.DOCUMENTATION_CONTEXT_KEYWORDS[:5])}... Words like 'planned' or 'upcoming' that show something is future work.",
        'exclusion_folders': f"Exclusion Folders: {', '.join(Config.EXCLUSION_FOLDERS)}... Folder names where translations live. If these exist with translations, they've already launched.",
        'source_locale_patterns': f"Source Locale Patterns: Files like en.json or base.json. If only these exist, they're still preparing.",
        'dependency_injection_files': f"Dependency Files: {', '.join(Config.DEPENDENCY_INJECTION_FILES[:5])}... Package manager files where we look for translation libraries.",
        'framework_config_files': f"Framework Configs: {', '.join(Config.FRAMEWORK_CONFIG_FILES[:3])}... Configuration files that may have translation settings.",
        'documentation_files': f"Documentation Files: {', '.join(Config.DOCUMENTATION_FILES[:4])}... Project docs where companies mention translation plans.",
        'i18n_script_keywords': f"Script Keywords: {', '.join(Config.I18N_SCRIPT_KEYWORDS[:3])}... Commands in build scripts for translation work.",
        'build_script_i18n_keywords': f"Build Keywords: {', '.join(Config.BUILD_SCRIPT_I18N_KEYWORDS[:4])}... Keywords in build commands related to translations.",
        'open_protocol_disqualifiers': f"Disqualifiers: {', '.join(Config.OPEN_PROTOCOL_DISQUALIFIERS[:4])}... Phrases that indicate a project is open-source/community-driven, not a commercial company.",
        'high_value_patterns': f"High-Value Patterns: {', '.join(Config.HIGH_VALUE_PATTERNS[:5])}... Repo names that indicate the main product.",
        'low_value_patterns': f"Low-Value Patterns: {', '.join(Config.LOW_VALUE_PATTERNS)}... Repo names that indicate secondary projects.",
        'high_value_languages': f"High-Value Languages: {', '.join(Config.HIGH_VALUE_LANGUAGES)}... Programming languages commonly used for user-facing apps.",
        'documentation_launched_indicators': f"Launched Indicators: {', '.join(Config.DOCUMENTATION_LAUNCHED_INDICATORS[:3])}... Phrases showing translations are already live.",
    }

    lines = []
    for rule_name in rule_names:
        if rule_name in context_map:
            lines.append(f"- {rule_name}: {context_map[rule_name]}")
        else:
            lines.append(f"- {rule_name}: A scanning rule for detecting internationalization signals.")

    return "\n".join(lines)


@app.route('/api/rules/explain-all', methods=['POST'])
def api_rules_explain_all():
    """
    Generate and cache explanations for all rules at once.

    This is useful for pre-populating the cache so users don't wait.
    Returns all explanations.
    """
    if not Config.GEMINI_API_KEY:
        return jsonify({'error': 'Gemini API key not configured'}), 500

    all_rule_names = [
        'rfc_keywords', 'smoking_gun_libs', 'smoking_gun_fork_repos',
        'linter_libraries', 'cms_i18n_libs', 'ghost_branch_patterns',
        'documentation_intent_keywords', 'documentation_context_keywords',
        'exclusion_folders', 'source_locale_patterns', 'dependency_injection_files',
        'framework_config_files', 'documentation_files', 'i18n_script_keywords',
        'build_script_i18n_keywords', 'open_protocol_disqualifiers',
        'high_value_patterns', 'low_value_patterns', 'high_value_languages',
        'documentation_launched_indicators'
    ]

    explanations = {}
    rules_to_generate = []

    # Check cache first
    for rule_name in all_rule_names:
        cached = _get_cached_rule_explanation(rule_name)
        if cached:
            explanations[rule_name] = cached
        else:
            rules_to_generate.append(rule_name)

    # Generate in batches to avoid overwhelming the API
    batch_size = 5
    for i in range(0, len(rules_to_generate), batch_size):
        batch = rules_to_generate[i:i + batch_size]
        try:
            new_explanations = _generate_rule_explanations(batch)
            for rule_name, explanation in new_explanations.items():
                _cache_rule_explanation(rule_name, explanation)
                explanations[rule_name] = explanation
        except Exception as e:
            print(f"[RULES] Error generating batch explanations: {e}")
            for rule_name in batch:
                if rule_name not in explanations:
                    explanations[rule_name] = "Unable to generate explanation at this time."

    return jsonify({
        'success': True,
        'explanations': explanations,
        'generated_count': len(rules_to_generate),
        'cached_count': len(all_rule_names) - len(rules_to_generate)
    })


def _scheduled_rules_update():
    """
    Scheduled task to update rules timestamp at 7am EST daily.

    This runs in a background thread and updates the timestamp to indicate
    rules are current. In a production system, this could also pull rules
    from a remote config or notify the team.
    """
    import pytz
    from datetime import time as dt_time

    est = pytz.timezone('US/Eastern')

    while True:
        try:
            now_est = datetime.now(est)
            target_time = now_est.replace(hour=7, minute=0, second=0, microsecond=0)

            # If we're past 7am today, schedule for tomorrow
            if now_est >= target_time:
                target_time = target_time + timedelta(days=1)

            # Calculate seconds until target time
            seconds_until_target = (target_time - now_est).total_seconds()

            print(f"[RULES] Next rules update scheduled for {target_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            time.sleep(seconds_until_target)

            # Update the timestamp
            _update_rules_timestamp()
            print(f"[RULES] Rules timestamp updated at 7am EST: {datetime.now(est).isoformat()}")

        except Exception as e:
            print(f"[RULES] Error in scheduled update: {e}")
            # Sleep for an hour on error and retry
            time.sleep(3600)


def start_rules_scheduler():
    """Start the rules update scheduler in a background daemon thread."""
    thread = threading.Thread(target=_scheduled_rules_update, daemon=True, name="RulesScheduler")
    thread.start()


def _watchdog_worker():
    """
    Background worker that runs indefinitely to clear stale processing statuses
    and recover stuck queued accounts.
    Runs every 2 minutes.
    """
    print("[WATCHDOG] Background thread started")
    while True:
        try:
            # Clear any account stuck in 'processing' for more than 15 minutes
            recovered_processing = clear_stale_scan_statuses(timeout_minutes=15)
            if recovered_processing > 0:
                print(f"[WATCHDOG] Recovered {recovered_processing} stale processing scans")

            # Recover accounts stuck in 'queued' for more than 30 minutes
            # These are accounts that were queued but never picked up by a worker
            stale_queued = reset_stale_queued_accounts(timeout_minutes=30)
            if stale_queued:
                print(f"[WATCHDOG] Found {len(stale_queued)} stale queued accounts, re-queueing...")
                # Re-queue these accounts by submitting them to the executor
                for company_name in stale_queued:
                    try:
                        spawn_background_scan(company_name)
                        print(f"[WATCHDOG] Re-queued: {company_name}")
                    except Exception as e:
                        print(f"[WATCHDOG] Failed to re-queue {company_name}: {e}")

        except Exception as e:
            print(f"[WATCHDOG] Error in watchdog: {e}")

        # Sleep for 2 minutes
        time.sleep(120)


def start_watchdog():
    """Start the watchdog in a background daemon thread."""
    thread = threading.Thread(target=_watchdog_worker, daemon=True, name="ScanWatchdog")
    thread.start()


if __name__ == '__main__':
    # Initialize when running directly
    print("[APP] Starting application...")
    print(f"[APP] ThreadPoolExecutor configured with {MAX_SCAN_WORKERS} workers")

    # Cleanup any duplicate accounts
    cleanup_result = cleanup_duplicate_accounts()
    removed_count = cleanup_result.get('deleted', 0)
    if removed_count > 0:
        print(f"[APP] Removed {removed_count} duplicate accounts")

    # Initialize the executor BEFORE recovery functions need it
    get_executor()

    # Start the background watchdog thread
    start_watchdog()

    # Start the rules scheduler for 7am EST daily updates
    start_rules_scheduler()

    # IMPORTANT: Recover stuck queued accounts BEFORE reset_all_scan_statuses
    # This captures accounts stuck in 'queued' state and re-queues them
    _recover_stuck_queued_accounts()

    # Reset any remaining stale scan statuses (processing accounts)
    reset_count = reset_all_scan_statuses()
    if reset_count > 0:
        print(f"[APP] Reset {reset_count} stale processing statuses from previous run")

    # Clear any misclassified errors (tier evidence stored as errors)
    cleared_errors = clear_misclassified_errors()
    if cleared_errors > 0:
        print(f"[APP] Cleared {cleared_errors} misclassified error messages")

    # Resume any interrupted import batches
    _resume_interrupted_import_batches()

    # Auto-scan any accounts that were never scanned
    _auto_scan_pending_accounts()

    # Mark as initialized to prevent duplicate auto-scan on first request
    _app_initialized = True

    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000, threaded=True)
