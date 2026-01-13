"""
Lead Machine - Deep-Dive Research Engine

A Flask application for analyzing GitHub organizations for localization signals.
"""
import json
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
from datetime import datetime
from flask import Flask, render_template, Response, request, jsonify, redirect, url_for, stream_with_context, send_file
from config import Config
from database import (
    save_report, get_report, get_recent_reports, search_reports,
    update_account_status, get_all_accounts, add_account_to_tier_0, TIER_CONFIG,
    get_account_by_company, get_account_by_company_case_insensitive,
    mark_account_as_invalid, get_refreshable_accounts, delete_account,
    get_db_connection, get_setting, set_setting, increment_daily_stat,
    get_stats_last_n_days, log_webhook, get_recent_webhook_logs,
    set_scan_status, get_scan_status, get_queued_and_processing_accounts,
    clear_stale_scan_statuses, reset_all_scan_statuses, batch_set_scan_status_queued,
    SCAN_STATUS_IDLE, SCAN_STATUS_QUEUED, SCAN_STATUS_PROCESSING,
    save_signals, cleanup_duplicate_accounts
)
from monitors.scanner import deep_scan_generator
from monitors.discovery import search_github_orgs, resolve_org_fast, discover_companies_via_ai
from ai_summary import generate_analysis
from pdf_generator import generate_report_pdf


app = Flask(__name__)
app.config.from_object(Config)


# =============================================================================
# WEBHOOK NOTIFICATIONS - Push leads to Zapier/Salesforce
# =============================================================================

