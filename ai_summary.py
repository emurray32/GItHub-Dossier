"""
AI Summary Module using Google Gemini.

Generates strategic localization analysis from scan data.
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
    Generate AI-powered strategic analysis of scan data.

    Args:
        scan_data: The complete scan results dictionary.

    Yields:
        SSE-formatted progress messages.

    Returns:
        Analysis result dictionary.
    """
    yield _sse_log("Initializing AI analysis engine...")

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

    yield _sse_log("Preparing data for Gemini analysis...")

    # Build prompt
    prompt = _build_analysis_prompt(scan_data)

    yield _sse_log("Sending to Gemini 2.5 Flash...")

    try:
        client = genai.Client(api_key=Config.GEMINI_API_KEY)

        response = client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=prompt
        )

        yield _sse_log("Processing AI response...")

        analysis = _parse_gemini_response(response.text, scan_data)

        yield _sse_log("AI analysis complete")
        yield _sse_data('ANALYSIS_COMPLETE', analysis)

        return analysis

    except Exception as e:
        yield _sse_log(f"AI analysis error: {str(e)}")
        yield _sse_log("Falling back to rule-based analysis...")
        analysis = _generate_fallback_analysis(scan_data)
        yield _sse_data('ANALYSIS_COMPLETE', analysis)
        return analysis


def _build_analysis_prompt(scan_data: dict) -> str:
    """Build the analysis prompt for Gemini."""
    company = scan_data.get('company_name', 'Unknown')
    org_name = scan_data.get('org_name', scan_data.get('org_login', ''))
    signals = scan_data.get('signals', [])
    repos_scanned = scan_data.get('repos_scanned', [])

    # Format signals for prompt
    signals_text = ""
    if signals:
        for i, signal in enumerate(signals[:30], 1):  # Limit to top 30
            signal_type = signal.get('type', 'unknown')
            repo = signal.get('repo', 'unknown')

            if signal_type == 'commit_message':
                signals_text += f"{i}. [Commit] {repo}: {signal.get('message', '')[:100]}\n"
            elif signal_type == 'pull_request':
                signals_text += f"{i}. [PR #{signal.get('number')}] {repo}: {signal.get('title', '')}\n"
            elif signal_type == 'file_change':
                signals_text += f"{i}. [File] {repo}: {signal.get('file', '')} ({signal.get('status', '')})\n"
            elif signal_type == 'hreflang':
                signals_text += f"{i}. [hreflang] {repo}: {signal.get('file', '')}\n"
    else:
        signals_text = "No explicit i18n signals detected in recent activity.\n"

    # Format repos
    repos_text = ""
    for repo in repos_scanned[:10]:
        repos_text += f"- {repo.get('name')}: {repo.get('commits_analyzed', 0)} commits, {repo.get('prs_analyzed', 0)} PRs analyzed\n"

    prompt = f"""You are a Senior Localization Strategist and Sales Intelligence Analyst.
Analyze the following deep-dive GitHub data for {company} ({org_name}) and generate a comprehensive strategic sales narrative.

## Scan Summary
- Organization: {org_name}
- Total Repositories Scanned: {len(repos_scanned)}
- Total Commits Analyzed: {scan_data.get('total_commits_analyzed', 0)}
- Total PRs Analyzed: {scan_data.get('total_prs_analyzed', 0)}
- I18n Signals Detected: {len(signals)}

## Repositories Analyzed
{repos_text}

## I18n/Localization Signals Detected
{signals_text}

## Your Task
Generate a detailed strategic analysis in the following JSON format:

{{
    "executive_summary": "A 2-3 sentence high-level overview for sales outreach",
    "localization_maturity": "emerging|developing|mature|advanced",
    "opportunity_score": 1-10,
    "key_findings": [
        {{"finding": "description", "significance": "high|medium|low", "evidence": "what was detected"}}
    ],
    "sales_narrative": "A compelling 2-3 paragraph narrative for sales outreach that references specific findings",
    "recommended_approach": "Strategic recommendations for approaching this company",
    "timeline_insights": [
        {{"period": "timeframe", "activity": "what was happening", "implication": "what this means"}}
    ],
    "key_contacts_to_find": ["Role/title to target for outreach"],
    "conversation_starters": ["Specific talking points based on detected activity"],
    "risk_factors": ["Any concerns or challenges to be aware of"],
    "next_steps": ["Recommended actions"]
}}

Be specific and reference actual signals detected. If no signals were found, provide analysis based on the repository structure and activity patterns.
Respond with ONLY the JSON, no additional text."""

    return prompt


