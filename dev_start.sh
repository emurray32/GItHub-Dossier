#!/bin/bash
# Dev startup script for Replit workflow
# Sources the Nix environment and starts both servers

# Source the Nix profile to get python3 in PATH
if [ -f /home/runner/.nix-profile/etc/profile.d/nix.sh ]; then
  . /home/runner/.nix-profile/etc/profile.d/nix.sh
fi

# Add pythonlibs to PATH
export PATH="/home/runner/workspace/.pythonlibs/bin:$PATH"

# Set DASHBOARD_ONLY_MODE
export DASHBOARD_ONLY_MODE=1

# Start MCP server in background
MCP_TRANSPORT=sse MCP_PORT=5001 python3 mcp_server.py &
MCP_PID=$!

# Give MCP server a moment to start
sleep 1

# Start Flask app in foreground
python3 app.py

# If Flask exits, kill MCP server too
kill $MCP_PID 2>/dev/null
