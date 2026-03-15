"""
GitHub Dossier — Lightweight BDR Sequencing Tool

A Flask application for managing intent signals, campaigns, prospects,
and Apollo sequence enrollment for BDR outreach.
"""
import json
import hmac
import re
import time
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import requests
from datetime import datetime
from flask import (
    Flask, render_template, Response, request, jsonify,
    redirect, url_for, stream_with_context, g,
)
from werkzeug.exceptions import HTTPException
from config import Config
from database import (
    db_connection, get_setting, set_setting, log_audit_event,
    get_all_accounts, get_tier_counts, get_archived_count,
    get_archived_accounts, archive_account, unarchive_account,
    update_account_notes, add_account_to_tier_0,
    get_account_by_company, get_account,
    TIER_CONFIG, SCAN_STATUS_IDLE,
    # Campaigns
    create_campaign, update_campaign, delete_campaign,
    get_campaign, get_all_campaigns,
    # Sequence Mappings
    upsert_sequence_mapping, get_all_sequence_mappings,
    update_sequence_mapping, delete_sequence_mapping,
    search_sequence_mappings, toggle_sequence_mapping_enabled,
    get_campaigns_for_sequence,
    # Campaign Personas
    create_campaign_persona, update_campaign_persona,
    delete_campaign_persona, get_campaign_personas,
    replace_campaign_personas,
    # Enrollment
    create_enrollment_batch, get_enrollment_batch, update_enrollment_batch,
    get_enrollment_contacts, get_enrollment_batch_summary,
    get_next_contacts_for_phase,
    bulk_create_enrollment_contacts, update_enrollment_contact,
    # Scorecard (for enrollment enrichment)
    get_scorecard_score,
    # Signals
    get_signals_by_company,
)
from email_utils import (
    _PERSONAL_EMAIL_DOMAINS, _filter_personal_email,
    _derive_company_domain, _check_company_match,
)
from auth import auth_bp
from validators import (
    validate_company_name, validate_email,
    validate_search_query, validate_notes,
    validate_positive_int, validate_sort_direction,
    validate_csv_upload,
)
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Uptime tracking
_APP_START_TIME = time.time()

app = Flask(__name__)
app.config.from_object(Config)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Register Auth blueprint
app.register_blueprint(auth_bp)

# Register V2 blueprints (intent-signal-first platform)
try:
    from v2.routes.web import web_bp
    app.register_blueprint(web_bp)
except ImportError:
    logging.warning("[APP] v2.routes.web not found — skipping web blueprint")

try:
    from v2.routes.api import api_bp
    app.register_blueprint(api_bp)
except ImportError:
    logging.warning("[APP] v2.routes.api not found — skipping API blueprint")

try:
    from v2.routes.ingestion import ingestion_bp
    app.register_blueprint(ingestion_bp)
except ImportError:
    logging.warning("[APP] v2.routes.ingestion not found — skipping ingestion blueprint")

try:
    from v2.routes.draft import draft_bp
    app.register_blueprint(draft_bp)
except ImportError:
    logging.warning("[APP] v2.routes.draft not found — skipping draft blueprint")

try:
    from v2.routes.enrollment import enrollment_bp
    app.register_blueprint(enrollment_bp)
except ImportError:
    logging.warning("[APP] v2.routes.enrollment not found — skipping enrollment blueprint")

try:
    from v2.routes.webhooks import webhooks_bp
    app.register_blueprint(webhooks_bp)
except ImportError:
    logging.warning("[APP] v2.routes.webhooks not found — skipping webhooks blueprint")

try:
    from v2.routes.analytics import analytics_bp
    app.register_blueprint(analytics_bp)
except ImportError:
    logging.warning("[APP] v2.routes.analytics not found — skipping analytics blueprint")

try:
    from v2.routes.dedup import dedup_bp
    app.register_blueprint(dedup_bp)
except ImportError:
    logging.warning("[APP] v2.routes.dedup not found — skipping dedup blueprint")


# =============================================================================
# SECURITY MIDDLEWARE
# =============================================================================

_PUBLIC_PREFIXES = ('/static/', '/login', '/logout',
                    '/.well-known/', '/authorize', '/token', '/register',
                    '/sse', '/messages')
_PUBLIC_ENDPOINTS = {
    'index', 'health_check', 'serve_favicon',
    'inject_cache_buster', 'add_security_headers',
    'auth.login', 'auth.logout',
}


@app.before_request
def enforce_authentication():
    """API key authentication middleware (opt-in)."""
    api_key = Config.API_KEY
    if not api_key:
        return

    for prefix in _PUBLIC_PREFIXES:
        if request.path.startswith(prefix):
            return

    if request.endpoint in _PUBLIC_ENDPOINTS:
        return

    if not request.path.startswith(('/api/', '/v2/api/')) and request.method == 'GET':
        return

    from urllib.parse import urlparse
    referer = request.headers.get('Referer', '')
    origin = request.headers.get('Origin', '')
    request_host = request.host
    if referer:
        parsed = urlparse(referer)
        if parsed.netloc == request_host:
            return
    if origin:
        parsed = urlparse(origin)
        if parsed.netloc == request_host:
            return

    provided_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    if not provided_key or not hmac.compare_digest(provided_key, api_key):
        log_audit_event('auth_failure', f'path={request.path} method={request.method}', ip_address=request.remote_addr)
        return jsonify({'status': 'error', 'message': 'Unauthorized: invalid or missing API key'}), 401


@app.before_request
def enforce_csrf_protection():
    """CSRF protection via Origin/Referer validation for state-mutating requests."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return

    if request.path in ('/authorize', '/token', '/register') or request.path.startswith(('/messages', '/.well-known')):
        return

    origin = request.headers.get('Origin')
    referer = request.headers.get('Referer')

    if not origin and not referer:
        return

    trusted_host = request.host
    if origin:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        if parsed.netloc == trusted_host:
            return
    if referer:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        if parsed.netloc == trusted_host:
            return

    return jsonify({'status': 'error', 'message': 'CSRF validation failed: request origin mismatch'}), 403


# =============================================================================
# CORS for Claude CoWork
# =============================================================================

_CORS_PATHS = ('/.well-known/', '/authorize', '/token', '/register', '/sse', '/messages')


def _get_allowed_cors_origin():
    """Return the request Origin if it's on the allowlist, else None."""
    origin = request.headers.get('Origin', '')
    if not origin:
        return None
    if origin in ('https://claude.ai', 'https://console.anthropic.com'):
        return origin
    if origin.startswith('https://') and origin.endswith('.claude.ai'):
        return origin
    if origin.startswith('https://') and origin.endswith('.anthropic.com'):
        return origin
    if origin.startswith('http://localhost'):
        return origin
    return None


@app.before_request
def handle_cors_preflight():
    """Handle OPTIONS preflight requests for MCP/OAuth endpoints."""
    if request.method == 'OPTIONS' and any(request.path.startswith(p) for p in _CORS_PATHS):
        allowed = _get_allowed_cors_origin()
        if not allowed:
            return Response('Origin not allowed', status=403)
        resp = Response('', status=204)
        resp.headers['Access-Control-Allow-Origin'] = allowed
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
        resp.headers['Access-Control-Max-Age'] = '86400'
        return resp


