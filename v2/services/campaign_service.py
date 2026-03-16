"""
Campaign Service — campaign CRUD, persona lookup, and deterministic campaign
recommendation based on signal type.

Campaign recommendation is the core logic that maps an intent signal to the
right outreach campaign. It queries existing campaigns from the DB and matches
by signal type keywords — no hardcoded campaign IDs.
"""
import logging
from typing import Optional, List

from v2.db import db_connection, row_to_dict, rows_to_dicts, safe_json_loads

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal-type → campaign keyword mapping
#
# Each signal type maps to a list of keyword pairs (tried in order).
# We match against campaign name (case-insensitive).
# ---------------------------------------------------------------------------

_SIGNAL_CAMPAIGN_KEYWORDS = {
    # RepoRadar (GitHub-detected engineering signals)
    'dependency_injection': ['reporadar'],
    'rfc_discussion':       ['reporadar'],
    'rfc_discussion_high':  ['reporadar'],
    'ghost_branch':         ['reporadar'],
    'ghost_branch_active':  ['reporadar'],
    'smoking_gun_fork':     ['reporadar'],
    'documentation_intent': ['reporadar'],

    # Hiring Signal
    'hiring_localization':              ['hiring'],
    'hiring_international':             ['hiring'],
    'hiring_hidden_role_i18n':          ['hiring'],
    'hiring_hidden_role_platform_i18n': ['hiring'],
    'hidden_localization_role':         ['hiring'],
    'job_posting_intent':               ['hiring'],

    # Scale & Expansion
    'market_expansion':       ['scale', 'expansion'],
    'expansion_signal_apac':  ['scale', 'expansion'],
    'already_launched':       ['scale', 'expansion'],
    'funding_round':          ['scale', 'expansion'],
    'product_multilingual':   ['scale', 'expansion'],

    # Translation Quality
    'broken_translation_site':  ['translation quality'],
    'broken_translation_path':  ['translation quality'],
    'missing_translations':     ['translation quality'],
    'missing_translation_404':  ['translation quality'],
    'website_audit':            ['translation quality'],
    'website_translation_audit': ['translation quality'],

    # Competitive Displacement
    'tms_config_file':    ['competitive', 'displacement'],
    'competitor_usage':   ['competitive', 'displacement'],

    # Phrase Studio
    'academy_university':    ['phrase studio'],
    'youtube_channel':       ['phrase studio'],
    'youtube_channel_academy': ['phrase studio'],
    'website_demo_videos':   ['phrase studio'],
}

# Human-readable reasoning per signal type
_SIGNAL_REASONING = {
    'dependency_injection': (
        "Active i18n library adoption detected. The '{campaign}' campaign targets "
        "engineering teams mid-build with GitHub Sync and CI/CD automation messaging."
    ),
    'rfc_discussion': (
        "Team is evaluating i18n approaches. The '{campaign}' campaign educates "
        "early-stage teams before they commit to a DIY path."
    ),
    'ghost_branch': (
        "Active i18n branch work in progress. The '{campaign}' campaign helps "
        "teams already building with implementation support."
    ),
    'documentation_intent': (
        "i18n is on the roadmap but hasn't started. The '{campaign}' campaign "
        "positions Phrase before they go DIY."
    ),
    'hiring_localization': (
        "Hiring for localization roles. The '{campaign}' campaign positions Phrase "
        "as the force multiplier — making the new hire 10x more productive."
    ),
    'hiring_international': (
        "Hiring for international roles, signaling global ambitions. The '{campaign}' "
        "campaign connects hiring intent to localization infrastructure."
    ),
    'hiring_hidden_role_i18n': (
        "Hidden i18n role detected in job posting. The '{campaign}' campaign "
        "highlights how Phrase reduces the tooling burden for this hire."
    ),
    'job_posting_intent': (
        "Job posting signals localization investment. The '{campaign}' campaign "
        "speaks to teams building out their loc function."
    ),
    'market_expansion': (
        "International expansion signal detected. The '{campaign}' campaign "
        "positions Phrase as growth infrastructure for new markets."
    ),
    'expansion_signal_apac': (
        "APAC expansion signal. The '{campaign}' campaign addresses regional "
        "launch needs with Phrase's 50+ language support."
    ),
    'already_launched': (
        "Already shipping in multiple languages. The '{campaign}' campaign "
        "focuses on scale optimization and expanding to more markets."
    ),
    'funding_round': (
        "Recent funding signals growth ambitions. The '{campaign}' campaign "
        "connects investment to international expansion infrastructure."
    ),
    'broken_translation_site': (
        "Broken translations found on their website. The '{campaign}' campaign "
        "leads with the specific quality issue as a pain point."
    ),
    'missing_translations': (
        "Missing translations detected. The '{campaign}' campaign highlights "
        "the gap and positions Phrase Quality Evaluation as the fix."
    ),
    'website_audit': (
        "Website translation audit revealed issues. The '{campaign}' campaign "
        "offers Quality Evaluation and Language AI to fix quality gaps."
    ),
    'tms_config_file': (
        "Existing TMS config detected — they already use a translation tool. "
        "The '{campaign}' campaign positions Phrase as the modern upgrade."
    ),
    'competitor_usage': (
        "Using a competitor TMS. The '{campaign}' campaign offers a unified "
        "platform migration path without naming the competitor."
    ),
    'academy_university': (
        "Company has academy/education content. The '{campaign}' campaign "
        "introduces Phrase Studio for transcription, subtitles, and voiceovers."
    ),
    'youtube_channel': (
        "Active YouTube channel detected. The '{campaign}' campaign shows how "
        "Phrase Studio can reach global audiences with multilingual subtitles and voiceovers."
    ),
    'youtube_channel_academy': (
        "YouTube + academy content. The '{campaign}' campaign positions Phrase Studio "
        "as the intelligence layer for their multimedia content."
    ),
    'website_demo_videos': (
        "Product demo videos on their website. The '{campaign}' campaign shows how "
        "Phrase Studio turns demos into multilingual assets in 100+ languages."
    ),
}


# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------

def list_campaigns(active_only: bool = True) -> List[dict]:
    """Return campaigns with their personas attached.

    Args:
        active_only: if True, only return campaigns whose status is 'active'.
    """
    with db_connection() as conn:
        cursor = conn.cursor()

        if active_only:
            cursor.execute('''
                SELECT * FROM campaigns WHERE status = 'active'
                ORDER BY created_at DESC
            ''')
        else:
            cursor.execute('''
                SELECT * FROM campaigns ORDER BY created_at DESC
            ''')

        campaigns = rows_to_dicts(cursor.fetchall())

        # Attach personas to each campaign
        for camp in campaigns:
            cursor.execute('''
                SELECT * FROM campaign_personas
                WHERE campaign_id = ?
                ORDER BY priority ASC
            ''', (camp['id'],))
            camp['personas'] = rows_to_dicts(cursor.fetchall())

        return campaigns


def get_campaign(campaign_id: int) -> Optional[dict]:
    """Return a single campaign with its personas, or None."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM campaigns WHERE id = ?', (campaign_id,))
        campaign = row_to_dict(cursor.fetchone())
        if not campaign:
            return None

        cursor.execute('''
            SELECT * FROM campaign_personas
            WHERE campaign_id = ?
            ORDER BY priority ASC
        ''', (campaign_id,))
        campaign['personas'] = rows_to_dicts(cursor.fetchall())

        return campaign


# ---------------------------------------------------------------------------
# Campaign Recommendation
# ---------------------------------------------------------------------------

def recommend_campaign(
    signal_type: Optional[str],
    outreach_angle: Optional[str] = None,
    account_metadata: Optional[dict] = None,
) -> dict:
    """Deterministically recommend a campaign for a given signal type.

    Strategy (in order):
    1. Look up keywords for the signal_type and find a matching active campaign.
    2. If outreach_angle is provided, try matching the angle label in campaign
       name or prompt.
    3. Fallback: return the first active campaign.

    Returns:
        dict with keys: campaign_id, campaign_name, reasoning.
        If no campaigns exist at all, returns a stub with campaign_id=None.
    """
    active_campaigns = list_campaigns(active_only=True)

    if not active_campaigns:
        return {
            'campaign_id': None,
            'campaign_name': None,
            'reasoning': 'No active campaigns found in the system.',
        }

    # --- 1. Match by signal_type keywords ---
    if signal_type and signal_type in _SIGNAL_CAMPAIGN_KEYWORDS:
        keywords = _SIGNAL_CAMPAIGN_KEYWORDS[signal_type]
        for kw in keywords:
            for camp in active_campaigns:
                if kw.lower() in (camp.get('name') or '').lower():
                    reasoning_template = _SIGNAL_REASONING.get(signal_type, '')
                    reasoning = reasoning_template.format(campaign=camp['name'])
                    return {
                        'campaign_id': camp['id'],
                        'campaign_name': camp['name'],
                        'reasoning': reasoning,
                    }

    # --- 2. Match by outreach_angle label ---
    if outreach_angle:
        # Convert enum-style value to display-friendly words for matching.
        angle_words = outreach_angle.replace('_', ' ').lower()
        for camp in active_campaigns:
            camp_text = ((camp.get('name') or '') + ' ' + (camp.get('prompt') or '')).lower()
            if angle_words in camp_text:
                return {
                    'campaign_id': camp['id'],
                    'campaign_name': camp['name'],
                    'reasoning': (
                        f"Matched campaign '{camp['name']}' based on outreach angle "
                        f"'{outreach_angle}'. Signal type: {signal_type or 'unknown'}."
                    ),
                }

    # --- 3. Fallback: first active campaign ---
    fallback = active_campaigns[0]
    return {
        'campaign_id': fallback['id'],
        'campaign_name': fallback['name'],
        'reasoning': (
            f"No campaign matched signal type '{signal_type}'. "
            f"Falling back to '{fallback['name']}' as the default active campaign."
        ),
    }


# ---------------------------------------------------------------------------
# Campaign Writing Guidelines + Personas
# ---------------------------------------------------------------------------

def get_campaign_writing_guidelines(campaign_id: int) -> Optional[str]:
    """Return the writing_guidelines text for a campaign, or None."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT writing_guidelines FROM campaigns WHERE id = ?',
            (campaign_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return row['writing_guidelines'] if isinstance(row, dict) else row[0]


def get_personas_for_campaign(campaign_id: int) -> List[dict]:
    """Return parsed persona dicts for a campaign.

    Each dict has: persona_name, titles (list), seniorities (list),
    sequence_id, sequence_name, priority.
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM campaign_personas
            WHERE campaign_id = ?
            ORDER BY priority ASC
        ''', (campaign_id,))
        raw_rows = rows_to_dicts(cursor.fetchall())

    personas = []
    for row in raw_rows:
        personas.append({
            'id': row.get('id'),
            'campaign_id': row.get('campaign_id'),
            'persona_name': row.get('persona_name'),
            'titles': safe_json_loads(row.get('titles_json'), default=[]),
            'seniorities': safe_json_loads(row.get('seniorities_json'), default=[]),
            'sequence_id': row.get('sequence_id'),
            'sequence_name': row.get('sequence_name'),
            'priority': row.get('priority', 0),
        })

    return personas
