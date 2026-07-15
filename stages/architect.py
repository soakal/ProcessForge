"""Seam: Opportunity -> Recommendation."""
from __future__ import annotations
import uuid
from contracts.records import Opportunity, Recommendation


def run(inp: Opportunity, ctx) -> Recommendation:
    """inp: a single ranked opportunity. out: a Recommendation with approval_state=draft.

    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    summary = (
        f"Automate tasks {inp.task_ids} to save an estimated "
        f"{inp.roi_low_hrs:.1f}-{inp.roi_high_hrs:.1f} hrs/year "
        f"(confidence {inp.confidence:.0%})."
    )
    return Recommendation(
        id=str(uuid.uuid4()),
        opportunity_id=inp.id,
        summary=summary,
    )
