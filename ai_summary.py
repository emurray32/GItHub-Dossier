"""
AI Summary Module for High-Intent Sales Intelligence.

Generates actionable sales assets including:
- Pain Point Analysis
- Tech Stack Hooks
- Ready-to-send Email Drafts
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
    Generate AI-powered sales intelligence from scan data.

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

    yield _sse_log("Preparing high-intent data for Gemini...")

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
    """Build the high-signal sales intelligence prompt."""
    company = scan_data.get('company_name', 'Unknown')
    org_name = scan_data.get('org_name', scan_data.get('org_login', ''))
    
    # Extract raw signals to let the LLM filter them
    tech_stack = scan_data.get('tech_stack', {})
    signals = scan_data.get('signals', [])
    frustration = scan_data.get('frustration_signals', [])
    
    # Format tech stack for context
    tech_context = "Unknown"
    if tech_stack.get('i18n_libraries'):
        tech_context = f"Libraries: {', '.join(tech_stack['i18n_libraries'])} | Framework: {tech_stack.get('primary_framework', 'Unknown')}"

    prompt = f"""
You are an expert Technical Sales Engineer. Your goal is to analyze GitHub data to find "High Intent" sales opportunities for a localization platform.

Your Input Data:
- Company: {company} ({org_name})
- Tech Stack: {tech_context}
- Signal Count: {len(signals)}
- Pain Indicators: {len(frustration)} events

CRITICAL INSTRUCTION: You must filter out "Noise." Ignore typos, simple text updates, logo swaps, or README changes. Only focus on "Architectural Signals" that indicate pain, scaling issues, or migration.

Generate a JSON response with exactly these fields:

1. "fit_analysis":
   - Assess Technical Maturity (e.g., "High - building custom tooling" vs "Low - basic JSON files").
   - Identify the "Hook" (Connect their specific tech stack to a value prop).

2. "timing_trigger":
   - Look for MIGRATION signals (keywords: refactor, deprecate, rewrite).
   - Look for BREAKING signals (keywords: crash, missing keys, build fail).
   - Look for EXPANSION signals (new language files added).
   - If none found, state "No immediate timing trigger detected."

3. "buying_committee":
   - Identify the "Architect/Buyer" (users merging PRs for tooling/config/builds).
   - Identify the "Implementer/Champion" (users fixing bugs in locale files).
   - Return a list of objects: {{ "name": "...", "role_inference": "...", "reason": "..." }}

4. "cold_email_draft":
   - "subject": A punchy subject line referencing their specific migration or pain.
   - "body": A 3-sentence email. Sentence 1: "Saw you're refactoring [Project]." Sentence 2: "Most teams struggle with [Specific Pain] during this phase." Sentence 3: "Our SDK automates this."

5. "opportunity_score": (1-10 based on PAIN, not just volume).

