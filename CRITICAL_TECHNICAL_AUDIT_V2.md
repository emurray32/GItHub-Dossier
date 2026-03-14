# Critical Technical Audit: Intent Signal v2 (Handoff-Ready)

**Branch:** `codex/intent-signal-v2`  
**Auditor:** Gemini CLI (Interactive Assistant)  
**Ref ID:** GHD-V2-CRITICAL-AUDIT-2026-03-12-002

---

## 1. Executive Summary: "Architecture vs. Reality"

The `codex/intent-signal-v2` branch delivers a visually polished SPA and a logical service layer, but it contains **critical workflow failures** and **logic mismatches** that will cause immediate failures in production (especially regarding Apollo integration and status management).

The system is currently a **"Polished Prototype"** rather than "Feature-Complete."

---

## 2. High-Priority Workflow Failures

### 2.1 Status Model Mismatch (The "Signal vs Account" Problem)
- **The Issue:** `v2/routes/api.py` (line 63) validates status against `('new', 'sequenced', 'revisit', 'noise')`, but the underlying service `list_signals` filters these against `monitored_accounts.account_status` instead of the `intent_signals.status`.
- **Impact:** In a multi-signal scenario (e.g., one account has both a `ghost_branch` and an `rfc_discussion`), actioning one signal will incorrectly hide or misrepresent the other, as the UI is filtering by the *account's* state rather than the *signal's* state.
- **Mismatch:** `intent_signals` table uses `('new', 'actioned', 'archived')` while the API expects the account-level pipeline status.

### 2.2 Broken Apollo Integration (Payload & Verb Mismatch)
- **The Issue:** `v2/services/enrollment_service.py` (line 144) attempts to update existing contacts using `POST /v1/contacts/{id}`. 
- **Correction:** The proven v1 flow in `app.py` (line 10138) correctly identifies that Apollo requires **`PUT`** for this endpoint.
- **Risk:** Contact updates (injecting the email drafts into custom fields) will likely fail with a 405 or 404, resulting in "empty" emails being sent from Apollo.

### 2.3 Safety Gap: Verified Email Enforcement
- **The Issue:** `enroll_prospect` does **not** check the `email_verified` flag before proceeding to enrollment.
- **Impact:** The system will attempt to enroll unverified or "risky" emails, potentially damaging the domain's sender reputation. The legacy pipeline has strict checks for this; v2 has regressed.

### 2.4 Workflow Gap: DNC Enforcement in Search
- **The Issue:** While `enroll_prospect` checks `do_not_contact`, the `prospect_service.py` and the Apollo search route do not proactively filter out contacts that are already in a global DNC state.
- **Impact:** Users will waste time drafting emails for prospects who should never have been pulled into the workspace.

---

## 3. Component Analysis

### 3.1 MCP Tools: Source Attribution Failure
- **The Issue:** `v2/mcp_tools.py` `create_signal` (line 102) hardcodes `signal_source='manual_entry'`.
- **Correction:** It should be `signal_source='cowork'` to distinguish between human-entered signals and AI-discovered signals via the MCP interface.

### 3.2 SPA State Issues (UI Truthfulness)
- **The Issue:** `templates/v2/app.html` has inconsistent state handling for the "Approve/Enroll" flow. 
- **Evidence:** Lines 945-983 show a brittle local state update for draft editing that may not sync correctly with the bulk-approval logic, leading to "Approved" signals being sent to the API with stale or empty body content.

### 3.3 Campaign Mapping
- **The Issue:** The "Auto-recommend" logic in `campaign_service.py` is purely keyword-based on the campaign name.
- **Risk:** If a user renames a campaign or uses a non-standard naming convention, the recommendation engine breaks completely, defaulting to the "first available" campaign which may be irrelevant.

---

## 4. Required Fixes (Priority Order)

1.  **Sync Status Models:** Decide if the Queue is Signal-First or Account-First. If Signal-First, the API must filter on `intent_signals.status`.
2.  **Fix Apollo Verbs:** Change Contact Update from `POST` to `PUT` in `enrollment_service.py`.
3.  **Add Safety Gates:** 
    *   Add `if not prospect.get('email_verified'): return error` in `enroll_prospect`.
    *   Implement global DNC filtering during Apollo Search.
4.  **Fix MCP Attribution:** Update `create_signal` to use `cowork` as the source.
5.  **Robust SPA Sync:** Refactor the React `handleDraftEdit` to use a more reliable state-to-API synchronization (e.g., debounced auto-save) rather than relying on `onBlur` + bulk approval.

---
**Audit Performed by:** Gemini CLI  
**Status:** CRITICAL - NOT READY FOR PRODUCTION
