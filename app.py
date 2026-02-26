"""
GitHub Dossier - AI-Powered Sales Intelligence for Localization Opportunities

A Flask application for analyzing GitHub organizations for localization signals.
"""
import json
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Sequence
import requests
from datetime import datetime
from flask import Flask, render_template, Response, request, jsonify, redirect, url_for, stream_with_context, send_file, send_from_directory
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
    update_account_website, update_account_metadata, update_account_notes,
    create_import_batch, get_pending_import_batches, update_batch_progress, get_import_batch,
    increment_hourly_api_calls, get_current_hour_api_calls, cleanup_old_hourly_stats,
    archive_account, unarchive_account, get_archived_accounts, get_archived_accounts_for_rescan,
    get_archived_count, auto_archive_tier4_accounts,
    find_potential_duplicates, get_import_duplicates_summary,
    save_website_analysis, get_website_analysis, get_latest_website_analysis,
    get_all_website_analyses, get_accounts_with_websites, delete_website_analysis,
    # WebScraper Accounts
    populate_webscraper_from_reporadar, get_webscraper_tier_counts, get_webscraper_accounts_datatable,
    update_webscraper_notes, archive_webscraper_account, unarchive_webscraper_account,
    delete_webscraper_account, get_webscraper_archived_count, webscraper_bulk_archive,
    webscraper_bulk_delete, webscraper_bulk_change_tier, get_webscraper_account,
    is_webscraper_accounts_empty, WEBSCRAPER_TIER_CONFIG, update_webscraper_scan_results,
    # Contributors
    save_contributor, save_contributors_batch, get_contributors_datatable,
    get_contributor_stats, update_contributor_apollo_status, update_contributor_email,
    increment_contributor_emails, get_contributor_by_id, delete_contributor,
    # ScoreCard
    get_scorecard_datatable, upsert_scorecard_scores, update_scorecard_systems,
    update_scorecard_enrollment, get_scorecard_score,
    # Scheduled Rescans
    TIER_SCAN_INTERVALS, get_scheduled_rescan_summary,
)
from monitors.webscraper_utils import (
    detect_expansion_signals, calculate_webscraper_tier, extract_tier_from_scan_results,
    generate_evidence_summary, TIER_CONFIG as WEBSCRAPER_TIER_CONFIG_NEW
)
from monitors.scanner import deep_scan_generator
from monitors.discovery import search_github_orgs, resolve_org_fast, discover_companies_via_ai
from monitors.web_analyzer import analyze_website, analyze_website_technical
from ai_summary import generate_analysis
from pdf_generator import generate_report_pdf
from agentmail_client import is_agentmail_configured, send_email_draft
from sheets_client import is_sheets_configured, get_sheet_info
from sheets_sync import (
    run_sync as sheets_run_sync,
    get_sync_config as sheets_get_sync_config,
    set_sync_config as sheets_set_sync_config,
    start_cron_scheduler as sheets_start_cron,
    is_sync_in_progress as sheets_sync_in_progress
)


app = Flask(__name__)
app.config.from_object(Config)


def sanitize_ai_error(exception):
    """Map raw AI/API exceptions to user-friendly error messages."""
    msg = str(exception).lower()
    if '429' in msg or 'resource exhausted' in msg or 'quota' in msg:
        return 'AI service is temporarily overloaded. Please wait a moment and try again.'
    if 'timeout' in msg or 'timed out' in msg or 'deadline' in msg:
        return 'AI request timed out. Please try again.'
    if '401' in msg or '403' in msg or 'api key' in msg or 'authentication' in msg or 'permission' in msg:
        return 'AI service authentication error. Please check API key configuration.'
    if '404' in msg or 'not found' in msg:
        return 'AI model not found. Please check configuration.'
    if '500' in msg or '503' in msg or 'internal' in msg or 'unavailable' in msg:
        return 'AI service is temporarily unavailable. Please try again later.'
    return 'Email generation failed. Please try again.'


@app.context_processor
def inject_cache_buster():
    return {'cache_bust': int(time.time())}


@app.after_request
def add_no_cache_headers(response):
    if 'text/html' in response.content_type or 'text/css' in response.content_type or 'javascript' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


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
                        'contributions': c.get('contributions', 0),
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
                            user_data['email'] = ''  # Emails come from Apollo only, not GitHub profiles
                            user_data['github_email'] = user_info.get('email') or ''  # Keep for reference
                            user_data['blog'] = user_info.get('blog') or ''
                            user_data['github_profile_company'] = user_info.get('company') or ''
                    except Exception as e:
                        print(f"[CONTRIBUTORS] Failed to fetch user profile for {login}: {e}")

                    contributors.append(user_data)

                    if len(contributors) >= limit:
                        break

            return contributors
    except Exception as e:
        print(f"[CONTRIBUTORS] Contributor fetch failed for {org_login}/{repo_name}: {e}")
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

@app.template_filter('format_revenue')
def format_revenue_filter(value):
    """Format revenue as human-readable currency (e.g., $431.7M)."""
    if not value:
        return ''
    # If already formatted (contains $ or letters), return as-is
    s = str(value).strip()
    if any(c.isalpha() or c == '$' for c in s):
        return s
    try:
        num = float(s.replace(',', ''))
    except (ValueError, TypeError):
        return s
    if num >= 1_000_000_000:
        formatted = num / 1_000_000_000
        return f'${formatted:.1f}B' if formatted != int(formatted) else f'${int(formatted)}B'
    elif num >= 1_000_000:
        formatted = num / 1_000_000
        return f'${formatted:.1f}M' if formatted != int(formatted) else f'${int(formatted)}M'
    elif num >= 1_000:
        formatted = num / 1_000
        return f'${formatted:.1f}K' if formatted != int(formatted) else f'${int(formatted)}K'
    return f'${num:,.0f}'


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
                    "text": f"🤖 GitHub Dossier • {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
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
# GOOGLE SHEETS WEBHOOK - Export Tier 1/2 accounts to Google Sheets
# =============================================================================

def format_gsheet_payload(event_type: str, company_data: dict) -> dict:
    """
    Format webhook data for Google Sheets Apps Script.

    Creates a flat JSON payload matching the RepoRadar table columns:
    - company_name, annual_revenue, github_org, tier, tier_name, status
    - last_scanned_at, evidence_summary, report_link, notes

    Args:
        event_type: Type of event (e.g., 'tier_change')
        company_data: Dictionary with company info, tier, signals, etc.

    Returns:
        Dictionary formatted for Google Apps Script webhook receiver
    """
    tier = company_data.get('tier', 0)
    tier_config = TIER_CONFIG.get(tier, TIER_CONFIG[0])

    # Get base URL for report links
    try:
        from flask import request, has_request_context
        if has_request_context():
            base_url = request.host_url.rstrip('/')
        else:
            base_url = os.environ.get('BASE_URL', 'http://localhost:5000')
    except Exception:
        base_url = os.environ.get('BASE_URL', 'http://localhost:5000')

    # Build report link
    report_id = company_data.get('report_id')
    report_link = f"{base_url}/report/{report_id}" if report_id else ""

    # Format last scanned timestamp
    last_scanned = company_data.get('last_scanned_at', '')
    if not last_scanned:
        last_scanned = datetime.now().isoformat()

    return {
        'event_type': event_type,
        'timestamp': datetime.now().isoformat(),
        'company': {
            'company_name': company_data.get('company', company_data.get('company_name', '')),
            'annual_revenue': company_data.get('revenue', company_data.get('annual_revenue', '')),
            'github_org': company_data.get('github_org', ''),
            'current_tier': tier,
            'tier_name': tier_config.get('name', 'Unknown'),
            'tier_status': tier_config.get('status', ''),
            'last_scanned_at': last_scanned,
            'evidence_summary': company_data.get('evidence', company_data.get('evidence_summary', '')),
            'report_id': report_id,
            'report_link': report_link,
            'notes': company_data.get('notes', ''),
            'website': company_data.get('website', ''),
            # Include signals summary if available
            'signals_summary': company_data.get('signals_summary', [])
        }
    }


def trigger_gsheet_webhook(event_type: str, company_data: dict) -> None:
    """
    Send a webhook notification to Google Sheets Apps Script.

    This function runs in a background thread to avoid blocking the UI.
    Fetches the Google Sheets webhook URL from system_settings and sends a POST request.

    The Google Apps Script will receive the data and append/update a row in the sheet.

    Args:
        event_type: Type of event (e.g., 'tier_change', 'scan_complete')
        company_data: Dictionary containing company information to send
    """
    # Check if Google Sheets webhook is enabled
    gsheet_enabled = get_setting('gsheet_webhook_enabled')
    if gsheet_enabled != 'true':
        print("[GSHEET WEBHOOK] Google Sheets webhook is disabled, skipping")
        return

    gsheet_url = get_setting('gsheet_webhook_url')
    if not gsheet_url:
        print("[GSHEET WEBHOOK] No gsheet_webhook_url configured, skipping")
        return

    # Only send Tier 1 and Tier 2 accounts
    tier = company_data.get('tier', 0)
    if tier not in [1, 2]:
        print(f"[GSHEET WEBHOOK] Skipping tier {tier} - only Tier 1 and 2 are exported")
        return

    def send_gsheet_webhook():
        company_name = company_data.get('company', company_data.get('company_name', 'Unknown'))

        try:
            # Format payload for Google Apps Script
            payload = format_gsheet_payload(event_type, company_data)

            response = requests.post(
                gsheet_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=30  # Google Apps Script can be slow
            )

            if response.status_code >= 200 and response.status_code < 300:
                print(f"[GSHEET WEBHOOK] Success: {company_name} exported to Google Sheets (status: {response.status_code})")
                try:
                    log_webhook(f'gsheet_{event_type}', company_name, 'success')
                except Exception as db_err:
                    print(f"[GSHEET WEBHOOK] DB logging error: {db_err}")
            else:
                print(f"[GSHEET WEBHOOK] Failed: {company_name} (status: {response.status_code}, response: {response.text[:200]})")
                try:
                    log_webhook(f'gsheet_{event_type}', company_name, 'fail')
                except Exception as db_err:
                    print(f"[GSHEET WEBHOOK] DB logging error: {db_err}")

        except requests.exceptions.Timeout:
            print(f"[GSHEET WEBHOOK] Timeout: {company_name} -> {gsheet_url}")
            try:
                log_webhook(f'gsheet_{event_type}', company_name, 'fail')
            except Exception:
                pass
        except requests.exceptions.RequestException as e:
            print(f"[GSHEET WEBHOOK] Error: {company_name} -> {str(e)}")
            try:
                log_webhook(f'gsheet_{event_type}', company_name, 'fail')
            except Exception:
                pass

    # Run in background thread to avoid blocking
    gsheet_thread = threading.Thread(target=send_gsheet_webhook, daemon=True, name="GSheetWebhookSender")
    gsheet_thread.start()


# =============================================================================
# THREAD POOL EXECUTOR - Concurrent scan processing with DB-backed state
# =============================================================================

# Configurable number of workers (default 20)
# Increased from 5 to 20 to improve queue processing throughput
# With 4000+ items in queue, 5 workers would take ~33 days
# With 20 workers, we can process ~4x faster (assuming sufficient API tokens)
MAX_SCAN_WORKERS = int(os.environ.get('SCAN_WORKERS', 20))

# Batch rescan orchestrator state
_batch_rescan_lock = threading.Lock()
_batch_rescan_state = {
    'active': False,
    'cancelled': False,
    'total': 0,
    'completed': 0,
    'current_batch': 0,
    'total_batches': 0,
    'batch_size': 50,
    'delay_seconds': 30,
    'started_at': None,
    'scope': '',
}

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
                    'revenue': result.get('revenue'),
                    'website': result.get('website', '')
                }
                # Enrich with report details and signals
                webhook_data = enrich_webhook_data(webhook_data, report_id)
                trigger_webhook('tier_change', webhook_data)
                # Also trigger Google Sheets export for Tier 1/2 accounts
                trigger_gsheet_webhook('tier_change', webhook_data)
                print(f"[WORKER] Webhook triggered for {company_name} (Tier {result.get('tier')})")
        except Exception as e:
            # Store the error for user visibility instead of silently failing
            scan_error = f"Tier classification failed: {str(e)}"
            print(f"[WORKER] {scan_error}")
            import traceback; traceback.print_exc()

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
            # Support both string format and object format with annual_revenue/website/metadata
            if isinstance(company_item, dict):
                company_name = company_item.get('name', '').strip()
                # Validate revenue - ignore text that doesn't look like a number
                annual_revenue = validate_revenue_value(company_item.get('annual_revenue'))
                website = company_item.get('website', '').strip() if company_item.get('website') else None
                metadata = company_item.get('metadata')  # Extra CSV fields as dict
            else:
                company_name = str(company_item).strip()
                annual_revenue = None
                website = None
                metadata = None

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
                    if metadata:
                        update_account_metadata(company_name, metadata)
                    skipped.append(company_name)
                    processed_count = i + 1
                    # Update progress every 10 items
                    if processed_count % 10 == 0:
                        update_batch_progress(batch_id, processed_count)
                        print(f"[BATCH-WORKER] Batch {batch_id}: processed {processed_count}/{total_count}")
                    continue

                # Add to monitored_accounts at Tier 0 without GitHub org
                # Users can manually link GitHub orgs via /api/update-org
                add_account_to_tier_0(company_name, '', annual_revenue, website, metadata)
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

        # Run deduplication after import to clean up any duplicates that slipped through
        try:
            dedup_result = cleanup_duplicate_accounts()
            dedup_removed = dedup_result.get('deleted', 0)
            if dedup_removed > 0:
                print(f"[BATCH-WORKER] Post-import deduplication removed {dedup_removed} duplicate accounts")
        except Exception as e:
            print(f"[BATCH-WORKER] Post-import deduplication error: {e}")

        # Auto-queue newly imported accounts for scanning (with throttling)
        # Limit initial queue to prevent overwhelming the executor
        MAX_AUTO_QUEUE = 50  # Queue at most 50 accounts immediately, rest will be picked up by watchdog
        if added:
            queued_for_scan = 0
            to_queue = added[:MAX_AUTO_QUEUE]  # Only queue first batch
            for company_name in to_queue:
                try:
                    spawn_background_scan(company_name)
                    queued_for_scan += 1
                except Exception as e:
                    print(f"[BATCH-WORKER] Error queuing {company_name} for scan: {e}")
            
            remaining = len(added) - queued_for_scan
            if remaining > 0:
                print(f"[BATCH-WORKER] Auto-queued {queued_for_scan} accounts for scanning ({remaining} more will be queued by watchdog)")
            else:
                print(f"[BATCH-WORKER] Auto-queued {queued_for_scan} new accounts for scanning")

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
                # Also trigger Google Sheets export for Tier 1/2 accounts
                trigger_gsheet_webhook('tier_change', webhook_data)
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
        }
    )


@app.route('/report/<int:report_id>/pdf')
def download_pdf(report_id: int):
    """Generate and download a PDF report."""
    report = get_report(report_id)
    if not report:
        return render_template('error.html', message='Report not found'), 404
        
    import tempfile
    filename = f"GitHubDossier_Report_{report['github_org']}_{report_id}.pdf"

    try:
        # Write to a temp file and delete after sending — avoids accumulating
        # PDFs on disk in static/pdfs/ indefinitely.
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp_path = tmp.name

        generate_report_pdf(report, tmp_path)
        return send_file(
            tmp_path,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf',
        )
    except Exception as e:
        print(f"[ERROR] PDF generation failed for report {report_id}: {e}")
        return render_template('error.html', message='PDF generation failed. Please try again later.'), 500
    finally:
        # Clean up temp file after response is sent
        try:
            import os as _os
            _os.unlink(tmp_path)
        except Exception:
            pass


@app.route('/report/<int:report_id>')
def view_report(report_id: int):
    """View a saved report. Pass ?embed=1 to render without app shell (for drawer/iframe)."""
    report = get_report(report_id)
    if not report:
        return render_template('error.html', message='Report not found'), 404
    embed = request.args.get('embed') == '1'
    template = 'report_embed.html' if embed else 'report.html'
    return render_template(template, report=report)


@app.route('/search')
def search():
    """Search for a company - redirects to scan page."""
    company = request.args.get('q', '').strip()
    # Sanitize input: remove dangerous characters, keep alphanumeric, dots, dashes, underscores
    company = "".join(c for c in company if c.isalnum() or c in ".-_ ").strip()
    
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
    Generate a Deep Dive analysis for a report using OpenAI GPT-5 mini.

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
        return jsonify({'error': 'Failed to generate Deep Dive'}), 500


@app.route('/history')
def history():
    """View scan history."""
    reports = get_recent_reports(limit=50)
    return render_template('history.html', reports=reports)


@app.route('/campaigns')
def campaigns():
    """Campaigns & Map Sequences landing page."""
    return render_template('campaigns.html')


@app.route('/scorecard')
def scorecard():
    """ScoreCard - Account scoring & qualification page."""
    return render_template('scorecard.html')


def _parse_revenue(s):
    """Parse revenue string like '$4.6B', '$500M', raw numbers → float."""
    import re
    if not s:
        return 0.0
    s = str(s).strip().replace(',', '')
    m = re.match(r'\$?\s*([\d.]+)\s*(T|B|M|K)?', s, re.IGNORECASE)
    if m:
        num = float(m.group(1))
        suffix = (m.group(2) or '').upper()
        if suffix == 'T': return num * 1e12
        if suffix == 'B': return num * 1e9
        if suffix == 'M': return num * 1e6
        if suffix == 'K': return num * 1e3
        return num
    cleaned = re.sub(r'[^0-9.]', '', s)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


