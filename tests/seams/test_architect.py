"""Seam: (Opportunity[], Task[]) -> Recommendation[]."""


def _opportunity(**overrides):
    from contracts.records import Opportunity

    defaults = dict(
        id="opp-1",
        task_ids=["task-1"],
        roi_low_hrs=10.0,
        roi_high_hrs=20.0,
        assumptions=["Task occurs weekly."],
        complexity=3,
        confidence=0.5,
    )
    defaults.update(overrides)
    return Opportunity(**defaults)


def _task(**overrides):
    from contracts.records import Task

    defaults = dict(
        id="task-1",
        session_id="session-1",
        task="processing excel file that has to update daily",
        frequency="daily",
        frequency_per_week=7.0,
        time_spent_min=20,
        pain_level=3,
        desired_outcome="excel",
    )
    defaults.update(overrides)
    return Task(**defaults)


class _Ctx:
    session_id = "session-1"


def test_architect_produces_one_recommendation_per_opportunity():
    from stages import architect
    from contracts.records import Recommendation

    opportunities = [
        _opportunity(id="opp-1", task_ids=["task-1"]),
        _opportunity(id="opp-2", task_ids=["task-2"], roi_low_hrs=5.0, roi_high_hrs=8.0),
    ]
    tasks = [
        _task(id="task-1", task="processing excel file that has to update daily"),
        _task(id="task-2", task="re-keying vendor invoices into QuickBooks"),
    ]

    recommendations = architect.run((opportunities, tasks), _Ctx())

    assert len(recommendations) == len(opportunities)
    for recommendation, opportunity in zip(recommendations, opportunities):
        assert isinstance(recommendation, Recommendation)
        Recommendation.model_validate(recommendation.model_dump())
        assert recommendation.approval_state == "draft"
        assert recommendation.opportunity_id == opportunity.id


def test_architect_summary_uses_readable_task_names_not_raw_ids():
    from stages import architect

    opportunities = [_opportunity(id="opp-1", task_ids=["task-1"])]
    tasks = [_task(id="task-1", task="processing excel file that has to update daily")]

    recommendations = architect.run((opportunities, tasks), _Ctx())

    summary = recommendations[0].summary
    assert "processing excel file that has to update daily" in summary
    assert "task-1" not in summary


def test_architect_summary_falls_back_to_id_for_unmatched_task():
    from stages import architect

    opportunities = [_opportunity(id="opp-1", task_ids=["task-missing"])]

    recommendations = architect.run((opportunities, []), _Ctx())

    assert "task-missing" in recommendations[0].summary
