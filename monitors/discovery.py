"""
GitHub Organization Discovery Module.

Handles finding GitHub organizations and their repositories.
"""
import json
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional, Generator, List, Dict
from functools import lru_cache
from config import Config
from utils import get_github_headers, make_github_request




def discover_organization(company_name: str) -> Generator[str, None, Optional[dict]]:
    """
    Discover a GitHub organization by company name.

    Yields progress messages and returns the organization data.

    Args:
        company_name: The company name to search for.

    Yields:
        Status messages during discovery.

    Returns:
        Organization data dict or None if not found.
    """
    yield f"Searching for GitHub organization: {company_name}"

    # Try direct org lookup first
    direct_result = _try_direct_lookup(company_name)
    if direct_result:
        yield f"Found organization via direct lookup: {direct_result['login']}"
        return direct_result

    # Fall back to search API
    yield "Direct lookup failed, searching GitHub..."

    search_url = f"{Config.GITHUB_API_BASE}/search/users"
    params = {
        'q': f'{company_name} type:org',
        'per_page': 10
    }

    try:
        response = make_github_request(
            search_url,
            params=params,
            timeout=30,
            priority='high'
        )
        response.raise_for_status()
        data = response.json()

        if data.get('total_count', 0) == 0:
            yield f"No GitHub organization found for: {company_name}"
            return None

        # Find best match
        items = data.get('items', [])
        best_match = _find_best_match(company_name, items)

        if best_match:
            yield f"Found organization: {best_match['login']}"
            # Fetch full org details
            org_details = _get_org_details(best_match['login'])
            return org_details or best_match

        yield "No suitable organization match found"
        return None

    except requests.RequestException as e:
        yield f"Error searching for organization: {str(e)}"
        return None


