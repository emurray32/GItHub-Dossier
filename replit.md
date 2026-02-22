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

## Recent Changes
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
