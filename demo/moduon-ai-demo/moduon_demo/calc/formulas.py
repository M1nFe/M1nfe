"""계산 엔진 — 고정 공식(결정론).

이 패키지는 AI(anthropic)나 수집·AI 코드(moduon_demo.ai, moduon_demo.llm)를 import하지 않는다.
테스트(tests/test_calc_isolation.py)가 이를 검사한다.

※ 아래 공식은 시연용 예시다. 실제 모두온 계산식(정산 또는 고객 가격)은 클라이언트 확인 후 교체한다.
금액은 모두 원 단위 정수(int)로만 계산한다. float를 쓰지 않는다.
"""

FORMULA_VERSION = "demo-v1"


def _floor(value: int, unit: int) -> int:
    return value // unit * unit


def telecom_monthly_payment(monthly_fee: int, device_price: int, subsidy: int, months: int = 24) -> dict:
    """[예시] 통신 월 납부액 = 요금제 월정액 + 단말 할부금.
    단말 할부금 = (출고가 − 공시지원금) ÷ 약정 개월 수, 10원 미만 절사. 할부 이자는 넣지 않는다."""
    for v in (monthly_fee, device_price, subsidy, months):
        assert isinstance(v, int), "금액은 정수만 허용"
    installment = _floor((device_price - subsidy) // months, 10)
    return {"단말 할부금": installment, "월 납부액": monthly_fee + installment}


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
