"""
High-Intent Sales Intelligence Scanner for GitHub Repository Analysis.

Transforms raw GitHub data into actionable sales intelligence by detecting:
1. Tech Stack & i18n Libraries (with framework mapping)
2. Competitor Config Files (HIGH INTENT)
3. Frustration Signals (pain points in commit messages)
4. Developer-as-Translator Metric (human vs bot ratio)
5. Reviewer Bottleneck Detection
6. Locale Inventory with Geo-Spatial Inference
7. Greenfield Opportunity Detection
"""
import json
import re
import requests
import base64
from datetime import datetime, timedelta
from typing import Generator, Optional
from collections import Counter
from config import Config
from .discovery import get_github_headers, discover_organization, get_organization_repos
from utils import (
    parse_locale_code, infer_market_strategy, detect_dependencies_in_content,
    detect_frustration_signal, is_bot_account, calculate_developer_translator_ratio,
    get_framework_from_libraries
)


def deep_scan_generator(company_name: str) -> Generator[str, None, None]:
    """
    Perform a high-intent sales intelligence scan of a company's GitHub presence.

    This is a generator that yields Server-Sent Events (SSE) formatted messages.
    Progress updates are prefixed with "LOG:" and the final result with "RESULT:".

    Args:
        company_name: The company name to scan.

    Yields:
        SSE-formatted strings for streaming response.
    """
    start_time = datetime.now()

    yield _sse_log(f"🔍 Starting High-Intent Sales Intelligence Scan: {company_name}")
    yield _sse_log("=" * 60)

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
    yield _sse_log(f"✓ Selected {len(repos_to_scan)} repositories for intelligence scan")

    # Phase 3: High-Intent Signal Detection
    yield _sse_log("")
    yield _sse_log("PHASE 3: High-Intent Signal Detection")
    yield _sse_log("-" * 40)

    # Initialize scan results with all intelligence fields
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
        'scan_timestamp': datetime.now().isoformat(),

        # High-Intent Intelligence Fields
        'tech_stack': {
            'i18n_libraries': [],
            'frameworks': [],
            'primary_framework': None,
        },
        'competitor_detection': {
            'config_files_found': [],
            'tms_in_dependencies': [],
            'is_using_competitor': False,
        },
        'frustration_signals': [],
        'developer_translator_metric': {
            'human_edits': 0,
            'bot_edits': 0,
            'human_ratio': 0,
            'human_authors': [],
            'is_high_pain': False,
        },
        'reviewer_bottleneck': {
            'reviewers': {},
            'bottleneck_user': None,
            'is_bottleneck': False,
        },
        'locale_inventory': {
            'paths_found': [],
            'locales_detected': [],
            'file_count': 0,
            'language_count': 0,
        },
        'seo_i18n_config': {
            'configs_found': [],
            'locales_from_seo': [],
            'has_marketing_intent': False,
        },
        'market_insights': {},
        'is_greenfield': False,
        'total_stars': sum(r.get('stargazers_count', 0) for r in repos_to_scan),
        'contributors': {},  # login -> {name, bio, company, blog, i18n_commits, i18n_prs, frustration_count}
        'confidence_score': 0,  # Will be calculated at the end
        'pr_commenters': {},  # Track PR commenters as potential decision makers
    }
    
    # Track all file signals for developer-translator calculation
    all_file_signals = []
    all_pr_reviewers = Counter()

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
            'signals': [],
            'frustration_count': 0,
            'competitor_configs': [],
        }

        # Scan for competitor config files (HIGH INTENT!)
        for log_msg, config_result in _scan_competitor_configs(org_login, repo_name):
            yield _sse_log(f"  {log_msg}")
            if config_result:
                scan_results['competitor_detection']['config_files_found'].append({
                    'repo': repo_name,
                    'file': config_result
                })
                repo_result['competitor_configs'].append(config_result)
                # Stream SIGNAL_FOUND for competitor detection (HIGH VALUE)
                yield _sse_signal({
                    'type': 'competitor_config',
                    'repo': repo_name,
                    'file': config_result,
                    'significance': 'high'
                })

        # Scan commits with frustration detection
        for log_msg, commits_count, commit_signals, frustration_signals in _scan_commits_with_frustration(org_login, repo_name):
            yield _sse_log(f"  {log_msg}")
            if commits_count is not None:
                repo_result['commits_analyzed'] = commits_count
                scan_results['total_commits_analyzed'] += commits_count
            if commit_signals:
                repo_result['signals'].extend(commit_signals)
                # Track file signals with author for developer-translator metric
                for sig in commit_signals:
                    if sig.get('type') == 'file_change':
                        all_file_signals.append(sig)
                        # Stream SIGNAL_FOUND for new file changes
                        if sig.get('status') == 'added':
                            yield _sse_signal({
                                'type': 'new_locale_file',
                                'repo': repo_name,
                                'file': sig.get('file'),
                                'author': sig.get('author_login'),
                                'significance': 'high'
                            })
            if frustration_signals:
                scan_results['frustration_signals'].extend(frustration_signals)
                repo_result['frustration_count'] = len(frustration_signals)
                # Stream SIGNAL_FOUND for frustration signals (HIGH VALUE PAIN POINTS)
                for fs in frustration_signals[:3]:  # First 3 only to avoid spam
                    yield _sse_signal({
                        'type': 'frustration',
                        'repo': repo_name,
                        'pain_indicator': fs.get('pain_indicator'),
                        'message': fs.get('message', '')[:80],
                        'author': fs.get('author_login'),
                        'significance': 'high'
                    })

        # Scan PRs with reviewer and commenter tracking
        for log_msg, prs_count, pr_signals, reviewers, commenters in _scan_pull_requests_with_reviewers(org_login, repo_name):
            yield _sse_log(f"  {log_msg}")
            if prs_count is not None:
                repo_result['prs_analyzed'] = prs_count
                scan_results['total_prs_analyzed'] += prs_count
            if pr_signals:
                repo_result['signals'].extend(pr_signals)
                # Stream SIGNAL_FOUND for i18n PRs
                for pr_sig in pr_signals[:2]:  # First 2 only
                    yield _sse_signal({
                        'type': 'i18n_pr',
                        'repo': repo_name,
                        'pr_number': pr_sig.get('number'),
                        'title': pr_sig.get('title', '')[:60],
                        'author': pr_sig.get('author'),
                        'significance': 'medium'
                    })
            if reviewers:
                all_pr_reviewers.update(reviewers)
            if commenters:
                # Merge commenters into scan results (prioritize those who comment most)
                for login, data in commenters.items():
                    if login not in scan_results['pr_commenters']:
                        scan_results['pr_commenters'][login] = data
                    else:
                        scan_results['pr_commenters'][login]['comment_count'] += data['comment_count']
                        scan_results['pr_commenters'][login]['prs_commented_on'].extend(data['prs_commented_on'])

        # Scan dependencies (only first 5 repos for speed)
        if idx <= 5:
            for log_msg, dep_result in _scan_dependencies(org_login, repo_name):
                yield _sse_log(f"  {log_msg}")
                if dep_result:
                    if dep_result.get('i18n_libraries'):
                        scan_results['tech_stack']['i18n_libraries'].extend(dep_result['i18n_libraries'])
                    if dep_result.get('frameworks'):
                        scan_results['tech_stack']['frameworks'].extend(dep_result['frameworks'])
                    if dep_result.get('tms_detected'):
                        scan_results['competitor_detection']['tms_in_dependencies'].extend(dep_result['tms_detected'])

        # Scan locale inventory
        for log_msg, inventory_result in _scan_locale_inventory(org_login, repo_name):
            yield _sse_log(f"  {log_msg}")
            if inventory_result:
                if inventory_result.get('locales'):
                    scan_results['locale_inventory']['locales_detected'].extend(inventory_result['locales'])
                    scan_results['locale_inventory']['file_count'] += len(inventory_result['locales'])
                    # Stream SIGNAL_FOUND for locale discovery
                    yield _sse_signal({
                        'type': 'locale_inventory',
                        'repo': repo_name,
                        'count': len(inventory_result['locales']),
                        'locales': inventory_result['locales'][:5]  # First 5 for preview
                    })
                if inventory_result.get('path'):
                    scan_results['locale_inventory']['paths_found'].append({
                        'repo': repo_name,
                        'path': inventory_result['path']
                    })

        # Scan SEO i18n config (next.config.js, sitemap.xml)
        if idx <= 5:  # Only first 5 repos for speed
            for log_msg, seo_result in _scan_seo_i18n_config(org_login, repo_name):
                yield _sse_log(f"  {log_msg}")
                if seo_result:
                    scan_results['seo_i18n_config']['configs_found'].append({
                        'repo': repo_name,
                        'source': seo_result['source'],
                        'framework': seo_result.get('framework'),
                        'locales': seo_result.get('locales', [])
                    })
                    scan_results['seo_i18n_config']['locales_from_seo'].extend(seo_result.get('locales', []))
                    scan_results['seo_i18n_config']['has_marketing_intent'] = True
                    # Stream SIGNAL_FOUND for SEO config (HIGH VALUE)
                    yield _sse_signal({
                        'type': 'seo_i18n_config',
                        'repo': repo_name,
                        'source': seo_result['source'],
                        'framework': seo_result.get('framework'),
                        'locales': seo_result.get('locales', [])
                    })

        # Add repo signals to main list
        for signal in repo_result['signals']:
            signal['repo'] = repo_name
            signal['repo_url'] = repo.get('html_url')
            scan_results['signals'].append(signal)

        # Summary for this repo
        signal_count = len(repo_result['signals'])
        frustration_count = repo_result['frustration_count']
        competitor_count = len(repo_result['competitor_configs'])

        if signal_count > 0 or frustration_count > 0 or competitor_count > 0:
            yield _sse_log(f"  📊 Found: {signal_count} signals, {frustration_count} frustrations, {competitor_count} competitor configs")

        scan_results['repos_scanned'].append(repo_result)

    # Phase 4: Contributor Intelligence
    yield _sse_log("")
    yield _sse_log("PHASE 4: Contributor Intelligence")
    yield _sse_log("-" * 40)
    
    # Aggregate activity into main contributors list
    temp_contribs = {} # login -> counts
    
    # From signals
    for signal in scan_results['signals']:
        login = signal.get('author_login')
        if login and not is_bot_account(login):
            if login not in temp_contribs: temp_contribs[login] = {'i18n_commits': 0, 'i18n_prs': 0, 'frustration_count': 0}
            if signal['type'] in ['commit_message', 'file_change']:
                temp_contribs[login]['i18n_commits'] += 1
            elif signal['type'] == 'pull_request':
                temp_contribs[login]['i18n_prs'] += 1

    # From frustration signals
    for signal in scan_results['frustration_signals']:
        login = signal.get('author_login')
        if login and not is_bot_account(login):
            if login not in temp_contribs: temp_contribs[login] = {'i18n_commits': 0, 'i18n_prs': 0, 'frustration_count': 0}
            temp_contribs[login]['frustration_count'] += 1

    # Sort and pick top contributors for profile fetching
    top_logins = sorted(temp_contribs.keys(), key=lambda x: (temp_contribs[x]['frustration_count'] * 2 + temp_contribs[x]['i18n_commits']), reverse=True)[:5]
    
    for login in top_logins:
        yield _sse_log(f"  Fetching profile for key contributor: @{login}")
        profile = _get_user_profile(login)
        if profile:
            scan_results['contributors'][login] = {
                'name': profile.get('name'),
                'bio': profile.get('bio'),
                'company': profile.get('company'),
                'blog': profile.get('blog'),
                'location': profile.get('location'),
                'avatar_url': profile.get('avatar_url'),
                'html_url': profile.get('html_url'),
                **temp_contribs[login]
            }

    # Phase 5: Intelligence Analysis
    yield _sse_log("")
    yield _sse_log("PHASE 5: Intelligence Analysis")
    yield _sse_log("-" * 40)

    # Deduplicate and finalize tech stack
    scan_results['tech_stack']['i18n_libraries'] = list(set(scan_results['tech_stack']['i18n_libraries']))
    scan_results['tech_stack']['frameworks'] = list(set(scan_results['tech_stack']['frameworks']))
    scan_results['tech_stack']['primary_framework'] = get_framework_from_libraries(
        scan_results['tech_stack']['i18n_libraries']
    )
    
    if scan_results['tech_stack']['i18n_libraries']:
        yield _sse_log(f"🛠️ Tech Stack: {', '.join(scan_results['tech_stack']['i18n_libraries'])}")
        if scan_results['tech_stack']['primary_framework']:
            yield _sse_log(f"   Framework: {scan_results['tech_stack']['primary_framework']}")

    # Competitor detection finalization
    scan_results['competitor_detection']['tms_in_dependencies'] = list(set(
        scan_results['competitor_detection']['tms_in_dependencies']
    ))
    scan_results['competitor_detection']['is_using_competitor'] = bool(
        scan_results['competitor_detection']['config_files_found'] or 
        scan_results['competitor_detection']['tms_in_dependencies']
    )
    
    if scan_results['competitor_detection']['is_using_competitor']:
        configs = [c['file'] for c in scan_results['competitor_detection']['config_files_found']]
        tms = scan_results['competitor_detection']['tms_in_dependencies']
        yield _sse_log(f"⚠️ COMPETITOR DETECTED: configs={configs}, deps={tms}")

    # Developer-as-Translator metric
    dev_metric = calculate_developer_translator_ratio(all_file_signals)
    scan_results['developer_translator_metric'] = dev_metric
    if dev_metric['total'] > 0:
        yield _sse_log(f"👨‍💻 Developer-Translator Ratio: {dev_metric['human_percentage']} human edits ({dev_metric['human_edits']}/{dev_metric['total']})")
        if dev_metric['is_high_pain']:
            yield _sse_log("   ⚡ HIGH PAIN: Engineers are doing translation work!")

    # Reviewer bottleneck detection
    if all_pr_reviewers:
        total_reviews = sum(all_pr_reviewers.values())
        top_reviewer, top_count = all_pr_reviewers.most_common(1)[0]
        top_ratio = top_count / total_reviews if total_reviews > 0 else 0
        
        scan_results['reviewer_bottleneck'] = {
            'reviewers': dict(all_pr_reviewers),
            'bottleneck_user': top_reviewer if top_ratio >= Config.REVIEWER_BOTTLENECK_THRESHOLD else None,
            'is_bottleneck': top_ratio >= Config.REVIEWER_BOTTLENECK_THRESHOLD,
            'top_reviewer_ratio': top_ratio,
        }
        
        if scan_results['reviewer_bottleneck']['is_bottleneck']:
            yield _sse_log(f"🚨 BOTTLENECK: @{top_reviewer} reviews {top_ratio*100:.0f}% of i18n PRs")

    # Frustration signals summary
    if scan_results['frustration_signals']:
        pain_types = Counter(f['pain_indicator'] for f in scan_results['frustration_signals'])
        yield _sse_log(f"😤 Frustration Signals: {len(scan_results['frustration_signals'])} detected")
        for pain_type, count in pain_types.most_common(3):
            yield _sse_log(f"   {pain_type}: {count}")

    # Locale inventory and market insights
    unique_locales = list(set(scan_results['locale_inventory']['locales_detected']))
    scan_results['locale_inventory']['locales_detected'] = unique_locales
    scan_results['locale_inventory']['language_count'] = len(unique_locales)

    if unique_locales:
        market_insights = infer_market_strategy(unique_locales)
        scan_results['market_insights'] = market_insights
        yield _sse_log(f"🌍 Locale Inventory: {len(unique_locales)} languages")
        if market_insights.get('primary_market'):
            yield _sse_log(f"   Primary Market: {market_insights['primary_market']}")
        yield _sse_log(f"   {market_insights.get('narrative', '')}")

    # Greenfield detection
    total_signals = len(scan_results['signals'])
    has_i18n_libs = len(scan_results['tech_stack']['i18n_libraries']) > 0
    has_locale_files = scan_results['locale_inventory']['file_count'] > 0
    has_competitor = scan_results['competitor_detection']['is_using_competitor']
    total_stars = scan_results['total_stars']

    if (total_signals == 0 and not has_i18n_libs and not has_locale_files and
        not has_competitor and total_stars >= Config.GREENFIELD_STAR_THRESHOLD):
        scan_results['is_greenfield'] = True
        yield _sse_log(f"🎯 GREENFIELD OPPORTUNITY: {total_stars}+ stars, NO localization infrastructure!")
        yield _sse_log("   Mature codebase with NO localization layer. Risk of technical debt.")
        # Stream greenfield signal
        yield _sse_signal({
            'type': 'greenfield_opportunity',
            'total_stars': total_stars,
            'significance': 'high'
        })

    # SEO i18n config summary
    if scan_results['seo_i18n_config']['has_marketing_intent']:
        seo_locales = list(set(scan_results['seo_i18n_config']['locales_from_seo']))
        scan_results['seo_i18n_config']['locales_from_seo'] = seo_locales
        yield _sse_log(f"🎯 SEO I18n CONFIG DETECTED: Marketing intent confirmed!")
        yield _sse_log(f"   Locales from SEO configs: {', '.join(seo_locales[:10])}")

    # Calculate confidence score
    scan_results['confidence_score'] = calculate_confidence_score(scan_results)
    yield _sse_log(f"📊 Confidence Score: {scan_results['confidence_score']}/100")

    # Phase 5: Summary
    yield _sse_log("")
    yield _sse_log("PHASE 5: Scan Complete")
    yield _sse_log("-" * 40)

    duration = (datetime.now() - start_time).total_seconds()
    scan_results['scan_duration_seconds'] = duration

    yield _sse_log(f"📊 Repositories scanned: {len(scan_results['repos_scanned'])}")
    yield _sse_log(f"📊 Total commits analyzed: {scan_results['total_commits_analyzed']}")
    yield _sse_log(f"📊 Total PRs analyzed: {scan_results['total_prs_analyzed']}")
    yield _sse_log(f"📊 I18n signals found: {len(scan_results['signals'])}")
    yield _sse_log(f"📊 Frustration signals: {len(scan_results['frustration_signals'])}")
    yield _sse_log(f"📊 Scan duration: {duration:.1f} seconds")
    yield _sse_log("")
    yield _sse_log("🤖 Generating AI Sales Intelligence...")

    # Send scan results
    yield _sse_data('SCAN_COMPLETE', scan_results)