@app.after_request
def add_security_headers(response):
    """Add security headers to every response."""
    if any(request.path.startswith(p) for p in _CORS_PATHS):
        allowed = _get_allowed_cors_origin()
        if allowed:
            response.headers['Access-Control-Allow-Origin'] = allowed
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
            response.headers['Access-Control-Expose-Headers'] = 'Content-Type'

    if 'text/html' in response.content_type or 'text/css' in response.content_type or 'javascript' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'

    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.datatables.net https://unpkg.com https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.datatables.net https://fonts.googleapis.com https://cdn.tailwindcss.com https://unpkg.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'"
    )

    return response


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def strip_code_fences(text: str) -> str:
    """Remove markdown code fences from AI responses."""
    text = text.strip()
    text = re.sub(r'^```\w*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text


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
    return {'cache_bust': int(time.time()), 'dashboard_only_mode': True}


# =============================================================================
# BASIC ROUTES
# =============================================================================

@app.route('/health')
def health_ping():
    """Lightweight health check."""
    return 'ok', 200


@app.route('/api/health')
def health_check():
    """Health check with database connectivity."""
    checks = {}
    overall_status = 'healthy'

    try:
        t0 = time.time()
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT 1')
            cur.fetchone()
        latency_ms = round((time.time() - t0) * 1000, 1)
        checks['database'] = {'status': 'ok', 'latency_ms': latency_ms}
    except Exception as e:
        checks['database'] = {'status': 'error', 'latency_ms': None}
        overall_status = 'degraded'
        logging.warning(f'Health check: database connectivity failed: {e}')

    uptime_seconds = round(time.time() - _APP_START_TIME, 1)
    timestamp = datetime.utcnow().isoformat() + 'Z'

    payload = {
        'status': overall_status,
        'checks': checks,
        'uptime_seconds': uptime_seconds,
        'timestamp': timestamp,
    }

    status_code = 200 if overall_status == 'healthy' else 503
    return jsonify(payload), status_code


@app.route('/favicon.ico')
def favicon():
    return Response(status=204)


@app.route('/')
def index():
    """Redirect to the v2 signal-queue UI."""
    return redirect('/app')


# =============================================================================
# OAUTH + MCP PROXY (for Claude CoWork integration)
# =============================================================================

_oauth_pending_codes = {}
_oauth_registered_clients = {}
_oauth_access_tokens = {}
_oauth_csrf_tokens = {}

_OAUTH_CODE_TTL = 300
_OAUTH_TOKEN_TTL = 86400
_OAUTH_CLIENT_TTL = 86400
_OAUTH_CSRF_TTL = 600
_MAX_REGISTERED_CLIENTS = 100
_MAX_PENDING_CODES = 200
_MAX_ACCESS_TOKENS = 500


@app.route('/.well-known/oauth-protected-resource')
def oauth_protected_resource():
    """RFC 9728 — OAuth Protected Resource Metadata (MCP spec 2025)."""
    base = request.url_root.rstrip('/')
    return jsonify({
        'resource': f'{base}/sse',
        'authorization_servers': [base],
    })


@app.route('/.well-known/oauth-authorization-server')
def oauth_metadata():
    """RFC 8414 — OAuth Authorization Server Metadata."""
    base = request.url_root.rstrip('/')
    return jsonify({
        'issuer': base,
        'authorization_endpoint': f'{base}/authorize',
        'token_endpoint': f'{base}/token',
        'registration_endpoint': f'{base}/register',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code'],
        'token_endpoint_auth_methods_supported': ['none'],
        'code_challenge_methods_supported': ['S256'],
    })


@app.route('/register', methods=['POST'])
def oauth_register():
    """RFC 7591 — Dynamic Client Registration."""
    import secrets as _secrets

    if request.content_type and 'json' in request.content_type:
        data = request.get_json(silent=True) or {}
    else:
        data = {}

    now = time.time()
    expired = [k for k, v in _oauth_registered_clients.items()
               if v.get('_expires_at', float('inf')) < now]
    for k in expired:
        del _oauth_registered_clients[k]
    if len(_oauth_registered_clients) >= _MAX_REGISTERED_CLIENTS:
        return jsonify({'error': 'server_error',
                        'error_description': 'Too many registered clients'}), 503

    redirect_uris = data.get('redirect_uris', [])
    if not isinstance(redirect_uris, list):
        return jsonify({'error': 'invalid_request',
                        'error_description': 'redirect_uris must be an array'}), 400
    for uri in redirect_uris:
        if not isinstance(uri, str) or not uri.startswith('https://'):
            if not (isinstance(uri, str) and uri.startswith('http://localhost')):
                return jsonify({'error': 'invalid_request',
                                'error_description': 'redirect_uris must use https'}), 400

    client_id = _secrets.token_urlsafe(16)
    registration = {
        'client_id': client_id,
        'client_name': data.get('client_name', 'Claude CoWork'),
        'redirect_uris': redirect_uris,
        'grant_types': data.get('grant_types', ['authorization_code']),
        'response_types': data.get('response_types', ['code']),
        'token_endpoint_auth_method': 'none',
        'client_id_issued_at': int(now),
        'client_secret_expires_at': 0,
        '_expires_at': now + _OAUTH_CLIENT_TTL,
    }

    _oauth_registered_clients[client_id] = registration
    return jsonify(registration), 201


@app.route('/authorize', methods=['GET'])
def oauth_authorize():
    """Show OAuth approval page."""
    import html as _html
    import secrets as _secrets

    redirect_uri = request.args.get('redirect_uri', '')
    state = request.args.get('state', '')
    response_type = request.args.get('response_type', '')
    code_challenge = request.args.get('code_challenge', '')
    code_challenge_method = request.args.get('code_challenge_method', '')
    client_id = request.args.get('client_id', '')

    if response_type != 'code':
        return jsonify({'error': 'unsupported_response_type'}), 400
    if not redirect_uri:
        return jsonify({'error': 'invalid_request', 'error_description': 'missing redirect_uri'}), 400
    if not client_id:
        return jsonify({'error': 'invalid_request', 'error_description': 'missing client_id'}), 400

    client = _oauth_registered_clients.get(client_id)
    if not client:
        return jsonify({'error': 'invalid_client', 'error_description': 'unknown client_id'}), 400
    registered_uris = client.get('redirect_uris', [])
    if registered_uris and redirect_uri not in registered_uris:
        return jsonify({'error': 'invalid_request',
                        'error_description': 'redirect_uri not registered for this client'}), 400

    if not code_challenge:
        return jsonify({'error': 'invalid_request',
                        'error_description': 'code_challenge is required (PKCE S256)'}), 400

    csrf_token = _secrets.token_urlsafe(24)
    now = time.time()
    _oauth_csrf_tokens[csrf_token] = now + _OAUTH_CSRF_TTL
    expired = [k for k, v in _oauth_csrf_tokens.items() if v < now]
    for k in expired:
        del _oauth_csrf_tokens[k]

    e_redirect = _html.escape(redirect_uri, quote=True)
    e_state = _html.escape(state, quote=True)
    e_challenge = _html.escape(code_challenge, quote=True)
    e_method = _html.escape(code_challenge_method, quote=True)
    e_client = _html.escape(client_id, quote=True)
    e_csrf = _html.escape(csrf_token, quote=True)

    page = f"""<!DOCTYPE html>
<html><head><title>Authorize Lead Machine</title>
<style>
  body {{ font-family: -apple-system, sans-serif; display: flex;
         justify-content: center; align-items: center; height: 100vh;
         margin: 0; background: #f5f5f5; }}
  .card {{ background: white; padding: 2rem 3rem; border-radius: 12px;
           box-shadow: 0 2px 10px rgba(0,0,0,.1); text-align: center;
           max-width: 400px; }}
  h2 {{ margin-top: 0; }}
  .btn {{ background: #e84b3a; color: white; border: none; padding: 12px 32px;
          border-radius: 8px; font-size: 16px; cursor: pointer; margin-top: 1rem; }}
  .btn:hover {{ background: #d43d2e; }}
  .info {{ color: #666; font-size: 14px; margin-top: 12px; }}
</style></head>
<body><div class="card">
  <h2>Authorize Lead Machine</h2>
  <p>Claude CoWork is requesting access to your Lead Machine MCP server.</p>
  <form method="POST" action="/authorize">
    <input type="hidden" name="redirect_uri" value="{e_redirect}">
    <input type="hidden" name="state" value="{e_state}">
    <input type="hidden" name="code_challenge" value="{e_challenge}">
    <input type="hidden" name="code_challenge_method" value="{e_method}">
    <input type="hidden" name="client_id" value="{e_client}">
    <input type="hidden" name="csrf_token" value="{e_csrf}">
    <button type="submit" class="btn">Approve</button>
  </form>
  <p class="info">This grants access to account data, signals, and Apollo tools.</p>
</div></body></html>"""
    return Response(page, content_type='text/html')


@app.route('/authorize', methods=['POST'])
def oauth_authorize_post():
    """Handle approval — generate auth code and redirect to callback."""
    import secrets as _secrets

    redirect_uri = request.form.get('redirect_uri', '')
    state = request.form.get('state', '')
    code_challenge = request.form.get('code_challenge', '')
    code_challenge_method = request.form.get('code_challenge_method', '')
    client_id = request.form.get('client_id', '')
    csrf_token = request.form.get('csrf_token', '')

    csrf_expires = _oauth_csrf_tokens.pop(csrf_token, None)
    if not csrf_expires or csrf_expires < time.time():
        return jsonify({'error': 'invalid_request', 'error_description': 'invalid or expired CSRF token'}), 403

    if not redirect_uri:
        return jsonify({'error': 'invalid_request', 'error_description': 'missing redirect_uri'}), 400
    if not client_id:
        return jsonify({'error': 'invalid_request', 'error_description': 'missing client_id'}), 400

    client = _oauth_registered_clients.get(client_id)
    if not client:
        return jsonify({'error': 'invalid_client', 'error_description': 'unknown client_id'}), 400
    registered_uris = client.get('redirect_uris', [])
    if registered_uris and redirect_uri not in registered_uris:
        return jsonify({'error': 'invalid_request',
                        'error_description': 'redirect_uri not registered for this client'}), 400

    now = time.time()
    expired = [k for k, v in _oauth_pending_codes.items() if v['expires'] < now]
    for k in expired:
        del _oauth_pending_codes[k]
    if len(_oauth_pending_codes) >= _MAX_PENDING_CODES:
        return jsonify({'error': 'server_error', 'error_description': 'too many pending codes'}), 503

    code = _secrets.token_urlsafe(32)
    _oauth_pending_codes[code] = {
        'redirect_uri': redirect_uri,
        'code_challenge': code_challenge,
        'code_challenge_method': code_challenge_method,
        'client_id': client_id,
        'expires': now + _OAUTH_CODE_TTL,
    }

    sep = '&' if '?' in redirect_uri else '?'
    location = f'{redirect_uri}{sep}code={code}'
    if state:
        location += f'&state={state}'
    return redirect(location, code=302)


@app.route('/token', methods=['POST'])
def oauth_token():
    """Exchange auth code for a per-session Bearer token."""
    import hashlib, base64
    import secrets as _secrets

    if request.content_type and 'json' in request.content_type:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form

    grant_type = data.get('grant_type', '')
    code = data.get('code', '')
    code_verifier = data.get('code_verifier', '')
    client_id = data.get('client_id', '')
    token_redirect_uri = data.get('redirect_uri', '')

    if grant_type != 'authorization_code':
        return jsonify({'error': 'unsupported_grant_type'}), 400

    entry = _oauth_pending_codes.pop(code, None)
    if not entry or entry['expires'] < time.time():
        return jsonify({'error': 'invalid_grant'}), 400

    if entry.get('client_id') and entry['client_id'] != client_id:
        return jsonify({'error': 'invalid_grant',
                        'error_description': 'client_id mismatch'}), 400

    if entry.get('redirect_uri') and entry['redirect_uri'] != token_redirect_uri:
        return jsonify({'error': 'invalid_grant',
                        'error_description': 'redirect_uri mismatch'}), 400

    stored_challenge = entry.get('code_challenge', '')
    if stored_challenge:
        if not code_verifier:
            return jsonify({'error': 'invalid_request',
                            'error_description': 'code_verifier is required'}), 400
        digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
        if computed != stored_challenge:
            return jsonify({'error': 'invalid_grant',
                            'error_description': 'PKCE verification failed'}), 400

    api_key = os.environ.get('MCP_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'server_error', 'error_description': 'MCP_API_KEY not configured'}), 500

    now = time.time()
    expired = [k for k, v in _oauth_access_tokens.items() if v['expires'] < now]
    for k in expired:
        del _oauth_access_tokens[k]
    if len(_oauth_access_tokens) >= _MAX_ACCESS_TOKENS:
        return jsonify({'error': 'server_error', 'error_description': 'token limit reached'}), 503

    access_token = _secrets.token_urlsafe(32)
    _oauth_access_tokens[access_token] = {
        'api_key': api_key,
        'client_id': client_id,
        'issued_at': now,
        'expires': now + _OAUTH_TOKEN_TTL,
    }

    return jsonify({
        'access_token': access_token,
        'token_type': 'bearer',
        'expires_in': _OAUTH_TOKEN_TTL,
    })


# ---------------------------------------------------------------------------
# MCP SSE Proxy
# ---------------------------------------------------------------------------

_MCP_INTERNAL = 'http://localhost:5001'


def _translate_bearer_token(auth_header: str):
    """Translate a per-session OAuth token to the real MCP API key."""
    if not auth_header.startswith('Bearer '):
        return None
    session_token = auth_header[7:]
    entry = _oauth_access_tokens.get(session_token)
    if not entry:
        return None
    if entry['expires'] < time.time():
        _oauth_access_tokens.pop(session_token, None)
        return None
    return f"Bearer {entry['api_key']}"


@app.route('/sse')
def proxy_sse():
    """Proxy SSE stream from MCP server, rewriting endpoint URLs."""
    auth = request.headers.get('Authorization', '')

    real_auth = _translate_bearer_token(auth)
    if not real_auth:
        resource_url = f"{request.scheme}://{request.host}/.well-known/oauth-protected-resource"
        return (
            jsonify({'error': 'invalid_token',
                     'error_description': 'Missing or expired access token'}),
            401,
            {'WWW-Authenticate': f'Bearer realm="Lead Machine MCP", resource_metadata="{resource_url}"'},
        )

    try:
        mcp_resp = requests.get(
            f'{_MCP_INTERNAL}/sse',
            headers={'Authorization': real_auth},
            stream=True,
            timeout=(5, None),
        )
    except requests.ConnectionError:
        return jsonify({'error': 'MCP server not available on port 5001'}), 502

    if mcp_resp.status_code != 200:
        return Response(
            mcp_resp.content,
            status=mcp_resp.status_code,
            content_type=mcp_resp.headers.get('content-type', 'application/json'),
        )

    public_base = request.url_root.rstrip('/')

    def rewrite_stream():
        for line in mcp_resp.iter_lines(decode_unicode=True):
            if line is None:
                yield '\n'
            else:
                line = line.replace(f'{_MCP_INTERNAL}', public_base)
                line = line.replace('http://0.0.0.0:5001', public_base)
                if line.startswith('data: /messages/'):
                    line = f'data: {public_base}{line[6:]}'
                yield line + '\n'

    return Response(
        stream_with_context(rewrite_stream()),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


@app.route('/messages/', methods=['POST'])
@app.route('/messages', methods=['POST'])
def proxy_messages():
    """Proxy message POSTs to MCP server."""
    auth = request.headers.get('Authorization', '')

    real_auth = _translate_bearer_token(auth)
    if not real_auth:
        resource_url = f"{request.scheme}://{request.host}/.well-known/oauth-protected-resource"
        return (
            jsonify({'error': 'invalid_token',
                     'error_description': 'Missing or expired access token'}),
            401,
            {'WWW-Authenticate': f'Bearer realm="Lead Machine MCP", resource_metadata="{resource_url}"'},
        )

    qs = request.query_string.decode()
    try:
        mcp_resp = requests.post(
            f'{_MCP_INTERNAL}/messages/?{qs}' if qs else f'{_MCP_INTERNAL}/messages/',
            data=request.get_data(),
            headers={
                'Content-Type': request.content_type or 'application/json',
                'Authorization': real_auth,
            },
            timeout=30,
        )
    except requests.ConnectionError:
        return jsonify({'error': 'MCP server not available on port 5001'}), 502

    return Response(mcp_resp.content, status=mcp_resp.status_code,
                    content_type=mcp_resp.headers.get('content-type', 'application/json'))


# =============================================================================
# CAMPAIGN MANAGEMENT
# =============================================================================

@app.route('/campaigns')
def campaigns():
    """Campaigns dashboard."""
    all_campaigns = get_all_campaigns()
    return render_template('campaigns.html', campaigns=all_campaigns)


@app.route('/campaigns/new')
def campaign_new():
    return render_template('campaign_form.html', campaign=None)


@app.route('/campaigns/<int:campaign_id>/edit')
def campaign_edit(campaign_id):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return redirect(url_for('campaigns'))
    return render_template('campaign_form.html', campaign=campaign)


@app.route('/mapping-sequences')
def mapping_sequences():
    """Mapping Sequences — browse enabled Apollo sequences."""
    mappings = get_all_sequence_mappings(enabled_only=True)
    return render_template('mapping_sequences.html', mappings=mappings)


# =============================================================================
# SEQUENCE MAPPINGS API
# =============================================================================

def _sync_sequences_from_apollo():
    """Pull sequences from Apollo and upsert into sequence_mappings table."""
    import requests as req

    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        return 0, 'Apollo API key not configured. Add APOLLO_API_KEY in Settings.'

    try:
        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}

        all_sequences = []
        page = 1
        while True:
            resp = req.post('https://api.apollo.io/api/v1/emailer_campaigns/search',
                           json={'page': page},
                           headers=apollo_headers,
                           timeout=15)

            if resp.status_code == 403:
                return 0, 'API key lacks permission. Ensure you are using a Master API key.'
            if resp.status_code != 200:
                logging.error(f"[SEQUENCE SYNC] Apollo returned {resp.status_code}: {resp.text[:500]}")
                return 0, f'Apollo API returned {resp.status_code}'

            data = resp.json()
            batch = data.get('emailer_campaigns', [])
            all_sequences.extend(batch)

            pagination = data.get('pagination', {})
            if page >= pagination.get('total_pages', 1):
                break
            page += 1

        synced = 0
        for seq in all_sequences:
            seq_id = seq.get('id')
            if not seq_id:
                continue
            num_steps = seq.get('num_steps', 0)
            config_type = None

            owner_name = None
            creator = seq.get('user') or seq.get('creator') or {}
            if isinstance(creator, dict):
                first = (creator.get('first_name') or '').strip()
                last = (creator.get('last_name') or '').strip()
                if first or last:
                    owner_name = f"{first} {last}".strip()

            upsert_sequence_mapping(
                sequence_id=seq_id,
                sequence_name=seq.get('name', 'Unnamed Sequence'),
                sequence_config=config_type,
                num_steps=num_steps,
                active=seq.get('active', False),
                owner_name=owner_name,
            )
            synced += 1

        return synced, None
    except Exception as e:
        logging.error(f"[SEQUENCE MAPPINGS SYNC ERROR] {e}")
        return 0, 'Failed to sync sequences from Apollo'


@app.route('/api/sequence-mappings/sync', methods=['POST'])
def api_sequence_mappings_sync():
    synced, error = _sync_sequences_from_apollo()
    if error:
        return jsonify({'status': 'error', 'message': error}), 400 if 'not configured' in error else 502
    mappings = get_all_sequence_mappings()
    return jsonify({'status': 'success', 'synced': synced, 'mappings': mappings})


@app.route('/api/sequence-mappings/<int:mapping_id>', methods=['PUT'])
def api_sequence_mapping_update(mapping_id):
    data = request.get_json() or {}
    updates = {}
    if 'owner_name' in data:
        updates['owner_name'] = (data['owner_name'] or '').strip() or None
    if not updates:
        return jsonify({'status': 'error', 'message': 'No fields to update'}), 400
    update_sequence_mapping(mapping_id, **updates)
    return jsonify({'status': 'success'})


@app.route('/api/sequence-mappings/<int:mapping_id>', methods=['DELETE'])
def api_sequence_mapping_delete(mapping_id):
    delete_sequence_mapping(mapping_id)
    return jsonify({'status': 'success'})


@app.route('/api/sequence-mappings/search', methods=['GET'])
def api_sequence_mappings_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'status': 'success', 'results': []})
    valid, q = validate_search_query(q)
    if not valid:
        return jsonify({'status': 'error', 'message': q}), 400
    results = search_sequence_mappings(q, enabled_only=False)
    return jsonify({'status': 'success', 'results': results})


