"""pipeline.py: _repo_root() frozen-vs-unfrozen path resolution.

Mirrors the compiled-exe alembic.ini bug: _REPO_ROOT used to be an
import-time constant (Path(__file__).resolve().parent), which resolves
inside PyInstaller's temp extraction dir once frozen, so _migrate() could
never find alembic.ini in the shipped ProcessForgeSetup.exe. _repo_root()
replaces it with a call-time helper using the same frozen-detection pattern
already established in desktop/tray_app.py's project_root property and
desktop/setup_account.py's _project_root()."""
from __future__ import annotations

import sys
from pathlib import Path

import pipeline


def test_repo_root_not_frozen_uses_module_parent(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert pipeline._repo_root() == Path(pipeline.__file__).resolve().parent


def test_repo_root_frozen_uses_executable_parent(monkeypatch, tmp_path):
    fake_exe = tmp_path / "ProcessForgeSetup.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    assert pipeline._repo_root() == fake_exe.resolve().parent
