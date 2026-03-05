---
name: signal-hooks
description: How to turn each of the 3 GitHub signals into a compelling email hook. The hook is the single most important sentence in the email.
---

# Signal Hooks — Turning GitHub Signals into Email Openers

The hook is the first sentence after "Hey {{first_name}},". It must prove you've done your homework in under 15 words. Each signal type has its own hook pattern.

## What Makes a Good Hook
1. **Specific** — names the exact library, repo, branch, or file
2. **Verifiable** — the prospect can confirm it's true
3. **Non-threatening** — observation, not accusation
4. **Technical** — uses backtick formatting for code elements
5. **Short** — one sentence, under 20 words

## Signal Type 1: Dependency Injection (Goldilocks)

**What you found:** An i18n library installed in `package.json`, `requirements.txt`, `go.mod`, etc., but NO locale files exist yet.

**Why it's the best signal:** This is the Goldilocks Zone. Infrastructure ready, no content yet. Perfect timing.

### Hook Formula
```
"Noticed you added `{library}` to `{repo}`{temporal} but no locale files yet."
```

### Examples by Library

**JavaScript/React:**
- "Noticed you added `react-i18next` to `main-app` but no locale files yet."
- "Noticed `i18next` in `frontend`'s `package.json` recently — no locales directory yet."
- "Spotted `react-intl` in `web-client` but no translation files yet."
- "Noticed `next-intl` in `marketing-site` — looks like i18n setup is underway."
- "Saw `vue-i18n` in `dashboard-app` recently — no locale files yet though."
- "Noticed `formatjs` in `ui-components` but translations aren't wired up yet."

**Python:**
- "Noticed `django-modeltranslation` in `requirements.txt` but no locale directory yet."
- "Spotted `babel` in `backend-api`'s dependencies recently."
- "Saw `flask-babel` in your requirements — looks like i18n is on the radar."

**Go:**
- "Noticed `go-i18n` in `go.mod` for `api-service` recently."
- "Spotted internationalization imports in `platform-core`."

**Ruby:**
- "Noticed `i18n` gem updates in `main-app` recently — no new locale files yet."
- "Saw `rails-i18n` configuration changes in `web-platform`."

### Key Rules for Dependency Hooks
- ALWAYS name the specific library (not "an i18n library")
- ALWAYS name the specific repo (not "your repositories")
- Add "but no locale files yet" when that's true — it's the smoking gun
- Use "recently" only if signal is < 30 days old
- Backtick the library name AND repo name

---

## Signal Type 2: RFC & Discussion (Thinking Phase)

**What you found:** GitHub Issues, Discussions, or Pull Requests mentioning internationalization, localization, i18n, or related topics.

**Why it matters:** Team is THINKING about i18n but hasn't started building. Early = opportunity to shape their approach.

### Hook Formula
```
"Saw the discussion about {topic} in `{repo}`."
```

### Examples

**Issue/Discussion based:**
- "Saw the discussion about internationalization in `platform-core`."
- "Noticed the i18n RFC in `frontend-app` — looks like the team is evaluating approaches."
- "Saw the issue about multi-language support in `web-client`."
- "Noticed the localization discussion thread in `main-app`."

**PR based:**
- "Saw the PR exploring i18n architecture in `dashboard`."
- "Noticed the RFC about translation infrastructure in `api-service`."

**Roadmap/docs based:**
- "Noticed internationalization on the roadmap in `docs`."
- "Saw localization mentioned in your `ROADMAP.md`."

### Key Rules for RFC Hooks
- Reference the TOPIC being discussed, not just "a discussion"
- Name the repo where the discussion lives
- Don't quote specific issue text — that feels surveillance-y
- Frame it as "the team is evaluating" not "you haven't decided yet"
- These contacts are earlier in their journey — tone should be more educational

---

## Signal Type 3: Ghost Branch (Active Development)

**What you found:** Active WIP branches named `feature/i18n`, `l10n`, `feature/localization`, `translations`, etc. that haven't been merged to main.

**Why it matters:** Someone is ACTIVELY BUILDING this right now. Highest urgency — they're making architecture decisions this week.

### Hook Formula
```
"Noticed the `{branch}` branch in `{repo}`{temporal} — looks like active work."
```

### Examples

**Feature branches:**
- "Noticed the `feature/i18n` branch in `frontend-core` — looks like active work."
- "Spotted the `l10n` branch in `main-app` recently."
- "Noticed the `feature/localization` branch in `web-platform` — looks like the team is building i18n infrastructure."
- "Saw the `translations` branch in `dashboard-app` — looks like active work."

**With temporal context:**
- "Noticed the `feature/i18n` branch in `frontend-core` recently — looks like active work." (< 30 days)
- "Noticed the `feature/i18n` branch in `frontend-core` earlier this quarter." (30-90 days)

### Key Rules for Ghost Branch Hooks
- ALWAYS name the exact branch name
- ALWAYS name the repo
- "looks like active work" is the standard closer — it's observational, not assumptive
- These signals are highest urgency — CTA should be slightly more direct
- Don't say "I see you haven't merged yet" — that's pushy

---

## Signal Type 4: Documentation Intent (Roadmap Mentions)

**What you found:** Localization or internationalization mentioned in README, ROADMAP, or docs files.

**Why it matters:** Weakest signal but still shows it's on their radar. Best for nurture sequences.

### Hook Formula
```
"Noticed localization mentioned in your `{file}` in `{repo}`."
```

### Examples
- "Noticed localization mentioned in your `ROADMAP.md` in `platform-docs`."
- "Saw internationalization in the README for `main-app`."
- "Noticed i18n on the roadmap in `engineering-docs`."

### Key Rules for Documentation Hooks
- Weakest signal — tone should be most exploratory
- Use softer CTAs: "Worth a look?" not "Open to seeing how we fit?"
- Don't oversell the signal — they mentioned it in docs, they didn't commit to it

---

## Hook Anti-Patterns (What NEVER to Write)

| Anti-Pattern | Why It Fails |
|---|---|
| "I noticed your company..." | Which company? Be specific. |
| "I see you're working on i18n..." | WHERE? Name the repo. |
| "Your team seems to be..." | Vague. What exactly did you find? |
| "I've been researching your tech stack..." | Creepy without specifics |
| "I found something interesting in your code..." | Clickbait. Just say what it is. |
| "Congrats on your i18n work..." | Patronizing. Not a congratulations situation. |

## The Hook Litmus Test
Ask yourself: "If I sent this hook to someone who DIDN'T add that library, would they know this email wasn't meant for them?"

If yes — the hook is specific enough.
If no — make it more specific.