@app.route('/api/scorecard/datatable')
def api_scorecard_datatable():
    """DataTable endpoint for scorecard scores with server-side pagination."""
    draw = request.args.get('draw', 1, type=int)
    start = request.args.get('start', 0, type=int)
    length = request.args.get('length', 25, type=int)
    search_value = request.args.get('search[value]', '').strip()
    order_column = request.args.get('order[0][column]', 1, type=int)
    order_dir = request.args.get('order[0][dir]', 'desc')
    cohort = request.args.get('cohort', '').strip()

    result = get_scorecard_datatable(
        draw=draw, start=start, length=length,
        search_value=search_value, cohort_filter=cohort,
        order_column=order_column, order_dir=order_dir
    )
    return jsonify(result)


@app.route('/api/scorecard/rescore', methods=['POST'])
def api_scorecard_rescore():
    """Score all non-archived accounts using the provided rubric and save to DB."""
    data = request.get_json()
    if not data or 'rubric' not in data:
        return jsonify({'status': 'error', 'message': 'Missing rubric'}), 400

    rubric = data['rubric']

    # Fetch all non-archived accounts with locale data
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            ma.id,
            ma.company_name,
            ma.annual_revenue,
            ws.locale_count
        FROM monitored_accounts ma
        LEFT JOIN webscraper_accounts ws ON ws.monitored_account_id = ma.id
        WHERE ma.archived_at IS NULL
        GROUP BY ma.id
    ''')
    rows = cursor.fetchall()
    conn.close()

    scores = []
    for row in rows:
        acct = dict(row)
        lc = acct.get('locale_count') or 0

        # Language score
        lang_score = 0
        if lc >= 10: lang_score = rubric.get('lang_10_plus', 0)
        elif lc >= 5: lang_score = rubric.get('lang_5_9', 0)
        elif lc >= 2: lang_score = rubric.get('lang_2_4', 0)
        elif lc >= 1: lang_score = rubric.get('lang_1', 0)

        # Revenue score
        rev_raw = _parse_revenue(acct.get('annual_revenue'))
        rev_score = 0
        if rev_raw >= 5e9: rev_score = rubric.get('rev_5b_plus', 0)
        elif rev_raw >= 3e9: rev_score = rubric.get('rev_3b_5b', 0)
        elif rev_raw >= 1.5e9: rev_score = rubric.get('rev_1_5b_3b', 0)
        elif rev_raw >= 1e9: rev_score = rubric.get('rev_1b_1_5b', 0)
        elif rev_raw >= 5e8: rev_score = rubric.get('rev_500_1b', 0)
        elif rev_raw >= 1e8: rev_score = rubric.get('rev_100_499', 0)

        cohort = 'A' if rev_raw >= 1.5e9 else 'B'
        total = lang_score + rev_score  # systems_score preserved by upsert

        scores.append({
            'account_id': acct['id'],
            'company_name': acct['company_name'],
            'annual_revenue': acct.get('annual_revenue', ''),
            'revenue_raw': rev_raw,
            'locale_count': lc,
            'total_score': total,
            'lang_score': lang_score,
            'revenue_score': rev_score,
            'cohort': cohort,
        })

    count = upsert_scorecard_scores(scores) if scores else 0
    return jsonify({'status': 'success', 'scored': count})


@app.route('/api/scorecard/systems', methods=['POST'])
def api_scorecard_systems():
    """Update systems checkboxes for an account and recalculate score."""
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data'}), 400

    account_id = data.get('account_id')
    systems = data.get('systems', {})
    rubric = data.get('rubric', {})

    if not account_id:
        return jsonify({'status': 'error', 'message': 'Missing account_id'}), 400

    # Calculate systems_score from checkboxes x rubric weights
    sys_keys = {
        'vcs': 'sys_vcs', 'design': 'sys_design', 'oss_cms': 'sys_oss_cms',
        'enterprise_cms': 'sys_enterprise_cms', 'customer_svc': 'sys_customer_svc',
        'ecommerce': 'sys_ecommerce', 'marketing': 'sys_marketing'
    }
    sys_score = 0
    for sys_key, rubric_key in sys_keys.items():
        if systems.get(sys_key):
            sys_score += int(rubric.get(rubric_key, 0))

    systems_json = json.dumps(systems)
    ok = update_scorecard_systems(account_id, systems_json, sys_score)

    if ok:
        row = get_scorecard_score(account_id)
        return jsonify({'status': 'success', 'systems_score': sys_score, 'total_score': row['total_score'] if row else sys_score})
    return jsonify({'status': 'error', 'message': 'Account not found'}), 404


@app.route('/api/scorecard/enroll', methods=['POST'])
def api_scorecard_enroll():
    """Enroll a scorecard account into an Apollo sequence, then persist status."""
    import requests as req

    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        return jsonify({'status': 'error', 'message': 'Apollo API key not configured'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data'}), 400

    account_id = data.get('account_id')
    email = data.get('email', '').strip()
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    sequence_id = data.get('sequence_id', '').strip()
    company_name = data.get('company_name', '').strip()
    sequence_name = data.get('sequence_name', '').strip()

    if not email or not sequence_id or not account_id:
        return jsonify({'status': 'error', 'message': 'Missing required: email, sequence_id, account_id'}), 400

    # Convert plain newlines to HTML for Apollo
    def to_html(text):
        if not text: return ''
        return text.strip().replace('\n\n', '<br><br>').replace('\n', '<br>')

    # Collect personalized fields
    personalized_subject_1 = data.get('personalized_subject_1', '').strip()
    personalized_subject_2 = data.get('personalized_subject_2', '').strip()
    personalized_email_1 = to_html(data.get('personalized_email_1', ''))
    personalized_email_2 = to_html(data.get('personalized_email_2', ''))
    personalized_email_3 = to_html(data.get('personalized_email_3', ''))
    personalized_email_4 = to_html(data.get('personalized_email_4', ''))

    try:
        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}

        # Resolve custom field IDs
        FIELD_ENV_OVERRIDES = {
            'personalized_subject_1': os.environ.get('APOLLO_FIELD_PERSONALIZED_SUBJECT_1', ''),
            'personalized_subject_2': os.environ.get('APOLLO_FIELD_PERSONALIZED_SUBJECT_2', ''),
            'personalized_email_1': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_1', ''),
            'personalized_email_2': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_2', ''),
            'personalized_email_3': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_3', ''),
            'personalized_email_4': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_4', ''),
        }

        field_values = {}
        if personalized_subject_1: field_values['personalized_subject_1'] = personalized_subject_1
        if personalized_subject_2: field_values['personalized_subject_2'] = personalized_subject_2
        if personalized_email_1: field_values['personalized_email_1'] = personalized_email_1
        if personalized_email_2: field_values['personalized_email_2'] = personalized_email_2
        if personalized_email_3: field_values['personalized_email_3'] = personalized_email_3
        if personalized_email_4: field_values['personalized_email_4'] = personalized_email_4

        typed_custom_fields = {}
        if field_values:
            try:
                cf_resp = req.get('https://api.apollo.io/v1/typed_custom_fields',
                                  headers=apollo_headers, timeout=15)
                if cf_resp.status_code == 200:
                    field_id_map = {}
                    for f in cf_resp.json().get('typed_custom_fields', []):
                        fid = f.get('id')
                        name = (f.get('name') or '').lower().replace(' ', '_')
                        if fid and name:
                            field_id_map[name] = fid
                    for k, v in FIELD_ENV_OVERRIDES.items():
                        if v and k not in field_id_map:
                            field_id_map[k] = v
                else:
                    field_id_map = {k: v for k, v in FIELD_ENV_OVERRIDES.items() if v}

                for field_key, field_val in field_values.items():
                    if field_key in field_id_map:
                        typed_custom_fields[field_id_map[field_key]] = field_val
            except Exception as cf_err:
                print(f"[SCORECARD ENROLL] Warning: custom field lookup failed: {cf_err}")
                for field_key, field_val in field_values.items():
                    env_id = FIELD_ENV_OVERRIDES.get(field_key, '')
                    if env_id:
                        typed_custom_fields[env_id] = field_val

        # Search for existing contact
        contact_id = None
        search_resp = req.post('https://api.apollo.io/api/v1/contacts/search',
                               json={'q_keywords': email, 'per_page': 1},
                               headers=apollo_headers, timeout=15)
        if search_resp.status_code == 200:
            contacts = search_resp.json().get('contacts', [])
            if contacts:
                contact_id = contacts[0].get('id')

        # Create or update contact
        if not contact_id:
            create_payload = {
                'first_name': first_name or email.split('@')[0],
                'last_name': last_name or '',
                'email': email,
                'organization_name': company_name,
            }
            if typed_custom_fields:
                create_payload['typed_custom_fields'] = typed_custom_fields
            create_resp = req.post('https://api.apollo.io/v1/contacts',
                                   json=create_payload, headers=apollo_headers, timeout=15)
            if create_resp.status_code in (200, 201):
                contact_id = create_resp.json().get('contact', {}).get('id')
            else:
                return jsonify({'status': 'error', 'message': 'Failed to create Apollo contact'}), 502
        elif typed_custom_fields:
            req.put(f'https://api.apollo.io/v1/contacts/{contact_id}',
                    json={'typed_custom_fields': typed_custom_fields},
                    headers=apollo_headers, timeout=15)

        if not contact_id:
            return jsonify({'status': 'error', 'message': 'Could not find or create contact'}), 500

        # Resolve email account
        email_account_id = None
        preferred_sender = os.environ.get('APOLLO_SENDER_EMAIL', '').strip().lower()
        try:
            ea_resp = req.get('https://api.apollo.io/api/v1/email_accounts',
                              headers=apollo_headers, timeout=15)
            if ea_resp.status_code == 200:
                accounts = ea_resp.json().get('email_accounts', [])
                active = [a for a in accounts if a.get('active')]
                if preferred_sender:
                    match = next((a for a in active if a.get('email', '').lower() == preferred_sender), None)
                    email_account_id = match['id'] if match else (active[0]['id'] if active else None)
                elif active:
                    email_account_id = active[0]['id']
        except Exception:
            pass

        if not email_account_id:
            return jsonify({'status': 'error', 'message': 'No active Apollo email account found'}), 500

        # Enroll in sequence
        enroll_resp = req.post(
            f'https://api.apollo.io/api/v1/emailer_campaigns/{sequence_id}/add_contact_ids',
            json={'emailer_campaign_id': sequence_id, 'contact_ids': [contact_id],
                  'send_email_from_email_account_id': email_account_id},
            headers=apollo_headers, timeout=15
        )

        if enroll_resp.status_code in (200, 201):
            update_scorecard_enrollment(account_id, 'enrolled', sequence_name)
            return jsonify({'status': 'success', 'message': f'Enrolled {email} in sequence', 'contact_id': contact_id})
        else:
            error_msg = enroll_resp.json().get('message', enroll_resp.text[:200]) if enroll_resp.text else 'Unknown error'
            return jsonify({'status': 'error', 'message': f'Failed to enroll: {error_msg}'}), 502

    except Exception as e:
        print(f"[SCORECARD ENROLL ERROR] {e}")
        return jsonify({'status': 'error', 'message': 'Failed to enroll in Apollo sequence'}), 500


@app.route('/api/scorecard/generate-email', methods=['POST'])
def api_scorecard_generate_email():
    """Generate personalized outreach emails for a scored account."""
    from openai import OpenAI

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
    base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')
    if not api_key or not base_url:
        return jsonify({'status': 'error', 'message': 'AI API not configured'}), 400

    company_name = data.get('company_name', '')
    annual_revenue = data.get('annual_revenue', '')
    cohort = data.get('cohort', 'B')
    locale_count = data.get('locale_count', 0)
    systems_json = data.get('systems_json', '{}')
    num_emails = data.get('num_emails', 4)
    bdr_prompt = data.get('bdr_prompt', '').strip()

    # Build structure guidance based on num_emails
    if num_emails == 1:
        structure_guidance = "Structure: subject_1 + email_1 (cold outreach, 3-4 sentences)"
        json_structure = '{"subject_1": "...", "email_1": "..."}'
    elif num_emails == 2:
        structure_guidance = "Structure: subject_1 + email_1 (cold outreach) + email_2 (follow-up bump)"
        json_structure = '{"subject_1": "...", "email_1": "...", "email_2": "..."}'
    elif num_emails == 3:
        structure_guidance = "Structure: subject_1 + email_1 (cold) + email_2 (follow-up) + email_3 (breakup)"
        json_structure = '{"subject_1": "...", "email_1": "...", "email_2": "...", "email_3": "..."}'
    else:
        structure_guidance = "Structure: subject_1 (thread 1) + subject_2 (thread 2) + email_1 + email_2 + email_3 + email_4"
        json_structure = '{"subject_1": "...", "subject_2": "...", "email_1": "...", "email_2": "...", "email_3": "...", "email_4": "..."}'

    # Parse systems for context
    try:
        systems = json.loads(systems_json) if systems_json else {}
    except (json.JSONDecodeError, TypeError):
        systems = {}
    active_systems = [k for k, v in systems.items() if v]

    prompt = f"""You are a BDR at Phrase, a localization/internationalization platform. Write a personalized cold outreach ACCOUNT-BASED sequence.

Account info:
- Company: {company_name}
- Annual Revenue: {annual_revenue}
- Cohort: {cohort} ({'Enterprise $1.5B+' if cohort == 'A' else 'Mid-Market'})
- Languages/Locales detected: {locale_count}
- Systems in use: {', '.join(active_systems) if active_systems else 'unknown'}

{('BDR Instructions: ' + bdr_prompt) if bdr_prompt else ''}

Write a {num_emails}-email cold outreach sequence targeting a VP/Director of Engineering or Localization at this company. The goal is to start a conversation about their internationalization workflow and how Phrase can help.

{structure_guidance}

Rules:
- Reference the company by name and real signals (revenue tier, locale count, systems)
- Each email body: concise, value-driven, references something real
- End each email with a simple CTA
- No fluff. Sound like a human.
- Use \\n for line breaks in email bodies

Return ONLY valid JSON: {json_structure}"""

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "You are a BDR at Phrase. Write diverse, natural emails. Return ONLY valid JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=4096
        )

        response_text = response.choices[0].message.content
        if not response_text:
            return jsonify({'status': 'error', 'message': 'AI returned empty response'}), 500
        response_text = response_text.strip()
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            lines = [l for l in lines if not l.startswith('```')]
            response_text = '\n'.join(lines)

        email_data = json.loads(response_text)
        return jsonify({'status': 'success', **email_data})

    except Exception as e:
        print(f"[SCORECARD EMAIL GEN ERROR] {e}")
        return jsonify({'status': 'error', 'message': f'Email generation failed: {str(e)}'}), 500


@app.route('/sequence')
def sequence_redirect():
    """Redirect /sequence to the campaigns page."""
    return redirect(url_for('campaigns'))


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

    # Get archived accounts count
    archived_count = get_archived_count()

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
        tier_counts=tier_counts,
        archived_count=archived_count
    )


@app.route('/accounts-tabler')
def accounts_tabler():
    """Tabler UI proof-of-concept for the accounts dashboard."""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)
    tiers = request.args.getlist('tier', type=int)
    if not tiers:
        tiers = None
    search_query = request.args.get('q', '').strip()
    if not search_query:
        search_query = None
    result = get_all_accounts(page=page, limit=limit, tier_filter=tiers, search_query=search_query)
    tier_counts = get_tier_counts()
    archived_count = get_archived_count()
    return render_template(
        'accounts_tabler.html',
        accounts=result['accounts'],
        total_items=result['total_items'],
        total_pages=result['total_pages'],
        current_page=result['current_page'],
        limit=result['limit'],
        current_tier_filter=tiers,
        current_search=search_query or '',
        tier_config=TIER_CONFIG,
        tier_counts=tier_counts,
        archived_count=archived_count
    )


@app.route('/api/accounts/scan-statuses')
def api_accounts_scan_statuses():
    """Lightweight endpoint returning only scan status fields for active/recent accounts.

    Used by the 6-second poller instead of fetching all 1000+ full account records.
    Returns accounts that are currently scanning/queued OR were scanned in the last 2 minutes.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, scan_status, scan_start_time, last_scanned_at, next_scan_due, last_scan_error
        FROM monitored_accounts
        WHERE scan_status IS NOT NULL AND scan_status != 'idle'
           OR last_scanned_at >= datetime('now', '-2 minutes')
    ''')
    rows = cursor.fetchall()
    conn.close()
    accounts = []
    for row in rows:
        a = dict(row)
        if a.get('scan_start_time'):
            a['scan_started_at'] = a['scan_start_time']
        if not a.get('scan_status'):
            a['scan_status'] = 'idle'
        accounts.append(a)
    return jsonify({'accounts': accounts})


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

    Supports filters:
    - tier: Multi-select tier filter (0-4)
    - last_scanned: Filter by last scan time ('never', '7d', '30d', '90d', 'older')
    - revenue_min: Minimum revenue in millions
    - revenue_max: Maximum revenue in millions
    """
    # DataTables parameters
    draw = request.args.get('draw', 1, type=int)
    start = request.args.get('start', 0, type=int)
    length = request.args.get('length', 50, type=int)
    search_value = request.args.get('search[value]', '').strip()

    # Get tier filter if provided
    tiers = request.args.getlist('tier', type=int)
    tier_filter = tiers if tiers else None

    # Get last scanned filter
    last_scanned_filter = request.args.get('last_scanned', '').strip() or None

    # Get revenue range filter (in millions)
    revenue_min = request.args.get('revenue_min', type=int)
    revenue_max = request.args.get('revenue_max', type=int)

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
        order_dir=order_dir,
        last_scanned_filter=last_scanned_filter,
        revenue_min=revenue_min,
        revenue_max=revenue_max
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


@app.route('/api/accounts/<int:account_id>/archive', methods=['POST'])
def api_archive_account(account_id: int):
    """Archive an account (hide from main list but retain for periodic re-scan)."""
    archived = archive_account(account_id)
    if not archived:
        return jsonify({'error': 'Account not found or already archived'}), 404
    return jsonify({'status': 'success', 'message': 'Account archived'})


@app.route('/api/accounts/<int:account_id>/unarchive', methods=['POST'])
def api_unarchive_account(account_id: int):
    """Unarchive an account (restore to main accounts list)."""
    unarchived = unarchive_account(account_id)
    if not unarchived:
        return jsonify({'error': 'Account not found or not archived'}), 404
    return jsonify({'status': 'success', 'message': 'Account unarchived'})


@app.route('/api/accounts/archived')
def api_get_archived_accounts():
    """
    Get archived accounts with pagination.

    Query parameters:
        page: Page number (default 1)
        limit: Items per page (default 50)
        search: Search query (optional)

    Returns JSON with archived accounts and pagination info.
    """
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)
    search_query = request.args.get('search', None)

    result = get_archived_accounts(page=page, limit=limit, search_query=search_query)
    return jsonify(result)


@app.route('/api/accounts/archived/count')
def api_get_archived_count():
    """Get the count of archived accounts."""
    count = get_archived_count()
    return jsonify({'count': count})


@app.route('/grow')
def grow():
    """Render the Grow pipeline dashboard."""
    return render_template('grow.html')


@app.route('/settings')
def settings():
    """Settings page with links to tools and configuration."""
    return render_template('settings.html')


@app.route('/rules')
def rules():
    """View the scanning rule set configuration."""
    return render_template('rules.html')


@app.route('/contributors')
def contributors():
    """View top GitHub contributors for BDR outreach."""
    stats = get_contributor_stats()
    return render_template('contributors.html', stats=stats)


@app.route('/api/contributors/datatable', methods=['GET'])
def api_contributors_datatable():
    """Server-side datatable endpoint for contributors."""
    draw = request.args.get('draw', 1, type=int)
    start = request.args.get('start', 0, type=int)
    length = request.args.get('length', 50, type=int)
    search_value = request.args.get('search[value]', '').strip()

    order_column = request.args.get('order[0][column]', 6, type=int)
    order_dir = request.args.get('order[0][dir]', 'desc').lower()

    apollo_filter = request.args.get('apollo_filter', '').strip() or None
    has_email_filter = request.args.get('has_email', '').strip() or None
    warm_hot_filter = request.args.get('warm_hot', '').strip() or None
    i18n_filter = request.args.get('i18n_involved', '').strip() or None
    not_contacted_filter = request.args.get('not_contacted', '').strip() or None

    length = max(1, min(length, 10000))
    start = max(0, start)

    result = get_contributors_datatable(
        draw=draw, start=start, length=length,
        search_value=search_value,
        order_column=order_column, order_dir=order_dir,
        apollo_filter=apollo_filter,
        has_email_filter=has_email_filter,
        warm_hot_filter=warm_hot_filter,
        i18n_filter=i18n_filter,
        not_contacted_filter=not_contacted_filter
    )

    # Enrich results with priority_score and company_tier
    # Build a tier lookup from monitored_accounts
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT LOWER(company_name) as company_lower, current_tier FROM monitored_accounts')
        tier_lookup = {row['company_lower']: row['current_tier'] for row in cursor.fetchall()}
        conn.close()
    except Exception:
        tier_lookup = {}

    tier_names = {0: 'Tracking', 1: 'Thinking', 2: 'Preparing', 3: 'Launched', 4: 'Not Found'}

    for row in result.get('data', []):
        # Filter out personal and domain-mismatched emails before exposing to frontend
        raw_email = row.get('email', '')
        row['email'] = _sanitize_contributor_email(raw_email, row.get('company'))

        company = (row.get('company') or '').lower()
        tier = tier_lookup.get(company)
        row['company_tier'] = tier
        row['company_tier_name'] = tier_names.get(tier, '') if tier is not None else ''

        # Compute priority_score: contributions weight + tier bonus + i18n bonus
        score = min(row.get('contributions', 0), 100)  # cap at 100
        if tier in (1, 2):
            score += 50  # warm/hot account
        if tier == 2:
            score += 25  # preparing = goldilocks
        insight = (row.get('insight') or '').lower()
        if 'i18n' in insight or 'internationalization' in insight or 'locale' in insight:
            score += 20
        row['priority_score'] = score

    # Sort by priority_score descending if no explicit sort requested (default)
    if order_column == 6 and order_dir == 'desc':
        result['data'] = sorted(result['data'], key=lambda x: x.get('priority_score', 0), reverse=True)

    return jsonify(result)


@app.route('/api/contributors/stats')
def api_contributors_stats():
    """Get aggregate contributor stats."""
    stats = get_contributor_stats()
    return jsonify(stats)


@app.route('/api/contributors/<int:contributor_id>/apollo', methods=['POST'])
def api_update_contributor_apollo(contributor_id: int):
    """Update Apollo/sequence enrollment status for a contributor."""
    data = request.get_json() or {}
    status = data.get('status', 'sent')
    sequence_name = data.get('sequence_name', '')

    updated = update_contributor_apollo_status(contributor_id, status, sequence_name)
    if not updated:
        return jsonify({'error': 'Contributor not found'}), 404
    return jsonify({'status': 'success'})


@app.route('/api/contributors/<int:contributor_id>/email', methods=['POST'])
def api_update_contributor_email(contributor_id: int):
    """Save an email address found via Apollo lookup for a contributor."""
    data = request.get_json() or {}
    email = _filter_personal_email(data.get('email', '').strip())
    if not email:
        return jsonify({'error': 'No valid work email provided'}), 400

    updated = update_contributor_email(contributor_id, email)
    if not updated:
        return jsonify({'error': 'Contributor not found'}), 404
    return jsonify({'status': 'success', 'email': email})


@app.route('/api/contributors/<int:contributor_id>/send-email', methods=['POST'])
def api_contributor_send_email(contributor_id: int):
    """Send an outreach email to a contributor via AgentMail."""
    contributor = get_contributor_by_id(contributor_id)
    if not contributor:
        return jsonify({'error': 'Contributor not found'}), 404

    data = request.get_json() or {}
    to_email = data.get('to_email', contributor.get('email', ''))
    subject = data.get('subject', f"Quick question about {contributor.get('company', 'your work')}")
    body = data.get('body', '')

    if not to_email:
        return jsonify({'error': 'No email address available for this contributor'}), 400

    result = send_email_draft(
        to_email=to_email,
        subject=subject,
        body=body,
        company_name=contributor.get('company', contributor.get('name', ''))
    )

    if result.get('success'):
        increment_contributor_emails(contributor_id)

    return jsonify(result)


@app.route('/api/contributors/<int:contributor_id>', methods=['DELETE'])
def api_delete_contributor(contributor_id: int):
    """Delete a contributor."""
    deleted = delete_contributor(contributor_id)
    if not deleted:
        return jsonify({'error': 'Contributor not found'}), 404
    return jsonify({'status': 'success'})


@app.route('/api/contributors/fetch', methods=['POST'])
def api_fetch_contributors():
    """
    Fetch top contributors from GitHub for all scanned accounts (or a specific org).
    Stores them in the contributors table.
    """
    from utils import make_github_request

    data = request.get_json() or {}
    specific_org = data.get('org', '').strip()
    limit_per_repo = data.get('limit', 5)

    conn = get_db_connection()
    cursor = conn.cursor()

    if specific_org:
        cursor.execute('''
            SELECT DISTINCT company_name, github_org, annual_revenue
            FROM monitored_accounts
            WHERE github_org = ? AND archived_at IS NULL
        ''', (specific_org,))
    else:
        cursor.execute('''
            SELECT DISTINCT company_name, github_org, annual_revenue
            FROM monitored_accounts
            WHERE github_org IS NOT NULL AND github_org != '' AND archived_at IS NULL
        ''')

    accounts = [dict(row) for row in cursor.fetchall()]
    conn.close()

    total_saved = 0
    errors = []

    for account in accounts:
        org = account['github_org']
        company = account['company_name']
        revenue = account.get('annual_revenue', '')

        try:
            # Fetch org members list once per org (1 API call) for employee classification
            org_members = set()
            try:
                members_url = f"{Config.GITHUB_API_BASE}/orgs/{org}/members"
                members_resp = make_github_request(members_url, params={'per_page': 100}, timeout=15)
                if members_resp.status_code == 200:
                    for m in members_resp.json():
                        org_members.add(m.get('login', '').lower())
            except Exception as e:
                print(f"[CONTRIBUTORS] Could not fetch members for {org}: {e}")

            url = f"{Config.GITHUB_API_BASE}/orgs/{org}/repos"
            response = make_github_request(url, params={'per_page': 5, 'sort': 'pushed'}, timeout=15)
            if response.status_code != 200:
                continue

            repos = response.json()
            for repo in repos[:3]:
                repo_name = repo['name']
                contributors_list = get_top_contributors(org, repo_name, limit=limit_per_repo)

                batch = []
                for c in contributors_list:
                    # Classify contributor as org member or external
                    is_member = 0
                    login_lower = c['login'].lower()
                    profile_company = c.get('github_profile_company', '')

                    # Signal 1: Direct org membership
                    if login_lower in org_members:
                        is_member = 1

                    # Signal 2: GitHub profile company matches org/company name
                    if not is_member and profile_company:
                        clean = profile_company.lower().strip().lstrip('@')
                        if (org.lower() in clean or company.lower() in clean
                                or clean in org.lower() or clean in company.lower()):
                            is_member = 1

                    batch.append({
                        'github_login': c['login'],
                        'github_url': c.get('github_url', f"https://github.com/{c['login']}"),
                        'name': c.get('name', c['login']),
                        'email': _filter_personal_email(c.get('email', '')),
                        'blog': c.get('blog', ''),
                        'company': company,
                        'annual_revenue': revenue,
                        'repo_source': f"{org}/{repo_name}",
                        'github_org': org,
                        'contributions': c.get('contributions', 0),
                        'insight': f"Top contributor to {org}/{repo_name} — active in {company}'s codebase.",
                        'is_org_member': is_member,
                        'github_profile_company': profile_company
                    })

                if batch:
                    saved = save_contributors_batch(batch)
                    total_saved += saved

        except Exception as e:
            errors.append(f"{org}: {str(e)}")

    return jsonify({
        'status': 'success',
        'accounts_processed': len(accounts),
        'contributors_saved': total_saved,
        'errors': errors
    })


@app.route('/api/contributors/generate-email', methods=['POST'])
def api_generate_contributor_email():
    """Generate a personalized outreach email for a contributor using OpenAI GPT-5 mini."""
    from openai import OpenAI

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
    base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')
    if not api_key or not base_url:
        return jsonify({'status': 'error', 'message': 'OpenAI API key not configured'}), 400

    name = data.get('name', 'there')
    first_name = name.split(' ')[0] if name else 'there'
    github_login = data.get('github_login', '')
    company = data.get('company', '')
    repo_source = data.get('repo_source', '')
    insight = data.get('insight', '')
    contributions = data.get('contributions', 0)
    goldilocks_status = data.get('goldilocks_status', '')

    num_emails = data.get('num_emails', 4)

    # Build dynamic prompt structure based on num_emails
    if num_emails == 1:
        structure_guidance = """Structure:
