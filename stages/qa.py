"""Seam: (Automation, feedback) -> Automation (revised)."""
from __future__ import annotations
from contracts.records import Automation


def run(inp: tuple[Automation, str], ctx) -> Automation:
    """inp: (prior automation, human feedback text). out: a revised Automation.

    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    raise NotImplementedError
