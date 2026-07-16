"""Seam: (WorkflowGraph, Task[]) -> Opportunity[]."""


def _task(**overrides):
    from contracts.records import Task

    defaults = dict(
        id="task-1",
        session_id="session-1",
        task="Reconcile invoices",
        frequency="weekly",
        frequency_per_week=1.0,
        time_spent_min=60,
        pain_level=3,
        dependencies=[],
        desired_outcome="Automate reconciliation.",
    )
    defaults.update(overrides)
    return Task(**defaults)


class _Ctx:
    session_id = "session-1"


def _graph():
    from contracts.records import WorkflowGraph

    return WorkflowGraph(id="graph-1", session_id="session-1", nodes=[], edges=[])


def test_analyzer_produces_one_opportunity_per_task():
    from stages import analyzer
    from contracts.records import Opportunity

    tasks = [
        _task(id="task-1", task="Reconcile invoices"),
        _task(id="task-2", task="Send report", time_spent_min=15),
    ]

    opportunities = analyzer.run((_graph(), tasks), _Ctx())

    assert len(opportunities) == len(tasks)
    for opportunity, task in zip(opportunities, tasks):
        assert isinstance(opportunity, Opportunity)
        Opportunity.model_validate(opportunity.model_dump())
        assert opportunity.task_ids == [task.id]
        assert opportunity.roi_low_hrs < opportunity.roi_high_hrs
        assert opportunity.assumptions


def test_analyzer_contradictory_desired_outcome_flags_crosscheck():
    from stages import analyzer

    task = _task(
        frequency_per_week=5,
        time_spent_min=10,
        desired_outcome="We want to save hours every day on this task.",
    )

    opportunity = analyzer.run((_graph(), [task]), _Ctx())[0]

    assert opportunity.crosscheck_flags


def test_analyzer_consistent_desired_outcome_does_not_flag_crosscheck():
    from stages import analyzer

    task = _task(
        frequency_per_week=5,
        time_spent_min=90,
        desired_outcome="We'd like to save hours every day on this daily grind.",
    )

    opportunity = analyzer.run((_graph(), [task]), _Ctx())[0]

    assert opportunity.crosscheck_flags == []
