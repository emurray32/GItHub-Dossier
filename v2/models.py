"""
V2 Pydantic Models — domain objects for the intent-signal-first platform.

These models are used for validation, serialization, and as the contract
between services, routes, and MCP tools. They do NOT enforce database
constraints — that's the schema's job.
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AccountStatus(str, Enum):
    NEW = "new"
    SEQUENCED = "sequenced"
    REVISIT = "revisit"
    NOISE = "noise"


class SignalStatus(str, Enum):
    """Internal bookkeeping status for individual signals.

    NOT the user-facing workflow status. The product workflow is driven
    by AccountStatus (new/sequenced/revisit/noise). Signal status is set
    automatically via cascade when account status changes:
      - account → sequenced/revisit: signals cascade to 'actioned'
      - account → noise: signals cascade to 'archived'
    """
    NEW = "new"
    ACTIONED = "actioned"
    ARCHIVED = "archived"


class EvidenceType(str, Enum):
    SCAN_SIGNAL = "scan_signal"
    CSV_IMPORT = "csv_import"
    MANUAL = "manual"
    COWORK_PUSH = "cowork_push"
    DOCUMENT_PARSE = "document_parse"
    WEBHOOK = "webhook"


class SignalSource(str, Enum):
    GITHUB_SCAN = "github_scan"
    CSV_UPLOAD = "csv_upload"
    MANUAL_ENTRY = "manual_entry"
    COWORK = "cowork"
    WEBHOOK = "webhook"
    DOCUMENT = "document"


class EnrollmentStatus(str, Enum):
    FOUND = "found"
    DRAFTING = "drafting"
    ENROLLED = "enrolled"
    SEQUENCE_COMPLETE = "sequence_complete"


class DraftStatus(str, Enum):
    GENERATED = "generated"
    EDITED = "edited"
    APPROVED = "approved"
    ENROLLED = "enrolled"


class CampaignType(str, Enum):
    PERSONA_BASED = "persona_based"
    SIGNAL_BASED = "signal_based"


class EventType(str, Enum):
    SIGNAL_CREATED = "signal_created"
    ACCOUNT_CREATED = "account_created"
    CAMPAIGN_ASSIGNED = "campaign_assigned"
    PROSPECTS_FOUND = "prospects_found"
    PROSPECT_FILTERED_DNC = "prospect_filtered_dnc"
    DRAFT_GENERATED = "draft_generated"
    DRAFT_REGENERATED = "draft_regenerated"
    DRAFT_APPROVED = "draft_approved"
    PROSPECT_ENROLLED = "prospect_enrolled"
    SEQUENCE_COMPLETED = "sequence_completed"
    REVISIT_SIGNAL_CREATED = "revisit_signal_created"
    ACCOUNT_MARKED_NOISE = "account_marked_noise"
    CSV_IMPORTED = "csv_imported"


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------

class IntentSignal(BaseModel):
    id: Optional[int] = None
    account_id: int
    signal_description: str
    evidence_type: str = EvidenceType.MANUAL.value
    evidence_value: Optional[str] = None
    signal_type: Optional[str] = None
    signal_source: str = SignalSource.MANUAL_ENTRY.value
    recommended_campaign_id: Optional[int] = None
    recommended_campaign_reasoning: Optional[str] = None
    status: str = SignalStatus.NEW.value
    created_by: Optional[str] = None
    ingestion_batch_id: Optional[str] = None
    raw_payload: Optional[str] = None
    scan_signal_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Joined fields (not stored in intent_signals table)
    company_name: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    company_size: Optional[str] = None
    annual_revenue: Optional[str] = None
    account_status: Optional[str] = None
    account_owner: Optional[str] = None


class Account(BaseModel):
    id: Optional[int] = None
    company_name: str
    github_org: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None
    annual_revenue: Optional[str] = None
    company_size: Optional[str] = None
    industry: Optional[str] = None
    hq_location: Optional[str] = None
    account_owner: Optional[str] = None
    account_status: str = AccountStatus.NEW.value
    current_tier: int = 0
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Prospect(BaseModel):
    id: Optional[int] = None
    account_id: int
    signal_id: Optional[int] = None
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    email_verified: bool = False
    linkedin_url: Optional[str] = None
    apollo_person_id: Optional[str] = None
    do_not_contact: bool = False
    enrollment_status: str = EnrollmentStatus.FOUND.value
    sequence_id: Optional[str] = None
    sequence_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Joined fields
    company_name: Optional[str] = None


class Draft(BaseModel):
    id: Optional[int] = None
    prospect_id: int
    signal_id: Optional[int] = None
    campaign_id: Optional[int] = None
    sequence_step: int
    subject: Optional[str] = None
    body: Optional[str] = None
    generated_by: Optional[str] = None
    generation_model: Optional[str] = None
    generation_context: Optional[str] = None
    last_feedback: Optional[str] = None
    status: str = DraftStatus.GENERATED.value
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FeedbackEntry(BaseModel):
    id: Optional[int] = None
    draft_id: Optional[int] = None
    prospect_id: Optional[int] = None
    signal_id: Optional[int] = None
    critique: str
    sequence_step: Optional[int] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None


class ActivityEntry(BaseModel):
    id: Optional[int] = None
    event_type: str
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    details: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None


class CampaignRecommendation(BaseModel):
    campaign_id: int
    campaign_name: str
    reasoning: str
    confidence: Optional[str] = None  # high, medium, low


class PersonaSpec(BaseModel):
    title: str
    seniority: Optional[str] = None


class IngestResult(BaseModel):
    signals_created: int = 0
    accounts_created: int = 0
    accounts_matched: int = 0
    errors: List[str] = Field(default_factory=list)
    batch_id: Optional[str] = None


class SignalWorkspace(BaseModel):
    """Full context for working on a single signal — used by both web UI and MCP."""
    signal: IntentSignal
    account: Account
    campaign_recommendation: Optional[CampaignRecommendation] = None
    personas: List[PersonaSpec] = Field(default_factory=list)
    prospects: List[Prospect] = Field(default_factory=list)
    drafts: List[Draft] = Field(default_factory=list)
    writing_preferences: dict = Field(default_factory=dict)
