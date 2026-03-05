---
name: first-touch-email
description: How to write Email 1 — the very first cold email to a prospect. The most important email in the sequence. Every word matters.
---

# First Touch Email (Email 1 of Sequence)

You are writing the MOST IMPORTANT email — the first one. If this fails, nothing else matters. Every sentence must earn the next sentence.

## The 5-Line Structure (Non-Negotiable)

```
Line 1: "Hey {{first_name}},"
Line 2: [THE HOOK — specific signal you found]
Line 3: [THE PAIN — what happens next without Phrase]
Line 4: [THE CTA — one soft question]
Line 5: "{{sender_first_name}}"
```

That's it. Five lines. Double line breaks between each. No more.

## Line-by-Line Breakdown

### Line 1: The Greeting
- ALWAYS: `Hey {{first_name}},`
- NEVER: "Hi", "Hello", "Dear", "Hope you're well", "Hope this finds you well"
- WHY: "Hey" is peer-to-peer. "Hi" is a stranger. "Dear" is 2005.

### Line 2: The Hook (This Is Everything)
The hook MUST prove you did your homework. It references the EXACT signal from their GitHub.

**Formula:** `"Noticed [specific thing] in [specific place]."`

Good hooks:
- "Noticed you added `react-i18next` to `main-app` but no locale files yet."
- "Noticed the `feature/i18n` branch in `frontend-core` — looks like active work."
- "Saw the discussion about internationalization in `platform-api`."
- "Noticed `i18next` in your `package.json` recently."

Bad hooks (NEVER write these):
- "I came across your company and thought..." ← generic
- "I noticed your company is growing..." ← says nothing
- "I see you're working on exciting things..." ← vapid
- "I was impressed by..." ← flattery, not insight

**Rules:**
- Use backticks (`) around library names, repo names, branch names, file names
- Name the SPECIFIC library, not "an i18n library"
- Name the SPECIFIC repo, not "your repositories"
- If the signal is < 30 days old, add "recently" — e.g., "recently added"
- If 30-90 days, "earlier this quarter"
- If > 180 days, drop temporal language entirely

### Line 3: The Pain
Connect the signal to the pain that Phrase solves. One to two sentences MAX.

**Formula:** `"[What happens next without us]. [How Phrase prevents that]."`

Good pain lines:
- "That usually means manual JSON wrangling is next. Phrase automates that via GitHub Sync — your devs never touch translation files."
- "Teams at this stage usually hit complexity with key management. Phrase handles that infrastructure so devs focus on shipping."
- "When the team is still deciding, that's the best time to wire in automation. Phrase plugs into your CI/CD so localization scales with your sprints."

Bad pain lines (NEVER write these):
- "We offer a comprehensive localization solution..." ← brochure talk
- "Phrase is the leading TMS..." ← nobody cares about your ranking
- "We help companies like yours..." ← vague
- "Our platform enables seamless..." ← marketing copy

**Rules:**
- Always anchor to THEIR specific situation, not Phrase's features
- Use "your team" / "your devs" — make it about THEM
- Mention GitHub Sync, CI/CD, or API — devs care about integration points
- Never exceed 2 sentences

### Line 4: The CTA
One question. Low friction. Asking for INTEREST, not TIME.

**Approved CTAs (pick one):**
- "Worth a look?"
- "Open to seeing how we fit into your CI/CD?"
- "Worth a quick look at how we speed up international launches?"
- "Open to comparing workflows?"

**Banned CTAs (NEVER use):**
- "Can we schedule a call?" ← too much commitment
- "Do you have 15 minutes?" ← asking for time, not interest
- "Would love to set up a demo" ← salesy
- "Let me know if you're interested" ← passive, weak
- "Can I send you more info?" ← you already did

### Line 5: The Signature
- ALWAYS: `{{sender_first_name}}`
- NEVER: Full name, title, phone number, LinkedIn, "Best regards", "Cheers", "Thanks"
- WHY: First name only = peer. Full sig = salesperson.

## Word Count
- **Target: 50-75 words** (body only, excluding greeting and signature)
- **Hard cap: 100 words**
- If you hit 100, cut. Every word past 75 is a word they won't read.

## Tone Calibration
- Sound like a developer Slacking a friend about something they found, not a salesperson sending a pitch
- Contractions: "that's", "you're", "we've" — always
- No exclamation marks. Ever.
- No emoji. Ever.
- No bold, italic, or formatting in the email body
- No bullet points or numbered lists in Email 1

## Pre-Send Checklist
Before finalizing Email 1, verify:
- [ ] Greeting is exactly `Hey {{first_name}},`
- [ ] Hook names a specific library, repo, branch, or file
- [ ] Hook uses backticks around technical names
- [ ] Pain connects their signal to a real problem
- [ ] Pain mentions GitHub Sync, CI/CD, or API
- [ ] CTA is a question, not a statement
- [ ] CTA asks for interest, not time
- [ ] Signature is just `{{sender_first_name}}`
- [ ] Body is under 100 words
- [ ] No exclamation marks
- [ ] No emoji
- [ ] Subject line contains `{{company}}`
- [ ] Subject line does NOT contain `{{first_name}}`
