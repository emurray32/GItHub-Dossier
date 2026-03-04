"""
Apollo Pipeline — Automated Contact Discovery & Bulk Enrollment.

Provides:
    - ApolloRateLimiter: Thread-safe token-bucket rate limiter (50 req/min)
    - apollo_api_call(): Rate-limited wrapper for all Apollo API requests
    - auto_discover_contacts(): People Search for monitored accounts
    - select_sequence(): Map tier + persona + signal type -> sequence
    - bulk_enroll_contacts(): Full enrollment pipeline with audit trail
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy imports — avoid circular imports with app.py / database.py
# ---------------------------------------------------------------------------

def _db():
    import database
    return database


# ---------------------------------------------------------------------------
# Thread-safe Apollo Rate Limiter (token-bucket, 50 requests / 60 seconds)
# ---------------------------------------------------------------------------

class ApolloRateLimiter:
    """Thread-safe token-bucket rate limiter for Apollo API (50 req/min)."""

    def __init__(self, max_tokens=50, refill_period=60.0):
        self._max_tokens = max_tokens
        self._refill_period = refill_period
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        added = elapsed * (self._max_tokens / self._refill_period)
        self._tokens = min(self._max_tokens, self._tokens + added)
        self._last_refill = now

    def acquire(self, timeout=120.0):
        """Block until a token is available. Returns True, or False on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    @property
    def available_tokens(self):
        with self._lock:
            self._refill()
            return int(self._tokens)


# Global singleton
rate_limiter = ApolloRateLimiter(max_tokens=50, refill_period=60.0)


from email_utils import _filter_personal_email, _derive_company_domain, _check_company_match


# ---------------------------------------------------------------------------
# apollo_api_call — rate-limited wrapper
# ---------------------------------------------------------------------------

def apollo_api_call(method, url, **kwargs):
    """Rate-limited Apollo API call. Blocks until a token is available.

    Args:
        method: 'get' or 'post'
        url: Apollo API endpoint
        **kwargs: passed to requests.get/post (json, headers, timeout, etc.)

    Returns:
        requests.Response object

    Raises:
        RuntimeError if rate limit timeout exceeded or API key missing.
    """
    import requests as req

    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        raise RuntimeError('Apollo API key not configured (APOLLO_API_KEY)')

    headers = kwargs.pop('headers', {})
    headers.setdefault('X-Api-Key', apollo_key)
    headers.setdefault('Content-Type', 'application/json')
    kwargs['headers'] = headers
    kwargs.setdefault('timeout', 15)

    if not rate_limiter.acquire(timeout=120):
        raise RuntimeError('Apollo rate limit timeout — too many requests queued')

    if method.lower() == 'get':
        return req.get(url, **kwargs)
    return req.post(url, **kwargs)


# ---------------------------------------------------------------------------
# auto_discover_contacts
# ---------------------------------------------------------------------------

