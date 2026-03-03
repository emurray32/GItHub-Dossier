"""
Authentication module for GitHub Dossier.

Provides:
- API key authentication for /api/* routes (X-API-Key header)
- Session-based authentication for web UI routes
- Decorators for route-level auth control
- Login/logout views
"""
import functools
import hmac
import logging
import os
from datetime import datetime

from flask import (
    Blueprint, request, jsonify, redirect, url_for,
    session, render_template_string, current_app,
)

auth_bp = Blueprint('auth', __name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# When DOSSIER_API_KEY is set, API routes require it.
# When DOSSIER_UI_PASSWORD is set, web UI routes require login.
# If neither is set, auth is disabled (backward compatible).

_LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - GitHub Dossier</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }
        .login-card {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 2rem;
            width: 100%;
            max-width: 400px;
        }
        .login-card h1 { font-size: 1.5rem; margin-bottom: 1.5rem; text-align: center; }
        .login-card label { display: block; margin-bottom: 0.5rem; font-size: 0.875rem; color: #94a3b8; }
        .login-card input[type=password] {
            width: 100%;
            padding: 0.75rem;
            border: 1px solid #475569;
            border-radius: 6px;
            background: #0f172a;
            color: #e2e8f0;
            font-size: 1rem;
            margin-bottom: 1rem;
        }
        .login-card button {
            width: 100%;
            padding: 0.75rem;
            background: #3b82f6;
            color: #fff;
            border: none;
            border-radius: 6px;
            font-size: 1rem;
            cursor: pointer;
        }
        .login-card button:hover { background: #2563eb; }
        .error { color: #f87171; font-size: 0.875rem; margin-bottom: 1rem; text-align: center; }
    </style>
</head>
<body>
    <div class="login-card">
        <h1>GitHub Dossier</h1>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="POST" action="{{ url_for('auth.login') }}">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" autocomplete="current-password" required autofocus>
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>
'''


def _get_ui_password():
    """Return the UI password if configured, else empty string (auth disabled)."""
    return os.getenv('DOSSIER_UI_PASSWORD', '')


def _get_api_key():
    """Return the API key if configured, else empty string (auth disabled)."""
    return os.getenv('DOSSIER_API_KEY', '')


def is_auth_enabled():
    """Return True if either API key or UI password is configured."""
    return bool(_get_api_key() or _get_ui_password())


# ---------------------------------------------------------------------------
# Audit log helper (lazy import to avoid circular dependency)
# ---------------------------------------------------------------------------

def _audit_log(action, details, ip_address=None):
    """Log to audit_log if database module is available."""
    try:
        from database import log_audit_event
        log_audit_event(action, details, ip_address=ip_address)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Login / Logout views
# ---------------------------------------------------------------------------

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page for web UI authentication."""
    if not _get_ui_password():
        return redirect(url_for('index'))

    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        expected = _get_ui_password()

        # Constant-time comparison to prevent timing attacks
        if hmac.compare_digest(password.encode(), expected.encode()):
            session['authenticated'] = True
            session['login_time'] = datetime.utcnow().isoformat()
            session.permanent = True
            logging.info(f"[AUTH] Successful login from {request.remote_addr}")
            next_url = request.args.get('next', '/')
            # Prevent open redirect: must start with / and not //
            if not next_url.startswith('/') or next_url.startswith('//'):
                next_url = '/'
            return redirect(next_url)
        else:
            logging.warning(f"[AUTH] Failed login attempt from {request.remote_addr}")
            _audit_log('auth_failure', f'failed_login from {request.remote_addr}', ip_address=request.remote_addr)
            error = 'Invalid password'

    return render_template_string(_LOGIN_TEMPLATE, error=error)


@auth_bp.route('/logout')
def logout():
    """Clear session and redirect to login."""
    session.clear()
    return redirect(url_for('auth.login'))


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def require_api_key(f):
    """Decorator: require valid API key in X-API-Key header or ?api_key= param."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        api_key = _get_api_key()
        if not api_key:
            return f(*args, **kwargs)  # Auth disabled

        provided = request.headers.get('X-API-Key') or request.args.get('api_key')
        if not provided or not hmac.compare_digest(provided.encode(), api_key.encode()):
            _audit_log('auth_failure', f'invalid_api_key path={request.path}', ip_address=request.remote_addr)
            return jsonify({'status': 'error', 'message': 'Unauthorized: invalid or missing API key'}), 401

        return f(*args, **kwargs)
    return decorated


def require_session(f):
    """Decorator: require authenticated session for web UI routes."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _get_ui_password():
            return f(*args, **kwargs)  # Auth disabled

        if not session.get('authenticated'):
            return redirect(url_for('auth.login', next=request.path))

        return f(*args, **kwargs)
    return decorated
