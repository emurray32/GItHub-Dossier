"""
Google Sheets Client for Lead Machine.

Reads accounts from a Coefficient-synced Google Sheet and feeds them
into the Lead Machine scanning pipeline.

Setup:
    1. Create a Google Cloud project at https://console.cloud.google.com
    2. Enable the Google Sheets API
    3. Create a Service Account and download the JSON key file
    4. Share your Google Sheet with the service account email
    5. Set GOOGLE_SHEETS_CREDENTIALS_FILE in .env (path to JSON key)
    6. Set GOOGLE_SHEETS_SPREADSHEET_ID in .env (from the sheet URL)

The spreadsheet ID is the long string in the Google Sheets URL:
    https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit
"""

import os
import json
from datetime import datetime
from typing import Optional

# Google API imports (installed via google-api-python-client)
try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_SHEETS_AVAILABLE = True
except ImportError:
    GOOGLE_SHEETS_AVAILABLE = False

# Scopes needed: read/write to sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Column name mappings - maps various CSV/Sheet column names to our internal fields
# This handles Apollo exports, Salesforce exports, Coefficient syncs, etc.
COLUMN_MAPPINGS = {
    'company_name': ['company_name', 'company name', 'company', 'account name',
                     'account_name', 'organization', 'org name', 'org_name', 'name'],
    'domain': ['domain', 'website', 'company domain', 'company_domain',
               'website url', 'website_url', 'url', 'web', 'company website',
               'company_website', 'primary domain'],
    'industry': ['industry', 'sector', 'vertical', 'company industry',
                 'company_industry', 'account industry'],
    'employees': ['employees', 'employee count', 'employee_count',
                  '# employees', 'num_employees', 'company size',
                  'number of employees', 'headcount'],
    'salesforce_id': ['salesforce_id', 'sf_id', 'account id', 'account_id',
                      'sfdc id', 'sfdc_id', 'crm_id', 'crm id'],
    'city': ['city', 'hq city', 'headquarters city', 'company city'],
    'state': ['state', 'hq state', 'headquarters state', 'company state', 'region'],
    'country': ['country', 'hq country', 'headquarters country', 'company country'],
}

# Status column name - Lead Machine writes back to this column
STATUS_COLUMN_NAME = 'lead_machine_status'
PROCESSED_DATE_COLUMN_NAME = 'lead_machine_processed_at'


def is_sheets_configured() -> bool:
    """Check if Google Sheets integration is properly configured."""
    if not GOOGLE_SHEETS_AVAILABLE:
        return False

    creds_file = os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE', '')
    spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID', '')

    # Also support credentials as JSON string (for Replit secrets)
    creds_json = os.getenv('GOOGLE_SHEETS_CREDENTIALS_JSON', '')

    has_creds = bool(creds_file and os.path.exists(creds_file)) or bool(creds_json)
    has_sheet = bool(spreadsheet_id)

    return has_creds and has_sheet


def get_sheets_service():
    """
    Create and return an authenticated Google Sheets API service.

    Supports two auth methods:
    1. GOOGLE_SHEETS_CREDENTIALS_FILE - path to service account JSON
    2. GOOGLE_SHEETS_CREDENTIALS_JSON - JSON string (for Replit/cloud)

    Returns:
        Google Sheets API service object, or None if not configured.
    """
    if not GOOGLE_SHEETS_AVAILABLE:
        print("[SHEETS] google-api-python-client not installed")
        return None

    creds = None

    # Method 1: JSON file path
    creds_file = os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE', '')
    if creds_file and os.path.exists(creds_file):
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)

    # Method 2: JSON string (Replit secrets)
    if not creds:
        creds_json = os.getenv('GOOGLE_SHEETS_CREDENTIALS_JSON', '')
        if creds_json:
            try:
                creds_info = json.loads(creds_json)
                creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
            except json.JSONDecodeError as e:
                print(f"[SHEETS] Invalid GOOGLE_SHEETS_CREDENTIALS_JSON: {e}")
                return None

    if not creds:
        print("[SHEETS] No valid credentials found")
        return None

    try:
        service = build('sheets', 'v4', credentials=creds)
        return service
    except Exception as e:
        print(f"[SHEETS] Failed to build Sheets service: {e}")
        return None


def _map_headers(raw_headers: list) -> dict:
    """
    Map raw spreadsheet headers to our internal field names.

    Args:
        raw_headers: List of header strings from the first row.

    Returns:
        Dict mapping internal field name -> column index.
        e.g., {'company_name': 0, 'domain': 3, 'industry': 5}
    """
    header_map = {}
    normalized_headers = [h.strip().lower() for h in raw_headers]

    for field_name, aliases in COLUMN_MAPPINGS.items():
        for alias in aliases:
            if alias in normalized_headers:
                header_map[field_name] = normalized_headers.index(alias)
                break

    return header_map


