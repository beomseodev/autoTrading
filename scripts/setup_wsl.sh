#!/usr/bin/env bash
# WSL/Linux: 저장소 루트에서 venv 생성 후 개발 의존성 설치
# 수정: 2026-04-15 — WSL용 셋업 스크립트 추가

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 가 없습니다. sudo apt update && sudo apt install -y python3 python3-venv python3-pip" >&2
  exit 1
fi

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -e ".[dev]"
echo "완료. 활성화: source .venv/bin/activate"
