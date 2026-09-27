"""휴대폰 요금 설계 화면(HTML). 확정(canonical) 데이터와 계산 결과만 읽어 한 화면에 모은다.

- 흩어진 전산(엑셀·카톡·API·CSV)에서 들어와 사람이 확정한 값만 보인다. DB 역할 screen_reader는 staging을 읽을 수 없다.
- 셀러 화면: 요금제·가입유형별 리베이트(개 = 만원)까지 표시 / 고객 안내 화면: 리베이트·내부 조건 숨김.
- 이 모듈은 AI 코드를 import하지 않는다(테스트로 확인). 계산도 하지 않고 계산 엔진 결과를 표시만 한다.
"""
import html
import json

from ..rules import queries


def collect(store, month: str, prev_month: str, calc: list[dict], notices: list[str], mode_label: str) -> dict:
    """화면에 필요한 값을 확정 DB에서 모은다(읽기 전용)."""
    rows = queries.price_lookup(store, month, category="telecom")
    values = {(r["partner"], r["product_id"], r["plan_id"], r["field_code"], r["condition_key"]):
              (r["value_int"], r["month"]) for r in rows}
    prev = {(r["partner"], r["product_id"], r["plan_id"], r["field_code"], r["condition_key"]): r["value_int"]
            for r in store.query("select * from canonical_price where month=? and field_code='rebate'", (prev_month,))}
    plans = {}
    for p in store.query("select * from canonical_plan order by partner, monthly_fee desc"):
        plans.setdefault(p["partner"], []).append(dict(p))
    products = [dict(p) for p in store.query(
        "select distinct p.id, p.name from canonical_price cp join canonical_product p on p.id = cp.product_id "
        "where p.category = 'telecom' order by p.id")]
    sources = {}
    for r in store.query("select distinct partner, source from canonical_price cp join canonical_product p "
                         "on p.id = cp.product_id where p.category = 'telecom' and cp.month = ?", (month,)):
        label = (r["source"] or "").split(" · ")[0]
        if label and label not in sources.setdefault(r["partner"], []):
            sources[r["partner"]].append(label)
    notes = [dict(n) for n in store.query("select partner, product_id, text from canonical_note where month=?", (month,))]
    calc_by = {(c["partner"], c["product_id"], c["plan_id"]): c for c in calc if c["category"] == "telecom"}
    return {"month": month, "prev_month": prev_month, "mode_label": mode_label, "products": products,
            "partners": sorted({k[0] for k in values}), "plans": plans, "values": values, "prev": prev,
            "sources": sources, "notes": notes, "calc": calc_by, "notices": notices}


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _won(v) -> str:
    return "—" if v is None else f"{v:,}원"


def _gae(v) -> str:
    """리베이트 업계 표기: 1개 = 1만원."""
    if v is None:
        return "—"
    n = v / 10_000
    return f"{n:,.0f}개" if n == int(n) else f"{n:,.1f}개"


def _stale(month_of_value: str, month: str) -> str:
    if month_of_value == month:
        return ""
    return (f'<span class="badge warn" title="{_e(month)} 값이 확정되지 않아(검수 제외) {_e(month_of_value)} 확정값을 표시">'
            f'{_e(month_of_value[-2:].lstrip("0"))}월 값</span>')


def _rebate_cell(d, partner, pid, plan_id, cond) -> str:
    got = d["values"].get((partner, pid, plan_id, "rebate", cond))
    if not got:
        return '<td class="num seller-only muted" title="이 통신사에서 받은 리베이트 정책 없음">—</td>'
    v, m = got
    badge = ""
    before = d["prev"].get((partner, pid, plan_id, "rebate", cond))
    if m == d["month"] and before is not None and before != v:
        diff = (v - before) / 10_000
        cls = "up" if diff > 0 else "down"
        badge = (f'<span class="badge {cls}" title="{_e(d["prev_month"])} {_gae(before)} → {_gae(v)} '
                 f'(담당자 승인)">{"▲" if diff > 0 else "▼"}{abs(diff):,.0f}</span>')
    return f'<td class="num seller-only" title="{v:,}원"><b>{_gae(v)}</b>{badge}{_stale(m, d["month"])}</td>'


