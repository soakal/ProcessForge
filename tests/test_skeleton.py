"""End-to-end walking skeleton (Loop 1 ACCEPT gate, §6).

RED until pipeline.py exists and threads: 3 hardcoded questions -> 1 Task ->
dumb ROI Opportunity -> 1 Recommendation, persisted end to end via KBSink.
"""


def test_walking_skeleton_end_to_end(tmp_path):
    from pipeline import run_session  # does not exist yet — Loop 1 builds this

    db_path = tmp_path / "test.db"
    result = run_session(
        business_name="Test Co",
        tenant="test-tenant",
        answers=[
            "We manually reconcile invoices every week.",
            "It takes about 2 hours each time.",
            "We'd like it automated so no one has to touch a spreadsheet.",
        ],
        db_path=str(db_path),
    )

    assert len(result.tasks) == 1
    assert result.opportunity.roi_low_hrs < result.opportunity.roi_high_hrs
    assert result.opportunity.assumptions
    assert result.recommendation.approval_state.value == "draft"
