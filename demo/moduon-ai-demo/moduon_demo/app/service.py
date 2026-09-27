"""실행 프로그램(모두온 AI 콘솔)의 서비스 계층.

터미널 시연과 같은 파이프라인을 쓰되, 검수자 역할을 정답표가 아니라 **사용자가 직접** 한다.
  전산 수집(AI·규칙) → 상품명 매칭 확인 → 값 검수(승인·수정·제외) → 확정 반영 → 계산 → 요금 설계 화면

- DB는 메모리 SQLite 한 개(세션). 접근은 self.lock으로 한 번에 하나씩. AI 호출은 잠금 밖에서 한다.
- AI 결과는 staging까지만 간다. canonical에는 사용자의 승인 후 reviewer 역할로만 들어간다.
- 모의(mock) 모드는 샘플 자료에만 맞는 예시 응답을 쓴다. 내 자료는 Ollama나 Claude로 처리한다.
"""
import csv
import io
import json
import threading
import time
import uuid
from pathlib import Path

from .. import ingest
from ..ai import anomaly_explain, extract_doc, header_map, kakao_parse, match_judge, nlq_parse, notice_parse
from ..calc import engine as calc_engine
from ..console import Reporter
from ..ingest import MONTH, PREV_MONTH
from ..llm import DEFAULT_MODELS, LLM, AIError
from ..rules import anomaly as anomaly_rules
from ..rules import queries, verify
from ..rules.conditions import CONDITION_LABELS, PlanBook, label
from ..rules.matching import Catalog, match_grade, verify_attributes
from ..rules.text import parse_krw
from ..screen import rate_design
from ..store import Store

PARTNERS = {"A통신": "telecom", "B통신": "telecom", "C통신": "telecom", "C렌탈": "appliance", "B상조": "funeral"}
FIELD_KO = {"device_price": "출고가", "monthly_fee": "월정액", "subsidy_amount": "공시지원금", "rebate": "리베이트",
            "monthly_rental_fee": "월 렌탈료", "mandatory_months": "의무사용기간", "registration_fee": "등록비",
            "monthly_installment": "월 납입금", "installment_count": "납입 횟수", "product_name": "상품명",
            "model_code": "모델코드", "memo": "비고", "ignore": "(사용 안 함)"}
KIND_KO = {"xlsx": "엑셀 정책표", "kakao": "카톡·문자 공지", "api": "전산 API", "csv": "전산 CSV", "pdf": "PDF 안내문",
           "email": "공지 메일", "manual": "사람 직접 입력"}
SOURCE_LABEL = {"xlsx": "엑셀 정책표(AI 열 매핑 → 규칙 파서)", "kakao": "카톡 공지(AI 추출)", "api": "전산 API(규칙)",
                "csv": "전산 CSV(승인 양식·규칙)", "pdf": "PDF 안내문(AI 추출)", "manual": "사람 직접 입력"}
EXTRACTOR_KO = {"rule": "규칙 파서", "ai": "AI 추출", "api": "API(규칙)", "template": "승인 양식(규칙)", "manual": "사람 입력"}
SAMPLES = [
    {"id": "a_excel", "partner": "A통신", "kind": "xlsx", "title": "A통신 엑셀 정책표", "file": "data/a_telecom_price_2610.xlsx",
     "how": "🤖 AI 열 매핑 → 🙋 승인 → ⚙️ 규칙 파서", "desc": "처음 보는 양식, 두 줄 열 제목, 리베이트는 만원 단위"},
    {"id": "b_kakao", "partner": "B통신", "kind": "kakao", "title": "B통신 카톡 단가 공지", "file": "data/b_telecom_kakao_2610.txt",
     "how": "🤖 AI 추출 → ⚙️ 원문 대조", "desc": "요금제×가입유형별 리베이트, '개'(=만원) 단위"},
    {"id": "b_api", "partner": "B통신", "kind": "api", "title": "B통신 전산 API", "file": "data/b_telecom_api_2610.json",
     "how": "⚙️ 규칙(AI 없음)", "desc": "출고가·요금제별 공시지원금(JSON)"},
    {"id": "c_csv", "partner": "C통신", "kind": "csv", "title": "C통신 전산 CSV", "file": "data/c_telecom_2610.csv",
     "how": "⚙️ 승인 양식 → 규칙(AI 없음)", "desc": "출고가·공시지원금·리베이트"},
    {"id": "c_pdf", "partner": "C렌탈", "kind": "pdf", "title": "C렌탈 PDF 안내문", "file": "data/c_rental_price_2610.pdf",
     "how": "🤖 AI 추출 → ⚙️ 3중 대조", "desc": "월 렌탈료·의무기간·등록비 (문서 속 지시문 포함)"},
    {"id": "b_mail", "partner": "B상조", "kind": "email", "title": "B상조 공지 메일", "file": "data/b_funeral_notice_2610.txt",
     "how": "🤖 AI 구조화(기록만)", "desc": "납입금 변경·판매 종료 안내"},
]
# 모의 모드의 상품명 매칭 예시 응답은 샘플 이름 순서(m01~m08)로 적혀 있다 → 이름으로 찾는다
MOCK_MATCH_NAMES = ["갤럭시S24 512 블랙", "아이폰16 256 블랙", "갤럭시 Z플립6 256 실버", "갤S24 256", "아이폰16 256",
                    "퓨어워터 WP500 냉온", "퓨어워터 WP500 냉정", "클린에어 AC300"]
UPLOAD_KINDS = {"xlsx": ["A통신", "B통신", "C통신"], "kakao": ["A통신", "B통신", "C통신"], "csv": ["C통신"],
                "pdf": ["C렌탈"], "email": ["B상조"]}
MAX_UPLOAD = 10 * 1024 * 1024


class UserError(Exception):
    """사용자에게 그대로 보여 줄 오류(잘못된 입력, 순서 오류 등)."""


def _man(v) -> str:
    if v is None:
        return "-"
    n = v / 10_000
    return f"{n:,.0f}" if n == int(n) else f"{n:,.1f}"


def _table(title, columns, rows, note=None) -> dict:
    return {"title": title, "columns": columns, "rows": [["" if v is None else str(v) for v in r] for r in rows],
            "note": note}