- subject: Subject line
- email_1: Cold outreach email (3-4 sentences). Reference their GitHub activity."""
        json_structure = """{{\n  "subject_1": "subject line",\n  "email_1": "email body (use \\\\n for line breaks)"\n}}"""
    elif num_emails == 2:
        structure_guidance = """Structure:
- subject_1: Subject line
- email_1: Initial cold outreach (3-4 sentences). Reference their GitHub activity.
- email_2: Follow-up bump (2-3 sentences). Add a new angle."""
        json_structure = """{{\n  "subject_1": "subject line",\n  "email_1": "first email body (use \\\\n for line breaks)",\n  "email_2": "follow-up (use \\\\n for line breaks)"\n}}"""
    elif num_emails == 3:
        structure_guidance = """Structure:
- subject_1: Subject line for all 3 emails
- email_1: Initial cold outreach (3-4 sentences). Reference their GitHub activity.
- email_2: Follow-up bump (2-3 sentences). Add a new angle or value prop.
- email_3: Final breakup email (2-3 sentences). Light, low-pressure."""
        json_structure = """{{\n  "subject_1": "subject line",\n  "email_1": "first email (use \\\\n for line breaks)",\n  "email_2": "follow-up (use \\\\n for line breaks)",\n  "email_3": "breakup email (use \\\\n for line breaks)"\n}}"""
    else:
        structure_guidance = """Structure:
- subject_1: Main subject thread (emails 1 & 2 reply under this subject)
- subject_2: New subject angle for follow-ups (emails 3 & 4 reply under this subject)
- email_1: Initial cold outreach (3-4 sentences). Reference their GitHub activity.
- email_2: Follow-up bump (2-3 sentences). Add a new angle or value prop.
- email_3: New thread with different angle (3-4 sentences). Reference a different pain point.
- email_4: Final breakup email (2-3 sentences). Light, low-pressure."""
        json_structure = """{{\n  "subject_1": "main subject line",\n  "subject_2": "second subject line",\n  "email_1": "first email (use \\\\n for line breaks)",\n  "email_2": "follow-up bump (use \\\\n for line breaks)",\n  "email_3": "new thread email (use \\\\n for line breaks)",\n  "email_4": "breakup email (use \\\\n for line breaks)"\n}}"""

    # Temperature-aware tone guidance
    tone_guidance = ''
    if goldilocks_status == 'preparing':
        tone_guidance = '\nTone: URGENT — this company is actively setting up i18n infrastructure right now. Create urgency, reference their recent activity, and push for an immediate meeting. They are in the Goldilocks window.'
    elif goldilocks_status == 'thinking':
        tone_guidance = '\nTone: NURTURE — this company shows early interest in localization. Be helpful and educational. Position yourself as a trusted advisor. Offer value without being pushy.'
    elif goldilocks_status == 'launched':
        tone_guidance = '\nTone: LOW PRIORITY — this company already has localization in place. Keep it light. Focus on potential pain points with their current solution or future scaling needs.'
    else:
        tone_guidance = '\nTone: EDUCATIONAL — this is a cold lead with no clear i18n signals yet. Focus on education about the market opportunity and plant seeds for when they do start thinking about localization.'

    prompt = f"""You are a BDR (Business Development Rep) at Phrase, a localization/internationalization platform. Write a personalized cold outreach email SEQUENCE to a software contributor.

Contact info:
- Name: {name}
- First name: {first_name}
- GitHub: {github_login}
- Company: {company}
- Active repo: {repo_source}
- Contributions: {contributions}
- Insight: {insight}
- Lead temperature: {goldilocks_status or 'unknown'}
{tone_guidance}

Write a {num_emails}-email cold outreach sequence. The goal is to start a conversation about their internationalization/localization (i18n) workflow and how Phrase can help their engineering team ship to global markets faster.

{structure_guidance}

Rules:
- Each email body: concise, specific, references something real about them
- End each email with a simple CTA
- No fluff, no "I hope this email finds you well"
- Sound like a human, not a robot
- Use their first name
- Use the actual contact's name and company in the email. Do NOT use template variables like {{{{company}}}}, {{{{name}}}}, or {{{{first_name}}}}.

Return ONLY valid JSON with no markdown formatting:
{json_structure}"""

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "You are a BDR at Phrase, a localization platform. Write diverse, natural-sounding emails. Vary your sentence structure and word choice. Return ONLY valid JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=4096
        )

        response_text = response.choices[0].message.content
        if not response_text:
            print(f"[CONTRIBUTOR EMAIL GEN] Empty response from AI. Finish reason: {response.choices[0].finish_reason}")
            return jsonify({'status': 'error', 'message': 'AI returned empty response. Please try again.'}), 500
        response_text = response_text.strip()
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            lines = [l for l in lines if not l.startswith('```')]
            response_text = '\n'.join(lines)

        email_data = json.loads(response_text)

        merge_replacements = {
            '{{company}}': company, '{{name}}': name, '{{first_name}}': first_name,
            '{{ company }}': company, '{{ name }}': name, '{{ first_name }}': first_name,
        }
        for key in email_data:
            if isinstance(email_data[key], str):
                for tag, val in merge_replacements.items():
                    if tag and val and tag in email_data[key]:
                        email_data[key] = email_data[key].replace(tag, val)

        return jsonify({'status': 'success', 'email': email_data})

    except Exception as e:
        print(f"[CONTRIBUTOR EMAIL GEN] Error ({type(e).__name__}): {e}")
        return jsonify({'status': 'error', 'message': sanitize_ai_error(e)}), 500


@app.route('/experiment')
def experiment():
    """WebScraper - Analyze websites using natural language prompts."""
    return render_template('webscraper.html')


@app.route('/webscraper-accounts')
def webscraper_accounts():
    """WebScraper Accounts - Scalable website localization analysis."""
    return render_template('webscraper_accounts.html')


@app.route('/api/webscraper/analyze', methods=['POST'])
def api_webscraper_analyze():
    """
    Analyze a website using a natural language prompt.

    Request JSON:
        url: Website URL to analyze (required)
        prompt: Natural language prompt describing what to analyze (required)

    Returns JSON with analysis results.
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'Invalid JSON payload'}), 400

        url = data.get('url', '').strip()
        prompt = data.get('prompt', '').strip()

        if not url:
            return jsonify({'error': 'Missing required field: url'}), 400

        if not prompt:
            return jsonify({'error': 'Missing required field: prompt'}), 400

        # Analyze the website
        result = analyze_website(url, prompt)

        if not result.get('success'):
            error_msg = result.get('error', 'Analysis failed')
            return jsonify({'error': error_msg}), 500

        return jsonify(result), 200

    except Exception as e:
        print(f"[ERROR] Website analysis failed: {e}")
        return jsonify({'error': 'Analysis failed'}), 500


