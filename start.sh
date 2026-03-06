#!/bin/bash
# Start both Flask app and MCP server for production deployment

# Start MCP server in background
MCP_TRANSPORT=sse MCP_PORT=5001 python mcp_server.py &
MCP_PID=$!

# Start Flask app via gunicorn (NO --preload, starts accepting connections faster)
gunicorn --bind=0.0.0.0:5000 --reuse-port --workers=2 --timeout=120 app:app

# If gunicorn exits, kill MCP server too
kill $MCP_PID 2>/dev/null