def _find_status_column(raw_headers: list) -> Optional[int]:
    """Find the lead_machine_status column index, or None if it doesn't exist."""
    normalized = [h.strip().lower() for h in raw_headers]
    if STATUS_COLUMN_NAME in normalized:
        return normalized.index(STATUS_COLUMN_NAME)
    return None


def _find_processed_date_column(raw_headers: list) -> Optional[int]:
    """Find the lead_machine_processed_at column index."""
    normalized = [h.strip().lower() for h in raw_headers]
    if PROCESSED_DATE_COLUMN_NAME in normalized:
        return normalized.index(PROCESSED_DATE_COLUMN_NAME)
    return None


def _col_letter(index: int) -> str:
    """Convert a 0-based column index to a column letter (A, B, ... Z, AA, AB, ...)."""
    result = ''
    while index >= 0:
        result = chr(index % 26 + ord('A')) + result
        index = index // 26 - 1
    return result


def read_sheet_accounts(
    sheet_name: str = 'Sheet1',
    limit: int = 300,
    only_unprocessed: bool = True
) -> dict:
    """
    Read accounts from the Google Sheet.

    Args:
        sheet_name: Name of the sheet/tab to read from (default 'Sheet1').
        limit: Maximum number of accounts to return (default 300).
        only_unprocessed: If True, skip rows where lead_machine_status is set.

    Returns:
        Dictionary with:
            - accounts: List of account dicts with mapped fields.
            - total_rows: Total rows in the sheet (excluding header).
            - unprocessed_rows: Number of rows without a status.
            - headers: Raw header names.
            - header_map: Mapped field -> column index.
            - errors: List of any errors encountered.
    """
    result = {
        'accounts': [],
        'total_rows': 0,
        'unprocessed_rows': 0,
        'headers': [],
        'header_map': {},
        'errors': []
    }

    spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID', '')
    if not spreadsheet_id:
        result['errors'].append('GOOGLE_SHEETS_SPREADSHEET_ID not set')
        return result

    service = get_sheets_service()
    if not service:
        result['errors'].append('Google Sheets service not available. Check credentials.')
        return result

    try:
        # Read all data from the sheet
        range_name = f'{sheet_name}!A2:ZZ'
        response = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()

        values = response.get('values', [])
        if not values or len(values) < 2:
            result['errors'].append('Sheet is empty or has no data rows')
            return result

        # First row is headers
        raw_headers = values[0]
        result['headers'] = raw_headers

        # Map headers to our fields
        header_map = _map_headers(raw_headers)
        result['header_map'] = header_map

        if 'company_name' not in header_map:
            result['errors'].append(
                f'No company name column found. Headers: {raw_headers}. '
                f'Expected one of: {COLUMN_MAPPINGS["company_name"]}'
            )
            return result

        # Find status columns
        status_col = _find_status_column(raw_headers)
        processed_date_col = _find_processed_date_column(raw_headers)

        # Process data rows
        data_rows = values[1:]
        result['total_rows'] = len(data_rows)

        accounts = []
        unprocessed_count = 0

        for row_idx, row in enumerate(data_rows):
            # Pad row if shorter than headers
            while len(row) < len(raw_headers):
                row.append('')

            # Check if already processed
            is_processed = False
            if status_col is not None and status_col < len(row):
                status_value = row[status_col].strip()
                if status_value:
                    is_processed = True

            if not is_processed:
                unprocessed_count += 1

            if only_unprocessed and is_processed:
                continue

            # Extract mapped fields
            account = {
                '_row_index': row_idx + 3,  # 1-indexed, +1 for header, +1 for banner
                '_raw_row': row,
            }

            for field_name, col_idx in header_map.items():
                if col_idx < len(row):
                    value = row[col_idx].strip()
                    account[field_name] = value
                else:
                    account[field_name] = ''

            # Skip rows with no company name
            if not account.get('company_name'):
                continue

            # Clean up domain (remove http://, trailing slashes, etc.)
            if account.get('domain'):
                domain = account['domain']
                domain = domain.replace('https://', '').replace('http://', '')
                domain = domain.rstrip('/')
                # Remove www. prefix
                if domain.startswith('www.'):
                    domain = domain[4:]
                account['domain'] = domain

            accounts.append(account)

            # Respect limit
            if len(accounts) >= limit:
                break

        result['accounts'] = accounts
        result['unprocessed_rows'] = unprocessed_count

    except HttpError as e:
        result['errors'].append(f'Google Sheets API error: {e}')
    except Exception as e:
        result['errors'].append(f'Error reading sheet: {e}')

    return result


