"""모의(mock) 모드로 전체 시연을 돌려 파이프라인이 끝까지 동작하는지 확인한다(API 키 불필요)."""
import copy
import io
import json
from pathlib import Path

from moduon_demo import scenes
from moduon_demo.console import Reporter
from moduon_demo.llm import LLM
from moduon_demo.store import Store

ROOT = Path(__file__).resolve().parent.parent
MOCK = {p.stem: json.loads(p.read_text(encoding="utf-8"))["output"] for p in (ROOT / "mock_responses").glob("*.json")}


def _ctx(tmp_path=None):
    rep = Reporter(file=io.StringIO(), width=160)
    ctx = scenes.Ctx(root=ROOT, store=Store(), llm=LLM("mock", ROOT, rep), rep=rep,
                     key=json.loads((ROOT / "data/answer_key.json").read_text(encoding="utf-8")),
                     tag="pytest_mock")
    return ctx


def _run(monkeypatch=None, overrides: dict | None = None, upto=None):
    """모의 모드로 장면을 차례로 돌리되(upto까지), 일부 AI 응답을 주어진 출력으로 바꾼다."""
    if overrides:
        orig = LLM.run

        def fake_run(self, step_id, **kw):
            if step_id in overrides:
                self.calls.append(None)
                return overrides[step_id]
            return orig(self, step_id, **kw)
        monkeypatch.setattr(LLM, "run", fake_run)
    ctx = _ctx()
    for s in scenes.SCENES:
        s(ctx)
        if s is upto:
            break
    return ctx


def _canon(st, partner, pid, plan, field, cond, month="2026-10"):
    r = st.one("select value_int from canonical_price where partner=? and product_id=? and plan_id=? and field_code=? "
               "and condition_key=? and month=?", (partner, pid, plan, field, cond, month))
    return r["value_int"] if r else None


def test_full_demo_mock():
    ctx = _run()
    st = ctx.store
    try:
        assert len(ctx.llm.calls) == len(scenes.STEP_IDS)
        assert ctx.metrics["① 자료 읽기(엑셀)"] == [14, 14] and ctx.metrics["① 자료 읽기(카톡)"] == [12, 12]
        assert ctx.metrics["② 상품명 매칭"] == [8, 8] and ctx.metrics["④ 자연어처리(조회)"] == [4, 4]
        # 단위 오류(만원 칸에 원 단위로 입력한 리베이트)는 확정되지 않았다
        assert _canon(st, "A통신", "P003", "A55", "rebate", "join=chg") is None
        assert st.one("select 1 from canonical_price where month='2026-10' and value_int=3500000000") is None
        a = st.one("select * from staging_anomaly where rule_code='UNIT_SCALE'")
        assert a["severity"] == "block" and "3,500,000,000원" in a["ai_explanation"] and "원로" not in a["ai_explanation"]
        # 신규 상품(Z플립6)은 매칭되지 않고 확정되지 않았다
        assert st.one("select match_state from staging_mention where raw_name like '%Z플립6%'")["match_state"] == "new_product_requested"
        # 공지 메일은 가격을 바꾸지 않았다
        assert st.one("select max(value_int) v from canonical_price where product_id='P020' and field_code='monthly_installment'")["v"] == 33000
        # 리베이트: 엑셀(만원) · 카톡(개) · CSV(원)에서 온 값이 모두 원 단위로 확정됐다
        assert _canon(st, "A통신", "P003", "A115", "rebate", "join=mnp") == 600000
        assert _canon(st, "B통신", "P001", "B110", "rebate", "join=mnp") == 550000
        assert _canon(st, "C통신", "P005", "C105", "rebate", "join=mnp") == 460000
        # B통신: 출고가·공시지원금은 API, 리베이트는 카톡에서 와서 한 단말에 모였다
        assert _canon(st, "B통신", "P001", "B110", "subsidy_amount", "base") == 700000
        # 계산: 요금제별 월 납부액(고정 공식), 월정액은 요금제 마스터
        r = json.loads(st.one("select result from calc_result where partner='A통신' and product_id='P001' and plan_id='A115'")["result"])
        assert r["공시 월 납부액"] == 142290 and r["선약 월 납부액"] == 134370
        # 모든 확정 반영은 검수자(사람) 이름으로 기록
        assert st.one("select count(*) n from canonical_price where month='2026-10' and approved_by != '검수자(시뮬레이션)'")["n"] == 0
        # 요금 설계 화면: 셀러용 리베이트, 고객 화면 전환, 확정 안 된 칸은 9월 값 표시
        html = ctx.screen_path.read_text(encoding="utf-8")
        assert "고객 안내 화면" in html and "body.customer .seller-only{display:none}" in html
        assert 'class="num seller-only" title="550,000원"><b>55개</b>' in html
        assert "9월 값" in html and "▲5" in html and "갤럭시 Z플립6" in html
        assert "V컬러링 부가서비스 93일 유지 조건" in html
    finally:
        if ctx.screen_path:
            ctx.screen_path.unlink(missing_ok=True)


