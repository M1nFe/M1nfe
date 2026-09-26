"""계산 실행기. 확정(canonical) 테이블만 읽는다 — DB 역할 calc_engine은 staging을 읽을 수 없다."""
import hashlib
import json

from . import formulas as F

# 공식별 입력: (필드, 조건 키)
SPECS = {
    "telecom": ("통신 월 납부액(번호이동)", F.telecom_monthly_payment,
                {"monthly_fee": ("monthly_fee", "contract=24"), "device_price": ("device_price", "base"),
                 "subsidy": ("subsidy_amount", "join=mnp")}),
    "appliance": ("렌탈 의무기간 총비용", F.rental_total_cost,
                  {"monthly_rental_fee": ("monthly_rental_fee", "base"),
                   "mandatory_months": ("mandatory_months", "base"),
                   "registration_fee": ("registration_fee", "base")}),
    "funeral": ("상조 총 납입액", F.funeral_total_payment,
                {"monthly_installment": ("monthly_installment", "base"),
                 "installment_count": ("installment_count", "base")}),
}


def _latest(store, partner, product_id, field, cond, as_of):
    """기준 월(as_of) 이전의 가장 최근 확정값. 확정되지 않은 값은 절대 쓰지 않는다."""
    return store.one(
        "select value_int, month from canonical_price where partner=? and product_id=? and field_code=? "
        "and condition_key=? and month<=? order by month desc limit 1",
        (partner, product_id, field, cond, as_of))


def run(store, as_of: str) -> list[dict]:
    out = []
    pairs = store.query("select distinct cp.partner, cp.product_id, p.category, p.name from canonical_price cp "
                        "join canonical_product p on p.id = cp.product_id order by p.category, p.name")
    for pr in pairs:
        label, fn, spec = SPECS[pr["category"]]
        inputs, sources, missing = {}, {}, []
        for arg, (field, cond) in spec.items():
            row = _latest(store, pr["partner"], pr["product_id"], field, cond, as_of)
            if row is None:
                missing.append(f"{field}({cond})")
            else:
                inputs[arg] = row["value_int"]
                sources[arg] = row["month"]
        if missing:
            out.append({**pr, "formula": label, "inputs": inputs, "sources": sources,
                        "result": None, "skipped": "확정값 없음: " + ", ".join(missing)})
            continue
        result = fn(**inputs)
        h = hashlib.sha256(json.dumps([F.FORMULA_VERSION, label, inputs], sort_keys=True).encode()).hexdigest()
        store.exec("insert into calc_result(formula, version, as_of, partner, product_id, inputs, result, input_hash) "
                   "values (?,?,?,?,?,?,?,?)",
                   (label, F.FORMULA_VERSION, as_of, pr["partner"], pr["product_id"],
                    json.dumps(inputs, ensure_ascii=False), list(result.values())[-1], h))
        out.append({**pr, "formula": label, "inputs": inputs, "sources": sources,
                    "result": result, "hash": h[:12], "skipped": None})
    return out
