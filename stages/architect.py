"""Seam: Opportunity -> Recommendation."""
from __future__ import annotations
from contracts.records import Opportunity, Recommendation


def run(inp: Opportunity, ctx) -> Recommendation:
    """inp: a single ranked opportunity. out: a Recommendation with approval_state=draft.

    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    raise NotImplementedError
