"""Seam: (Recommendation, Opportunity | None, list[Task]) -> Automation.

The approval gate is the hard boundary.
"""
import pytest


def _recommendation(**overrides):
    from contracts.records import Recommendation

    defaults = dict(
        id="rec-1",
        opportunity_id="opp-1",
        summary="Automate invoice reconciliation.",
    )
    defaults.update(overrides)
    return Recommendation(**defaults)


def _task(**overrides):
    from contracts.records import Task

    defaults = dict(
        id="task-1",
        session_id="session-1",
        task="Reconcile invoices",
        frequency="daily",
        frequency_per_week=5.0,
        time_spent_min=30,
        pain_level=3,
        tools_used=["Excel"],
        dependencies=[],
        desired_outcome="Invoices matched automatically",
    )
    defaults.update(overrides)
    return Task(**defaults)


def _opportunity(**overrides):
    from contracts.records import Opportunity

    defaults = dict(
        id="opp-1",
        task_ids=["task-1"],
        roi_low_hrs=10.0,
        roi_high_hrs=20.0,
        assumptions=["Manual process today."],
        complexity=2,
        confidence=0.7,
    )
    defaults.update(overrides)
    return Opportunity(**defaults)


class _Ctx:
    """A Ctx whose complete() blows up if ever called — proves the builder never
    calls it, on any code path, rather than merely asserting it by inspection."""

    session_id = "session-1"

    def complete(self, *args, **kwargs):
        raise AssertionError("builder must never call ctx.complete() — it is fully deterministic")


def test_builder_refuses_unapproved_recommendation():
    from stages import builder

    recommendation = _recommendation()
    assert recommendation.approval_state == "draft"

    with pytest.raises(PermissionError):
        builder.run((recommendation, None, []), _Ctx())


def test_builder_produces_valid_automation_for_approved_recommendation():
    from stages import builder
    from contracts.records import Automation

    recommendation = _recommendation(approval_state="approved")
    task = _task()
    opportunity = _opportunity(task_ids=[task.id])

    automation = builder.run((recommendation, opportunity, [task]), _Ctx())

    assert isinstance(automation, Automation)
    Automation.model_validate(automation.model_dump())
    assert automation.recommendation_id == recommendation.id
    assert isinstance(automation.spec, dict)
    assert automation.spec
    assert automation.blast_radius
    assert automation.rollback
    assert automation.approval_state == "draft"


def test_builder_handoff_shape():
    from stages import builder

    recommendation = _recommendation(approval_state="approved")
    task = _task()
    opportunity = _opportunity(task_ids=[task.id])

    automation = builder.run((recommendation, opportunity, [task]), _Ctx())

    handoff = automation.spec["handoff"]
    assert set(handoff.keys()) == {"known", "open_questions", "suggested_approach"}

    known = handoff["known"]
    assert isinstance(known, dict)
    assert set(known.keys()) == {"task", "frequency", "time_spent", "tools", "desired_outcome"}
    assert known["task"] == [task.task]
    assert known["frequency"] == [task.frequency]
    assert known["time_spent"] == [task.time_spent_min]
    assert known["tools"] == sorted(task.tools_used)
    assert known["desired_outcome"] == [task.desired_outcome]

    open_questions = handoff["open_questions"]
    assert isinstance(open_questions, list)
    assert open_questions
    assert all(isinstance(q, str) for q in open_questions)
    assert any("input file live" in q for q in open_questions)

    suggested_approach = handoff["suggested_approach"]
    assert isinstance(suggested_approach, list)
    assert suggested_approach
    assert all(isinstance(s, str) for s in suggested_approach)
    assert suggested_approach == [recommendation.summary]


def test_builder_handoff_tolerates_missing_opportunity():
    from stages import builder

    recommendation = _recommendation(approval_state="approved")

    automation = builder.run((recommendation, None, []), _Ctx())

    handoff = automation.spec["handoff"]
    assert handoff["known"] == {
        "task": [],
        "frequency": [],
        "time_spent": [],
        "tools": [],
        "desired_outcome": [],
    }
    assert handoff["open_questions"] == ["Which specific tasks does this automation cover?"]
    assert handoff["suggested_approach"] == [recommendation.summary]


def test_builder_handoff_flags_thin_task_fields():
    from stages import builder

    recommendation = _recommendation(approval_state="approved")
    task = _task(tools_used=[], dependencies=[])
    opportunity = _opportunity(task_ids=[task.id])

    automation = builder.run((recommendation, opportunity, [task]), _Ctx())

    open_questions = automation.spec["handoff"]["open_questions"]
    assert f"What tool or system is used to do '{task.task}'?" in open_questions
    assert f"Does '{task.task}' depend on any upstream task or system?" in open_questions


def test_builder_handoff_is_deterministic():
    from stages import builder

    recommendation = _recommendation(approval_state="approved")
    task = _task()
    opportunity = _opportunity(task_ids=[task.id])

    first = builder.run((recommendation, opportunity, [task]), _Ctx())
    second = builder.run((recommendation, opportunity, [task]), _Ctx())

    assert first.spec["handoff"] == second.spec["handoff"]


def test_builder_handoff_is_pure_json_serializable_data():
    import json

    from stages import builder

    recommendation = _recommendation(approval_state="approved")
    task = _task()
    opportunity = _opportunity(task_ids=[task.id])

    automation = builder.run((recommendation, opportunity, [task]), _Ctx())

    # Round-trips through json.dumps/loads with no loss — proves it's plain
    # dict/list/str/int data, never a callable or other executable object.
    handoff = automation.spec["handoff"]
    assert json.loads(json.dumps(handoff)) == handoff
