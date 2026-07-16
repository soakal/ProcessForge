"""Tests for KBRepository.log_approval_change / list_audit_log: write/read,
tenant isolation, and genuine append-only enforcement, built against the real
production migration path (pipeline._migrate), same convention as
tests/test_auth_migration.py / tests/test_auth_repository.py."""
from __future__ import annotations

import sqlite3
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


def test_log_approval_change_and_list_audit_log_round_trip(repo):
    repo.log_approval_change(
        operator_id="op-1",
        tenant="tenant-a",
        record_kind="recommendation",
        record_id="rec-1",
        field="approval_state",
        old_value="pending",
        new_value="approved",
    )

    entries = repo.list_audit_log(tenant="tenant-a")

    assert len(entries) == 1
    entry = entries[0]
    assert entry["operator_id"] == "op-1"
    assert entry["tenant"] == "tenant-a"
    assert entry["record_kind"] == "recommendation"
    assert entry["record_id"] == "rec-1"
    assert entry["field"] == "approval_state"
    assert entry["old_value"] == "pending"
    assert entry["new_value"] == "approved"
    assert entry["id"]
    assert entry["ts"]
    # ts must be a valid ISO datetime.
    datetime.fromisoformat(entry["ts"])


def test_list_audit_log_filters_by_record_id(repo):
    repo.log_approval_change(
        operator_id="op-1",
        tenant="tenant-a",
        record_kind="recommendation",
        record_id="rec-1",
        field="approval_state",
        old_value="pending",
        new_value="approved",
    )
    repo.log_approval_change(
        operator_id="op-1",
        tenant="tenant-a",
        record_kind="recommendation",
        record_id="rec-2",
        field="approval_state",
        old_value="pending",
        new_value="rejected",
    )

    entries = repo.list_audit_log(tenant="tenant-a", record_id="rec-1")

    assert len(entries) == 1
    assert entries[0]["record_id"] == "rec-1"
    assert entries[0]["new_value"] == "approved"


def test_list_audit_log_enforces_tenant_isolation(repo):
    repo.log_approval_change(
        operator_id="op-1",
        tenant="tenant-a",
        record_kind="recommendation",
        record_id="rec-1",
        field="approval_state",
        old_value="pending",
        new_value="approved",
    )
    repo.log_approval_change(
        operator_id="op-2",
        tenant="tenant-b",
        record_kind="recommendation",
        record_id="rec-1",
        field="approval_state",
        old_value="pending",
        new_value="rejected",
    )

    entries_a = repo.list_audit_log(tenant="tenant-a")

    assert len(entries_a) == 1
    assert all(e["tenant"] == "tenant-a" for e in entries_a)


def test_audit_log_is_genuinely_append_only(repo, tmp_path):
    repo.log_approval_change(
        operator_id="op-1",
        tenant="tenant-a",
        record_kind="recommendation",
        record_id="rec-1",
        field="approval_state",
        old_value="pending",
        new_value="approved",
    )
    entry_id = repo.list_audit_log(tenant="tenant-a")[0]["id"]

    # DB-level enforcement: the migration installs BEFORE UPDATE/DELETE triggers
    # on audit_log that RAISE(ABORT, ...), so tampering via a raw connection
    # (bypassing KBRepository entirely) must fail with sqlite3.IntegrityError —
    # this proves append-only is enforced by SQLite itself, not just by
    # KBRepository never exposing an update/delete method.
    conn = sqlite3.connect(str(tmp_path / "pf.db"))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE audit_log SET old_value = 'tampered' WHERE id = ?", (entry_id,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_log WHERE id = ?", (entry_id,))
    finally:
        conn.close()

    # Confirm the row is genuinely untouched after both failed attempts.
    entries = repo.list_audit_log(tenant="tenant-a")
    assert len(entries) == 1
    assert entries[0]["old_value"] == "pending"

    # Code-level check too: KBRepository itself exposes no way to mutate or
    # remove rows from audit_log.
    assert not hasattr(repo, "update_audit_log")
    assert not hasattr(repo, "delete_audit_log")
