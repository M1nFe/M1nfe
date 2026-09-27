"""조건 문구 → 조건 키, 요금제 표기 → 요금제 ID 사전. AI는 문구를 '옮겨 적기'만 하고, 해석은 이 사전이 한다."""
from .text import compact

CONDITION_PHRASES = {
    "24개월약정": "contract=24",
    "24개월": "contract=24",
    "번호이동": "join=mnp",
    "번이": "join=mnp",
    "기기변경": "join=chg",
    "기변": "join=chg",
    "신규가입": "join=new",
    "신규": "join=new",
}

CONDITION_LABELS = {
    "base": "조건 없음",
    "contract=24": "24개월 약정",
    "join=mnp": "번호이동",
    "join=chg": "기기변경",
    "join=new": "신규가입",
}
JOIN_KEYS = {"mnp": "join=mnp", "chg": "join=chg", "new": "join=new"}


class UnknownCondition(Exception):
    """사전으로 해석할 수 없는 문구. 실제 시스템에서는 block으로 막고 사람이 사전에 추가한다."""


def _find(text: str, table: dict) -> set:
    t = compact(text)
    return {v for k, v in table.items() if compact(k) in t}


def resolve(phrase: str | None) -> str:
    """조건 문구 안에서 사전에 있는 표기를 찾는다. 없으면 base, 서로 다른 조건이 둘 이상이면 오류."""
    if not phrase:
        return "base"
    found = _find(phrase, CONDITION_PHRASES)
    if len(found) == 1:
        return found.pop()
    raise UnknownCondition(phrase)


class PlanBook:
    """파트너별 요금제 사전(확정 데이터). '115요금제'·'플래티넘' 같은 표기를 요금제 ID로 바꾼다."""

    def __init__(self, plans: list[dict], aliases: list[dict]):
        self.plans = {(p["partner"], p["plan_id"]): p for p in plans}
        self.aliases = {}
        for a in aliases:
            self.aliases.setdefault(a["partner"], {})[a["alias"]] = a["plan_id"]
        for p in plans:
            self.aliases.setdefault(p["partner"], {})[p["plan_id"]] = p["plan_id"]

    @classmethod
    def from_db(cls, store) -> "PlanBook":
        return cls(store.query("select * from canonical_plan"), store.query("select * from canonical_plan_alias"))

    def resolve(self, partner: str | None, phrase: str | None) -> str:
        """요금제 ID. 표기가 없으면 ''(요금제 무관), 해석할 수 없거나 둘 이상이면 UnknownCondition."""
        if not phrase:
            return ""
        partners = [partner] if partner else list(self.aliases)
        found = {(pt, pid) for pt in partners for pid in _find(phrase, self.aliases.get(pt, {}))}
        if len(found) == 1:
            return found.pop()[1]
        raise UnknownCondition(phrase)

    def name(self, partner: str, plan_id: str) -> str:
        p = self.plans.get((partner, plan_id))
        return f"{p['name']} {p['monthly_fee'] // 1000}K" if p else (plan_id or "-")


def label(plan_book: PlanBook | None, partner: str, plan_id: str, condition_key: str) -> str:
    """화면 표시용 '요금제·조건' 라벨."""
    parts = []
    if plan_id:
        parts.append(plan_book.name(partner, plan_id) if plan_book else plan_id)
    if condition_key != "base" or not parts:
        parts.append(CONDITION_LABELS.get(condition_key, condition_key))
    return "·".join(parts)
