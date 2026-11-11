$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) { python -m venv .venv }
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python .venv/Scripts/python.exe --cache-dir ./tmp/uv-cache -r requirements.txt
} else {
    & ./.venv/Scripts/python.exe -m pip install -r requirements.txt
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
