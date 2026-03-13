# Lead Machine — UI/UX Dogfood Report
**Date:** March 13, 2026  
**Tested by:** Replit Agent (full preview + API + template inspection)  
**Scope:** `/app` (v2 signal-first UI) + `/accounts` (legacy RepoRadar)

---

## 1. Executive Summary

**Short answer: Almost — but not yet ready for high-volume BDR daily use.**

The new signal-first UI at `/app` is structurally sound and loads correctly as the default experience. The information architecture is right: queue on the left, workspace on the right, a clean linear flow from signal → campaign → contacts → drafts → enrollment. Keyboard navigation works for the queue. However, the product breaks down materially in the middle of the workflow: the queue hard-caps at 100 signals (9 are invisible right now), signal sort order is wrong (highest-scored signals are buried at the bottom), keyboard control stops entirely once you open a workspace, drafts are never pre-generated, the campaign recommendation always falls back to the generic "RepoRadar" campaign for CSV-imported signals, and there is no visible path to fix any of this without deep product knowledge. A BDR sitting down for the first time would move through the queue confidently, open a signal, then hit a wall.

---

## 2. Biggest Findings (Priority Order)

| # | Finding | Severity |
|---|---------|----------|
| 1 | Queue hard-caps at 100 — 9 signals are invisible right now | Critical |
| 2 | Queue sort order is wrong — best signals (Figma, Airbnb, Webflow) are buried at the bottom | Critical |
| 3 | Keyboard support stops at the queue — entire workspace requires mouse | High |
| 4 | Campaign recommendation always says "No campaign matched… Falling back to RepoRadar" for CSV signals | High |
| 5 | Drafts are never pre-generated — user must trigger generation per-contact manually | High |
| 6 | Two completely different navigation sidebars exist simultaneously (base.html vs base_tabler.html) | High |
| 7 | No way to sort, filter by score, or prioritize signals in the queue | Medium |
| 8 | Shortcut hints ("↑↓ Navigate • Enter Select • Esc Back") are 10px gray text — nearly invisible | Medium |
| 9 | Empty state in workspace gives no clear "what to do next" instruction | Medium |
| 10 | `/accounts` page (11,872 rows of RepoRadar accounts) is still fully accessible and confusingly labeled | Low-Medium |

---

## 3. Keyboard & Speed Review

### What Works
- **Arrow ↑↓ in queue:** Navigate up and down the signal list. Works correctly, selection highlights.
- **Enter to select:** Opens the workspace for the highlighted signal.
- **Escape to deselect:** Clears the selection and returns focus to queue.
- **Hint text:** "↑↓ Navigate • Enter Select • Esc Back" is present in the bottom bar.

### What Is Missing
- **No keyboard shortcuts inside the workspace at all.** Once you select a signal, every subsequent action — confirming the campaign, triggering contact search, reviewing drafts, regenerating an email, enrolling — requires a mouse click. For a "keyboard-first BDR workflow," this is the most important gap.
- **No shortcut to Skip (move to next signal).** The Skip button exists but is mouse-only.
- **No shortcut to Mark as Noise.** Same.
- **No shortcut to trigger draft regeneration or enrollment.**
- **No `?` key or shortcut reference panel.** Shortcuts are not discoverable.
- **No shortcut to jump back to the queue** from inside a workspace without pressing Escape.

### What Is Slowing Operators Down
1. After opening a workspace, the operator must scroll the right panel to find the Contact Search section, click "Search Contacts," wait for Apollo, click to save each contact, scroll further to find Drafts, click "Generate" per draft, then scroll to Enrollment.
2. The tab order through the right panel workspace has not been optimized for speed. Moving between sections requires mouse or lots of Tab presses.
3. No "next signal" advancement after enrollment without going back to click in the queue.

### Mouse Dependency Still Exists
- Everything in the right panel workspace (campaign confirm, contact search, save contacts, generate drafts, review/edit drafts, enroll)
- Switching between queue filter tabs (New / Sequenced / Revisit / Noise / All)
- "Skip" and "Mark as Noise" action buttons

