"""
Signal Enrichment — Part 1.

Wraps raw signal dicts into EnrichedSignal objects with computed metadata:
raw_strength, age_in_days, source_context, signal_category, woe_value.

Also provides new detection functions for TMS files, CI/CD workflows,
Figma signals, and infrastructure patterns.
"""
import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from scoring.models import EnrichedSignal, SignalCategory
from scoring.woe_tables import (
    WOE_TABLE,
    DEFAULT_WOE,
    SIGNAL_CATEGORY_MAP,
    RAW_STRENGTH_TABLE,
    DEFAULT_RAW_STRENGTH,
)


def enrich_signals(raw_signals: List[Dict[str, Any]], scan_results: Dict[str, Any]) -> List[EnrichedSignal]:
    """Enrich raw signal dicts with scoring metadata.

    Args:
        raw_signals: List of signal dicts from the scanner.
        scan_results: Full scan_results dict for context.

    Returns:
        List of EnrichedSignal objects with WoE, strength, age, category.
    """
    enriched = []

    for signal in raw_signals:
        es = EnrichedSignal.from_legacy_dict(signal)

        # Determine effective signal type for lookups
        signal_type = _resolve_signal_type(signal)

        # Assign signal category
        es.signal_category = SIGNAL_CATEGORY_MAP.get(signal_type, SignalCategory.DOC_MENTION)

        # Assign WoE value
        es.woe_value = WOE_TABLE.get(signal_type, DEFAULT_WOE)

        # Assign raw strength
        es.raw_strength = RAW_STRENGTH_TABLE.get(signal_type, DEFAULT_RAW_STRENGTH)
        es.decayed_strength = es.raw_strength  # Will be decayed later

        # Compute age_in_days
        es.age_in_days = _compute_age_days(signal)

        # Build source context string
        es.source_context = _build_source_context(signal, scan_results)

        enriched.append(es)

    # Add synthetic signals from new detections
    enriched.extend(_detect_new_signals(scan_results))

    return enriched


def _resolve_signal_type(signal: Dict[str, Any]) -> str:
    """Resolve the effective signal type for WoE/strength lookups.

    Handles sub-types like dependency_injection with goldilocks_status='preparing'.
    """
    sig_type = signal.get('type', signal.get('Signal', 'unknown'))

    # Dependency injection with preparing status gets higher WoE
    if sig_type == 'dependency_injection':
        if signal.get('goldilocks_status') == 'preparing' or signal.get('gap_verified'):
            return 'dependency_injection_preparing'

    # RFC discussion with HIGH priority
    if sig_type == 'rfc_discussion' and signal.get('priority') == 'HIGH':
        return 'rfc_discussion_high'

    # Documentation intent with HIGH priority
    if sig_type == 'documentation_intent' and signal.get('priority') == 'HIGH':
        return 'documentation_intent_high'

    # Ghost branch with recent push
    if sig_type == 'ghost_branch':
        pushed_at = signal.get('pushed_at') or signal.get('last_commit_date')
        if pushed_at:
            age = _compute_age_from_timestamp(pushed_at)
            if age is not None and age <= 14:
                return 'ghost_branch_active'

    return sig_type


def _compute_age_days(signal: Dict[str, Any]) -> Optional[int]:
    """Compute the age of a signal in days from its timestamps."""
    timestamp_str = (
        signal.get('detected_at')
        or signal.get('created_at')
        or signal.get('pushed_at')
        or signal.get('last_commit_date')
        or signal.get('timestamp')
    )
    if not timestamp_str:
        return None
    return _compute_age_from_timestamp(timestamp_str)


def _compute_age_from_timestamp(timestamp_str: str) -> Optional[int]:
    """Parse a timestamp string and return age in days."""
    if not isinstance(timestamp_str, str):
        return None

    now = datetime.now(timezone.utc)

    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(timestamp_str[:19], fmt.replace('Z', ''))
            dt = dt.replace(tzinfo=timezone.utc)
            delta = now - dt
            return max(0, delta.days)
        except (ValueError, TypeError):
            continue

    return None


def _build_source_context(signal: Dict[str, Any], scan_results: Dict[str, Any]) -> str:
    """Build a human-readable source context string."""
    parts = []

    repo = signal.get('repo', '')
    if repo:
        parts.append(f"repo:{repo}")

    file_path = signal.get('file', signal.get('Link', ''))
    if file_path and 'github.com' not in str(file_path):
        parts.append(f"file:{file_path}")

    sig_type = signal.get('type', '')
    if sig_type:
        parts.append(f"type:{sig_type}")

    return ' | '.join(parts)


