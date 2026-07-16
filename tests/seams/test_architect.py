"""Seam: Opportunity[] -> Recommendation[]."""


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


class _Ctx:
    session_id = "session-1"


def test_architect_produces_one_recommendation_per_opportunity():
    from stages import architect
    from contracts.records import Recommendation

    opportunities = [
        _opportunity(id="opp-1", task_ids=["task-1"]),
        _opportunity(id="opp-2", task_ids=["task-2"], roi_low_hrs=5.0, roi_high_hrs=8.0),
    ]

    recommendations = architect.run(opportunities, _Ctx())

    assert len(recommendations) == len(opportunities)
    for recommendation, opportunity in zip(recommendations, opportunities):
        assert isinstance(recommendation, Recommendation)
        Recommendation.model_validate(recommendation.model_dump())
        assert recommendation.approval_state == "draft"
        assert recommendation.opportunity_id == opportunity.id
