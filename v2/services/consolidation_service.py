"""
Consolidation Service — merge multiple signals per company into one.

When an account has multiple intent signals (e.g., Ghost Branch + Timezone Library
+ Dependency Detected), they get consolidated into a single signal with combined
evidence. The strongest signal type becomes the primary, and all individual
findings are preserved in the description and evidence.

Usage:
    from v2.services.consolidation_service import consolidate_account, consolidate_all

    # Consolidate one account
    consolidate_account(account_id=42)

    # Consolidate all accounts with 2+ active signals
    consolidate_all()
"""
import json
import logging
from typing import Optional

from v2.db import db_connection, insert_returning_id, row_to_dict, rows_to_dicts, safe_json_dumps

logger = logging.getLogger(__name__)

# Signal type strength ranking (higher = stronger evidence of i18n intent)
_SIGNAL_TYPE_PRIORITY = {
    'dependency_injection': 10,
    'dependency_detected': 10,
    'smoking_gun_fork': 9,
    'tms_config_file': 8,
    'competitor_usage': 8,
    'ghost_branch': 7,
    'ghost_branch_active': 7,
    'ci_localization_pipeline': 7,
    'rfc_discussion': 6,
    'rfc_discussion_high': 6,
    'framework_config': 6,
    'documentation_intent': 5,
    'hiring_localization': 5,
    'hiring_international': 5,
    'hiring_hidden_role_i18n': 5,
    'hiring_hidden_role_platform_i18n': 5,
    'hidden_localization_role': 5,
    'job_posting_intent': 5,
    'broken_translation_site': 4,
    'broken_translation_path': 4,
    'missing_translations': 4,
    'missing_translation_404': 4,
    'website_audit': 4,
    'website_translation_audit': 4,
    'timezone_library': 3,
    'academy_university': 3,
    'youtube_channel': 3,
    'youtube_channel_academy': 3,
    'website_demo_videos': 3,
    'regional_domain_detection': 2,
    'market_expansion': 2,
    'product_multilingual': 2,
    'expansion_signal_apac': 2,
    'already_launched': 2,
    'funding_round': 2,
    'global_expansion': 2,
}


def _signal_strength(signal_type: str) -> int:
    """Return priority score for a signal type (higher = stronger)."""
    return _SIGNAL_TYPE_PRIORITY.get(signal_type or '', 1)


def _build_consolidated_description(signals: list) -> str:
    """Build a concise combined description from multiple signals."""
    parts = []
    for s in sorted(signals, key=lambda x: -_signal_strength(x.get('signal_type', ''))):
        stype = (s.get('signal_type') or 'unknown').replace('_', ' ').title()
        desc = s.get('signal_description', '')
        # Truncate long descriptions
        if len(desc) > 120:
            desc = desc[:117] + '...'
        parts.append(f"{stype}: {desc}")

    header = f"{len(signals)} i18n signals detected"
    body = '; '.join(parts)

    # Keep total length reasonable
    if len(body) > 800:
        body = body[:797] + '...'

    return f"{header}. {body}"


def _build_consolidated_evidence(signals: list) -> str:
    """Combine evidence from all signals into a JSON array."""
    evidence_items = []
    for s in signals:
        item = {
            'signal_type': s.get('signal_type'),
            'description': s.get('signal_description', ''),
            'evidence': s.get('evidence_value', ''),
            'original_signal_id': s.get('id'),
        }
        evidence_items.append(item)
    return safe_json_dumps(evidence_items)


def consolidate_account(account_id: int) -> Optional[int]:
    """Merge all active signals for an account into one consolidated signal.

    - Creates a new consolidated signal with combined description/evidence
    - Archives all original signals (preserved, not deleted)
    - Picks the strongest signal type as the primary
    - Re-recommends the best campaign

    Returns:
        The consolidated signal_id, or None if no consolidation needed.
    """
    from v2.services.campaign_service import recommend_campaign

    with db_connection() as conn:
        cursor = conn.cursor()

        # Get all active (non-archived) signals for this account
        cursor.execute('''
            SELECT * FROM intent_signals
            WHERE account_id = ? AND status NOT IN ('archived', 'noise')
            ORDER BY created_at ASC
        ''', (account_id,))
        signals = rows_to_dicts(cursor.fetchall())

        if len(signals) <= 1:
            return None  # Nothing to consolidate

        # Find the strongest signal type
        best = max(signals, key=lambda s: _signal_strength(s.get('signal_type', '')))
        best_type = best.get('signal_type')

        # Build consolidated description and evidence
        consolidated_desc = _build_consolidated_description(signals)
        consolidated_evidence = _build_consolidated_evidence(signals)

        # Pick the best BDR quality score and positioning from originals
        best_score = max((s.get('bdr_quality_score') or 0) for s in signals)
        best_positioning = best.get('bdr_positioning', '')

        # Recommend campaign for the strongest signal type
        rec = recommend_campaign(signal_type=best_type)

        # Create the consolidated signal
        signal_id = insert_returning_id(cursor, '''
            INSERT INTO intent_signals (
                account_id, signal_description, signal_type, evidence_type,
                evidence_value, signal_source, recommended_campaign_id,
                recommended_campaign_reasoning, status, created_by,
                bdr_quality_score, bdr_positioning
            ) VALUES (?, ?, ?, 'consolidated', ?, 'consolidation', ?, ?, 'new', 'consolidation_service', ?, ?)
        ''', (
            account_id, consolidated_desc, best_type,
            consolidated_evidence, rec.get('campaign_id'), rec.get('reasoning'),
            best_score if best_score > 0 else None, best_positioning or None,
        ))

        # Archive all original signals
        original_ids = [s['id'] for s in signals]
        placeholders = ','.join(['?'] * len(original_ids))
        cursor.execute(f'''
            UPDATE intent_signals SET status = 'archived'
            WHERE id IN ({placeholders})
        ''', original_ids)

        conn.commit()

    logger.info(
        "[CONSOLIDATE] Account %d: merged %d signals into signal %d (primary type: %s)",
        account_id, len(signals), signal_id, best_type,
    )
    return signal_id


def consolidate_all(dry_run: bool = False) -> dict:
    """Consolidate signals for ALL accounts that have 2+ active signals.

    Args:
        dry_run: if True, just report what would be consolidated without changing anything.

    Returns:
        Summary dict with counts and details.
    """
    with db_connection() as conn:
        cursor = conn.cursor()

        # Find accounts with multiple active signals
        cursor.execute('''
            SELECT s.account_id, a.company_name, COUNT(*) as signal_count
            FROM intent_signals s
            JOIN monitored_accounts a ON s.account_id = a.id
            WHERE s.status NOT IN ('archived', 'noise')
            GROUP BY s.account_id
            HAVING signal_count > 1
            ORDER BY signal_count DESC
        ''')
        accounts = rows_to_dicts(cursor.fetchall())

    if not accounts:
        return {'consolidated': 0, 'signals_merged': 0, 'accounts': []}

    results = {
        'consolidated': 0,
        'signals_merged': 0,
        'accounts': [],
    }

    for acct in accounts:
        account_id = acct['account_id']
        company = acct['company_name']
        count = acct['signal_count']

        if dry_run:
            results['accounts'].append({
                'company': company,
                'signal_count': count,
                'action': 'would consolidate',
            })
            results['consolidated'] += 1
            results['signals_merged'] += count
            continue

        new_signal_id = consolidate_account(account_id)
        if new_signal_id:
            results['accounts'].append({
                'company': company,
                'signals_merged': count,
                'new_signal_id': new_signal_id,
            })
            results['consolidated'] += 1
            results['signals_merged'] += count

    return results
