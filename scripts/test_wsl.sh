#!/usr/bin/env bash
# WSL/Linux: pytest 실행
# 수정: 2026-04-15 — WSL용 테스트 스크립트 추가

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PY="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "가상환경이 없습니다. 먼저 ./scripts/setup_wsl.sh 를 실행하세요." >&2
  exit 1
fi

exec "$PY" -m pytest "$@"
