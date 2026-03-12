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
    'dependency_injection': ['implementation', 'smoking gun'],
    'rfc_discussion':       ['greenfield', 'educator'],
    'ghost_branch':         ['implementation', 'active'],
    'documentation_intent': ['greenfield', 'preparing'],
    'job_posting_intent':   ['scale', 'expansion'],
    'tms_config_file':      ['migration'],
    'smoking_gun_fork':     ['implementation', 'smoking gun'],
    'already_launched':     ['expansion', 'scale'],
}

# Human-readable reasoning fragments per signal type
_SIGNAL_REASONING = {
    'dependency_injection': (
        "This account has a dependency_injection signal, indicating active "
        "i18n library adoption. The '{campaign}' campaign targets teams "
        "mid-build with integration-focused messaging that emphasizes "
        "CI/CD automation and GitHub Sync."
    ),
    'rfc_discussion': (
        "This account has an rfc_discussion signal, meaning the team is "
        "evaluating i18n approaches. The '{campaign}' campaign educates "
        "early-stage teams on modern localization workflows before they "
        "commit to a DIY path."
    ),
    'ghost_branch': (
        "This account has a ghost_branch signal — active i18n branch work "
        "is in progress. The '{campaign}' campaign helps teams already "
        "building with hands-on implementation support."
    ),
    'documentation_intent': (
        "This account has a documentation_intent signal — i18n is on the "
        "roadmap but hasn't started. The '{campaign}' campaign positions "
        "Phrase as the tool that lets them skip the DIY phase."
    ),
    'job_posting_intent': (
        "This account has a job_posting_intent signal — they're hiring for "
        "localization roles, signaling scale. The '{campaign}' campaign "
        "speaks to teams outgrowing manual processes."
    ),
    'tms_config_file': (
        "This account has a tms_config_file signal — they already use a "
        "TMS and may be ready to migrate. The '{campaign}' campaign offers "
        "a clear migration path from legacy tools."
    ),
    'smoking_gun_fork': (
        "This account forked a key i18n project, showing hands-on "
        "evaluation. The '{campaign}' campaign targets teams actively "
        "comparing tools with implementation-ready messaging."
    ),
    'already_launched': (
        "This account already ships in multiple languages. The '{campaign}' "
        "campaign focuses on scale optimization and expansion into new "
        "markets."
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
