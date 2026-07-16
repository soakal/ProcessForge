"""Seam: Opportunity[] -> Recommendation[]."""
from __future__ import annotations
import uuid
from contracts.records import Opportunity, Recommendation


def run(inp: list[Opportunity], ctx) -> list[Recommendation]:
    """inp: ranked opportunities for a session. out: one draft Recommendation per opportunity.

    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    recommendations = []
    for opportunity in inp:
        summary = (
            f"Automate tasks {opportunity.task_ids} to save an estimated "
            f"{opportunity.roi_low_hrs:.1f}-{opportunity.roi_high_hrs:.1f} hrs/year "
            f"(confidence {opportunity.confidence:.0%})."
        )
        recommendation = Recommendation(
            id=str(uuid.uuid4()),
            opportunity_id=opportunity.id,
            summary=summary,
        )
        Recommendation.model_validate(recommendation.model_dump())
        recommendations.append(recommendation)
    return recommendations
