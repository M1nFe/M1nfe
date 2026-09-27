# 모두온 AI 시연 (UI 없음)

화면 없이 터미널에서 **AI가 정확히 무엇을 하는지** 단계별로 보여 주는 시연 프로그램입니다.
가상 샘플 데이터(통신 엑셀, 렌탈 PDF, 상조 공지 메일)로 AI 기능 4가지를 처음부터 끝까지 실행합니다. 흐름은 수집 → AI → 검증 → 사람 확인 → 확정 DB → 계산 순서입니다.

> 한 줄 요약: **AI는 '읽고, 고르고, 분류하고, 설명하는 제안'만 합니다.** 확정은 사람이, 계산은 고정 공식이 합니다.

---

## 1. AI가 하는 일 (정확히)

시연에서 Claude를 부르는 곳은 아래 6가지, 호출은 모두 8번입니다. 호출은 전부 `moduon_demo/llm.py` 한 곳을 거칩니다. AI의 출력 형식은 JSON Schema로 고정되어 있어서, 정해진 칸 말고 다른 것은 돌려줄 수 없습니다.

| # | 기능 | AI에게 주는 것 | AI가 돌려주는 것 | AI 다음에 코드가 하는 일 | AI가 **하지 않는** 일 |
|---|---|---|---|---|---|
| 1 | 자료 읽기 – 엑셀 새 양식 (`ai/header_map.py`) | 시트의 처음 9행(제목, 열 제목, 샘플 5행) | 열마다 표준 필드 이름·단위·조건 문구 **제안** | 헤더 글자가 실제 칸과 같은지, 조건 문구가 사전에 있는지 검증. 사람이 승인하면 **규칙 파서가** 전체 행의 값을 읽음 | 값 읽기·계산, 조건 코드 확정 |
| 2 | 자료 읽기 – PDF 정책표 (`ai/extract_doc.py`) | PDF 원본(개인정보·지시문 사전 검사 통과분) | 표의 값을 **원문 표기 그대로**, AI가 읽은 숫자, 그 값이 있는 원문 한 줄(근거) | ① 근거 문장이 원문에 정확히 한 번 있는지 ② 표의 [행×열] 칸 값과 같은지 ③ 원문 표기를 코드가 직접 파싱한 숫자와 같은지 → 등급. 저장하는 숫자는 **코드가 파싱한 값** | 할인가·합계 계산, 원문에 없는 값 생성, 문서 속 지시 따르기 |
| 3 | 상품명 매칭 (`ai/match_judge.py`) | 규칙으로 못 푼 상품명 + **코드가 뽑은 후보 5개** | 후보 키 하나(`c1`~`c5`) 또는 '신규 상품/해당 없음/애매함', 이름에 적힌 속성, 한 줄 근거 | 고른 키가 준 후보인지, 속성이 이름에 실제로 있는지 확인 → 등급. 사람이 확정하면 이름을 alias 사전에 저장해 **다음부터는 AI 없이** 규칙으로 매칭 | 후보 밖 상품 만들기, 매칭 확정, 상품 등록 |
| 4 | 이상 데이터 설명 (`ai/anomaly_explain.py`) | **규칙이 이미 이상으로 잡은** 항목(이전 값, 새 값, 원문 행, 비고) | 원인 분류(목록 중 하나), `{prev_value}` 같은 자리표시자만 쓴 설명 틀, 확인할 일 | 설명 틀에 자리표시자 밖의 숫자가 있으면 버림. **숫자는 코드가 채움** | 이상 여부·심각도 판정, 차단 해제, 승인 |
| 5 | 자연어처리 – 공지 메일 (`ai/notice_parse.py`) | 메일 본문 | 변경 종류, 대상 상품(원문), 새 값(원문 표기+숫자), 적용일(원문 표기), 근거 문장 | 근거 문장 대조, 금액 재파싱, **날짜 해석과 상품 연결은 코드**. '담당자 확인 필요' 기록으로만 저장 | DB 가격 변경, 날짜 계산 |
| 6 | 자연어처리 – 관리자 질문 (`ai/nlq_parse.py`, 3회) | 질문 한 문장 + 기준 월 | 조회 종류(intent) + 미리 정한 선택지(enum) 안의 조건 | 미리 작성된 고정 조회 함수 실행(읽기 전용). **표의 숫자는 DB 값** | SQL 작성, 숫자 계산·요약, 정산 계산 |

