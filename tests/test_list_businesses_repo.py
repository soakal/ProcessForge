"""Tests for KBRepository.list_businesses: tenant-scoped listing of businesses
with a per-row session_count, exercised via direct repository calls only (no API
this cycle), built against the real production migration path (pipeline._migrate),
same convention as tests/test_delete_business_repo.py."""
from __future__ import annotations

import uuid

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


def _new_business(repo: KBRepository, tenant: str, name: str) -> str:
    business_id = _new_id()
    repo.put("businesses", {
        "id": business_id, "schema_version": 1, "tenant": tenant,
        "name": name, "meta": {},
    })
    return business_id


def _new_session(repo: KBRepository, business_id: str) -> str:
    session_id = _new_id()
    repo.put("sessions", {
        "id": session_id, "schema_version": 1, "business_id": business_id,
        "status": "active", "transcript_ref": None,
    })
    return session_id


def test_list_businesses_empty_tenant_returns_empty_list(repo):
    assert repo.list_businesses("acme") == []


def test_list_businesses_returned_shape(repo):
    tenant = "acme"
    business_id = _new_business(repo, tenant, "Acme Co")

    result = repo.list_businesses(tenant)

    assert result == [{
        "id": business_id,
        "schema_version": 1,
        "tenant": tenant,
        "name": "Acme Co",
        "meta": {},
        "session_count": 0,
    }]


def test_list_businesses_session_count_zero_one_and_n(repo):
    tenant = "acme"
    no_sessions_id = _new_business(repo, tenant, "Bare Co")
    one_session_id = _new_business(repo, tenant, "Single Co")
    _new_session(repo, one_session_id)
    many_sessions_id = _new_business(repo, tenant, "Busy Co")
    for _ in range(3):
        _new_session(repo, many_sessions_id)

    result = repo.list_businesses(tenant)
    counts = {row["id"]: row["session_count"] for row in result}

    assert counts[no_sessions_id] == 0
    assert counts[one_session_id] == 1
    assert counts[many_sessions_id] == 3


def test_list_businesses_ordered_by_name_then_id(repo):
    tenant = "acme"
    id_z = _new_business(repo, tenant, "Zebra Co")
    id_a1 = _new_business(repo, tenant, "Alpha Co")
    id_a2 = _new_business(repo, tenant, "Alpha Co")
    id_m = _new_business(repo, tenant, "Middle Co")

    result = repo.list_businesses(tenant)
    ordered_ids = [row["id"] for row in result]

    # Both "Alpha Co" rows sort before "Middle Co" sorts before "Zebra Co";
    # within the tied name, id (a UUID string) breaks the tie.
    assert ordered_ids.index(id_m) < ordered_ids.index(id_z)
    for aid in (id_a1, id_a2):
        assert ordered_ids.index(aid) < ordered_ids.index(id_m)
    alpha_pair = sorted([id_a1, id_a2])
    assert ordered_ids[:2] == alpha_pair


def test_list_businesses_two_tenant_isolation(repo):
    acme_id = _new_business(repo, "acme", "Acme Co")
    _new_session(repo, acme_id)
    other_id = _new_business(repo, "other", "Other Co")
    _new_session(repo, other_id)
    _new_session(repo, other_id)

    acme_result = repo.list_businesses("acme")
    other_result = repo.list_businesses("other")

    assert [row["id"] for row in acme_result] == [acme_id]
    assert acme_result[0]["session_count"] == 1

    assert [row["id"] for row in other_result] == [other_id]
    assert other_result[0]["session_count"] == 2


def test_list_businesses_unknown_tenant_returns_empty_list(repo):
    _new_business(repo, "acme", "Acme Co")

    assert repo.list_businesses("does-not-exist") == []
