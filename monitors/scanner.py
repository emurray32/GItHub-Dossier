"""
3-Signal Internationalization Intent Scanner.

Detects companies in the THINKING and PREPARING phases of internationalization
by scanning GitHub repositories for pre-launch signals:

1. RFC & Discussion Signal - Intent before code is written
2. Dependency Injection Signal - The moment i18n tools are bought
3. Ghost Branch Signal - Work-in-progress that hasn't launched

This scanner focuses on PRE-MERGE, PRE-LAUNCH detection - the ideal sales window.
"""
import json
import re
import requests
import base64
import time
from datetime import datetime, timedelta, timezone
from typing import Generator, Optional, List, Dict
from config import Config
from .discovery import get_github_headers, discover_organization, get_organization_repos, _get_org_details
from database import increment_daily_stat
from utils import make_github_request




def _parse_timestamp(timestamp: Optional[object]) -> Optional[datetime]:
    if not timestamp:
        return None
    if isinstance(timestamp, datetime):
        return timestamp
    if isinstance(timestamp, str):
        normalized = timestamp
        if normalized.endswith('Z'):
            normalized = normalized.replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def _format_request_exception(error: requests.RequestException) -> str:
    response = getattr(error, 'response', None)
    if response is None:
        return str(error)

    status_code = response.status_code
    if status_code == 429:
        reason = 'Rate Limit'
    else:
        reason = response.reason or 'Request Failed'
    return f"Error: {status_code} {reason}"


def _is_open_protocol_project(org_description: Optional[str]) -> Optional[str]:
    """
    Check if an organization description matches open protocol/decentralized project patterns.

    These are NOT commercial companies with buying intent - they're community-driven
    open source projects, blockchain protocols, DAOs, etc.

    Args:
        org_description: The GitHub organization's description field

    Returns:
        The matched disqualifier pattern if found, None otherwise
    """
    if not org_description:
        return None

    description_lower = org_description.lower()

    for pattern in Config.OPEN_PROTOCOL_DISQUALIFIERS:
        if pattern.lower() in description_lower:
            return pattern

    return None


