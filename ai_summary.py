"""
AI Summary Module for 3-Signal Internationalization Intent Scanner.

Generates actionable sales intelligence from pre-launch i18n signals:
- RFC & Discussion (Thinking Phase)
- Dependency Injection (Preparing Phase)
- Ghost Branch (Active Phase)
"""
import json
import os
from typing import Generator
from config import Config


def _load_cold_outreach_skill() -> str:
    """Load the cold-outreach SKILL.md file if it exists."""
    skill_paths = [
        '.agent/skills/cold-outreach/SKILL.md',
        'skills/cold-outreach/SKILL.md',
        '.agent/skill/cold-outreach/SKILL.md',
    ]
    
    for path in skill_paths:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    content = f.read()
                    print(f"[AI] Loaded cold outreach skill from: {path}")
                    return content
            except Exception as e:
                print(f"[AI] Failed to load skill from {path}: {e}")
    
    print("[AI] No cold-outreach skill file found, using defaults")
    return ""


def _get_cold_email_instructions() -> str:
    """Get cold email instructions from SKILL.md or use defaults."""
    skill_content = _load_cold_outreach_skill()

    if skill_content:
        return f"""
   *** COLD OUTREACH SKILL INSTRUCTIONS (FROM SKILL.md) ***
   Follow these instructions EXACTLY when drafting the cold email:

{skill_content}

   *** END OF SKILL INSTRUCTIONS ***
"""

    return """
   APOLLO.IO DYNAMIC VARIABLES (REQUIRED):
   - Use {{first_name}} in the greeting (e.g., "Hey {{first_name}},")
   - Use {{company}} in subject lines (increases open rates)
   - Use {{sender_first_name}} as the email signature
   - NEVER use {{first_name}} in subject lines (triggers spam filters)

   FORMATTING RULES:
   - Total body MUST be under 100 words (2025 best practice)
   - NEVER write a paragraph longer than 2 sentences
   - Use double line breaks between thoughts (visual spacing matters)
   - Tone: peer-to-peer, technical, helpful - NOT "salesy" or enthusiastic

   STRUCTURE (5 parts):
   a) "subject": Short, references specific library/file + {{company}} (e.g., "react-i18next in main-app / {{company}}")
   b) "body": Follow this exact structure:
      - GREETING: Start with "Hey {{first_name}},"
      - THE HOOK: Immediately reference the specific library, file, or branch found.
        Do NOT use "I hope you are well" or pleasantries.
        Example: "Noticed you added `react-i18next` but no locale files yet."
      - THE PAIN/VALUE: Connect that signal to the pain of manual localization.
        Mention GitHub Sync and automation (1-2 sentences max).
      - THE SOFT CTA: Ask for INTEREST, not time. Low friction.
        Examples: "Worth a look?" or "Open to seeing how we fit into your CI/CD?"
      - SIGNATURE: End with "{{sender_first_name}}"

   PHRASE MESSAGING:
   - DO mention: automation, API, GitHub integration, "infrastructure," "continuous localization"
   - DO NOT mention: "high quality translations," "professional linguists" (devs care about process, not linguists)
"""

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

AI_INTEGRATIONS_OPENAI_API_KEY = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
AI_INTEGRATIONS_OPENAI_BASE_URL = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")


def generate_analysis(scan_data: dict) -> Generator[str, None, dict]:
    """
    Generate AI-powered sales intelligence from 3-Signal scan data.
    Uses Gemini 3.1 Pro (primary), falls back to OpenAI GPT-5-mini,
    then rule-based analysis.
    """
    yield _sse_log("Initializing AI Sales Intelligence Engine...")
    ai_succeeded = False

    if not ai_succeeded and GENAI_AVAILABLE and Config.GEMINI_API_KEY:
        yield _sse_log("Preparing 3-Signal data for Gemini 3.1 Pro...")
        prompt = _build_sales_intelligence_prompt(scan_data)
        yield _sse_log("Sending to Gemini 3.1 Pro...")

        try:
            client = genai.Client(api_key=Config.GEMINI_API_KEY)
            response = client.models.generate_content(
                model=Config.GEMINI_MODEL,
                contents=prompt
            )
            yield _sse_log("Processing AI response...")
            analysis = _parse_ai_response(response.text, scan_data)
            yield _sse_log("AI Sales Intelligence Complete (Gemini 3.1 Pro)")
            yield _sse_data('ANALYSIS_COMPLETE', analysis)
            ai_succeeded = True

        except Exception as e:
            yield _sse_log(f"Gemini error: {str(e)}")
            yield _sse_log("Falling back to OpenAI...")

    if not ai_succeeded and OPENAI_AVAILABLE and AI_INTEGRATIONS_OPENAI_API_KEY and AI_INTEGRATIONS_OPENAI_BASE_URL:
        yield _sse_log("Trying OpenAI GPT-5-mini fallback...")
        prompt = _build_sales_intelligence_prompt(scan_data)

        try:
            client = OpenAI(
                api_key=AI_INTEGRATIONS_OPENAI_API_KEY,
                base_url=AI_INTEGRATIONS_OPENAI_BASE_URL
            )
            response = client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {"role": "system", "content": "You are a sales strategist for a Localization Platform. Return your analysis as valid JSON only, with no markdown formatting."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=8192
            )

            yield _sse_log("Processing AI response...")
            analysis = _parse_ai_response(response.choices[0].message.content, scan_data)
            yield _sse_log("AI Sales Intelligence Complete (GPT-5-mini fallback)")
            yield _sse_data('ANALYSIS_COMPLETE', analysis)
            ai_succeeded = True

        except Exception as e:
            yield _sse_log(f"OpenAI fallback error: {str(e)}")

    if not ai_succeeded:
        yield _sse_log("Using rule-based analysis...")
        analysis = _generate_fallback_analysis(scan_data)
        yield _sse_data('ANALYSIS_COMPLETE', analysis)


