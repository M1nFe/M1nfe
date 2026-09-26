"""조건 문구 → 조건 키 사전. AI는 조건 문구를 '옮겨 적기'만 하고, 해석은 이 사전이 한다."""
import re

from .text import nfkc

CONDITION_PHRASES = {
    "24개월약정": "contract=24",
    "24개월": "contract=24",
    "번호이동": "join=mnp",
    "기기변경": "join=chg",
    "신규가입": "join=new",
}

CONDITION_LABELS = {
    "base": "조건 없음",
    "contract=24": "24개월 약정",
    "join=mnp": "번호이동",
    "join=chg": "기기변경",
    "join=new": "신규가입",
}


class UnknownCondition(Exception):
    """사전에 없는 조건 문구. 실제 시스템에서는 UNKNOWN_CONDITION(block)으로 막고 사람이 사전에 추가한다."""


def resolve(phrase: str | None) -> str:
    if not phrase:
        return "base"
    key = re.sub(r"[\s()]", "", nfkc(phrase))
    if key in CONDITION_PHRASES:
        return CONDITION_PHRASES[key]
    raise UnknownCondition(phrase)