def _scan_competitor_configs(org: str, repo: str) -> Generator[tuple, None, None]:
    """
    Scan for competitor TMS configuration files (HIGH INTENT signal).

    Yields:
        Tuples of (log_message, config_file_found)
    """
    yield ("Checking for competitor configs...", None)

    for config_file in Config.COMPETITOR_CONFIGS:
        try:
            # Handle nested paths like .tx/config
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{config_file}"
            response = requests.get(
                url,
                headers=get_github_headers(),
                timeout=10
            )

            if response.status_code == 200:
                yield (f"⚠️ COMPETITOR CONFIG FOUND: {config_file}", config_file)

        except requests.RequestException:
            continue

    yield ("Competitor config scan complete", None)


def _scan_commits_with_frustration(org: str, repo: str) -> Generator[tuple, None, None]:
    """
    Scan commits for i18n signals AND frustration indicators.

    Yields:
        Tuples of (log_message, commits_count, signals_list, frustration_signals)
    """
    yield ("Analyzing commits for signals and frustration...", None, None, None)

    cutoff_date = datetime.now() - timedelta(days=Config.COMMIT_LOOKBACK_DAYS)
    cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')

    commits_analyzed = 0
    signals = []
    frustration_signals = []
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
                yield ("Repository is empty", 0, [], [])
                return

            response.raise_for_status()
            commits = response.json()

            if not commits:
                break

            for commit in commits:
                sha = commit.get('sha', '')[:7]
                message = commit.get('commit', {}).get('message', '')
                author = commit.get('commit', {}).get('author', {}).get('name', '')
                author_login = commit.get('author', {}).get('login', '') if commit.get('author') else ''

                # Skip noise
                if _is_noise(message):
                    continue

                # Check for frustration signals (HIGH PRIORITY)
                frustration = detect_frustration_signal(message)
                if frustration:
                    frustration_signals.append({
                        'type': 'frustration_signal',
                        'sha': sha,
                        'message': message[:200],
                        'matched_text': frustration['matched_text'],
                        'pain_indicator': frustration['pain_indicator'],
                        'author': author,
                        'author_login': author_login,
                        'url': commit.get('html_url'),
                        'date': commit.get('commit', {}).get('author', {}).get('date'),
                    })

                # Check for i18n signals in commit message
                if _has_i18n_keywords(message):
                    signals.append({
                        'type': 'commit_message',
                        'sha': sha,
                        'message': message[:200],
                        'url': commit.get('html_url'),
                        'date': commit.get('commit', {}).get('author', {}).get('date'),
                        'author': author,
                        'author_login': author_login,
                    })

                # Check commit files for i18n patterns
                commit_file_signals = _analyze_commit_files_with_author(
                    org, repo, commit.get('sha'), author, author_login
                )
                signals.extend(commit_file_signals)

                commits_analyzed += 1
                if commits_analyzed >= Config.COMMITS_PER_REPO:
                    break

            page += 1
            if page > 10:  # Safety limit
                break

        except requests.RequestException as e:
            yield (f"Error fetching commits: {str(e)}", commits_analyzed, signals, frustration_signals)
            return

    yield (f"Analyzed {commits_analyzed} commits ({len(frustration_signals)} frustrations)", 
           commits_analyzed, signals, frustration_signals)


