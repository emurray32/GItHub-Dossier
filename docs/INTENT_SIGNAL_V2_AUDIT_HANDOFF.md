# Intent Signal V2 — Audit Handoff

Branch: `codex/intent-signal-v2`
Date: 2026-03-12
Author: Claude Opus 4.6 (automated)

---

## 1. What Changed at a High Level

This branch adds a complete **intent-signal-first prospecting platform** (v2) inside the existing GitHub-Dossier Flask app. The v2 layer coexists with the legacy scan-first codebase. It introduces:

- 6 new database tables
- 6 new columns on 2 existing tables
- 11 service modules (CRUD, business logic, LLM integration)
- 5 Flask blueprint modules with 27 route handlers
- 1 MCP tools module with 16 tools for Claude CoWork
- 1 React SPA template (signal-queue UI at `/app`)
- 1 CSS file for the SPA

The legacy app continues to function unchanged. All v2 code lives in the `v2/` package.

---

## 2. Architecture and Main Domain Objects

### Domain Model

| Entity | Table | Purpose |
|--------|-------|---------|
| Intent Signal | `intent_signals` | Root object. Every workflow starts here. |
| Account | `monitored_accounts` (extended) | Company records. New fields: `account_status`, `account_owner`. |
| Prospect | `prospects` | People found via Apollo. Tied to signals + accounts. |
| Draft | `drafts` | Persisted email drafts per prospect per sequence step. |
| Feedback Log | `feedback_log` | Stores critiques when BDR rejects/regenerates a draft. |
| Activity Log | `activity_log` | Audit trail for all key actions. |
| Writing Preferences | `writing_preferences` | Org-wide writing rules (key-value). |
| Campaign | `campaigns` (extended) | New fields: `campaign_type`, `writing_guidelines`. |

### Status Flows

**Account status:** `new` → `sequenced` → `revisit` → `noise`
- `sequenced`: when any prospect on account gets enrolled
- `revisit`: when all sequences complete with no reply
- `noise`: manual action only

**Signal status:** `new` → `actioned` → `archived`

**Prospect enrollment:** `found` → `drafting` → `enrolled` → `sequence_complete`

**Draft status:** `generated` → `edited` → `approved` → `enrolled`

### Architecture Layers

```
v2/
├── schema.py              # DDL (called from database.init_db)
├── models.py              # Pydantic models + enums
├── db.py                  # DB helper wrapper
├── mcp_tools.py           # 15 MCP tools for CoWork
├── services/              # Business logic (1 module per domain entity)
│   ├── signal_service.py
│   ├── account_service.py
│   ├── prospect_service.py
│   ├── writing_prefs_service.py
│   ├── campaign_service.py
│   ├── ingestion_service.py
│   ├── draft_service.py
│   ├── enrollment_service.py
│   ├── feedback_service.py
│   └── activity_service.py
└── routes/                # Flask blueprints
    ├── web.py             # GET /app (SPA shell)
    ├── api.py             # /v2/api/* (signals, prospects, campaigns)
    ├── ingestion.py       # /v2/api/ingest/* (CSV, manual, scan import)
    ├── draft.py           # /v2/api/drafts/* (generate, edit, approve)
    └── enrollment.py      # /v2/api/enrollment/* (enroll, bulk, complete)
```

---

## 3. Legacy vs Active

### Now Legacy (still functional, not the primary interface)
- All scan-first routes in `app.py` (scan, stream_scan, reports)
- Old account management UX (accounts.html, accounts_tabler.html)
- Scorecard, history, webscraper pages
- BDR Review page (templates/bdr_review.html)
- The old enrollment batch system (enrollment_batches, enrollment_contacts tables)
- The old MCP tools in mcp_server.py (scan-oriented)

### Active / New
- Signal queue at `/app` — the new primary interface
- All `/v2/api/*` endpoints
- All v2 services
- 16 new MCP tools (registered alongside legacy tools)
- `intent_signals`, `prospects`, `drafts`, `feedback_log`, `activity_log`, `writing_preferences` tables

