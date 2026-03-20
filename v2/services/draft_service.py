"""
Draft Service — generate, regenerate, edit, and approve email drafts.

Each prospect gets a multi-step email sequence stored in the drafts table.
Drafts flow through: generated -> edited -> approved -> enrolled.
LLM generation uses the shared llm_client module (Gemini Flash primary, OpenAI fallback).
"""
import json
import logging
import os
import re
from typing import Optional, List

from v2.db import (
    db_connection, insert_returning_id, row_to_dict, rows_to_dicts,
    safe_json_dumps, safe_json_loads,
)
from v2.services.llm_client import llm_generate as _llm_generate, get_active_provider, get_active_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Template-based fallback (when LLM is unavailable)
# ---------------------------------------------------------------------------

_STEP_TEMPLATES = {
    1: {
        'subject': 'i18n at {{company}}',
        'body': (
            'Hey {{first_name}},\n\n'
            '{hook}\n\n'
            'Phrase connects directly to your repos via GitHub Sync -- '
            'locale files stay in lockstep with your branches, and your '
            'devs never have to manually manage translation files.\n\n'
            'Here is a quick overview of how it works: '
            'https://phrase.com/blog/posts/i18n-guide\n\n'
            'Worth a quick look?\n\n'
            '{{sender_first_name}}'
        ),
    },
    2: {
        'subject': 'Quick follow-up -- {{company}}',
        'body': (
            'Hey {{first_name}},\n\n'
            'Circling back on my last note. Teams at your stage typically spend '
            '40% of their i18n time on manual file handoffs between devs and translators.\n\n'
            'Phrase eliminates that with GitHub Sync and an API-first architecture. '
            'Your CI/CD pipeline triggers translations automatically -- no Slack messages, '
            'no spreadsheets, no waiting.\n\n'
            'Companies like Shopify, Decathlon, and Phorest use Phrase to ship '
            'localized products faster.\n\n'
            'Open to seeing how it would fit your workflow?\n\n'
            '{{sender_first_name}}'
        ),
    },
    3: {
        'subject': 're: i18n at {{company}}',
        'body': (
            'Hey {{first_name}},\n\n'
            'One more thought -- Phrase also supports machine translation '
            'built into the workflow, so your team can get first-pass translations '
            'instantly and focus human reviewers on what matters.\n\n'
            'Here is what pricing looks like if helpful: '
            'https://phrase.com/pricing\n\n'
            'Happy to walk through a quick demo if the timing works.\n\n'
            '{{sender_first_name}}'
        ),
    },
    4: {
        'subject': 're: i18n at {{company}}',
        'body': (
            'Hey {{first_name}},\n\n'
            'Just want to make sure I am not cluttering your inbox. If localization '
            'tooling is not on the radar right now, no worries at all.\n\n'
            'Either way, happy to help whenever timing is right.\n\n'
            '{{sender_first_name}}'
        ),
    },
}


def _resolve_fallback_sender_name(user_email: Optional[str] = None) -> str:
    """Derive a readable sender name for fallback drafts."""
    email = (user_email or os.environ.get('APOLLO_SENDER_EMAIL') or '').strip()
    if not email or '@' not in email:
        return 'Phrase'

    local_part = email.split('@', 1)[0]
    first_token = re.split(r'[._+-]+', local_part)[0].strip()
    return first_token.title() if first_token else 'Phrase'


