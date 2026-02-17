"""
Scoring V2 Data Models.

Defines all dataclasses and enums for the multi-stage Bayesian scoring engine.
"""
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Optional, Any
from datetime import datetime


# ============================================================
# ENUMS
# ============================================================

class MaturitySegment(Enum):
    """Six maturity segments for org i18n classification."""
    PRE_I18N = "pre_i18n"
    PREPARING = "preparing"
    ACTIVE_IMPLEMENTATION = "active_implementation"
    RECENTLY_LAUNCHED = "recently_launched"
    MATURE_MIDMARKET = "mature_midmarket"
    ENTERPRISE_SCALE = "enterprise_scale"

    @property
    def display_label(self) -> str:
        labels = {
            "pre_i18n": "Pre-i18n",
            "preparing": "Preparing",
            "active_implementation": "Active Implementation",
            "recently_launched": "Recently Launched",
            "mature_midmarket": "Mature / Midmarket",
            "enterprise_scale": "Enterprise Scale",
        }
        return labels.get(self.value, self.value)

    @property
    def color(self) -> str:
        colors = {
            "pre_i18n": "#6b7280",         # gray
            "preparing": "#f59e0b",         # amber
            "active_implementation": "#3b82f6",  # blue
            "recently_launched": "#10b981",  # green
            "mature_midmarket": "#8b5cf6",   # purple
            "enterprise_scale": "#ef4444",   # red
        }
        return colors.get(self.value, "#6b7280")


class OutreachAngle(Enum):
    """Seven recommended outreach angles."""
    GREENFIELD_EDUCATOR = "greenfield_educator"
    IMPLEMENTATION_PARTNER = "implementation_partner"
    SCALE_OPTIMIZER = "scale_optimizer"
    EXPANSION_ACCELERATOR = "expansion_accelerator"
    MIGRATION_CANDIDATE = "migration_candidate"
    ENTERPRISE_STRATEGIC = "enterprise_strategic"
    PAIN_DRIVEN = "pain_driven"

    @property
    def display_label(self) -> str:
        labels = {
            "greenfield_educator": "Greenfield Educator",
            "implementation_partner": "Implementation Partner",
            "scale_optimizer": "Scale Optimizer",
            "expansion_accelerator": "Expansion Accelerator",
            "migration_candidate": "Migration Candidate",
            "enterprise_strategic": "Enterprise Strategic",
            "pain_driven": "Pain-Driven",
        }
        return labels.get(self.value, self.value)

    @property
    def description(self) -> str:
        descriptions = {
            "greenfield_educator": "No i18n yet — educate on best practices and modern workflows",
            "implementation_partner": "Actively building i18n — help them do it right from the start",
            "scale_optimizer": "Growing pains with current setup — optimize for scale",
            "expansion_accelerator": "Ready to expand to new markets — accelerate their rollout",
            "migration_candidate": "Using a competitor or legacy tool — present migration path",
            "enterprise_strategic": "Large org with complex needs — strategic partnership approach",
            "pain_driven": "Experiencing specific pain points — solve their immediate problem",
        }
        return descriptions.get(self.value, "")