def _build_sales_intelligence_prompt(scan_data: dict) -> str:
    """Build the sales intelligence prompt for Goldilocks Zone detection."""
    # Title case the company name for cleaner output in generated emails
    raw_company = scan_data.get('company_name', 'Unknown')
    company = raw_company.title() if raw_company else 'Unknown'
    org_name = scan_data.get('org_name', scan_data.get('org_login', ''))
    signals = scan_data.get('signals', [])
    signal_summary = scan_data.get('signal_summary', {})
    intent_score = scan_data.get('intent_score', 0)
    goldilocks_status = scan_data.get('goldilocks_status', 'unknown')
    lead_status = scan_data.get('lead_status', 'Unknown')

    # Extract signal details (use 'or 0' to handle NULL values from database)
    rfc_count = signal_summary.get('rfc_discussion', {}).get('count') or 0
    rfc_high = signal_summary.get('rfc_discussion', {}).get('high_priority_count') or 0
    dep_count = signal_summary.get('dependency_injection', {}).get('count') or 0
    ghost_count = signal_summary.get('ghost_branch', {}).get('count') or 0

    # Get sample evidence with BDR translations
    sample_evidence = []
    for signal in signals[:5]:
        sample_evidence.append({
            'Signal': signal.get('Signal'),
            'Evidence': signal.get('Evidence'),
            'Priority': signal.get('priority', 'MEDIUM'),
            'BDR_Summary': signal.get('bdr_summary', ''),
            'Goldilocks_Status': signal.get('goldilocks_status', ''),
        })

    # Get libraries found for BDR-friendly explanation
    libraries_found = []
    for signal in signals:
        if signal.get('type') == 'dependency_injection':
            libraries_found.extend(signal.get('libraries_found', []))

    # Get contributors data for Key Engineering Contacts
    contributors = scan_data.get('contributors', {})
    contributors_list = []
    for login, data in contributors.items():
        contributors_list.append({
            'login': login,
            'name': data.get('name', login),
            'contributions': data.get('contributions', 0),
            'company': data.get('company', ''),
            'bio': data.get('bio', ''),
            'repos': data.get('repos', [])
        })

    # Build signal timeline data with timestamps
    signal_timeline = []
    for signal in signals:
        timestamp = signal.get('created_at') or signal.get('pushed_at') or signal.get('timestamp')
        if timestamp:
            signal_timeline.append({
                'date': timestamp[:10] if isinstance(timestamp, str) else str(timestamp)[:10],
                'type': signal.get('type', signal.get('Signal', 'unknown')),
                'description': signal.get('Evidence', signal.get('title', ''))[:100]
            })

    # Scoring V2 context (if available)
    scoring_v2 = scan_data.get('scoring_v2', {})
    v2_context = ""
    if scoring_v2:
        v2_maturity = scoring_v2.get('org_maturity_label', 'Unknown')
        v2_readiness = scoring_v2.get('readiness_index', 0)
        v2_confidence = scoring_v2.get('confidence_percent', 0)
        v2_outreach = scoring_v2.get('outreach_angle_label', 'Unknown')
        v2_outreach_desc = scoring_v2.get('outreach_angle_description', '')
        v2_risk = scoring_v2.get('risk_level_label', 'Unknown')
        v2_clusters = scoring_v2.get('signal_clusters_detected', [])
        v2_sales_motion = scoring_v2.get('recommended_sales_motion', '')
        v2_primary_repo = scoring_v2.get('primary_repo_of_concern', '')

        # Build enriched signal age context
        v2_signals = scoring_v2.get('enriched_signals', [])
        signal_ages = []
        for sig in v2_signals[:10]:
            age = sig.get('age_in_days')
            if age is not None:
                signal_ages.append(f"  - {sig.get('signal_type', 'unknown')}: {age} days old (strength: {sig.get('decayed_strength', 0):.2f})")

        v2_context = f"""

## SCORING V2 INTELLIGENCE (use this data to enrich your analysis):
- Maturity Level: {v2_maturity}
- Readiness Index: {v2_readiness:.2f}/1.00
- Confidence: {v2_confidence:.0f}%
- Risk Level: {v2_risk}
- Recommended Outreach: {v2_outreach} — {v2_outreach_desc}
- Recommended Sales Motion: {v2_sales_motion}
- Primary Repo of Concern: {v2_primary_repo}
- Signal Clusters: {', '.join(v2_clusters) if v2_clusters else 'None'}
- Signal Ages:
{chr(10).join(signal_ages) if signal_ages else '  No age data available'}

IMPORTANT: Reference specific signals and their ages in your analysis. Mention the maturity level and readiness index. Use the recommended outreach angle to shape the cold email tone.
"""

    prompt = f"""
You are a SALES STRATEGIST for a Localization Platform. Your job is to help Business Development Reps (BDRs) understand technical findings and turn them into sales opportunities.

CRITICAL CONTEXT - THE "GOLDILOCKS ZONE":
Our ideal customer is a company that has JUST STARTED setting up i18n infrastructure but has NOT YET launched or localized a single word. This is the "Goldilocks Zone" - not too early (no infrastructure), not too late (already localized).

## Company: {company} ({org_name})
## Intent Score: {intent_score}/100
## Lead Status: {lead_status}
## Goldilocks Status: {goldilocks_status.upper()}
{v2_context}

## Technical Findings (You must translate these to BDR-friendly language):

1. RFC & Discussion Signal (THINKING Phase): {rfc_count} hits ({rfc_high} HIGH priority)
   - BDR Translation: "The team is DISCUSSING going international but hasn't started building yet"

2. Dependency Injection Signal (PREPARING Phase - GOLDILOCKS ZONE!): {dep_count} hits
   - Libraries Found: {', '.join(libraries_found) if libraries_found else 'None'}
   - BDR Translation: "The SHELVES are built but the BOOKS (translations) are MISSING"
   - This is our IDEAL customer!

3. Ghost Branch Signal (ACTIVE Phase): {ghost_count} hits
   - BDR Translation: "Developers are actively working on i18n in a side branch"

## Top Contributors (for Key Engineering Contacts):
{json.dumps(contributors_list, indent=2)}

## Signal Timeline (chronological activity):
{json.dumps(signal_timeline, indent=2)}

## Sample Evidence:
{json.dumps(sample_evidence, indent=2)}

## Full Signals Data:
{json.dumps(signals[:10], indent=2, default=str)}

---

Generate a JSON response with these fields. USE CLEAR, CONCISE, NON-TECHNICAL LANGUAGE (sentence case, no ALL CAPS):

1. "executive_summary": (2-3 sentences for BDRs)
   - Start with the Goldilocks status label
   - If status is "preparing": "Goldilocks zone — [Company] has installed i18n infrastructure but has zero translations. The infrastructure is ready but no content exists yet. This is the ideal time to reach out."
   - If status is "launched": "Too late — [Company] already has translation files. Low priority."
   - If status is "thinking": "Early stage — [Company] is discussing i18n but hasn't started. Worth nurturing."

2. "phase_assessment":
   - "phase": (Preparing/Thinking/Launched/None)
   - "confidence": (High/Medium/Low)
   - "bdr_explanation": Plain English explanation a non-technical person understands

3. "timing_window":
   - "urgency": (CALL NOW / Warm Lead / Nurture / Too Late)
   - "reasoning": Why this timing matters (1 sentence, bold language)

4. "key_findings": (list of objects, each with:)
   - "finding": Technical finding translated to BDR language
   - "significance": (critical/high/medium/low)
   - "sales_angle": How to use this in a conversation
   Example: {{"finding": "Found react-i18next — infrastructure is ready but no translations exist yet", "significance": "critical", "sales_angle": "The infrastructure is built but translations are missing. Ideal time to reach out."}}

5. "cold_email_draft":
   Generate a hyper-personalized cold email following these STRICT rules:
   
{_get_cold_email_instructions()}

   Return as: {{"subject": "...", "body": "..."}}

6. "conversation_starters": (list of 3 questions BDRs can ask)
   - Non-technical, open-ended questions

7. "risk_factors": (list of concerns)
   - What could go wrong with this lead

8. "opportunity_score": (1-10)
   - 9-10 if GOLDILOCKS ZONE (preparing)
   - 4-6 if THINKING
   - 1-2 if LAUNCHED (too late)

9. "key_engineering_contacts": (list of 3-5 recommended contacts from the contributors)
   - Select contacts most likely to be decision-makers or influencers for i18n
   - Prioritize: Engineering Managers, Frontend/Platform leads, high-volume committers
   - Each contact should have:
     - "login": GitHub username
     - "name": Real name
     - "role_inference": Inferred role based on bio/company/contributions (e.g., "Likely Frontend Lead", "High-volume Committer")
     - "outreach_reason": Why this person is worth contacting
   - If no contributors data available, return empty list

10. "engineering_velocity": (list of 3-5 bullet points showing progression)
   - Generate a chronological narrative from the signal timeline data
   - Show the progression of i18n work over time
   - Format: "Mon YYYY: Brief description of activity"
   - Example: ["Oct 2025: React-Intl library added to package.json", "Nov 2025: i18n RFC discussion opened", "Dec 2025: WIP i18n branch created"]
   - If no timeline data available, return empty list
"""

    return prompt


