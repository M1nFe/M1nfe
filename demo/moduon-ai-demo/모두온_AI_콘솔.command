#!/bin/bash
# 모두온 AI 콘솔 실행기 (macOS: Finder에서 더블클릭)
# 터미널 창이 열리고, 잠시 뒤 브라우저에 콘솔 화면이 뜹니다. 끝내려면 이 터미널 창에서 Ctrl+C.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ] && [ -z "${PYTHON:-}" ]; then
  for py in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$py" >/dev/null 2>&1; then export PYTHON="$py"; break; fi
  done
fi

if command -v ollama >/dev/null 2>&1 && ! curl -s --max-time 1 http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "Ollama를 켭니다…"
  open -a Ollama >/dev/null 2>&1 || nohup ollama serve >/dev/null 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -s --max-time 1 http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
fi

./run.sh app "$@"
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "실행하지 못했습니다(위 메시지 참고). 창을 닫으려면 Enter를 누르세요."
  read -r _
fi
