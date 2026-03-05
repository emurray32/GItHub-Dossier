#!/usr/bin/env python3
"""
One-time migration: Re-calculate tiers from existing scan data.

This script reads the stored scoring_v2 data from each account's latest
report and re-applies the tier mapping (which now includes THINKING -> Tier 1).

No re-scanning needed -- it uses the data already in the database.

Usage:
    python migrate_tiers.py          # Dry run (shows what would change)
    python migrate_tiers.py --apply  # Actually update the database
"""
import json
import sys
from database import db_connection, calculate_tier_from_scan


def migrate_tiers(apply=False):
    with db_connection() as conn:
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

            if not scan_data:
                continue

            new_tier, evidence = calculate_tier_from_scan(scan_data)

            if new_tier != old_tier:
                changes.append({
                    'id': account_id,
                    'company': company,
                    'old_tier': old_tier,
                    'new_tier': new_tier,
                    'evidence': evidence,
                })

        # Report findings
        print(f"\nAccounts with latest report: {len(rows)}")
        print(f"Tier changes needed: {len(changes)}\n")

        if not changes:
            print("No changes needed — all tiers are already correct.")
            return

        # Group by change type
        by_change = {}
        for c in changes:
            key = f"Tier {c['old_tier']} → Tier {c['new_tier']}"
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


if __name__ == '__main__':
    apply = '--apply' in sys.argv
    migrate_tiers(apply=apply)
