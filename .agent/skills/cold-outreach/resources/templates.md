# Cold Outreach Templates for Apollo.io

Use these templates as a starting point. **Always** customize them based on the specific company and repo data.

> **Apollo.io Dynamic Variables Used:**
> - `{{first_name}}` - Contact's first name
> - `{{company}}` - Company name
> - `{{sender_first_name}}` - Your first name (BDR)
>
> **Best Practice:** Never use `{{first_name}}` in subject lines (spam trigger). `{{company}}` in subjects increases open rates.

---

## Template 1: Goldilocks Signal (Library Found, No Locales)
**Subject:** `[Library]` in [Repo Name] / {{company}}

Hey {{first_name}},

Noticed you added `[Library]` to `[Repo Name]` but no locale files yet.

This is usually when manual JSON wrangling starts. We built Phrase to automate that via GitHub Sync—your team never touches translation files.

Worth a look?

{{sender_first_name}}

---

## Template 2: Friction Signal (Manual Process Pain)
**Subject:** Localization at {{company}}

Hey {{first_name}},

Noticed some recent i18n work in `[Repo Name]`—looks like translation updates might be manual right now.

We help teams automate the dev-to-translator handoff via CI/CD, so no one has to wrangle JSON files.

Worth a quick chat to see if we can remove that friction?

{{sender_first_name}}

---

## Template 3: Ghost Branch Signal (Active WIP)
**Subject:** Your [branch-name] work

Hey {{first_name}},

Noticed your team's working on i18n in the `[branch-name]` branch.

Teams often hit complexity here with key management and automation. Phrase handles that infrastructure so devs can focus on shipping.

Open to seeing how we fit into your CI/CD?

{{sender_first_name}}

---

## Template 4: Dossier Follow-up
**Subject:** {{company}} - Global Readiness Assessment

Hey {{first_name}},

Put together a quick assessment of {{company}}'s localization architecture from your GitHub footprint.

Two things jumped out:
1. Your `[Library]` implementation shows you're prioritizing speed
2. Translation updates look manual—potential bottleneck

Happy to share the full dossier. Worth a look?

{{sender_first_name}}
