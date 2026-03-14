# Deep Dive Audit: Intent Signal v2 (Comprehensive)

**Branch:** `codex/intent-signal-v2`  
**Auditor:** Gemini CLI  
**Ref ID:** GHD-V2-DEEP-DIVE-2026-03-12-003  
**Scope:** Full codebase traversal, logic analysis, security review, and legacy comparison.

---

## 1. Authentication & Security Architecture

### 1.1 The "Public Shell" Vulnerability
- **Finding:** The SPA entry point (`GET /app` in `v2/routes/web.py`) is **publicly accessible** without authentication.
- **Evidence:** `app.py` (lines 237-239) explicitly allows `GET` requests to non-API routes.
- **Impact:** While the API endpoints (`/v2/api/*`) *are* protected, the application shell itself (including any hardcoded configuration or intellectual property in the JS bundle) is exposed to the open internet.
- **Recommendation:** Add `if not is_authenticated(): return redirect('/login')` to the `v2_app` route handler.

### 1.2 API Authentication
- **Status:** **PASS**. The `enforce_authentication` middleware in `app.py` correctly traps requests starting with `/v2/api/` and verifies the `X-API-Key`.

### 1.3 SQL Injection
- **Status:** **PASS**. All reviewed service layers use parameterized queries (`?` for SQLite/PG adapter). No evidence of f-string SQL injection found in `v2/services/`.

---

## 2. Data Integrity & Logic Mismatches

### 2.1 Account Deduplication (Fragility)
- **Finding:** `account_service.find_or_create_account` uses `LOWER(company_name) = LOWER(?)` for deduplication.
- **Risk:** This is **fragile**. "Acme Inc" and "Acme Inc." (with dot) will create duplicate accounts.
- **Recommendation:** Implement a domain-based lookup first (if website is provided), or a normalized company name check (stripping punctuation/suffixes).

### 2.2 Draft Parsing (Fragility)
- **Finding:** `draft_service._parse_llm_output` relies on splitting by `SUBJECT:` and `BODY:`. The fallback (first line = subject) is risky.
- **Risk:** If the LLM is "chatty" (e.g., "Sure, here is the draft:\nSubject: ..."), the parser will capture the chat preamble as the subject line.
- **Recommendation:** Enforce a stricter regex-based parser or use a structured output format (JSON mode) if the provider supports it (Replit AI proxy may not, so robust regex is preferred).

### 2.3 Frontend State Race Condition (High Severity)
- **Finding:** The "Approve All" workflow in `templates/v2/app.html` is susceptible to a race condition.
- **Scenario:**
    1. User edits a draft (local state updates).
    2. User immediately clicks "Approve All".
    3. `handleDraftEdit` (onBlur) fires an async `PUT` to save the edit.
    4. `handleApproveAll` (onClick) fires an async `POST` to approve *server-side* state.
- **Outcome:** If the `POST /approve-all` is processed before the `PUT` completes, the **stale (unedited) draft** is approved and queued for sending.
- **Recommendation:** `approve_all` endpoint should accept the current *client-side* draft content to ensure what you see is what you approve, OR the UI must block "Approve" until all pending saves resolve.

---

## 3. Workflow Gaps (Legacy vs. V2)

### 3.1 Missing Features from Legacy
- **Export to CSV:** The legacy "Accounts" tab allows exporting the grid. V2 has no export functionality for the Signal Queue.
- **Bulk Actions:** Legacy allows bulk-archiving accounts. V2 only allows processing signals one-by-one or filtering.
- **Scorecard Integration:** V2 workspaces do not show the "Account Scorecard" (technographics, hiring signals) that exists in the legacy view, meaning BDRs have *less* context in V2 than V1.

### 3.2 Apollo Integration (Critical)
- **Finding:** `enrollment_service.py` uses `POST` for `update_contact` (legacy uses `PUT`).
- **Finding:** `enroll_prospect` fails to check `email_verified`.
- **Finding:** `prospect_service` lacks global DNC filtering.
- **Status:** **FAIL**. These are blocking issues for production usage.

---

## 4. Operational Readiness

### 4.1 Rate Limiting
- **Status:** **PASS**. `enrollment_service.py` correctly uses `apollo_pipeline.apollo_api_call`, which wraps a thread-safe token-bucket limiter (50 req/min).

### 4.2 Error Handling
- **Status:** **MIXED**.
    - **API:** Wraps most logic in `try/except` blocks and returns 500s.
    - **Frontend:** Basic toast notifications exist, but there is no global error boundary. If the initial `GET /signals` fails, the app likely renders a blank or broken state without a clear "Retry" mechanism.

---

## 5. Final Recommendations (The "Fix It" List)

1.  **Secure the App Shell:** Protect `GET /app` with login logic.
2.  **Fix Race Condition:** Update `POST /approve-all` to accept a list of `{id, body, subject}` to overwrite and approve in one atomic transaction (or strictly enforce UI blocking).
3.  **Harden Parser:** Improve `_parse_llm_output` to handle chatty preambles.
4.  **Fix Apollo Verbs:** `POST` -> `PUT`.
5.  **Add Legacy Context:** Pull `scorecard` data into `get_signal_workspace` so BDRs don't lose the technographic context they rely on.

---
**Conclusion:**
The V2 architecture is sound, but the implementation details (frontend state, API verbs, deduplication) are brittle. It requires a "Surgical Fix" phase before it can replace the legacy workflow.