@app.route('/api/webscraper/accounts-with-websites', methods=['GET'])
def api_webscraper_accounts():
    """
    Get all monitored accounts that have websites.

    Query parameters:
        include_analyzed: Include accounts that already have analyses (default: false)

    Returns JSON list of accounts with websites.
    """
    try:
        include_analyzed = request.args.get('include_analyzed', 'false').lower() == 'true'
        accounts = get_accounts_with_websites(include_analyzed=include_analyzed)

        return jsonify({
            'success': True,
            'count': len(accounts),
            'accounts': accounts
        }), 200

    except Exception as e:
        print(f"[ERROR] Failed to fetch accounts with websites: {e}")
        return jsonify({'error': 'Failed to fetch accounts'}), 500


@app.route('/api/webscraper/analyze-batch', methods=['POST'])
def api_webscraper_analyze_batch():
    """
    Analyze multiple websites in batch (technical analysis only, no AI prompts).

    Request JSON:
        account_ids: List of account IDs to analyze

    Returns JSON with batch analysis results.
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'Invalid JSON payload'}), 400

        account_ids = data.get('account_ids', [])

        if not account_ids:
            return jsonify({'error': 'Missing required field: account_ids'}), 400

        # Get accounts
        conn = get_db_connection()
        cursor = conn.cursor()

        placeholders = ','.join('?' * len(account_ids))
        cursor.execute(
            'SELECT id, company_name, website FROM monitored_accounts WHERE id IN (' + placeholders + ') AND website IS NOT NULL AND website != \'\'',
            account_ids)

        accounts = [dict(row) for row in cursor.fetchall()]
        conn.close()

        results = []

        for account in accounts:
            try:
                # Perform technical analysis only
                analysis_result = analyze_website_technical(account['website'])

                if analysis_result.get('success'):
                    # Save to database
                    analysis_id = save_website_analysis(
                        company_name=account['company_name'],
                        website_url=analysis_result['url'],
                        localization_score=analysis_result['localization_score'],
                        quality_metrics=analysis_result['quality_metrics'],
                        tech_stack=analysis_result['tech_stack'],
                        account_id=account['id']
                    )

                    results.append({
                        'account_id': account['id'],
                        'company_name': account['company_name'],
                        'website': account['website'],
                        'success': True,
                        'analysis_id': analysis_id,
                        'localization_score': analysis_result['localization_score']['score'],
                        'quality_score': analysis_result['quality_metrics']['overall_score']
                    })
                else:
                    results.append({
                        'account_id': account['id'],
                        'company_name': account['company_name'],
                        'website': account['website'],
                        'success': False,
                        'error': analysis_result.get('error', 'Analysis failed')
                    })

            except Exception as e:
                results.append({
                    'account_id': account['id'],
                    'company_name': account['company_name'],
                    'website': account['website'],
                    'success': False,
                    'error': str(e)
                })

        return jsonify({
            'success': True,
            'total': len(results),
            'succeeded': sum(1 for r in results if r['success']),
            'failed': sum(1 for r in results if not r['success']),
            'results': results
        }), 200

    except Exception as e:
        print(f"[ERROR] Batch analysis failed: {e}")
        return jsonify({'error': 'Batch analysis failed'}), 500


@app.route('/api/webscraper/analyses', methods=['GET'])
def api_webscraper_analyses():
    """
    Get all website analyses.

    Query parameters:
        limit: Maximum number of results (default: 100)
        offset: Number of results to skip (default: 0)

    Returns JSON list of analyses.
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)

        analyses = get_all_website_analyses(limit=limit, offset=offset)

        return jsonify({
            'success': True,
            'count': len(analyses),
            'analyses': analyses
        }), 200

    except Exception as e:
        print(f"[ERROR] Failed to fetch analyses: {e}")
        return jsonify({'error': 'Failed to fetch analyses'}), 500


@app.route('/api/webscraper/analysis/<int:analysis_id>', methods=['GET'])
def api_webscraper_analysis_detail(analysis_id):
    """
    Get detailed website analysis by ID.

    Returns JSON with full analysis data.
    """
    try:
        analysis = get_website_analysis(analysis_id)

        if not analysis:
            return jsonify({'error': 'Analysis not found'}), 404

        return jsonify({
            'success': True,
            'analysis': analysis
        }), 200

    except Exception as e:
        print(f"[ERROR] Failed to fetch analysis {analysis_id}: {e}")
        return jsonify({'error': 'Failed to fetch analysis'}), 500


@app.route('/api/webscraper/analysis/<int:analysis_id>', methods=['DELETE'])
def api_webscraper_analysis_delete(analysis_id):
    """
    Delete a website analysis.

    Returns JSON with success status.
    """
    try:
        deleted = delete_website_analysis(analysis_id)

        if not deleted:
            return jsonify({'error': 'Analysis not found'}), 404

        return jsonify({
            'success': True,
            'message': 'Analysis deleted'
        }), 200

    except Exception as e:
        print(f"[ERROR] Failed to delete analysis: {e}")
        return jsonify({'error': 'Failed to delete analysis'}), 500


# =============================================================================
# WEBSCRAPER ACCOUNTS API - Scalable website localization analysis
# =============================================================================

