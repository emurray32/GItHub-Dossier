---
description: Pull signals for a company and launch the BDR review workflow
argument-hint: [company-name]
---

Run the BDR outreach review workflow for **$ARGUMENTS**.

## Step 1: Find Signals

Call `list_signal_queue` with status "new" to get the queue. Find signals matching "$ARGUMENTS" by company name.

If no signals match, tell the user and suggest they create one with `create_signal`.

## Step 2: Load Workspace

For the first matching signal, call `get_signal_workspace` with the signal_id. This returns:
- Signal details (type, description, evidence)
- Account info (company, website, industry, status)
- Recommended campaign (with reasoning)
- Existing prospects and their drafts
- Writing preferences

## Step 3: Search for Prospects (if none exist)

If the workspace has no prospects yet:
1. Call `find_prospects` with the signal_id and appropriate titles/seniorities
2. Present the results to the BDR
3. After BDR confirms, call `save_prospects` to persist them

## Step 4: Generate Drafts (if none exist)

For each prospect without drafts:
1. Call `generate_draft_sequence` with prospect_id, signal_id, and campaign_id
2. This creates a 3-email sequence (initial outreach, follow-up, breakup)

## Step 5: Build the Review Artifact

Create a React artifact that displays:

1. **Signal header** — company name, signal type badge, evidence summary, account status
2. **Campaign banner** — recommended campaign name and reasoning, with option to change
3. **Prospect cards** — one per prospect with:
   - Name, title, email, LinkedIn link
   - Enrollment status badge
   - 3 draft tabs (Step 1 / Step 2 / Step 3) with editable subject and body
   - Draft status badges (generated/edited/approved)
   - Approve All / Skip toggle per prospect
4. **Action bar** (sticky bottom):
   - "Enroll Selected (N)" primary button
   - "Mark as Noise" destructive button
   - Signal count badge

Use the data shape from `skills/bdr-workflow/references/artifact-spec.md` and the JSX template from `skills/bdr-workflow/templates/bdr-review-ui.jsx` as a reference.

Bake actual data from the workspace into the artifact as JavaScript constants. The artifact is self-contained — no API calls from the artifact itself.

Style with Tailwind utility classes. Palette: slate/gray base, emerald for approvals, amber/orange for signals, red for noise.

## Step 6: Present to BDR

Show the artifact and explain:
- Which campaign was auto-selected and why
- How many prospects are ready for review
- That they can edit emails inline, toggle approvals, and change the campaign

## Step 7: Handle BDR Actions

**When the BDR confirms enrollment:**
1. For each approved prospect, call `approve_all_drafts` then `enroll_prospect`
2. Report results per prospect (success/failure)
3. Call `mark_account_sequenced` for the account

**When the BDR edits a draft in chat:**
1. Call `save_edited_draft` with the updated subject/body
2. Confirm the edit was saved

**When the BDR rejects a draft:**
1. Call `regenerate_draft_step` with their critique
2. Show the regenerated version

**When the BDR marks as noise:**
1. Call `mark_account_noise` with the account_id
2. Confirm all signals for that account were archived