AI 결과는 모두 `staging_*`(검수 전) 테이블까지만 갑니다.

## 2. AI가 없는 곳

| 영역 | 담당 | 시연에서 확인하는 것 |
|---|---|---|
| 확정 DB(`canonical_*`)에 쓰기 | 사람의 승인 함수 | AI 워커 역할로 쓰기를 시도하면 **DB가 거부**합니다(SQLite 권한 검사 `set_authorizer`). |
| 계산 | `moduon_demo/calc/`의 고정 공식(정수 연산) | 계산 엔진 역할은 staging을 **읽을 수조차 없습니다**. 확정되지 않은 값은 AI 추정값이 아니라 마지막 확정값을 씁니다. |
| 이상 판정 | `rules/anomaly.py`(단위 오류 10배, 변동폭 20% 초과, 범위 밖) | block은 AI가 '정상'이라고 해도 풀리지 않습니다. |
| 조건·날짜 해석 | `rules/conditions.py` 사전, `rules/verify.py` 날짜 파서 | 사전에 없는 조건 문구는 사람이 사전에 추가합니다. |

`tests/test_firewall_and_calc.py`는 `calc/`와 `rules/`가 AI 코드를 import하지 않는지 검사합니다.

## 3. 실행

```bash
cd demo/moduon-ai-demo
./run.sh                      # 처음에 .venv를 만들고 패키지 설치. 이후 자동 모드로 시연
./run.sh --pause              # 발표용: 단계마다 Enter를 기다림
./run.sh test                 # 테스트 (API 키 불필요)
```

`run.sh`는 `.venv`를 만든 뒤 `run_demo.py`에 인자를 그대로 넘깁니다. 직접 실행하려면 `pip install -r requirements.txt` 후 `python run_demo.py ...`를 쓰면 됩니다.

| 모드 | 명령 | 설명 |
|---|---|---|
| **live** | `ANTHROPIC_API_KEY=... ./run.sh --mode live` | 실제 Claude 호출. 응답은 `recordings/<모델>/`에 녹화됩니다. 1회 실행에 8번 호출하고, 끝나면 실제 토큰·시간·비용이 표시됩니다. |
| **replay** | `./run.sh --mode replay` | 녹화된 **실제 응답**을 재생합니다. 키 없이, 인터넷 없이 시연할 때 씁니다. |
| **mock** | `./run.sh --mode mock` | 사람이 미리 써 둔 **모의 응답**입니다. 실제 AI 결과가 아니며 화면에 계속 그렇게 표시됩니다. 코드 검증, DB 권한, 계산은 실제로 실행됩니다. |

모드를 지정하지 않으면 API 키가 있을 때 live, 없으면 녹화본(replay), 녹화본도 없으면 mock으로 자동 선택합니다.

- **권장 순서:** 시연 전에 live로 한 번 실행해 녹화합니다. 발표 때는 `--mode replay --pause`로 진행합니다.
- **결과 파일:** 실행이 끝나면 `output/`에 두 파일이 생깁니다.
  - `demo_report_<모델>_<모드>.md`: AI에게 보낸 원문(시스템 프롬프트, 보낸 내용, JSON Schema)과 응답 전체
  - `demo_summary_<모델>_<모드>.json`: AI가 한 일, 정답 대조, 토큰·시간·비용, DB 결과 요약. OpenClaw 같은 에이전트가 읽고 보고하는 용도입니다.

### 모델 — 기본은 Haiku 4.5

| 모델 | 옵션 | 단가(입력/출력, 1M 토큰) | 추론 설정 |
|---|---|---|---|
| **Claude Haiku 4.5 (기본)** | `--model claude-haiku-4-5` | $1 / $5 | 단순 작업은 thinking 끔. PDF 추출만 thinking 예산 2,048토큰. `effort` 파라미터는 Haiku 4.5가 받지 않아 쓰지 않습니다. |
| Claude Sonnet 5 | `--model claude-sonnet-5` | $2 / $10 | adaptive thinking + effort |
| Claude Opus 5 | `--model claude-opus-5` | $5 / $25 | adaptive thinking + effort. 안전 분류기 거절에 대비한 서버측 fallback(`fallbacks="default"`) 사용 |

