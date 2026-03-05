---
name: qualifying-questions
description: Discovery and qualification questions for BDR calls. Organized by what you're trying to learn. Each question has a follow-up and a "what good looks like" answer.
---

# Qualifying Questions — Discovery Framework

Your job on a discovery call is to learn, not pitch. Ask questions, listen, take notes. The goal is to determine: **Is this a real opportunity, and how do we position Phrase?**

## The 5 Things You Must Learn

1. **Timing** — When is i18n happening? This quarter? Next year?
2. **Scope** — How many languages? How many apps/repos?
3. **Process** — How are they handling localization today?
4. **Decision** — Who decides? What's the evaluation process?
5. **Pain** — What's broken or hard about their current approach?

---

## Timing Questions

### "Is internationalization on the roadmap for this quarter or next?"
- **What you learn:** Budget cycle, urgency level
- **Good answer:** "We're targeting Q3 launch for DACH" → HOT. Specific market + timeline.
- **Concerning answer:** "Eventually" or "We've talked about it" → WARM. Nurture.
- **Follow-up:** "What's driving the timeline — a specific market launch or customer demand?"

### "What's driving the push to go international right now?"
- **What you learn:** Business trigger (new funding, customer requests, competitor pressure)
- **Good answer:** "We have paying customers asking for German and French" → Customer-driven = urgent
- **Concerning answer:** "Our CEO mentioned it once" → No real momentum
- **Follow-up:** "Are those customers on contract, or is it more inbound requests?"

### "When does the team need to have the translation workflow figured out?"
- **What you learn:** Internal deadline pressure
- **Good answer:** "Before we ship v2 in September" → Hard deadline = real
- **Concerning answer:** "No specific timeline" → Low urgency
- **Follow-up:** "What happens if translations aren't ready by the v2 launch?"

---

## Scope Questions

### "How many languages are you targeting for the first launch?"
- **What you learn:** Deal size indicator. 2 languages = small deal. 15 = enterprise.
- **Good answer:** "We need 8 languages for EMEA + APAC" → Substantial scope
- **Concerning answer:** "Just Spanish for now" → Small but could expand
- **Follow-up:** "Is there a phase 2 with additional languages planned?"

### "Is the i18n work spanning all your apps, or focused on one repo?"
- **What you learn:** Scope of implementation. One repo = limited. All apps = platform deal.
- **Good answer:** "We need it across our web app, mobile, and API docs" → Multi-product
- **Concerning answer:** "Just our marketing site" → Might be too small
- **Follow-up:** "Are the other apps on the same stack, or different frameworks?"

### "How many strings/keys are you estimating?"
- **What you learn:** Volume = pricing tier
- **Good answer:** "We haven't counted but our app is pretty large — maybe 5,000+ strings"
- **Follow-up:** "Do you have a rough sense of how many screens/pages have user-facing text?"

---

## Process Questions

### "How are translation files getting to translators today?"
- **What you learn:** Current workflow, manual pain level
- **Good answer:** "Developer exports a JSON, emails it, translator sends it back" → HIGH PAIN
- **Concerning answer:** "We have a pretty good system" → Might not need Phrase
- **Follow-up:** "How long does a round-trip take — from new string to translated string in production?"

### "What happens when a developer pushes a new string right now?"
- **What you learn:** The handoff gap Phrase solves
- **Good answer:** "Nothing automated — someone has to manually update the files" → Phrase's sweet spot
- **Concerning answer:** "Our CI picks it up and sends it to [competitor]" → Already automated
- **Follow-up:** "How's that working — any friction in the process?"

### "Are you working with any translation vendors or agencies?"
- **What you learn:** Whether they have translators or need them
- **Good answer:** "We have a vendor but the handoff is painful" → Phrase connects the dots
- **Concerning answer:** "No, we're using machine translation" → Different value prop needed
- **Follow-up:** "How are files getting to the vendor and back into the codebase?"

---

## Decision Questions

### "Who else on the team is involved in the i18n decision?"
- **What you learn:** Buying committee, next steps
- **Good answer:** "Me, our VP of Eng, and the localization lead" → Multi-stakeholder, real process
- **Concerning answer:** "Just me, but I don't really have budget" → May not go anywhere
- **Follow-up:** "Would it make sense to loop them into a quick walkthrough?"

### "Have you evaluated any localization tools before?"
- **What you learn:** Competitive landscape, evaluation maturity
- **Good answer:** "We looked at Crowdin last year but didn't move forward" → Know the space, might be ready now
- **Concerning answer:** "No, we haven't started evaluating" → Early stage, needs education
- **Follow-up:** "What made you not move forward last time?" (gold mine question)

### "What would need to be true for you to move forward with a tool this quarter?"
- **What you learn:** Their decision criteria. Whatever they say IS the deal.
- **Good answer:** Specific criteria ("needs to integrate with GitHub, support 10 languages, fit in our budget") → Clear path to close
- **Concerning answer:** "I don't know" → Not a decision-maker or too early
- **Follow-up:** "If the tool checked those boxes, what would the next step look like internally?"

---

## Pain Questions

### "What's the biggest headache with localization right now?"
- **What you learn:** Self-identified pain = strongest selling angle
- **Good answer:** "File management is a nightmare" → Phrase's core pitch
- **Follow-up:** "Can you walk me through what that looks like day-to-day?"

### "Have you hit the 'translation freeze' problem yet — where releases wait on translations?"
- **What you learn:** Whether localization is blocking shipping
- **Good answer:** "Yes, we had to delay the last release by two weeks" → Critical pain
- **Concerning answer:** "No, we haven't started translating yet" → Pre-pain, but Goldilocks
- **Follow-up:** "How did the team handle that delay? Who felt it most?"

### "If you could fix one thing about your localization workflow, what would it be?"
- **What you learn:** Priority pain point for the proposal
- **Follow-up:** "How much time does that cost the team per sprint?"

---

## Disqualification Signals

Not every lead is a deal. Watch for these signals that the opportunity isn't real:

| Signal | Meaning | Action |
|---|---|---|
| "We're just exploring" + no timeline | No urgency, no budget | Tier 0 — Quarterly check-in |
| "I'm an intern/contractor" | Not a decision-maker | Ask who leads the i18n effort |
| "We only need 1 language" | Tiny scope | May not justify Phrase's pricing |
| "We built our own TMS" | Invested in custom solution | Very hard to displace. Move on unless pain is acute. |
| "We're an open source project" | No budget | Disqualify unless there's a commercial entity behind it |
| Can't answer any scope questions | Too early or not real | Tier 0 — Check back in 90 days |
| "Our CEO wants this but engineering doesn't" | Internal misalignment | Risky. Proceed carefully. |

## Note-Taking Template
After every call, log:
```
Company: [name]
Contact: [name, title]
Timing: [when is i18n happening?]
Scope: [languages, repos, apps]
Current Process: [how it works today]
Pain Level: [1-5, what hurts most]
Decision Maker: [who decides, who else is involved]
Competition: [any tools evaluated?]
Next Step: [what you agreed to do next]
Tier Recommendation: [0/1/2 based on conversation]
```