---

## 4. First-Time User Confusion

### `/app` (Good)
- The root `/` correctly 302-redirects to `/app`. A new user lands on the Signal Queue immediately. This is correct.
- "Signal Queue" header, tabs (New / Sequenced / Revisit / Noise / All), queue list on left, empty right panel with "Select a signal from the queue to begin" — clear enough.
- The hint to use arrow keys is visible but very small.

### `/accounts` (Confusing if reached)
- The `/accounts` page (RepoRadar) is fully accessible and shows 11,872 rows of account data.
- It uses a **completely different sidebar** (`base_tabler.html`): Signal Queue, Contacts, Campaigns, BDR Review, Grow, Settings.
- The `/app` page uses the `base.html` sidebar: Signal Queue, Contributors, Campaigns, Settings.
- These two sidebars have different items and are visually distinct. If a user navigates to `/accounts`, they now see "BDR Review" and "Grow" items that don't exist in the `/app` sidebar.
- "BDR Review" in the `/accounts` sidebar links to the legacy `bdr_review.html` pipeline page — a completely separate old workflow with its own "Pipeline Review / Intent signals queue — select account to attack" header. This is dead legacy product that should not be reachable.
- **A non-technical operator who accidentally hits `/accounts` via a bookmark would think they're looking at the whole product.** The 11,872-row table is visually dominant.

---

## 5. Detailed Issues

### Issue 1 — Queue Hard-Cap at 100
- **Severity:** Critical
- **Where:** `/app` signal queue, `loadSignals()` function in `v2/app.html`
- **Repro:** Upload 109 signals. Queue shows "New (109)" in the tab badge, but only 100 are fetched and rendered. 9 signals are silently invisible.
- **Expected:** Either paginate or show a "Load more" control. The badge count should match what's visible.
- **Actual:** Queue fetches `limit=100` hardcoded. 9 signals cannot be reached without code changes.
- **Why it matters:** A BDR trusts the count badge. If it says 109 but only 100 are accessible, they'll never work those 9.
- **Fix:** Implement infinite scroll or a "Load more" button in the queue. Or raise the limit to 500 with virtual scrolling.

### Issue 2 — Wrong Sort Order (Best Signals at Bottom)
- **Severity:** Critical
- **Where:** `/app` signal queue
- **Repro:** Upload the Phrase Intent Tracker Excel. Signals with Score 5/5 (Figma, Airbnb, Webflow) appear at the bottom of the queue. Signals with Score 3/5 (Intezer) appear at the top.
- **Expected:** Highest-scored / highest-priority signals surface first.
- **Actual:** Queue sorts by database insertion order (ID ascending or descending based on batch). Since Figma/Airbnb were in the first rows of the Excel, they got the lowest IDs and appear last.
- **Why it matters:** The first thing a BDR sees is the least important signals. The whole point of a queue is prioritization.
- **Fix:** Sort by score DESC, then by created_at DESC. Add a sort control (Score, Company Name, Signal Type, Date Added) to the queue header.

### Issue 3 — No Keyboard Control Inside Workspace
- **Severity:** High
- **Where:** Right panel of `/app` after selecting a signal
- **Repro:** Select a signal with Enter. Try to confirm campaign, search contacts, or review drafts using only keyboard.
- **Expected:** Tab/Enter navigation through campaign confirm → contact search → draft review → enroll. Keyboard shortcuts for primary actions (e.g., `S` for Skip, `N` for Noise, `→` for next signal).
- **Actual:** Only Escape works (deselects). Every workspace action is mouse-only.
- **Fix:** Add keyboard shortcuts for at minimum: Skip (`S` or `]`), Mark Noise (`X`), Next Signal (`→`), and focus the first actionable element in the workspace when a signal is opened.

