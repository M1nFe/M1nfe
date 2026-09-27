"""계산 실행기. 확정(canonical) 테이블만 읽는다 — DB 역할 calc_engine은 staging을 읽을 수 없다."""
import hashlib
import json

from . import formulas as F

# 통신: (파트너, 단말) × 그 파트너의 요금제마다 계산. 월정액은 요금제 마스터, 나머지는 확정 단가.
TELECOM = ("통신 월 납부액(요금제별)", F.telecom_plan_payment)
# 공식별 입력: 인자 → (필드, 조건 키)
SPECS = {
    "appliance": ("렌탈 의무기간 총비용", F.rental_total_cost,
                  {"monthly_rental_fee": ("monthly_rental_fee", "base"),
                   "mandatory_months": ("mandatory_months", "base"),
                   "registration_fee": ("registration_fee", "base")}),
    "funeral": ("상조 총 납입액", F.funeral_total_payment,
                {"monthly_installment": ("monthly_installment", "base"),
                 "installment_count": ("installment_count", "base")}),
}


def _latest(store, partner, product_id, plan_id, field, cond, as_of):
    """기준 월(as_of) 이전의 가장 최근 확정값. 확정되지 않은 값은 절대 쓰지 않는다."""
    return store.one(
        "select value_int, month from canonical_price where partner=? and product_id=? and plan_id=? "
        "and field_code=? and condition_key=? and month<=? order by month desc limit 1",
        (partner, product_id, plan_id, field, cond, as_of))


def compute(category: str, inputs: dict) -> dict:
    return TELECOM[1](**inputs) if category == "telecom" else SPECS[category][1](**inputs)


def _one(store, as_of, pr, label, plan, spec, fixed=None):
    inputs, sources, missing = dict(fixed or {}), {k: "요금제 마스터" for k in fixed or {}}, []
    for arg, (field, plan_id, cond) in spec.items():
        row = _latest(store, pr["partner"], pr["product_id"], plan_id, field, cond, as_of)
        if row is None:
            missing.append(f"{field}({plan_id or cond})")
        else:
            inputs[arg] = row["value_int"]
            sources[arg] = row["month"]
    base = {**pr, "plan_id": plan["plan_id"] if plan else "", "plan_name": plan["name"] if plan else "",
            "formula": label, "inputs": inputs, "sources": sources}
    if missing:
        return {**base, "result": None, "skipped": "확정값 없음: " + ", ".join(missing)}
    result = compute(pr["category"], inputs)
    h = hashlib.sha256(json.dumps([F.FORMULA_VERSION, label, inputs], sort_keys=True).encode()).hexdigest()
    store.exec("insert into calc_result(formula, version, as_of, partner, product_id, plan_id, inputs, result, "
               "input_hash) values (?,?,?,?,?,?,?,?,?)",
               (label, F.FORMULA_VERSION, as_of, pr["partner"], pr["product_id"], base["plan_id"],
                json.dumps(inputs, ensure_ascii=False), json.dumps(result, ensure_ascii=False), h))
    return {**base, "result": result, "hash": h[:12], "skipped": None}


def run(store, as_of: str) -> list[dict]:
    out = []
    pairs = store.query("select distinct cp.partner, cp.product_id, p.category, p.name from canonical_price cp "
                        "join canonical_product p on p.id = cp.product_id order by p.category, p.name, cp.partner")
    for pr in pairs:
        if pr["category"] == "telecom":
            for plan in store.query("select plan_id, name, monthly_fee from canonical_plan where partner=? "
                                    "order by monthly_fee desc", (pr["partner"],)):
                spec = {"device_price": ("device_price", "", "base"),
                        "subsidy": ("subsidy_amount", plan["plan_id"], "base")}
                out.append(_one(store, as_of, pr, TELECOM[0], plan, spec, {"monthly_fee": plan["monthly_fee"]}))
        else:
            label, _, spec = SPECS[pr["category"]]
            out.append(_one(store, as_of, pr, label, None, {a: (f, "", c) for a, (f, c) in spec.items()}))
    return out
