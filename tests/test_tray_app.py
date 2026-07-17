"""desktop/tray_app.py: ServerController subprocess lifecycle, GUI-free.

Tests drive ServerController directly with an injected fake Popen factory —
no real uvicorn process is spawned and port 8010 is never bound. No
pystray/Pillow import ever happens here since main() is gated under
`if __name__ == "__main__":`, same pattern as tests/test_setup_account.py."""
from __future__ import annotations

import sys
from pathlib import Path

from desktop.tray_app import ServerController


class FakeProcess:
    """Stand-in for subprocess.Popen. `terminate()` marks the process as
    exited immediately unless `hangs` is True, in which case it only exits
    after an explicit kill()."""

    def __init__(self, hangs: bool = False):
        self.hangs = hangs
        self.terminated = False
        self.killed = False
        self._exited = False
        self.wait_calls: list[float | None] = []

    def poll(self):
        return 0 if self._exited else None

    def terminate(self):
        self.terminated = True
        if not self.hangs:
            self._exited = True

    def kill(self):
        self.killed = True
        self._exited = True

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if not self._exited:
            import subprocess

            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return 0


def make_factory(hangs: bool = False):
    processes: list[FakeProcess] = []

    def factory(command):
        proc = FakeProcess(hangs=hangs)
        proc.command = command
        processes.append(proc)
        return proc

    factory.processes = processes
    return factory


def test_is_running_false_before_start():
    controller = ServerController(popen_factory=make_factory())
    assert controller.is_running() is False


def test_start_spawns_process_once():
    factory = make_factory()
    controller = ServerController(popen_factory=factory)

    controller.start()

    assert len(factory.processes) == 1
    assert controller.is_running() is True


def test_start_does_not_double_spawn_if_already_running():
    factory = make_factory()
    controller = ServerController(popen_factory=factory)

    controller.start()
    controller.start()

    assert len(factory.processes) == 1


def test_is_running_reflects_poll_none_means_running():
    factory = make_factory()
    controller = ServerController(popen_factory=factory)
    controller.start()

    assert controller.is_running() is True
    assert factory.processes[0].poll() is None


def test_is_running_reflects_poll_non_none_means_exited():
    factory = make_factory()
    controller = ServerController(popen_factory=factory)
    controller.start()
    factory.processes[0]._exited = True

    assert controller.is_running() is False


def test_stop_terminates_process():
    factory = make_factory()
    controller = ServerController(popen_factory=factory)
    controller.start()
    proc = factory.processes[0]

    controller.stop()

    assert proc.terminated is True
    assert controller.is_running() is False


def test_stop_force_kills_if_terminate_does_not_exit_in_time():
    factory = make_factory(hangs=True)
    controller = ServerController(popen_factory=factory)
    controller.start()
    proc = factory.processes[0]

    controller.stop()

    assert proc.terminated is True
    assert proc.killed is True
    assert controller.is_running() is False


def test_stop_when_never_started_is_a_no_op():
    controller = ServerController(popen_factory=make_factory())

    controller.stop()  # must not raise

    assert controller.is_running() is False


def test_restart_stops_then_starts():
    factory = make_factory()
    controller = ServerController(popen_factory=factory)
    controller.start()
    first_proc = factory.processes[0]

    controller.restart()

    assert first_proc.terminated is True
    assert len(factory.processes) == 2
    assert controller.is_running() is True


def test_build_command_uses_venv_resolved_python_path():
    controller = ServerController(popen_factory=make_factory())

    command = controller.build_command()

    assert command[0] == str(controller.venv_python)
    assert command[0].endswith(str(Path(".venv") / "Scripts" / "python.exe"))
    assert command[1:] == ["-m", "uvicorn", "api.main:app", "--port", "8010"]


def test_venv_python_is_resolved_from_project_root_not_hardcoded():
    controller = ServerController(popen_factory=make_factory())

    # project_root is derived from this module's own location, not a
    # hardcoded absolute path — desktop/tray_app.py's grandparent.
    assert controller.project_root == Path(__file__).resolve().parent.parent
    assert controller.venv_python == controller.project_root / ".venv" / "Scripts" / "python.exe"


def test_project_root_not_frozen_uses_module_grandparent(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    controller = ServerController(popen_factory=make_factory())

    assert controller.project_root == Path(__file__).resolve().parent.parent


def test_project_root_frozen_uses_executable_parent(monkeypatch, tmp_path):
    fake_exe = tmp_path / "ProcessForge.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    controller = ServerController(popen_factory=make_factory())

    assert controller.project_root == fake_exe.resolve().parent
