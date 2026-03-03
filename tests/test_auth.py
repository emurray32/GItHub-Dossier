"""
Tests for auth.py — authentication module and enforce_authentication middleware.

Tests cover:
- API key authentication (DOSSIER_API_KEY)
- Session-based UI authentication (DOSSIER_UI_PASSWORD)
- Path exemptions (/static/, /login, /slack/)
- Login/logout flows
- Open redirect prevention
- Auth-disabled fallback behavior
"""
import os
import pytest
from unittest.mock import patch, MagicMock

# Set env vars before importing app to avoid side effects
os.environ.setdefault('APOLLO_API_KEY', 'test-apollo-key')
os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret-key-for-auth')


from app import app
from rate_limiter import limiter


@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """Clear rate limiter state before each test."""
    limiter._hits.clear()


@pytest.fixture
def client():
    """Create a Flask test client with TESTING enabled."""
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret-key-for-auth'
    with app.test_client() as client:
        yield client


# ──────────────────────────────────────────────────────────────────────
# API Key Authentication (/api/* routes)
# ──────────────────────────────────────────────────────────────────────

class TestApiKeyAuth:
    """Tests for API key authentication on /api/* routes."""

    def test_api_request_without_key_returns_401(self, client, monkeypatch):
        """API request without key returns 401 when DOSSIER_API_KEY is set."""
        monkeypatch.setattr('config.Config.API_KEY', 'secret-api-key')
        resp = client.post('/api/apollo-lookup', json={'name': 'Test'})
        assert resp.status_code == 401
        data = resp.get_json()
        assert data['status'] == 'error'
        assert 'unauthorized' in data['message'].lower()

    def test_api_request_with_correct_key_via_header(self, client, monkeypatch):
        """API request with correct X-API-Key header passes auth."""
        monkeypatch.setattr('config.Config.API_KEY', 'secret-api-key')
        resp = client.post(
            '/api/apollo-lookup',
            json={'name': 'Test', 'company': 'Acme'},
            headers={'X-API-Key': 'secret-api-key'},
        )
        # Should pass auth and hit the actual route logic (may return 400/500
        # depending on route requirements, but NOT 401)
        assert resp.status_code != 401

    def test_api_request_with_correct_key_via_query_param(self, client, monkeypatch):
        """API request with correct ?api_key= query param passes auth."""
        monkeypatch.setattr('config.Config.API_KEY', 'secret-api-key')
        resp = client.post(
            '/api/apollo-lookup?api_key=secret-api-key',
            json={'name': 'Test', 'company': 'Acme'},
        )
        assert resp.status_code != 401

    def test_api_request_with_wrong_key_returns_401(self, client, monkeypatch):
        """API request with wrong key returns 401."""
        monkeypatch.setattr('config.Config.API_KEY', 'secret-api-key')
        resp = client.post(
            '/api/apollo-lookup',
            json={'name': 'Test'},
            headers={'X-API-Key': 'wrong-key'},
        )
        assert resp.status_code == 401

    def test_no_api_key_configured_allows_all(self, client, monkeypatch):
        """When DOSSIER_API_KEY is not set, API requests pass through."""
        monkeypatch.setattr('config.Config.API_KEY', '')
        resp = client.post('/api/apollo-lookup', json={})
        # Should not be 401; route returns 400 for missing body fields
        assert resp.status_code != 401


# ──────────────────────────────────────────────────────────────────────
# Path Exemptions
# ──────────────────────────────────────────────────────────────────────

