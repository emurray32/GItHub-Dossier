"""
Writing Preferences Service — manages org-wide and per-BDR writing rules.

Three-layer system merged at draft generation time:
1. Org-wide preferences (tone, banned phrases, structure, CTA, signoff)
2. Per-BDR personal overrides with three modes:
   - 'add': appends to the org value (e.g., additional banned words)
   - 'replace': fully replaces the org value for this BDR (e.g., custom signoff)
   - 'remove': removes specific items from the org list (e.g., un-ban a word)
3. Campaign-specific writing_guidelines overlay (applied last)
"""
import logging
from typing import Optional, List

from v2.db import db_connection, row_to_dict, rows_to_dicts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Org-wide preferences
# ---------------------------------------------------------------------------

def get_writing_preferences() -> dict:
    """Get all org-wide writing preferences as a key-value dict."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT preference_key, preference_value FROM writing_preferences")
        rows = rows_to_dicts(cursor.fetchall())
        return {r['preference_key']: r['preference_value'] for r in rows}


def get_preference(key: str) -> Optional[str]:
    """Get a single org-wide writing preference by key."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT preference_value FROM writing_preferences WHERE preference_key = ?",
            (key,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return row['preference_value'] if isinstance(row, dict) else row[0]


def update_preference(key: str, value: str) -> bool:
    """Update a single org-wide writing preference. Creates if it doesn't exist."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE writing_preferences
            SET preference_value = ?, updated_at = CURRENT_TIMESTAMP
            WHERE preference_key = ?
        ''', (value, key))

        if not hasattr(cursor, 'rowcount') or cursor.rowcount == 0:
            try:
                cursor.execute('''
                    INSERT INTO writing_preferences (preference_key, preference_value)
                    VALUES (?, ?)
                ''', (key, value))
            except Exception:
                pass  # Already exists (race condition)

        conn.commit()
        logger.info("[WRITING_PREFS] Updated org pref: %s", key)
        return True


def update_writing_preferences(preferences: dict) -> bool:
    """Bulk update org-wide writing preferences from a dict."""
    for key, value in preferences.items():
        update_preference(key, str(value))
    return True


# ---------------------------------------------------------------------------
# Per-BDR preferences
# ---------------------------------------------------------------------------

def get_bdr_preferences(user_email: str) -> List[dict]:
    """Get all personal writing preferences for a BDR.

    Returns:
        List of dicts with keys: preference_key, preference_value, override_mode
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT preference_key, preference_value, override_mode
            FROM bdr_writing_preferences
            WHERE user_email = ?
            ORDER BY preference_key, override_mode
        ''', (user_email,))
        return rows_to_dicts(cursor.fetchall())


def update_bdr_preference(user_email: str, key: str, value: str,
                          override_mode: str = 'add') -> bool:
    """Set a personal writing preference for a BDR.

    Args:
        user_email: BDR's email address
        key: preference key (e.g., 'banned_phrases', 'tone', 'signoff_guidance')
        value: preference value
        override_mode: 'add' (append), 'replace' (override), or 'remove' (subtract)
    """
    if override_mode not in ('add', 'replace', 'remove'):
        raise ValueError(f"Invalid override_mode: {override_mode}. Must be 'add', 'replace', or 'remove'.")

    with db_connection() as conn:
        cursor = conn.cursor()

        # Upsert
        cursor.execute('''
            UPDATE bdr_writing_preferences
            SET preference_value = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_email = ? AND preference_key = ? AND override_mode = ?
        ''', (value, user_email, key, override_mode))

        if not hasattr(cursor, 'rowcount') or cursor.rowcount == 0:
            try:
                cursor.execute('''
                    INSERT INTO bdr_writing_preferences
                        (user_email, preference_key, preference_value, override_mode)
                    VALUES (?, ?, ?, ?)
                ''', (user_email, key, value, override_mode))
            except Exception:
                pass

        conn.commit()
        logger.info("[WRITING_PREFS] Updated BDR pref: %s/%s/%s", user_email, key, override_mode)
        return True


def delete_bdr_preference(user_email: str, key: str,
                          override_mode: Optional[str] = None) -> bool:
    """Delete a personal writing preference for a BDR."""
    with db_connection() as conn:
        cursor = conn.cursor()
        if override_mode:
            cursor.execute('''
                DELETE FROM bdr_writing_preferences
                WHERE user_email = ? AND preference_key = ? AND override_mode = ?
            ''', (user_email, key, override_mode))
        else:
            cursor.execute('''
                DELETE FROM bdr_writing_preferences
                WHERE user_email = ? AND preference_key = ?
            ''', (user_email, key))
        conn.commit()
        return True


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def _merge_comma_list(org_value: str, bdr_prefs: List[dict]) -> str:
    """Merge a comma-separated list field with BDR add/remove overrides.

    For list-type fields like banned_phrases:
    - 'replace' mode: fully replaces the org list
    - 'remove' mode: removes BDR items from the org list
    - 'add' mode: appends BDR items to the org list
    """
    # Full replace takes priority
    for pref in bdr_prefs:
        if pref['override_mode'] == 'replace':
            return pref['preference_value']

    # Parse org list (preserve original casing in output)
    items = [x.strip() for x in org_value.split(',') if x.strip()]
    items_lower = {x.lower() for x in items}

    # Apply removes
    remove_set = set()
    for pref in bdr_prefs:
        if pref['override_mode'] == 'remove':
            for x in pref['preference_value'].split(','):
                remove_set.add(x.strip().lower())

    items = [x for x in items if x.lower() not in remove_set]
    items_lower -= remove_set

    # Apply adds
    for pref in bdr_prefs:
        if pref['override_mode'] == 'add':
            for x in pref['preference_value'].split(','):
                x = x.strip()
                if x and x.lower() not in items_lower:
                    items.append(x)
                    items_lower.add(x.lower())

    return ', '.join(items)


def _merge_text_field(org_value: str, bdr_prefs: List[dict]) -> str:
    """Merge a text field with BDR overrides.

    For non-list fields like tone, signoff_guidance:
    - 'replace' mode: fully replaces the org value
    - 'add' mode: appends after the org value
    - 'remove' mode: ignored for text fields
    """
    for pref in bdr_prefs:
        if pref['override_mode'] == 'replace':
            return pref['preference_value']

    result = org_value
    for pref in bdr_prefs:
        if pref['override_mode'] == 'add':
            result = result.rstrip() + '\n' + pref['preference_value']

    return result


# Keys that use comma-separated list merge logic
_LIST_KEYS = {'banned_phrases'}


def get_merged_preferences(user_email: Optional[str] = None) -> dict:
    """Get writing preferences with BDR personal overrides applied.

    Args:
        user_email: BDR's email. If None, returns org-wide only.

    Returns:
        Merged preference dict.
    """
    org_prefs = get_writing_preferences()

    if not user_email:
        return org_prefs

    bdr_prefs = get_bdr_preferences(user_email)
    if not bdr_prefs:
        return org_prefs

    # Group BDR prefs by key
    by_key = {}
    for pref in bdr_prefs:
        key = pref['preference_key']
        if key not in by_key:
            by_key[key] = []
        by_key[key].append(pref)

    # Merge each key
    merged = dict(org_prefs)
    for key, prefs in by_key.items():
        org_value = org_prefs.get(key, '')
        if key in _LIST_KEYS:
            merged[key] = _merge_comma_list(org_value, prefs)
        else:
            merged[key] = _merge_text_field(org_value, prefs)

    return merged


# ---------------------------------------------------------------------------
# Writing context builder
# ---------------------------------------------------------------------------

def build_writing_context(campaign_guidelines: Optional[str] = None,
                          user_email: Optional[str] = None) -> str:
    """Build a combined writing context string for LLM prompts.

    Merge order: org prefs → BDR overrides → campaign guidelines.

    Args:
        campaign_guidelines: campaign-specific writing guidelines overlay
        user_email: BDR's email for personal preference lookup
    """
    prefs = get_merged_preferences(user_email)

    parts = []

    if prefs.get('tone'):
        parts.append(f"TONE: {prefs['tone']}")

    if prefs.get('preferred_structure'):
        parts.append(f"EMAIL STRUCTURE: {prefs['preferred_structure']}")

    if prefs.get('banned_phrases'):
        parts.append(f"BANNED PHRASES (never use): {prefs['banned_phrases']}")

    if prefs.get('cta_guidance'):
        parts.append(f"CTA GUIDANCE: {prefs['cta_guidance']}")

    if prefs.get('signoff_guidance'):
        parts.append(f"SIGNOFF: {prefs['signoff_guidance']}")

    if prefs.get('custom_rules'):
        parts.append(f"ADDITIONAL RULES: {prefs['custom_rules']}")

    if campaign_guidelines:
        parts.append(f"\nCAMPAIGN-SPECIFIC GUIDELINES:\n{campaign_guidelines}")

    return "\n\n".join(parts)
