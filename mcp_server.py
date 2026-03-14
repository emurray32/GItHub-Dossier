#!/usr/bin/env python3
"""
MCP Server for GitHub Dossier — Lightweight BDR Sequencing Tool.

Exposes intent signal management, prospect discovery, email drafting,
and Apollo enrollment tools for Claude Code / CoWork integration.

Usage:
    python mcp_server.py              # stdio transport (for Claude Code)
    mcp dev mcp_server.py             # interactive dev/test mode
"""

import logging
import os
import secrets
import sys

# Ensure project root is on sys.path so we can import existing modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

# Import database initialization
from database import init_db

# Initialize database (creates tables if needed)
init_db()

# Create MCP server
mcp = FastMCP("dossier_mcp")


# ---------------------------------------------------------------------------
# MCP Resource: Cold Outreach Skill
# ---------------------------------------------------------------------------

_SKILL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".agent", "skills", "cold-outreach", "SKILL.md",
)


@mcp.resource("dossier://skills/cold-outreach")
def cold_outreach_skill() -> str:
    """Cold outreach writing rules and BDR workflow for Phrase."""
    try:
        with open(_SKILL_PATH, "r") as f:
            return f.read()
    except FileNotFoundError:
        return "Cold outreach skill file not found."


# ---------------------------------------------------------------------------
# MCP Prompt: Write Outreach for Account
# ---------------------------------------------------------------------------

@mcp.prompt()
def write_outreach(company_name: str) -> str:
    """Start the cold email writing workflow for a target account.

    Gathers intent signals, finds prospects, and guides you through
    writing a personalized email sequence one email at a time.
    """
    return f"""The BDR wants to write cold outreach emails for **{company_name}**.

Follow this workflow:

1. **Gather context** — Call these tools in parallel:
   - `dossier_get_account_signals` for "{company_name}"
   - `dossier_get_contributors` for "{company_name}" with has_email=true
   - `dossier_get_account` for "{company_name}"

2. **Brief the BDR** — Show a concise 3-4 line summary:
   - Company name + maturity level
   - Strongest intent signal (1-2 sentences, e.g., "Added react-i18next to main-app repo 3 weeks ago")
   - Top prospects with email (names + titles)

3. **Ask who to target** — "Who do you want to reach out to? Or should I pick the best match?"

4. **Write Email 1** — Present TWO versions (A and B, different angles). Follow the cold outreach skill rules exactly (read `dossier://skills/cold-outreach` for writing rules).

5. **Iterate one at a time** — After BDR picks/edits Email 1, write Email 2 (one version only). Get approval. Then Email 3. Then Email 4. Each email must use a different angle and build on the sequence arc:
   - Email 1: Hook + value prop (strongest signal)
   - Email 2: Different angle (different signal or pain point)
   - Email 3: Lighter touch (social proof or quick insight)
   - Email 4: Breakup (final value add, graceful close)

6. **Enroll** — After all emails are approved, ask: "Ready to enroll [name] into the Apollo sequence?" If yes, call `dossier_enroll_contributor`.

IMPORTANT: Read the cold outreach skill resource for email formatting rules, Apollo dynamic variables, persona adaptation, and Phrase messaging guidelines."""


# ---------------------------------------------------------------------------
# Register V2 MCP tools (intent-signal-first workflow)
# ---------------------------------------------------------------------------

try:
    from v2.mcp_tools import register_v2_tools
    register_v2_tools(mcp)
    logging.info("[MCP] V2 tools registered successfully")
except ImportError:
    logging.warning("[MCP] v2.mcp_tools not found — v2 tools not available")
except Exception as e:
    logging.error("[MCP] Failed to register v2 tools: %s", e)


# ---------------------------------------------------------------------------
# Bearer-token auth wrapper (OAuth is handled by Flask on port 5000)
# ---------------------------------------------------------------------------

def _create_authenticated_sse_app(mcp_instance, api_key: str):
    """Wrap the MCP SSE app with Bearer-token auth middleware."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            else:
                token = ""

            if not token or not secrets.compare_digest(token, api_key):
                return JSONResponse(
                    {"error": "Unauthorized — provide MCP_API_KEY as Bearer token in Authorization header"},
                    status_code=401,
                )
            return await call_next(request)

    inner_app = mcp_instance.sse_app()
    inner_app.add_middleware(BearerAuthMiddleware)
    return inner_app


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "sse":
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "5001"))

        api_key = os.environ.get("MCP_API_KEY", "")
        if not api_key:
            logging.warning(
                "[MCP] MCP_API_KEY is not set — MCP server is UNPROTECTED. "
                "Set MCP_API_KEY in Secrets to require authentication."
            )
            mcp.settings.host = host
            mcp.settings.port = port
            mcp.run(transport="sse")
        else:
            logging.info("[MCP] OAuth + Bearer-token auth enabled (MCP_API_KEY is set)")
            import uvicorn
            app = _create_authenticated_sse_app(mcp, api_key)
            uvicorn.run(app, host=host, port=port)
    else:
        mcp.run(transport="stdio")
