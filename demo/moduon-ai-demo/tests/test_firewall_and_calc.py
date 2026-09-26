"""AI와 계산 사이의 방화벽, 계산 결정론 테스트."""
import ast
import sqlite3
from pathlib import Path

import pytest

from moduon_demo.calc import formulas
from moduon_demo.store import Store

PKG = Path(__file__).resolve().parent.parent / "moduon_demo"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(("." * node.level) + (node.module or ""))
    return names


@pytest.mark.parametrize("folder", ["calc", "rules"])
def test_no_ai_imports(folder):
    """calc/ 와 rules/ 는 AI SDK나 AI 호출 모듈을 import하지 않는다."""
    for f in (PKG / folder).glob("*.py"):
        for name in _imports(f):
            assert "anthropic" not in name, f"{f.name} imports {name}"
            assert not name.startswith(("..ai", "..llm", "moduon_demo.ai", "moduon_demo.llm")), f"{f.name} imports {name}"


def test_ai_worker_cannot_write_canonical():
    st = Store()
    with st.role("ingest_worker"):
        st.exec("insert into staging_record(partner) values ('A')")          # staging은 가능
        with pytest.raises(sqlite3.DatabaseError):
            st.exec("insert into canonical_price(partner, product_id, field_code, condition_key, value_int, unit, month)"
                    " values ('A','P','f','base',1,'KRW','2026-10')")
        with pytest.raises(sqlite3.DatabaseError):
            st.exec("update canonical_price set value_int = 0")


def test_calc_engine_cannot_read_staging():
    st = Store()
    with st.role("calc_engine"):
        st.query("select * from canonical_price")
        with pytest.raises(sqlite3.DatabaseError):
            st.query("select * from staging_record")
        with pytest.raises(sqlite3.DatabaseError):
            st.query("select * from raw_file")


def test_nlq_reader_is_read_only():
    st = Store()
    with st.role("nlq_reader"):
        with pytest.raises(sqlite3.DatabaseError):
            st.exec("insert into staging_notice(partner) values ('X')")


def test_formulas_are_deterministic_integers():
    a = formulas.telecom_monthly_payment(69000, 1155000, 450000)
    assert a == formulas.telecom_monthly_payment(69000, 1155000, 450000)
    assert a == {"단말 할부금": 29370, "월 납부액": 98370}      # (1,155,000−450,000)÷24=29,375 → 10원 절사
    assert formulas.rental_total_cost(25900, 36, 0) == {"의무기간 총비용": 932400}
    assert formulas.funeral_total_payment(33000, 120) == {"총 납입액": 3960000}
    with pytest.raises(AssertionError):
        formulas.rental_total_cost(25900.0, 36, 0)                # float 금지
