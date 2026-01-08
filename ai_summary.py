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
    """Build the sales intelligence prompt for Gemini."""
    company = scan_data.get('company_name', 'Unknown')
    org_name = scan_data.get('org_name', scan_data.get('org_login', ''))
    
    # Extract high-intent data
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

    # Format tech stack
    tech_stack_text = "None detected"
    if tech_stack.get('i18n_libraries'):
        libs = tech_stack['i18n_libraries']
        framework = tech_stack.get('primary_framework', 'Unknown')
        tech_stack_text = f"Libraries: {', '.join(libs)} | Framework: {framework}"

    # Format competitor detection
    competitor_text = "No competitor TMS detected"
    if competitor.get('is_using_competitor'):
        configs = [c['file'] for c in competitor.get('config_files_found', [])]
        deps = competitor.get('tms_in_dependencies', [])
        competitor_text = f"USING COMPETITOR: Configs={configs}, Deps={deps}"

    # Format frustration signals
    frustration_text = "No frustration signals"
    if frustration:
        pain_samples = [f"- \"{f['message'][:80]}...\" ({f['pain_indicator']})" for f in frustration[:5]]
        frustration_text = f"{len(frustration)} frustration signals detected:\n" + "\n".join(pain_samples)

    # Format developer-translator metric
    dev_metric_text = "No data"
    if dev_metric.get('total', 0) > 0:
        ratio = dev_metric.get('human_percentage', '0%')
        is_pain = "⚡ HIGH PAIN" if dev_metric.get('is_high_pain') else "Normal"
        dev_metric_text = f"{ratio} of translation edits by humans ({dev_metric['human_edits']}/{dev_metric['total']}) - {is_pain}"

    # Format bottleneck
    bottleneck_text = "No bottleneck detected"
    if bottleneck.get('is_bottleneck'):
        bottleneck_text = f"🚨 BOTTLENECK: @{bottleneck['bottleneck_user']} reviews {bottleneck['top_reviewer_ratio']*100:.0f}% of i18n PRs"

    # Format locale inventory
    inventory_text = "No locale files detected"
    if locale_inventory.get('locales_detected'):
        locales = locale_inventory['locales_detected'][:10]
        inventory_text = f"{len(locales)} languages: {', '.join(locales)}"

    # Format market insights
    market_text = "No market data"
    if market_insights.get('primary_market'):
        market_text = f"Primary: {market_insights['primary_market']} | {market_insights.get('narrative', '')}"

    # Greenfield section
    greenfield_section = ""
    if is_greenfield:
        greenfield_section = f"""
## 🎯 GREENFIELD OPPORTUNITY
- Total Stars: {total_stars}+
- NO i18n libraries detected
- NO locale files found
- NO competitor TMS detected
- This is a mature codebase with high velocity but NO localization infrastructure
- They are incurring technical debt that will compound as they scale internationally
"""

    # Format contributors
    contributors_text = "No human contributors detected"
    if scan_data.get('contributors'):
        contributors_text = "Top i18n Contributors:\n"
        for login, data in scan_data['contributors'].items():
            contributors_text += f"- @{login} ({data.get('name') or 'No name'}): bio=\"{data.get('bio') or 'No bio'}\", company=\"{data.get('company') or 'N/A'}\", i18n_commits={data['i18n_commits']}, frustration_count={data['frustration_count']}\n"

    prompt = f"""You are a Senior Sales Intelligence Analyst specializing in localization/i18n solutions.
Analyze this GitHub intelligence data for {company} and generate ACTIONABLE SALES ASSETS.

## COMPANY INTELLIGENCE
- Company: {company} ({org_name})
- Total Stars: {total_stars}
- Repos Scanned: {len(scan_data.get('repos_scanned', []))}
- Total Signals: {len(signals)}

## TECH STACK
{tech_stack_text}

## COMPETITOR DETECTION
{competitor_text}

## FRUSTRATION SIGNALS (PAIN POINTS)
{frustration_text}

## DEVELOPER-AS-TRANSLATOR METRIC
{dev_metric_text}

## REVIEWER BOTTLENECK
{bottleneck_text}

## LOCALE INVENTORY
{inventory_text}

## MARKET EXPANSION
{market_text}
{greenfield_section}

## CONTRIBUTORS & PROSPECTS
{contributors_text}

## YOUR TASK
Generate a JSON response with these EXACT fields for sales outreach:

{{
    "pain_point_analysis": "Specific pain points detected (e.g., 'Engineers are manually editing JSON files - 15 commits show merge conflicts')",
    "tech_stack_hook": "Specific SDK/integration pitch (e.g., 'Since you use Next.js with i18next, our Next.js SDK integrates directly with your existing setup')",
    "semantic_analysis": {{
        "severity": "critical|major|minor",
        "primary_pain_category": "sync_issues|manual_bottlenecks|technical_debt|market_expansion",
        "description": "Short summary of the 'semantic type' of activity detected (e.g. 'Emergency hotfixes for missing locale keys')"
    }},
    "top_prospects": [
        {{
            "login": "github_login",
            "name": "full_name",
            "role_inference": "e.g. Senior Frontend Engineer / Platform Lead",
            "outreach_strategy": "Why target this person? (e.g. 'Made 12 high-frustration commits to i18n files, likely feeling the pain directly')",
            "icebreaker": "Specific line about their actual activity"
        }}
    ],
    "opportunity_score": 1-10,
    "opportunity_type": "greenfield|competitor_displacement|pain_driven|expansion",
    "email_draft": {{
        "subject": "Specific subject referencing their data (e.g., '{{Company}}'s expansion into Mexico' or 'Fixing the translation merge conflicts')",
        "body": "3 sentences max. Connect their SPECIFIC signal to our solution. Reference actual data (e.g., 'I noticed your team has had 12 commits fixing translation sync issues...')"
    }},
    "key_findings": [
        {{"finding": "description", "significance": "high|medium|low", "sales_angle": "how to use this in conversation"}}
    ],
    "recommended_approach": "Strategic recommendation for this specific opportunity",
    "conversation_starters": ["Specific openers based on their data"],
    "risk_factors": ["What could prevent closing this deal"],
    "next_steps": ["Actionable items"]
}}

CRITICAL RULES:
1. Be SPECIFIC - reference actual numbers and signals from the data
2. In 'top_prospects', only include human contributors provided in the data. Rank by 'relevancy' (pain felt or influence).
3. 'semantic_analysis' should interpret the commit messages to find patterns (e.g. 'Fixing translation' looks like a hotfix).
4. Respond with ONLY the JSON, no additional text."""

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

        # Add metadata
        analysis['_source'] = 'gemini'
        analysis['_model'] = Config.GEMINI_MODEL

        return analysis

    except json.JSONDecodeError:
        # Fallback if JSON parsing fails
        return {
            'pain_point_analysis': response_text[:500] if response_text else 'Analysis unavailable',
            'tech_stack_hook': 'Further investigation needed',
            'opportunity_score': 5,
            'opportunity_type': 'unknown',
            'email_draft': {
                'subject': f"Localization opportunities at {scan_data.get('company_name', 'your company')}",
                'body': 'I noticed your team has been working on internationalization. Would love to discuss how we can help streamline your localization workflow.'
            },
            'key_findings': [],
            'recommended_approach': 'Manual review of findings recommended',
            'conversation_starters': [],
            'risk_factors': ['AI analysis could not be fully parsed'],
            'next_steps': ['Manual review of scan data recommended'],
            '_source': 'gemini_fallback',
            '_model': Config.GEMINI_MODEL,
            '_raw_response': response_text
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

    return {
        'pain_point_analysis': pain_point_analysis,
        'tech_stack_hook': tech_stack_hook,
        'semantic_analysis': semantic_analysis,
        'top_prospects': top_prospects,
        'opportunity_score': opportunity_score,
        'opportunity_type': opportunity_type,
        'email_draft': {
            'subject': email_subject,
            'body': email_body
        },
        'key_findings': key_findings,
        'recommended_approach': _get_recommended_approach(opportunity_type, competitor.get('is_using_competitor', False), is_greenfield),
        'conversation_starters': conversation_starters[:3],
        'risk_factors': risk_factors,
        'next_steps': _get_next_steps(opportunity_type, competitor.get('is_using_competitor', False)),
        '_source': 'fallback'
    }


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