def _analyze_commit_files_with_author(org: str, repo: str, sha: str, author: str, author_login: str) -> list:
    """Analyze individual commit files for i18n signals, including author info."""
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

            # SMART FILTERING: Skip noise files
            if _is_noise_file(filename):
                continue

            # Check if file is in i18n-related path
            if _is_i18n_file(filename):
                signals.append({
                    'type': 'file_change',
                    'file': filename,
                    'status': status,
                    'sha': sha[:7],
                    'additions': file.get('additions', 0),
                    'deletions': file.get('deletions', 0),
                    'url': file.get('blob_url'),
                    'author': author,
                    'author_login': author_login,
                    'is_translation_file': filename.endswith(('.json', '.yaml', '.yml', '.po', '.strings')),
                })

            # Check for hreflang in HTML files
            if filename.endswith(('.html', '.htm', '.jsx', '.tsx')):
                patch = file.get('patch', '')
                if 'hreflang' in patch.lower():
                    signals.append({
                        'type': 'hreflang',
                        'file': filename,
                        'sha': sha[:7],
                        'url': file.get('blob_url'),
                        'author': author,
                    })

    except requests.RequestException:
        pass

    return signals


def _is_noise_file(filename: str) -> bool:
    """
    Check if a file should be ignored (noise filtering).

    Filters out documentation, images, lock files, and test files
    that often contain false positive i18n keywords.
    """
    if not filename:
        return True

    filename_lower = filename.lower()
    basename = filename.split('/')[-1]

    # Check against noise filenames
    if basename in Config.NOISE_FILENAMES:
        return True

    # Check against noise extensions
    for ext in Config.NOISE_FILE_EXTENSIONS:
        if filename_lower.endswith(ext):
            return True

    return False


