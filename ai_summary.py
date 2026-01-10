"""
AI Summary Module for 3-Signal Internationalization Intent Scanner.

Generates actionable sales intelligence from pre-launch i18n signals:
- RFC & Discussion (Thinking Phase)
- Dependency Injection (Preparing Phase)
- Ghost Branch (Active Phase)
"""
import json
from typing import Generator
from config import Config

try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


def generate_analysis(scan_data: dict) -> Generator[str, None, dict]:
    """
    Generate AI-powered sales intelligence from 3-Signal scan data.

    Args:
        scan_data: The complete scan results dictionary.

    Yields:
        SSE-formatted progress messages.

    Returns:
        Analysis result dictionary with sales assets.
    """
    yield _sse_log("Initializing AI Sales Intelligence Engine...")

    if not GENAI_AVAILABLE:
        yield _sse_log("Warning: google-genai not installed, using fallback analysis")
        analysis = _generate_fallback_analysis(scan_data)
        yield _sse_data('ANALYSIS_COMPLETE', analysis)
        return analysis

    if not Config.GEMINI_API_KEY:
        yield _sse_log("Warning: GEMINI_API_KEY not configured, using fallback analysis")
        analysis = _generate_fallback_analysis(scan_data)
        yield _sse_data('ANALYSIS_COMPLETE', analysis)
        return analysis

    yield _sse_log("Preparing 3-Signal data for Gemini...")

    # Build prompt
    prompt = _build_sales_intelligence_prompt(scan_data)

    yield _sse_log("Sending to Gemini 2.5 Flash...")

    try:
        client = genai.Client(api_key=Config.GEMINI_API_KEY)

        response = client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=prompt
        )

        yield _sse_log("Processing AI response...")

        analysis = _parse_gemini_response(response.text, scan_data)

        yield _sse_log("✅ AI Sales Intelligence Complete")
        yield _sse_data('ANALYSIS_COMPLETE', analysis)

        return analysis

    except Exception as e:
        yield _sse_log(f"AI analysis error: {str(e)}")
        yield _sse_log("Falling back to rule-based analysis...")
        analysis = _generate_fallback_analysis(scan_data)
        yield _sse_data('ANALYSIS_COMPLETE', analysis)
        return analysis


def _build_sales_intelligence_prompt(scan_data: dict) -> str:
    """Build the sales intelligence prompt for Goldilocks Zone detection."""
    company = scan_data.get('company_name', 'Unknown')
    org_name = scan_data.get('org_name', scan_data.get('org_login', ''))
    signals = scan_data.get('signals', [])
    signal_summary = scan_data.get('signal_summary', {})
    intent_score = scan_data.get('intent_score', 0)
    goldilocks_status = scan_data.get('goldilocks_status', 'unknown')
    lead_status = scan_data.get('lead_status', 'Unknown')

    # Extract signal details
    rfc_count = signal_summary.get('rfc_discussion', {}).get('count', 0)
    rfc_high = signal_summary.get('rfc_discussion', {}).get('high_priority_count', 0)
    dep_count = signal_summary.get('dependency_injection', {}).get('count', 0)
    ghost_count = signal_summary.get('ghost_branch', {}).get('count', 0)

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

    prompt = f"""
You are a SALES STRATEGIST for a Localization Platform. Your job is to help Business Development Reps (BDRs) understand technical findings and turn them into sales opportunities.

CRITICAL CONTEXT - THE "GOLDILOCKS ZONE":
Our ideal customer is a company that has JUST STARTED setting up i18n infrastructure but has NOT YET launched or localized a single word. This is the "Goldilocks Zone" - not too early (no infrastructure), not too late (already localized).

## Company: {company} ({org_name})
## Intent Score: {intent_score}/100
## Lead Status: {lead_status}
## Goldilocks Status: {goldilocks_status.upper()}

## Technical Findings (You must translate these to BDR-friendly language):

1. RFC & Discussion Signal (THINKING Phase): {rfc_count} hits ({rfc_high} HIGH priority)
   - BDR Translation: "The team is DISCUSSING going international but hasn't started building yet"

2. Dependency Injection Signal (PREPARING Phase - GOLDILOCKS ZONE!): {dep_count} hits
   - Libraries Found: {', '.join(libraries_found) if libraries_found else 'None'}
   - BDR Translation: "The SHELVES are built but the BOOKS (translations) are MISSING"
   - This is our IDEAL customer!

3. Ghost Branch Signal (ACTIVE Phase): {ghost_count} hits
   - BDR Translation: "Developers are actively working on i18n in a side branch"

## Sample Evidence:
{json.dumps(sample_evidence, indent=2)}

## Full Signals Data:
{json.dumps(signals[:10], indent=2, default=str)}

---

Generate a JSON response with these fields. USE BOLD, PUNCHY, NON-TECHNICAL LANGUAGE:

1. "executive_summary": (2-3 sentences for BDRs)
   - Start with the Goldilocks status in CAPS
   - If status is "preparing": "GOLDILOCKS ZONE DETECTED! [Company] has installed i18n infrastructure but has ZERO translations. The shelving is built, books are missing. PERFECT time to call."
   - If status is "launched": "TOO LATE - [Company] already has translation files. Low priority."
   - If status is "thinking": "EARLY STAGE - [Company] is discussing i18n but hasn't started. Worth nurturing."

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
   Example: {{"finding": "Found react-i18next - Infrastructure is READY but no translations exist yet", "significance": "critical", "sales_angle": "They built the car but have no gas. Offer to fill the tank."}}

5. "cold_email_draft":
   - "subject": Short, punchy, references their specific situation
   - "body": 3 sentences max. Reference what you found. Non-technical.

6. "conversation_starters": (list of 3 questions BDRs can ask)
   - Non-technical, open-ended questions

7. "risk_factors": (list of concerns)
   - What could go wrong with this lead

8. "opportunity_score": (1-10)
   - 9-10 if GOLDILOCKS ZONE (preparing)
   - 4-6 if THINKING
   - 1-2 if LAUNCHED (too late)
"""

    return prompt


