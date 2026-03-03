"""
Tests for global error boundary handling.

Verifies that:
- Unhandled exceptions in API routes return JSON 500 responses
- Unhandled exceptions in HTML routes return HTML 500 responses
- Error responses never leak tracebacks or internal details
- Slow request logging fires for requests > 5 seconds
- 404 returns the correct format for API vs HTML routes
- A request_id is included in JSON error responses
"""
import json
import logging
import os
import time

import pytest
from unittest.mock import patch

os.environ.setdefault('APOLLO_API_KEY', 'test-apollo-key')

from app import app
from rate_limiter import limiter


# ---------------------------------------------------------------------------
# Register test-only routes that deliberately raise exceptions
# ---------------------------------------------------------------------------

@app.route('/api/_test/crash')
def _test_api_crash():
    """Test route: raises an unhandled exception on an API path."""
    raise RuntimeError('deliberate test explosion')


@app.route('/_test/crash')
def _test_html_crash():
    """Test route: raises an unhandled exception on an HTML path."""
    raise RuntimeError('deliberate test explosion')


@app.route('/api/_test/slow')
def _test_api_slow():
    """Test route: simulates a slow request (time is mocked in tests)."""
    return json.dumps({'status': 'ok'}), 200, {'Content-Type': 'application/json'}


@app.route('/api/_test/value-error')
def _test_api_value_error():
    """Test route: raises a ValueError (non-HTTP exception)."""
    raise ValueError('bad value in request processing')


@app.route('/_test/value-error')
def _test_html_value_error():
    """Test route: raises a ValueError on an HTML path."""
    raise ValueError('bad value in request processing')


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_rate_limiter():
    """Clear rate limiter state before each test."""
    limiter._hits.clear()


@pytest.fixture
def client():
    """Create a Flask test client with TESTING enabled."""
    app.config['TESTING'] = True
    # PROPAGATE_EXCEPTIONS must be False so error handlers are invoked
    app.config['PROPAGATE_EXCEPTIONS'] = False
    # Trap HTTP exceptions must be False so our errorhandlers run
    app.config['TRAP_HTTP_EXCEPTIONS'] = False
    with app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# 500 Error — API routes return JSON
# ---------------------------------------------------------------------------

class TestApiUnhandledException:
    """Unhandled exceptions on /api/* routes must return JSON 500."""

    def test_runtime_error_returns_json_500(self, client):
        resp = client.get('/api/_test/crash')
        assert resp.status_code == 500
        data = resp.get_json()
        assert data is not None, 'Expected JSON response body'
        assert data['status'] == 'error'
        assert data['message'] == 'Internal server error'
        assert 'request_id' in data

    def test_value_error_returns_json_500(self, client):
        resp = client.get('/api/_test/value-error')
        assert resp.status_code == 500
        data = resp.get_json()
        assert data is not None
        assert data['status'] == 'error'
        assert data['message'] == 'Internal server error'

    def test_api_error_never_contains_traceback(self, client):
        resp = client.get('/api/_test/crash')
        body = resp.get_data(as_text=True)
        assert 'Traceback' not in body
        assert 'deliberate test explosion' not in body
        assert 'RuntimeError' not in body

    def test_api_error_never_contains_internal_details(self, client):
        resp = client.get('/api/_test/value-error')
        body = resp.get_data(as_text=True)
        assert 'bad value in request processing' not in body
        assert 'ValueError' not in body


# ---------------------------------------------------------------------------
# 500 Error — HTML routes return HTML
# ---------------------------------------------------------------------------

class TestHtmlUnhandledException:
    """Unhandled exceptions on non-API routes must return HTML 500."""

    def test_runtime_error_returns_html_500(self, client):
        resp = client.get('/_test/crash')
        assert resp.status_code == 500
        assert 'text/html' in resp.content_type
        body = resp.get_data(as_text=True)
        # Should contain the generic error message from error.html template
        assert 'Internal server error' in body or 'Something went wrong' in body

    def test_value_error_returns_html_500(self, client):
        resp = client.get('/_test/value-error')
        assert resp.status_code == 500
        assert 'text/html' in resp.content_type

    def test_html_error_never_contains_traceback(self, client):
        resp = client.get('/_test/crash')
        body = resp.get_data(as_text=True)
        assert 'Traceback' not in body
        assert 'deliberate test explosion' not in body
        assert 'RuntimeError' not in body

    def test_html_error_never_contains_internal_details(self, client):
        resp = client.get('/_test/value-error')
        body = resp.get_data(as_text=True)
        assert 'bad value in request processing' not in body
        assert 'ValueError' not in body


