"""
Signal Verifier Module - LLM-powered false positive detection.

Runs AFTER signal collection and BEFORE tier assignment to filter out
misleading signals that don't indicate genuine localization needs.

Three layers of verification:
1. Hard filters (zero-cost, instant) - Docusaurus defaults, forked repos, wrong org mappings
2. Heuristic filters (zero-cost, fast) - Published i18n libraries, SDK/docs repos, keyword mismatches
3. LLM deep verification (low-cost, for ambiguous cases) - GPT-5-mini contextual analysis
"""

import json
import os
import re
from typing import Optional

# Try to import OpenAI for LLM verification
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


# ============================================================
# LAYER 1: HARD FILTERS (zero-cost, instant disqualification)
# ============================================================

# Documentation frameworks that ship with i18n config stubs by default
DOCS_FRAMEWORK_FILES = {
    'docusaurus.config.js',
    'docusaurus.config.ts',
    'mkdocs.yml',
    'hugo.toml',
    'hugo.yaml',
    'hugo.json',
    'gatsby-config.js',
    'gatsby-config.ts',
    'vuepress.config.js',
    'vuepress.config.ts',
    '.vitepress/config.js',
    '.vitepress/config.ts',
}

# Repos that are clearly NOT product code
NON_PRODUCT_REPO_PATTERNS = [
    r'.*[-_]docs?$',           # *-docs, *-doc
    r'.*[-_]documentation$',
    r'.*[-_]sdk[-_]docs?$',    # *-sdk-docs
    r'.*[-_]api[-_]docs?$',    # *-api-docs
    r'.*\.github\.io$',        # GitHub Pages sites
    r'.*[-_]samples?$',        # *-samples
    r'.*[-_]examples?$',       # *-examples
    r'.*[-_]demo$',            # *-demo
    r'.*[-_]tutorial$',        # *-tutorial
    r'.*[-_]playground$',      # *-playground
    r'.*[-_]boilerplate$',     # *-boilerplate
    r'.*[-_]template$',        # *-template
    r'.*[-_]starter$',         # *-starter
]

# GitHub orgs that are actually localization/translation TOOL makers
# (not companies that NEED localization services)
LOCALIZATION_TOOL_ORGS = {
    'projectfluent',   # Mozilla's Project Fluent
    'i18next',         # i18next framework
    'formatjs',        # FormatJS (react-intl)
    'lingui',          # LinguiJS
    'transifex',       # Transifex TMS
    'crowdin',         # Crowdin TMS
    'lokalise',        # Lokalise TMS
    'phrase',          # Phrase TMS (your own company!)
    'weblate',         # Weblate
    'pontoon',         # Mozilla Pontoon
    'globalizejs',     # Globalize
    'polyglot',        # Airbnb Polyglot
}

# Keywords that trigger false positives when "translate" doesn't mean language
FALSE_TRANSLATE_CONTEXTS = [
    'translate colors',
    'translate coordinates',
    'translate transform',
    'css translate',
    'translate3d',
    'translatex',
    'translatey',
    'translatez',
    'translate matrix',
    'translate position',
    'translate offset',
    'palette',           # color palette translation
    'rgb',
    'hex color',
]


