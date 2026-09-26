"""AI 출력 검증 — AI를 믿지 않고 원문과 대조한다(결정론 코드)."""
import re
from dataclasses import dataclass, field

from .conditions import UnknownCondition, resolve
from .text import compact, nfkc, parse_count, parse_krw


@dataclass
class Check:
    name: str
    ok: bool | None          # None = 이 문서 형식에서는 계산 불가
    detail: str = ""


@dataclass
class CheckResult:
    checks: list[Check] = field(default_factory=list)

    def add(self, name, ok, detail=""):
        self.checks.append(Check(name, ok, detail))

    @property
    def passed(self) -> bool:
        return all(c.ok is not False for c in self.checks)


# ---------- ① 엑셀 헤더 매핑 제안 검증 ----------

REQUIRED_TELECOM_FIELDS = {"product_name"}
PRICE_FIELDS = {"device_price", "monthly_fee", "subsidy_amount"}


def verify_header_mapping(grid: dict, ai: dict) -> CheckResult:
    """grid: {(row, col_letter): text}. AI가 말한 헤더 텍스트가 실제 그 칸에 있는지 등을 본다."""
    r = CheckResult()
    row = ai["header_row"]
    seen = {}
    for m in ai["mappings"]:
        actual = grid.get((row, m["col"]), "")
        r.add(f"{m['col']}{row} 헤더 일치", actual == m["source_header"],
              f"AI: '{m['source_header']}' / 실제 칸: '{actual}'")
        if m["field_code"] not in PRICE_FIELDS:
            continue
        key = (m["field_code"], m["condition_phrase"] or "")
        if key in seen:
            r.add("중복 매핑 없음", False, f"{key}가 {seen[key]}열과 {m['col']}열에 중복")
        seen[key] = m["col"]
        try:
            ck = resolve(m["condition_phrase"])
            r.add(f"{m['col']}열 조건 문구 사전 확인", True, f"'{m['condition_phrase'] or '-'}' → {ck}")
        except UnknownCondition:
            r.add(f"{m['col']}열 조건 문구 사전 확인", False,
                  f"'{m['condition_phrase']}'는 조건 사전에 없음 → 사람이 사전에 추가해야 함")
    fields = {m["field_code"] for m in ai["mappings"]}
    missing = REQUIRED_TELECOM_FIELDS - fields
    r.add("필수 필드(상품명) 포함", not missing, f"누락: {sorted(missing)}" if missing else "")
    r.add("가격 필드 1개 이상", bool(fields & PRICE_FIELDS))
    return r


# ---------- ② PDF 추출 검증 ----------

def evidence_match(page_text: str, evidence: str, row_label: str, value_text: str) -> Check:
    """근거 문장이 페이지에 정확히 한 번 있고, 그 안에 행 제목과 값이 모두 있어야 통과."""
    # 표 구분 기호(|, 탭 등)는 대조에서 무시한다
    page, ev = compact(page_text.replace("|", "")), compact(evidence.replace("|", ""))
    if not ev or len(evidence) > 200:
        return Check("원문 대조(evidence)", False, "근거 문장이 비었거나 200자 초과")
    n = page.count(ev)
    if n != 1:
        return Check("원문 대조(evidence)", False, f"근거 문장이 원문에 {n}번 나옴")
    if compact(row_label) not in ev or compact(value_text) not in ev:
        return Check("원문 대조(evidence)", False, "근거 문장 안에 행 제목이나 값이 없음")
    return Check("원문 대조(evidence)", True, f"'{evidence}' 원문에서 확인")


def cell_match(tables: list, row_label: str, col_header: str | None, value_text: str) -> Check:
    """표에서 (행 제목, 열 제목)이 만나는 칸의 값이 AI 값과 같은지 — 행·열 밀림을 잡는다."""
    for t in tables:
        if not t or not col_header:
            continue
        header = [compact(c or "") for c in t[0]]
        if compact(col_header) not in header:
            continue
        ci = header.index(compact(col_header))
        for row in t[1:]:
            if row and compact(row[0] or "") == compact(row_label):
                cell = row[ci] or ""
                ok = compact(cell) == compact(value_text)
                return Check("셀 대조(cell)", ok, f"표의 [{row_label} × {col_header}] 칸 = '{cell}'")
    return Check("셀 대조(cell)", None, "표 구조에서 해당 칸을 찾을 수 없음")


def number_crosscheck(value_text: str, value_number, unit: str) -> Check:
    """AI가 적은 숫자(value_number)와, 코드가 원문 표기(value_text)를 직접 파싱한 값이 같은지."""
    parsed = parse_krw(value_text) if unit == "KRW" else parse_count(value_text)
    ok = parsed is not None and value_number is not None and int(value_number) == parsed
    return Check("숫자 교차검증", ok, f"코드 파싱 '{value_text}' → {parsed} / AI 숫자 {value_number}")


# ---------- 문서 속 지시문(프롬프트 인젝션) 사전 검사 ----------

_INJECTION = re.compile(
    r"(이전\s*지시|지시는?\s*(모두\s*)?무시|ignore\s+(all|previous)|system\s*:|assistant\s*:|프롬프트)",
    re.IGNORECASE,
)


def injection_scan(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _INJECTION.search(line)]


# ---------- 공지 메일 근거 확인 ----------

def evidence_in_text(text: str, evidence: str) -> Check:
    ok = bool(evidence) and compact(evidence) in compact(text)
    return Check("원문 대조(evidence)", ok, f"'{evidence}'" if ok else "근거 문장이 원문에 없음")


_DATE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def parse_korean_date(text: str) -> str | None:
    """'2026년 11월 1일' → '2026-11-01'. 날짜 해석은 AI가 아니라 코드가 한다."""
    m = _DATE.search(nfkc(text or ""))
    return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}" if m else None