### Issue 4 — Campaign Recommendation Always Falls Back
- **Severity:** High
- **Where:** Campaign section of workspace for CSV-imported signals
- **Repro:** Open any signal from the Phrase Intent Tracker upload. Campaign section shows "No campaign matched signal type 'Hiring - Localization'. Falling back to 'RepoRadar' as the default active campaign."
- **Expected:** The system finds a real campaign match or at least doesn't surface the fallback reasoning as the primary text.
- **Actual:** Every signal imported via CSV shows the generic fallback reasoning. The campaign recommendation reasoning text is highly visible and says "No campaign matched" — erodes BDR confidence.
- **Why it matters:** If BDRs see "No campaign matched" on every signal, they lose trust in the automation.
- **Fix:** Either (a) create campaigns that match the CSV signal types ("Hiring - Localization", "Funding Round", "GitHub Archaeology", etc.), or (b) hide the fallback reasoning text and show something neutral when using the default campaign.

### Issue 5 — Drafts Never Pre-Generated
- **Severity:** High
- **Where:** Draft section of workspace
- **Repro:** Open any signal. Scroll to "Draft Emails." No drafts exist. Must find contacts first, then manually trigger draft generation for each.
- **Expected:** In a speed-optimized workflow, draft generation should start immediately or in the background when a contact is saved.
- **Actual:** `"drafts": []` for all signals. User must: search Apollo → save a contact → scroll to Draft section → click Generate → wait for AI.
- **Fix:** Auto-trigger draft generation when a prospect is saved. Show a loading state in the Draft section immediately.

### Issue 6 — Two Navigation Systems
- **Severity:** High
- **Where:** `/app` uses `base.html` sidebar; `/accounts` uses `base_tabler.html` sidebar
- **Repro:** Navigate to `/app`, note the sidebar (Signal Queue, Contributors, Campaigns, Settings). Navigate to `/accounts`, note the different sidebar (Signal Queue, Contacts, Campaigns, BDR Review, Grow, Settings).
- **Expected:** One consistent navigation system across the entire product.
- **Actual:** Two completely different sidebars. Items that exist in one don't exist in the other. "BDR Review" and "Grow" appear only in the legacy sidebar.
- **Fix:** Pick one navigation system. Either redirect `/accounts` to `/app` entirely, or unify both sidebars.

### Issue 7 — No Signal Sorting or Filtering in Queue
- **Severity:** Medium
- **Where:** `/app` signal queue header
- **Repro:** Look for a way to sort by score, signal type, company size, or industry. It doesn't exist.
- **Expected:** At minimum a "Sort by" control. Ideally filter by signal type or industry.
- **Actual:** Queue shows signals in insertion order only. No controls.
- **Fix:** Add a sort dropdown (Score, Date, Company Name, Signal Type) to the queue header.

### Issue 8 — Shortcut Hints Are Nearly Invisible
- **Severity:** Medium
- **Where:** Bottom bar of workspace, `text-[0.625rem] text-slate-300 hidden sm:inline`
- **Repro:** Look for keyboard shortcuts while using the app.
- **Expected:** Discoverable shortcut hints, either in the UI or via a `?` key that shows a modal.
- **Actual:** "↑↓ Navigate • Enter Select • Esc Back" in 10px gray — almost invisible. Hidden on small screens.
- **Fix:** Make hints larger and more visible, or add a `?` keyboard shortcut for a shortcuts reference modal.

### Issue 9 — Workspace Empty State Is Passive
- **Severity:** Medium
- **Where:** Right panel of `/app` before a signal is selected, and workspace sections with no data
- **Repro:** Open `/app` with no signal selected. See "Select a signal from the queue to begin."
- **Expected:** The empty state actively guides the user: "Press ↓ to select the first signal" or auto-selects the first signal.
- **Actual:** Passive empty state. The user must know to use arrow keys to get started.
- **Fix:** Auto-select the first signal on load, or make the empty state include an actionable shortcut button.

