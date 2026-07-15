"""Seam: (WorkflowGraph, Task[]) -> Opportunity[]. ROI is always a range, never a point estimate."""
from __future__ import annotations
from contracts.records import Task, WorkflowGraph, Opportunity


def run(inp: tuple[WorkflowGraph, list[Task]], ctx) -> list[Opportunity]:
    """inp: (graph, tasks) for a session. out: ranked automation opportunities.

    Every Opportunity must carry roi_low_hrs < roi_high_hrs and non-empty assumptions
    (enforced by the model), and must run an arithmetic cross-check against
    self-reported task numbers, surfacing contradictions in crosscheck_flags.
    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    raise NotImplementedError