def test_header_mapping_wrong_unit_is_caught(monkeypatch):
    """AI가 A열을 목록에서 빼고(= 사용 안 함으로 채점), H열 단위를 원으로 잘못 본 경우 → 범위 검사가 잡고 사람이 고친다."""
    e2 = copy.deepcopy(MOCK["e2_header_map"])
    e2["mappings"] = [m for m in e2["mappings"] if m["col"] != "A"]
    next(m for m in e2["mappings"] if m["col"] == "H")["unit"] = "KRW"
    ctx = _run(monkeypatch, {"e2_header_map": e2}, upto=scenes.scene1_excel)
    assert ctx.metrics["① 자료 읽기(엑셀)"] == [13, 14]
    assert "중앙값" in "\n".join(ctx.rep.md) and "55원" in "\n".join(ctx.rep.md)
    v = ctx.store.one("select value_int from staging_record where loc='H6'")["value_int"]
    assert v == 550000            # 사람이 매핑을 고친 뒤 규칙 파서가 만원 단위로 읽음


def test_kakao_wrong_section_and_omission(monkeypatch):
    """AI가 번호이동 값을 기기변경으로 적고, 한 값을 빠뜨린 경우 → 구역 검사가 잡고, 사람이 반려·직접 입력해 확정값은 정답과 같다."""
    k2 = copy.deepcopy(MOCK["k2_kakao_parse"])
    k2["records"][0]["join_phrase"] = "기기변경"          # 갤S24 256 번호이동 플래티넘 55개
    dropped = k2["records"].pop()                         # 아이폰16 256 기기변경 세이브 12개
    ctx = _run(monkeypatch, {"k2_kakao_parse": k2}, upto=scenes.scene8_confirm_and_calc)
    assert ctx.metrics["① 자료 읽기(카톡)"] == [9, 12]     # 틀린 칸 1 + 같은 칸에 두 값(충돌) 1 + 누락 1
    rec = ctx.store.one("select grade, checks from staging_record where evidence_text like '갤S24 256 : 플래티넘 55개%' "
                        "and condition_key='join=chg'")
    assert rec["grade"] == "low" and json.loads(rec["checks"])["구역(가입유형) 대조"] is False
    st = ctx.store
    assert _canon(st, "B통신", "P001", "B110", "rebate", "join=mnp") == 550000
    assert _canon(st, "B통신", "P001", "B110", "rebate", "join=chg") == 380000
    assert _canon(st, "B통신", "P005", "B59", "rebate", "join=chg") == 120000 and dropped["value_text"] == "12개"
    assert st.one("select count(*) n from canonical_price where source like '%사람 직접 입력%'")["n"] == 2


def test_no_match_counts_as_new_product(monkeypatch):
    m4 = copy.deepcopy(MOCK["m4_match_judge"])
    for r in m4["results"]:
        if r["mention_key"] == "m03":
            r["decision"] = "no_match"
    ctx = _run(monkeypatch, {"m4_match_judge": m4}, upto=scenes.scene5_matching)
    assert ctx.metrics["② 상품명 매칭"] == [8, 8]


def test_unknown_placeholder_in_anomaly_template(monkeypatch):
    """실제 qwen2.5:7b가 설명 틀에 {memo}를 쓴 경우(2026-09-27 리허설에서 KeyError로 중단) → 틀을 버리고 기본 문구."""
    a3 = copy.deepcopy(MOCK["a3_anomaly_explain"])
    a3["items"][1]["explanation_template_ko"] = "공시지원금이 {prev_value}에서 {new_value}로 줄었습니다. 비고: {memo}"
    ctx = _run(monkeypatch, {"a3_anomaly_explain": a3}, upto=scenes.scene6_anomaly)
    row = ctx.store.one("select ai_explanation from staging_anomaly where rule_code='PRICE_JUMP'")
    assert row["ai_explanation"] == "이전 값 500,000원에서 300,000원으로 바뀌었습니다(-40.0%)."
    assert "허용되지 않은 자리표시자 {memo}" in "\n".join(ctx.rep.md)


def test_fill_template_rules():
    ok, why = scenes.fill_template("{prev_value}에서 {new_value}로 바뀜({change_pct})", 69000, 690000)
    assert why is None and ok == "69,000원에서 690,000원으로 바뀜(+900.0%)"
    for bad, reason in [("{memo} 확인", "{memo}"), ("{0}에서 바뀜", "{0}"), ("{prev_value 오타", "중괄호"),
                        ("10배로 뛰었습니다", "'10'"), ("   ", "비어")]:
        text, why = scenes.fill_template(bad, 69000, 690000)
        assert reason in why and text == "이전 값 69,000원에서 690,000원으로 바뀌었습니다(+900.0%)."


def test_nlq_codes_resolve_plan_and_gae_amount():
    ctx = _run(upto=scenes.scene0_setup)
    p, notes = scenes.resolve_nlq(ctx, {"partner": "A통신", "plan_phrase": "115요금제", "join": "mnp",
                                        "amount_text": "50개", "field": "rebate", "op": "gte"})
    assert p["plan_id"] == "A115" and p["amount"] == 500000 and p["condition_key"] == "join=mnp"
    p, notes = scenes.resolve_nlq(ctx, {"plan_phrase": "없는요금제", "amount_text": "많이"})
    assert p["plan_id"] is None and p["amount"] is None and len(notes) == 2
