# IMPLEMENTATION PLAN — MEGAPROMPT_SPEC_V1

> **Generated:** 2026-03-04 | **Spec:** MEGAPROMPT_SPEC_V1.md | **Status:** AWAITING APPROVAL

---

## EXECUTIVE SUMMARY

The MEGAPROMPT spec describes a system that is **~70% already built**. The codebase has GitHub scanning, Apollo integration, email generation, campaigns, contributors, enrollment pipelines, caching, and scheduling. The gaps are primarily:

1. **Email generation needs to produce full 4-email sequences** (currently generates 1 email with 3 variants)
2. **Campaign flow missing AE-specific features** (CSV file upload, AI persona suggestions, verified email filter, review modes)
3. **Repo prioritization needs spec's 3-tier structure** (currently simpler)
4. **Scan cadence intervals don't match spec** (spec wants much more aggressive scanning)
5. **Several edge cases and polish items** (alert banners, contributor staleness, batch error states)

---

## AMBIGUITIES & CONFLICTS

| # | Issue | Spec Says | Code Currently Has | Resolution Needed |
|---|-------|-----------|-------------------|-------------------|
| A1 | **Scan intervals** | Tier 0=3d, Tier 1=2d, Tier 2=1d, Tier 3=7d, Tier 4=Never | Tier 0=30d, Tier 1=7d, Tier 2=3d, Tier 3=14d, Tier 4=90d | **Ask Eric**: Spec intervals are 10x more aggressive — confirm these are intentional? At current account volume, what's the GitHub API budget impact? |
| A2 | **Repo scan caps** | Small(<30)=ALL, Mid(30-100)=40, Large(100+)=25 | MAX_REPOS_TO_SCAN=50, mega-corp(200+)=30 | Spec EC-004 says "never >50 repos" which aligns with small orgs. Mid(40) and Large(25) are new caps. |
| A3 | **CSV upload format** | "Required: company_name, website/domain" | Import API accepts JSON, not CSV files | Spec Open Question #2 acknowledges format needs definition. Will implement multipart CSV upload with company_name + website required, all other columns optional and saved to metadata. |
| A4 | **AI persona suggestion** | "System suggests target personas based on campaign assets" | No AI persona suggestion exists | Need to define: does the AI analyze campaign prompt text and suggest roles? Or use predefined mappings? Will implement AI-based suggestion using the GPT-5 mini endpoint. |
| A5 | **Review toggle** | "Review in-tool OR checkbox to defer review to Apollo" | No toggle exists | Interpretation: "defer to Apollo" = generate emails, push custom fields to Apollo, but skip the in-tool review step. Contacts go straight from "generated" to enrollment. |
| A6 | **5 Apollo sequences** | "5 reusable sequences built on custom fields" | Sequence mappings exist but specific sequences not documented | Spec Open Question #4. Current `sequence_mappings` table already supports N sequences. The 5 specific ones need to be synced from Apollo. No code change needed — just sync. |
| A7 | **4-email sequence** | "All 4 emails visible simultaneously" | Email engine generates 1 email (3 variants) | Major change: email_engine.py must generate Email 1 (hook), Email 2 (different angle), Email 3 (light touch), Email 4 (breakup). Each is a separate AI call. |
| A8 | **Contact cap column** | "Default 20, configurable per campaign" | `campaigns` table has no `contact_cap` column | Add via `_safe_add_column()` |
| A9 | **Contributor staleness** | "2+ years no activity = deprioritize" | No staleness filtering | Need to add last_activity_date tracking or infer from contribution data |

---

## FILES TO MODIFY

