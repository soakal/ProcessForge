"""Seam: transcript -> Task[]. Hand-built (Loop 2), not council-built."""
from __future__ import annotations
from contracts.records import Task


def run(inp: str, ctx) -> list[Task]:
    """inp: interview transcript text. out: extracted Task records.

    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    raise NotImplementedError