- Haiku 4.5로 1회 실행하면 수 센트 수준으로 추정합니다. 실제 값은 실행 후 표시됩니다.
- Haiku 4.5는 프롬프트 캐시 최소 길이가 4,096토큰이라, 이 시연의 짧은 프롬프트는 캐시되지 않습니다(오류는 아님).

**Haiku로 충분한지 숫자로 확인하기:**

```bash
ANTHROPIC_API_KEY=... ./run.sh compare                                   # Haiku 4.5 vs Opus 5
./run.sh compare --models claude-haiku-4-5 claude-sonnet-5 claude-opus-5
./run.sh compare --mode replay                                           # 녹화본끼리(키 불필요)
```

같은 시연을 모델별로 돌려서 기능별 정답 대조, 토큰, 응답 시간, 비용을 한 표로 보여 줍니다(`output/model_comparison.md`). 정답표가 작은 시연용이라, 운영 결정은 실제 골든셋으로 다시 확인해야 합니다.

### OpenClaw로 실행

`openclaw-skills/moduon-ai-demo/SKILL.md`는 OpenClaw 스킬입니다. 연결하면 채팅(텔레그램, 웹 등)에서 "모두온 AI 시연 돌려줘", "하이쿠랑 오퍼스 비교해줘"라고 하면 OpenClaw가 `run.sh`를 실행하고 요약 JSON을 읽어 보고합니다. OpenClaw 2026.9.6에서 `✓ Ready`로 인식되는 것을 확인했습니다.

1. 설정 패치 파일을 만듭니다. `openclaw-skills/openclaw.patch.example.json5`를 복사한 뒤 `extraDirs` 경로를 이 저장소의 실제 경로로 바꿉니다.

   ```json5
   {
     skills: {
       load: { extraDirs: ["/절대경로/M1nfe/demo/moduon-ai-demo/openclaw-skills"] },
       entries: {
         "moduon-ai-demo": { enabled: true, apiKey: { source: "env", provider: "default", id: "ANTHROPIC_API_KEY" } },
       },
     },
     // (선택) OpenClaw 에이전트 자신도 Haiku 4.5로
     agents: { defaults: { model: { primary: "anthropic/claude-haiku-4-5" } } },
   }
   ```

2. 적용하고 확인합니다.

   ```bash
   openclaw config patch --file ./openclaw.patch.json5
   openclaw skills info moduon-ai-demo      # "✓ Ready" 확인
   ```

3. 채팅에서 `/moduon-ai-demo`로 부르거나 "모두온 AI 시연 돌려줘"라고 말합니다. 새 스킬이 안 보이면 `/new`로 새 세션을 시작합니다.

- **키 주입:** `apiKey`는 OpenClaw가 실행될 때 `ANTHROPIC_API_KEY`를 스킬 실행 환경에 넣어 줍니다. OpenClaw를 샌드박스(Docker)로 돌리면 키가 들어가지 않으므로 샌드박스에 따로 넣어야 합니다.
- **실행 승인:** OpenClaw의 exec 승인 설정에 따라 `bash .../run.sh` 실행 전에 승인을 물을 수 있습니다.
- **비용 관리:** 스킬은 live·compare처럼 비용이 드는 실행을 사용자가 요청했을 때만 하도록 적혀 있습니다.

기타 옵션:
- `--quiet`: 화면 출력 없이 결과 파일만 만듭니다(에이전트용).
- `--no-fallback`: Opus 5의 서버측 fallback beta를 끕니다. 계정에서 이 beta가 거부되면 쓰세요.
- `--no-tamper`: 'AI가 값을 잘못 읽었다면?' 가상 검증 시연을 건너뜁니다.

## 4. 시연 장면

