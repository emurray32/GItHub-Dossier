"""
V2 Database Schema — new tables for the intent-signal-first domain model.

Called from database.init_db() to create tables alongside the legacy schema.
Uses the same _adapt_ddl() and _safe_add_column() helpers for PG/SQLite compat.
"""
import logging

logger = logging.getLogger(__name__)


def init_v2_schema(cursor, adapt_ddl, safe_add_column):
    """Create all v2 tables and add new columns to existing tables.

    Args:
        cursor: database cursor (already inside a transaction)
        adapt_ddl: the _adapt_ddl function from database.py
        safe_add_column: the _safe_add_column function from database.py
    """
    logger.info("[V2] Initializing v2 schema...")

    # -----------------------------------------------------------------------
    # Extend existing tables
    # -----------------------------------------------------------------------

    # monitored_accounts: add account_owner and account_status
    safe_add_column(cursor, 'monitored_accounts', "account_owner TEXT")
    safe_add_column(cursor, 'monitored_accounts', "account_status TEXT DEFAULT 'new'")
    safe_add_column(cursor, 'monitored_accounts', "linkedin_url TEXT")
    safe_add_column(cursor, 'monitored_accounts', "company_size TEXT")

    # campaigns: add campaign_type and writing_guidelines
    safe_add_column(cursor, 'campaigns', "campaign_type TEXT DEFAULT 'signal_based'")
    safe_add_column(cursor, 'campaigns', "writing_guidelines TEXT")

    # -----------------------------------------------------------------------
    # intent_signals — the root object of the v2 domain
    # -----------------------------------------------------------------------
    cursor.execute(adapt_ddl('''
        CREATE TABLE IF NOT EXISTS intent_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            signal_description TEXT NOT NULL,
            evidence_type TEXT DEFAULT 'manual',
            evidence_value TEXT,
            signal_type TEXT,
            signal_source TEXT DEFAULT 'manual_entry',
            recommended_campaign_id INTEGER,
            recommended_campaign_reasoning TEXT,
            status TEXT DEFAULT 'new',
            created_by TEXT,
            ingestion_batch_id TEXT,
            raw_payload TEXT,
            scan_signal_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES monitored_accounts(id) ON DELETE CASCADE,
            FOREIGN KEY (recommended_campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL
        )
    '''))

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_intent_signals_account
        ON intent_signals(account_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_intent_signals_status
        ON intent_signals(status)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_intent_signals_created
        ON intent_signals(created_at DESC)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_intent_signals_type
        ON intent_signals(signal_type)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_intent_signals_source
        ON intent_signals(signal_source)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_intent_signals_scan_signal
        ON intent_signals(scan_signal_id)
    ''')

    # -----------------------------------------------------------------------
    # prospects — people found via Apollo, tied to signals + accounts
    # -----------------------------------------------------------------------
    cursor.execute(adapt_ddl('''
        CREATE TABLE IF NOT EXISTS prospects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            signal_id INTEGER,
            full_name TEXT,
            first_name TEXT,
            last_name TEXT,
            title TEXT,
            email TEXT,
            email_verified INTEGER DEFAULT 0,
            linkedin_url TEXT,
            apollo_person_id TEXT,
            apollo_contact_id TEXT,
            do_not_contact INTEGER DEFAULT 0,
            enrollment_status TEXT DEFAULT 'found',
            sequence_id TEXT,
            sequence_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES monitored_accounts(id) ON DELETE CASCADE,
            FOREIGN KEY (signal_id) REFERENCES intent_signals(id) ON DELETE SET NULL
        )
    '''))

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_prospects_account
        ON prospects(account_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_prospects_signal
        ON prospects(signal_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_prospects_email
        ON prospects(email)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_prospects_enrollment
        ON prospects(enrollment_status)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_prospects_apollo
        ON prospects(apollo_person_id)
    ''')

    # Backwards compat: add apollo_contact_id if table existed before this column was added
    safe_add_column(cursor, 'prospects', "apollo_contact_id TEXT")

    # -----------------------------------------------------------------------
    # drafts — persisted, editable email drafts per prospect per step
    # -----------------------------------------------------------------------
    cursor.execute(adapt_ddl('''
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prospect_id INTEGER NOT NULL,
            signal_id INTEGER,
            campaign_id INTEGER,
            sequence_step INTEGER NOT NULL,
            subject TEXT,
            body TEXT,
            generated_by TEXT,
            generation_model TEXT,
            generation_context TEXT,
            last_feedback TEXT,
            status TEXT DEFAULT 'generated',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE,
            FOREIGN KEY (signal_id) REFERENCES intent_signals(id) ON DELETE SET NULL,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL
        )
    '''))

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_drafts_prospect
        ON drafts(prospect_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_drafts_signal
        ON drafts(signal_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_drafts_status
        ON drafts(status)
    ''')

    # -----------------------------------------------------------------------
    # feedback_log — critique/regeneration history
    # -----------------------------------------------------------------------
    cursor.execute(adapt_ddl('''
        CREATE TABLE IF NOT EXISTS feedback_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER,
            prospect_id INTEGER,
            signal_id INTEGER,
            critique TEXT NOT NULL,
            sequence_step INTEGER,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE SET NULL,
            FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE SET NULL,
            FOREIGN KEY (signal_id) REFERENCES intent_signals(id) ON DELETE SET NULL
        )
    '''))

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_feedback_draft
        ON feedback_log(draft_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_feedback_signal
        ON feedback_log(signal_id)
    ''')

    # -----------------------------------------------------------------------
    # activity_log — audit trail for all key actions
    # -----------------------------------------------------------------------
    cursor.execute(adapt_ddl('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            entity_type TEXT,
            entity_id INTEGER,
            details TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    '''))

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_activity_event_type
        ON activity_log(event_type)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_activity_entity
        ON activity_log(entity_type, entity_id)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_activity_created
        ON activity_log(created_at DESC)
    ''')

    # -----------------------------------------------------------------------
    # writing_preferences — org-wide writing rules (key-value)
    # -----------------------------------------------------------------------
    cursor.execute(adapt_ddl('''
        CREATE TABLE IF NOT EXISTS writing_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            preference_key TEXT NOT NULL UNIQUE,
            preference_value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    '''))

    # Seed default writing preferences if empty
    _seed_writing_preferences(cursor)

    logger.info("[V2] Schema initialization complete.")


def _seed_writing_preferences(cursor):
    """Insert default writing preferences if the table is empty."""
    cursor.execute("SELECT COUNT(*) as cnt FROM writing_preferences")
    row = cursor.fetchone()
    count = row['cnt'] if isinstance(row, dict) else row[0]
    if count > 0:
        return

    defaults = [
        ('tone', 'Professional but direct. No fluff. Sound like a peer, not a salesperson.'),
        ('banned_phrases', 'synergy, leverage, circle back, touch base, low-hanging fruit, paradigm shift, move the needle, deep dive, align, best-in-class, world-class, game-changer, disruptive'),
        ('preferred_structure', 'Hook (1 sentence referencing their specific signal/evidence) → Pain (1-2 sentences on the problem they face) → Bridge (1 sentence on how Phrase solves it) → CTA (1 sentence, specific and low-commitment)'),
        ('cta_guidance', 'Always propose a specific next step. Prefer: "Open to a 15-min call this week?" or "Want me to send a sandbox link?" Avoid vague CTAs like "Let me know your thoughts."'),
        ('signoff_guidance', 'Use first name only. No "Best regards" or "Sincerely." Just: "— Eric" or "Eric"'),
        ('custom_rules', 'Never mention competitors by name. Never claim Phrase is "the best" — let the evidence speak. Always reference their specific repo, library, or signal when possible. Keep emails under 120 words.'),
    ]

    for key, value in defaults:
        try:
            cursor.execute(
                "INSERT INTO writing_preferences (preference_key, preference_value) VALUES (?, ?)",
                (key, value),
            )
        except Exception:
            pass  # Already exists (race condition guard)