def _generate_template_draft(
    step: int,
    prospect: dict,
    signal: dict,
    sender_name: Optional[str] = None,
) -> dict:
    """Generate a template-based draft for one sequence step.

    Fallback drafts are shown directly in the UI, so resolve the obvious
    placeholders to readable copy instead of leaking template variables.
    """
    tmpl = _STEP_TEMPLATES.get(step, _STEP_TEMPLATES[4])
    hook = "Your team seems to be building out localization infrastructure."
    if signal:
        desc = signal.get('signal_description', '')
        sig_type = (signal.get('signal_type') or '').replace('_', ' ')
        angle = signal.get('outreach_angle', '')
        if angle:
            hook = f"{desc[:80]}. {angle[:80]}" if desc else angle[:160]
        elif desc and sig_type:
            hook = f"I came across some {sig_type} activity at {{{{company}}}} -- {desc[:100]}"
        elif desc:
            hook = f"I came across some interesting activity at {{{{company}}}} -- {desc[:120]}"
    company = (
        prospect.get('company_name')
        or (signal or {}).get('company_name')
        or 'your team'
    )
    first_name = prospect.get('first_name') or prospect.get('full_name') or 'there'
    sender_name = sender_name or 'Phrase'

    subject = tmpl['subject'].replace('{{company}}', company)
    body = tmpl['body'].replace('{hook}', hook)
    body = body.replace('{{company}}', company)
    body = body.replace('{{first_name}}', first_name)
    body = body.replace('{{sender_first_name}}', sender_name)
    return {'subject': subject, 'body': body}


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_STEP_PURPOSES = {
    1: 'Initial outreach. Lead with the specific signal/evidence. Present core value proposition. One clear, low-commitment CTA.',
    2: 'Follow-up. Take a different angle from email 1. Add new value -- a different pain point, social proof, or industry insight. Do NOT repeat the same hook.',
    3: 'Second follow-up. Bring a fresh angle -- a mini case study, specific metric, or different feature benefit. Keep it brief. Do NOT repeat hooks from emails 1 or 2.',
    4: 'Breakup / final touch. Very short (under 50 words). Respectful close. Give them an easy out while keeping the door open.',
}


def _build_system_prompt(writing_context: str) -> str:
    """Build the system prompt for draft generation."""
    return f"""You are writing cold outreach emails on behalf of a BDR at Phrase, a localization and translation management platform.

ABOUT PHRASE (use naturally, don't dump all at once):
- Phrase connects to dev workflows via GitHub Sync — locale files stay in lockstep with branches
- API-first architecture, plugs into CI/CD pipelines
- Supports 50+ file formats (JSON, XLIFF, YAML, etc.)
- Built-in machine translation + translation memory
- Used by Shopify, Decathlon, Phorest, and thousands of engineering teams
- Key links to include where relevant:
  * Overview: https://phrase.com/suite/
  * GitHub Sync: https://phrase.com/blog/posts/i18n-guide
  * Pricing: https://phrase.com/pricing

VOICE & TONE:
- Write like a human peer, not a salesperson. Casual, direct, no corporate speak.
- Short sentences. Short paragraphs (1-2 sentences each).
- ALWAYS use line breaks between paragraphs — never write a wall of text.
- Never use backticks, code formatting, or technical repo paths in emails.
- Vary your openers — never start with "I noticed."
- Subject lines: lowercase, short, curiosity-driven (e.g. "quick question", "i18n at {{{{company}}}}")
- Sign off with just {{{{sender_first_name}}}} — no "Best," or "Regards,"

WHAT MAKES A GOOD COLD EMAIL:
- Lead with something specific about THEIR situation
- Connect their situation to a concrete Phrase capability (not a feature list)
- Include ONE relevant hyperlink per email (blog post, pricing, or product page)
- End with a low-friction CTA (question, not a meeting request)
- 80-120 words. Concise but substantive. White space matters.

{writing_context}

FORMAT: Return EXACTLY this format (no markdown, no JSON, no extra text):
SUBJECT: <subject line here>

BODY:
<email body here — use blank lines between paragraphs>"""


def _build_generation_prompt(
    step: int,
    prospect: dict,
    signal: dict,
    campaign: Optional[dict] = None,
) -> str:
    """Build the user prompt for generating a single draft step."""
    purpose = _STEP_PURPOSES.get(step, _STEP_PURPOSES[3])
    company = prospect.get('company_name', 'the company')
    name = prospect.get('full_name') or prospect.get('first_name', 'the prospect')
    title = prospect.get('title', '')

    signal_type = signal.get('signal_type', 'unknown') if signal else 'unknown'
    signal_desc = signal.get('signal_description', '') if signal else ''
    evidence = signal.get('evidence_value', '') if signal else ''
    outreach_angle = signal.get('outreach_angle', '') if signal else ''

    campaign_name = campaign.get('campaign_name', '') or campaign.get('name', '') if campaign else ''
    campaign_prompt = campaign.get('prompt', '') if campaign else ''

    signal_section = f"""SIGNAL:
- Type: {signal_type}
- Description: {signal_desc}
- Evidence: {evidence[:300] if evidence else 'None'}"""

    if outreach_angle:
        signal_section += f"\n- OUTREACH ANGLE: {outreach_angle}"

    parts = [f"""Generate email step {step} of a 4-email sequence.

STEP {step} PURPOSE: {purpose}

PROSPECT:
- Name: {name}
- Title: {title}
- Company: {company}

{signal_section}"""]

    if campaign_prompt:
        parts.append(f"CAMPAIGN INSTRUCTIONS:\n{campaign_prompt}")
    elif campaign_name:
        parts.append(f"CAMPAIGN: {campaign_name}")

    parts.append("80-120 words. Reference their signal naturally (don't quote repo paths or branch names verbatim). Include one relevant Phrase link. Use blank lines between paragraphs.")

    return '\n\n'.join(parts)


