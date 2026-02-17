"""
Enhanced Heuristics Module for Global Expansion Intent Detection.

This module provides 10 additional heuristics for identifying companies
with high intent on:
1. Expanding globally
2. Making changes to their current localization
3. Tech stack, domains, HREF tags analysis

These heuristics complement the core 3-signal scanner with deeper insights.
"""

import re
import requests
import base64
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Generator
from config import Config
from utils import make_github_request


def _safe_json_parse(response: requests.Response, default=None):
    """Safely parse JSON from a response."""
    content_type = response.headers.get('content-type', '')
    if 'application/json' not in content_type:
        return default
    try:
        return response.json()
    except (ValueError, Exception):
        return default


# ============================================================
# HEURISTIC 1: JOB POSTING INTENT ANALYSIS
# ============================================================

def scan_job_posting_intent(org: str, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Scan for job postings indicating localization/i18n hiring intent.

    Searches for:
    - JOBS.md, CAREERS.md, HIRING.md files
    - careers/ or jobs/ directories
    - Job-related keywords in README

    Args:
        org: GitHub organization login
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("Scanning for job posting intent signals...", None)

    job_signals = []

    for repo in repos[:10]:  # Check top 10 repos
        repo_name = repo.get('name', '')

        # Check for job-related files
        for job_file in Config.JOB_POSTING_FILES:
            try:
                if job_file.endswith('/'):
                    # Directory check via tree API
                    url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/{job_file.rstrip('/')}"
                else:
                    url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/{job_file}"

                response = make_github_request(url, timeout=10)

                if response.status_code == 200:
                    content_data = _safe_json_parse(response, {})

                    # If it's a file, check content for i18n job keywords
                    if isinstance(content_data, dict) and content_data.get('content'):
                        try:
                            content = base64.b64decode(content_data['content']).decode('utf-8', errors='ignore').lower()

                            for keyword in Config.JOB_INTENT_KEYWORDS:
                                if keyword.lower() in content:
                                    signal = {
                                        'Company': org,
                                        'Signal': 'Job Posting Intent',
                                        'detected_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                        'Evidence': f"Found '{keyword}' in {job_file} ({repo_name})",
                                        'Link': content_data.get('html_url', ''),
                                        'priority': 'HIGH',
                                        'type': 'job_posting_intent',
                                        'repo': repo_name,
                                        'file': job_file,
                                        'keyword_matched': keyword,
                                    }
                                    job_signals.append(signal)
                                    yield (f"JOB SIGNAL: Found '{keyword}' in {repo_name}/{job_file}", signal)
                                    break  # One match per file is enough
                        except Exception:
                            pass

            except requests.RequestException:
                continue

    if not job_signals:
        yield ("No job posting intent signals found.", None)


# ============================================================
# HEURISTIC 2: REGIONAL DOMAIN / ccTLD DETECTION
# ============================================================

def scan_regional_domains(org: str, org_data: Dict, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Detect regional domain patterns indicating international presence.

    Searches for:
    - Country-code TLDs in org website/blog
    - Regional subdomains in repo homepages
    - Multiple regional domains in README

    Args:
        org: GitHub organization login
        org_data: Organization metadata
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("Scanning for regional domain patterns...", None)

    regional_signals = []
    detected_regions = set()

    # Check org website
    org_blog = org_data.get('blog', '') or ''
    if org_blog:
        for cctld, country in Config.REGIONAL_CCTLDS.items():
            if f'.{cctld}' in org_blog.lower() or org_blog.lower().endswith(f'.{cctld}'):
                detected_regions.add((cctld, country, org_blog))

    # Check repo homepages
    for repo in repos[:20]:
        homepage = repo.get('homepage', '') or ''
        if homepage:
            for cctld, country in Config.REGIONAL_CCTLDS.items():
                if f'.{cctld}' in homepage.lower() or homepage.lower().endswith(f'.{cctld}'):
                    detected_regions.add((cctld, country, homepage))

    # Check for regional subdomain patterns
    for repo in repos[:10]:
        homepage = repo.get('homepage', '') or ''
        for pattern in Config.REGIONAL_SUBDOMAIN_PATTERNS:
            if re.search(pattern, homepage, re.IGNORECASE):
                match = re.search(pattern, homepage, re.IGNORECASE)
                detected_regions.add(('subdomain', match.group(0), homepage))

    if detected_regions:
        regions_list = list(detected_regions)[:5]  # Limit to 5
        signal = {
            'Company': org,
            'Signal': 'Regional Domain Detection',
            'Evidence': f"Found {len(detected_regions)} regional domain(s): {', '.join([r[1] for r in regions_list])}",
            'Link': org_data.get('html_url', ''),
            'priority': 'MEDIUM',
            'type': 'regional_domain_detection',
            'regions_detected': [{'code': r[0], 'name': r[1], 'url': r[2]} for r in regions_list],
            'total_regions': len(detected_regions),
        }
        regional_signals.append(signal)
        yield (f"REGIONAL DOMAINS: Found {len(detected_regions)} regional presence(s)", signal)
    else:
        yield ("No regional domain patterns detected.", None)


# ============================================================
# HEURISTIC 3: HEADLESS CMS LOCALIZATION READINESS
# ============================================================

def scan_headless_cms_i18n(org: str, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Detect headless CMS configurations with i18n/multi-locale setup.

    Checks for:
    - Contentful, Sanity, Strapi, Prismic config files
    - i18n-related configuration keys
    - Multi-locale setup indicators

    Args:
        org: GitHub organization login
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("Scanning for headless CMS localization configurations...", None)

    cms_signals = []

    for repo in repos[:15]:
        repo_name = repo.get('name', '')

        for cms_name, cms_config in Config.HEADLESS_CMS_I18N_CONFIGS.items():
            for config_file in cms_config['config_files']:
                try:
                    url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/{config_file}"
                    response = make_github_request(url, timeout=10)

                    if response.status_code == 200:
                        content_data = _safe_json_parse(response, {})

                        if content_data.get('content'):
                            try:
                                content = base64.b64decode(content_data['content']).decode('utf-8', errors='ignore').lower()

                                # Check for i18n indicators
                                i18n_found = []
                                for indicator in cms_config['i18n_indicators']:
                                    if indicator.lower() in content:
                                        i18n_found.append(indicator)

                                if i18n_found:
                                    signal = {
                                        'Company': org,
                                        'Signal': 'Headless CMS i18n Config',
                                        'Evidence': f"{cms_name.title()} with i18n config: {', '.join(i18n_found[:3])} in {repo_name}",
                                        'Link': content_data.get('html_url', ''),
                                        'priority': 'HIGH',
                                        'type': 'headless_cms_i18n',
                                        'repo': repo_name,
                                        'cms': cms_name,
                                        'config_file': config_file,
                                        'i18n_indicators': i18n_found,
                                    }
                                    cms_signals.append(signal)
                                    yield (f"CMS i18n: {cms_name.title()} configured for localization in {repo_name}", signal)
                            except Exception:
                                pass

                except requests.RequestException:
                    continue

    if not cms_signals:
        yield ("No headless CMS i18n configurations found.", None)


# ============================================================
# HEURISTIC 4: MULTI-CURRENCY PAYMENT INFRASTRUCTURE
# ============================================================

def scan_payment_infrastructure(org: str, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Detect multi-currency payment processing setup.

    Searches for:
    - International payment libraries (Stripe, PayPal, regional providers)
    - Multi-currency handling patterns
    - Currency conversion code

    Args:
        org: GitHub organization login
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("Scanning for multi-currency payment infrastructure...", None)

    payment_signals = []

    for repo in repos[:15]:
        repo_name = repo.get('name', '')

        # Check package.json for payment libraries
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/package.json"
            response = make_github_request(url, timeout=10)

            if response.status_code == 200:
                content_data = _safe_json_parse(response, {})

                if content_data.get('content'):
                    try:
                        content = base64.b64decode(content_data['content']).decode('utf-8', errors='ignore').lower()

                        payment_libs_found = []
                        for lib in Config.PAYMENT_I18N_LIBRARIES:
                            if lib.lower() in content:
                                payment_libs_found.append(lib)

                        # Check for multi-currency patterns
                        currency_patterns_found = []
                        for pattern in Config.PAYMENT_MULTI_CURRENCY_PATTERNS:
                            if pattern.lower() in content:
                                currency_patterns_found.append(pattern)

                        if payment_libs_found and currency_patterns_found:
                            signal = {
                                'Company': org,
                                'Signal': 'Multi-Currency Payment',
                                'Evidence': f"Payment libs ({', '.join(payment_libs_found[:2])}) with currency handling in {repo_name}",
                                'Link': content_data.get('html_url', ''),
                                'priority': 'HIGH',
                                'type': 'payment_multi_currency',
                                'repo': repo_name,
                                'payment_libraries': payment_libs_found,
                                'currency_patterns': currency_patterns_found[:5],
                            }
                            payment_signals.append(signal)
                            yield (f"PAYMENT: Multi-currency setup found in {repo_name}", signal)
                    except Exception:
                        pass

        except requests.RequestException:
            continue

    if not payment_signals:
        yield ("No multi-currency payment infrastructure found.", None)


# ============================================================
# HEURISTIC 5: TIMEZONE & DATE FORMATTING LIBRARIES
# ============================================================

def scan_timezone_libraries(org: str, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Detect timezone and locale-aware date formatting libraries.

    These indicate preparation for global users across time zones.

    Args:
        org: GitHub organization login
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("Scanning for timezone/date formatting libraries...", None)

    tz_signals = []

    for repo in repos[:15]:
        repo_name = repo.get('name', '')

        # Check package.json
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/package.json"
            response = make_github_request(url, timeout=10)

            if response.status_code == 200:
                content_data = _safe_json_parse(response, {})

                if content_data.get('content'):
                    try:
                        content = base64.b64decode(content_data['content']).decode('utf-8', errors='ignore').lower()

                        tz_libs_found = []
                        for lib in Config.TIMEZONE_I18N_LIBRARIES:
                            if lib.lower() in content:
                                tz_libs_found.append(lib)

                        # Heavy i18n libraries are stronger signals
                        heavy_i18n = [lib for lib in tz_libs_found if 'formatjs' in lib.lower() or 'full-icu' in lib.lower()]

                        if heavy_i18n:
                            priority = 'HIGH'
                            evidence = f"Heavy i18n libraries ({', '.join(heavy_i18n)}) in {repo_name}"
                        elif tz_libs_found:
                            priority = 'MEDIUM'
                            evidence = f"Timezone libraries ({', '.join(tz_libs_found[:3])}) in {repo_name}"
                        else:
                            continue

                        signal = {
                            'Company': org,
                            'Signal': 'Timezone/Date i18n',
                            'Evidence': evidence,
                            'Link': content_data.get('html_url', ''),
                            'priority': priority,
                            'type': 'timezone_library',
                            'repo': repo_name,
                            'libraries': tz_libs_found,
                            'heavy_i18n': heavy_i18n,
                        }
                        tz_signals.append(signal)
                        yield (f"TIMEZONE: {evidence}", signal)
                    except Exception:
                        pass

        except requests.RequestException:
            continue

    if not tz_signals:
        yield ("No timezone/date i18n libraries found.", None)


# ============================================================
# HEURISTIC 6: CI/CD LOCALIZATION PIPELINE DETECTION
# ============================================================

def scan_ci_localization_pipeline(org: str, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Detect CI/CD pipelines with translation platform integrations.

    Searches for:
    - GitHub Actions with Crowdin, Lokalise, etc.
    - Translation platform config files
    - Sync scripts for translations

    Args:
        org: GitHub organization login
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("Scanning for CI/CD localization pipelines...", None)

    ci_signals = []

    for repo in repos[:15]:
        repo_name = repo.get('name', '')

        # Check for translation platform config files
        for config_file in Config.CI_LOCALIZATION_PATTERNS['platform_configs']:
            try:
                url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/{config_file}"
                response = make_github_request(url, timeout=10)

                if response.status_code == 200:
                    content_data = _safe_json_parse(response, {})

                    platform = config_file.split('.')[0].replace('.', '').replace('/', '')
                    signal = {
                        'Company': org,
                        'Signal': 'CI/CD Localization Pipeline',
                        'Evidence': f"Translation platform config ({platform}) found in {repo_name}",
                        'Link': content_data.get('html_url', ''),
                        'priority': 'HIGH',
                        'type': 'ci_localization_pipeline',
                        'repo': repo_name,
                        'config_file': config_file,
                        'platform': platform,
                    }
                    ci_signals.append(signal)
                    yield (f"CI/CD: {platform.title()} config in {repo_name}", signal)

            except requests.RequestException:
                continue

        # Check GitHub Actions workflows
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/.github/workflows"
            response = make_github_request(url, timeout=10)

            if response.status_code == 200:
                workflows = _safe_json_parse(response, [])

                for workflow in workflows[:10]:
                    if workflow.get('type') == 'file' and workflow.get('name', '').endswith(('.yml', '.yaml')):
                        try:
                            workflow_url = workflow.get('url')
                            workflow_response = make_github_request(workflow_url, timeout=10)

                            if workflow_response.status_code == 200:
                                workflow_data = _safe_json_parse(workflow_response, {})

                                if workflow_data.get('content'):
                                    content = base64.b64decode(workflow_data['content']).decode('utf-8', errors='ignore').lower()

                                    for pattern in Config.CI_LOCALIZATION_PATTERNS['github_actions']['patterns']:
                                        if pattern.lower() in content:
                                            signal = {
                                                'Company': org,
                                                'Signal': 'CI/CD Localization Pipeline',
                                                'Evidence': f"GitHub Action with '{pattern}' in {repo_name}/{workflow.get('name')}",
                                                'Link': workflow.get('html_url', ''),
                                                'priority': 'HIGH',
                                                'type': 'ci_localization_pipeline',
                                                'repo': repo_name,
                                                'workflow': workflow.get('name'),
                                                'pattern_matched': pattern,
                                            }
                                            ci_signals.append(signal)
                                            yield (f"CI/CD: Translation automation in {repo_name}", signal)
                                            break
                        except Exception:
                            pass

        except requests.RequestException:
            continue

    if not ci_signals:
        yield ("No CI/CD localization pipelines found.", None)


# ============================================================
# HEURISTIC 7: LEGAL/COMPLIANCE DOCUMENTATION SIGNALS
# ============================================================

def scan_compliance_documentation(org: str, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Detect regional compliance documentation indicating expansion plans.

    Searches for:
    - GDPR, CCPA, LGPD compliance files
    - Privacy policy variations
    - Data processing agreements

    Args:
        org: GitHub organization login
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("Scanning for compliance documentation signals...", None)

    compliance_signals = []
    compliance_keywords_found = set()

    for repo in repos[:10]:
        repo_name = repo.get('name', '')

        for compliance_file in Config.COMPLIANCE_FILES:
            try:
                if compliance_file.endswith('/'):
                    url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/{compliance_file.rstrip('/')}"
                else:
                    url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/{compliance_file}"

                response = make_github_request(url, timeout=10)

                if response.status_code == 200:
                    content_data = _safe_json_parse(response, {})

                    # Check file content for compliance keywords
                    if isinstance(content_data, dict) and content_data.get('content'):
                        try:
                            content = base64.b64decode(content_data['content']).decode('utf-8', errors='ignore').lower()

                            for keyword in Config.COMPLIANCE_KEYWORDS:
                                if keyword.lower() in content:
                                    compliance_keywords_found.add(keyword)
                        except Exception:
                            pass

                    # Directory found with compliance-related content
                    if isinstance(content_data, list):
                        signal = {
                            'Company': org,
                            'Signal': 'Compliance Documentation',
                            'Evidence': f"Legal/compliance directory found: {compliance_file} in {repo_name}",
                            'Link': f"https://github.com/{org}/{repo_name}/tree/main/{compliance_file.rstrip('/')}",
                            'priority': 'MEDIUM',
                            'type': 'compliance_documentation',
                            'repo': repo_name,
                            'path': compliance_file,
                        }
                        compliance_signals.append(signal)

            except requests.RequestException:
                continue

    # If we found compliance keywords, generate a signal
    if compliance_keywords_found:
        regions_indicated = []
        if any('gdpr' in k for k in compliance_keywords_found):
            regions_indicated.append('EU/EEA')
        if any('ccpa' in k or 'california' in k for k in compliance_keywords_found):
            regions_indicated.append('US/California')
        if any('lgpd' in k for k in compliance_keywords_found):
            regions_indicated.append('Brazil')
        if any('pdpa' in k for k in compliance_keywords_found):
            regions_indicated.append('Singapore/Thailand')
        if any('appi' in k for k in compliance_keywords_found):
            regions_indicated.append('Japan')
        if any('pipl' in k for k in compliance_keywords_found):
            regions_indicated.append('China')

        if regions_indicated:
            signal = {
                'Company': org,
                'Signal': 'Regional Compliance Intent',
                'Evidence': f"Compliance for: {', '.join(regions_indicated)}",
                'Link': '',
                'priority': 'MEDIUM',
                'type': 'compliance_documentation',
                'regions': regions_indicated,
                'keywords_found': list(compliance_keywords_found)[:10],
            }
            compliance_signals.append(signal)
            yield (f"COMPLIANCE: Preparing for {', '.join(regions_indicated)}", signal)

    if not compliance_signals:
        yield ("No compliance documentation signals found.", None)


# ============================================================
# HEURISTIC 8: SOCIAL PROOF / MULTI-REGION META TAGS
# ============================================================

def analyze_social_multi_region(website_data: Dict) -> Dict[str, any]:
    """
    Analyze website for social/OpenGraph multi-region indicators.

    This is called from web_analyzer.py with fetched website data.

    Args:
        website_data: Dictionary containing website analysis data

    Returns:
        Dictionary with social multi-region analysis
    """
    result = {
        'has_multi_region_social': False,
        'og_locale_count': 0,
        'regional_handles': [],
        'structured_data_i18n': False,
        'details': {},
    }

    # Check hreflang count (already extracted)
    hreflang_tags = website_data.get('hreflang_tags', [])
    if len(hreflang_tags) > 1:
        result['has_multi_region_social'] = True
        result['og_locale_count'] = len(hreflang_tags)

    # Check localization score details
    loc_score = website_data.get('localization_score', {})
    details = loc_score.get('details', {})

    if details.get('og_locale_alternate_count', 0) > 0:
        result['has_multi_region_social'] = True
        result['og_locale_count'] = max(result['og_locale_count'], details['og_locale_alternate_count'])

    # Check for regional social handles in links
    links = website_data.get('links', [])
    for link in links[:50]:
        href = link.get('href', '').lower()
        text = link.get('text', '').lower()

        for handle_pattern in Config.SOCIAL_MULTI_REGION_PATTERNS['regional_social_handles']:
            if handle_pattern.lower() in href or handle_pattern.lower() in text:
                if 'twitter' in href or 'facebook' in href or 'linkedin' in href or 'instagram' in href:
                    result['regional_handles'].append({
                        'pattern': handle_pattern,
                        'url': link.get('href', ''),
                    })

    if result['regional_handles']:
        result['has_multi_region_social'] = True

    return result


# ============================================================
# HEURISTIC 9: CONTENT FRESHNESS & UPDATE VELOCITY
# ============================================================

def scan_locale_velocity(org: str, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Track commit velocity on locale-related files.

    Identifies active localization work vs dormant infrastructure.

    Args:
        org: GitHub organization login
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("Scanning locale file update velocity...", None)

    velocity_signals = []
    total_locale_commits = 0
    active_repos = []

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=Config.LOCALE_VELOCITY_LOOKBACK_DAYS)

    for repo in repos[:10]:
        repo_name = repo.get('name', '')
        repo_locale_commits = 0

        try:
            # Get recent commits
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/commits"
            params = {
                'since': cutoff_date.isoformat(),
                'per_page': 100,
            }
            response = make_github_request(url, params=params, timeout=15)

            if response.status_code == 200:
                commits = _safe_json_parse(response, [])

                for commit in commits:
                    message = commit.get('commit', {}).get('message', '').lower()

                    # Check if commit message mentions locale-related work
                    locale_keywords = ['locale', 'i18n', 'l10n', 'translation', 'localization',
                                     'language', 'intl', 'internationalization']

                    if any(kw in message for kw in locale_keywords):
                        repo_locale_commits += 1
                        total_locale_commits += 1

                if repo_locale_commits > 0:
                    active_repos.append({
                        'repo': repo_name,
                        'commits': repo_locale_commits,
                    })

        except requests.RequestException:
            continue

    # Determine velocity category
    thresholds = Config.LOCALE_VELOCITY_THRESHOLDS

    if total_locale_commits >= thresholds['high_activity']:
        priority = 'HIGH'
        activity_level = 'high'
    elif total_locale_commits >= thresholds['medium_activity']:
        priority = 'MEDIUM'
        activity_level = 'medium'
    elif total_locale_commits >= thresholds['low_activity']:
        priority = 'LOW'
        activity_level = 'low'
    else:
        yield (f"Locale velocity: {total_locale_commits} commits in last {Config.LOCALE_VELOCITY_LOOKBACK_DAYS} days (dormant)", None)
        return

    signal = {
        'Company': org,
        'Signal': 'Locale Update Velocity',
        'Evidence': f"{total_locale_commits} locale-related commits in last {Config.LOCALE_VELOCITY_LOOKBACK_DAYS} days ({activity_level} activity)",
        'Link': '',
        'priority': priority,
        'type': f'locale_velocity_{activity_level}',
        'total_commits': total_locale_commits,
        'activity_level': activity_level,
        'active_repos': active_repos[:5],
        'lookback_days': Config.LOCALE_VELOCITY_LOOKBACK_DAYS,
    }
    velocity_signals.append(signal)
    yield (f"VELOCITY: {total_locale_commits} locale commits ({activity_level} activity)", signal)


# ============================================================
# HEURISTIC 10: API INTERNATIONAL ENDPOINT DETECTION
# ============================================================

def scan_api_international_endpoints(org: str, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Detect API patterns indicating multi-region architecture.

    Searches for:
    - OpenAPI/Swagger specs with i18n fields
    - Region/locale parameters in API routes
    - Internationalization middleware patterns

    Args:
        org: GitHub organization login
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("Scanning for international API patterns...", None)

    api_signals = []

    for repo in repos[:15]:
        repo_name = repo.get('name', '')

        # Check for OpenAPI/Swagger files
        for api_file in Config.OPENAPI_FILES:
            try:
                if api_file.endswith('/'):
                    continue

                url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/{api_file}"
                response = make_github_request(url, timeout=10)

                if response.status_code == 200:
                    content_data = _safe_json_parse(response, {})

                    if content_data.get('content'):
                        try:
                            content = base64.b64decode(content_data['content']).decode('utf-8', errors='ignore').lower()

                            # Check for i18n-related API fields
                            i18n_fields = []
                            for field in Config.API_INTERNATIONAL_PATTERNS['openapi_i18n_fields']:
                                if field.lower() in content:
                                    i18n_fields.append(field)

                            # Check for endpoint patterns
                            endpoint_matches = []
                            for pattern in Config.API_INTERNATIONAL_PATTERNS['endpoint_patterns']:
                                if re.search(pattern, content, re.IGNORECASE):
                                    endpoint_matches.append(pattern)

                            if i18n_fields or endpoint_matches:
                                signal = {
                                    'Company': org,
                                    'Signal': 'API International Endpoints',
                                    'Evidence': f"API spec with i18n patterns in {repo_name}/{api_file}",
                                    'Link': content_data.get('html_url', ''),
                                    'priority': 'MEDIUM',
                                    'type': 'api_international',
                                    'repo': repo_name,
                                    'api_file': api_file,
                                    'i18n_fields': i18n_fields,
                                    'endpoint_patterns': endpoint_matches[:3],
                                }
                                api_signals.append(signal)
                                yield (f"API: International endpoints in {repo_name}", signal)
                        except Exception:
                            pass

            except requests.RequestException:
                continue

        # Check for API route files with international patterns
        api_route_files = ['routes.js', 'routes.ts', 'api.js', 'api.ts',
                          'src/routes/index.js', 'src/routes/index.ts',
                          'server/routes.js', 'server/routes.ts']

        for route_file in api_route_files:
            try:
                url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo_name}/contents/{route_file}"
                response = make_github_request(url, timeout=10)

                if response.status_code == 200:
                    content_data = _safe_json_parse(response, {})

                    if content_data.get('content'):
                        try:
                            content = base64.b64decode(content_data['content']).decode('utf-8', errors='ignore')

                            code_patterns_found = []
                            for pattern in Config.API_INTERNATIONAL_PATTERNS['code_patterns']:
                                if pattern.lower() in content.lower():
                                    code_patterns_found.append(pattern)

                            if len(code_patterns_found) >= 2:  # Need at least 2 patterns
                                signal = {
                                    'Company': org,
                                    'Signal': 'API International Endpoints',
                                    'Evidence': f"Route handlers with i18n patterns ({', '.join(code_patterns_found[:3])}) in {repo_name}",
                                    'Link': content_data.get('html_url', ''),
                                    'priority': 'MEDIUM',
                                    'type': 'api_international',
                                    'repo': repo_name,
                                    'route_file': route_file,
                                    'code_patterns': code_patterns_found,
                                }
                                api_signals.append(signal)
                                yield (f"API: i18n route handlers in {repo_name}", signal)
                        except Exception:
                            pass

            except requests.RequestException:
                continue

    if not api_signals:
        yield ("No international API patterns found.", None)


# ============================================================
# MAIN ENHANCED SCAN FUNCTION
# ============================================================

def run_enhanced_heuristics(org: str, org_data: Dict, repos: List[Dict]) -> Generator[Tuple[str, Optional[Dict]], None, None]:
    """
    Run all enhanced heuristics and yield signals.

    This is the main entry point called from scanner.py.

    Args:
        org: GitHub organization login
        org_data: Organization metadata
        repos: List of repository dictionaries

    Yields:
        Tuples of (log_message, signal_dict or None)
    """
    yield ("", None)
    yield ("=" * 60, None)
    yield ("ENHANCED HEURISTICS: Global Expansion Intent Analysis", None)
    yield ("=" * 60, None)

    # Helper: inject detected_at timestamp into all yielded signals
    _now_ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def _with_timestamp(gen):
        for msg, signal in gen:
            if signal is not None and isinstance(signal, dict) and 'detected_at' not in signal:
                signal['detected_at'] = _now_ts
            yield (msg, signal)

    # 1. Job Posting Intent
    yield ("", None)
    yield ("[1/10] Job Posting Intent Analysis", None)
    yield ("-" * 40, None)
    for msg, signal in _with_timestamp(scan_job_posting_intent(org, repos)):
        yield (msg, signal)

    # 2. Regional Domain Detection
    yield ("", None)
    yield ("[2/10] Regional Domain Detection", None)
    yield ("-" * 40, None)
    for msg, signal in _with_timestamp(scan_regional_domains(org, org_data, repos)):
        yield (msg, signal)

    # 3. Headless CMS i18n
    yield ("", None)
    yield ("[3/10] Headless CMS Localization Readiness", None)
    yield ("-" * 40, None)
    for msg, signal in _with_timestamp(scan_headless_cms_i18n(org, repos)):
        yield (msg, signal)

    # 4. Payment Infrastructure
    yield ("", None)
    yield ("[4/10] Multi-Currency Payment Infrastructure", None)
    yield ("-" * 40, None)
    for msg, signal in _with_timestamp(scan_payment_infrastructure(org, repos)):
        yield (msg, signal)

    # 5. Timezone Libraries
    yield ("", None)
    yield ("[5/10] Timezone & Date Formatting Libraries", None)
    yield ("-" * 40, None)
    for msg, signal in _with_timestamp(scan_timezone_libraries(org, repos)):
        yield (msg, signal)

    # 6. CI/CD Pipeline
    yield ("", None)
    yield ("[6/10] CI/CD Localization Pipeline", None)
    yield ("-" * 40, None)
    for msg, signal in _with_timestamp(scan_ci_localization_pipeline(org, repos)):
        yield (msg, signal)

    # 7. Compliance Documentation
    yield ("", None)
    yield ("[7/10] Legal/Compliance Documentation", None)
    yield ("-" * 40, None)
    for msg, signal in _with_timestamp(scan_compliance_documentation(org, repos)):
        yield (msg, signal)

    # 8. Locale Velocity (Content Freshness)
    yield ("", None)
    yield ("[8/10] Locale Update Velocity", None)
    yield ("-" * 40, None)
    for msg, signal in _with_timestamp(scan_locale_velocity(org, repos)):
        yield (msg, signal)

    # 9. API International Endpoints
    yield ("", None)
    yield ("[9/10] API International Endpoints", None)
    yield ("-" * 40, None)
    for msg, signal in _with_timestamp(scan_api_international_endpoints(org, repos)):
        yield (msg, signal)

    # 10. Social Multi-Region is handled in web_analyzer.py
    yield ("", None)
    yield ("[10/10] Social Multi-Region (analyzed via website)", None)
    yield ("-" * 40, None)
    yield ("Social multi-region signals analyzed during website scan.", None)

    yield ("", None)
    yield ("=" * 60, None)
    yield ("Enhanced Heuristics Scan Complete", None)
    yield ("=" * 60, None)
