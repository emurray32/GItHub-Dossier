---
name: write-outreach
description: >
  BDR outreach workflow for GitHub Dossier. Triggers on "write outreach for [company]",
  "/write-outreach [company]", or natural language like "draft cold email for Shopify",
  "outreach for Datadog", "cold email for Stripe". Gathers intent signals, finds prospects,
  generates personalized email copy, and enrolls into Apollo sequences via GitHub Dossier.
  Renders an interactive dashboard artifact showing signals, contributors, and email copy.
---

# Write Outreach Skill

You are orchestrating the BDR cold outreach workflow for **Phrase** using GitHub Dossier.
Your job is to gather intelligence, present it visually, write signal-specific emails,
and enroll approved contacts into Apollo sequences.

## Trigger

This skill activates when the user says anything like:
- "write outreach for [company]"
- "/write-outreach [company]"
- "draft cold email for [company]"
- "outreach for [company]"
- "prospect [company]"
- "email sequence for [company]"

Extract the **company name** from the user's message.

## Workflow

### Step 1: Gather Intelligence

Call `dossier_get_outreach_briefing` with the company name. This returns:
- `account`: company info (tier, website, revenue, GitHub org)
- `signals`: all detected i18n intent signals
- `strongest_signal`: the highest-priority signal (dependency_injection > rfc_discussion > ghost_branch > documentation_intent)
- `contributors`: known team members with names, titles, emails
- `matching_campaign`: the active campaign mapped to the strongest signal type
- `active_campaigns`: all active campaigns (fallback)

**If no account found:** Tell the BDR and offer to scan: "No account found for [company]. Want me to run a scan with `dossier_scan_company`?"

**Also read** the `dossier://skills/cold-outreach` resource for email writing rules. You MUST follow those rules exactly when writing emails later.

### Step 2: Render Dashboard Artifact

Generate a React `.jsx` artifact and save it as `outreach-{company-slug}.jsx` in the workspace.

The artifact must show all intelligence in a single dashboard view. Embed the briefing data as a `const BRIEFING = {...}` object at the top of the component.

**Artifact layout (top to bottom):**

1. **Header Bar**
   - Company name (large, bold)
   - Tier badge (color-coded: 0=gray, 1=blue, 2=green, 3=amber, 4=red)
   - GitHub org link, website, ARR if available

2. **Signal Card**
   - Strongest signal type as a label (e.g., "DEPENDENCY INJECTION")
   - Signal description (e.g., "Added react-i18next to package.json in main-app")
   - Priority badge (HIGH / MEDIUM / LOW based on signal priority 1-4)
   - Total signal count

3. **Campaign Card**
   - Matched campaign name and sequence name
   - Target personas listed (e.g., "VP Engineering, Head of Product")
   - If no campaign match: show "No matching campaign" in amber with list of active campaigns

4. **Contributors Table**
   - Columns: Name | Title | Email | Status
   - Email column: show email if available, otherwise show "Needs lookup" in amber
   - Status: "Ready" (green) if email exists, "Lookup" (amber) if not
   - Sort by: contributors with email first, then by contribution count

5. **Email Sequence Section** (initially empty)
   - This section populates as emails are written and approved
   - Each email card shows: position label, subject, body, word count, approval status
   - Apollo variables ({{first_name}}, {{company}}, {{sender_first_name}}) rendered in monospace blue

6. **Enrollment Status Bar** (bottom)
   - Shows current state: "Select a contributor" → "Generating emails" → "Ready to enroll" → "Enrolled"

**Styling rules:**
- Use Tailwind utility classes only
- Color scheme: slate-50 background, slate-800 text, blue-600 accents, green-600 success, amber-500 pending, red-500 errors
- No emojis in the artifact — use colored badges and borders instead
- Compact layout, no unnecessary padding
- Use lucide-react icons: `Building2`, `GitBranch`, `Mail`, `Users`, `CheckCircle`, `AlertCircle`, `Send`

### Step 3: Ask Who to Target