@app.route('/api/sequence-mappings/<int:mapping_id>/toggle', methods=['POST'])
def api_sequence_mapping_toggle(mapping_id):
    data = request.get_json() or {}
    enabled = bool(data.get('enabled', False))
    changed = toggle_sequence_mapping_enabled(mapping_id, enabled)
    if not changed:
        return jsonify({'status': 'error', 'message': 'Mapping not found'}), 404
    return jsonify({'status': 'success', 'enabled': enabled})


@app.route('/api/sequence-mappings/enabled', methods=['GET'])
def api_sequence_mappings_enabled():
    mappings = get_all_sequence_mappings(enabled_only=True)
    return jsonify({'status': 'success', 'sequences': mappings})


# =============================================================================
# CAMPAIGNS API
# =============================================================================

@app.route('/api/campaigns/by-sequence/<sequence_id>')
def api_campaigns_by_sequence(sequence_id):
    campaigns_list = get_campaigns_for_sequence(sequence_id)
    return jsonify({'status': 'success', 'campaigns': campaigns_list})


@app.route('/api/campaigns', methods=['GET'])
def api_campaigns_list():
    return jsonify({'status': 'success', 'campaigns': get_all_campaigns()})


@app.route('/api/campaigns', methods=['POST'])
def api_campaigns_create():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'Campaign name is required'}), 400

    prompt = (data.get('prompt') or '').strip()
    assets = data.get('assets', [])
    if isinstance(assets, str):
        assets = [a.strip() for a in assets.split('\n') if a.strip()]
    sequence_id = (data.get('sequence_id') or '').strip() or None
    sequence_name = (data.get('sequence_name') or '').strip() or None
    sequence_config = (data.get('sequence_config') or '').strip() or None
    tone = (data.get('tone') or '').strip() or None
    try:
        contact_cap = int(data.get('contact_cap', 20))
    except (TypeError, ValueError):
        contact_cap = 20
    if contact_cap < 1 or contact_cap > 200:
        return jsonify({'status': 'error', 'message': 'contact_cap must be between 1 and 200'}), 400
    verified_emails_only = data.get('verified_emails_only', 0)
    review_in_tool = data.get('review_in_tool', 1)

    result = create_campaign(name, prompt, assets, sequence_id, sequence_name, sequence_config,
                             contact_cap=contact_cap, verified_emails_only=verified_emails_only,
                             review_in_tool=review_in_tool, tone=tone)

    personas_data = data.get('personas', [])
    if personas_data and result.get('id'):
        replace_campaign_personas(result['id'], personas_data)

    return jsonify({'status': 'success', 'campaign': result})


