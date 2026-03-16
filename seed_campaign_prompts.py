"""Seed campaign prompts into the campaigns table.

Each prompt gives the LLM campaign-specific instructions for writing
email sequences — what signals to reference, how to position Phrase,
and how to structure the 3-step sequence.

Usage:
    python seed_campaign_prompts.py

Idempotent: updates existing campaigns by name, skips missing ones.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from v2.db import db_connection

CAMPAIGN_PROMPTS = {
    "RepoRadar": (
        "Write emails for prospects at companies where we detected GitHub-level "
        "engineering signals: i18n library additions, locale branches, RFC discussions, "
        "or config file changes. Lead with the exact evidence — name the repo, library, "
        "or branch. Position Phrase as the automation layer they need before manual file "
        "management becomes a bottleneck. Email 1: reference the specific signal and "
        "connect it to GitHub Sync. Email 2: shift to a different angle — developer "
        "velocity, CI/CD integration, or how teams at their stage avoid building "
        "translation infrastructure from scratch. Email 3: short breakup, under 50 words. "
        "These prospects are technical — write like an engineer talking to an engineer. "
        "No marketing language. Reference repos and libraries by name. CTA should ask "
        "for interest, not time."
    ),

    "Hiring Signal": (
        "Write emails for prospects at companies hiring for localization, "
        "internationalization, or related roles. The signal is the job posting itself — "
        "reference the specific role title and what it suggests about their i18n plans. "
        "Email 1: acknowledge they are building out their localization team and position "
        "Phrase as the platform that team will need. Email 2: shift angle to how Phrase "
        "reduces the infrastructure burden so their new hire can focus on strategy, not "
        "plumbing. Email 3: short breakup. Tone should be helpful and peer-level — they "
        "are investing in localization, not exploring it. Respect their sophistication. "
        "Avoid implying they do not know what they need."
    ),

    "Scale & Expansion": (
        "Write emails for prospects at companies showing market expansion signals: new "
        "funding rounds, APAC launches, multilingual product features, or international "
        "growth mentions. Lead with the specific expansion evidence. Email 1: reference "
        "their growth signal and connect to the operational challenge of scaling "
        "localization across new markets. Position Phrase as continuous localization "
        "infrastructure. Email 2: take a different angle — speed to market, reducing "
        "localization as a launch bottleneck, or how similar-stage companies automate "
        "this. Email 3: short breakup. Tone is business-outcome focused rather than "
        "deeply technical. These buyers care about speed and coverage, not GitHub "
        "integrations."
    ),

    "Translation Quality": (
        "Write emails for prospects at companies with visible translation problems: "
        "broken locale paths, missing translations, 404s on localized pages, or "
        "inconsistent UI strings on their website. Lead with a specific, tactful "
        "observation — never embarrass them. Email 1: reference what you found on their "
        "site and frame it as a common scaling problem, not a failure. Position Phrase "
        "as the quality layer that catches these issues automatically. Email 2: shift to "
        "the cost of poor translations — user trust, conversion impact, or support "
        "tickets from international users. Email 3: short breakup. Be respectful — "
        "website quality issues are sensitive. Frame everything as \"this is normal at "
        "your stage\" rather than pointing out mistakes."
    ),

    "Competitive Displacement": (
        "Write emails for prospects at companies already using a competing TMS — "
        "detected via config files, integration references, or known competitor tooling. "
        "Do not trash the competitor by name. Lead with curiosity about their current "
        "setup. Email 1: acknowledge they already have localization infrastructure and "
        "position Phrase as the modern alternative — better developer experience, native "
        "GitHub integration, faster workflows. Email 2: shift to a specific pain point "
        "common with legacy TMS tools — slow connector syncs, manual file handoffs, or "
        "limited API access. Email 3: short breakup. These prospects are educated buyers "
        "who know the space. Write with respect for their existing investment. The angle "
        "is \"better fit\" not \"your tool is bad.\""
    ),

    "Phrase Studio": (
        "Write emails for prospects at companies producing video content — YouTube "
        "channels, product demos, training videos, or marketing content — that could "
        "reach more markets through localization. Lead with the specific video content "
        "you found. Email 1: reference their video presence and position Phrase Studio "
        "as the way to localize video content for new audiences without rebuilding from "
        "scratch. Email 2: shift angle to the business opportunity — how much of their "
        "audience they are leaving on the table by keeping video content in one language, "
        "or how competitors are already localizing video. Email 3: short breakup. Tone "
        "depends on persona — marketing leaders care about reach and engagement, product "
        "leaders care about user experience across markets. Keep it concrete. These "
        "prospects may not realize video localization is even practical at scale yet, so "
        "frame Phrase Studio as the tool that makes it possible."
    ),
}


def seed():
    updated = 0
    skipped = 0

    with db_connection() as conn:
        cursor = conn.cursor()
        for name, prompt in CAMPAIGN_PROMPTS.items():
            cursor.execute(
                "UPDATE campaigns SET prompt = ? WHERE name = ?",
                (prompt, name),
            )
            if cursor.rowcount and cursor.rowcount > 0:
                updated += 1
                print(f"  Updated: {name}")
            else:
                skipped += 1
                print(f"  Skipped (not found): {name}")
        conn.commit()

    print(f"\nDone. {updated} updated, {skipped} skipped.")


if __name__ == '__main__':
    seed()