def trigger_webhook(event_type: str, company_data: dict) -> None:
    """
    Send a webhook notification asynchronously.

    This function runs in a background thread to avoid blocking the UI.
    Fetches the webhook URL from system_settings and sends a POST request.

    Args:
        event_type: Type of event (e.g., 'tier_change', 'scan_complete')
        company_data: Dictionary containing company information to send
    """
    webhook_url = get_setting('webhook_url')
    if not webhook_url:
        print("[WEBHOOK] No webhook_url configured in settings, skipping notification")
        return

    payload = {
        'event_type': event_type,
        'timestamp': datetime.now().isoformat(),
        **company_data
    }

    def send_webhook():
        company_name = company_data.get('company', company_data.get('company_name', 'Unknown'))
        try:
            response = requests.post(
                webhook_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            if response.status_code >= 200 and response.status_code < 300:
                print(f"[WEBHOOK] Success: {company_name} -> {webhook_url} (status: {response.status_code})")
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
    """
    print(f"[WORKER] Starting background scan for: {company_name}")

    try:
        # Update database status to 'processing'
        set_scan_status(company_name, SCAN_STATUS_PROCESSING, 'Starting scan...')

        start_time = time.time()
        scan_data = None
        analysis_data = None

        # Phase 1: Run the deep scan (silent)
        account = get_account_by_company_case_insensitive(company_name)
        last_scanned_at = account.get('last_scanned_at') if account else None
        for message in deep_scan_generator(company_name, last_scanned_at):
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
            print(f"[WORKER] Failed to save report for {company_name}: {str(e)}")
            return

        # Phase 3b: Save signals detected during the scan
        try:
            signals = scan_data.get('signals', [])
            if signals:
                signals_count = save_signals(report_id, company_name, signals)
                print(f"[WORKER] Saved {signals_count} signals for {company_name}")
        except Exception as e:
            print(f"[WORKER] Failed to save signals for {company_name}: {str(e)}")

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
                    'signal': result.get('evidence', '')
                }
                trigger_webhook('tier_change', webhook_data)
                print(f"[WORKER] Webhook triggered for {company_name} (Tier {result.get('tier')})")
        except Exception as e:
            print(f"[WORKER] Failed to update account status for {company_name}: {str(e)}")

        print(f"[WORKER] Completed scan for {company_name} in {duration:.1f}s")

    except Exception as e:
        print(f"[WORKER] Background scan failed for {company_name}: {str(e)}")
    finally:
        # ALWAYS reset scan status to idle when done (success or failure)
        set_scan_status(company_name, SCAN_STATUS_IDLE)


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


@app.route('/')
def index():
    """Render the homepage with search bar."""
    recent = get_recent_reports(limit=10)
    return render_template('index.html', recent_reports=recent)


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
            for message in deep_scan_generator(company, last_scanned_at):
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
                    'signal': account_result.get('evidence', '')
                }
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

    return render_template(
        'accounts.html',
        accounts=result['accounts'],
        total_items=result['total_items'],
        total_pages=result['total_pages'],
        current_page=result['current_page'],
        limit=result['limit'],
        current_tier_filter=tiers,
        current_search=search_query or '',
        tier_config=TIER_CONFIG
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


@app.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def api_delete_account(account_id: int):
    """Delete a monitored account by ID."""
    deleted = delete_account(account_id)
    if not deleted:
        return jsonify({'error': 'Account not found'}), 404
    return jsonify({'status': 'success'})


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


@app.route('/api/import', methods=['POST'])
def api_import():
    """
    Bulk import companies by resolving them to GitHub organizations.

    Expects JSON payload: {"companies": ["Shopify", "Stripe", ...]}

    Returns:
        JSON with: {
            "added": ["Shopify", ...],
            "failed": ["MomPop", ...],
            "results": [{"company": "...", "github_org": "...", "status": "..."}],
            "batch_id": timestamp
        }

    After adding companies to the database, batch-queues them for scanning.
    This is optimized for bulk imports - all accounts are queued at once.
    """
    data = request.get_json() or {}
    companies = data.get('companies', [])

    if not isinstance(companies, list) or not companies:
        return jsonify({'error': 'Invalid payload: expected {"companies": [...]}'}), 400

    # Generate batch ID using current timestamp
    batch_id = int(time.time())

    added = []
    failed = []
    skipped = []
    results = []

    # Phase 1: Resolve and add all companies to database (no scanning yet)
    for company_name in companies:
        company_name = company_name.strip()
        if not company_name:
            continue

        try:
            existing = get_account_by_company_case_insensitive(company_name)
            if existing:
                skipped.append(company_name)
                results.append({
                    'company': company_name,
                    'github_org': existing.get('github_org'),
                    'status': 'already_indexed'
                })
                continue

            # Try to resolve the company to a GitHub org
            org = resolve_org_fast(company_name)

            if org:
                github_org = org.get('login', '')
                # Add to monitored_accounts at Tier 0
                add_account_to_tier_0(company_name, github_org)
                added.append(company_name)
                results.append({
                    'company': company_name,
                    'github_org': github_org,
                    'status': 'added'
                })
            else:
                failed.append(company_name)
                results.append({
                    'company': company_name,
                    'github_org': None,
                    'status': 'not_found'
                })
        except Exception as e:
            failed.append(company_name)
            results.append({
                'company': company_name,
                'github_org': None,
                'status': f'error: {str(e)}'
            })

    # Phase 2: Batch update all added accounts to 'queued' status at once
    # This makes the queue populate instantly rather than trickling in
    if added:
        batch_set_scan_status_queued(added)
        print(f"[IMPORT] Batch queued {len(added)} accounts")

        # Phase 3: Submit all to executor for background scanning
        executor = get_executor()
        for company_name in added:
            executor.submit(perform_background_scan, company_name)
            print(f"[EXECUTOR] Submitted scan for {company_name}")

    return jsonify({
        'added': added,
        'failed': failed,
        'skipped': skipped,
        'total_processed': len(companies),
        'results': results,
        'batch_id': batch_id
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

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Update the github_org for this account
        cursor.execute('''
            UPDATE monitored_accounts
            SET github_org = ?, current_tier = 0, evidence_summary = 'GitHub org updated manually'
            WHERE company_name = ?
        ''', (github_org, company_name))

        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'status': 'error', 'message': 'Account not found'}), 404

        conn.commit()
        conn.close()

        return jsonify({
            'status': 'success',
            'company_name': company_name,
            'github_org': github_org
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/rescan/<company_name>', methods=['POST'])
def api_rescan(company_name: str):
    """
    Queue a rescan for a company.

    Instead of running synchronously (which blocks and can cause database locks),
    this submits the scan job to the thread pool and returns immediately.

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

    # Submit to thread pool (this also sets status to 'queued' in DB)
    spawn_background_scan(company_name)
    print(f"[EXECUTOR] Rescan submitted for {company_name}")

    # Get current account info for response
    account = get_account_by_company(company_name)
    active_jobs = get_queued_and_processing_accounts()
    queue_size = len(active_jobs.get('queued', [])) + len(active_jobs.get('processing', []))

    if account:
        tier = account.get('current_tier', 0)
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
    cursor = conn.cursor()

    # Find accounts that have never been scanned (last_scanned_at is NULL)
    # and are not currently being scanned
    cursor.execute('''
        SELECT company_name FROM monitored_accounts
        WHERE last_scanned_at IS NULL
          AND (scan_status IS NULL OR scan_status = ?)
    ''', (SCAN_STATUS_IDLE,))

    pending_accounts = [row[0] for row in cursor.fetchall()]
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
    - Resets any scan statuses that were stuck in 'queued' or 'processing'
    - Auto-scans any accounts that were imported but never scanned
    """
    global _app_initialized
    if not _app_initialized:
        print("[APP] First request - initializing executor and cleaning up...")

        # Reset any stale scan statuses from previous run
        reset_count = reset_all_scan_statuses()
        if reset_count > 0:
            print(f"[APP] Reset {reset_count} stale scan statuses from previous run")

        # Initialize the executor
        get_executor()

        # Auto-scan any accounts that were imported but never scanned
        _auto_scan_pending_accounts()

        _app_initialized = True


def _auto_scan_pending_accounts():
    """
    Automatically queue scans for accounts that were imported but never scanned.

    This is called on app startup to ensure imported accounts get scanned
    even if the app was restarted before their initial scan completed.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Find accounts that have never been scanned
        cursor.execute('''
            SELECT company_name FROM monitored_accounts
            WHERE last_scanned_at IS NULL
        ''')

        pending_accounts = [row[0] for row in cursor.fetchall()]
        conn.close()

        if pending_accounts:
            print(f"[APP] Found {len(pending_accounts)} accounts pending initial scan")
            for company_name in pending_accounts:
                spawn_background_scan(company_name)
            print(f"[APP] Auto-submitted {len(pending_accounts)} pending accounts for scan")
        else:
            print("[APP] No pending accounts to scan")

    except Exception as e:
        print(f"[APP] Error auto-scanning pending accounts: {str(e)}")


# =============================================================================
# SETTINGS & STATS API ROUTES
# =============================================================================

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    """
    Get or update system settings.

    GET: Returns current settings (webhook_url, etc.)
    POST: Updates settings from JSON payload {"webhook_url": "..."}
    """
    if request.method == 'GET':
        return jsonify({
            'webhook_url': get_setting('webhook_url') or ''
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

    return jsonify({
        'status': 'success',
        'webhook_url': get_setting('webhook_url') or ''
    })


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


def _watchdog_worker():
    """
    Background worker that runs indefinitely to clear stale processing statuses.
    Runs every 2 minutes.
    """
    print("[WATCHDOG] Background thread started")
    while True:
        try:
            # Clear any account stuck in 'processing' for more than 15 minutes
            recovered = clear_stale_scan_statuses(timeout_minutes=15)
            if recovered > 0:
                print(f"[WATCHDOG] Recovered {recovered} stale scans")
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

    # Reset any stale scan statuses from previous run
    reset_count = reset_all_scan_statuses()
    if reset_count > 0:
        print(f"[APP] Reset {reset_count} stale scan statuses from previous run")

    # Cleanup any duplicate accounts
    cleanup_result = cleanup_duplicate_accounts()
    removed_count = cleanup_result.get('deleted', 0)
    if removed_count > 0:
        print(f"[APP] Removed {removed_count} duplicate accounts")

    # Initialize the executor
    get_executor()

    # Start the background watchdog thread
    start_watchdog()

    # Auto-scan any pending accounts on direct startup
    _auto_scan_pending_accounts()

    # Mark as initialized to prevent duplicate auto-scan on first request
    _app_initialized = True

    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000, threaded=True)
