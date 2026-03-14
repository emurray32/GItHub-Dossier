# Cold Outreach Rules

## Apollo.io Dynamic Variables (REQUIRED)

All emails are sent via Apollo.io. Use these dynamic variables:

| Variable | Description | Usage |
|----------|-------------|-------|
| `{{first_name}}` | Contact's first name | Greeting only |
| `{{company}}` | Company name | Subject line and body |
| `{{sender_first_name}}` | BDR's first name | Email signature |
| `{{title}}` | Contact's job title | Optional, for role-specific messaging |

### Critical Rules
- Use `{{company}}` in subject lines (increases open rates)
- NEVER use `{{first_name}}` in subject lines (triggers spam filters)
- Always end with `{{sender_first_name}}` as signature
- Start body with "Hey {{first_name}}," (casual, peer-to-peer)

## Signal-Specific Hooks

Before drafting, analyze the signal type to determine the hook angle:

### Global Expansion
- **Evidence:** New offices, market entry, headcount growth in non-English markets
- **Hook:** Reference the specific market/office and note English-only product/docs

### Learning Platform / Academy
- **Evidence:** Training courses, academy, certification programs in English only
- **Hook:** Reference global user base vs English-only training content

### Dependency / Library Detection
- **Evidence:** i18n libraries installed but no locale files
- **Hook:** Reference the specific library and repo, note infrastructure is ready but translation hasn't started

### Hiring / Localization Role
- **Evidence:** Job postings for localization, i18n engineering roles
- **Hook:** Reference the role and note it signals localization is a priority

### Ghost Branch
- **Evidence:** Active branches named feature/i18n, l10n, etc.
- **Hook:** Reference the branch name, note active development

## Drafting Rules

### Formatting
- Never write a paragraph longer than 2 sentences
- Use double line breaks between thoughts
- Total email body: **under 100 words**
- Tone: peer-to-peer, helpful, slightly technical. Not "salesy"

### Structure
1. **Greeting:** `Hey {{first_name}},`
2. **Hook:** Start with the specific signal evidence. No "I hope you are well."
3. **Pain/Value:** Connect signal to the pain of manual localization and how automation solves it
4. **Soft CTA:** Ask for interest, not time. "Worth a look?" / "Open to seeing how we fit?"
5. **Signature:** `{{sender_first_name}}`

## Sequence Arc (3-Email)

| Email | Angle | Approach |
|-------|-------|----------|
| Step 1 | Hook + value prop | Lead with strongest signal evidence |
| Step 2 | Different angle | Different pain point or social proof |
| Step 3 | Breakup | Final value add, graceful close |

## Templates by Signal Type

### Global Expansion
**Subject:** EMEA expansion at {{company}}

Hey {{first_name}},

Noticed your [region] office grew to [size] but your [product/docs] are still English-only for [N] customers.

We help teams like yours automate localization so your product speaks every market's language without slowing down engineering.

Worth a look?

{{sender_first_name}}

### Learning Platform
**Subject:** {{company}} Academy localization

Hey {{first_name}},

Your Academy has [N]+ courses but they're English-only — and most of your [user type] growth is coming from non-English markets.

We can unlock that with automated translation workflows that plug into your existing content pipeline.

Open to a quick look?

{{sender_first_name}}

### Dependency Detected
**Subject:** `[library]` in [repo] / {{company}}

Hey {{first_name}},

Noticed you added `[library]` to `[repo]` but no locale files yet.

This is usually when manual JSON wrangling starts. We built Phrase to automate that via GitHub Sync — your team never touches translation files.

Worth a look?

{{sender_first_name}}

### Follow-up / Dossier
**Subject:** {{company}} — localization readiness

Hey {{first_name}},

Put together a quick assessment of {{company}}'s localization footprint.

Two things jumped out:
1. [Signal 1 evidence]
2. [Signal 2 evidence]

Happy to share the full picture. Worth a look?

{{sender_first_name}}
