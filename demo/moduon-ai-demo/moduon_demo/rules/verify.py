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
PRICE_FIELDS = {"device_price", "monthly_fee", "subsidy_amount", "rebate"}
UNIT_MULT = {"KRW": 1, "KRW_1K": 1_000, "KRW_10K": 10_000, "none": 1}


def compose_header(grid: dict, rows: list[int], col: str) -> str:
    """여러 줄 열 제목을 위에서부터 ' / '로 잇는다(병합 셀로 같은 글자가 반복되면 한 번만)."""
    parts = []
    for r in rows:
        t = grid.get((r, col), "")
        if t and (not parts or parts[-1] != t):
            parts.append(t)
    return " / ".join(parts)


def _same_header(a: str, b: str) -> bool:
    return compact(a.replace("/", "")) == compact(b.replace("/", ""))


def _median(values: list[int]) -> int | None:
    v = sorted(values)
    return v[len(v) // 2] if v else None


@dataclass
class ColumnCheck:
    col: str
    source_header: str
    header_ok: bool
    field_code: str
    plan_id: str | None = None        # None = 해석 실패
    plan_detail: str = ""
    condition_key: str | None = None
    cond_detail: str = ""
    unit: str = "none"
    median_after_unit: int | None = None
    range_ok: bool | None = None

    @property
    def passed(self) -> bool:
        return (self.header_ok and self.plan_id is not None and self.condition_key is not None
                and self.range_ok is not False)


def verify_header_mapping(grid: dict, ai: dict, partner: str, plan_book, data_rows: range):
    """AI 열 매핑 제안을 시트와 사전으로 검증한다. (열별 결과 목록, 전체 검사 CheckResult)"""
    from .anomaly import FIELD_RANGES   # 순환 import 방지
    r = CheckResult()
    rows = ai["header_rows"]
    ok_rows = bool(rows) and len(rows) <= 3 and rows == list(range(rows[0], rows[0] + len(rows)))
    r.add("열 제목 행", ok_rows, f"AI: {rows}행" + ("" if ok_rows else " — 1~3개의 연속된 행이어야 함"))
    cols, seen = [], {}
    for m in ai["mappings"]:
        actual = compose_header(grid, rows, m["col"]) if ok_rows else ""
        c = ColumnCheck(m["col"], m["source_header"], _same_header(actual, m["source_header"]), m["field_code"],
                        plan_id="", condition_key="base", unit=m["unit"])
        if not c.header_ok:
            c.plan_detail = f"실제 열 제목: '{actual}'"
        if m["field_code"] in PRICE_FIELDS:
            try:
                c.plan_id = plan_book.resolve(partner, m["plan_phrase"])
                c.plan_detail = f"'{m['plan_phrase']}' → {c.plan_id}" if m["plan_phrase"] else "요금제 무관"
            except UnknownCondition:
                c.plan_id, c.plan_detail = None, f"'{m['plan_phrase']}' 사전에 없음"
            try:
                c.condition_key = resolve(m["join_phrase"])
                c.cond_detail = f"'{m['join_phrase']}' → {c.condition_key}" if m["join_phrase"] else "조건 없음"
            except UnknownCondition:
                c.condition_key, c.cond_detail = None, f"'{m['join_phrase']}' 사전에 없음"
            vals = []
            for row in data_rows:
                v = grid.get((row, m["col"]), "")
                n = int(v) if v.isdigit() else parse_krw(v)
                if n is not None:
                    vals.append(n * UNIT_MULT[m["unit"]])
            c.median_after_unit = _median(vals)
            lo, hi = FIELD_RANGES.get(m["field_code"], (None, None))
            if lo is not None and c.median_after_unit is not None:
                c.range_ok = lo <= c.median_after_unit <= hi
            key = (m["field_code"], c.plan_id, c.condition_key)
            if key in seen:
                r.add("중복 매핑 없음", False, f"{m['col']}열과 {seen[key]}열이 같은 항목·요금제·조건")
            seen[key] = m["col"]
        cols.append(c)
    fields = {m["field_code"] for m in ai["mappings"]}
    missing = REQUIRED_TELECOM_FIELDS - fields
    r.add("필수 필드(상품명) 포함", not missing, f"누락: {sorted(missing)}" if missing else "")
    r.add("가격 필드 1개 이상", bool(fields & PRICE_FIELDS))
    return cols, r


# ---------- ① 카톡·문자 공지 추출 검증 ----------

def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def kakao_checks(text: str, rec: dict, plan_book, partner: str) -> tuple[list[Check], dict]:
    """카톡 공지에서 AI가 옮긴 값 하나를 원문과 대조한다. (검사 목록, 코드가 해석한 값)"""
    lines = _lines(text)
    ev = compact(rec["evidence_text"])
    hits = [i for i, line in enumerate(lines) if ev and ev in compact(line)]
    checks = [Check("원문 줄 대조", len(hits) == 1,
                    "원문에서 확인" if len(hits) == 1 else f"근거 줄이 원문에 {len(hits)}번 나옴")]
    pair = compact(rec["plan_phrase"] + rec["value_text"])
    ok = bool(hits) and compact(rec["product_ref_raw"]) in ev and pair in ev
    checks.append(Check("상품·요금제·값 짝", ok, "근거 줄 안에 상품명, '요금제 값' 순서로 있음" if ok
                        else "근거 줄에 상품명이나 '요금제 값' 짝이 없음"))
    got = {"plan_id": None, "condition_key": None, "value": parse_krw(rec["value_text"], gae=True)}
    try:
        got["condition_key"] = resolve(rec["join_phrase"])
    except UnknownCondition:
        pass
    section = None
    if len(hits) == 1:
        for line in reversed(lines[:hits[0]]):   # 위로 올라가며 가장 가까운 구역 제목(가입유형만 있는 줄)
            if not any(ch.isdigit() for ch in line):
                try:
                    k = resolve(line)
                except UnknownCondition:
                    continue
                if k != "base":
                    section = (line.strip(), k)
                    break
    checks.append(Check("구역(가입유형) 대조", section is not None and section[1] == got["condition_key"],
                        f"이 줄은 '{section[0]}' 구역" if section else "구역 제목을 찾지 못함"))
    try:
        got["plan_id"] = plan_book.resolve(partner, rec["plan_phrase"]) or None
        checks.append(Check("요금제 사전", got["plan_id"] is not None, f"'{rec['plan_phrase']}' → {got['plan_id']}"))
    except UnknownCondition:
        checks.append(Check("요금제 사전", False, f"'{rec['plan_phrase']}' 사전에 없음"))
    checks.append(Check("금액 해석(코드)", got["value"] is not None,
                        f"'{rec['value_text']}' → {got['value']:,}원" if got["value"] is not None else "해석 불가"))
    return checks, got


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
