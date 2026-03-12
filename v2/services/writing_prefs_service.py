"""
Writing Preferences Service — manages org-wide writing rules.

These are separate from campaign-specific guidelines. Both layers are
combined when generating drafts:
1. Org-wide preferences (tone, banned phrases, structure, CTA, signoff)
2. Campaign-specific writing_guidelines overlay
"""
import logging
from typing import Optional

from v2.db import db_connection, row_to_dict, rows_to_dicts

logger = logging.getLogger(__name__)


def get_writing_preferences() -> dict:
    """Get all writing preferences as a key-value dict."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT preference_key, preference_value FROM writing_preferences")
        rows = rows_to_dicts(cursor.fetchall())
        return {r['preference_key']: r['preference_value'] for r in rows}


def get_preference(key: str) -> Optional[str]:
    """Get a single writing preference by key."""
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
    """Update a single writing preference. Creates it if it doesn't exist."""
    with db_connection() as conn:
        cursor = conn.cursor()
        # Try update first
        cursor.execute('''
            UPDATE writing_preferences
            SET preference_value = ?, updated_at = CURRENT_TIMESTAMP
            WHERE preference_key = ?
        ''', (value, key))

        if not hasattr(cursor, 'rowcount') or cursor.rowcount == 0:
            # Row might not exist — insert
            try:
                cursor.execute('''
                    INSERT INTO writing_preferences (preference_key, preference_value)
                    VALUES (?, ?)
                ''', (key, value))
            except Exception:
                pass  # Already exists (race condition)

        conn.commit()
        logger.info("[WRITING_PREFS] Updated %s", key)
        return True


def update_writing_preferences(preferences: dict) -> bool:
    """Bulk update writing preferences from a dict."""
    for key, value in preferences.items():
        update_preference(key, str(value))
    return True


def build_writing_context(campaign_guidelines: Optional[str] = None) -> str:
    """Build a combined writing context string for LLM prompts.

    Merges org-wide preferences with optional campaign-specific guidelines.
    """
    prefs = get_writing_preferences()

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
