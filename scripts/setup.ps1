# Windows PowerShell: 저장소 루트에서 venv 생성 후 개발 의존성 설치
# 수정: 2026-04-15 — PowerShell 전용 셋업 스크립트 추가

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

function Find-Python {
    # py 런처 → python 순으로 시도, pyproject requires-python >= 3.13 충족 여부 확인
    $code = "import sys; assert sys.version_info >= (3, 13); print(sys.executable)"
    $attempts = @(
        @{ Exe = "py"; Extra = @("-3.13") },
        @{ Exe = "py"; Extra = @("-3") },
        @{ Exe = "python"; Extra = @() }
    )
    foreach ($a in $attempts) {
        try {
            $cmdArgs = @() + $a.Extra + @("-c", $code)
            $out = & $a.Exe @cmdArgs 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $line = ($out | Select-Object -First 1).ToString().Trim()
                if ($line -and (Test-Path $line)) { return $line }
            }
        } catch { }
    }
    return $null
}

$pythonExe = Find-Python
if (-not $pythonExe) {
    Write-Error "Python 3.13+ 가 PATH에 없습니다. python.org 또는 Microsoft Store에서 설치하세요."
}

& $pythonExe -m venv .venv
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$pip = Join-Path $RepoRoot ".venv\Scripts\pip.exe"
& $pip install --upgrade pip
& $pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "완료. 활성화: .\.venv\Scripts\Activate.ps1 (실행 정책이 막으면: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned)" -ForegroundColor Green