def _parse_ai_response(response_text: str, scan_data: dict) -> dict:
    """Parse AI response (OpenAI or Gemini) into structured analysis."""
    try:
        # Clean up response text
        text = response_text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

        analysis = json.loads(text)

        phase = analysis.get('phase_assessment', {})
        analysis['pain_point_analysis'] = phase.get('bdr_explanation', phase.get('conclusion', ''))
        SYSTEM_MESSAGES = [
        'Phase analysis complete.', 'Phase analysis complete', 
        'Phase analysis is complete.', 'Phase analysis is complete',
        'Analysis complete.', 'Analysis complete',
        'Processing done.', 'Processing done',
        'Timing analysis complete.', 'Timing analysis complete',
        'N/A for pre-launch detection', 'N/A for pre-launch detection.',
        'Scan complete.', 'Scan complete',
        'Data collection complete.', 'Data collection complete',
        'Report generated.', 'Report generated',
        'No pain signals detected.', 'No pain signals detected',
    ]
        pain_val = analysis.get('pain_point_analysis', '').strip()
        if pain_val in SYSTEM_MESSAGES or pain_val.lower().rstrip('.') in [m.lower().rstrip('.') for m in SYSTEM_MESSAGES] or any(pain_val.lower().startswith(m.lower().rstrip('.')) for m in SYSTEM_MESSAGES):
            analysis['pain_point_analysis'] = ''
        analysis['tech_stack_hook'] = analysis.get('conversation_angle', 'Discuss their i18n journey.')

        timing = analysis.get('timing_window', {})
        urgency = timing.get('urgency', '')
        analysis['semantic_analysis'] = {
            'severity': 'major' if urgency in ('CALL NOW', 'Strike Now') else 'minor',
            'primary_pain_category': 'pre_launch_intent',
            'description': timing.get('reasoning', 'Timing analysis complete.')
        }

        if 'cold_email_draft' in analysis:
            analysis['email_draft'] = analysis['cold_email_draft']

        if 'key_talking_points' in analysis and 'key_findings' not in analysis:
            analysis['key_findings'] = [
                {'finding': point, 'significance': 'high', 'sales_angle': 'Use in outreach'}
                for point in analysis['key_talking_points']
            ]

        analysis.setdefault('compliance_risk', {'level': 'low', 'description': 'N/A for pre-launch detection'})
        analysis.setdefault('top_prospects', [])
        analysis.setdefault('outreach_suggestions', [])
        analysis.setdefault('next_steps', ['Review signals', 'Draft personalized outreach', 'Research decision makers'])

        analysis['_source'] = 'ai'

        return analysis

    except json.JSONDecodeError:
        # Fallback if JSON parsing fails
        return _generate_fallback_analysis(scan_data)