@app.route('/api/campaigns/<int:campaign_id>', methods=['GET'])
def api_campaigns_get(campaign_id):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404
    return jsonify({'status': 'success', 'campaign': campaign})


@app.route('/api/campaigns/<int:campaign_id>', methods=['PUT'])
def api_campaigns_update(campaign_id):
    data = request.get_json() or {}
    personas_data = data.pop('personas', None)
    updated = update_campaign(campaign_id, **data)
    if personas_data is not None:
        replace_campaign_personas(campaign_id, personas_data)
        updated = True
    if not updated:
        return jsonify({'status': 'error', 'message': 'Campaign not found or no changes'}), 404
    return jsonify({'status': 'success', 'updated': True})


@app.route('/api/campaigns/<int:campaign_id>', methods=['DELETE'])
def api_campaigns_delete(campaign_id):
    deleted = delete_campaign(campaign_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404
    return jsonify({'status': 'success', 'deleted': True})


@app.route('/api/campaigns/<int:campaign_id>/activate', methods=['POST'])
def api_campaigns_activate(campaign_id):
    campaign = get_campaign(campaign_id)
    if not campaign:
        return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404
    new_status = 'active' if campaign['status'] == 'draft' else 'draft'
    update_campaign(campaign_id, status=new_status)
    return jsonify({'status': 'success', 'new_status': new_status})


@app.route('/api/campaigns/<int:campaign_id>/stats')
def api_campaign_stats(campaign_id):
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT
                COALESCE(SUM(total_contacts), 0) AS total_enrolled,
                COALESCE(SUM(generated), 0) AS total_generated,
                COALESCE(SUM(failed), 0) AS total_failed
            FROM enrollment_batches WHERE campaign_id = ?
        ''', (campaign_id,))
        row = cursor.fetchone()
    return jsonify({
        'status': 'success',
        'total_enrolled': row['total_enrolled'] if row else 0,
        'total_generated': row['total_generated'] if row else 0,
        'total_failed': row['total_failed'] if row else 0,
    })


# Campaign Personas API

@app.route('/api/campaigns/<int:campaign_id>/personas', methods=['GET'])
def api_campaign_personas_list(campaign_id):
    personas = get_campaign_personas(campaign_id)
    return jsonify({'status': 'success', 'personas': personas})


@app.route('/api/campaigns/<int:campaign_id>/personas', methods=['POST'])
def api_campaign_personas_create(campaign_id):
    data = request.get_json() or {}
    persona_name = (data.get('persona_name') or '').strip()
    if not persona_name:
        return jsonify({'status': 'error', 'message': 'Persona name is required'}), 400
    sequence_id = (data.get('sequence_id') or '').strip()
    if not sequence_id:
        return jsonify({'status': 'error', 'message': 'Sequence is required'}), 400
    titles = data.get('titles', [])
    seniorities = data.get('seniorities', [])
    sequence_name = data.get('sequence_name', '')
    priority = data.get('priority', 0)
    result = create_campaign_persona(campaign_id, persona_name, titles, seniorities,
                                      sequence_id, sequence_name, priority)
    return jsonify({'status': 'success', 'persona': result})


@app.route('/api/campaigns/<int:campaign_id>/personas/<int:persona_id>', methods=['PUT'])
def api_campaign_personas_update(campaign_id, persona_id):
    data = request.get_json() or {}
    updated = update_campaign_persona(persona_id, **data)
    if not updated:
        return jsonify({'status': 'error', 'message': 'Persona not found or no changes'}), 404
    return jsonify({'status': 'success', 'updated': True})


@app.route('/api/campaigns/<int:campaign_id>/personas/<int:persona_id>', methods=['DELETE'])
def api_campaign_personas_delete(campaign_id, persona_id):
    deleted = delete_campaign_persona(persona_id)
    if not deleted:
        return jsonify({'status': 'error', 'message': 'Persona not found'}), 404
    return jsonify({'status': 'success', 'deleted': True})


@app.route('/api/campaigns/<int:campaign_id>/personas/replace', methods=['POST'])
def api_campaign_personas_replace(campaign_id):
    data = request.get_json() or {}
    personas = data.get('personas', [])
    count = replace_campaign_personas(campaign_id, personas)
    return jsonify({'status': 'success', 'count': count})


@app.route('/api/campaigns/<int:campaign_id>/suggest-personas', methods=['POST'])
def api_campaign_suggest_personas(campaign_id):
    """Use AI to suggest target buyer personas."""
    from openai import OpenAI

    campaign = get_campaign(campaign_id)
    if not campaign:
        return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404

    api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
    base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')
    if not api_key or not base_url:
        return jsonify({'status': 'error', 'message': 'AI not configured'}), 500

    prompt_text = campaign.get('prompt', '')
    tone = campaign.get('tone', '')
    assets = campaign.get('assets', '')

    system_msg = (
        "You are a B2B sales strategist specializing in localization and internationalization software. "
        "Based on the campaign context below, suggest 3-5 target buyer personas for cold email outreach. "
        "For each persona, provide:\n"
        "- persona_name: A clear role title (e.g., 'Head of Localization')\n"
        "- titles: Array of 2-4 job title search terms for Apollo People Search\n"
        "- seniorities: Array from ['director', 'vp', 'c_suite', 'manager', 'senior']\n"
        "- reasoning: One sentence explaining why this persona is a good target\n\n"
        "Respond with a JSON array only, no markdown."
    )

    user_msg = f"Campaign prompt: {prompt_text}\n"
    if tone:
        user_msg += f"Tone: {tone}\n"
    if assets:
        user_msg += f"Assets/links: {assets}\n"

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model='gpt-5-mini',
            messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg},
            ],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        suggestions = json.loads(raw)
        if not isinstance(suggestions, list):
            suggestions = [suggestions]
        return jsonify({'status': 'success', 'suggestions': suggestions})
    except Exception as e:
        logging.error(f"[CAMPAIGN PERSONAS] AI suggestion failed: {e}")
        return jsonify({'status': 'error', 'message': 'AI suggestion failed. Please try again.'}), 500


# =============================================================================
# ENROLLMENT PIPELINE
# =============================================================================

import threading as _threading


class _RateLimiter:
    """Simple token-bucket rate limiter for API calls."""
    def __init__(self, max_per_minute=50):
        self.interval = 60.0 / max_per_minute
        self.last_call = 0
        self._lock = _threading.Lock()

    def wait(self):
        import time as _time
        with self._lock:
            now = _time.time()
            elapsed = now - self.last_call
            if elapsed < self.interval:
                _time.sleep(self.interval - elapsed)
            self.last_call = _time.time()


_apollo_limiter = _RateLimiter(max_per_minute=50)
_openai_limiter = _RateLimiter(max_per_minute=30)


def _derive_domain(website: str, company_name: str = '') -> str:
    """Derive a domain from a website URL or company name."""
    if website:
        d = website.strip().lower()
        for prefix in ['https://', 'http://', 'www.']:
            d = d.replace(prefix, '')
        return d.split('/')[0].strip()
    name = company_name.strip().lower()
    for suffix in [' inc', ' inc.', ' llc', ' corp', ' ltd', ' co']:
        name = name.replace(suffix, '')
    return name.replace(' ', '') + '.com'


def _discovery_worker(batch_id: int):
    """Background worker: discover contacts at target accounts via Apollo People Search."""
    import requests as req

    try:
        batch = get_enrollment_batch(batch_id)
        if not batch:
            return
        campaign = get_campaign(batch['campaign_id'])
        if not campaign:
            update_enrollment_batch(batch_id, status='failed', error_message='Campaign not found')
            return
        personas = campaign.get('personas', [])
        if not personas:
            update_enrollment_batch(batch_id, status='failed', error_message='No personas configured')
            return

        apollo_key = os.environ.get('APOLLO_API_KEY', '')
        if not apollo_key:
            update_enrollment_batch(batch_id, status='failed', error_message='Apollo API key not configured')
            return

        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}
        update_enrollment_batch(batch_id, status='discovering',
                                started_at=datetime.now().isoformat())

        account_ids = batch.get('account_ids', [])
        with db_connection() as conn:
            cursor = conn.cursor()
            if account_ids:
                placeholders = ','.join('?' * len(account_ids))
                cursor.execute(
                    f'SELECT id, company_name, website, github_org FROM monitored_accounts WHERE id IN ({placeholders})',
                    account_ids
                )
            else:
                cursor.execute('SELECT id, company_name, website, github_org FROM monitored_accounts LIMIT 0')
            accounts = [dict(r) for r in cursor.fetchall()]

        verified_only = bool(campaign.get('verified_emails_only'))
        contact_cap = campaign.get('contact_cap') or 20

        seen_emails = set()
        contacts_to_insert = []
        total_discovered = 0

        for i, acct in enumerate(accounts):
            domain = _derive_domain(acct.get('website', ''), acct.get('company_name', ''))
            update_enrollment_batch(batch_id,
                current_phase=f'Discovering at {acct["company_name"]} ({i+1}/{len(accounts)})...')
            acct_contacts = 0

            for persona in personas:
                if acct_contacts >= contact_cap:
                    break
                titles = persona.get('titles', [])
                seniorities = persona.get('seniorities', [])
                if not titles and not seniorities:
                    continue

                _apollo_limiter.wait()
                try:
                    search_payload = {
                        'q_organization_domains_list': [domain],
                        'per_page': 25,
                        'page': 1
                    }
                    if titles:
                        search_payload['person_titles'] = titles
                    if seniorities:
                        search_payload['person_seniorities'] = seniorities
                    if verified_only:
                        search_payload['email_status'] = ['verified']

                    resp = req.post(
                        'https://api.apollo.io/v1/mixed_people/search',
                        json=search_payload,
                        headers=apollo_headers,
                        timeout=15
                    )
                    if resp.status_code != 200:
                        app.logger.warning(f'Apollo People Search failed for {domain}: {resp.status_code}')
                        continue

                    data = resp.json()
                    people = data.get('people', [])

                    for person in people:
                        if acct_contacts >= contact_cap:
                            break
                        email = (person.get('email') or '').lower().strip()
                        if not email or email in seen_emails:
                            continue
                        seen_emails.add(email)
                        acct_contacts += 1

                        contacts_to_insert.append({
                            'batch_id': batch_id,
                            'account_id': acct['id'],
                            'company_name': acct['company_name'],
                            'company_domain': domain,
                            'persona_name': persona.get('persona_name', 'Default'),
                            'sequence_id': persona.get('sequence_id', ''),
                            'sequence_name': persona.get('sequence_name', ''),
                            'apollo_person_id': person.get('id', ''),
                            'first_name': person.get('first_name', ''),
                            'last_name': person.get('last_name', ''),
                            'email': email,
                            'title': person.get('title', ''),
                            'seniority': person.get('seniority', ''),
                            'linkedin_url': person.get('linkedin_url', ''),
                            'status': 'discovered'
                        })
                        total_discovered += 1

                except Exception as e:
                    app.logger.warning(f'Discovery error for {domain}/{persona.get("persona_name")}: {e}')

            if len(contacts_to_insert) >= 50:
                bulk_create_enrollment_contacts(contacts_to_insert)
                contacts_to_insert.clear()
                update_enrollment_batch(batch_id, discovered=total_discovered,
                                         total_contacts=total_discovered)

        if contacts_to_insert:
            bulk_create_enrollment_contacts(contacts_to_insert)

        update_enrollment_batch(batch_id,
            status='discovered',
            discovered=total_discovered,
            total_contacts=total_discovered,
            current_phase=f'Discovery complete — {total_discovered} contacts found')

    except Exception as e:
        app.logger.error(f'Discovery worker error for batch {batch_id}: {e}')
        update_enrollment_batch(batch_id, status='failed', error_message=str(e)[:500])


def _enrollment_pipeline_worker(batch_id: int):
    """Background worker: generate emails then enroll contacts into Apollo sequences."""
    import requests as req
    from openai import OpenAI

    try:
        batch = get_enrollment_batch(batch_id)
        if not batch:
            return
        campaign = get_campaign(batch['campaign_id'])
        if not campaign:
            update_enrollment_batch(batch_id, status='failed', error_message='Campaign not found')
            return

        apollo_key = os.environ.get('APOLLO_API_KEY', '')
        api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
        base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')

        if not apollo_key:
            update_enrollment_batch(batch_id, status='failed', error_message='Apollo API key not configured')
            return
        if not api_key or not base_url:
            update_enrollment_batch(batch_id, status='failed', error_message='OpenAI API not configured')
            return

        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}
        openai_client = OpenAI(api_key=api_key, base_url=base_url)

        # ===== PHASE 1: Email Generation =====
        update_enrollment_batch(batch_id, status='generating',
                                started_at=datetime.now().isoformat())

        generated_count = 0
        while True:
            contacts = get_next_contacts_for_phase(batch_id, 'discovered', limit=10)
            if not contacts:
                break
            b = get_enrollment_batch(batch_id)
            if b and b.get('status') == 'cancelled':
                return

            for contact in contacts:
                try:
                    account_data = {}
                    if contact.get('account_id'):
                        score = get_scorecard_score(contact['account_id'])
                        if score:
                            account_data = score
                        acct_info = get_account_by_company(contact['company_name'])
                        if acct_info:
                            account_data['evidence_summary'] = acct_info.get('evidence_summary', '')

                    systems_raw = account_data.get('systems_json', '{}')
                    try:
                        systems = json.loads(systems_raw) if systems_raw else {}
                    except (json.JSONDecodeError, TypeError):
                        systems = {}
                    active_systems = [k for k, v in systems.items() if v]

                    bdr_prompt = campaign.get('prompt', '').strip()
                    prompt = f"""You are a BDR at Phrase, a localization/internationalization platform. Write a personalized cold outreach sequence.

Account info:
- Company: {contact['company_name']}
- Annual Revenue: {account_data.get('annual_revenue', 'unknown')}
- Cohort: {account_data.get('cohort', 'B')} ({'Enterprise $1.5B+' if account_data.get('cohort') == 'A' else 'Mid-Market'})
- Languages/Locales detected: {account_data.get('locale_count', 0)}
- Systems in use: {', '.join(active_systems) if active_systems else 'unknown'}
- Evidence: {(account_data.get('evidence_summary', '') or '')[:300]}

Contact info:
- Name: {contact.get('first_name', '')} {contact.get('last_name', '')}
- Title: {contact.get('title', 'unknown')}
- Seniority: {contact.get('seniority', 'unknown')}
- Persona: {contact.get('persona_name', 'Default')}

{('Campaign Instructions: ' + bdr_prompt) if bdr_prompt else ''}

Write a 4-email cold outreach sequence targeting {contact.get('first_name', 'this person')} specifically. Reference their title/role and their company's signals.

Structure: subject_1 (thread 1) + subject_2 (thread 2) + email_1 + email_2 + email_3 + email_4

Rules:
- Reference the company by name and real signals (revenue tier, locale count, systems)
- Personalize to their specific role ({contact.get('title', 'their role')})
- Each email body: concise, value-driven, 3-4 sentences max
- End each email with a simple CTA
- No fluff. Sound like a human.
- Use \\n for line breaks in email bodies

Return ONLY valid JSON: {{"subject_1": "...", "subject_2": "...", "email_1": "...", "email_2": "...", "email_3": "...", "email_4": "..."}}"""

                    _openai_limiter.wait()
                    response = openai_client.chat.completions.create(
                        model="gpt-5-mini",
                        messages=[
                            {"role": "system", "content": "You are a BDR at Phrase. Write diverse, natural emails. Return ONLY valid JSON."},
                            {"role": "user", "content": prompt}
                        ],
                        response_format={"type": "json_object"},
                        max_completion_tokens=4096
                    )
                    email_data = json.loads(response.choices[0].message.content)
                    update_enrollment_contact(contact['id'],
                        generated_emails_json=json.dumps(email_data),
                        status='email_generated')
                    generated_count += 1

                except Exception as e:
                    app.logger.warning(f'Email gen error for contact {contact["id"]}: {e}')
                    update_enrollment_contact(contact['id'],
                        status='failed',
                        error_message=f'Email generation failed: {str(e)[:300]}')
                    batch = get_enrollment_batch(batch_id)
                    update_enrollment_batch(batch_id, failed=(batch or {}).get('failed', 0) + 1)

                batch = get_enrollment_batch(batch_id)
                total = (batch or {}).get('total_contacts', 0)
                update_enrollment_batch(batch_id,
                    generated=generated_count,
                    current_phase=f'Generating emails ({generated_count}/{total})...')

        # ===== PHASE 2: Apollo Enrollment =====
        update_enrollment_batch(batch_id, status='enrolling')

        custom_field_map = {}
        try:
            _apollo_limiter.wait()
            fields_resp = req.get(
                'https://api.apollo.io/v1/typed_custom_fields',
                headers=apollo_headers, timeout=15
            )
            if fields_resp.status_code == 200:
                for f in fields_resp.json().get('typed_custom_fields', []):
                    norm = f.get('name', '').lower().replace(' ', '_')
                    custom_field_map[norm] = f.get('id', '')
        except Exception as e:
            app.logger.warning(f'Custom field discovery failed: {e}')

        field_env = {
            'personalized_subject_1': os.environ.get('APOLLO_FIELD_PERSONALIZED_SUBJECT_1', ''),
            'personalized_subject_2': os.environ.get('APOLLO_FIELD_PERSONALIZED_SUBJECT_2', ''),
            'personalized_email_1': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_1', ''),
            'personalized_email_2': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_2', ''),
            'personalized_email_3': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_3', ''),
            'personalized_email_4': os.environ.get('APOLLO_FIELD_PERSONALIZED_EMAIL_4', ''),
        }
        for key, env_val in field_env.items():
            if env_val and key not in custom_field_map:
                custom_field_map[key] = env_val

        email_account_id = None
        try:
            _apollo_limiter.wait()
            ea_resp = req.get(
                'https://api.apollo.io/api/v1/email_accounts',
                headers=apollo_headers, timeout=15
            )
            if ea_resp.status_code == 200:
                sender_email = os.environ.get('APOLLO_SENDER_EMAIL', '')
                for ea in ea_resp.json().get('email_accounts', []):
                    if ea.get('active'):
                        if not email_account_id:
                            email_account_id = ea['id']
                        if sender_email and ea.get('email') == sender_email:
                            email_account_id = ea['id']
                            break
        except Exception as e:
            app.logger.warning(f'Email account resolution failed: {e}')

        if not email_account_id:
            update_enrollment_batch(batch_id, status='failed',
                                     error_message='No active Apollo email account found')
            return

        enrolled_count = 0
        failed_count = (get_enrollment_batch(batch_id) or {}).get('failed', 0)

        while True:
            contacts = get_next_contacts_for_phase(batch_id, 'email_generated', limit=10)
            if not contacts:
                break
            b = get_enrollment_batch(batch_id)
            if b and b.get('status') == 'cancelled':
                return

            for contact in contacts:
                try:
                    email_data = json.loads(contact.get('generated_emails_json') or '{}')

                    typed_fields = {}

                    def _html(text):
                        if not text:
                            return text
                        return text.replace('\n\n', '<br><br>').replace('\n', '<br>')

                    for field_name, field_key in [
                        ('subject_1', 'personalized_subject_1'),
                        ('subject_2', 'personalized_subject_2'),
                        ('email_1', 'personalized_email_1'),
                        ('email_2', 'personalized_email_2'),
                        ('email_3', 'personalized_email_3'),
                        ('email_4', 'personalized_email_4'),
                    ]:
                        val = email_data.get(field_name, '')
                        fid = custom_field_map.get(field_key, '')
                        if val and fid:
                            typed_fields[fid] = _html(val) if 'email' in field_name else val

                    _apollo_limiter.wait()
                    search_resp = req.post(
                        'https://api.apollo.io/api/v1/contacts/search',
                        json={'q_keywords': contact['email'], 'per_page': 1},
                        headers=apollo_headers, timeout=15
                    )
                    existing_contact = None
                    if search_resp.status_code == 200:
                        contacts_found = search_resp.json().get('contacts', [])
                        if contacts_found:
                            existing_contact = contacts_found[0]

                    _apollo_limiter.wait()
                    if existing_contact:
                        apollo_contact_id = existing_contact['id']
                        req.put(
                            f'https://api.apollo.io/v1/contacts/{apollo_contact_id}',
                            json={'typed_custom_fields': typed_fields},
                            headers=apollo_headers, timeout=15
                        )
                    else:
                        create_payload = {
                            'first_name': contact.get('first_name', ''),
                            'last_name': contact.get('last_name', ''),
                            'email': contact['email'],
                            'organization_name': contact['company_name'],
                            'typed_custom_fields': typed_fields
                        }
                        create_resp = req.post(
                            'https://api.apollo.io/v1/contacts',
                            json=create_payload,
                            headers=apollo_headers, timeout=15
                        )
                        if create_resp.status_code in (200, 201):
                            apollo_contact_id = create_resp.json().get('contact', {}).get('id', '')
                        else:
                            raise Exception(f'Contact creation failed: {create_resp.status_code}')

                    _apollo_limiter.wait()
                    seq_id = contact.get('sequence_id', '')
                    enroll_resp = req.post(
                        f'https://api.apollo.io/api/v1/emailer_campaigns/{seq_id}/add_contact_ids',
                        json={
                            'emailer_campaign_id': seq_id,
                            'contact_ids': [apollo_contact_id],
                            'send_email_from_email_account_id': email_account_id
                        },
                        headers=apollo_headers, timeout=15
                    )
                    if enroll_resp.status_code not in (200, 201):
                        raise Exception(f'Enrollment failed: {enroll_resp.status_code} - {enroll_resp.text[:200]}')

                    update_enrollment_contact(contact['id'],
                        status='enrolled',
                        apollo_contact_id=apollo_contact_id,
                        enrolled_at=datetime.now().isoformat())
                    enrolled_count += 1

                except Exception as e:
                    app.logger.warning(f'Enrollment error for contact {contact["id"]}: {e}')
                    update_enrollment_contact(contact['id'],
                        status='failed',
                        error_message=str(e)[:500])
                    failed_count += 1

                batch = get_enrollment_batch(batch_id)
                total = (batch or {}).get('total_contacts', 0)
                update_enrollment_batch(batch_id,
                    enrolled=enrolled_count,
                    failed=failed_count,
                    current_phase=f'Enrolling ({enrolled_count}/{total})...')

        update_enrollment_batch(batch_id,
            status='completed',
            enrolled=enrolled_count,
            failed=failed_count,
            current_phase=f'Complete — {enrolled_count} enrolled, {failed_count} failed',
            completed_at=datetime.now().isoformat())

    except Exception as e:
        app.logger.error(f'Enrollment pipeline error for batch {batch_id}: {e}')
        update_enrollment_batch(batch_id, status='failed', error_message=str(e)[:500])


# CSV Upload for Campaign Accounts

@app.route('/api/campaigns/<int:campaign_id>/upload-accounts', methods=['POST'])
def api_campaign_upload_accounts(campaign_id):
    """Upload a CSV of target accounts for a campaign."""
    campaign = get_campaign(campaign_id)
    if not campaign:
        return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404

    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file uploaded. Use form field name "file".'}), 400

    file = request.files['file']
    is_valid, result = validate_csv_upload(file)
    if not is_valid:
        return jsonify({'status': 'error', 'message': result}), 400

    valid_rows = result['valid_rows']
    rejected_rows = result['rejected_rows']

    if not valid_rows:
        return jsonify({
            'status': 'error',
            'message': f'No valid accounts found. {len(rejected_rows)} rejected (missing website/domain).',
            'rejected': rejected_rows,
        }), 400

    COLUMN_MAP = {
        'annual_revenue': 'annual_revenue', 'revenue': 'annual_revenue',
        'industry': 'industry',
        'employee_count': 'employee_count', 'employees': 'employee_count',
        'hq_location': 'hq_location', 'headquarters': 'hq_location', 'location': 'hq_location',
        'funding_stage': 'funding_stage', 'funding': 'funding_stage',
        'github_org': 'github_org', 'github': 'github_org',
        'notes': 'notes',
    }

    saved = 0
    skipped_archived = 0
    row_failures = []

    with db_connection() as conn:
        cursor = conn.cursor()
        for row in valid_rows:
            company_name = row['company_name']
            website = row['website']

            try:
                existing = get_account_by_company(company_name)
                if existing and existing.get('archived_at'):
                    skipped_archived += 1
                    rejected_rows.append({
                        'company_name': company_name,
                        'reason': 'Account is archived — skipped re-import',
                    })
                    continue

                annual_revenue = row.get('annual_revenue') or row.get('revenue') or ''
                github_org = row.get('github_org') or row.get('github') or ''

                add_account_to_tier_0(
                    company_name=company_name,
                    github_org=github_org,
                    annual_revenue=annual_revenue or None,
                    website=website,
                )

                extra_updates = {}
                for csv_key, db_col in COLUMN_MAP.items():
                    if csv_key in row and db_col not in ('annual_revenue', 'github_org'):
                        extra_updates[db_col] = row[csv_key]

                if extra_updates:
                    set_parts = [f'{col} = ?' for col in extra_updates.keys()]
                    values = list(extra_updates.values()) + [company_name.lower().strip()]
                    cursor.execute(
                        f'UPDATE monitored_accounts SET {", ".join(set_parts)} WHERE LOWER(company_name) = ?',
                        values
                    )
                    conn.commit()

                saved += 1
            except Exception as e:
                logging.warning(f'[CSV-UPLOAD] Failed to import row {company_name}: {e}')
                row_failures.append({'company_name': company_name, 'reason': 'Import failed for this row'})

    return jsonify({
        'status': 'success',
        'saved': saved,
        'rejected': len(rejected_rows),
        'skipped_archived': skipped_archived,
        'row_failures': row_failures[:50],
        'rejected_accounts': rejected_rows[:50],
        'total_in_csv': len(valid_rows) + len(rejected_rows) - skipped_archived,
    })


# Enrollment API Routes

@app.route('/api/campaigns/<int:campaign_id>/discover-contacts', methods=['POST'])
def api_discover_contacts(campaign_id):
    """Start contact discovery for a campaign."""
    campaign = get_campaign(campaign_id)
    if not campaign:
        return jsonify({'status': 'error', 'message': 'Campaign not found'}), 404
    personas = campaign.get('personas', [])
    if not personas:
        return jsonify({'status': 'error', 'message': 'No personas configured for this campaign'}), 400

    data = request.get_json() or {}
    account_ids = data.get('account_ids', [])
    if not account_ids:
        return jsonify({'status': 'error', 'message': 'No accounts selected'}), 400
    if not isinstance(account_ids, list):
        return jsonify({'status': 'error', 'message': 'account_ids must be a list'}), 400
    for aid in account_ids:
        valid, _ = validate_positive_int(aid, name='account_id')
        if not valid:
            return jsonify({'status': 'error', 'message': f'Invalid account_id: {aid}'}), 400

    batch_id = create_enrollment_batch(campaign_id, account_ids)
    if not hasattr(app, '_enrollment_executor'):
        app._enrollment_executor = ThreadPoolExecutor(max_workers=3)
    app._enrollment_executor.submit(_discovery_worker, batch_id)

    return jsonify({'status': 'success', 'batch_id': batch_id})


@app.route('/api/campaigns/<int:campaign_id>/enroll', methods=['POST'])
def api_campaign_enroll(campaign_id):
    """Start email generation + Apollo enrollment for a discovered batch."""
    data = request.get_json() or {}
    batch_id = data.get('batch_id')
    if not batch_id:
        return jsonify({'status': 'error', 'message': 'batch_id is required'}), 400

    batch = get_enrollment_batch(batch_id)
    if not batch:
        return jsonify({'status': 'error', 'message': 'Batch not found'}), 404
    if batch['status'] not in ('discovered', 'failed'):
        return jsonify({'status': 'error', 'message': f'Batch is in {batch["status"]} state, cannot enroll'}), 400

    if not hasattr(app, '_enrollment_executor'):
        app._enrollment_executor = ThreadPoolExecutor(max_workers=3)
    app._enrollment_executor.submit(_enrollment_pipeline_worker, batch_id)

    return jsonify({'status': 'success', 'batch_id': batch_id})


@app.route('/api/enrollment-batches/<int:batch_id>/status')
def api_enrollment_batch_status(batch_id):
    batch = get_enrollment_batch(batch_id)
    if not batch:
        return jsonify({'status': 'error', 'message': 'Batch not found'}), 404
    summary = get_enrollment_batch_summary(batch_id)
    batch['contact_summary'] = summary
    batch.pop('account_ids_json', None)
    return jsonify({'status': 'success', 'batch': batch})


@app.route('/api/enrollment-batches/<int:batch_id>/contacts')
def api_enrollment_batch_contacts(batch_id):
    status_filter = request.args.get('status')
    try:
        limit = min(int(request.args.get('limit', 500)), 1000)
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'limit must be an integer'}), 400
    try:
        offset = int(request.args.get('offset', 0))
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'offset must be an integer'}), 400
    valid, limit = validate_positive_int(limit, name='limit', max_val=1000)
    if not valid:
        return jsonify({'status': 'error', 'message': limit}), 400
    valid, offset = validate_positive_int(offset, name='offset', max_val=100000)
    if not valid:
        return jsonify({'status': 'error', 'message': offset}), 400
    contacts = get_enrollment_contacts(batch_id, status=status_filter, limit=limit, offset=offset)
    return jsonify({'status': 'success', 'contacts': contacts})


@app.route('/api/enrollment-batches/<int:batch_id>/cancel', methods=['POST'])
def api_enrollment_batch_cancel(batch_id):
    batch = get_enrollment_batch(batch_id)
    if not batch:
        return jsonify({'status': 'error', 'message': 'Batch not found'}), 404
    update_enrollment_batch(batch_id, status='cancelled', current_phase='Cancelled by user')
    return jsonify({'status': 'success', 'cancelled': True})


@app.route('/api/enrollment-batches/<int:batch_id>/retry', methods=['POST'])
def api_enrollment_batch_retry(batch_id):
    batch = get_enrollment_batch(batch_id)
    if not batch:
        return jsonify({'status': 'error', 'message': 'Batch not found'}), 404

    failed_contacts = get_enrollment_contacts(batch_id, status='failed')
    if not failed_contacts:
        return jsonify({'status': 'error', 'message': 'No failed contacts to retry'}), 400

    for c in failed_contacts:
        new_status = 'email_generated' if c.get('generated_emails_json') else 'discovered'
        update_enrollment_contact(c['id'], status=new_status, error_message=None)

    update_enrollment_batch(batch_id,
                            status='enrolling',
                            current_phase='Retrying failed contacts...',
                            failed=0)

    if not hasattr(app, '_enrollment_executor'):
        app._enrollment_executor = ThreadPoolExecutor(max_workers=3)
    app._enrollment_executor.submit(_enrollment_pipeline_worker, batch_id)
    return jsonify({'status': 'success', 'retrying': len(failed_contacts)})


@app.route('/api/enrollment/accounts')
def api_enrollment_accounts():
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT a.id, a.company_name, a.website, a.github_org, a.annual_revenue,
                   a.current_tier, s.cohort, s.locale_count, s.total_score
            FROM monitored_accounts a
            LEFT JOIN scorecard_scores s ON s.account_id = a.id
            WHERE a.archived_at IS NULL
            ORDER BY a.company_name ASC
        ''')
        accounts = [dict(r) for r in cursor.fetchall()]
    return jsonify({'status': 'success', 'accounts': accounts})


