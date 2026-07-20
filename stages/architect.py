"""Seam: (Opportunity[], Task[]) -> Recommendation[]."""
from __future__ import annotations
import uuid
from contracts.records import Opportunity, Task, Recommendation


def run(inp: tuple[list[Opportunity], list[Task]], ctx) -> list[Recommendation]:
    """inp: (ranked opportunities for a session, that session's Tasks). out: one draft
    Recommendation per opportunity.

    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    opportunities, tasks = inp
    task_names_by_id = {task.id: task.task for task in tasks}
    recommendations = []
    for opportunity in opportunities:
        task_names = [task_names_by_id.get(task_id, task_id) for task_id in opportunity.task_ids]
        summary = (
            f"Automate tasks ({', '.join(task_names)}) to save an estimated "
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
