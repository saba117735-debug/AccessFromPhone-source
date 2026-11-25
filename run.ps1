# Start AccessFromPhone. Creates the virtual environment on first run.
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    python -m venv .venv
    .\.venv\Scripts\python -m pip install --quiet -e ".[dev]"
}
.\.venv\Scripts\python -m handoff
