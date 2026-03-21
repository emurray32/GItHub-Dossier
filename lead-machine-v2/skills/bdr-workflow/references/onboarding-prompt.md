# BDR Onboarding — First Session Writing Preferences

When a BDR uses the outreach workflow for the first time, check if they have personal writing preferences saved. Use `get_bdr_writing_preferences` with their email.

## If no personal preferences exist:

Run this onboarding flow ONCE. Keep it fast — 3 questions max.

### Onboarding Script

Say:

> "Before we start, quick setup so your emails sound like you — not like AI. Three questions."

**Question 1: Sign-off**
> "How do you sign off your emails? Just your first name? 'Cheers, [name]'? Something else?"

Save their answer with:
```
update_bdr_writing_preference(user_email, "signoff_guidance", "<their answer>", "replace")
```

**Question 2: Banned words/phrases**
> "Any words or phrases you personally avoid in emails? Things that sound off to you?"

If they give you words, save with:
```
update_bdr_writing_preference(user_email, "banned_phrases", "<their words>", "add")
```

If they say "no" or "nothing specific", skip — they'll inherit the org defaults.

**Question 3: Tone preference**
> "Last one — how would you describe your email style? (e.g., 'very casual', 'slightly formal', 'technical and direct')"

If they give a clear preference that differs from the org default, save with:
```
update_bdr_writing_preference(user_email, "tone", "<their preference>", "replace")
```

If they're happy with the default, skip.

### After onboarding:

Say:
> "All set. You can change these anytime — just say 'update my writing preferences'. Let's get to work."

Then proceed to the normal outreach workflow.

## If personal preferences already exist:

Skip onboarding. Go straight to work. They've already been set up.

## Mid-session preference changes:

If a BDR says anything like:
- "stop using [word/phrase]"
- "I don't like how that sounds"
- "change my sign-off to..."
- "make it more casual"
- "update my preferences"

Use the appropriate `update_bdr_writing_preference` call and confirm:
> "Got it — added '[word]' to your personal banned list."

Or:
> "Updated your tone preference. Future drafts will reflect that."

Then regenerate the current draft if one is active.