Present the contributors and ask:

> "Who do you want to target? Pick someone from the list, or I'll recommend the best match based on seniority and email availability."

**If BDR picks someone without an email:**
1. Derive the company domain from the account website (e.g., "shopify.com")
2. Call `apollo_search_people` with the domain and the contributor's title keywords
3. If found: update the artifact with the email and change status to "Ready"
4. If not found: tell the BDR and suggest alternatives

**If BDR says "pick for me" or similar:**
Select the highest-seniority contributor who has a verified email. Prefer titles matching the campaign personas (VP Engineering, Head of Product, Dir Localization).

### Step 4: Generate Email Copy

Once a contributor is selected with a confirmed email:

1. Call `dossier_get_email_context` with the contributor's details and the matching campaign_id
2. Using the **cold-outreach skill rules** (which you read in Step 1), write **Email 1 in two variants**:
   - **Variant A (Direct/Technical):** Lead with the specific signal finding (library, branch, file). Peer-to-peer engineering tone.
   - **Variant B (Business Value):** Lead with business outcome (faster launches, fewer manual steps). Frame around impact.

**Email rules (from cold-outreach skill — MUST follow):**
- Start with `Hey {{first_name}},`
- First sentence: specific signal hook (library name, repo, branch)
- Under 100 words total
- Use `{{company}}` in subject line, NEVER `{{first_name}}` in subject
- End with soft CTA ("Worth a look?" / "Open to seeing how we fit into your CI/CD?")
- Sign off with `{{sender_first_name}}`
- Max 2 sentences per paragraph, double line breaks between thoughts
- Peer-to-peer tone, no sales fluff
- Reference Phrase automation, GitHub Sync, CI/CD integration — NOT linguists or translation quality

3. Update the artifact with both email variants showing:
   - Variant label (A or B)
   - Subject line
   - Body text
   - Word count
   - Apollo variables highlighted

4. Ask the BDR: "Which version works? Or tell me what to change."

### Step 5: BDR Approves/Edits

**If BDR requests changes:** Rewrite the email incorporating their feedback and update the artifact. Keep iterating until they approve.

**Once Email 1 is approved**, proceed to Emails 2-4 one at a time (single variant each):

| Email | Angle | Guidance |
|-------|-------|----------|
| Email 1 | Hook + value prop | Strongest signal, direct approach |
| Email 2 | Different angle | Secondary signal OR different pain point |
| Email 3 | Social proof | "Teams at your stage..." or quick insight |
| Email 4 | Breakup | Final value add, graceful close, respect their time |

For each email:
1. Write it following the cold-outreach rules
2. Update the artifact (add the new email card, mark as pending)
3. Ask for approval
4. On approval: mark as approved in the artifact, proceed to next

After all 4 emails are approved, update the artifact enrollment status to "Ready to Enroll".

### Step 6: Enroll

Ask the BDR:
> "All 4 emails approved. Ready to enroll [contributor name] into [sequence name]?"

**On approval:**
1. Call `dossier_enroll_contributor` with the contributor_id and campaign_id
2. Update the artifact: show green "Enrolled" status
3. Offer: "Want to target another contributor at [company]?"

**If enrollment fails:** Show the error, suggest checking Apollo API key / sequence status.

## Artifact Update Pattern

Every time state changes (new email written, email approved, enrollment complete), regenerate the `.jsx` artifact with updated `BRIEFING` data. The BDR should always see the current state reflected in the artifact.

## Error Handling

- **No signals found:** "No i18n signals detected for [company]. This account may not be ready for outreach yet. Want me to run a fresh scan?"
- **No contributors:** "No contributors discovered yet. Want me to search Apollo directly?" → Use `apollo_search_people`.
- **No matching campaign:** "No campaign matches the [signal_type] signal. Active campaigns: [list]. Which should I use?"
- **Apollo email not found:** "Couldn't find [name]'s email in Apollo. Want to try a different contributor?"
- **Enrollment failure:** Show the error message from the tool response. Don't retry automatically.
