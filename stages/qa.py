"""Seam: (Automation, feedback) -> Automation (revised)."""
from __future__ import annotations
import copy
import uuid
from contracts.records import Automation


def run(inp: tuple[Automation, str], ctx) -> Automation:
    """inp: (prior automation, human feedback text). out: a revised Automation.

    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    prior, feedback_text = inp
    spec = copy.deepcopy(prior.spec)
    revision = spec.get("revision", 1) + 1
    spec["revision"] = revision
    spec["feedback"] = feedback_text
    spec["revision_notes"] = f"Revision {revision}: {feedback_text}"
    automation = Automation(
        id=str(uuid.uuid4()),
        recommendation_id=prior.recommendation_id,
        spec=spec,
        blast_radius=prior.blast_radius,
        rollback=prior.rollback,
    )
    Automation.model_validate(automation.model_dump())
    return automation
