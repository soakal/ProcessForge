"""Primary store — always on. Writes the raw record to SQLite via kb/repository.py (§10)."""
from __future__ import annotations
from pydantic import BaseModel
from contracts.records import (
    Business, Session, Task, WorkflowGraph, Opportunity, Recommendation, Automation,
)

_KIND_BY_TYPE = {
    Business: "businesses",
    Session: "sessions",
    Task: "tasks",
    WorkflowGraph: "workflow_graphs",
    Opportunity: "opportunities",
    Recommendation: "recommendations",
    Automation: "automations",
}


class KBSink:
    """ctx must expose `.repo`, a kb.repository.KBRepository."""

    def save(self, record: BaseModel, ctx) -> None:
        kind = _KIND_BY_TYPE.get(type(record))
        if kind is None:
            raise ValueError(f"no KB table registered for record type {type(record).__name__}")
        ctx.repo.put(kind, record.model_dump(mode="json"))