| File | Lines | Changes | Phase |
|------|-------|---------|-------|
| `config.py` | 1,709 | Update scan intervals, add 3-tier repo constants, i18n keyword boost list | 1 |
| `monitors/discovery.py` | ~400 | Rewrite repo prioritization to 3-tier (small/mid/large) with i18n keyword override | 1 |
| `monitors/scanner.py` | 2,891 | EC-001 (all private repos → Tier 0), contributor staleness filtering | 1, 5 |
| `database.py` | 6,776 | Add `contact_cap`, `verified_emails_only`, `review_in_tool` columns to campaigns; add `generated_sequence_json` to enrollment_contacts | 2, 3 |
| `app.py` | 9,340 | CSV file upload endpoint, domain validation, alert banner API, campaign persona suggestion endpoint, verified email param passthrough, contributor "Find Email" improvements | 2, 4, 5, 6 |
| `email_engine.py` | 655 | Full 4-email sequence generation, signal age hooks, CAN-SPAM footer, Apollo variable injection, link hyperlinking, word/subject enforcement | 3 |
| `email_routes.py` | 190 | Support full sequence preview (all 4 emails) | 3 |
| `apollo_pipeline.py` | 606 | Verified email filter, contact cap enforcement, EC-008 (no email status), email account per sequence mapping (existing TODO) | 4 |
| `sheets_sync.py` | ~200 | EC-024: skip archived accounts during Google Sheets import | 6 |
| `templates/campaigns.html` | — | CSV upload UI, AI persona suggestions, verified email checkbox, contact cap input, review toggle, individual vs bulk assignment | 2, 4 |
| `templates/contributors.html` | — | "Find Email" button styling, staleness badges, sequence email review panel | 5 |
| `templates/enrollment.html` | — | All-4-emails visible at once, individual vs bulk mode toggle | 4 |
| `templates/base_tabler.html` | — | Alert banner system (rate limit / cache), auto-dismiss via polling | 6 |
| `templates/accounts_tabler.html` | — | Contributors section in account detail view | 5 |
| `static/css/campaigns.css` | — | CSV upload area, persona suggestion cards, review toggle styles | 2, 4 |
| `static/css/enrollment.css` | — | 4-email grid layout, mode toggle styles | 4 |
| `static/css/contributors.css` | — | Staleness badge, Find Email button styles | 5 |
| `validators.py` | 295 | Add `validate_csv_file()`, `validate_campaign_contact_cap()` | 2 |

### Files to CREATE: None
Following the codebase convention — no new Python files or separate JS files. All changes go into existing files.

---

## DEPENDENCY GRAPH

```
Phase 1 (Repo Prioritization) ──────────────────────────────────────────→ Independent
Phase 2 (CSV Upload & Data Model) ──→ Phase 4 (Campaign UI) depends on this
Phase 3 (Email Sequence Engine) ───→ Phase 4 (Campaign UI) depends on this
                                   ───→ Phase 5 (Contributors) depends on this
Phase 5 (Contributors) ────────────────────────────────────────────────→ After Phase 3
Phase 4 (Campaign UI) ─────────────────────────────────────────────────→ After Phase 2 + 3
Phase 6 (Resilience & Edge Cases) ──────────────────────────────────────→ Independent
```

**Optimal execution order:** Phase 1 → Phase 2 → Phase 3 → Phase 5 → Phase 4 → Phase 6

Phase 1 and Phase 6 can be parallelized. Phase 2 and Phase 3 can be parallelized after Phase 1 is done. But serial execution is safer given the monolithic app.py.

---

## PHASE 1: REPO PRIORITIZATION & SCAN CADENCE

**Goal:** Align scanning behavior with spec §4.2 and §4.3.
**Risk:** Low — config changes + discovery.py rewrite. No new tables.
**Estimated scope:** ~150 lines changed across 3 files.

### Step 1.1: Update Scan Intervals (config.py)

