#!/usr/bin/env python3
"""
One-time migration: Re-calculate tiers from existing scan data.

This script reads the stored scoring_v2 data from each account's latest
report and re-applies the tier mapping (which now includes THINKING → Tier 1).

No re-scanning needed — it uses the data already in the database.

Usage:
    python migrate_tiers.py          # Dry run (shows what would change)
    python migrate_tiers.py --apply  # Actually update the database
"""
import json
import sys
from database import get_db_connection

# The updated mapping (must match database.py and scoring/compat.py)
_MATURITY_TO_TIER = {
    'pre_i18n': 0,
    'thinking': 1,
    'preparing': 2,
    'active_implementation': 2,
    'recently_launched': 3,
    'mature_midmarket': 3,
    'enterprise_scale': 2,
}


def migrate_tiers(apply=False):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all accounts with their latest report's scan_data
    cursor.execute('''
        SELECT ma.id, ma.company_name, ma.current_tier, r.scan_data
        FROM monitored_accounts ma
        JOIN reports r ON r.id = ma.latest_report_id
        WHERE ma.latest_report_id IS NOT NULL
    ''')
    rows = cursor.fetchall()

    changes = []
    for row in rows:
        account_id = row['id']
        company = row['company_name']
        old_tier = row['current_tier']

        try:
            scan_data = json.loads(row['scan_data']) if isinstance(row['scan_data'], str) else row['scan_data']
        except (json.JSONDecodeError, TypeError):
            continue

        scoring_v2 = scan_data.get('scoring_v2') if scan_data else None
        if not scoring_v2 or not isinstance(scoring_v2, dict):
            continue

        maturity = scoring_v2.get('org_maturity_level', '')
        new_tier = _MATURITY_TO_TIER.get(maturity, 0)

        if new_tier != old_tier:
            maturity_label = scoring_v2.get('org_maturity_label', maturity)
            confidence = scoring_v2.get('confidence_percent', 0)
            readiness = scoring_v2.get('readiness_index', 0)
            outreach = scoring_v2.get('outreach_angle_label', '')
            evidence = (
                f"V2: {maturity_label} (confidence: {confidence:.0f}%, "
                f"readiness: {readiness:.2f}, outreach: {outreach})"
            )
            changes.append({
                'id': account_id,
                'company': company,
                'old_tier': old_tier,
                'new_tier': new_tier,
                'maturity': maturity,
                'evidence': evidence,
            })

    # Report findings
    print(f"\nAccounts with latest report: {len(rows)}")
    print(f"Tier changes needed: {len(changes)}\n")

    if not changes:
        print("No changes needed — all tiers are already correct.")
        conn.close()
        return

    # Group by change type
    by_change = {}
    for c in changes:
        key = f"Tier {c['old_tier']} → Tier {c['new_tier']} ({c['maturity']})"
        by_change.setdefault(key, []).append(c)

    for change_type, accounts in sorted(by_change.items()):
        print(f"  {change_type}: {len(accounts)} accounts")
        for a in accounts[:5]:
            print(f"    - {a['company']}")
        if len(accounts) > 5:
            print(f"    ... and {len(accounts) - 5} more")
        print()

    if apply:
        print("Applying changes...")
        for c in changes:
            cursor.execute('''
                UPDATE monitored_accounts
                SET current_tier = ?, evidence_summary = ?
                WHERE id = ?
            ''', (c['new_tier'], c['evidence'], c['id']))
        conn.commit()
        print(f"Done — updated {len(changes)} accounts.")
    else:
        print("Dry run — no changes made. Run with --apply to update the database.")

    conn.close()


if __name__ == '__main__':
    apply = '--apply' in sys.argv
    migrate_tiers(apply=apply)
