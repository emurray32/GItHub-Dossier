"""
Weight of Evidence (WoE) Tables and Scoring Constants.

Central repository for all WoE values, half-lives, interaction bonuses,
Bayesian priors, and action thresholds used by the scoring engine.
"""
from scoring.models import SignalCategory, MaturitySegment


# ============================================================
# WoE TABLE — Weight of Evidence per signal type
# ============================================================
# Positive WoE = evidence FOR intent
# Negative WoE = evidence AGAINST intent
# Scale: roughly -3.0 to +3.0 log-odds contribution

WOE_TABLE = {
    # === STRONG SIGNALS (high WoE) ===
    'smoking_gun_fork': 2.5,
    'dependency_injection': 1.8,
    'dependency_injection_preparing': 2.2,  # dep + no locale folders
    'ghost_branch': 1.5,
    'ghost_branch_active': 2.0,  # recently pushed

    # === MEDIUM SIGNALS ===
    'rfc_discussion': 1.2,
    'rfc_discussion_high': 1.8,
    'documentation_intent': 0.8,
    'documentation_intent_high': 1.3,

    # === TMS / CI / INFRA SIGNALS ===
    'tms_config_file': 2.0,      # phraseapp.yml, crowdin.yml, etc.
    'ci_cd_i18n_workflow': 1.6,   # GitHub Actions i18n steps
    'figma_i18n_plugin': 1.4,     # Figma design-system i18n refs
    'monorepo_i18n_package': 1.5, # packages/i18n in monorepo
    'feature_flag_locale': 1.0,   # locale-gated feature flags
    'intl_number_format': 0.6,    # Intl.NumberFormat usage

    # === ENHANCED HEURISTICS ===
    'job_posting_intent': 1.4,
    'regional_domain': 0.7,
    'headless_cms_i18n': 1.2,
    'payment_multi_currency': 1.0,
    'timezone_library': 0.5,
    'ci_localization_pipeline': 1.6,
    'legal_compliance': 0.6,
    'social_multi_region': 0.5,
    'locale_update_velocity': 1.3,
    'api_international': 0.8,

    # === NEGATIVE / DISQUALIFIER SIGNALS ===
    'already_launched': -1.5,
    'mega_corp_launched': -1.0,
    'mega_corp_weak_signals': -0.8,
    'fork_repo': -2.0,
    'archived_repo': -2.0,
    'tutorial_repo': -1.5,
    'sdk_library_repo': -1.2,
}

# Default WoE for unknown signal types
DEFAULT_WOE = 0.3


# ============================================================
# SIGNAL CATEGORY MAPPING
# ============================================================
# Maps signal type strings to SignalCategory enum

SIGNAL_CATEGORY_MAP = {
    'smoking_gun_fork': SignalCategory.FORK,
    'dependency_injection': SignalCategory.LIBRARY_INSTALL,
    'ghost_branch': SignalCategory.BRANCH_COMMIT,
    'rfc_discussion': SignalCategory.PR_ISSUE,
    'documentation_intent': SignalCategory.DOC_MENTION,
    'tms_config_file': SignalCategory.TMS_FILE,
    'ci_cd_i18n_workflow': SignalCategory.CI_CD,
    'ci_localization_pipeline': SignalCategory.CI_CD,
    'figma_i18n_plugin': SignalCategory.CONFIG_CHANGE,
    'monorepo_i18n_package': SignalCategory.INFRASTRUCTURE,
    'feature_flag_locale': SignalCategory.INFRASTRUCTURE,
    'intl_number_format': SignalCategory.INFRASTRUCTURE,
    'job_posting_intent': SignalCategory.ENHANCED_HEURISTIC,
    'regional_domain': SignalCategory.ENHANCED_HEURISTIC,
    'headless_cms_i18n': SignalCategory.ENHANCED_HEURISTIC,
    'payment_multi_currency': SignalCategory.ENHANCED_HEURISTIC,
    'timezone_library': SignalCategory.ENHANCED_HEURISTIC,
    'legal_compliance': SignalCategory.ENHANCED_HEURISTIC,
    'social_multi_region': SignalCategory.ENHANCED_HEURISTIC,
    'locale_update_velocity': SignalCategory.ENHANCED_HEURISTIC,
    'api_international': SignalCategory.ENHANCED_HEURISTIC,
    'already_launched': SignalCategory.CONFIG_CHANGE,
}


# ============================================================
# HALF-LIFE TABLE — Exponential decay per signal category
# ============================================================
# In days. Formula: decayed = raw × 0.5^(age / half_life)

