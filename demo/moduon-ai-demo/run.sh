#!/usr/bin/env bash
# 모두온 AI 시연 실행 스크립트 (사람·OpenClaw 스킬 공용)
#
#   ./run.sh check                         # 시연 전 점검 (Ollama·모델·녹화본·터미널 폭)
#   ./run.sh rehearse                      # 리허설: Ollama로 실제 실행하고 응답을 녹화
#   ./run.sh present                       # 발표: 녹화본 재생, 단계마다 Enter, 장면 9에서 요금 설계 화면 열기
#   ./run.sh --mode live --quiet           # 시연 실행 → output/ 에 보고서·요약 JSON
#   ./run.sh compare                       # 모델 비교 (Haiku 4.5 vs Opus 5)
#   ./run.sh test                          # 테스트 (API 키 불필요)
#
# 처음 실행할 때 .venv 를 만들고 requirements.txt 를 설치한다.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    echo "Python 3.10 이상이 필요합니다 (현재: $("$PY" --version 2>&1))." >&2
    echo "macOS 기본 python3는 3.9입니다. 예: brew install python@3.12 후  PYTHON=python3.12 ./run.sh $*" >&2
    exit 1
  fi
  echo "[setup] 가상환경(.venv)을 만들고 패키지를 설치합니다…" >&2
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
fi

case "${1:-}" in
  check)    shift; exec .venv/bin/python preflight.py "$@" ;;
  rehearse) shift; exec .venv/bin/python run_demo.py --provider ollama --mode live "$@" ;;
  present)  shift; exec .venv/bin/python run_demo.py --provider ollama --mode replay --pause --open-screen "$@" ;;
  compare) shift; exec .venv/bin/python compare_models.py "$@" ;;
  test)    shift; exec .venv/bin/python -m pytest -q "$@" ;;
  *)       exec .venv/bin/python run_demo.py "$@" ;;
esac
