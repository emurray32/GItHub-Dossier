"""
Email Engine — Scalable personalized cold email generation for the dossier pipeline.

Generates signal-specific, persona-tuned cold emails at scale (500/week target).
Uses GPT-5 mini via Replit AI proxy (OpenAI client). Does NOT use temperature param.

Key features:
- Signal-specific template selection (dependency_injection, rfc_discussion, ghost_branch)
- Persona-aware tone adjustment (VP Eng, Head of Product, Dir Localization)
- Multi-variant generation (3 per contact) with specificity scoring
- CAN-SPAM compliance metadata
- Apollo variable integration ({{first_name}}, {{company}}, {{sender_first_name}})
"""
import json
import os
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    OPENAI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Signal type → email template mapping
# ---------------------------------------------------------------------------

SIGNAL_TEMPLATES = {
    'dependency_injection': {
        'label': 'Smoking Gun',
        'priority': 1,
        'hook': 'I noticed your team added `{library}` to `{repo}`{age_clause}.',
        'pain': 'That usually means locale files and manual JSON wrangling are next. Phrase automates all of that via GitHub Sync — your devs never touch translation files.',
        'fallback_hook': 'I noticed your team recently added an i18n library.',
    },
    'rfc_discussion': {
        'label': 'RFC / Discussion',
        'priority': 2,
        'hook': 'I saw the discussion about {topic} in `{repo}`.',
        'pain': 'When the team is still deciding how to handle translations, that is the best time to wire in automation. Phrase plugs into your CI/CD so localization scales with your sprint cadence.',
        'fallback_hook': 'I saw your team discussing internationalization plans.',
    },
    'ghost_branch': {
        'label': 'Ghost Branch',
        'priority': 3,
        'hook': 'I noticed the `{branch}` branch in `{repo}`{age_clause}.',
        'pain': 'Looks like i18n work is actively in progress. Phrase can plug into that workflow today — GitHub Sync keeps translation files in lockstep with your branch.',
        'fallback_hook': 'I noticed active i18n branch work in your repositories.',
    },
    'documentation_intent': {
        'label': 'Documentation Intent',
        'priority': 4,
        'hook': 'I noticed localization mentioned in your `{file_path}` in `{repo}`.',
        'pain': 'When internationalization shows up on the roadmap, that is usually when teams evaluate whether to build or buy the translation pipeline. Phrase gives you the API and GitHub integration to skip the DIY phase entirely.',
        'fallback_hook': 'I noticed internationalization on your project roadmap.',
    },
}

# Priority order for signal selection
SIGNAL_PRIORITY_ORDER = ['dependency_injection', 'rfc_discussion', 'ghost_branch', 'documentation_intent']

# ---------------------------------------------------------------------------
# Persona → tone/angle mapping
# ---------------------------------------------------------------------------

PERSONA_TONES = {
    'vp_engineering': {
        'match_titles': ['vp of engineering', 'vp engineering', 'vice president engineering',
                         'vice president of engineering', 'svp engineering', 'evp engineering',
                         'head of engineering', 'cto', 'chief technology officer'],
        'system_prompt': 'You write emails for a technical engineering audience. Be precise, reference specific repos/libraries, and focus on developer velocity and CI/CD integration. Avoid marketing language.',
        'angle': 'developer velocity and CI/CD automation',
        'cta_style': 'Open to seeing how we fit into your CI/CD?',
    },
    'head_of_product': {
        'match_titles': ['head of product', 'vp product', 'vp of product', 'vice president product',
                         'chief product officer', 'cpo', 'director of product', 'product director',
                         'svp product', 'group product manager'],
        'system_prompt': 'You write emails for product leaders. Focus on time-to-market, reducing localization bottlenecks in the release cycle, and reaching new markets faster. Light on technical details.',
        'angle': 'faster time-to-market for international launches',
        'cta_style': 'Worth a quick look at how we speed up international launches?',
    },
    'dir_localization': {
        'match_titles': ['director of localization', 'localization director', 'head of localization',
                         'vp localization', 'localization manager', 'senior localization manager',
                         'localization lead', 'director of globalization', 'globalization manager',
                         'internationalization manager', 'i18n manager', 'translation manager'],
        'system_prompt': 'You write emails for localization professionals. They know the space — reference TMS capabilities, connector ecosystem, and workflow automation. Show you understand their pain (manual handoffs, lack of dev context, QA overhead).',
        'angle': 'TMS workflow automation and developer-connected localization',
        'cta_style': 'Open to comparing workflows?',
    },
    'default': {
        'match_titles': [],
        'system_prompt': 'You write peer-to-peer cold emails for a technical product. Be concise, reference specific findings, and avoid sounding like a sales pitch.',
        'angle': 'automating localization infrastructure',
        'cta_style': 'Worth a look?',
    },
}

