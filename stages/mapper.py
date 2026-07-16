"""Seam: Task[] -> WorkflowGraph."""
from __future__ import annotations
import uuid
from collections import Counter
from contracts.records import Task, WorkflowGraph


def run(tasks: list[Task], ctx) -> WorkflowGraph:
    """inp: extracted tasks for a session. out: the workflow graph connecting them.

    Deterministic: one node per task, edges from each Task.dependencies. A node id
    is a bottleneck when more than one task depends on it.
    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    nodes = [{"id": task.id, "task_id": task.id, "label": task.task} for task in tasks]

    edges = []
    dep_counts: Counter[str] = Counter()
    for task in tasks:
        for dep_id in task.dependencies:
            edges.append({"from": dep_id, "to": task.id, "kind": "dependency"})
            dep_counts[dep_id] += 1

    bottlenecks = [dep_id for dep_id, count in dep_counts.items() if count > 1]

    return WorkflowGraph(
        id=str(uuid.uuid4()),
        session_id=ctx.session_id,
        nodes=nodes,
        edges=edges,
        bottlenecks=bottlenecks,
    )
