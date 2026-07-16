"""Seam: transcript -> Task[]. Loop 1's deterministic placeholder extraction:
first line = task description, last line = desired outcome, regex-based
time/frequency detection. True Loop 2 (adaptive, conversational extraction) is
still pending and will be hand-built separately, not through the council."""
from __future__ import annotations
import re
import uuid
from contracts.records import Task

_HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*hour")
_MINUTES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*min")


def run(inp: str, ctx) -> list[Task]:
    """inp: interview transcript text. out: extracted Task records.

    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    lines = [line.strip() for line in inp.strip().splitlines() if line.strip()]
    task_desc = lines[0] if lines else "Untitled task"
    desired_outcome = lines[-1] if lines else "Automate this task."
    lowered = inp.lower()

    hours_match = _HOURS_RE.search(lowered)
    minutes_match = _MINUTES_RE.search(lowered)
    if hours_match:
        time_spent_min = int(float(hours_match.group(1)) * 60)
    elif minutes_match:
        time_spent_min = int(float(minutes_match.group(1)))
    else:
        time_spent_min = 60

    if "daily" in lowered or "every day" in lowered:
        frequency, frequency_per_week = "daily", 7.0
    elif "month" in lowered:
        frequency, frequency_per_week = "monthly", 1 / 4.345
    else:
        frequency, frequency_per_week = "weekly", 1.0

    task = Task(
        id=str(uuid.uuid4()),
        session_id=ctx.session_id,
        task=task_desc,
        frequency=frequency,
        frequency_per_week=frequency_per_week,
        time_spent_min=time_spent_min,
        pain_level=3,
        tools_used=[],
        dependencies=[],
        desired_outcome=desired_outcome,
    )
    return [task]