def _parse_gemini_response(response_text: str, scan_data: dict) -> dict:
    """Parse Gemini response into structured analysis."""
    try:
        # Clean up response text (remove markdown code blocks if present)
        text = response_text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

        analysis = json.loads(text)

        # Add metadata
        analysis['_source'] = 'gemini'
        analysis['_model'] = Config.GEMINI_MODEL

        return analysis

    except json.JSONDecodeError:
        # If JSON parsing fails, create structured response from text
        return {
            'executive_summary': response_text[:500] if response_text else 'Analysis unavailable',
            'localization_maturity': 'unknown',
            'opportunity_score': 5,
            'key_findings': [],
            'sales_narrative': response_text,
            'recommended_approach': 'Further investigation recommended',
            'timeline_insights': [],
            'key_contacts_to_find': ['Engineering Lead', 'VP of Product'],
            'conversation_starters': [],
            'risk_factors': ['AI analysis could not be fully parsed'],
            'next_steps': ['Manual review of findings recommended'],
            '_source': 'gemini_fallback',
            '_model': Config.GEMINI_MODEL,
            '_raw_response': response_text
        }


def _generate_fallback_analysis(scan_data: dict) -> dict:
    """Generate rule-based analysis when AI is unavailable."""
    signals = scan_data.get('signals', [])
    company = scan_data.get('company_name', 'Unknown')
    org_name = scan_data.get('org_name', '')

    # Categorize signals
    commit_signals = [s for s in signals if s.get('type') == 'commit_message']
    pr_signals = [s for s in signals if s.get('type') == 'pull_request']
    file_signals = [s for s in signals if s.get('type') == 'file_change']
    hreflang_signals = [s for s in signals if s.get('type') == 'hreflang']

    # Determine maturity level
    total_signals = len(signals)
    if total_signals == 0:
        maturity = 'emerging'
        opportunity_score = 3
    elif total_signals < 5:
        maturity = 'developing'
        opportunity_score = 6
    elif total_signals < 15:
        maturity = 'mature'
        opportunity_score = 7
    else:
        maturity = 'advanced'
        opportunity_score = 5  # May already have solutions in place

    # Build key findings
    key_findings = []

    if file_signals:
        key_findings.append({
            'finding': f'Active i18n file management detected in {len(set(s.get("repo") for s in file_signals))} repositories',
            'significance': 'high',
            'evidence': f'{len(file_signals)} file changes in localization directories'
        })

    if pr_signals:
        key_findings.append({
            'finding': 'Localization-related pull requests in active development',
            'significance': 'high',
            'evidence': f'{len(pr_signals)} PRs with i18n keywords detected'
        })

    if hreflang_signals:
        key_findings.append({
            'finding': 'hreflang implementation activity detected',
            'significance': 'medium',
            'evidence': f'Found in {len(hreflang_signals)} files'
        })

    if commit_signals:
        key_findings.append({
            'finding': 'Regular commits mentioning localization/i18n',
            'significance': 'medium',
            'evidence': f'{len(commit_signals)} commits with i18n references'
        })

    if not key_findings:
        key_findings.append({
            'finding': 'No explicit localization signals detected in recent activity',
            'significance': 'low',
            'evidence': 'Scanned commits and PRs showed no i18n patterns'
        })

    # Build narrative
    if total_signals > 0:
        narrative = f"""Based on our deep analysis of {company}'s GitHub activity, we've identified {total_signals}
localization-related signals across their codebase. This indicates {"active investment" if total_signals > 10 else "growing interest"}
in internationalization.

The activity spans {len(scan_data.get('repos_scanned', []))} repositories, with particular focus on
{', '.join(set(s.get('repo') for s in signals[:5]))}. This suggests a {"coordinated localization effort" if total_signals > 5 else "nascent but promising localization initiative"}.

Given their current trajectory, {company} would benefit from {"streamlined localization workflows" if maturity == 'developing' else "enterprise-grade localization infrastructure"}
to scale their international presence efficiently."""
    else:
        narrative = f"""Our analysis of {company}'s GitHub presence ({org_name}) did not reveal explicit
localization activity in the last 90 days. However, this presents a potential greenfield opportunity.

With {scan_data.get('org_public_repos', 'multiple')} public repositories and active development,
{company} may be approaching a stage where internationalization becomes strategically important.

This could be an ideal time to introduce localization best practices before technical debt accumulates."""

    return {
        'executive_summary': f"{company} shows {maturity} localization maturity with {total_signals} detected signals. "
                           f"{'Strong opportunity for localization partnership.' if opportunity_score >= 6 else 'Potential early-stage opportunity.'}",
        'localization_maturity': maturity,
        'opportunity_score': opportunity_score,
        'key_findings': key_findings,
        'sales_narrative': narrative,
        'recommended_approach': _get_recommended_approach(maturity),
        'timeline_insights': _extract_timeline_insights(signals),
        'key_contacts_to_find': [
            'VP of Engineering',
            'Director of Product',
            'Internationalization Lead',
            'Technical Program Manager'
        ],
        'conversation_starters': _get_conversation_starters(signals, company),
        'risk_factors': _get_risk_factors(maturity, total_signals),
        'next_steps': [
            'Review detected signals for conversation specifics',
            'Research company news for expansion announcements',
            'Identify LinkedIn contacts in engineering leadership',
            'Prepare tailored demo based on their tech stack'
        ],
        '_source': 'fallback'
    }