def _scan_pull_requests_with_reviewers(org: str, repo: str) -> Generator[tuple, None, None]:
    """
    Scan pull requests for i18n signals AND track reviewers/commenters for bottleneck detection.

    Enhanced to prioritize:
    - Reviewers (likely managers/leads who approve work)
    - Commenters (engaged stakeholders with opinions)
    - Authors (the implementers, may be junior devs)

    Yields:
        Tuples of (log_message, prs_count, signals_list, reviewers_counter, commenters_dict)
    """
    yield ("Analyzing pull requests...", None, None, None, None)

    cutoff_date = datetime.now() - timedelta(days=Config.PR_LOOKBACK_DAYS)
    cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')

    prs_analyzed = 0
    signals = []
    reviewers = Counter()
    commenters = {}  # login -> {comment_count, prs_commented_on, sample_comments}

    try:
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
            pr_number = pr.get('number')

            # Skip noise
            if _is_noise(title):
                continue

            # Check for i18n signals
            is_i18n_pr = _has_i18n_keywords(title) or _has_i18n_keywords(body)

            if is_i18n_pr:
                # Track who merged the PR (often a lead/manager)
                merged_by = pr.get('merged_by', {}).get('login') if pr.get('merged') else None

                signals.append({
                    'type': 'pull_request',
                    'number': pr_number,
                    'title': title,
                    'state': pr.get('state'),
                    'url': pr.get('html_url'),
                    'created_at': created_at,
                    'author': pr.get('user', {}).get('login'),
                    'merged_by': merged_by,
                    'labels': [l.get('name') for l in pr.get('labels', [])]
                })

                # Fetch reviewers for i18n PRs (for bottleneck detection)
                try:
                    reviews_url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/pulls/{pr_number}/reviews"
                    reviews_response = requests.get(
                        reviews_url,
                        headers=get_github_headers(),
                        timeout=10
                    )
                    if reviews_response.status_code == 200:
                        reviews = reviews_response.json()
                        for review in reviews:
                            reviewer = review.get('user', {}).get('login')
                            if reviewer and not is_bot_account(reviewer):
                                reviewers[reviewer] += 1
                except requests.RequestException:
                    pass

                # Fetch commenters for i18n PRs (potential decision makers)
                try:
                    comments_url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/issues/{pr_number}/comments"
                    comments_response = requests.get(
                        comments_url,
                        headers=get_github_headers(),
                        params={'per_page': 50},
                        timeout=10
                    )
                    if comments_response.status_code == 200:
                        comments = comments_response.json()
                        for comment in comments:
                            commenter = comment.get('user', {}).get('login')
                            if commenter and not is_bot_account(commenter):
                                if commenter not in commenters:
                                    commenters[commenter] = {
                                        'comment_count': 0,
                                        'prs_commented_on': set(),
                                        'sample_comments': []
                                    }
                                commenters[commenter]['comment_count'] += 1
                                commenters[commenter]['prs_commented_on'].add(pr_number)
                                if len(commenters[commenter]['sample_comments']) < 2:
                                    commenters[commenter]['sample_comments'].append(
                                        comment.get('body', '')[:100]
                                    )
                except requests.RequestException:
                    pass

            prs_analyzed += 1

    except requests.RequestException as e:
        yield (f"Error fetching PRs: {str(e)}", prs_analyzed, signals, reviewers, commenters)
        return

    # Convert sets to lists for JSON serialization
    for login in commenters:
        commenters[login]['prs_commented_on'] = list(commenters[login]['prs_commented_on'])

    yield (f"Analyzed {prs_analyzed} PRs ({len(commenters)} commenters)", prs_analyzed, signals, reviewers, commenters)


