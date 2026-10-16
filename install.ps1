$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$image = "ghcr.io/heitorrosa/memo:3.0.0"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is required. Install Docker Engine or Docker Desktop (https://docs.docker.com/engine/install/) and re-run this script."
    exit 1
}

$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Write-Host ".env already exists, keeping its MEMO_TOKEN"
} else {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = -join ($bytes | ForEach-Object { $_.ToString("x2") })
    Set-Content -Path $envFile -Value "MEMO_TOKEN=$token" -Encoding ascii
    Write-Host ".env created with a generated MEMO_TOKEN"
}

$modelsDir = Join-Path $root "models"
if (-not (Test-Path $modelsDir)) {
    New-Item -ItemType Directory -Path $modelsDir | Out-Null
}

if (Get-ChildItem -Path $modelsDir -Recurse -File -ErrorAction SilentlyContinue) {
    Write-Host "models/ already populated, skipping weights fetch"
} else {
    Write-Host "Fetching embedding weights into models/ with a temporary container (no local Python needed)"
    docker run --rm -v "${modelsDir}:/models" -v "$(Join-Path $root 'scripts'):/scripts:ro" -e FASTEMBED_CACHE_PATH=/models $image python /scripts/fetch_model.py
}

docker compose -f (Join-Path $root "docker-compose.user.yml") up -d
Write-Host "memo is up. Health: http://localhost:5000/health"
