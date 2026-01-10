"""
Lead Machine - Deep-Dive Research Engine

A Flask application for analyzing GitHub organizations for localization signals.
"""
import json
import time
import os
import threading
from datetime import datetime
from flask import Flask, render_template, Response, request, jsonify, redirect, url_for, stream_with_context, send_file
from config import Config
from database import (
    save_report, get_report, get_recent_reports, search_reports,
    update_account_status, get_all_accounts, add_account_to_tier_0, TIER_CONFIG,
    get_account_by_company, mark_account_as_invalid
)
from monitors.scanner import deep_scan_generator
from monitors.discovery import search_github_orgs, resolve_org_fast
from ai_summary import generate_analysis
from pdf_generator import generate_report_pdf


app = Flask(__name__)
app.config.from_object(Config)


def spawn_background_scan(company_name: str):
    """
    Spawn a background thread to scan a company.

    This function runs the full scan pipeline asynchronously without
    blocking the API response. The account tier is automatically
    updated with the scan results.

    If the scan fails (e.g., org not found, no repos), the account
    is marked as invalid (Tier 4) and excluded from future scans.
    """
    def _run_scan():
        try:
            start_time = time.time()
            scan_data = None
            analysis_data = None

            # Phase 1: Run the deep scan (silent)
            for message in deep_scan_generator(company_name):
                # Check for scan errors - mark as invalid and exit
                if 'data: ERROR:' in message:
                    # Extract error message
                    error_msg = message.split('data: ERROR:', 1)[1].strip()
                    # Clean up newlines from SSE format
                    error_msg = error_msg.replace('\n', '').strip()
                    # Mark account as invalid (Tier 4)
                    mark_account_as_invalid(company_name, error_msg)
                    return

                if 'SCAN_COMPLETE:' in message:
                    json_str = message.split('SCAN_COMPLETE:', 1)[1].strip()
                    if json_str.startswith('data: '):
                        json_str = json_str[6:]
                    scan_data = json.loads(json_str)

            if not scan_data:
                # No scan data generated - mark as invalid
                mark_account_as_invalid(company_name, 'No scan data generated')
                return

            # Phase 2: Generate AI analysis (silent)
            try:
                for message in generate_analysis(scan_data):
                    if 'ANALYSIS_COMPLETE:' in message:
                        json_str = message.split('ANALYSIS_COMPLETE:', 1)[1].strip()
                        if json_str.startswith('data: '):
                            json_str = json_str[6:]
                        analysis_data = json.loads(json_str)
            except Exception:
                analysis_data = {'error': 'Analysis failed'}

            # Phase 3: Save report to database
            duration = time.time() - start_time
            try:
                report_id = save_report(
                    company_name=company_name,
                    github_org=scan_data.get('org_login', ''),
                    scan_data=scan_data,
                    ai_analysis=analysis_data or {},
                    scan_duration=duration
                )
            except Exception:
                return

            # Phase 4: Update monitored account status and tier
            try:
                update_account_status(scan_data, report_id)
            except Exception:
                pass

        except Exception:
            pass  # Silent failure in background

    # Start scan in background thread (daemon=False ensures it completes)
    thread = threading.Thread(target=_run_scan, daemon=False)
    thread.start()


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


@app.route('/api/rescan/<company_name>', methods=['POST'])
def api_rescan(company_name: str):
    """
    Background rescan endpoint - runs scan silently and updates account status.

    Performs the full scan pipeline:
    1. Runs deep_scan_generator to completion
    2. Generates AI analysis
    3. Saves report to database
    4. Updates account status and tier

    Returns:
        JSON with new account status including tier, evidence, and freshness.
    """
    start_time = time.time()
    scan_data = None
    analysis_data = None

    # Phase 1: Run the deep scan (silent)
    try:
        for message in deep_scan_generator(company_name):
            # Check if this is the scan complete message
            if 'SCAN_COMPLETE:' in message:
                json_str = message.split('SCAN_COMPLETE:', 1)[1].strip()
                # Remove the SSE data prefix formatting
                if json_str.startswith('data: '):
                    json_str = json_str[6:]
                scan_data = json.loads(json_str)

    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Scan failed: {str(e)}'}), 500

    if not scan_data:
        return jsonify({'status': 'error', 'message': 'No scan data generated'}), 500

    # Phase 2: Generate AI analysis (silent)
    try:
        for message in generate_analysis(scan_data):
            # Check if this is the analysis complete message
            if 'ANALYSIS_COMPLETE:' in message:
                json_str = message.split('ANALYSIS_COMPLETE:', 1)[1].strip()
                if json_str.startswith('data: '):
                    json_str = json_str[6:]
                analysis_data = json.loads(json_str)

    except Exception as e:
        analysis_data = {'error': str(e), 'executive_summary': 'Analysis failed'}

    # Phase 3: Save report to database
    duration = time.time() - start_time

    try:
        report_id = save_report(
            company_name=company_name,
            github_org=scan_data.get('org_login', ''),
            scan_data=scan_data,
            ai_analysis=analysis_data or {},
            scan_duration=duration
        )
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Failed to save report: {str(e)}'}), 500

    # Phase 4: Update monitored account status
    try:
        account_result = update_account_status(scan_data, report_id)
        tier = account_result.get('tier', 0)
        tier_config = TIER_CONFIG.get(tier, TIER_CONFIG[0])

        # Format response with all frontend-needed data
        return jsonify({
            'status': 'success',
            'company': company_name,
            'new_tier': tier,
            'tier_name': tier_config['name'],
            'tier_color': tier_config['color'],
            'tier_emoji': tier_config['emoji'],
            'evidence': account_result.get('evidence', ''),
            'last_scanned': 'Just now',
            'tier_changed': account_result.get('tier_changed', False)
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Failed to update account status: {str(e)}'}), 500


@app.route('/api/scan-pending', methods=['POST'])
def api_scan_pending():
    """
    Scan all accounts that have never been scanned.

    Logic: Query monitored_accounts for all records where last_scanned_at
    is NULL or equal to created_at (meaning never scanned).

    Action: Loop through them and trigger a background scan for each.

    Returns:
        JSON with count of scans started.
    """
    from database import get_db_connection

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

    # Spawn background scan for each pending account
    scan_count = 0
    for company_name in pending_accounts:
        spawn_background_scan(company_name)
        scan_count += 1

    return jsonify({
        'status': 'success',
        'scans_started': scan_count,
        'accounts': pending_accounts
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


if __name__ == '__main__':
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000, threaded=True)
