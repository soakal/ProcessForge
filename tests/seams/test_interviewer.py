"""Seam: transcript -> Task[]."""


class _Ctx:
    session_id = "session-1"


def test_interviewer_hours_regex_sets_time_spent_min():
    from stages import interviewer

    transcript = "\n".join([
        "Reconcile invoices manually.",
        "It takes about 2 hours each time.",
        "We want it automated.",
    ])

    task = interviewer.run(transcript, _Ctx())[0]

    assert task.time_spent_min == 120


def test_interviewer_minutes_regex_sets_time_spent_min():
    from stages import interviewer

    transcript = "\n".join([
        "Send the weekly report.",
        "It takes 45 min each time.",
        "We want it automated.",
    ])

    task = interviewer.run(transcript, _Ctx())[0]

    assert task.time_spent_min == 45


def test_interviewer_defaults_time_when_no_duration_mentioned():
    from stages import interviewer

    transcript = "\n".join([
        "We handle customer refunds by hand each time someone complains.",
        "It's frustrating and slow.",
        "We want this to run without any manual work.",
    ])

    task = interviewer.run(transcript, _Ctx())[0]

    assert task.time_spent_min == 60


def test_interviewer_daily_frequency_branch():
    from stages import interviewer

    transcript = "\n".join([
        "Reconcile invoices manually.",
        "We do this every day and it's annoying.",
        "We want it automated.",
    ])

    task = interviewer.run(transcript, _Ctx())[0]

    assert task.frequency == "daily"
    assert task.frequency_per_week == 7.0


def test_interviewer_monthly_frequency_branch():
    from stages import interviewer

    transcript = "\n".join([
        "Run the compliance report.",
        "We only run this report monthly.",
        "We want it automated.",
    ])

    task = interviewer.run(transcript, _Ctx())[0]

    assert task.frequency == "monthly"
    assert task.frequency_per_week == 1 / 4.345


def test_interviewer_defaults_to_weekly_frequency():
    from stages import interviewer

    transcript = "\n".join([
        "Send the invoice summary.",
        "It's a recurring chore we'd rather not do by hand.",
        "We want it automated.",
    ])

    task = interviewer.run(transcript, _Ctx())[0]

    assert task.frequency == "weekly"
    assert task.frequency_per_week == 1.0


def test_interviewer_first_line_is_task_and_last_line_is_outcome():
    from stages import interviewer

    transcript = "\n".join([
        "Reconcile invoices manually.",
        "It takes 3 hours and we do it monthly.",
        "We would like it to happen automatically.",
    ])

    task = interviewer.run(transcript, _Ctx())[0]

    assert task.task == "Reconcile invoices manually."
    assert task.desired_outcome == "We would like it to happen automatically."


def test_interviewer_output_validates_against_task_contract():
    from stages import interviewer
    from contracts.records import Task

    transcript = "\n".join([
        "Reconcile invoices manually.",
        "It takes 2 hours and we do it weekly.",
        "We would like it to happen automatically.",
    ])

    task = interviewer.run(transcript, _Ctx())[0]

    assert isinstance(task, Task)
    Task.model_validate(task.model_dump())
    assert task.session_id == "session-1"