@app.route('/enrollment')
def enrollment_page():
    campaigns_list = get_all_campaigns()
    active_campaigns = [c for c in campaigns_list if c.get('status') == 'active']
    return render_template('enrollment.html', campaigns=active_campaigns)


# =============================================================================
# APOLLO SEQUENCE ROUTES
# =============================================================================

@app.route('/api/apollo/sequence-steps/<sequence_id>')
def api_apollo_sequence_steps(sequence_id):
    """Fetch the individual steps of an Apollo sequence for preview."""
    import requests as req

    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        return jsonify({'status': 'no_key', 'steps': []}), 200

    try:
        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}

        campaign = None
        page = 1
        while True:
            resp = req.post(
                'https://api.apollo.io/api/v1/emailer_campaigns/search',
                json={'page': page},
                headers=apollo_headers,
                timeout=15
            )
            if resp.status_code != 200:
                return jsonify({'status': 'api_error', 'steps': []}), 200

            data = resp.json()
            campaigns_data = data.get('emailer_campaigns', [])
            campaign = next((c for c in campaigns_data if c.get('id') == sequence_id), None)
            if campaign:
                break

            pagination = data.get('pagination', {})
            if page >= pagination.get('total_pages', 1):
                break
            page += 1

        if not campaign:
            return jsonify({'status': 'not_found', 'steps': []}), 200

        steps = []
        for i, s in enumerate(campaign.get('emailer_steps', [])):
            step_type = s.get('type', 'email')
            type_label = step_type.replace('_', ' ').title()
            if step_type in ('auto_email', 'manual_email', 'email'):
                type_label = 'Email'
            elif step_type == 'linkedin_step_message':
                type_label = 'LinkedIn Message'
            elif step_type == 'linkedin_step_connect':
                type_label = 'LinkedIn Connect'
            elif step_type == 'call_task':
                type_label = 'Call Task'
            elif step_type == 'action_item':
                type_label = 'Task'

            steps.append({
                'position': i + 1,
                'type': step_type,
                'type_label': type_label,
                'subject': s.get('subject', '') or '(threaded reply)',
                'wait_time': s.get('wait_time', 0),
                'wait_mode': s.get('wait_mode', 'delay'),
            })

        return jsonify({
            'status': 'success',
            'sequence_name': campaign.get('name', 'Unknown'),
            'steps': steps,
        })

    except Exception as e:
        logging.error(f"[APOLLO STEPS ERROR] {e}")
        return jsonify({'status': 'error', 'steps': []}), 200


