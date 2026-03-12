"""
Ingestion Service — turns raw data (CSV files, manual entries, scan signals)
into intent signals in the v2 pipeline.

Every ingestion path follows the same pattern:
  1. Find or create the account
  2. Auto-recommend a campaign
  3. Create the intent signal
  4. Log the activity

Errors are captured per-row so one bad record never crashes the batch.
"""
import csv
import io
import logging
import uuid
from typing import Optional

from v2.db import db_connection, row_to_dict, rows_to_dicts, safe_json_dumps
from v2.services import activity_service
from v2.services import campaign_service
from v2.services import signal_service
from v2.services import account_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CSV Ingestion
# ---------------------------------------------------------------------------

def ingest_csv(
    file_content: bytes,
    source_label: str = 'csv_upload',
    created_by: Optional[str] = None,
) -> dict:
    """Parse a CSV and create one intent signal per valid row.

    Required columns: company_name, signal_description
    Optional columns: website, signal_type, evidence, industry,
                      company_size, annual_revenue, account_owner

    Returns:
        dict with keys: signals_created, accounts_created, accounts_matched,
                        errors (list of strings), batch_id
    """
    batch_id = uuid.uuid4().hex[:12]
    result = {
        'signals_created': 0,
        'accounts_created': 0,
        'accounts_matched': 0,
        'errors': [],
        'batch_id': batch_id,
    }

    # --- Decode ---
    try:
        text = file_content.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = file_content.decode('latin-1')
        except UnicodeDecodeError:
            result['errors'].append('File encoding not supported (use UTF-8)')
            return result

    # --- Parse CSV ---
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    headers_lower = [h.lower().strip() for h in headers]

    # Map flexible header names to canonical keys
    company_col = _find_column(headers, headers_lower,
                               ('company_name', 'company', 'name', 'account_name'))
    signal_col = _find_column(headers, headers_lower,
                              ('signal_description', 'signal', 'description'))

    if not company_col:
        result['errors'].append(
            'CSV must have a company_name column '
            '(also accepts: company, name, account_name)')
        return result

    if not signal_col:
        result['errors'].append(
            'CSV must have a signal_description column '
            '(also accepts: signal, description)')
        return result

    # Optional columns
    website_col = _find_column(headers, headers_lower,
                               ('website', 'domain', 'website_url', 'url'))
    signal_type_col = _find_column(headers, headers_lower,
                                   ('signal_type', 'type'))
    evidence_col = _find_column(headers, headers_lower,
                                ('evidence', 'evidence_value'))
    industry_col = _find_column(headers, headers_lower,
                                ('industry',))
    size_col = _find_column(headers, headers_lower,
                            ('company_size', 'size', 'employees'))
    revenue_col = _find_column(headers, headers_lower,
                               ('annual_revenue', 'revenue'))
    owner_col = _find_column(headers, headers_lower,
                             ('account_owner', 'owner'))

    # --- Process rows ---
    for row_num, row in enumerate(reader, start=2):
        try:
            company_name = (row.get(company_col) or '').strip()
            signal_desc = (row.get(signal_col) or '').strip()

            if not company_name:
                result['errors'].append(f'Row {row_num}: missing company_name')
                continue
            if not signal_desc:
                result['errors'].append(f'Row {row_num}: missing signal_description')
                continue

            # Extract optional fields
            website = (row.get(website_col) or '').strip() if website_col else None
            signal_type = (row.get(signal_type_col) or '').strip() if signal_type_col else None
            evidence = (row.get(evidence_col) or '').strip() if evidence_col else None
            industry = (row.get(industry_col) or '').strip() if industry_col else None
            company_size = (row.get(size_col) or '').strip() if size_col else None
            annual_revenue = (row.get(revenue_col) or '').strip() if revenue_col else None
            account_owner = (row.get(owner_col) or '').strip() if owner_col else None

            # 1. Find or create account
            existing = account_service.find_account_by_name(company_name)
            if existing:
                account_id = existing['id']
                result['accounts_matched'] += 1
            else:
                account_id = account_service.find_or_create_account(
                    company_name=company_name,
                    website=website or None,
                    industry=industry or None,
                    company_size=company_size or None,
                    annual_revenue=annual_revenue or None,
                    account_owner=account_owner or None,
                )
                result['accounts_created'] += 1

            # 2. Auto-recommend campaign
            rec = campaign_service.recommend_campaign(
                signal_type=signal_type or None,
            )

            # 3. Create intent signal
            signal_id = signal_service.create_signal(
                account_id=account_id,
                signal_description=signal_desc,
                signal_type=signal_type or None,
                evidence_type='csv_import',
                evidence_value=evidence or None,
                signal_source='csv_upload',
                recommended_campaign_id=rec.get('campaign_id'),
                recommended_campaign_reasoning=rec.get('reasoning'),
                created_by=created_by,
                ingestion_batch_id=batch_id,
                raw_payload=safe_json_dumps(dict(row)),
            )

            result['signals_created'] += 1

            # 4. Log activity
            activity_service.log_activity(
                event_type='signal_created',
                entity_type='signal',
                entity_id=signal_id,
                details={
                    'source': 'csv_upload',
                    'batch_id': batch_id,
                    'company_name': company_name,
                    'signal_type': signal_type,
                },
                created_by=created_by,
            )

        except Exception as exc:
            logger.exception("[INGEST] Error on CSV row %d", row_num)
            result['errors'].append(f'Row {row_num}: {str(exc)[:200]}')

    # Log batch-level activity
    activity_service.log_activity(
        event_type='csv_imported',
        entity_type='batch',
        entity_id=None,
        details={
            'batch_id': batch_id,
            'source_label': source_label,
            'signals_created': result['signals_created'],
            'accounts_created': result['accounts_created'],
            'accounts_matched': result['accounts_matched'],
            'error_count': len(result['errors']),
        },
        created_by=created_by,
    )

    logger.info(
        "[INGEST] CSV batch %s complete: %d signals, %d new accounts, %d matched, %d errors",
        batch_id, result['signals_created'], result['accounts_created'],
        result['accounts_matched'], len(result['errors']),
    )

    return result


