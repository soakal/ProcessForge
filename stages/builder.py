"""Seam: (Recommendation, Opportunity | None, list[Task]) -> Automation.

The approval gate is the hard boundary (see §7/§9).
"""
from __future__ import annotations
import uuid
from contracts.records import ApprovalState, Automation, Opportunity, Recommendation, Task


def _known(tasks: list[Task], interview_answers: dict[str, str]) -> dict:
    """Deterministic 'known' facts pulled straight from Task fields — no invention.
    interview_answers (see `_interview_answers`) adds each present answer to the
    three cycle-4 interview questions (input-file location, filter rule/column
    value, output format) as its own key — omitted entirely when no matching
    answer was found, so this stays additive and doesn't change the key set for
    callers that pass no turns."""
    known = {
        "task": [t.task for t in tasks],
        "frequency": [t.frequency for t in tasks],
        "time_spent": [t.time_spent_min for t in tasks],
        "tools": sorted({tool for t in tasks for tool in t.tools_used}),
        "desired_outcome": [t.desired_outcome for t in tasks],
    }
    for key in _ANSWER_KEYS:
        if key in interview_answers:
            known[key] = interview_answers[key]
    return known


def _open_questions(tasks: list[Task], interview_answers: dict[str, str]) -> list[str]:
    """Deterministic gaps surfaced from thin/missing Task fields — no LLM, no invention.
    The "where does the input file live" line is only dropped once a real answer
    to that interview question was found (see `_interview_answers`) — never
    guessed or dropped speculatively."""
    if not tasks:
        return ["Which specific tasks does this automation cover?"]
    input_file_known = "input_file_location" in interview_answers
    questions: list[str] = []
    for t in tasks:
        if not t.tools_used:
            questions.append(f"What tool or system is used to do '{t.task}'?")
        # Task's frozen contract has no field for where source data/files live —
        # always thin, so always surface it, UNLESS the interview already
        # captured an answer to that question.
        if not input_file_known:
            questions.append(f"Where does the input file live for '{t.task}'?")
        if not t.dependencies:
            questions.append(f"Does '{t.task}' depend on any upstream task or system?")
    return questions


# Keyword groups used to deterministically match an interview answer to the
# question (deterministic-ladder wording OR LLM-generated wording — either can
# ask about the same three dimensions) that immediately preceded it. Order
# matters only in that each turn is checked against every group; the first
# group whose keyword appears in the question text wins.
_ANSWER_KEYS = ("input_file_location", "filter_rule", "output_format")
_ANSWER_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("input_file_location", ("input file", "source data")),
    ("filter_rule", ("filter", "column")),
    ("output_format", ("format", "output")),
)


def _match_answer_category(question_text: str) -> str | None:
    lowered = (question_text or "").lower()
    for key, keywords in _ANSWER_CATEGORY_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return key
    return None


def _interview_answers(turns: list[dict]) -> dict[str, str]:
    """Deterministically pair answers to the three cycle-4 interview questions
    (input-file location, filter rule/column value, output format) by matching
    each answer turn's immediately preceding question-role turn against
    keywords for that category, positional by turn order.

    Never invents or guesses an answer: a category only appears in the result
    when a real answer turn was found directly after a matching question turn.
    If a category's question/answer pair never occurred (e.g. no turns were
    supplied, or the interview stopped before that question), the category is
    simply absent — no LLM call, no fabrication."""
    answers: dict[str, str] = {}
    for i, turn in enumerate(turns):
        if i == 0 or turn.get("role") != "answer":
            continue
        preceding = turns[i - 1]
        if preceding.get("role") != "question":
            continue
        category = _match_answer_category(preceding.get("content"))
        if category is None or category in answers:
            continue
        content = turn.get("content")
        if not content:
            continue
        answers[category] = content
    return answers


def _suggested_approach(spec_steps: list[dict]) -> list[str]:
    """Deterministic suggested steps drawn only from the spec's own existing steps —
    no invention, no LLM."""
    return [step.get("detail") or step.get("action", "") for step in spec_steps]


def run(
    inp: tuple[Recommendation, Opportunity | None, list[Task]]
    | tuple[Recommendation, Opportunity | None, list[Task], list[dict]],
    ctx,
) -> Automation:
    """inp: (an approved Recommendation, its Opportunity if resolvable, that Opportunity's
    Tasks, optionally the interview's turns as a 4th element — defaults to `[]` when
    omitted, so the existing 3-tuple call shape keeps working unmodified). out: a
    declarative Automation spec (never executable code).

    Un-bypassable gate: Builder refuses to produce an executable Automation for any
    Recommendation whose approval_state is not 'approved'. This must hold even under
    test/prompt pressure — see tests/seams/test_builder.py.
    Builder is fully deterministic: it never calls ctx.complete() or any other LLM
    method, on any code path, including the handoff fields below.
    Output MUST validate against its contract before return.
    """
    recommendation, opportunity, tasks, *rest = inp
    turns: list[dict] = rest[0] if rest else []
    if recommendation.approval_state != ApprovalState.approved:
        raise PermissionError(
            f"Builder refuses: Recommendation {recommendation.id} is not approved "
            f"(approval_state={recommendation.approval_state.value})"
        )
    steps = [
        {"action": "review", "detail": recommendation.summary},
    ]
    interview_answers = _interview_answers(turns)
    spec = {
        "kind": "declarative_automation",
        "recommendation_id": recommendation.id,
        "opportunity_id": recommendation.opportunity_id,
        "summary": recommendation.summary,
        "steps": steps,
        "handoff": {
            "known": _known(tasks, interview_answers),
            "open_questions": _open_questions(tasks, interview_answers),
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
