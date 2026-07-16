"""Seam: (Automation, feedback) -> Automation (revised)."""


def _automation(**overrides):
    from contracts.records import Automation

    defaults = dict(
        id="auto-1",
        recommendation_id="rec-1",
        spec={"kind": "declarative_automation", "steps": []},
        blast_radius="Affects the workflow tied to opportunity opp-1.",
        rollback="Delete or disable this Automation record.",
    )
    defaults.update(overrides)
    return Automation(**defaults)


class _Ctx:
    session_id = "session-1"


def test_qa_produces_valid_revised_automation():
    from stages import qa
    from contracts.records import Automation

    prior = _automation()
    prior_spec_copy = dict(prior.spec)
    feedback = "The rollback step is missing a notification to the on-call engineer."

    revised = qa.run((prior, feedback), _Ctx())

    assert isinstance(revised, Automation)
    Automation.model_validate(revised.model_dump())
    assert revised.id != prior.id
    assert revised.recommendation_id == prior.recommendation_id
    assert revised.blast_radius
    assert revised.rollback
    assert revised.approval_state == "draft"
    assert revised.spec["revision"] == 2
    assert revised.spec["feedback"] == feedback
    assert feedback in revised.spec["revision_notes"]
    assert prior.spec == prior_spec_copy


def test_qa_bumps_revision_on_second_pass():
    from stages import qa

    prior = _automation(spec={"kind": "declarative_automation", "revision": 2})

    revised = qa.run((prior, "Second round of feedback."), _Ctx())

    assert revised.spec["revision"] == 3
