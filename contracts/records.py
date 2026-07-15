from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field, model_validator


class SessionStatus(str, Enum):
    active = "active"
    paused = "paused"
    complete = "complete"


class ApprovalState(str, Enum):
    draft = "draft"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Business(BaseModel):
    schema_version: int = 1
    id: str
    tenant: str
    name: str
    meta: dict = Field(default_factory=dict)


class Session(BaseModel):
    schema_version: int = 1
    id: str
    business_id: str
    status: SessionStatus = SessionStatus.active
    transcript_ref: str | None = None       # pointer to stored transcript


class Task(BaseModel):
    schema_version: int = 1
    id: str
    session_id: str
    task: str
    frequency: str                          # free text, e.g. "daily", "3x/week"
    frequency_per_week: float                # normalized for ROI math
    time_spent_min: int                      # minutes per occurrence
    pain_level: int = Field(ge=1, le=5)
    tools_used: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    desired_outcome: str


class WorkflowGraph(BaseModel):
    schema_version: int = 1
    id: str
    session_id: str
    nodes: list[dict]                        # {id, task_id, label}
    edges: list[dict]                        # {from, to, kind}
    bottlenecks: list[str] = Field(default_factory=list)  # node ids


class Opportunity(BaseModel):
    schema_version: int = 1
    id: str
    task_ids: list[str]
    roi_low_hrs: float                       # per-year hours saved, low bound
    roi_high_hrs: float                      # per-year hours saved, high bound
    assumptions: list[str]                   # MUST be non-empty
    complexity: int = Field(ge=1, le=5)      # automation difficulty
    confidence: float = Field(ge=0.0, le=1.0)
    crosscheck_flags: list[str] = Field(default_factory=list)  # arithmetic contradictions

    @model_validator(mode="after")
    def _guard(self):
        if self.roi_low_hrs >= self.roi_high_hrs:
            raise ValueError("ROI must be a range: roi_low_hrs < roi_high_hrs")
        if not self.assumptions:
            raise ValueError("Opportunity must surface at least one assumption")
        return self


class Recommendation(BaseModel):
    schema_version: int = 1
    id: str
    opportunity_id: str
    summary: str
    approval_state: ApprovalState = ApprovalState.draft


class Automation(BaseModel):
    schema_version: int = 1
    id: str
    recommendation_id: str
    spec: dict
    blast_radius: str                        # what it can touch if it goes wrong
    rollback: str                            # how to undo
    approval_state: ApprovalState = ApprovalState.draft