| 장면 | 내용 | AI |
|---|---|---|
| 0 | 기존 확정 데이터(9월 단가, 상품 마스터) 적재 | – |
| 1 | A통신 엑셀(새 양식) → AI가 열 매핑 제안 → 검증 → 승인 → 규칙 파서가 값 읽기 | ① |
| 2 | C렌탈 PDF → 사전 검사(개인정보, 문서 속 지시문) → AI 추출 → 3중 대조. AI가 값을 잘못 읽은 가상 상황에서 검증기가 잡아내는 것도 보여 줌 | ① |
| 3 | 상품명 매칭: 규칙(M0/M1) → 유사도 후보(M2) → AI 판정 → 사람 확정 → alias 학습. **유사도 1순위만 쓰면 신규 단말(Z플립6)이 갤럭시 S24로 잘못 붙는 것**과 비교 | ② |
| 4 | 9월 대비 비교 → 규칙이 이상 판정(단위 오류 690,000원, 지원금 −43%) → AI가 원인·설명 틀 작성 | ③ |
| 5 | B상조 공지 메일 → 변경 사항 구조화. 확정 가격은 바뀌지 않음 | ④ |
| 6 | 권한 방화벽 시연 → 사람 검수 → 확정 반영 → 계산 엔진(결정론, AI import 없음) | – |
| 7 | 자연어 조회 3건. 세 번째 "정산금 계산해줘"는 AI가 지원하지 않는 질문으로 분류 | ④ |
| 8 | 정리: AI가 한 일과 하지 않은 일, 호출 수, 토큰, 비용 | – |

## 5. 주의 — 이 시연이 흉내 내는 것

- **데이터는 모두 가상입니다.** 파트너명(A통신·B상조·C렌탈), 가격, 공지는 지어낸 샘플입니다.
- **'사람'은 시뮬레이션입니다.** 정답표(`data/answer_key.json`)를 아는 검수자로 가정합니다. 실제 운영에서는 관리자 화면에서 사람이 합니다.
- **정답 대조는 작은 평가(eval)입니다.** 운영에서는 과거 수작업 결과로 만든 골든셋이 이 역할을 합니다. mock 모드의 정확도 수치는 의미가 없습니다.
- **계산 공식은 예시입니다**(`calc/formulas.py`). 실제 모두온 계산식(정산 또는 고객 가격)은 클라이언트에게 확인한 뒤 교체합니다.
- **DB는 메모리 SQLite입니다.** 실제 설계(Postgres + 컬럼 GRANT·트리거)의 '권한 방화벽'을 SQLite authorizer로 흉내 냅니다.
- **엑셀·PDF 샘플은 커밋되어 있습니다.** 다시 만들려면 `python scripts/make_sample_files.py`를 실행합니다(PDF에는 한글 TrueType 폰트 필요).

## 6. 파일 구조

```
run.sh                      실행 스크립트(.venv 자동 설치, compare/test 하위 명령)
run_demo.py                 실행기 (--mode, --model, --quiet)
compare_models.py           모델 비교
openclaw-skills/            OpenClaw 스킬(SKILL.md) + 설정 예시
moduon_demo/
  llm.py                    Claude 호출은 전부 여기 (모델별 설정, live / replay / mock, 녹화, 비용)
  ai/                       ← AI를 부르는 코드: 시스템 프롬프트 + 출력 JSON Schema
    header_map.py  extract_doc.py  match_judge.py  anomaly_explain.py  notice_parse.py  nlq_parse.py
  rules/                    ← AI 없음: 파싱·정규화·검증·매칭 규칙·이상 규칙·고정 조회
  calc/                     ← AI 없음: 고정 공식 + 계산 실행기 (canonical만 읽음)
  store.py                  raw / staging / canonical / calc 테이블 + 역할별 권한
  scenes.py                 시연 장면 0~8
  console.py                터미널 출력 + 보고서
data/                       가상 샘플(엑셀·PDF·메일), 상품 마스터, 9월 확정 단가, 정답표
mock_responses/             모의 응답 (실제 AI 아님)
recordings/<모델>/           live 실행 시 실제 응답 녹화본
tests/                      규칙·방화벽·계산·live 호출 경로(가짜 HTTP)·전체 파이프라인(mock)
```