def _generate_fallback_analysis(scan_data: dict) -> dict:
    """Generate rule-based sales intelligence when AI is unavailable - GOLDILOCKS ZONE FOCUSED."""
    # Title case the company name for cleaner output
    raw_company = scan_data.get('company_name', 'Unknown')
    company = raw_company.title() if raw_company else 'Unknown'
    org_name = scan_data.get('org_name', '')
    signals = scan_data.get('signals', [])
    signal_summary = scan_data.get('signal_summary', {})
    intent_score = scan_data.get('intent_score', 0)
    goldilocks_status = scan_data.get('goldilocks_status', 'none')
    lead_status = scan_data.get('lead_status', 'Unknown')

    # Extract counts
    rfc_hits = signal_summary.get('rfc_discussion', {}).get('hits', [])
    rfc_count = len(rfc_hits)
    rfc_high = signal_summary.get('rfc_discussion', {}).get('high_priority_count') or 0
    dep_hits = signal_summary.get('dependency_injection', {}).get('hits', [])
    dep_count = len(dep_hits)
    ghost_hits = signal_summary.get('ghost_branch', {}).get('hits', [])
    ghost_count = len(ghost_hits)

    # Check for "already launched" signals
    already_launched = [s for s in signals if s.get('type') == 'already_launched']

    # Determine dominant phase based on Goldilocks Zone model
    if already_launched:
        dominant_phase = 'Launched'
        phase_evidence = 'Locale folders already exist - they have launched i18n'
        goldilocks_status = 'launched'
    elif dep_count > 0 and goldilocks_status == 'preparing':
        dominant_phase = 'Preparing'
        phase_evidence = 'Goldilocks zone: i18n libraries installed but no locale folders'
    elif ghost_count > 0:
        dominant_phase = 'Active'
        phase_evidence = 'WIP branches with i18n work'
    elif rfc_count > 0:
        dominant_phase = 'Thinking'
        phase_evidence = 'Strategic discussions about i18n'
    else:
        dominant_phase = 'None'
        phase_evidence = 'No pre-launch signals detected'

    # Determine opportunity score based on Goldilocks Zone
    if goldilocks_status == 'preparing':
        opportunity_score = 10  # GOLDILOCKS ZONE = MAX SCORE
    elif goldilocks_status == 'launched':
        opportunity_score = 2  # Too late
    elif goldilocks_status == 'thinking':
        opportunity_score = 5  # Warm lead
    else:
        opportunity_score = min(10, intent_score // 10) if intent_score > 0 else 3

    # Determine timing based on Goldilocks Zone
    if goldilocks_status == 'preparing':
        timing = 'CALL NOW'
        timing_reason = 'Goldilocks zone — they built the infrastructure but have no translations. Call immediately.'
    elif goldilocks_status == 'launched':
        timing = 'Too Late'
        timing_reason = 'They already have locale folders with translations. Low priority — they have a working system.'
    elif ghost_count > 0:
        timing = 'CALL NOW'
        timing_reason = 'Active WIP work means they are building right now — influence their decisions.'
    elif rfc_high > 0:
        timing = 'Warm Lead'
        timing_reason = 'High-priority RFC indicates executive attention. A decision is being made.'
    elif rfc_count > 0:
        timing = 'Warm Lead'
        timing_reason = 'Discussions happening but no concrete action yet.'
    else:
        timing = 'Nurture'
        timing_reason = 'No strong signals - monitor for future activity.'

    # Build executive summary - BOLD, PUNCHY language for BDRs
    if goldilocks_status == 'preparing':
        libs = ', '.join(dep_hits[0].get('libraries_found', ['i18n library'])) if dep_hits else 'i18n library'
        executive_summary = f"Goldilocks zone — {company} has installed {libs} but has zero translations. The infrastructure is ready but no content exists yet. This is the ideal time to reach out."
    elif goldilocks_status == 'launched':
        folders = ', '.join(already_launched[0].get('locale_folders_found', ['locales'])) if already_launched else 'locales'
        executive_summary = f"Too late — {company} already has translation files in /{folders}/. They have a working i18n system. Low priority."
    elif goldilocks_status == 'thinking':
        executive_summary = f"Early stage — {company} is discussing internationalization but hasn't started building. Worth nurturing."
    else:
        executive_summary = f"Cold lead — No significant i18n signals detected for {company}. Consider for future outreach."

    # Build email draft based on Goldilocks status (following Cold Outreach Skill rules)
    # Uses Apollo.io dynamic variables: {{first_name}}, {{company}}, {{sender_first_name}}
    # Best practices: <100 words, no {{first_name}} in subject, personalized hook + value + soft CTA
    if goldilocks_status == 'preparing':
        lib = dep_hits[0].get('libraries_found', ['i18n library'])[0] if dep_hits else 'i18n library'
        email_subject = f"{lib} in {org_name} / {{{{company}}}}"
        email_body = (
            "Hey {{first_name}},\n\n"
            f"Noticed you added `{lib}` but no locale files yet.\n\n"
            "This is usually when manual JSON wrangling starts. We built Phrase to automate that via GitHub Sync—your team never touches translation files.\n\n"
            "Worth a look?\n\n"
            "{{sender_first_name}}"
        )
    elif goldilocks_status == 'launched':
        email_subject = f"Localization at {{{{company}}}}"
        email_body = (
            "Hey {{first_name}},\n\n"
            f"Did some recon on {org_name}'s GitHub—saw you have a mature localization setup.\n\n"
            "Curious if the team feels any pain around file syncs or manual handoffs? We help teams automate that friction via CI/CD integrations.\n\n"
            "Worth a quick chat?\n\n"
            "{{sender_first_name}}"
        )
    elif dominant_phase == 'Active':
        branch = ghost_hits[0].get('branch_name', 'i18n branch') if ghost_hits else 'i18n branch'
        email_subject = f"Your {branch} work"
        email_body = (
            "Hey {{first_name}},\n\n"
            f"Noticed your team's working on i18n in the `{branch}` branch.\n\n"
            "Teams often hit complexity here with key management and automation. Phrase handles that infrastructure so devs can focus on shipping.\n\n"
            "Open to seeing how we fit into your CI/CD?\n\n"
            "{{sender_first_name}}"
        )
    elif dominant_phase == 'Thinking':
        keyword = rfc_hits[0].get('keywords_matched', ['i18n'])[0] if rfc_hits else 'internationalization'
        email_subject = f"Re: {keyword} at {{{{company}}}}"
        email_body = (
            "Hey {{first_name}},\n\n"
            f"Came across your team's `{keyword}` discussion.\n\n"
            "We've helped teams automate the dev-to-translator handoff before the first file is created—saves massive technical debt.\n\n"
            "Worth a chat?\n\n"
            "{{sender_first_name}}"
        )
    else:
        email_subject = f"Localization at {{{{company}}}}"
        email_body = (
            "Hey {{first_name}},\n\n"
            f"Been researching {org_name}'s tech stack—you're at a good stage for localization automation.\n\n"
            "Most teams wait until they have a file management mess. Setting up GitHub Sync now prevents that entirely.\n\n"
            "Open to a quick tactical chat?\n\n"
            "{{sender_first_name}}"
        )

    # Build key findings - BDR-friendly language
    key_findings = []

    if goldilocks_status == 'preparing':
        libs = ', '.join(dep_hits[0].get('libraries_found', [])) if dep_hits else 'i18n libraries'
        bdr_explanation = dep_hits[0].get('bdr_summary', '') if dep_hits else ''
        key_findings.append({
            'finding': f"Found {libs} — infrastructure is ready but no translations exist yet",
            'significance': 'critical',
            'sales_angle': 'The infrastructure is built but translations are missing. Ideal time to reach out.',
            'bdr_explanation': bdr_explanation or Config.BDR_TRANSLATIONS.get('locale_folder_missing', '')
        })

    if goldilocks_status == 'launched':
        folders = already_launched[0].get('locale_folders_found', ['locales']) if already_launched else ['locales']
        key_findings.append({
            'finding': f"Found existing locale folders: /{', '.join(folders)}/",
            'significance': 'low',
            'sales_angle': 'They already have translations. Too late for the greenfield opportunity.',
            'bdr_explanation': Config.BDR_TRANSLATIONS.get('locale_folder_exists', '')
        })

    if rfc_high > 0:
        key_findings.append({
            'finding': f'{rfc_high} HIGH priority RFCs/Proposals about i18n',
            'significance': 'high',
            'sales_angle': 'Executive attention - decision is being made at leadership level'
        })

    if ghost_count > 0:
        key_findings.append({
            'finding': f'{ghost_count} WIP i18n branches/PRs detected',
            'significance': 'high',
            'sales_angle': 'Developers are actively working on this right now'
        })

    # Build outreach suggestions
    outreach_suggestions = []

    if goldilocks_status == 'preparing':
        libs = dep_hits[0].get('libraries_found', ['unknown']) if dep_hits else ['unknown']
        outreach_suggestions.append({
            'why_account': f"Goldilocks zone: Found {', '.join(libs)} but no translations",
            'why_now': "Infrastructure is ready, translations are missing — ideal timing",
            'who_to_reach': 'Frontend/Platform engineering lead',
            'message_hook': f"Noticed you added {libs[0]} but no locales folder - want to share best practices for getting started?"
        })

    if ghost_count > 0:
        outreach_suggestions.append({
            'why_account': f"Active i18n work in {ghost_hits[0].get('branch_name', 'feature branch') if ghost_hits else 'feature branch'}",
            'why_now': 'In the middle of implementation - can influence decisions before launch',
            'who_to_reach': 'Developer working on the branch',
            'message_hook': 'Saw your i18n branch - hitting any complexity we can help with?'
        })

    if rfc_count > 0:
        outreach_suggestions.append({
            'why_account': f"Strategic discussion about i18n happening ({rfc_count} issues/discussions)",
            'why_now': 'Decision is being made - can provide expert input',
            'who_to_reach': 'Author of the RFC/discussion',
            'message_hook': 'Saw your i18n strategy discussion - happy to share learnings from similar projects'
        })

    # Risk factors
    risk_factors = []

    if goldilocks_status == 'launched':
        risk_factors.append('Company already has translations in place - low switching likelihood')

    if not signals:
        risk_factors.append('No pre-launch signals detected - may not be a current priority')

    if dominant_phase == 'Thinking' and rfc_high == 0:
        risk_factors.append('Early stage discussions only - may take time to move forward')

    if not risk_factors:
        risk_factors.append('Standard sales cycle considerations apply')

    # Conversation starters - Non-technical
    conversation_starters = []

    if goldilocks_status == 'preparing':
        conversation_starters.append("I noticed you're setting up for multiple languages - what markets are you planning to launch in?")
        conversation_starters.append("How's the i18n setup going? Any roadblocks we could help with?")

    if ghost_count > 0:
        conversation_starters.append("Saw some WIP i18n work in your repo - how's the implementation going?")

    if rfc_count > 0:
        conversation_starters.append("I came across your team's i18n discussion - what approach are you leaning toward?")

    if not conversation_starters:
        conversation_starters.append(f"Is internationalization on {company}'s roadmap?")

    # Build key engineering contacts from contributor data
    contributors = scan_data.get('contributors', {})
    key_engineering_contacts = []
    for login, data in list(contributors.items())[:5]:
        role_inference = "High-volume Committer"
        if data.get('bio'):
            bio_lower = data['bio'].lower()
            if 'manager' in bio_lower or 'lead' in bio_lower or 'director' in bio_lower:
                role_inference = "Engineering Lead/Manager"
            elif 'frontend' in bio_lower or 'ui' in bio_lower:
                role_inference = "Frontend Engineer"
            elif 'platform' in bio_lower or 'infrastructure' in bio_lower:
                role_inference = "Platform Engineer"
        key_engineering_contacts.append({
            'login': login,
            'name': data.get('name', login),
            'role_inference': role_inference,
            'outreach_reason': f"Top contributor with {data.get('contributions', 0)} commits"
        })

    # Build engineering velocity timeline from signals
    engineering_velocity = []
    for signal in signals:
        timestamp = signal.get('created_at') or signal.get('pushed_at') or signal.get('timestamp')
        if timestamp:
            try:
                date_str = timestamp[:10] if isinstance(timestamp, str) else str(timestamp)[:10]
                from datetime import datetime as dt
                date_obj = dt.fromisoformat(date_str)
                month_year = date_obj.strftime('%b %Y')
                signal_type = signal.get('type', signal.get('Signal', 'activity'))
                description = signal.get('Evidence', signal.get('title', 'i18n activity detected'))[:50]
                engineering_velocity.append(f"{month_year}: {description}")
            except (ValueError, TypeError):
                continue
    # Remove duplicates and limit to 5
    engineering_velocity = list(dict.fromkeys(engineering_velocity))[:5]

    return {
        'executive_summary': executive_summary,
        'pain_point_analysis': f"Company is in {dominant_phase} phase: {phase_evidence}",
        'tech_stack_hook': f"Based on Goldilocks Zone analysis, {company} shows {intent_score}/100 intent score.",
        'semantic_analysis': {
            'severity': 'critical' if goldilocks_status == 'preparing' else 'major' if timing == 'CALL NOW' else 'minor',
            'primary_pain_category': 'goldilocks_zone' if goldilocks_status == 'preparing' else 'pre_launch_intent',
            'description': f"{dominant_phase} phase detected. {timing_reason}"
        },
        'compliance_risk': {
            'level': 'low',
            'description': 'N/A for pre-launch detection'
        },
        'forensic_evidence': f'Goldilocks Zone Detection: {goldilocks_status.upper()}',
        'top_prospects': [],
        'opportunity_score': opportunity_score,
        'opportunity_type': goldilocks_status,
        'localization_maturity': 'launched' if goldilocks_status == 'launched' else 'emerging',
        'email_draft': {
            'subject': email_subject,
            'body': email_body
        },
        'key_findings': key_findings,
        'outreach_suggestions': outreach_suggestions,
        'recommended_approach': _get_recommended_approach_goldilocks(goldilocks_status),
        'conversation_starters': conversation_starters[:3],
        'risk_factors': risk_factors,
        'next_steps': _get_next_steps_goldilocks(goldilocks_status),
        'phase_assessment': {
            'phase': dominant_phase,
            'confidence': 'High' if goldilocks_status == 'preparing' else 'Medium' if intent_score > 25 else 'Low',
            'evidence': phase_evidence,
            'bdr_explanation': executive_summary,
        },
        'timing_window': {
            'urgency': timing,
            'reasoning': timing_reason,
        },
        'goldilocks_status': goldilocks_status,
        'lead_status': lead_status,
        'key_engineering_contacts': key_engineering_contacts,
        'engineering_velocity': engineering_velocity,
        '_source': 'fallback'
    }


def _get_recommended_approach(phase: str) -> str:
    """Get strategic recommendation based on phase."""
    approaches = {
        'Preparing': "IMPLEMENTATION PARTNER: They've bought tools but need help. Position as implementation experts. Offer architecture review or quick-start guidance.",
        'Active': "ACCELERATOR: They're already building. Position on speed and avoiding common pitfalls. Offer to review their current approach.",
        'Thinking': "STRATEGIC ADVISOR: They're exploring options. Position as thought partner. Share case studies and best practices to influence their decision.",
        'None': "EDUCATION FIRST: No active signals. Focus on building awareness and relationship for future opportunities.",
    }
    return approaches.get(phase, approaches['None'])


def _get_recommended_approach_goldilocks(status: str) -> str:
    """Get strategic recommendation based on Goldilocks Zone status - BDR-friendly language."""
    approaches = {
        'preparing': "Goldilocks zone — call immediately. They have the infrastructure but zero translations. Position as the partner who helps them launch globally. Offer a free architecture review.",
        'thinking': "Warm nurture — they're researching but haven't started building. Share educational content and case studies. Stay top of mind for when they're ready.",
        'launched': "Low priority — they already have translations. Only worth pursuing if they're unhappy with their current solution. Ask about pain points.",
        'none': "Cold outreach — no signals detected. Generic outreach only if you have bandwidth. Focus on hotter leads.",
    }
    return approaches.get(status, approaches['none'])


def _get_next_steps(phase: str) -> list:
    """Get actionable next steps based on phase."""
    steps = ['Review detected signals in detail', 'Research key stakeholders on LinkedIn']

    if phase == 'Preparing':
        steps.extend([
            'Prepare implementation quickstart offer',
            'Draft email referencing specific libraries they added'
        ])
    elif phase == 'Active':
        steps.extend([
            'Review their WIP branch for complexity insights',
            'Prepare demo focused on their specific pain points'
        ])
    elif phase == 'Thinking':
        steps.extend([
            'Prepare case study relevant to their industry',
            'Draft response to their RFC/discussion'
        ])
    else:
        steps.extend([
            'Add to nurture campaign',
            'Set reminder to check signals in 30 days'
        ])

    return steps


def _get_next_steps_goldilocks(status: str) -> list:
    """Get actionable next steps based on Goldilocks Zone status - BDR-friendly."""
    if status == 'preparing':
        return [
            'Send personalized email today referencing the specific library found',
            'Find the engineering lead on LinkedIn and connect',
            'Prepare a quick demo showing how we help teams go from "library installed" to "live in production"',
            'Check if they have any open roles for localization — indicates urgency'
        ]
    elif status == 'thinking':
        return [
            'Add to nurture sequence with educational content',
            'Find the RFC/discussion author and follow their activity',
            'Prepare a case study relevant to their industry',
            'Set reminder to re-scan in 30 days for progression'
        ]
    elif status == 'launched':
        return [
            'Low priority — only pursue if you have extra bandwidth',
            'Look for signs of dissatisfaction (complaints in issues/discussions)',
            'Consider for reference/case study if they are a known brand',
            'Move on to higher-priority leads'
        ]
    else:
        return [
            'Add to long-term nurture list',
            'Set reminder to re-scan in 60 days',
            'Focus energy on hotter leads',
            'Consider generic brand awareness outreach if time permits'
        ]


def _sse_log(message: str) -> str:
    """Format a log message for SSE."""
    return f"data: LOG:{message}\n\n"


def _sse_data(event_type: str, data: dict) -> str:
    """Format a data payload for SSE."""
    return f"data: {event_type}:{json.dumps(data)}\n\n"


def generate_deep_dive(scan_data: dict, ai_analysis: dict) -> dict:
    """
    Generate a Deep Dive analysis using Gemini AI.

    This provides:
    1. Important timeline events from the company's i18n journey
    2. Key insights from their code repo related to i18n
    3. A 3-4 sentence narrative summarizing why this is a good account

    Args:
        scan_data: The complete scan results dictionary
        ai_analysis: The existing AI analysis dictionary

    Returns:
        Dictionary with deep_dive analysis results
    """
    if not GENAI_AVAILABLE:
        return _generate_deep_dive_fallback(scan_data, ai_analysis)

    if not Config.GEMINI_API_KEY:
        return _generate_deep_dive_fallback(scan_data, ai_analysis)

    # Build the Deep Dive prompt
    prompt = _build_deep_dive_prompt(scan_data, ai_analysis)

    try:
        client = genai.Client(api_key=Config.GEMINI_API_KEY)

        response = client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=prompt
        )

        return _parse_deep_dive_response(response.text, scan_data, ai_analysis)

    except Exception as e:
        print(f"[AI] Deep Dive error: {str(e)}")
        return _generate_deep_dive_fallback(scan_data, ai_analysis)