class Session:
    def __init__(self, root: Path, provider: str = "ollama", model: str | None = None, mode: str = "mock",
                 base_url: str | None = None):
        self.root = root
        self.lock = threading.RLock()
        self.store = Store()
        self.history: list[dict] = []
        self.rep = Reporter(file=io.StringIO(), width=120)
        self.base_url = base_url
        self.llm = None
        self.set_engine(provider, model, mode)
        self.key = json.loads((root / "data/answer_key.json").read_text(encoding="utf-8"))
        self.mock_out = {p.stem: json.loads(p.read_text(encoding="utf-8"))["output"]
                         for p in (root / "mock_responses").glob("*.json")}
        self.sources: dict[str, dict] = {}
        for s in SAMPLES:
            self.sources[s["id"]] = {**s, "sample": True, "status": "대기", "result": None}
        self.pending_mapping: dict[str, dict] = {}
        self.calc: list[dict] = []
        self.promotions = 0
        with self.lock:
            ingest.load_masters(self.store, root)
            self._run_calc()

    # ───────────────────────── 엔진
    def set_engine(self, provider: str, model: str | None, mode: str):
        if provider not in DEFAULT_MODELS or mode not in ("live", "mock"):
            raise UserError("지원하지 않는 엔진 설정입니다.")
        try:
            self.llm = LLM(mode, self.root, self.rep, model=model or DEFAULT_MODELS[provider], provider=provider,
                           base_url=self.base_url, record=False, history=self.history)
        except AIError as e:
            raise UserError(str(e)) from e

    @property
    def mock(self) -> bool:
        return self.llm.mode == "mock"

    def engine_info(self) -> dict:
        return {"provider": self.llm.provider, "model": self.llm.model, "mode": self.llm.mode,
                "label": self.llm.mode_label, "display": self.llm.display_name}

    # ───────────────────────── 상태 요약
    def state(self) -> dict:
        with self.lock:
            st = self.store
            todo_map = sum(1 for s in self.sources.values() if s["status"] == "매핑 승인 대기")
            todo_match = st.one("select count(*) n from staging_mention where product_id is null and "
                                "match_state in ('unmatched','pending_match_review')")["n"]
            groups = self._review_groups()
            return {
                "engine": self.engine_info(),
                "month": MONTH,
                "sources": [{k: v for k, v in s.items() if k not in ("result", "data")} for s in self.sources.values()],
                "todo": {"mapping": todo_map, "matching": todo_match,
                         "review": len(groups["blocked"]) + len(groups["needs"]) + len(groups["safe"]),
                         "to_promote": len(groups["approved"])},
                "confirmed": st.one("select count(*) n from canonical_price where month=?", (MONTH,))["n"],
                "promotions": self.promotions,
                "upload_kinds": {k: {"label": KIND_KO[k], "partners": v} for k, v in UPLOAD_KINDS.items()},
                "sample_questions": [q["question"] for q in self.key["nlq"]],
            }

    def source_detail(self, sid: str) -> dict:
        s = self.sources.get(sid)
        if not s:
            raise UserError("없는 자료입니다.")
        out = {k: v for k, v in s.items() if k != "data"}
        if sid in self.pending_mapping:
            out["mapping"] = self._mapping_view(sid)
        return out

    # ───────────────────────── 자료 올리기
    def add_upload(self, kind: str, partner: str, filename: str, data: bytes) -> str:
        if kind not in UPLOAD_KINDS or partner not in UPLOAD_KINDS[kind]:
            raise UserError("자료 종류와 파트너 조합이 맞지 않습니다.")
        if not data:
            raise UserError("내용이 비어 있습니다.")
        if len(data) > MAX_UPLOAD:
            raise UserError("파일이 너무 큽니다(10MB 이하).")
        if self.mock:
            raise UserError("모의 모드는 샘플 자료에만 맞는 예시 응답을 씁니다. 내 자료는 위쪽에서 엔진을 "
                            "Ollama(로컬) 또는 Claude로 바꾼 뒤 올려 주세요.")
        sid = "u_" + uuid.uuid4().hex[:8]
        self.sources[sid] = {"id": sid, "partner": partner, "kind": kind, "title": f"{partner} {KIND_KO[kind]} (올린 자료)",
                             "file": filename, "how": "올린 자료", "desc": filename, "sample": False, "status": "대기",
                             "result": None, "data": data}
        return sid

    # ───────────────────────── 처리(작업 스레드에서 실행)
    def process(self, sid: str, progress) -> dict:
        s = self.sources.get(sid)
        if not s:
            raise UserError("없는 자료입니다.")
        if s["status"] not in ("대기", "오류"):
            raise UserError(f"이미 처리한 자료입니다({s['status']}). 다시 하려면 '처음부터'를 누르세요.")
        data = s.get("data") if not s["sample"] else (self.root / s["file"]).read_bytes()
        s["status"] = "처리 중"
        steps: list[dict] = []
        s["result"] = {"steps": steps, "tables": [], "ai_calls": []}

        def say(actor, text):
            steps.append({"actor": actor, "text": text})
            progress(text)
        first_call = len(self.history)
        try:
            handler = {"xlsx": self._p_xlsx, "kakao": self._p_kakao, "api": self._p_api, "csv": self._p_csv,
                       "pdf": self._p_pdf, "email": self._p_email}[s["kind"]]
            handler(s, data, say)
        except AIError as e:
            s["status"] = "오류"
            say("오류", f"AI 호출 실패: {e}")
            raise UserError(f"AI 호출 실패: {e}") from e
        except UserError:
            s["status"] = "오류"
            raise
        finally:
            s["result"]["ai_calls"] = list(range(first_call + 1, len(self.history) + 1))
        return s["result"]

    def process_all(self, progress) -> dict:
        done = []
        for sid, s in list(self.sources.items()):
            if s["sample"] and s["status"] in ("대기", "오류"):
                progress(f"▶ {s['title']}")
                self.process(sid, progress)
                done.append(s["title"])
        return {"processed": done}

    def _raw(self, s, data, injection=False) -> int:
        with self.lock:
            return ingest.save_raw(self.store, s["partner"], s["kind"], Path(s["file"]).name, data, injection)

    # 엑셀: AI가 열 매핑을 제안하고, 사람이 승인해야 규칙 파서가 값을 읽는다
    def _p_xlsx(self, s, data, say):
        rid = self._raw(s, data)
        say("코드", f"원본 보관(raw_file #{rid}). 승인된 양식 템플릿 없음 → AI에게 열 매핑 '제안'을 요청")
        try:
            ws, grid, grid_text, cols = ingest.excel_grid(data)
        except Exception as e:
            raise UserError(f"엑셀 파일을 읽지 못했습니다: {e}") from e
        s["result"]["tables"].append({"title": "AI에게 보낸 시트 내용(처음 10행, 병합 셀은 코드가 채움)", "text": grid_text})
        say("AI", "AI가 열 제목을 읽고 항목·요금제·가입유형·단위를 제안하는 중…")
        ai = header_map.run(self.llm, Path(s["file"]).name, ws.title, grid_text, cols)
        with self.lock:
            plans = PlanBook.from_db(self.store)
        self.pending_mapping[s["id"]] = {"ws": ws, "grid": grid, "cols": cols, "rid": rid, "ai": ai, "plans": plans}
        view = self._mapping_view(s["id"])
        bad = [c["col"] for c in view["columns"] if not c["passed"]]
        say("코드", "열마다 검증 완료: " + (f"확인이 필요한 열 {', '.join(bad)}" if bad else "모든 열 통과"))
        say("사람", "매핑 표를 확인하고 [매핑 승인]을 누르세요. 틀린 열은 고친 뒤 승인합니다")
        s["status"] = "매핑 승인 대기"

    def _mapping_view(self, sid) -> dict:
        pm = self.pending_mapping[sid]
        ai, grid, plans, partner = pm["ai"], pm["grid"], pm["plans"], self.sources[sid]["partner"]
        hrows = ai["header_rows"] or [1]
        colchk, glob = verify.verify_header_mapping(grid, ai, partner, plans, range(max(hrows) + 1, 11))
        cols = []
        by_col = {c.col: c for c in colchk}
        for L in pm["cols"]:
            c = by_col.get(L)
            actual = verify.compose_header(grid, hrows, L)
            if c is None:            # AI가 빼먹은 열 → 사용 안 함으로
                cols.append({"col": L, "actual": actual, "ai_header": "(AI가 빠뜨림)", "header_ok": None,
                             "field_code": "ignore", "unit": "none", "plan_id": "", "condition_key": "base",
                             "plan_detail": "", "cond_detail": "", "median": None, "range_ok": None, "passed": True})
                continue
            cols.append({"col": L, "actual": actual, "ai_header": c.source_header, "header_ok": c.header_ok,
                         "field_code": c.field_code, "unit": c.unit, "plan_id": c.plan_id or "",
                         "condition_key": c.condition_key or "base", "plan_detail": c.plan_detail,
                         "cond_detail": c.cond_detail, "median": c.median_after_unit, "range_ok": c.range_ok,
                         "passed": c.passed})
        return {"header_rows": hrows, "columns": cols,
                "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in glob.checks],
                "options": {"fields": [[k, FIELD_KO[k]] for k in header_map.FIELDS],
                            "units": [["KRW", "원"], ["KRW_1K", "천원"], ["KRW_10K", "만원"], ["none", "-"]],
                            "plans": [["", "요금제 무관"]] + [[p["plan_id"], f"{p['name']} {p['monthly_fee']:,}원"]
                                                          for (pt, _), p in plans.plans.items() if pt == partner],
                            "conditions": [[k, v] for k, v in CONDITION_LABELS.items()]}}

    def approve_mapping(self, sid: str, columns: list[dict], header_rows: list[int], progress=lambda _: None) -> dict:
        s, pm = self.sources.get(sid), self.pending_mapping.get(sid)
        if not s or not pm:
            raise UserError("승인할 매핑이 없습니다.")
        fields = {c["field_code"] for c in columns}
        if sum(1 for c in columns if c["field_code"] == "product_name") != 1:
            raise UserError("상품명 열이 정확히 하나 있어야 합니다.")
        if not fields & verify.PRICE_FIELDS:
            raise UserError("가격 항목(출고가·공시지원금·리베이트 등) 열이 하나 이상 있어야 합니다.")
        if not header_rows or any(r < 1 or r > 10 for r in header_rows):
            raise UserError("열 제목 행 번호가 올바르지 않습니다.")
        final = {}
        for c in columns:
            if c["col"] not in pm["cols"] or c["field_code"] not in FIELD_KO:
                raise UserError(f"{c.get('col')}열 설정이 올바르지 않습니다.")
            price = c["field_code"] in verify.PRICE_FIELDS
            final[c["col"]] = (c["field_code"], c.get("plan_id", "") if price else "",
                               c.get("condition_key", "base") if price else "base", c.get("unit", "none"))
        steps = s["result"]["steps"]
        first_call = len(self.history)

        def say(actor, text):
            steps.append({"actor": actor, "text": text})
            progress(text)
        say("사람", f"매핑 승인 → {s['partner']} 양식 템플릿으로 저장(다음부터는 AI 없이 읽음)")
        with self.lock:
            price_cols, rows = ingest.excel_parse(self.store, pm["rid"], s["partner"], PARTNERS[s["partner"]], pm["ws"],
                                                  pm["grid"], pm["cols"], final, sorted(header_rows))
            self.store.audit("사용자", "approve_mapping", f"{s['title']} 열 {len(columns)}개")
        del self.pending_mapping[sid]
        heads = [f"{FIELD_KO[f]} {p}{'·' + CONDITION_LABELS[c] if c != 'base' else ''}".strip() for _, (f, p, c, _) in price_cols]
        s["result"]["tables"].append(_table("규칙 파서가 읽은 값(staging, 원 단위)", ["상품명(원문)"] + heads,
                                            [[r[0]] + [ingest.won(v) for v in r[1:]] for r in rows]))
        say("코드", f"규칙 파서가 {len(rows)}개 행 × {len(price_cols)}개 항목을 읽어 staging에 저장")
        s["status"] = "완료"
        self._after_ingest(s, say)
        s["result"]["ai_calls"] += list(range(first_call + 1, len(self.history) + 1))
        return s["result"]

    def _p_kakao(self, s, data, say):
        text = data.decode("utf-8", "replace")
        rid = self._raw(s, data)
        inj = verify.injection_scan(text)
        say("코드", f"원본 보관(raw_file #{rid}) · 지시문 의심 문장 {len(inj)}건")
        s["result"]["tables"].append({"title": "원문", "text": text})
        say("AI", "AI가 공지에서 값마다 상품·가입유형·요금제·값을 원문 그대로 옮기는 중…")
        ai = kakao_parse.run(self.llm, text, Path(s["file"]).name)
        with self.lock:
            plans = PlanBook.from_db(self.store)
            staged = ingest.kakao_verify(text, ai, plans, s["partner"])
            kept = ingest.kakao_store(self.store, rid, s["partner"], text, staged, ai["conditions"])
        mark = {True: "✅", False: "❌", None: "➖"}
        s["result"]["tables"].append(_table(
            "코드 검증 결과(값별) — '개'는 코드 사전이 1만원으로 해석",
            ["상품(원문)", "구역(AI)", "요금제(AI → 사전)", "값(원문)", "코드 해석(원)", "줄", "짝", "구역", "요금제", "금액", "등급"],
            [[r["product_ref_raw"], r["join_phrase"], f"{r['plan_phrase']} → {g['plan_id'] or '?'}", r["value_text"],
              ingest.won(g["value"]), *[mark[c.ok] for c in checks], gr] for r, checks, g, gr in staged]))
        low = sum(1 for *_, gr in staged if gr == "low")
        say("코드", f"리베이트 {len(staged)}건 staging 저장(검증 실패 {low}건은 값 검수에서 사람이 확인), 조건 메모 {kept}건")
        if s["sample"]:
            ok, extra = ingest.kakao_score(staged, self.key["kakao_values"])
            say("정답", f"샘플 정답표와 대조: {ok}/{len(self.key['kakao_values'])} 일치" + (" (모의 응답)" if self.mock else ""))
        s["status"] = "완료"
        self._after_ingest(s, say)

    def _p_api(self, s, data, say):
        with self.lock:
            b = ingest.feed_api(self.store, self.root / s["file"], s["partner"], PlanBook.from_db(self.store))
        say("코드", f"연동 규격(고정 JSON) → 규칙으로 읽음: 단말 {b['devices']}개, 값 {b['values']}건 (AI 호출 0회)")
        s["status"] = "완료"
        self._after_ingest(s, say)

    def _p_csv(self, s, data, say):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig", "replace"))))
        if not rows:
            raise UserError("CSV가 비어 있습니다.")
        with self.lock:
            c = ingest.feed_csv(self.store, rows, Path(s["file"]).name, s["partner"], PlanBook.from_db(self.store), data)
        if not c["template_ok"]:
            raise UserError("CSV 열 제목이 승인된 양식과 다릅니다: " + ", ".join(ingest.C_TEMPLATE))
        say("코드", f"열 제목이 승인된 양식과 같음 → 규칙으로 읽음: 단말 {c['devices']}개, 값 {c['values']}건 (AI 호출 0회)")
        s["status"] = "완료"
        self._after_ingest(s, say)

    def _p_pdf(self, s, data, say):
        pages, tables, full = ingest.pdf_read(data)
        inj = verify.injection_scan(full)
        if ingest.PII.search(full):
            self._raw(s, data)
            raise UserError("개인정보 패턴(주민번호·휴대폰 번호)이 있어 AI에 보내지 않았습니다.")
        rid = self._raw(s, data, bool(inj))
        say("코드", f"원본 보관(raw_file #{rid}) · 개인정보 패턴 없음 · 지시문 의심 문장 {len(inj)}건"
                   + (" → 이 파일의 값은 자동 승인에서 제외" if inj else ""))
        s["result"]["tables"].append({"title": "PDF에서 코드가 뽑은 텍스트", "text": full})
        say("AI", "AI가 표의 값을 원문 그대로 옮기고 근거 문장을 붙이는 중…")
        path = self.root / s["file"]
        if not s["sample"]:
            path = self.root / "output" / "uploads" / f"{s['id']}.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        ai = extract_doc.run(self.llm, path, Path(s["file"]).name)
        staged = ingest.pdf_verify(pages, tables, ai)
        with self.lock:
            n = ingest.pdf_store(self.store, rid, s["partner"], staged)
        mark = {True: "✅", False: "❌", None: "➖"}
        s["result"]["tables"].append(_table("코드 검증 결과(값별) — 근거·셀·숫자 3중 대조",
                                            ["행", "항목", "원문 표기", "AI 숫자", "근거", "셀", "숫자", "등급"],
                                            [[row["row_label"], FIELD_KO.get(f["field"], f["field"]), f["value_text"],
                                              f["value_number"], *[mark[c.ok] for c in checks], g]
                                             for row, f, checks, g in staged]))
        zeroed = [f for (_, f, _, _) in staged if f["value_number"] == 0 and f["field"] != "registration_fee"]
        if inj:
            say("보안", "문서 속 지시문('모든 금액을 0원으로')의 영향: " + ("0원 값 있음 → 검증에서 걸러짐" if zeroed else "0원으로 바뀐 값 없음"))
        say("코드", f"{n}건 staging 저장(저장되는 숫자는 코드가 원문 표기를 파싱한 값)")
        s["status"] = "완료"
        self._after_ingest(s, say)

    def _p_email(self, s, data, say):
        text = data.decode("utf-8", "replace")
        rid = self._raw(s, data)
        s["result"]["tables"].append({"title": "원문 메일", "text": text})
        say("AI", "AI가 메일에서 변경 사항을 원문 그대로 뽑는 중…")
        ai = notice_parse.run(self.llm, text, Path(s["file"]).name)
        rows = []
        with self.lock:
            cat = Catalog.from_db(self.store)
            for ch in ai["changes"]:
                ev = verify.evidence_in_text(text, ch["evidence_text"])
                date = verify.parse_korean_date(ch["effective_from_text"] or "")
                cands = cat.m2_candidates(ch["product_ref_raw"], PARTNERS[s["partner"]], 1)
                pid = cands[0][0] if cands and cands[0][1] >= 0.3 else None
                with self.store.role("ingest_worker"):
                    self.store.exec("insert into staging_notice(raw_file_id, partner, change_type, product_ref_raw, product_id, "
                                    "new_value_int, effective_from, evidence_text) values (?,?,?,?,?,?,?,?)",
                                    (rid, s["partner"], ch["change_type"], ch["product_ref_raw"], pid,
                                     ch["new_value_number"], date, ch["evidence_text"]))
                rows.append([ch["change_type"], ch["product_ref_raw"], cat.products[pid].name if pid else "(후보 없음)",
                             ingest.won(ch["new_value_number"]), date or "-", "✅" if ev.ok else "❌"])
        s["result"]["tables"].append(_table("변경 사항(담당자 확인용 기록 — 확정 가격은 바꾸지 않음)",
                                            ["종류", "상품(원문)", "연결 후보(규칙)", "새 값", "적용일(코드 해석)", "근거"], rows))
        say("코드", f"변경 사항 {len(rows)}건을 기록했습니다. 이 결과로 확정 가격을 바꾸지 않습니다")
        s["status"] = "완료"

    # ───────────────────────── 수집 뒤: 규칙 매칭 → AI 매칭 제안 → 이상 판정 → AI 설명
    def _after_ingest(self, s, say):
        with self.lock:
            mentions = self.store.query("select * from staging_mention where product_id is null and "
                                        "match_state='unmatched' order by id")
            rule_rows, need_ai = ingest.rule_match(self.store, mentions)
        if rule_rows and s is not None:
            s["result"]["tables"].append(_table("상품명 매칭 — 규칙 단계(AI 없음)", ["파트너", "상품명(원문)", "단계", "결과"], rule_rows))
        if need_ai:
            say("AI", f"규칙으로 못 푼 상품명 {len(need_ai)}개 → AI가 후보 중 같은 상품을 고르는 중…")
            self._ai_match(need_ai, say)
            say("사람", f"상품명 {len(need_ai)}개는 [상품명 매칭]에서 확인·확정하세요")
        self._detect_and_explain(say)

    def _ai_match(self, need_ai, say):
        with self.lock:
            cat = Catalog.from_db(self.store)
        keys = [f"m{i:02d}" for i in range(1, len(need_ai) + 1)]
        blocks = [ingest.match_block(cat, k, m, c) for k, (m, c) in zip(keys, need_ai)]
        results = {}
        if self.mock:
            for k, (m, _), b in zip(keys, need_ai, blocks):
                if m["raw_name"] not in MOCK_MATCH_NAMES:
                    continue
                mock_key = f"m{MOCK_MATCH_NAMES.index(m['raw_name']) + 1:02d}"
                r = next(x for x in self.mock_out["m4_match_judge"]["results"] if x["mention_key"] == mock_key)
                out = self.llm.mock_output(f"m4_match_judge__{k}", title=f"상품명 → 후보 고르기 ({m['raw_name']})",
                                           system=match_judge.SYSTEM, content=[{"type": "text", "text": b}],
                                           schema=match_judge.schema([k]), output={"results": [dict(r, mention_key=k)]})
                results[k] = out["results"][0]
        elif self.llm.provider == "ollama":
            for i, (k, b) in enumerate(zip(keys, blocks), 1):
                say("AI", f"상품명 {i}/{len(keys)}: {need_ai[i - 1][0]['raw_name']}")
                results.update({r["mention_key"]: r for r in
                                match_judge.run(self.llm, b, [k], step_id=f"m4_match_judge__{k}")["results"]})
        else:
            results = {r["mention_key"]: r for r in match_judge.run(self.llm, "\n\n".join(blocks), keys)["results"]}
        with self.lock, self.store.role("ingest_worker"):
            for k, (m, cands) in zip(keys, need_ai):
                r = results.get(k)
                sugg = {"decision": "no_answer", "product_id": None, "reason": "AI 응답 없음", "grade": "low", "why": ""}
                if r:
                    chosen, idx = None, None
                    if r["decision"] == "match" and r["candidate_key"]:
                        idx = int(r["candidate_key"][1:]) - 1
                        chosen = cands[idx][0] if idx < len(cands) else None
                    attrs = verify_attributes(m["raw_name"], r["extracted_attributes"])
                    g, why = (match_grade(cat.products[chosen], m["model_code"], attrs, idx == 0, r["decision"])
                              if chosen else ("low", f"AI 판정: {r['decision']}"))
                    sugg = {"decision": r["decision"], "product_id": chosen, "reason": r["reason_ko"], "grade": g, "why": why}
                self.store.exec("update staging_mention set match_state='pending_match_review', ai_decision=?, ai_grade=? "
                                "where id=?", (json.dumps(sugg, ensure_ascii=False), sugg["grade"], m["id"]))

    def _detect_and_explain(self, say):
        with self.lock:
            recs = self.store.query("""select r.*, m.product_id, p.name as product_name from staging_record r
                                       join staging_mention m on m.id = r.mention_id
                                       join canonical_product p on p.id = m.product_id
                                       where r.state = 'validated' order by r.id""")
            out = ingest.detect(self.store, recs)
        if out:
            changed = [x for x in out if x[3] != "unchanged"]
            flagged = [x for x in out if x[2]]
            say("코드", f"지난달 확정값과 비교: 같음 {len(out) - len(changed)}건, 바뀜 {len(changed)}건, 규칙 위반 {len(flagged)}건")
        self.explain(say)

    def explain_pending(self, progress=lambda _: None) -> int:
        return self.explain(lambda actor, text: progress(text))

    def explain(self, say) -> int:
        """규칙이 잡은 이상 건 중 설명이 없는 것에 AI 설명을 받는다(숫자는 코드가 채움)."""
        with self.lock:
            rows = self.store.query("""select a.record_id, group_concat(a.rule_code || '(' || a.reason || ')', ', ') rules,
                                              r.partner, r.plan_id, r.field_code, r.condition_key, r.value_int,
                                              r.evidence_text, r.note, a.prev_value, p.name as product_name,
                                              group_concat(a.rule_code, ',') codes
                                       from staging_anomaly a join staging_record r on r.id = a.record_id
                                       join staging_mention m on m.id = r.mention_id
                                       join canonical_product p on p.id = m.product_id
                                       where a.ai_explanation is null group by a.record_id order by a.record_id""")
            plans = PlanBook.from_db(self.store)
        if not rows:
            return 0
        ids = [f"a{i}" for i in range(1, len(rows) + 1)]
        lines = [f"{iid} | 파트너: {r['partner']} | 상품: {r['product_name']} | 항목: {FIELD_KO[r['field_code']]}"
                 f"({label(plans, r['partner'], r['plan_id'], r['condition_key'])}) | 규칙: {r['rules']} | 이전 값: "
                 f"{r['prev_value']} | 새 값: {r['value_int']} | 원문 행: {r['evidence_text']} | 비고: {r['note'] or '없음'}"
                 for iid, r in zip(ids, rows)]
        say("AI", f"규칙이 잡은 이상 {len(rows)}건의 원인 분류·설명 틀을 쓰는 중…")
        block = "\n".join(lines)
        if self.mock:
            m = {x["item_id"]: x for x in self.mock_out["a3_anomaly_explain"]["items"]}
            items = []
            for iid, r in zip(ids, rows):
                src = m["a1"] if "UNIT_SCALE" in r["codes"] else m["a2"] if "PRICE_JUMP" in r["codes"] else \
                    {"likely_cause": "unknown", "explanation_template_ko": "", "check_points_ko": []}
                items.append(dict(src, item_id=iid))
            ai = self.llm.mock_output("a3_anomaly_explain", title="이상 항목 원인 분류 + 설명 틀", system=anomaly_explain.SYSTEM,
                                      content=[{"type": "text", "text": block}], schema=anomaly_explain.schema(ids),
                                      output={"items": items})
        else:
            ai = anomaly_explain.run(self.llm, block, ids)
        by = {x["item_id"]: x for x in ai["items"]}
        with self.lock, self.store.role("ingest_worker"):
            for iid, r in zip(ids, rows):
                x = by.get(iid)
                text, why = ingest.fill_template(x["explanation_template_ko"] if x else "", r["prev_value"], r["value_int"])
                if why:
                    text += f" (AI 설명 틀 버림: {why})"
                self.store.exec("update staging_anomaly set ai_cause=?, ai_explanation=? where record_id=?",
                                (x["likely_cause"] if x else "unknown", text[:300], r["record_id"]))
        return len(rows)

    # ───────────────────────── 상품명 매칭(사람 확정)
    def matches(self) -> list[dict]:
        with self.lock:
            cat = Catalog.from_db(self.store)
            ms = self.store.query("select * from staging_mention where product_id is null and "
                                  "match_state in ('unmatched','pending_match_review') order by id")
        out = []
        for m in ms:
            cands = cat.m2_candidates(m["raw_name"], m["category"], 5)
            sugg = json.loads(m["ai_decision"]) if m["ai_decision"] else None
            out.append({"id": m["id"], "partner": m["partner"], "raw_name": m["raw_name"], "model_code": m["model_code"],
                        "candidates": [{"id": pid, "name": cat.products[pid].name, "similarity": round(sim, 2)}
                                       for pid, sim in cands],
                        "suggestion": sugg, "top1": cands[0][0] if cands else None})
        return out

    def ai_match_pending(self, progress=lambda _: None) -> int:
        say = lambda actor, text: progress(text)   # noqa: E731
        with self.lock:
            ms = self.store.query("select * from staging_mention where product_id is null and match_state='unmatched' "
                                  "order by id")
            cat = Catalog.from_db(self.store)
        need = [(m, cat.m2_candidates(m["raw_name"], m["category"], 5)) for m in ms]
        if need:
            self._ai_match(need, say)
        return len(need)

    def confirm_match(self, mention_id: int, choice: str, progress=lambda _: None) -> dict:
        say = lambda actor, text: progress(text)   # noqa: E731
        with self.lock:
            m = self.store.one("select * from staging_mention where id=?", (mention_id,))
            if not m or m["product_id"] or m["match_state"] not in ("unmatched", "pending_match_review"):
                raise UserError("확정할 수 없는 상품명입니다.")
            sugg = json.loads(m["ai_decision"]) if m["ai_decision"] else {}
            with self.store.role("reviewer"):
                if choice == "NEW":
                    self.store.exec("update staging_mention set match_state='new_product_requested', match_path='사람' "
                                    "where id=?", (mention_id,))
                    self.store.exec("update staging_record set state='excluded', review_note='신규 상품 등록 대기' "
                                    "where mention_id=?", (mention_id,))
                    self.store.audit("사용자", "new_product_request", m["raw_name"])
                    return {"ok": True}
                if not self.store.one("select 1 from canonical_product where id=?", (choice,)):
                    raise UserError("없는 상품입니다.")
                path = "사람 확정(AI 제안과 같음)" if sugg.get("product_id") == choice else "사람 확정(AI 제안을 고침)"
                self.store.exec("update staging_mention set product_id=?, match_state='matched', match_path=? where id=?",
                                (choice, path, mention_id))
                if not self.store.one("select 1 from canonical_alias where partner=? and alias=?", (m["partner"], m["raw_name"])):
                    self.store.exec("insert into canonical_alias values (?,?,?,?)", (m["partner"], m["raw_name"], choice, "사용자"))
                self.store.audit("사용자", "confirm_match", f"{m['raw_name']} → {choice}")
        self._detect_and_explain(say)
        return {"ok": True}

    # ───────────────────────── 값 검수(사람)
    def _review_rows(self) -> list[dict]:
        st = self.store
        rows = st.query("""select r.*, m.product_id, m.raw_name, m.match_state, p.name as product_name,
                                  f.kind, f.injection_suspect
                           from staging_record r join staging_mention m on m.id = r.mention_id
                           left join canonical_product p on p.id = m.product_id
                           left join raw_file f on f.id = r.raw_file_id
                           where m.product_id is not null or m.match_state = 'new_product_requested'
                           order by r.partner, p.name, r.id""")
        anomalies = {}
        for a in st.query("select * from staging_anomaly order by id"):
            anomalies.setdefault(a["record_id"], []).append(a)
        plans = PlanBook.from_db(st)
        out = []
        for r in rows:
            prev = None
            if r["product_id"]:
                p = st.one("select value_int from canonical_price where partner=? and product_id=? and plan_id=? "
                           "and field_code=? and condition_key=? and month=?",
                           (r["partner"], r["product_id"], r["plan_id"], r["field_code"], r["condition_key"], PREV_MONTH))
                prev = p["value_int"] if p else None
            ans = anomalies.get(r["id"], [])
            reasons = []
            if r["field_code"] in anomaly_rules.SETTLEMENT_FIELDS and r["state"] == "pending_review":
                reasons.append("정산 금액(리베이트) 변경 → 사람 승인 필수")
            if any(a["severity"] == "warn" for a in ans):
                reasons.append("이상 경고(warn)")
            if r["grade"] == "low":
                reasons.append("코드 검증 실패(원문과 다름)")
            elif r["grade"] == "medium":
                reasons.append("일부 검증 불가")
            if r["injection_suspect"]:
                reasons.append("문서 속 지시문 의심 파일")
            state = r["state"]
            group = {"blocked": "blocked", "approved": "approved", "excluded": "excluded", "promoted": "promoted"}.get(state)
            if group is None:
                group = "needs" if reasons else "safe" if state in ("unchanged", "pending_review") else "other"
            pct = anomaly_rules.change_pct(prev, r["value_int"]) if r["value_int"] is not None else None
            out.append({"id": r["id"], "partner": r["partner"], "product": r["product_name"] or f"{r['raw_name']}(신규 후보)",
                        "raw_name": r["raw_name"], "field": FIELD_KO.get(r["field_code"], r["field_code"]),
                        "field_code": r["field_code"],
                        "condition": label(plans, r["partner"], r["plan_id"], r["condition_key"]), "prev": prev,
                        "value": r["value_int"], "value_text": r["value_text"],
                        "change_pct": None if pct is None else round(pct, 1),
                        "is_rebate": r["field_code"] == "rebate", "extractor": EXTRACTOR_KO.get(r["extractor"], r["extractor"]),
                        "source": KIND_KO.get(r["kind"], r["kind"] or "사람"), "grade": r["grade"],
                        "evidence": r["evidence_text"], "state": state, "group": group, "reasons": reasons,
                        "anomalies": [{"rule": a["rule_code"], "severity": a["severity"], "reason": a["reason"],
                                       "cause": a["ai_cause"], "explanation": a["ai_explanation"]} for a in ans],
                        "review_note": r["review_note"], "human_edited": bool(r["human_edited"])})
        return out

    def _review_groups(self) -> dict:
        groups = {k: [] for k in ("blocked", "needs", "safe", "approved", "excluded", "promoted", "other")}
        for r in self._review_rows():
            groups[r["group"]].append(r)
        return groups

    def review(self) -> dict:
        with self.lock:
            groups = self._review_groups()
            waiting = self.store.one("""select count(*) n from staging_record r join staging_mention m on m.id = r.mention_id
                                        where m.product_id is null and m.match_state != 'new_product_requested'""")["n"]
            products = [{"id": p["id"], "name": p["name"], "category": p["category"]}
                        for p in self.store.query("select * from canonical_product order by id")]
            plans = [dict(p) for p in self.store.query("select * from canonical_plan order by partner, monthly_fee desc")]
        return {"groups": groups, "waiting_for_match": waiting, "products": products, "plans": plans,
                "partners": list(PARTNERS), "fields": [["device_price", "출고가"], ["subsidy_amount", "공시지원금"],
                                                       ["rebate", "리베이트"], ["monthly_rental_fee", "월 렌탈료"]],
                "conditions": [[k, v] for k, v in CONDITION_LABELS.items() if k != "contract=24"]}

    def decide(self, record_id: int, action: str, value: str | None = None) -> dict:
        with self.lock:
            r = self.store.one("select * from staging_record where id=?", (record_id,))
            if not r or r["state"] in ("promoted",):
                raise UserError("처리할 수 없는 값입니다.")
            m = self.store.one("select * from staging_mention where id=?", (r["mention_id"],))
            if not m["product_id"]:
                raise UserError("상품명 매칭을 먼저 확정하세요.")
            with self.store.role("reviewer"):
                if action == "approve":
                    if r["state"] == "blocked":
                        raise UserError("차단(block)된 값은 그대로 승인할 수 없습니다. 원문을 확인해 값을 고치거나 제외하세요.")
                    self.store.exec("update staging_record set state='approved', review_note='사람 승인' where id=?", (record_id,))
                elif action == "edit":
                    v = parse_krw(str(value or ""), gae=True) if not str(value or "").strip().isdigit() else int(str(value).strip())
                    if v is None or v < 0:
                        raise UserError("금액을 읽을 수 없습니다. 예: 550000, 55개, 55만원")
                    self.store.exec("update staging_record set state='approved', value_int=?, human_edited=1, review_note=? "
                                    "where id=?", (v, f"사람 수정 {ingest.won(r['value_int'])} → {ingest.won(v)}", record_id))
                elif action == "reject":
                    self.store.exec("update staging_record set state='excluded', review_note='사람 제외' where id=?", (record_id,))
                elif action == "undo":
                    self.store.exec("delete from staging_anomaly where record_id=?", (record_id,))
                    self.store.exec("update staging_record set state='validated', review_note=null where id=?", (record_id,))
                else:
                    raise UserError("알 수 없는 동작입니다.")
                self.store.audit("사용자", f"review_{action}", f"staging#{record_id}")
            if action == "undo":
                recs = self.store.query("""select r.*, m.product_id from staging_record r join staging_mention m
                                           on m.id = r.mention_id where r.id=?""", (record_id,))
                ingest.detect(self.store, recs)
        return {"ok": True}

    def approve_safe(self) -> int:
        return self.approve_group("safe")

    def approve_group(self, group: str) -> int:
        """'일괄 승인 가능'(safe) 또는 '사람 확인 필요'(needs) 구역을 한 번에 승인한다. 차단(block)은 안 된다."""
        notes = {"safe": "일괄 승인(규칙·검증 통과)", "needs": "사람 확인 후 일괄 승인"}
        if group not in notes:
            raise UserError("차단된 값은 한꺼번에 승인할 수 없습니다. 하나씩 고치거나 제외하세요.")
        with self.lock:
            ids = [r["id"] for r in self._review_groups()[group]]
            with self.store.role("reviewer"):
                for i in ids:
                    self.store.exec("update staging_record set state='approved', review_note=? where id=?",
                                    (notes[group], i))
                self.store.audit("사용자", f"approve_{group}", f"{len(ids)}건")
        return len(ids)

    def add_manual(self, partner, product_id, plan_id, field_code, condition_key, value) -> dict:
        v = parse_krw(str(value), gae=True) if not str(value).strip().isdigit() else int(str(value).strip())
        if v is None:
            raise UserError("금액을 읽을 수 없습니다. 예: 550000, 55개, 55만원")
        with self.lock:
            p = self.store.one("select * from canonical_product where id=?", (product_id,))
            if partner not in PARTNERS or not p or field_code not in FIELD_KO or condition_key not in CONDITION_LABELS:
                raise UserError("입력값을 확인하세요.")
            if plan_id and not self.store.one("select 1 from canonical_plan where partner=? and plan_id=?", (partner, plan_id)):
                raise UserError("이 파트너의 요금제가 아닙니다.")
            with self.store.role("reviewer"):
                mid = self.store.exec("insert into staging_mention(partner, category, raw_name, product_id, match_state, "
                                      "match_path) values (?,?,?,?,?,?)",
                                      (partner, p["category"], p["name"], product_id, "matched", "사람 직접 입력"))
                self.store.exec("insert into staging_record(mention_id, partner, plan_id, field_code, condition_key, "
                                "value_int, unit, value_text, extractor, grade, state, review_note) values "
                                "(?,?,?,?,?,?,?,?,?,?,?,?)",
                                (mid, partner, plan_id or "", field_code, condition_key, v, "KRW", str(value), "manual",
                                 "human", "approved", "사람 직접 입력"))
                self.store.audit("사용자", "manual_add", f"{partner} {product_id} {field_code} {v}")
        return {"ok": True, "value": v}

    def promote(self) -> dict:
        """승인된 값을 확정 DB에 반영하고 계산을 다시 한다."""
        with self.lock:
            st = self.store
            recs = st.query("""select r.*, m.product_id, f.kind from staging_record r
                               join staging_mention m on m.id = r.mention_id left join raw_file f on f.id = r.raw_file_id
                               where r.state='approved' order by r.id""")
            if not recs:
                raise UserError("반영할 승인된 값이 없습니다. [값 검수]에서 먼저 승인하세요.")
            with st.role("reviewer"):
                for r in recs:
                    kind = r["kind"] or "manual"
                    st.exec("""insert into canonical_price(partner, product_id, plan_id, field_code, condition_key, value_int,
                                 unit, month, source, approved_by) values (?,?,?,?,?,?,?,?,?,?)
                               on conflict(partner, product_id, plan_id, field_code, condition_key, month)
                               do update set value_int=excluded.value_int, source=excluded.source,
                                             approved_by=excluded.approved_by""",
                            (r["partner"], r["product_id"], r["plan_id"], r["field_code"], r["condition_key"], r["value_int"],
                             r["unit"], MONTH, f"{SOURCE_LABEL.get(kind, kind)} · staging#{r['id']}", "사용자"))
                    st.exec("update staging_record set state='promoted' where id=?", (r["id"],))
                have = {(n["partner"], n["product_id"], n["text"]) for n in st.query("select * from canonical_note")}
                notes = [(x["partner"], None, x["text"]) for x in st.query("select * from staging_note order by id")]
                notes += [(x["partner"], x["product_id"], x["note"]) for x in st.query(
                    "select distinct r.partner, m.product_id, r.note from staging_record r join staging_mention m "
                    "on m.id = r.mention_id where r.note is not null and r.state='promoted'")]
                n_notes = 0
                for partner, pid, text in notes:
                    if (partner, pid, text) not in have:
                        st.exec("insert into canonical_note(partner, product_id, text, month, source, approved_by) "
                                "values (?,?,?,?,?,?)", (partner, pid, text, MONTH, "원문 조건·비고", "사용자"))
                        have.add((partner, pid, text))
                        n_notes += 1
                st.audit("사용자", "promote", f"{MONTH} 확정 {len(recs)}건, 조건 메모 {n_notes}건")
            self._run_calc()
            self.promotions += 1
        return {"promoted": len(recs), "notes": n_notes}

    def _run_calc(self):
        with self.store.role("calc_engine"):
            self.calc = calc_engine.run(self.store, MONTH)

    # ───────────────────────── 요금 설계 화면
    def screen_html(self) -> str:
        with self.lock:
            news = self.store.query("select partner, raw_name from staging_mention where match_state='new_product_requested' "
                                    "and category='telecom'")
            notices = [f"신규 상품 등록 대기: {x['raw_name']}({x['partner']}) — 관리자 승인 후 이 화면에 표시" for x in news]
            if not self.promotions:
                notices.insert(0, f"아직 {MONTH} 값을 확정하지 않아 {PREV_MONTH} 확정값을 보여 줍니다. "
                                  "[값 검수]에서 승인하고 [확정 반영]을 누르면 바뀝니다.")
            with self.store.role("screen_reader"):
                d = rate_design.collect(self.store, MONTH, PREV_MONTH, self.calc, notices, self.llm.mode_label)
        return rate_design.render(d)

    # ───────────────────────── 자연어 조회
    def nlq(self, question: str, progress=lambda _: None) -> dict:
        say = lambda actor, text: progress(text)   # noqa: E731
        question = (question or "").strip()
        if not question or len(question) > 200:
            raise UserError("질문을 200자 이내로 입력하세요.")
        samples = [q["question"] for q in self.key["nlq"]]
        if self.mock:
            if question not in samples:
                raise UserError("모의 모드에서는 예시 질문만 할 수 있습니다. 직접 질문하려면 엔진을 Ollama나 Claude로 바꾸세요.")
            step = f"q{samples.index(question) + 1}_nlq"
        else:
            step = f"nlq_{int(time.time())}"
        say("AI", "AI가 질문을 조회 종류와 조건으로 바꾸는 중…")
        ai = nlq_parse.run(self.llm, step, question, MONTH, PREV_MONTH)
        with self.lock:
            p, notes = ingest.resolve_nlq(PlanBook.from_db(self.store), ai["params"])
            plans = PlanBook.from_db(self.store)
            table = None
            with self.store.role("nlq_reader"):
                if ai["intent"] == "price_lookup":
                    rows = queries.price_lookup(self.store, MONTH, p["partner"], p["category"], p["field"], p["plan_id"],
                                                p["condition_key"], p["op"], p["amount"])
                    table = _table("조회 결과(숫자는 DB 확정값)", ["파트너", "상품", "항목", "요금제·조건", "값(원)", "확정 월"],
                                   [[r["partner"], r["name"], FIELD_KO[r["field_code"]],
                                     label(plans, r["partner"], r["plan_id"], r["condition_key"]),
                                     ingest.won(r["value_int"]), r["month"]] for r in rows])
                elif ai["intent"] == "price_change_list":
                    rows = queries.price_change_list(self.store, MONTH, PREV_MONTH, p["category"], p["field"], p["direction"])
                    table = _table("조회 결과(숫자는 DB 확정값)", ["파트너", "상품", "항목", "요금제·조건", "지난달", "이번 달"],
                                   [[r["partner"], r["name"], FIELD_KO[r["field_code"]],
                                     label(plans, r["partner"], r["plan_id"], r["condition_key"]),
                                     ingest.won(r["prev_value"]), ingest.won(r["new_value"])] for r in rows])
                elif ai["intent"] == "unmatched_products":
                    rows = queries.unmatched_products(self.store)
                    table = _table("조회 결과", ["파트너", "상품명", "모델코드", "상태"],
                                   [[r["partner"], r["raw_name"], r["model_code"], r["match_state"]] for r in rows])
                elif ai["intent"] == "anomaly_list":
                    rows = queries.anomaly_list(self.store)
                    table = _table("조회 결과", ["규칙", "심각도", "파트너", "항목", "값", "상태"],
                                   [[r["rule_code"], r["severity"], r["partner"], FIELD_KO.get(r["field_code"]),
                                     ingest.won(r["value_int"]), r["status"]] for r in rows])
        return {"ai": ai, "resolved": notes, "table": table,
                "unsupported": ai["unsupported_reason"] if ai["intent"] == "unsupported" else None,
                "call": len(self.history)}

    # ───────────────────────── 기록
    def audit(self) -> list[dict]:
        with self.lock:
            return self.store.query("select * from audit_log order by id desc limit 200")


