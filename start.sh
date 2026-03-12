#!/bin/bash
# Start both Flask app and MCP server for production deployment

set -euo pipefail

export DASHBOARD_ONLY_MODE="${DASHBOARD_ONLY_MODE:-1}"

# Start gunicorn FIRST so port 5000 binds immediately for health checks.
# Fall back to the Flask server if gunicorn is not available on PATH but the
# Python package environment is otherwise healthy.
if python3 -m gunicorn --version >/dev/null 2>&1; then
  python3 -m gunicorn --bind=0.0.0.0:5000 --reuse-port --workers=1 --threads=4 --timeout=120 app:app &
else
  echo "[startup] gunicorn not available; falling back to python3 app.py"
  python3 app.py &
fi
APP_PID=$!

# Give gunicorn a moment to bind the port, then start MCP server
sleep 2
MCP_TRANSPORT=sse MCP_PORT=5001 python3 mcp_server.py &
MCP_PID=$!

# Wait for the primary web process
wait $APP_PID

# If the primary web process exits, kill MCP server too
kill $MCP_PID 2>/dev/null