# ---------------------------------------------------------------------------
# 404 Error — API vs HTML format
# ---------------------------------------------------------------------------

class TestNotFoundHandling:
    """404 errors should return JSON for API routes, HTML otherwise."""

    def test_api_404_returns_json(self, client):
        resp = client.get('/api/this-route-does-not-exist')
        assert resp.status_code == 404
        data = resp.get_json()
        assert data is not None, 'Expected JSON response for API 404'
        assert data['status'] == 'error'
        assert data['message'] == 'Not found'

    def test_html_404_returns_html(self, client):
        resp = client.get('/this-page-does-not-exist')
        assert resp.status_code == 404
        assert 'text/html' in resp.content_type
        body = resp.get_data(as_text=True)
        assert 'Page not found' in body

    def test_api_404_never_contains_traceback(self, client):
        resp = client.get('/api/nonexistent-endpoint')
        body = resp.get_data(as_text=True)
        assert 'Traceback' not in body


# ---------------------------------------------------------------------------
# Exception logging
# ---------------------------------------------------------------------------

class TestExceptionLogging:
    """Error handlers must log full traceback server-side."""

    def test_api_crash_logs_exception(self, client, caplog):
        with caplog.at_level(logging.ERROR):
            client.get('/api/_test/crash')
        # Verify the traceback was logged
        assert any('UNHANDLED EXCEPTION' in r.message or '500 Internal Server Error' in r.message
                    for r in caplog.records)

    def test_html_crash_logs_exception(self, client, caplog):
        with caplog.at_level(logging.ERROR):
            client.get('/_test/crash')
        assert any('UNHANDLED EXCEPTION' in r.message or '500 Internal Server Error' in r.message
                    for r in caplog.records)

    def test_logged_message_contains_request_id(self, client, caplog):
        with caplog.at_level(logging.ERROR):
            client.get('/api/_test/crash')
        assert any('request_id=' in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Slow request logging
# ---------------------------------------------------------------------------

class TestSlowRequestLogging:
    """Requests taking > 5 seconds should produce a warning log."""

    def test_slow_request_logs_warning(self, client, caplog):
        """Verify slow requests (>5s) are logged with a warning."""
        original_time = time.time

        call_count = [0]

        def mock_time():
            call_count[0] += 1
            real_now = original_time()
            # After the first call (before_request sets g.request_start_time),
            # subsequent calls (in after_request) return +6 seconds
            if call_count[0] <= 1:
                return real_now
            return real_now + 6.0

        with patch('app.time') as mock_time_module:
            # time.time() is called in before_request and after_request
            mock_time_module.time = mock_time
            # Ensure time.sleep still works if needed
            mock_time_module.sleep = time.sleep

            with caplog.at_level(logging.WARNING):
                resp = client.get('/api/_test/slow')

        assert resp.status_code == 200
        slow_logs = [r for r in caplog.records if 'SLOW REQUEST' in r.message]
        assert len(slow_logs) >= 1, 'Expected a SLOW REQUEST warning log'
        assert '/api/_test/slow' in slow_logs[0].message

    def test_fast_request_does_not_log_warning(self, client, caplog):
        """Verify fast requests (<5s) do not produce a slow request warning."""
        with caplog.at_level(logging.WARNING):
            resp = client.get('/api/_test/slow')

        assert resp.status_code == 200
        slow_logs = [r for r in caplog.records if 'SLOW REQUEST' in r.message]
        assert len(slow_logs) == 0, 'Fast request should not produce SLOW REQUEST warning'


# ---------------------------------------------------------------------------
# Request ID presence
# ---------------------------------------------------------------------------

class TestRequestId:
    """Every error response for API routes should contain a request_id."""

    def test_500_includes_request_id(self, client):
        resp = client.get('/api/_test/crash')
        data = resp.get_json()
        assert 'request_id' in data
        # Must look like a UUID (36 chars with dashes)
        assert len(data['request_id']) == 36
        assert data['request_id'].count('-') == 4

    def test_value_error_includes_request_id(self, client):
        resp = client.get('/api/_test/value-error')
        data = resp.get_json()
        assert 'request_id' in data
        assert len(data['request_id']) == 36
