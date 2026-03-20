"""Legacy Google Sheets sync compatibility shim."""
from __future__ import annotations

from typing import Mapping

from database import add_account_to_tier_0, update_account_metadata


def _store_account_metadata(company_name: str, metadata: Mapping[str, object]) -> bool:
    """Persist Sheets-derived metadata on the matching account row."""
    if not company_name:
        return False

    payload = dict(metadata or {})
    payload.setdefault('source', 'google_sheets')

    if update_account_metadata(company_name, payload):
        return True

    github_org = str(payload.get('github_org') or payload.get('org_login') or '').strip()
    add_account_to_tier_0(company_name, github_org, metadata=payload)
    return update_account_metadata(company_name, payload)