### Shared (used by both old and new)
- `monitored_accounts` table (extended with new columns)
- `campaigns` and `campaign_personas` tables (extended)
- `database.py` connection infrastructure
- `apollo_pipeline.py` for Apollo API calls
- `email_engine.py` templates and patterns
- `auth.py` authentication
- `validators.py` input validation

---

## 4. All New/Changed Routes

### New Routes (27 total)

**Web (v2/routes/web.py)**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/app` | Serve the v2 SPA shell |

**API (v2/routes/api.py, prefix /v2/api)**
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v2/api/signals` | List signals with filters |
| GET | `/v2/api/signals/<id>` | Get signal workspace (full context) |
| GET | `/v2/api/signals/counts` | Signal counts by status |
| GET | `/v2/api/signals/owners` | Distinct account owners |
| PUT | `/v2/api/signals/<id>/status` | Update signal status |
| PUT | `/v2/api/signals/<id>/campaign` | Update recommended campaign |
| POST | `/v2/api/signals/<id>/search` | Apollo people search |
| POST | `/v2/api/prospects` | Save found prospects |
| GET | `/v2/api/prospects` | Get prospects by signal_id |
| GET | `/v2/api/campaigns` | List campaigns |
| GET | `/v2/api/writing-preferences` | Get org writing preferences |
| PUT | `/v2/api/writing-preferences` | Update writing preferences |
| PUT | `/v2/api/accounts/<id>/status` | Update account status |

**Drafts (v2/routes/draft.py, prefix /v2/api/drafts)**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v2/api/drafts/generate` | Generate drafts for prospect |
| GET | `/v2/api/drafts` | Get drafts by prospect_id |
| GET | `/v2/api/drafts/<id>` | Get single draft |
| PUT | `/v2/api/drafts/<id>` | Update draft subject/body |
| POST | `/v2/api/drafts/<id>/regenerate` | Regenerate with critique |
| POST | `/v2/api/drafts/<id>/approve` | Approve single draft |
| POST | `/v2/api/drafts/approve-all` | Approve all for prospect |

**Enrollment (v2/routes/enrollment.py, prefix /v2/api/enrollment)**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v2/api/enrollment/enroll` | Enroll single prospect |
| POST | `/v2/api/enrollment/bulk` | Bulk enroll |
| POST | `/v2/api/enrollment/complete` | Mark sequence complete |

**Ingestion (v2/routes/ingestion.py, prefix /v2/api/ingest)**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v2/api/ingest/csv` | Upload CSV of signals |
| POST | `/v2/api/ingest/manual` | Create single signal |
| POST | `/v2/api/ingest/from-scans` | Convert scan signals to v2 |

---

## 5. All New/Changed MCP Tools

16 new tools registered via `register_v2_tools(mcp)`:

| Tool | Purpose |
|------|---------|
| `list_signal_queue` | List signals filtered by status/owner |
| `get_signal_workspace` | Full context for one signal |
| `create_signal` | Create signal for account (find-or-create) |
| `ingest_signals_from_csv` | Parse CSV content into signals |
| `recommend_campaign` | Get campaign recommendation for signal |
| `find_prospects` | Apollo people search by domain + titles |
| `save_prospects` | Persist found prospects to shared table (CoWork parity) |
| `generate_draft_sequence` | Generate email drafts for prospect |
| `regenerate_draft_step` | Rewrite one draft step with critique |
| `save_edited_draft` | Save manual edits to draft |
| `approve_draft` | Mark draft approved |
| `enroll_prospect` | Enroll in Apollo sequence |
| `mark_account_noise` | Mark account as noise (cascades signals to archived) |
| `mark_account_revisit` | Mark account for revisit (cascades signals to actioned) |
| `create_revisit_signal` | Create fresh signal for revisit |
| `list_feedback_log` | View recent draft critiques |

Legacy MCP tools in `mcp_server.py` are untouched and still functional.

---

## 6. Schema/Data-Model Changes

### New Tables

```sql
intent_signals (id, account_id FK, signal_description, evidence_type, evidence_value,
    signal_type, signal_source, recommended_campaign_id FK, recommended_campaign_reasoning,
    status, created_by, ingestion_batch_id, raw_payload, scan_signal_id, created_at, updated_at)