Raw Data to Analyze: {json.dumps(scan_data, default=str)[:15000]}
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

        # BACKWARDS COMPATIBILITY MAPPING
        # Map new "High-Signal" fields to legacy UI fields so the report doesn't break
        
        # 1. Map 'fit_analysis' to 'pain_point_analysis' and 'tech_stack_hook'
        if 'fit_analysis' in analysis:
            fit = analysis['fit_analysis']
            if isinstance(fit, dict):
                analysis['pain_point_analysis'] = fit.get('maturity_assessment', 'Technical maturity assessed by AI.') # Fallback
                analysis['tech_stack_hook'] = fit.get('hook', 'Custom integration available.')
        
        # 2. Map 'buying_committee' to 'top_prospects'
        if 'buying_committee' in analysis:
            committee = analysis['buying_committee']
            prospects = []
            if isinstance(committee, list):
                for member in committee:
                    prospects.append({
                        'login': member.get('name', 'unknown').lower().replace(' ', ''), # Best guess
                        'name': member.get('name', 'Unknown'),
                        'role_inference': member.get('role_inference', 'Contributor'),
                        'outreach_strategy': member.get('reason', 'Identified as key stakeholder'),
                        'icebreaker': f"Saw your work on {scan_data.get('company_name')}."
                    })
            analysis['top_prospects'] = prospects

        # 3. Map 'cold_email_draft' to 'email_draft'
        if 'cold_email_draft' in analysis:
            analysis['email_draft'] = analysis['cold_email_draft']

        # 4. Map 'timing_trigger' to 'semantic_analysis' description
        if 'timing_trigger' in analysis:
            trigger = analysis['timing_trigger']
            analysis['semantic_analysis'] = {
                'severity': 'major', # Default to major if trigger exists
                'primary_pain_category': 'technical_change',
                'description': trigger if isinstance(trigger, str) else "Timing trigger detected."
            }

        # Add metadata
        analysis['_source'] = 'gemini'
        analysis['_model'] = Config.GEMINI_MODEL

        return analysis

    except json.JSONDecodeError:
        # Fallback if JSON parsing fails
        return {
            'pain_point_analysis': response_text[:500] if response_text else 'Analysis unavailable due to rate limit or error.',
            'tech_stack_hook': 'Further investigation needed',
            'opportunity_score': 5,
            'opportunity_type': 'unknown',
            'email_draft': {
                'subject': f"Localization opportunities at {scan_data.get('company_name', 'your company')}",
                'body': 'I noticed your team has been working on internationalization. Would love to discuss how we can help streamline your localization workflow.'
            },
            'semantic_analysis': {
                'severity': 'minor',
                'primary_pain_category': 'unknown',
                'description': 'AI analysis failed to parse. Please review raw signals.'
            },
            'compliance_risk': {
                'level': 'low',
                'description': 'AI compliance analysis unavailable.'
            },
            'forensic_evidence': 'No external forensics available.',
            'top_prospects': [],
            'key_findings': [],
            'outreach_suggestions': [],
            'recommended_approach': 'Manual review of findings recommended',
            'conversation_starters': [],
            'risk_factors': ['AI analysis could not be fully parsed'],
            'next_steps': ['Manual review of scan data recommended'],
            '_source': 'gemini_fallback',
            '_model': Config.GEMINI_MODEL,
        }


