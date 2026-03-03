"""
Tests for the /api/health endpoint.

Verifies:
- Healthy response when all checks pass
- Degraded response when database is down
- Degraded response when no GitHub tokens are available
- Response structure contains all required fields
- No authentication required (no X-API-Key header needed)
"""
import os
import time
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

# Set env vars before importing app to avoid side effects
os.environ.setdefault('APOLLO_API_KEY', 'test-apollo-key')
os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret-key-for-health')

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
    with app.test_client() as c:
        yield c


# ──────────────────────────────────────────────────────────────────────
# Healthy Response
# ──────────────────────────────────────────────────────────────────────

class TestHealthyResponse:
    """Health endpoint returns 200 when all subsystems are working."""

    def test_healthy_when_all_checks_pass(self, client):
        """GET /api/health returns 200 with status=healthy when DB and tokens are OK."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 5,
            'tokens_available': 3,
            'tokens_rate_limited': 2,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool) as mock_get_pool, \
             patch('scoring.get_scoring_fingerprint', return_value='abc123def456'):
            # Make db_connection work as a context manager
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'healthy'
        assert data['checks']['database']['status'] == 'ok'
        assert data['checks']['github_tokens']['status'] == 'ok'
        assert data['checks']['github_tokens']['available'] == 3
        assert data['checks']['github_tokens']['total'] == 5
        assert data['checks']['scoring_version'] == 'abc123def456'

    def test_uptime_is_positive(self, client):
        """Uptime should be a positive number of seconds."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 1,
            'tokens_available': 1,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='fp123'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        data = resp.get_json()
        assert data['uptime_seconds'] > 0


# ──────────────────────────────────────────────────────────────────────
# Degraded: Database Down
# ──────────────────────────────────────────────────────────────────────

class TestDegradedDatabase:
    """Health endpoint returns 503 when database is unreachable."""

    def test_degraded_when_db_raises(self, client):
        """GET /api/health returns 503 with database error status when DB raises."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 3,
            'tokens_available': 3,
        }

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='fp456'):
            # Make db_connection raise an exception
            mock_db_ctx.return_value.__enter__ = MagicMock(
                side_effect=Exception('connection refused')
            )
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        assert resp.status_code == 503
        data = resp.get_json()
        assert data['status'] == 'degraded'
        assert data['checks']['database']['status'] == 'error'
        assert data['checks']['database']['latency_ms'] is None
        # Token check should still be healthy
        assert data['checks']['github_tokens']['status'] == 'ok'

    def test_degraded_when_cursor_execute_raises(self, client):
        """Database check fails if SELECT 1 query raises."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 2,
            'tokens_available': 2,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception('disk I/O error')

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='fp789'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        assert resp.status_code == 503
        data = resp.get_json()
        assert data['status'] == 'degraded'
        assert data['checks']['database']['status'] == 'error'


# ──────────────────────────────────────────────────────────────────────
# Degraded: No GitHub Tokens
# ──────────────────────────────────────────────────────────────────────

class TestDegradedTokens:
    """Health endpoint returns 503 when GitHub tokens are unavailable."""

    def test_degraded_when_no_tokens_configured(self, client):
        """GET /api/health returns degraded with error when pool_size=0."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 0,
            'tokens_available': 0,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='fp000'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        assert resp.status_code == 503
        data = resp.get_json()
        assert data['status'] == 'degraded'
        assert data['checks']['github_tokens']['status'] == 'error'
        assert data['checks']['github_tokens']['total'] == 0

    def test_degraded_when_all_tokens_rate_limited(self, client):
        """GET /api/health returns degraded with warning when available=0 but total>0."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 5,
            'tokens_available': 0,
            'tokens_rate_limited': 5,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='fp111'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        assert resp.status_code == 503
        data = resp.get_json()
        assert data['status'] == 'degraded'
        assert data['checks']['github_tokens']['status'] == 'warning'
        assert data['checks']['github_tokens']['available'] == 0
        assert data['checks']['github_tokens']['total'] == 5


# ──────────────────────────────────────────────────────────────────────
# Response Structure Validation
# ──────────────────────────────────────────────────────────────────────

class TestResponseStructure:
    """Verify the response contains all required fields with correct types."""

    def test_all_top_level_keys_present(self, client):
        """Response must have status, checks, uptime_seconds, timestamp."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 1,
            'tokens_available': 1,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='struct-fp'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        data = resp.get_json()
        assert 'status' in data
        assert 'checks' in data
        assert 'uptime_seconds' in data
        assert 'timestamp' in data

    def test_checks_has_all_subsystems(self, client):
        """checks dict must include database, github_tokens, scoring_version."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 2,
            'tokens_available': 2,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='check-fp'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        checks = resp.get_json()['checks']
        assert 'database' in checks
        assert 'github_tokens' in checks
        assert 'scoring_version' in checks

    def test_database_check_has_latency(self, client):
        """database check must include status and latency_ms."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 1,
            'tokens_available': 1,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='lat-fp'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        db_check = resp.get_json()['checks']['database']
        assert 'status' in db_check
        assert 'latency_ms' in db_check
        assert isinstance(db_check['latency_ms'], (int, float))

    def test_github_tokens_check_has_counts(self, client):
        """github_tokens check must include status, available, total."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 4,
            'tokens_available': 2,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='tok-fp'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        tokens_check = resp.get_json()['checks']['github_tokens']
        assert 'status' in tokens_check
        assert 'available' in tokens_check
        assert 'total' in tokens_check
        assert isinstance(tokens_check['available'], int)
        assert isinstance(tokens_check['total'], int)

    def test_timestamp_is_iso_format(self, client):
        """Timestamp field should be ISO 8601 format ending with Z."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 1,
            'tokens_available': 1,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='ts-fp'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        ts = resp.get_json()['timestamp']
        assert ts.endswith('Z')
        # Should be parseable as ISO format (minus the trailing Z)
        from datetime import datetime
        datetime.fromisoformat(ts.rstrip('Z'))

    def test_status_is_valid_enum(self, client):
        """status field must be either 'healthy' or 'degraded'."""
        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 1,
            'tokens_available': 1,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='enum-fp'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.get('/api/health')

        assert resp.get_json()['status'] in ('healthy', 'degraded')


# ──────────────────────────────────────────────────────────────────────
# No Auth Required
# ──────────────────────────────────────────────────────────────────────

class TestNoAuthRequired:
    """Health endpoint must be accessible without authentication."""

    def test_no_api_key_required(self, client, monkeypatch):
        """GET /api/health succeeds even when DOSSIER_API_KEY is set and no key provided."""
        monkeypatch.setattr('config.Config.API_KEY', 'super-secret-api-key')

        mock_pool = MagicMock()
        mock_pool.get_pool_status.return_value = {
            'pool_size': 1,
            'tokens_available': 1,
        }

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        with patch('app.db_connection') as mock_db_ctx, \
             patch('utils.get_token_pool', return_value=mock_pool), \
             patch('scoring.get_scoring_fingerprint', return_value='noauth-fp'):
            mock_db_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

            # No X-API-Key header, no api_key query param
            resp = client.get('/api/health')

        # Should NOT be 401 — health check is public
        assert resp.status_code != 401
        data = resp.get_json()
        assert data['status'] in ('healthy', 'degraded')

    def test_other_api_routes_still_require_auth(self, client, monkeypatch):
        """Other /api/ routes still return 401 when API key is set."""
        monkeypatch.setattr('config.Config.API_KEY', 'super-secret-api-key')

        resp = client.get('/api/reports')
        assert resp.status_code == 401
