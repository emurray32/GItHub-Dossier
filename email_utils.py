"""
Shared email-filtering utilities.

These pure-Python helpers are used by app.py and apollo_pipeline.py to filter
personal/consumer email domains and perform company-domain matching.
Centralised here to avoid duplication and circular-import risk.
"""

# Personal email domains to filter from Apollo results (not useful for B2B outreach)
_PERSONAL_EMAIL_DOMAINS = {
    'gmail.com', 'googlemail.com', 'yahoo.com', 'hotmail.com',
    'outlook.com', 'aol.com', 'icloud.com', 'me.com', 'live.com',
    'msn.com', 'protonmail.com', 'proton.me', 'mail.com', 'ymail.com',
}


def _filter_personal_email(email):
    """Return empty string if email is from a personal domain (gmail, yahoo, etc.)."""
    if not email:
        return ''
    domain = email.lower().split('@')[-1] if '@' in email else ''
    return '' if domain in _PERSONAL_EMAIL_DOMAINS else email


def _derive_company_domain(company):
    """Derive a likely domain from a company name (e.g. 'Clay' -> 'clay.com')."""
    if not company:
        return ''
    clean = company.strip().lower()
    for suffix in [' inc', ' inc.', ' corp', ' corp.', ' ltd', ' ltd.',
                   ' llc', ' co', ' co.', ' gmbh', ' ag', ' sa']:
        if clean.endswith(suffix):
            clean = clean[:len(clean) - len(suffix)]
    return clean.replace(' ', '') + '.com'


def _check_company_match(email, target_company):
    """Return True if the email domain plausibly matches the target company."""
    if not email or not target_company:
        return True  # nothing to compare -- allow through
    if '@' not in email:
        return True
    email_domain = email.lower().split('@')[-1]
    # Check against derived domain
    target_domain = _derive_company_domain(target_company)
    if target_domain and email_domain == target_domain:
        return True
    # Fuzzy: company name appears in email domain or vice versa
    co_lower = target_company.lower().strip().replace(' ', '')
    if co_lower in email_domain or email_domain.split('.')[0] in co_lower:
        return True
    return False
