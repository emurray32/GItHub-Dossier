"""
Lead Machine - Deep-Dive Research Engine

A Flask application for analyzing GitHub organizations for localization signals.
"""
import json
import time
import os
import threading
import queue
from datetime import datetime
from flask import Flask, render_template, Response, request, jsonify, redirect, url_for, stream_with_context, send_file
from config import Config
from database import (
    save_report, get_report, get_recent_reports, search_reports,
    update_account_status, get_all_accounts, add_account_to_tier_0, TIER_CONFIG,
    get_account_by_company, mark_account_as_invalid, get_refreshable_accounts,
    get_db_connection
)
from monitors.scanner import deep_scan_generator
from monitors.discovery import search_github_orgs, resolve_org_fast, discover_companies_via_ai
from ai_summary import generate_analysis
from pdf_generator import generate_report_pdf


app = Flask(__name__)
app.config.from_object(Config)

# =============================================================================
# JOB QUEUE SYSTEM - Prevents SQLite 'Database Locked' errors
# =============================================================================

# Single job queue for all background scans
scan_queue = queue.Queue()
_scan_worker_thread = None
_worker_lock = threading.Lock()


def perform_background_scan(company_name: str):
    """
    Execute a background scan for a company.

    CRITICAL: This function creates its own database connection for thread safety.
    SQLite requires each thread to have its own connection.
    """
    import sqlite3
    from config import Config as AppConfig

    print(f"[WORKER] Starting background scan for: {company_name}")

    try:
        start_time = time.time()
        scan_data = None
        analysis_data = None

        # Phase 1: Run the deep scan (silent)
        for message in deep_scan_generator(company_name):
            # Check for scan errors - mark as invalid and exit
            if 'data: ERROR:' in message:
                error_msg = message.split('data: ERROR:', 1)[1].strip()
                error_msg = error_msg.replace('\n', '').strip()
                print(f"[WORKER] Scan error for {company_name}: {error_msg}")
                # Create fresh connection for this thread
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

        # Phase 4: Update monitored account status and tier
        try:
            result = update_account_status(scan_data, report_id)
            tier_name = result.get('tier_name', 'Unknown')
            print(f"[WORKER] Account status updated for {company_name}: Tier {result.get('tier')} ({tier_name})")
        except Exception as e:
            print(f"[WORKER] Failed to update account status for {company_name}: {str(e)}")

        print(f"[WORKER] Completed scan for {company_name} in {duration:.1f}s")

    except Exception as e:
        print(f"[WORKER] Background scan failed for {company_name}: {str(e)}")


def scan_worker():
    """
    Single worker thread that processes scan jobs from the queue.

    Runs as a daemon thread - pulls company names from the queue
    and processes them one at a time to prevent database conflicts.
    """
    print("[WORKER] Scan worker thread started")
    while True:
        try:
            company_name = scan_queue.get()
            if company_name is None:  # Shutdown signal
                print("[WORKER] Received shutdown signal")
                break
            queue_size = scan_queue.qsize()
            print(f"[WORKER] Processing: {company_name} (queue size: {queue_size})")
            perform_background_scan(company_name)
        except Exception as e:
            print(f"[WORKER] Error in worker loop: {str(e)}")
        finally:
            scan_queue.task_done()


def start_scan_worker():
    """Start the background scan worker thread."""
    global _scan_worker_thread
    with _worker_lock:
        # Check if worker already exists and is alive
        if _scan_worker_thread is not None and _scan_worker_thread.is_alive():
            print("[WORKER] Worker thread already running")
            return _scan_worker_thread

        print("[WORKER] Starting new scan worker thread...")
        _scan_worker_thread = threading.Thread(target=scan_worker, daemon=True, name="ScanWorker")
        _scan_worker_thread.start()
        print(f"[WORKER] Worker thread started (alive: {_scan_worker_thread.is_alive()})")
        return _scan_worker_thread


