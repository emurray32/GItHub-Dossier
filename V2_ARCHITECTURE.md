# V2 Architecture — Intent-Signal-First Prospecting Platform

## Product Model

Lead Machine v2 is an **intent-signal-first** prospecting platform. Everything starts from an intent signal — a reason to care about a company. The platform ingests signals, organizes them into a queue, and enables BDRs/AEs to move from signal → prospect → enrollment in as few keystrokes as possible.

## Domain Entities

### Intent Signal (root object)
- Everything hangs off signals
- One account can have multiple signals
- Queue is signal-first, not account-first
- Status: `new` → `actioned` → `archived`
- Table: `intent_signals`

### Account
- Extends existing `monitored_accounts` table
- New fields: `account_owner`, `account_status`, `linkedin_url`, `company_size`
- Status flow: `new` → `sequenced` → `revisit` → `noise`
- Tier (0-4) is separate from status — tier is for scoring, status is for pipeline state

### Prospect
- People found via Apollo
- Tied to accounts and signals
- Status: `found` → `drafting` → `enrolled` → `sequence_complete`
- Table: `prospects`

### Draft
- Persisted, editable email drafts per prospect per sequence step
- Status: `generated` → `edited` → `approved` → `enrolled`
- Table: `drafts`

### Feedback Log
- Stores critiques when a BDR rejects or regenerates a draft
- Reviewable later but does NOT auto-train prompts
- Table: `feedback_log`

### Activity Log
- Audit trail for all key actions
- Table: `activity_log`

### Writing Preferences
- Org-wide writing rules (key-value)
- Separate from campaign-specific guidelines
- Both layers combine during draft generation
- Table: `writing_preferences`

## Multi-Signal Rules

When an account has multiple signals:
1. Each signal is **independent in the queue**
2. When ANY prospect on the account gets enrolled → `account_status = 'sequenced'`
3. When ALL sequences on the account complete with no reply → `account_status = 'revisit'`
4. `noise` is always a manual action
5. Archiving a signal doesn't change account status

## Directory Structure

```
v2/
├── __init__.py
├── schema.py          # DDL for new tables + column additions
├── models.py          # Pydantic domain models
├── db.py              # DB helper wrapper
├── mcp_tools.py       # MCP tools for CoWork interface
├── services/
│   ├── __init__.py
│   ├── signal_service.py      # Signal CRUD + workspace
│   ├── account_service.py     # Account status management
│   ├── prospect_service.py    # Prospect CRUD + filtering
│   ├── writing_prefs_service.py  # Writing preferences
│   ├── campaign_service.py    # Campaign recommendation
│   ├── ingestion_service.py   # CSV/manual/scan signal ingestion
│   ├── draft_service.py       # Draft generation + regeneration
│   ├── enrollment_service.py  # Apollo enrollment orchestration
│   ├── feedback_service.py    # Critique logging
│   └── activity_service.py    # Activity audit trail
└── routes/
    ├── __init__.py
    ├── web.py          # GET /app → SPA shell
    ├── api.py          # REST API for the web frontend
    ├── ingestion.py    # CSV upload + manual signal creation
    ├── draft.py        # Draft CRUD + regeneration
    └── enrollment.py   # Enrollment orchestration
```

## Integration Points

### Database
- v2 schema is initialized via `init_v2_schema()` called from `database.init_db()`
- Uses the same `db_connection()` context manager and dialect adapters
- v2 tables coexist with legacy tables

### App
- v2 blueprints registered in `app.py` via try/except imports
- Mounted at `/app` (web UI) and `/v2/api/*` (REST API)
- Legacy routes remain at their current paths

### MCP
- v2 tools registered via `register_v2_tools(mcp)` in `mcp_server.py`
- Coexist with legacy tools
- Same FastMCP server on port 5001

### Apollo
- Uses existing `apollo_pipeline.py` for all API calls
- Rate limiter shared with legacy enrollment

### Email Generation
- Uses existing `email_engine.py` patterns for initial generation
- Direct LLM calls via Replit AI proxy for fast regeneration
- Writing context = org-wide preferences + campaign guidelines

## Workflow (Web App)

1. User opens `/app`
2. Left panel: signal queue (filterable by owner, status)
3. Select signal → right panel loads workspace
4. See signal context, confirm campaign recommendation
5. Search Apollo for prospects (using campaign personas)
6. Review/edit generated drafts (per prospect, per sequence step)
7. Regenerate weak drafts with one-line critique
8. Approve and enroll in Apollo sequences
9. Move to next signal

## Workflow (CoWork/MCP)

Same workflow via MCP tools:
`list_signal_queue` → `get_signal_workspace` → `recommend_campaign` → `find_prospects` → `generate_draft_sequence` → `regenerate_draft_step` → `approve_draft` → `enroll_prospect`

## Account Status Rules

| Trigger | Account Status Change |
|---------|----------------------|
| Signal ingested, new account | → `new` |
| First prospect enrolled on account | → `sequenced` |
| All sequences complete, no reply | → `revisit` |
| User manually marks noise | → `noise` |
| Revisit signal created | stays `revisit` until re-sequenced |
