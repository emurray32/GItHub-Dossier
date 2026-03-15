"""Seed campaign persona hierarchies into the campaign_personas table.

Each campaign gets tiered personas ordered by priority. When find_prospects
runs, it searches Apollo tier by tier — priority 0 first, then 1, then 2 —
and tags each result with the matched persona. This ensures the right
contacts are found for each signal type.

Usage:
    python seed_campaign_personas.py

Idempotent: skips campaigns that already have personas defined.
"""

import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from database import db_connection, create_campaign_persona
from v2.db import rows_to_dicts


# ═══════════════════════════════════════════════════════════════════
# PERSONA DEFINITIONS BY CAMPAIGN TYPE
#
# Each campaign maps to a list of persona tiers. Tiers are searched
# in priority order (0 = highest priority, searched first).
#
# Fields:
#   persona_name: Human label shown in UI and logs
#   titles: Apollo person_titles search parameter
#   seniorities: Apollo person_seniorities parameter
#   sequence_id: Apollo sequence to enroll this persona into (empty = use campaign default)
#   priority: Search order (0 first)
# ═══════════════════════════════════════════════════════════════════

PERSONA_DEFINITIONS = {
    # ── Engineering-Led Signals ──────────────────────────────────
    # These signal types come from GitHub scans: someone added an
    # i18n library, opened an RFC about localization, or has an
    # active branch. The right contacts are engineering leaders
    # and the engineers actually doing the work.

    "dependency_injection": [
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "VP of Engineering",
                "Head of Engineering", "CTO",
                "Director of Engineering", "Director Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "i18n / Platform Engineer",
            "titles": [
                "Localization Engineer", "Internationalization Engineer",
                "i18n Engineer", "Platform Engineer",
                "Senior Software Engineer", "Staff Engineer",
            ],
            "seniorities": ["senior", "manager"],
            "priority": 1,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product", "VP of Product",
                "Director of Product", "Director of Product Management",
            ],
            "seniorities": ["vp", "director"],
            "priority": 2,
        },
    ],

    "ghost_branch": [
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "VP of Engineering",
                "Head of Engineering", "CTO",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "i18n / Platform Engineer",
            "titles": [
                "Localization Engineer", "Internationalization Engineer",
                "Platform Engineer", "Senior Software Engineer",
                "Staff Engineer", "Software Engineer",
            ],
            "seniorities": ["senior", "manager"],
            "priority": 1,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product",
                "Director of Product",
            ],
            "seniorities": ["vp", "director"],
            "priority": 2,
        },
    ],

    "ghost_branch_active": [
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "VP of Engineering",
                "Head of Engineering", "CTO",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "i18n / Platform Engineer",
            "titles": [
                "Localization Engineer", "Internationalization Engineer",
                "Platform Engineer", "Senior Software Engineer",
            ],
            "seniorities": ["senior", "manager"],
            "priority": 1,
        },
    ],

    "rfc_discussion": [
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "VP of Engineering",
                "Head of Engineering", "CTO",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "i18n / Platform Engineer",
            "titles": [
                "Localization Engineer", "Internationalization Engineer",
                "Platform Engineer", "Staff Engineer",
                "Senior Software Engineer",
            ],
            "seniorities": ["senior", "manager"],
            "priority": 1,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product",
                "Director of Product", "Product Manager",
            ],
            "seniorities": ["vp", "director", "manager"],
            "priority": 2,
        },
    ],

    "rfc_discussion_high": [
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "VP of Engineering",
                "Head of Engineering", "CTO",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "i18n / Platform Engineer",
            "titles": [
                "Localization Engineer", "Internationalization Engineer",
                "Platform Engineer", "Staff Engineer",
            ],
            "seniorities": ["senior", "manager"],
            "priority": 1,
        },
    ],

    "smoking_gun_fork": [
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "VP of Engineering",
                "Head of Engineering", "CTO",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "i18n / Platform Engineer",
            "titles": [
                "Localization Engineer", "Internationalization Engineer",
                "Platform Engineer", "Staff Engineer",
            ],
            "seniorities": ["senior", "manager"],
            "priority": 1,
        },
    ],

    "tms_config_file": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "VP Localization",
                "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "VP of Engineering",
                "Head of Engineering", "CTO",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 1,
        },
    ],

    # ── Expansion / Market Signals ───────────────────────────────
    # These come from spreadsheet imports, job postings, or market
    # research. The right contacts are localization leaders (if they
    # exist) and product leaders driving international expansion.

    "global_expansion": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "VP Localization",
                "Localization Manager", "Head of Globalization",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product", "VP of Product",
                "CPO", "Director of Product Management",
                "Director of Product",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 1,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "VP of Engineering",
                "Head of Engineering", "CTO",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 2,
        },
    ],

    "market_expansion": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "VP Localization",
                "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product",
                "CPO", "Director of Product",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 1,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering", "CTO",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 2,
        },
    ],

    "expansion_signal_apac": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product",
                "Director of Product",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
        {
            "persona_name": "Regional Leader",
            "titles": [
                "Head of APAC", "VP APAC", "GM APAC",
                "Director of International",
                "Head of International",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 2,
        },
    ],

    # ── Hiring Signals ───────────────────────────────────────────

    "hiring_localization": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "VP Localization",
                "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering",
                "CTO", "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 1,
        },
    ],

    "hiring_international": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product",
                "Director of Product",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
    ],

    "hiring_hidden_role_i18n": [
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering",
                "CTO", "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "i18n / Platform Engineer",
            "titles": [
                "Localization Engineer", "Internationalization Engineer",
                "Platform Engineer", "Staff Engineer",
            ],
            "seniorities": ["senior", "manager"],
            "priority": 1,
        },
    ],

    "hiring_hidden_role_platform_i18n": [
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering",
                "CTO", "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "i18n / Platform Engineer",
            "titles": [
                "Localization Engineer", "Internationalization Engineer",
                "Platform Engineer", "Staff Engineer",
            ],
            "seniorities": ["senior", "manager"],
            "priority": 1,
        },
    ],

    "hidden_localization_role": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
    ],

    # ── Website / Translation Quality Signals ────────────────────

    "broken_translation_site": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product",
                "Director of Product",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
        {
            "persona_name": "Marketing Leader",
            "titles": [
                "VP Marketing", "Head of Marketing",
                "Director of Marketing", "CMO",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 2,
        },
    ],

    "broken_translation_path": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
    ],

    "missing_translations": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product",
                "Director of Product",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
    ],

    "missing_translation_404": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
    ],

    "website_audit": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Marketing Leader",
            "titles": [
                "VP Marketing", "Head of Marketing",
                "Director of Marketing", "CMO",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 1,
        },
    ],

    "website_translation_audit": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Marketing Leader",
            "titles": [
                "VP Marketing", "Head of Marketing",
                "Director of Marketing",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
    ],

    # ── Product / Competitor Signals ─────────────────────────────

    "product_multilingual": [
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product", "VP of Product",
                "CPO", "Director of Product",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 1,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering", "CTO",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 2,
        },
    ],

    "competitor_usage": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "VP Localization",
                "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering",
                "CTO", "Director of Engineering",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 1,
        },
    ],

    # ── Funding / Growth Signals ─────────────────────────────────

    "funding_round": [
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product", "CPO",
                "Director of Product",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 0,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering", "CTO",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 1,
        },
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 2,
        },
    ],

    # ── Job Posting Signals ──────────────────────────────────────

    "job_posting_intent": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering",
                "Director of Engineering",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
    ],

    # ── Content / Academy Signals ────────────────────────────────

    "academy_university": [
        {
            "persona_name": "Content / Education Leader",
            "titles": [
                "Head of Education", "Director of Content",
                "Head of Academy", "VP Content",
                "Director of Education", "Head of Learning",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Product Leader",
            "titles": [
                "Head of Product", "VP Product",
                "Director of Product",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 2,
        },
    ],

    "youtube_channel": [
        {
            "persona_name": "Content / Video Leader",
            "titles": [
                "Head of Content", "Director of Content",
                "VP Content", "Head of Video",
                "Director of Video Production",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Marketing Leader",
            "titles": [
                "VP Marketing", "Head of Marketing",
                "Director of Marketing", "CMO",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 1,
        },
    ],

    "youtube_channel_academy": [
        {
            "persona_name": "Content / Education Leader",
            "titles": [
                "Head of Education", "Director of Content",
                "Head of Academy", "VP Content",
                "Head of Learning",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Marketing Leader",
            "titles": [
                "VP Marketing", "Head of Marketing",
                "Director of Marketing",
            ],
            "seniorities": ["vp", "director"],
            "priority": 1,
        },
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 2,
        },
    ],

    "website_demo_videos": [
        {
            "persona_name": "Product / Marketing Leader",
            "titles": [
                "Head of Product Marketing", "VP Marketing",
                "Director of Marketing", "Head of Content",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 1,
        },
    ],

    # ── Already Launched ─────────────────────────────────────────

    "already_launched": [
        {
            "persona_name": "Localization Leader",
            "titles": [
                "Director of Localization", "Globalization Manager",
                "Head of Localization", "VP Localization",
                "Localization Manager",
            ],
            "seniorities": ["director", "vp", "manager"],
            "priority": 0,
        },
        {
            "persona_name": "Engineering Leader",
            "titles": [
                "VP Engineering", "Head of Engineering", "CTO",
            ],
            "seniorities": ["vp", "director", "c_suite"],
            "priority": 1,
        },
    ],
}


# ═══════════════════════════════════════════════════════════════════
# SIGNAL TYPE → CAMPAIGN MAPPING
#
# Maps signal types to the campaign name they should use for personas.
# Multiple signal types can share the same persona definitions.
# ═══════════════════════════════════════════════════════════════════

# For signal types that share a persona set, we can alias them.
# The PERSONA_DEFINITIONS dict above already covers each signal type
# individually where needed.


def seed_campaign_personas():
    """Insert persona tiers for each campaign that has signal types defined.

    This works by:
    1. Loading all campaigns from the DB
    2. For each campaign, looking up its campaign_type or matching signal types
    3. Inserting persona rows from PERSONA_DEFINITIONS

    Skips campaigns that already have personas defined.
    """
    with db_connection() as conn:
        cursor = conn.cursor()

        # Get all campaigns
        cursor.execute("SELECT id, name, campaign_type FROM campaigns ORDER BY id")
        campaigns = rows_to_dicts(cursor.fetchall())

        if not campaigns:
            print("[SEED] No campaigns found in database. Create campaigns first.")
            return

        total_created = 0

        for campaign in campaigns:
            cid = campaign['id']
            cname = campaign['name']
            ctype = campaign.get('campaign_type', '')

            # Check if personas already exist
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM campaign_personas WHERE campaign_id = ?",
                (cid,)
            )
            existing = cursor.fetchone()
            existing_count = existing[0] if existing else 0

            if existing_count > 0:
                print(f"[SEED] Campaign '{cname}' (id={cid}) already has {existing_count} personas. Skipping.")
                continue

            # Find matching persona definitions
            # Try: campaign_type first, then campaign name (lowercased, underscored)
            personas = None

            if ctype and ctype in PERSONA_DEFINITIONS:
                personas = PERSONA_DEFINITIONS[ctype]
            else:
                # Try matching by campaign name
                name_key = cname.lower().replace(' ', '_').replace('-', '_')
                if name_key in PERSONA_DEFINITIONS:
                    personas = PERSONA_DEFINITIONS[name_key]

            if not personas:
                # Fallback: use dependency_injection personas (Engineering + i18n + Product)
                personas = PERSONA_DEFINITIONS.get('dependency_injection')
                if personas:
                    print(f"[SEED] Campaign '{cname}' (id={cid}, type={ctype}) — using default persona set.")
                else:
                    print(f"[SEED] Campaign '{cname}' (id={cid}, type={ctype}) — no persona definitions found. Skipping.")
                    continue

            # Insert persona tiers
            for p in personas:
                create_campaign_persona(
                    campaign_id=cid,
                    persona_name=p['persona_name'],
                    titles=p['titles'],
                    seniorities=p['seniorities'],
                    sequence_id=p.get('sequence_id', ''),
                    sequence_name=None,
                    priority=p['priority'],
                )
                total_created += 1

            print(f"[SEED] Campaign '{cname}' (id={cid}) — created {len(personas)} persona tiers.")

        conn.commit()
        print(f"\n[SEED] Done. Created {total_created} total persona records.")


def seed_standalone_signal_personas():
    """For V2 signal-based workflow: seed personas keyed by signal_type.

    Since V2 campaigns may not have a 1:1 mapping with signal types,
    this creates a lookup table that find_prospects can use when a
    campaign_id is provided but has no personas of its own.

    This inserts into campaign_personas using a synthetic campaign_id = -1
    with the signal_type stored in the persona_name prefix.
    """
    # This is handled by the main seed — each campaign should have its
    # campaign_type set to match the signal types it handles.
    print("[SEED] Signal-type personas are handled via campaign.campaign_type mapping.")
    print("[SEED] Ensure each campaign's campaign_type matches a key in PERSONA_DEFINITIONS.")


if __name__ == '__main__':
    print("=" * 60)
    print("Seeding Campaign Persona Hierarchies")
    print("=" * 60)
    seed_campaign_personas()
