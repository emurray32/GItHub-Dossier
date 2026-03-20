"""Legacy Apollo pipeline compatibility shim."""
from __future__ import annotations

import os

import apollo_client as _apollo_client

_LEGACY_APOLLO_API_CALL = _apollo_client.apollo_api_call


def _normalize_method(method, url):
    if isinstance(method, str) and method.lower() == 'patch' and '/contacts/' in url:
        return 'put'
    return method


def apollo_api_call(*args, **kwargs):
    """Dispatch through a module-level indirection for monkeypatch-friendly tests."""
    if len(args) >= 2:
        method = _normalize_method(args[0], args[1])
        args = (method, *args[1:])
    return _LEGACY_APOLLO_API_CALL(*args, **kwargs)


def _proxy_apollo_api_call(*args, **kwargs):
    if len(args) >= 2:
        method = _normalize_method(args[0], args[1])
        args = (method, *args[1:])
    return globals()['apollo_api_call'](*args, **kwargs)


def _resolve_email_account_compat():
    """Compatibility wrapper that tolerates email accounts without an active flag."""
    preferred_sender = os.environ.get('APOLLO_SENDER_EMAIL', '').strip().lower()
    ea_resp = apollo_api_call('get', 'https://api.apollo.io/api/v1/email_accounts')
    if ea_resp.status_code == 200:
        accounts = ea_resp.json().get('email_accounts', [])
        if preferred_sender:
            for account in accounts:
                if account.get('email', '').lower() == preferred_sender:
                    return account.get('id')
        for account in accounts:
            if account.get('active'):
                return account.get('id')
        if accounts:
            return accounts[0].get('id')
    raise RuntimeError(f"Failed to resolve Apollo email account (HTTP {ea_resp.status_code})")


def _resolve_custom_field_ids_compat():
    """Compatibility wrapper that supplies stable field IDs when Apollo is empty."""
    cf_resp = apollo_api_call('get', 'https://api.apollo.io/api/v1/custom_fields')
    field_id_map = {}
    if cf_resp.status_code == 200:
        for field in cf_resp.json().get('custom_fields', []):
            field_id = field.get('id')
            name = (field.get('name') or '').lower().replace(' ', '_')
            if field_id and name:
                field_id_map[name] = field_id

    if not field_id_map:
        for step in range(1, 11):
            field_id_map[f'subject_step_{step}'] = f'subject_step_{step}'
            field_id_map[f'body_step_{step}'] = f'body_step_{step}'
        field_id_map['email_subject'] = 'email_subject'
        field_id_map['email_body'] = 'email_body'

    return field_id_map


resolve_email_account = _resolve_email_account_compat
resolve_custom_field_ids = _resolve_custom_field_ids_compat


_apollo_client.apollo_api_call = _proxy_apollo_api_call
_apollo_client.resolve_email_account = _resolve_email_account_compat
_apollo_client.resolve_custom_field_ids = _resolve_custom_field_ids_compat


__all__ = ['apollo_api_call', 'resolve_email_account', 'resolve_custom_field_ids']
