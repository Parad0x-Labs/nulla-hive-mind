$ErrorActionPreference = "Stop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "docker not found; cannot start SearXNG"
}
docker compose version | Out-Null
Set-Location (Join-Path $PSScriptRoot "..\infra\searxng")
docker compose up -d
Write-Host "SearXNG up at http://localhost:8080 (JSON: /search?q=...&format=json)"
