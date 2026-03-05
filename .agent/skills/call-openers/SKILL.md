---
name: call-openers
description: Phone conversation starters for BDRs. What to say in the first 30 seconds of a cold call or a warm follow-up call after email engagement.
---

# Call Openers — First 30 Seconds on the Phone

Phone calls are different from email. You have 10 seconds to earn 30 more seconds. These scripts are for the OPENER only — the first thing you say.

## Cold Call Framework (No Prior Email Reply)

### The 10-Second Opener
```
"Hey [First Name], this is [Your Name] from Phrase.

I noticed your team added [library] to [repo] on GitHub —
looks like you're setting up i18n infrastructure.

Quick question: is the team still deciding how to handle
the translation workflow, or have you already picked a path?"
```

### Why This Works
1. **Name + company** (2 seconds — get it out fast)
2. **The signal** (5 seconds — proves you did homework)
3. **An open question** (3 seconds — gets them talking)

### Rules
- Say their name ONCE at the start. Don't repeat it.
- Say "Phrase" not "Phrase Localization Platform" — keep it short.
- Reference the SPECIFIC signal. Not "I see you're working on i18n."
- Ask an OPEN question, not a yes/no. Open questions keep them talking.
- If they say "not a good time": "Totally understand — when's better?" (Don't pitch.)

---

## Warm Call Framework (After Email Reply or Engagement)

### The 10-Second Opener
```
"Hey [First Name], this is [Your Name] from Phrase —
you replied to my email about the [library] setup in [repo].

Wanted to pick up where we left off — where are you all
in the i18n build right now?"
```

### Rules
- Reference their reply specifically. They invested time — acknowledge it.
- Jump straight to their situation, not your pitch.
- First question should be about THEIR status, not about scheduling a demo.

---

## Conversation Starter Questions (After the Opener)

These are open-ended, non-technical questions that keep the conversation flowing. Use 2-3 per call max.

### Discovery Questions (Understanding Their Situation)
| Question | What You Learn |
|---|---|
| "What markets are you planning to launch in first?" | Geographic priority, timeline |
| "Is internationalization on the roadmap for this quarter or next?" | Timing, budget cycle |
| "How is the team planning to manage translation handoffs today?" | Current process, pain level |
| "Are you building the translation pipeline in-house or evaluating tools?" | Build vs. buy stage |
| "Who else on the team is involved in the i18n decision?" | Buying committee |
| "What's driving the push to go international right now?" | Business trigger |

### Pain-Probing Questions (Surfacing the Problem)
| Question | What Pain It Surfaces |
|---|---|
| "How are translation files getting to translators today?" | Manual handoff pain |
| "What happens when a developer pushes a new string — how does it get translated?" | Workflow friction |
| "Have you run into the 'translation freeze' problem yet — where releases wait on translations?" | Launch bottleneck |
| "How does your team handle translation QA before releases?" | Quality process gaps |
| "What's the biggest headache with the current i18n setup?" | Self-identified pain |

### Technical Qualification Questions
| Question | What It Qualifies |
|---|---|
| "Are you using [library] across all your apps, or just [repo]?" | Scope of opportunity |
| "Is the i18n work in a feature branch still, or has it hit main?" | Development stage |
| "How many languages are you targeting for the first launch?" | Deal size indicator |
| "Are you working with any translation vendors yet?" | Competitive landscape |

---

## Objection Responses on the Phone

### "We're handling it ourselves"
**Response:** "Makes sense — a lot of teams start that way. The part that usually gets painful is the file sync between devs and translators. How are you planning to handle that handoff?"

### "We already use [competitor]"
**Response:** "Got it — how's that working for the GitHub integration? I ask because that's where we hear the most friction from teams at your stage."

### "Not a priority right now"
**Response:** "Totally fair. When do you think i18n will move up the list? Happy to reconnect then so you don't have to start the evaluation from scratch."

### "Send me an email"
**Response:** "Will do. Quick question so I can make it relevant — are you more focused on the dev workflow side or the translation management side?" (Gets intel, then send a targeted email.)

### "How did you find me?"
**Response:** "Your GitHub contributions to [repo] — specifically the [i18n-related work]. We scan public repos for i18n signals to find teams at the right stage." (Honest, specific, non-creepy.)

---

## Call Tone Rules

### DO:
- Talk at a normal pace. Rushing = nervous = salesperson.
- Pause after your question. Silence is fine. Let them think.
- Mirror their energy. If they're casual, be casual. If they're formal, match.
- Take notes. Reference what they said later in the call.
- Say "that makes sense" or "got it" — validating language.

### DON'T:
- Pitch Phrase features unless they ask. Discovery first.
- Say "that's a great question" — it's patronizing.
- Interrupt. Let them finish.
- Overcorrect on objections. Acknowledge, ask a question, move on.
- End without a next step. Even "I'll send you X" is a next step.

## Ending the Call
Always end with a concrete next step:
- "I'll send over a quick summary of what we talked about. Worth looping in [person they mentioned] for a 15-min walkthrough next week?"
- "Makes sense to wait until Q3. I'll set a reminder and ping you in July — sound good?"
- "I'll send you a link to the GitHub Sync docs so you can see how the integration works. If it looks relevant, happy to do a deeper dive."
