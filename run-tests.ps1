# ProcessForge test command. A failing pip-audit is a Realist non-ACCEPT (§9).
$ErrorActionPreference = "Stop"
& .\.venv\Scripts\python.exe -m pip_audit -r requirements.lock.txt
& .\.venv\Scripts\python.exe -m pytest -q
