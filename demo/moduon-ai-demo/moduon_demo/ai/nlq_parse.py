"""AI ④-2 자연어처리: 관리자 질문을 '미리 정해진 조회 종류 + 조건'으로 바꾼다.

AI가 받는 것: 질문 한 문장 + 기준 월.
AI가 돌려주는 것: intent(조회 종류)와 enum 조건값, 요금제·금액은 원문 표기. SQL이나 답(숫자)은 만들지 않는다.
실제 조회는 rules/queries.py의 고정 함수가, 숫자는 DB가 만든다.
"""

INTENTS = ["price_lookup", "price_change_list", "unmatched_products", "anomaly_list", "unsupported"]
FIELDS = ["device_price", "monthly_fee", "subsidy_amount", "rebate", "monthly_rental_fee", "mandatory_months",
          "registration_fee", "monthly_installment", "installment_count"]

SYSTEM = """너는 모두온 관리자 '자연어 조회' 해석 담당이다.
관리자의 질문을 미리 정해진 조회 종류(intent)와 조건(params)으로 바꾸기만 한다.
SQL을 쓰지 않고, 숫자를 계산하거나 답을 만들지 않는다. 실제 조회와 숫자는 DB가 만든다.

intent:
- price_lookup: 조건에 맞는 확정 단가 목록
- price_change_list: 지난달 대비 오르거나 내린 확정 단가 목록
- unmatched_products: 아직 매칭되지 않은(신규 후보) 상품 목록
- anomaly_list: 이상 데이터 목록
- unsupported: 위 조회로 답할 수 없는 질문(정산금 합계·할인가처럼 계산이 필요한 요청, 예측, 데이터 수정 요청 등)
  리베이트 금액을 보여 달라는 질문은 계산이 아니라 조회이므로 price_lookup 또는 price_change_list로 바꾼다.

params:
- partner: A통신 / B통신 / C통신 / B상조 / C렌탈
- category: telecom(통신) / funeral(상조) / appliance(가전 렌탈)
- field: device_price(출고가), monthly_fee(월정액), subsidy_amount(공시지원금), rebate(리베이트·판매장려금), monthly_rental_fee(월 렌탈료), mandatory_months(의무사용기간), registration_fee(등록비), monthly_installment(월 납입금), installment_count(납입 횟수)
- plan_phrase: 질문에 적힌 요금제 표기 원문 그대로(예: "115요금제"). 요금제 해석은 코드가 한다.
- join: mnp(번호이동) / chg(기기변경) / new(신규가입)
- op: 금액 비교(gt 초과, gte 이상, lt 미만, lte 이하, eq 같음)
- amount_text: 질문에 적힌 금액 표기 원문 그대로(예: "30만원", "50개"). 숫자로 바꾸지 않는다. 금액 해석은 코드가 한다.
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
            "required": ["partner", "category", "field", "plan_phrase", "join", "op", "amount_text", "direction"],
            "properties": {
                "partner": _n({"type": "string", "enum": ["A통신", "B통신", "C통신", "B상조", "C렌탈"]}),
                "category": _n({"type": "string", "enum": ["telecom", "funeral", "appliance"]}),
                "field": _n({"type": "string", "enum": FIELDS}),
                "plan_phrase": _n({"type": "string"}),
                "join": _n({"type": "string", "enum": ["mnp", "chg", "new"]}),
                "op": _n({"type": "string", "enum": ["gt", "gte", "lt", "lte", "eq"]}),
                "amount_text": _n({"type": "string"}),
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
