$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is required. Install Docker Engine or Docker Desktop (https://docs.docker.com/engine/install/) and re-run this script."
    exit 1
}

# The token is the only thing a new user has to keep: the image ships the
# dashboard and the embedding weights, so nothing has to be fetched first.
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

docker compose -f (Join-Path $root "docker-compose.user.yml") pull
docker compose -f (Join-Path $root "docker-compose.user.yml") up -d
Write-Host "memo is up. Dashboard: http://localhost:8080/ui/   (paste the MEMO_TOKEN from .env)"
Write-Host "Health:    http://localhost:8080/health"
