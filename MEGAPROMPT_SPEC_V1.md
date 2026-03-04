# MEGA PROMPT: GitHub Dossier (Repo Radar) — Implementation Spec for Claude Code

> **Author:** Eric Murray | **Date:** 2026-03-04 | **Status:** Draft
> **Build Target:** Hybrid (AI Agent + Human operator)
> **Project Path:** `/Users/ericmurray/GItHub-Dossier`
> **Hosting:** Replit | **Runtime:** Python 3.x / Flask | **Database:** PostgreSQL (primary), SQLite (fallback)

---

## 0. CONTEXT & PURPOSE

You are implementing improvements to **GitHub Dossier** (also called **Repo Radar**), a Flask-based sales intelligence platform built for Phrase (phrase.com). This tool exists because Phrase has **zero functional outbound strategy**. Account Executives don't prospect because it's too manual, there are no email templates or sequences, and everyone freelances — resulting in no action. BDRs are underworked and have no tooling.

**The mandate:** Go from **0 outbound emails to 10,000 touches per quarter** across the team.

**What this tool does:**
1. Scans GitHub organizations for internationalization (i18n) intent signals
2. Identifies active contributors who are likely involved in localization decisions
3. Discovers contacts at target companies via Apollo's API
4. Generates personalized cold email copy informed by GitHub signals and campaign context
5. Enrolls contacts into Apollo sequences with AI-generated copy injected via custom fields

**The genius of the architecture:** There are 5 reusable email sequences in Apollo built entirely on custom fields. The same 5 sequences work for ANY campaign (Repo Radar, Phrase Studio, Machine Translation, etc.) because fresh AI-generated copy is injected through custom fields every time. The tool generates the copy, pushes it to Apollo, and the sequence fires.

---

## 1. USER ROLES & WORKFLOWS

### 1.1 User Role: BDR (Primary for Repo Radar)

**What they do:**
- Operate the Repo Radar signal detection workflow day-to-day
- Review intent signals on scanned accounts, perform quick validation
- View contributors (auto-pulled during scan) in the Contributors tab
- Click "Find Email" on individual contributors (triggers Apollo API enrichment)
- Review AI-generated personalized emails that reference GitHub activity/signals
- Enroll contacts into Apollo sequences (individually or bulk)
- Eventually this becomes a single-operator role for Repo Radar

**Key UX requirement:** A BDR should be able to enroll 10 contacts with high-quality, personalized, signal-informed emails from the Repo Radar dashboard quickly — not a 30-minute ordeal.

### 1.2 User Role: AE (Primary for Campaign Types — Phrase Studio, MT, etc.)

