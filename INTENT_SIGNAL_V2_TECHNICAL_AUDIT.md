# Technical Audit: Intent-Signal-First Prospecting Platform (v2)

**Branch:** `codex/intent-signal-v2`  
**Date:** March 12, 2026  
**Status:** Feature-Complete Prototype / Integration Phase  

---

## 1. Executive Summary

The `codex/intent-signal-v2` branch implements a fundamental architectural pivot for the GitHub Dossier platform. It transitions the system from a "repository-scan-first" tool to an **"intent-signal-first"** prospecting engine. 

The implementation is highly modular, coexisting with the legacy codebase without breaking existing functionality. It introduces a complete service-oriented backend (`v2/` package), a modern React-based Single Page Application (SPA), and a corresponding set of 15 MCP tools for Claude CoWork integration.

---

## 2. Architectural Overview

### 2.1 The v2 Package Structure
The new logic is strictly contained within the `v2/` directory:
- `v2/services/`: Domain-driven business logic (Signal, Account, Prospect, Draft, etc.).
- `v2/routes/`: Flask blueprints for REST API and Ingestion.
- `v2/models.py`: Pydantic models acting as the contract between layers.
- `v2/schema.py`: DDL and migrations for SQLite/PostgreSQL compatibility.
- `v2/mcp_tools.py`: 15 new tools mirroring the web UI functionality.

### 2.2 Domain Flow
The platform enforces a strict linear workflow:
1. **Signal Ingestion:** Raw data (CSV, Manual, Scans) -> `IntentSignal`.
2. **Workspace Loading:** Signal + Account + Recommended Campaign context.
3. **Prospecting:** Apollo Search -> `Prospect` (linked to Signal).
4. **Drafting:** LLM Generation (GPT-5-mini) -> `Draft` (multi-step sequence).
5. **Enrollment:** Approval -> Apollo Sequence Enrollment -> Account Status Update.

---

## 3. Database Schema & Data Integrity

### 3.1 New Tables
Six new tables were added with proper Foreign Key relationships:
- `intent_signals`: The root object.
- `prospects`: People found for a specific signal.
- `drafts`: Multi-step email sequences.
- `feedback_log`: Critiques for LLM regeneration.
- `activity_log`: Audit trail.
- `writing_preferences`: Global configuration.

### 3.2 Schema Extensions
Existing tables were extended with new columns:
- `monitored_accounts`: Added `account_owner`, `account_status`, `linkedin_url`, `company_size`.
- `campaigns`: Added `campaign_type`, `writing_guidelines`.

### 3.3 Observations on Integrity
- **FK Cascades:** `intent_signals` and `prospects` use `ON DELETE CASCADE` on `account_id`, ensuring no orphaned signals/prospects remain if an account is deleted.
- **Indexing:** Comprehensive indexes were added to `intent_signals` (status, created_at, signal_type) to support high-performance queue filtering.

---

## 4. Component Deep-Dive

### 4.1 Signal Service (`v2/services/signal_service.py`)
- **Queue Management:** Implements efficient status-based filtering (`new`, `actioned`, `archived`).
- **Workspace Logic:** The `get_signal_workspace` function is the "brain" of the UI, loading the full graph (Signal -> Account -> Campaign -> Prospects -> Drafts) in a single service call.

### 4.2 Draft Service (`v2/services/draft_service.py`)
- **LLM Prompting:** Uses a two-tier prompting strategy:
  - **System Prompt:** Injects global `writing_preferences`.
  - **User Prompt:** Injects specific Signal context and Campaign guidelines.
- **Regeneration:** Implements a critique-based regeneration flow (`regenerate_draft_step`) that appends user feedback to the LLM context.
- **Fallback:** Includes a robust `_STEP_TEMPLATES` dictionary for hard-coded fallback if the AI proxy is unavailable.

### 4.3 Enrollment Service (`v2/services/enrollment_service.py`)
- **Apollo Integration:** Calls the Apollo `add_contact_ids` endpoint.
- **Account Rollup:** Crucially, enrolling a prospect triggers an account status update to `sequenced`.
- **Completion Logic:** Marks prospects as `sequence_complete` and transitions the account to `revisit` once all sequences for that account are finished.

### 4.4 Ingestion Service (`v2/services/ingestion_service.py`)
- **Header Mapping:** Employs flexible header matching (e.g., `company_name` matches `company`, `name`, or `account_name`), making CSV imports resilient to different export formats.
- **Atomicity:** Row-level error handling prevents a single bad CSV record from failing the entire batch.

---

## 5. Frontend Analysis (`templates/v2/app.html`)

- **Tech Stack:** React 18 (CDN), Babel Standalone, Tailwind CSS, Lucide Icons.
- **State Machine:** The SPA handles the complex transition from Signal Queue -> Persona Search -> Draft Review -> Enrollment status perfectly.
- **Performance:** While the single-file 1300+ line template is large, it avoids a complex build system, matching the project's existing "deployment-ready" philosophy for Replit.

---

## 6. Risks & Validation Points (For Next LLM)

**Please validate the following items:**

1. **Apollo Payload Verification:** Confirm `enrollment_service.py` uses the exact JSON structure expected by the `emailer_campaigns/{id}/add_contact_ids` endpoint.
2. **LLM Temperature Parameter:** The `draft_service.py` explicitly omits the `temperature` parameter because the Replit AI proxy is known to error when it's present. Verify if this is still the case or if it should be added for better creativity.
3. **Cross-Service Contracts:**
   - Verify `v2/routes/api.py` correctly handles the `Optional` return type of `get_signal_workspace`.
   - Ensure `prospect_service.save_prospects` handles duplicates (Conflict on Email) gracefully.
4. **SQL Parameterization:** Double-check that all `v2/services/` queries use `?` placeholders (SQLite) or `%s` (PG) via the `db_connection()` proxy, and no f-string SQL injection points exist.
5. **Campaign Recommendation Logic:** `campaign_service.recommend_campaign` currently uses keyword matching on names. Validate if this should be upgraded to an LLM-based classifier for better accuracy.

---

## 7. Recommended Fixes / Enhancements

1. **Automated Tests:** Add `tests/test_v2_services.py` using a mock database to verify status transitions (e.g., `enrolled` -> `sequenced`).
2. **Signal Conversion Script:** Create a utility to convert existing `scan_signals` (legacy) into `intent_signals` (v2) to provide immediate value for existing users.
3. **Writing Preferences UI:** Add a settings panel in `app.html` to edit the `writing_preferences` table (currently only accessible via API).
4. **Apollo Webhook Receiver:** Implement a route to receive Apollo "Sequence Completed" webhooks to automate the `mark_sequence_complete` flow.

---
**Audit Performed by:** Gemini CLI (Interactive Assistant)  
**Reference ID:** GHD-V2-AUDIT-2026-03-12-001