# ---------------------------------------------------------------------------
# Variant definitions
# ---------------------------------------------------------------------------

VARIANT_STYLES = {
    'A': {
        'name': 'Direct/Technical',
        'instruction': 'Write a direct, technically precise email. Lead with the specific library/branch/file you found. Reference repo names and technical terms. Keep it peer-to-peer and engineering-focused.',
    },
    'B': {
        'name': 'Business Value',
        'instruction': 'Write a business-value-focused email. Lead with the outcome (faster launches, fewer manual steps, less engineering time on translation files). Still reference the specific signal but frame it in terms of business impact.',
    },
    'C': {
        'name': 'Social Proof',
        'instruction': 'Write a concise email that references the signal and briefly mentions that similar-stage companies use Phrase to automate this exact workflow. Do NOT name specific customers. Frame it as "teams at your stage" or "companies making this same move." Keep it natural, not salesy.',
    },
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def _get_openai_client() -> Optional[OpenAI]:
    """Return an OpenAI client configured for the Replit AI proxy, or None."""
    if not OPENAI_AVAILABLE:
        return None
    api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
    base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')
    if not api_key or not base_url:
        return None
    return OpenAI(api_key=api_key, base_url=base_url)


def _classify_persona(title: str) -> str:
    """Map a contact's job title to a persona key."""
    if not title:
        return 'default'
    title_lower = title.lower().strip()
    # Check more specific personas first (localization), then broader ones
    check_order = ['dir_localization', 'head_of_product', 'vp_engineering']
    for persona_key in check_order:
        persona = PERSONA_TONES[persona_key]
        for match in persona['match_titles']:
            # Word boundary check: match must appear as whole words, not as substring
            pattern = r'(?:^|[\s,/\-])' + re.escape(match) + r'(?:$|[\s,/\-])'
            if re.search(pattern, title_lower):
                return persona_key
    return 'default'


def _select_strongest_signal(signals: list) -> tuple:
    """Pick the strongest signal from a list and return (signal_type, signal_data).

    signals: list of dicts from scan_signals table (signal_type, description, file_path, etc.)
    Returns: (signal_type_key, signal_dict) or ('none', {})
    """
    if not signals:
        return ('none', {})

    # Sort by priority order
    for target_type in SIGNAL_PRIORITY_ORDER:
        for sig in signals:
            st = sig.get('signal_type', '')
            # Normalize: rfc_discussion_high / rfc_discussion_medium -> rfc_discussion
            normalized = st.replace('_high', '').replace('_medium', '')
            if normalized == target_type:
                return (target_type, sig)

    # Fallback: return first signal
    first = signals[0]
    st = first.get('signal_type', 'unknown')
    normalized = st.replace('_high', '').replace('_medium', '')
    return (normalized, first)


def _extract_signal_details(signal_type: str, signal_data: dict) -> dict:
    """Extract structured details from a signal for template interpolation."""
    desc = signal_data.get('description', '')
    file_path = signal_data.get('file_path', '')
    age_days = signal_data.get('age_in_days')

    details = {
        'library': '',
        'repo': '',
        'branch': '',
        'topic': '',
        'file_path': file_path or '',
        'age_in_days': age_days,
        'age_clause': '',
    }

    details['age_clause'] = _build_age_aware_clause(age_days)

    # Parse description for structured data
    if signal_type == 'dependency_injection':
        # Description often like: "Found react-i18next in frontend-app/package.json"
        parts = desc.split(' in ')
        if len(parts) >= 2:
            # Extract library name - look for backtick-wrapped or first word after "Found"
            lib_part = parts[0]
            for word in lib_part.split():
                word_clean = word.strip('`').strip("'").strip('"')
                if word_clean and word_clean.lower() not in ('found', 'detected', 'installed', 'added'):
                    details['library'] = word_clean
                    break
            # Extract repo from file path
            repo_part = parts[1].strip()
            if '/' in repo_part:
                details['repo'] = repo_part.split('/')[0]
            else:
                details['repo'] = repo_part

    elif signal_type == 'rfc_discussion':
        # Description: "Discussion about internationalization in org/repo#123"
        details['topic'] = 'internationalization'
        if ' about ' in desc.lower():
            after_about = desc.lower().split(' about ', 1)[1]
            topic_end = after_about.find(' in ')
            if topic_end > 0:
                details['topic'] = after_about[:topic_end].strip()
            else:
                details['topic'] = after_about[:60].strip()
        if ' in ' in desc:
            repo_part = desc.split(' in ')[-1].strip()
            repo_part = repo_part.split('#')[0].split('/')[0] if '/' in repo_part else repo_part
            details['repo'] = repo_part

    elif signal_type == 'ghost_branch':
        # Description: "Branch feature/i18n in org/repo"
        branch_candidates = ['feature/i18n', 'feature/l10n', 'feature/localization',
                             'i18n', 'l10n', 'localization', 'translations']
        details['branch'] = 'i18n'
        desc_lower = desc.lower()
        for bc in branch_candidates:
            if bc in desc_lower:
                details['branch'] = bc
                break
        if ' in ' in desc:
            details['repo'] = desc.split(' in ')[-1].strip().split('/')[0]

    elif signal_type == 'documentation_intent':
        if file_path:
            details['file_path'] = file_path.split('/')[-1] if '/' in file_path else file_path
        if ' in ' in desc:
            details['repo'] = desc.split(' in ')[-1].strip().split('/')[0]

    return details


def _build_age_aware_clause(age_days) -> str:
    """Build a signal-age-aware temporal clause for email hooks.

    < 30 days:   'recently'
    30-90 days:  'earlier this quarter'
    90-180 days: 'earlier this year'
    180+ days:   '' (don't use temporal language — signal is old)
    """
    if age_days is None or age_days <= 0:
        return ''
    if age_days <= 30:
        return ' recently'
    elif age_days <= 90:
        return ' earlier this quarter'
    elif age_days <= 180:
        return ' earlier this year'
    return ''


def _build_hook_line(signal_type: str, template: dict, details: dict) -> str:
    """Build the opening hook line using the template and extracted details."""
    hook = template.get('hook', '')
    try:
        formatted = hook.format(**details)
        # Check if any placeholder was left empty / unfilled
        if '{' not in formatted and formatted.strip():
            return formatted
    except (KeyError, IndexError) as e:
        logging.debug(f"[EMAIL] Hook template format error for {signal_type}: {e}")
    return template.get('fallback_hook', 'I noticed your team is working on internationalization.')


def _score_email_specificity(email_text: str, signal_details: dict) -> int:
    """Score an email 0-100 on specificity (signal references, personalization depth)."""
    score = 0
    text_lower = email_text.lower()

    # Signal reference scoring
    if signal_details.get('library') and signal_details['library'].lower() in text_lower:
        score += 25
    if signal_details.get('repo') and signal_details['repo'].lower() in text_lower:
        score += 20
    if signal_details.get('branch') and signal_details['branch'].lower() in text_lower:
        score += 20

    # Apollo variable usage
    if '{{first_name}}' in email_text:
        score += 10
    if '{{company}}' in email_text:
        score += 5
    if '{{sender_first_name}}' in email_text:
        score += 5

    # Brevity bonus (under 100 words)
    word_count = len(email_text.split())
    if word_count <= 100:
        score += 10
    elif word_count <= 120:
        score += 5

    # Soft CTA (question mark at end)
    if '?' in email_text[-50:]:
        score += 5

    return min(score, 100)


def _build_canspam_footer() -> str:
    """Return a CAN-SPAM compliant footer block.

    Apollo handles actual unsubscribe mechanics; we include placeholders that
    Apollo will replace at send time.
    """
    return (
        '\n\n---\n'
        'Phrase SE GmbH | Kriehubergasse 22/1, 1050 Vienna, Austria\n'
        '{{unsubscribe}}'
    )


def generate_personalized_emails(
    contact: dict,
    signals: list,
    persona_override: str = None,
    campaign_prompt: str = '',
    account_data: dict = None,
) -> dict:
    """Generate 3 email variants for a contact, score them, and return the best + all variants.

    Args:
        contact: enrollment_contacts row dict (first_name, last_name, title, company_name, etc.)
        signals: list of scan_signals rows for this company
        persona_override: force a persona key instead of auto-detecting from title
        campaign_prompt: optional BDR prompt from the campaign
        account_data: optional scorecard/account enrichment data

    Returns:
        dict with keys:
            best_variant: str ('A', 'B', or 'C')
            best_subject: str
            best_body: str
            variants: dict of {A: {subject, body, score}, B: {...}, C: {...}}
            signal_type: str
            persona: str
            canspam_footer: str
    """
    client = _get_openai_client()
    if not client:
        logger.warning('OpenAI client not available, returning fallback email')
        return _fallback_email(contact, signals)

    # Determine persona
    persona_key = persona_override or _classify_persona(contact.get('title', ''))
    persona = PERSONA_TONES.get(persona_key, PERSONA_TONES['default'])

    # Select strongest signal
    signal_type, signal_data = _select_strongest_signal(signals)
    template = SIGNAL_TEMPLATES.get(signal_type, SIGNAL_TEMPLATES['dependency_injection'])
    details = _extract_signal_details(signal_type, signal_data)
    hook_line = _build_hook_line(signal_type, template, details)

    # Build account context
    account_ctx = _build_account_context(account_data)

    company = contact.get('company_name', '').title()
    first_name = contact.get('first_name', '')
    title = contact.get('title', '')

    # Generate 3 variants
    variants = {}
    for variant_key, variant_def in VARIANT_STYLES.items():
        prompt = _build_variant_prompt(
            variant_key=variant_key,
            variant_instruction=variant_def['instruction'],
            company=company,
            first_name=first_name,
            title=title,
            persona=persona,
            hook_line=hook_line,
            pain_line=template.get('pain', ''),
            account_ctx=account_ctx,
            campaign_prompt=campaign_prompt,
            signal_type_label=template.get('label', signal_type),
        )

        try:
            response = client.chat.completions.create(
                model='gpt-5-mini',
                messages=[
                    {'role': 'system', 'content': persona['system_prompt']},
                    {'role': 'user', 'content': prompt},
                ],
                response_format={'type': 'json_object'},
                max_completion_tokens=2048,
            )
            text = response.choices[0].message.content.strip()
            # Strip markdown fences if present
            if text.startswith('```json'):
                text = text[7:]
            if text.startswith('```'):
                text = text[3:]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()

            email = json.loads(text)
            subject = email.get('subject', '')
            body = email.get('body', '')

            score = _score_email_specificity(subject + ' ' + body, details)
            variants[variant_key] = {
                'subject': subject,
                'body': body,
                'score': score,
                'variant_name': variant_def['name'],
            }

        except Exception as e:
            logger.warning(f'Variant {variant_key} generation failed: {e}')
            variants[variant_key] = {
                'subject': f'{signal_type} signal at {{{{company}}}}',
                'body': f'Hey {{{{first_name}}}},\n\n{hook_line}\n\n{template.get("pain", "")}\n\nWorth a look?\n\n{{{{sender_first_name}}}}',
                'score': 10,
                'variant_name': variant_def['name'],
                'error': str(e)[:200],
            }

    # Select best variant by score
    best_key = max(variants, key=lambda k: variants[k]['score'])

    canspam = _build_canspam_footer()

    return {
        'best_variant': best_key,
        'best_subject': variants[best_key]['subject'],
        'best_body': variants[best_key]['body'],
        'variants': variants,
        'signal_type': signal_type,
        'persona': persona_key,
        'canspam_footer': canspam,
    }


def generate_email_sequence(
    contact: dict,
    signals: list,
    persona_override: str = None,
    campaign_prompt: str = '',
    account_data: dict = None,
    campaign_links: list = None,
    num_emails: int = 4,
) -> dict:
    """Generate a full multi-email sequence in a single AI call.

    Produces a cohesive 4-email sequence where each email progresses naturally:
    Email 1 = hook + value prop, Email 2 = different angle, Email 3 = lighter touch,
    Email 4 = breakup. Designed for Apollo custom field injection.

    Args:
        contact: dict with first_name, last_name, title, company_name, company_domain
        signals: list of scan_signals rows for this company
        persona_override: force a persona key instead of auto-detecting
        campaign_prompt: optional campaign instructions (tone, formality, key messages)
        account_data: optional scorecard/account enrichment data
        campaign_links: optional list of {"text": "Phrase Strings", "url": "https://..."}
        num_emails: number of emails to generate (default 4)

    Returns:
        dict with keys:
            emails: list of {"position": 1-4, "subject": str, "body": str}
            persona: str persona key used
            signal_type: str strongest signal type
            signal_details: dict of extracted signal fields
            specificity_score: int 0-100 average across all emails
            canspam_footer: str
    """
    client = _get_openai_client()
    if not client:
        logger.warning('OpenAI client not available, returning fallback sequence')
        return _fallback_sequence(contact, signals, num_emails)

    # Determine persona
    persona_key = persona_override or _classify_persona(contact.get('title', ''))
    persona = PERSONA_TONES.get(persona_key, PERSONA_TONES['default'])

    # Select strongest signal
    signal_type, signal_data = _select_strongest_signal(signals)
    template = SIGNAL_TEMPLATES.get(signal_type, SIGNAL_TEMPLATES['dependency_injection'])
    details = _extract_signal_details(signal_type, signal_data)
    hook_line = _build_hook_line(signal_type, template, details)

    # Build account context
    account_ctx = _build_account_context(account_data)

    company = contact.get('company_name', '').title()
    first_name = contact.get('first_name', '')
    title = contact.get('title', '')

    # Build the sequence prompt
    prompt = _build_sequence_prompt(
        company=company,
        first_name=first_name,
        title=title,
        persona=persona,
        hook_line=hook_line,
        pain_line=template.get('pain', ''),
        account_ctx=account_ctx,
        campaign_prompt=campaign_prompt,
        campaign_links=campaign_links,
        signal_type_label=template.get('label', signal_type),
        num_emails=num_emails,
    )

    try:
        response = client.chat.completions.create(
            model='gpt-5-mini',
            messages=[
                {'role': 'system', 'content': persona['system_prompt']},
                {'role': 'user', 'content': prompt},
            ],
            response_format={'type': 'json_object'},
            max_completion_tokens=4096,
        )
        text = response.choices[0].message.content.strip()
        # Strip markdown fences
        if text.startswith('```json'):
            text = text[7:]
        if text.startswith('```'):
            text = text[3:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

        data = json.loads(text)
        emails = data.get('emails', [])

        # Validate and score each email
        scored_emails = []
        total_score = 0
        for i, email in enumerate(emails[:num_emails]):
            score = _score_email_specificity(
                (email.get('subject', '') + ' ' + email.get('body', '')),
                details
            )
            total_score += score
            scored_emails.append({
                'position': i + 1,
                'subject': email.get('subject', ''),
                'body': email.get('body', ''),
                'score': score,
            })

        avg_score = total_score // len(scored_emails) if scored_emails else 0

        canspam = _build_canspam_footer()

        return {
            'emails': scored_emails,
            'persona': persona_key,
            'signal_type': signal_type,
            'signal_details': details,
            'specificity_score': avg_score,
            'canspam_footer': canspam,
        }

    except Exception as e:
        # Propagate auth/rate-limit errors instead of falling back silently
        err_str = str(e).lower()
        if any(k in err_str for k in ('401', '403', 'unauthorized', 'authentication', '429', 'rate limit')):
            raise
        logger.error(f'Email sequence generation failed: {e}')
        return _fallback_sequence(contact, signals, num_emails)


def generate_batch_emails(
    contacts: list,
    signals_by_company: dict,
    campaign_prompt: str = '',
    account_data_by_company: dict = None,
) -> list:
    """Generate emails for a batch of contacts.

    Args:
        contacts: list of enrollment_contacts row dicts
        signals_by_company: dict mapping company_name -> list of signal rows
        campaign_prompt: optional BDR prompt
        account_data_by_company: optional dict mapping company_name -> account enrichment data

    Returns:
        list of (contact_id, result_dict) tuples
    """
    account_data_by_company = account_data_by_company or {}
    results = []
    for contact in contacts:
        company = contact.get('company_name', '')
        signals = signals_by_company.get(company, [])
        account_data = account_data_by_company.get(company)
        try:
            result = generate_personalized_emails(
                contact=contact,
                signals=signals,
                campaign_prompt=campaign_prompt,
                account_data=account_data,
            )
            results.append((contact['id'], result))
        except Exception as e:
            logger.error(f'Email generation failed for contact {contact.get("id")}: {e}')
            results.append((contact['id'], {
                'error': str(e)[:300],
                'best_variant': 'A',
                'best_subject': '',
                'best_body': '',
                'variants': {},
                'signal_type': 'none',
                'persona': 'default',
                'canspam_footer': _build_canspam_footer(),
            }))
    return results


def preview_email(
    contact: dict,
    signals: list,
    variant: str = None,
    account_data: dict = None,
) -> dict:
    """Generate an email preview for a single contact.

    If variant is specified ('A', 'B', 'C'), returns that variant.
    Otherwise returns the best-scored variant.
    """
    result = generate_personalized_emails(
        contact=contact,
        signals=signals,
        account_data=account_data,
    )

    if variant and variant in result.get('variants', {}):
        v = result['variants'][variant]
        return {
            'subject': v['subject'],
            'body': v['body'],
            'score': v['score'],
            'variant': variant,
            'variant_name': v['variant_name'],
            'signal_type': result['signal_type'],
            'persona': result['persona'],
            'canspam_footer': result['canspam_footer'],
        }

    return {
        'subject': result['best_subject'],
        'body': result['best_body'],
        'score': result['variants'].get(result['best_variant'], {}).get('score', 0),
        'variant': result['best_variant'],
        'variant_name': result['variants'].get(result['best_variant'], {}).get('variant_name', ''),
        'signal_type': result['signal_type'],
        'persona': result['persona'],
        'canspam_footer': result['canspam_footer'],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_variant_prompt(
    variant_key: str,
    variant_instruction: str,
    company: str,
    first_name: str,
    title: str,
    persona: dict,
    hook_line: str,
    pain_line: str,
    account_ctx: str,
    campaign_prompt: str,
    signal_type_label: str,
) -> str:
    """Build the GPT prompt for a single variant."""
    from ai_summary import _load_cold_outreach_skill, _load_email_skills
    skill_content = _load_cold_outreach_skill()
    granular_skills = _load_email_skills(email_position=1)

    skill_section = ''
    if granular_skills:
        skill_section = f"""
GRANULAR OUTREACH SKILL INSTRUCTIONS (follow these EXACTLY):
{granular_skills}
"""
    elif skill_content:
        skill_section = f"""
COLD OUTREACH SKILL INSTRUCTIONS (follow these EXACTLY):
{skill_content}
"""

    return f"""Generate a cold email for {company}.

VARIANT: {variant_key} — {variant_instruction}

SIGNAL FOUND ({signal_type_label}):
{hook_line}

VALUE PROPOSITION:
{pain_line}

CONTACT:
- Name: {first_name} (use {{{{first_name}}}} in the actual email)
- Title: {title}
- Company: {company} (use {{{{company}}}} in subject line)
- Persona angle: {persona.get('angle', '')}

{f'ACCOUNT CONTEXT:{chr(10)}{account_ctx}' if account_ctx else ''}

{f'CAMPAIGN INSTRUCTIONS: {campaign_prompt}' if campaign_prompt else ''}

{skill_section}

STRICT RULES:
1. Start with "Hey {{{{first_name}}}},"
2. The first sentence after greeting MUST reference the specific signal found above
3. Total body MUST be under 100 words
4. Never write a paragraph longer than 2 sentences
5. Use double line breaks between thoughts
6. End with a soft CTA: "{persona.get('cta_style', 'Worth a look?')}"
7. Sign off with "{{{{sender_first_name}}}}"
8. Subject line MUST include {{{{company}}}} — NEVER include {{{{first_name}}}} in subject
9. Subject line should reference the signal (library name, branch, etc.)
10. Tone: peer-to-peer, not salesy

DO mention: automation, API, GitHub integration, infrastructure, continuous localization
DO NOT mention: high quality translations, professional linguists

Return ONLY valid JSON: {{"subject": "...", "body": "..."}}"""


def _build_account_context(account_data: dict) -> str:
    """Build account context string from enrichment data."""
    if not account_data:
        return ''
    parts = []
    if account_data.get('annual_revenue'):
        parts.append(f"Revenue: {account_data['annual_revenue']}")
    if account_data.get('locale_count'):
        parts.append(f"Locales detected: {account_data['locale_count']}")
    systems_raw = account_data.get('systems_json', '{}')
    try:
        systems = json.loads(systems_raw) if systems_raw else {}
    except (json.JSONDecodeError, TypeError):
        systems = {}
    active = [k for k, v in systems.items() if v]
    if active:
        parts.append(f"Systems: {', '.join(active)}")
    evidence = (account_data.get('evidence_summary', '') or '')[:200]
    if evidence:
        parts.append(f"Evidence: {evidence}")
    notes = (account_data.get('notes', '') or '')[:200]
    if notes:
        parts.append(f"BDR Notes: {notes}")
    report_context = (account_data.get('report_context', '') or '')[:300]
    if report_context:
        parts.append(f"Report Findings: {report_context}")
    if parts:
        return '\n'.join(f'- {p}' for p in parts)
    return ''


def _build_sequence_prompt(
    company: str,
    first_name: str,
    title: str,
    persona: dict,
    hook_line: str,
    pain_line: str,
    account_ctx: str,
    campaign_prompt: str,
    campaign_links: list,
    signal_type_label: str,
    num_emails: int = 4,
) -> str:
    """Build the prompt for generating a full email sequence in one AI call."""
    from ai_summary import _load_cold_outreach_skill, _load_email_skills

    # Load granular skills for each email position and combine
    all_position_skills = []
    for pos in range(1, num_emails + 1):
        pos_skills = _load_email_skills(email_position=pos)
        if pos_skills:
            all_position_skills.append(f"--- EMAIL {pos} SKILL RULES ---\n{pos_skills}")

    skill_section = ''
    if all_position_skills:
        # Use first-touch skills as the primary (they include vocabulary/hooks)
        # Plus position-specific rules for each email
        first_touch_skills = _load_email_skills(email_position=1)
        skill_section = f"\nGRANULAR OUTREACH SKILL INSTRUCTIONS (follow these EXACTLY):\n{first_touch_skills}\n"
    else:
        skill_content = _load_cold_outreach_skill()
        if skill_content:
            skill_section = f"\nCOLD OUTREACH SKILL INSTRUCTIONS (follow these EXACTLY):\n{skill_content}\n"

    links_section = ''
    if campaign_links:
        link_lines = [f'- "{l["text"]}": {l["url"]}' for l in campaign_links if l.get('text') and l.get('url')]
        if link_lines:
            links_section = f"\nCAMPAIGN LINKS — hyperlink these phrases naturally in the email body:\n" + '\n'.join(link_lines) + "\n"

    return f"""Generate a {num_emails}-email cold outreach sequence for {company}.

SIGNAL FOUND ({signal_type_label}):
{hook_line}

VALUE PROPOSITION:
{pain_line}

CONTACT:
- Name: {first_name} (use {{{{first_name}}}} in the actual email)
- Title: {title}
- Company: {company} (use {{{{company}}}} in subject lines)
- Persona angle: {persona.get('angle', '')}

{f'ACCOUNT CONTEXT:{chr(10)}{account_ctx}' if account_ctx else ''}

{f'CAMPAIGN INSTRUCTIONS: {campaign_prompt}' if campaign_prompt else ''}
{links_section}
{skill_section}

SEQUENCE STRUCTURE:
- Email 1 (Hook + Value Prop): Open with the specific signal above. Present core value proposition. One clear CTA.
- Email 2 (Different Angle): Different approach — new pain point, social proof, or industry trend. Do NOT repeat the Email 1 hook.
- Email 3 (Lighter Touch): Shorter. Add value — share an insight, resource, or industry observation. Low-pressure.
- Email 4 (Breakup): Final attempt. Very short (under 50 words). Respectful close.

STRICT RULES:
1. Each email body MUST be under 150 words
2. Subject lines MUST be under 50 characters. No clickbait. No ALL CAPS. No emojis
3. Email 1 MUST reference the specific signal found above
4. Each email takes a DIFFERENT angle — never repeat the same hook
5. Every email starts with "Hey {{{{first_name}}}},"
6. Every email signs off with "{{{{sender_first_name}}}}"
7. Each email ends with ONE clear call-to-action
8. Subject lines should include {{{{company}}}} — NEVER include {{{{first_name}}}} in subject
9. Tone: conversational, peer-to-peer, human-sounding. NOT salesy
10. Use Apollo variables: {{{{first_name}}}}, {{{{company}}}}, {{{{sender_first_name}}}}
11. DO mention: automation, API, GitHub integration, infrastructure, continuous localization
12. DO NOT mention: high quality translations, professional linguists
13. DO NOT fabricate Phrase product features or fake case studies
14. CTA style for this persona: "{persona.get('cta_style', 'Worth a look?')}"

Return ONLY valid JSON:
{{"emails": [
  {{"position": 1, "subject": "...", "body": "..."}},
  {{"position": 2, "subject": "...", "body": "..."}},
  {{"position": 3, "subject": "...", "body": "..."}},
  {{"position": 4, "subject": "...", "body": "..."}}
]}}"""


def _fallback_sequence(contact: dict, signals: list, num_emails: int = 4) -> dict:
    """Generate a template-based email sequence when AI is unavailable."""
    signal_type, signal_data = _select_strongest_signal(signals)
    template = SIGNAL_TEMPLATES.get(signal_type, SIGNAL_TEMPLATES['dependency_injection'])
    details = _extract_signal_details(signal_type, signal_data)
    hook = _build_hook_line(signal_type, template, details)
    pain = template.get('pain', 'Phrase automates localization via GitHub Sync — your devs never touch translation files.')

    emails = [
        {
            'position': 1,
            'subject': f'i18n in {{{{company}}}}',
            'body': f'Hey {{{{first_name}}}},\n\n{hook}\n\n{pain}\n\nWorth a look?\n\n{{{{sender_first_name}}}}',
            'score': 15,
        },
        {
            'position': 2,
            'subject': f'Quick follow-up — {{{{company}}}}',
            'body': f'Hey {{{{first_name}}}},\n\nCircling back on my last note. Teams at your stage typically spend 40% of their i18n time on manual file handoffs.\n\nPhrase eliminates that with GitHub Sync — locale files stay in lockstep with your branches.\n\nOpen to a quick look?\n\n{{{{sender_first_name}}}}',
            'score': 10,
        },
        {
            'position': 3,
            'subject': f'Thought this might help — {{{{company}}}}',
            'body': f'Hey {{{{first_name}}}},\n\nOne thing I see teams overlook early: setting up continuous localization before launch saves weeks of catch-up later.\n\nHappy to share what that looks like in practice if it is useful.\n\n{{{{sender_first_name}}}}',
            'score': 10,
        },
        {
            'position': 4,
            'subject': f'Closing the loop — {{{{company}}}}',
            'body': f'Hey {{{{first_name}}}},\n\nJust want to make sure I am not cluttering your inbox. If localization tooling is not on the radar right now, no worries at all.\n\nEither way, happy to help whenever timing is right.\n\n{{{{sender_first_name}}}}',
            'score': 10,
        },
    ]

    return {
        'emails': emails[:num_emails],
        'persona': 'default',
        'signal_type': signal_type,
        'signal_details': details,
        'specificity_score': 12,
        'canspam_footer': _build_canspam_footer(),
        'is_fallback': True,
    }


def _fallback_email(contact: dict, signals: list) -> dict:
    """Generate a simple template-based email when AI is unavailable."""
    signal_type, signal_data = _select_strongest_signal(signals)
    template = SIGNAL_TEMPLATES.get(signal_type, SIGNAL_TEMPLATES['dependency_injection'])
    details = _extract_signal_details(signal_type, signal_data)
    hook = _build_hook_line(signal_type, template, details)
    pain = template.get('pain', 'Phrase automates localization via GitHub Sync — your devs never touch translation files.')

    subject = f"i18n in {{{{company}}}}"
    body = (
        f"Hey {{{{first_name}}}},\n\n"
        f"{hook}\n\n"
        f"{pain}\n\n"
        f"Worth a look?\n\n"
        f"{{{{sender_first_name}}}}"
    )

    variant_data = {
        'subject': subject,
        'body': body,
        'score': 15,
        'variant_name': 'Fallback',
    }

    return {
        'best_variant': 'A',
        'best_subject': subject,
        'best_body': body,
        'variants': {'A': variant_data},
        'signal_type': signal_type,
        'persona': 'default',
        'canspam_footer': _build_canspam_footer(),
    }
