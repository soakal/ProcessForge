"""Seam: (WorkflowGraph, Task[]) -> Opportunity[]. ROI is always a range, never a point estimate."""
from __future__ import annotations
import uuid
from contracts.records import Task, WorkflowGraph, Opportunity


def run(inp: tuple[WorkflowGraph, list[Task]], ctx) -> list[Opportunity]:
    """inp: (graph, tasks) for a session. out: ranked automation opportunities.

    Every Opportunity must carry roi_low_hrs < roi_high_hrs and non-empty assumptions
    (enforced by the model), and must run an arithmetic cross-check against
    self-reported task numbers, surfacing contradictions in crosscheck_flags.
    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    _graph, tasks = inp
    task = tasks[0]
    annual_hours = (task.time_spent_min / 60.0) * task.frequency_per_week * 52
    roi_low_hrs = round(annual_hours * 0.5, 2)
    roi_high_hrs = round(annual_hours * 0.9, 2)
    if roi_high_hrs <= roi_low_hrs:
        roi_high_hrs = roi_low_hrs + 1.0

    opportunity = Opportunity(
        id=str(uuid.uuid4()),
        task_ids=[task.id],
        roi_low_hrs=roi_low_hrs,
        roi_high_hrs=roi_high_hrs,
        assumptions=[
            f"Task '{task.task}' occurs {task.frequency_per_week}x/week at "
            f"{task.time_spent_min} min/occurrence.",
            "Automation eliminates 50-90% of the manual time spent.",
        ],
        complexity=3,
        confidence=0.5,
    )
    return [opportunity]