@app.route('/api/apollo/sequences')
def api_apollo_sequences():
    """Return enabled sequences from sequence_mappings table."""
    try:
        mappings = get_all_sequence_mappings(enabled_only=True)
        sequences = []
        for m in mappings:
            sequences.append({
                'id': m.get('sequence_id', ''),
                'name': m.get('sequence_name', 'Unnamed Sequence'),
                'active': bool(m.get('active', False)),
                'num_steps': m.get('num_steps', 0),
                'created_at': m.get('created_at', ''),
            })
        return jsonify({'status': 'success', 'sequences': sequences})
    except Exception as e:
        logging.error(f"[APOLLO SEQUENCES ERROR] {e}")
        return jsonify({'status': 'error', 'message': 'Failed to load sequences from mapping table'}), 500


@app.route('/api/apollo/sequence-detect', methods=['POST'])
def api_apollo_sequence_detect():
    """Auto-detect sequence configuration type from Apollo sequence ID."""
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

        campaign = None
        page = 1
        while True:
            resp = req.post(
                'https://api.apollo.io/api/v1/emailer_campaigns/search',
                json={'page': page},
                headers=apollo_headers,
                timeout=15
            )
            if resp.status_code == 403:
                return jsonify({'status': 'auth_error'}), 200
            if resp.status_code != 200:
                return jsonify({'status': 'api_error'}), 200

            data = resp.json()
            campaigns_list = data.get('emailer_campaigns', [])
            campaign = next((c for c in campaigns_list if c.get('id') == sequence_id), None)
            if campaign:
                break

            pagination = data.get('pagination', {})
            if page >= pagination.get('total_pages', 1):
                break
            page += 1

        if not campaign:
            return jsonify({'status': 'not_found'}), 200

        steps = campaign.get('emailer_steps', [])
        email_types = {'auto_email', 'manual_email', 'email'}
        email_steps = [s for s in steps if s.get('type') in email_types]
        if not email_steps:
            email_steps = steps

        num_emails = len(email_steps)
        subjects = [(s.get('subject') or '').strip() for s in email_steps]
        non_empty = [s for s in subjects if s]
        unique_subjects = len(set(s.lower() for s in non_empty))

        if num_emails == 1:
            detected = 'one_off'
            note = '1 email step detected'
        elif unique_subjects <= 1:
            detected = 'threaded_4'
            note = f'{num_emails} emails under one subject thread'
        elif unique_subjects == 2:
            detected = 'split_2x2'
            note = f'{num_emails} emails across 2 subject threads'
        else:
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
        logging.error(f"[APOLLO DETECT ERROR] {e}")
        return jsonify({'status': 'error'}), 200