_PREAMBLE_RE = re.compile(
    r'^(sure|of course|certainly|okay|ok|absolutely|no problem)[,!.:\s]',
    re.IGNORECASE,
)


def _parse_llm_output(text: str) -> dict:
    """Parse LLM output in SUBJECT: ... BODY: ... format.

    Handles chatty preambles, markdown code fences, and case variations.
    """
    # Strip markdown code fences
    text = re.sub(r'```[\w]*\n?', '', text).strip()

    subject = ''
    body = ''

    # Try regex extraction (handles preamble before SUBJECT:)
    subject_match = re.search(r'SUBJECT:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
    body_match = re.search(r'BODY:\s*(.*)', text, re.IGNORECASE | re.DOTALL)

    if subject_match and body_match:
        subject = subject_match.group(1).strip()
        body = body_match.group(1).strip()
    elif subject_match:
        # SUBJECT found but no BODY marker — everything after subject line is body
        subject = subject_match.group(1).strip()
        after = text[subject_match.end():].strip()
        body = after
    else:
        # Fallback: skip preamble lines, first real line = subject
        lines = text.strip().split('\n')
        start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or _PREAMBLE_RE.match(stripped):
                start = i + 1
                continue
            break
        remaining = lines[start:]
        if remaining:
            subject = remaining[0].strip()
            body = '\n'.join(remaining[1:]).strip() if len(remaining) > 1 else ''

    return {'subject': subject, 'body': body}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def generate_drafts(
    prospect_id: int,
    signal_id: int,
    campaign_id: int,
    writing_preferences: Optional[dict] = None,
    user_email: Optional[str] = None,
    sequence_config_override: Optional[dict] = None,
) -> List[dict]:
    """Generate a multi-step email sequence for a prospect.

    Steps:
        1. Load prospect, signal, campaign info
        2. Build writing context from preferences + campaign guidelines + BDR overrides
        3. Generate subject + body for each step via LLM (or template fallback)
        4. Save each draft to the drafts table
        5. Return list of created drafts

    Args:
        prospect_id: the prospect to write for
        signal_id: the intent signal that triggered this outreach
        campaign_id: the campaign to use for writing guidelines
        writing_preferences: optional override for writing prefs (skips DB load)
        user_email: BDR's email for personal preference lookup (optional)

    Returns:
        List of draft dicts (one per sequence step)
    """
    from v2.services.prospect_service import get_prospect
    from v2.services.signal_service import get_signal
    from v2.services.writing_prefs_service import build_writing_context

    # Load context
    prospect = get_prospect(prospect_id)
    if not prospect:
        raise ValueError(f"Prospect {prospect_id} not found")

    signal = get_signal(signal_id)
    if not signal:
        raise ValueError(f"Signal {signal_id} not found")

    # Load campaign info
    campaign = None
    campaign_guidelines = None
    personas = []
    with db_connection() as conn:
        cursor = conn.cursor()
        if campaign_id:
            cursor.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
            campaign = row_to_dict(cursor.fetchone())
            if campaign:
                campaign_guidelines = campaign.get('writing_guidelines')
                cursor.execute(
                    "SELECT * FROM campaign_personas WHERE campaign_id = ? ORDER BY priority ASC",
                    (campaign_id,),
                )
                personas = rows_to_dicts(cursor.fetchall())

    # Build writing context (org-wide → BDR overrides → campaign guidelines)
    writing_context = build_writing_context(campaign_guidelines, user_email=user_email)

    # Determine number of sequence steps and threading from sequence_config
    num_steps = 3
    single_thread = False

    # Priority: prospect override > campaign default
    effective_config = None
    if sequence_config_override:
        effective_config = sequence_config_override
    elif campaign and campaign.get('sequence_config'):
        try:
            effective_config = json.loads(campaign['sequence_config']) if isinstance(
                campaign['sequence_config'], str
            ) else campaign['sequence_config']
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    if isinstance(effective_config, dict):
        if effective_config.get('num_steps'):
            num_steps = int(effective_config['num_steps'])
        if effective_config.get('single_thread'):
            single_thread = True

    # Build system prompt
    system_prompt = _build_system_prompt(writing_context)

    created_drafts = []
    active_provider = get_active_provider()
    active_model = get_active_model()
    thread_subject = None  # For single-thread sequences, reuse step 1's subject
    sender_name = _resolve_fallback_sender_name(user_email)
    pending_drafts = []

    for step in range(1, num_steps + 1):
        # Try LLM generation first
        user_prompt = _build_generation_prompt(step, prospect, signal, campaign)
        llm_text = _llm_generate(system_prompt, user_prompt)

        if llm_text:
            parsed = _parse_llm_output(llm_text)
            subject = parsed['subject']
            body = parsed['body']
            generated_by = active_provider
            generation_model = active_model
            generation_notes = None
        else:
            # Fallback to template-based generation
            template_result = _generate_template_draft(
                step, prospect, signal, sender_name=sender_name
            )
            subject = template_result['subject']
            body = template_result['body']
            generated_by = 'template'
            generation_model = 'template'
            generation_notes = 'LLM unavailable — generated from template. Review before approving.'

        # Single-thread: reuse step 1's subject for all subsequent steps
        if single_thread:
            if step == 1:
                thread_subject = subject
            elif thread_subject:
                subject = thread_subject

        generation_context = safe_json_dumps({
            'prospect_id': prospect_id,
            'signal_id': signal_id,
            'campaign_id': campaign_id,
            'step': step,
            'generated_by': generated_by,
            'generation_notes': generation_notes,
        })

        pending_drafts.append({
            'prospect_id': prospect_id,
            'signal_id': signal_id,
            'campaign_id': campaign_id,
            'sequence_step': step,
            'subject': subject,
            'body': body,
            'generated_by': generated_by,
            'generation_model': generation_model,
            'status': 'generated',
            'generation_notes': generation_notes,
            'generation_context': generation_context,
        })

    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM drafts
            WHERE prospect_id = ? AND status != 'enrolled'
        ''', (prospect_id,))
        deleted = cursor.rowcount if hasattr(cursor, 'rowcount') else 0
        if deleted:
            logger.info("[DRAFT] Cleaned up %d old drafts for prospect %d", deleted, prospect_id)

        for draft in pending_drafts:
            draft_id = insert_returning_id(cursor, '''
                INSERT INTO drafts (
                    prospect_id, signal_id, campaign_id, sequence_step,
                    subject, body, generated_by, generation_model,
                    generation_context, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'generated')
            ''', (
                draft['prospect_id'],
                draft['signal_id'],
                draft['campaign_id'],
                draft['sequence_step'],
                draft['subject'],
                draft['body'],
                draft['generated_by'],
                draft['generation_model'],
                draft['generation_context'],
            ))
            draft['id'] = draft_id
            created_drafts.append(draft)
            logger.info("[DRAFT] Generated draft %d (step %d) for prospect %d",
                         draft_id, draft['sequence_step'], prospect_id)

        conn.commit()

    # Update prospect enrollment status to 'drafting'
    from v2.services.prospect_service import update_prospect_status
    update_prospect_status(prospect_id, 'drafting')

    # Log activity
    try:
        from v2.services.activity_service import log_activity
        log_activity(
            event_type='draft_generated',
            entity_type='prospect',
            entity_id=prospect_id,
            details={
                'signal_id': signal_id,
                'campaign_id': campaign_id,
                'num_steps': num_steps,
                'draft_ids': [d['id'] for d in created_drafts],
            },
            created_by='draft_service',
        )
    except Exception:
        logger.debug("[DRAFT] Could not log activity for draft generation")

    return created_drafts


def regenerate_draft(draft_id: int, critique: str) -> Optional[dict]:
    """Regenerate a draft incorporating feedback/critique.

    Args:
        draft_id: the draft to regenerate
        critique: the user's feedback on what to change

    Returns:
        The updated draft dict, or None if draft not found
    """
    from v2.services.prospect_service import get_prospect
    from v2.services.signal_service import get_signal
    from v2.services.writing_prefs_service import build_writing_context

    # Load existing draft
    draft = get_draft(draft_id)
    if not draft:
        return None

    # Load context
    prospect = get_prospect(draft['prospect_id'])
    signal = get_signal(draft['signal_id']) if draft.get('signal_id') else None

    # Load campaign (prompt + writing guidelines)
    campaign_guidelines = None
    campaign_prompt = ''
    if draft.get('campaign_id'):
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT writing_guidelines, prompt FROM campaigns WHERE id = ?",
                (draft['campaign_id'],),
            )
            row = cursor.fetchone()
            if row:
                if isinstance(row, dict):
                    campaign_guidelines = row.get('writing_guidelines')
                    campaign_prompt = row.get('prompt', '')
                else:
                    campaign_guidelines = row[0]
                    campaign_prompt = row[1] if len(row) > 1 else ''

    writing_context = build_writing_context(campaign_guidelines)
    system_prompt = _build_system_prompt(writing_context)

    campaign_section = f"\nCAMPAIGN INSTRUCTIONS:\n{campaign_prompt}\n" if campaign_prompt else ''

    user_prompt = f"""Rewrite this email draft incorporating the feedback below.

ORIGINAL SUBJECT: {draft.get('subject', '')}

ORIGINAL BODY:
{draft.get('body', '')}

FEEDBACK / CRITIQUE:
{critique}

PROSPECT: {prospect.get('full_name', '')} ({prospect.get('title', '')}) at {prospect.get('company_name', '')}
SIGNAL: {signal.get('signal_description', '') if signal else 'N/A'}
{campaign_section}
Keep the email concise (under 120 words). Apply the feedback precisely."""

    llm_text = _llm_generate(system_prompt, user_prompt)

    if llm_text:
        parsed = _parse_llm_output(llm_text)
        new_subject = parsed['subject']
        new_body = parsed['body']
    else:
        # LLM unavailable — keep the original body unchanged (never append
        # internal notes to text that could be sent to a prospect).
        logger.warning("[DRAFT] LLM unavailable for regeneration of draft %d; keeping original body", draft_id)

        # Store the feedback so the user can retry later, but don't touch the body
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE drafts
                SET last_feedback = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (critique, draft_id))
            conn.commit()

        # Log feedback even though regeneration failed
        try:
            from v2.services.feedback_service import log_feedback
            log_feedback(
                draft_id=draft_id,
                critique=critique,
                sequence_step=draft.get('sequence_step'),
                prospect_id=draft.get('prospect_id'),
                signal_id=draft.get('signal_id'),
                created_by='draft_service',
            )
        except ImportError:
            logger.debug("[DRAFT] feedback_service not available, skipping feedback log")
        except Exception:
            logger.debug("[DRAFT] Could not log feedback for draft %d", draft_id)

        result = get_draft(draft_id)
        if result:
            result['_warning'] = 'LLM unavailable — critique saved but draft body was not changed. Please retry.'
        return result

    # Update draft in DB with the regenerated content
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE drafts
            SET subject = ?, body = ?, last_feedback = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (new_subject, new_body, critique, draft_id))
        conn.commit()

    # Log feedback
    try:
        from v2.services.feedback_service import log_feedback
        log_feedback(
            draft_id=draft_id,
            critique=critique,
            sequence_step=draft.get('sequence_step'),
            prospect_id=draft.get('prospect_id'),
            signal_id=draft.get('signal_id'),
            created_by='draft_service',
        )
    except ImportError:
        logger.debug("[DRAFT] feedback_service not available, skipping feedback log")
    except Exception:
        logger.debug("[DRAFT] Could not log feedback for draft %d", draft_id)

    # Return updated draft
    return get_draft(draft_id)


def update_draft(
    draft_id: int,
    subject: Optional[str] = None,
    body: Optional[str] = None,
) -> Optional[dict]:
    """Update a draft's subject and/or body.

    If the draft was in 'generated' status, it moves to 'edited'.

    Args:
        draft_id: the draft to update
        subject: new subject (or None to keep existing)
        body: new body (or None to keep existing)

    Returns:
        The updated draft dict, or None if not found
    """
    draft = get_draft(draft_id)
    if not draft:
        return None

    # Do not allow editing drafts that have already been enrolled (pushed to Apollo)
    if draft.get('status') == 'enrolled':
        logger.warning("[DRAFT] Cannot edit enrolled draft %d", draft_id)
        return None

    new_subject = subject if subject is not None else draft.get('subject')
    new_body = body if body is not None else draft.get('body')
    new_status = 'edited' if draft.get('status') in ('generated', 'approved') else draft.get('status')

    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE drafts
            SET subject = ?, body = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (new_subject, new_body, new_status, draft_id))
        conn.commit()

    return get_draft(draft_id)


def approve_draft(draft_id: int) -> Optional[dict]:
    """Mark a single draft as approved.

    Only drafts in 'generated' or 'edited' status can be approved.
    Enrolled drafts (already pushed to Apollo) cannot be re-approved.

    Returns:
        The updated draft dict, or None if not found or not approvable
    """
    draft = get_draft(draft_id)
    if not draft:
        return None

    # Guard: only allow approval from generated or edited status
    if draft.get('status') not in ('generated', 'edited'):
        logger.warning("[DRAFT] Cannot approve draft %d with status '%s'", draft_id, draft.get('status'))
        return None

    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE drafts SET status = 'approved', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (draft_id,))
        conn.commit()

    # Log activity
    try:
        from v2.services.activity_service import log_activity
        log_activity(
            event_type='draft_approved',
            entity_type='draft',
            entity_id=draft_id,
            details={
                'prospect_id': draft.get('prospect_id'),
                'signal_id': draft.get('signal_id'),
                'sequence_step': draft.get('sequence_step'),
            },
            created_by='draft_service',
        )
    except Exception:
        logger.debug("[DRAFT] Could not log activity for draft approval")

    return get_draft(draft_id)


def approve_all_drafts(prospect_id: int) -> List[dict]:
    """Approve all drafts for a prospect.

    Returns:
        List of updated draft dicts
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE drafts SET status = 'approved', updated_at = CURRENT_TIMESTAMP
            WHERE prospect_id = ? AND status IN ('generated', 'edited')
        ''', (prospect_id,))
        conn.commit()

    # Log activity
    try:
        from v2.services.activity_service import log_activity
        log_activity(
            event_type='draft_approved',
            entity_type='prospect',
            entity_id=prospect_id,
            details={'action': 'approve_all'},
            created_by='draft_service',
        )
    except Exception:
        logger.debug("[DRAFT] Could not log activity for bulk approval")

    return get_drafts_for_prospect(prospect_id)