def _build_deep_dive_prompt(scan_data: dict, ai_analysis: dict) -> str:
    """Build the prompt for Deep Dive analysis."""
    raw_company = scan_data.get('company_name', 'Unknown')
    company = raw_company.title() if raw_company else 'Unknown'
    org_name = scan_data.get('org_name', scan_data.get('org_login', ''))
    signals = scan_data.get('signals', [])
    signal_summary = scan_data.get('signal_summary', {})
    goldilocks_status = scan_data.get('goldilocks_status', 'unknown')
    intent_score = scan_data.get('intent_score', 0)

    # Get existing analysis data
    executive_summary = ai_analysis.get('executive_summary', '')
    key_findings = ai_analysis.get('key_findings', [])
    engineering_velocity = ai_analysis.get('engineering_velocity', [])
    key_contacts = ai_analysis.get('key_engineering_contacts', [])

    # Extract detailed signal data
    rfc_hits = signal_summary.get('rfc_discussion', {}).get('hits', [])
    dep_hits = signal_summary.get('dependency_injection', {}).get('hits', [])
    ghost_hits = signal_summary.get('ghost_branch', {}).get('hits', [])

    # Build timeline data from signals
    timeline_data = []
    for signal in signals:
        timestamp = signal.get('created_at') or signal.get('pushed_at') or signal.get('timestamp')
        if timestamp:
            timeline_data.append({
                'date': timestamp[:10] if isinstance(timestamp, str) else str(timestamp)[:10],
                'type': signal.get('type', 'unknown'),
                'description': signal.get('Evidence', signal.get('title', signal.get('Signal', '')))[:150],
                'repo': signal.get('repo', ''),
                'priority': signal.get('priority', 'MEDIUM')
            })

    # Get libraries found
    libraries_found = []
    for hit in dep_hits:
        if isinstance(hit, dict):
            libraries_found.extend(hit.get('libraries_found', []))

    prompt = f"""
You are a SENIOR SALES INTELLIGENCE ANALYST creating a "Deep Dive" report for Business Development Representatives (BDRs).

Your job is to synthesize technical GitHub data into a compelling, easy-to-understand executive briefing that helps BDRs understand WHY this company is worth their time and HOW to approach them.

## Company: {company} (GitHub: {org_name})
## Current Status: {goldilocks_status.upper()}
## Intent Score: {intent_score}/100

## Existing Analysis Summary:
{executive_summary}

## Signal Data:
- RFC/Discussion Signals: {len(rfc_hits)} hits
- Dependency Injection Signals: {len(dep_hits)} hits
- Ghost Branch Signals: {len(ghost_hits)} hits
- Libraries Found: {', '.join(libraries_found) if libraries_found else 'None detected'}

## Timeline Data (chronological activity):
{json.dumps(timeline_data[:15], indent=2)}

## Key Findings:
{json.dumps(key_findings, indent=2)}

## Engineering Velocity:
{json.dumps(engineering_velocity, indent=2)}

## Key Contacts:
{json.dumps(key_contacts, indent=2)}

---

Generate a JSON response with EXACTLY these three fields:

1. "timeline_events": (list of 3-5 important events)
   - Each event should be an object with:
     - "date": Month/Year or date range (e.g., "Nov 2025", "Q4 2025")
     - "event": What happened (1 sentence, BDR-friendly language)
     - "significance": Why this matters for sales (1 sentence)
   - Focus on i18n-related milestones: library installations, RFC discussions, branch creation, etc.
   - Order chronologically from oldest to newest
   - If no timeline data available, create logical milestones based on the signals found

2. "code_insights": (list of 3-4 key insights)
   - Each insight should be a string (1-2 sentences)
   - Focus on ACTIONABLE intelligence from their codebase
   - Translate technical findings into sales opportunities
   - Examples:
     - "They've installed react-i18next but haven't created locale folders yet - they're ready to start but need help with the workflow."
     - "Their main product repo has 15K stars - this is a high-profile account that could become a case study."
     - "Found 3 different i18n discussions in the last 60 days - the team is actively researching solutions."

3. "outreach_narrative": (string, EXACTLY 3-4 sentences)
   - Write a compelling paragraph that a BDR can use in their notes or share with their manager
   - Structure: [Why this account] + [What we found] + [Why now] + [Recommended approach]
   - Use confident, persuasive language but NOT salesy
   - Include specific details from the scan (library names, repo names, etc.)
   - End with a clear call to action recommendation

IMPORTANT:
- Use PLAIN ENGLISH that a non-technical sales person can understand
- Be specific - reference actual library names, repo names, dates when available
- Focus on the "so what?" - why should a BDR care about each finding?
- Keep the narrative punchy and actionable
"""

    return prompt


