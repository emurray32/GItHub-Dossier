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

Also note the total queue size and the signal's position in the queue — these get injected as `QUEUE_POSITION` and `QUEUE_TOTAL`.

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

Create a React artifact using the **keyboard-first** template from `skills/bdr-workflow/templates/bdr-review-ui.jsx`.

The artifact MUST include:

1. **Signal header** (compact) — company name, badges, evidence, queue position counter
2. **Campaign banner** (single row) — dropdown + inline writing guidelines
3. **All prospect cards visible** — NO carousel, vertical stack, each with:
   - Checkbox include/exclude toggle
   - Name, title, email, verified badge, LinkedIn — one line
   - 3 draft step tabs with editable subject + body
   - Focus ring on keyboard-active prospect
4. **Shortcut hints** — `j/k` navigate, `s` toggle, `?` help — shown above prospects
5. **Sticky action bar** — "Enroll N [Enter]" left, queue position center, "Noise [n]" right
6. **Keyboard shortcut system** — `j/k` navigate, `1/2/3` steps, `s` toggle, `Enter` enroll, `n` noise, `?` help, `Esc` cancel
7. **Inline confirmation modals** — auto-focused confirm button, `Enter` to proceed, `Esc` to cancel
8. **Post-action screens** — success/noise card with short chat instruction

Bake actual data from the workspace into the artifact as JavaScript constants. The artifact is self-contained — no API calls from the artifact itself.

Style with Tailwind utility classes. Palette: slate base, emerald for enroll, amber for signals, red for noise. Compact spacing.

## Step 6: Present to BDR

Show the artifact. Keep the intro brief — one line max:
> "2 prospects for Gong — Global Expansion campaign. Review and hit Enter to enroll."

Do NOT over-explain the UI. The shortcuts are visible in the artifact.

## Step 7: Handle BDR Actions

**When the BDR says "enroll" (after confirming in artifact):**
1. For each included prospect, call `approve_all_drafts` then `enroll_prospect`
2. Report results per prospect in one line (success/failure)
3. Call `mark_account_sequenced` for the account
4. Immediately offer: "Next signal? (j to continue)"

**When the BDR says "noise" (after confirming in artifact):**
1. Call `mark_account_noise` with the account_id
2. Confirm in one line: "Gong marked as noise. All signals archived."
3. Immediately offer: "Next signal?"

**When the BDR edits a draft in chat:**
1. Call `save_edited_draft` with the updated subject/body
2. Confirm: "Draft updated."

**When the BDR rejects a draft:**
1. Call `regenerate_draft_step` with their critique
2. Show the regenerated version inline

**Speed principle:** After every action, immediately offer the next signal. Minimize back-and-forth. The BDR should be able to type "enroll" → see result → type "next" → see next artifact in a continuous flow.