def _parse_gemini_response(response_text: str, scan_data: dict) -> dict:
    """Parse Gemini response into structured analysis."""
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

        # Map to legacy fields for UI compatibility
        analysis['pain_point_analysis'] = analysis.get('phase_assessment', {}).get('conclusion', 'Phase analysis complete.')
        analysis['tech_stack_hook'] = analysis.get('conversation_angle', 'Discuss their i18n journey.')

        # Map timing to semantic analysis
        timing = analysis.get('timing_window', {})
        analysis['semantic_analysis'] = {
            'severity': 'major' if timing.get('urgency') == 'Strike Now' else 'minor',
            'primary_pain_category': 'pre_launch_intent',
            'description': timing.get('reasoning', 'Timing analysis complete.')
        }

        # Map email draft
        if 'cold_email_draft' in analysis:
            analysis['email_draft'] = analysis['cold_email_draft']

        # Map talking points to key findings
        if 'key_talking_points' in analysis:
            analysis['key_findings'] = [
                {'finding': point, 'significance': 'high', 'sales_angle': 'Use in outreach'}
                for point in analysis['key_talking_points']
            ]

        # Ensure all required fields exist
        analysis.setdefault('compliance_risk', {'level': 'low', 'description': 'N/A for pre-launch detection'})
        analysis.setdefault('top_prospects', [])
        analysis.setdefault('outreach_suggestions', [])
        analysis.setdefault('next_steps', ['Review signals', 'Draft personalized outreach', 'Research decision makers'])

        # Add metadata
        analysis['_source'] = 'gemini'
        analysis['_model'] = Config.GEMINI_MODEL

        return analysis

    except json.JSONDecodeError:
        # Fallback if JSON parsing fails
        return _generate_fallback_analysis(scan_data)


