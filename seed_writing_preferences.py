"""Seed org-wide writing preferences into the writing_preferences table.

These preferences are loaded by build_writing_context() and injected into
every draft generation prompt. They enforce anti-slop rules, tone, and
structural constraints across all campaigns.

Usage:
    python seed_writing_preferences.py

Idempotent: updates existing keys, creates missing ones.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from v2.services.writing_prefs_service import update_preference

PREFERENCES = {
    "tone": (
        "Peer-to-peer, slightly technical, never salesy. "
        "Write like a colleague sending a quick note, not like a marketer writing copy. "
        "Short sentences. Sentence fragments are fine. "
        "Confident, not apologetic. Don't hedge."
    ),

    "banned_phrases": (
        # AI-slop words
        "delve, leverage, streamline, empower, cutting-edge, game-changer, robust, seamless, "
        "synergy, holistic, innovative, revolutionize, elevate, optimize, harness, spearhead, "
        "deep dive, ecosystem, paradigm, scalable, best-in-class, unlock, supercharge, "
        "transformative, world-class, end-to-end, state-of-the-art, next-generation, "
        "mission-critical, utilize, facilitate, "
        # Banned openers
        "I hope this finds you well, I hope you're doing well, I came across your, "
        "I was impressed by, I couldn't help but notice, I wanted to reach out, "
        "I'm reaching out because, just wanted to, "
        # Banned closers
        "I'd love to, I'd be happy to, looking forward to, don't hesitate to, "
        "feel free to, let's connect, let's schedule a time, "
        # Banned filler
        "touching base, circle back, loop in, at the end of the day, move the needle, "
        "low-hanging fruit, thought leadership, value proposition, pain point"
    ),

    "preferred_structure": (
        "1. Greeting: Hey {{first_name}},\n"
        "2. Hook: Start with THEIR specific signal evidence. Never start with 'I'.\n"
        "3. Pain/Value: 1-2 sentences connecting signal to automation value.\n"
        "4. Soft CTA: Ask for interest, not time. 'Worth a look?' / 'On the radar?'\n"
        "5. Signature: {{sender_first_name}}"
    ),

    "cta_guidance": (
        "Ask for INTEREST, not TIME. "
        "Good: 'Worth a look?', 'Curious if this is on the radar?', 'Open to seeing how we fit?' "
        "Bad: 'Can we schedule 15 minutes?', 'Would you be available for a call?', "
        "'Let's set up a meeting.'"
    ),

    "signoff_guidance": (
        "End with just {{sender_first_name}}. No 'Best regards', no 'Cheers', "
        "no 'Thanks', no 'Best'. Just the name."
    ),

    "custom_rules": (
        "HARD LIMITS:\n"
        "- Under 100 words total body\n"
        "- Max 2 sentences per paragraph (prefer 1)\n"
        "- Never start email body with 'I'\n"
        "- Never use exclamation marks\n"
        "- Max 1 question per email\n"
        "- No bullet points in cold emails\n"
        "- No em dashes for dramatic effect\n"
        "- No 'we' statements before establishing relevance\n"
        "- Lead with THEIR situation, not your pitch\n"
        "\n"
        "PHRASE MESSAGING:\n"
        "- Product name is 'Phrase' (not 'Phrase TMS', not 'our platform')\n"
        "- GitHub Sync is the killer feature for engineering signals\n"
        "- DO mention: automation, API, GitHub integration, CI/CD\n"
        "- DO NOT mention: 'high quality translations', 'professional linguists', 'AI-powered'"
    ),
}


def seed_writing_preferences():
    """Insert or update all writing preferences."""
    for key, value in PREFERENCES.items():
        update_preference(key, value)
        print(f"[SEED] Writing preference '{key}' updated ({len(value)} chars)")

    print(f"\n[SEED] Done. {len(PREFERENCES)} writing preferences seeded.")


if __name__ == '__main__':
    print("=" * 60)
    print("Seeding Writing Preferences (Anti-Slop Rules)")
    print("=" * 60)
    seed_writing_preferences()
