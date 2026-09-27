"""이상 데이터 판정 규칙. 무엇이 이상이고 얼마나 심각한지는 여기(코드)가 정한다."""

FIELD_RANGES = {
    "device_price": (0, 3_000_000),
    "monthly_fee": (10_000, 200_000),
    "subsidy_amount": (0, 1_500_000),
    "monthly_rental_fee": (5_000, 300_000),
    "mandatory_months": (12, 84),
    "registration_fee": (0, 300_000),
    "monthly_installment": (10_000, 150_000),
    "installment_count": (12, 240),
    "rebate": (0, 1_500_000),
}
ZERO_OK = {"subsidy_amount", "registration_fee", "rebate"}
# 정산 금액: 바뀌면 규칙 위반이 없어도 자동 반영하지 않고 항상 사람이 승인한다 (설계서 4.2.1)
SETTLEMENT_FIELDS = {"rebate"}
JUMP_THRESHOLD = 0.20


def check(field: str, prev: int | None, curr: int) -> list[dict]:
    events = []
    if curr < 0 or (curr == 0 and field not in ZERO_OK):
        events.append({"rule_code": "NEG_OR_ZERO", "severity": "block", "reason": "금액이 0 이하"})
    unit_scale = False
    if prev and curr > 0:
        ratio = curr / prev
        for f in (10, 100, 1000, 10000):
            if abs(ratio / f - 1) <= 0.02 or abs(ratio * f - 1) <= 0.02:
                unit_scale = True
                events.append({"rule_code": "UNIT_SCALE", "severity": "block",
                               "reason": f"직전 값의 {f}배 또는 1/{f} — 원·천원·만원 단위 혼동 의심"})
                break
        if not unit_scale and abs(ratio - 1) > JUMP_THRESHOLD:
            events.append({"rule_code": "PRICE_JUMP", "severity": "warn",
                           "reason": f"직전 대비 변동폭 {abs(ratio - 1):.0%} > {JUMP_THRESHOLD:.0%}"})
    lo, hi = FIELD_RANGES.get(field, (None, None))
    if lo is not None and not (lo <= curr <= hi):
        events.append({"rule_code": "OUT_OF_RANGE", "severity": "warn",
                       "reason": f"허용 범위 {lo:,}~{hi:,} 밖"})
    return events


def change_pct(prev: int | None, curr: int) -> float | None:
    return None if not prev else (curr - prev) / prev * 100