@app.route('/api/webscraper/accounts/datatable', methods=['GET', 'POST'])
def api_webscraper_accounts_datatable():
    """
    DataTables server-side processing endpoint for WebScraper accounts.

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
    length = max(1, min(length, 10000))
    start = max(0, start)

    # Get data from database
    result = get_webscraper_accounts_datatable(
        draw=draw,
        start=start,
        length=length,
        search_value=search_value,
        tier_filter=tier_filter,
        order_column=order_column,
        order_dir=order_dir
    )

    return jsonify(result)


@app.route('/api/webscraper/accounts/tier-counts')
def api_webscraper_tier_counts():
    """
    Get counts of webscraper accounts per tier.

    If the webscraper_accounts table is empty, this will auto-populate it
    with existing RepoRadar accounts that have websites.

    Returns:
        JSON with tier counts: {"1": 5, "2": 12, "3": 45, "4": 238, "archived": 3}
    """
    # Auto-populate from RepoRadar if webscraper accounts table is empty
    # This provides a baseline of existing accounts with websites
    if is_webscraper_accounts_empty():
        populate_webscraper_from_reporadar()

    counts = get_webscraper_tier_counts()
    return jsonify(counts)


@app.route('/api/webscraper/accounts/populate', methods=['POST'])
def api_webscraper_populate():
    """
    Populate webscraper_accounts from monitored_accounts (RepoRadar).

    This migration creates webscraper account entries for all RepoRadar accounts
    that don't already have one. All new accounts are set to Tier 4 (Not Scanned).

    Returns:
        JSON with migration results: {created: int, skipped: int, errors: int}
    """
    try:
        result = populate_webscraper_from_reporadar()
        return jsonify({
            'success': True,
            'message': f"Created {result['created']} accounts, skipped {result['skipped']}, errors: {result['errors']}",
            **result
        })
    except Exception as e:
        print(f"[ERROR] Failed to create webscraper accounts: {e}")
        return jsonify({'error': 'Failed to create accounts'}), 500


@app.route('/api/webscraper/accounts/populate-from-reporadar', methods=['POST'])
def api_webscraper_populate_from_reporadar():
    """
    Populate webscraper accounts from RepoRadar accounts that have websites.
    """
    try:
        result = populate_webscraper_from_reporadar()
        return jsonify({
            'success': True,
            'added': result.get('created', 0),
            'skipped': result.get('skipped', 0)
        })
    except Exception as e:
        print(f"[ERROR] Failed to populate from RepoRadar: {e}")
        return jsonify({'error': 'Failed to populate accounts'}), 500


@app.route('/api/webscraper/ruleset')
def api_webscraper_ruleset():
    """
    Get the current WebScraper rule set based on scanner.py heuristics.

    This endpoint returns the detection patterns and classification rules
    used by the WebScraper to analyze websites for localization and
    global expansion signals. Rules are refreshed every 24 hours.
    """
    from config import Config
    from datetime import datetime

    # Get rule set last updated from system settings
    last_updated = get_setting('webscraper_ruleset_updated')
    if not last_updated:
        # Initialize on first call
        last_updated = datetime.now().isoformat()
        set_setting('webscraper_ruleset_updated', last_updated)

    # Check if 24 hours have passed
    try:
        last_dt = datetime.fromisoformat(last_updated)
        if (datetime.now() - last_dt).total_seconds() > 86400:  # 24 hours
            last_updated = datetime.now().isoformat()
            set_setting('webscraper_ruleset_updated', last_updated)
    except (ValueError, TypeError):
        last_updated = datetime.now().isoformat()
        set_setting('webscraper_ruleset_updated', last_updated)

    # Build rule set from scanner.py heuristics
    ruleset = {
        'expansion_keywords': getattr(Config, 'RFC_HIGH_INTENT_PHRASES', [])[:20],
        'locale_patterns': [
            '/[a-z]{2}/', '/[a-z]{2}-[A-Z]{2}/',
            'hreflang="*"', 'lang="*"',
            '/locales/', '/i18n/', '/translations/',
            'formatMessage', 'useTranslation', 't()',
            'Crowdin', 'Transifex', 'Lokalise', 'Phrase'
        ],
        'tier_rules': {
            '1': 'Global Leader - 10+ supported locales with mature infrastructure',
            '2': 'Active Expansion - 5-10 locales, actively adding new markets',
            '3': 'Going Global - 2-4 locales, early expansion phase',
            '4': 'Not Yet Global - English only, no localization detected'
        },
        'smoking_gun_libs': getattr(Config, 'SMOKING_GUN_LIBS', [])[:15],
        'last_updated': last_updated,
        'update_frequency': '24 hours'
    }

    return jsonify(ruleset)


@app.route('/api/webscraper/history')
def api_webscraper_history():
    """
    Get recent WebScraper scan history.

    Returns the most recent scans with their results for display in the History modal.
    """
    limit = request.args.get('limit', 20, type=int)
    limit = min(limit, 100)  # Cap at 100

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT
            company_name,
            current_tier as tier,
            locale_count,
            localization_coverage_score as score,
            evidence_summary as evidence,
            last_scanned_at as scanned_at
        FROM webscraper_accounts
        WHERE last_scanned_at IS NOT NULL
        ORDER BY last_scanned_at DESC
        LIMIT ?
    ''', (limit,))

    rows = cursor.fetchall()
    conn.close()

    history = []
    for row in rows:
        history.append({
            'company_name': row['company_name'],
            'tier': row['tier'] or 4,
            'locale_count': row['locale_count'] or 0,
            'score': row['score'],
            'evidence': row['evidence'],
            'scanned_at': row['scanned_at']
        })

    return jsonify(history)


@app.route('/api/webscraper/accounts/scan/<int:account_id>', methods=['POST'])
def api_webscraper_scan_account(account_id):
    """
    Scan a webscraper account's website for localization and global expansion signals.

    This endpoint performs a comprehensive website analysis including:
    - Technical localization detection (hreflang tags, i18n libraries)
    - Global expansion signal detection
    - Tier classification based on localization maturity

    Returns:
        JSON with scan results including tier, signals, and evidence
    """
    # Verify account exists
    account = get_webscraper_account(account_id)
    if not account:
        return jsonify({'error': 'Account not found'}), 404

    website_url = account.get('website_url')
    if not website_url:
        return jsonify({'error': 'Account has no website URL'}), 400

    try:
        # Perform technical website analysis
        from monitors.web_analyzer import WebAnalyzer
        analyzer = WebAnalyzer()
        website_data = analyzer.fetch_website(website_url)

        # Detect global expansion signals
        expansion_signals = detect_expansion_signals(website_data)

        # Extract localization metrics
        localization_score = website_data.get('localization_score', {})
        tech_stack = website_data.get('tech_stack', {})
        hreflang_tags = website_data.get('hreflang_tags', [])

        # Build scan results for tier calculation
        scan_results = {
            'locale_count': len(hreflang_tags),
            'languages_detected': [tag.get('hreflang', '') for tag in hreflang_tags],
            'hreflang_tags': hreflang_tags,
            'i18n_libraries': tech_stack.get('i18n_libs', []) + localization_score.get('details', {}).get('i18n_libraries', []),
            'expansion_signals': expansion_signals,
            'has_language_switcher': localization_score.get('details', {}).get('language_switcher', False),
        }

        # Calculate tier and scores
        tier_info = extract_tier_from_scan_results(scan_results)

        # Generate evidence summary
        evidence_summary = generate_evidence_summary(scan_results, expansion_signals)

        # Prepare final results for database
        final_results = {
            'tier': tier_info['tier'],
            'tier_label': tier_info['tier_label'],
            'localization_coverage_score': tier_info['localization_coverage_score'],
            'quality_gap_score': tier_info['quality_gap_score'],
            'enterprise_score': tier_info.get('enterprise_score', 0),
            'locale_count': tier_info['locale_count'],
            'languages_detected': tier_info['languages_detected'],
            'hreflang_tags': tier_info['hreflang_tags'],
            'i18n_libraries': list(set(tier_info['i18n_libraries'])),  # Dedupe
            'signals_json': expansion_signals,
            'evidence_summary': evidence_summary,
        }

        # Save results to database
        update_webscraper_scan_results(account_id, final_results)

        return jsonify({
            'status': 'completed',
            'message': 'Scan completed successfully',
            'account_id': account_id,
            'company_name': account['company_name'],
            'results': {
                'tier': final_results['tier'],
                'tier_label': final_results['tier_label'],
                'locale_count': final_results['locale_count'],
                'localization_score': final_results['localization_coverage_score'],
                'evidence': evidence_summary,
                'expansion_signals': {
                    'is_first_time_global': expansion_signals.get('is_first_time_global', False),
                    'is_actively_expanding': expansion_signals.get('is_actively_expanding', False),
                    'expansion_score': expansion_signals.get('expansion_score', 0),
                    'detected_intent': expansion_signals.get('detected_intent', []),
                    'new_markets': expansion_signals.get('new_markets', []),
                }
            }
        })

    except Exception as e:
        # Save error to database
        update_webscraper_scan_results(account_id, {
            'tier': 4,
            'tier_label': 'Not Yet Global',
            'scan_error': str(e),
            'evidence_summary': f'Scan failed: {str(e)}'
        })

        return jsonify({
            'status': 'error',
            'message': f'Scan failed: {str(e)}',
            'account_id': account_id,
            'company_name': account['company_name']
        }), 500


@app.route('/api/webscraper/accounts/bulk', methods=['POST'])
def api_webscraper_bulk_action():
    """
    Perform bulk actions on webscraper accounts.

    Request JSON:
        action: One of 'archive', 'unarchive', 'delete', 'change_tier'
        account_ids: List of account IDs
        tier: Required if action is 'change_tier' (1-4)

    Returns:
        JSON with result: {success: bool, affected: int}
    """
    try:
        data = request.get_json() or {}
        action = data.get('action', '').lower()
        account_ids = data.get('account_ids', [])

        if not action:
            return jsonify({'error': 'Missing required field: action'}), 400

        if not account_ids:
            return jsonify({'error': 'Missing required field: account_ids'}), 400

        if action == 'archive':
            affected = webscraper_bulk_archive(account_ids)
            return jsonify({'success': True, 'action': 'archive', 'affected': affected})

        elif action == 'unarchive':
            # Unarchive one by one
            affected = 0
            for aid in account_ids:
                if unarchive_webscraper_account(aid):
                    affected += 1
            return jsonify({'success': True, 'action': 'unarchive', 'affected': affected})

        elif action == 'delete':
            affected = webscraper_bulk_delete(account_ids)
            return jsonify({'success': True, 'action': 'delete', 'affected': affected})

        elif action == 'change_tier':
            new_tier = data.get('tier')
            if new_tier is None:
                return jsonify({'error': 'Missing required field: tier'}), 400
            if new_tier not in [1, 2, 3, 4]:
                return jsonify({'error': 'Invalid tier. Must be 1, 2, 3, or 4'}), 400
            affected = webscraper_bulk_change_tier(account_ids, new_tier)
            return jsonify({'success': True, 'action': 'change_tier', 'tier': new_tier, 'affected': affected})

        elif action == 'scan':
            # Perform bulk scanning with progress tracking
            results = {
                'completed': 0,
                'failed': 0,
                'skipped': 0,
                'details': []
            }

            from monitors.web_analyzer import WebAnalyzer
            analyzer = WebAnalyzer()

            for aid in account_ids:
                account = get_webscraper_account(aid)
                if not account:
                    results['skipped'] += 1
                    results['details'].append({'id': aid, 'status': 'skipped', 'reason': 'Account not found'})
                    continue

                website_url = account.get('website_url')
                if not website_url:
                    results['skipped'] += 1
                    results['details'].append({'id': aid, 'status': 'skipped', 'reason': 'No website URL'})
                    continue

                try:
                    # Perform scan
                    website_data = analyzer.fetch_website(website_url)
                    expansion_signals = detect_expansion_signals(website_data)

                    # Extract metrics
                    localization_score = website_data.get('localization_score', {})
                    tech_stack = website_data.get('tech_stack', {})
                    hreflang_tags = website_data.get('hreflang_tags', [])

                    scan_results = {
                        'locale_count': len(hreflang_tags),
                        'languages_detected': [tag.get('hreflang', '') for tag in hreflang_tags],
                        'hreflang_tags': hreflang_tags,
                        'i18n_libraries': tech_stack.get('i18n_libs', []) + localization_score.get('details', {}).get('i18n_libraries', []),
                        'expansion_signals': expansion_signals,
                    }

                    tier_info = extract_tier_from_scan_results(scan_results)
                    evidence_summary = generate_evidence_summary(scan_results, expansion_signals)

                    final_results = {
                        'tier': tier_info['tier'],
                        'tier_label': tier_info['tier_label'],
                        'localization_coverage_score': tier_info['localization_coverage_score'],
                        'quality_gap_score': tier_info['quality_gap_score'],
                        'enterprise_score': tier_info.get('enterprise_score', 0),
                        'locale_count': tier_info['locale_count'],
                        'languages_detected': tier_info['languages_detected'],
                        'hreflang_tags': tier_info['hreflang_tags'],
                        'i18n_libraries': list(set(tier_info['i18n_libraries'])),
                        'signals_json': expansion_signals,
                        'evidence_summary': evidence_summary,
                    }

                    update_webscraper_scan_results(aid, final_results)
                    results['completed'] += 1
                    results['details'].append({
                        'id': aid,
                        'status': 'completed',
                        'tier': tier_info['tier'],
                        'tier_label': tier_info['tier_label']
                    })

                except Exception as e:
                    results['failed'] += 1
                    results['details'].append({'id': aid, 'status': 'failed', 'reason': str(e)})
                    # Save error state
                    update_webscraper_scan_results(aid, {
                        'tier': 4,
                        'tier_label': 'Not Yet Global',
                        'scan_error': str(e),
                        'evidence_summary': f'Scan failed: {str(e)}'
                    })

            return jsonify({
                'success': True,
                'action': 'scan',
                'completed': results['completed'],
                'failed': results['failed'],
                'skipped': results['skipped'],
                'total': len(account_ids),
                'details': results['details']
            })

        else:
            return jsonify({'error': f'Unknown action: {action}'}), 400

    except Exception as e:
        print(f"[ERROR] Bulk action failed: {e}")
        return jsonify({'error': 'Bulk action failed'}), 500


@app.route('/api/webscraper/accounts/<int:account_id>/notes', methods=['PUT'])
def api_webscraper_update_notes(account_id):
    """
    Update the notes field for a webscraper account.

    Request JSON:
        notes: The new notes text

    Returns:
        JSON with result: {success: bool, notes: str}
    """
    data = request.get_json() or {}
    notes = data.get('notes', '')

    updated = update_webscraper_notes(account_id, notes)
    if not updated:
        return jsonify({'error': 'Account not found'}), 404

    return jsonify({'success': True, 'notes': notes})


@app.route('/api/webscraper/accounts/<int:account_id>/archive', methods=['POST'])
def api_webscraper_archive_account(account_id):
    """Archive a webscraper account."""
    archived = archive_webscraper_account(account_id)
    if not archived:
        return jsonify({'error': 'Account not found or already archived'}), 404
    return jsonify({'success': True, 'message': 'Account archived'})


@app.route('/api/webscraper/accounts/<int:account_id>/unarchive', methods=['POST'])
def api_webscraper_unarchive_account(account_id):
    """Unarchive a webscraper account."""
    unarchived = unarchive_webscraper_account(account_id)
    if not unarchived:
        return jsonify({'error': 'Account not found or not archived'}), 404
    return jsonify({'success': True, 'message': 'Account unarchived'})


@app.route('/api/webscraper/accounts/<int:account_id>', methods=['DELETE'])
def api_webscraper_delete_account(account_id):
    """Delete a webscraper account."""
    deleted = delete_webscraper_account(account_id)
    if not deleted:
        return jsonify({'error': 'Account not found'}), 404
    return jsonify({'success': True, 'message': 'Account deleted'})


@app.route('/api/webscraper/accounts/<int:account_id>')
def api_webscraper_get_account(account_id):
    """Get a single webscraper account by ID."""
    account = get_webscraper_account(account_id)
    if not account:
        return jsonify({'error': 'Account not found'}), 404
    return jsonify({'success': True, 'account': account})


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
        print(f"[ERROR] Lead stream failed: {e}")
        return jsonify({'error': 'Failed to generate lead stream'}), 500


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
    Optional: {"skip_duplicates": true} to automatically skip detected duplicates

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
    skip_duplicates = data.get('skip_duplicates', False)

    if not isinstance(companies, list) or not companies:
        return jsonify({'error': 'Invalid payload: expected {"companies": [...]}'}), 400

    # For large imports (> 100 companies), skip synchronous duplicate checking
    # The batch worker will handle duplicates during processing
    # This prevents request timeouts on large CSV imports
    if len(companies) > 100:
        # Just normalize the companies list without duplicate checking
        filtered_companies = []
        for company_item in companies:
            if isinstance(company_item, dict):
                company_name = company_item.get('name', '').strip()
            else:
                company_name = str(company_item).strip()
            if company_name:
                filtered_companies.append(company_item)
        
        if not filtered_companies:
            return jsonify({
                'batch_id': None,
                'total_count': 0,
                'status': 'skipped',
                'message': 'No valid companies to import',
            })
    else:
        # For smaller imports, do synchronous duplicate checking
        from database import find_potential_duplicates
        
        filtered_companies = []
        skipped_duplicates = []

        for company_item in companies:
            if isinstance(company_item, dict):
                company_name = company_item.get('name', '').strip()
                github_org = company_item.get('github_org', '').strip() if company_item.get('github_org') else None
                website = company_item.get('website', '').strip() if company_item.get('website') else None
            else:
                company_name = str(company_item).strip()
                github_org = None
                website = None

            if not company_name:
                continue

            if skip_duplicates:
                duplicates = find_potential_duplicates(company_name, github_org, website)
                # Only block import for 100% confidence matches (exact name, github org, website domain)
                confirmed_duplicates = [d for d in duplicates if d.get('match_confidence', 0) >= 100]
                if confirmed_duplicates:
                    skipped_duplicates.append({
                        'company': company_name,
                        'existing_match': confirmed_duplicates[0].get('company_name'),
                        'match_reason': confirmed_duplicates[0].get('match_reason'),
                    })
                    continue

            filtered_companies.append(company_item)

        if not filtered_companies:
            return jsonify({
                'batch_id': None,
                'total_count': 0,
                'status': 'skipped',
                'message': 'All companies were duplicates - nothing to import',
                'skipped_duplicates': skipped_duplicates if skip_duplicates else [],
            })

    # Create persistent batch in database with filtered companies
    batch_id = create_import_batch(filtered_companies)
    print(f"[IMPORT] Created batch {batch_id} with {len(filtered_companies)} companies")

    # Submit batch to thread pool for background processing
    executor = get_executor()
    executor.submit(process_import_batch_worker, batch_id)
    print(f"[EXECUTOR] Submitted batch {batch_id} for processing")

    response = {
        'batch_id': batch_id,
        'total_count': len(filtered_companies),
        'status': 'queued',
        'message': 'Import batch created and queued for processing',
    }

    # Only include skipped_duplicates for small imports (where we did synchronous checking)
    if len(companies) <= 100 and skip_duplicates and skipped_duplicates:
        response['skipped_duplicates'] = skipped_duplicates
        response['skipped_count'] = len(skipped_duplicates)

    return jsonify(response)


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


@app.route('/api/import/check-duplicates', methods=['POST'])
def api_check_import_duplicates():
    """
    Check a list of companies for potential duplicates before import.

    This endpoint performs smart duplicate detection including:
    - Exact case-insensitive name matches
    - Fuzzy name matches (removing Inc, LLC, Corp suffixes)
    - GitHub org matches
    - Website domain matches

    Expects JSON payload: {"companies": ["Shopify", "Stripe", ...]}
    Or with metadata: {"companies": [{"name": "Shopify", "github_org": "shopify", "website": "shopify.com"}, ...]}

    Returns:
        JSON with: {
            "total": <int>,
            "duplicates": <int>,
            "new": <int>,
            "details": [
                {
                    "company": "Shopify Inc",
                    "matches": [
                        {
                            "existing_name": "Shopify",
                            "match_type": "similar_name",
                            "match_confidence": "medium",
                            "match_detail": "'Shopify Inc' matches 'Shopify' (normalized)"
                        }
                    ]
                }
            ]
        }
    """
    data = request.get_json() or {}
    companies = data.get('companies', [])

    if not isinstance(companies, list) or not companies:
        return jsonify({'error': 'Invalid payload: expected {"companies": [...]}'}), 400

    try:
        results = get_import_duplicates_summary(companies)
        return jsonify(results)
    except Exception as e:
        print(f"[ERROR] Failed to check duplicates: {e}")
        return jsonify({'error': 'Failed to check duplicates'}), 500


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
        print(f"[ERROR] Failed to track organization {org_login}: {e}")
        return jsonify({'error': 'Failed to track organization'}), 500


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

        # Update the github_org for this account (preserve existing tier and evidence)
        cursor.execute('''
            UPDATE monitored_accounts
            SET github_org = ?
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
        print(f"[ERROR] Failed to update org for {company_name}: {e}")
        return jsonify({'status': 'error', 'message': 'Failed to update organization'}), 500
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


# =============================================================================
# BATCH RESCAN ORCHESTRATOR — Processes accounts in chunks with delays
# =============================================================================

def _batch_rescan_worker(accounts, batch_size, delay_seconds):
    """
    Background worker that processes accounts in batches.

    Chunks accounts into groups of batch_size, submits each batch to the
    thread pool, waits for the batch to drain, then sleeps before the next.
    """
    import math
    state = _batch_rescan_state
    total_batches = math.ceil(len(accounts) / batch_size)
    state['total_batches'] = total_batches

    try:
        for batch_idx in range(total_batches):
            if state['cancelled']:
                print(f"[BATCH-RESCAN] Cancelled after batch {batch_idx}/{total_batches}")
                break

            batch_start = batch_idx * batch_size
            batch = accounts[batch_start:batch_start + batch_size]
            state['current_batch'] = batch_idx + 1
            batch_names = [a['company_name'] for a in batch if a.get('company_name')]

            print(f"[BATCH-RESCAN] Starting batch {batch_idx + 1}/{total_batches} ({len(batch_names)} accounts)")

            # Queue the batch in a single DB transaction
            batch_set_scan_status_queued(batch_names)

            # Submit each to the thread pool
            for name in batch_names:
                spawn_background_scan(name)

            # Poll until this batch drains from queued/processing (timeout: 30 min)
            poll_start = time.time()
            max_poll_seconds = 30 * 60
            while True:
                if state['cancelled']:
                    break
                if time.time() - poll_start > max_poll_seconds:
                    print(f"[BATCH-RESCAN] Batch {batch_idx + 1} timed out after {max_poll_seconds}s, moving on")
                    break
                active = get_queued_and_processing_accounts()
                active_names = set(active.get('queued', []))
                processing_list = active.get('processing', [])
                for p in processing_list:
                    if isinstance(p, dict):
                        active_names.add(p.get('company_name', ''))
                    else:
                        active_names.add(p)

                # Check how many from THIS batch are still active
                batch_still_active = active_names.intersection(set(batch_names))
                if len(batch_still_active) == 0:
                    break
                time.sleep(5)

            # Update progress (skip if cancelled mid-batch to avoid over-counting)
            if not state['cancelled']:
                state['completed'] += len(batch_names)
                print(f"[BATCH-RESCAN] Batch {batch_idx + 1}/{total_batches} complete. Progress: {state['completed']}/{state['total']}")

            # Delay between batches (skip delay after last batch)
            if batch_idx < total_batches - 1 and not state['cancelled']:
                print(f"[BATCH-RESCAN] Waiting {delay_seconds}s before next batch...")
                # Sleep in small increments so cancel is responsive
                for _ in range(delay_seconds):
                    if state['cancelled']:
                        break
                    time.sleep(1)

    except Exception as e:
        print(f"[BATCH-RESCAN] Worker error: {e}")
        import traceback; traceback.print_exc()
    finally:
        state['active'] = False
        print(f"[BATCH-RESCAN] Finished. Completed: {state['completed']}/{state['total']}")


@app.route('/api/batch-rescan', methods=['POST'])
def api_batch_rescan():
    """
    Start a batch rescan of accounts in controlled chunks.

    Accepts JSON body:
        scope: "all" | "refreshable" | "never_scanned"
        batch_size: int (default 50)
        delay_seconds: int (default 30)

    Returns immediately with total count and estimated time.
    """
    import math

    with _batch_rescan_lock:
        if _batch_rescan_state['active']:
            return jsonify({
                'status': 'error',
                'message': 'A batch rescan is already in progress. Cancel it first or wait for it to finish.'
            }), 409
        _batch_rescan_state['active'] = True

    data = request.get_json() or {}
    scope = data.get('scope', 'all')
    try:
        batch_size = max(min(int(data.get('batch_size', 50)), 200), 1)
        delay_seconds = max(int(data.get('delay_seconds', 30)), 5)
    except (ValueError, TypeError):
        _batch_rescan_state['active'] = False
        return jsonify({'status': 'error', 'message': 'batch_size and delay_seconds must be integers'}), 400

    # Query accounts based on scope
    if scope == 'refreshable':
        accounts = get_refreshable_accounts()
    elif scope == 'never_scanned':
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM monitored_accounts
                WHERE archived_at IS NULL
                  AND github_org IS NOT NULL
                  AND github_org != ''
                  AND last_scanned_at IS NULL
            ''')
            columns = [desc[0] for desc in cursor.description]
            accounts = [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            conn.close()
    else:
        # "all" — all non-archived accounts with a github_org
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM monitored_accounts
                WHERE archived_at IS NULL
                  AND github_org IS NOT NULL
                  AND github_org != ''
            ''')
            columns = [desc[0] for desc in cursor.description]
            accounts = [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    if not accounts:
        _batch_rescan_state['active'] = False
        return jsonify({'status': 'error', 'message': f'No accounts found for scope "{scope}"'}), 404

    total = len(accounts)
    total_batches = math.ceil(total / batch_size)
    # Estimate: ~3 min per batch of 50 + delay between batches
    estimated_minutes = round((total_batches * 3) + ((total_batches - 1) * delay_seconds / 60), 1)

    # Reset state (active already set to True in the lock above)
    state = _batch_rescan_state
    state['cancelled'] = False
    state['total'] = total
    state['completed'] = 0
    state['current_batch'] = 0
    state['total_batches'] = total_batches
    state['batch_size'] = batch_size
    state['delay_seconds'] = delay_seconds
    state['started_at'] = datetime.utcnow().isoformat()
    state['scope'] = scope

    # Start worker in daemon thread
    worker = threading.Thread(
        target=_batch_rescan_worker,
        args=(accounts, batch_size, delay_seconds),
        daemon=True,
        name="BatchRescanWorker"
    )
    worker.start()

    print(f"[BATCH-RESCAN] Started: scope={scope}, total={total}, batches={total_batches}, batch_size={batch_size}, delay={delay_seconds}s")

    return jsonify({
        'status': 'started',
        'total': total,
        'total_batches': total_batches,
        'batch_size': batch_size,
        'delay_seconds': delay_seconds,
        'estimated_minutes': estimated_minutes,
        'scope': scope
    })


@app.route('/api/batch-rescan/status')
def api_batch_rescan_status():
    """Return current batch rescan progress."""
    return jsonify(_batch_rescan_state)


@app.route('/api/batch-rescan/cancel', methods=['POST'])
def api_batch_rescan_cancel():
    """Cancel the running batch rescan after the current batch finishes."""
    if not _batch_rescan_state['active']:
        return jsonify({'status': 'error', 'message': 'No batch rescan is currently running'}), 404

    _batch_rescan_state['cancelled'] = True
    return jsonify({'status': 'cancelled', 'message': 'Batch rescan will stop after the current batch completes'})


# =============================================================================
# SCHEDULED RESCAN API — View & control the tier-aware auto-rescan scheduler
# =============================================================================

@app.route('/api/scheduled-rescan/status')
def api_scheduled_rescan_status():
    """
    Get the current state of the scheduled rescan scheduler.

    Returns scheduler state, tier intervals, and per-tier due counts.
    """
    summary = get_scheduled_rescan_summary()
    return jsonify({
        'scheduler': _scheduled_rescan_state,
        'intervals': {str(k): v for k, v in TIER_SCAN_INTERVALS.items()},
        'tiers': {str(k): v for k, v in summary.items()},
    })


@app.route('/api/scheduled-rescan/toggle', methods=['POST'])
def api_scheduled_rescan_toggle():
    """Enable or disable the scheduled rescan scheduler."""
    data = request.get_json() or {}
    enabled = data.get('enabled')
    if enabled is None:
        # Toggle
        _scheduled_rescan_state['enabled'] = not _scheduled_rescan_state['enabled']
    else:
        _scheduled_rescan_state['enabled'] = bool(enabled)

    state = 'enabled' if _scheduled_rescan_state['enabled'] else 'paused'
    print(f"[SCHEDULED RESCAN] Scheduler {state} via API")
    return jsonify({'status': 'success', 'enabled': _scheduled_rescan_state['enabled']})


@app.route('/api/scheduled-rescan/config', methods=['POST'])
def api_scheduled_rescan_config():
    """
    Update scheduler configuration.

    Accepts JSON:
        check_interval_hours: int (1-48, how often the scheduler checks)
        max_per_cycle: int (1-500, max accounts to queue per check)
    """
    data = request.get_json() or {}

    if 'check_interval_hours' in data:
        try:
            hours = max(1, min(48, int(data['check_interval_hours'])))
            _scheduled_rescan_state['check_interval_hours'] = hours
        except (ValueError, TypeError):
            return jsonify({'status': 'error', 'message': 'check_interval_hours must be an integer'}), 400

    if 'max_per_cycle' in data:
        try:
            max_per = max(1, min(500, int(data['max_per_cycle'])))
            _scheduled_rescan_state['max_per_cycle'] = max_per
        except (ValueError, TypeError):
            return jsonify({'status': 'error', 'message': 'max_per_cycle must be an integer'}), 400

    print(f"[SCHEDULED RESCAN] Config updated: interval={_scheduled_rescan_state['check_interval_hours']}h, "
          f"max_per_cycle={_scheduled_rescan_state['max_per_cycle']}")
    return jsonify({'status': 'success', 'scheduler': _scheduled_rescan_state})


@app.route('/api/scheduled-rescan/run-now', methods=['POST'])
def api_scheduled_rescan_run_now():
    """
    Trigger an immediate scheduled rescan cycle (doesn't wait for the timer).

    Queues up to max_per_cycle accounts that are past their tier interval.
    """
    accounts_due = get_refreshable_accounts()
    max_per_cycle = _scheduled_rescan_state['max_per_cycle']
    batch = accounts_due[:max_per_cycle]

    queued_count = 0
    for account in batch:
        company_name = account.get('company_name')
        scan_status = account.get('scan_status')
        if company_name and scan_status not in (SCAN_STATUS_QUEUED, SCAN_STATUS_PROCESSING):
            spawn_background_scan(company_name)
            queued_count += 1

    _scheduled_rescan_state['last_check_at'] = datetime.now().isoformat()
    _scheduled_rescan_state['last_queued_count'] = queued_count
    _scheduled_rescan_state['total_queued_lifetime'] += queued_count

    print(f"[SCHEDULED RESCAN] Manual run: queued {queued_count}/{len(accounts_due)} due accounts")

    return jsonify({
        'status': 'success',
        'queued': queued_count,
        'total_due': len(accounts_due),
        'capped_at': max_per_cycle,
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


@app.route('/api/deduplicate', methods=['POST'])
def api_deduplicate():
    """
    Manually trigger deduplication of accounts.

    Removes duplicate accounts based on company name (case-insensitive) and
    GitHub organization. Keeps the 'best' account for each duplicate group:
    highest tier > most recent scan > newest ID.

    Returns:
        JSON with deduplication results including count of removed duplicates.
    """
    try:
        result = cleanup_duplicate_accounts()
        deleted = result.get('deleted', 0)
        groups = result.get('groups', [])

        return jsonify({
            'status': 'success',
            'deleted': deleted,
            'groups_cleaned': len(groups),
            'details': groups
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/api/queue-details')
def api_queue_details():
    """
    Get full account details for queued and processing accounts.

    Returns:
        JSON with complete account information for all queued and processing accounts.
    """
    from database import get_queue_account_details
    queue_data = get_queue_account_details()

    # Map scan_start_time to scan_started_at for API compatibility
    for account in queue_data['processing']:
        if account.get('scan_start_time'):
            account['scan_started_at'] = account['scan_start_time']

    for account in queue_data['queued']:
        if account.get('scan_start_time'):
            account['scan_started_at'] = account['scan_start_time']

    return jsonify({
        'queued': queue_data['queued'],
        'processing': queue_data['processing'],
        'queued_count': len(queue_data['queued']),
        'processing_count': len(queue_data['processing'])
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


# =============================================================================
# GOOGLE SHEETS INTEGRATION - Coefficient-synced account ingest
# =============================================================================

@app.route('/api/sheets/status')
def api_sheets_status():
    """Get Google Sheets integration status."""
    info = get_sheet_info()
    return jsonify(info)


@app.route('/api/sheets/config', methods=['GET', 'POST'])
def api_sheets_config():
    """Get or update Google Sheets sync configuration."""
    if request.method == 'GET':
        config = sheets_get_sync_config()
        return jsonify(config)
    data = request.get_json() or {}
    sheets_set_sync_config(data)
    return jsonify({'status': 'success', **sheets_get_sync_config()})


@app.route('/api/sheets/sync', methods=['POST'])
def api_sheets_sync():
    """Trigger a Google Sheets sync - reads accounts, resolves GitHub orgs, queues for scanning."""
    if sheets_sync_in_progress():
        return jsonify({'status': 'error', 'error': 'A sync is already in progress'}), 409
    data = request.get_json() or {}
    result = sheets_run_sync(
        limit=data.get('limit'),
        sheet_name=data.get('sheet_name'),
        auto_scan=data.get('auto_scan', True),
        dry_run=data.get('dry_run', False)
    )
    status_code = 200 if result.get('status') == 'success' else 500
    return jsonify(result), status_code


@app.route('/api/sheets/enable-cron', methods=['POST'])
def api_sheets_enable_cron():
    """Enable or disable the daily Google Sheets sync cron."""
    data = request.get_json() or {}
    sheets_set_sync_config(data)
    return jsonify({'status': 'success', **sheets_get_sync_config()})


@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors."""
    return render_template('error.html', message='Page not found'), 404


@app.errorhandler(500)
def internal_error(e):
    """Handle 500 errors."""
    # Log full error server-side, return generic message to client
    original = e.original_exception if hasattr(e, 'original_exception') else e
    print(f"[ERROR] 500 Internal Server Error: {original}")
    return render_template('error.html', message='Internal server error. Please try again later.'), 500


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
        # SSRF protection: reject private/loopback IPs
        if webhook_url:
            try:
                from urllib.parse import urlparse
                import socket
                import ipaddress
                parsed = urlparse(webhook_url)
                hostname = parsed.hostname
                if hostname:
                    ip = ipaddress.ip_address(socket.gethostbyname(hostname))
                    if ip.is_private or ip.is_loopback or ip.is_reserved:
                        return jsonify({
                            'status': 'error',
                            'message': 'Webhook URL cannot point to a private or internal address'
                        }), 400
            except (socket.gaierror, ValueError):
                pass  # Allow unresolvable hostnames (may resolve at webhook time)
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


@app.route('/api/settings/gsheet', methods=['GET', 'POST'])
def api_settings_gsheet():
    """
    GET/POST Google Sheets webhook settings.

    GET: Returns current Google Sheets webhook settings
    POST: Updates Google Sheets webhook URL and enabled status

    The Google Sheets webhook automatically exports Tier 1 and Tier 2 accounts
    to a Google Sheet via a Google Apps Script web app.

    POST payload: {
        "gsheet_webhook_url": "https://script.google.com/macros/s/XXXXX/exec",
        "gsheet_webhook_enabled": true
    }

    Returns:
        JSON with current settings and status.
    """
    if request.method == 'GET':
        gsheet_enabled = get_setting('gsheet_webhook_enabled')
        return jsonify({
            'gsheet_webhook_url': get_setting('gsheet_webhook_url') or '',
            'gsheet_webhook_enabled': gsheet_enabled == 'true'
        })

    # POST - update settings
    data = request.get_json() or {}

    # Update Google Sheets webhook URL if provided
    if 'gsheet_webhook_url' in data:
        gsheet_url = data['gsheet_webhook_url'].strip()

        # Validate URL format (must be empty or start with https://script.google.com or http(s)://)
        if gsheet_url and not (gsheet_url.startswith('http://') or gsheet_url.startswith('https://')):
            return jsonify({
                'status': 'error',
                'message': 'Google Sheets webhook URL must start with http:// or https://'
            }), 400

        set_setting('gsheet_webhook_url', gsheet_url)

    # Update enabled status if provided
    if 'gsheet_webhook_enabled' in data:
        gsheet_enabled = data['gsheet_webhook_enabled']
        set_setting('gsheet_webhook_enabled', 'true' if gsheet_enabled else 'false')

    gsheet_enabled = get_setting('gsheet_webhook_enabled')
    return jsonify({
        'status': 'success',
        'gsheet_webhook_url': get_setting('gsheet_webhook_url') or '',
        'gsheet_webhook_enabled': gsheet_enabled == 'true'
    })


@app.route('/api/settings/gsheet/test', methods=['POST'])
def api_settings_gsheet_test():
    """
    Test the Google Sheets webhook with a sample payload.

    Sends a test company to verify the webhook is configured correctly.

    Returns:
        JSON with test status and response details.
    """
    gsheet_url = get_setting('gsheet_webhook_url')
    if not gsheet_url:
        return jsonify({
            'status': 'error',
            'message': 'Google Sheets webhook URL not configured'
        }), 400

    # Create test payload
    test_payload = format_gsheet_payload('test', {
        'company': 'Test Company (RepoRadar)',
        'tier': 2,
        'tier_name': 'Preparing',
        'evidence': 'This is a test webhook from RepoRadar to verify your Google Sheets integration is working.',
        'github_org': 'test-org',
        'revenue': '$10M',
        'report_id': None,
        'notes': 'Test entry - you can delete this row'
    })

    try:
        response = requests.post(
            gsheet_url,
            json=test_payload,
            headers={'Content-Type': 'application/json'},
            timeout=30
        )

        if response.status_code >= 200 and response.status_code < 300:
            return jsonify({
                'status': 'success',
                'message': 'Test webhook sent successfully! Check your Google Sheet for a new row.',
                'response_status': response.status_code
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Webhook returned status {response.status_code}',
                'response_text': response.text[:500]
            }), 400

    except requests.exceptions.Timeout:
        return jsonify({
            'status': 'error',
            'message': 'Request timed out. Google Apps Script may be slow on first run.'
        }), 408
    except requests.exceptions.RequestException as e:
        return jsonify({
            'status': 'error',
            'message': f'Request failed: {str(e)}'
        }), 500


@app.route('/docs/<path:filename>')
def serve_docs(filename):
    """
    Serve documentation files from the docs directory.

    Used to serve the Google Apps Script for Google Sheets integration.
    """
    import os
    docs_dir = os.path.join(os.path.dirname(__file__), 'docs')
    return send_from_directory(docs_dir, filename)


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
    tier = account.get('current_tier', 0) if account else 0
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

    Uses OpenAI GPT-5 mini to generate layman-friendly explanations and caches them.

    Request body:
        {"rules": ["rule_name_1", "rule_name_2", ...]}

    Returns:
        JSON with explanations for each rule.
    """
    openai_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
    if not openai_key:
        return jsonify({'error': 'No AI API key configured (OpenAI)'}), 500

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
    Generate AI explanations for a batch of rules using GPT-5 mini.

    Returns a dict mapping rule names to their explanations.
    """
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

    api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
    base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')
    if api_key and base_url:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url)
            response = client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {"role": "system", "content": "You are explaining technical rules to non-technical people. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=4096
            )
            response_text = response.choices[0].message.content.strip()
            return json.loads(response_text)
        except Exception as e:
            print(f"[RULES] GPT-5 mini error: {e}")

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
    openai_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
    if not openai_key:
        return jsonify({'error': 'No AI API key configured (OpenAI)'}), 500

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
    Watches rules/heuristics/scanner files for changes and updates the
    rules timestamp whenever a change is detected. Also does a daily
    update at 7am EST as a fallback.

    This allows rapid iteration — edit a rule or heuristic, and the
    timestamp updates within seconds instead of waiting for the next day.
    """
    import pytz
    import hashlib
    from datetime import time as dt_time, timedelta

    est = pytz.timezone('US/Eastern')

    # Files to watch for changes
    WATCHED_FILES = [
        'config.py',
        'database.py',
        'monitors/scanner.py',
        'monitors/enhanced_heuristics.py',
        'monitors/discovery.py',
        'monitors/web_analyzer.py',
        'monitors/webscraper_utils.py',
    ]

    def _get_file_hashes():
        """Get a dict of file path -> content hash for all watched files."""
        hashes = {}
        for filepath in WATCHED_FILES:
            try:
                with open(filepath, 'rb') as f:
                    hashes[filepath] = hashlib.md5(f.read()).hexdigest()
            except FileNotFoundError:
                hashes[filepath] = None
        return hashes

    # Initialize with current file hashes
    last_hashes = _get_file_hashes()
    print(f"[RULES] File watcher initialized, monitoring {len(WATCHED_FILES)} files for changes")

    CHECK_INTERVAL = 5  # Check for file changes every 5 seconds
    
    while True:
        try:
            # Check for file changes
            current_hashes = _get_file_hashes()
            changed_files = []
            for filepath in WATCHED_FILES:
                if current_hashes.get(filepath) != last_hashes.get(filepath):
                    changed_files.append(filepath)
            
            if changed_files:
                last_hashes = current_hashes
                _update_rules_timestamp()
                print(f"[RULES] Detected changes in: {', '.join(changed_files)} — rules timestamp updated at {datetime.now(est).isoformat()}")
            
            # Also do the daily 7am EST update as a fallback
            now_est = datetime.now(est)
            # Check if it's within the first CHECK_INTERVAL seconds of 7:00 AM
            target_7am = now_est.replace(hour=7, minute=0, second=0, microsecond=0)
            diff = abs((now_est - target_7am).total_seconds())
            if diff < CHECK_INTERVAL:
                _update_rules_timestamp()
                print(f"[RULES] Daily 7am EST rules timestamp updated: {now_est.isoformat()}")
                # Sleep past the 7am window to avoid duplicate daily updates
                time.sleep(CHECK_INTERVAL + 1)
                last_hashes = _get_file_hashes()  # Refresh hashes after sleep
                continue

            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            print(f"[RULES] Error in rules watcher: {e}")
            # Sleep briefly on error and retry
            time.sleep(30)


def start_rules_scheduler():
    """Start the rules file watcher and scheduler in a background daemon thread."""
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


# =============================================================================
# SCHEDULED RESCAN — Tier-aware automatic re-scan scheduler
# =============================================================================

# In-memory state for the scheduler (visible via API)
_scheduled_rescan_state = {
    'enabled': True,
    'last_check_at': None,
    'last_queued_count': 0,
    'total_queued_lifetime': 0,
    'check_interval_hours': 6,
    'max_per_cycle': 100,
}

def _scheduled_rescan_worker():
    """
    Background worker that periodically queues stale accounts for re-scan.

    Uses TIER_SCAN_INTERVALS so hot leads (Tier 2) are scanned every 3 days
    while cold/not-found accounts (Tier 0/4) are scanned monthly/quarterly.

    Runs every N hours (default 6). Each cycle:
    1. Calls get_refreshable_accounts() which returns accounts past their
       tier-specific interval, ordered by priority (hot first).
    2. Queues up to max_per_cycle accounts to avoid flooding the executor.
    3. Updates in-memory state for the status API.
    """
    check_interval = _scheduled_rescan_state['check_interval_hours'] * 3600

    print(f"[SCHEDULED RESCAN] Background thread started — checking every {_scheduled_rescan_state['check_interval_hours']}h, "
          f"max {_scheduled_rescan_state['max_per_cycle']} per cycle")
    print(f"[SCHEDULED RESCAN] Tier intervals: {dict(TIER_SCAN_INTERVALS)}")

    while True:
        try:
            if not _scheduled_rescan_state['enabled']:
                time.sleep(60)
                continue

            accounts_due = get_refreshable_accounts()
            max_per_cycle = _scheduled_rescan_state['max_per_cycle']
            batch = accounts_due[:max_per_cycle]

            queued_count = 0
            for account in batch:
                company_name = account.get('company_name')
                if company_name:
                    try:
                        spawn_background_scan(company_name)
                        queued_count += 1
                        # Small delay between queueing to spread the load
                        time.sleep(1)
                    except Exception as e:
                        print(f"[SCHEDULED RESCAN] Failed to queue {company_name}: {e}")

            now_iso = datetime.now().isoformat()
            _scheduled_rescan_state['last_check_at'] = now_iso
            _scheduled_rescan_state['last_queued_count'] = queued_count
            _scheduled_rescan_state['total_queued_lifetime'] += queued_count

            if queued_count > 0:
                remaining = len(accounts_due) - queued_count
                print(f"[SCHEDULED RESCAN] Queued {queued_count} accounts for rescan "
                      f"({remaining} more still due)")
            else:
                print(f"[SCHEDULED RESCAN] No accounts due for rescan")

        except Exception as e:
            print(f"[SCHEDULED RESCAN] Error in worker: {e}")

        time.sleep(check_interval)


def start_scheduled_rescan_scheduler():
    """Start the tier-aware scheduled rescan scheduler in a background daemon thread."""
    thread = threading.Thread(target=_scheduled_rescan_worker, daemon=True, name="ScheduledRescanScheduler")
    thread.start()


def _deduplication_worker():
    """
    Background worker that runs daily to clean up duplicate accounts.

    Duplicates are identified by:
    1. Company name (case-insensitive) - keeps highest tier, most recent scan, newest ID
    2. GitHub organization (case-insensitive) - same priority logic

    This ensures the database stays clean even if duplicates slip through during imports.
    Runs once per day at a quiet time (4am).
    """
    DEDUP_CHECK_INTERVAL = 86400  # Check once per day (in seconds)

    print("[DEDUPLICATION] Background thread started - will run daily cleanup")

    while True:
        try:
            # Run the deduplication cleanup
            result = cleanup_duplicate_accounts()
            deleted = result.get('deleted', 0)

            if deleted > 0:
                print(f"[DEDUPLICATION] Removed {deleted} duplicate accounts")
                for group in result.get('groups', []):
                    if group.get('type') == 'name':
                        print(f"[DEDUPLICATION]   - Consolidated '{group.get('name')}' ({group.get('removed_count')} duplicates)")
                    else:
                        print(f"[DEDUPLICATION]   - Consolidated org '{group.get('org')}' ({group.get('removed_count')} duplicates)")
            else:
                print("[DEDUPLICATION] No duplicates found")

        except Exception as e:
            print(f"[DEDUPLICATION] Error in deduplication worker: {e}")

        # Sleep for 24 hours before checking again
        time.sleep(DEDUP_CHECK_INTERVAL)


def start_deduplication_scheduler():
    """Start the deduplication scheduler in a background daemon thread."""
    thread = threading.Thread(target=_deduplication_worker, daemon=True, name="DeduplicationScheduler")
    thread.start()



@app.route('/api/apollo-lookup', methods=['POST'])
def apollo_lookup():
    """Look up a contact's email via Apollo People Match API."""
    import requests as req
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400
    
    first_name = data.get('first_name', '')
    last_name = data.get('last_name', '')
    name = data.get('name', '')
    domain = data.get('domain', '')
    company = data.get('company', '')
    github_login = data.get('github_login', '')

    # Parse name if first/last not provided
    if not first_name and name:
        parts = name.strip().split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''

    # Derive domain from company name if domain not provided
    if not domain and company:
        # Simple heuristic: lowercase, remove common suffixes, add .com
        clean = company.strip().lower()
        for suffix in [' inc', ' inc.', ' corp', ' corp.', ' ltd', ' ltd.', ' llc', ' co', ' co.', ' gmbh', ' ag', ' sa']:
            if clean.endswith(suffix):
                clean = clean[:len(clean) - len(suffix)]
        domain = clean.replace(' ', '') + '.com'
    
    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        return jsonify({'status': 'error', 'message': 'Apollo API key not configured'}), 500

    def _check_apollo_match(email, org_name, target_domain, target_company):
        """Return True if the Apollo result plausibly matches the target company."""
        if not email and not org_name:
            return True
        if email and '@' in email:
            email_domain = email.lower().split('@')[-1]
            if target_domain and email_domain == target_domain.lower():
                return True
        if org_name and target_company:
            org_lower = org_name.lower().strip()
            co_lower = target_company.lower().strip()
            if co_lower in org_lower or org_lower in co_lower:
                return True
        return False

    try:
        # Try Apollo People Match API
        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}
        match_url = 'https://api.apollo.io/api/v1/people/match'
        payload = {
            'first_name': first_name,
            'last_name': last_name,
            'reveal_personal_emails': True,
        }
        if domain:
            payload['organization_domain'] = domain
        if company:
            payload['organization_name'] = company

        resp = req.post(match_url, json=payload, headers=apollo_headers, timeout=15)
        if resp.status_code == 200:
            person = resp.json().get('person', {})
            if person:
                email = _filter_personal_email(person.get('email', ''))
                email_status = person.get('email_status', 'unknown')
                org_name = person.get('organization', {}).get('name', '')

                if email and not _check_apollo_match(email, org_name, domain, company):
                    email_domain = email.split('@')[-1] if '@' in email else ''
                    print(f"[APOLLO LOOKUP] Domain mismatch: {email} (org: {org_name}) does not match target {company} ({domain})")
                    return jsonify({
                        'status': 'domain_mismatch',
                        'message': f'Email found ({email_domain}) belongs to {org_name or email_domain}, not {company}',
                        'email': email,
                        'organization': org_name,
                    })

                return jsonify({
                    'status': 'success',
                    'email': email,
                    'email_status': email_status,
                    'name': person.get('name', name),
                    'title': person.get('title', ''),
                    'linkedin_url': person.get('linkedin_url', ''),
                    'organization': org_name,
                })

        # Fallback: search by name + domain
        search_url = 'https://api.apollo.io/v1/mixed_people/search'
        search_payload = {
            'q_keywords': f'{first_name} {last_name}'.strip(),
            'per_page': 3,
        }
        if domain:
            search_payload['q_organization_domains'] = domain

        resp = req.post(search_url, json=search_payload, headers=apollo_headers, timeout=15)
        if resp.status_code == 200:
            people = resp.json().get('people', [])
            if people:
                person = people[0]
                email = _filter_personal_email(person.get('email', ''))
                org_name = person.get('organization', {}).get('name', '')

                if email and not _check_apollo_match(email, org_name, domain, company):
                    email_domain = email.split('@')[-1] if '@' in email else ''
                    print(f"[APOLLO LOOKUP] Domain mismatch: {email} (org: {org_name}) does not match target {company} ({domain})")
                    return jsonify({
                        'status': 'domain_mismatch',
                        'message': f'Email found ({email_domain}) belongs to {org_name or email_domain}, not {company}',
                        'email': email,
                        'organization': org_name,
                    })

                return jsonify({
                    'status': 'success',
                    'email': email,
                    'email_status': person.get('email_status', 'unknown'),
                    'name': person.get('name', name),
                    'title': person.get('title', ''),
                    'linkedin_url': person.get('linkedin_url', ''),
                    'organization': org_name,
                })

        return jsonify({'status': 'not_found', 'message': 'No matching contact found in Apollo'})
    except Exception as e:
        print(f"[APOLLO LOOKUP ERROR] {e}")
        return jsonify({'status': 'error', 'message': 'Apollo lookup failed'}), 500


