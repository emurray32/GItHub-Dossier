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
    """Build the sales intelligence prompt for 3-Signal data."""
    company = scan_data.get('company_name', 'Unknown')
    org_name = scan_data.get('org_name', scan_data.get('org_login', ''))
    signals = scan_data.get('signals', [])
    signal_summary = scan_data.get('signal_summary', {})
    intent_score = scan_data.get('intent_score', 0)

    # Extract signal details
    rfc_count = signal_summary.get('rfc_discussion', {}).get('count', 0)
    rfc_high = signal_summary.get('rfc_discussion', {}).get('high_priority_count', 0)
    dep_count = signal_summary.get('dependency_injection', {}).get('count', 0)
    ghost_count = signal_summary.get('ghost_branch', {}).get('count', 0)

    # Get sample evidence
    sample_evidence = []
    for signal in signals[:5]:
        sample_evidence.append({
            'Signal': signal.get('Signal'),
            'Evidence': signal.get('Evidence'),
            'Priority': signal.get('priority', 'MEDIUM'),
        })

    prompt = f"""
You are an expert Technical Sales Engineer specializing in localization platforms.

You're analyzing a company's GitHub data to detect if they're in the THINKING or PREPARING phase of internationalization - BEFORE they've launched.

## Company: {company} ({org_name})
## Intent Score: {intent_score}/100

## 3-Signal Detection Results:

1. RFC & Discussion Signal (Thinking Phase): {rfc_count} hits ({rfc_high} HIGH priority)
   - These indicate strategic discussions about i18n before any code is written

2. Dependency Injection Signal (Preparing Phase): {dep_count} hits
   - Found i18n libraries installed but NO locale folders exist yet
   - This is a "smoking gun" - they bought the tools but haven't built anything

3. Ghost Branch Signal (Active Phase): {ghost_count} hits
   - WIP branches or unmerged PRs with i18n work
   - Active experimentation that hasn't shipped

## Sample Evidence:
{json.dumps(sample_evidence, indent=2)}

## Full Signals Data:
{json.dumps(signals[:10], indent=2, default=str)}

---

Generate a JSON response with these fields:

1. "phase_assessment":
   - Which phase is this company in? (Thinking, Preparing, Active, or Multiple)
   - Confidence level (High/Medium/Low)
   - What signals led to this conclusion?

2. "timing_window":
   - Is this a good time to reach out? Why?
   - What's the urgency level? (Strike Now, Warm Lead, Nurture)

3. "conversation_angle":
   - What's the best way to start the conversation?
   - Reference specific signals they would recognize

4. "cold_email_draft":
   - "subject": Punchy subject referencing their specific phase/activity
   - "body": 3 sentences.
     S1: Reference what you saw (RFC, dependency, branch)
     S2: Connect to a common pain point for their phase
     S3: Soft CTA for a conversation

5. "key_talking_points": (list of 3)
   - Specific things to mention based on signals

6. "risk_factors": (list)
   - Any red flags or concerns

7. "opportunity_score": (1-10 based on timing and signal strength)
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
    """Generate rule-based sales intelligence when AI is unavailable."""
    company = scan_data.get('company_name', 'Unknown')
    org_name = scan_data.get('org_name', '')
    signals = scan_data.get('signals', [])
    signal_summary = scan_data.get('signal_summary', {})
    intent_score = scan_data.get('intent_score', 0)

    # Extract counts
    rfc_hits = signal_summary.get('rfc_discussion', {}).get('hits', [])
    rfc_count = len(rfc_hits)
    rfc_high = signal_summary.get('rfc_discussion', {}).get('high_priority_count', 0)
    dep_hits = signal_summary.get('dependency_injection', {}).get('hits', [])
    dep_count = len(dep_hits)
    ghost_hits = signal_summary.get('ghost_branch', {}).get('hits', [])
    ghost_count = len(ghost_hits)

    # Determine dominant phase
    if dep_count > 0:
        dominant_phase = 'Preparing'
        phase_evidence = 'i18n libraries installed but no locale folders'
    elif ghost_count > 0:
        dominant_phase = 'Active'
        phase_evidence = 'WIP branches with i18n work'
    elif rfc_count > 0:
        dominant_phase = 'Thinking'
        phase_evidence = 'Strategic discussions about i18n'
    else:
        dominant_phase = 'None'
        phase_evidence = 'No pre-launch signals detected'

    # Determine opportunity score
    opportunity_score = min(10, intent_score // 10) if intent_score > 0 else 3

    # Determine timing
    if dep_count > 0:
        timing = 'Strike Now'
        timing_reason = 'They have bought the tools but not implemented - perfect timing to help them get started right.'
    elif ghost_count > 0:
        timing = 'Strike Now'
        timing_reason = 'Active WIP work means they are in the middle of implementation - can influence decisions.'
    elif rfc_high > 0:
        timing = 'Strike Now'
        timing_reason = 'HIGH priority RFC/Proposal indicates executive attention on i18n strategy.'
    elif rfc_count > 0:
        timing = 'Warm Lead'
        timing_reason = 'Discussions happening but no concrete action yet.'
    else:
        timing = 'Nurture'
        timing_reason = 'No strong signals - monitor for future activity.'

    # Build email draft based on phase
    if dominant_phase == 'Preparing':
        lib = dep_hits[0].get('libraries_found', ['i18n library'])[0] if dep_hits else 'i18n library'
        email_subject = f"Noticed {lib} in your stack - ready to help you launch"
        email_body = (
            f"Hi, I noticed you've added {lib} to your dependencies but haven't set up your localization structure yet. "
            f"This is actually the perfect time to get the architecture right - before technical debt accumulates. "
            f"Would you be open to a quick call to share some best practices for getting started?"
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

    # Build key findings
    key_findings = []

    if rfc_high > 0:
        key_findings.append({
            'finding': f'{rfc_high} HIGH priority RFCs/Proposals about i18n',
            'significance': 'high',
            'sales_angle': 'Executive attention - decision is being made at leadership level'
        })

    if dep_count > 0:
        libs = ', '.join(dep_hits[0].get('libraries_found', [])) if dep_hits else 'i18n libraries'
        key_findings.append({
            'finding': f'SMOKING GUN: {libs} installed but no locale folders',
            'significance': 'critical',
            'sales_angle': 'They bought the tools but need help implementing'
        })

    if ghost_count > 0:
        key_findings.append({
            'finding': f'{ghost_count} WIP i18n branches/PRs detected',
            'significance': 'high',
            'sales_angle': 'Active work in progress - can influence architecture decisions'
        })

    # Build outreach suggestions
    outreach_suggestions = []

    if dep_count > 0:
        outreach_suggestions.append({
            'why_account': f"Found i18n libraries ({', '.join(dep_hits[0].get('libraries_found', ['unknown']))}) but no implementation",
            'why_now': "They've invested in tools but haven't built yet - greenfield opportunity",
            'who_to_reach': 'Frontend/Platform engineering lead',
            'message_hook': 'Noticed you added react-intl but no locales folder - want to share best practices for getting started?'
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

    if not signals:
        risk_factors.append('No pre-launch signals detected - may not be a current priority')

    if dominant_phase == 'Thinking' and rfc_high == 0:
        risk_factors.append('Early stage discussions only - may take time to move forward')

    if not risk_factors:
        risk_factors.append('Standard sales cycle considerations apply')

    # Conversation starters
    conversation_starters = []

    if dep_count > 0:
        conversation_starters.append("I noticed you've set up i18n libraries - are you planning a multi-language launch?")

    if ghost_count > 0:
        conversation_starters.append("Saw some WIP i18n work in your repo - how's the implementation going?")

    if rfc_count > 0:
        conversation_starters.append("I came across your team's i18n discussion - what approach are you leaning toward?")

    if not conversation_starters:
        conversation_starters.append(f"Is internationalization on {company}'s roadmap?")

    return {
        'pain_point_analysis': f"Company is in {dominant_phase} phase: {phase_evidence}",
        'tech_stack_hook': f"Based on 3-Signal analysis, {company} shows {intent_score}/100 intent score.",
        'semantic_analysis': {
            'severity': 'major' if timing == 'Strike Now' else 'minor',
            'primary_pain_category': 'pre_launch_intent',
            'description': f"{dominant_phase} phase detected. {timing_reason}"
        },
        'compliance_risk': {
            'level': 'low',
            'description': 'N/A for pre-launch detection'
        },
        'forensic_evidence': 'Pre-launch signal detection complete.',
        'top_prospects': [],
        'opportunity_score': opportunity_score,
        'opportunity_type': dominant_phase.lower(),
        'email_draft': {
            'subject': email_subject,
            'body': email_body
        },
        'key_findings': key_findings,
        'outreach_suggestions': outreach_suggestions,
        'recommended_approach': _get_recommended_approach(dominant_phase),
        'conversation_starters': conversation_starters[:3],
        'risk_factors': risk_factors,
        'next_steps': _get_next_steps(dominant_phase),
        'phase_assessment': {
            'phase': dominant_phase,
            'confidence': 'High' if intent_score > 50 else 'Medium' if intent_score > 25 else 'Low',
            'evidence': phase_evidence,
        },
        'timing_window': {
            'urgency': timing,
            'reasoning': timing_reason,
        },
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


def _sse_log(message: str) -> str:
    """Format a log message for SSE."""
    return f"data: LOG:{message}\n\n"


def _sse_data(event_type: str, data: dict) -> str:
    """Format a data payload for SSE."""
    return f"data: {event_type}:{json.dumps(data)}\n\n"