@lru_cache(maxsize=128)
def _get_org_details_cached(org_login: str) -> Optional[dict]:
    """Internal cached version of get_org_details."""
    try:
        url = f"{Config.GITHUB_API_BASE}/orgs/{org_login}"
        response = make_github_request(
            url,
            timeout=10,
            priority='high'
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass
    return None


def _try_direct_lookup(company_name: str) -> Optional[dict]:
    """Try to find org by direct name lookup, preferring those with repositories."""
    # Normalize company name for URL
    normalized = company_name.lower().replace(' ', '').replace('-', '').replace('_', '')

    # Try variations
    variations = [
        company_name,
        normalized,
        f"{normalized}labs",
        f"{normalized}engineering",
        company_name.lower().replace(' ', '-'),
        company_name.lower().replace(' ', '_'),
    ]

    matches = []
    for variant in variations:
        try:
            url = f"{Config.GITHUB_API_BASE}/orgs/{variant}"
            response = make_github_request(
                url,
                timeout=5,
                priority='high'
            )
            if response.status_code == 200:
                org_data = response.json()
                if org_data.get('public_repos', 0) > 0:
                    # Found a good one, can stop early for high repo count
                    if org_data.get('public_repos', 0) > 10:
                        return org_data
                    matches.append(org_data)
                else:
                    # Keep as fallback but keep looking
                    matches.append(org_data)
        except requests.RequestException:
            continue

    if not matches:
        return None
        
    # Pick the one with the most repos
    return max(matches, key=lambda x: x.get('public_repos', 0))


def _get_org_details(org_login: str) -> Optional[dict]:
    """Get full organization details (using internal cache)."""
    return _get_org_details_cached(org_login)


def _find_best_match(company_name: str, items: list) -> Optional[dict]:
    """Find the best matching organization from search results, considering repo count."""
    if not items:
        return None

    company_lower = company_name.lower()
    matches_with_details = []

    # Fetch details for the first few items to see repo counts
    for item in items[:5]:
        details = _get_org_details(item['login'])
        if details:
            matches_with_details.append(details)
        else:
            matches_with_details.append(item)

    # 1. Exact match with repos
    for org in matches_with_details:
        if org['login'].lower() == company_lower and org.get('public_repos', 0) > 0:
            return org

    # 2. Match containing name with most repos
    best_org = None
    max_repos = -1
    
    for org in matches_with_details:
        login_lower = org['login'].lower()
        if company_lower in login_lower or login_lower in company_lower:
            repos = org.get('public_repos', 0)
            if repos > max_repos:
                max_repos = repos
                best_org = org

    return best_org or matches_with_details[0]


def get_organization_repos(org_login: str) -> Generator[str, None, list]:
    """
    Get all non-archived repositories for an organization.

    Args:
        org_login: The organization login name.

    Yields:
        Status messages during fetching.

    Returns:
        List of repository data dicts, sorted by activity.
    """
    yield f"Fetching repositories for {org_login}..."

    all_repos = []
    all_repos_unfiltered = []  # Keep track of all repos before activity filter
    page = 1
    per_page = 100
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=Config.REPO_INACTIVITY_DAYS)

    while True:
        try:
            url = f"{Config.GITHUB_API_BASE}/orgs/{org_login}/repos"
            params = {
                'type': 'all',
                'per_page': per_page,
                'page': page,
                'sort': 'pushed',
                'direction': 'desc'
            }

            response = make_github_request(
                url,
                params=params,
                timeout=30
            )
            response.raise_for_status()
            repos = response.json()

            if not repos:
                break

            # Filter out archived repos and inactive repos
            active_repos = []
            non_archived_repos = []
            for repo in repos:
                if repo.get('archived', False):
                    continue

                non_archived_repos.append(repo)

                pushed_at = repo.get('pushed_at')
                if pushed_at:
                    try:
                        pushed_date = datetime.strptime(pushed_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                    except ValueError:
                        pushed_date = None
                else:
                    pushed_date = None

                if pushed_date and pushed_date < cutoff_date:
                    continue

                active_repos.append(repo)
            all_repos.extend(active_repos)
            all_repos_unfiltered.extend(non_archived_repos)

            yield f"Fetched page {page}: {len(active_repos)} active repos (total: {len(all_repos)})"

            if len(repos) < per_page:
                break

            page += 1

            # Safety limit
            if page > 20:
                yield "Reached maximum page limit"
                break

        except requests.RequestException as e:
            yield f"Error fetching repos (page {page}): {str(e)}"
            break

    # Fallback: if all repos were filtered out due to inactivity, use top N most recent
    if not all_repos and all_repos_unfiltered:
        fallback_count = min(Config.REPO_INACTIVITY_FALLBACK, len(all_repos_unfiltered))
        yield f"All repos inactive for >{Config.REPO_INACTIVITY_DAYS} days. Using top {fallback_count} most recent repos."
        # Sort by pushed_at descending and take top N
        all_repos_unfiltered.sort(key=lambda r: r.get('pushed_at', ''), reverse=True)
        all_repos = all_repos_unfiltered[:fallback_count]

    # Sort and prioritize repos
    prioritized = _prioritize_repos(all_repos)
    yield f"Total repositories found: {len(prioritized)}"

    return prioritized


def score_repository(repo: dict) -> int:
    """
    Calculate a priority score for a repository.

    Scoring Logic:
        - Base score: stargazers_count
        - +1000 points if name contains a HIGH_VALUE pattern (core product)
        - -500 points if name contains a LOW_VALUE pattern (non-core)
        - -1000 points if fork is True
        - +500 points if language is TypeScript, JavaScript, Swift, or Kotlin

    Args:
        repo: Repository data dict from GitHub API.

    Returns:
        Integer score (higher = more valuable for scanning).
    """
    name_lower = repo.get('name', '').lower()
    language = repo.get('language') or ''
    is_fork = repo.get('fork', False)

    # Start with stargazers_count as base score
    score = repo.get('stargazers_count', 0)

    # +1000 for high-value patterns (core product repos)
    for pattern in Config.HIGH_VALUE_PATTERNS:
        if pattern.lower() in name_lower:
            score += 1000
            break  # Only apply bonus once

    # -500 for low-value patterns (docs, tools, demos, etc.)
    for pattern in Config.LOW_VALUE_PATTERNS:
        if pattern.lower() in name_lower:
            score -= 500
            break  # Only apply penalty once

    # -1000 for forks (not original work)
    if is_fork:
        score -= 1000

    # +500 for high-value languages (frontend/mobile focus)
    if language in Config.HIGH_VALUE_LANGUAGES:
        score += 500

    return score


def _prioritize_repos(repos: list) -> list:
    """
    Sort repositories by priority score (descending).

    Uses score_repository() to calculate a composite score based on:
    - Star count (base score)
    - High-value name patterns (+1000)
    - Low-value name patterns (-500)
    - Fork status (-1000)
    - High-value languages (+500)

    This ensures the scanner focuses on core product repos first,
    deprioritizing docs, forks, tools, and demos.

    Args:
        repos: List of repository data dicts.

    Returns:
        List of repos sorted by priority score (highest first).
    """
    return sorted(repos, key=score_repository, reverse=True)