def ensure_worker_running():
    """Ensure the worker thread is running, restart if necessary."""
    global _scan_worker_thread
    with _worker_lock:
        if _scan_worker_thread is None or not _scan_worker_thread.is_alive():
            print("[WORKER] Worker thread not running, starting...")
            _scan_worker_thread = threading.Thread(target=scan_worker, daemon=True, name="ScanWorker")
            _scan_worker_thread.start()
            print(f"[WORKER] Worker thread restarted (alive: {_scan_worker_thread.is_alive()})")
        return _scan_worker_thread.is_alive()


def spawn_background_scan(company_name: str):
    """
    Queue a company for background scanning.

    Instead of spawning a new thread (which causes SQLite 'database locked' errors),
    this adds the company to the single-worker job queue.

    The scan will run asynchronously without blocking the API response.
    The account tier is automatically updated with the scan results.
    """
    # Ensure worker is running before queuing
    ensure_worker_running()
    scan_queue.put(company_name)
    print(f"[QUEUE] Added {company_name} to scan queue (size: {scan_queue.qsize()})")


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
            for message in deep_scan_generator(company):
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

        # Phase 3.5: Update monitored account status
        try:
            account_result = update_account_status(scan_data, report_id)
            tier_name = account_result.get('tier_name', 'Unknown')
            tier_status = account_result.get('tier_status', '')
            tier_emoji = TIER_CONFIG.get(account_result.get('tier', 0), {}).get('emoji', '')
            yield f"data: LOG:Account status updated: {tier_emoji} {tier_name} ({tier_status})\n\n"

            if account_result.get('tier_changed'):
                yield f"data: LOG:Tier changed! Evidence: {account_result.get('evidence', 'N/A')}\n\n"

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
    all_accounts = get_all_accounts()
    return render_template('accounts.html', accounts=all_accounts, tier_config=TIER_CONFIG)


@app.route('/api/accounts')
def api_accounts():
    """API endpoint to get all monitored accounts."""
    all_accounts = get_all_accounts()
    return jsonify(all_accounts)


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

    # Get existing accounts to filter them out
    existing_accounts = get_all_accounts()
    existing_logins = {acc['github_org'].lower() for acc in existing_accounts if acc.get('github_org')}

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

    # Get existing accounts to filter them out
    existing_accounts = get_all_accounts()
    existing_logins = {acc['github_org'].lower() for acc in existing_accounts if acc.get('github_org')}

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
            "results": [{"company": "...", "github_org": "...", "status": "..."}]
        }

    After adding each company to the database, spawns a background scan
    thread so the company data is analyzed automatically.
    """
    data = request.get_json() or {}
    companies = data.get('companies', [])

    if not isinstance(companies, list) or not companies:
        return jsonify({'error': 'Invalid payload: expected {"companies": [...]}'}), 400

    added = []
    failed = []
    results = []

    for company_name in companies:
        company_name = company_name.strip()
        if not company_name:
            continue

        try:
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

                # Spawn background scan immediately after adding to DB
                spawn_background_scan(company_name)
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

    return jsonify({
        'added': added,
        'failed': failed,
        'total_processed': len(companies),
        'results': results
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
    this queues the scan job and returns immediately.

    Returns:
        JSON with queued status. The UI should refresh to see updated results.
    """
    # Ensure worker is running and queue the scan job
    worker_alive = ensure_worker_running()
    scan_queue.put(company_name)
    print(f"[QUEUE] Rescan queued for {company_name} (worker_alive: {worker_alive}, queue_size: {scan_queue.qsize()})")

    # Get current account info for response
    account = get_account_by_company(company_name)
    if account:
        tier = account.get('current_tier', 0)
        tier_config = TIER_CONFIG.get(tier, TIER_CONFIG[0])
        return jsonify({
            'status': 'queued',
            'company': company_name,
            'message': 'Scan queued successfully',
            'queue_size': scan_queue.qsize(),
            'worker_alive': worker_alive,
            'current_tier': tier,
            'tier_name': tier_config['name'],
            'tier_emoji': tier_config['emoji']
        })

    return jsonify({
        'status': 'queued',
        'company': company_name,
        'message': 'Scan queued successfully',
        'queue_size': scan_queue.qsize(),
        'worker_alive': worker_alive
    })