@app.route('/api/send-outreach-email', methods=['POST'])
def send_outreach_email():
    """Send an outreach email to a contributor via AgentMail."""
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400
    
    to_email = data.get('to_email', '')
    subject = data.get('subject', '')
    body = data.get('body', '')
    company_name = data.get('company_name', '')
    report_id = data.get('report_id', '')
    
    if not to_email or not subject or not body:
        return jsonify({'status': 'error', 'message': 'Missing required fields: to_email, subject, body'}), 400
    
    try:
        report_url = None
        if report_id:
            report_url = request.url_root.rstrip('/') + f'/report/{report_id}'
        
        result = send_email_draft(
            to_email=to_email,
            subject=subject,
            body=body,
            company_name=company_name,
            report_url=report_url
        )
        
        if result and result.get('success'):
            return jsonify({'status': 'success', 'message': f'Email sent to {to_email}'})
        else:
            return jsonify({'status': 'error', 'message': result.get('error', 'Failed to send email')}), 500
    except Exception as e:
        print(f"[SEND EMAIL ERROR] {e}")
        return jsonify({'status': 'error', 'message': 'Failed to send email'}), 500


@app.route('/api/apollo/sequences')
def api_apollo_sequences():
    """Fetch available Apollo email sequences."""
    import requests as req
    
    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        return jsonify({'status': 'error', 'code': 'NO_API_KEY', 'message': 'Apollo API key not configured. Add APOLLO_API_KEY in Settings.'}), 400
    
    try:
        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}
        resp = req.post('https://api.apollo.io/api/v1/emailer_campaigns/search',
                       json={'per_page': 200},
                       headers=apollo_headers,
                       timeout=15)

        if resp.status_code == 403:
            return jsonify({'status': 'error', 'message': 'API key lacks permission. Ensure you are using a Master API key in Apollo.'}), 502
        if resp.status_code != 200:
            return jsonify({'status': 'error', 'message': f'Apollo API returned {resp.status_code}'}), 502
        
        campaigns = resp.json().get('emailer_campaigns', [])
        sequences = []
        for c in campaigns:
            sequences.append({
                'id': c.get('id'),
                'name': c.get('name', 'Unnamed Sequence'),
                'active': c.get('active', False),
                'num_steps': len(c.get('emailer_steps', [])),
                'created_at': c.get('created_at', ''),
            })
        
        return jsonify({'status': 'success', 'sequences': sequences})
    except Exception as e:
        print(f"[APOLLO SEQUENCES ERROR] {e}")
        return jsonify({'status': 'error', 'message': 'Failed to fetch Apollo sequences'}), 500