def auto_discover_contacts(account_id, batch_id=None, personas=None,
                           existing_emails=None, verified_emails_only=False,
                           contact_cap=None):
    """Discover contacts for a monitored account via Apollo People Search.

    Args:
        account_id: monitored_accounts.id
        batch_id: optional enrollment_batches.id to link contacts to
        personas: list of persona dicts with titles_json, seniorities_json, etc.
                  If None, looks up campaign_personas for active campaigns.
        existing_emails: optional set of lowercase emails for dedup. If provided,
                         skips the DB query to load enrollment emails.
        verified_emails_only: if True, only return contacts with verified emails
        contact_cap: max contacts to return per account (default unlimited)

    Returns:
        dict with 'contacts' (list of dicts), 'total', 'new', 'skipped_dedup'
    """
    db = _db()
    account = db.get_account(account_id)
    if not account:
        return {'error': f'Account {account_id} not found', 'contacts': [],
                'total': 0, 'new': 0, 'skipped_dedup': 0}

    company_name = account.get('company_name', '')
    domain = account.get('website', '')
    if domain:
        domain = re.sub(r'^https?://', '', domain).rstrip('/')
    if not domain:
        domain = _derive_company_domain(company_name)

    # Resolve personas
    if not personas:
        with db.db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT cp.* FROM campaign_personas cp
                JOIN campaigns c ON c.id = cp.campaign_id
                WHERE c.status = 'active'
                ORDER BY cp.priority ASC
            ''')
            personas = [dict(r) for r in cursor.fetchall()]

    if not personas:
        return {'error': 'No campaign personas configured', 'contacts': [],
                'total': 0, 'new': 0, 'skipped_dedup': 0}

    # Collect existing emails for dedup (skip DB query if caller provided the set)
    if existing_emails is None:
        with db.db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT email FROM enrollment_contacts WHERE email IS NOT NULL AND email != ?',
                ('',)
            )
            existing_emails = {r['email'].lower() for r in cursor.fetchall() if r['email']}

    all_contacts = []
    skipped_dedup = 0

    for persona in personas:
        titles = persona.get('titles_json', '[]')
        if isinstance(titles, str):
            titles = json.loads(titles)
        seniorities = persona.get('seniorities_json', '[]')
        if isinstance(seniorities, str):
            seniorities = json.loads(seniorities)

        if not titles:
            continue

        search_payload = {
            'person_titles': titles,
            'q_organization_domains': domain,
            'per_page': 25,
            'page': 1,
        }
        if seniorities:
            search_payload['person_seniorities'] = seniorities
        if verified_emails_only:
            search_payload['email_status'] = ['verified']

        try:
            resp = apollo_api_call('post',
                                   'https://api.apollo.io/v1/mixed_people/search',
                                   json=search_payload)
            if resp.status_code != 200:
                logger.warning(
                    f"[PIPELINE DISCOVER] Apollo search failed ({resp.status_code}) "
                    f"for {company_name}, persona {persona.get('persona_name')}"
                )
                continue

            people = resp.json().get('people', [])
            for person in people:
                email = _filter_personal_email(person.get('email') or '')
                if not email:
                    continue

                if email.lower() in existing_emails:
                    skipped_dedup += 1
                    continue

                if not _check_company_match(email, company_name):
                    logger.info(
                        f"[PIPELINE DISCOVER] Skipping {email} — "
                        f"domain mismatch for {company_name}"
                    )
                    continue

                contact = {
                    'account_id': account_id,
                    'company_name': company_name,
                    'company_domain': domain,
                    'persona_name': persona.get('persona_name', ''),
                    'sequence_id': persona.get('sequence_id', ''),
                    'sequence_name': persona.get('sequence_name', ''),
                    'apollo_person_id': person.get('id', ''),
                    'first_name': person.get('first_name', ''),
                    'last_name': person.get('last_name', ''),
                    'email': email,
                    'title': person.get('title', ''),
                    'seniority': person.get('seniority', ''),
                    'linkedin_url': person.get('linkedin_url', ''),
                    'status': 'discovered',
                }
                if batch_id:
                    contact['batch_id'] = batch_id

                all_contacts.append(contact)
                existing_emails.add(email.lower())

        except Exception as e:
            logger.error(
                f"[PIPELINE DISCOVER] Error searching for {company_name}, "
                f"persona {persona.get('persona_name')}: {e}"
            )
            continue

    # Enforce contact cap per account (EC-009: sort by seniority before truncating)
    capped = 0
    if contact_cap and len(all_contacts) > contact_cap:
        seniority_rank = {'c_suite': 0, 'vp': 1, 'director': 2, 'manager': 3, 'senior': 4}
        all_contacts.sort(key=lambda c: seniority_rank.get(c.get('seniority', ''), 9))
        capped = len(all_contacts) - contact_cap
        all_contacts = all_contacts[:contact_cap]

    # Persist discovered contacts
    if batch_id and all_contacts:
        db.bulk_create_enrollment_contacts(all_contacts)
        db.update_enrollment_batch(batch_id, discovered=len(all_contacts))

    return {
        'contacts': all_contacts,
        'total': len(all_contacts) + skipped_dedup + capped,
        'new': len(all_contacts),
        'skipped_dedup': skipped_dedup,
        'capped': capped,
    }


# ---------------------------------------------------------------------------
# select_sequence — Map tier + persona + signal type to correct sequence
# ---------------------------------------------------------------------------

def select_sequence(tier, persona_name, signal_type=None):
    """Select the correct Apollo sequence ID for a given tier + persona + signal.

    Strategy:
    1. Direct persona->sequence mapping from campaign_personas.
    2. Signal-type keyword match in sequence_mappings.
    3. Fall back to first enabled sequence_mapping.

    Args:
        tier: int (1-4)
        persona_name: str (e.g. 'VP Engineering', 'Head of Product')
        signal_type: optional str (e.g. 'i18n_library', 'translation_api')

    Returns:
        dict with 'sequence_id', 'sequence_name' or None
    """
    db = _db()
    with db.db_connection() as conn:
        cursor = conn.cursor()

        # Strategy 1: Direct persona->sequence mapping
        cursor.execute('''
            SELECT cp.sequence_id, cp.sequence_name
            FROM campaign_personas cp
            JOIN campaigns c ON c.id = cp.campaign_id
            WHERE c.status = 'active'
              AND cp.persona_name = ?
              AND cp.sequence_id IS NOT NULL
              AND cp.sequence_id != ''
            ORDER BY cp.priority ASC
            LIMIT 1
        ''', (persona_name,))
        row = cursor.fetchone()
        if row and row['sequence_id']:
            return {'sequence_id': row['sequence_id'],
                    'sequence_name': row['sequence_name'] or ''}

        # Strategy 2: Signal-type keyword match
        if signal_type:
            keyword = signal_type.replace('_', ' ').lower()
            cursor.execute('''
                SELECT sequence_id, sequence_name FROM sequence_mappings
                WHERE enabled = 1 AND LOWER(sequence_name) LIKE ?
                ORDER BY sequence_name ASC
                LIMIT 1
            ''', (f'%{keyword}%',))
            row = cursor.fetchone()
            if row:
                return {'sequence_id': row['sequence_id'],
                        'sequence_name': row['sequence_name']}

        # Strategy 3: First enabled sequence
        cursor.execute('''
            SELECT sequence_id, sequence_name FROM sequence_mappings
            WHERE enabled = 1
            ORDER BY sequence_name ASC
            LIMIT 1
        ''')
        row = cursor.fetchone()
        if row:
            return {'sequence_id': row['sequence_id'],
                    'sequence_name': row['sequence_name']}

    return None


# ---------------------------------------------------------------------------
# bulk_enroll_contacts — Full pipeline
# ---------------------------------------------------------------------------

def bulk_enroll_contacts(batch_id, contact_ids=None, limit=25):
    """Process contacts through the full enrollment pipeline.

    Steps per contact:
    1. Validate email (not personal, matches company domain)
    2. Search/create contact in Apollo
    3. Inject personalized custom fields
    4. Enroll in sequence
    5. Update enrollment_contacts status at each step

    Args:
        batch_id: enrollment_batches.id
        contact_ids: optional list of enrollment_contacts.id to process
        limit: max contacts to process in this call

    Returns:
        dict with enrolled, failed, skipped counts
    """
    db = _db()
    apollo_key = os.environ.get('APOLLO_API_KEY', '')
    if not apollo_key:
        return {'error': 'Apollo API key not configured',
                'enrolled': 0, 'failed': 0, 'skipped': 0}

    # Get contacts to process
    if contact_ids:
        with db.db_connection() as conn:
            cursor = conn.cursor()
            placeholders = ', '.join('?' * len(contact_ids))
            cursor.execute(
                f'SELECT * FROM enrollment_contacts '
                f'WHERE id IN ({placeholders}) AND batch_id = ?',
                contact_ids + [batch_id]
            )
            contacts = [dict(r) for r in cursor.fetchall()]
    else:
        contacts = db.get_next_contacts_for_phase(batch_id, 'generated', limit=limit)
        if not contacts:
            contacts = db.get_next_contacts_for_phase(batch_id, 'discovered', limit=limit)

    if not contacts:
        return {'enrolled': 0, 'failed': 0, 'skipped': 0,
                'message': 'No contacts ready for enrollment'}

    db.update_enrollment_batch(batch_id, status='in_progress', current_phase='enrolling')

    # Resolve sending email account — prefer per-sequence override, fall back to global
    email_account_id = None
    if contacts:
        seq_id = contacts[0].get('sequence_id', '')
        if seq_id:
            with db.db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT owner_email_account_id FROM sequence_mappings WHERE sequence_id = ?',
                    (seq_id,))
                row = cursor.fetchone()
                if row and row['owner_email_account_id']:
                    email_account_id = row['owner_email_account_id']
    if not email_account_id:
        email_account_id = _resolve_email_account()
    if not email_account_id:
        db.update_enrollment_batch(batch_id, current_phase='error',
                                   error_message='No active Apollo email account found')
        return {'error': 'No active Apollo email account',
                'enrolled': 0, 'failed': 0, 'skipped': 0}

    # Resolve custom field IDs (once per batch)
    field_id_map = _resolve_custom_field_ids()

    enrolled = 0
    failed = 0
    skipped = 0

    for contact in contacts:
        contact_db_id = contact['id']
        email = (contact.get('email') or '').strip()
        company_name = contact.get('company_name', '')

        # Validate
        if not email:
            db.update_enrollment_contact(contact_db_id, status='skipped',
                                         error_message='No email address')
            skipped += 1
            continue

        if not _filter_personal_email(email):
            db.update_enrollment_contact(contact_db_id, status='skipped',
                                         error_message='Personal email domain')
            skipped += 1
            continue

        if not _check_company_match(email, company_name):
            db.update_enrollment_contact(contact_db_id, status='skipped',
                                         error_message='Email domain does not match company')
            skipped += 1
            continue

        # Determine sequence
        sequence_id = contact.get('sequence_id', '')
        if not sequence_id:
            seq = select_sequence(
                tier=0,
                persona_name=contact.get('persona_name', ''),
                signal_type=None
            )
            if seq:
                sequence_id = seq['sequence_id']
                db.update_enrollment_contact(
                    contact_db_id,
                    sequence_id=seq['sequence_id'],
                    sequence_name=seq.get('sequence_name', '')
                )

        if not sequence_id:
            db.update_enrollment_contact(contact_db_id, status='failed',
                                         error_message='No sequence assigned')
            failed += 1
            continue

        try:
            result = _enroll_single_contact(
                contact, contact_db_id, email, company_name,
                sequence_id, email_account_id, field_id_map
            )
            if result == 'enrolled':
                enrolled += 1
            elif result == 'failed':
                failed += 1
        except Exception as e:
            db.update_enrollment_contact(contact_db_id, status='failed',
                                         error_message=str(e)[:500])
            failed += 1
            logger.error(f"[PIPELINE ENROLL] Error enrolling {email}: {e}")

    # Update batch counters
    db.update_enrollment_batch(batch_id, enrolled=enrolled, failed=failed, skipped=skipped)

    # Check if batch is complete
    summary = db.get_enrollment_batch_summary(batch_id)
    remaining = summary.get('discovered', 0) + summary.get('generated', 0)
    if remaining == 0:
        db.update_enrollment_batch(
            batch_id, status='completed', current_phase='done',
            completed_at=datetime.utcnow().isoformat()
        )

    return {'enrolled': enrolled, 'failed': failed, 'skipped': skipped}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_email_account():
    """Resolve the Apollo sending email account ID."""
    preferred_sender = os.environ.get('APOLLO_SENDER_EMAIL', '').strip().lower()
    try:
        ea_resp = apollo_api_call('get', 'https://api.apollo.io/api/v1/email_accounts')
        if ea_resp.status_code == 200:
            accounts = ea_resp.json().get('email_accounts', [])
            active = [a for a in accounts if a.get('active')]
            if preferred_sender:
                match = next(
                    (a for a in active if a.get('email', '').lower() == preferred_sender),
                    None
                )
                return match['id'] if match else (active[0]['id'] if active else None)
            elif active:
                return active[0]['id']
    except Exception as e:
        logger.warning(f"[PIPELINE ENROLL] Could not fetch email accounts: {e}")
    return None


def _resolve_custom_field_ids():
    """Fetch Apollo custom field ID mapping."""
    field_id_map = {}
    try:
        cf_resp = apollo_api_call('get', 'https://api.apollo.io/v1/typed_custom_fields')
        if cf_resp.status_code == 200:
            for f in cf_resp.json().get('typed_custom_fields', []):
                fid = f.get('id')
                name = (f.get('name') or '').lower().replace(' ', '_')
                if fid and name:
                    field_id_map[name] = fid
    except Exception as e:
        logger.warning(f"[PIPELINE ENROLL] Could not fetch custom fields: {e}")
    return field_id_map


def _enroll_single_contact(contact, contact_db_id, email, company_name,
                           sequence_id, email_account_id, field_id_map):
    """Enroll a single contact into Apollo sequence. Returns 'enrolled' or 'failed'."""
    db = _db()

    # Search for existing Apollo contact
    apollo_contact_id = None
    search_resp = apollo_api_call(
        'post', 'https://api.apollo.io/api/v1/contacts/search',
        json={'q_keywords': email, 'per_page': 1}
    )
    if search_resp.status_code == 200:
        found = search_resp.json().get('contacts', [])
        if found:
            apollo_contact_id = found[0].get('id')

    # Build custom fields from generated emails
    typed_custom_fields = {}
    try:
        gen_emails = json.loads(contact.get('generated_emails_json') or '{}')
        if isinstance(gen_emails, dict):
            for field_key, field_val in gen_emails.items():
                if field_key in field_id_map and field_val:
                    typed_custom_fields[field_id_map[field_key]] = field_val
    except (json.JSONDecodeError, TypeError):
        pass

    # Create or update Apollo contact
    if not apollo_contact_id:
        create_payload = {
            'first_name': contact.get('first_name') or email.split('@')[0],
            'last_name': contact.get('last_name', ''),
            'email': email,
            'organization_name': company_name,
        }
        if typed_custom_fields:
            create_payload['typed_custom_fields'] = typed_custom_fields

        create_resp = apollo_api_call('post', 'https://api.apollo.io/v1/contacts',
                                     json=create_payload)
        if create_resp.status_code in (200, 201):
            apollo_contact_id = create_resp.json().get('contact', {}).get('id')
        else:
            err = create_resp.text[:200]
            db.update_enrollment_contact(contact_db_id, status='failed',
                                         error_message=f'Apollo create failed: {err}')
            return 'failed'
    elif typed_custom_fields:
        apollo_api_call(
            'post', f'https://api.apollo.io/v1/contacts/{apollo_contact_id}',
            json={'typed_custom_fields': typed_custom_fields}
        )

    if not apollo_contact_id:
        db.update_enrollment_contact(contact_db_id, status='failed',
                                     error_message='Could not create or find Apollo contact')
        return 'failed'

    db.update_enrollment_contact(contact_db_id, apollo_contact_id=apollo_contact_id)

    # Enroll in sequence
    enroll_resp = apollo_api_call(
        'post',
        f'https://api.apollo.io/api/v1/emailer_campaigns/{sequence_id}/add_contact_ids',
        json={
            'emailer_campaign_id': sequence_id,
            'contact_ids': [apollo_contact_id],
            'send_email_from_email_account_id': email_account_id,
        }
    )

    if enroll_resp.status_code in (200, 201):
        db.update_enrollment_contact(
            contact_db_id, status='enrolled',
            enrolled_at=datetime.utcnow().isoformat()
        )
        logger.info(f"[PIPELINE ENROLL] Enrolled {email} in sequence {sequence_id}")
        return 'enrolled'
    else:
        err = enroll_resp.text[:200]
        db.update_enrollment_contact(
            contact_db_id, status='failed',
            error_message=f'Enrollment failed: {err}'
        )
        return 'failed'