@app.route('/api/apollo/enroll-sequence', methods=['POST'])
def api_apollo_enroll_sequence():
    """Enroll a contact into an Apollo email sequence using Custom Field Injection."""
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

    if not email or not sequence_id:
        return jsonify({'status': 'error', 'message': 'Missing required fields: email and sequence_id'}), 400

    valid, email = validate_email(email)
    if not valid:
        return jsonify({'status': 'error', 'message': email}), 400
    if company_name:
        valid, company_name = validate_company_name(company_name)
        if not valid:
            return jsonify({'status': 'error', 'message': company_name}), 400

    def to_html(text):
        if not text:
            return ''
        return text.strip().replace('\n\n', '<br><br>').replace('\n', '<br>')

    personalized_subject_1 = data.get('personalized_subject', '').strip() or data.get('personalized_subject_1', '').strip()
    personalized_subject_2 = data.get('personalized_subject_2', '').strip()
    personalized_email_1 = to_html(data.get('personalized_email_body', '') or data.get('personalized_email_1', ''))
    personalized_email_2 = to_html(data.get('personalized_email_2', ''))
    personalized_email_3 = to_html(data.get('personalized_email_3', ''))
    personalized_email_4 = to_html(data.get('personalized_email_4', ''))

    try:
        apollo_headers = {'X-Api-Key': apollo_key, 'Content-Type': 'application/json'}

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
                    for k, v in FIELD_ENV_OVERRIDES.items():
                        if v and k not in field_id_map:
                            field_id_map[k] = v
                else:
                    field_id_map = {k: v for k, v in FIELD_ENV_OVERRIDES.items() if v}

                for field_key, field_val in field_values.items():
                    if field_key in field_id_map:
                        typed_custom_fields[field_id_map[field_key]] = field_val

                logging.info(f"[APOLLO ENROLL] Will inject {len(typed_custom_fields)} custom field(s): {list(field_values.keys())}")
            except Exception as cf_err:
                logging.warning(f"[APOLLO ENROLL] Warning: could not fetch custom field definitions: {cf_err}")
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
            contacts_list = search_resp.json().get('contacts', [])
            if contacts_list:
                contact_id = contacts_list[0].get('id')

        # Step 2a: Create new contact
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
            else:
                error_msg = create_resp.json().get('message', create_resp.text[:200])
                return jsonify({'status': 'error', 'message': f'Failed to create Apollo contact: {error_msg}'}), 502

        # Step 2b: Inject custom fields into existing contact
        elif typed_custom_fields and contact_id:
            update_resp = req.put(
                f'https://api.apollo.io/v1/contacts/{contact_id}',
                json={'typed_custom_fields': typed_custom_fields},
                headers=apollo_headers,
                timeout=15
            )
            if update_resp.status_code not in (200, 201):
                logging.warning(f"[APOLLO ENROLL] Warning: custom field injection failed: {update_resp.text[:200]}")

        if not contact_id:
            return jsonify({'status': 'error', 'message': 'Could not find or create contact in Apollo'}), 500

        # Resolve sending email account
        email_account_id = None
        preferred_sender = os.environ.get('APOLLO_SENDER_EMAIL', '').strip().lower()
        try:
            ea_resp = req.get('https://api.apollo.io/api/v1/email_accounts',
                              headers=apollo_headers, timeout=15)
            if ea_resp.status_code == 200:
                accounts_list = ea_resp.json().get('email_accounts', [])
                active = [a for a in accounts_list if a.get('active')]
                if preferred_sender:
                    match = next((a for a in active if a.get('email', '').lower() == preferred_sender), None)
                    email_account_id = match['id'] if match else (active[0]['id'] if active else None)
                elif active:
                    email_account_id = active[0]['id']
        except Exception as ea_err:
            logging.warning(f"[APOLLO ENROLL] Warning: could not fetch email accounts: {ea_err}")

        if not email_account_id:
            return jsonify({'status': 'error', 'message': 'No active Apollo email account found to send from. Set APOLLO_SENDER_EMAIL in .env.'}), 500

        # Step 3: Add contact to sequence
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
            log_audit_event('apollo_enrollment', f'email={email} company={company_name} sequence={sequence_id}', ip_address=request.remote_addr)
            return jsonify({
                'status': 'success',
                'message': f'Successfully enrolled {email} in sequence',
                'contact_id': contact_id,
            })
        else:
            error_msg = enroll_resp.json().get('message', enroll_resp.text[:200]) if enroll_resp.text else 'Unknown error'
            return jsonify({'status': 'error', 'message': f'Failed to enroll in sequence: {error_msg}'}), 502

    except Exception as e:
        logging.error(f"[APOLLO ENROLL ERROR] {e}")
        return jsonify({'status': 'error', 'message': 'Failed to enroll in Apollo sequence'}), 500