class Jobs:
    """오래 걸리는 작업(AI 호출)을 한 번에 하나씩 작업 스레드에서 돌리고, 화면은 진행 상황을 주기적으로 읽는다."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.running: str | None = None
        self._lock = threading.Lock()

    def start(self, title: str, fn, *args) -> str:
        with self._lock:
            if self.running and self.jobs[self.running]["status"] == "running":
                raise UserError("다른 작업이 진행 중입니다. 끝난 뒤에 다시 눌러 주세요.")
            jid = uuid.uuid4().hex[:10]
            job = {"id": jid, "title": title, "status": "running", "messages": [], "result": None, "error": None,
                   "started": time.time()}
            self.jobs[jid] = job
            self.running = jid

        def progress(msg):
            job["messages"].append(msg)

        def work():
            try:
                job["result"] = fn(*args, progress)
                job["status"] = "done"
            except UserError as e:
                job["error"], job["status"] = str(e), "error"
            except Exception as e:     # 예상하지 못한 오류도 화면에 보여 준다
                job["error"], job["status"] = f"{type(e).__name__}: {e}", "error"
            job["elapsed"] = round(time.time() - job["started"], 1)
        threading.Thread(target=work, daemon=True).start()
        return jid

    def get(self, jid: str) -> dict:
        job = self.jobs.get(jid)
        if not job:
            raise UserError("없는 작업입니다.")
        return {k: v for k, v in job.items()}
