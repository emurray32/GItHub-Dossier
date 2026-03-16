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

    # -----------------------------------------------------------------------
    # bdr_writing_preferences — per-BDR personal writing overrides
    # -----------------------------------------------------------------------
    cursor.execute(adapt_ddl('''
        CREATE TABLE IF NOT EXISTS bdr_writing_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            preference_key TEXT NOT NULL,
            preference_value TEXT NOT NULL,
            override_mode TEXT NOT NULL DEFAULT 'add',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_email, preference_key, override_mode)
        )
    '''))

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_bdr_prefs_email
        ON bdr_writing_preferences(user_email)
    ''')

    # -----------------------------------------------------------------------
    # Smart Ingestion — BDR evaluation columns on intent_signals
    # -----------------------------------------------------------------------
    safe_add_column(cursor, 'intent_signals', "bdr_quality_score INTEGER")
    safe_add_column(cursor, 'intent_signals', "bdr_positioning TEXT")

    logger.info("[V2] Schema initialization complete.")


def _seed_writing_preferences(cursor):
    """Insert default writing preferences if the table is empty."""
    cursor.execute("SELECT COUNT(*) as cnt FROM writing_preferences")
    row = cursor.fetchone()
    count = row['cnt'] if isinstance(row, dict) else row[0]
    if count > 0:
        return

    defaults = [
        ('tone',
         'Peer-to-peer, slightly technical, never salesy. '
         'Write like a colleague sending a quick note, not like a marketer writing copy. '
         'Short sentences. Sentence fragments are fine. '
         'Confident, not apologetic. Don\'t hedge.'),
        ('banned_phrases',
         'delve, leverage, streamline, empower, cutting-edge, game-changer, robust, seamless, '
         'synergy, holistic, innovative, revolutionize, elevate, optimize, harness, spearhead, '
         'deep dive, ecosystem, paradigm, scalable, best-in-class, unlock, supercharge, '
         'transformative, world-class, end-to-end, state-of-the-art, next-generation, '
         'mission-critical, utilize, facilitate, '
         'I hope this finds you well, I hope you\'re doing well, I came across your, '
         'I was impressed by, I couldn\'t help but notice, I wanted to reach out, '
         'I\'m reaching out because, just wanted to, '
         'I\'d love to, I\'d be happy to, looking forward to, don\'t hesitate to, '
         'feel free to, let\'s connect, let\'s schedule a time, '
         'touching base, circle back, loop in, at the end of the day, move the needle, '
         'low-hanging fruit, thought leadership, value proposition, pain point'),
        ('preferred_structure',
         '1. Greeting: Hey {{first_name}},\n'
         '2. Hook: Start with THEIR specific signal evidence. Never start with \'I\'.\n'
         '3. Pain/Value: 1-2 sentences connecting signal to automation value.\n'
         '4. Soft CTA: Ask for interest, not time. \'Worth a look?\' / \'On the radar?\'\n'
         '5. Signature: {{sender_first_name}}'),
        ('cta_guidance',
         'Ask for INTEREST, not TIME. '
         'Good: \'Worth a look?\', \'Curious if this is on the radar?\', \'Open to seeing how we fit?\' '
         'Bad: \'Can we schedule 15 minutes?\', \'Would you be available for a call?\', '
         '\'Let\'s set up a meeting.\''),
        ('signoff_guidance',
         'End with just {{sender_first_name}}. No \'Best regards\', no \'Cheers\', '
         'no \'Thanks\', no \'Best\'. Just the name.'),
        ('custom_rules',
         'HARD LIMITS:\n'
         '- Under 80 words total body\n'
         '- Max 2 sentences per paragraph (prefer 1)\n'
         '- Never start email body with \'I\'\n'
         '- Never use exclamation marks\n'
         '- Max 1 question per email\n'
         '- No bullet points in cold emails\n'
         '- No em dashes for dramatic effect\n'
         '- No \'we\' statements before establishing relevance\n'
         '- Lead with THEIR situation, not your pitch\n'
         '\n'
         'PHRASE MESSAGING:\n'
         '- Product name is \'Phrase\' (not \'Phrase TMS\', not \'our platform\')\n'
         '- GitHub Sync is the killer feature for engineering signals\n'
         '- DO mention: automation, API, GitHub integration, CI/CD\n'
         '- DO NOT mention: \'high quality translations\', \'professional linguists\', \'AI-powered\''),
    ]

    for key, value in defaults:
        try:
            cursor.execute(
                "INSERT INTO writing_preferences (preference_key, preference_value) VALUES (?, ?)",
                (key, value),
            )
        except Exception:
            pass  # Already exists (race condition guard)
