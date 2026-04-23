# Windows PowerShell: pytest 실행
# 수정: 2026-04-15 — PowerShell 전용 테스트 스크립트 추가

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArguments
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "가상환경이 없습니다. 먼저 .\scripts\setup.ps1 를 실행하세요."
}

& $py -m pytest @RemainingArguments
