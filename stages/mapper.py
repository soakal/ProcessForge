"""Seam: Task[] -> WorkflowGraph."""
from __future__ import annotations
from contracts.records import Task, WorkflowGraph


def run(inp: list[Task], ctx) -> WorkflowGraph:
    """inp: extracted tasks for a session. out: the workflow graph connecting them.

    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    raise NotImplementedError