HALF_LIFE_DAYS = {
    SignalCategory.BRANCH_COMMIT: 21,
    SignalCategory.LIBRARY_INSTALL: 45,
    SignalCategory.PR_ISSUE: 30,
    SignalCategory.CONFIG_CHANGE: 60,
    SignalCategory.DOC_MENTION: 90,
    SignalCategory.TMS_FILE: 60,
    SignalCategory.CI_CD: 45,
    SignalCategory.INFRASTRUCTURE: 90,
    SignalCategory.ENHANCED_HEURISTIC: 60,
    SignalCategory.FORK: 45,
}


# ============================================================
# SIGNAL STRENGTH TABLE — Raw strength per signal type
# ============================================================

RAW_STRENGTH_TABLE = {
    'smoking_gun_fork': 3.0,
    'dependency_injection': 2.0,
    'dependency_injection_preparing': 2.5,
    'ghost_branch': 1.5,
    'ghost_branch_active': 2.0,
    'rfc_discussion': 1.2,
    'rfc_discussion_high': 2.0,
    'documentation_intent': 0.8,
    'documentation_intent_high': 1.5,
    'tms_config_file': 2.5,
    'ci_cd_i18n_workflow': 1.8,
    'figma_i18n_plugin': 1.5,
    'monorepo_i18n_package': 1.8,
    'feature_flag_locale': 1.0,
    'intl_number_format': 0.6,
    'job_posting_intent': 1.5,
    'regional_domain': 0.7,
    'headless_cms_i18n': 1.3,
    'payment_multi_currency': 1.0,
    'timezone_library': 0.5,
    'ci_localization_pipeline': 1.8,
    'legal_compliance': 0.6,
    'social_multi_region': 0.5,
    'locale_update_velocity': 1.5,
    'api_international': 0.8,
    'already_launched': 0.5,
}

DEFAULT_RAW_STRENGTH = 1.0


# ============================================================
# INTERACTION BONUS TABLE — Signal pair synergies
# ============================================================
# When both signals in a pair are present, add this bonus to log-odds

INTERACTION_BONUSES = {
    ('dependency_injection', 'ghost_branch'): 0.8,     # Lib + active branch = strong
    ('dependency_injection', 'rfc_discussion'): 0.6,    # Lib + discussion
    ('smoking_gun_fork', 'ghost_branch'): 1.0,          # Fork + branch = very strong
    ('rfc_discussion', 'ghost_branch'): 0.5,            # Talking + building
    ('tms_config_file', 'ci_cd_i18n_workflow'): 0.7,    # TMS + CI pipeline
    ('dependency_injection', 'tms_config_file'): 0.6,   # Lib + TMS config
    ('job_posting_intent', 'dependency_injection'): 0.5, # Hiring + building
    ('headless_cms_i18n', 'dependency_injection'): 0.4,  # CMS + lib
}


# ============================================================
# BAYESIAN PRIORS PER MATURITY SEGMENT
# ============================================================
# Base prior P(intent) per segment, converted to log-odds for updating

SEGMENT_PRIORS = {
    MaturitySegment.PRE_I18N: 0.05,            # Very unlikely
    MaturitySegment.PREPARING: 0.60,            # Likely
    MaturitySegment.ACTIVE_IMPLEMENTATION: 0.75, # Very likely
    MaturitySegment.RECENTLY_LAUNCHED: 0.30,     # Moderate (already have solution)
    MaturitySegment.MATURE_MIDMARKET: 0.20,      # Lower (entrenched)
    MaturitySegment.ENTERPRISE_SCALE: 0.40,      # Moderate (complex needs)
}


# ============================================================
# ACTION THRESHOLDS
# ============================================================
# P(intent) thresholds for sales actions

THRESHOLDS = {
    'hot_lead': 0.75,       # Immediate outreach
    'warm_lead': 0.50,      # Nurture sequence
    'monitor': 0.30,        # Add to watch list
    'cold': 0.15,           # Low priority
}


# ============================================================
# READINESS COMPONENT WEIGHTS
# ============================================================

READINESS_WEIGHTS = {
    'preparation': 0.40,
    'velocity': 0.30,
    'launch_gap': 0.20,
    'pain_intensity': 0.10,
}


# ============================================================
# ORG SCORE COMPONENT WEIGHTS
# ============================================================

ORG_SCORE_WEIGHTS = {
    'peak': 0.30,
    'mean_top3': 0.25,
    'breadth': 0.20,
    'high_value_concentration': 0.15,
    'momentum': 0.10,
}


# ============================================================
# COMPANY SIZE THRESHOLDS
# ============================================================

SIZE_THRESHOLDS = {
    'small': {'max_stars': 500, 'max_repos': 20},
    'medium': {'max_stars': 5000, 'max_repos': 100},
    'large': {'max_stars': 20000, 'max_repos': 400},
    # 'enterprise' = anything above large
}
