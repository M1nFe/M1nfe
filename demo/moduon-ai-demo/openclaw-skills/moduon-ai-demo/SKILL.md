---
name: moduon-ai-demo
description: 모두온 AI 시연 실행 — 자료 읽기·상품명 매칭·이상 감지·자연어처리에서 AI가 정확히 무엇을 하는지 돌려 보고 결과를 요약한다.
metadata: { "openclaw": { "emoji": "🧪", "requires": { "bins": ["python3"] }, "primaryEnv": "ANTHROPIC_API_KEY" } }
---

# 모두온 AI 시연

사용자가 "모두온 AI 시연 돌려줘", "AI가 뭘 하는지 보여줘", "하이쿠로 돌려봐", "모델 비교해줘" 같은 요청을 할 때 쓴다.
시연 프로그램은 가상 샘플 데이터(통신 엑셀, 렌탈 PDF, 상조 공지 메일)로 AI 기능 4가지를 끝까지 실행하고,
AI가 한 일 / 코드가 검증한 것 / 사람이 확인할 것을 나눠서 기록한다.

## 위치

시연 폴더는 `MODUON_DEMO_DIR` 환경변수가 있으면 그 경로, 없으면 이 스킬 폴더의 두 단계 위(`{baseDir}/../..`)다.

```bash
DEMO="${MODUON_DEMO_DIR:-{baseDir}/../..}"
```

## 실행 (`exec` 도구)

처음 실행하면 `run.sh`가 `.venv`를 만들고 패키지를 설치한다(1~2분).
`--pause`는 Enter 입력을 기다리므로 쓰지 않는다. 항상 `--quiet`로 실행하고 결과 파일을 읽어서 보고한다.

| 요청 | 명령 | 비용 |
|---|---|---|
| 무료 로컬 모델로 시연(Ollama, 기본 qwen2.5:7b) | `bash "$DEMO/run.sh" --provider ollama --quiet` | 없음(내 PC에서 실행) |
| 실제 Claude로 시연(기본 모델 Haiku 4.5) | `bash "$DEMO/run.sh" --mode live --quiet` | API 과금(1회 8번 호출) |
| 다른 모델로 시연 | `bash "$DEMO/run.sh" --mode live --model claude-opus-5 --quiet` | API 과금 |
| 녹화본 재생(키 불필요) | `bash "$DEMO/run.sh" --mode replay --quiet` | 없음 |
| 모의 실행(실제 AI 아님) | `bash "$DEMO/run.sh" --mode mock --quiet` | 없음 |
| 모델 비교(Haiku 4.5 vs Opus 5) | `bash "$DEMO/run.sh" compare` | API 과금(모델마다 8번) |
| 테스트 | `bash "$DEMO/run.sh" test` | 없음 |

- `--mode live`와 `compare`는 `ANTHROPIC_API_KEY`가 필요하고 돈이 든다. 사용자가 실제 실행이나 비교를 요청했을 때만 쓴다.
- "무료로", "로컬로", "오픈소스 모델로", "Ollama로"라는 요청이면 `--provider ollama`를 쓴다. Ollama가 꺼져 있거나 모델이 없으면 실패 메시지에 나온 명령(`ollama serve`, `ollama pull …`)을 사용자에게 전달한다.
- 모드를 말하지 않았으면 `--mode`를 빼고 실행한다. 키가 있으면 live, 없으면 녹화본, 녹화본도 없으면 mock으로 자동 선택된다.
- 실행이 실패하면 stderr 마지막 줄(예: `AI 호출 실패: …`)을 그대로 전달한다. API 키 값은 절대 출력하지 않는다.

## 결과 읽고 보고하기

1. `$DEMO/output/demo_summary_<모델>_<모드>.json`을 읽는다. 실행 마지막 줄에 경로가 찍힌다.
2. 한국어로 짧게 보고한다.
   - **첫 줄에 모드를 밝힌다.** `is_real_ai`가 false이면 "모의 응답이라 실제 AI 결과가 아닙니다"라고 먼저 쓴다.
   - `what_ai_did`: 기능별로 "AI가 한 일"과 "정답 대조"를 표로 정리한다.
   - `accuracy`의 `(비교) 유사도 1순위만 사용` 값과 `② 상품명 매칭` 값을 나란히 보여 준다. 규칙·유사도만으로는 부족하고 AI 판정이 필요한 이유가 드러난다.
   - `totals`: 호출 수, 토큰, 시간, 비용(추정)
   - `db`: 확정 건수, 그중 AI 추출값, 사람이 고친 값, 제외 건수
3. 자세한 근거가 필요하면 `report_path`의 마크다운에서 해당 장면만 발췌한다. AI에게 보낸 원문과 응답이 들어 있다.
4. 모델 비교를 했다면 `$DEMO/output/model_comparison.md`의 표를 그대로 보여 준다.

## 보고할 때 지킬 것

- 정답 대조는 시연용 소규모 정답표 기준이다. "정확도 100%" 같은 일반화를 하지 않는다.
- mock 결과를 실제 AI 성능처럼 말하지 않는다.
- AI는 제안만 하고, 확정은 사람이 하며, 계산은 고정 공식이 한다. 이 구분을 흐리지 않는다.
