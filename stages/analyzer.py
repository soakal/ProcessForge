"""Seam: (WorkflowGraph, Task[]) -> Opportunity[]. ROI is always a range, never a point estimate."""
from __future__ import annotations
import re
import uuid
from contracts.records import Task, WorkflowGraph, Opportunity

_DAILY_RE = re.compile(r"\bdaily\b|every day", re.I)
_LARGE_SAVINGS_RE = re.compile(r"\bhours?\b|\bsignificant\b|\bmassive\b|\bhuge\b", re.I)
_LARGE_WEEKLY_MINUTES = 300


def _crosscheck_flags(task: Task) -> list[str]:
    weekly_minutes = task.frequency_per_week * task.time_spent_min
    lowered = task.desired_outcome.lower()
    claims_large_daily_savings = bool(_DAILY_RE.search(lowered)) and bool(_LARGE_SAVINGS_RE.search(lowered))
    if claims_large_daily_savings and weekly_minutes < _LARGE_WEEKLY_MINUTES:
        return [
            f"desired_outcome implies large/daily savings but weekly footprint is only "
            f"{weekly_minutes:.0f} min/week "
            f"({task.frequency_per_week}x/week x {task.time_spent_min} min/occurrence)."
        ]
    return []


def run(inp: tuple[WorkflowGraph, list[Task]], ctx) -> list[Opportunity]:
    """inp: (graph, tasks) for a session. out: ranked automation opportunities, one per task.

    Every Opportunity must carry roi_low_hrs < roi_high_hrs and non-empty assumptions
    (enforced by the model), and must run an arithmetic cross-check against
    self-reported task numbers, surfacing contradictions in crosscheck_flags.
    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    _graph, tasks = inp
    opportunities = []
    for task in tasks:
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
            crosscheck_flags=_crosscheck_flags(task),
        )
        opportunities.append(opportunity)
    return opportunities
