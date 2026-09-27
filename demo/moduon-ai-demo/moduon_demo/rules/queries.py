"""자연어 조회용 '고정 조회 함수'. AI는 SQL을 쓰지 않고, 여기 있는 함수 이름과 enum 값만 고른다.

SQL 문자열은 모두 고정이고, 사용자 값은 바인딩 파라미터(?)로만 들어간다.
"""

OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "="}   # enum → 고정 연산자(화이트리스트)

_LATEST = """
  select cp.partner, p.category, p.name, cp.product_id, cp.plan_id, pl.name as plan_name, cp.field_code,
         cp.condition_key, cp.value_int, cp.month
  from canonical_price cp join canonical_product p on p.id = cp.product_id
  left join canonical_plan pl on pl.partner = cp.partner and pl.plan_id = cp.plan_id
  where cp.month = (select max(month) from canonical_price x
                    where x.partner = cp.partner and x.product_id = cp.product_id and x.plan_id = cp.plan_id
                      and x.field_code = cp.field_code and x.condition_key = cp.condition_key
                      and x.month <= ?)
"""


def price_lookup(store, month, partner=None, category=None, field=None, plan_id=None, condition=None,
                 op=None, amount=None):
    sql, params = _LATEST, [month]
    if partner:
        sql += " and cp.partner = ?"; params.append(partner)
    if category:
        sql += " and p.category = ?"; params.append(category)
    if field:
        sql += " and cp.field_code = ?"; params.append(field)
    if plan_id:
        sql += " and cp.plan_id = ?"; params.append(plan_id)
    if condition:
        sql += " and cp.condition_key = ?"; params.append(condition)
    if op and amount is not None:
        sql += f" and cp.value_int {OPS[op]} ?"; params.append(amount)
    return store.query(sql + " order by cp.partner, p.name, cp.field_code, cp.plan_id limit 500", params)


def price_change_list(store, month, prev_month, category=None, field=None, direction=None):
    sql = """
      select n.partner, p.name, n.plan_id, pl.name as plan_name, n.field_code, n.condition_key,
             o.value_int as prev_value, n.value_int as new_value
      from canonical_price n
      join canonical_price o on o.partner = n.partner and o.product_id = n.product_id and o.plan_id = n.plan_id
           and o.field_code = n.field_code and o.condition_key = n.condition_key and o.month = ?
      join canonical_product p on p.id = n.product_id
      left join canonical_plan pl on pl.partner = n.partner and pl.plan_id = n.plan_id
      where n.month = ? and n.value_int <> o.value_int
    """
    params = [prev_month, month]
    if category:
        sql += " and p.category = ?"; params.append(category)
    if field:
        sql += " and n.field_code = ?"; params.append(field)
    if direction == "up":
        sql += " and n.value_int > o.value_int"
    elif direction == "down":
        sql += " and n.value_int < o.value_int"
    return store.query(sql + " order by n.partner, p.name, n.plan_id limit 500", params)


def unmatched_products(store):
    return store.query("select partner, raw_name, model_code, match_state from staging_mention "
                       "where product_id is null order by partner limit 500")


def anomaly_list(store, severity=None):
    sql = """select a.rule_code, a.severity, r.partner, r.field_code, r.value_int, a.status
             from staging_anomaly a join staging_record r on r.id = a.record_id"""
    params = []
    if severity:
        sql += " where a.severity = ?"; params.append(severity)
    return store.query(sql + " limit 500", params)
