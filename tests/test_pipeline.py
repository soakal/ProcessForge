"""pipeline.py: _migrate() concurrency safety (FIX 5)."""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pipeline


def test_migrate_serializes_concurrent_calls(tmp_path):
    """FIX 5 regression: _migrate() runs a full Alembic upgrade AND mutates the global
    PROCESSFORGE_DB_PATH env var on every call. FastAPI runs sync handlers in a
    threadpool, so concurrent requests can call _migrate() at the same time; without a
    lock, two migrations can race against the same SQLite file / the shared env var.

    This forces the race window open (via a slow fake `command.upgrade`) and asserts
    the lock keeps calls to _migrate() serialized (never more than one "in progress" at
    once), rather than relying on a real Alembic race actually manifesting as an
    exception, which would be timing-dependent and flaky.
    """
    in_progress = {"count": 0}
    max_concurrent = {"value": 0}
    lock_for_counters = threading.Lock()

    def fake_upgrade(cfg, revision):
        with lock_for_counters:
            in_progress["count"] += 1
            max_concurrent["value"] = max(max_concurrent["value"], in_progress["count"])
        time.sleep(0.05)  # widen the race window so any overlap gets caught
        with lock_for_counters:
            in_progress["count"] -= 1

    db_path = str(tmp_path / "test.db")
    errors: list[Exception] = []

    def call_migrate():
        try:
            pipeline._migrate(db_path)
        except Exception as exc:  # pragma: no cover - failure path, asserted below
            errors.append(exc)

    with patch("pipeline.command.upgrade", side_effect=fake_upgrade):
        threads = [threading.Thread(target=call_migrate) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not errors
    assert max_concurrent["value"] == 1
