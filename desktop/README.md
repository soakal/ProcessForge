# ProcessForge — Desktop Launcher (Windows)

This folder contains two small desktop helpers for running ProcessForge on
this machine without typing commands by hand:

- **`tray_app.py`** — a system-tray icon that starts/stops/restarts the
  ProcessForge API server (`ServerController`) and gives you quick links to
  open ProcessForge in a browser and open `.env`.
- **`setup_account.py`** — a tiny window for creating an operator account
  (`create_account()`), instead of running the `auth.users create` CLI
  command.

Both files have an `if __name__ == "__main__":` guard that calls their own
`main()` — that's the entry point PyInstaller needs to build an exe from.

This document explains how to package each one into a standalone
double-clickable `.exe` using PyInstaller.

**These exes are a personal convenience launcher for this exact machine and
checkout — they are NOT a redistributable installer.** See "Not
relocatable" below.

---

## 1. Install PyInstaller (one-off, build-only)

PyInstaller is only needed on the machine doing the *building* — it is not a
runtime dependency of ProcessForge itself, and it is intentionally **not**
added to `requirements.lock.txt`. Install it into the project's existing
`.venv` as a one-off step whenever you need to (re)build the exes:

```powershell
.\.venv\Scripts\pip.exe install pyinstaller
```

## 2. Build the exes

Run these from the **ProcessForge project root** (not from inside
`desktop/`), so the relative paths below resolve correctly. Each command
builds a single `--windowed` (no console window) `.exe`:

```powershell
.\.venv\Scripts\pyinstaller.exe --windowed --onefile --name ProcessForgeTray desktop/tray_app.py
.\.venv\Scripts\pyinstaller.exe --windowed --onefile --name ProcessForgeSetup --hidden-import logging.config desktop/setup_account.py
```

`ProcessForgeSetup` needs `--hidden-import logging.config`: `create_account()` runs the real
Alembic migration (`pipeline._migrate`), which loads `kb/migrations/env.py` dynamically (by
file path, not a static `import`) — PyInstaller's analyzer never sees that file, so it never
bundles the `logging.config` stdlib submodule `env.py` imports on its first line. Without this
flag the built exe fails with `No module named 'logging.config'` the moment "Create account" is
clicked. If you regenerate `ProcessForgeSetup.spec` some other way (e.g. `pyi-makespec`), add
`'logging.config'` to its `hiddenimports=[]` list instead.

The built exes land in:

- `dist/ProcessForgeTray.exe`
- `dist/ProcessForgeSetup.exe`

**Move (or copy) both exes to the project root before running them** — they must sit
next to `alembic.ini`, `.env`, and `kb/`:

```powershell
Copy-Item dist\ProcessForgeTray.exe, dist\ProcessForgeSetup.exe .
```

When frozen, each exe resolves the project root as *its own folder* (`_repo_root()` /
`_project_root()` use `sys.executable`'s directory). From `dist/`, `ProcessForgeSetup.exe`
can't find `alembic.ini` (fails with `No 'script_location' key found in configuration`) and
would write to the wrong `kb/processforge.db`; `ProcessForgeTray.exe` can't find `.venv`.
From the project root, all of that resolves correctly. The root `/ProcessForgeTray.exe` and
`/ProcessForgeSetup.exe` are already gitignored.

PyInstaller also creates a `build/` working directory and a `.spec` file
(`ProcessForgeTray.spec`, `ProcessForgeSetup.spec`) next to the project
root — all of this (`build/`, `dist/`, `*.spec`) is PyInstaller output, not
source, and should be gitignored. At the time of writing, the project's
`.gitignore` does not yet list `build/`, `dist/`, or `*.spec` — worth adding
if you build these locally, so the generated exe/spec files don't end up
tracked by git.

## 3. Not relocatable — this is a personal launcher, not an installer

Both `ProcessForgeTray.exe` and `ProcessForgeSetup.exe` shell out to
**this project's own virtual environment** at runtime, not a bundled Python.
`ServerController.venv_python` (in `tray_app.py`) resolves
`.venv/Scripts/python.exe` relative to the location of `tray_app.py` itself
(project root = parent of `desktop/`), and `setup_account.py`'s
`create_account()` imports directly from this checkout's `auth` and
`pipeline` modules.

That means these exes only work when run from inside (or next to) a
ProcessForge checkout that already has a working `.venv` set up per the
main setup instructions (see `USER_MANUAL.md`, Setup step 1). Copying
`ProcessForgeTray.exe` alone to another machine, or to a machine without
this exact checkout + venv, will not work — there is no bundled Python
interpreter or bundled ProcessForge code inside the exe that's usable on
its own. Treat these as a personal double-click shortcut for this machine,
not something to hand out or install elsewhere.
