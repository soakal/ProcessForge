"""Seam: Recommendation -> Automation. The approval gate is the hard boundary (see §7/§9)."""
from __future__ import annotations
import uuid
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
    spec = {
        "kind": "declarative_automation",
        "recommendation_id": inp.id,
        "opportunity_id": inp.opportunity_id,
        "summary": inp.summary,
        "steps": [
            {"action": "review", "detail": inp.summary},
        ],
    }
    automation = Automation(
        id=str(uuid.uuid4()),
        recommendation_id=inp.id,
        spec=spec,
        blast_radius=(
            f"Affects the workflow tied to opportunity {inp.opportunity_id}; "
            "no external systems are touched until this Automation is separately approved."
        ),
        rollback="Delete or disable this Automation record; no changes are applied automatically.",
    )
    Automation.model_validate(automation.model_dump())
    return automation