@app.route('/api/scan-pending', methods=['POST'])
def api_scan_pending():
    """
    Queue scans for all accounts that have never been scanned.

    Logic: Query monitored_accounts for all records where last_scanned_at
    is NULL or equal to created_at (meaning never scanned).

    Action: Add them all to the scan queue.

    Returns:
        JSON with count of scans queued.
    """
    # Ensure worker is running before queuing
    worker_alive = ensure_worker_running()

    conn = get_db_connection()
    cursor = conn.cursor()

    # Find accounts that have never been scanned
    # (last_scanned_at equals created_at or is NULL)
    cursor.execute('''
        SELECT company_name FROM monitored_accounts
        WHERE last_scanned_at IS NULL OR last_scanned_at = created_at
    ''')

    pending_accounts = [row[0] for row in cursor.fetchall()]
    conn.close()

    # Queue scans for each pending account
    queued_count = 0
    for company_name in pending_accounts:
        scan_queue.put(company_name)
        queued_count += 1

    print(f"[QUEUE] Queued {queued_count} pending accounts (worker_alive: {worker_alive})")

    return jsonify({
        'status': 'success',
        'queued': queued_count,
        'accounts': pending_accounts,
        'queue_size': scan_queue.qsize(),
        'worker_alive': worker_alive
    })


@app.route('/api/refresh-pipeline', methods=['POST'])
def api_refresh_pipeline():
    """
    Queue all accounts eligible for weekly refresh.

    Selects accounts where:
    - current_tier IN (0, 1, 2) - Tracking, Thinking, or Preparing
    - last_scanned_at < 7 days ago OR IS NULL

    Excludes Tier 3 (Launched) and Tier 4 (Invalid) accounts.

    Returns:
        JSON with count of accounts queued for refresh.
    """
    # Ensure worker is running before queuing
    worker_alive = ensure_worker_running()

    # Get all refreshable accounts
    refreshable = get_refreshable_accounts()

    # Queue each account for scanning
    queued_count = 0
    queued_companies = []
    for account in refreshable:
        company_name = account.get('company_name')
        if company_name:
            scan_queue.put(company_name)
            queued_count += 1
            queued_companies.append(company_name)

    print(f"[QUEUE] Queued {queued_count} accounts for refresh (worker_alive: {worker_alive})")

    return jsonify({
        'status': 'success',
        'queued': queued_count,
        'accounts': queued_companies,
        'queue_size': scan_queue.qsize(),
        'worker_alive': worker_alive
    })


@app.route('/api/queue-status')
def api_queue_status():
    """
    Get the current status of the scan queue.

    Returns:
        JSON with queue size and status.
    """
    global _scan_worker_thread
    worker_alive = _scan_worker_thread is not None and _scan_worker_thread.is_alive()

    return jsonify({
        'queue_size': scan_queue.qsize(),
        'worker_alive': worker_alive,
        'worker_name': _scan_worker_thread.name if _scan_worker_thread else None
    })


@app.route('/api/worker-restart', methods=['POST'])
def api_worker_restart():
    """
    Manually restart the scan worker thread.

    Use this if scans are stuck in pending state.

    Returns:
        JSON with worker status after restart.
    """
    worker_alive = ensure_worker_running()
    return jsonify({
        'status': 'success',
        'worker_alive': worker_alive,
        'queue_size': scan_queue.qsize(),
        'message': 'Worker thread started' if worker_alive else 'Failed to start worker'
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
# App Startup - Ensure worker is running
# =============================================================================

# Use a flag to track if we've initialized on first request
_app_initialized = False

@app.before_request
def initialize_on_first_request():
    """Ensure worker thread is running on first request."""
    global _app_initialized
    if not _app_initialized:
        print("[APP] First request - ensuring worker is running...")
        ensure_worker_running()
        _app_initialized = True


if __name__ == '__main__':
    # Start worker when running directly
    print("[APP] Starting application...")
    start_scan_worker()
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000, threaded=True)
