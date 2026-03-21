# GitHub Dossier V2 — Workspace Redesign Megaprompt

This prompt covers 6 areas: sidebar navigation, workspace layout compaction, workflow changes, keyboard navigation, writing preferences page, and campaign detail page. Work through each area in order.

---

## AREA 1: Persistent Collapsible Sidebar on Every Page

The left sidebar (currently showing Signal Queue, Campaigns, Sequences) only appears on some pages and is missing on others (e.g., `/app`). Fix this.

### Requirements:
- The sidebar must render on **every page** of the web app: `/app`, `/campaigns`, `/mapping-sequences`, `/writing-preferences` (new), and any future pages
- Add a **collapse/expand toggle** — a hamburger icon or `[` keyboard shortcut that collapses the sidebar to icon-only mode (like Linear or Notion)
- Collapsed state should persist (use a JS variable — NOT localStorage since this is a server-rendered app, just keep it in React state or a cookie)
- Sidebar nav items:
  1. **Signal Queue** → `/app`
  2. **Campaigns** → `/campaigns`
  3. **Sequences** → `/mapping-sequences`
  4. **Writing Preferences** → `/writing-preferences` (NEW — see Area 6)
- Active page should be highlighted in the sidebar
- The sidebar is a **web app only** feature — it does NOT exist in the Claude Cowork plugin

### Files to modify:
- `templates/v2/app.html` — add sidebar to the V2 SPA shell
- `templates/campaign_form.html`, `templates/campaigns.html`, `templates/mapping_sequences.html` — ensure sidebar is present on these pages too. If these are separate Flask templates, extract the sidebar into a shared partial/include, OR convert them to use the same SPA shell.

---

## AREA 2: Compact Workspace Layout

The signal workspace has way too much vertical whitespace. Every section is a full-width card with `p-5` padding and section dividers between them. Fix this.

### 2a. Merge Signal Context + Campaign into one compact top bar
Replace the two separate full-width cards (`SignalContext` and `CampaignSection` components) with a single compact row:
- **Left side**: Company name + badges (industry, size, revenue) + external link icon + signal type badge + date
- **Right side**: Campaign name as a small clickable pill/badge (see 2b)
- Keep the signal description as one line of text below this row — no card wrapper, minimal padding
- Keep the Evidence toggle (collapsed by default) and BDR positioning angle, but make them tight
- This replaces ~200px of vertical space with ~60px

### 2b. Campaign is a pill, not a section — auto-save on selection
- Show the campaign name as a colored pill/badge in the top-right of the header row
- Clicking the pill opens an inline dropdown of available campaigns (fetched from `GET /v2/api/campaigns`)
- Selecting a campaign from the dropdown **auto-saves** via `PUT /v2/api/signals/{id}/campaign` — NO "Confirm" button
- If the campaign was auto-recommended, show a small "auto" indicator on the pill
- Remove the entire `CampaignSection` component and its section divider

### 2c. Shrink Search Apollo button
- Place the "Search Apollo" button **inline** with the persona chips, not as a full-width block
- Layout should look like: `[VP Engineering (vp) x] [Dir Localization (dir) x] [+ Add] [🔍 Search]`
- The search button should be the same height as the persona chips

### 2d. Remove ALL section dividers
- Delete every `<div className="v2-section-divider">` element in the workspace
- These waste ~80px of vertical space each and add nothing

### 2e. Reduce padding everywhere
- Change card padding from `p-5` to `p-3`
- Change `space-y-4` between sections to `space-y-2`
- Tighten margins throughout the workspace

---

## AREA 3: Workflow Changes

### 3a. Contacts are NOT auto-pulled from Apollo
Do NOT auto-trigger Apollo search on workspace load. The BDR must:
1. Review the signal and evidence
2. Confirm the campaign looks right (or override via the pill dropdown)
3. Confirm the persona chips look right (pre-loaded from `workspace.personas`, editable)
4. THEN click Search (or press keyboard shortcut `f`)

This is intentional — the BDR is the quality gate before spending Apollo credits.

