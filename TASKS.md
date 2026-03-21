# Tasks

## Active

- [ ] **Change LinkedIn Banner**
- [ ] **GitHub Dossier V2 — Workspace Redesign** - Megaprompt covering 6 areas, hand off to Claude Code
  - Area 1: Persistent collapsible sidebar on every page
  - Area 2: Compact workspace layout (merge top bar, campaign pill, reduce padding)
  - Area 3: Workflow changes (batches of 3 verified contacts, default sequence, sequence override)
  - Area 4: Keyboard nav fixes (arrow+Enter, remove number-key jumping, `/` search, show-10 toggle)
  - Area 5: Campaign detail page prompt textarea at bottom
  - Area 6: Writing Preferences full page (not a modal)
- [ ] **Run seed scripts on Replit** - `python seed_campaign_personas.py` and `python seed_writing_preferences.py`
- [ ] **Fix template fallback in draft generation** - Drafts falling back to templates instead of LLM (`generated_by: template`). Diagnose API key config.
- [ ] **Fix template variable syntax** - Templates use `{first_name}` instead of Apollo's `{{first_name}}`
- [ ] **Fix 6 QA bugs from agent testing** - Campaigns 500, stale signal on empty filter, Add Manually disabled, intake empty state, noise signal vanishing, Apollo search silent failure

## Waiting On

## Someday

- [ ] **Scheduled lead discovery agent** - Auto-scan for new intent signals every 30 min (discussed but paused to focus on UI/UX first)
- [ ] **Web app keyboard shortcuts port** - Port Cowork keyboard shortcuts to `templates/v2/app.html` (may be handled by another Claude Code terminal)

## Done
