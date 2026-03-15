# BDR Review Artifact Specification (V2)

The `/outreach` command generates a React (.jsx) artifact as the BDR's review interface. It displays signal context, prospects, and editable drafts. The artifact is **keyboard-first** — every action is accessible via hotkey. The BDR should be able to process a signal without touching the mouse.

## Design Principles

1. **Keyboard-first.** Every action has a shortcut. Hint keys are shown inline on buttons.
2. **All prospects visible.** No carousel, no pagination. Show every prospect card vertically so the BDR can scan at a glance.
3. **One-keystroke happy path.** `Enter` opens enroll confirmation, `Enter` again confirms. Two keystrokes to ship.
4. **Inline confirmation.** No chat round-trip for enroll/noise. Modal with auto-focused confirm button — `Enter` to proceed, `Esc` to cancel.
5. **Post-action summary.** After enroll or noise, show a confirmation screen with a short chat instruction ("say enroll" / "say noise") so Claude can execute the MCP calls.
6. **Compact layout.** Signal header, campaign, and prospects should fit on one screen for 2-prospect signals. Minimize vertical padding.

## Keyboard Shortcuts

| Key | Action | Context |
|-----|--------|---------|
| `j` / `k` | Next / previous prospect | Main view |
| `1` `2` `3` | Switch draft step tab | Main view (applies to focused prospect) |
| `s` | Toggle include/exclude | Main view (applies to focused prospect) |
| `Enter` | Open enroll confirmation | Main view |
| `n` | Open noise confirmation | Main view |
| `?` | Show shortcuts overlay | Main view |
| `Enter` | Confirm action | Inside modal |
| `Esc` | Cancel / close modal / blur input | Everywhere |

**Rules:**
- Shortcuts are disabled when an `<input>`, `<textarea>`, or `<select>` is focused (except `Esc` which blurs the field).
- Shortcuts are disabled after enrollment/noise is confirmed (post-action screen).
- In modals, only `Enter` (confirm) and `Esc` (cancel) are active.

## Data Shape

The command injects data from `get_signal_workspace` as constants:

```javascript
const SIGNAL = {
  id: 42,
  signal_type: "global_expansion",
  signal_description: "Dublin EMEA HQ 125->200+ employees...",
  evidence_value: "...",
  signal_source: "excel_upload",
  status: "new",
  created_at: "2026-03-13T10:30:00Z",
  recommended_campaign_id: 3,
  recommended_campaign_reasoning: "Signal type matches expansion campaign",
};

const ACCOUNT = {
  id: 15,
  company_name: "Gong",
  website: "https://gong.io",
  industry: "Sales Intelligence",
  company_size: "1,000+",
  account_status: "new",
  account_owner: null,
};

const CAMPAIGN = {
  id: 3,
  name: "Global Expansion",
  writing_guidelines: "Reference specific market expansion evidence...",
  campaign_type: "expansion",
  reasoning: "Signal type 'global_expansion' matches expansion campaign keywords",
};

const PROSPECTS = [
  {
    id: 101,
    full_name: "Jane Smith",
    first_name: "Jane",
    last_name: "Smith",
    email: "jane@gong.io",
    email_verified: true,
    title: "VP Engineering",
    linkedin_url: "https://linkedin.com/in/janesmith",
    enrollment_status: "found",
  },
];

const DRAFTS = {
  101: [ // keyed by prospect_id
    {
      id: 201,
      step_number: 1,
      subject: "EMEA expansion at {{company}}",
      body: "Hey {{first_name}},\n\nNoticed your Dublin office grew...",
      status: "generated",
    },
    // step 2, step 3...
  ],
};

const ALL_CAMPAIGNS = [
  { id: 1, name: "Dependency Detected" },
  { id: 2, name: "Ghost Branch" },
  { id: 3, name: "Global Expansion" },
];

const WRITING_PREFS = {
  tone: "peer-to-peer, slightly technical, never salesy",
  banned_phrases: "touching base, hope this finds you, synergy",
};

// Queue context — so BDR sees progress
const QUEUE_POSITION = 1;   // 1-indexed current position
const QUEUE_TOTAL = 12;     // total new signals in queue
```

## UI Layout

### Signal Header (compact)
- Company name (large) + account status badge + signal type badge — single row
- Company metadata (website, industry, size, source, timestamp) — second row, xs text
- Evidence summary in amber highlight box
- Queue position counter in top-right (`1/12`)

### Campaign Banner (single row)
- "Campaign" label + dropdown selector + writing guidelines as truncated inline text
- No collapsed `<details>` — guidelines visible at all times as a hint

### Prospect Cards (all visible, vertical stack)
Each prospect gets a card. **No carousel.** All cards are visible and scrollable.

Per card:
- **Checkbox toggle** — green check when included, empty when excluded
- Full name + title + email + verified badge + LinkedIn link — single line
- Enrollment status badge (right-aligned)
- **Draft step tabs** — `Step 1` / `Step 2` / `Step 3` (keyboard: `1` `2` `3`)
  - Subject line (editable input)
  - Body (editable textarea, 6 rows)
  - Draft status badge
- **Focus ring** — blue ring on the currently keyboard-focused prospect
- Excluded prospects are dimmed (`opacity-50`) and hide their draft editors

### Action Bar (sticky bottom)
- Left: "Enroll N" primary button with `Enter` kbd hint
- Center: queue position (`1 of 12`)
- Right: "Noise" destructive button with `n` kbd hint

### Shortcut hints
- Above prospect list: inline hint showing `j`/`k` navigate, `s` toggle, `?` help
- On every actionable button: `<kbd>` element showing the shortcut key

### Confirmation Modals (inline, no chat round-trip)
**Enroll modal:**
- Count + campaign + prospect list
- "Enroll N" button (auto-focused, green) + "Cancel" button
- `Enter` confirms, `Esc` cancels

**Noise modal:**
- Company name + warning text
- "Confirm Noise" button (auto-focused, red) + "Cancel" button
- `Enter` confirms, `Esc` cancels

### Post-Action Screens
**After enroll:** Green success card with checkmark, prospect list, and instruction: `Say "enroll" in chat to proceed.`

**After noise:** Slate card with X mark, company name, and instruction: `Say "noise" in chat to proceed.`

## Styling

- Tailwind utility classes only
- Palette: slate/gray base, emerald for include/enroll, amber for signals, red for noise
- Cards: `rounded-lg shadow-sm border`
- Focus ring: `ring-2 ring-blue-400 border-blue-300`
- Kbd elements: `bg-slate-100 text-slate-500 border border-slate-300 rounded shadow-sm text-[10px] font-mono`
- Excluded cards: `opacity-50`
- Compact spacing: `p-3`/`p-4`, `mb-2`/`mb-3`
- Desktop-optimized, single column, max-w-3xl centered