**What they do:**
- Come into the tool to spin up a campaign (NOT Repo Radar — that's BDR territory)
- Upload a CSV of target accounts (**website/domain is required** — accounts without domains are rejected with clear messaging; company_name is also required)
- Define campaign assets: custom instructions, links for hyperlinking in email copy, tone/formality guidance
- System **suggests** target personas (e.g., Head of Localization, Video Manager, Content Writer) based on campaign assets and instructions — AE validates, approves, or modifies
- Search for contacts via Apollo API across all uploaded accounts
- **Verified email checkbox** — filter to only pull contacts with verified emails in Apollo
- **Contact cap per account** — configurable per campaign, default max 20 contacts per account
- Two assignment modes:
  - **Individual:** Go contact-by-contact, select a specific sequence for each, review all emails in the sequence at once (all 4 emails visible simultaneously)
  - **Bulk assign:** Select the same sequence for all contacts at once
- **Review toggle:** Either review email copy in-tool OR check a box to defer review to Apollo
- Push contacts + generated copy to Apollo for sequencing

**Key UX requirement:** The AI does all the heavy lifting — creative writing, finding contacts, enrolling them. The AE provides direction and approves. That's how frictionless this needs to be.

### 1.3 User Role: Eric (Admin/Builder — Current Phase)

- Building and testing the tool
- Full access to all features, configuration, and system settings

---

## 2. TWO CORE WORKFLOWS

### 2.1 Repo Radar Flow (BDR-Operated)

```
Step 1: Account is uploaded/synced to Repo Radar
  → Automatic scan begins (GitHub org scanned for i18n signals)
  → Contributors are auto-pulled during scan
  → Contributors appear in Contributors tab AND on the account detail view
  → Account is assigned a tier (0-4) based on signals detected

Step 2: BDR opens account in Repo Radar
  → Sees signal report (RFC discussions, dependency injections, ghost branches, enhanced heuristics)
  → Sees Contributors tab with all auto-pulled contributors
  → Does quick validation of the signal quality

Step 3: BDR clicks "Find Email" on individual contributors
  → Triggers Apollo API enrichment call
  → Email status updates (found / not found / not available)

Step 4: System generates personalized email copy
  → References actual GitHub activity, signals, and code repo insights
  → Matches persona based on contributor's title
  → Generates all emails in the sequence (e.g., all 4 emails)

Step 5: BDR reviews emails
  → All emails in the sequence visible at once (not one at a time)
  → BDR can edit if needed

Step 6: BDR enrolls contacts into Apollo sequence
  → Contacts + custom field copy pushed to Apollo
  → Status in tool updates to "enrolled"
  → If Apollo rejects (already in sequence, rules conflict), status = "failed" with ability to re-enroll
```

### 2.2 Campaign Flow (AE-Operated — Phrase Studio, MT, etc.)

```
Step 1: AE creates a new campaign
  → Provides campaign name
  → Writes custom instructions (tone, formality, key messages)
  → Provides links for hyperlinking specific phrases in email copy
  → Uploads any campaign assets

Step 2: AE uploads CSV of target accounts
  → Required columns: company_name, website/domain
  → All fields from CSV are saved to database (even if not displayed in UI)
  → Accounts without website/domain are REJECTED with clear pop-up message
  → Accounts with domains are saved and ready for contact search

Step 3: System suggests target personas
  → AI analyzes campaign assets and instructions
  → Suggests roles/functions/titles (e.g., Head of Localization, Video Manager, Content Writer)
  → AE validates, approves, or modifies suggestions

Step 4: Contact search via Apollo API
  → Searches all uploaded accounts × selected personas
  → Verified email checkbox available to filter results
  → Contact cap per account enforced (default: 20, configurable per campaign)
  → Contacts without email → status "email not available / not found"

Step 5: System generates email copy
  → AI generates personalized emails based on campaign assets, custom instructions, and links
  → Cold email best practices baked in (see §6)
  → Each contact gets all emails in the sequence generated

Step 6: AE reviews and assigns
  → Option A (Individual): AE goes contact-by-contact, selects sequence, reviews all 4 emails at once
  → Option B (Bulk): AE selects same sequence for all contacts
  → Review toggle: Review in-tool OR checkbox to defer review to Apollo

Step 7: Push to Apollo
  → Contacts + generated copy pushed via custom fields
  → Enrollment status tracked per contact (discovered → generated → enrolled / failed)
  → Failed contacts can be re-enrolled
```

---

## 3. DATA MODEL

### 3.1 Core Entities (Current Schema)

**monitored_accounts** — Company being tracked
- `id` (PK), `company_name` (UNIQUE), `github_org`, `website`, `annual_revenue`, `notes`
- `current_tier` (0-4), `previous_tier`, `latest_report_id`, `last_scanned_at`, `next_scan_due`
- `scan_status` (idle/running), `scan_progress`, `scan_start_time`
- `archived_at`, `metadata` (JSONB), `imported_at`, `import_source`
- `status_changed_at`, `evidence_summary`

**reports** — Scan result history
- `id` (PK), `company_name`, `github_org`, `scan_data` (JSONB), `ai_analysis` (JSONB)
- `signals_found`, `repos_scanned`, `commits_analyzed`, `prs_analyzed`
- `created_at`, `scan_duration_seconds`, `is_favorite`

**scan_signals** — Individual i18n signals detected
- `id` (PK), `report_id` (FK→reports), `company_name`, `signal_type` (TEXT)
- `description`, `file_path`, `timestamp`, `raw_strength` (REAL)
- `age_in_days`, `source_context`, `woe_value`, `freshness_score`

**contributors** — GitHub users found during scans
- `id` (PK), `github_login` (UNIQUE with github_org), `github_url`, `name`, `email`
- `blog`, `company`, `company_size`, `annual_revenue`, `repo_source`, `github_org`
- `contributions`, `insight`, `apollo_status` (not_sent/sent/enrolled), `emails_sent`
- `enrolled_in_sequence`, `sequence_name`, `enrolled_at`
- `is_org_member`, `github_profile_company`, `created_at`, `updated_at`

**campaigns** — Outbound campaign configuration
- `id` (PK), `name`, `prompt` (TEXT), `assets` (JSONB)
- `sequence_id`, `sequence_name`, `sequence_config` (JSONB)
- `status` (draft/active/paused), `created_at`, `updated_at`

**campaign_personas** — Target buyer segments per campaign
- `id` (PK), `campaign_id` (FK→campaigns), `persona_name`
- `titles_json` (JSONB), `seniorities_json` (JSONB)
- `sequence_id`, `sequence_name`, `priority`, `created_at`

**enrollment_batches** — Bulk enrollment runs
- `id` (PK), `campaign_id` (FK→campaigns)
- `status` (pending/in_progress/completed/failed)
- `total_accounts`, `total_contacts`, `discovered`, `generated`, `enrolled`, `failed`, `skipped`
- `current_phase` (idle/discovering/generating/enrolling)
- `error_message`, `account_ids_json` (JSONB)
- `created_at`, `started_at`, `completed_at`

**enrollment_contacts** — Per-contact audit trail
- `id` (PK), `batch_id` (FK→enrollment_batches), `account_id` (FK→monitored_accounts)
- `company_name`, `company_domain`, `persona_name`
- `sequence_id`, `sequence_name`
- `apollo_person_id`, `first_name`, `last_name`, `email`, `title`, `seniority`, `linkedin_url`
- `generated_emails_json` (JSONB), `apollo_contact_id`
- `status` (discovered/generated/enrolled/skipped/failed)
- `error_message`, `created_at`, `enrolled_at`

**sequence_mappings** — Apollo sequences mapped into the tool
- `id` (PK), `sequence_id` (UNIQUE), `sequence_name`, `sequence_config` (JSONB)
- `num_steps`, `active`, `enabled`
- `campaign_id` (FK→campaigns), `owner_name`, `owner_email_account_id`
- `created_at`, `updated_at`

**scorecard_scores** — Aggregated account scoring
- `id` (PK), `account_id` (UNIQUE, FK→monitored_accounts), `company_name`
- `annual_revenue`, `revenue_raw` (REAL), `locale_count`
- `total_score`, `lang_score`, `systems_score`, `revenue_score`
- `cohort` (A/B/C), `systems_json` (JSONB)
- `has_loc_titles`, `has_app_loc`
- `apollo_status` (not_enrolled/pending/enrolled), `sequence_name`, `enrolled_at`
- `scored_at`, `updated_at`

**webscraper_accounts** — Website localization analysis
- `id` (PK), `company_name`, `website_url`
- `current_tier` (1-4), `tier_label`
- `localization_coverage_score`, `quality_gap_score`, `enterprise_score`
- `locale_count`, `languages_detected`, `hreflang_tags`, `i18n_libraries`
- `last_scanned_at`, `scan_status`, `scan_error`
- `signals_json` (JSONB), `evidence_summary`
- `monitored_account_id` (FK→monitored_accounts)
- `prompt_history` (JSONB), `notes`, `archived_at`, `created_at`, `updated_at`

### 3.2 Cross-System ID Mapping

| Internal DB | GitHub | Apollo |
|---|---|---|
| `monitored_accounts.id` | `github_org` (text) | — |
| `contributors.id` | `github_login` + `github_org` | `apollo_person_id` (via Find Email enrichment) |
| `enrollment_contacts.id` | — | `apollo_person_id` → `apollo_contact_id` (after creation) |
| `sequence_mappings.sequence_id` | — | Apollo's `sequence_id` (text) |

**Key rule:** Apollo primarily searches by **company domain**, not company name. Accounts without a domain/website cannot have reliable contact search results. Domain is REQUIRED for any contact discovery flow.

### 3.3 Data Freshness & Caching

- **GitHub org metadata:** Redis cache, 24h TTL (how often does basic org info change? Rarely)
- **GitHub repo list:** Redis cache, 7d TTL (new repos appear infrequently)
- **GitHub branches:** Redis cache, 12h TTL (ghost branch detection — moderate change frequency)
- **GitHub issues/discussions:** Redis cache, 6h TTL (RFC signals — fastest-changing data)
- **Apollo contacts:** Real-time API calls, no caching (always fresh)
- **Signal data:** RFC lookback window is 180 days; repos inactive >730 days are skipped

**Caching explained:** When the system queries GitHub, it saves the response locally. Subsequent requests for the same data use the saved response until the TTL expires, at which point the next request fetches fresh data from GitHub. This prevents burning through the GitHub API rate limit (5,000 req/hr per token). Nothing is deleted — expired cache entries are simply refreshed on next access.

### 3.4 Data Ingestion Rule

**CRITICAL:** When accounts are uploaded or synced (CSV upload, Google Sheets sync, manual entry), **ALL fields must be saved to the database**, regardless of whether they are displayed in the UI. The principle is: **ingest everything, display selectively.** This ensures data is available for future features, AI context, filtering, segmentation, and export — even if the current UI only surfaces a subset.

---

## 4. BUSINESS LOGIC & DECISION RULES

### 4.1 Tier Classification

Accounts are automatically classified into tiers based on weighted signal scores:

| Signal Type | Weight |
|---|---|
| Dependency injection (smoking gun library) | 40 points |
| Job posting intent (hiring for localization) | 35 points |
| CI/CD localization (GitHub Actions with i18n) | 35 points |
| RFC/Discussion (high-intent) | 30 points |
| Ghost branch (active i18n branch) | 25 points |
| Regional domain detection | 25 points |
| Headless CMS i18n | 20 points |
| Payment multi-currency | 20 points |
| Locale velocity (commit frequency in locale folders) | 15 points |
| API internationalization | 10 points |

**Tier Assignment:**
- **Tier 0 (Tracking):** No signals detected
- **Tier 1 (Thinking):** RFC/discussion signals detected
- **Tier 2 (Preparing):** Dependency injection or ghost branch signals detected
- **Tier 3 (Launched):** Already using i18n extensively
- **Tier 4 (Not Found):** GitHub org not found or scan error

### 4.2 Scan Cadence (Automatic Rescans)

Scans are triggered automatically when accounts are uploaded. After initial scan, rescan cadence is based on tier:

| Tier | Rescan Interval |
|---|---|
| Tier 0 (Tracking) | Every 3 days |
| Tier 1 (Thinking) | Every 2 days |
| Tier 2 (Preparing) | Every 1 day |
| Tier 3 (Launched) | Every 7 days |
| Tier 4 (Not Found) | Never (archived) |

### 4.3 Repo Prioritization Strategy

**The problem:** A startup may have 5 repos; an enterprise may have 500+. Scanning all repos for large orgs burns API budget and wastes time on irrelevant repos.

**Tiered approach by org size:**

**Small orgs (under 30 repos): Scan ALL repos.**
- API cost is negligible. Every repo could reveal intent.

**Mid-size orgs (30–100 repos): Scan top 40, prioritized by:**
1. Last pushed date (most recently active first)
2. Stars + forks (community importance signal)
3. Repo size (larger repos tend to be core product)
4. Skip archived repos entirely

**Large/enterprise orgs (100+ repos): Scan top 25, with smart filtering:**
1. All mid-size criteria, plus:
2. **Name/description keyword boost:** Repos containing "app", "platform", "web", "mobile", "frontend", "backend", "core", "main", "api" in name or description jump to the top — these are product repos, not internal tooling
3. **Exclude forks:** Enterprises fork tons of open source. Those aren't their code.
4. **Exclude repos with no commits in 2 years** (existing `REPO_INACTIVITY_DAYS = 730`)

**i18n keyword override (ALL org sizes):** If ANY repo's name or description contains "i18n", "l10n", "locale", "translation", or "internationalization" — it goes **straight to the top of the scan queue** regardless of other scoring. That's the exact signal we're looking for.

**API budget math:**
- Each repo scan ≈ 3-8 API calls
- Small org (30 repos × 8) = ~240 calls
- Large org (25 repos × 8) = ~200 calls
- With 5,000/hr rate limit per token and multi-token rotation, multiple orgs can be scanned per hour

### 4.4 Sequence Selection Logic

1. **Direct persona mapping:** `campaign_personas.persona_name` → `sequence_id` (highest priority)
2. **Signal type match:** Match `sequence_mappings.sequence_name` keywords to signal type
3. **Fallback:** First enabled sequence in `sequence_mappings` where `enabled = 1`

### 4.5 Contact Filtering Rules

- **Skip personal email domains:** gmail.com, hotmail.com, yahoo.com, etc. (defined in `email_utils.py`)
- **Validate company domain match:** Contact's email domain should match the target company
- **Dedup against already-enrolled contacts:** Don't re-enroll contacts already in an active sequence
- **Verified email filter (optional):** When checkbox is checked, only return contacts with verified emails in Apollo
- **Contact cap per account:** Default 20, configurable per campaign

### 4.6 Contributor Activity Filtering

Contributors who haven't had any GitHub activity in **2 years** should not surface as active contributors. Use logic to assess recency of contributions before displaying them prominently. Stale contributors (no activity in 2+ years) should be deprioritized or flagged.

---

## 5. EDGE CASES & ERROR HANDLING

### Signal Detection Edge Cases

| ID | Scenario | Expected Behavior |
|---|---|---|
| EC-001 | Company has GitHub org but all repos are private | Scan returns no results. Do not assign Tier 4 (Not Found) — assign Tier 0 (Tracking) with note "All repos private" |
| EC-002 | Company has multiple GitHub orgs (acquisitions) | Currently scan single org. Flag for future: allow multiple orgs per account |
| EC-003 | False positive: repo mentions "i18n" but dependency is 3+ years old | Signal detection must weight recency. Old dependencies with no recent locale activity should score lower. Enforce via `freshness_score` and `age_in_days` |
| EC-004 | Org has thousands of repos, scan would timeout | Apply repo prioritization strategy (§4.3). Never scan more than 50 repos per org |

### Contact Discovery Edge Cases

| ID | Scenario | Expected Behavior |
|---|---|---|
| EC-005 | Apollo returns contacts who no longer work at company | Deprioritize contacts with stale data indicators. If contributor has no GitHub activity in 2+ years, flag accordingly |
| EC-006 | Same person appears as GitHub contributor AND Apollo contact search result | Apollo's enrollment rules handle dedup. If enrollment fails due to existing enrollment, status = "failed" with clear reason. Do not send duplicate emails |
| EC-007 | Contributor's GitHub email doesn't match Apollo work email | Always use Apollo's email for outreach, never GitHub profile emails. These may be different people or the same person with different emails — Apollo is source of truth for contact info |
| EC-008 | Apollo has the contact but with zero email addresses | Display status "Email not available / not found" on the contact. Do not attempt enrollment |
| EC-009 | 200+ contacts found at one company | Enforce contact cap per account (default: 20, configurable per campaign). Return top 20 by relevance/seniority |

### Email Generation Edge Cases

| ID | Scenario | Expected Behavior |
|---|---|---|
| EC-010 | Signal is 6+ months old | Factor signal age into email hook. Use age-aware language like "Earlier this year your team..." rather than "I just noticed..." The signal age should inform the approach, not disqualify the outreach |
| EC-011 | AI hallucinates a Phrase product feature | BDR/AE reviews emails before enrollment. Bake product accuracy rules into the AI prompt (see §6). Include only verified Phrase capabilities |
| EC-012 | Generated email is too long or sounds robotic | Cold email best practices enforced in prompt (see §6). Emails should be under 150 words, conversational, human-sounding |
| EC-013 | Two contacts at same company get nearly identical emails | Persona-based tone adjustment already varies emails. Additionally, vary the hook and angle per contact based on their title/role |

### Enrollment Edge Cases

| ID | Scenario | Expected Behavior |
|---|---|---|
| EC-014 | Contact already in active Apollo sequence from different campaign | Apollo's rules handle this. If enrollment fails, status = "failed" in tool with reason from Apollo. Contact should show enrollment status clearly |
| EC-015 | Apollo sending email account at daily limit | Apollo API returns error. Surface this in tool. This is an Apollo-side issue, not managed in this tool |
| EC-016 | Sequence paused or deleted in Apollo after enrollment from tool | Status in tool should reflect "enrolled" — downstream Apollo management is separate |
| EC-017 | Custom fields exceed Apollo character limits | Enforce cold email best practices (§6) — emails aligned with best practices should not hit character limits |

### Campaign Flow Edge Cases

| ID | Scenario | Expected Behavior |
|---|---|---|
| EC-018 | CSV upload has accounts without website/domain | Reject those accounts. Show clear pop-up/message: "X accounts were rejected — website/domain is required for contact search." Only save accounts with valid domains |
| EC-019 | Two AEs upload overlapping account lists | Should not happen per process (AEs manage their own territories). No technical prevention needed in Phase 1 |
| EC-020 | AE uploads accounts already in Repo Radar | Apollo's enrollment rules handle dedup at sequence level. Not managed in this tool |

### System Edge Cases

| ID | Scenario | Expected Behavior |
|---|---|---|
| EC-021 | GitHub token pool runs dry (all tokens rate-limited) | Show alert banner on Repo Radar dashboard — ONLY visible when there's a rate limit issue. Auto-dismiss when tokens recover. Do not show permanently |
| EC-022 | Redis cache goes down | Show alert banner on Repo Radar dashboard — ONLY visible when there's a cache issue. System should fall back to direct API calls (already in codebase) |
| EC-023 | Enrollment batch fails halfway (25 enrolled, 25 not) | Failed contacts get status = "failed" with ability to re-enroll. Successfully enrolled contacts remain "enrolled." Batch status = "completed with errors" |
| EC-024 | Google Sheets sync imports previously archived account | Archived accounts should NOT be re-imported. Previously deleted accounts CAN be re-imported |

---

## 6. COLD EMAIL RULES & BEST PRACTICES

**These rules apply to ALL campaigns — Repo Radar, Phrase Studio, MT, and any future campaign type.**

### Email Generation Rules (Bake Into AI Prompts)

1. **Length:** Under 150 words per email. Cold email should be scannable in 10 seconds
2. **Tone:** Conversational, peer-to-peer. Should sound like a human wrote it, not a marketing team
3. **Personalization:** Every email MUST reference something specific to the recipient or their company — a GitHub signal, a technology choice, a business context. Generic emails are unacceptable
4. **Subject lines:** Under 50 characters. No clickbait. No ALL CAPS. No emojis in subject lines
5. **CTA:** One clear call-to-action per email. Don't ask for multiple things
6. **No lies:** Never claim Phrase can do something it can't. Never fabricate case studies or statistics
7. **No competitor bashing:** Never mention competitors by name negatively
8. **Signal age awareness:** If a signal is older than 3 months, adjust language accordingly. Don't say "I just noticed" for something that happened 6 months ago
9. **Sequence flow:** Email 1 = hook + value prop. Email 2 = different angle or social proof. Email 3 = lighter touch, add value. Email 4 = breakup/final attempt. Each email should stand alone but build on the narrative
10. **CAN-SPAM compliance:** Every email must include `{{unsubscribe}}` placeholder and company address
11. **Apollo variables:** Use `{{first_name}}`, `{{company}}`, `{{sender_first_name}}` for dynamic personalization
12. **Links:** When campaign provides links, hyperlink relevant phrases naturally in the email body. Don't just dump URLs

### Persona-Based Tone Adjustment

| Persona | Angle | Tone |
|---|---|---|
| VP Engineering / CTO | Developer velocity, CI/CD integration, technical debt | Technical, peer-to-peer |
| Head of Product / PM | Time-to-market, release cadence, business impact | Business-focused, outcome-oriented |
| Director of Localization | TMS capabilities, workflow automation, manual handoff pain | Domain expert, empathetic to pain |
| Content / Marketing | Content operations, multi-market reach, brand consistency | Creative, strategic |
| Default | General value prop, efficiency gains | Friendly, consultative |

---

## 7. INTEGRATION SPECIFICATIONS

### 7.1 GitHub API

| Attribute | Value |
|---|---|
| **Purpose** | Scan GitHub orgs for i18n intent signals, extract contributors |
| **API Base URL** | https://api.github.com |
| **Auth Method** | Personal Access Token (PAT), multi-token pool rotation |
| **Rate Limits** | 5,000 requests/hour per token |
| **Token Rotation** | Auto-rotate when remaining < 50 (low threshold) or < 10 (critical threshold) |
| **Caching** | Redis-backed: org metadata (24h), repos (7d), branches (12h), issues (6h) |

**Key Operations:**
- Fetch org metadata and repo list
- Scan repo contents (package.json, requirements.txt, Gemfile, etc.) for i18n dependencies
- Search issues/discussions for RFC signals (180-day lookback)
- Detect ghost branches (branch names containing i18n keywords)
- Extract top contributors per repo

**Error Handling:**
- Rate limit hit → rotate to next token, if all tokens exhausted → queue scan and show alert
- 404 on org → assign Tier 4 (Not Found)
- Timeout → cancel scan, log, allow retry

### 7.2 Apollo API

| Attribute | Value |
|---|---|
| **Purpose** | Contact discovery, email enrichment, contact creation, sequence enrollment |
| **API Base URL** | https://api.apollo.io |
| **Auth Method** | X-Api-Key header |
| **Rate Limits** | 50 requests/minute |
| **Rate Limiting** | Token-bucket algorithm, max 120s wait timeout |

**Key Endpoints:**
| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/mixed_people/search` | POST | Find contacts by title, seniority, company domain |
| `/api/v1/emailer_campaigns/search` | GET | List available sequences |
| `/api/v1/emailer_campaigns/add_contact_ids` | POST | Enroll contacts in sequence |
| `/api/v1/email_accounts` | GET | List sending email accounts |

**Key Rules:**
- Apollo searches by **company domain**, not company name. Domain is required
- Personal email domains (gmail, hotmail, yahoo, etc.) are filtered out
- Contact email domain must match target company domain
- Enrollment failures (already in sequence, rule conflicts) → status = "failed" with Apollo's error reason
- Sequence rules, dedup, and analytics are managed in Apollo, not in this tool

### 7.3 OpenAI API (GPT-5 mini via Replit Proxy)

| Attribute | Value |
|---|---|
| **Purpose** | Sales intelligence generation, cold email copy generation |
| **Auth** | `AI_INTEGRATIONS_OPENAI_API_KEY` + `AI_INTEGRATIONS_OPENAI_BASE_URL` |
| **Fallback** | Hardcoded template email if API unavailable |

**Note:** AI provider may switch to Google Gemini in the future. Architecture should remain provider-agnostic where possible.

### 7.4 Google Sheets API

| Attribute | Value |
|---|---|
| **Purpose** | Daily account import sync via Coefficient connector |
| **Rate** | Max 300 rows/day |
| **Operations** | Read unprocessed rows, mark as imported, resolve GitHub orgs |

### 7.5 Slack API

| Attribute | Value |
|---|---|
| **Purpose** | Bot notifications for pipeline status, enrollment updates |
| **Auth** | `SLACK_BOT_TOKEN` + `SLACK_SIGNING_SECRET` |

### 7.6 Future Integrations (Phase 2 — Out of Scope)

- Salesforce CRM integration
- HubSpot CRM integration
- Apollo MCP for richer contact search within Claude Code

---

## 8. TECHNOLOGY STACK

| Layer | Technology |
|---|---|
| **Backend** | Python 3.x, Flask 3.0+ |
| **Database** | PostgreSQL (primary), SQLite (fallback) |
| **Cache** | Redis (optional, falls back to disk cache) |
| **Frontend** | Jinja2 templates, Tabler UI framework, DataTables, vanilla JS (Fetch API) |
| **AI** | OpenAI GPT-5 mini (via Replit proxy), Gemini as alternative |
| **Job Scheduling** | APScheduler, ThreadPoolExecutor (20 max workers) |
| **PDF Generation** | fpdf2 |
| **Hosting** | Replit |
| **External APIs** | GitHub API v3, Apollo API, Google Sheets API, Slack API |

---

## 9. NON-FUNCTIONAL REQUIREMENTS

| Category | Requirement |
|---|---|
| **Concurrent Users** | Support 5-10 simultaneous BDRs and AEs |
| **Availability** | 24/7 uptime required |
| **GitHub Scanning** | Background/async, continuous cadence based on tier (§4.2). Must not block UI |
| **Apollo Contact Search** | Real-time, immediate response expected. Must feel instant to the user |
| **Scan Progress** | Server-Sent Events (SSE) for real-time scan progress (already implemented) |
| **Alert Banners** | Rate limit / system alerts only visible when there's an active issue. Auto-dismiss when resolved |

---

## 10. TECHNICAL CONSTRAINTS & CONVENTIONS

### Codebase Conventions (From Existing Code)

**Route Organization:**
- Main routes in `app.py` (monolith — 370K file)
- Blueprints for: auth (`auth.py`), Slack (`slack_bot.py`), pipeline (`pipeline_routes.py`), email (`email_routes.py`)
- RESTful convention with `/api/` prefix for JSON endpoints
- HTML page routes at root level (`/`, `/campaigns`, `/accounts`, `/scorecard`)

**Database Patterns:**
- Raw SQL with parameterized queries (NOT ORM)
- SQLite uses `?` placeholders; PostgreSQL uses `%s` (auto-converted via `_CursorProxy`)
- Context manager pattern: `with db_connection() as conn:`
- Migrations via `_safe_add_column()` for backward compatibility
- Dict-like row access (SQLite `Row` factory, PostgreSQL `RealDictCursor`)

**Error Handling:**
- Try/except with logging: `logging.error(f"[CONTEXT] Error: {str(e)}")`
- Structured log labels: `[WEBHOOK]`, `[SCAN_LIMITER]`, `[CIRCUIT-BREAKER]`, etc.
- API error responses: `{"status": "error", "message": "description"}`
- API success responses: `{"status": "success", ...data}`

**Frontend Patterns:**
- Jinja2 templates extending `base_tabler.html`
- JavaScript is inline in template files (not separate .js files)
- All AJAX via standard `fetch()` API (no jQuery)
- Toast notifications with auto-dismiss and error deduplication
- DataTables with server-side pagination

**Background Jobs:**
- Thread-based via `ThreadPoolExecutor`
- No persistent job queue (jobs lost on server restart)
- Status tracking via database columns (`scan_status`, `scan_progress`)
- Stale job cleanup via `reset_stale_queued_accounts()`

### What NOT To Do (Anti-Patterns)

1. **Do NOT introduce an ORM.** The codebase uses raw SQL throughout. Adding SQLAlchemy or Peewee would create an inconsistent mess. Continue with parameterized raw SQL
2. **Do NOT add jQuery.** The frontend uses vanilla `fetch()`. Keep it that way
3. **Do NOT create new database tables without checking if an existing table can be extended.** Use `_safe_add_column()` for adding new columns to existing tables
4. **Do NOT call the Apollo API without going through the rate limiter.** All Apollo calls must use `apollo_api_call()` wrapper or `ApolloRateLimiter`
5. **Do NOT call the GitHub API without going through the token pool.** All GitHub calls must use the token rotation mechanism
6. **Do NOT store secrets in code.** All API keys and tokens come from environment variables
7. **Do NOT change the 5-sequence architecture.** The custom field injection approach is intentional. Sequences are managed in Apollo, not in this tool
8. **Do NOT manage sequence rules (dedup, daily limits, analytics) in this tool.** That's Apollo's responsibility
9. **Do NOT use async/await.** The codebase uses threading, not asyncio. Stay consistent
10. **Do NOT create separate .js files.** JavaScript lives inline in Jinja2 templates. Follow the existing pattern
11. **Do NOT skip input validation.** Use `validators.py` for all user inputs
12. **Do NOT log sensitive data** (API keys, tokens, full email content) in plain text

### Existing Code to Reference

| File | Purpose | Key Patterns |
|---|---|---|
| `app.py` | All routes, main application logic | Route definitions, SSE streaming, background scan orchestration |
| `database.py` | Schema, queries, connection management | `db_connection()` context manager, `_safe_add_column()`, parameterized SQL |
| `config.py` | Constants, library lists, thresholds | Signal weights, false positive patterns, repo scoring |
| `email_engine.py` | Cold email generation | Persona classification, signal-to-hook mapping, AI prompt construction, variant scoring |
| `apollo_pipeline.py` | Apollo integration | Contact discovery, enrollment, rate limiting |
| `monitors/scanner.py` | GitHub scanning | 3-signal detection, contributor extraction |
| `signal_verifier.py` | Signal confidence verification | Freshness scoring, WoE calculation |
| `validators.py` | Input validation | Validation functions for all user inputs |
| `circuit_breaker.py` | Circuit breaker pattern | Failure threshold tracking, auto-recovery |
| `cache.py` | Redis/disk caching | TTL management, cache key patterns |

### Known TODOs in Codebase

1. **`apollo_pipeline.py` line 389:** Email account selection is hardcoded globally instead of per-sequence-mapping. TODO says to use `sequence_mappings.owner_email_account_id` when set
2. **PDF cleanup:** Generated PDFs stored in `static/pdfs/` indefinitely with no automatic cleanup
3. **Error response inconsistency:** Some endpoints return `{"error": "..."}` instead of `{"status": "error", "message": "..."}`. Should be standardized

---

## 11. CONTRIBUTORS TAB — REQUIREMENTS

The Contributors tab is a **Phase 1** feature. It must be fully built out.

**Data displayed per contributor:**
- GitHub username + profile link
- Full name (from GitHub profile)
- Company (from GitHub profile)
- Email status (found via Apollo / not found / not available)
- Email address (when found)
- Title (from Apollo enrichment)
- Number of contributions
- Org membership status
- Enrollment status (not sent / sent / enrolled / failed)
- Sequence name (if enrolled)
- Enrolled date

**Behavior:**
- Contributors are **auto-pulled during the scan** — they appear automatically when a scan completes
- Contributors are visible in TWO places:
  1. When you click on an account in Repo Radar (account detail view)
  2. In the dedicated Contributors tab (all contributors across all accounts)
- "Find Email" button per contributor triggers Apollo API enrichment
- Table should support sorting, filtering, and search
- All contributor data is saved to the database even if not all fields are displayed

---

## 12. VALIDATION & SUCCESS CRITERIA

### 12.1 Functional Acceptance Criteria

- [ ] Given an account is uploaded to Repo Radar, when the scan completes, then contributors are auto-populated in both the account detail view and the Contributors tab
- [ ] Given a BDR clicks "Find Email" on a contributor, when Apollo returns a result, then the email status updates immediately in the UI
- [ ] Given a BDR selects contributors for enrollment, when they review emails, then ALL emails in the sequence are visible simultaneously (not one at a time)
- [ ] Given a contact is enrolled successfully, when the BDR checks the tool, then the status shows "enrolled" with the sequence name and date
- [ ] Given Apollo rejects an enrollment (already in sequence), when the tool receives the error, then the status shows "failed" with the reason and a re-enroll button
- [ ] Given an AE uploads a CSV, when accounts lack a website/domain, then those accounts are rejected with a clear message and only valid accounts are saved
- [ ] Given an AE creates a campaign with custom instructions, when the system suggests personas, then the suggestions are relevant to the campaign type
- [ ] Given a campaign has a contact cap of 20, when Apollo returns 50 contacts for one account, then only 20 are saved (top by relevance/seniority)
- [ ] Given the verified email checkbox is checked, when contacts are searched, then only contacts with verified emails in Apollo are returned
- [ ] Given GitHub token pool is exhausted, when a user views Repo Radar, then an alert banner appears (and auto-dismisses when tokens recover)
- [ ] Given all fields are provided in a CSV upload, when accounts are saved, then ALL fields persist in the database regardless of UI display

### 12.2 Success Metrics

| Metric | Target |
|---|---|
| Outbound touches per quarter | 10,000 across the team |
| Time for BDR to enroll 10 contacts from Repo Radar | Under 10 minutes |
| Email personalization quality | References specific GitHub signals/activity — not generic |
| Campaign spin-up time (AE) | Under 30 minutes from CSV upload to contacts enrolled |

---

## 13. OUT OF SCOPE (Phase 2 or N/A)

| Item | Reason | Planned For |
|---|---|---|
| Salesforce integration | Phase 2 | Phase 2 |
| HubSpot integration | Phase 2 | Phase 2 |
| Apollo MCP for contact search | Phase 2 | Phase 2 |
| Sequence rule management (dedup, daily limits) | Handled in Apollo | N/A |
| Sequence analytics and performance tracking | Handled in Apollo | N/A |
| Legal/compliance/GDPR | Not in scope for this spec | N/A |
| Mobile experience | Not needed | Not planned |
| Multi-org scanning (companies with multiple GitHub orgs) | Future enhancement | Phase 2 |
| Preventing AE territory overlap on campaigns | Process-managed, not tool-managed | N/A |

---

## 14. OPEN QUESTIONS

| # | Question | Status |
|---|---|---|
| 1 | OpenAI or Gemini as the long-term AI provider? | OpenAI for now, not finalized |
| 2 | What does the CSV upload format need to look like? Required: company_name, website/domain. What other optional columns? | Needs definition |
| 3 | What specific Phrase product capabilities should be whitelisted in the AI prompt to prevent hallucination? | Needs product marketing input |
| 4 | Should the 5 Apollo sequences be documented with their specific custom field mappings in this spec? | Needs documentation |

---

## 15. GLOSSARY

| Term | Definition |
|---|---|
| **Repo Radar** | The GitHub signal detection workflow within GitHub Dossier, operated by BDRs |
| **i18n** | Internationalization — the process of designing software to support multiple languages/locales |
| **l10n** | Localization — the process of adapting software for a specific language/market |
| **Signal** | Evidence of i18n intent detected in a company's GitHub activity (RFC discussions, dependency additions, WIP branches) |
| **Ghost Branch** | A GitHub branch with i18n-related naming (e.g., `feature/i18n`, `add-locale-support`) indicating active work |
| **RFC/Discussion** | GitHub issues or discussions where a company is talking about adding internationalization support |
| **Dependency Injection** | Detection of i18n libraries being added to a project's dependency file (package.json, Gemfile, etc.) |
| **Smoking Gun Library** | An i18n library (like react-i18next, vue-i18n) that strongly indicates pre-launch internationalization intent |
| **Tier** | Classification of an account's i18n intent level (0-4), from Tracking to Not Found |
| **Apollo** | Sales engagement platform used for contact data, email sequences, and outbound execution |
| **Sequence** | An automated email cadence in Apollo (typically 4 emails over several weeks) |
| **Custom Fields** | Apollo contact fields that can be programmatically populated. The 5 reusable sequences are built entirely on custom fields, allowing any campaign to inject fresh AI-generated copy |
| **TTL** | Time to Live — duration before a cached value expires and must be refreshed |
| **BDR** | Business Development Representative — runs Repo Radar, handles outbound prospecting |
| **AE** | Account Executive — runs campaigns (Phrase Studio, MT, etc.), manages deals |
| **Phrase TMS** | Phrase's Translation Management System — the core product |
| **Phrase Studio** | Phrase's video localization tool — one campaign type that can be run through this tool |
| **MCP** | Model Context Protocol — integration framework for AI agents to interact with external services |
| **SSE** | Server-Sent Events — real-time streaming from server to browser, used for scan progress |
| **WoE** | Weight of Evidence — statistical method used in signal confidence scoring |

---

## 16. REFERENCE FILES

| File | Purpose |
|---|---|
| `/Users/ericmurray/GItHub-Dossier/CLAUDE.md` | Project-specific conventions (referenced by Claude Code) |
| `/Users/ericmurray/CLAUDE.md` | Workspace-wide conventions across all projects |
| `/Users/ericmurray/GItHub-Dossier/MEGAPROMPT_UI_OVERHAUL.md` | Previous UI/UX design specs |
| `/Users/ericmurray/GItHub-Dossier/.env.example` | Environment variable template |
| `/Users/ericmurray/GItHub-Dossier/config.py` | All configurable constants, thresholds, library lists |
| `/Users/ericmurray/GItHub-Dossier/email_engine.py` | Email generation logic, persona classification, prompt templates |
| `/Users/ericmurray/GItHub-Dossier/apollo_pipeline.py` | Apollo integration, enrollment pipeline |
| `/Users/ericmurray/GItHub-Dossier/monitors/scanner.py` | GitHub scanning, signal detection |
| `/Users/ericmurray/GItHub-Dossier/database.py` | Database schema, queries, all CRUD operations |