def deep_scan_generator(company_name: str, last_scanned_timestamp: Optional[object] = None, github_org: Optional[str] = None) -> Generator[str, None, None]:
    """
    Perform a 3-Signal Intent Scan of a company's GitHub presence.

    Focuses on detecting pre-launch internationalization signals:
    - RFC & Discussion (Thinking Phase)
    - Dependency Injection (Preparing Phase)
    - Ghost Branch (Active Phase)

    Args:
        company_name: The company name to scan.
        last_scanned_timestamp: Optional timestamp of the last scan to skip unchanged repos.
        github_org: Optional pre-linked GitHub organization login. If provided, skips discovery
                    and uses this org directly. This is critical for accounts that have been
                    manually linked to a GitHub org that may not match the company name exactly.

    Yields:
        SSE-formatted strings for streaming response.
    """
    start_time = datetime.now()

    yield _sse_log(f"Starting 3-Signal Intent Scan: {company_name}")
    yield _sse_log("=" * 60)
    yield _sse_log("Target: Pre-launch internationalization signals")
    yield _sse_log("")

    # Phase 1: Discover Organization (or use pre-linked org)
    yield _sse_log("PHASE 1: Organization Discovery")
    yield _sse_log("-" * 40)

    org_data = None

    # If a github_org is pre-linked, use it directly instead of discovery
    if github_org:
        yield _sse_log(f"Using pre-linked GitHub organization: @{github_org}")
        org_data = _get_org_details(github_org)
        if org_data:
            yield _sse_log(f"Organization found: @{github_org}")
        else:
            yield _sse_log(f"Pre-linked org @{github_org} not found, falling back to discovery...")
            # Fall through to discovery below

    # Run discovery if no pre-linked org or pre-linked org wasn't found
    if not org_data:
        org_generator = discover_organization(company_name)

        try:
            while True:
                try:
                    message = next(org_generator)
                    yield _sse_log(message)
                except StopIteration as e:
                    org_data = e.value
                    break
        except Exception as e:
            yield _sse_log(f"Error during discovery: {str(e)}")

    if not org_data:
        yield _sse_error("Could not find GitHub organization. Scan aborted.")
        return

    org_login = org_data.get('login')
    org_name = org_data.get('name') or org_login
    org_description = org_data.get('description')

    yield _sse_log(f"Organization confirmed: {org_name} (@{org_login})")
    yield _sse_log(f"  Public repos: {org_data.get('public_repos', 'N/A')}")

    # Check for open protocol / decentralized project disqualifiers
    # These are NOT commercial companies with buying intent
    matched_pattern = _is_open_protocol_project(org_description)
    if matched_pattern:
        yield _sse_log("")
        yield _sse_log("⚠️ OPEN PROTOCOL PROJECT DETECTED")
        yield _sse_log(f"  Description: {org_description}")
        yield _sse_log(f"  Matched pattern: '{matched_pattern}'")
        yield _sse_error(f"DISQUALIFIED: Open protocol/decentralized project - not a commercial buyer. Pattern matched: '{matched_pattern}'")
        return

    # Phase 2: Fetch Repositories
    yield _sse_log("")
    yield _sse_log("PHASE 2: Repository Discovery")
    yield _sse_log("-" * 40)

    repos = []
    repos_generator = get_organization_repos(org_login)

    try:
        while True:
            try:
                message = next(repos_generator)
                yield _sse_log(message)
            except StopIteration as e:
                repos = e.value
                break
    except Exception as e:
        yield _sse_log(f"Error fetching repos: {str(e)}")

    if not repos:
        # Don't abort - continue with empty repos so tier calculation can properly classify
        # This is NOT an error - the org exists but has no public repos (or all filtered)
        yield _sse_log("No active repositories found. Organization may have private repos only.")
        repos = []  # Continue with empty list

    # Select top repos for deep scan
    # NEW HEURISTIC: Mega-Corp check to handle massive orgs (PostHog, etc)
    is_mega_corp = False
    total_stars_top_10 = sum(r.get('stargazers_count', 0) for r in repos[:10])
    public_repos_count = org_data.get('public_repos', 0)

    if public_repos_count > 200 or total_stars_top_10 > 5000:
        is_mega_corp = True
        yield _sse_log(f"⚠️ MEGA-CORP DETECTED: {org_login} has {public_repos_count} repos and {total_stars_top_10} stars in top 10.")
        yield _sse_log("Limiting scan to top 30 highest-value repositories to prevent timeout.")
        repos_to_scan = repos[:30]
    else:
        repos_to_scan = repos[:Config.MAX_REPOS_TO_SCAN]

    original_repos_to_scan = repos_to_scan.copy()  # Keep original list for tier calculation
    last_scanned_at = _parse_timestamp(last_scanned_timestamp)

    if last_scanned_at:
        filtered_repos = []
        for repo in repos_to_scan:
            repo_name = repo.get('name')
            pushed_at = _parse_timestamp(repo.get('pushed_at'))
            if pushed_at:
                if pushed_at.tzinfo and last_scanned_at.tzinfo is None:
                    last_scanned_at = last_scanned_at.replace(tzinfo=timezone.utc)
                elif last_scanned_at.tzinfo and pushed_at.tzinfo is None:
                    pushed_at = pushed_at.replace(tzinfo=last_scanned_at.tzinfo)
                if pushed_at <= last_scanned_at:
                    yield _sse_log(f"Skipping unchanged repo {repo_name}...")
                    continue
            filtered_repos.append(repo)
        repos_to_scan = filtered_repos

        # CRITICAL FIX: If all repos were filtered as "unchanged", fall back to original list
        # This prevents false "Disqualified" status when repos exist but haven't changed
        if not repos_to_scan and original_repos_to_scan:
            yield _sse_log("All repos unchanged since last scan, using original repo list for tier calculation")
            repos_to_scan = original_repos_to_scan

    yield _sse_log(f"Selected {len(repos_to_scan)} repositories for intent scan")

    # Initialize scan results with 3-Signal structure
    scan_results = {
        'company_name': company_name,
        'org_login': org_login,
        'org_name': org_name,
        'org_url': org_data.get('html_url', f'https://github.com/{org_login}'),
        'org_description': org_data.get('description'),
        'org_public_repos': org_data.get('public_repos'),
        'repos_scanned': [],
        'scan_timestamp': datetime.now().isoformat(),
        'total_stars': sum(r.get('stargazers_count', 0) for r in repos_to_scan),

        # 3-Signal Intent Results (plus documentation intent)
        'signals': [],  # List of structured signal objects
        'signal_summary': {
            'rfc_discussion': {
                'count': 0,
                'high_priority_count': 0,
                'hits': []
            },
            'dependency_injection': {
                'count': 0,
                'hits': []
            },
            'smoking_gun_fork': {
                'count': 0,
                'hits': []
            },
            'ghost_branch': {
                'count': 0,
                'hits': []
            },
            'documentation_intent': {
                'count': 0,
                'high_priority_count': 0,
                'hits': []
            }
        },
        'intent_score': 0,  # Calculated at the end
    }

    # Phase 2b: Smoking Gun Fork Detection
    # Check if the org has forked any known i18n libraries (uppy, react-intl, etc.)
    yield _sse_log("")
    yield _sse_log("PHASE 2b: Smoking Gun Fork Detection")
    yield _sse_log("-" * 40)
    yield _sse_log("Checking for forked i18n libraries (uppy, react-intl, i18next, etc.)...")

    for log_msg, signal in _scan_smoking_gun_forks(repos, company_name, org_login):
        if log_msg:
            yield _sse_log(f"  {log_msg}")
        if signal:
            scan_results['signals'].append(signal)
            scan_results['signal_summary']['smoking_gun_fork']['hits'].append(signal)
            scan_results['signal_summary']['smoking_gun_fork']['count'] += 1
            yield _sse_signal(signal)

    fork_count = scan_results['signal_summary']['smoking_gun_fork']['count']
    if fork_count > 0:
        yield _sse_log(f"Smoking Gun Fork detection complete: {fork_count} HIGH intent signals!")
    else:
        yield _sse_log("Smoking Gun Fork detection complete: No known i18n library forks found")

    # Phase 3: Signal 1 - RFC & Discussion Scan (Thinking Phase)
    yield _sse_log("")
    yield _sse_log("PHASE 3: RFC & Discussion Scan (Thinking Phase)")
    yield _sse_log("-" * 40)
    yield _sse_log("Scanning Issues and Discussions for high-intent keywords...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos for issues
        repo_name = repo.get('name')
        yield _sse_log(f"  [{idx}/5] Scanning issues in: {repo_name}")

        for log_msg, signal in _scan_rfc_discussion(org_login, repo_name, company_name, since_timestamp=last_scanned_at):
            if log_msg:
                yield _sse_log(f"    {log_msg}")
            if signal:
                scan_results['signals'].append(signal)
                scan_results['signal_summary']['rfc_discussion']['hits'].append(signal)
                scan_results['signal_summary']['rfc_discussion']['count'] += 1
                if signal.get('priority') == 'HIGH':
                    scan_results['signal_summary']['rfc_discussion']['high_priority_count'] += 1
                yield _sse_signal(signal)

    rfc_count = scan_results['signal_summary']['rfc_discussion']['count']
    high_priority = scan_results['signal_summary']['rfc_discussion']['high_priority_count']
    yield _sse_log(f"RFC & Discussion scan complete: {rfc_count} signals ({high_priority} HIGH priority)")

    # Phase 4: Signal 2 - Dependency Injection Scan (Preparing Phase)
    yield _sse_log("")
    yield _sse_log("PHASE 4: Dependency Injection Scan (Preparing Phase)")
    yield _sse_log("-" * 40)
    yield _sse_log("Checking for i18n libraries WITHOUT locale folders...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos
        repo_name = repo.get('name')
        is_fork = repo.get('fork', False)
        fork_indicator = " (fork)" if is_fork else ""
        yield _sse_log(f"  [{idx}/5] Scanning dependencies in: {repo_name}{fork_indicator}")

        for log_msg, signal in _scan_dependency_injection(org_login, repo_name, company_name, is_fork=is_fork):
            if log_msg:
                yield _sse_log(f"    {log_msg}")
            if signal:
                scan_results['signals'].append(signal)
                scan_results['signal_summary']['dependency_injection']['hits'].append(signal)
                scan_results['signal_summary']['dependency_injection']['count'] += 1
                yield _sse_signal(signal)

    dep_count = scan_results['signal_summary']['dependency_injection']['count']
    yield _sse_log(f"Dependency Injection scan complete: {dep_count} signals")

    # Phase 4b: Mobile Architecture Scan (iOS & Android Goldilocks)
    yield _sse_log("")
    yield _sse_log("PHASE 4b: Mobile Architecture Scan (iOS & Android)")
    yield _sse_log("-" * 40)
    yield _sse_log("Checking for mobile i18n infrastructure without translations...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos
        repo_name = repo.get('name')
        is_fork = repo.get('fork', False)
        fork_indicator = " (fork)" if is_fork else ""
        yield _sse_log(f"  [{idx}/5] Scanning mobile architecture in: {repo_name}{fork_indicator}")

        for log_msg, signal in _scan_mobile_architecture(org_login, repo_name, company_name, is_fork=is_fork):
            if log_msg:
                yield _sse_log(f"    {log_msg}")
            if signal:
                scan_results['signals'].append(signal)
                scan_results['signal_summary']['dependency_injection']['hits'].append(signal)
                scan_results['signal_summary']['dependency_injection']['count'] += 1
                yield _sse_signal(signal)

    mobile_count = scan_results['signal_summary']['dependency_injection']['count'] - dep_count
    yield _sse_log(f"Mobile Architecture scan complete: {mobile_count} signals")

    # Update dep_count to include mobile signals
    dep_count = scan_results['signal_summary']['dependency_injection']['count']

    # Phase 4c: Framework Configuration Scan (Preparing Phase)
    yield _sse_log("")
    yield _sse_log("PHASE 4c: Framework Configuration Scan")
    yield _sse_log("-" * 40)
    yield _sse_log("Checking for i18n routing config without translations...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos
        repo_name = repo.get('name')
        is_fork = repo.get('fork', False)
        fork_indicator = " (fork)" if is_fork else ""
        yield _sse_log(f"  [{idx}/5] Scanning framework configs in: {repo_name}{fork_indicator}")

        for log_msg, signal in _scan_framework_configs(org_login, repo_name, company_name, is_fork=is_fork):
            if log_msg:
                yield _sse_log(f"    {log_msg}")
            if signal:
                scan_results['signals'].append(signal)
                scan_results['signal_summary']['dependency_injection']['hits'].append(signal)
                scan_results['signal_summary']['dependency_injection']['count'] += 1
                yield _sse_signal(signal)

    framework_count = scan_results['signal_summary']['dependency_injection']['count'] - dep_count
    yield _sse_log(f"Framework Configuration scan complete: {framework_count} signals")

    # Update dep_count to include framework config signals
    dep_count = scan_results['signal_summary']['dependency_injection']['count']

    # Phase 4d: Documentation Intent Scan (Thinking Phase)
    yield _sse_log("")
    yield _sse_log("PHASE 4d: Documentation Intent Scan")
    yield _sse_log("-" * 40)
    yield _sse_log("Checking documentation for i18n intent signals...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos
        repo_name = repo.get('name')
        yield _sse_log(f"  [{idx}/5] Scanning documentation in: {repo_name}")

        for log_msg, signal in _scan_documentation_files(org_login, repo_name, company_name):
            if log_msg:
                yield _sse_log(f"    {log_msg}")
            if signal:
                scan_results['signals'].append(signal)
                scan_results['signal_summary']['documentation_intent']['hits'].append(signal)
                scan_results['signal_summary']['documentation_intent']['count'] += 1
                if signal.get('priority') == 'HIGH':
                    scan_results['signal_summary']['documentation_intent']['high_priority_count'] += 1
                yield _sse_signal(signal)

    doc_count = scan_results['signal_summary']['documentation_intent']['count']
    doc_high = scan_results['signal_summary']['documentation_intent']['high_priority_count']
    yield _sse_log(f"Documentation Intent scan complete: {doc_count} signals ({doc_high} HIGH priority)")

    # Phase 5: Signal 3 - Ghost Branch Scan (Active Phase)
    yield _sse_log("")
    yield _sse_log("PHASE 5: Ghost Branch Scan (Active Phase)")
    yield _sse_log("-" * 40)
    yield _sse_log("Scanning for WIP i18n branches and unmerged PRs...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos
        repo_name = repo.get('name')
        yield _sse_log(f"  [{idx}/5] Scanning branches in: {repo_name}")

        for log_msg, signal in _scan_ghost_branches(org_login, repo_name, company_name, since_timestamp=last_scanned_at):
            if log_msg:
                yield _sse_log(f"    {log_msg}")
            if signal:
                scan_results['signals'].append(signal)
                scan_results['signal_summary']['ghost_branch']['hits'].append(signal)
                scan_results['signal_summary']['ghost_branch']['count'] += 1
                yield _sse_signal(signal)

    ghost_count = scan_results['signal_summary']['ghost_branch']['count']
    yield _sse_log(f"Ghost Branch scan complete: {ghost_count} signals")

    # Store repo metadata
    for repo in repos_to_scan:
        scan_results['repos_scanned'].append({
            'name': repo.get('name'),
            'full_name': repo.get('full_name'),
            'url': repo.get('html_url'),
            'stars': repo.get('stargazers_count', 0),
            'language': repo.get('language'),
        })

    # Phase 6: Intent Score Calculation
    yield _sse_log("")
    yield _sse_log("PHASE 6: Intent Score Calculation")
    yield _sse_log("-" * 40)

    scan_results['intent_score'] = _calculate_intent_score(scan_results)

    # Summary
    yield _sse_log("")
    yield _sse_log("=" * 60)
    yield _sse_log("3-SIGNAL INTENT SCAN COMPLETE")
    yield _sse_log("=" * 60)

    duration = (datetime.now() - start_time).total_seconds()
    scan_results['scan_duration_seconds'] = duration

    total_signals = len(scan_results['signals'])
    yield _sse_log(f"Total Signals Detected: {total_signals}")
    yield _sse_log(f"   • RFC & Discussion: {rfc_count} ({high_priority} HIGH)")
    yield _sse_log(f"   • Dependency Injection: {dep_count}")
    yield _sse_log(f"   • Documentation Intent: {doc_count} ({doc_high} HIGH)")
    yield _sse_log(f"   • Ghost Branches: {ghost_count}")
    yield _sse_log(f"Intent Score: {scan_results['intent_score']}/100")
    yield _sse_log(f"Scan Duration: {duration:.1f}s")

    if total_signals > 0:
        yield _sse_log("")
        yield _sse_log("INTENT DETECTED: Company is in Thinking/Preparing phase!")
    else:
        yield _sse_log("")
        yield _sse_log("⚪ No pre-launch signals detected.")

    yield _sse_log("")
    yield _sse_log("Generating AI Sales Intelligence...")

    # Track stats - increment scans_run and estimate API calls
    try:
        increment_daily_stat('scans_run', 1)
        # Estimate API calls: ~3-5 per repo scanned (issues, branches, files)
        api_calls_estimate = len(repos_to_scan) * 4
        increment_daily_stat('api_calls_estimated', api_calls_estimate)
    except Exception as e:
        pass  # Stats tracking should not break scans

    # Send scan results
    yield _sse_data('SCAN_COMPLETE', scan_results)


def _scan_rfc_discussion(org: str, repo: str, company: str, since_timestamp: datetime = None) -> Generator[tuple, None, None]:
    """
    Signal 1: RFC & Discussion Scan (Thinking Phase)

    Target: Issues and Discussions (Open & Closed)
    Logic: Flag if title or body contains high-intent keywords in last 6 months (or since last scan)
    Keywords: 'i18n strategy', 'localization support', 'handle timezones',
              'currency formatting', 'RTL support', 'translation workflow', 'multi-currency',
              'internationalization', 'translate', 'global expansion'
    Priority: HIGH if title starts with 'RFC' or 'Proposal'

    Yields:
        Tuples of (log_message, signal_object)
    """
    if since_timestamp:
        cutoff_date = since_timestamp
    else:
        cutoff_date = datetime.now() - timedelta(days=Config.RFC_LOOKBACK_DAYS)
    
    cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Scan Issues
    try:
        url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/issues"
        params = {
            'state': 'all',
            'since': cutoff_str,
            'per_page': 100,
            'sort': 'created',
            'direction': 'desc'
        }

        response = make_github_request(url, params=params, timeout=30)

        if response.status_code == 200:
            issues = response.json()

            for issue in issues:
                # Skip pull requests (they appear in issues API)
                if issue.get('pull_request'):
                    continue

                title = issue.get('title', '')
                body = issue.get('body', '') or ''
                issue_number = issue.get('number')
                issue_url = issue.get('html_url')

                # Check for high-intent keywords
                text_to_check = f"{title} {body}".lower()
                matched_keywords = []

                for keyword in Config.RFC_KEYWORDS:
                    if keyword.lower() in text_to_check:
                        matched_keywords.append(keyword)

                if matched_keywords:
                    # Determine priority
                    title_upper = title.upper()
                    is_high_priority = (
                        title_upper.startswith('RFC') or
                        title_upper.startswith('PROPOSAL') or
                        title_upper.startswith('[RFC]') or
                        title_upper.startswith('[PROPOSAL]')
                    )

                    signal = {
                        'Company': company,
                        'Signal': 'RFC & Discussion',
                        'Evidence': f"Issue #{issue_number}: '{title[:60]}...' contains: {', '.join(matched_keywords)}",
                        'Link': issue_url,
                        'priority': 'HIGH' if is_high_priority else 'MEDIUM',
                        'type': 'rfc_discussion',
                        'repo': repo,
                        'issue_number': issue_number,
                        'title': title,
                        'keywords_matched': matched_keywords,
                        'state': issue.get('state'),
                        'created_at': issue.get('created_at'),
                    }

                    priority_label = "HIGH" if is_high_priority else "MEDIUM"
                    yield (f"{priority_label}: Issue #{issue_number} - {title[:40]}...", signal)

    except requests.RequestException as e:
        error_detail = _format_request_exception(e)
        yield (f"Error scanning issues: {error_detail}", None)

    # Scan Discussions (if available via GraphQL - simplified to REST search)
    try:
        # Use search API to find discussions mentioning keywords
        for keyword in Config.RFC_KEYWORDS[:3]:  # Top 3 keywords to limit API calls
            search_url = f"{Config.GITHUB_API_BASE}/search/issues"
            params = {
                'q': f'repo:{org}/{repo} "{keyword}" in:title,body',
                'per_page': 10,
                'sort': 'created',
                'order': 'desc'
            }

            response = make_github_request(search_url, params=params, timeout=15)

            if response.status_code == 200:
                results = response.json().get('items', [])
                for item in results:
                    # Skip if already processed or too old
                    created_at = item.get('created_at', '')
                    if created_at < cutoff_str:
                        continue

                    title = item.get('title', '')
                    issue_url = item.get('html_url')
                    issue_number = item.get('number')

                    # Check if this is a discussion (URL contains /discussions/)
                    is_discussion = '/discussions/' in issue_url

                    if is_discussion:
                        title_upper = title.upper()
                        is_high_priority = (
                            title_upper.startswith('RFC') or
                            title_upper.startswith('PROPOSAL')
                        )

                        signal = {
                            'Company': company,
                            'Signal': 'RFC & Discussion',
                            'Evidence': f"Discussion: '{title[:60]}...' mentions '{keyword}'",
                            'Link': issue_url,
                            'priority': 'HIGH' if is_high_priority else 'MEDIUM',
                            'type': 'rfc_discussion',
                            'repo': repo,
                            'title': title,
                            'keywords_matched': [keyword],
                            'is_discussion': True,
                            'created_at': created_at,
                        }

                        priority_label = "HIGH" if is_high_priority else "MEDIUM"
                        yield (f"{priority_label}: Discussion - {title[:40]}...", signal)

    except requests.RequestException:
        pass  # Search API failures are non-fatal


def _scan_smoking_gun_forks(repos: list, company: str, org_login: str) -> Generator[tuple, None, None]:
    """
    Detect when a company has forked a known i18n-related library.
    
    This is a HIGH intent signal - forking uppy/react-intl/i18next/etc.
    indicates the company is actively customizing i18n infrastructure.
    This is even stronger than just listing a dependency because they're
    modifying the source code.
    
    Args:
        repos: List of repository data dicts from the organization.
        company: Company name for signal records.
        org_login: GitHub organization login.
        
    Yields:
        Tuple of (log_message, signal_dict or None)
    """
    smoking_gun_repos = [r.lower() for r in Config.SMOKING_GUN_FORK_REPOS]
    
    for repo in repos:
        if not repo.get('fork', False):
            continue
            
        repo_name = repo.get('name', '')
        repo_name_lower = repo_name.lower()
        
        if repo_name_lower in smoking_gun_repos:
            # This is a "Smoking Gun" fork!
            repo_html_url = repo.get('html_url', f"https://github.com/{org_login}/{repo_name}")
            repo_description = repo.get('description', 'No description')
            pushed_at = repo.get('pushed_at', 'Unknown')
            
            # Get library-specific messaging from existing config
            lib_desc = Config.I18N_LIBRARIES.get(repo_name_lower, 'i18n library')
            lib_meaning = Config.BDR_TRANSLATIONS.get(repo_name_lower, 'i18n infrastructure is being prepared')
            
            signal = {
                'Company': company,
                'Signal': 'Smoking Gun Fork',
                'Evidence': f"Company forked '{repo_name}' ({lib_desc}). {lib_meaning}",
                'Link': repo_html_url,
                'priority': 'HIGH',
                'type': 'smoking_gun_fork',
                'goldilocks_status': 'preparing',
                'repo': repo_name,
                'description': repo_description,
                'pushed_at': pushed_at,
                'libraries_found': [repo_name_lower],
                'bdr_summary': f"Forked {repo_name} library - signals active i18n customization work",
            }
            
            yield (f"🎯 SMOKING GUN FORK: '{repo_name}' - known i18n library!", signal)


def _scan_dependency_injection(org: str, repo: str, company: str, is_fork: bool = False) -> Generator[tuple, None, None]:
    """
    Signal 2: Dependency Injection Scan (Preparing Phase) - GOLDILOCKS ZONE DETECTION

    Target: package.json, Gemfile (focusing on our 4 target libraries)
    Logic: Flag if SMOKING GUN i18n libraries are present

    THE "GAP" REQUIREMENT (Negative Check):
    - A signal is ONLY valid if the repository has NO localization folders
    - If /locales, /i18n, /translations, or /lang exist -> DISQUALIFY (Already Launched)
    - EXCEPTION: If is_fork=True, skip the negative check (fork's translations belong to upstream)

    TARGET LIBRARIES:
    - babel-plugin-react-intl
    - react-i18next
    - formatjs
    - uppy (only if i18n/locale properties are configured)

    Args:
        org: GitHub organization login
        repo: Repository name
        company: Company name for signal attribution
        is_fork: If True, skip locale folder checks (fork translations belong to upstream)

    Yields:
        Tuples of (log_message, signal_object)
    """
    for log_msg, signal in _scan_pseudo_localization_configs(org, repo, company):
        yield (log_msg, signal)

    # ============================================================
    # NEGATIVE CHECK: Verify NO exclusion folders exist (with source-only exception)
    # FORK EXCEPTION: Skip this check for forks - their translations belong to upstream
    # ============================================================
    locale_exists = False
    found_folders = []
    source_only = False
    folder_contents = {}
    source_only_evidence = None

    if is_fork:
        # Skip locale folder check for forks - translations belong to upstream project
        yield (f"FORK DETECTED: Skipping locale folder check (upstream translations don't disqualify)", None)
    else:
        locale_exists, found_folders, source_only, folder_contents = _check_locale_folders_exist_detailed(org, repo)

    if locale_exists:
        if source_only:
            # GOLDILOCKS ZONE EXCEPTION: Folder exists but ONLY contains source files
            # This means infrastructure is ready, but no translations yet!
            # Format the files for logging
            all_files = []
            for folder, files in folder_contents.items():
                all_files.extend(files)
            files_str = ', '.join(all_files) if all_files else 'empty'
            folders_str = ', '.join(found_folders)

            source_only_evidence = f"Found locale folder ({folders_str}) but it only contains source files ({files_str}) - Infrastructure ready, waiting for translation."
            yield (f"GOLDILOCKS: {folders_str} contains only source files ({files_str}) - Still a valid lead!", None)
            # Continue to positive check - don't return!
        else:
            # Company has ALREADY LAUNCHED - mark as "Too Late"
            yield (f"DISQUALIFIED: Found locale folders ({', '.join(found_folders)}) - Already Launched", None)

            # Still emit a signal but mark it as "launched" status
            signal = {
                'Company': company,
                'Signal': 'Already Launched',
                'Evidence': f"Found localization folders: {', '.join(found_folders)} - Company has already launched i18n",
                'Link': f"https://github.com/{org}/{repo}",
                'priority': 'LOW',
                'type': 'already_launched',
                'repo': repo,
                'locale_folders_found': found_folders,
                'goldilocks_status': 'launched',
                'bdr_summary': Config.BDR_TRANSLATIONS.get('locale_folder_exists', 'Already has translations'),
            }
            yield (f"LOW PRIORITY: {repo} already has locale folders", signal)
            return

    # ============================================================
    # POSITIVE CHECK: Scan for Goldilocks Zone libraries
    # ============================================================
    # Using Search API instead of recursive tree fetching to handle "Mega-Corp" repos efficiently
    for dep_file in Config.DEPENDENCY_INJECTION_FILES:
        yield ("Scanning dependencies...", None)

        # Query: q=repo:{org}/{repo} filename:{dep_filename}
        query = f"repo:{org}/{repo} filename:{dep_file}"
        search_url = f"{Config.GITHUB_API_BASE}/search/code"

        dep_paths = []
        try:
            # Note: make_github_request handles rate limits and priority
            search_response = make_github_request(search_url, params={'q': query}, timeout=15)

            if search_response.status_code == 200:
                search_data = search_response.json()
                items = search_data.get('items', [])
                # Limit to top 20 matches per file type (most common in monorepos)
                dep_paths = [item.get('path') for item in items[:20]]
            elif search_response.status_code == 422:
                # Search sometimes fails on empty repos or if indexing is incomplete
                dep_paths = [dep_file]
            else:
                # Fallback to root for other errors
                dep_paths = [dep_file]
        except Exception:
            dep_paths = [dep_file]

        if not dep_paths:
            continue

        for dep_path in dep_paths:
            try:
                url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{dep_path}"
                response = make_github_request(url, timeout=15)

                if response.status_code != 200:
                    continue

                file_data = response.json()
                content_b64 = file_data.get('content', '')
                file_url = file_data.get('html_url')

                if content_b64:
                    try:
                        content = base64.b64decode(content_b64).decode('utf-8')
                        content_lower = content.lower()

                        if dep_file == 'package.json':
                            try:
                                package_json = json.loads(content)
                            except json.JSONDecodeError:
                                package_json = {}

                            scripts = package_json.get('scripts', {})
                            if isinstance(scripts, dict):
                                for script_name, script_command in scripts.items():
                                    script_name_text = str(script_name)
                                    script_command_text = str(script_command)
                                    script_name_lower = script_name_text.lower()
                                    script_command_lower = script_command_text.lower()

                                    matched_keyword = next(
                                        (
                                            keyword for keyword in Config.I18N_SCRIPT_KEYWORDS
                                            if keyword in script_name_lower or keyword in script_command_lower
                                        ),
                                        None
                                    )

                                    if matched_keyword:
                                        signal = {
                                            'Company': company,
                                            'Signal': 'NPM Script',
                                            'Evidence': (
                                                f"Found automation script '{script_name_text}' in package.json - "
                                                "Team is building translation pipeline."
                                            ),
                                            'Link': file_url,
                                            'priority': 'MEDIUM',
                                            'type': 'npm_script',
                                            'repo': repo,
                                            'file': dep_path,
                                            'script_name': script_name_text,
                                            'script_command': script_command_text,
                                            'keyword_matched': matched_keyword,
                                            'goldilocks_status': 'preparing',
                                        }
                                        yield (f"NPM SCRIPT: {script_name_text} in {dep_path}", signal)

                        found_libs = []
                        bdr_explanations = []
                        found_linter_libs = []
                        found_cms_i18n_libs = []

                        # Check for our 4 target SMOKING GUN libraries
                        for lib in Config.SMOKING_GUN_LIBS:
                            # --- FIX START: Prevent 'babel' false positives in JS files ---
                            # Only detect 'babel' in Python dependency files
                            if lib == 'babel' and dep_file not in ['requirements.txt', 'pyproject.toml', 'setup.py', 'Pipfile']:
                                continue
                            # --- FIX END ---

                            # Context-aware matching:
                            # strict quotes for JSON/Lockfiles, loose matching for others
                            is_strict_file = dep_file in ['package.json', 'composer.json', 'package-lock.json']

                            found = False
                            if is_strict_file:
                                if f'"{lib}"' in content_lower or f"'{lib}'" in content_lower:
                                    found = True
                            else:
                                if lib in content_lower:
                                    found = True

                            if found:
                                found_libs.append(lib)
                                bdr_explanations.append(Config.BDR_TRANSLATIONS.get(lib, f'Found {lib}'))

                        # Special handling for Uppy - only count if i18n config is present
                        if Config.UPPY_LIBRARY in content_lower:
                            # Check if any i18n indicators are present
                            for indicator in Config.UPPY_I18N_INDICATORS:
                                if indicator in content_lower:
                                    found_libs.append(f"{Config.UPPY_LIBRARY} (with {indicator} config)")
                                    bdr_explanations.append(Config.BDR_TRANSLATIONS.get('uppy', 'Uppy with i18n'))
                                    break

                        for lib in Config.LINTER_LIBRARIES:
                            if f'"{lib}"' in content_lower or f"'{lib}'" in content_lower:
                                found_linter_libs.append(lib)

                        for lib in Config.CMS_I18N_LIBS:
                            if f'"{lib}"' in content_lower or f"'{lib}'" in content_lower:
                                found_cms_i18n_libs.append(lib)

                        if found_libs:
                            # This is the GOLDILOCKS ZONE - Library found + NO locale folders (or source-only)!
                            if source_only_evidence:
                                # Source-only case: folder exists but only has source files
                                evidence = f"GOLDILOCKS ZONE: Found {', '.join(found_libs)} in {dep_path}. {source_only_evidence}"
                                gap_explanation = Config.BDR_TRANSLATIONS.get('locale_folder_source_only', 'Infrastructure ready, only source files')
                            else:
                                # No folder case: no locale folders at all
                                evidence = f"GOLDILOCKS ZONE: Found {', '.join(found_libs)} in {dep_path} but NO locale folders exist!"
                                gap_explanation = Config.BDR_TRANSLATIONS.get('locale_folder_missing', 'Infrastructure ready, no translations')

                            signal = {
                                'Company': company,
                                'Signal': 'Dependency Injection',
                                'Evidence': evidence,
                                'Link': file_url,
                                'priority': 'CRITICAL',
                                'type': 'dependency_injection',
                                'repo': repo,
                                'file': dep_path,
                                'libraries_found': found_libs,
                                'goldilocks_status': 'preparing',
                                'gap_verified': True,  # Negative check passed!
                                'source_only': source_only_evidence is not None,
                                'bdr_summary': ' | '.join(bdr_explanations),
                                'bdr_gap_explanation': gap_explanation,
                            }

                            yield (f"GOLDILOCKS ZONE: {', '.join(found_libs)} in {dep_path} - NO TRANSLATIONS YET!", signal)

                        for lib in found_linter_libs:
                            signal = {
                                'Company': company,
                                'Signal': 'Linter Config',
                                'Evidence': (
                                    f"Found Code Cleaning tool '{lib}' - Team is scrubbing hardcoded strings to "
                                    "prepare for i18n."
                                ),
                                'Link': file_url,
                                'priority': 'MEDIUM',
                                'type': 'linter_config',
                                'repo': repo,
                                'file': dep_path,
                                'library_found': lib,
                                'goldilocks_status': 'preparing',
                            }

                            yield (f"CODE CLEANING: {lib} found in {dep_path}", signal)

                        for lib in found_cms_i18n_libs:
                            signal = {
                                'Company': company,
                                'Signal': 'CMS Localization',
                                'Evidence': (
                                    f"Found CMS Localization tool '{lib}' - Preparing content strategy for "
                                    "internationalization."
                                ),
                                'Link': file_url,
                                'priority': 'MEDIUM',
                                'type': 'cms_config',
                                'repo': repo,
                                'file': dep_path,
                                'library_found': lib,
                                'goldilocks_status': 'preparing',
                            }

                            yield (f"CMS LOCALIZATION: {lib} found in {dep_path}", signal)

                    except Exception:
                        pass

            except requests.RequestException:
                continue


def _scan_pseudo_localization_configs(org: str, repo: str, company: str) -> Generator[tuple, None, None]:
    """
    Scan config files for pseudo-localization patterns.
    """
    config_files = ['next.config.js', 'nuxt.config.js', 'i18n.config.js']

    for config_file in config_files:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{config_file}"
            response = make_github_request(url, timeout=15)

            if response.status_code != 200:
                continue

            file_data = response.json()
            content_b64 = file_data.get('content', '')
            file_url = file_data.get('html_url')

            if not content_b64:
                continue

            try:
                content = base64.b64decode(content_b64).decode('utf-8')
            except Exception:
                continue

            content_lower = content.lower()

            for pattern in Config.PSEUDO_CONFIG_PATTERNS:
                pattern_lower = pattern.lower()
                if pattern_lower in content_lower:
                    signal = {
                        'Company': company,
                        'Signal': 'Pseudo-Localization',
                        'Evidence': f"Found pseudo-localization config '{pattern}' in {config_file} - Testing layout for i18n.",
                        'Link': file_url,
                        'priority': 'HIGH',
                        'type': 'pseudo_localization',
                        'repo': repo,
                        'file': config_file,
                        'pattern': pattern,
                    }
                    yield (f"PSEUDO-LOCALIZATION: {pattern} found in {config_file}", signal)

        except requests.RequestException:
            continue


def _scan_mobile_architecture(org: str, repo: str, company: str, is_fork: bool = False) -> Generator[tuple, None, None]:
    """
    Scan for Mobile Goldilocks Zone signals (iOS and Android).

    iOS Logic:
        - Check if Base.lproj folder exists
        - If YES, search for ANY other folders ending in .lproj
        - Signal if Base.lproj exists AND count of other .lproj folders == 0
        - EXCEPTION: If is_fork=True, skip translation checks (fork translations belong to upstream)

    Android Logic:
        - Check if res/values/strings.xml exists
        - If YES, search for ANY folders matching values-[a-z]{2}
        - Signal if strings.xml exists AND count of values-* folders == 0
        - EXCEPTION: If is_fork=True, skip translation checks (fork translations belong to upstream)

    Args:
        org: GitHub organization login
        repo: Repository name
        company: Company name for signal attribution
        is_fork: If True, skip translation folder checks (fork translations belong to upstream)

    Yields:
        Tuples of (log_message, signal_object)
    """
    # ============================================================
    # iOS DETECTION: Base.lproj without translations
    # ============================================================
    ios_indicator = Config.MOBILE_INDICATORS.get('ios', {})
    base_lproj_path = ios_indicator.get('path', 'Base.lproj')

    try:
        # Search for Base.lproj folder anywhere in the repo
        search_url = f"{Config.GITHUB_API_BASE}/search/code"
        params = {
            'q': f'repo:{org}/{repo} path:**/{base_lproj_path}',
            'per_page': 5
        }

        response = make_github_request(search_url, params=params, timeout=15)

        base_lproj_found = False
        base_lproj_parent = None

        if response.status_code == 200:
            results = response.json().get('items', [])
            for item in results:
                path = item.get('path', '')
                # Check if this is inside a Base.lproj folder
                if '/Base.lproj/' in path or path.startswith('Base.lproj/'):
                    base_lproj_found = True
                    # Extract parent directory for searching siblings
                    if '/Base.lproj/' in path:
                        base_lproj_parent = path.split('/Base.lproj/')[0]
                    else:
                        base_lproj_parent = ''
                    break

        if base_lproj_found:
            yield (f"iOS: Found Base.lproj folder", None)

            # Search for other .lproj folders (translations)
            # FORK EXCEPTION: Skip translation check for forks - translations belong to upstream
            other_lproj_count = 0

            if is_fork:
                yield (f"iOS FORK: Skipping translation check (upstream translations don't disqualify)", None)
            else:
                lproj_search_url = f"{Config.GITHUB_API_BASE}/search/code"
                lproj_params = {
                    'q': f'repo:{org}/{repo} path:*.lproj',
                    'per_page': 50
                }

                lproj_response = make_github_request(lproj_search_url, params=lproj_params, timeout=15)

                if lproj_response.status_code == 200:
                    lproj_results = lproj_response.json().get('items', [])
                    seen_folders = set()

                    for item in lproj_results:
                        path = item.get('path', '')
                        # Extract .lproj folder name from path
                        lproj_match = re.search(r'/([^/]+\.lproj)/', path)
                        if lproj_match:
                            folder_name = lproj_match.group(1)
                            if folder_name != 'Base.lproj' and folder_name not in seen_folders:
                                seen_folders.add(folder_name)
                                other_lproj_count += 1

            if other_lproj_count == 0 or is_fork:
                # GOLDILOCKS ZONE: Base.lproj exists but no translations (or fork - ignore upstream translations)!
                evidence = "iOS GOLDILOCKS: Base.lproj exists but NO other .lproj folders found - iOS app ready for localization!"
                if is_fork:
                    evidence = "iOS GOLDILOCKS (FORK): Base.lproj exists - fork's upstream translations ignored, treating as preparing!"
                signal = {
                    'Company': company,
                    'Signal': 'Mobile Architecture (iOS)',
                    'Evidence': evidence,
                    'Link': f"https://github.com/{org}/{repo}",
                    'priority': 'CRITICAL',
                    'type': 'mobile_architecture',
                    'platform': 'ios',
                    'repo': repo,
                    'goldilocks_status': 'preparing',
                    'gap_verified': True,
                    'is_fork': is_fork,
                    'bdr_summary': 'iOS Base Architecture ready, no translations',
                }
                yield (f"iOS GOLDILOCKS: Base.lproj found, no translations yet!", signal)
            else:
                yield (f"iOS: Found {other_lproj_count} translation .lproj folders - Already localized", None)

    except requests.RequestException as e:
        error_detail = _format_request_exception(e)
        yield (f"iOS scan error: {error_detail}", None)

    # ============================================================
    # ANDROID DETECTION: strings.xml without translations
    # ============================================================
    android_indicator = Config.MOBILE_INDICATORS.get('android', {})
    strings_xml_path = android_indicator.get('path', 'res/values/strings.xml')

    try:
        # Check if res/values/strings.xml exists
        url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{strings_xml_path}"
        response = make_github_request(url, timeout=15)

        if response.status_code == 200:
            yield (f"Android: Found {strings_xml_path}", None)

            # Search for values-XX folders (language variants)
            # FORK EXCEPTION: Skip translation check for forks - translations belong to upstream
            values_folder_count = 0

            if is_fork:
                yield (f"Android FORK: Skipping translation check (upstream translations don't disqualify)", None)
            else:
                # Get contents of res/ folder to check for values-* folders
                res_url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/res"
                res_response = make_github_request(res_url, timeout=15)

                if res_response.status_code == 200:
                    res_contents = res_response.json()
                    if isinstance(res_contents, list):
                        for item in res_contents:
                            item_name = item.get('name', '')
                            item_type = item.get('type', '')
                            # Match values-XX pattern (e.g., values-fr, values-de)
                            if item_type == 'dir' and re.match(r'^values-[a-z]{2}(-[a-zA-Z]+)?$', item_name):
                                values_folder_count += 1

            if values_folder_count == 0 or is_fork:
                # GOLDILOCKS ZONE: strings.xml exists but no translations (or fork - ignore upstream translations)!
                evidence = "Android GOLDILOCKS: res/values/strings.xml exists but NO values-* folders found - Android app ready for localization!"
                if is_fork:
                    evidence = "Android GOLDILOCKS (FORK): res/values/strings.xml exists - fork's upstream translations ignored, treating as preparing!"
                signal = {
                    'Company': company,
                    'Signal': 'Mobile Architecture (Android)',
                    'Evidence': evidence,
                    'Link': f"https://github.com/{org}/{repo}",
                    'priority': 'CRITICAL',
                    'type': 'mobile_architecture',
                    'platform': 'android',
                    'repo': repo,
                    'goldilocks_status': 'preparing',
                    'gap_verified': True,
                    'is_fork': is_fork,
                    'bdr_summary': 'Android Strings architecture ready, no translations',
                }
                yield (f"Android GOLDILOCKS: strings.xml found, no translations yet!", signal)
            else:
                yield (f"Android: Found {values_folder_count} translation values-* folders - Already localized", None)

    except requests.RequestException as e:
        error_detail = _format_request_exception(e)
        yield (f"Android scan error: {error_detail}", None)


def _scan_framework_configs(org: str, repo: str, company: str, is_fork: bool = False) -> Generator[tuple, None, None]:
    """
    Scan framework configuration files for i18n routing configuration.

    Detects when developers have enabled i18n routing in their framework config
    before adding translation files - a key "Preparing" phase signal.

    Target files: next.config.js, nuxt.config.js/ts, remix.config.js, angular.json
    Logic: Look for active i18n configuration blocks while ignoring comments.
    Gap Requirement: Only signal if locale folders don't exist.

    Args:
        org: GitHub organization login
        repo: Repository name
        company: Company name for signal attribution
        is_fork: If True, skip locale folder checks (fork translations belong to upstream)

    Yields:
        Tuples of (log_message, signal_object)
    """
    # ============================================================
    # NEGATIVE CHECK: Verify NO locale folders exist (with source-only exception)
    # FORK EXCEPTION: Skip this check for forks - their translations belong to upstream
    # ============================================================
    locale_exists = False
    found_folders = []
    source_only = False
    folder_contents = {}

    if is_fork:
        # Skip locale folder check for forks - translations belong to upstream project
        yield (f"FORK DETECTED: Skipping locale folder check (upstream translations don't disqualify)", None)
    else:
        locale_exists, found_folders, source_only, folder_contents = _check_locale_folders_exist_detailed(org, repo)

    if locale_exists:
        if source_only:
            # SOURCE-ONLY EXCEPTION: Folder exists but ONLY contains source files (en.json, etc.)
            # This means infrastructure is ready, but no translations yet - still a valid lead!
            all_files = []
            for folder, files in folder_contents.items():
                all_files.extend(files)
            files_str = ', '.join(all_files) if all_files else 'empty'
            folders_str = ', '.join(found_folders)
            yield (f"Found locale folder ({folders_str}), but it appears to be source-only ({files_str}) - marking as PREPARING, not LAUNCHED", None)
            # Continue to positive check - don't return!
        else:
            # Locale folders with actual translations exist - skip framework config scan
            yield (f"Framework config scan skipped - locale folders already exist ({', '.join(found_folders)})", None)
            return

    # ============================================================
    # POSITIVE CHECK: Scan framework config files for i18n patterns
    # ============================================================
    # Patterns to detect active i18n configuration
    i18n_patterns = [
        # Next.js / general JS configs
        r'i18n\s*:\s*\{',           # i18n: {
        r"i18n\s*:\s*\[",           # i18n: [
        # Nuxt.js module
        r"modules\s*:\s*\[[^\]]*['\"]@nuxtjs/i18n['\"]",  # modules: ['@nuxtjs/i18n']
        r"modules\s*:\s*\[[^\]]*['\"]nuxt-i18n['\"]",     # modules: ['nuxt-i18n']
        # JSON format (angular.json)
        r'"i18n"\s*:\s*\{',          # "i18n": {
        # Remix i18n
        r'i18next',                  # i18next references in config
    ]

    for config_file in Config.FRAMEWORK_CONFIG_FILES:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{config_file}"
            response = make_github_request(url, timeout=15)

            if response.status_code != 200:
                continue

            file_data = response.json()
            content_b64 = file_data.get('content', '')
            file_url = file_data.get('html_url')

            if not content_b64:
                continue

            try:
                content = base64.b64decode(content_b64).decode('utf-8')
            except Exception:
                continue

            # Remove comments before checking for patterns
            cleaned_content = _strip_comments(content)

            # Check for i18n configuration patterns
            for pattern in i18n_patterns:
                if re.search(pattern, cleaned_content, re.IGNORECASE):
                    signal = {
                        'Company': company,
                        'Signal': 'Framework Config',
                        'Evidence': f"Found active i18n configuration in {config_file} - Routing is enabled but translations are missing.",
                        'Link': file_url,
                        'priority': 'HIGH',
                        'type': 'framework_config',
                        'repo': repo,
                        'file': config_file,
                        'pattern_matched': pattern,
                        'goldilocks_status': 'preparing',
                        'gap_verified': True,
                        'bdr_summary': f'i18n routing enabled in {config_file}, no translations yet',
                    }
                    yield (f"FRAMEWORK CONFIG: i18n routing found in {config_file}", signal)
                    break  # Only one signal per file

        except requests.RequestException:
            continue


def _scan_documentation_files(org: str, repo: str, company: str) -> Generator[tuple, None, None]:
    """
    Signal 4: Documentation Intent Scan (Thinking Phase)

    Target: CHANGELOG.md, CONTRIBUTING.md, README.md, ROADMAP.md
    Logic: Flag if i18n keywords are found NEAR context words indicating
           future or in-progress work (e.g., "beta", "roadmap", "upcoming")

    This catches companies mentioning i18n in their documentation before
    the code is fully live - an early intent signal.

    Gap Requirement: Also checks for "launched" indicators to avoid
    false positives on companies that already have i18n.

    Yields:
        Tuples of (log_message, signal_object)
    """
    for doc_file in Config.DOCUMENTATION_FILES:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{doc_file}"
            response = make_github_request(url, timeout=15)

            if response.status_code != 200:
                continue

            file_data = response.json()
            content_b64 = file_data.get('content', '')
            file_url = file_data.get('html_url')

            if not content_b64:
                continue

            try:
                content = base64.b64decode(content_b64).decode('utf-8')
            except Exception:
                continue

            content_lower = content.lower()

            # Determine file priority based on filename
            file_basename = doc_file.lower().replace('.md', '')
            file_priority = Config.DOCUMENTATION_FILE_WEIGHTS.get(file_basename, 'MEDIUM')

            # Check for "already launched" indicators first (negative check)
            has_launched_indicator = False
            for indicator in Config.DOCUMENTATION_LAUNCHED_INDICATORS:
                if indicator.lower() in content_lower:
                    has_launched_indicator = True
                    break

            if has_launched_indicator:
                yield (f"Skipping {doc_file} - contains launched indicators", None)
                continue

            # Search for intent keywords
            for keyword in Config.DOCUMENTATION_INTENT_KEYWORDS:
                keyword_lower = keyword.lower()
                keyword_pos = content_lower.find(keyword_lower)

                while keyword_pos != -1:
                    # Extract context window around the keyword
                    window_start = max(0, keyword_pos - Config.DOCUMENTATION_PROXIMITY_CHARS)
                    window_end = min(len(content_lower), keyword_pos + len(keyword_lower) + Config.DOCUMENTATION_PROXIMITY_CHARS)
                    context_window = content_lower[window_start:window_end]

                    # Check if any context keyword is within the proximity window
                    matched_context = None
                    for context_kw in Config.DOCUMENTATION_CONTEXT_KEYWORDS:
                        if context_kw.lower() in context_window:
                            matched_context = context_kw
                            break

                    if matched_context:
                        # Extract the actual line containing the keyword for evidence
                        line_start = content.rfind('\n', 0, keyword_pos) + 1
                        line_end = content.find('\n', keyword_pos)
                        if line_end == -1:
                            line_end = len(content)
                        matched_line = content[line_start:line_end].strip()

                        # Truncate long lines for readability
                        if len(matched_line) > 120:
                            matched_line = matched_line[:117] + '...'

                        signal = {
                            'Company': company,
                            'Signal': 'Documentation Intent',
                            'Evidence': f"Found '{keyword}' near '{matched_context}' in {doc_file}: \"{matched_line}\"",
                            'Link': file_url,
                            'priority': file_priority,
                            'type': 'documentation_intent',
                            'repo': repo,
                            'file': doc_file,
                            'keyword_matched': keyword,
                            'context_matched': matched_context,
                            'matched_line': matched_line,
                            'goldilocks_status': 'thinking',
                        }

                        yield (f"DOC INTENT ({file_priority}): '{keyword}' + '{matched_context}' in {doc_file}", signal)
                        # Only one signal per keyword per file to avoid spam
                        break

                    # Search for next occurrence of the keyword
                    keyword_pos = content_lower.find(keyword_lower, keyword_pos + 1)

        except requests.RequestException as e:
            error_detail = _format_request_exception(e)
            yield (f"Error scanning {doc_file}: {error_detail}", None)


def _strip_comments(content: str) -> str:
    """
    Remove JavaScript/TypeScript comments from content.

    Strips:
    - Single-line comments (// ...)
    - Multi-line block comments (/* ... */)

    Args:
        content: The file content to clean

    Returns:
        Content with comments removed
    """
    # Remove multi-line block comments (/* ... */) - non-greedy match
    content = re.sub(r'/\*[\s\S]*?\*/', '', content)

    # Remove single-line comments (// ...) but preserve URLs (http://, https://)
    # Split by lines to handle single-line comments properly
    lines = content.split('\n')
    cleaned_lines = []

    for line in lines:
        # Find // that's not part of http:// or https://
        # Simple approach: remove everything after // unless preceded by :
        result = []
        i = 0
        while i < len(line):
            if i < len(line) - 1 and line[i:i+2] == '//':
                # Check if this is part of a URL (preceded by :)
                if i > 0 and line[i-1] == ':':
                    result.append(line[i])
                    i += 1
                else:
                    # This is a comment - skip rest of line
                    break
            else:
                result.append(line[i])
                i += 1
        cleaned_lines.append(''.join(result))

    return '\n'.join(cleaned_lines)


def _check_locale_folders_exist_detailed(org: str, repo: str) -> tuple:
    """
    Check if locale/exclusion folders exist in the repo and analyze their contents.

    Returns a tuple of:
        - folders_exist (bool): True if any locale folder exists
        - found_folders (list): List of folder paths that were found
        - source_only (bool): True if ALL found folders contain ONLY source language files
        - folder_contents (dict): Mapping of folder path to list of filenames

    Source-only folders (e.g., containing just en.json) indicate infrastructure
    is ready but translations haven't started - this is still a GOLDILOCKS ZONE.

    IMPORTANT: Ignores locale folders found inside test/vendor directories
    (node_modules, vendor, test, tests, fixtures, __tests__) as these are
    typically test fixtures, not production translation files.
    """
    # Directories to ignore when searching for locale folders
    # Test fixtures, documentation, examples, and dependency directories should not count as "launched"
    IGNORED_PARENT_DIRS = [
        'node_modules', 'vendor', 'test', 'tests', 'fixtures',
        '__tests__', '__mocks__', 'spec', 'e2e', 'cypress',
        'examples', 'example', 'demo', 'demos', 'sample', 'samples',
        'docs', 'documentation', 'site', 'website', 'temp', 'tmp',
        'dist', 'build', 'out', 'deps', 'third_party', 'third-party',
        'external', 'lib', 'libs', 'plugins'
    ]
    
    # Specific path patterns that are NOT i18n locale folders (false positives)
    # These are editor/syntax files, library internals, etc.
    # NOTE: Avoid overly broad patterns like 'languages/' which could match legitimate i18n
    IGNORED_PATH_PATTERNS = [
        'monaco/languages',      # Monaco editor syntax highlighting
        'monaco-editor/esm',     # Monaco editor package internal
        'codemirror/lang',       # CodeMirror language modes
        'ace/lib/ace/mode',      # Ace editor language modes
        'prismjs/components',    # Prism syntax highlighting
        'highlight.js/lib/languages',  # Highlight.js syntax (specific path)
        'syntaxes/',             # TextMate/VS Code syntax grammars
        'grammars/',             # Generic grammar definitions
    ]

    def _is_in_ignored_directory(path: str) -> bool:
        """Check if a path is inside an ignored directory or a third-party library."""
        path_lower = path.lower()
        path_parts = path_lower.split('/')
        
        # 1. Check for standard ignored directories
        for part in path_parts[:-1]:  # Check all parent directories
            if part in IGNORED_PARENT_DIRS:
                return True
        
        # 2. Check for specific false-positive path patterns (editor syntax files, etc.)
        for pattern in IGNORED_PATH_PATTERNS:
            if pattern in path_lower:
                return True
                
        # 3. Check for third-party library patterns in monorepos
        for i, part in enumerate(path_parts):
            # Scoped npm packages (e.g., packages/@uppy/locales, packages/@company/lib)
            if part.startswith('@'):
                return True
            
            # Common monorepo third-party package patterns
            if part == 'packages' and i < len(path_parts) - 2:
                # packages/some-library/locales - likely a library's locales, not the main app
                next_part = path_parts[i + 1]
                # Allow if it looks like a main app package (common names: app, web, frontend, main, core)
                main_app_names = ['app', 'web', 'frontend', 'main', 'core', 'client', 'server', 'api']
                if next_part not in main_app_names:
                    return True
        
        # 4. Skip programming language mode folders (for editors) - but NOT generic 'languages'
        #    since 'languages' is a legitimate i18n folder in WordPress, Drupal, etc.
        folder_name = path_parts[-1] if path_parts else ''
        programming_mode_folders = ['modes', 'syntaxes', 'grammars']  # Exclude 'languages' - it's often legitimate i18n
        if folder_name in programming_mode_folders:
            return True

        return False

    found_folders = []
    folder_contents = {}
    all_source_only = True  # Assume source-only until proven otherwise

    # First, check root-level locale folders
    for path in Config.EXCLUSION_FOLDERS:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{path}"
            response = make_github_request(url, timeout=10)
            if response.status_code == 200:
                # Parse the folder contents (files AND directories)
                contents = response.json()
                entries_in_folder = []
                entry_types = []

                if isinstance(contents, list):
                    for item in contents:
                        item_name = item.get('name', '')
                        item_type = item.get('type', '')  # 'file' or 'dir'
                        entries_in_folder.append(item_name)
                        entry_types.append(item_type)

                # CRITICAL: Validate folder contains actual translation files or locale subdirs
                # Skip folders with code files that don't look like translations
                if not _looks_like_translation_folder(entries_in_folder, entry_types):
                    continue
                
                # Separate files and locale subdirectories
                files_in_folder = [e for e, t in zip(entries_in_folder, entry_types) if t == 'file']
                locale_subdirs = [e for e, t in zip(entries_in_folder, entry_types) 
                                  if t == 'dir' and _is_locale_name(e)]

                found_folders.append(path)
                folder_contents[path] = files_in_folder

                # Check if this folder contains ONLY source locale files
                # For folders with locale subdirs, fetch their contents to verify translations
                if locale_subdirs:
                    # Aggregate files from locale subdirs to check for actual translations
                    source_locales = ['en', 'en-us', 'en-gb', 'en_us', 'en_gb', 'base', 'source']
                    aggregated_files = list(files_in_folder)  # Start with top-level files
                    
                    for subdir in locale_subdirs:
                        try:
                            subdir_url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{path}/{subdir}"
                            subdir_response = make_github_request(subdir_url, timeout=10)
                            if subdir_response.status_code == 200:
                                subdir_contents = subdir_response.json()
                                if isinstance(subdir_contents, list):
                                    # Add files with locale prefix preserved
                                    for item in subdir_contents:
                                        if item.get('type') == 'file':
                                            file_name = item.get('name', '')
                                            # Always preserve locale prefix for proper evaluation
                                            aggregated_files.append(f"{subdir}/{file_name}")
                        except requests.RequestException:
                            pass
                    
                    # Check if aggregated files contain non-source translations
                    folder_contents[path] = aggregated_files
                    if aggregated_files:
                        folder_is_source_only = _is_source_only_folder_with_subdirs(aggregated_files, source_locales)
                        if not folder_is_source_only:
                            all_source_only = False
                elif files_in_folder:
                    folder_is_source_only = _is_source_only_folder(files_in_folder)
                    if not folder_is_source_only:
                        all_source_only = False
                # else: Empty folder - treat as source-only (infrastructure ready)

        except requests.RequestException:
            continue

    # Also search the repo tree for locale folders in subdirectories
    # (e.g., app/locales, src/i18n, packages/web/translations)
    try:
        repo_url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}"
        repo_response = make_github_request(repo_url, timeout=15)
        default_branch = repo_response.json().get('default_branch', 'main') if repo_response.status_code == 200 else 'main'

        tree_url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/git/trees/{default_branch}"
        tree_response = make_github_request(tree_url, params={'recursive': 1}, timeout=30)

        if tree_response.status_code == 200:
            tree_entries = tree_response.json().get('tree', [])
            locale_folder_names = set(Config.EXCLUSION_FOLDERS)

            for entry in tree_entries:
                if entry.get('type') != 'tree':  # Only look at directories
                    continue

                entry_path = entry.get('path', '')
                folder_name = entry_path.split('/')[-1].lower()

                # Check if this is a locale folder by name
                if folder_name not in locale_folder_names:
                    continue

                # Skip if already found at root level
                if entry_path in found_folders:
                    continue

                # CRITICAL: Skip if inside an ignored directory (test fixtures, node_modules, etc.)
                if _is_in_ignored_directory(entry_path):
                    continue

                # Fetch folder contents to validate it's actually a translation folder
                try:
                    folder_url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{entry_path}"
                    folder_response = make_github_request(folder_url, timeout=10)

                    if folder_response.status_code == 200:
                        contents = folder_response.json()
                        entries_in_folder = []
                        entry_types_list = []

                        if isinstance(contents, list):
                            for item in contents:
                                item_name = item.get('name', '')
                                item_type = item.get('type', '')  # 'file' or 'dir'
                                entries_in_folder.append(item_name)
                                entry_types_list.append(item_type)

                        # CRITICAL: Validate folder contains actual translation files or locale subdirs
                        # Skip folders with code files that don't look like translations
                        if not _looks_like_translation_folder(entries_in_folder, entry_types_list):
                            continue

                        # Separate files and locale subdirectories
                        files_in_folder = [e for e, t in zip(entries_in_folder, entry_types_list) if t == 'file']
                        locale_subdirs = [e for e, t in zip(entries_in_folder, entry_types_list) 
                                          if t == 'dir' and _is_locale_name(e)]

                        # Valid locale folder with translation files
                        found_folders.append(entry_path)
                        folder_contents[entry_path] = files_in_folder

                        # Check if this folder contains ONLY source locale files
                        # For folders with locale subdirs, fetch their contents to verify translations
                        if locale_subdirs:
                            source_locales = ['en', 'en-us', 'en-gb', 'en_us', 'en_gb', 'base', 'source']
                            aggregated_files = list(files_in_folder)
                            
                            for subdir in locale_subdirs:
                                try:
                                    subdir_url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{entry_path}/{subdir}"
                                    subdir_response = make_github_request(subdir_url, timeout=10)
                                    if subdir_response.status_code == 200:
                                        subdir_contents = subdir_response.json()
                                        if isinstance(subdir_contents, list):
                                            for item in subdir_contents:
                                                if item.get('type') == 'file':
                                                    file_name = item.get('name', '')
                                                    # Always preserve locale prefix for proper evaluation
                                                    aggregated_files.append(f"{subdir}/{file_name}")
                                except requests.RequestException:
                                    pass
                            
                            folder_contents[entry_path] = aggregated_files
                            if aggregated_files:
                                folder_is_source_only = _is_source_only_folder_with_subdirs(aggregated_files, source_locales)
                                if not folder_is_source_only:
                                    all_source_only = False
                        elif files_in_folder:
                            folder_is_source_only = _is_source_only_folder(files_in_folder)
                            if not folder_is_source_only:
                                all_source_only = False

                except requests.RequestException:
                    pass

    except requests.RequestException:
        pass  # Tree search failure is non-fatal

    folders_exist = len(found_folders) > 0

    # If no folders found, source_only is not applicable (return False)
    source_only = all_source_only if folders_exist else False

    return (folders_exist, found_folders, source_only, folder_contents)


def _looks_like_translation_folder(files_and_dirs: list, entry_types: list = None) -> bool:
    """
    Check if folder contents look like actual translation files (not code/syntax files).
    
    Translation files typically have patterns like:
    - Locale-named JSON/YAML: en.json, fr.json, de-DE.json, zh_CN.yml
    - Standard translation formats: messages.po, translations.xliff, strings.xml
    - gettext files: *.pot, *.po, *.mo
    - Locale-named SUBDIRECTORIES: en/, fr/, de-DE/, zh_CN/ (common in React, Rails, etc.)
    
    Non-translation "messages" folders typically contain:
    - JavaScript/TypeScript files: MessageHandler.js, ChatMessage.tsx
    - Generic code files without locale patterns
    
    Args:
        files_and_dirs: List of filenames/directory names in the folder
        entry_types: Optional list of types ('file' or 'dir') matching files_and_dirs
    
    Returns:
        True if the folder looks like it contains actual translations
    """
    if not files_and_dirs:
        return False  # Empty folder is not a translation folder
    
    # Known translation file extensions
    TRANSLATION_EXTENSIONS = ['.po', '.pot', '.mo', '.xliff', '.xlf', '.arb', '.properties']
    
    # Locale-pattern regex: matches files/dirs like en.json, fr-FR.json, zh_CN.yml, or just 'en', 'fr-FR'
    locale_pattern = re.compile(r'^[a-z]{2}([_-][a-z]{2,4})?(\.[a-z]+)?$', re.IGNORECASE)
    
    translation_indicator_count = 0
    total_relevant_entries = 0
    
    for i, entry_name in enumerate(files_and_dirs):
        entry_name_lower = entry_name.lower()
        entry_type = entry_types[i] if entry_types and i < len(entry_types) else None
        
        # Skip obvious non-translation entries
        if entry_name_lower.startswith('.') or entry_name_lower == 'readme.md':
            continue
        
        # Handle directories (locale-named subdirs like 'en/', 'fr-FR/')
        if entry_type == 'dir':
            total_relevant_entries += 1
            # Check if directory name matches locale pattern (e.g., 'en', 'fr', 'de-DE', 'zh_CN')
            if locale_pattern.match(entry_name_lower):
                translation_indicator_count += 1
            continue
            
        # Check for known translation extensions (files only)
        for ext in TRANSLATION_EXTENSIONS:
            if entry_name_lower.endswith(ext):
                translation_indicator_count += 1
                total_relevant_entries += 1
                break
        else:
            # Check if it's a JSON/YAML/JS file with locale-like name
            if entry_name_lower.endswith(('.json', '.yml', '.yaml')):
                total_relevant_entries += 1
                # Check if filename matches locale pattern (e.g., en.json, fr-CA.json)
                if locale_pattern.match(entry_name_lower):
                    translation_indicator_count += 1
                # Also check for common translation file names
                elif any(name in entry_name_lower for name in ['translation', 'message', 'string', 'locale']):
                    translation_indicator_count += 1
            elif entry_name_lower.endswith(('.js', '.ts', '.jsx', '.tsx')):
                # JS/TS files - only count if they match locale patterns
                total_relevant_entries += 1
                if locale_pattern.match(entry_name_lower):
                    translation_indicator_count += 1
    
    # Folder looks like translations if at least one translation indicator exists
    # and a reasonable portion of contents are translation-like
    if total_relevant_entries == 0:
        return False
    
    return translation_indicator_count >= 1 and (translation_indicator_count / total_relevant_entries) >= 0.3


def _is_locale_name(name: str) -> bool:
    """
    Check if a directory name looks like a locale identifier.
    
    Examples: en, fr, de-DE, zh_CN, pt-BR
    """
    locale_pattern = re.compile(r'^[a-z]{2}([_-][a-z]{2,4})?$', re.IGNORECASE)
    return bool(locale_pattern.match(name))


def _is_source_only_folder_with_subdirs(files: list, source_locales: list) -> bool:
    """
    Check if aggregated files from locale subdirs contain ONLY source language files.
    
    Files are in format: "locale/filename" where locale is the subdir name.
    If any file has a non-source locale prefix, folder has translations.
    
    The key insight: the locale SUBDIR NAME determines if it's source or not.
    The actual filename inside the subdir doesn't matter for classification.
    
    Examples:
    - ["en/messages.json"] -> True (source-only: "en" is source locale)
    - ["en/messages.json", "fr/messages.json"] -> False (has "fr" which is not source)
    - ["en.json", "base.json"] -> True (top-level source files)
    - ["en.json", "fr.json"] -> False (has "fr" which is not source)
    """
    if not files:
        return True  # Empty = source-only
    
    for filename in files:
        # Check if file is from a locale subdir (has prefix)
        if '/' in filename:
            # Has subdir prefix (e.g., "en/messages.json" or "fr/messages.json")
            # The SUBDIR NAME determines if it's source - not the filename inside
            locale_prefix = filename.split('/')[0].lower()
            if locale_prefix not in source_locales:
                # File from non-source locale (fr, de, etc.) = has translations
                return False
            # File from source locale (en, en-us, etc.) is source-only, continue
        else:
            # Top-level file - check against known source patterns (en.json, base.json, etc.)
            if filename.lower() not in Config.SOURCE_LOCALE_PATTERNS:
                # Not a recognized source file - could be a translation (like fr.json)
                return False
    
    return True


def _is_source_only_folder(files: list) -> bool:
    """
    Check if a list of files contains ONLY source language files.

    Source language files are files that match patterns like:
    en.json, en-US.json, en-GB.json, base.json, source.json
    and their .js/.ts/.yml variants.

    Args:
        files: List of filenames in the folder

    Returns:
        True if ALL files are source language files, False otherwise
    """
    if not files:
        return True  # Empty folder is considered source-only

    for filename in files:
        filename_lower = filename.lower()

        # Check if this file matches any source locale pattern
        is_source_file = filename_lower in Config.SOURCE_LOCALE_PATTERNS

        if not is_source_file:
            # This file is not a source locale file - folder has translations
            return False

    return True


def _check_locale_folders_exist(org: str, repo: str) -> bool:
    """
    Check if locale/messages folders exist in the repo.
    Returns True if any locale folder exists.
    """
    locale_paths = ['locales', 'locale', 'messages', 'i18n', 'translations', 'lang']

    for path in locale_paths:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{path}"
            response = make_github_request(url, timeout=10)
            if response.status_code == 200:
                return True
        except requests.RequestException:
            continue

    return False


def _scan_ghost_branches(org: str, repo: str, company: str, since_timestamp: datetime = None) -> Generator[tuple, None, None]:
    """
    Signal 3: Ghost Branch Scan (Active Phase)

    Target: List Branches and Pull Requests
    Logic: Flag branches or PRs that indicate work-in-progress localization
    Keywords: feature/i18n, chore/localization, add-translation-support,
              refactor/extract-strings, l10n-setup

    Yields:
        Tuples of (log_message, signal_object)
    """
    if since_timestamp:
        cutoff_date = since_timestamp.replace(tzinfo=timezone.utc) if since_timestamp.tzinfo is None else since_timestamp
    else:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)

    # Scan branches
    try:
        url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/branches"
        params = {'per_page': 100}

        response = make_github_request(url, params=params, timeout=30)

        if response.status_code == 200:
            branches = response.json()

            for branch in branches:
                branch_name = branch.get('name', '').lower()
                commit_date = _parse_github_datetime(
                    branch.get('commit', {}).get('commit', {}).get('committer', {}).get('date')
                    or branch.get('commit', {}).get('commit', {}).get('author', {}).get('date')
                )
                if commit_date and commit_date < cutoff_date:
                    continue

                # Check against ghost branch patterns
                for pattern in Config.GHOST_BRANCH_PATTERNS:
                    if pattern.lower() in branch_name:
                        branch_url = f"https://github.com/{org}/{repo}/tree/{branch.get('name')}"

                        signal = {
                            'Company': company,
                            'Signal': 'Ghost Branch',
                            'Evidence': f"WIP branch found: '{branch.get('name')}' matches pattern '{pattern}'",
                            'Link': branch_url,
                            'priority': 'HIGH',
                            'type': 'ghost_branch',
                            'repo': repo,
                            'branch_name': branch.get('name'),
                            'pattern_matched': pattern,
                        }

                        yield (f"GHOST BRANCH: {branch.get('name')}", signal)
                        break  # Only match once per branch

    except requests.RequestException as e:
        error_detail = _format_request_exception(e)
        yield (f"Error scanning branches: {error_detail}", None)

    # Scan unmerged PRs
    try:
        url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/pulls"
        params = {
            'state': 'open',  # Only open (unmerged) PRs
            'per_page': 50,
            'sort': 'created',
            'direction': 'desc'
        }

        response = make_github_request(url, params=params, timeout=30)

        if response.status_code == 200:
            prs = response.json()

            for pr in prs:
                title = pr.get('title', '').lower()
                head_branch = pr.get('head', {}).get('ref', '').lower()
                pr_url = pr.get('html_url')
                pr_number = pr.get('number')
                updated_at = _parse_github_datetime(pr.get('updated_at') or pr.get('created_at'))
                if updated_at and updated_at < cutoff_date:
                    continue

                # Check title and branch for ghost patterns
                text_to_check = f"{title} {head_branch}"

                for pattern in Config.GHOST_BRANCH_PATTERNS:
                    if pattern.lower() in text_to_check:
                        signal = {
                            'Company': company,
                            'Signal': 'Ghost Branch',
                            'Evidence': f"Unmerged PR #{pr_number}: '{pr.get('title')[:50]}...' (branch: {pr.get('head', {}).get('ref')})",
                            'Link': pr_url,
                            'priority': 'HIGH',
                            'type': 'ghost_branch',
                            'repo': repo,
                            'pr_number': pr_number,
                            'pr_title': pr.get('title'),
                            'branch_name': pr.get('head', {}).get('ref'),
                            'pattern_matched': pattern,
                            'state': 'open',
                            'created_at': pr.get('created_at'),
                        }

                        yield (f"UNMERGED PR: #{pr_number} - {pr.get('title')[:40]}...", signal)
                        break  # Only match once per PR

    except requests.RequestException as e:
        error_detail = _format_request_exception(e)
        yield (f"Error scanning PRs: {error_detail}", None)


def _parse_github_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _calculate_intent_score(scan_results: dict) -> int:
    """
    Calculate intent score based on GOLDILOCKS ZONE scoring model.

    NEW SCORING (Gap Requirement):
    - PREPARING (Library + No locale folders) = 90-100/100 (GOLDILOCKS ZONE!)
    - THINKING (RFC/Discussions found) = 40/100
    - LAUNCHED (Locale folders exist) = 10/100 (Too Late)

    The "GAP" is what we want: Infrastructure ready, but no translations.

    Returns score from 0-100.
    """
    summary = scan_results.get('signal_summary', {})
    signals = scan_results.get('signals', [])

    # ============================================================
    # Check for PREPARING status (Goldilocks Zone!) - HIGHEST PRIORITY
    # ============================================================
    # This check MUST come before "Already Launched" to ensure that if a company
    # has ONE localized repo but is actively preparing another (Smoking Gun),
    # we don't disqualify them based on the single localized repo.
    # We want to capture the NEW opportunity.
    dep_hits = summary.get('dependency_injection', {}).get('hits', [])
    goldilocks_hits = [h for h in dep_hits if h.get('goldilocks_status') == 'preparing' or h.get('gap_verified')]

    # SMOKING GUN FORKS are ALWAYS PREPARING status - forking an i18n library
    # is even stronger intent than just using it as a dependency!
    smoking_gun_hits = summary.get('smoking_gun_fork', {}).get('hits', [])
    
    # Combine both types of PREPARING signals
    all_preparing_hits = goldilocks_hits + smoking_gun_hits

    if all_preparing_hits:
        # This is the GOLDILOCKS ZONE!
        # Library found + No locale folders = PERFECT TIMING
        # OR forked i18n library = ACTIVE CUSTOMIZATION
        scan_results['goldilocks_status'] = 'preparing'
        scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('preparing', 'HOT LEAD')

        # Score based on number of signals (90-100 range)
        base_score = Config.GOLDILOCKS_SCORES.get('preparing_min', 90)
        bonus = min(len(all_preparing_hits) * 5, 10)  # Up to +10 bonus

        # Smoking gun forks get extra bonus - they're forking the source!
        if smoking_gun_hits:
            bonus = max(bonus, 8)  # At least +8 for any smoking gun fork

        # Add bonus for ghost branches (active work)
        ghost_count = summary.get('ghost_branch', {}).get('count', 0)
        if ghost_count > 0:
            bonus = 10  # Max bonus if actively working on it

        return min(base_score + bonus, Config.GOLDILOCKS_SCORES.get('preparing_max', 100))

    # ============================================================
    # Check for LAUNCHED status (disqualifying condition)
    # ============================================================
    # Only if NO preparing signals were found, does a launched signal disqualify.
    already_launched_signals = [s for s in signals if s.get('type') == 'already_launched']
    if already_launched_signals:
        # They have locale folders - TOO LATE
        scan_results['goldilocks_status'] = 'launched'
        scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('launched', 'LOW PRIORITY')
        return Config.GOLDILOCKS_SCORES.get('launched', 10)

    # ============================================================
    # Check for THINKING status (RFC/Discussion OR Documentation Intent)
    # ============================================================
    # This check MUST come before the Mega-Corp heuristic to ensure
    # valid thinking signals (RFCs/Discussions/Documentation) take precedence
    rfc_count = summary.get('rfc_discussion', {}).get('count', 0)
    doc_count = summary.get('documentation_intent', {}).get('count', 0)

    if rfc_count > 0 or doc_count > 0:
        scan_results['goldilocks_status'] = 'thinking'
        scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('thinking', 'WARM LEAD')

        # Base score for thinking
        base_score = Config.GOLDILOCKS_SCORES.get('thinking', 40)

        # Bonus for HIGH priority discussions
        rfc_high_priority = summary.get('rfc_discussion', {}).get('high_priority_count', 0)
        rfc_bonus = min(rfc_high_priority * 10, 15)  # Up to +15 for RFC high priority

        # Bonus for HIGH priority documentation signals (CHANGELOG, ROADMAP)
        doc_high_priority = summary.get('documentation_intent', {}).get('high_priority_count', 0)
        doc_bonus = min(doc_high_priority * 8, 15)  # Up to +15 for doc high priority

        # Combined bonus (cap at +25)
        total_bonus = min(rfc_bonus + doc_bonus, 25)

        return min(base_score + total_bonus, 65)  # Cap at 65 for thinking

    # ============================================================
    # Check for Ghost Branches only (Active experimentation)
    # ============================================================
    ghost_count = summary.get('ghost_branch', {}).get('count', 0)
    if ghost_count > 0:
        scan_results['goldilocks_status'] = 'thinking'
        scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('thinking', 'WARM LEAD')
        return min(35 + ghost_count * 5, 50)

    # ============================================================
    # MEGA-CORP HEURISTIC: Detect high-maturity orgs without any other signals
    # ============================================================
    # Large engineering orgs (Airbnb, Uber, Facebook, etc.) often use custom/internal
    # i18n solutions that won't appear in our standard library scans. If they have
    # significant GitHub presence but no other signals, they've likely already
    # launched with proprietary tooling.
    #
    # IMPORTANT: This check runs LAST after all other signals (Preparing, Thinking, Ghost)
    # to ensure valid intent signals take precedence.
    total_stars = scan_results.get('total_stars', 0)
    public_repos = scan_results.get('org_public_repos', 0)
    repos_scanned = len(scan_results.get('repos_scanned', []))

    # Only apply mega-corp heuristic if:
    # 1. We actually scanned repos (repos_scanned > 0)
    # 2. The org has very high activity (stars > 20000 OR repos > 400)
    if repos_scanned > 0 and (total_stars > 20000 or public_repos > 400):
        # High-maturity org with no Preparing/Thinking signals = Already Launched
        scan_results['goldilocks_status'] = 'launched'
        scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('launched', 'LOW PRIORITY')
        scan_results['mega_corp_heuristic'] = True
        scan_results['mega_corp_evidence'] = 'High-maturity engineering org (likely custom/internal i18n)'

        # Add a synthetic signal to explain the classification
        mega_corp_signal = {
            'Company': scan_results.get('company_name', 'Unknown'),
            'Signal': 'Mega-Corp Heuristic',
            'Evidence': f'High-maturity engineering org (likely custom/internal i18n). Stars: {total_stars:,}, Repos: {public_repos}',
            'Link': scan_results.get('org_url', ''),
            'priority': 'LOW',
            'type': 'mega_corp_launched',
            'goldilocks_status': 'launched',
            'total_stars': total_stars,
            'public_repos': public_repos,
        }
        signals.append(mega_corp_signal)

        return Config.GOLDILOCKS_SCORES.get('launched', 10)

    # ============================================================
    # No signals found
    # ============================================================
    scan_results['goldilocks_status'] = 'none'
    scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('none', 'COLD')
    return 0


def _sse_log(message: str) -> str:
    """Format a log message for SSE."""
    return f"data: LOG:{message}\n\n"


def _sse_error(message: str) -> str:
    """Format an error message for SSE."""
    return f"data: ERROR:{message}\n\n"


def _sse_data(event_type: str, data: dict) -> str:
    """Format a data payload for SSE."""
    return f"data: {event_type}:{json.dumps(data)}\n\n"


def _sse_signal(signal: dict) -> str:
    """
    Format a SIGNAL_FOUND event for real-time streaming.

    Provides immediate feedback when a signal is detected.
    """
    return f"data: SIGNAL_FOUND:{json.dumps(signal)}\n\n"


# Legacy function for backwards compatibility
def calculate_confidence_score(scan_results: dict) -> int:
    """
    Legacy function - redirects to intent score.
    """
    return _calculate_intent_score(scan_results)
