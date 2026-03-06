#!/bin/bash
# Start both Flask app and MCP server for production deployment

# Start gunicorn FIRST so port 5000 binds immediately for health checks
gunicorn --bind=0.0.0.0:5000 --reuse-port --workers=1 --threads=4 --timeout=120 app:app &
GUNICORN_PID=$!

# Give gunicorn a moment to bind the port, then start MCP server
sleep 2
MCP_TRANSPORT=sse MCP_PORT=5001 python mcp_server.py &
MCP_PID=$!

# Wait for gunicorn (the primary process)
wait $GUNICORN_PID

# If gunicorn exits, kill MCP server too
kill $MCP_PID 2>/dev/null
