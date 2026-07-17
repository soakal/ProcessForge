"""Seam: (Recommendation, Opportunity | None, list[Task]) -> Automation.

The approval gate is the hard boundary (see §7/§9).
"""
from __future__ import annotations
import uuid
from contracts.records import ApprovalState, Automation, Opportunity, Recommendation, Task


def _known(tasks: list[Task]) -> dict:
    """Deterministic 'known' facts pulled straight from Task fields — no invention."""
    return {
        "task": [t.task for t in tasks],
        "frequency": [t.frequency for t in tasks],
        "time_spent": [t.time_spent_min for t in tasks],
        "tools": sorted({tool for t in tasks for tool in t.tools_used}),
        "desired_outcome": [t.desired_outcome for t in tasks],
    }


def _open_questions(tasks: list[Task]) -> list[str]:
    """Deterministic gaps surfaced from thin/missing Task fields — no LLM, no invention."""
    if not tasks:
        return ["Which specific tasks does this automation cover?"]
    questions: list[str] = []
    for t in tasks:
        if not t.tools_used:
            questions.append(f"What tool or system is used to do '{t.task}'?")
        # Task's frozen contract has no field for where source data/files live —
        # always thin, so always surface it.
        questions.append(f"Where does the input file live for '{t.task}'?")
        if not t.dependencies:
            questions.append(f"Does '{t.task}' depend on any upstream task or system?")
    return questions


def _suggested_approach(spec_steps: list[dict]) -> list[str]:
    """Deterministic suggested steps drawn only from the spec's own existing steps —
    no invention, no LLM."""
    return [step.get("detail") or step.get("action", "") for step in spec_steps]


def run(inp: tuple[Recommendation, Opportunity | None, list[Task]], ctx) -> Automation:
    """inp: (an approved Recommendation, its Opportunity if resolvable, that Opportunity's
    Tasks). out: a declarative Automation spec (never executable code).

    Un-bypassable gate: Builder refuses to produce an executable Automation for any
    Recommendation whose approval_state is not 'approved'. This must hold even under
    test/prompt pressure — see tests/seams/test_builder.py.
    Builder is fully deterministic: it never calls ctx.complete() or any other LLM
    method, on any code path, including the handoff fields below.
    Output MUST validate against its contract before return.
    """
    recommendation, opportunity, tasks = inp
    if recommendation.approval_state != ApprovalState.approved:
        raise PermissionError(
            f"Builder refuses: Recommendation {recommendation.id} is not approved "
            f"(approval_state={recommendation.approval_state.value})"
        )
    steps = [
        {"action": "review", "detail": recommendation.summary},
    ]
    spec = {
        "kind": "declarative_automation",
        "recommendation_id": recommendation.id,
        "opportunity_id": recommendation.opportunity_id,
        "summary": recommendation.summary,
        "steps": steps,
        "handoff": {
            "known": _known(tasks),
            "open_questions": _open_questions(tasks),
            "suggested_approach": _suggested_approach(steps),
        },
    }
    automation = Automation(
        id=str(uuid.uuid4()),
        recommendation_id=recommendation.id,
        spec=spec,
        blast_radius=(
            f"Affects the workflow tied to opportunity {recommendation.opportunity_id}; "
            "no external systems are touched until this Automation is separately approved."
        ),
        rollback="Delete or disable this Automation record; no changes are applied automatically.",
    )
    Automation.model_validate(automation.model_dump())
    return automation