### 3b. Apollo search returns batches of 3 verified contacts
When the BDR triggers Search Apollo:
- Search using the persona tiers from `workspace.personas`
- Request `per_page: 10` from Apollo (to get a pool to filter from)
- Filter to **verified emails only** (`email_verified === true` or Apollo's `email_status === 'verified'`)
- Display a **max of 3 contacts** per search
- Pre-check all 3 by default (BDR can uncheck ones they don't want)
- Display as compact rows: checkbox + name + title + email

The Apollo search endpoint (`POST /v2/api/signals/{id}/search`) already has `verified_only` and `max_results` params — the frontend just needs to pass them and handle the response properly. If no verified contacts are found, show: "No verified contacts found. Try adding different persona titles."

### 3c. Default Apollo sequence for all campaigns
Every campaign should default to "Template - 4 Email (Single Thread)" (Apollo sequence ID: `699a30753ab26800215fa07e`).

Run this SQL update:
```sql
UPDATE campaigns
SET sequence_id = '699a30753ab26800215fa07e',
    sequence_name = 'Template - 4 Email (Single Thread)'
WHERE sequence_id IS NULL OR sequence_id = '';
```

### 3d. BDR can override the sequence at enrollment time
In the Enrollment component, show:
- The default sequence name ("Template - 4 Email (Single Thread)")
- A small "Change" link next to it
- Clicking "Change" shows a dropdown of Apollo sequences (fetch from the sequences API)
- Override applies to THIS enrollment only
- Pass the selected `sequence_id` to the enrollment API call

### 3e. Post-selection flow: sequencing individual contacts
After the BDR selects 1-3 contacts and clicks Save/Sequence (or presses Enter):
1. Save prospects to DB via `POST /v2/api/prospects`
2. Transition to show the FIRST selected contact's draft sequence
3. BDR reviews/edits the 4-step draft, confirms enrollment
4. Advance to next selected contact
5. After all contacts enrolled, advance to next signal in queue

---

## AREA 4: Keyboard Navigation Fixes

### 4a. Remove number-key signal selection from table view
Number keys (1-9) currently try to jump to signal #N in the table view, but `1/2/3` are already used for draft step tabs in the workspace. This creates conflicts (pressing `1` to go to signal 1 vs. pressing `1` to see draft step 1) and only supports 10 signals anyway.

**Fix**: Remove the number-key signal jumping from the table view entirely. Numbers ONLY work as draft step tab switches in the workspace.

### 4b. Arrow keys + Enter are the primary queue navigation
- **↑/↓ arrow keys**: Move selection up/down in the signal table. Ensure focus ring is visible.
- **Enter**: Open the selected signal's workspace
- **Esc**: Go back from workspace to table view
- This already partially works — make sure focus starts on the first signal on page load.

### 4c. `/` to focus search box
Pressing `/` (when not in an input field) should focus the search/filter input in the signal table. This is the "jump" mechanism for large queues — type a company name to filter, arrow down to it, Enter to open. Like GitHub's search.

### 4d. Paginated queue with "show 10" toggle
Add a toggle in the signal table header area:
- **Default**: Show all signals in the current filter (scrollable table)
- **Toggle on**: Show only 10 signals at a time with Previous/Next pagination buttons
- Keyboard: `[` and `]` to go to previous/next page when paginated mode is on
- Persist the toggle state in React state

---

## AREA 5: Campaign Detail Page — Prompt Textarea at Bottom

When clicking into an individual campaign (the edit/detail view), the campaign's prompt/writing instructions field needs to be:
- **Full-width textarea** — not a truncated preview
- **The LAST element on the page** — at the very bottom, below all other campaign settings (name, status, assets, persona editor, sequence config)
- **Tall enough to see the full prompt** without scrolling inside the textarea — use `min-height: 400px` or auto-grow based on content
- **Editable** — BDR can modify the prompt and save
- Label it "Campaign Prompt / Writing Instructions" with a helper text: "These instructions guide AI draft generation for this campaign"

### Files to modify:
- `templates/campaign_form.html` — reorder the form fields so the prompt textarea is last. Resize it.

---

## AREA 6: Writing Preferences — Full Page (NOT a Settings Modal)

Create a new full page at `/writing-preferences` accessible from the sidebar navigation.

### 6a. Route setup
- Add a Flask route for `GET /writing-preferences` that renders a page (can use the same SPA shell as `/app` or a standalone template)
- The page fetches data from the existing API endpoints:
  - `GET /v2/api/writing-preferences` — org-wide prefs
  - `GET /v2/api/bdr-writing-preferences` — list of BDR emails with personal prefs
  - `GET /v2/api/bdr-writing-preferences/<email>` — specific BDR's prefs + merged view

### 6b. Page layout — two sections

**Section 1: Org-Wide Writing Preferences**
- Each preference key (tone, banned_phrases, preferred_structure, cta_guidance, signoff_guidance, custom_rules) as a labeled textarea
- Save button calls `PUT /v2/api/writing-preferences`
- Changes apply to ALL BDRs globally
- Visual: clean form layout, each key gets a label + textarea + helper text explaining what it does

**Section 2: BDR Personal Overrides**
- Dropdown to select a BDR by email (from `GET /v2/api/bdr-writing-preferences`)
- Option to add a new BDR email
- Once selected, shows:
  - **Merged view** (read-only) — what the BDR will actually see when drafts are generated
  - **Personal overrides** (editable) — each override row has: key dropdown, value textarea, override_mode selector (add/replace/remove)
  - Add new override button
  - Delete override button (calls `DELETE /v2/api/bdr-writing-preferences/<email>/<key>`)
- Inline explanation of override modes:
  - `add` = appends to the org value (e.g., additional banned words)
  - `replace` = fully replaces the org value for this BDR
  - `remove` = removes specific items from the org list (e.g., un-ban a word)

### 6c. Sidebar integration
Add "Writing Preferences" as the 4th nav item in the sidebar, below "Sequences". Use a pencil/edit icon.

---

## Testing Checklist

After all changes, verify:

1. [ ] Sidebar appears on `/app`, `/campaigns`, `/mapping-sequences`, `/writing-preferences`
2. [ ] Sidebar collapses and expands
3. [ ] Workspace top bar shows company + campaign pill in one row
4. [ ] Clicking campaign pill opens dropdown, selecting auto-saves
5. [ ] No section dividers in workspace
6. [ ] Padding is tight (p-3, space-y-2)
7. [ ] Search Apollo button is inline with persona chips
8. [ ] Apollo search returns max 3 verified contacts
9. [ ] Number keys don't jump signals in table view
10. [ ] Arrow keys + Enter navigate and open signals
11. [ ] `/` focuses search box
12. [ ] "Show 10" toggle works with `[`/`]` pagination
13. [ ] Campaign detail page has prompt textarea at the bottom, full-width
14. [ ] `/writing-preferences` page loads with org prefs and BDR override editor
15. [ ] Sequence override dropdown appears at enrollment time
16. [ ] All existing keyboard shortcuts still work in workspace (j/k, 1/2/3, s, n, Enter, ?, Esc)

---

## Files Summary

| File | Changes |
|------|---------|
| `templates/v2/app.html` | Sidebar, workspace layout rewrite, keyboard nav fixes, pagination toggle |
| `templates/campaign_form.html` | Move prompt textarea to bottom, resize |
| `templates/campaigns.html` | Add sidebar |
| `templates/mapping_sequences.html` | Add sidebar |
| `v2/routes/web.py` or `app.py` | Add `/writing-preferences` route |
| New template or SPA page | Writing Preferences full page |
| SQL migration | `UPDATE campaigns SET sequence_id = '699a30753ab26800215fa07e', sequence_name = 'Template - 4 Email (Single Thread)' WHERE sequence_id IS NULL OR sequence_id = '';` |

## Constraints
- All layout changes apply to ALL signals — this is UI, not data
- Do NOT modify the Claude Cowork plugin files (anything in `lead-machine-v2/`)
- Keep all existing API endpoints working — additive changes only
- Use the existing React + Tailwind styling patterns in `app.html`
