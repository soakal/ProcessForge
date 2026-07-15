"""Seam: Recommendation -> Automation. The approval gate is the hard boundary (see §7/§9)."""
from __future__ import annotations
from contracts.records import ApprovalState, Recommendation, Automation


def run(inp: Recommendation, ctx) -> Automation:
    """inp: an approved Recommendation. out: a declarative Automation spec (never executable code).

    Un-bypassable gate: Builder refuses to produce an executable Automation for any
    Recommendation whose approval_state is not 'approved'. This must hold even under
    test/prompt pressure — see tests/seams/test_builder.py.
    LLM calls go through ctx.complete(messages, tier).
    Output MUST validate against its contract before return.
    """
    if inp.approval_state != ApprovalState.approved:
        raise PermissionError(
            f"Builder refuses: Recommendation {inp.id} is not approved "
            f"(approval_state={inp.approval_state.value})"
        )
    raise NotImplementedError
