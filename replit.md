# Lead Machine - Deep-Dive Research Engine

## Overview
A Flask application for analyzing GitHub organizations to detect localization signals. The app scans GitHub repositories, commits, and PRs to find internationalization (i18n) indicators and provides AI-powered analysis using OpenAI GPT-5-mini (via Replit AI Integrations).

## Project Structure
```
├── app.py              # Main Flask application
├── config.py           # Configuration settings
├── database.py         # SQLite database module
├── ai_summary.py       # AI-powered analysis (OpenAI GPT-5-mini via Replit AI Integrations)
├── monitors/           # GitHub scanning modules
│   ├── discovery.py    # GitHub org discovery
│   └── scanner.py      # Deep scan functionality
├── static/             # Static assets
│   ├── css/style.css   # Styles
│   └── js/stream.js    # SSE streaming client
├── templates/          # Jinja2 templates
│   ├── base.html       # Base template
│   ├── index.html      # Homepage
│   ├── console.html    # Scan console view
│   ├── report.html     # Report view
│   ├── history.html    # Scan history
│   └── error.html      # Error page
└── data/               # SQLite database storage
```

## Running the Application
The Flask server runs on port 5000:
```bash
python app.py
```

## Environment Variables
- `FLASK_SECRET_KEY` - Flask secret key (optional, has default)
- `FLASK_DEBUG` - Enable debug mode (optional)
- `GITHUB_TOKEN` - GitHub API token for scanning
- `AI_INTEGRATIONS_OPENAI_API_KEY` - Auto-managed by Replit AI Integrations
- `AI_INTEGRATIONS_OPENAI_BASE_URL` - Auto-managed by Replit AI Integrations

## Technology Stack
- **Backend**: Python 3.11, Flask
- **Database**: SQLite (local file-based)
- **Frontend**: HTML, CSS, JavaScript with Server-Sent Events
- **AI**: OpenAI GPT-5-mini (all tasks — analysis, writing, cold emails, via Replit AI Integrations)

## MCP Server
The MCP Server runs on port 5001 with SSE transport for Claude Desktop/CoWork integration. Flask on port 5000 is the public gateway, proxying `/sse` and `/messages/` to the MCP server.

**Architecture**: Flask handles all OAuth 2.0 (RFC 9728 + RFC 8414) and proxies authenticated SSE/message requests to the internal MCP server. The MCP server only validates Bearer tokens — no duplicate OAuth endpoints.

**OAuth Flow (for Claude CoWork)**:
1. Client discovers `/.well-known/oauth-protected-resource` (RFC 9728) → finds auth server
2. Client fetches `/.well-known/oauth-authorization-server` (RFC 8414) → gets authorize/token URLs
3. User approves at `/authorize` (no password gate currently)
4. Client exchanges auth code for Bearer token at `/token` → receives `MCP_API_KEY`
5. Client connects to `/sse` with Bearer token → Flask proxies to MCP server on port 5001

**Deployment**: Reserved VM (`deploymentTarget = "vm"`) via `start.sh` which launches MCP server (background) + gunicorn (foreground). Health check at `/health` returns 200 instantly.

**Auth Exemptions**: `/.well-known/`, `/authorize`, `/token`, `/sse`, `/messages` are exempt from both `DOSSIER_API_KEY` middleware and CSRF protection (they use Bearer token auth instead).

## Recent Changes
- 2026-03-06: Fixed MCP deployment and OAuth for Claude CoWork
  - Added `/.well-known/oauth-protected-resource` (RFC 9728) — Claude CoWork discovers auth server through this
  - Exempted OAuth/MCP paths from DOSSIER_API_KEY and CSRF middleware
  - Removed duplicate OAuth routes from `mcp_server.py` (Flask is the sole OAuth gateway)
  - Added `/health` endpoint for fast deployment health checks
  - Deployment target set to Reserved VM (`vm`) with `start.sh`
  - Fixed duplicate `health_check` function name conflict
- 2026-03-05: Fixed MCP Server startup
  - Installed `mcp[cli]` package (was missing)
  - Fixed `FastMCP.run()` API: host/port set via `mcp.settings` (not run() kwargs)
  - Hardened `_safe_add_column` in database.py: catches only duplicate-column errors, re-raises others
