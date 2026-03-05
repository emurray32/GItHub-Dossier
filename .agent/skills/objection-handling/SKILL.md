---
name: objection-handling
description: Every common objection a BDR will hear, with the exact response framework. Covers email replies and phone objections.
---

# Objection Handling — Response Playbook

Every objection is a signal. Someone who objects is engaged — they're telling you what matters to them. Your job is to acknowledge, redirect, and keep the conversation going.

## The Framework: AQR (Acknowledge → Question → Redirect)

1. **Acknowledge**: Validate their concern. Never dismiss.
2. **Question**: Ask a question that reframes the objection.
3. **Redirect**: Bridge to Phrase's value from their answer.

Never argue. Never defend. Always ask.

---

## Objection 1: "We're building it in-house"

### What It Really Means
They've decided to invest engineering time in localization infrastructure. They may not know the ongoing maintenance cost yet.

### Response (Email)
```
Hey {{first_name}},

Makes total sense — a lot of teams start with an in-house build.

The part that usually gets tricky is the ongoing file sync and key management as the number of locales grows. That's where we tend to complement in-house setups rather than replace them.

Worth a quick look at how the two approaches fit together?

{{sender_first_name}}
```

### Response (Phone)
"Makes sense — a lot of teams start that way. Curious: how are you planning to handle the file sync between devs and translators as you add more languages? That's usually where the maintenance cost compounds."