Update `RESCAN_INTERVALS` dict to match spec (pending Eric's confirmation on A1):

```
RESCAN_INTERVALS = {
    0: 3,    # Tier 0 (Tracking) — every 3 days
    1: 2,    # Tier 1 (Thinking) — every 2 days
    2: 1,    # Tier 2 (Preparing) — every 1 day
    3: 7,    # Tier 3 (Launched) — every 7 days
    4: None, # Tier 4 (Not Found) — never
}
```

### Step 1.2: Add 3-Tier Repo Constants (config.py)

```python
# Repo prioritization by org size (§4.3)
REPO_TIER_SMALL_THRESHOLD = 30      # Under 30 repos: scan ALL
REPO_TIER_MID_THRESHOLD = 100       # 30-100 repos: scan top 40
REPO_TIER_MID_SCAN_CAP = 40
REPO_TIER_LARGE_SCAN_CAP = 25       # 100+ repos: scan top 25

# i18n keyword override — repos matching these go to top of queue
I18N_REPO_KEYWORDS = ['i18n', 'l10n', 'locale', 'translation', 'internationalization']
```

### Step 1.3: Rewrite Repo Prioritization (monitors/discovery.py)

Replace current `_prioritize_repos()` with 3-tier logic:
1. Separate repos into i18n-keyword-matches (always first) and others
2. If org < 30 repos: return all (i18n first, then by score)
3. If org 30-100 repos: return top 40 (i18n first, then by score, exclude forks and archived)
4. If org 100+ repos: return top 25 (i18n first, then by score, exclude forks, archived, and repos with no commits in 2 years)

Also add repo name/description keyword boost from spec §4.3: "app", "platform", "web", "mobile", "frontend", "backend", "core", "main", "api".

### Step 1.4: Handle EC-001 — All Private Repos (monitors/scanner.py)

In the scan logic, when GitHub API returns 0 public repos for an existing org:
- Set tier to 0 (Tracking) instead of 4 (Not Found)
- Add note: "All repositories appear to be private"
- Store in `evidence_summary`

### Milestone: Repo prioritization tests pass. Scan a known large org (e.g., microsoft) and verify only 25 repos are selected. Scan a known small org and verify all repos are scanned.

---

## PHASE 2: CSV UPLOAD & DATA MODEL EXTENSIONS

**Goal:** Enable AE CSV upload workflow per spec §2.2.
**Risk:** Medium — new upload endpoint, schema changes.
**Estimated scope:** ~300 lines changed across 5 files.

### Step 2.1: Add Campaign Columns (database.py)

Using `_safe_add_column()` in `init_db()`:

```python
_safe_add_column(cursor, 'campaigns', 'contact_cap', 'INTEGER DEFAULT 20')
_safe_add_column(cursor, 'campaigns', 'verified_emails_only', 'BOOLEAN DEFAULT FALSE')
_safe_add_column(cursor, 'campaigns', 'review_in_tool', 'BOOLEAN DEFAULT TRUE')
_safe_add_column(cursor, 'campaigns', 'links_json', 'TEXT')  # JSONB - hyperlinks for email copy
_safe_add_column(cursor, 'campaigns', 'tone', 'TEXT')  # formality guidance
```

### Step 2.2: Add CSV Upload Validator (validators.py)

```python
def validate_csv_upload(file):
    """Validate CSV file: must have company_name + website/domain columns."""
    # Check file extension (.csv)
    # Check file size (max 5MB)
    # Parse headers, verify required columns
    # Return (valid_rows, rejected_rows, errors)
```

### Step 2.3: CSV File Upload Endpoint (app.py)

New endpoint: `POST /api/campaigns/<id>/upload-accounts`
- Accepts multipart form data with CSV file
- Parses CSV using Python `csv` module
- Validates: company_name required, website/domain required
- Rejected rows (missing domain) → returned in response with clear message (EC-018)
- Valid rows → ALL columns saved to `monitored_accounts` (company_name, website in columns; everything else in `metadata` JSONB) per §3.4
- Returns: `{"saved": 45, "rejected": 3, "rejected_accounts": [{"company_name": "Foo", "reason": "Missing website/domain"}]}`

### Step 2.4: Domain Validation on Import (app.py)

Update existing `api_import()` to also enforce domain requirement when `source='campaign'`:
- If account has no website/domain → reject with clear message
- EC-024: Check `archived_at IS NOT NULL` — skip re-importing archived accounts

### Step 2.5: Campaign CSV Upload UI (templates/campaigns.html)

Add to campaign detail/edit view:
- Drag-and-drop CSV upload area (styled with existing Tabler patterns)
- Upload progress indicator
- Results display: "45 accounts saved, 3 rejected (missing domain)"
- Rejected accounts list with reason column
- Contact cap input (number field, default 20)
- Verified email checkbox
- Review toggle (checkbox: "Review emails in tool before enrollment")

### Milestone: Upload a CSV with mixed valid/invalid accounts. Verify valid accounts appear in the campaign, invalid ones are rejected with clear messaging.

---

## PHASE 3: EMAIL SEQUENCE ENGINE OVERHAUL

**Goal:** Generate full 4-email sequences per spec §6 and §2.1 Step 4.
**Risk:** High — this is the most complex change. Core to the value proposition.
**Estimated scope:** ~400 lines changed in email_engine.py, ~50 in email_routes.py.

### Step 3.1: Restructure Email Engine for Multi-Email Generation (email_engine.py)

Replace single-email generation with full sequence generation:

```python
def generate_email_sequence(
    contact: dict,
    account: dict,
    signals: list,
    campaign: dict = None,
    persona: str = None,
    num_emails: int = 4
) -> dict:
    """Generate a full email sequence (default 4 emails).

    Returns:
        {
            "emails": [
                {"position": 1, "subject": "...", "body": "...", "purpose": "hook + value prop"},
                {"position": 2, "subject": "...", "body": "...", "purpose": "different angle"},
                {"position": 3, "subject": "...", "body": "...", "purpose": "lighter touch"},
                {"position": 4, "subject": "...", "body": "...", "purpose": "breakup"}
            ],
            "persona": "vp_engineering",
            "signal_used": {...},
            "specificity_score": 85
        }
    """
```

Each email gets its own AI prompt with:
- **Email 1:** Hook referencing specific signal + value proposition
- **Email 2:** Different angle (social proof, case study reference, different pain point)
- **Email 3:** Lighter touch, add value (share a resource, insight, or industry data)
- **Email 4:** Breakup — final attempt, short, respectful

### Step 3.2: Signal Age Awareness (email_engine.py)

Update `_build_signal_hook()` to factor in signal age:
- Signal < 30 days: "I noticed your team recently..."
- Signal 30-90 days: "Earlier this quarter your team..."
- Signal 90-180 days: "Earlier this year your team..."
- Signal 180+ days: "Your team has been exploring..." (don't use "just noticed")

Use `age_in_days` from `scan_signals` table.

### Step 3.3: Cold Email Compliance (email_engine.py)

Add to every generated email:
- `{{unsubscribe}}` placeholder in footer (CAN-SPAM, spec rule 10)
- Apollo variables: `{{first_name}}`, `{{company}}`, `{{sender_first_name}}` (spec rule 11)
- Word count validation: reject/regenerate if over 150 words (spec rule 1)
- Subject line validation: reject/regenerate if over 50 chars (spec rule 4)

### Step 3.4: Campaign Link Hyperlinking (email_engine.py)

When campaign provides `links_json` (e.g., `[{"text": "Phrase Strings", "url": "https://phrase.com/strings"}]`):
- Include in AI prompt: "Naturally hyperlink these phrases in the email body: [list]"
- Post-process: verify links were included, if not → append as natural reference

### Step 3.5: Sequence Preview Endpoint (email_routes.py)

Update `/api/pipeline/email-preview` to return full sequence (all 4 emails) instead of single email. Also update `/api/contributors/generate-email` to support full sequence generation.

### Step 3.6: Enrollment Contact Schema Update (database.py)

The existing `generated_emails_json` column in `enrollment_contacts` already stores JSONB. Update the format from single email to array of 4 emails:

```json
[
    {"position": 1, "subject": "...", "body": "..."},
    {"position": 2, "subject": "...", "body": "..."},
    {"position": 3, "subject": "...", "body": "..."},
    {"position": 4, "subject": "...", "body": "..."}
]
```

No schema change needed — just data format change. Update all code that reads/writes this field.

### Milestone: Generate a full 4-email sequence for a test contact. Verify: each email <150 words, subjects <50 chars, signal age reflected in hook language, CAN-SPAM footer present, Apollo variables present.

---

## PHASE 4: CAMPAIGN FLOW UI

**Goal:** Build the AE workflow per spec §2.2 Steps 3-6.
**Risk:** Medium-High — significant UI work, but building on existing templates.
**Estimated scope:** ~500 lines across templates and app.py.
**Depends on:** Phase 2 (CSV upload) + Phase 3 (email sequences)

### Step 4.1: AI Persona Suggestion (app.py)

New endpoint: `POST /api/campaigns/<id>/suggest-personas`
- Reads campaign `prompt`, `assets`, and `tone`
- Calls GPT-5 mini with prompt: "Based on these campaign instructions and assets, suggest 3-5 target buyer personas (job titles, seniority levels) for cold email outreach in the localization/internationalization space."
- Returns structured suggestions: `[{"persona_name": "Head of Localization", "titles": [...], "seniorities": [...], "reasoning": "..."}]`
- AE can accept, modify, or reject each suggestion

### Step 4.2: Verified Email Filter (apollo_pipeline.py)

Update `auto_discover_contacts()`:
- Accept `verified_emails_only` parameter
- When True: add `email_status[]=verified` to Apollo People Search request
- This filters Apollo results to only contacts with verified email addresses

### Step 4.3: Contact Cap Enforcement (apollo_pipeline.py)

Update `auto_discover_contacts()`:
- Accept `contact_cap` parameter (default 20)
- After Apollo search, limit results per account to `contact_cap`
- Sort by relevance/seniority before truncating (EC-009)

### Step 4.4: Individual vs Bulk Assignment UI (templates/enrollment.html)

Add mode toggle to enrollment page:
- **Bulk mode (default):** Select sequence for all contacts at once → "Assign All" button
- **Individual mode:** Go contact-by-contact, select sequence per contact, review all 4 emails for that contact side-by-side

### Step 4.5: Review Toggle (templates/campaigns.html + app.py)

Add checkbox to campaign settings: "Review emails in tool before enrollment"
- When checked (default): contacts go through full review flow
- When unchecked: contacts skip review, go directly from "generated" to enrollment
- Stored in `campaigns.review_in_tool` column

### Step 4.6: All-4-Emails Visible at Once (templates/enrollment.html)

When reviewing a contact's emails:
- Show all 4 emails in a grid/accordion layout (not one at a time)
- Each email card shows: position label ("Email 1: Hook"), subject, body, word count
- Editable: AE can modify any email before enrollment
- "Approve & Enroll" button submits all 4 to Apollo via custom fields

### Step 4.7: Email Account Selection Fix (apollo_pipeline.py)

Fix existing TODO (line 389): Use `sequence_mappings.owner_email_account_id` when set, instead of global email account selection.

### Milestone: End-to-end campaign flow: create campaign → upload CSV → get persona suggestions → search contacts (with verified filter, cap at 20) → review all 4 emails → enroll. Full cycle under 30 minutes.

---

## PHASE 5: CONTRIBUTORS TAB ENHANCEMENT

**Goal:** Fully build out Contributors tab per spec §11.
**Risk:** Low-Medium — mostly UI improvements on existing infrastructure.
**Estimated scope:** ~200 lines across templates and app.py.
**Depends on:** Phase 3 (email sequence generation)

### Step 5.1: "Find Email" Button Enhancement (templates/contributors.html)

The "Find Email" button already exists (triggers `/api/contributors/<id>/apollo`). Enhance:
- Better loading state (spinner on the button itself)
- Immediate UI update when email found/not found (no page reload)
- Clear status messaging: "Email found: john@company.com" / "Email not available in Apollo" (EC-008)

### Step 5.2: Contributor Activity Staleness (monitors/scanner.py + templates/contributors.html)

Add staleness logic per spec §4.6:
- During contributor extraction, capture last contribution date
- Add `last_activity_at` column to contributors table via `_safe_add_column()`
- In contributors datatable: add visual badge for stale contributors (>2 years inactive)
- Deprioritize stale contributors in sort order (lower priority score)
- Do NOT hide them — just visually flag and sort lower

### Step 5.3: Account Detail View Contributors (templates/accounts_tabler.html)

Add a "Contributors" section to the account detail view:
- Show contributors for that specific account (filtered by `github_org`)
- Same columns as contributors tab (username, name, email status, title, contributions, enrollment status)
- "Find Email" button inline
- Link to full Contributors tab for more actions

### Step 5.4: Full Sequence Email Review on Contributors Tab

When generating/reviewing emails for a contributor:
- Use the new 4-email sequence generation from Phase 3
- Display all 4 emails in an expandable panel
- Allow editing before enrollment
- "Enroll" button pushes to Apollo

### Milestone: Open an account, see its contributors. Click "Find Email" on a contributor — status updates instantly. Generate full 4-email sequence, review all emails, enroll. Stale contributors are visually flagged.

---

## PHASE 6: SYSTEM RESILIENCE & EDGE CASES

**Goal:** Alert banners, error state handling, edge case fixes.
**Risk:** Low — isolated changes.
**Estimated scope:** ~200 lines across multiple files.

### Step 6.1: Alert Banner System (templates/base_tabler.html + app.py)

Add a global alert banner that:
- Polls `/api/system-alerts` every 30 seconds
- Shows banner ONLY when there's an active issue (EC-021, EC-022)
- Alert types: `github_rate_limit`, `cache_down`, `apollo_rate_limit`
- Auto-dismisses when the issue resolves
- Banner is non-intrusive (top of page, collapsible)

New endpoint: `GET /api/system-alerts`
- Checks GitHub token pool remaining (< threshold → alert)
- Checks Redis connectivity (ping fails → alert)
- Checks Apollo rate limiter state (exhausted → alert)
- Returns: `{"alerts": [{"type": "github_rate_limit", "message": "GitHub API rate limit low — scans may be delayed", "severity": "warning"}]}`

### Step 6.2: Batch "Completed with Errors" Status (apollo_pipeline.py + database.py)

Per EC-023: when a batch has some enrolled and some failed:
- Set batch status to `completed_with_errors` (not just `completed`)
- Add to allowed status values in enrollment_batches
- UI shows distinct badge for this state
- Failed contacts clearly listed with re-enroll buttons

### Step 6.3: Standardize Error Responses (app.py)

Per spec §10 Known TODO #3: Audit all API endpoints for error response format. Replace any `{"error": "..."}` with `{"status": "error", "message": "..."}` for consistency.

### Step 6.4: Google Sheets Skip Archived (sheets_sync.py)

Per EC-024: During Google Sheets sync, check if account is archived before importing:
```python
# Before inserting/updating account from Sheets:
existing = get_account_by_name(company_name)
if existing and existing.get('archived_at'):
    logging.info(f"[SHEETS_SYNC] Skipping archived account: {company_name}")
    continue
```

### Step 6.5: EC-008 — No Email Contacts (apollo_pipeline.py)

When Apollo returns a contact with zero email addresses:
- Set status to `email_not_available`
- Display clearly in UI: "Email not available"
- Do not attempt enrollment

### Milestone: Trigger a rate limit condition (or simulate). Verify alert banner appears. Resolve condition — banner auto-dismisses. Run a batch enrollment where some contacts fail — verify "completed with errors" state.

---

## TESTING STRATEGY

No formal test suite exists in the codebase. Testing is manual via the running Replit app.

For each phase:
1. **Smoke test** the changed routes via `curl` or browser
2. **Edge case test** using the specific EC-### scenarios from the spec
3. **Verify on Replit** — push to GitHub, pull on Replit, republish

Key manual test scenarios (from spec §12.1):
- Upload CSV with mixed valid/invalid accounts → verify rejection messaging
- Scan account → verify contributors auto-populate in both views
- Click "Find Email" → verify immediate UI update
- Generate 4-email sequence → verify all visible at once
- Enroll contact → verify "enrolled" status with sequence name and date
- Apollo rejects enrollment → verify "failed" status with reason + re-enroll button
- GitHub token exhaustion → verify alert banner appears and auto-dismisses

---

## IMPLEMENTATION ORDER (RECOMMENDED)

```
Week 1: Phase 1 — Repo Prioritization & Scan Cadence
         (config.py, monitors/discovery.py, monitors/scanner.py)

Week 2: Phase 3 — Email Sequence Engine
         (email_engine.py, email_routes.py — the hardest phase)

Week 3: Phase 2 — CSV Upload & Data Model
         (database.py, app.py, validators.py, templates/campaigns.html)

Week 4: Phase 5 — Contributors Tab
         (templates/contributors.html, templates/accounts_tabler.html, app.py)

Week 5: Phase 4 — Campaign Flow UI
         (templates/campaigns.html, templates/enrollment.html, app.py, apollo_pipeline.py)

Week 6: Phase 6 — Resilience & Edge Cases
         (templates/base_tabler.html, apollo_pipeline.py, sheets_sync.py, app.py)
```

**Rationale:** Phase 3 (email engine) is the most complex and has the most downstream dependencies. Doing it early unblocks Phases 4 and 5. Phase 1 is low-risk and builds confidence. Phase 6 is polish and can be last.

---

## DECISIONS — CONFIRMED BY ERIC (2026-03-04)

1. **Scan intervals (A1): USE SPEC INTERVALS.** ~50 Tier 2 accounts max. Tier 0=3d, Tier 1=2d, Tier 2=1d, Tier 3=7d, Tier 4=never.

2. **CSV / Data Columns (A3): REAL COLUMNS, NOT JSONB.** Add dedicated columns via `_safe_add_column()`: annual_revenue, industry, employee_count, hq_location, notes, funding_stage. All CSV data maps to real columns — queryable, sortable, filterable. No metadata JSONB for common fields.

3. **Email Sequence (A7): SINGLE AI CALL.** Generate all 4 emails in one prompt for cohesion. AE can edit any email inline — no cascade regeneration.

4. **Scope: ALL 6 PHASES.** One commit per phase. Order: Phase 1 → Phase 3 → Phase 2 → Phase 5 → Phase 4 → Phase 6.

---

## RISK REGISTER

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| Email engine rewrite breaks existing enrollment flows | High | Medium | Keep old `generate_personalized_email()` as fallback. New function is additive. |
| 4x AI calls per contact (one per email) increases cost/latency | Medium | High | Batch calls where possible. Add timeout/retry. Consider generating all 4 in single prompt. |
| Aggressive scan intervals exhaust GitHub tokens faster | Medium | Medium | Monitor token pool via `/api/token-pool`. Add Phase 6 alert banners early if needed. |
| app.py is 9,340 lines — merge conflicts likely | Medium | High | Work on feature branches. Keep changes surgical. |
| Replit deployment issues after large changes | Medium | Medium | Push incrementally. Test each phase on Replit before starting next. |