- 2026-03-02: Fixed API authentication blocking browser requests
  - `DOSSIER_API_KEY` middleware was returning 401 for all browser JS fetch calls (queue status, scan statuses, etc.)
  - Added secure same-origin exemption using strict `urlparse` netloc comparison
  - Browser requests from the app's own pages are now allowed through; external requests still require API key
  - Prevents subdomain/substring spoofing attacks on Referer/Origin headers
- 2026-02-22: Consolidated all AI to OpenAI GPT-5-mini
  - Removed all Google Gemini dependencies (genai library, GEMINI_API_KEY, GEMINI_MODEL)
  - GPT-5-mini now handles everything: scan analysis, cold emails, deep-dive narratives, LinkedIn outreach, signal verification, website analysis, company discovery
  - Single provider via Replit AI Integrations (auto-managed key, no separate API key needed)
  - Upgraded last remaining gpt-4o-mini reference in ai_summary.py to gpt-5-mini
- 2026-02-19: Split AI models by task type for cost optimization
  - Fixed AI fallback chain: only one engine runs per scan (was running all three)
  - Fixed LinkedIn Prospector routes that were defined after app.run() (never registered)
- 2026-02-13: Added Apollo sequence enrollment to report page
  - New API: GET /api/apollo/sequences fetches available Apollo email sequences
  - New API: POST /api/apollo/enroll-sequence searches/creates contact and enrolls in sequence
  - "Enroll in Sequence" button added to each contributor card in report.html
  - Sequence selector dropdown with caching, auto-email-fetch, and enrollment status badges
  - Fixed corrupted /api/send-outreach-email route decorator
- 2026-02-10: Switched AI engine from Gemini to OpenAI GPT-5-mini
  - Installed Replit AI Integrations for OpenAI (no API key needed, billed to credits)
  - GPT-5-mini is now the primary AI engine for scan analysis
  - Gemini kept as fallback if OpenAI is unavailable
  - Rule-based analysis remains as final fallback
- 2026-01-29: Fixed CSV import data loss and added auto-scan functionality
  - Added localStorage backup for CSV data to prevent data loss between page states
  - Added auto-queuing: newly imported accounts are automatically submitted for GitHub scanning
  - Throttled auto-queue to max 50 accounts per batch to prevent executor overload
  - Remaining accounts are picked up by the watchdog process
  - Changed import UX: immediate redirect to Accounts page after batch queued (no polling/waiting required)
- 2026-01-14: Fixed AgentMail integration and SKILL.md cold email generation
  - Rewrote agentmail_client.py to use the official AgentMail Python SDK
  - Fixed inbox retrieval (inbox_id attribute) and message sending (inboxes.messages.send)
  - Added SKILL.md integration: ai_summary.py now loads `.agent/skills/cold-outreach/SKILL.md`
  - Cold email generation now uses custom skill instructions if the file exists
  - Token pool auto-discovery: config.py now finds tokens matching GITHUB_TOKEN_*, GitHubToken_* (case-insensitive)
  - BDRs can add their own GitHub tokens as secrets without modifying any config
- 2026-01-13: Improved UX for queued accounts - shows "In Queue" with queue position instead of "Never" for accounts waiting to be scanned
- 2026-01-12: Comprehensive UI redesign to professional light theme
  - Removed all emojis from templates and backend logs
  - Replaced emoji icons with SVG icons on homepage feature list
  - Inverted console log output to dark background for terminal-like appearance
  - Clean tier badges remain functional (badge-tier_class pattern)
  - Fixed app port configuration to 5000
- 2026-01-11: Added Settings & Status backend (webhook configuration, usage stats tracking, webhook logs)
  - New tables: system_settings, system_stats, webhook_logs
  - New API routes: GET/POST /api/settings, GET /api/stats, GET /api/webhook-logs
  - Scanner now tracks scans_run and api_calls_estimated per day
  - Webhooks are logged with success/fail status
- 2026-01-07: Initial Replit environment setup