def _scan_dependencies(org: str, repo: str) -> Generator[tuple, None, None]:
    """
    Scan dependency files for i18n libraries and TMS.

    Yields:
        Tuples of (log_message, result_dict)
    """
    yield ("Scanning dependencies...", None)

    for dep_file in Config.DEPENDENCY_FILES:
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

            if content_b64:
                try:
                    content = base64.b64decode(content_b64).decode('utf-8')
                    result = detect_dependencies_in_content(content, dep_file)

                    if result['i18n_libraries'] or result['tms_detected']:
                        libs_str = ', '.join(result['i18n_libraries']) if result['i18n_libraries'] else 'none'
                        frameworks_str = ', '.join(result['frameworks']) if result['frameworks'] else ''
                        tms_str = ', '.join(result['tms_detected']) if result['tms_detected'] else 'none'
                        
                        msg = f"Found in {dep_file}: {libs_str}"
                        if frameworks_str:
                            msg += f" ({frameworks_str})"
                        if result['tms_detected']:
                            msg += f" | TMS: {tms_str}"
                        
                        yield (msg, result)
                except Exception:
                    pass

        except requests.RequestException:
            continue

    yield ("Dependency scan complete", None)


def _scan_locale_inventory(org: str, repo: str) -> Generator[tuple, None, None]:
    """
    Scan for locale files in common directories (Inventory Scan).

    Yields:
        Tuples of (log_message, result_dict)
    """
    yield ("Scanning locale inventory...", None)

    for locale_path in Config.LOCALE_PATHS:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{locale_path}"
            response = requests.get(
                url,
                headers=get_github_headers(),
                timeout=15
            )

            if response.status_code != 200:
                continue

            contents = response.json()

            if isinstance(contents, list):
                locale_extensions = ('.json', '.yml', '.yaml', '.properties', '.xliff', '.po', '.pot', '.strings', '.resx', '.arb')
                locale_files = []

                for item in contents:
                    if item.get('type') == 'file':
                        name = item.get('name', '')
                        if name.endswith(locale_extensions):
                            parsed = parse_locale_code(name)
                            if parsed.get('lang'):
                                locale_files.append(name)

                if locale_files:
                    yield (f"Found {len(locale_files)} locale file(s) in /{locale_path}/", {
                        'path': locale_path,
                        'locales': locale_files
                    })

        except requests.RequestException:
            continue

    yield ("Locale inventory complete", None)