prospects (id, account_id FK, signal_id FK, full_name, first_name, last_name, title,
    email, email_verified, linkedin_url, apollo_person_id, do_not_contact,
    enrollment_status, sequence_id, sequence_name, created_at, updated_at)

drafts (id, prospect_id FK, signal_id FK, campaign_id FK, sequence_step, subject, body,
    generated_by, generation_model, generation_context, last_feedback, status,
    created_at, updated_at)

feedback_log (id, draft_id FK, prospect_id FK, signal_id FK, critique, sequence_step,
    created_by, created_at)

activity_log (id, event_type, entity_type, entity_id, details, created_by, created_at)

writing_preferences (id, preference_key UNIQUE, preference_value, updated_at)
```

### Columns Added to Existing Tables

```
monitored_accounts: account_owner TEXT, account_status TEXT DEFAULT 'new',
                    linkedin_url TEXT, company_size TEXT

campaigns: campaign_type TEXT DEFAULT 'signal_based', writing_guidelines TEXT
```

### Indexes Created
- `intent_signals`: account_id, status, created_at DESC, signal_type, signal_source, scan_signal_id
- `prospects`: account_id, signal_id, email, enrollment_status, apollo_person_id
- `drafts`: prospect_id, signal_id, status
- `feedback_log`: draft_id, signal_id
- `activity_log`: event_type, (entity_type + entity_id), created_at DESC

### Seeded Data
- 6 default writing preferences (tone, banned_phrases, preferred_structure, cta_guidance, signoff_guidance, custom_rules)

---

## 7. All Major Files Touched, Grouped by Purpose

### Foundation (Terminal 1)
| File | Lines | Purpose |
|------|-------|---------|
| `v2/__init__.py` | 7 | Package init |
| `v2/schema.py` | 210 | DDL for 6 tables + column migrations |
| `v2/models.py` | 230 | 15 Pydantic models, 7 enums |
| `v2/db.py` | 62 | DB helper wrapper |

### Core Services (Terminal 1)
| File | Lines | Purpose |
|------|-------|---------|
| `v2/services/signal_service.py` | 240 | Signal CRUD, workspace loader, queue queries |
| `v2/services/account_service.py` | 175 | Account status flow, owner mgmt, find-or-create |
| `v2/services/prospect_service.py` | 195 | Prospect CRUD, bulk create, DNC filtering |
| `v2/services/writing_prefs_service.py` | 110 | Writing preferences CRUD, LLM context builder |

### Business Logic Services (Terminal 3)
| File | Lines | Purpose |
|------|-------|---------|
| `v2/services/activity_service.py` | ~100 | Activity audit trail |
| `v2/services/feedback_service.py` | ~90 | Draft critique logging |
| `v2/services/campaign_service.py` | ~250 | Campaign listing + deterministic recommendation |
| `v2/services/ingestion_service.py` | ~280 | CSV, manual, scan-to-signal ingestion |

### Draft + Enrollment (Terminal 4)
| File | Lines | Purpose |
|------|-------|---------|
| `v2/services/draft_service.py` | 597 | LLM draft generation, regeneration, approval |
| `v2/services/enrollment_service.py` | 359 | Apollo enrollment, bulk enroll, account rollup |

### Routes (Terminal 2 + 3 + 4)
| File | Lines | Purpose |
|------|-------|---------|
| `v2/routes/web.py` | 13 | GET /app |
| `v2/routes/api.py` | 484 | 13 REST endpoints for frontend |
| `v2/routes/ingestion.py` | ~100 | 3 ingestion endpoints |
| `v2/routes/draft.py` | ~150 | 7 draft endpoints |
| `v2/routes/enrollment.py` | ~80 | 3 enrollment endpoints |

### MCP (Terminal 4)
| File | Lines | Purpose |
|------|-------|---------|
| `v2/mcp_tools.py` | 630 | 16 MCP tools for CoWork |

### Frontend (Terminal 2)
| File | Lines | Purpose |
|------|-------|---------|
| `templates/v2/app.html` | 1333 | React 18 SPA (Babel standalone) |
| `static/v2/app.css` | 305 | Custom CSS for SPA |

### Integration Hooks (Terminal 1)
| File | Change |
|------|--------|
| `database.py` | +8 lines: call `init_v2_schema()` from `init_db()` |
| `app.py` | +30 lines: register 5 v2 blueprints |
| `mcp_server.py` | +10 lines: register v2 MCP tools |

---

## 8. What Was Tested

### Passed
- **Syntax check**: All 22 v2 Python files compile cleanly (`py_compile`)
- **Import chain**: All 19 module imports resolve, all exported functions exist
- **Blueprint names**: All 5 blueprints have correct variable names and url_prefixes
- **MCP registration**: `register_v2_tools` is callable
- **Schema integration**: All 6 tables create correctly, FK chain works (signal→prospect→draft→feedback JOIN succeeds)
- **Column migrations**: `_safe_add_column` adds all 6 new columns on existing tables
- **Writing preferences seeding**: 6 defaults auto-seeded on first init
- **Jinja template**: `v2/app.html` parses without errors

### NOT Tested (could not run without Replit environment)
- **Flask app startup with v2 blueprints**: Requires `DATABASE_URL` and full environment
- **Apollo API calls**: Requires `APOLLO_API_KEY` (Replit secret)
- **LLM draft generation**: Requires `AI_INTEGRATIONS_OPENAI_BASE_URL` (Replit secret)
- **End-to-end workflow**: Signal → search → draft → enroll
- **React SPA rendering in browser**: Requires running Flask server
- **MCP tool execution**: Requires MCP server running
- **PostgreSQL-specific behavior**: Tested with SQLite; PG dialect handled by existing `_CursorProxy`

---

## 9. Codex Audit Findings — All Fixed

All 9 bugs from the initial Codex audit have been resolved in commit `bea6b53`:

### P1 Fixes (Critical)
| ID | Bug | Fix |
|----|-----|-----|
| P1-1 | Enrollment didn't inject draft content into Apollo | `enrollment_service.py`: builds `custom_field_values` dict from approved drafts (subject/body per step + top-level `email_subject`/`email_body`) |
| P1-2 | Campaign endpoint used nonexistent `campaign_name` column | `api.py` + `app.html`: changed to `name` (the real column) |
| P1-3 | Web app didn't persist draft edits | `app.html`: added `handleDraftEdit()` → `PUT /v2/api/drafts/<id>` on blur |
| P1-4 | No server-side contact filtering | `api.py`: added `is_already_enrolled()`, `_filter_personal_email()`, empty-email checks |
| P1-5 | Account status changes didn't cascade to signals | `account_service.py`: added `_cascade_signal_status()` — sequenced/revisit → signals to 'actioned'; noise → signals to 'archived' |
| P1-6 | Account revisit triggered prematurely | `account_service.py`: `check_all_sequences_complete()` now requires ALL non-DNC prospects to be `sequence_complete` (not just enrolled ones) |

### P2 Fixes (Important)
| ID | Bug | Fix |
|----|-----|-----|
| P2-1 | Duplicate draft rows on regenerate | `draft_service.py`: `generate_drafts()` deletes old generated/edited drafts before inserting new ones |
| P2-2 | Signal dedup too aggressive | `signal_service.py`: `check_duplicate_signal()` now includes `evidence_value` in dedup check |
| P2-3 | CoWork missing save_prospects tool | `mcp_tools.py`: added `save_prospects` tool with same filtering as web endpoint |

---

## 10. Repair Pass — 5-Issue Fix (Post-Audit)

Five structural issues were identified after the initial Codex audit. All fixed in a single commit:

### Fix 1: Apollo Enrollment Alignment
**Problem:** V2 enrollment used `apollo_person_id` (People Search API) and guessed `custom_field_values`. Apollo enrollment requires `apollo_contact_id` (Contacts API).
**Fix:** Complete rewrite of `enrollment_service.py` to follow the proven v1 pattern from `apollo_pipeline.py`:
- Search/create/update Contact via Contacts API (not People Search)
- Build `typed_custom_fields` from approved drafts using `_resolve_custom_field_ids()`
- Resolve `send_email_from_email_account_id` via `_resolve_email_account()`
- Enroll using `apollo_contact_id` (not `apollo_person_id`)
- Store `apollo_contact_id` on prospect record
- Added `apollo_contact_id TEXT` column to prospects table in `schema.py`
- Added `update_apollo_contact_id()` to `prospect_service.py`

### Fix 2: Queue/Status Model → Workflow Statuses
**Problem:** Queue tabs, filters, and counts used internal signal statuses (`new`/`actioned`/`archived`) instead of the intended workflow statuses.
**Fix:**
- `signal_service.py`: `list_signals()` now filters on `a.account_status` (not `s.status`)
- `signal_service.py`: `get_signal_counts_by_status()` now groups by `a.account_status`
- `api.py`: Signal list endpoint accepts `('new', 'sequenced', 'revisit', 'noise')`
- `app.html`: Queue tabs show New / Sequenced / Revisit / Noise / All
- `mcp_tools.py`: `list_signal_queue` docstring updated to workflow statuses

### Fix 3: Verified Email Enforcement
**Problem:** Both web and MCP prospect-save paths accepted unverified emails.
**Fix:**
- `api.py` `api_save_prospects()`: Skips prospects where `email_verified` is falsy
- `mcp_tools.py` `save_prospects()`: Same enforcement
- Both paths now report `skipped_unverified` count in response

### Fix 4: MCP Auto Campaign Mapping
**Problem:** `create_signal` and `create_revisit_signal` MCP tools created signals with no campaign recommendation.
**Fix:**
- Both tools now call `campaign_service.recommend_campaign()` before creating the signal
- `recommended_campaign_id` and `recommended_campaign_reasoning` are set on the signal
- Both return campaign info in their response

### Fix 5: Web App Workflow/State Fixes
**Problem:** Multiple UI state management issues.
**Fixes:**
- `handleApproveAll`: Now collects draft IDs from both session drafts AND `workspace.drafts` (existing server-loaded drafts)
- `handleRegenerate`: Now updates local draft state with the regenerated draft from the server response
- `handleEnrollAll`: Only sets `allDone=true` if zero failures; shows error toast with failure count otherwise
- Success message shows actual count: "All prospects enrolled (X/Y)"
- Subject inputs use `key` prop tied to `draft.updated_at` to force re-render after regeneration

---

## 11. Final Repair Pass — Merge Hardening

Seven issues resolved in the final pass before main. 19 regression tests added.

### Fix 1: Apollo Existing-Contact Update — POST → PUT
**Problem:** v2 used `POST /v1/contacts/{id}` to update existing Apollo contacts.
The proven pattern throughout `app.py` (3 instances) uses `PUT /v1/contacts/{id}`.
**Fix:** Changed `enrollment_service.py` to use `'put'` for existing contact updates.
Added explicit error handling for failed contact updates — returns error to caller
instead of silently proceeding. Comment documents that PUT /v1/ is correct per
Apollo API docs and the existing repo pattern.

### Fix 2: Status Model Cleanup
**Problem:** Two competing lifecycle systems: workflow status (account_status: new/sequenced/revisit/noise) and signal status (status: new/actioned/archived) were both exposed as user-facing concepts.
**Fix:**
- **Workflow status is the primary product lifecycle.** Queue tabs, counts, filters, and MCP listing all use account_status.
- **Signal status is internal bookkeeping only.** It cascades automatically when account status changes (sequenced → signals actioned, noise → signals archived).
- `PUT /v2/api/signals/<id>/status` is documented as internal-only and accepts only (new/actioned/archived). Normal UI flows do not call it.
- `markNoise` in the web app now only calls the account status endpoint — the signal cascade happens server-side automatically via `mark_account_noise()`.
- Signal rows returned from `list_signals()` now include an explicit `workflow_status` field (aliased from `account_status`).
- `SignalStatus` enum in `models.py` documented as internal.
- **Contract:** `GET /v2/api/signals?status=` accepts workflow statuses only. `GET /v2/api/signals/counts` groups by workflow status. Queue tabs show New/Sequenced/Revisit/Noise/All.

### Fix 3: Do-Not-Contact Enforcement on Save Paths
**Problem:** Backend save paths (web API + MCP) blocked enrolled, personal-email, no-email, and unverified contacts, but did NOT block contacts flagged as do-not-contact in existing prospect data.
**Fix:**
- Added `is_do_not_contact(email)` helper in `prospect_service.py` — queries across all prospect records.
- Both `api.py` `api_save_prospects()` and `mcp_tools.py` `save_prospects()` now call it before persisting.
- Skip summary includes `skipped_dnc` count.
- Check runs before the enrolled check (DNC is a harder block).

### Fix 4: Draft/Enrollment UI State Truthfulness
**Problem:** Draft editing and enrollment used inconsistent state sources. `handleEnrollAll` relied on async React state timing for success/failure determination.
**Fix:**
- `DraftReview` initializes local `drafts` state from `workspace.drafts` on mount — so server-loaded drafts are immediately editable without needing "Generate Drafts" first.
- `handleEnrollAll` uses an explicit `successCount`/`failCount` accumulator from actual request results, not from `setStatuses` callback timing.
- `handleEnroll` returns `'enrolled'` or `'failed'` directly to callers.
- Success/failure messaging is based on the accumulator, not React state.
- "Enroll All" button hidden once all prospects are already enrolled.
- `markNoise` removed the redundant signal-status API call (cascade handles it).

### Fix 5: Schema Ordering Bug
**Problem:** `schema.py` called `safe_add_column('prospects', 'apollo_contact_id')` before the prospects table was created, causing `OperationalError` on fresh databases (including test runs).
**Fix:** Added `apollo_contact_id` to the CREATE TABLE definition and moved the `safe_add_column` call to after the table creation (for backwards compat with existing DBs).

### Fix 6: Verified-Email Enforcement on Enroll Step
**Problem:** `enroll_prospect()` did not check `email_verified` before starting Apollo work. Bad legacy data, manually inserted data, or future regressions could push unverified emails into Apollo sequences.
**Fix:** Added an explicit `email_verified` check in `enrollment_service.py` immediately after loading the prospect. If the prospect's email is not verified, enrollment returns an error: "Prospect email is not verified. Only verified emails can be enrolled." This gate is enforced server-side in the final enroll path, independent of earlier save-path filtering.

### Fix 7: MCP Source Attribution
**Problem:** MCP `create_signal()` used `signal_source='manual_entry'` and `evidence_type='manual'`, making CoWork/MCP-created signals indistinguishable from manual web entries.
**Fix:** Changed `create_signal()` to use `evidence_type='cowork_push'` and `signal_source='cowork'`. `create_revisit_signal()` already used `signal_source='cowork'` correctly. Both now consistently attribute MCP-created signals to the CoWork channel.

### Regression Tests Added
`tests/test_v2_repair_pass.py` — 19 tests covering:
- Apollo contact update uses PUT (mocked Apollo calls, asserts no POST to update endpoint)
- `is_do_not_contact()` correctly identifies DNC emails
- API save path rejects DNC contacts
- `list_signals()` filters by workflow status (account_status)
- Signal counts group by workflow status
- Signal rows include `workflow_status` field
- API accepts workflow statuses on `/v2/api/signals?status=`
- API rejects old signal statuses (`actioned`) on queue endpoint
- Signal status endpoint accepts internal values only
- Unverified prospects cannot be enrolled (returns error with "not verified")
- Verified prospects pass the email verification gate
- MCP-created signals use `signal_source='cowork'` and `evidence_type='cowork_push'`
- MCP revisit signals also use `signal_source='cowork'`
- Flask smoke tests: `/app`, `/v2/api/campaigns`, `/v2/api/signals/counts`

---

## 12. What Still Feels Incomplete / Risky

### Incomplete
1. **Limited test coverage**: 19 regression tests in `test_v2_repair_pass.py` cover key safety gates (DNC, verified email, PUT vs POST, workflow status). End-to-end integration tests still missing.
2. **No data migration script**: Existing scan_signals are not auto-converted to intent_signals. The `POST /v2/api/ingest/from-scans` endpoint exists but requires manual triggering.
3. **No revisit automation**: The `mark_sequence_complete` → `revisit` → fresh signal flow is modeled but there's no automated trigger (e.g., webhook from Apollo when a sequence completes).
4. **No document/DOCX/PDF ingestion**: The `ingestion_service` supports CSV and manual only. The architecture supports future parsers but none are built.
5. **Writing preferences UI**: The API exists but the SPA may not have a dedicated settings panel for editing writing preferences.
6. **Account owner assignment**: The field exists but there's no auth-based auto-assignment. Users must manually set owners.

### Risky
1. **Draft generation quality**: The LLM prompt construction in `draft_service.py` is untested against the Replit AI proxy. The template fallback is basic.
2. **Apollo enrollment**: Rewritten to follow the proven v1 pattern from `apollo_pipeline.py`. Uses Contacts API (create/update) with `typed_custom_fields`, resolves sender email account, and enrolls with `apollo_contact_id`. Not yet live-tested against Apollo's API in production.
3. **Campaign recommendation accuracy**: The `campaign_service.recommend_campaign()` keyword matching depends on campaign names containing expected keywords (e.g., "implementation", "migration"). If existing campaigns have different naming, recommendations will fall back to the first active campaign.
4. **React SPA size**: `app.html` is 1333 lines of inline JSX. Babel standalone compilation on every page load has performance implications.

---

## 13. What Codex Should Inspect Most Carefully

1. **Cross-service contracts**: Verify that `api.py` route handlers call service functions with correct argument names and handle return values correctly. Key interfaces:
   - `api.py` → `signal_service.get_signal_workspace()` return shape
   - `api.py` → `apollo_pipeline.apollo_api_call()` response handling
   - `draft.py` → `draft_service.generate_drafts()` return shape
   - `enrollment.py` → `enrollment_service.enroll_prospect()` return shape

2. **SQL injection safety**: All v2 services use parameterized queries (`?` placeholders), but verify no f-string SQL construction leaked in.

3. **The enrollment flow**: `enrollment_service.enroll_prospect()` → Apollo API call → prospect status update → account status rollup. This is the most critical business logic path.

4. **Draft generation LLM prompt**: `draft_service.py` `_build_system_prompt()` and `_build_user_prompt()` — verify these produce coherent prompts.

5. **Campaign recommendation logic**: `campaign_service.py` `recommend_campaign()` — verify the keyword matching is reasonable and the reasoning text is accurate.

6. **React SPA data flow**: `templates/v2/app.html` — verify fetch URLs match the actual API routes, error states are handled, and the workflow state machine (signal → campaign → search → draft → enroll) is coherent.

7. **Schema FK integrity**: Verify CASCADE and SET NULL behaviors are correct for the deletion scenarios.

8. **MCP tool <-> service parity**: Verify that every MCP tool in `mcp_tools.py` correctly calls the corresponding service function and handles the response.

---

## 14. Assumptions Made Without User Confirmation

1. **Flask + Jinja + React CDN stack**: Kept the existing stack (React 18 via CDN, Babel standalone, Tailwind CDN) rather than introducing a build system. This matches existing patterns in the repo.

2. **PostgreSQL as primary**: v2 schema uses the same SQLite/PG dual-dialect approach from `database.py`. Assumed Replit deployment uses PostgreSQL.

3. **GPT-5-mini for drafts**: Used the Replit AI proxy (`AI_INTEGRATIONS_OPENAI_BASE_URL`) with model `gpt-5-mini` for email generation, matching the existing `email_engine.py` pattern. No temperature parameter.

4. **Account status is separate from tier**: `account_status` (new/sequenced/revisit/noise) is a new field alongside the existing `current_tier` (0-4). They serve different purposes: tier = scoring quality, status = pipeline state.

5. **No auth changes**: v2 routes rely on the same `enforce_authentication()` before_request hook from the legacy app. No new auth/permission model.

6. **Signal queue replaces account-first UX**: The `/app` route is the new primary interface. Legacy routes remain but are considered secondary.

7. **Campaign recommendation is keyword-based**: Chose deterministic keyword matching over LLM-based recommendation for speed and predictability. Falls back gracefully.

8. **One shared Apollo account**: All enrollment goes through one `APOLLO_API_KEY`. No per-user Apollo auth.

9. **Writing preferences are org-wide**: One set of preferences shared by all users. No per-user overrides.

10. **Multi-signal rule**: When one account has multiple signals, each signal is independent in the queue. Account status changes are driven by the union of all enrollment states across all signals for that account.
