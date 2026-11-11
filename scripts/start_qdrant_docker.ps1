# Запуск Qdrant в Docker + проверка HTTP
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$docker = $null
foreach ($c in @(
    "docker",
    "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe",
    "${env:ProgramFiles(x86)}\Docker\Docker\resources\bin\docker.exe"
)) {
    if ($c -eq "docker") {
        $cmd = Get-Command docker -ErrorAction SilentlyContinue
        if ($cmd) { $docker = $cmd.Source; break }
    } elseif (Test-Path $c) {
        $docker = $c; break
    }
}

if (-not $docker) {
    Write-Host "docker.exe не найден. Откройте Docker Desktop и новый PowerShell."
    exit 1
}

Write-Host "Docker: $docker"
& $docker compose -f "$Root\docker-compose.yml" up -d
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Ждём http://localhost:6333 ..."
for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-RestMethod -Uri "http://localhost:6333/collections" -TimeoutSec 2
        Write-Host "OK Qdrant:" ($r | ConvertTo-Json -Compress)
        Write-Host "Дальше: python scripts/index_qdrant.py --recreate"
        exit 0
    } catch {
        Start-Sleep -Seconds 2
    }
}
Write-Host "Qdrant не ответил за 60с — проверьте Docker Desktop"
exit 1
