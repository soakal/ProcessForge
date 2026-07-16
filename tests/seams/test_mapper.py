"""Seam: Task[] -> WorkflowGraph."""


def test_mapper_produces_valid_workflow_graph():
    from stages import mapper
    from contracts.records import Task, WorkflowGraph

    class _Ctx:
        session_id = "session-1"

    tasks = [
        Task(
            id="task-1",
            session_id="session-1",
            task="Collect invoices",
            frequency="weekly",
            frequency_per_week=1.0,
            time_spent_min=30,
            pain_level=3,
            dependencies=[],
            desired_outcome="Automate collection.",
        ),
        Task(
            id="task-2",
            session_id="session-1",
            task="Reconcile invoices",
            frequency="weekly",
            frequency_per_week=1.0,
            time_spent_min=60,
            pain_level=4,
            dependencies=["task-1"],
            desired_outcome="Automate reconciliation.",
        ),
        Task(
            id="task-3",
            session_id="session-1",
            task="Send report",
            frequency="weekly",
            frequency_per_week=1.0,
            time_spent_min=15,
            pain_level=2,
            dependencies=["task-1"],
            desired_outcome="Automate reporting.",
        ),
    ]

    graph = mapper.run(tasks, _Ctx())

    assert isinstance(graph, WorkflowGraph)
    WorkflowGraph.model_validate(graph.model_dump())

    assert {node["id"] for node in graph.nodes} == {"task-1", "task-2", "task-3"}
    assert {(edge["from"], edge["to"]) for edge in graph.edges} == {
        ("task-1", "task-2"),
        ("task-1", "task-3"),
    }
    assert graph.bottlenecks == ["task-1"]
