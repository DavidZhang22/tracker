$ErrorActionPreference = 'Stop'
$trackerRoot = $PSScriptRoot
Push-Location (Join-Path $trackerRoot 'frontend')
try {
    if (-not (Test-Path -LiteralPath 'node_modules')) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Push-Location (Join-Path $trackerRoot 'backend')
try {
    uv sync --frozen --no-dev
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
    Write-Host 'Open http://127.0.0.1:8000. Press Ctrl+C to stop.'
    uv run --frozen --no-dev uvicorn app.main:app --host 127.0.0.1 --port 8000
} finally { Pop-Location }
