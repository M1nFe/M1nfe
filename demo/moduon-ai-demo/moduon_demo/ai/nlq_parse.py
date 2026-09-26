"""AI ④-2 자연어처리: 관리자 질문을 '미리 정해진 조회 종류 + 조건'으로 바꾼다.

AI가 받는 것: 질문 한 문장 + 기준 월.
AI가 돌려주는 것: intent(조회 종류)와 enum 조건값. SQL이나 답(숫자)은 만들지 않는다.
실제 조회는 rules/queries.py의 고정 함수가, 숫자는 DB가 만든다.
"""

INTENTS = ["price_lookup", "price_change_list", "unmatched_products", "anomaly_list", "unsupported"]
FIELDS = ["device_price", "monthly_fee", "subsidy_amount", "monthly_rental_fee", "mandatory_months",
          "registration_fee", "monthly_installment", "installment_count"]

SYSTEM = """너는 모두온 관리자 '자연어 조회' 해석 담당이다.
관리자의 질문을 미리 정해진 조회 종류(intent)와 조건(params)으로 바꾸기만 한다.
SQL을 쓰지 않고, 숫자를 계산하거나 답을 만들지 않는다. 실제 조회와 숫자는 DB가 만든다.

intent:
- price_lookup: 조건에 맞는 확정 단가 목록
- price_change_list: 지난달 대비 오르거나 내린 확정 단가 목록
- unmatched_products: 아직 매칭되지 않은(신규 후보) 상품 목록
- anomaly_list: 이상 데이터 목록
- unsupported: 위 조회로 답할 수 없는 질문(계산·정산·예측·데이터 수정 요청 등)

params:
- partner: A통신 / B상조 / C렌탈
- category: telecom(통신) / funeral(상조) / appliance(가전 렌탈)
- field: device_price(출고가), monthly_fee(월정액), subsidy_amount(공시지원금), monthly_rental_fee(월 렌탈료), mandatory_months(의무사용기간), registration_fee(등록비), monthly_installment(월 납입금), installment_count(납입 횟수)
- condition: join=mnp(번호이동), join=chg(기기변경), join=new(신규가입), contract=24(24개월 약정), base(조건 없음)
- op/amount: 금액 비교(gt, gte, lt, lte, eq)와 원 단위 정수
- direction: up(오름) / down(내림)
질문에 없는 조건은 null로 둔다. unsupported일 때는 unsupported_reason에 이유를 한 문장으로 쓴다."""

_n = lambda t: {"anyOf": [t, {"type": "null"}]}   # noqa: E731

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["intent", "params", "unsupported_reason"],
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "params": {
            "type": "object", "additionalProperties": False,
            "required": ["partner", "category", "field", "condition", "op", "amount", "direction"],
            "properties": {
                "partner": _n({"type": "string", "enum": ["A통신", "B상조", "C렌탈"]}),
                "category": _n({"type": "string", "enum": ["telecom", "funeral", "appliance"]}),
                "field": _n({"type": "string", "enum": FIELDS}),
                "condition": _n({"type": "string", "enum": ["join=mnp", "join=chg", "join=new", "contract=24", "base"]}),
                "op": _n({"type": "string", "enum": ["gt", "gte", "lt", "lte", "eq"]}),
                "amount": _n({"type": "integer"}),
                "direction": _n({"type": "string", "enum": ["up", "down"]}),
            },
        },
        "unsupported_reason": _n({"type": "string"}),
    },
}


def run(llm, step_id: str, question: str, month: str, prev_month: str) -> dict:
    # 날짜처럼 매번 바뀌는 값은 시스템 프롬프트가 아니라 사용자 메시지에 넣는다(프롬프트 캐시 보존)
    content = [{"type": "text", "text": f"기준 월: {month} (지난달: {prev_month})\n질문: {question}"}]
    return llm.run(step_id, title=f"자연어 질문 해석: “{question}”",
                   system=SYSTEM, content=content, schema=SCHEMA, effort="low")
