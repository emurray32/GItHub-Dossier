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

    # Phase 4b: Mobile Architecture Scan (iOS & Android Goldilocks)
    yield _sse_log("")
    yield _sse_log("PHASE 4b: Mobile Architecture Scan (iOS & Android)")
    yield _sse_log("-" * 40)
    yield _sse_log("Checking for mobile i18n infrastructure without translations...")

    for idx, repo in enumerate(repos_to_scan[:5], 1):  # Top 5 repos
        repo_name = repo.get('name')
        yield _sse_log(f"  [{idx}/5] Scanning mobile architecture in: {repo_name}")

        for log_msg, signal in _scan_mobile_architecture(org_login, repo_name, company_name):
            if log_msg:
                yield _sse_log(f"    {log_msg}")
            if signal:
                scan_results['signals'].append(signal)
                scan_results['signal_summary']['dependency_injection']['hits'].append(signal)
                scan_results['signal_summary']['dependency_injection']['count'] += 1
                yield _sse_signal(signal)

    mobile_count = scan_results['signal_summary']['dependency_injection']['count'] - dep_count
    yield _sse_log(f"✓ Mobile Architecture scan complete: {mobile_count} signals")

    # Update dep_count to include mobile signals
    dep_count = scan_results['signal_summary']['dependency_injection']['count']

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
    Signal 2: Dependency Injection Scan (Preparing Phase) - GOLDILOCKS ZONE DETECTION

    Target: package.json, Gemfile (focusing on our 4 target libraries)
    Logic: Flag if SMOKING GUN i18n libraries are present

    THE "GAP" REQUIREMENT (Negative Check):
    - A signal is ONLY valid if the repository has NO localization folders
    - If /locales, /i18n, /translations, or /lang exist -> DISQUALIFY (Already Launched)

    TARGET LIBRARIES:
    - babel-plugin-react-intl
    - react-i18next
    - formatjs
    - uppy (only if i18n/locale properties are configured)

    Yields:
        Tuples of (log_message, signal_object)
    """
    for log_msg, signal in _scan_pseudo_localization_configs(org, repo, company):
        yield (log_msg, signal)

    # ============================================================
    # NEGATIVE CHECK: Verify NO exclusion folders exist (with source-only exception)
    # ============================================================
    locale_exists, found_folders, source_only, folder_contents = _check_locale_folders_exist_detailed(org, repo)

    # Track if we found source-only folders for evidence logging
    source_only_evidence = None

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
            yield (f"✅ GOLDILOCKS: {folders_str} contains only source files ({files_str}) - Still a valid lead!", None)
            # Continue to positive check - don't return!
        else:
            # Company has ALREADY LAUNCHED - mark as "Too Late"
            yield (f"⚠️ DISQUALIFIED: Found locale folders ({', '.join(found_folders)}) - Already Launched", None)

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
            yield (f"📉 LOW PRIORITY: {repo} already has locale folders", signal)
            return

    # ============================================================
    # POSITIVE CHECK: Scan for Goldilocks Zone libraries
    # ============================================================
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
                                        'file': dep_file,
                                        'script_name': script_name_text,
                                        'script_command': script_command_text,
                                        'keyword_matched': matched_keyword,
                                        'goldilocks_status': 'preparing',
                                    }
                                    yield (f"🧰 NPM SCRIPT: {script_name_text} in package.json", signal)

                    found_libs = []
                    bdr_explanations = []
                    # Check for our 4 target SMOKING GUN libraries
                    for lib in Config.SMOKING_GUN_LIBS:
                        # Match as dependency name (with quotes for JSON)
                        if f'"{lib}"' in content_lower or f"'{lib}'" in content_lower:
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

                    for lib in Config.CMS_I18N_LIBS:
                        if f'"{lib}"' in content_lower or f"'{lib}'" in content_lower:
                            signal = {
                                'Company': company,
                                'Signal': 'CMS Internationalization',
                                'Evidence': (
                                    "Found CMS Localization tool "
                                    f"'{lib}' - Preparing content strategy for internationalization."
                                ),
                                'Link': file_url,
                                'priority': 'MEDIUM',
                                'type': 'cms_config',
                                'repo': repo,
                                'file': dep_file,
                                'library': lib,
                                'goldilocks_status': 'preparing',
                            }
                            yield (f"🧭 CMS I18N: {lib} in {dep_file}", signal)

                    if found_libs:
                        # This is the GOLDILOCKS ZONE - Library found + NO locale folders (or source-only)!
                        if source_only_evidence:
                            # Source-only case: folder exists but only has source files
                            evidence = f"🎯 GOLDILOCKS ZONE: Found {', '.join(found_libs)} in {dep_file}. {source_only_evidence}"
                            gap_explanation = Config.BDR_TRANSLATIONS.get('locale_folder_source_only', 'Infrastructure ready, only source files')
                        else:
                            # No folder case: no locale folders at all
                            evidence = f"🎯 GOLDILOCKS ZONE: Found {', '.join(found_libs)} in {dep_file} but NO locale folders exist!"
                            gap_explanation = Config.BDR_TRANSLATIONS.get('locale_folder_missing', 'Infrastructure ready, no translations')

                        signal = {
                            'Company': company,
                            'Signal': 'Dependency Injection',
                            'Evidence': evidence,
                            'Link': file_url,
                            'priority': 'CRITICAL',
                            'type': 'dependency_injection',
                            'repo': repo,
                            'file': dep_file,
                            'libraries_found': found_libs,
                            'goldilocks_status': 'preparing',
                            'gap_verified': True,  # Negative check passed!
                            'source_only': source_only_evidence is not None,
                            'bdr_summary': ' | '.join(bdr_explanations),
                            'bdr_gap_explanation': gap_explanation,
                        }

                        yield (f"🎯 GOLDILOCKS ZONE: {', '.join(found_libs)} in {dep_file} - NO TRANSLATIONS YET!", signal)

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
                    yield (f"🔎 PSEUDO-LOCALIZATION: {pattern} found in {config_file}", signal)

        except requests.RequestException:
            continue


def _scan_mobile_architecture(org: str, repo: str, company: str) -> Generator[tuple, None, None]:
    """
    Scan for Mobile Goldilocks Zone signals (iOS and Android).

    iOS Logic:
        - Check if Base.lproj folder exists
        - If YES, search for ANY other folders ending in .lproj
        - Signal if Base.lproj exists AND count of other .lproj folders == 0

    Android Logic:
        - Check if res/values/strings.xml exists
        - If YES, search for ANY folders matching values-[a-z]{2}
        - Signal if strings.xml exists AND count of values-* folders == 0

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

        response = requests.get(
            search_url,
            headers=get_github_headers(),
            timeout=15
        )

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
            yield (f"📱 iOS: Found Base.lproj folder", None)

            # Search for other .lproj folders (translations)
            other_lproj_count = 0
            lproj_search_url = f"{Config.GITHUB_API_BASE}/search/code"
            lproj_params = {
                'q': f'repo:{org}/{repo} path:*.lproj',
                'per_page': 50
            }

            lproj_response = requests.get(
                lproj_search_url,
                headers=get_github_headers(),
                timeout=15
            )

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

            if other_lproj_count == 0:
                # GOLDILOCKS ZONE: Base.lproj exists but no translations!
                signal = {
                    'Company': company,
                    'Signal': 'Mobile Architecture (iOS)',
                    'Evidence': f"🎯 iOS GOLDILOCKS: Base.lproj exists but NO other .lproj folders found - iOS app ready for localization!",
                    'Link': f"https://github.com/{org}/{repo}",
                    'priority': 'CRITICAL',
                    'type': 'mobile_architecture',
                    'platform': 'ios',
                    'repo': repo,
                    'goldilocks_status': 'preparing',
                    'gap_verified': True,
                    'bdr_summary': 'iOS Base Architecture ready, no translations',
                }
                yield (f"🎯 iOS GOLDILOCKS: Base.lproj found, no translations yet!", signal)
            else:
                yield (f"📱 iOS: Found {other_lproj_count} translation .lproj folders - Already localized", None)

    except requests.RequestException as e:
        yield (f"iOS scan error: {str(e)}", None)

    # ============================================================
    # ANDROID DETECTION: strings.xml without translations
    # ============================================================
    android_indicator = Config.MOBILE_INDICATORS.get('android', {})
    strings_xml_path = android_indicator.get('path', 'res/values/strings.xml')

    try:
        # Check if res/values/strings.xml exists
        url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{strings_xml_path}"
        response = requests.get(
            url,
            headers=get_github_headers(),
            timeout=15
        )

        if response.status_code == 200:
            yield (f"📱 Android: Found {strings_xml_path}", None)

            # Search for values-XX folders (language variants)
            values_folder_count = 0

            # Get contents of res/ folder to check for values-* folders
            res_url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/res"
            res_response = requests.get(
                res_url,
                headers=get_github_headers(),
                timeout=15
            )

            if res_response.status_code == 200:
                res_contents = res_response.json()
                if isinstance(res_contents, list):
                    for item in res_contents:
                        item_name = item.get('name', '')
                        item_type = item.get('type', '')
                        # Match values-XX pattern (e.g., values-fr, values-de)
                        if item_type == 'dir' and re.match(r'^values-[a-z]{2}(-[a-zA-Z]+)?$', item_name):
                            values_folder_count += 1

            if values_folder_count == 0:
                # GOLDILOCKS ZONE: strings.xml exists but no translations!
                signal = {
                    'Company': company,
                    'Signal': 'Mobile Architecture (Android)',
                    'Evidence': f"🎯 Android GOLDILOCKS: res/values/strings.xml exists but NO values-* folders found - Android app ready for localization!",
                    'Link': f"https://github.com/{org}/{repo}",
                    'priority': 'CRITICAL',
                    'type': 'mobile_architecture',
                    'platform': 'android',
                    'repo': repo,
                    'goldilocks_status': 'preparing',
                    'gap_verified': True,
                    'bdr_summary': 'Android Strings architecture ready, no translations',
                }
                yield (f"🎯 Android GOLDILOCKS: strings.xml found, no translations yet!", signal)
            else:
                yield (f"📱 Android: Found {values_folder_count} translation values-* folders - Already localized", None)

    except requests.RequestException as e:
        yield (f"Android scan error: {str(e)}", None)


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
    """
    found_folders = []
    folder_contents = {}
    all_source_only = True  # Assume source-only until proven otherwise

    for path in Config.EXCLUSION_FOLDERS:
        try:
            url = f"{Config.GITHUB_API_BASE}/repos/{org}/{repo}/contents/{path}"
            response = requests.get(
                url,
                headers=get_github_headers(),
                timeout=10
            )
            if response.status_code == 200:
                found_folders.append(path)

                # Parse the folder contents
                contents = response.json()
                files_in_folder = []

                if isinstance(contents, list):
                    for item in contents:
                        item_name = item.get('name', '')
                        item_type = item.get('type', '')

                        # Only consider files, not subdirectories
                        if item_type == 'file':
                            files_in_folder.append(item_name)

                folder_contents[path] = files_in_folder

                # Check if this folder contains ONLY source locale files
                if files_in_folder:
                    folder_is_source_only = _is_source_only_folder(files_in_folder)
                    if not folder_is_source_only:
                        all_source_only = False
                else:
                    # Empty folder - treat as source-only (infrastructure ready)
                    pass

        except requests.RequestException:
            continue

    folders_exist = len(found_folders) > 0

    # If no folders found, source_only is not applicable (return False)
    source_only = all_source_only if folders_exist else False

    return (folders_exist, found_folders, source_only, folder_contents)


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
    # Check for LAUNCHED status (disqualifying condition)
    # ============================================================
    already_launched_signals = [s for s in signals if s.get('type') == 'already_launched']
    if already_launched_signals:
        # They have locale folders - TOO LATE
        scan_results['goldilocks_status'] = 'launched'
        scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('launched', 'LOW PRIORITY')
        return Config.GOLDILOCKS_SCORES.get('launched', 10)

    # ============================================================
    # Check for PREPARING status (Goldilocks Zone!)
    # ============================================================
    dep_hits = summary.get('dependency_injection', {}).get('hits', [])
    goldilocks_hits = [h for h in dep_hits if h.get('goldilocks_status') == 'preparing' or h.get('gap_verified')]

    if goldilocks_hits:
        # This is the GOLDILOCKS ZONE!
        # Library found + No locale folders = PERFECT TIMING
        scan_results['goldilocks_status'] = 'preparing'
        scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('preparing', 'HOT LEAD')

        # Score based on number of libraries found (90-100 range)
        base_score = Config.GOLDILOCKS_SCORES.get('preparing_min', 90)
        bonus = min(len(goldilocks_hits) * 5, 10)  # Up to +10 bonus

        # Add bonus for ghost branches (active work)
        ghost_count = summary.get('ghost_branch', {}).get('count', 0)
        if ghost_count > 0:
            bonus = 10  # Max bonus if actively working on it

        return min(base_score + bonus, Config.GOLDILOCKS_SCORES.get('preparing_max', 100))

    # ============================================================
    # MEGA-CORP HEURISTIC: Detect high-maturity orgs without Preparing signals
    # ============================================================
    # Large engineering orgs (Airbnb, Uber, Facebook, etc.) often use custom/internal
    # i18n solutions that won't appear in our standard library scans. If they have
    # significant GitHub presence but no Preparing signals, they've likely already
    # launched with proprietary tooling.
    total_stars = scan_results.get('total_stars', 0)
    public_repos = scan_results.get('org_public_repos', 0)

    if total_stars > 5000 or public_repos > 100:
        # High-maturity org with no Preparing signals = Already Launched
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
    # Check for THINKING status
    # ============================================================
    rfc_count = summary.get('rfc_discussion', {}).get('count', 0)
    if rfc_count > 0:
        scan_results['goldilocks_status'] = 'thinking'
        scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('thinking', 'WARM LEAD')

        # Base score for thinking + bonus for HIGH priority discussions
        base_score = Config.GOLDILOCKS_SCORES.get('thinking', 40)
        high_priority_count = summary.get('rfc_discussion', {}).get('high_priority_count', 0)
        bonus = min(high_priority_count * 10, 20)  # Up to +20 for high priority

        return min(base_score + bonus, 60)  # Cap at 60 for thinking

    # ============================================================
    # Check for Ghost Branches only (Active experimentation)
    # ============================================================
    ghost_count = summary.get('ghost_branch', {}).get('count', 0)
    if ghost_count > 0:
        scan_results['goldilocks_status'] = 'thinking'
        scan_results['lead_status'] = Config.LEAD_STATUS_LABELS.get('thinking', 'WARM LEAD')
        return min(35 + ghost_count * 5, 50)

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
