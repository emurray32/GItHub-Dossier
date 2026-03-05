# GitHub Dossier — AI Sales Intelligence for Phrase

## What This Is
GitHub Dossier (RepoRadar) is a BDR tool that scans public GitHub organizations to find companies preparing for internationalization. It detects the "Goldilocks Zone" — companies that have installed i18n libraries but haven't deployed translations yet.

## Who Uses This
- **You (the builder):** Full access to code, architecture, and system design
- **BDR Team (in CoWork):** Uses the MCP tools + skill playbooks to research companies, write emails, and manage outreach

---

## BDR Skill Index

These skills are the BDR playbook. Each one covers ONE specific aspect of outreach at maximum granularity.

### Email Writing Skills
| Skill | What It Covers | When to Reference |
|---|---|---|
| `first-touch-email` | Email 1 structure, 5-line formula, pre-send checklist | Writing any first cold email |
| `follow-up-emails` | Emails 2-3 angles, anti-patterns, approved approaches | Writing follow-ups after no reply |
| `breakup-email` | Email 4 template, 40-word limit, graceful close | Writing the final sequence email |
| `subject-lines` | Rules by position, character limits, patterns | Writing any subject line |

### Vocabulary Skills
| Skill | What It Covers | When to Reference |
|---|---|---|
| `words-to-use` | Approved verbs, nouns, tone words, contractions | Choosing any word in an email |
| `words-to-avoid` | Banned phrases, marketing speak, pressure language | Reviewing any draft for violations |

### Targeting Skills
| Skill | What It Covers | When to Reference |
|---|---|---|
| `signal-hooks` | How to turn each signal type into an email opener | Writing the hook sentence |
| `cta-formulas` | Every approved CTA by energy level and position | Choosing how to end an email |
| `persona-playbook` | Tone/angle/vocabulary per buyer type | Adjusting for VP Eng vs Product vs Localization |

### Phone & Meeting Skills
| Skill | What It Covers | When to Reference |
|---|---|---|
| `call-openers` | First 30 seconds of cold/warm calls, conversation starters | Before any phone call |
| `objection-handling` | AQR framework, 8 common objections with exact responses | During or after any objection |
| `qualifying-questions` | Discovery questions organized by what you're learning | During discovery calls |

### Integration Skills (Existing)
| Skill | What It Covers |
|---|---|
| `cold-outreach` | Original comprehensive cold outreach skill (references above for detail) |
| `apollo-api` | Apollo.io REST API integration guide |
| `apollo-prospecting` | Prospecting strategies using Apollo |
| `apollo-sequences` | Email sequence automation |

---

## MCP Tools Available

The MCP server (port 5001) provides these tools for CoWork/Agent use:

### Scanning & Research
- `dossier_scan_company` — Run a full 3-signal scan on a company
- `dossier_get_report` — Get a completed scan report
- `dossier_get_signals` — Get specific i18n signals from a scan
- `dossier_analyze_website` — Analyze a company's website for expansion signals
- `dossier_search_reports` — Search past reports by company name

### Outreach Generation
- `dossier_generate_outreach_email` — Generate 3 personalized email variants
- `dossier_generate_email_sequence` — Generate a full 4-email sequence
- `dossier_get_contributors` — Get company's top GitHub contributors

### Apollo.io Integration
- `apollo_search_people` — Search for contacts by domain/title
- `apollo_create_contact` — Create a contact in Apollo
- `apollo_enroll_contact` — Enroll a contact in a sequence
- `apollo_batch_enroll` — Batch enroll multiple contacts

### Pipeline Management
- `dossier_list_accounts` — List all monitored accounts
- `dossier_add_account` — Add a company to the pipeline
- `dossier_get_pipeline_summary` — Get tier distribution counts

---

## BDR Workflow in CoWork

### Standard Research → Outreach Flow
1. **Scan:** Use `dossier_scan_company` with the company name
2. **Analyze:** Review the report — check Goldilocks status, signals found, tier
3. **Identify Contacts:** Use `dossier_get_contributors` to find engineering contacts
4. **Enrich:** Use `apollo_search_people` to find emails and titles
5. **Draft Email:** Use `dossier_generate_outreach_email` OR write manually using the skill playbooks
6. **Review:** Check draft against `words-to-avoid` and `first-touch-email` checklists
7. **Enroll:** Use `apollo_enroll_contact` to start the sequence

### Quick Reference: The Goldilocks Zone
- **Not too early:** i18n libraries are installed (proven intent)
- **Not too late:** No translation files exist (Phrase can be their system from day one)
- **The signal:** `react-i18next` / `i18next` / `vue-i18n` / etc. in `package.json` BUT no `locales/` directory

### Quick Reference: Tier System
| Tier | Meaning | Action |
|---|---|---|
| Tier 2 (HOT) | Goldilocks Zone | Immediate outreach — "Call Now" |
| Tier 1 (WARM) | Early signals | Nurture sequence |
| Tier 0 (MONITOR) | Curiosity, no clear intent | Quarterly check-in |
| Tier 3 (LAUNCHED) | Already has translations | Low priority |
| Tier 4 (ARCHIVED) | Invalid / no opportunity | No action |

---

## Tech Stack
- Python 3.11 / Flask / SQLite (or PostgreSQL)
- OpenAI GPT-5-mini via Replit AI proxy
- GitHub REST API for scanning
- Apollo.io API for contact discovery and sequences
- SSE for real-time scan streaming
- MCP server on port 5001