def _carrier(d, partner, prod) -> str:
    pid = prod["id"]
    price = d["values"].get((partner, pid, "", "device_price", "base"))
    body, best_pay, best_reb = [], None, None
    for plan in d["plans"].get(partner, []):
        c = d["calc"].get((partner, pid, plan["plan_id"]))
        sub = d["values"].get((partner, pid, plan["plan_id"], "subsidy_amount", "base"))
        if c and c["result"]:
            a, b = c["result"]["공시 월 납부액"], c["result"]["선약 월 납부액"]
            pay = [f'<td class="num{" best" if a <= b else ""}">{_won(a)}</td>',
                   f'<td class="num{" best" if b < a else ""}">{_won(b)}</td>']
            low = min(a, b)
            if best_pay is None or low < best_pay[0]:
                best_pay = (low, plan["name"], "공시지원금" if a <= b else "선택약정")
        else:
            why = c["skipped"] if c else "확정값 없음"
            pay = [f'<td class="num muted" colspan="2" title="{_e(why)}">계산 불가</td>']
        mnp = d["values"].get((partner, pid, plan["plan_id"], "rebate", "join=mnp"))
        if mnp and (best_reb is None or mnp[0] > best_reb[0]):
            best_reb = (mnp[0], plan["name"])
        body.append(
            f'<tr><th scope="row">{_e(plan["name"])}<small>{plan["monthly_fee"]:,}원/월</small></th>'
            f'<td class="num">{_won(sub[0]) if sub else "—"}{_stale(sub[1], d["month"]) if sub else ""}</td>'
            + "".join(pay)
            + _rebate_cell(d, partner, pid, plan["plan_id"], "join=mnp")
            + _rebate_cell(d, partner, pid, plan["plan_id"], "join=chg") + "</tr>")
    notes = [n["text"] for n in d["notes"] if n["partner"] == partner and n["product_id"] in (None, pid)]
    src = " · ".join(d["sources"].get(partner, [])) or "기존 확정"
    summary = []
    if best_pay:
        summary.append(f'<span class="chip">최저 월 납부액 <b>{_won(best_pay[0])}</b> '
                       f'<small>{_e(best_pay[1])} · {_e(best_pay[2])}</small></span>')
    if best_reb:
        summary.append(f'<span class="chip seller-only rebate">번호이동 최대 리베이트 <b>{_gae(best_reb[0])}</b> '
                       f'<small>{_e(best_reb[1])}</small></span>')
    return f'''
    <section class="carrier">
      <header>
        <div><h3>{_e(partner)}</h3><p class="src">출처: {_e(src)}</p></div>
        <div class="price">출고가 <b>{_won(price[0]) if price else "—"}</b></div>
      </header>
      <div class="chips">{"".join(summary)}</div>
      <div class="scroll"><table>
        <thead><tr><th scope="col">요금제</th><th scope="col" class="num">공시지원금</th>
          <th scope="col" class="num">월 납부액<small>공시지원금</small></th>
          <th scope="col" class="num">월 납부액<small>선택약정 25%</small></th>
          <th scope="col" class="num seller-only">리베이트<small>번호이동</small></th>
          <th scope="col" class="num seller-only">리베이트<small>기기변경</small></th></tr></thead>
        <tbody>{"".join(body)}</tbody>
      </table></div>
      {"<ul class='notes seller-only'>" + "".join(f"<li>{_e(t)}</li>" for t in notes) + "</ul>" if notes else ""}
    </section>'''


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--sub:#5b6270;--line:#e3e6eb;--accent:#2f5bea;--accent-soft:#e8eefe;
--good:#0f7b4b;--good-soft:#e3f5ec;--warn:#9a5b00;--warn-soft:#fff2d9;--up:#c2410c;--down:#1d4ed8;--rebate:#7c3aed;--rebate-soft:#f1eafe}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e8eaee;--sub:#9aa2b1;--line:#2a2f3a;--accent:#7a9bff;
--accent-soft:#1f2a4a;--good:#4ade80;--good-soft:#123324;--warn:#fbbf24;--warn-soft:#3a2d0c;--up:#fb923c;--down:#93c5fd;--rebate:#c4a5ff;--rebate-soft:#2a2140}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:20px 16px 48px}
.top{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;justify-content:space-between;margin-bottom:12px}
h1{font-size:22px;margin:0}h1 small{display:block;font-size:13px;color:var(--sub);font-weight:400;margin-top:4px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--card)}
.seg button{border:0;background:none;color:var(--sub);padding:8px 14px;font:inherit;cursor:pointer}
.seg button[aria-pressed=true]{background:var(--accent);color:#fff}
.banner{border-radius:10px;padding:10px 14px;margin:8px 0;background:var(--warn-soft);color:var(--warn);font-size:14px}
.banner.info{background:var(--accent-soft);color:var(--accent)}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 12px}
.tabs button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:7px 14px;font:inherit;cursor:pointer}
.tabs button[aria-selected=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent);font-weight:600}
.product[hidden]{display:none}
.carrier{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
.carrier header{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.carrier h3{margin:0;font-size:18px}.src{margin:2px 0 0;color:var(--sub);font-size:13px}
.price{font-size:14px;color:var(--sub)}.price b{color:var(--ink);font-size:16px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.chip{background:var(--good-soft);color:var(--good);border-radius:8px;padding:4px 10px;font-size:13px}
.chip.rebate{background:var(--rebate-soft);color:var(--rebate)}.chip small{opacity:.8;margin-left:4px}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:560px}
th,td{padding:9px 10px;border-top:1px solid var(--line);text-align:left;vertical-align:top}
thead th{border-top:0;color:var(--sub);font-weight:600;font-size:13px}
th small,td small{display:block;font-size:12px;color:var(--sub);font-weight:400}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.best{color:var(--good);font-weight:700}td.muted{color:var(--sub)}
td.seller-only b{color:var(--rebate)}
.badge{display:inline-block;margin-left:6px;padding:0 6px;border-radius:6px;font-size:12px;font-weight:600;vertical-align:1px}
.badge.up{color:var(--up);background:color-mix(in srgb,var(--up) 14%,transparent)}
.badge.down{color:var(--down);background:color-mix(in srgb,var(--down) 14%,transparent)}
.badge.warn{color:var(--warn);background:var(--warn-soft)}
.notes{margin:10px 0 0;padding-left:18px;color:var(--sub);font-size:13px}
.missing{color:var(--sub);font-size:14px;margin:8px 2px}
footer.flow{margin-top:24px;color:var(--sub);font-size:13px;border-top:1px solid var(--line);padding-top:12px}
body.customer .seller-only{display:none}
@media (max-width:640px){.carrier{padding:12px}th,td{padding:8px 6px;font-size:13px}table{min-width:520px}
h1{font-size:20px}}
"""

JS = """
const views=document.querySelectorAll('[data-view]');
views.forEach(b=>b.addEventListener('click',()=>{document.body.classList.toggle('customer',b.dataset.view==='customer');
views.forEach(x=>x.setAttribute('aria-pressed',String(x===b)));}));
const tabs=document.querySelectorAll('[role=tab]');
tabs.forEach(t=>t.addEventListener('click',()=>{tabs.forEach(x=>x.setAttribute('aria-selected',String(x===t)));
document.querySelectorAll('.product').forEach(p=>p.hidden=p.id!==t.getAttribute('aria-controls'));}));
"""


def render(d: dict) -> str:
    tabs, panels = [], []
    for i, prod in enumerate(d["products"]):
        pid = prod["id"]
        tabs.append(f'<button role="tab" aria-selected="{str(i == 0).lower()}" aria-controls="p-{pid}">{_e(prod["name"])}</button>')
        have = [pt for pt in d["partners"] if any(k[0] == pt and k[1] == pid for k in d["values"])]
        lacking = [pt for pt in d["partners"] if pt not in have]
        panels.append(f'<div class="product" id="p-{pid}" role="tabpanel"{"" if i == 0 else " hidden"}>'
                      + "".join(_carrier(d, pt, prod) for pt in have)
                      + (f'<p class="missing">{_e(", ".join(lacking))}: 이 단말의 정책을 받지 않음</p>' if lacking else "")
                      + "</div>")
    banners = []
    if "모의" in d["mode_label"]:
        banners.append('<div class="banner">모의 응답으로 만든 시연 화면입니다. AI 단계의 결과는 미리 써 둔 예시입니다.</div>')
    banners += [f'<div class="banner info">{_e(n)}</div>' for n in d["notices"]]
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>휴대폰 요금 설계</title><style>{CSS}</style></head>
<body><div class="wrap">
  <div class="top">
    <h1>휴대폰 요금 설계<small>모두온 · 통신 3사 통합 · 기준 {_e(d["month"])} 확정 데이터 · 가상 샘플 · {_e(d["mode_label"])}</small></h1>
    <div class="seg" role="group" aria-label="보기">
      <button data-view="seller" aria-pressed="true">셀러 화면</button>
      <button data-view="customer" aria-pressed="false">고객 안내 화면</button>
    </div>
  </div>
  {"".join(banners)}
  <div class="tabs" role="tablist">{"".join(tabs)}</div>
  {"".join(panels)}
  <footer class="flow">
    이 화면은 확정(canonical) 데이터와 계산 엔진 결과만 읽습니다. 검수 전 값은 DB 권한상 이 화면에 나올 수 없습니다.<br>
    월 납부액은 고정 공식(예시): 공시지원금 = 월정액 + (출고가 − 공시지원금) ÷ 24 / 선택약정 = 월정액 × 75% + 출고가 ÷ 24 (10원 미만 절사).
    초록색이 더 싼 방식입니다.<br>
    <span class="seller-only">리베이트는 통신사 원천 금액이며 1개 = 1만원입니다. ▲▼는 지난달 대비 변동(담당자 승인 완료)입니다.
    분양몰에서는 총판·대리점 정책에 따라 셀러에게 보이는 금액이 달라집니다(설정 예정).</span>
  </footer>
</div><script>{JS}</script></body></html>
"""


def summary_rows(d: dict, product_id: str) -> list[list]:
    """터미널 미리보기용: 한 단말의 통신사 × 요금제 표."""
    out = []
    for partner in d["partners"]:
        for plan in d["plans"].get(partner, []):
            c = d["calc"].get((partner, product_id, plan["plan_id"]))
            if not c:
                continue
            sub = d["values"].get((partner, product_id, plan["plan_id"], "subsidy_amount", "base"))
            mnp = d["values"].get((partner, product_id, plan["plan_id"], "rebate", "join=mnp"))
            chg = d["values"].get((partner, product_id, plan["plan_id"], "rebate", "join=chg"))
            r = c["result"] or {}
            out.append([partner, f"{plan['name']} {plan['monthly_fee']:,}", _won(sub[0]) if sub else "—",
                        _won(r.get("공시 월 납부액")), _won(r.get("선약 월 납부액")),
                        _gae(mnp[0]) if mnp else "—", _gae(chg[0]) if chg else "—"])
    return out


def to_json(d: dict) -> str:
    """테스트·디버그용 직렬화(튜플 키를 문자열로)."""
    return json.dumps({**d, "values": {"|".join(k): v for k, v in d["values"].items()},
                       "prev": {"|".join(k): v for k, v in d["prev"].items()},
                       "calc": {"|".join(k): v["result"] for k, v in d["calc"].items()}}, ensure_ascii=False)
