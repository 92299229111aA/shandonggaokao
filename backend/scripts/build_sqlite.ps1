# 构建桌面版随包分发的 SQLite 数据文件。
#
# 两步必须用不同的 GAOKAO_DB_MODE 在**两个进程**里跑：
# 模型的 schema 与外键目标在 import 时按方言定型，一个进程不可能同时是两种方言。
#
# 用法：  pwsh -File scripts/build_sqlite.ps1
#         pwsh -File scripts/build_sqlite.ps1 -SkipDump    # 复用已有 staging

param(
    [string]$OutFile = "build/gaokao.db",
    [string]$Staging = "build/staging",
    [switch]$SkipDump
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "找不到虚拟环境：$python"
}
if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = "postgresql+psycopg://gaokao:gaokao_dev_only@127.0.0.1:5432/gaokao"
}

Push-Location $root
try {
    if (-not $SkipDump) {
        Write-Host "[1/2] PostgreSQL -> staging（只导 reference + admission）" -ForegroundColor Cyan
        $env:GAOKAO_DB_MODE = "postgres"
        & $python -m app.ingestion.export_sqlite dump --out $Staging
        if ($LASTEXITCODE -ne 0) { throw "dump 失败" }
    } else {
        Write-Host "[1/2] 跳过 dump，复用 $Staging" -ForegroundColor DarkGray
    }

    Write-Host "[2/2] staging -> SQLite" -ForegroundColor Cyan
    $env:GAOKAO_DB_MODE = "sqlite"
    & $python -m app.ingestion.export_sqlite load --staging $Staging --out $OutFile --verify
    if ($LASTEXITCODE -ne 0) { throw "load 失败" }

    $size = [math]::Round((Get-Item $OutFile).Length / 1MB, 1)
    Write-Host "完成：$OutFile  $size MB" -ForegroundColor Green
} finally {
    Remove-Item Env:GAOKAO_DB_MODE -ErrorAction SilentlyContinue
    Pop-Location
}
