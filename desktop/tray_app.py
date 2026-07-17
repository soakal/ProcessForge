"""GUI-free system-tray controller for the ProcessForge API server.

ServerController owns the uvicorn subprocess lifecycle (start/stop/restart/
is_running) using only the standard library — no pystray, no Pillow, no GUI
of any kind — so it can be imported and driven directly by tests without a
tray icon ever being created and without binding the real API port.

It accepts an injectable `popen_factory` (default `subprocess.Popen`) so
tests can substitute a fake process object instead of actually spawning
uvicorn.

`python -m desktop.tray_app` (or running this file directly) launches the
real pystray tray icon on top of ServerController — those imports are gated
under `if __name__ == "__main__":` so importing this module never requires
pystray/Pillow to be installed and never opens a tray icon or GUI loop.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional

#: Time (seconds) to wait for the process to exit gracefully after
#: terminate() before escalating to kill().
_STOP_TIMEOUT_SECONDS = 5


class ServerController:
    """Starts, stops, and restarts the ProcessForge API server subprocess.

    Locates the project root as the parent of the `desktop/` directory this
    module lives in, and runs the venv's python.exe as
    `-m uvicorn api.main:app --port 8010` — no absolute machine-specific
    path is hardcoded.
    """

    def __init__(self, popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen):
        self._popen_factory = popen_factory
        self._process: Optional[subprocess.Popen] = None

    @property
    def project_root(self) -> Path:
        # desktop/tray_app.py -> desktop -> project root
        return Path(__file__).resolve().parent.parent

    @property
    def venv_python(self) -> Path:
        return self.project_root / ".venv" / "Scripts" / "python.exe"

    def build_command(self) -> list[str]:
        return [
            str(self.venv_python),
            "-m",
            "uvicorn",
            "api.main:app",
            "--port",
            "8010",
        ]

    def is_running(self) -> bool:
        """True if a subprocess was started and hasn't exited yet."""
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Spawn the server subprocess, unless one is already running."""
        if self.is_running():
            return
        self._process = self._popen_factory(self.build_command())

    def stop(self) -> None:
        """Terminate the server subprocess, force-killing it if it doesn't
        exit within _STOP_TIMEOUT_SECONDS of a graceful terminate()."""
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._process = None

    def restart(self) -> None:
        """Stop the server (if running) and start it again."""
        self.stop()
        self.start()


def main() -> int:
    """Thin pystray wrapper around ServerController. Only imported/executed
    when this module is run directly — never at import time."""
    import pystray
    from PIL import Image, ImageDraw

    controller = ServerController()
    controller.start()

    def make_icon_image() -> Image.Image:
        image = Image.new("RGB", (64, 64), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((16, 16, 48, 48), fill="white")
        return image

    def on_restart(icon: "pystray.Icon", item: "pystray.MenuItem") -> None:
        controller.restart()

    def on_quit(icon: "pystray.Icon", item: "pystray.MenuItem") -> None:
        controller.stop()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Restart server", on_restart),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("ProcessForge", make_icon_image(), "ProcessForge", menu)
    icon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
