"""One-time cleanup: consolidate multiple signals per company into one.

Finds all accounts with 2+ active signals and merges them into a single
consolidated signal per company. Original signals are archived (not deleted).

Usage:
    python seed_consolidate_signals.py           # Run consolidation
    python seed_consolidate_signals.py --dry-run  # Preview without changing anything
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from v2.services.consolidation_service import consolidate_all


def main():
    dry_run = '--dry-run' in sys.argv

    if dry_run:
        print("DRY RUN — no changes will be made.\n")

    result = consolidate_all(dry_run=dry_run)

    if result['consolidated'] == 0:
        print("No accounts need consolidation. All accounts have 1 or fewer active signals.")
        return

    print(f"\n{'Would consolidate' if dry_run else 'Consolidated'} {result['consolidated']} accounts "
          f"({result['signals_merged']} signals merged):\n")

    for acct in result['accounts']:
        company = acct['company']
        count = acct.get('signals_merged') or acct.get('signal_count', 0)
        action = acct.get('action', f"-> signal #{acct.get('new_signal_id', '?')}")
        print(f"  {company}: {count} signals {action}")

    if dry_run:
        print("\nRun without --dry-run to apply changes.")
    else:
        print("\nDone. Original signals archived, consolidated signals created.")


if __name__ == '__main__':
    main()
