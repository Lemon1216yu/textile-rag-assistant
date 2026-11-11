$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) { python -m venv .venv }
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python .venv/Scripts/python.exe --cache-dir ./tmp/uv-cache -r requirements-enterprise.txt
} else {
    & ./.venv/Scripts/python.exe -m pip install -r requirements-enterprise.txt
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
if (-not (Test-Path '.env.enterprise')) { Copy-Item -LiteralPath '.env.enterprise.example' -Destination '.env.enterprise' }
if (Select-String -LiteralPath '.env.enterprise' -Pattern '^LOCAL_RAG_MODELS=1$' -Quiet) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv pip install --python .venv/Scripts/python.exe --cache-dir ./tmp/uv-cache -r requirements-local-models.txt
    } else {
        & ./.venv/Scripts/python.exe -m pip install -r requirements-local-models.txt
    }
    if ($LASTEXITCODE -ne 0) { throw 'Local model runtime installation failed' }
    & ./.venv/Scripts/python.exe scripts/download_rag_assets.py
    if ($LASTEXITCODE -ne 0) { throw 'Model or PDF verification failed' }
}
Push-Location frontend
try {
    npm.cmd ci --cache ../tmp/npm-cache --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally { Pop-Location }
& ./.venv/Scripts/python.exe -m enterprise.cli init-dev
if ($LASTEXITCODE -ne 0) { throw 'Development initialization failed' }
& ./.venv/Scripts/python.exe -m enterprise.cli rebuild-bm25
if ($LASTEXITCODE -ne 0) { throw 'Lexical index preparation failed' }
if (Select-String -LiteralPath '.env.enterprise' -Pattern '^LOCAL_RAG_MODELS=1$' -Quiet) {
    & ./.venv/Scripts/python.exe -m scripts.import_public_rag
    if ($LASTEXITCODE -ne 0) { throw 'Public PDF indexing failed' }
}
Write-Host 'Open http://127.0.0.1:8766. Local account: tmp/enterprise-access.txt'
$taskWorker = Start-Process -FilePath (Join-Path $PWD '.venv/Scripts/python.exe') -ArgumentList '-m','enterprise.cli','worker' -WorkingDirectory $PWD -WindowStyle Hidden -PassThru
try {
    & ./.venv/Scripts/python.exe -m uvicorn enterprise.main:app --host 127.0.0.1 --port 8766
} finally {
    Stop-Process -Id $taskWorker.Id -ErrorAction SilentlyContinue
}