def _generate_fallback_analysis(scan_data: dict) -> dict:
    """Generate rule-based sales intelligence when AI is unavailable."""
    company = scan_data.get('company_name', 'Unknown')
    org_name = scan_data.get('org_name', '')
    
    # Extract data
    tech_stack = scan_data.get('tech_stack', {})
    competitor = scan_data.get('competitor_detection', {})
    frustration = scan_data.get('frustration_signals', [])
    dev_metric = scan_data.get('developer_translator_metric', {})
    bottleneck = scan_data.get('reviewer_bottleneck', {})
    locale_inventory = scan_data.get('locale_inventory', {})
    market_insights = scan_data.get('market_insights', {})
    is_greenfield = scan_data.get('is_greenfield', False)
    total_stars = scan_data.get('total_stars', 0)
    signals = scan_data.get('signals', [])

    # Determine opportunity type and score
    if is_greenfield:
        opportunity_type = 'greenfield'
        opportunity_score = 8
    elif competitor.get('is_using_competitor'):
        opportunity_type = 'competitor_displacement'
        opportunity_score = 7
    elif frustration or dev_metric.get('is_high_pain'):
        opportunity_type = 'pain_driven'
        opportunity_score = 8
    elif market_insights.get('primary_market'):
        opportunity_type = 'expansion'
        opportunity_score = 7
    else:
        opportunity_type = 'emerging'
        opportunity_score = 5

    # Build pain point analysis
    pain_points = []
    if frustration:
        pain_types = {}
        for f in frustration:
            pt = f.get('pain_indicator', 'unknown')
            pain_types[pt] = pain_types.get(pt, 0) + 1
        top_pain = max(pain_types.items(), key=lambda x: x[1]) if pain_types else ('unknown', 0)
        pain_points.append(f"{len(frustration)} frustration signals detected (most common: {top_pain[0]} - {top_pain[1]} occurrences)")
    
    if dev_metric.get('is_high_pain'):
        pain_points.append(f"Engineers are doing translation work: {dev_metric.get('human_percentage', '0%')} of edits by humans")
    
    if bottleneck.get('is_bottleneck'):
        pain_points.append(f"Reviewer bottleneck: @{bottleneck['bottleneck_user']} reviews {bottleneck['top_reviewer_ratio']*100:.0f}% of i18n PRs")

    if competitor.get('is_using_competitor'):
        configs = [c['file'] for c in competitor.get('config_files_found', [])]
        pain_points.append(f"Currently using competitor TMS: {configs or competitor.get('tms_in_dependencies', [])}")

    pain_point_analysis = "; ".join(pain_points) if pain_points else "No specific pain points detected - general localization need"

    # Build tech stack hook
    framework = tech_stack.get('primary_framework')
    libs = tech_stack.get('i18n_libraries', [])
    
    if framework == 'Next.js':
        tech_stack_hook = f"Since you're using Next.js with {libs[0] if libs else 'i18n'}, our Next.js SDK provides seamless integration with zero config changes."
    elif framework == 'React':
        tech_stack_hook = f"Your React app using {libs[0] if libs else 'react-intl'} can benefit from our React SDK - drop-in replacement with automated sync."
    elif framework:
        tech_stack_hook = f"We have native {framework} support that integrates directly with your {libs[0] if libs else 'existing i18n setup'}."
    else:
        tech_stack_hook = "Our platform supports all major frameworks and can integrate with your existing workflow."

    # Build email draft
    primary_market = market_insights.get('primary_market')
    
    if is_greenfield:
        email_subject = f"Future-proofing {company}'s international growth"
        email_body = (
            f"Hi, I've been analyzing {company}'s codebase and noticed you have a mature platform with {total_stars}+ stars "
            f"but no localization infrastructure in place. Many companies at your scale are preparing for international expansion. "
            f"I'd love to share how we can help you build localization into your architecture before it becomes a costly retrofit."
        )
    elif competitor.get('is_using_competitor'):
        tms_name = competitor.get('tms_in_dependencies', ['your current TMS'])[0] if competitor.get('tms_in_dependencies') else 'your current TMS'
        email_subject = f"Improving {company}'s localization workflow"
        email_body = (
            f"Hi, I noticed your team is using {tms_name} for localization. Many teams in similar situations have found our "
            f"developer-first approach reduces sync issues and merge conflicts. Would you be open to a quick comparison? "
            f"I can show you specifically how we'd integrate with your {'Next.js' if framework == 'Next.js' else 'existing'} setup."
        )
    elif primary_market:
        email_subject = f"{company}'s expansion into {primary_market}"
        email_body = (
            f"Hi, I noticed {company} has been expanding into {primary_market} based on your recent locale file additions. "
            f"Managing {locale_inventory.get('language_count', 'multiple')} languages can get complex quickly. "
            f"Would love to show you how we automate the translation sync process for growing teams."
        )
    elif frustration:
        email_subject = f"Fixing the translation workflow at {company}"
        email_body = (
            f"Hi, I've been looking at {company}'s GitHub activity and noticed some commits mentioning translation sync issues. "
            f"This is a common pain point - developers spending time on manual JSON edits instead of building features. "
            f"I'd love to show you how we automate this entirely."
        )
    else:
        email_subject = f"Localization opportunities at {company}"
        email_body = (
            f"Hi, I've been researching {company}'s technical stack and noticed you're building with {framework or 'modern frameworks'}. "
            f"Many teams at your stage start thinking about internationalization. Would you be open to a quick chat about your roadmap? "
            f"I'd love to share some best practices for scaling localization."
        )

    # Build key findings
    key_findings = []
    
    if tech_stack.get('i18n_libraries'):
        key_findings.append({
            'finding': f"Using {', '.join(tech_stack['i18n_libraries'])}",
            'significance': 'high',
            'sales_angle': 'Reference their specific library when discussing integration'
        })
    
    if frustration:
        key_findings.append({
            'finding': f"{len(frustration)} frustration signals in commits",
            'significance': 'high',
            'sales_angle': 'Lead with pain relief - "I noticed your team has been dealing with translation sync issues..."'
        })
    
    if dev_metric.get('is_high_pain'):
        key_findings.append({
            'finding': f"{dev_metric['human_percentage']} of translation edits by engineers",
            'significance': 'high',
            'sales_angle': 'Highlight developer time savings - "Your engineers are spending time on translation work instead of features"'
        })

    if competitor.get('is_using_competitor'):
        key_findings.append({
            'finding': 'Already using competitor TMS',
            'significance': 'high',
            'sales_angle': 'Position on pain points with current solution - ask about their experience'
        })

    if market_insights.get('primary_market'):
        key_findings.append({
            'finding': f"Expanding into {market_insights['primary_market']}",
            'significance': 'high',
            'sales_angle': 'Reference their expansion - shows they have budget and priority'
        })

    # Conversation starters
    conversation_starters = []
    
    if competitor.get('is_using_competitor'):
        tms = competitor.get('tms_in_dependencies', ['your current TMS'])[0] if competitor.get('tms_in_dependencies') else 'your current TMS'
        conversation_starters.append(f"How has your experience been with {tms}? Any pain points I should know about?")
    
    if frustration:
        conversation_starters.append("I noticed some commits mentioning translation sync issues - is that something your team is actively trying to solve?")
    
    if dev_metric.get('is_high_pain'):
        conversation_starters.append("Are your engineers spending a lot of time on translation file management? That's a common issue we help solve.")
    
    if primary_market:
        conversation_starters.append(f"How's the expansion into {primary_market} going? Any localization challenges we can help with?")
    
    if not conversation_starters:
        conversation_starters = [
            f"What's {company}'s roadmap for international expansion?",
            "Is localization on your team's priority list for this year?"
        ]

    # Risk factors
    risk_factors = []
    
    if competitor.get('is_using_competitor'):
        risk_factors.append("Already invested in competitor - may have switching costs or contracts")
    
    if not signals and not frustration:
        risk_factors.append("Low localization activity - may not be a current priority")
    
    if is_greenfield:
        risk_factors.append("No existing localization = no existing budget allocation")
    
    if not risk_factors:
        risk_factors.append("Standard competitive landscape considerations apply")

    # Build top prospects from contributors
    top_prospects = []
    if scan_data.get('contributors'):
        for login, data in list(scan_data['contributors'].items())[:3]:
            top_prospects.append({
                'login': login,
                'name': data.get('name') or login,
                'role_inference': 'Technical Contributor' if not data.get('company') else f"Engineer at {data.get('company')}",
                'outreach_strategy': f"Directly involved in {data['i18n_commits']} localization commits. Likely building the i18n infrastructure.",
                'icebreaker': f"I noticed your recent work on localization files in the {scan_data['org_login']} repositories."
            })

    # Build semantic analysis
    severity = 'minor'
    if frustration: severity = 'major'
    if dev_metric.get('is_high_pain'): severity = 'critical'
    
    semantic_analysis = {
        'severity': severity,
        'primary_pain_category': 'technical_debt' if is_greenfield else 'sync_issues',
        'description': f"Detected {len(frustration)} pain signals related to translation synchronization and human-driven localization edits."
    }

    # Build compliance analysis
    comp_risk = 'low'
    if scan_data['compliance_assets']['is_compliant_risk']:
        comp_risk = 'high'
    
    compliance_risk = {
        'level': comp_risk,
        'description': f"Detected {len(scan_data['compliance_assets']['detected_files'])} legal assets. Localized: {scan_data['compliance_assets']['localized_count']}."
    }

    return {
        'pain_point_analysis': pain_point_analysis,
        'tech_stack_hook': tech_stack_hook,
        'semantic_analysis': semantic_analysis,
        'compliance_risk': compliance_risk,
        'forensic_evidence': "External forensic scan complete (No public threads detected).",
        'top_prospects': top_prospects,
        'opportunity_score': opportunity_score,
        'opportunity_type': opportunity_type,
        'email_draft': {
            'subject': email_subject,
            'body': email_body
        },
        'key_findings': key_findings,
        'outreach_suggestions': _build_outreach_suggestions(scan_data, opportunity_type),
        'recommended_approach': _get_recommended_approach(opportunity_type, competitor.get('is_using_competitor', False), is_greenfield),
        'conversation_starters': conversation_starters[:3],
        'risk_factors': risk_factors,
        'next_steps': _get_next_steps(opportunity_type, competitor.get('is_using_competitor', False)),
        '_source': 'fallback'
    }