@app.route('/api/apollo/sequence-detect', methods=['POST'])
def api_apollo_sequence_detect():
    """Auto-detect sequence configuration type from Apollo sequence ID.

    Fetches the sequence's emailer_steps and classifies it as:
      - one_off:    1 email step
      - threaded_4: multiple steps sharing one subject thread
      - split_2x2:  multiple steps across exactly two subject threads

    Returns status='no_key' (silently, falls back to manual) when
    APOLLO_API_KEY is not configured.
    """
    import requests as req

    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        return jsonify({'status': 'no_key'}), 200

    data = request.get_json() or {}
    sequence_id = data.get('sequence_id', '').strip()
    if not sequence_id:
        return jsonify({'status': 'error', 'message': 'No sequence_id provided'}), 400

    try:
        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}
        resp = req.post(
            'https://api.apollo.io/api/v1/emailer_campaigns/search',
            json={'per_page': 200},
            headers=apollo_headers,
            timeout=15
        )

        if resp.status_code == 403:
            return jsonify({'status': 'auth_error'}), 200
        if resp.status_code != 200:
            return jsonify({'status': 'api_error'}), 200

        campaigns = resp.json().get('emailer_campaigns', [])

        # Find the specific sequence by ID
        campaign = next((c for c in campaigns if c.get('id') == sequence_id), None)
        if not campaign:
            return jsonify({'status': 'not_found'}), 200

        steps = campaign.get('emailer_steps', [])

        # Filter to email-type steps only (exclude LinkedIn, call tasks, etc.)
        email_types = {'auto_email', 'manual_email', 'email'}
        email_steps = [s for s in steps if s.get('type') in email_types]
        if not email_steps:
            email_steps = steps  # Fallback: treat all steps as emails

        num_emails = len(email_steps)

        # Collect non-empty subjects (empty subject = threaded reply)
        subjects = [(s.get('subject') or '').strip() for s in email_steps]
        non_empty = [s for s in subjects if s]
        unique_subjects = len(set(s.lower() for s in non_empty))

        # Classify
        if num_emails == 1:
            detected = 'one_off'
            note = '1 email step detected'
        elif unique_subjects <= 1:
            # All replies thread under one subject (or all subjects identical)
            detected = 'threaded_4'
            note = f'{num_emails} emails under one subject thread'
        elif unique_subjects == 2:
            detected = 'split_2x2'
            note = f'{num_emails} emails across 2 subject threads'
        else:
            # More than 2 distinct subjects — best-effort guess
            detected = 'threaded_4'
            note = f'{num_emails} steps, {unique_subjects} subjects (best guess: threaded)'

        return jsonify({
            'status': 'success',
            'sequence_name': campaign.get('name', 'Unknown'),
            'num_steps': len(steps),
            'num_email_steps': num_emails,
            'detected_config': detected,
            'note': note,
        })

    except Exception as e:
        print(f"[APOLLO DETECT ERROR] {e}")
        return jsonify({'status': 'error'}), 200


