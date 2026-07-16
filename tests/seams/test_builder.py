"""Seam: Recommendation -> Automation. The approval gate is the hard boundary."""
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


class _Ctx:
    session_id = "session-1"


def test_builder_refuses_unapproved_recommendation():
    from stages import builder

    recommendation = _recommendation()
    assert recommendation.approval_state == "draft"

    with pytest.raises(PermissionError):
        builder.run(recommendation, _Ctx())


def test_builder_produces_valid_automation_for_approved_recommendation():
    from stages import builder
    from contracts.records import Automation

    recommendation = _recommendation(approval_state="approved")

    automation = builder.run(recommendation, _Ctx())

    assert isinstance(automation, Automation)
    Automation.model_validate(automation.model_dump())
    assert automation.recommendation_id == recommendation.id
    assert isinstance(automation.spec, dict)
    assert automation.spec
    assert automation.blast_radius
    assert automation.rollback
    assert automation.approval_state == "draft"
