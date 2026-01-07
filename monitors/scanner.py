"""
Deep Scanner Module for GitHub Repository Analysis.

Provides streaming generator for real-time scan progress updates.
"""
import json
import requests
from datetime import datetime, timedelta
from typing import Generator
from config import Config
from .discovery import get_github_headers, discover_organization, get_organization_repos


def deep_scan_generator(company_name: str) -> Generator[str, None, None]:
    """
    Perform a deep scan of a company's GitHub presence.

    This is a generator that yields Server-Sent Events (SSE) formatted messages.
    Progress updates are prefixed with "LOG:" and the final result with "RESULT:".

    Args:
        company_name: The company name to scan.

    Yields:
        SSE-formatted strings for streaming response.
    """
    start_time = datetime.now()

    yield _sse_log(f"Starting deep scan for: {company_name}")
    yield _sse_log("=" * 50)

    # Phase 1: Discover Organization
    yield _sse_log("PHASE 1: Organization Discovery")
    yield _sse_log("-" * 30)

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

    yield _sse_log(f"Organization confirmed: {org_name} (@{org_login})")
    yield _sse_log(f"Public repos: {org_data.get('public_repos', 'N/A')}")

    # Phase 2: Fetch Repositories
    yield _sse_log("")
    yield _sse_log("PHASE 2: Repository Discovery")
    yield _sse_log("-" * 30)

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
    yield _sse_log(f"Selected {len(repos_to_scan)} repositories for deep scan")

    # Phase 3: Deep Scan Repositories
    yield _sse_log("")
    yield _sse_log("PHASE 3: Deep Repository Scan")
    yield _sse_log("-" * 30)

    scan_results = {
        'company_name': company_name,
        'org_login': org_login,
        'org_name': org_name,
        'org_url': org_data.get('html_url', f'https://github.com/{org_login}'),
        'org_description': org_data.get('description'),
        'org_public_repos': org_data.get('public_repos'),
        'repos_scanned': [],
        'signals': [],
        'total_commits_analyzed': 0,
        'total_prs_analyzed': 0,
        'scan_timestamp': datetime.now().isoformat()
    }

    for idx, repo in enumerate(repos_to_scan, 1):
        repo_name = repo.get('name')
        full_name = repo.get('full_name', f"{org_login}/{repo_name}")

        yield _sse_log("")
        yield _sse_log(f"[{idx}/{len(repos_to_scan)}] Scanning: {repo_name}")

        repo_result = {
            'name': repo_name,
            'full_name': full_name,
            'url': repo.get('html_url'),
            'description': repo.get('description'),
            'stars': repo.get('stargazers_count', 0),
            'language': repo.get('language'),
            'commits_analyzed': 0,
            'prs_analyzed': 0,
            'signals': []
        }

        # Scan commits
        for log_msg, commits_count, commit_signals in _scan_commits(org_login, repo_name):
            yield _sse_log(f"  {log_msg}")
            if commits_count is not None:
                repo_result['commits_analyzed'] = commits_count
                scan_results['total_commits_analyzed'] += commits_count
            if commit_signals:
                repo_result['signals'].extend(commit_signals)

        # Scan PRs
        for log_msg, prs_count, pr_signals in _scan_pull_requests(org_login, repo_name):
            yield _sse_log(f"  {log_msg}")
            if prs_count is not None:
                repo_result['prs_analyzed'] = prs_count
                scan_results['total_prs_analyzed'] += prs_count
            if pr_signals:
                repo_result['signals'].extend(pr_signals)

        # Add repo signals to main list
        for signal in repo_result['signals']:
            signal['repo'] = repo_name
            signal['repo_url'] = repo.get('html_url')
            scan_results['signals'].append(signal)

        signal_count = len(repo_result['signals'])
        if signal_count > 0:
            yield _sse_log(f"  Found {signal_count} i18n signal(s)")

        scan_results['repos_scanned'].append(repo_result)

    # Phase 4: Summary
    yield _sse_log("")
    yield _sse_log("PHASE 4: Scan Complete")
    yield _sse_log("-" * 30)

    duration = (datetime.now() - start_time).total_seconds()
    scan_results['scan_duration_seconds'] = duration

    yield _sse_log(f"Repositories scanned: {len(scan_results['repos_scanned'])}")
    yield _sse_log(f"Total commits analyzed: {scan_results['total_commits_analyzed']}")
    yield _sse_log(f"Total PRs analyzed: {scan_results['total_prs_analyzed']}")
    yield _sse_log(f"I18n signals found: {len(scan_results['signals'])}")
    yield _sse_log(f"Scan duration: {duration:.1f} seconds")
    yield _sse_log("")
    yield _sse_log("Generating AI analysis...")

    # Send scan results (will be picked up for AI analysis)
    yield _sse_data('SCAN_COMPLETE', scan_results)