def apply_hard_filters(scan_data: dict) -> dict:
    """
    Apply zero-cost hard filters to scan data before tier assignment.
    
    Returns modified scan_data with:
    - Filtered signals (false positives removed)
    - verification_results dict showing what was filtered and why
    """
    org_login = (scan_data.get('org_login', '') or '').lower()
    signals = scan_data.get('signals', [])
    signal_summary = scan_data.get('signal_summary', {})
    
    verification = {
        'hard_filters_applied': [],
        'signals_removed': 0,
        'original_signal_count': len(signals),
        'false_positive_reasons': [],
        'verified': False,
    }
    
    # ---- Filter 1: Wrong org mapping (localization tool orgs) ----
    if org_login in LOCALIZATION_TOOL_ORGS:
        verification['hard_filters_applied'].append('WRONG_ORG_MAPPING')
        verification['false_positive_reasons'].append(
            f"GitHub org @{org_login} is a localization/translation TOOL maker, "
            f"not a company that needs localization services. Wrong mapping."
        )
        verification['is_false_positive'] = True
        verification['recommended_tier'] = 0  # Demote to tracking
        scan_data['verification'] = verification
        return scan_data
    
    # ---- Filter 2: Check if ALL signals come from docs/SDK repos ----
    filtered_signals = []
    docs_only_signals = []
    
    for signal in signals:
        repo_name = (signal.get('repo', '') or '').lower()
        is_docs_repo = False
        
        # Check repo name against non-product patterns
        for pattern in NON_PRODUCT_REPO_PATTERNS:
            if re.match(pattern, repo_name):
                is_docs_repo = True
                break
        
        # Check if repo contains docs framework config files
        if signal.get('docs_framework_detected'):
            is_docs_repo = True
        
        if is_docs_repo:
            docs_only_signals.append(signal)
            verification['hard_filters_applied'].append(f'DOCS_REPO:{repo_name}')
        else:
            filtered_signals.append(signal)
    
    if docs_only_signals and not filtered_signals:
        verification['false_positive_reasons'].append(
            f"ALL {len(docs_only_signals)} signals came from documentation/SDK repos, "
            f"not product code. Docs frameworks (Docusaurus, MkDocs, etc.) ship with "
            f"i18n config stubs by default."
        )
        verification['is_false_positive'] = True
        verification['recommended_tier'] = 0
    
    # ---- Filter 3: Forked repo check ----
    forked_signals = [s for s in signals if s.get('is_fork', False)]
    if forked_signals and len(forked_signals) == len(signals):
        verification['hard_filters_applied'].append('ALL_FORKED_REPOS')
        verification['false_positive_reasons'].append(
            f"ALL signals came from forked repositories. Forks indicate the company "
            f"is using existing i18n tools (already localized), not building new ones."
        )
        verification['is_false_positive'] = True
        verification['recommended_tier'] = 0
    
    # ---- Filter 4: Published i18n library detection ----
    for signal in signals:
        repo_name = (signal.get('repo', '') or '').lower()
        # If the repo IS an i18n library (has "i18n" in name + has npm releases/stars)
        if any(kw in repo_name for kw in ['i18n', 'intl', 'locale', 'l10n', 'translation']):
            repo_stars = signal.get('repo_stars', 0) or 0
            if repo_stars > 5:  # Published library with community usage
                verification['hard_filters_applied'].append(f'PUBLISHED_I18N_LIB:{repo_name}')
                verification['false_positive_reasons'].append(
                    f"Repo '{repo_name}' appears to be a published i18n library "
                    f"({repo_stars} stars). Company ALREADY HAS localization, "
                    f"not building it."
                )
                verification['is_false_positive'] = True
                verification['recommended_tier'] = 0
    
    # ---- Filter 5: False "translate" keyword matching ----
    for signal in signals:
        evidence = (signal.get('Evidence', '') or signal.get('title', '') or '').lower()
        for false_context in FALSE_TRANSLATE_CONTEXTS:
            if false_context in evidence:
                verification['hard_filters_applied'].append(f'FALSE_TRANSLATE:{false_context}')
                verification['false_positive_reasons'].append(
                    f"Signal contains '{false_context}' - this is about data/visual "
                    f"transformation, not language translation."
                )
                # Remove this specific signal
                if signal in filtered_signals:
                    filtered_signals.remove(signal)
                verification['signals_removed'] += 1
                break
    
    # Update scan_data with filtered signals
    if filtered_signals != signals:
        verification['signals_removed'] = len(signals) - len(filtered_signals)
    
    scan_data['verification'] = verification
    return scan_data


# ============================================================
# LAYER 2: HEURISTIC FILTERS (zero-cost, pattern-based)
# ============================================================

def apply_heuristic_filters(scan_data: dict) -> dict:
    """
    Apply heuristic pattern matching to detect common false positive scenarios.
    Runs after hard filters.
    """
    verification = scan_data.get('verification', {})
    org_login = (scan_data.get('org_login', '') or '').lower()
    signals = scan_data.get('signals', [])
    
    # Already flagged as false positive by hard filters
    if verification.get('is_false_positive'):
        return scan_data
    
    # ---- Heuristic 1: Large org with only SDK/CLI repos public ----
    repos_scanned = scan_data.get('repos_scanned', 0) or 0
    total_org_repos = scan_data.get('total_org_repos', 0) or 0
    
    if total_org_repos > 50:
        # Large org - their product is almost certainly in private repos
        # Public repos are likely SDKs, CLIs, docs
        repo_types = set()
        for signal in signals:
            repo_name = (signal.get('repo', '') or '').lower()
            if any(kw in repo_name for kw in ['sdk', 'cli', 'api', 'client', 'docs', 'doc']):
                repo_types.add('tooling')
            else:
                repo_types.add('other')
        
        if repo_types == {'tooling'}:
            verification.setdefault('heuristic_filters_applied', []).append('LARGE_ORG_SDK_ONLY')
            verification.setdefault('false_positive_reasons', []).append(
                f"Large org ({total_org_repos} repos) but ALL signals came from "
                f"SDK/CLI/API/docs repos. Product code is likely private."
            )
            verification['is_likely_false_positive'] = True
            verification['confidence'] = 'medium'
    
    # ---- Heuristic 2: Docusaurus default i18n config detection ----
    for signal in signals:
        evidence = (signal.get('Evidence', '') or '').lower()
        if 'docusaurus' in evidence or 'docusaurus' in (signal.get('repo', '') or '').lower():
            if 'locales: ["en"]' in evidence or 'defaultlocale' in evidence:
                verification.setdefault('heuristic_filters_applied', []).append('DOCUSAURUS_DEFAULT')
                verification.setdefault('false_positive_reasons', []).append(
                    "Docusaurus default i18n config detected (only English locale). "
                    "This is boilerplate, not genuine localization work."
                )
                verification['is_likely_false_positive'] = True
                verification['confidence'] = 'high'
    
    scan_data['verification'] = verification
    return scan_data