# ---------------------------------------------------------------------------
# Manual / Single-Signal Ingestion
# ---------------------------------------------------------------------------

def ingest_manual(
    account_id: int,
    signal_description: str,
    signal_type: Optional[str] = None,
    evidence_value: Optional[str] = None,
    created_by: Optional[str] = None,
) -> int:
    """Create a single intent signal for an existing account.

    Returns:
        The new signal id.
    """
    # Auto-recommend campaign
    rec = campaign_service.recommend_campaign(signal_type=signal_type)

    signal_id = signal_service.create_signal(
        account_id=account_id,
        signal_description=signal_description,
        signal_type=signal_type,
        evidence_type='manual',
        evidence_value=evidence_value,
        signal_source='manual_entry',
        recommended_campaign_id=rec.get('campaign_id'),
        recommended_campaign_reasoning=rec.get('reasoning'),
        created_by=created_by,
    )

    activity_service.log_activity(
        event_type='signal_created',
        entity_type='signal',
        entity_id=signal_id,
        details={
            'source': 'manual_entry',
            'signal_type': signal_type,
            'account_id': account_id,
        },
        created_by=created_by,
    )

    return signal_id


# ---------------------------------------------------------------------------
# Scan-Signal Conversion
# ---------------------------------------------------------------------------

def create_signal_from_scan(account_id: int, scan_signal_id: int) -> Optional[int]:
    """Convert a single scan_signal row into an intent signal.

    Reads the scan_signal from the legacy scan_signals table, checks for
    duplicates, auto-recommends a campaign, and creates the intent signal.

    Returns:
        The new signal id, or None if it was a duplicate.
    """
    # Read the scan_signal
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, report_id, company_name, signal_type, description,
                   file_path, timestamp
            FROM scan_signals
            WHERE id = ?
        ''', (scan_signal_id,))
        scan_row = row_to_dict(cursor.fetchone())

    if not scan_row:
        logger.warning("[INGEST] scan_signal %d not found", scan_signal_id)
        return None

    signal_type = scan_row.get('signal_type')
    signal_source = 'github_scan'

    # Check for duplicate (include evidence to avoid dropping valid repeats)
    evidence_val = scan_row.get('file_path')
    if signal_service.check_duplicate_signal(account_id, signal_type, signal_source, evidence_value=evidence_val):
        logger.info("[INGEST] Duplicate signal skipped: account=%d type=%s source=%s evidence=%s",
                    account_id, signal_type, signal_source, evidence_val)
        return None

    # Auto-recommend campaign
    rec = campaign_service.recommend_campaign(signal_type=signal_type)

    signal_id = signal_service.create_signal(
        account_id=account_id,
        signal_description=scan_row.get('description') or f"{signal_type} signal detected",
        signal_type=signal_type,
        evidence_type='scan_signal',
        evidence_value=scan_row.get('file_path'),
        signal_source=signal_source,
        recommended_campaign_id=rec.get('campaign_id'),
        recommended_campaign_reasoning=rec.get('reasoning'),
        scan_signal_id=scan_signal_id,
    )

    activity_service.log_activity(
        event_type='signal_created',
        entity_type='signal',
        entity_id=signal_id,
        details={
            'source': 'scan_signal',
            'scan_signal_id': scan_signal_id,
            'signal_type': signal_type,
            'account_id': account_id,
        },
    )

    return signal_id


def batch_import_from_scans(tier_filter: Optional[list] = None) -> dict:
    """Bulk-convert scan_signals into intent signals for all accounts.

    Args:
        tier_filter: optional list of tier ints to limit which accounts
            are processed (e.g. [1, 2] for top-tier only).

    Returns:
        dict with keys: signals_created, accounts_processed.
    """
    result = {
        'signals_created': 0,
        'accounts_processed': 0,
    }

    # Get accounts
    with db_connection() as conn:
        cursor = conn.cursor()

        if tier_filter:
            placeholders = ', '.join(['?'] * len(tier_filter))
            cursor.execute(f'''
                SELECT id, company_name FROM monitored_accounts
                WHERE archived_at IS NULL
                  AND current_tier IN ({placeholders})
                ORDER BY current_tier ASC
            ''', tuple(tier_filter))
        else:
            cursor.execute('''
                SELECT id, company_name FROM monitored_accounts
                WHERE archived_at IS NULL
                ORDER BY current_tier ASC
            ''')

        accounts = rows_to_dicts(cursor.fetchall())

    for acct in accounts:
        account_id = acct['id']
        company_name = acct['company_name']

        # Get scan_signals for this account
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, signal_type, description, file_path, timestamp
                FROM scan_signals
                WHERE company_name = ?
                ORDER BY timestamp DESC
            ''', (company_name,))
            scan_signals = rows_to_dicts(cursor.fetchall())

        if not scan_signals:
            continue

        result['accounts_processed'] += 1

        for ss in scan_signals:
            try:
                signal_id = create_signal_from_scan(account_id, ss['id'])
                if signal_id:
                    result['signals_created'] += 1
            except Exception:
                logger.exception(
                    "[INGEST] Error converting scan_signal %d for account %d",
                    ss['id'], account_id,
                )

    logger.info(
        "[INGEST] Batch scan import complete: %d signals created across %d accounts",
        result['signals_created'], result['accounts_processed'],
    )

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_column(headers, headers_lower, candidates):
    """Find the first matching header from a tuple of candidate names.

    Returns the original-case header name, or None if no match.
    """
    for candidate in candidates:
        for orig, low in zip(headers, headers_lower):
            if low == candidate:
                return orig
    return None