def _detect_new_signals(scan_results: Dict[str, Any]) -> List[EnrichedSignal]:
    """Detect new signal types: TMS files, CI/CD, Figma, infrastructure.

    These are derived from data already in scan_results (repos_scanned,
    signals, etc.) rather than making new API calls.
    """
    new_signals = []

    signals = scan_results.get('signals', [])
    repos = scan_results.get('repos_scanned', [])
    org = scan_results.get('org_login', '')

    # Detect TMS config files from existing signals
    new_signals.extend(_detect_tms_from_signals(signals, org))

    # Detect CI/CD i18n workflows from existing signals
    new_signals.extend(_detect_ci_cd_from_signals(signals, org))

    # Detect Figma signals from existing signals
    new_signals.extend(_detect_figma_from_signals(signals, org))

    # Detect infrastructure signals from existing signals
    new_signals.extend(_detect_infra_from_signals(signals, org))

    return new_signals


def _detect_tms_from_signals(signals: List[Dict], org: str) -> List[EnrichedSignal]:
    """Check existing signals for TMS config file evidence."""
    from config import Config

    tms_signals = []
    seen_tms = set()

    for signal in signals:
        evidence = str(signal.get('Evidence', '') or signal.get('Signal', '')).lower()
        file_path = str(signal.get('file', '') or signal.get('Link', '')).lower()

        for pattern in Config.TMS_FILE_PATTERNS:
            pattern_lower = pattern.lower()
            if pattern_lower in evidence or pattern_lower in file_path:
                tms_key = pattern_lower
                if tms_key not in seen_tms:
                    seen_tms.add(tms_key)
                    es = EnrichedSignal(
                        signal_type='tms_config_file',
                        evidence=f"TMS config detected: {pattern}",
                        company=org,
                        link=signal.get('Link', ''),
                        priority='HIGH',
                        repo=signal.get('repo', ''),
                        signal_category=SignalCategory.TMS_FILE,
                        woe_value=WOE_TABLE.get('tms_config_file', 2.0),
                        raw_strength=RAW_STRENGTH_TABLE.get('tms_config_file', 2.5),
                        decayed_strength=RAW_STRENGTH_TABLE.get('tms_config_file', 2.5),
                        raw_signal=signal,
                    )
                    tms_signals.append(es)

        # Check for TMS CLI keywords in scripts
        for keyword in Config.TMS_CLI_KEYWORDS:
            if keyword.lower() in evidence:
                cli_key = f"cli:{keyword.lower()}"
                if cli_key not in seen_tms:
                    seen_tms.add(cli_key)
                    es = EnrichedSignal(
                        signal_type='tms_config_file',
                        evidence=f"TMS CLI reference: {keyword}",
                        company=org,
                        link=signal.get('Link', ''),
                        priority='HIGH',
                        repo=signal.get('repo', ''),
                        signal_category=SignalCategory.TMS_FILE,
                        woe_value=WOE_TABLE.get('tms_config_file', 2.0),
                        raw_strength=RAW_STRENGTH_TABLE.get('tms_config_file', 2.5),
                        decayed_strength=RAW_STRENGTH_TABLE.get('tms_config_file', 2.5),
                        raw_signal=signal,
                    )
                    tms_signals.append(es)

    return tms_signals


def _detect_ci_cd_from_signals(signals: List[Dict], org: str) -> List[EnrichedSignal]:
    """Check existing signals for CI/CD i18n workflow evidence."""
    from config import Config

    ci_signals = []
    seen_ci = set()

    for signal in signals:
        evidence = str(signal.get('Evidence', '') or signal.get('Signal', '')).lower()

        for keyword in Config.CI_CD_I18N_KEYWORDS:
            if keyword.lower() in evidence and 'workflow' in evidence:
                ci_key = keyword.lower()
                if ci_key not in seen_ci:
                    seen_ci.add(ci_key)
                    es = EnrichedSignal(
                        signal_type='ci_cd_i18n_workflow',
                        evidence=f"CI/CD i18n workflow: {keyword}",
                        company=org,
                        link=signal.get('Link', ''),
                        priority='HIGH',
                        repo=signal.get('repo', ''),
                        signal_category=SignalCategory.CI_CD,
                        woe_value=WOE_TABLE.get('ci_cd_i18n_workflow', 1.6),
                        raw_strength=RAW_STRENGTH_TABLE.get('ci_cd_i18n_workflow', 1.8),
                        decayed_strength=RAW_STRENGTH_TABLE.get('ci_cd_i18n_workflow', 1.8),
                        raw_signal=signal,
                    )
                    ci_signals.append(es)

        for action in Config.CI_CD_I18N_ACTIONS:
            if action.lower() in evidence:
                action_key = f"action:{action.lower()}"
                if action_key not in seen_ci:
                    seen_ci.add(action_key)
                    es = EnrichedSignal(
                        signal_type='ci_cd_i18n_workflow',
                        evidence=f"CI/CD i18n action: {action}",
                        company=org,
                        link=signal.get('Link', ''),
                        priority='HIGH',
                        repo=signal.get('repo', ''),
                        signal_category=SignalCategory.CI_CD,
                        woe_value=WOE_TABLE.get('ci_cd_i18n_workflow', 1.6),
                        raw_strength=RAW_STRENGTH_TABLE.get('ci_cd_i18n_workflow', 1.8),
                        decayed_strength=RAW_STRENGTH_TABLE.get('ci_cd_i18n_workflow', 1.8),
                        raw_signal=signal,
                    )
                    ci_signals.append(es)

    return ci_signals


