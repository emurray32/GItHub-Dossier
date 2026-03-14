---
name: bdr-workflow
description: >
  Use when the user asks to "review leads", "pull outreach", "check the pipeline",
  "run the BDR workflow", "review prospects", "enroll contacts", "work signals",
  or needs guidance on the intent-signal-first workflow: reviewing signals, matching
  campaigns, editing cold emails, and enrolling prospects into Apollo sequences.
version: 2.0.0
---

# BDR Outreach Review Workflow (V2)

Guide BDRs through the intent-signal-first pipeline: signal queue, prospect discovery, draft review, and Apollo enrollment.

## V2 Domain Model

Everything starts from an **intent signal** — a reason to care about a company.

### Entities
- **Intent Signal** — the root object. Status: `new` -> `actioned` -> `archived`
- **Account** — extends monitored_accounts. Status: `new` -> `sequenced` -> `revisit` -> `noise`
- **Prospect** — people found via Apollo. Status: `found` -> `drafting` -> `enrolled` -> `sequence_complete`
- **Draft** — editable email per prospect per sequence step. Status: `generated` -> `edited` -> `approved` -> `enrolled`
- **Campaign** — outreach template with writing guidelines
- **Writing Preferences** — org-wide tone, banned phrases, structure rules

### Key Rules
- One account can have MULTIPLE signals (each independent in the queue)
- The queue is signal-first, not account-first
- Account status cascades to signal statuses automatically
- Marking an account as `noise` archives ALL its signals
- Marking as `sequenced` or `revisit` actions all `new` signals

## Workflow Steps

### 1. Pull the Signal Queue
Use `list_signal_queue` filtered by status. Default is "new" signals.

### 2. Load Signal Workspace
Use `get_signal_workspace` for the target signal. Returns everything needed: signal details, account info, recommended campaign, prospects, drafts, and writing preferences.

### 3. Search for Prospects
If no prospects exist yet, use `find_prospects` with the signal_id. This searches Apollo for people at the account's domain. Then `save_prospects` to persist them. Server-side filtering automatically rejects:
- Already enrolled contacts
- Personal emails (gmail, yahoo, etc.)
- Unverified emails
- Do-not-contact flagged contacts

### 4. Generate Email Drafts
Use `generate_draft_sequence` for each prospect. Creates 3 drafts: initial outreach, follow-up, breakup. Uses AI when available, falls back to templates.

### 5. Review Drafts
BDR reviews each draft. Options:
- Edit inline -> `save_edited_draft`
- Regenerate with critique -> `regenerate_draft_step`
- Approve -> `approve_draft` or `approve_all_drafts`

### 6. Enroll
- Single: `enroll_prospect`
- Batch: `bulk_enroll_prospects`
- Automatically injects approved draft content into Apollo sequence
- Account moves to `sequenced` status

### 7. Post-Enrollment
- `mark_sequence_complete` when Apollo sequence finishes
- If ALL prospects for an account complete, account auto-moves to `revisit`
- `create_revisit_signal` to start a new outreach cycle

## Campaign Matching

`recommend_campaign` auto-matches based on signal type:

| Signal Type | Campaign Keywords |
|------------|-------------------|
| `dependency_injection` | dependency, library, i18next, react-intl |
| `global_expansion` | expansion, international, localization |
| `learning_platform` | academy, training, lms, learning |
| `hiring_localization` | hiring, localization engineer |
| `rfc_discussion` | rfc, discussion, planning |
| `ghost_branch` | branch, wip, feature/i18n |
| `documentation_intent` | documentation, docs |
| `revisit` | revisit, follow-up |

BDR can always override via `update_signal_campaign`.

## Email Drafting Rules

See `references/cold-outreach-rules.md` for:
- Apollo dynamic variable conventions
- Signal-specific hook templates
- Sequence arc (3-email structure)
- Formatting rules (under 100 words, peer-to-peer tone)

## BDR Actions Summary

| Action | Tool | Effect |
|--------|------|--------|
| Approve draft | `approve_draft` | Draft ready for enrollment |
| Approve all | `approve_all_drafts` | All drafts for a prospect approved |
| Edit draft | `save_edited_draft` | Subject/body updated |
| Regenerate | `regenerate_draft_step` | AI rewrites with critique |
| Enroll | `enroll_prospect` | Prospect added to Apollo sequence |
| Bulk enroll | `bulk_enroll_prospects` | Multiple prospects enrolled |
| Mark noise | `mark_account_noise` | Account + all signals archived |
| Skip | (no action) | Move to next signal |
| Change campaign | `update_signal_campaign` | Different campaign assigned |