### Issue 10 — Legacy BDR Review Page Still Accessible
- **Severity:** Low-Medium
- **Where:** `/bdr_review` route; also reachable via "BDR Review" link in the `/accounts` sidebar
- **Repro:** Navigate to `/accounts`, click "BDR Review" in sidebar.
- **Expected:** Either removed or redirected to `/app`.
- **Actual:** Full legacy pipeline page still loads with "Pipeline Review / Intent signals queue — select account to attack" — confusing because it uses "intent signals" language but is a completely different (account-centric) workflow.
- **Fix:** Remove or redirect `/bdr_review` to `/app`.

---

## 6. Fastest Wins (Highest ROI Right Now)

1. **Fix the queue limit** — Change `limit=100` to `limit=500` or add a "Load more" button. 20-minute fix. Immediately unblocks 9 invisible signals and prevents silent data loss as the queue grows.

2. **Fix the sort order** — Sort by `score DESC, created_at DESC` by default. Figma (5/5) should be first, not Intezer (3/5). 30-minute fix. Dramatically improves first impression and daily workflow prioritization.

3. **Add Skip and Noise keyboard shortcuts** — `S` to skip, `X` to mark as noise, `→` for next signal. 1-hour fix. Immediately enables single-hand queue triage.

4. **Auto-select the first signal on load** — Don't make the user press ↓ to get started. 20-minute fix. Eliminates the passive empty state and immediately shows what the product does.

5. **Suppress fallback campaign reasoning** — When the system is using the default fallback campaign, show "Using default campaign — change if needed" instead of "No campaign matched signal type 'Hiring - Localization'." 15-minute fix. Removes the biggest trust-eroding text in the UI.

---

## 7. Longer-Term UX Improvements

1. **Unified navigation** — Consolidate to one sidebar system. Remove the `base_tabler.html` sidebar entirely or make `/accounts` use `base.html`. Kill "BDR Review" and "Grow" from the legacy sidebar.

2. **Background draft pre-generation** — When a signal is selected, start generating drafts in the background using the campaign personas. By the time the user has confirmed the campaign and searched for contacts, drafts should be ready.

3. **Sort + filter controls in queue** — Score, signal type, industry, company size dropdowns in the queue header. Let BDRs build their own working priority.

4. **Keyboard-first workspace** — Full tab order through workspace sections. `Enter` on campaign section confirms it and focuses contact search. `Enter` on contact search triggers Apollo search. Keyboard-accessible contact selection. Draft review keyboard shortcuts (edit, regenerate, next draft).

5. **Queue-level progress visualization** — Show a mini progress bar or completion indicator per signal in the queue item (e.g., a green dot for enrolled, gray for untouched, yellow for in-progress). Right now all 109 signals look identical — no visual difference between "touched" and "untouched."

6. **Enrollment confirmation feedback** — After enrolling, the UI should visually advance to the next signal automatically (with a brief "Enrolled ✓" flash) rather than waiting for the user to navigate away.

7. **Batch signal import UI** — A drag-and-drop CSV upload button in the Signal Queue empty state. Currently there's no UI path to import signals — it requires direct API calls or asking for help.

8. **Score surfacing** — The score (1-5) from the Phrase Intent Tracker is stored in notes but not displayed in the queue or workspace. Surface it as a priority badge on each signal card.

---

## Appendix: Technical Observations

- **Route:** `GET /` → 302 → `/app` ✅
- **Queue API:** `GET /v2/api/signals?status=new&limit=100` returns 100 of 109 signals
- **Workspace API:** `GET /v2/api/signals/<id>` returns full workspace (signal, account, campaign, personas, drafts, prospects)
- **Workspace API (wrong path):** `GET /v2/api/signals/<id>/workspace` returns 404 — not a registered route. Frontend correctly uses the right path.
- **Keyboard handlers:** ArrowUp/Down/Enter/Escape only. No other shortcuts registered.
- **Draft generation:** 0 drafts exist for any signal. All must be manually triggered.
- **Campaign match rate:** 0% for CSV-imported signals (all fall back to "RepoRadar")
- **CDN warnings:** `cdn.tailwindcss.com` and Babel standalone are production CDN anti-patterns. No functional impact but will need to be addressed before this app is served at scale.