class RiskLevel(Enum):
    """Risk classification for a lead."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def display_label(self) -> str:
        return self.value.upper()


class SignalCategory(Enum):
    """Categories for signal classification."""
    BRANCH_COMMIT = "branch_commit"
    LIBRARY_INSTALL = "library_install"
    PR_ISSUE = "pr_issue"
    CONFIG_CHANGE = "config_change"
    DOC_MENTION = "doc_mention"
    TMS_FILE = "tms_file"
    CI_CD = "ci_cd"
    INFRASTRUCTURE = "infrastructure"
    ENHANCED_HEURISTIC = "enhanced_heuristic"
    FORK = "fork"


# ============================================================
# DATACLASSES
# ============================================================

@dataclass
class EnrichedSignal:
    """A signal enriched with metadata for Bayesian scoring."""
    # Original signal fields
    signal_type: str
    evidence: str
    company: str = ""
    link: str = ""
    priority: str = "MEDIUM"
    repo: str = ""
    file_path: str = ""

    # Enrichment fields
    raw_strength: float = 1.0
    age_in_days: Optional[int] = None
    source_context: str = ""
    signal_category: SignalCategory = SignalCategory.DOC_MENTION
    woe_value: float = 0.0
    decayed_strength: float = 1.0

    # Timestamps
    detected_at: Optional[str] = None
    created_at: Optional[str] = None

    # Filtering
    is_filtered: bool = False
    filter_reason: str = ""
    filter_multiplier: float = 1.0

    # Original raw signal dict for reference
    raw_signal: Dict = field(default_factory=dict)

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Convert to legacy signal dict format for backward compat."""
        d = {
            'Company': self.company,
            'Signal': self.signal_type,
            'Evidence': self.evidence,
            'Link': self.link,
            'priority': self.priority,
            'type': self.signal_type,
            'repo': self.repo,
            'file': self.file_path,
        }
        # Merge any extra keys from raw_signal
        for k, v in self.raw_signal.items():
            if k not in d:
                d[k] = v
        return d

    @classmethod
    def from_legacy_dict(cls, signal: Dict[str, Any]) -> 'EnrichedSignal':
        """Create from a legacy signal dict."""
        return cls(
            signal_type=signal.get('type', signal.get('Signal', 'unknown')),
            evidence=signal.get('Evidence', signal.get('Signal', '')),
            company=signal.get('Company', ''),
            link=signal.get('Link', ''),
            priority=signal.get('priority', 'MEDIUM'),
            repo=signal.get('repo', ''),
            file_path=signal.get('file', signal.get('Link', '')),
            detected_at=signal.get('detected_at'),
            created_at=signal.get('created_at', signal.get('pushed_at', signal.get('timestamp'))),
            raw_signal=signal,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict (JSON-safe)."""
        return {
            'signal_type': self.signal_type,
            'evidence': self.evidence,
            'company': self.company,
            'link': self.link,
            'priority': self.priority,
            'repo': self.repo,
            'file_path': self.file_path,
            'raw_strength': self.raw_strength,
            'age_in_days': self.age_in_days,
            'source_context': self.source_context,
            'signal_category': self.signal_category.value,
            'woe_value': self.woe_value,
            'decayed_strength': self.decayed_strength,
            'detected_at': self.detected_at,
            'created_at': self.created_at,
            'is_filtered': self.is_filtered,
            'filter_reason': self.filter_reason,
            'filter_multiplier': self.filter_multiplier,
        }


@dataclass
class RepoScore:
    """Score for an individual repository."""
    repo_name: str
    tier: int = 0  # 0=excluded, 1=product, 2=support, 3=internal
    tier_weight: float = 0.0
    signal_count: int = 0
    weighted_score: float = 0.0
    signals: List[EnrichedSignal] = field(default_factory=list)
    is_fork: bool = False
    is_archived: bool = False
    stars: int = 0
    last_push: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'repo_name': self.repo_name,
            'tier': self.tier,
            'tier_weight': self.tier_weight,
            'signal_count': self.signal_count,
            'weighted_score': round(self.weighted_score, 4),
            'is_fork': self.is_fork,
            'is_archived': self.is_archived,
            'stars': self.stars,
        }


@dataclass
class OrgScore:
    """Organization-level composite score."""
    peak_score: float = 0.0
    mean_top3: float = 0.0
    breadth: float = 0.0
    high_value_concentration: float = 0.0
    momentum: float = 0.0
    composite: float = 0.0  # Final OrgScore = weighted sum
    cluster_bonus: float = 1.0
    proven_buyer_multiplier: float = 1.0
    repo_scores: List[RepoScore] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'peak_score': round(self.peak_score, 4),
            'mean_top3': round(self.mean_top3, 4),
            'breadth': round(self.breadth, 4),
            'high_value_concentration': round(self.high_value_concentration, 4),
            'momentum': round(self.momentum, 4),
            'composite': round(self.composite, 4),
            'cluster_bonus': round(self.cluster_bonus, 2),
            'proven_buyer_multiplier': round(self.proven_buyer_multiplier, 2),
            'repo_breakdown': [r.to_dict() for r in self.repo_scores],
        }


@dataclass
class ScoringResult:
    """Complete scoring result from the v2 pipeline."""
    # Core scores
    org_intent_score: float = 0.0  # 0.0-1.0 probability
    org_maturity_level: MaturitySegment = MaturitySegment.PRE_I18N
    readiness_index: float = 0.0   # 0.0-1.0 continuous

    # Detail scores
    p_intent: float = 0.0         # Raw Bayesian P(intent)
    log_odds: float = 0.0         # Log-odds from Bayesian updating
    org_score: Optional[OrgScore] = None

    # Classification
    recommended_outreach_angle: OutreachAngle = OutreachAngle.GREENFIELD_EDUCATOR
    risk_level: RiskLevel = RiskLevel.HIGH
    confidence_percent: float = 0.0

    # Signals
    enriched_signals: List[EnrichedSignal] = field(default_factory=list)
    signal_clusters: List[str] = field(default_factory=list)

    # Pipeline metadata
    stage1_passed: bool = False
    stage1_label: str = ""
    primary_repo: str = ""
    confidence_factors: Dict[str, float] = field(default_factory=dict)
    recommended_sales_motion: str = ""

    # Readiness components
    readiness_components: Dict[str, float] = field(default_factory=dict)

    def to_structured_output(self) -> Dict[str, Any]:
        """Produce the full structured JSON output for scoring_v2 namespace."""
        return {
            'org_intent_score': round(self.org_intent_score, 4),
            'org_maturity_level': self.org_maturity_level.value,
            'org_maturity_label': self.org_maturity_level.display_label,
            'org_maturity_color': self.org_maturity_level.color,
            'readiness_index': round(self.readiness_index, 4),
            'readiness_components': {k: round(v, 4) for k, v in self.readiness_components.items()},
            'p_intent': round(self.p_intent, 4),
            'log_odds': round(self.log_odds, 4),
            'org_score': self.org_score.to_dict() if self.org_score else None,
            'recommended_outreach_angle': self.recommended_outreach_angle.value,
            'outreach_angle_label': self.recommended_outreach_angle.display_label,
            'outreach_angle_description': self.recommended_outreach_angle.description,
            'risk_level': self.risk_level.value,
            'risk_level_label': self.risk_level.display_label,
            'confidence_percent': round(self.confidence_percent, 1),
            'confidence_factors': {k: round(v, 4) for k, v in self.confidence_factors.items()},
            'signal_clusters_detected': self.signal_clusters,
            'primary_repo_of_concern': self.primary_repo,
            'recommended_sales_motion': self.recommended_sales_motion,
            'stage1_passed': self.stage1_passed,
            'stage1_label': self.stage1_label,
            'enriched_signal_count': len(self.enriched_signals),
            'enriched_signals': [s.to_dict() for s in self.enriched_signals[:20]],
        }

    def apply_to_scan_results(self, scan_results: Dict[str, Any]) -> None:
        """Set legacy fields on scan_results dict for backward compatibility."""
        from scoring.compat import map_to_legacy
        legacy = map_to_legacy(self)
        scan_results.update(legacy)
