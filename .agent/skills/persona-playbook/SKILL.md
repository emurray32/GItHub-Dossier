---
name: persona-playbook
description: How to adjust tone, angle, vocabulary, and CTA for each buyer persona. The same signal needs completely different framing depending on who you're emailing.
---

# Persona Playbook — Tone Adjustment by Buyer Type

The same Goldilocks signal requires a completely different email for a VP of Engineering vs. a Head of Product vs. a Dir of Localization. This skill defines exactly how to adjust.

---

## Persona 1: VP of Engineering / CTO / Head of Engineering

### Who They Are
- Own the engineering org's velocity and architecture decisions
- Care about developer productivity, not translation quality
- Think in systems, CI/CD, and infrastructure
- Hate anything that creates toil for their team
- Skeptical of vendor pitches — they've heard hundreds

### What They Care About
1. Developer velocity (will this slow my team down or speed them up?)
2. Integration quality (does it fit our stack or is it another tool?)
3. Maintenance burden (does this create more work long-term?)
4. Build vs. buy calculus (should we build this ourselves?)

### Tone
- **Very technical.** Name libraries, repos, branches, CI/CD concepts.
- **Peer-to-peer.** You're one engineer talking to another.
- **Zero marketing language.** They'll delete instantly.
- **Respect their time.** Shortest possible email.

### Vocabulary Adjustments
| Use | Don't Use |
|---|---|
| "CI/CD" | "workflow" |
| "GitHub Sync" | "integration" |
| "infrastructure" | "platform" |
| "automates" | "streamlines" |
| "developer velocity" | "productivity" |
| "plugs into your stack" | "works with your tools" |
| "sprint cadence" | "release cycle" |
| "key management" | "translation management" |

### Email Angle
Frame Phrase as **developer infrastructure** that eliminates toil:
- "Phrase automates that via GitHub Sync — your devs never touch translation files."
- "Phrase plugs into your CI/CD so localization scales with your sprint cadence."

### Default CTA
"Open to seeing how we fit into your CI/CD?"

### Example Email 1
```
Hey {{first_name}},

Noticed you added `react-i18next` to `main-app` but no locale files yet.

That's usually when manual JSON wrangling starts. Phrase automates that via GitHub Sync — your devs never touch translation files.

Open to seeing how we fit into your CI/CD?

{{sender_first_name}}
```

---

## Persona 2: Head of Product / VP Product / CPO

### Who They Are
- Own the product roadmap and launch timelines
- Care about speed-to-market and removing launch blockers
- Think in quarters, launches, and market expansion
- Less technical — they won't know what `package.json` means
- Want outcomes, not implementation details

### What They Care About
1. Time-to-market (can we launch internationally faster?)
2. Launch blockers (is localization going to delay our Q3 launch?)
3. Market expansion (can we get into DACH/APAC/LATAM faster?)
4. Team coordination (does this reduce handoff friction?)

### Tone
- **Business-outcome focused.** Talk about launches, markets, speed.
- **Light on technical details.** No repo names or library names.
- **Strategic.** Frame localization as a launch enabler.
- **Collaborative.** "Your team" and "your launches."

### Vocabulary Adjustments
| Use | Don't Use |
|---|---|
| "international launches" | "localization" |
| "time-to-market" | "CI/CD pipeline" |
| "new markets" | "locale files" |
| "launch faster" | "GitHub Sync" |
| "release cycle" | "sprint cadence" |
| "translation handoffs" | "JSON wrangling" |
| "scale globally" | "key management" |
| "bottleneck" | "technical debt" |

### Email Angle
Frame Phrase as a **launch accelerator** that removes the localization bottleneck:
- "Phrase keeps translations moving at sprint speed so launches don't slip."
- "We help teams remove the localization bottleneck from the release cycle."

### Default CTA
"Worth a quick look at how we speed up international launches?"

### Example Email 1
```
Hey {{first_name}},

Your engineering team recently started setting up internationalization infrastructure — looks like global expansion is on the roadmap.

Teams at this stage often find localization becomes a launch bottleneck. Phrase keeps translations moving at sprint speed so international launches don't slip.

Worth a quick look at how we speed up international launches?

{{sender_first_name}}
```

### Special Note
For Product personas, you can reference the signal more broadly. Instead of "`react-i18next` in `main-app`," say "your engineering team recently started setting up internationalization infrastructure." They don't need the technical specifics.

---

## Persona 3: Director of Localization / Localization Manager / i18n Lead

### Who They Are
- Own the localization function — this is their DOMAIN
- Know TMS, connectors, CAT tools, QA processes intimately
- Their pain is developer handoffs, not technology selection
- They've used Lokalise, Crowdin, Transifex, or built something in-house
- They want to talk shop, not be sold to

### What They Care About
1. Workflow automation (can I stop chasing developers for strings?)
2. Context for translators (do translators see where strings are used?)
3. QA process (how do I catch translation bugs before release?)
4. Connector ecosystem (does Phrase connect to our stack?)
5. Migration effort (how hard is it to switch from our current setup?)

### Tone
- **Domain expert to domain expert.** They know the space — don't explain basics.
- **Focus on workflow pain.** Developer handoffs, manual processes, QA.
- **Mention TMS capabilities.** They'll evaluate on features.
- **Never badmouth competitors.** They might be using one.

### Vocabulary Adjustments
| Use | Don't Use |
|---|---|
| "TMS" | "translation platform" (they know what a TMS is) |
| "connector ecosystem" | "integrations" |
| "workflow automation" | "automation" (be specific) |
| "dev-to-translator handoff" | "collaboration" |
| "string context" | "developer context" |
| "in-context editing" | "visual editor" |
| "QA checks" | "quality assurance" |
| "translation memory" | Never skip this — they care |

### Email Angle
Frame Phrase as **workflow automation that connects the dev-translator handoff**:
- "Phrase automates the dev-to-translator handoff via GitHub Sync — context travels with the strings."
- "We handle the infrastructure between your devs and translators so handoffs don't bottleneck releases."

### Default CTA
"Open to comparing workflows?"

### Example Email 1
```
Hey {{first_name}},

Noticed your engineering team added `react-i18next` to `main-app` — looks like i18n infrastructure is going in.

This is usually when the dev-to-translator handoff question comes up. Phrase automates that with GitHub Sync and in-context editing — strings flow with full context, no manual exports.

Open to comparing workflows?

{{sender_first_name}}
```

---

## Persona 4: Default / Unknown Title

### When to Use
When you don't know the person's title, or their title doesn't match any of the above.

### Tone
- **Middle ground.** Technical enough to be credible, not so technical it alienates.
- **Lean toward the Engineering persona** (most GitHub contacts are engineers).
- **Use the standard hooks** from the `signal-hooks` skill.

### Default CTA
"Worth a look?"

### Example Email 1
```
Hey {{first_name}},

Noticed you added `react-i18next` to `main-app` but no locale files yet.

That usually means manual JSON wrangling is next. Phrase automates that via GitHub Sync — your team never touches translation files.

Worth a look?

{{sender_first_name}}
```

---

## Persona Detection Rules

When writing for a contact, detect their persona from their title:

| Title Contains | Persona |
|---|---|
| "VP Engineering", "CTO", "Head of Engineering", "SVP Engineering" | VP Engineering |
| "VP Product", "Head of Product", "CPO", "Director of Product" | Head of Product |
| "Localization", "i18n", "Internationalization", "Globalization", "Translation" | Dir Localization |
| Anything else / Unknown | Default |

If a title contains multiple signals (e.g., "VP of Engineering and Product"), prefer the more specific persona (Engineering > Product > Default).
