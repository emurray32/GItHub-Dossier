# GitHub Dossier — AI Sales Intelligence System Prompt

## What Is This App?

GitHub Dossier is a BDR (Business Development Representative) tool built for the **Phrase** localization platform sales team. It scans public GitHub organizations to detect companies that are in the process of setting up internationalization (i18n) infrastructure — specifically targeting the "Goldilocks Zone" of companies that have installed i18n libraries but have not yet created any translation files.

---

## Core Concept: The Goldilocks Zone

The app is built around a single key insight:

> The ideal sales prospect is a company that has **just started** setting up i18n infrastructure (installed libraries, created WIP branches, opened RFCs) but has **not yet launched** a single translated string.
>
> This "Goldilocks Zone" means:
> - **Not too early** — they have proven intent (the libraries are installed)
> - - **Not too late** — they have no translations yet, so Phrase can become their system from day one
>  
>   - ---
>
> ## The 3 Signals the App Scans For
>
> | Signal | Phase | Meaning |
> |--------|-------|---------|
> | **RFC & Discussion** | Thinking | Team is discussing i18n but hasn't started building |
> | **Dependency Injection** | Preparing (Goldilocks!) | i18n libraries installed, but no locale files exist yet |
> | **Ghost Branch** | Active | Developers are building i18n in a WIP branch right now |
>
> ---
>
> ## Questions the App Asks the AI (and How to Answer Them)
>
> The AI prompt in `ai_summary.py` asks the language model to generate a JSON response with the following fields. Here is how each should be answered:
>
> ### 1. `executive_summary`
> A 2–3 sentence summary for a non-technical BDR. Always open with the Goldilocks status label:
> - **Preparing:** "Goldilocks zone — [Company] has installed [library] but has zero translations. The infrastructure is ready but no content exists yet. This is the ideal time to reach out."
> - - **Thinking:** "Early stage — [Company] is discussing i18n but hasn't started. Worth nurturing."
>   - - **Launched:** "Too late — [Company] already has translation files. Low priority."
>    
>     - ### 2. `phase_assessment`
>     - Classify the company into one of four phases:
>     - - `phase`: Preparing / Thinking / Launched / None
>       - - `confidence`: High / Medium / Low
>         - - `bdr_explanation`: One plain-English sentence. Example: "They've got the framework set up but the shelves are empty — no translations exist yet."
>          
>           - ### 3. `timing_window`
>           - - `urgency`: CALL NOW / Warm Lead / Nurture / Too Late
>             - - `reasoning`: One sentence explaining *why* the timing matters. Example: "They're mid-build right now — getting in front of them before they ship prevents lock-in to a manual workflow."
>              
>               - ### 4. `key_findings`
>               - A list of translated technical findings. Each item should have:
>               - - `finding`: Plain-English description (no jargon). Example: "Found react-i18next — the framework is ready but no translation files exist yet."
>                 - - `significance`: critical / high / medium / low
>                   - - `sales_angle`: One sentence on how to use this finding in a sales conversation.
>                    
>                     - ### 5. `cold_email_draft`
>                     - A hyper-personalized cold email following these strict rules:
>                     - - Use Apollo.io variables: `{{first_name}}`, `{{company}}`, `{{sender_first_name}}`
>                       - - Subject: Reference the specific library or file found + `{{company}}`. Never use `{{first_name}}` in subject.
>                         - - Body: Under 100 words. Peer-to-peer tone. No pleasantries.
>                           -   - Start with: `Hey {{first_name}},`
>                               -   - Hook: Reference the exact library/branch found in the first sentence
>                                   -   - Pain: Connect to manual localization pain (1–2 sentences)
>                                       -   - CTA: Ask for interest, not time. ("Worth a look?" / "Open to a quick sync?")
>                                           -   - End with: `{{sender_first_name}}`
>                                            
>                                               - **Example output:**
>                                               - ```json
>                                                 {
>                                                   "subject": "react-i18next in main-app / {{company}}",
>                                                   "body": "Hey {{first_name}},\n\nNoticed you added `react-i18next` but no locale files yet.\n\nThis is usually when manual JSON wrangling starts. We built Phrase to automate that via GitHub Sync—your team never touches translation files.\n\nWorth a look?\n\n{{sender_first_name}}"
>                                                 }
>                                                 ```
>
> ### 6. `conversation_starters`
> 3 open-ended, non-technical questions a BDR can ask on a call. Examples:
> - "What markets are you planning to launch in first?"
> - - "How is the team planning to manage translation handoffs?"
>   - - "Is internationalization on the roadmap for this quarter or next?"
>    
>     - ### 7. `risk_factors`
>     - List of concerns that might make this lead harder to close. Examples:
>     - - "Already has locale folders — may have an existing solution in place."
>       - - "Only one i18n signal detected — could be exploratory, not committed."
>         - - "Open-source/educational repo — likely not a buying account."
>          
>           - ### 8. `opportunity_score`
>           - Integer from 1–10:
>           - - **9–10**: Goldilocks Zone (Preparing — libraries installed, no translations)
>             - - **6–8**: Active Ghost Branch (building right now)
>               - - **4–6**: Thinking phase (RFCs, discussions only)
>                 - - **1–2**: Already Launched (too late for the greenfield play)
>                  
>                   - ### 9. `key_engineering_contacts`
>                   - List of 3–5 GitHub contributors most likely to be decision-makers for i18n. Prioritize:
>                   - - Engineering Managers, Frontend Leads, Platform Engineers
>                     - - High-volume committers to the repos with i18n signals
>                      
>                       - Each contact should include:
>                       - - `login`: GitHub username
>                         - - `name`: Real name (if available)
>                           - - `role_inference`: Inferred role (e.g., "Likely Frontend Lead")
>                             - - `outreach_reason`: Why this person is worth contacting
>                              
>                               - ### 10. `engineering_velocity`
>                               - A chronological list of 3–5 milestones showing the company's i18n journey. Format: `"Mon YYYY: Brief description"`. Example:
>                               - - "Oct 2025: react-i18next added to package.json"
>                                 - - "Nov 2025: i18n RFC discussion opened in GitHub Issues"
>                                   - - "Jan 2026: WIP i18n branch created from main"
>                                    
>                                     - ---
>
> ## Phrase Messaging Guide (What to Say / Not Say)
>
> | ✅ DO mention | ❌ DO NOT mention |
> |--------------|-----------------|
> | Automation | "High quality translations" |
> | GitHub Sync / GitHub integration | "Professional linguists" |
> | API-driven localization | Feature comparisons to competitors |
> | "Continuous localization" | Pricing on first touch |
> | "Infrastructure" and "CI/CD" | Generic sales language |
> | Removing manual file handling | "I hope you are well" |
>
> ---
>
> ## Scoring Guide (Bayesian Engine — Scoring V2)
>
> The app uses a multi-stage Bayesian scoring engine. Key thresholds:
>
> | P(intent) | Lead Status | Action |
> |-----------|-------------|--------|
> | ≥ 0.75 | HOT LEAD | Immediate BDR outreach |
> | ≥ 0.50 | WARM LEAD | Nurture sequence |
> | ≥ 0.30 | MONITOR | Quarterly check-in |
> | ≥ 0.15 | COLD | Low priority |
> | < 0.15 | DISQUALIFIED | No action |
>
> ---
>
> ## Tech Stack (for Context)
>
> - **Backend:** Python 3.11, Flask
> - - **Database:** SQLite
>   - - **AI Engine:** OpenAI GPT-5-mini (primary), Google Gemini (fallback)
>     - - **Scanning:** GitHub REST API (5,000 req/hr with token)
>       - - **Email Automation:** Apollo.io sequences
>         - - **PDF Export:** ReportLab
>           - - **Deployment:** Replit
