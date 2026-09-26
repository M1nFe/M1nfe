"""상품명 매칭의 규칙 단계(M0~M2)와 AI 판정 결과의 등급 계산 — 결정론 코드."""
import re
from dataclasses import dataclass

from .text import compact, name_norm, similarity


@dataclass
class Product:
    id: str
    category: str
    vendor: str
    name: str
    model_code: str
    storage_gb: str
    color: str
    variant: str


class Catalog:
    """확정(canonical) 상품 마스터와 alias 사전을 읽기 전용으로 들고 있는 객체."""

    def __init__(self, products: list[Product], aliases: list[dict]):
        self.products = {p.id: p for p in products}
        self.aliases = {(a["partner"], compact(a["alias"])): a["product_id"] for a in aliases}

    @classmethod
    def from_db(cls, store) -> "Catalog":
        prods = [Product(**r) for r in store.query("select * from canonical_product")]
        aliases = store.query("select partner, alias, product_id from canonical_alias")
        return cls(prods, aliases)

    # M0: 사람이 전에 승인한 alias와 정확히 같은 이름
    def m0_alias(self, partner: str, raw: str) -> str | None:
        return self.aliases.get((partner, compact(raw)))

    # M0: 모델코드 정확 일치(유일할 때만 확정)
    def m0_model_code(self, model_code: str | None) -> list[str]:
        if not model_code or model_code.strip() in {"", "-"}:
            return []
        return [p.id for p in self.products.values() if p.model_code == model_code.strip()]

    # M1: 정규화 규칙으로 이름이 같아지면 확정(유일할 때만)
    def m1_norm(self, raw: str, category: str) -> list[str]:
        n = name_norm(raw)
        return [p.id for p in self.products.values() if p.category == category and name_norm(p.name) == n]

    # M2: 트라이그램 유사도로 후보만 뽑음(확정하지 않음)
    def m2_candidates(self, raw: str, category: str, k: int = 5) -> list[tuple[str, float]]:
        scored = [(p.id, similarity(raw, p.name)) for p in self.products.values() if p.category == category]
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:k]


_COLOR_WORDS = {"블랙": "black", "bk": "black", "black": "black", "실버": "silver", "silver": "silver"}


def verify_attributes(raw_name: str, attrs: dict) -> dict:
    """AI가 뽑은 속성 중 원래 이름에 실제로 적힌 것만 남긴다(없는 글자를 지어냈으면 버림)."""
    raw = compact(raw_name)
    return {k: v for k, v in attrs.items() if v and compact(v) in raw}


def match_grade(product: Product, mention_model_code: str | None, attrs: dict,
                chosen_is_top1: bool, decision: str) -> tuple[str, str]:
    """등급(high/medium/low)과 이유. 모델이 스스로 말한 확신도는 쓰지 않는다."""
    if decision != "match":
        return "low", f"AI 판정: {decision}"
    compared, agreed, hard_fail = 0, 0, []
    if attrs.get("storage") and product.storage_gb:
        compared += 1
        digits = re.sub(r"\D", "", attrs["storage"])
        if digits == product.storage_gb:
            agreed += 1
        else:
            hard_fail.append(f"용량 {attrs['storage']}≠{product.storage_gb}GB")
    if attrs.get("color") and product.color:
        compared += 1
        agreed += _COLOR_WORDS.get(compact(attrs["color"])) == product.color
    if attrs.get("variant") and product.variant:
        compared += 1
        agreed += compact(attrs["variant"]) in compact(product.variant)
    model_ok = None
    if mention_model_code and mention_model_code != "-" and product.model_code:
        model_ok = mention_model_code == product.model_code
        if not model_ok:
            hard_fail.append(f"모델코드 {mention_model_code}≠{product.model_code}")
    attr_agree = agreed / compared if compared else 0.0
    why = f"속성 일치 {agreed}/{compared}, 모델코드 {'일치' if model_ok else '없음' if model_ok is None else '불일치'}, 유사도 1순위 {'예' if chosen_is_top1 else '아니오'}"
    if hard_fail:
        return "low", "하드 규칙 위반: " + ", ".join(hard_fail)
    if model_ok and attr_agree == 1 and chosen_is_top1:
        return "high", why
    if attr_agree >= 0.5 or chosen_is_top1:
        return "medium", why
    return "low", why
