---
name: subject-lines
description: Exact rules for writing subject lines. What works, what doesn't, character limits, and patterns by email position.
---

# Subject Line Rules

Subject lines determine whether your email gets opened. In B2B cold outreach, the subject line has ONE job: look like it came from a colleague, not a salesperson.

## Universal Rules (All Emails)

### MUST:
- Include `{{company}}` (increases open rates 22%)
- Be under 50 characters (mobile truncates at ~45)
- Look like an internal FYI, not a marketing email
- Be lowercase-start or sentence case (never Title Case Every Word)
- Reference something specific (library, repo, branch, topic)

### MUST NOT:
- Include `{{first_name}}` (triggers spam filters)
- Use ALL CAPS or any fully capitalized words
- Use emoji
- Use exclamation marks
- Use clickbait ("You won't believe...")
- Use numbers/stats ("5 ways to...")
- Use question format for Email 1 (save it for later)
- Use "Re:" or "Fwd:" tricks (dishonest, damages trust)

## Patterns by Email Position

### Email 1 Subject: The Signal Reference
Format: `[specific thing] in [repo/context] / {{company}}`

**Good:**
- `react-i18next in main-app / {{company}}`
- `i18n branch in frontend-core / {{company}}`
- `i18next in package.json / {{company}}`
- `Localization discussion in {{company}}`

**Bad:**
- `Localization solution for {{company}}` ← salesy
- `Quick question for you` ← vague, spammy
- `Helping {{company}} scale globally` ← marketing speak
- `{{first_name}}, quick question` ← spam filter trigger

### Email 2 Subject: The New Angle
Format: Short phrase + `{{company}}`

**Good:**
- `i18n automation — {{company}}`
- `Translation handoffs at {{company}}`
- `Quick thought — {{company}}`
- `Localization at scale — {{company}}`

**Bad:**
- `Following up — {{company}}` ← "following up" is a spam trigger
- `Re: react-i18next in main-app` ← fake threading
- `Checking in` ← empty calories

### Email 3 Subject: The Value-Add
Format: Casual, FYI-style

**Good:**
- `Thought this might help — {{company}}`
- `Quick observation — {{company}}`
- `i18n timing — {{company}}`
- `One thing I keep seeing`

**Bad:**
- `3 reasons to automate localization` ← listicle, not email
- `Don't miss this` ← clickbait
- `Important: localization update` ← fake urgency

### Email 4 Subject: The Breakup
Format: Short question or close signal

**Good:**
- `Closing the loop — {{company}}`
- `Should I close this out?`
- `Quick question — {{company}}`
- `One last thought — {{company}}`

**Bad:**
- `Last chance` ← scarcity pressure
- `I've tried reaching you` ← guilt
- `Final offer` ← there was no offer

## Character Count Reference

| Length | Verdict | Example |
|--------|---------|---------|
| < 25 chars | Too short, may look spammy | `i18n at {{company}}` |
| 25-40 chars | Ideal sweet spot | `react-i18next in main-app / {{company}}` |
| 40-50 chars | Acceptable | `Localization automation — {{company}}` |
| 50+ chars | Gets truncated on mobile | Rewrite shorter |

## Subject Line Testing Heuristic
Read the subject line and ask: "Would I open this if a colleague sent it to me on Slack?" If yes, it works. If it sounds like marketing, rewrite.
