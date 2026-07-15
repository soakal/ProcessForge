"""Walking skeleton (Loop 1, §6): threads answers -> Task -> Opportunity -> Recommendation,
persisting every record via KBSink. No LLM calls — stages here are deterministic."""
from __future__ import annotations
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config

from contracts.records import Business, Session, Task, Opportunity, Recommendation
from kb.repository import KBRepository
from sinks.kb_sink import KBSink
from stages import interviewer, analyzer, architect

_REPO_ROOT = Path(__file__).resolve().parent


class _Ctx:
    def __init__(self, repo: KBRepository, session_id: str):
        self.repo = repo
        self.session_id = session_id

    def complete(self, messages, tier):
        raise NotImplementedError("LLM client not wired in Loop 1")


@dataclass
class SessionResult:
    business: Business
    session: Session
    tasks: list[Task]
    opportunity: Opportunity
    recommendation: Recommendation


def _migrate(db_path: str) -> None:
    os.environ["PROCESSFORGE_DB_PATH"] = db_path
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")


def run_session(business_name: str, tenant: str, answers: list[str], db_path: str) -> SessionResult:
    _migrate(db_path)
    repo = KBRepository(db_path)
    sink = KBSink()
    try:
        business = Business(id=str(uuid.uuid4()), tenant=tenant, name=business_name)
        sink.save(business, _Ctx(repo, session_id=""))

        session = Session(id=str(uuid.uuid4()), business_id=business.id)
        sink.save(session, _Ctx(repo, session_id=""))

        ctx = _Ctx(repo, session_id=session.id)

        transcript = "\n".join(answers)
        tasks = interviewer.run(transcript, ctx)
        for task in tasks:
            sink.save(task, ctx)

        opportunities = analyzer.run((None, tasks), ctx)
        opportunity = opportunities[0]
        sink.save(opportunity, ctx)

        recommendation = architect.run(opportunity, ctx)
        sink.save(recommendation, ctx)

        return SessionResult(
            business=business,
            session=session,
            tasks=tasks,
            opportunity=opportunity,
            recommendation=recommendation,
        )
    finally:
        repo.close()