# ============================================================
# LAYER 3: LLM DEEP VERIFICATION (low-cost, for ambiguous cases)
# ============================================================

def apply_llm_verification(scan_data: dict) -> dict:
    """
    Use GPT-5-mini to verify ambiguous signals that passed hard/heuristic filters.
    Only called when hard/heuristic filters didn't produce a definitive answer.
    
    Cost: ~$0.001-0.003 per verification call (GPT-5-mini pricing).
    """
    verification = scan_data.get('verification', {})
    
    # Skip if already definitively classified
    if verification.get('is_false_positive'):
        verification['llm_verification'] = 'SKIPPED - already classified as false positive'
        scan_data['verification'] = verification
        return scan_data
    
    # Skip if no signals to verify
    signals = scan_data.get('signals', [])
    if not signals:
        verification['llm_verification'] = 'SKIPPED - no signals'
        scan_data['verification'] = verification
        return scan_data
    
    if not OPENAI_AVAILABLE:
        verification['llm_verification'] = 'SKIPPED - OpenAI not available'
        scan_data['verification'] = verification
        return scan_data
    
    api_key = os.environ.get('AI_INTEGRATIONS_OPENAI_API_KEY', '')
    base_url = os.environ.get('AI_INTEGRATIONS_OPENAI_BASE_URL', '')
    if not api_key or not base_url:
        verification['llm_verification'] = 'SKIPPED - no API key or base URL'
        scan_data['verification'] = verification
        return scan_data
    
    # Build context for the LLM
    company_name = scan_data.get('company_name', 'Unknown')
    org_login = scan_data.get('org_login', '')
    total_org_repos = scan_data.get('total_org_repos', 0)
    
    # Collect signal evidence
    signal_evidence = []
    for s in signals[:10]:  # Limit to 10 signals to keep cost low
        signal_evidence.append({
            'type': s.get('type', s.get('Signal', 'unknown')),
            'evidence': (s.get('Evidence', s.get('title', '')))[:200],
            'repo': s.get('repo', ''),
            'is_fork': s.get('is_fork', False),
            'priority': s.get('priority', 'MEDIUM'),
        })
    
    # Collect repo descriptions
    repo_descriptions = []
    seen_repos = set()
    for s in signals:
        repo = s.get('repo', '')
        if repo and repo not in seen_repos:
            seen_repos.add(repo)
            repo_descriptions.append({
                'name': repo,
                'description': s.get('repo_description', ''),
                'is_fork': s.get('is_fork', False),
                'stars': s.get('repo_stars', 0),
            })
    
    prompt = f"""You are a signal quality analyst for a B2B sales tool that finds companies that need localization/translation services.

TASK: Evaluate whether these GitHub signals indicate GENUINE localization need, or are FALSE POSITIVES.

COMPANY: {company_name}
GITHUB ORG: @{org_login} ({total_org_repos} total repos)

SIGNALS FOUND:
{json.dumps(signal_evidence, indent=2)}

REPOS INVOLVED:
{json.dumps(repo_descriptions, indent=2)}

COMMON FALSE POSITIVE PATTERNS TO CHECK:
1. Documentation site i18n configs (Docusaurus, MkDocs, Hugo) - these ship with i18n stubs by default
2. Published/mature i18n libraries (company ALREADY has localization)
3. Forked repos (company is USING existing tools, not building new ones)
4. SDK/CLI/API client repos (not the actual product)
5. "translate" keyword used for non-language purposes (CSS transforms, coordinate transforms, color mapping)
6. Wrong org-to-company mapping (org doesn't belong to the company)
7. Large enterprise companies that obviously already support multiple languages

RESPOND WITH EXACTLY THIS JSON:
{{
  "verdict": "TRUE_SIGNAL" | "FALSE_POSITIVE" | "UNCERTAIN",
  "confidence": 0.0 to 1.0,
  "reason": "One sentence explanation",
  "false_positive_type": "DOCS_FRAMEWORK" | "PUBLISHED_LIBRARY" | "FORKED_REPO" | "SDK_NOT_PRODUCT" | "KEYWORD_MISMATCH" | "WRONG_ORG" | "ALREADY_LOCALIZED" | "NONE",
  "recommendation": "KEEP_TIER" | "DOWNGRADE_TO_TRACKING" | "REMOVE"
}}"""

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Cost-effective model
            messages=[
                {"role": "system", "content": "You are a precise signal quality analyst. Return ONLY valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,  # Low temperature for consistency
            max_tokens=300,
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # Parse JSON response
        if result_text.startswith('```'):
            result_text = result_text.split('\n', 1)[1] if '\n' in result_text else result_text[3:]
        if result_text.endswith('```'):
            result_text = result_text[:-3]
        result_text = result_text.strip()
        
        llm_result = json.loads(result_text)
        
        verification['llm_verification'] = llm_result
        verification['llm_model'] = 'gpt-4o-mini'
        
        # Apply LLM verdict
        if llm_result.get('verdict') == 'FALSE_POSITIVE' and llm_result.get('confidence', 0) >= 0.7:
            verification['is_false_positive'] = True
            verification['false_positive_reasons'] = verification.get('false_positive_reasons', [])
            verification['false_positive_reasons'].append(
                f"LLM verification ({llm_result.get('confidence', 0):.0%} confidence): "
                f"{llm_result.get('reason', 'No reason provided')}"
            )
            if llm_result.get('recommendation') == 'DOWNGRADE_TO_TRACKING':
                verification['recommended_tier'] = 0
            elif llm_result.get('recommendation') == 'REMOVE':
                verification['recommended_tier'] = 0
        
        elif llm_result.get('verdict') == 'UNCERTAIN':
            verification['is_likely_false_positive'] = True
            verification['confidence'] = 'low'
        
        # Mark as verified
        verification['verified'] = True
        
    except json.JSONDecodeError as e:
        verification['llm_verification'] = f'JSON parse error: {str(e)}'
        verification['llm_raw_response'] = result_text[:500] if 'result_text' in locals() else 'N/A'
    except Exception as e:
        verification['llm_verification'] = f'Error: {str(e)}'
    
    scan_data['verification'] = verification
    return scan_data


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def verify_signals(scan_data: dict, use_llm: bool = True) -> dict:
    """
    Main entry point for signal verification.
    Runs all three layers in order:
    1. Hard filters (instant, free)
    2. Heuristic filters (fast, free)
    3. LLM verification (only if needed, low cost)
    
    Args:
        scan_data: The complete scan results dictionary
        use_llm: Whether to use LLM for ambiguous cases (default True)
    
    Returns:
        Modified scan_data with verification results
    """
    print(f"[VERIFIER] Starting signal verification for {scan_data.get('company_name', 'Unknown')}...")
    
    # Layer 1: Hard filters
    scan_data = apply_hard_filters(scan_data)
    verification = scan_data.get('verification', {})
    
    if verification.get('is_false_positive'):
        fp_reasons = verification.get('false_positive_reasons', [])
        print(f"[VERIFIER] HARD FILTER: False positive detected - {fp_reasons[0] if fp_reasons else 'unknown'}")
        return scan_data
    
    # Layer 2: Heuristic filters
    scan_data = apply_heuristic_filters(scan_data)
    verification = scan_data.get('verification', {})
    
    if verification.get('is_false_positive'):
        fp_reasons = verification.get('false_positive_reasons', [])
        print(f"[VERIFIER] HEURISTIC: False positive detected - {fp_reasons[0] if fp_reasons else 'unknown'}")
        return scan_data
    
    # Layer 3: LLM verification (only for ambiguous cases)
    if use_llm and not verification.get('is_false_positive'):
        # Only call LLM if there are actual signals worth verifying
        signals = scan_data.get('signals', [])
        if signals:
            print(f"[VERIFIER] Running LLM deep verification on {len(signals)} signals...")
            scan_data = apply_llm_verification(scan_data)
            verification = scan_data.get('verification', {})
            
            if verification.get('is_false_positive'):
                llm_result = verification.get('llm_verification', {})
                reason = llm_result.get('reason', 'LLM flagged as false positive') if isinstance(llm_result, dict) else str(llm_result)
                print(f"[VERIFIER] LLM: False positive - {reason}")
            else:
                print(f"[VERIFIER] LLM: Signal verified as genuine")
    
    verification['verified'] = True
    scan_data['verification'] = verification
    
    print(f"[VERIFIER] Verification complete. False positive: {verification.get('is_false_positive', False)}")
    return scan_data