def mark_rows_processed(
    row_indices: list,
    status: str = 'imported',
    sheet_name: str = 'Sheet1'
) -> dict:
    """
    Write status back to the sheet to mark rows as processed.

    If the lead_machine_status column doesn't exist, creates it.

    Args:
        row_indices: List of 1-indexed row numbers to mark.
        status: Status string to write (default 'imported').
        sheet_name: Sheet tab name.

    Returns:
        Dict with success count and any errors.
    """
    result = {'updated': 0, 'errors': []}

    spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID', '')
    service = get_sheets_service()

    if not service or not spreadsheet_id:
        result['errors'].append('Sheets service not available')
        return result

    try:
        # Read headers first to find or create status columns
        header_range = f'{sheet_name}!1:1'
        response = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=header_range
        ).execute()

        headers = response.get('values', [[]])[0]
        status_col = _find_status_column(headers)
        date_col = _find_processed_date_column(headers)

        # Create status columns if they don't exist
        if status_col is None:
            status_col = len(headers)
            col_letter = _col_letter(status_col)
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f'{sheet_name}!{col_letter}1',
                valueInputOption='RAW',
                body={'values': [[STATUS_COLUMN_NAME]]}
            ).execute()
            headers.append(STATUS_COLUMN_NAME)

        if date_col is None:
            date_col = len(headers)
            col_letter = _col_letter(date_col)
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f'{sheet_name}!{col_letter}1',
                valueInputOption='RAW',
                body={'values': [[PROCESSED_DATE_COLUMN_NAME]]}
            ).execute()
            headers.append(PROCESSED_DATE_COLUMN_NAME)

        # Batch update the status and date for each row
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        status_col_letter = _col_letter(status_col)
        date_col_letter = _col_letter(date_col)

        batch_data = []
        for row_idx in row_indices:
            batch_data.append({
                'range': f'{sheet_name}!{status_col_letter}{row_idx}',
                'values': [[status]]
            })
            batch_data.append({
                'range': f'{sheet_name}!{date_col_letter}{row_idx}',
                'values': [[now]]
            })

        if batch_data:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    'valueInputOption': 'RAW',
                    'data': batch_data
                }
            ).execute()
            result['updated'] = len(row_indices)

    except HttpError as e:
        result['errors'].append(f'Google Sheets API error: {e}')
    except Exception as e:
        result['errors'].append(f'Error updating sheet: {e}')

    return result


def update_row_status(
    row_index: int,
    status: str,
    sheet_name: str = 'Sheet1'
) -> bool:
    """
    Update the status of a single row.

    Args:
        row_index: 1-indexed row number.
        status: Status string (e.g., 'imported', 'scanned_tier_2', 'failed').
        sheet_name: Sheet tab name.

    Returns:
        True if successful, False otherwise.
    """
    result = mark_rows_processed([row_index], status, sheet_name)
    return result['updated'] > 0


def get_sheet_info() -> dict:
    """
    Get information about the configured Google Sheet.

    Returns:
        Dict with sheet title, tab names, row counts, and configuration status.
    """
    info = {
        'configured': is_sheets_configured(),
        'spreadsheet_id': os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID', ''),
        'credentials_type': 'none',
        'title': '',
        'tabs': [],
        'errors': []
    }

    if not info['configured']:
        if not GOOGLE_SHEETS_AVAILABLE:
            info['errors'].append('google-api-python-client not installed. Run: pip install google-api-python-client google-auth')
        else:
            if not os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID'):
                info['errors'].append('GOOGLE_SHEETS_SPREADSHEET_ID not set in .env')
            if not (os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE') or os.getenv('GOOGLE_SHEETS_CREDENTIALS_JSON')):
                info['errors'].append('No credentials configured. Set GOOGLE_SHEETS_CREDENTIALS_FILE or GOOGLE_SHEETS_CREDENTIALS_JSON in .env')
        return info

    # Determine credentials type
    if os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE'):
        info['credentials_type'] = 'file'
    elif os.getenv('GOOGLE_SHEETS_CREDENTIALS_JSON'):
        info['credentials_type'] = 'json'

    service = get_sheets_service()
    if not service:
        info['errors'].append('Failed to authenticate with Google Sheets')
        return info

    try:
        spreadsheet = service.spreadsheets().get(
            spreadsheetId=info['spreadsheet_id']
        ).execute()

        info['title'] = spreadsheet.get('properties', {}).get('title', 'Unknown')
        info['tabs'] = [
            {
                'name': sheet['properties']['title'],
                'row_count': sheet['properties'].get('gridProperties', {}).get('rowCount', 0),
                'col_count': sheet['properties'].get('gridProperties', {}).get('columnCount', 0),
            }
            for sheet in spreadsheet.get('sheets', [])
        ]
    except HttpError as e:
        info['errors'].append(f'Google Sheets API error: {e}')
    except Exception as e:
        info['errors'].append(f'Error getting sheet info: {e}')

    return info