@app.route('/api/apollo/enroll-sequence', methods=['POST'])
def api_apollo_enroll_sequence():
    """Enroll a contact into an Apollo email sequence using Custom Field Injection.

    Two-step pattern:
      1. Create/update contact with typed_custom_fields (personalized_subject + personalized_email_1)
      2. Add contact to sequence — Apollo merge tags resolve automatically from those fields.
    """
    import requests as req

    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        return jsonify({'status': 'error', 'code': 'NO_API_KEY', 'message': 'Apollo API key not configured'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    email = data.get('email', '').strip()
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    sequence_id = data.get('sequence_id', '').strip()
    company_name = data.get('company_name', '').strip()

    # Convert plain newlines to HTML breaks for Apollo rendering
    def to_html(text):
        if not text:
            return ''
        return text.strip().replace('\n\n', '<br><br>').replace('\n', '<br>')

    # All 6 Salesforce custom fields (Personalized_Subject_1__c, _2__c, Personalized_Email_1-4__c)
    personalized_subject_1 = data.get('personalized_subject', '').strip() or data.get('personalized_subject_1', '').strip()
    personalized_subject_2 = data.get('personalized_subject_2', '').strip()
    personalized_email_1 = to_html(data.get('personalized_email_body', '') or data.get('personalized_email_1', ''))
    personalized_email_2 = to_html(data.get('personalized_email_2', ''))
    personalized_email_3 = to_html(data.get('personalized_email_3', ''))
    personalized_email_4 = to_html(data.get('personalized_email_4', ''))

    if not email or not sequence_id:
        return jsonify({'status': 'error', 'message': 'Missing required fields: email and sequence_id'}), 400

    try:
        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}

        # All 6 Salesforce/Apollo custom field mappings.
        # Field IDs are discovered dynamically from /v1/typed_custom_fields.
        # Env vars serve as overrides if the API lookup fails.
        FIELD_ENV_OVERRIDES = {
            'personalized_subject_1': os.environ.get('APOLLO_FIELD_PERSONALIZED_SUBJECT_1', ''),
            'personalized_subject_2': os.environ.get('APOLLO_FIELD_PERSONALIZED_SUBJECT_2', ''),
            'personalized_email_1': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_1', ''),
            'personalized_email_2': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_2', ''),
            'personalized_email_3': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_3', ''),
            'personalized_email_4': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_4', ''),
        }

        # Map of field key -> value to inject (only non-empty values)
        field_values = {}
        if personalized_subject_1: field_values['personalized_subject_1'] = personalized_subject_1
        if personalized_subject_2: field_values['personalized_subject_2'] = personalized_subject_2
        if personalized_email_1: field_values['personalized_email_1'] = personalized_email_1
        if personalized_email_2: field_values['personalized_email_2'] = personalized_email_2
        if personalized_email_3: field_values['personalized_email_3'] = personalized_email_3
        if personalized_email_4: field_values['personalized_email_4'] = personalized_email_4

        typed_custom_fields = {}
        if field_values:
            try:
                cf_resp = req.get(
                    'https://api.apollo.io/v1/typed_custom_fields',
                    headers=apollo_headers,
                    timeout=15
                )
                if cf_resp.status_code == 200:
                    field_id_map = {}
                    for f in cf_resp.json().get('typed_custom_fields', []):
                        fid = f.get('id')
                        name = (f.get('name') or '').lower().replace(' ', '_')
                        if fid and name:
                            field_id_map[name] = fid
                    # Merge env overrides as fallback
                    for k, v in FIELD_ENV_OVERRIDES.items():
                        if v and k not in field_id_map:
                            field_id_map[k] = v
                else:
                    # API failed — use env overrides only
                    field_id_map = {k: v for k, v in FIELD_ENV_OVERRIDES.items() if v}

                # Map values to their resolved Apollo field IDs
                for field_key, field_val in field_values.items():
                    if field_key in field_id_map:
                        typed_custom_fields[field_id_map[field_key]] = field_val

                print(f"[APOLLO ENROLL] Will inject {len(typed_custom_fields)} custom field(s): {list(field_values.keys())}")
            except Exception as cf_err:
                print(f"[APOLLO ENROLL] Warning: could not fetch custom field definitions: {cf_err}")
                # Fall back to env override IDs
                for field_key, field_val in field_values.items():
                    env_id = FIELD_ENV_OVERRIDES.get(field_key, '')
                    if env_id:
                        typed_custom_fields[env_id] = field_val

        # Step 1: Search for existing contact
        contact_id = None
        search_resp = req.post('https://api.apollo.io/api/v1/contacts/search',
                               json={'q_keywords': email, 'per_page': 1},
                               headers=apollo_headers,
                               timeout=15)

        if search_resp.status_code == 200:
            contacts = search_resp.json().get('contacts', [])
            if contacts:
                contact_id = contacts[0].get('id')
                print(f"[APOLLO ENROLL] Found existing contact {contact_id} for {email}")

        # Step 2a: Create new contact (with typed_custom_fields)
        if not contact_id:
            create_payload = {
                'first_name': first_name or email.split('@')[0],
                'last_name': last_name or '',
                'email': email,
                'organization_name': company_name,
            }
            if typed_custom_fields:
                create_payload['typed_custom_fields'] = typed_custom_fields

            create_resp = req.post('https://api.apollo.io/v1/contacts',
                                   json=create_payload,
                                   headers=apollo_headers,
                                   timeout=15)

            if create_resp.status_code in (200, 201):
                contact_data = create_resp.json().get('contact', {})
                contact_id = contact_data.get('id')
                print(f"[APOLLO ENROLL] Created new contact {contact_id} for {email}")
            else:
                error_msg = create_resp.json().get('message', create_resp.text[:200])
                return jsonify({'status': 'error', 'message': f'Failed to create Apollo contact: {error_msg}'}), 502

        # Step 2b: Inject custom fields into existing contact before enrolling
        # Must use PUT /v1/ (not PATCH /api/v1/) — see Apollo API docs
        elif typed_custom_fields and contact_id:
            update_resp = req.put(
                f'https://api.apollo.io/v1/contacts/{contact_id}',
                json={'typed_custom_fields': typed_custom_fields},
                headers=apollo_headers,
                timeout=15
            )
            if update_resp.status_code in (200, 201):
                print(f"[APOLLO ENROLL] Injected custom fields into existing contact {contact_id}")
            else:
                print(f"[APOLLO ENROLL] Warning: custom field injection failed: {update_resp.text[:200]}")

        if not contact_id:
            return jsonify({'status': 'error', 'message': 'Could not find or create contact in Apollo'}), 500

        # Resolve sending email account (use APOLLO_SENDER_EMAIL env var to pick a specific account,
        # otherwise fall back to the first active account on the API key's org)
        email_account_id = None
        preferred_sender = os.environ.get('APOLLO_SENDER_EMAIL', '').strip().lower()
        try:
            ea_resp = req.get('https://api.apollo.io/api/v1/email_accounts',
                              headers=apollo_headers, timeout=15)
            if ea_resp.status_code == 200:
                accounts = ea_resp.json().get('email_accounts', [])
                active = [a for a in accounts if a.get('active')]
                if preferred_sender:
                    match = next((a for a in active if a.get('email', '').lower() == preferred_sender), None)
                    email_account_id = match['id'] if match else (active[0]['id'] if active else None)
                elif active:
                    email_account_id = active[0]['id']
        except Exception as ea_err:
            print(f"[APOLLO ENROLL] Warning: could not fetch email accounts: {ea_err}")

        if not email_account_id:
            return jsonify({'status': 'error', 'message': 'No active Apollo email account found to send from. Set APOLLO_SENDER_EMAIL in .env.'}), 500

        print(f"[APOLLO ENROLL] Sending from email account {email_account_id}")

        # Step 3: Add contact to sequence (merge tags will resolve from injected fields)
        enroll_resp = req.post(
            f'https://api.apollo.io/api/v1/emailer_campaigns/{sequence_id}/add_contact_ids',
            json={
                'emailer_campaign_id': sequence_id,
                'contact_ids': [contact_id],
                'send_email_from_email_account_id': email_account_id,
            },
            headers=apollo_headers,
            timeout=15
        )

        if enroll_resp.status_code in (200, 201):
            print(f"[APOLLO ENROLL] Enrolled {email} (contact {contact_id}) in sequence {sequence_id}")
            return jsonify({
                'status': 'success',
                'message': f'Successfully enrolled {email} in sequence',
                'contact_id': contact_id,
            })
        else:
            error_msg = enroll_resp.json().get('message', enroll_resp.text[:200]) if enroll_resp.text else 'Unknown error'
            return jsonify({'status': 'error', 'message': f'Failed to enroll in sequence: {error_msg}'}), 502

    except Exception as e:
        print(f"[APOLLO ENROLL ERROR] {e}")
        return jsonify({'status': 'error', 'message': 'Failed to enroll in Apollo sequence'}), 500



# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn Prospector Routes
# ─────────────────────────────────────────────────────────────────────────────

# Personal email domains to filter from Apollo results (not useful for B2B outreach)
_PERSONAL_EMAIL_DOMAINS = {'gmail.com', 'googlemail.com', 'yahoo.com', 'hotmail.com',
                           'outlook.com', 'aol.com', 'icloud.com', 'me.com', 'live.com',
                           'msn.com', 'protonmail.com', 'proton.me', 'mail.com', 'ymail.com'}


def _filter_personal_email(email):
    """Return empty string if email is from a personal domain (gmail, yahoo, etc.)."""
    if not email:
        return ''
    domain = email.lower().split('@')[-1] if '@' in email else ''
    return '' if domain in _PERSONAL_EMAIL_DOMAINS else email


def _derive_company_domain(company):
    """Derive a likely domain from a company name (e.g. 'Clay' -> 'clay.com')."""
    if not company:
        return ''
    clean = company.strip().lower()
    for suffix in [' inc', ' inc.', ' corp', ' corp.', ' ltd', ' ltd.', ' llc', ' co', ' co.', ' gmbh', ' ag', ' sa']:
        if clean.endswith(suffix):
            clean = clean[:len(clean) - len(suffix)]
    return clean.replace(' ', '') + '.com'


def _check_company_match(email, target_company):
    """Return True if the email domain plausibly matches the target company."""
    if not email or not target_company:
        return True  # nothing to compare — allow through
    if '@' not in email:
        return True
    email_domain = email.lower().split('@')[-1]
    # Check against derived domain
    target_domain = _derive_company_domain(target_company)
    if target_domain and email_domain == target_domain:
        return True
    # Fuzzy: company name appears in email domain or vice versa
    co_lower = target_company.lower().strip().replace(' ', '')
    if co_lower in email_domain or email_domain.split('.')[0] in co_lower:
        return True
    return False


def _sanitize_contributor_email(email, company=None):
    """Filter out personal emails and emails that don't match the contributor's company."""
    if not email:
        return ''
    # Filter personal domains
    email = _filter_personal_email(email)
    if not email:
        return ''
    # Filter domain mismatch (consultant/external contributor)
    if company and not _check_company_match(email, company):
        return ''
    return email


@app.route('/linkedin-prospector')
def linkedin_prospector():
    return render_template('linkedin_prospector.html')


@app.route('/api/linkedin/extract', methods=['POST'])
def api_linkedin_extract():
    """Extract contact info from a LinkedIn screenshot using GPT-5 mini."""
    import base64

    if 'image' not in request.files:
        return jsonify({'status': 'error', 'message': 'No image file provided'}), 400

    image_file = request.files['image']
    if not image_file.filename:
        return jsonify({'status': 'error', 'message': 'Empty filename'}), 400

    try:
        image_bytes = image_file.read()
        mime_type = image_file.content_type or 'image/png'
        image_b64 = base64.b64encode(image_bytes).decode('utf-8')

        prompt = """Extract the following from this LinkedIn profile screenshot and return ONLY valid JSON with no markdown or code blocks:
{
  "name": "Full name of the person",
  "title": "Current job title",
  "company": "Current company name",
  "location": "City, State/Country",
  "headline": "LinkedIn headline text",
  "summary": "Brief summary or about section if visible",
  "linkedin_url": "LinkedIn profile URL if visible in the browser address bar (e.g. https://www.linkedin.com/in/username)"
}

If a field is not visible or cannot be determined, use an empty string "".
Return ONLY the JSON object, nothing else."""

        response_text = None

        api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
        base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')
        if api_key and base_url:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=api_key, base_url=base_url)
                response = client.chat.completions.create(
                    model="gpt-5-mini",
                    messages=[
                        {"role": "system", "content": "You are a data extraction expert. Return ONLY valid JSON."},
                        {"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                            {"type": "text", "text": prompt}
                        ]}
                    ],
                    response_format={"type": "json_object"},
                    max_completion_tokens=1024
                )
                response_text = response.choices[0].message.content.strip()
            except Exception as e:
                print(f"[LINKEDIN] GPT-5 mini error: {e}")

        if response_text is None:
            return jsonify({'status': 'error', 'message': 'No AI API key configured (OpenAI).'}), 400

        if response_text.startswith('```'):
            lines = response_text.split('\n')
            lines = [l for l in lines if not l.startswith('```')]
            response_text = '\n'.join(lines)

        extracted = json.loads(response_text)
        return jsonify({'status': 'success', 'data': extracted})

    except json.JSONDecodeError as e:
        return jsonify({'status': 'error', 'message': 'Failed to parse AI response. Please try a clearer screenshot.'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': sanitize_ai_error(e)}), 500


@app.route('/api/linkedin/find-contact', methods=['POST'])
def api_linkedin_find_contact():
    """Find or enrich a contact in Apollo by name + company."""
    import requests as req_lib

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    company = data.get('company', '').strip()
    name = data.get('name', '').strip()
    linkedin_url = data.get('linkedin_url', '').strip()

    # Parse name from LinkedIn URL slug as last resort
    if not name and not first_name and linkedin_url:
        import re as _re
        slug_match = _re.search(r'/in/([a-zA-Z0-9_-]+)', linkedin_url)
        if slug_match:
            slug = slug_match.group(1)
            # Convert slug like "john-doe" → "John Doe", stripping trailing ID segments (contain digits)
            parts = [part for part in slug.replace('_', '-').split('-') if part]
            # Remove trailing parts that contain digits (e.g., "123", "a1b2c3")
            while parts and _re.search(r'\d', parts[-1]):
                parts.pop()
            if parts:
                name = ' '.join(part.capitalize() for part in parts)

    # Parse name if first/last not provided
    if name and not first_name:
        parts = name.split(' ')
        first_name = parts[0]
        last_name = ' '.join(parts[1:]) if len(parts) > 1 else ''

    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        return jsonify({'status': 'error', 'message': 'Apollo API key not configured. Add APOLLO_API_KEY in Settings.'}), 400

    headers = {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache',
        'X-Api-Key': apollo_key
    }

    # Try people/match first for enrichment
    match_payload = {
        'reveal_personal_emails': True
    }

    # Add linkedin_url if available — dramatically improves email match rate
    if linkedin_url:
        match_payload['linkedin_url'] = linkedin_url

    # Add name/company when available
    if first_name:
        match_payload['first_name'] = first_name
    if last_name:
        match_payload['last_name'] = last_name
    if company:
        match_payload['organization_name'] = company
        # Derive domain from company name to improve match confidence
        clean = company.strip().lower()
        for suffix in [' inc', ' inc.', ' corp', ' corp.', ' ltd', ' ltd.', ' llc', ' co', ' co.', ' gmbh', ' ag', ' sa']:
            if clean.endswith(suffix):
                clean = clean[:len(clean) - len(suffix)]
        match_payload['organization_domain'] = clean.replace(' ', '') + '.com'

    # Step 1: Try people/match for enrichment
    match_person = None
    try:
        match_resp = req_lib.post(
            'https://api.apollo.io/api/v1/people/match',
            headers=headers,
            json=match_payload,
            timeout=15
        )

        if match_resp.status_code == 200:
            match_data = match_resp.json()
            person = match_data.get('person')
            if person:
                print(f"[LINKEDIN] people/match found person: email={person.get('email')}, id={person.get('id')}, has_photo={bool(person.get('photo_url'))}")
                apollo_first = person.get('first_name') or ''
                apollo_last = person.get('last_name') or ''
                apollo_name = f"{apollo_first} {apollo_last}".strip() or (person.get('name') or '')
                fallback_name = name or f"{first_name} {last_name}".strip()
                match_person = {
                    'id': person.get('id') or '',
                    'name': apollo_name or fallback_name,
                    'first_name': apollo_first or first_name,
                    'last_name': apollo_last or last_name,
                    'email': _filter_personal_email(person.get('email') or ''),
                    'title': person.get('title') or '',
                    'company': person.get('organization_name') or (person.get('organization') or {}).get('name', ''),
                    'linkedin_url': person.get('linkedin_url') or '',
                    'photo_url': person.get('photo_url') or '',
                    'phone': person.get('sanitized_phone') or ((person.get('phone_numbers') or [{}])[0].get('raw_number', '') if person.get('phone_numbers') else ''),
                    'city': person.get('city') or '',
                    'state': person.get('state') or '',
                    'country': person.get('country') or ''
                }
                # If we already have a (non-personal) email, return immediately
                if match_person['email']:
                    return jsonify({'status': 'success', 'source': 'people/match', 'contact': match_person})

    except Exception as e:
        print(f"[LINKEDIN] people/match error: {e}")

    # Step 2: Try contacts/search in CRM (catches emails added via Chrome extension)
    search_query = f"{first_name} {last_name}".strip()
    if company:
        search_query += f" {company}"

    if search_query:
        try:
            search_payload = {
                'q_keywords': search_query,
                'page': 1,
                'per_page': 5
            }
            search_resp = req_lib.post(
                'https://api.apollo.io/api/v1/contacts/search',
                headers=headers,
                json=search_payload,
                timeout=15
            )

            if search_resp.status_code == 200:
                search_data = search_resp.json()
                contacts = search_data.get('contacts', [])
                if contacts:
                    person = contacts[0]
                    print(f"[LINKEDIN] contacts/search found: email={person.get('email')}")
                    s_first = person.get('first_name') or ''
                    s_last = person.get('last_name') or ''
                    s_name = f"{s_first} {s_last}".strip() or (person.get('name') or '')
                    fallback_name = name or f"{first_name} {last_name}".strip()
                    search_contact = {
                        'id': person.get('id') or '',
                        'name': s_name or fallback_name,
                        'first_name': s_first or first_name,
                        'last_name': s_last or last_name,
                        'email': _filter_personal_email(person.get('email') or ''),
                        'title': person.get('title') or '',
                        'company': person.get('organization_name') or (person.get('account') or {}).get('name', ''),
                        'linkedin_url': person.get('linkedin_url') or '',
                        'photo_url': person.get('photo_url') or '',
                        'phone': person.get('sanitized_phone') or '',
                        'city': person.get('city') or '',
                        'state': person.get('state') or '',
                        'country': person.get('country') or ''
                    }
                    # If CRM search has a (non-personal) email, return it (merge with match data for photo)
                    if search_contact['email']:
                        # Prefer match_person's photo_url if search doesn't have one
                        if match_person and not search_contact['photo_url'] and match_person.get('photo_url'):
                            search_contact['photo_url'] = match_person['photo_url']
                        return jsonify({'status': 'success', 'source': 'contacts/search', 'contact': search_contact})

        except Exception as e:
            print(f"[LINKEDIN] contacts/search error: {e}")

    # Step 3: Return match_person without email if we found them, else not_found
    if match_person:
        return jsonify({'status': 'success', 'source': 'people/match', 'contact': match_person})

    not_found_name = search_query or 'this LinkedIn profile'
    return jsonify({'status': 'not_found', 'message': f'No contact found for {not_found_name}. They may not be in Apollo yet.'})


@app.route('/api/linkedin/generate-email', methods=['POST'])
def api_linkedin_generate_email():
    """Generate a personalized outreach email for a LinkedIn contact using OpenAI GPT-5 mini."""
    from openai import OpenAI

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    contact = data.get('contact', {})
    linkedin_data = data.get('linkedin_data', {})

    api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
    base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')
    if not api_key or not base_url:
        return jsonify({'status': 'error', 'message': 'OpenAI API key not configured'}), 400

    name = contact.get('name') or linkedin_data.get('name', 'there')
    first_name = contact.get('first_name') or (name.split(' ')[0] if name else 'there')
    title = contact.get('title') or linkedin_data.get('title', '')
    company = contact.get('company') or linkedin_data.get('company', '')
    headline = contact.get('headline') or linkedin_data.get('headline', '')
    summary = contact.get('summary') or linkedin_data.get('summary', '')
    goldilocks_status = data.get('goldilocks_status', '')

    tone_guidance = ''
    if goldilocks_status == 'preparing':
        tone_guidance = '\nTone: URGENT — this company is actively setting up i18n. Create urgency and push for an immediate meeting.'
    elif goldilocks_status == 'thinking':
        tone_guidance = '\nTone: NURTURE — early i18n signals. Be helpful, educational, and position as a trusted advisor.'
    elif goldilocks_status == 'launched':
        tone_guidance = '\nTone: LOW PRIORITY — already localized. Focus on pain points with current solution.'
    else:
        tone_guidance = '\nTone: EDUCATIONAL — cold lead. Educate on the market opportunity.'

    prompt = f"""You are a BDR (Business Development Rep) writing a personalized cold outreach email to a software engineering leader.

Contact info:
- Name: {name}
- First name: {first_name}
- Title: {title}
- Company: {company}
- LinkedIn headline: {headline}
- LinkedIn summary: {summary}
- Lead temperature: {goldilocks_status or 'unknown'}
{tone_guidance}

Write a SHORT, personalized cold outreach email. The goal is to start a conversation about their internationalization/localization (i18n) workflow and how our tool (Lead Machine) can help their engineering team ship to global markets faster.

Rules:
- Subject line: short, specific, references their role or company
- Body: 3-4 sentences MAX. Reference something specific about them.
- End with a simple CTA: "Worth a quick chat?"
- No fluff, no generic templates, no "I hope this email finds you well"
- Sound like a human, not a robot

Return ONLY valid JSON with no markdown:
{{
  "subject": "the subject line",
  "body": "the full email body (use \\n for line breaks)"
}}"""

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "You are a BDR writing personalized cold outreach emails. Return ONLY valid JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=4096
        )

        response_text = response.choices[0].message.content.strip()
        if response_text.startswith('```'):
            lines = response_text.split('\n')
            lines = [l for l in lines if not l.startswith('```')]
            response_text = '\n'.join(lines)

        email_data = json.loads(response_text)
        return jsonify({'status': 'success', 'email': email_data})

    except Exception as e:
        return jsonify({'status': 'error', 'message': sanitize_ai_error(e)}), 500

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

    # Start Google Sheets cron scheduler
    sheets_start_cron()
    print("[APP] Google Sheets cron scheduler started")

    # Start the rules scheduler for 7am EST daily updates
    start_rules_scheduler()

    # Start the tier-aware scheduled rescan scheduler
    start_scheduled_rescan_scheduler()
    print("[APP] Tier-aware scheduled rescan started (intervals: " +
          ", ".join(f"T{t}={d}d" for t, d in sorted(TIER_SCAN_INTERVALS.items())) + ")")

    # Start the deduplication scheduler (runs daily to clean up duplicates)
    start_deduplication_scheduler()

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

    port = int(os.environ.get('PORT', 5000))
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=port, threaded=True)
