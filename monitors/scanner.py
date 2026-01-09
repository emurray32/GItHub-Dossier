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
from datetime import datetime, timedelta
from typing import Generator, Optional, List, Dict
from config import Config
from .discovery import get_github_headers, discover_organization, get_organization_repos


def deep_scan_generator(company_name: str) -> Generator[str, None, None]:
    """
    Perform a 3-Signal Intent Scan of a company's GitHub presence.

    Focuses on detecting pre-launch internationalization signals:
    - RFC & Discussion (Thinking Phase)
    - Dependency Injection (Preparing Phase)
    - Ghost Branch (Active Phase)

    Args:
        company_name: The company name to scan.

    Yields:
        SSE-formatted strings for streaming response.
    """
    start_time = datetime.now()

    yield _sse_log(f"🔍 Starting 3-Signal Intent Scan: {company_name}")
    yield _sse_log("=" * 60)
    yield _sse_log("Target: Pre-launch internationalization signals")
    yield _sse_log("")

    # Phase 1: Discover Organization
    yield _sse_log("PHASE 1: Organization Discovery")
    yield _sse_log("-" * 40)

    org_data = None
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

    yield _sse_log(f"✓ Organization confirmed: {org_name} (@{org_login})")
    yield _sse_log(f"  Public repos: {org_data.get('public_repos', 'N/A')}")

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
        yield _sse_error("No repositories found. Scan aborted.")
        return

    # Select top repos for deep scan
    repos_to_scan = repos[:Config.MAX_REPOS_TO_SCAN]
    yield _sse_log(f"✓ Selected {len(repos_to_scan)} repositories for intent scan")

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

        # 3-Signal Intent Results
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
            'ghost_branch': {
                'count': 0,
                'hits': []
            }
        },
        'intent_score': 0,  # Calculated at the end
    }

    # Phase 3: Signal 1 - RFC & Discussion Scan (Thinking Phase)
    yield _sse_log("")
    yield _sse_log("PHASE 3: RFC & Discussion Scan (Thinking Phase)")
    yield _sse_log("-" * 40)
    yield _sse_log("Scanning Issues and Discussions for high-intent keywords...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos for issues
        repo_name = repo.get('name')
        yield _sse_log(f"  [{idx}/5] Scanning issues in: {repo_name}")

        for log_msg, signal in _scan_rfc_discussion(org_login, repo_name, company_name):
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
    yield _sse_log(f"✓ RFC & Discussion scan complete: {rfc_count} signals ({high_priority} HIGH priority)")

    # Phase 4: Signal 2 - Dependency Injection Scan (Preparing Phase)
    yield _sse_log("")
    yield _sse_log("PHASE 4: Dependency Injection Scan (Preparing Phase)")
    yield _sse_log("-" * 40)
    yield _sse_log("Checking for i18n libraries WITHOUT locale folders...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos
        repo_name = repo.get('name')
        yield _sse_log(f"  [{idx}/5] Scanning dependencies in: {repo_name}")

        for log_msg, signal in _scan_dependency_injection(org_login, repo_name, company_name):
            if log_msg:
                yield _sse_log(f"    {log_msg}")
            if signal:
                scan_results['signals'].append(signal)
                scan_results['signal_summary']['dependency_injection']['hits'].append(signal)
                scan_results['signal_summary']['dependency_injection']['count'] += 1
                yield _sse_signal(signal)

    dep_count = scan_results['signal_summary']['dependency_injection']['count']
    yield _sse_log(f"✓ Dependency Injection scan complete: {dep_count} signals")

    # Phase 5: Signal 3 - Ghost Branch Scan (Active Phase)
    yield _sse_log("")
    yield _sse_log("PHASE 5: Ghost Branch Scan (Active Phase)")
    yield _sse_log("-" * 40)
    yield _sse_log("Scanning for WIP i18n branches and unmerged PRs...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos
        repo_name = repo.get('name')
        yield _sse_log(f"  [{idx}/5] Scanning branches in: {repo_name}")

        for log_msg, signal in _scan_ghost_branches(org_login, repo_name, company_name):
            if log_msg:
                yield _sse_log(f"    {log_msg}")
            if signal:
                scan_results['signals'].append(signal)
                scan_results['signal_summary']['ghost_branch']['hits'].append(signal)
                scan_results['signal_summary']['ghost_branch']['count'] += 1
                yield _sse_signal(signal)

    ghost_count = scan_results['signal_summary']['ghost_branch']['count']
    yield _sse_log(f"✓ Ghost Branch scan complete: {ghost_count} signals")

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
    yield _sse_log(f"📊 Total Signals Detected: {total_signals}")
    yield _sse_log(f"   • RFC & Discussion: {rfc_count} ({high_priority} HIGH)")
    yield _sse_log(f"   • Dependency Injection: {dep_count}")
    yield _sse_log(f"   • Ghost Branches: {ghost_count}")
    yield _sse_log(f"📊 Intent Score: {scan_results['intent_score']}/100")
    yield _sse_log(f"📊 Scan Duration: {duration:.1f}s")

    if total_signals > 0:
        yield _sse_log("")
        yield _sse_log("🎯 INTENT DETECTED: Company is in Thinking/Preparing phase!")
    else:
        yield _sse_log("")
        yield _sse_log("⚪ No pre-launch signals detected.")

    yield _sse_log("")
    yield _sse_log("🤖 Generating AI Sales Intelligence...")

    # Send scan results
    yield _sse_data('SCAN_COMPLETE', scan_results)


def _scan_rfc_discussion(org: str, repo: str, company: str) -> Generator[tuple, None, None]:
    """
    Signal 1: RFC & Discussion Scan (Thinking Phase)

    Target: Issues and Discussions (Open & Closed)
    Logic: Flag if title or body contains high-intent keywords in last 6 months
    Keywords: 'i18n strategy', 'localization support', 'handle timezones',
              'currency formatting', 'RTL support', 'translation workflow', 'multi-currency'
    Priority: HIGH if title starts with 'RFC' or 'Proposal'

    Yields:
        Tuples of (log_message, signal_object)
    """
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

        response = requests.get(
            url,
            headers=get_github_headers(),
            params=params,
            timeout=30
        )

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

                    priority_label = "🔴 HIGH" if is_high_priority else "🟡 MEDIUM"
                    yield (f"{priority_label}: Issue #{issue_number} - {title[:40]}...", signal)

    except requests.RequestException as e:
        yield (f"Error scanning issues: {str(e)}", None)

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

            response = requests.get(
                search_url,
                headers=get_github_headers(),
                params=params,
                timeout=15
            )

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

                        priority_label = "🔴 HIGH" if is_high_priority else "🟡 MEDIUM"
                        yield (f"{priority_label}: Discussion - {title[:40]}...", signal)

    except requests.RequestException:
        pass  # Search API failures are non-fatal


def _scan_dependency_injection(org: str, repo: str, company: str) -> Generator[tuple, None, None]:
    """
    Signal 2: Dependency Injection Scan (Preparing Phase)

    Target: package.json, Gemfile, requirements.txt, composer.json, mix.exs
    Logic: Flag if any of the 'Smoking Gun' i18n libraries are present
    Constraint: Signal is valid ONLY if /locales/ or /messages/ folder does NOT exist

    Yields:
        Tuples of (log_message, signal_object)
    """
    # First, check if locale folders exist (if they do, skip this repo)
    locale_exists = _check_locale_folders_exist(org, repo)

    if locale_exists:
        yield ("Locale folder exists - skipping (already implemented)", None)
        return

    # Scan each dependency file
    for dep_file in Config.DEPENDENCY_INJECTION_FILES:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{dep_file}"
            response = requests.get(
                url,
                headers=get_github_headers(),
                timeout=15
            )

            if response.status_code != 200:
                continue

            file_data = response.json()
            content_b64 = file_data.get('content', '')
            file_url = file_data.get('html_url')

            if content_b64:
                try:
                    content = base64.b64decode(content_b64).decode('utf-8')
                    content_lower = content.lower()

                    # Check for smoking gun libraries
                    found_libs = []
                    for lib in Config.SMOKING_GUN_LIBS:
                        # Match as dependency name (with quotes for JSON/YAML)
                        if f'"{lib}"' in content_lower or f"'{lib}'" in content_lower or f'{lib}' in content_lower:
                            found_libs.append(lib)

                    if found_libs:
                        signal = {
                            'Company': company,
                            'Signal': 'Dependency Injection',
                            'Evidence': f"Found {', '.join(found_libs)} in {dep_file} but no /locales/ folder",
                            'Link': file_url,
                            'priority': 'HIGH',
                            'type': 'dependency_injection',
                            'repo': repo,
                            'file': dep_file,
                            'libraries_found': found_libs,
                        }

                        yield (f"🎯 SMOKING GUN: {', '.join(found_libs)} in {dep_file}", signal)

                except Exception:
                    pass

        except requests.RequestException:
            continue


def _check_locale_folders_exist(org: str, repo: str) -> bool:
    """
    Check if locale/messages folders exist in the repo.
    Returns True if any locale folder exists.
    """
    locale_paths = ['locales', 'locale', 'messages', 'i18n', 'translations', 'lang']

    for path in locale_paths:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{path}"
            response = requests.get(
                url,
                headers=get_github_headers(),
                timeout=10
            )
            if response.status_code == 200:
                return True
        except requests.RequestException:
            continue

    return False


def _scan_ghost_branches(org: str, repo: str, company: str) -> Generator[tuple, None, None]:
    """
    Signal 3: Ghost Branch Scan (Active Phase)

    Target: List Branches and Pull Requests
    Logic: Flag branches or PRs that indicate work-in-progress localization
    Keywords: feature/i18n, chore/localization, add-translation-support,
              refactor/extract-strings, l10n-setup

    Yields:
        Tuples of (log_message, signal_object)
    """
    # Scan branches
    try:
        url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/branches"
        params = {'per_page': 100}

        response = requests.get(
            url,
            headers=get_github_headers(),
            params=params,
            timeout=30
        )

        if response.status_code == 200:
            branches = response.json()

            for branch in branches:
                branch_name = branch.get('name', '').lower()

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

                        yield (f"👻 GHOST BRANCH: {branch.get('name')}", signal)
                        break  # Only match once per branch

    except requests.RequestException as e:
        yield (f"Error scanning branches: {str(e)}", None)

    # Scan unmerged PRs
    try:
        url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/pulls"
        params = {
            'state': 'open',  # Only open (unmerged) PRs
            'per_page': 50,
            'sort': 'created',
            'direction': 'desc'
        }

        response = requests.get(
            url,
            headers=get_github_headers(),
            params=params,
            timeout=30
        )

        if response.status_code == 200:
            prs = response.json()

            for pr in prs:
                title = pr.get('title', '').lower()
                head_branch = pr.get('head', {}).get('ref', '').lower()
                pr_url = pr.get('html_url')
                pr_number = pr.get('number')

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

                        yield (f"👻 UNMERGED PR: #{pr_number} - {pr.get('title')[:40]}...", signal)
                        break  # Only match once per PR

    except requests.RequestException as e:
        yield (f"Error scanning PRs: {str(e)}", None)


def _calculate_intent_score(scan_results: dict) -> int:
    """
    Calculate intent score based on detected signals.

    Scoring:
    - RFC & Discussion (HIGH): +30 pts each
    - RFC & Discussion (MEDIUM): +15 pts each
    - Dependency Injection: +40 pts each (smoking gun!)
    - Ghost Branch: +25 pts each

    Returns score from 0-100.
    """
    score = 0
    summary = scan_results.get('signal_summary', {})

    # RFC & Discussion signals
    rfc_hits = summary.get('rfc_discussion', {}).get('hits', [])
    for hit in rfc_hits:
        if hit.get('priority') == 'HIGH':
            score += 30
        else:
            score += 15

    # Dependency Injection signals (highest value - smoking gun!)
    dep_count = summary.get('dependency_injection', {}).get('count', 0)
    score += dep_count * 40

    # Ghost Branch signals
    ghost_count = summary.get('ghost_branch', {}).get('count', 0)
    score += ghost_count * 25

    return min(score, 100)


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