def _parse_deep_dive_response(response_text: str, scan_data: dict, ai_analysis: dict) -> dict:
    """Parse Gemini Deep Dive response."""
    try:
        # Clean up response text
        text = response_text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

        result = json.loads(text)

        # Validate required fields
        if 'timeline_events' not in result:
            result['timeline_events'] = []
        if 'code_insights' not in result:
            result['code_insights'] = []
        if 'outreach_narrative' not in result:
            result['outreach_narrative'] = ''

        result['_source'] = 'gemini'
        result['_model'] = Config.GEMINI_MODEL

        return result

    except json.JSONDecodeError:
        return _generate_deep_dive_fallback(scan_data, ai_analysis)


def _generate_deep_dive_fallback(scan_data: dict, ai_analysis: dict) -> dict:
    """Generate fallback Deep Dive when AI is unavailable."""
    raw_company = scan_data.get('company_name', 'Unknown')
    company = raw_company.title() if raw_company else 'Unknown'
    org_name = scan_data.get('org_login', '')
    signals = scan_data.get('signals', [])
    signal_summary = scan_data.get('signal_summary', {})
    goldilocks_status = scan_data.get('goldilocks_status', 'none')
    intent_score = scan_data.get('intent_score', 0)

    # Extract signal data
    rfc_hits = signal_summary.get('rfc_discussion', {}).get('hits', [])
    dep_hits = signal_summary.get('dependency_injection', {}).get('hits', [])
    ghost_hits = signal_summary.get('ghost_branch', {}).get('hits', [])

    # Get libraries found
    libraries_found = []
    for hit in dep_hits:
        if isinstance(hit, dict):
            libraries_found.extend(hit.get('libraries_found', []))

    # Build timeline events from signals
    timeline_events = []
    for signal in signals[:5]:
        timestamp = signal.get('created_at') or signal.get('pushed_at') or signal.get('timestamp')
        if timestamp:
            try:
                date_str = timestamp[:10] if isinstance(timestamp, str) else str(timestamp)[:10]
                from datetime import datetime as dt
                date_obj = dt.fromisoformat(date_str)
                month_year = date_obj.strftime('%b %Y')

                signal_type = signal.get('type', 'activity')
                evidence = signal.get('Evidence', signal.get('title', 'i18n activity'))[:100]

                significance = "Indicates active i18n consideration"
                if signal_type == 'dependency_injection':
                    significance = "Infrastructure being set up - perfect timing for outreach"
                elif signal_type == 'rfc_discussion':
                    significance = "Team is researching solutions - opportunity to influence"
                elif signal_type == 'ghost_branch':
                    significance = "Active development - can help accelerate their work"

                timeline_events.append({
                    'date': month_year,
                    'event': evidence,
                    'significance': significance
                })
            except (ValueError, TypeError):
                continue

    # Build code insights
    code_insights = []

    if goldilocks_status == 'preparing':
        libs = ', '.join(libraries_found[:3]) if libraries_found else 'i18n libraries'
        code_insights.append(f"Goldilocks zone: {company} has installed {libs} but has zero translation files — they're ready to start but need a workflow solution.")

    if len(rfc_hits) > 0:
        code_insights.append(f"Found {len(rfc_hits)} i18n-related discussion(s) - the team is actively researching internationalization strategies.")

    if len(ghost_hits) > 0:
        code_insights.append(f"Detected {len(ghost_hits)} work-in-progress i18n branch(es) - developers are actively building localization features.")

    total_stars = scan_data.get('total_stars', 0)
    if total_stars > 1000:
        code_insights.append(f"Their repositories have {total_stars:,} total stars - this is a high-profile account that could become a valuable case study.")

    if not code_insights:
        code_insights.append(f"Monitoring {company}'s GitHub activity for emerging i18n signals.")

    # Build outreach narrative
    if goldilocks_status == 'preparing':
        libs = libraries_found[0] if libraries_found else 'i18n libraries'
        narrative = f"{company} represents a perfect-timing opportunity. Our scan detected {libs} installed in their codebase with zero translation files created yet - they've built the infrastructure but haven't started the localization work. This is the ideal moment to engage before they develop manual workflows. Recommend immediate outreach referencing the specific library found and offering to help them set up an automated translation pipeline."
    elif goldilocks_status == 'thinking':
        narrative = f"{company} is in the early research phase for internationalization. We found active discussions and RFCs about i18n strategy, indicating the team is evaluating options. This is a nurture opportunity - they're not ready to buy today but are building their requirements. Recommend sharing educational content and positioning as a thought partner."
    elif goldilocks_status == 'launched':
        narrative = f"{company} already has localization infrastructure in place with existing translation files. While they've already launched i18n, there may be pain points with their current workflow. Lower priority than greenfield opportunities, but worth exploring if they're experiencing scaling challenges or dissatisfaction with their current solution."
    else:
        narrative = f"{company} shows limited i18n signals at this time. The account should be monitored for future activity. Recommend adding to a nurture sequence and re-scanning in 30-60 days to check for new signals."

    return {
        'timeline_events': timeline_events,
        'code_insights': code_insights,
        'outreach_narrative': narrative,
        '_source': 'fallback'
    }
