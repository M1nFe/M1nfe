#!/usr/bin/env bash
# 모두온 AI 시연 실행 스크립트 (사람·OpenClaw 스킬 공용)
#
#   ./run.sh --mode live --quiet           # 시연 실행 → output/ 에 보고서·요약 JSON
#   ./run.sh compare                       # 모델 비교 (Haiku 4.5 vs Opus 5)
#   ./run.sh test                          # 테스트 (API 키 불필요)
#
# 처음 실행할 때 .venv 를 만들고 requirements.txt 를 설치한다.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  echo "[setup] 가상환경(.venv)을 만들고 패키지를 설치합니다…" >&2
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
fi

case "${1:-}" in
  compare) shift; exec .venv/bin/python compare_models.py "$@" ;;
  test)    shift; exec .venv/bin/python -m pytest -q "$@" ;;
  *)       exec .venv/bin/python run_demo.py "$@" ;;
esac