@app.route('/bdr-review')
def bdr_review():
    """Redirect legacy BDR review to V2 signal queue."""
    return redirect('/app')


# =============================================================================
# ACCOUNTS
# =============================================================================

@app.route('/accounts')
def accounts():
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


@app.route('/api/accounts')
def api_accounts():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)
    tiers = request.args.getlist('tier', type=int)
    if not tiers:
        tiers = None
    search_query = request.args.get('q', '').strip()
    if not search_query:
        search_query = None

    result = get_all_accounts(page=page, limit=limit, tier_filter=tiers, search_query=search_query)
    for account in result['accounts']:
        if not account.get('scan_status'):
            account['scan_status'] = SCAN_STATUS_IDLE

    return jsonify({
        'accounts': result['accounts'],
        'total_items': result['total_items'],
        'total_pages': result['total_pages'],
        'current_page': result['current_page'],
        'limit': result['limit']
    })


@app.route('/api/accounts/<int:account_id>/notes', methods=['PUT'])
def api_update_account_notes(account_id: int):
    data = request.get_json() or {}
    notes = data.get('notes', '')
    if notes:
        valid, notes = validate_notes(notes)
        if not valid:
            return jsonify({'status': 'error', 'message': notes}), 400
    updated = update_account_notes(account_id, notes)
    if not updated:
        return jsonify({'status': 'error', 'message': 'Account not found'}), 404
    return jsonify({'status': 'success', 'notes': notes})


@app.route('/api/accounts/<int:account_id>/archive', methods=['POST'])
def api_archive_account(account_id: int):
    archived = archive_account(account_id)
    if not archived:
        return jsonify({'status': 'error', 'message': 'Account not found or already archived'}), 404
    return jsonify({'status': 'success', 'message': 'Account archived'})


@app.route('/api/accounts/<int:account_id>/unarchive', methods=['POST'])
def api_unarchive_account(account_id: int):
    unarchived = unarchive_account(account_id)
    if not unarchived:
        return jsonify({'status': 'error', 'message': 'Account not found or not archived'}), 404
    return jsonify({'status': 'success', 'message': 'Account unarchived'})


@app.route('/api/accounts/archived')
def api_get_archived_accounts():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 50, type=int)
    search_query = request.args.get('search', None)
    result = get_archived_accounts(page=page, limit=limit, search_query=search_query)
    return jsonify(result)


@app.route('/api/accounts/archived/count')
def api_get_archived_count():
    count = get_archived_count()
    return jsonify({'count': count})


# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(401)
def unauthorized(e):
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
    return render_template('error.html', message='Unauthorized. Please log in.'), 401


@app.errorhandler(403)
def forbidden(e):
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': 'Forbidden'}), 403
    return render_template('error.html', message='Access denied.'), 403


@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    return render_template('error.html', message='Page not found'), 404


@app.errorhandler(429)
def too_many_requests(e):
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': 'Too many requests. Please slow down.'}), 429
    return render_template('error.html', message='Too many requests. Please wait a moment and try again.'), 429


@app.errorhandler(500)
def internal_error(e):
    request_id = getattr(g, 'request_id', 'unknown')
    original = e.original_exception if hasattr(e, 'original_exception') else e
    logging.exception(f"[ERROR] 500 Internal Server Error (request_id={request_id}): {original}")
    if request.path.startswith('/api/'):
        return jsonify({
            'status': 'error',
            'message': 'Internal server error',
            'request_id': request_id,
        }), 500
    return render_template('error.html', message='Internal server error. Please try again later.'), 500


@app.errorhandler(Exception)
def handle_unhandled_exception(e):
    if isinstance(e, HTTPException):
        return e
    request_id = getattr(g, 'request_id', 'unknown')
    logging.exception(
        f"[UNHANDLED EXCEPTION] {request.method} {request.path} "
        f"(request_id={request_id}): {type(e).__name__}"
    )
    if request.path.startswith('/api/'):
        return jsonify({
            'status': 'error',
            'message': 'Internal server error',
            'request_id': request_id,
        }), 500
    return render_template('error.html', message='Internal server error. Please try again later.'), 500


# =============================================================================
# STARTUP
# =============================================================================

if __name__ == '__main__':
    from database import init_db
    init_db()

    logging.info("[APP] Starting application...")

    from seed_reporadar_campaign import seed_reporadar_campaign
    seed_reporadar_campaign()

    port = int(os.environ.get('PORT', 5000))

    debug_mode = Config.DEBUG
    if debug_mode and not Config.API_KEY:
        print("[WARNING] Debug mode is ON without DOSSIER_API_KEY set!")
    if os.environ.get('PRODUCTION', '').lower() in ('true', '1', 'yes'):
        if debug_mode:
            print("[APP] PRODUCTION=true detected, forcing debug mode OFF")
        debug_mode = False

    app.run(debug=debug_mode, host='0.0.0.0', port=port, threaded=True)
