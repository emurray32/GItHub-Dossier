# BDR Review Artifact Specification (V2)

The `/outreach` command generates a React (.jsx) artifact as the BDR's review interface. It displays signal context, prospects, and editable drafts. Enrollment actions happen in chat after BDR confirms.

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
    {
      id: 202,
      step_number: 2,
      subject: "Following up — {{company}} localization",
      body: "Hey {{first_name}},\n\nQuick follow-up on my last note...",
      status: "generated",
    },
    {
      id: 203,
      step_number: 3,
      subject: "Last note — {{company}}",
      body: "Hey {{first_name}},\n\nI'll keep this short...",
      status: "generated",
    },
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
```

## UI Layout

### Signal Header
- Company name (large)
- Account status badge (new/sequenced/revisit/noise, color-coded)
- Signal type badge (amber/orange)
- Evidence summary (the signal description)
- Signal source label
- Relative timestamp

### Campaign Banner
- Recommended campaign name
- Reasoning text
- Campaign dropdown to override selection
- Writing guidelines preview (collapsed by default)

### Prospect Cards
Each prospect gets a card with:
- Full name and title
- Email (with verified badge if applicable)
- LinkedIn link
- Enrollment status badge (found/drafting/enrolled/sequence_complete)
- Approve/Skip toggle (default: Approve)
- **Draft tabs** — 3 tabs for Step 1 / Step 2 / Step 3
  - Each tab shows: subject line (editable), body (editable textarea)
  - Draft status badge (generated/edited/approved)
  - "Approve All Drafts" button per prospect

### Action Bar (sticky bottom)
- "Enroll Selected (N)" primary button — shows count of approved prospects
- "Mark as Noise" destructive button
- Queue count badge showing remaining signals

### Confirmation Panel
When "Enroll Selected" is clicked:
- Summary of selections (prospect names, campaign)
- Instruction: "Confirm in chat to proceed with enrollment"

## Styling

- Tailwind utility classes only
- Palette: slate/gray base, emerald for approve, amber for signals, red for noise/archive
- Cards with `rounded-lg shadow-sm border`
- Status badges: small pills with colored backgrounds
- Responsive but desktop-optimized