def _scan_seo_i18n_config(org: str, repo: str) -> Generator[tuple, None, None]:
    """
    Scan for SEO/marketing i18n configuration files.

    Detects next.config.js, nuxt.config.js, sitemap.xml for international SEO strategy.
    This is the STRONGEST signal of marketing intent vs just engineering cleanup.

    Yields:
        Tuples of (log_message, result_dict)
    """
    yield ("Scanning for SEO i18n configs...", None)

    for config_file in Config.SEO_CONFIG_FILES:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{config_file}"
            response = requests.get(
                url,
                headers=get_github_headers(),
                timeout=15
            )

            if response.status_code != 200:
                continue

            file_data = response.json()
            content_b64 = file_data.get('content', '')

            if content_b64:
                try:
                    content = base64.b64decode(content_b64).decode('utf-8')
                    result = _parse_seo_i18n_config(content, config_file)

                    if result:
                        yield (f"SEO i18n detected in {config_file}: {result.get('locales', [])}", result)

                except Exception:
                    pass

        except requests.RequestException:
            continue

    yield ("SEO config scan complete", None)


def _parse_seo_i18n_config(content: str, filename: str) -> Optional[dict]:
    """
    Parse SEO config file for i18n settings.

    Handles:
    - next.config.js: i18n: { locales: ['en', 'fr', 'es'] }
    - nuxt.config.js: i18n module with locales
    - sitemap.xml: hreflang alternate links
    """
    result = None

    if 'next.config' in filename:
        # Look for i18n config block
        i18n_match = re.search(r'i18n\s*:\s*\{([^}]+)\}', content, re.DOTALL)
        if i18n_match:
            i18n_block = i18n_match.group(1)
            # Extract locales array
            locales_match = re.search(r'locales\s*:\s*\[([^\]]+)\]', i18n_block)
            if locales_match:
                locales_str = locales_match.group(1)
                # Parse locale strings
                locales = re.findall(r'["\']([a-zA-Z-]+)["\']', locales_str)
                if locales:
                    result = {
                        'type': 'seo_i18n_config',
                        'source': filename,
                        'locales': locales,
                        'framework': 'Next.js',
                        'is_marketing_intent': True
                    }

    elif 'nuxt.config' in filename:
        # Look for i18n module
        if 'i18n' in content.lower():
            locales_match = re.search(r'locales\s*:\s*\[([^\]]+)\]', content)
            if locales_match:
                locales_str = locales_match.group(1)
                locales = re.findall(r'["\']([a-zA-Z-]+)["\']', locales_str)
                if locales:
                    result = {
                        'type': 'seo_i18n_config',
                        'source': filename,
                        'locales': locales,
                        'framework': 'Nuxt',
                        'is_marketing_intent': True
                    }

    elif 'sitemap.xml' in filename:
        # Look for hreflang in sitemap
        if 'hreflang' in content.lower():
            # Extract all hreflang values
            hreflang_matches = re.findall(r'hreflang=["\']([^"\']+)["\']', content, re.IGNORECASE)
            if hreflang_matches:
                result = {
                    'type': 'seo_i18n_config',
                    'source': filename,
                    'locales': list(set(hreflang_matches)),
                    'framework': 'SEO/Sitemap',
                    'is_marketing_intent': True
                }

    return result


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