### Key Moves
- Don't challenge the build decision. Respect it.
- Position Phrase as a complement, not a replacement.
- Surface the MAINTENANCE cost (they're thinking about BUILD cost).
- "As the number of locales grows" plants a future-pain seed.

---

## Objection 2: "We already use [Competitor]"

### What It Really Means
They've solved this before. You need to find the gap, not attack the incumbent.

### Response (Email)
```
Hey {{first_name}},

Got it — good to know you've already invested in the workflow.

Curious how the GitHub integration piece is working? That's where we hear the most friction from teams at your stage — keeping locale files in sync with branches without manual steps.

Open to a quick comparison?

{{sender_first_name}}
```

### Response (Phone)
"Got it — how's that working for the dev workflow side? Specifically the GitHub integration and branch sync? That's where we tend to differentiate."

### Key Moves
- NEVER name or badmouth the competitor.
- "Good to know you've already invested" validates their decision.
- Probe for a specific gap (GitHub integration is Phrase's strength).
- "Quick comparison" is low-commitment.
- If they love their current tool, gracefully exit. Not every prospect converts.

---

## Objection 3: "Not a priority right now"

### What It Really Means
Timing is wrong, not interest. This is a FUTURE opportunity.

### Response (Email)
```
Hey {{first_name}},

Totally fair — timing is everything.

When do you think internationalization will move up the list? Happy to reconnect then so you have a head start on the evaluation.

{{sender_first_name}}
```

### Response (Phone)
"Totally fair. When do you think i18n will move up the roadmap — Q3? Q4? Happy to ping you then so you don't have to start from scratch."

### Key Moves
- Validate immediately. "Totally fair" — no pushback.
- Ask for a TIMELINE, not a meeting. When will it be a priority?
- Position the reconnect as a benefit to THEM (head start, not starting from scratch).
- Log the timeline in the account notes for re-sequence.
- This is a Tier 0 (Monitor) account. Quarterly check-in.

---

## Objection 4: "Send me some info"

### What It Really Means
Could be a brush-off. Could be genuine interest. The key is to make the "info" targeted.

### Response (Phone — before sending)
"Happy to — quick question so I can send something relevant: are you more focused on the dev workflow side (GitHub integration, file sync) or the translation management side (TMS, vendor coordination)?"

### Response (Email — the follow-up)
```
Hey {{first_name}},

As promised — here's a quick look at the GitHub Sync workflow:
[single relevant link]

The short version: Phrase keeps locale files in lockstep with your branches so devs never manually manage translation files.

Worth a deeper look?

{{sender_first_name}}
```

### Key Moves
- Always ask a qualifying question BEFORE sending info.
- Send ONE link, not five. Targeted > comprehensive.
- The follow-up email should be 3-4 lines, not a product brochure.

---

## Objection 5: "How much does it cost?"

### What It Really Means
They're interested enough to evaluate budget. This is actually a buying signal.

### Response (Phone)
"Great question — pricing depends on the number of languages and keys, so it varies a lot. Rather than throw out a number that might not apply, want me to put together a quick estimate based on your setup? How many languages are you targeting?"

### Response (Email)
```
Hey {{first_name}},

Pricing depends on languages and volume, so it varies quite a bit.

Rather than a generic number — how many languages are you targeting for the first launch? I can put together something specific.

{{sender_first_name}}
```

### Key Moves
- Never give pricing in a cold email or first call. It anchors without context.
- Redirect to a qualifying question (number of languages = deal size).
- "Put together something specific" = next step = progress.
- If they insist on a number: "Teams your size typically start at [range] — but it really depends on scope. Let me get you something accurate."

---

## Objection 6: "We're too early for this"

### What It Really Means
They think they need to build more before evaluating tooling. They're wrong — earlier is better — but you can't say that.

### Response (Email)
```
Hey {{first_name}},

That actually makes you the ideal stage. Teams that wire in localization automation before the first launch avoid weeks of catch-up later.

No pressure to move fast — but happy to show what "wiring in early" looks like in practice whenever it's useful.

{{sender_first_name}}
```

### Response (Phone)
"That's actually the stage where it's easiest to set up. The teams that add automation before the first locale avoid the 'translation debt' problem entirely. Want me to show you what that looks like — 10 minutes, no commitment?"

### Key Moves
- Reframe "too early" as "perfect timing."
- Use "translation debt" as a concept — it compounds like tech debt.
- Keep the ask light. They think they're early, so don't push.

---

## Objection 7: "I'm not the right person"

### What It Really Means
They might genuinely not be the decision-maker, OR they're deflecting.

### Response (Phone)
"Totally understand — who on the team is leading the i18n effort? Happy to reach out to them directly and mention you pointed me their way."

### Response (Email)
```
Hey {{first_name}},

No worries at all — who on your team is closest to the internationalization effort? Happy to reach out directly.

{{sender_first_name}}
```

### Key Moves
- Ask for a NAME, not just "the right person."
- "Mention you pointed me their way" = social proof when you contact the referral.
- If they give a name: that's a warm intro. Use it.
- If they don't respond: they were deflecting. Move on.

---

## Objection 8: "How did you find us?"

### What It Really Means
They want to know if this is random spam or targeted outreach.

### Response
"Your public GitHub activity — specifically [library] in [repo]. We scan public repositories for i18n signals to find teams at the right stage for our tooling. We only reach out when the signal is clear."

### Key Moves
- Be completely honest. This is a trust moment.
- Name the SPECIFIC signal. It proves you're not spam.
- "Public" repositories — emphasize public. Nothing private was accessed.
- "We only reach out when the signal is clear" — positions outreach as selective, not mass.

---

## Objection Response Anti-Patterns

| Bad Response | Why It Fails |
|---|---|
| "But have you considered..." | Argumentative. Sounds like you're not listening. |
| "I understand, but..." | "But" negates the acknowledgment. |
| "Let me explain why..." | Lecturing, not conversing. |
| "You should really look at this because..." | Pushy. They said no. |
| "Most companies your size..." | Presumptuous. You don't know their size. |
| "I guarantee you'll love it" | Overpromise. Cringe. |
| Long paragraphs of rebuttal | Shows desperation. Keep it short. |

## The Golden Rule
If someone objects three times on the same point, stop. They mean it. Thank them, leave the door open, and move on. Persistence past three is harassment.