def get_drafts_for_prospect(prospect_id: int) -> List[dict]:
    """Get all drafts for a prospect, ordered by sequence step.

    Returns:
        List of draft dicts
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM drafts
            WHERE prospect_id = ?
            ORDER BY sequence_step ASC, updated_at DESC, created_at DESC, id DESC
        ''', (prospect_id,))
        return collapse_draft_versions(rows_to_dicts(cursor.fetchall()))


def collapse_draft_versions(
    drafts: List[dict],
    key_fields: tuple = ('sequence_step',),
) -> List[dict]:
    """Keep only the newest draft for each logical step/key."""
    normalized = []
    for draft in drafts or []:
        hydrated = dict(draft)
        if not hydrated.get('generation_notes'):
            context = safe_json_loads(hydrated.get('generation_context'), default={}) or {}
            if isinstance(context, dict) and context.get('generation_notes'):
                hydrated['generation_notes'] = context['generation_notes']
        normalized.append(hydrated)

    latest_by_key = {}
    ranked = sorted(
        normalized,
        key=lambda d: (
            d.get('updated_at') or d.get('created_at') or '',
            d.get('id') or 0,
        ),
        reverse=True,
    )

    for draft in ranked:
        key = tuple(draft.get(field) for field in key_fields)
        if key not in latest_by_key:
            latest_by_key[key] = draft

    return sorted(
        latest_by_key.values(),
        key=lambda d: tuple(d.get(field) or 0 for field in key_fields),
    )


def get_draft(draft_id: int) -> Optional[dict]:
    """Get a single draft by id.

    Returns:
        Draft dict, or None if not found
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,))
        return row_to_dict(cursor.fetchone())