def _build_outreach_suggestions(scan_data: dict, opportunity_type: str) -> list:
    """Build a list of outreach suggestions for BDRs."""
    company = scan_data.get('company_name', 'this account')
    signals = scan_data.get('signals', [])
    repo_count = len(scan_data.get('repos_scanned', []))
    tech_stack = scan_data.get('tech_stack', {})
    competitor = scan_data.get('competitor_detection', {})
    frustration = scan_data.get('frustration_signals', [])
    dev_metric = scan_data.get('developer_translator_metric', {})
    bottleneck = scan_data.get('reviewer_bottleneck', {})
    locale_inventory = scan_data.get('locale_inventory', {})
    market_insights = scan_data.get('market_insights', {})
    compliance_assets = scan_data.get('compliance_assets', {})
    contributors = scan_data.get('contributors', {})

    signals_count = len(signals)
    frustration_count = len(frustration)
    framework = tech_stack.get('primary_framework', 'their stack')
    i18n_libs = ", ".join(tech_stack.get('i18n_libraries', [])) or "their current i18n setup"
    language_count = locale_inventory.get('language_count') or len(locale_inventory.get('locales_detected', [])) or "multiple"
    primary_market = market_insights.get('primary_market', 'new markets')
    compliance_total = len(compliance_assets.get('detected_files', []))
    compliance_localized = compliance_assets.get('localized_count', 0)

    top_contributors = _get_top_contributors(contributors, limit=2)
    contributor_targets = ", ".join([f"@{login}" for login in top_contributors]) if top_contributors else "Localization lead / Engineering manager"

    suggestions = []

    suggestions.append({
        "why_account": f"{signals_count or 'Multiple'} localization signals across {repo_count or 'several'} repos show active i18n investment.",
        "why_now": "Recent scan detected ongoing localization-related changes, indicating active priorities.",
        "who_to_reach": contributor_targets,
        "message_hook": "Noticed active i18n changes across multiple repos—curious how you're managing translation updates at scale."
    })

    suggestions.append({
        "why_account": f"{frustration_count or 'Several'} commit messages flag translation friction or rework.",
        "why_now": "Recent frustration signals suggest the team is feeling the pain today.",
        "who_to_reach": contributor_targets,
        "message_hook": "Saw commits that suggest translation sync issues—how painful is that in your current workflow?"
    })

    human_percentage = dev_metric.get('human_percentage') or "a large share"
    suggestions.append({
        "why_account": f"Developers are doing translation edits ({human_percentage} by humans).",
        "why_now": "Manual localization effort compounds as more languages are added.",
        "who_to_reach": "Frontend/platform engineering lead",
        "message_hook": "How much engineer time goes to translation file updates vs feature work today?"
    })

    bottleneck_user = bottleneck.get('bottleneck_user', 'a single reviewer')
    suggestions.append({
        "why_account": "Review bottlenecks slow localization throughput and release cadence.",
        "why_now": "Current review concentration hints at a single point of failure.",
        "who_to_reach": f"@{bottleneck_user}" if bottleneck.get('is_bottleneck') else "Engineering manager / release owner",
        "message_hook": "Looks like i18n reviews may be centralized—want to reduce that bottleneck and speed ship cycles?"
    })

    competitor_name = "current TMS"
    if competitor.get('is_using_competitor'):
        competitor_name = competitor.get('tms_in_dependencies', ['current TMS'])[0]
    suggestions.append({
        "why_account": f"Existing localization tooling ({competitor_name}) creates an opening to improve developer UX.",
        "why_now": "Migration is easiest while active localization work is already in motion.",
        "who_to_reach": "Localization platform owner / tooling lead",
        "message_hook": f"Teams often see merge-conflict pain with {competitor_name}—is that showing up for you?"
    })

    suggestions.append({
        "why_account": f"Locale footprint suggests {language_count} languages in flight.",
        "why_now": f"Expansion into {primary_market} raises complexity and QA risk.",
        "who_to_reach": "Product leader for international growth",
        "message_hook": f"How are you ensuring translation quality as you expand into {primary_market}?"
    })

    suggestions.append({
        "why_account": f"{compliance_total or 'Some'} compliance assets detected, but only {compliance_localized} localized.",
        "why_now": "Regulatory exposure grows with each new market.",
        "who_to_reach": "Legal ops / localization program owner",
        "message_hook": "Do you have a process to keep legal/privacy pages localized as new locales ship?"
    })

    suggestions.append({
        "why_account": f"Stack includes {framework} + {i18n_libs}, making integration straightforward.",
        "why_now": "SDK-based integrations are fastest during active development cycles.",
        "who_to_reach": "Developer productivity / platform engineering",
        "message_hook": f"We integrate directly with {framework} and {i18n_libs}—open to a 15-min walkthrough?"
    })

    suggestions.append({
        "why_account": f"{company} has a multi-repo footprint, which multiplies localization coordination cost.",
        "why_now": "Centralizing localization now avoids long-term process drift.",
        "who_to_reach": "Director of engineering / program manager",
        "message_hook": "How do you coordinate localization across multiple repos without slowing releases?"
    })

    suggestions.append({
        "why_account": f"{company}'s opportunity type reads as {opportunity_type.replace('_', ' ')}.",
        "why_now": "Priority alignment is highest when the need is already visible in the codebase.",
        "who_to_reach": "VP Engineering / Globalization lead",
        "message_hook": "We see clear signals of localization investment—open to comparing your current workflow to a modernized approach?"
    })

    return suggestions[:10]