def _get_user_profile(login: str) -> Optional[dict]:
    """Fetch GitHub user profile details."""
    try:
        url = f"{Config.GITHUB_API_BASE}/users/{login}"
        response = requests.get(
            url,
            headers=get_github_headers(),
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


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

    This provides immediate gratification to the user by showing
    findings as they happen, not just at the end.
    """
    return f"data: SIGNAL_FOUND:{json.dumps(signal)}\n\n"


def calculate_confidence_score(scan_results: dict) -> int:
    """
    Calculate a confidence score for the lead based on detected signals.

    Returns a score from 0-100 representing the probability of
    this being a high-value localization opportunity.
    """
    score = 0
    weights = Config.CONFIDENCE_WEIGHTS

    # +10 pts for each locales/ folder found
    locale_paths = len(scan_results.get('locale_inventory', {}).get('paths_found', []))
    score += min(locale_paths * weights['locale_folder'], 30)

    # +20 pts for i18n PR in last 30 days
    recent_prs = [s for s in scan_results.get('signals', [])
                  if s.get('type') == 'pull_request']
    if recent_prs:
        score += weights['i18n_pr_recent']

    # +50 pts for new language added (detected via file additions)
    new_files = [s for s in scan_results.get('signals', [])
                 if s.get('type') == 'file_change' and s.get('status') == 'added']
    if new_files:
        score += weights['new_language_added']

    # +40 pts for competitor config file
    if scan_results.get('competitor_detection', {}).get('config_files_found'):
        score += weights['competitor_config']

    # +30 pts for TMS in dependencies
    if scan_results.get('competitor_detection', {}).get('tms_in_dependencies'):
        score += weights['tms_in_deps']

    # +15 pts per frustration signal (max 3 = 45 pts)
    frustration_count = min(len(scan_results.get('frustration_signals', [])), 3)
    score += frustration_count * weights['frustration_signal']

    # +25 pts if >70% human translation edits
    if scan_results.get('developer_translator_metric', {}).get('is_high_pain'):
        score += weights['human_translator_pain']

    # +10 pts per i18n library detected (max 30 pts)
    i18n_libs = len(scan_results.get('tech_stack', {}).get('i18n_libraries', []))
    score += min(i18n_libs * weights['i18n_library'], 30)

    # +35 pts for greenfield opportunity
    if scan_results.get('is_greenfield'):
        score += weights['greenfield_opportunity']

    # +20 pts for detected market expansion
    if scan_results.get('market_insights', {}).get('primary_market'):
        score += weights['market_expansion']

    # +20 pts for reviewer bottleneck
    if scan_results.get('reviewer_bottleneck', {}).get('is_bottleneck'):
        score += weights['reviewer_bottleneck']

    # +30 pts for SEO i18n config (highest marketing intent signal)
    if scan_results.get('seo_i18n_config', {}).get('has_marketing_intent'):
        score += weights['seo_i18n_config']

    # Cap at 100
    return min(score, 100)
