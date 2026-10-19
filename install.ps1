$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$image = "ghcr.io/heitorrosa/memo:latest"
$port = if ($env:MEMO_PORT) { $env:MEMO_PORT } else { "8080" }

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
$token = (Get-Content $envFile | Select-String '^MEMO_TOKEN=').ToString().Split('=', 2)[1].Trim()

$dataDir = Join-Path $root "data"
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}

docker pull $image
docker rm -f memo 2>$null | Out-Null
docker run -d --name memo --restart unless-stopped -p "${port}:8080" -v "${dataDir}:/data" -e MEMO_TOKEN=$token $image | Out-Null

Write-Host "memo is up."
Write-Host "Dashboard: http://localhost:${port}/ui/   (paste the MEMO_TOKEN from .env)"
Write-Host "Health:    http://localhost:${port}/health"
