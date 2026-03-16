"""Link all campaigns to the 4-Email Single Thread Apollo sequence.

Sets each campaign's default sequence to "Template - 4 Email (Single Thread)"
so draft generation produces 4 threaded emails with one subject line.

Usage:
    python seed_campaign_sequences.py

Idempotent: safe to run multiple times.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from v2.db import db_connection

# Apollo sequence ID for "Template - 4 Email (Single Thread)"
SEQUENCE_ID = '699a30753ab26800215fa07e'
SEQUENCE_NAME = 'Template - 4 Email (Single Thread)'
SEQUENCE_CONFIG = json.dumps({'num_steps': 4, 'single_thread': True})

CAMPAIGN_NAMES = [
    'RepoRadar',
    'Hiring Signal',
    'Scale & Expansion',
    'Translation Quality',
    'Competitive Displacement',
    'Phrase Studio',
]


def seed():
    updated = 0
    skipped = 0

    with db_connection() as conn:
        cursor = conn.cursor()
        for name in CAMPAIGN_NAMES:
            cursor.execute('''
                UPDATE campaigns
                SET sequence_id = ?, sequence_name = ?, sequence_config = ?
                WHERE name = ?
            ''', (SEQUENCE_ID, SEQUENCE_NAME, SEQUENCE_CONFIG, name))
            if cursor.rowcount and cursor.rowcount > 0:
                updated += 1
                print(f'  Updated: {name} -> {SEQUENCE_NAME}')
            else:
                skipped += 1
                print(f'  Skipped (not found): {name}')
        conn.commit()

    print(f'\nDone. {updated} updated, {skipped} skipped.')


if __name__ == '__main__':
    seed()