def _get_recommended_approach(maturity: str) -> str:
    """Get recommended sales approach based on maturity."""
    approaches = {
        'emerging': "Position as a strategic partner for building localization foundations. Focus on future-proofing their codebase for international growth.",
        'developing': "Highlight efficiency gains and automation opportunities. They're likely experiencing growing pains that your solution can address.",
        'mature': "Emphasize advanced features, integrations, and enterprise-grade capabilities. Focus on optimization and scale.",
        'advanced': "Look for pain points in their current setup. Position as a premium alternative with better support/features."
    }
    return approaches.get(maturity, approaches['developing'])


def _extract_timeline_insights(signals: list) -> list:
    """Extract timeline insights from signals."""
    insights = []

    # Group signals by date (rough)
    dated_signals = [s for s in signals if s.get('date') or s.get('created_at')]

    if dated_signals:
        insights.append({
            'period': 'Last 90 days',
            'activity': f'{len(dated_signals)} localization-related activities detected',
            'implication': 'Active ongoing work in i18n space'
        })

    return insights


def _get_conversation_starters(signals: list, company: str) -> list:
    """Generate conversation starters based on signals."""
    starters = []

    pr_signals = [s for s in signals if s.get('type') == 'pull_request']
    if pr_signals:
        starters.append(
            f"I noticed your team has been working on localization - PR #{pr_signals[0].get('number', 'N/A')} "
            f"caught my attention. How's that initiative progressing?"
        )

    file_signals = [s for s in signals if s.get('type') == 'file_change']
    if file_signals:
        starters.append(
            f"Your team's recent work in the locales directory suggests international expansion. "
            f"What markets are you targeting?"
        )

    if not starters:
        starters = [
            f"I've been researching {company}'s technical architecture. Are you considering international markets?",
            "Many companies at your stage start thinking about localization. Is that on your roadmap?"
        ]

    return starters


def _get_risk_factors(maturity: str, signal_count: int) -> list:
    """Identify risk factors for the opportunity."""
    risks = []

    if maturity == 'advanced':
        risks.append('May already have established localization vendor relationships')

    if signal_count > 20:
        risks.append('Heavy existing investment may mean switching costs are high')

    if signal_count == 0:
        risks.append('No visible i18n activity - may not be a current priority')

    if not risks:
        risks.append('Standard competitive landscape considerations apply')

    return risks


def _sse_log(message: str) -> str:
    """Format a log message for SSE."""
    return f"data: LOG:{message}\n\n"


def _sse_data(event_type: str, data: dict) -> str:
    """Format a data payload for SSE."""
    return f"data: {event_type}:{json.dumps(data)}\n\n"
