"""
GitHub Organization Discovery Module.

Handles finding GitHub organizations and their repositories.
"""
import requests
from typing import Optional, Generator
from config import Config


def get_github_headers() -> dict:
    """Get headers for GitHub API requests."""
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'Lead-Machine/1.0'
    }
    if Config.GITHUB_TOKEN:
        headers['Authorization'] = f'token {Config.GITHUB_TOKEN}'
    return headers


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
        response = requests.get(
            search_url,
            headers=get_github_headers(),
            params=params,
            timeout=30
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


def _try_direct_lookup(company_name: str) -> Optional[dict]:
    """Try to find org by direct name lookup."""
    # Normalize company name for URL
    normalized = company_name.lower().replace(' ', '').replace('-', '').replace('_', '')

    # Try variations
    variations = [
        company_name,
        normalized,
        company_name.lower().replace(' ', '-'),
        company_name.lower().replace(' ', '_'),
    ]

    for variant in variations:
        try:
            url = f"{Config.GITHUB_API_BASE}/orgs/{variant}"
            response = requests.get(
                url,
                headers=get_github_headers(),
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
        except requests.RequestException:
            continue

    return None


def _get_org_details(org_login: str) -> Optional[dict]:
    """Get full organization details."""
    try:
        url = f"{Config.GITHUB_API_BASE}/orgs/{org_login}"
        response = requests.get(
            url,
            headers=get_github_headers(),
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass
    return None


def _find_best_match(company_name: str, items: list) -> Optional[dict]:
    """Find the best matching organization from search results."""
    if not items:
        return None

    company_lower = company_name.lower()

    # Exact match first
    for item in items:
        if item['login'].lower() == company_lower:
            return item

    # Contains match
    for item in items:
        login_lower = item['login'].lower()
        if company_lower in login_lower or login_lower in company_lower:
            return item

    # Return first result as fallback
    return items[0]


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
    page = 1
    per_page = 100

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

            response = requests.get(
                url,
                headers=get_github_headers(),
                params=params,
                timeout=30
            )
            response.raise_for_status()
            repos = response.json()

            if not repos:
                break

            # Filter out archived repos
            active_repos = [r for r in repos if not r.get('archived', False)]
            all_repos.extend(active_repos)

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

    # Sort and prioritize repos
    prioritized = _prioritize_repos(all_repos)
    yield f"Total repositories found: {len(prioritized)}"

    return prioritized


def _prioritize_repos(repos: list) -> list:
    """
    Sort repositories by relevance and activity.

    Priority is given to repos matching keywords, then sorted by recent activity.
    """
    def score_repo(repo: dict) -> tuple:
        name_lower = repo.get('name', '').lower()
        desc_lower = (repo.get('description') or '').lower()

        # Check for priority keywords
        keyword_match = any(
            kw in name_lower or kw in desc_lower
            for kw in Config.PRIORITY_KEYWORDS
        )

        # Activity score based on recent push
        pushed_at = repo.get('pushed_at', '')

        return (
            keyword_match,  # Priority keyword match first
            pushed_at,      # Then by most recent push
            repo.get('stargazers_count', 0)  # Then by stars
        )

    return sorted(repos, key=score_repo, reverse=True)