def _get_top_contributors(contributors: dict, limit: int = 2) -> list:
    """Return top contributor logins ranked by i18n impact."""
    if not contributors:
        return []

    def score(data: dict) -> int:
        return data.get('i18n_commits', 0) + data.get('frustration_count', 0)

    sorted_contributors = sorted(
        contributors.items(),
        key=lambda item: score(item[1]),
        reverse=True
    )
    return [login for login, _ in sorted_contributors[:limit]]


def _get_recommended_approach(opportunity_type: str, has_competitor: bool, is_greenfield: bool) -> str:
    """Get strategic recommendation based on opportunity type."""
    if is_greenfield:
        return "GREENFIELD APPROACH: Position as strategic architecture partner. Lead with 'future-proofing' narrative - the cost of retrofitting localization later vs building it right. Offer a free 'localization readiness assessment'."
    
    if has_competitor:
        return "COMPETITIVE DISPLACEMENT: Research their current TMS pain points thoroughly. Position on developer experience and reduced friction. Offer a side-by-side comparison or migration assessment. Focus on what's broken, not features."
    
    approaches = {
        'pain_driven': "PAIN RELIEF: Lead with their specific pain points. Show exactly how you solve merge conflicts, manual edits, sync issues. Demo should focus on developer workflow, not marketing features.",
        'expansion': "GROWTH ENABLEMENT: They're already investing in localization - help them scale. Focus on efficiency gains and reduced time-to-market for new languages.",
        'emerging': "EDUCATION FIRST: They may not know they have a problem yet. Lead with industry trends and case studies from similar companies.",
    }
    return approaches.get(opportunity_type, approaches['emerging'])


def _get_next_steps(opportunity_type: str, has_competitor: bool) -> list:
    """Get actionable next steps based on opportunity type."""
    steps = ['Review scan data and email draft', 'Find decision maker on LinkedIn']
    
    if has_competitor:
        steps.extend([
            'Research common pain points with their current TMS',
            'Prepare competitive positioning deck'
        ])
    elif opportunity_type == 'greenfield':
        steps.extend([
            'Prepare localization readiness assessment offer',
            'Find VP Eng or Technical Lead to target'
        ])
    elif opportunity_type == 'pain_driven':
        steps.extend([
            'Prepare demo focused on workflow automation',
            'Screenshot their frustration commits for personalization'
        ])
    else:
        steps.extend([
            'Research recent company news (expansion, funding)',
            'Prepare general platform demo'
        ])
    
    return steps


def _sse_log(message: str) -> str:
    """Format a log message for SSE."""
    return f"data: LOG:{message}\n\n"


def _sse_data(event_type: str, data: dict) -> str:
    """Format a data payload for SSE."""
    return f"data: {event_type}:{json.dumps(data)}\n\n"
