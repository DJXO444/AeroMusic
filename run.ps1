# Aero launcher (Windows) — always uses the correct Python (venv 3.13, not system 3.14)
# Usage:  .\run.ps1
$env:Path += ";$env:USERPROFILE\.deno\bin"   # ensure deno is on PATH
uv run python -m aero