def _scan_commits(org: str, repo: str) -> Generator[tuple, None, None]:
    """
    Scan commits for i18n signals.

    Yields:
        Tuples of (log_message, commits_count, signals_list)
    """
    yield ("Analyzing commits...", None, None)

    cutoff_date = datetime.now() - timedelta(days=Config.COMMIT_LOOKBACK_DAYS)
    cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')

    commits_analyzed = 0
    signals = []
    page = 1

    while commits_analyzed < Config.COMMITS_PER_REPO:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/commits"
            params = {
                'since': cutoff_str,
                'per_page': min(100, Config.COMMITS_PER_REPO - commits_analyzed),
                'page': page
            }

            response = requests.get(
                url,
                headers=get_github_headers(),
                params=params,
                timeout=30
            )

            if response.status_code == 409:  # Empty repo
                yield ("Repository is empty", 0, [])
                return

            response.raise_for_status()
            commits = response.json()

            if not commits:
                break

            for commit in commits:
                sha = commit.get('sha', '')[:7]
                message = commit.get('commit', {}).get('message', '')

                # Skip noise
                if _is_noise(message):
                    continue

                # Check for i18n signals in commit message
                if _has_i18n_keywords(message):
                    signals.append({
                        'type': 'commit_message',
                        'sha': sha,
                        'message': message[:200],
                        'url': commit.get('html_url'),
                        'date': commit.get('commit', {}).get('author', {}).get('date'),
                        'author': commit.get('commit', {}).get('author', {}).get('name')
                    })

                # Check commit files for i18n patterns
                commit_signals = _analyze_commit_files(org, repo, commit.get('sha'))
                signals.extend(commit_signals)

                commits_analyzed += 1
                if commits_analyzed >= Config.COMMITS_PER_REPO:
                    break

            page += 1
            if page > 10:  # Safety limit
                break

        except requests.RequestException as e:
            yield (f"Error fetching commits: {str(e)}", commits_analyzed, signals)
            return

    yield (f"Analyzed {commits_analyzed} commits", commits_analyzed, signals)


def _analyze_commit_files(org: str, repo: str, sha: str) -> list:
    """Analyze individual commit files for i18n signals."""
    signals = []

    try:
        url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/commits/{sha}"
        response = requests.get(
            url,
            headers=get_github_headers(),
            timeout=15
        )

        if response.status_code != 200:
            return signals

        commit_data = response.json()
        files = commit_data.get('files', [])

        for file in files:
            filename = file.get('filename', '')
            status = file.get('status', '')

            # Check if file is in i18n-related path
            if _is_i18n_file(filename):
                signals.append({
                    'type': 'file_change',
                    'file': filename,
                    'status': status,
                    'sha': sha[:7],
                    'additions': file.get('additions', 0),
                    'deletions': file.get('deletions', 0),
                    'url': file.get('blob_url')
                })

            # Check for hreflang in HTML files
            if filename.endswith(('.html', '.htm', '.jsx', '.tsx')):
                patch = file.get('patch', '')
                if 'hreflang' in patch.lower():
                    signals.append({
                        'type': 'hreflang',
                        'file': filename,
                        'sha': sha[:7],
                        'url': file.get('blob_url')
                    })

    except requests.RequestException:
        pass

    return signals


def _scan_pull_requests(org: str, repo: str) -> Generator[tuple, None, None]:
    """
    Scan pull requests for i18n signals.

    Yields:
        Tuples of (log_message, prs_count, signals_list)
    """
    yield ("Analyzing pull requests...", None, None)

    cutoff_date = datetime.now() - timedelta(days=Config.PR_LOOKBACK_DAYS)
    cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')

    prs_analyzed = 0
    signals = []

    try:
        # Get open PRs
        url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/pulls"
        params = {
            'state': 'all',
            'sort': 'created',
            'direction': 'desc',
            'per_page': 100
        }

        response = requests.get(
            url,
            headers=get_github_headers(),
            params=params,
            timeout=30
        )
        response.raise_for_status()
        prs = response.json()

        for pr in prs:
            created_at = pr.get('created_at', '')
            if created_at < cutoff_str:
                continue

            title = pr.get('title', '')
            body = pr.get('body', '') or ''

            # Skip noise
            if _is_noise(title):
                continue

            # Check for i18n signals
            if _has_i18n_keywords(title) or _has_i18n_keywords(body):
                signals.append({
                    'type': 'pull_request',
                    'number': pr.get('number'),
                    'title': title,
                    'state': pr.get('state'),
                    'url': pr.get('html_url'),
                    'created_at': created_at,
                    'author': pr.get('user', {}).get('login'),
                    'labels': [l.get('name') for l in pr.get('labels', [])]
                })

            prs_analyzed += 1

    except requests.RequestException as e:
        yield (f"Error fetching PRs: {str(e)}", prs_analyzed, signals)
        return

    yield (f"Analyzed {prs_analyzed} PRs", prs_analyzed, signals)


def _is_i18n_file(filename: str) -> bool:
    """Check if a file path indicates i18n content."""
    filename_lower = filename.lower()
    return any(pattern in filename_lower for pattern in Config.I18N_FILE_PATTERNS)


def _has_i18n_keywords(text: str) -> bool:
    """Check if text contains i18n-related keywords."""
    if not text:
        return False
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in Config.I18N_PR_KEYWORDS)


def _is_noise(text: str) -> bool:
    """Check if text matches noise patterns to filter out."""
    if not text:
        return False
    text_lower = text.lower()
    return any(pattern in text_lower for pattern in Config.NOISE_PATTERNS)


def _sse_log(message: str) -> str:
    """Format a log message for SSE."""
    return f"data: LOG:{message}\n\n"


def _sse_error(message: str) -> str:
    """Format an error message for SSE."""
    return f"data: ERROR:{message}\n\n"


def _sse_data(event_type: str, data: dict) -> str:
    """Format a data payload for SSE."""
    return f"data: {event_type}:{json.dumps(data)}\n\n"