class TestPathExemptions:
    """Tests for paths that skip authentication."""

    def test_static_paths_skip_auth(self, client, monkeypatch):
        """Requests to /static/ skip auth even with API key set."""
        monkeypatch.setattr('config.Config.API_KEY', 'secret-api-key')
        resp = client.get('/static/js/stream.js')
        # Static files return 200 or 404, not 401
        assert resp.status_code != 401

    def test_login_path_skips_auth(self, client, monkeypatch):
        """Requests to /login skip auth."""
        monkeypatch.setattr('config.Config.API_KEY', 'secret-api-key')
        resp = client.get('/login')
        assert resp.status_code != 401

    def test_slack_paths_skip_auth(self, client, monkeypatch):
        """Requests to /slack/* skip auth."""
        monkeypatch.setattr('config.Config.API_KEY', 'secret-api-key')
        resp = client.post('/slack/events', json={'type': 'url_verification', 'challenge': 'test'})
        assert resp.status_code != 401


# ──────────────────────────────────────────────────────────────────────
# Login / Logout
# ──────────────────────────────────────────────────────────────────────

class TestLoginLogout:
    """Tests for login and logout flows."""

    def test_login_get_shows_form(self, client, monkeypatch):
        """GET /login returns the login form HTML."""
        monkeypatch.setenv('DOSSIER_UI_PASSWORD', 'test-pass')
        resp = client.get('/login')
        assert resp.status_code == 200
        assert b'password' in resp.data.lower()

    def test_login_post_correct_password(self, client, monkeypatch):
        """POST /login with correct password sets session and redirects."""
        monkeypatch.setenv('DOSSIER_UI_PASSWORD', 'test-pass')
        resp = client.post('/login', data={'password': 'test-pass'})
        assert resp.status_code in (302, 303)
        # Should redirect to /
        assert '/' in resp.headers.get('Location', '')

    def test_login_post_wrong_password(self, client, monkeypatch):
        """POST /login with wrong password returns error page."""
        monkeypatch.setenv('DOSSIER_UI_PASSWORD', 'test-pass')
        resp = client.post('/login', data={'password': 'wrong'})
        assert resp.status_code == 200
        assert b'invalid' in resp.data.lower() or b'Invalid' in resp.data

    def test_login_redirects_when_no_password_configured(self, client, monkeypatch):
        """GET /login redirects to / when no UI password is configured."""
        monkeypatch.setenv('DOSSIER_UI_PASSWORD', '')
        resp = client.get('/login')
        assert resp.status_code in (302, 303)

    def test_open_redirect_prevention(self, client, monkeypatch):
        """Login with next=//evil.com should redirect to / not to evil.com."""
        monkeypatch.setenv('DOSSIER_UI_PASSWORD', 'test-pass')
        resp = client.post(
            '/login?next=//evil.com',
            data={'password': 'test-pass'},
        )
        assert resp.status_code in (302, 303)
        location = resp.headers.get('Location', '')
        # Should NOT redirect to //evil.com
        assert '//evil.com' not in location
        assert location.endswith('/')

    def test_open_redirect_prevention_javascript(self, client, monkeypatch):
        """Login with next=javascript:... should redirect to /."""
        monkeypatch.setenv('DOSSIER_UI_PASSWORD', 'test-pass')
        resp = client.post(
            '/login?next=javascript:alert(1)',
            data={'password': 'test-pass'},
        )
        assert resp.status_code in (302, 303)
        location = resp.headers.get('Location', '')
        assert 'javascript' not in location

    def test_logout_clears_session(self, client, monkeypatch):
        """GET /logout clears session and redirects to /login."""
        monkeypatch.setenv('DOSSIER_UI_PASSWORD', 'test-pass')
        # First login
        client.post('/login', data={'password': 'test-pass'})
        # Then logout
        resp = client.get('/logout')
        assert resp.status_code in (302, 303)
        location = resp.headers.get('Location', '')
        assert 'login' in location

    def test_valid_next_url_preserved(self, client, monkeypatch):
        """Login with valid next=/dashboard redirects to /dashboard."""
        monkeypatch.setenv('DOSSIER_UI_PASSWORD', 'test-pass')
        resp = client.post(
            '/login?next=/dashboard',
            data={'password': 'test-pass'},
        )
        assert resp.status_code in (302, 303)
        location = resp.headers.get('Location', '')
        assert '/dashboard' in location
