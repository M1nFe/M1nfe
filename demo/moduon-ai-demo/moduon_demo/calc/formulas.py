"""계산 엔진 — 고정 공식(결정론).

이 패키지는 AI(anthropic)나 수집·AI 코드(moduon_demo.ai, moduon_demo.llm)를 import하지 않는다.
테스트(tests/test_calc_isolation.py)가 이를 검사한다.

※ 아래 공식은 시연용 예시다. 실제 모두온 계산식(정산 또는 고객 가격)은 클라이언트 확인 후 교체한다.
금액은 모두 원 단위 정수(int)로만 계산한다. float를 쓰지 않는다.
"""

FORMULA_VERSION = "demo-v2"


def _floor(value: int, unit: int) -> int:
    return value // unit * unit


def telecom_plan_payment(monthly_fee: int, device_price: int, subsidy: int, months: int = 24,
                         selective_pct: int = 25) -> dict:
    """[예시] 요금제 하나에 대한 두 할인 방식의 고객 월 납부액. 할부 이자는 넣지 않는다.
    ① 공시지원금: 월정액 + (출고가 − 공시지원금) ÷ 24, 10원 미만 절사
    ② 선택약정: 월정액 − 월정액×25%(10원 미만 절사) + 출고가 ÷ 24(10원 미만 절사)
    리베이트는 판매점 쪽 정산 금액이라 고객 월 납부액에 넣지 않는다."""
    for v in (monthly_fee, device_price, subsidy, months, selective_pct):
        assert isinstance(v, int), "금액은 정수만 허용"
    inst_sub = _floor((device_price - subsidy) // months, 10)
    discount = _floor(monthly_fee * selective_pct // 100, 10)
    inst_sel = _floor(device_price // months, 10)
    return {"공시 할부금": inst_sub, "공시 월 납부액": monthly_fee + inst_sub,
            "선약 요금할인": discount, "선약 할부금": inst_sel, "선약 월 납부액": monthly_fee - discount + inst_sel}


def rental_total_cost(monthly_rental_fee: int, mandatory_months: int, registration_fee: int) -> dict:
    """[예시] 렌탈 의무기간 총비용 = 월 렌탈료 × 의무사용기간 + 등록비."""
    for v in (monthly_rental_fee, mandatory_months, registration_fee):
        assert isinstance(v, int), "금액은 정수만 허용"
    return {"의무기간 총비용": monthly_rental_fee * mandatory_months + registration_fee}


def funeral_total_payment(monthly_installment: int, installment_count: int) -> dict:
    """[예시] 상조 총 납입액 = 월 납입금 × 납입 횟수."""
    for v in (monthly_installment, installment_count):
        assert isinstance(v, int), "금액은 정수만 허용"
    return {"총 납입액": monthly_installment * installment_count}