def _detect_figma_from_signals(signals: List[Dict], org: str) -> List[EnrichedSignal]:
    """Check existing signals for Figma i18n references."""
    from config import Config

    figma_signals = []
    seen_figma = set()

    for signal in signals:
        evidence = str(signal.get('Evidence', '') or signal.get('Signal', '')).lower()

        for keyword in Config.FIGMA_I18N_KEYWORDS:
            if keyword.lower() in evidence:
                figma_key = keyword.lower()
                if figma_key not in seen_figma:
                    seen_figma.add(figma_key)
                    es = EnrichedSignal(
                        signal_type='figma_i18n_plugin',
                        evidence=f"Figma i18n reference: {keyword}",
                        company=org,
                        link=signal.get('Link', ''),
                        priority='MEDIUM',
                        repo=signal.get('repo', ''),
                        signal_category=SignalCategory.CONFIG_CHANGE,
                        woe_value=WOE_TABLE.get('figma_i18n_plugin', 1.4),
                        raw_strength=RAW_STRENGTH_TABLE.get('figma_i18n_plugin', 1.5),
                        decayed_strength=RAW_STRENGTH_TABLE.get('figma_i18n_plugin', 1.5),
                        raw_signal=signal,
                    )
                    figma_signals.append(es)

    return figma_signals


def _detect_infra_from_signals(signals: List[Dict], org: str) -> List[EnrichedSignal]:
    """Check existing signals for infrastructure patterns."""
    from config import Config
    import re

    infra_signals = []
    seen_infra = set()

    for signal in signals:
        evidence = str(signal.get('Evidence', '') or signal.get('Signal', '')).lower()
        file_path = str(signal.get('file', '') or signal.get('Link', '')).lower()

        # Monorepo i18n packages
        for pattern in Config.INFRA_MONOREPO_I18N_PATTERNS:
            if pattern.lower() in evidence or pattern.lower() in file_path:
                infra_key = f"monorepo:{pattern.lower()}"
                if infra_key not in seen_infra:
                    seen_infra.add(infra_key)
                    es = EnrichedSignal(
                        signal_type='monorepo_i18n_package',
                        evidence=f"Monorepo i18n package: {pattern}",
                        company=org,
                        link=signal.get('Link', ''),
                        priority='HIGH',
                        repo=signal.get('repo', ''),
                        signal_category=SignalCategory.INFRASTRUCTURE,
                        woe_value=WOE_TABLE.get('monorepo_i18n_package', 1.5),
                        raw_strength=RAW_STRENGTH_TABLE.get('monorepo_i18n_package', 1.8),
                        decayed_strength=RAW_STRENGTH_TABLE.get('monorepo_i18n_package', 1.8),
                        raw_signal=signal,
                    )
                    infra_signals.append(es)

        # Intl API patterns
        for pattern in Config.INTL_API_PATTERNS:
            if pattern.lower() in evidence:
                intl_key = f"intl:{pattern.lower()}"
                if intl_key not in seen_infra:
                    seen_infra.add(intl_key)
                    es = EnrichedSignal(
                        signal_type='intl_number_format',
                        evidence=f"Intl API usage: {pattern}",
                        company=org,
                        link=signal.get('Link', ''),
                        priority='LOW',
                        repo=signal.get('repo', ''),
                        signal_category=SignalCategory.INFRASTRUCTURE,
                        woe_value=WOE_TABLE.get('intl_number_format', 0.6),
                        raw_strength=RAW_STRENGTH_TABLE.get('intl_number_format', 0.6),
                        decayed_strength=RAW_STRENGTH_TABLE.get('intl_number_format', 0.6),
                        raw_signal=signal,
                    )
                    infra_signals.append(es)

        # Feature flag locale patterns
        for pattern in Config.INFRA_FEATURE_FLAG_LOCALE_PATTERNS:
            if re.search(pattern, evidence, re.IGNORECASE):
                ff_key = f"ff:{pattern}"
                if ff_key not in seen_infra:
                    seen_infra.add(ff_key)
                    es = EnrichedSignal(
                        signal_type='feature_flag_locale',
                        evidence=f"Feature flag locale reference detected",
                        company=org,
                        link=signal.get('Link', ''),
                        priority='MEDIUM',
                        repo=signal.get('repo', ''),
                        signal_category=SignalCategory.INFRASTRUCTURE,
                        woe_value=WOE_TABLE.get('feature_flag_locale', 1.0),
                        raw_strength=RAW_STRENGTH_TABLE.get('feature_flag_locale', 1.0),
                        decayed_strength=RAW_STRENGTH_TABLE.get('feature_flag_locale', 1.0),
                        raw_signal=signal,
                    )
                    infra_signals.append(es)

    return infra_signals
