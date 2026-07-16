"""Tests for KBRepository.add_turn / list_turns: write/read, turn_index
auto-increment, and session isolation, built against the real production
migration path (pipeline._migrate), same convention as tests/test_audit_log.py."""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest

import pipeline
from kb.repository import KBRepository


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "pf.db")
    pipeline._migrate(db_path)
    r = KBRepository(db_path)
    yield r
    r.close()


def _new_id() -> str:
    return str(uuid.uuid4())


def _new_session(repo: KBRepository, tenant: str = "tenant-a") -> str:
    """Creates a business + session pair, wired together like
    pipeline.run_session does, and returns the session id — session_turns.session_id
    has a real FK to sessions(id), enforced by PRAGMA foreign_keys = ON."""
    business_id = _new_id()
    repo.put("businesses", {
        "id": business_id, "schema_version": 1, "tenant": tenant,
        "name": "Acme Co", "meta": {},
    })
    session_id = _new_id()
    repo.put("sessions", {
        "id": session_id, "schema_version": 1, "business_id": business_id,
        "status": "active", "transcript_ref": None,
    })
    return session_id


def test_add_turn_and_list_turns_round_trip(repo):
    session_id = _new_session(repo)
    repo.add_turn(session_id=session_id, role="question", content="What is your name?")
    repo.add_turn(session_id=session_id, role="answer", content="Jon")

    turns = repo.list_turns(session_id)

    assert len(turns) == 2
    assert turns[0]["turn_index"] == 0
    assert turns[0]["role"] == "question"
    assert turns[0]["content"] == "What is your name?"
    assert turns[1]["turn_index"] == 1
    assert turns[1]["role"] == "answer"
    assert turns[1]["content"] == "Jon"
    for turn in turns:
        assert turn["id"]
        assert turn["ts"]
        # ts must be a valid ISO datetime.
        datetime.fromisoformat(turn["ts"])


def test_turn_index_auto_increments_across_multiple_calls(repo):
    session_id = _new_session(repo)
    repo.add_turn(session_id=session_id, role="question", content="q1")
    repo.add_turn(session_id=session_id, role="answer", content="a1")
    repo.add_turn(session_id=session_id, role="question", content="q2")

    turns = repo.list_turns(session_id)

    assert [t["turn_index"] for t in turns] == [0, 1, 2]


def test_list_turns_enforces_session_isolation(repo):
    session_a = _new_session(repo)
    session_b = _new_session(repo)
    repo.add_turn(session_id=session_a, role="question", content="from A")
    repo.add_turn(session_id=session_b, role="question", content="from B")
    repo.add_turn(session_id=session_a, role="answer", content="also from A")

    turns_a = repo.list_turns(session_a)

    assert len(turns_a) == 2
    assert all(t["session_id"] == session_a for t in turns_a)
    assert [t["content"] for t in turns_a] == ["from A", "also from A"]


def test_list_turns_for_session_with_no_turns_returns_empty_list(repo):
    session_id = _new_session(repo)
    assert repo.list_turns(session_id) == []
