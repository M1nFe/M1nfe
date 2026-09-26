"""모의(mock) 모드로 전체 시연을 돌려 파이프라인이 끝까지 동작하는지 확인한다(API 키 불필요)."""
import io
import json
from pathlib import Path

from moduon_demo import scenes
from moduon_demo.console import Reporter
from moduon_demo.llm import LLM
from moduon_demo.store import Store

ROOT = Path(__file__).resolve().parent.parent


def run_all():
    rep = Reporter(file=io.StringIO(), width=160)
    ctx = scenes.Ctx(root=ROOT, store=Store(), llm=LLM("mock", ROOT, rep), rep=rep,
                     key=json.loads((ROOT / "data/answer_key.json").read_text(encoding="utf-8")))
    for s in (scenes.scene0_setup, scenes.scene1_excel, scenes.scene2_pdf, scenes.scene3_matching,
              scenes.scene4_anomaly, scenes.scene5_notice, scenes.scene6_confirm_and_calc,
              scenes.scene7_nlq, scenes.scene8_summary):
        s(ctx)
    return ctx


def test_full_demo_mock():
    ctx = run_all()
    st = ctx.store
    assert len(ctx.llm.calls) == len(scenes.STEP_IDS)
    # 단위 오류(690,000원) 값은 확정되지 않았다
    assert st.one("select 1 from canonical_price where month='2026-10' and value_int=690000") is None
    # 신규 상품(Z플립6)은 매칭되지 않고 확정되지 않았다
    assert st.one("select match_state from staging_mention where raw_name like '%Z플립6%'")["match_state"] == "new_product_requested"
    # 공지 메일은 가격을 바꾸지 않았다
    assert st.one("select max(value_int) v from canonical_price where product_id='P020' and field_code='monthly_installment'")["v"] == 33000
    # 이상 감지: block 1건, 설명은 숫자까지 코드가 채움
    a = st.one("select * from staging_anomaly where rule_code='UNIT_SCALE'")
    assert a["severity"] == "block" and "690,000원" in a["ai_explanation"] and "원로" not in a["ai_explanation"]
    # 계산은 확정값만 사용: S24+ 월정액은 9월 확정값 69,000원
    r = st.one("select inputs from calc_result where product_id='P003'")
    assert json.loads(r["inputs"])["monthly_fee"] == 69000
    # 모든 확정 반영은 검수자(사람) 이름으로 기록
    assert st.one("select count(*) n from canonical_price where month='2026-10' and approved_by != '검수자(시뮬레이션)'")["n"] == 0