def _generate_fallback_analysis(scan_data: dict) -> dict:
    """Generate rule-based sales intelligence when AI is unavailable - GOLDILOCKS ZONE FOCUSED."""
    company = scan_data.get('company_name', 'Unknown')
    org_name = scan_data.get('org_name', '')
    signals = scan_data.get('signals', [])
    signal_summary = scan_data.get('signal_summary', {})
    intent_score = scan_data.get('intent_score', 0)
    goldilocks_status = scan_data.get('goldilocks_status', 'none')
    lead_status = scan_data.get('lead_status', 'Unknown')

    # Extract counts
    rfc_hits = signal_summary.get('rfc_discussion', {}).get('hits', [])
    rfc_count = len(rfc_hits)
    rfc_high = signal_summary.get('rfc_discussion', {}).get('high_priority_count', 0)
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
        phase_evidence = 'GOLDILOCKS ZONE: i18n libraries installed but NO locale folders'
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
        timing_reason = 'GOLDILOCKS ZONE! They built the shelves but have NO books. PERFECT timing - call immediately!'
    elif goldilocks_status == 'launched':
        timing = 'Too Late'
        timing_reason = 'They already have locale folders with translations. Low priority - they have a working system.'
    elif ghost_count > 0:
        timing = 'CALL NOW'
        timing_reason = 'Active WIP work means they are building RIGHT NOW - influence their decisions!'
    elif rfc_high > 0:
        timing = 'Warm Lead'
        timing_reason = 'HIGH priority RFC indicates executive attention. Decision is being made.'
    elif rfc_count > 0:
        timing = 'Warm Lead'
        timing_reason = 'Discussions happening but no concrete action yet.'
    else:
        timing = 'Nurture'
        timing_reason = 'No strong signals - monitor for future activity.'

    # Build executive summary - BOLD, PUNCHY language for BDRs
    if goldilocks_status == 'preparing':
        libs = ', '.join(dep_hits[0].get('libraries_found', ['i18n library'])) if dep_hits else 'i18n library'
        executive_summary = f"GOLDILOCKS ZONE DETECTED! {company} has installed {libs} but has ZERO translations. The infrastructure is READY but no content exists yet. This is the PERFECT time to call - they need our help to fill the gap!"
    elif goldilocks_status == 'launched':
        folders = ', '.join(already_launched[0].get('locale_folders_found', ['locales'])) if already_launched else 'locales'
        executive_summary = f"TOO LATE - {company} already has translation files in /{folders}/. They have a working i18n system. LOW PRIORITY - focus on other leads."
    elif goldilocks_status == 'thinking':
        executive_summary = f"EARLY STAGE - {company} is discussing internationalization but hasn't started building. Worth nurturing - they're researching solutions."
    else:
        executive_summary = f"COLD LEAD - No significant i18n signals detected for {company}. Consider for future outreach or skip."

    # Build email draft based on Goldilocks status
    if goldilocks_status == 'preparing':
        lib = dep_hits[0].get('libraries_found', ['i18n library'])[0] if dep_hits else 'i18n library'
        email_subject = f"Saw you added {lib} - ready to help you launch globally"
        email_body = (
            f"Hi! I noticed you've added {lib} to your codebase but don't have any locale files yet. "
            f"This is actually the perfect timing - you can set up the architecture right before any technical debt builds up. "
            f"Quick 15-min call to share what's worked for similar teams?"
        )
    elif goldilocks_status == 'launched':
        email_subject = f"Quick question about your localization setup"
        email_body = (
            f"Hi, I saw you already have localization set up. "
            f"Curious if you're happy with your current workflow or exploring improvements? "
            f"Either way, no pressure - just reaching out."
        )
    elif dominant_phase == 'Active':
        branch = ghost_hits[0].get('branch_name', 'i18n branch') if ghost_hits else 'i18n branch'
        email_subject = f"Saw your {branch} work - can we help?"
        email_body = (
            f"Hi, I noticed your team has been working on internationalization (saw the {branch} branch). "
            f"Many teams hit unexpected complexity during this phase - translation sync, key management, workflow automation. "
            f"Happy to share how we help teams ship i18n faster - interested in a quick conversation?"
        )
    elif dominant_phase == 'Thinking':
        keyword = rfc_hits[0].get('keywords_matched', ['i18n'])[0] if rfc_hits else 'internationalization'
        email_subject = f"Re: {keyword} - thoughts from the trenches"
        email_body = (
            f"Hi, I came across your team's discussion about {keyword}. "
            f"We've helped many teams navigate this decision - happy to share learnings on what approaches work best. "
            f"Would a quick call be useful before you finalize your strategy?"
        )
    else:
        email_subject = f"Internationalization opportunities at {company}"
        email_body = (
            f"Hi, I've been researching {company}'s technical stack. "
            f"Many companies at your stage start thinking about international expansion. "
            f"Would you be open to a quick chat about your roadmap?"
        )

    # Build key findings - BDR-friendly language
    key_findings = []

    if goldilocks_status == 'preparing':
        libs = ', '.join(dep_hits[0].get('libraries_found', [])) if dep_hits else 'i18n libraries'
        bdr_explanation = dep_hits[0].get('bdr_summary', '') if dep_hits else ''
        key_findings.append({
            'finding': f"Found {libs} - Infrastructure is READY but no translations exist yet",
            'significance': 'critical',
            'sales_angle': 'The shelves are built but the books are missing. PERFECT time to call.',
            'bdr_explanation': bdr_explanation or Config.BDR_TRANSLATIONS.get('locale_folder_missing', '')
        })

    if goldilocks_status == 'launched':
        folders = already_launched[0].get('locale_folders_found', ['locales']) if already_launched else ['locales']
        key_findings.append({
            'finding': f"Found existing locale folders: /{', '.join(folders)}/",
            'significance': 'low',
            'sales_angle': 'They already have translations. We are too late for the greenfield opportunity.',
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
            'sales_angle': 'Developers are actively working on this RIGHT NOW'
        })

    # Build outreach suggestions
    outreach_suggestions = []

    if goldilocks_status == 'preparing':
        libs = dep_hits[0].get('libraries_found', ['unknown']) if dep_hits else ['unknown']
        outreach_suggestions.append({
            'why_account': f"GOLDILOCKS ZONE: Found {', '.join(libs)} but NO translations",
            'why_now': "Infrastructure is ready, translations are missing - PERFECT timing",
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
        'preparing': "GOLDILOCKS ZONE - CALL IMMEDIATELY! They have the infrastructure but ZERO translations. Position as the partner who helps them launch globally. Offer a free architecture review - this is our IDEAL customer.",
        'thinking': "WARM NURTURE: They're researching but haven't started building. Share educational content, case studies. Stay top of mind for when they're ready to build.",
        'launched': "LOW PRIORITY: They already have translations. Only worth pursuing if they're unhappy with current solution. Ask about pain points but don't push hard.",
        'none': "COLD OUTREACH: No signals detected. Generic outreach only if you have bandwidth. Better to focus on hotter leads.",
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
            'PRIORITY 1: Send personalized email TODAY referencing the specific library you found',
            'PRIORITY 2: Find the engineering lead on LinkedIn and connect',
            'PRIORITY 3: Prepare a quick demo showing how we help teams go from "library installed" to "live in production"',
            'BONUS: Check if they have any open roles for localization - indicates urgency'
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
            'LOW PRIORITY - only pursue if you have extra bandwidth',
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
